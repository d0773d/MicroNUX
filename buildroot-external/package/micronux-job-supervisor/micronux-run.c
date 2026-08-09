/* SPDX-License-Identifier: MIT */

#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <limits.h>
#include <poll.h>
#include <pwd.h>
#include <signal.h>
#include <spawn.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include "micronux-job-policy.h"

#define JOB_ARG_LIMIT 32
#define JOB_POLL_MS 25

static const char *const job_environment[] = {
	"HOME=/",
	"LOGNAME=" MICRONUX_JOB_USER,
	"PATH=/usr/bin:/bin",
	"TMPDIR=/tmp",
	"USER=" MICRONUX_JOB_USER,
	NULL,
};

static int64_t monotonic_ms(void)
{
	struct timespec now;

	if (clock_gettime(CLOCK_MONOTONIC, &now) < 0)
		return -1;
	return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static int set_nonblocking(int fd)
{
	int flags = fcntl(fd, F_GETFL, 0);

	if (flags < 0)
		return -1;
	return fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

static int mark_close_on_exec(int fd)
{
	int flags = fcntl(fd, F_GETFD, 0);

	if (flags < 0)
		return -1;
	return fcntl(fd, F_SETFD, flags | FD_CLOEXEC);
}

static bool target_is_admitted(const char *path)
{
	const size_t prefix_length = strlen(MICRONUX_JOB_APP_PREFIX);

	return strcmp(path, MICRONUX_JOB_IGNITE_PATH) == 0 ||
		(strncmp(path, MICRONUX_JOB_APP_PREFIX, prefix_length) == 0 &&
		 path[prefix_length] != '\0');
}

static int resolve_target(const char *requested, char *resolved,
	size_t resolved_size)
{
	struct stat status;
	char *canonical;

	canonical = realpath(requested, resolved);
	if (canonical == NULL)
		return -1;
	if (strlen(canonical) + 1 > resolved_size) {
		errno = ENAMETOOLONG;
		return -1;
	}
	if (!target_is_admitted(canonical)) {
		errno = EACCES;
		return -1;
	}
	if (stat(canonical, &status) < 0)
		return -1;
	if (!S_ISREG(status.st_mode) || status.st_uid != 0 ||
		(status.st_mode & (S_IWGRP | S_IWOTH | S_ISUID | S_ISGID)) != 0 ||
		access(canonical, X_OK) < 0) {
		errno = EACCES;
		return -1;
	}
	return 0;
}

static int check_installation(void)
{
	struct passwd *password = getpwnam(MICRONUX_JOB_USER);
	struct group *group = getgrnam(MICRONUX_JOB_USER);
	struct stat helper;
	struct stat app_directory;

	if (geteuid() != 0 || password == NULL || group == NULL ||
		password->pw_uid != (uid_t)MICRONUX_JOB_UID ||
		password->pw_gid != (gid_t)MICRONUX_JOB_GID ||
		group->gr_gid != (gid_t)MICRONUX_JOB_GID ||
		stat(MICRONUX_JOB_HELPER_PATH, &helper) < 0 ||
		!S_ISREG(helper.st_mode) || helper.st_uid != 0 ||
		(helper.st_mode & (S_IWGRP | S_IWOTH | S_ISUID | S_ISGID)) != 0 ||
		(helper.st_mode & S_IXUSR) == 0 ||
		stat("/opt/micronux/apps", &app_directory) < 0 ||
		!S_ISDIR(app_directory.st_mode) || app_directory.st_uid != 0 ||
		(app_directory.st_mode & (S_IWGRP | S_IWOTH)) != 0) {
		errno = EINVAL;
		return -1;
	}
	printf("MICRONUX:M7:JOB-SUPERVISOR state=ready uid=%u gid=%u "
		"admission=root-owned seccomp=allowlist\n",
		MICRONUX_JOB_UID, MICRONUX_JOB_GID);
	return 0;
}

static void close_pair(int descriptors[2])
{
	if (descriptors[0] >= 0)
		close(descriptors[0]);
	if (descriptors[1] >= 0)
		close(descriptors[1]);
	descriptors[0] = -1;
	descriptors[1] = -1;
}

static void kill_job_group(pid_t pid)
{
	if (kill(-pid, SIGKILL) < 0 && errno == ESRCH)
		(void)kill(pid, SIGKILL);
}

static int forward_output(int fd, bool quiet, uint64_t *total,
	bool *open_stream, bool *output_exceeded)
{
	char buffer[1024];

	for (;;) {
		ssize_t count = read(fd, buffer, sizeof(buffer));

		if (count > 0) {
			size_t accepted = 0;

			if (*total < MICRONUX_JOB_OUTPUT_BYTES) {
				uint64_t available = MICRONUX_JOB_OUTPUT_BYTES - *total;

				accepted = (uint64_t)count < available ?
					(size_t)count : (size_t)available;
			}
			if (!quiet && accepted > 0) {
				size_t offset = 0;

				while (offset < accepted) {
					ssize_t written = write(STDOUT_FILENO,
						buffer + offset, accepted - offset);

					if (written < 0 && errno == EINTR)
						continue;
					if (written <= 0)
						break;
					offset += (size_t)written;
				}
			}
			*total += (uint64_t)count;
			if (*total > MICRONUX_JOB_OUTPUT_BYTES)
				*output_exceeded = true;
			continue;
		}
		if (count == 0) {
			close(fd);
			*open_stream = false;
			return 0;
		}
		if (errno == EINTR)
			continue;
		if (errno == EAGAIN || errno == EWOULDBLOCK)
			return 0;
		close(fd);
		*open_stream = false;
		return -1;
	}
}

static int monitor_job(pid_t pid, int stdout_fd, int stderr_fd, bool quiet)
{
	struct pollfd polls[2];
	int status = 0;
	int64_t started = monotonic_ms();
	int64_t finished;
	uint64_t output = 0;
	bool stdout_open = true;
	bool stderr_open = true;
	bool child_done = false;
	bool group_killed = false;
	bool output_exceeded = false;
	const char *policy_reason = NULL;

	if (started < 0)
		started = 0;
	polls[0].fd = stdout_fd;
	polls[0].events = POLLIN;
	polls[1].fd = stderr_fd;
	polls[1].events = POLLIN;

	while (!child_done || stdout_open || stderr_open) {
		int rc;
		int64_t now = monotonic_ms();

		if (!child_done && policy_reason == NULL &&
			(now < 0 || now - started >= MICRONUX_JOB_WALL_MS))
			policy_reason = "wall";
		if (!child_done && policy_reason != NULL && !group_killed) {
			kill_job_group(pid);
			group_killed = true;
		}

		polls[0].fd = stdout_open ? stdout_fd : -1;
		polls[1].fd = stderr_open ? stderr_fd : -1;
		do {
			rc = poll(polls, 2, JOB_POLL_MS);
		} while (rc < 0 && errno == EINTR);
		if (rc < 0) {
			policy_reason = "monitor";
			kill_job_group(pid);
			group_killed = true;
		}
		if (stdout_open && (polls[0].revents &
			(POLLIN | POLLHUP | POLLERR | POLLNVAL)) != 0)
			(void)forward_output(stdout_fd, quiet, &output,
				&stdout_open, &output_exceeded);
		if (stderr_open && (polls[1].revents &
			(POLLIN | POLLHUP | POLLERR | POLLNVAL)) != 0)
			(void)forward_output(stderr_fd, quiet, &output,
				&stderr_open, &output_exceeded);
		if (!child_done && output_exceeded && policy_reason == NULL)
			policy_reason = "output";
		if (!child_done) {
			pid_t waited = waitpid(pid, &status, WNOHANG);

			if (waited == pid) {
				child_done = true;
				if (!group_killed) {
					kill_job_group(pid);
					group_killed = true;
				}
			} else if (waited < 0 && errno != EINTR) {
				status = 0;
				child_done = true;
				if (policy_reason == NULL)
					policy_reason = "wait";
			}
		}
	}
	finished = monotonic_ms();
	if (finished < started)
		finished = started;

	if (policy_reason != NULL) {
		int result = strcmp(policy_reason, "wall") == 0 ? 124 : 125;

		printf("MICRONUX:M7:JOB result=killed reason=%s signal=9 "
			"output=%llu duration_ms=%lld\n", policy_reason,
			(unsigned long long)output,
			(long long)(finished - started));
		return result;
	}
	if (WIFEXITED(status)) {
		int result = WEXITSTATUS(status);

		printf("MICRONUX:M7:JOB result=exit status=%d output=%llu "
			"duration_ms=%lld\n", result,
			(unsigned long long)output,
			(long long)(finished - started));
		return result;
	}
	if (WIFSIGNALED(status)) {
		int signal_number = WTERMSIG(status);

		printf("MICRONUX:M7:JOB result=signal signal=%d output=%llu "
			"duration_ms=%lld\n", signal_number,
			(unsigned long long)output,
			(long long)(finished - started));
		return 128 + signal_number;
	}
	printf("MICRONUX:M7:JOB result=invalid output=%llu duration_ms=%lld\n",
		(unsigned long long)output, (long long)(finished - started));
	return 125;
}

static int run_job(char **job_argv, bool quiet)
{
	char resolved[PATH_MAX];
	char stdout_descriptor_text[16];
	char stderr_descriptor_text[16];
	char *helper_argv[JOB_ARG_LIMIT + 4];
	int stdout_pipe[2] = { -1, -1 };
	int stderr_pipe[2] = { -1, -1 };
	posix_spawnattr_t attributes;
	sigset_t empty_mask;
	short flags = POSIX_SPAWN_USEVFORK | POSIX_SPAWN_SETPGROUP |
		POSIX_SPAWN_SETSIGMASK;
	pid_t pid;
	int argument_count = 0;
	int rc;
	int result;
	bool attributes_initialized = false;

	while (job_argv[argument_count] != NULL) {
		if (argument_count >= JOB_ARG_LIMIT) {
			errno = E2BIG;
			return -1;
		}
		argument_count++;
	}
	if (resolve_target(job_argv[0], resolved, sizeof(resolved)) < 0)
		return -1;
	helper_argv[0] = (char *)MICRONUX_JOB_HELPER_PATH;
	helper_argv[1] = stdout_descriptor_text;
	helper_argv[2] = stderr_descriptor_text;
	helper_argv[3] = resolved;
	for (rc = 1; rc < argument_count; rc++)
		helper_argv[rc + 3] = job_argv[rc];
	helper_argv[argument_count + 3] = NULL;

	if (pipe(stdout_pipe) < 0 || pipe(stderr_pipe) < 0 ||
		mark_close_on_exec(stdout_pipe[0]) < 0 ||
		mark_close_on_exec(stderr_pipe[0]) < 0 ||
		set_nonblocking(stdout_pipe[0]) < 0 ||
		set_nonblocking(stderr_pipe[0]) < 0) {
		close_pair(stdout_pipe);
		close_pair(stderr_pipe);
		return -1;
	}
	(void)snprintf(stdout_descriptor_text, sizeof(stdout_descriptor_text),
		"%d", stdout_pipe[1]);
	(void)snprintf(stderr_descriptor_text, sizeof(stderr_descriptor_text),
		"%d", stderr_pipe[1]);
	rc = posix_spawnattr_init(&attributes);
	if (rc == 0)
		attributes_initialized = true;
	if (rc == 0)
		rc = posix_spawnattr_setflags(&attributes, flags);
	if (rc == 0)
		rc = posix_spawnattr_setpgroup(&attributes, 0);
	sigemptyset(&empty_mask);
	if (rc == 0)
		rc = posix_spawnattr_setsigmask(&attributes, &empty_mask);
	if (rc == 0)
		rc = posix_spawn(&pid, MICRONUX_JOB_HELPER_PATH, NULL,
			&attributes, helper_argv, (char *const *)job_environment);
	if (attributes_initialized)
		(void)posix_spawnattr_destroy(&attributes);
	if (rc != 0) {
		errno = rc;
		close_pair(stdout_pipe);
		close_pair(stderr_pipe);
		return -1;
	}
	close(stdout_pipe[1]);
	stdout_pipe[1] = -1;
	close(stderr_pipe[1]);
	stderr_pipe[1] = -1;
	printf("MICRONUX:M7:JOB start uid=%u gid=%u wall_ms=%u cpu_s=%u "
		"nofile=%u nproc=%u stack_kib=%u data_kib=%u arena_kib=%u "
		"output_bytes=%u seccomp=allowlist\n", MICRONUX_JOB_UID,
		MICRONUX_JOB_GID, MICRONUX_JOB_WALL_MS,
		MICRONUX_JOB_CPU_SECONDS, MICRONUX_JOB_NOFILE,
		MICRONUX_JOB_NPROC, MICRONUX_JOB_STACK_BYTES / 1024U,
		MICRONUX_JOB_DATA_BYTES / 1024U,
		MICRONUX_JOB_ARENA_BYTES / 1024U,
		MICRONUX_JOB_OUTPUT_BYTES);
	result = monitor_job(pid, stdout_pipe[0], stderr_pipe[0], quiet);
	return result;
}

static void usage(FILE *stream)
{
	fprintf(stream,
		"usage: micronux-run --check | micronux-run [-q] -- program [args...]\n");
}

int main(int argc, char **argv)
{
	bool quiet = false;
	int command_index = 1;
	int lock_fd;
	int result;

	if (argc == 2 && strcmp(argv[1], "--check") == 0) {
		if (check_installation() < 0) {
			perror("micronux-run: policy installation");
			return 1;
		}
		return 0;
	}
	if (geteuid() != 0) {
		fprintf(stderr, "micronux-run: root supervisor required\n");
		return 1;
	}
	if (command_index < argc && strcmp(argv[command_index], "-q") == 0) {
		quiet = true;
		command_index++;
	}
	if (command_index >= argc || strcmp(argv[command_index], "--") != 0 ||
		command_index + 1 >= argc) {
		usage(stderr);
		return 2;
	}
	command_index++;
	lock_fd = open(MICRONUX_JOB_LOCK_PATH,
		O_CREAT | O_RDWR | O_CLOEXEC, 0600);
	if (lock_fd < 0 || flock(lock_fd, LOCK_EX | LOCK_NB) < 0) {
		if (lock_fd >= 0)
			close(lock_fd);
		fprintf(stderr, "micronux-run: another job is active\n");
		return 1;
	}
	result = run_job(&argv[command_index], quiet);
	if (result < 0) {
		perror("micronux-run: launch");
		result = 1;
	}
	(void)flock(lock_fd, LOCK_UN);
	close(lock_fd);
	return result;
}
