/* SPDX-License-Identifier: MIT */

#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <linux/audit.h>
#include <linux/capability.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/utsname.h>
#include <unistd.h>

#include "micronux-job-policy.h"

#define ALLOW_SYSCALL(name) \
	BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, SYS_##name, 0, 1), \
	BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW)

static int set_limit(int resource, rlim_t value)
{
	const struct rlimit limit = {
		.rlim_cur = value,
		.rlim_max = value,
	};

	return setrlimit(resource, &limit);
}

static int apply_resource_limits(void)
{
	return set_limit(RLIMIT_CPU, MICRONUX_JOB_CPU_SECONDS) == 0 &&
		set_limit(RLIMIT_NOFILE, MICRONUX_JOB_NOFILE) == 0 &&
		set_limit(RLIMIT_NPROC, MICRONUX_JOB_NPROC) == 0 &&
		set_limit(RLIMIT_STACK, MICRONUX_JOB_STACK_BYTES) == 0 &&
		set_limit(RLIMIT_DATA, MICRONUX_JOB_DATA_BYTES) == 0 &&
		set_limit(RLIMIT_AS, MICRONUX_JOB_ARENA_BYTES) == 0 &&
		set_limit(RLIMIT_FSIZE, MICRONUX_JOB_DATA_BYTES) == 0 &&
		set_limit(RLIMIT_CORE, 0) == 0 ? 0 : -1;
}

static int clear_capabilities(void)
{
	struct __user_cap_header_struct header = {
		.version = _LINUX_CAPABILITY_VERSION_3,
		.pid = 0,
	};
	struct __user_cap_data_struct capabilities[_LINUX_CAPABILITY_U32S_3] = { 0 };
	int capability;

	for (capability = 0; capability <= CAP_LAST_CAP; capability++) {
		if (prctl(PR_CAPBSET_DROP, capability, 0, 0, 0) < 0)
			return -1;
	}
	if (setgroups(0, NULL) < 0 ||
		setresgid((gid_t)MICRONUX_JOB_GID, (gid_t)MICRONUX_JOB_GID,
			(gid_t)MICRONUX_JOB_GID) < 0 ||
		setresuid((uid_t)MICRONUX_JOB_UID, (uid_t)MICRONUX_JOB_UID,
			(uid_t)MICRONUX_JOB_UID) < 0)
		return -1;
	if (syscall(SYS_capset, &header, capabilities) < 0)
		return -1;
	return 0;
}

static int verify_identity(void)
{
	struct __user_cap_header_struct header = {
		.version = _LINUX_CAPABILITY_VERSION_3,
		.pid = 0,
	};
	struct __user_cap_data_struct capabilities[_LINUX_CAPABILITY_U32S_3] = { 0 };
	uid_t real_uid;
	uid_t effective_uid;
	uid_t saved_uid;
	gid_t real_gid;
	gid_t effective_gid;
	gid_t saved_gid;
	int index;

	if (getresuid(&real_uid, &effective_uid, &saved_uid) < 0 ||
		getresgid(&real_gid, &effective_gid, &saved_gid) < 0 ||
		real_uid != (uid_t)MICRONUX_JOB_UID ||
		effective_uid != (uid_t)MICRONUX_JOB_UID ||
		saved_uid != (uid_t)MICRONUX_JOB_UID ||
		real_gid != (gid_t)MICRONUX_JOB_GID ||
		effective_gid != (gid_t)MICRONUX_JOB_GID ||
		saved_gid != (gid_t)MICRONUX_JOB_GID || getgroups(0, NULL) != 0 ||
		syscall(SYS_capget, &header, capabilities) < 0)
		return -1;
	for (index = 0; index < _LINUX_CAPABILITY_U32S_3; index++) {
		if (capabilities[index].effective != 0 ||
			capabilities[index].permitted != 0 ||
			capabilities[index].inheritable != 0)
			return -1;
	}
	return 0;
}

static void close_extra_descriptors(void)
{
	int descriptor;

	for (descriptor = STDERR_FILENO + 1; descriptor < 1024; descriptor++)
		close(descriptor);
}

