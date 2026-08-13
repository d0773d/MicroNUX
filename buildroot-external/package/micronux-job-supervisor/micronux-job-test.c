/* SPDX-License-Identifier: MIT */

#include <errno.h>
#include <fcntl.h>
#include <linux/reboot.h>
#include <poll.h>
#include <signal.h>
#include <spawn.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/ptrace.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#include "micronux/device.h"

#include "micronux-job-policy.h"

extern char **environ;

static int fail(const char *reason)
{
	fprintf(stderr, "MICRONUX:M7:JOB-CONTRACT:FAIL reason=%s errno=%d\n",
		reason, errno);
	return 1;
}

static int read_status_value(const char *name, unsigned long long *value)
{
	char line[160];
	FILE *status = fopen("/proc/self/status", "r");
	size_t name_length = strlen(name);

	if (status == NULL)
		return -1;
	while (fgets(line, sizeof(line), status) != NULL) {
		char *end;
		unsigned long long parsed;

		if (strncmp(line, name, name_length) != 0 ||
			line[name_length] != ':')
			continue;
		errno = 0;
		parsed = strtoull(line + name_length + 1, &end, 0);
		if (errno != 0 || end == line + name_length + 1) {
			fclose(status);
			return -1;
		}
		*value = parsed;
		fclose(status);
		return 0;
	}
	fclose(status);
	errno = ENOENT;
	return -1;
}

static int verify_status(void)
{
	static const char *const zero_fields[] = {
		"CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb",
	};
	unsigned long long value;
	size_t index;

	if (getuid() != (uid_t)MICRONUX_JOB_UID ||
		geteuid() != (uid_t)MICRONUX_JOB_UID ||
		getgid() != (gid_t)MICRONUX_JOB_GID ||
		getegid() != (gid_t)MICRONUX_JOB_GID || getgroups(0, NULL) != 0)
		return -1;
	for (index = 0; index < sizeof(zero_fields) / sizeof(zero_fields[0]);
		index++) {
		if (read_status_value(zero_fields[index], &value) < 0 || value != 0)
			return -1;
	}
	if (read_status_value("NoNewPrivs", &value) < 0 || value != 1 ||
		read_status_value("Seccomp", &value) < 0 || value != 2)
		return -1;
	return 0;
}

static int verify_limit(int resource, rlim_t expected)
{
	struct rlimit limit;

	if (getrlimit(resource, &limit) < 0)
		return -1;
	if (limit.rlim_cur != expected || limit.rlim_max != expected) {
		errno = ERANGE;
		return -1;
	}
	return 0;
}

static int verify_limits(void)
{
	return verify_limit(RLIMIT_CPU, MICRONUX_JOB_CPU_SECONDS) == 0 &&
		verify_limit(RLIMIT_NOFILE, MICRONUX_JOB_NOFILE) == 0 &&
		verify_limit(RLIMIT_NPROC, MICRONUX_JOB_NPROC) == 0 &&
		verify_limit(RLIMIT_STACK, MICRONUX_JOB_STACK_BYTES) == 0 &&
		verify_limit(RLIMIT_DATA, MICRONUX_JOB_DATA_BYTES) == 0 &&
		verify_limit(RLIMIT_AS, MICRONUX_JOB_ARENA_BYTES) == 0 ? 0 : -1;
}

static bool expected_device_error(int error)
{
	return error == EACCES || error == EPERM || error == ENOENT ||
		error == ENODEV;
}

static int verify_device_denial(void)
{
	static const char *const paths[] = {
		"/dev/mem", "/dev/kmem", "/dev/mmcblk0", "/dev/ttyGS0",
	};
	size_t index;
	int fd;

	for (index = 0; index < sizeof(paths) / sizeof(paths[0]); index++) {
		fd = open(paths[index], O_RDWR | O_NONBLOCK);
		if (fd >= 0) {
			close(fd);
			errno = EACCES;
			return -1;
		}
		if (!expected_device_error(errno))
			return -1;
	}
	fd = socket(AF_INET, SOCK_RAW, 1);
	if (fd >= 0) {
		close(fd);
		errno = EACCES;
		return -1;
	}
	return errno == EPERM || errno == EACCES ? 0 : -1;
}

static int expect_policy_errno(long result)
{
	return result < 0 && errno == EPERM ? 0 : -1;
}

static int verify_syscall_denial(void)
{
	errno = 0;
	if (expect_policy_errno(ioctl(-1, 0, 0)) < 0)
		return -1;
	errno = 0;
	if (expect_policy_errno(ptrace(PTRACE_TRACEME, 0, NULL, NULL)) < 0)
		return -1;
	errno = 0;
	if (expect_policy_errno(mount("none", "/", "none", 0, NULL)) < 0)
		return -1;
	errno = 0;
	if (expect_policy_errno(syscall(SYS_reboot, LINUX_REBOOT_MAGIC1,
		LINUX_REBOOT_MAGIC2, LINUX_REBOOT_CMD_RESTART, NULL)) < 0)
		return -1;
#ifdef SYS_init_module
	errno = 0;
	if (expect_policy_errno(syscall(SYS_init_module, NULL, 0, "")) < 0)
		return -1;
#endif
	return 0;
}

