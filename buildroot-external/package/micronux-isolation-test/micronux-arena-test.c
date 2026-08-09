// SPDX-License-Identifier: MIT

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <setjmp.h>
#include <signal.h>
#include <spawn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#define MICRONUX_USER_START ((uintptr_t)UINT32_C(0x49700000))
#define MICRONUX_USER_END ((uintptr_t)UINT32_C(0x49f00000))
#define REUSE_CYCLES 32U
#define TEST_PAGE_BYTES 4096U
#define FLAT_RELOC_START_OFFSET 28U
#define FLAT_RELOC_COUNT_OFFSET 32U

struct pool_state {
	unsigned long pages;
	unsigned long reserved;
	unsigned long mapped;
	unsigned long free;
	unsigned long arenas;
};

static sigjmp_buf fault_env;
static volatile sig_atomic_t fault_armed;

static int fail(const char *stage, uintptr_t detail)
{
	const int saved_errno = errno;

	printf("MICRONUX:M7:ARENA:FAIL stage=%s detail=%08" PRIxPTR
	       " errno=%d\n", stage, detail, saved_errno);
	return 1;
}

static int address_in_pool(uintptr_t address)
{
	return address >= MICRONUX_USER_START && address < MICRONUX_USER_END;
}

static void fault_handler(int signal_number)
{
	if (fault_armed && signal_number == SIGSEGV)
		siglongjmp(fault_env, 1);
	_exit(128 + signal_number);
}

static int expect_read_fault(uintptr_t address)
{
	struct sigaction action;
	struct sigaction previous;
	volatile uint32_t value;

	memset(&action, 0, sizeof(action));
	action.sa_handler = fault_handler;
	if (sigemptyset(&action.sa_mask) != 0 ||
	    sigaction(SIGSEGV, &action, &previous) != 0)
		return -1;
	if (sigsetjmp(fault_env, 1) == 0) {
		fault_armed = 1;
		value = *(volatile uint32_t *)address;
		(void)value;
		fault_armed = 0;
		(void)sigaction(SIGSEGV, &previous, NULL);
		errno = EACCES;
		return -1;
	}
	fault_armed = 0;
	if (sigaction(SIGSEGV, &previous, NULL) != 0)
		return -1;
	return 0;
}

static int write_all(int descriptor, const void *buffer, size_t length)
{
	const unsigned char *cursor = buffer;

	while (length != 0U) {
		ssize_t written = write(descriptor, cursor, length);

		if (written < 0) {
			if (errno == EINTR)
				continue;
			return -1;
		}
		cursor += (size_t)written;
		length -= (size_t)written;
	}
	return 0;
}

static int read_line(int descriptor, char *line, size_t capacity)
{
	size_t used = 0;

	if (capacity == 0U)
		return -1;
	while (used + 1U < capacity) {
		char byte;
		ssize_t received = read(descriptor, &byte, 1);

		if (received < 0) {
			if (errno == EINTR)
				continue;
			return -1;
		}
		if (received == 0)
			break;
		line[used++] = byte;
		if (byte == '\n')
			break;
	}
	line[used] = '\0';
	return used == 0U ? -1 : 0;
}

static int wait_success(pid_t child)
{
	pid_t waited;
	int status;

	do {
		waited = waitpid(child, &status, 0);
	} while (waited < 0 && errno == EINTR);
	if (waited != child || !WIFEXITED(status) || WEXITSTATUS(status) != 0)
		return -1;
	return 0;
}

static int parse_descriptor(const char *text)
{
	char *end = NULL;
	long parsed;

	errno = 0;
	parsed = strtol(text, &end, 10);
	if (errno != 0 || end == text || *end != '\0' || parsed < 0 ||
	    parsed > INT32_MAX) {
		errno = EINVAL;
		return -1;
	}
	return (int)parsed;
}

static int mark_close_on_exec(int descriptor)
{
	const int flags = fcntl(descriptor, F_GETFD);

	if (flags < 0)
		return -1;
	return fcntl(descriptor, F_SETFD, flags | FD_CLOEXEC);
}