static int parse_descriptor(const char *text, int *descriptor)
{
	char *end;
	long value;

	errno = 0;
	value = strtol(text, &end, 10);
	if (errno != 0 || end == text || *end != '\0' || value < 3 ||
		value >= 1024) {
		errno = EINVAL;
		return -1;
	}
	*descriptor = (int)value;
	return 0;
}

static int redirect_standard_descriptors(const char *stdout_text,
	const char *stderr_text)
{
	int stdout_descriptor;
	int stderr_descriptor;
	int null_descriptor;

	if (parse_descriptor(stdout_text, &stdout_descriptor) < 0 ||
		parse_descriptor(stderr_text, &stderr_descriptor) < 0 ||
		stdout_descriptor == stderr_descriptor)
		return -1;
	null_descriptor = open("/dev/null", O_RDONLY);
	if (null_descriptor < 0 || dup2(null_descriptor, STDIN_FILENO) < 0 ||
		dup2(stdout_descriptor, STDOUT_FILENO) < 0 ||
		dup2(stderr_descriptor, STDERR_FILENO) < 0) {
		if (null_descriptor >= 0)
			close(null_descriptor);
		return -1;
	}
	close(null_descriptor);
	return 0;
}

static int install_seccomp_filter(void)
{
	struct sock_filter filter[] = {
		BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
			offsetof(struct seccomp_data, arch)),
		BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_RISCV32, 1, 0),
		BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
		BPF_STMT(BPF_LD | BPF_W | BPF_ABS,
			offsetof(struct seccomp_data, nr)),
		ALLOW_SYSCALL(read),
		ALLOW_SYSCALL(write),
		ALLOW_SYSCALL(readv),
		ALLOW_SYSCALL(writev),
		ALLOW_SYSCALL(pread64),
		ALLOW_SYSCALL(pwrite64),
		ALLOW_SYSCALL(close),
		ALLOW_SYSCALL(llseek),
		ALLOW_SYSCALL(fcntl64),
		ALLOW_SYSCALL(dup),
		ALLOW_SYSCALL(dup3),
		ALLOW_SYSCALL(pipe2),
		ALLOW_SYSCALL(openat),
		ALLOW_SYSCALL(statx),
		ALLOW_SYSCALL(readlinkat),
		ALLOW_SYSCALL(getdents64),
		ALLOW_SYSCALL(faccessat),
#ifdef SYS_faccessat2
		ALLOW_SYSCALL(faccessat2),
#endif
		ALLOW_SYSCALL(unlinkat),
		ALLOW_SYSCALL(mkdirat),
#ifdef SYS_renameat2
		ALLOW_SYSCALL(renameat2),
#endif
		ALLOW_SYSCALL(fsync),
		ALLOW_SYSCALL(fdatasync),
		ALLOW_SYSCALL(ftruncate64),
		ALLOW_SYSCALL(getcwd),
		ALLOW_SYSCALL(chdir),
		ALLOW_SYSCALL(umask),
		ALLOW_SYSCALL(brk),
		ALLOW_SYSCALL(mmap2),
		ALLOW_SYSCALL(munmap),
		ALLOW_SYSCALL(mremap),
		ALLOW_SYSCALL(mprotect),
		ALLOW_SYSCALL(madvise),
		ALLOW_SYSCALL(clone),
#ifdef SYS_clone3
		ALLOW_SYSCALL(clone3),
#endif
		ALLOW_SYSCALL(execve),
		ALLOW_SYSCALL(waitid),
		ALLOW_SYSCALL(exit),
		ALLOW_SYSCALL(exit_group),
		ALLOW_SYSCALL(kill),
		ALLOW_SYSCALL(tkill),
		ALLOW_SYSCALL(tgkill),
		ALLOW_SYSCALL(set_tid_address),
		ALLOW_SYSCALL(set_robust_list),
#ifdef SYS_futex_time64
		ALLOW_SYSCALL(futex_time64),