static int verify_device_service(void)
{
	struct micronux_device_info info;
	uint32_t devices;

	if (micronux_device_get_info(&info) < 0 ||
		(info.capabilities & MICRONUX_DEVICE_CAP_OBSERVE) == 0 ||
		(info.capabilities & MICRONUX_DEVICE_CAP_ADMIN) != 0 ||
		micronux_device_list(&devices) < 0)
		return -1;
	errno = 0;
	if (micronux_device_admin_probe() == 0 || errno != EACCES)
		return -1;
	return 0;
}

static int verify_file_descriptors(void)
{
	int descriptors[MICRONUX_JOB_NOFILE];
	int count = 0;
	int index;

	while (count < (int)MICRONUX_JOB_NOFILE) {
		int descriptor = open("/dev/null", O_RDONLY);

		if (descriptor < 0)
			break;
		descriptors[count++] = descriptor;
	}
	if (errno != EMFILE || count < 10 || count > 13) {
		for (index = 0; index < count; index++)
			close(descriptors[index]);
		errno = EMFILE;
		return -1;
	}
	for (index = 0; index < count; index++)
		close(descriptors[index]);
	return 0;
}

static int verify_memory_limit(void)
{
	uint8_t *mapping = mmap(NULL, 1024U * 1024U,
		PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	void *oversized;

	if (mapping == MAP_FAILED)
		return -1;
	mapping[0] = 0x5a;
	mapping[1024U * 1024U - 1] = 0xa5;
	if (munmap(mapping, 1024U * 1024U) < 0)
		return -1;
	errno = 0;
	oversized = mmap(NULL, 7U * 1024U * 1024U,
		PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (oversized != MAP_FAILED) {
		munmap(oversized, 7U * 1024U * 1024U);
		errno = ENOMEM;
		return -1;
	}
	return errno == ENOMEM ? 0 : -1;
}

static int verify_process_limit(const char *program)
{
	pid_t children[MICRONUX_JOB_NPROC];
	char *const child_argv[] = { (char *)program, "hold", NULL };
	int spawned = 0;
	int rc = 0;
	int index;

	while (spawned < (int)MICRONUX_JOB_NPROC) {
		rc = posix_spawn(&children[spawned], program, NULL, NULL,
			child_argv, environ);
		if (rc != 0)
			break;
		spawned++;
	}
	for (index = 0; index < spawned; index++)
		(void)kill(children[index], SIGTERM);
	for (index = 0; index < spawned; index++) {
		int status;

		while (waitpid(children[index], &status, 0) < 0 && errno == EINTR)
			;
	}
	if (spawned != (int)MICRONUX_JOB_NPROC - 1 || rc != EAGAIN) {
		errno = rc == 0 ? EOVERFLOW : rc;
		return -1;
	}
	return 0;
}

static int run_contract(const char *program)
{
	if (verify_status() < 0)
		return fail("identity-or-capabilities");
	if (verify_limits() < 0)
		return fail("rlimit");
	if (verify_device_denial() < 0)
		return fail("device-dac");
	if (verify_syscall_denial() < 0)
		return fail("seccomp");
	if (verify_device_service() < 0)
		return fail("device-service-policy");
	if (verify_file_descriptors() < 0)
		return fail("file-descriptor-limit");
	if (verify_memory_limit() < 0)
		return fail("arena-limit");
	if (verify_process_limit(program) < 0)
		return fail("process-limit");
	printf("MICRONUX:M7:JOB-CONTRACT:PASS uid=%u gid=%u caps=zero "
		"seccomp=2 devices=restricted fd=%u nproc=%u arena_kib=%u\n",
		MICRONUX_JOB_UID, MICRONUX_JOB_GID, MICRONUX_JOB_NOFILE,
		MICRONUX_JOB_NPROC, MICRONUX_JOB_ARENA_BYTES / 1024U);
	return 0;
}

static void run_spin(void)
{
	volatile unsigned long counter = 0;

	for (;;)
		counter++;
}

static int run_flood(void)
{
	char output[1024];

	memset(output, 'J', sizeof(output));
	for (;;) {
		ssize_t count = write(STDOUT_FILENO, output, sizeof(output));

		if (count < 0 && errno == EINTR)
			continue;
		if (count <= 0)
			return 1;
	}
}

int main(int argc, char **argv)
{
	if (argc != 2) {
		fprintf(stderr, "usage: micronux-job-test MODE\n");
		return 2;
	}
	if (strcmp(argv[1], "contract") == 0)
		return run_contract(argv[0]);
	if (strcmp(argv[1], "hold") == 0) {
		for (;;)
			pause();
	}
	if (strcmp(argv[1], "spin") == 0) {
		run_spin();
		return 1;
	}
	if (strcmp(argv[1], "flood") == 0)
		return run_flood();
	fprintf(stderr, "micronux-job-test: unknown mode\n");
	return 2;
}