static int victim_main(const char *parent_address_text,
		       const char *command_descriptor_text,
		       const char *result_descriptor_text)
{
	volatile uint32_t marker = UINT32_C(0x4d374152);
	char command[32];
	char ready[32];
	char *end = NULL;
	unsigned long parent_address;
	int command_fd;
	int result_fd;

	errno = 0;
	parent_address = strtoul(parent_address_text, &end, 16);
	if (errno != 0 || end == parent_address_text || *end != '\0' ||
	    !address_in_pool((uintptr_t)parent_address))
		return fail("victim-address", (uintptr_t)parent_address);
	command_fd = parse_descriptor(command_descriptor_text);
	result_fd = parse_descriptor(result_descriptor_text);
	if (command_fd < 0 || result_fd < 0)
		return fail("victim-descriptor", 0);
	(void)snprintf(ready, sizeof(ready), "READY %08" PRIxPTR "\n",
		       (uintptr_t)&marker);
	if (write_all(result_fd, ready, strlen(ready)) != 0 ||
	    read_line(command_fd, command, sizeof(command)) != 0 ||
	    strcmp(command, "GO\n") != 0)
		return fail("victim-go", 0);
	if (expect_read_fault((uintptr_t)parent_address) != 0)
		return fail("victim-read-parent", (uintptr_t)parent_address);
	if (marker != UINT32_C(0x4d374152))
		return fail("victim-marker", marker);
	if (write_all(result_fd, "CHILD-PASS\n", 11U) != 0 ||
	    read_line(command_fd, command, sizeof(command)) != 0 ||
	    strcmp(command, "DONE\n") != 0)
		return fail("victim-done", 0);
	if (close(result_fd) != 0 || close(command_fd) != 0)
		return fail("victim-close", 0);
	return 0;
}

static int spawn_victim(const char *program, uintptr_t parent_address,
			uintptr_t *child_address, pid_t *child_pid,
			int *command_fd, int *result_fd)
{
	char parent_text[16];
	char command_descriptor_text[16];
	char result_descriptor_text[16];
	char line[64];
	char *end = NULL;
	char *arguments[] = {
		(char *)program,
		(char *)"--victim",
		parent_text,
		command_descriptor_text,
		result_descriptor_text,
		NULL,
	};
	char *environment[] = { (char *)"PATH=/bin:/usr/bin", NULL };
	int to_child[2];
	int from_child[2];
	int spawn_result;
	unsigned long parsed;

	if (pipe(to_child) != 0)
		return -1;
	if (pipe(from_child) != 0) {
		(void)close(to_child[0]);
		(void)close(to_child[1]);
		return -1;
	}
	if (mark_close_on_exec(to_child[1]) != 0 ||
	    mark_close_on_exec(from_child[0]) != 0) {
		(void)close(to_child[0]);
		(void)close(to_child[1]);
		(void)close(from_child[0]);
		(void)close(from_child[1]);
		return -1;
	}
	(void)snprintf(parent_text, sizeof(parent_text), "%08" PRIxPTR,
		       parent_address);
	(void)snprintf(command_descriptor_text,
		       sizeof(command_descriptor_text), "%d", to_child[0]);
	(void)snprintf(result_descriptor_text, sizeof(result_descriptor_text),
		       "%d", from_child[1]);
	spawn_result = posix_spawn(child_pid, program, NULL, NULL,
				   arguments, environment);
	(void)close(to_child[0]);
	(void)close(from_child[1]);
	if (spawn_result != 0) {
		errno = spawn_result;
		(void)close(to_child[1]);
		(void)close(from_child[0]);
		return -1;
	}
	if (read_line(from_child[0], line, sizeof(line)) != 0 ||
	    strncmp(line, "READY ", 6U) != 0) {
		(void)close(to_child[1]);
		(void)close(from_child[0]);
		return -1;
	}
	errno = 0;
	parsed = strtoul(line + 6, &end, 16);
	if (errno != 0 || end == line + 6 ||
	    (*end != '\n' && *end != '\0') ||
	    !address_in_pool((uintptr_t)parsed)) {
		(void)close(to_child[1]);
		(void)close(from_child[0]);
		return -1;
	}
	*child_address = (uintptr_t)parsed;
	*command_fd = to_child[1];
	*result_fd = from_child[0];
	return 0;
}

static int test_sibling_isolation(const char *program)
{
	volatile uint32_t marker = UINT32_C(0x50415245);
	uintptr_t child_address;
	pid_t child;
	char line[64];
	int command_fd;
	int result_fd;
	int status;

	if (spawn_victim(program, (uintptr_t)&marker, &child_address, &child,
			 &command_fd, &result_fd) != 0)
		return fail("spawn-victim", 0);
	if (child_address == (uintptr_t)&marker ||
	    expect_read_fault(child_address) != 0)
		return fail("parent-read-child", child_address);
	if (waitpid(child, &status, WNOHANG) != 0)
		return fail("victim-liveness", (uintptr_t)status);
	if (write_all(command_fd, "GO\n", 3U) != 0 ||
	    read_line(result_fd, line, sizeof(line)) != 0 ||
	    strcmp(line, "CHILD-PASS\n") != 0)
		return fail("victim-result", child_address);
	if (write_all(command_fd, "DONE\n", 5U) != 0 ||
	    close(command_fd) != 0 || close(result_fd) != 0 ||
	    wait_success(child) != 0)
		return fail("victim-exit", child_address);
	if (marker != UINT32_C(0x50415245))
		return fail("parent-marker", marker);
	printf("MICRONUX:M7:ARENA:SIBLING:PASS parent=%08" PRIxPTR
	       " child=%08" PRIxPTR " signal=%d both=live\n",
	       (uintptr_t)&marker, child_address, SIGSEGV);
	return 0;
}