#endif
		ALLOW_SYSCALL(sched_yield),
		ALLOW_SYSCALL(sched_getaffinity),
		ALLOW_SYSCALL(getpid),
		ALLOW_SYSCALL(getppid),
		ALLOW_SYSCALL(gettid),
		ALLOW_SYSCALL(getuid),
		ALLOW_SYSCALL(geteuid),
		ALLOW_SYSCALL(getgid),
		ALLOW_SYSCALL(getegid),
		ALLOW_SYSCALL(getresuid),
		ALLOW_SYSCALL(getresgid),
		ALLOW_SYSCALL(getgroups),
		ALLOW_SYSCALL(rt_sigaction),
		ALLOW_SYSCALL(rt_sigprocmask),
		ALLOW_SYSCALL(rt_sigreturn),
		ALLOW_SYSCALL(rt_sigsuspend),
		ALLOW_SYSCALL(sigaltstack),
		ALLOW_SYSCALL(restart_syscall),
#ifdef SYS_clock_gettime64
		ALLOW_SYSCALL(clock_gettime64),
#endif
#ifdef SYS_clock_nanosleep_time64
		ALLOW_SYSCALL(clock_nanosleep_time64),
#endif
		ALLOW_SYSCALL(times),
		ALLOW_SYSCALL(getrusage),
		ALLOW_SYSCALL(prlimit64),
#ifdef SYS_ppoll_time64
		ALLOW_SYSCALL(ppoll_time64),
#endif
#ifdef SYS_pselect6_time64
		ALLOW_SYSCALL(pselect6_time64),
#endif
		ALLOW_SYSCALL(socket),
		ALLOW_SYSCALL(socketpair),
		ALLOW_SYSCALL(connect),
		ALLOW_SYSCALL(bind),
		ALLOW_SYSCALL(listen),
		ALLOW_SYSCALL(accept),
		ALLOW_SYSCALL(accept4),
		ALLOW_SYSCALL(shutdown),
		ALLOW_SYSCALL(getsockname),
		ALLOW_SYSCALL(getpeername),
		ALLOW_SYSCALL(sendto),
		ALLOW_SYSCALL(recvfrom),
		ALLOW_SYSCALL(setsockopt),
		ALLOW_SYSCALL(getsockopt),
		ALLOW_SYSCALL(sendmsg),
		ALLOW_SYSCALL(recvmsg),
		ALLOW_SYSCALL(eventfd2),
		ALLOW_SYSCALL(signalfd4),
		ALLOW_SYSCALL(timerfd_create),
#ifdef SYS_timerfd_settime64
		ALLOW_SYSCALL(timerfd_settime64),
#endif
#ifdef SYS_timerfd_gettime64
		ALLOW_SYSCALL(timerfd_gettime64),
#endif
		ALLOW_SYSCALL(epoll_create1),
		ALLOW_SYSCALL(epoll_ctl),
		ALLOW_SYSCALL(epoll_pwait),
#ifdef SYS_epoll_pwait2
		ALLOW_SYSCALL(epoll_pwait2),
#endif
		ALLOW_SYSCALL(getrandom),
		ALLOW_SYSCALL(uname),
		ALLOW_SYSCALL(sysinfo),
		BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO |
			(SECCOMP_RET_DATA & EPERM)),
	};
	const struct sock_fprog program = {
		.len = (unsigned short)(sizeof(filter) / sizeof(filter[0])),
		.filter = filter,
	};

	if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0)
		return -1;
	return prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program);
}

int main(int argc, char **argv)
{
	if (argc < 4 || geteuid() != 0) {
		fprintf(stderr, "micronux-job-exec: invalid invocation\n");
		return 126;
	}
	if (redirect_standard_descriptors(argv[1], argv[2]) < 0) {
		perror("micronux-job-exec: redirect");
		return 126;
	}
	umask(0077);
	if (apply_resource_limits() < 0 || clear_capabilities() < 0 ||
		verify_identity() < 0 || chdir("/") < 0) {
		perror("micronux-job-exec: apply policy");
		return 126;
	}
	close_extra_descriptors();
	if (install_seccomp_filter() < 0) {
		perror("micronux-job-exec: seccomp");
		return 126;
	}
	dprintf(STDOUT_FILENO,
		"MICRONUX:M7:JOB-POLICY uid=%u gid=%u caps=zero "
		"no_new_privs=1 seccomp=allowlist\n",
		MICRONUX_JOB_UID, MICRONUX_JOB_GID);
	execv(argv[3], &argv[3]);
	dprintf(STDERR_FILENO, "micronux-job-exec: exec: %s\n",
		strerror(errno));
	return 126;
}