static int reuse_child(int writing, const char *result_descriptor_text)
{
	unsigned char *mapping;
	char result[32];
	size_t offset;
	int result_fd;

	result_fd = parse_descriptor(result_descriptor_text);
	if (result_fd < 0)
		return fail("reuse-descriptor", 0);

	mapping = mmap(NULL, TEST_PAGE_BYTES, PROT_READ | PROT_WRITE,
		       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (mapping == MAP_FAILED)
		return fail("reuse-mmap", TEST_PAGE_BYTES);
	for (offset = 0; offset < TEST_PAGE_BYTES; ++offset) {
		if (mapping[offset] != 0U)
			return fail("reuse-not-zero", (uintptr_t)&mapping[offset]);
	}
	if (writing)
		memset(mapping, 0xa5, TEST_PAGE_BYTES);
	(void)snprintf(result, sizeof(result), "REUSE %08" PRIxPTR "\n",
		       (uintptr_t)mapping);
	if (write_all(result_fd, result, strlen(result)) != 0 ||
	    close(result_fd) != 0)
		return fail("reuse-result", (uintptr_t)mapping);
	return 0;
}

static int spawn_reuse_child(const char *program, int writing,
			     uintptr_t *address, pid_t *pid)
{
	char result_descriptor_text[16];
	char line[64];
	char *end = NULL;
	char *arguments[] = {
		(char *)program,
		(char *)(writing ? "--reuse-write" : "--reuse-check"),
		result_descriptor_text,
		NULL,
	};
	char *environment[] = { (char *)"PATH=/bin:/usr/bin", NULL };
	int output[2];
	int result;
	unsigned long parsed;

	if (pipe(output) != 0)
		return -1;
	if (mark_close_on_exec(output[0]) != 0) {
		(void)close(output[0]);
		(void)close(output[1]);
		return -1;
	}
	(void)snprintf(result_descriptor_text, sizeof(result_descriptor_text),
		       "%d", output[1]);
	result = posix_spawn(pid, program, NULL, NULL, arguments,
			     environment);
	(void)close(output[1]);
	if (result != 0) {
		errno = result;
		(void)close(output[0]);
		return -1;
	}
	if (read_line(output[0], line, sizeof(line)) != 0 ||
	    close(output[0]) != 0 || wait_success(*pid) != 0 ||
	    strncmp(line, "REUSE ", 6U) != 0)
		return -1;
	errno = 0;
	parsed = strtoul(line + 6, &end, 16);
	if (errno != 0 || end == line + 6 ||
	    (*end != '\n' && *end != '\0') ||
	    !address_in_pool((uintptr_t)parsed))
		return -1;
	*address = (uintptr_t)parsed;
	return 0;
}

static int test_reuse(const char *program)
{
	uintptr_t expected = 0;
	pid_t first_pid = -1;
	pid_t last_pid = -1;
	unsigned int cycle;

	for (cycle = 0; cycle < REUSE_CYCLES; ++cycle) {
		uintptr_t written;
		uintptr_t checked;
		pid_t writer;
		pid_t checker;

		if (spawn_reuse_child(program, 1, &written, &writer) != 0 ||
		    spawn_reuse_child(program, 0, &checked, &checker) != 0)
			return fail("reuse-spawn", cycle);
		if (cycle == 0U) {
			expected = written;
			first_pid = writer;
		}
		last_pid = checker;
		if (written != expected || checked != expected)
			return fail("reuse-address", checked);
	}
	printf("MICRONUX:M7:ARENA:REUSE:PASS cycles=%u address=%08" PRIxPTR
	       " zero=pass first_pid=%ld last_pid=%ld\n",
	       REUSE_CYCLES, expected, (long)first_pid, (long)last_pid);
	return 0;
}

static uint32_t read_be32(const unsigned char *bytes)
{
	return (uint32_t)bytes[0] << 24 | (uint32_t)bytes[1] << 16 |
	       (uint32_t)bytes[2] << 8 | (uint32_t)bytes[3];
}

static int create_bad_flat(const char *path)
{
	unsigned char header[44];
	unsigned char buffer[4096];
	static const unsigned char bad_reloc[4] = { 0xff, 0xff, 0xff, 0xff };
	uint32_t reloc_start;
	uint32_t reloc_count;
	ssize_t received;
	int source;
	int target;

	source = open("/proc/self/exe", O_RDONLY);
	if (source < 0)
		return -1;
	if (read(source, header, sizeof(header)) != (ssize_t)sizeof(header) ||
	    memcmp(header, "bFLT", 4U) != 0) {
		(void)close(source);
		errno = ENOEXEC;
		return -1;
	}
	reloc_start = read_be32(header + FLAT_RELOC_START_OFFSET);
	reloc_count = read_be32(header + FLAT_RELOC_COUNT_OFFSET);
	if (reloc_count == 0U || lseek(source, 0, SEEK_SET) != 0) {
		(void)close(source);
		errno = ENOEXEC;
		return -1;
	}
	target = open(path, O_CREAT | O_TRUNC | O_WRONLY, 0700);
	if (target < 0) {
		(void)close(source);
		return -1;
	}
	while ((received = read(source, buffer, sizeof(buffer))) > 0) {
		if (write_all(target, buffer, (size_t)received) != 0) {
			(void)close(source);
			(void)close(target);
			return -1;
		}
	}
	if (received < 0 || lseek(target, reloc_start, SEEK_SET) < 0 ||
	    write_all(target, bad_reloc, sizeof(bad_reloc)) != 0 ||
	    fchmod(target, 0700) != 0 || close(source) != 0 ||
	    close(target) != 0)
		return -1;
	return 0;
}

static int read_pool_state(struct pool_state *state)
{
	char line[256];
	FILE *pool;

	pool = fopen("/proc/micronux_user_pool", "r");
	if (!pool)
		return -1;
	if (!fgets(line, sizeof(line), pool) || fclose(pool) != 0)
		return -1;
	if (sscanf(line,
		   "MICRONUX:M7:POOL range=[%*x,%*x) pages=%lu reserved=%lu mapped=%lu free=%lu arenas=%lu",
		   &state->pages, &state->reserved, &state->mapped,
		   &state->free, &state->arenas) != 5)
		return -1;
	return 0;
}

static int same_pool_state(const struct pool_state *left,
			   const struct pool_state *right)
{
	return left->pages == right->pages &&
	       left->reserved == right->reserved &&
	       left->mapped == right->mapped && left->free == right->free &&
	       left->arenas == right->arenas;
}

static int test_exec_failure(void)
{
	static const char bad_path[] = "/tmp/micronux-arena-bad";
	struct pool_state before;
	struct pool_state after;
	char *arguments[] = { (char *)bad_path, NULL };
	char *environment[] = { (char *)"PATH=/bin:/usr/bin", NULL };
	pid_t child = -1;
	int result;

	(void)unlink(bad_path);
	if (create_bad_flat(bad_path) != 0 || read_pool_state(&before) != 0)
		return fail("bad-flat-create", 0);
	result = posix_spawn(&child, bad_path, NULL, NULL, arguments,
			     environment);
	if (result == 0) {
		int status;
		pid_t waited;

		do {
			waited = waitpid(child, &status, 0);
		} while (waited < 0 && errno == EINTR);
		if (waited != child ||
		    (WIFEXITED(status) && WEXITSTATUS(status) == 0))
			return fail("bad-flat-exec", (uintptr_t)status);
	} else if (result != ENOEXEC && result != EFAULT) {
		errno = result;
		return fail("bad-flat-spawn", (uintptr_t)result);
	}
	if (unlink(bad_path) != 0 || read_pool_state(&after) != 0 ||
	    !same_pool_state(&before, &after))
		return fail("bad-flat-accounting", after.reserved);
	printf("MICRONUX:M7:ARENA:EXEC-FAIL:PASS result=%d reserved=%lu mapped=%lu arenas=%lu\n",
	       result, after.reserved, after.mapped, after.arenas);
	return 0;
}

int main(int argc, char **argv)
{
	setvbuf(stdout, NULL, _IONBF, 0);
	if (argc == 5 && strcmp(argv[1], "--victim") == 0)
		return victim_main(argv[2], argv[3], argv[4]);
	if (argc == 3 && strcmp(argv[1], "--reuse-write") == 0)
		return reuse_child(1, argv[2]);
	if (argc == 3 && strcmp(argv[1], "--reuse-check") == 0)
		return reuse_child(0, argv[2]);
	if (argc != 1)
		return fail("arguments", (uintptr_t)argc);
	if (test_sibling_isolation(argv[0]) != 0 || test_reuse(argv[0]) != 0 ||
	    test_exec_failure() != 0)
		return 1;
	puts("MICRONUX:M7:ARENA:PASS ownership=per-mm vfork=pass signals=pass");
	return 0;
}
