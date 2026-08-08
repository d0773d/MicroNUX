// SPDX-License-Identifier: MIT

#include <errno.h>
#include <inttypes.h>
#include <signal.h>
#include <spawn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define EXEC_ITERATIONS 64U
#define SIGNAL_ITERATIONS 64U
#define TIMER_ITERATIONS 32U
#define MEMORY_KIB 4096U
#define MEMORY_BLOCKS 32U
#define STACK_DEPTH 8U
#define STACK_FRAME_BYTES 128U
#define CONSOLE_LINES 64U

#define MICRONUX_MEMORY_START ((uintptr_t)UINT32_C(0x48400000))
#define MICRONUX_MEMORY_END ((uintptr_t)UINT32_C(0x49f00000))

static volatile sig_atomic_t signal_count;
static volatile sig_atomic_t timer_count;

static int fail(const char *stage, unsigned long detail)
{
	const int saved_errno = errno;

	printf("MICRONUX:M5:FAIL stage=%s detail=%lu errno=%d\n",
	       stage, detail, saved_errno);
	return 1;
}

static int address_in_linux_memory(const void *pointer, size_t size)
{
	const uintptr_t start = (uintptr_t)pointer;

	return start >= MICRONUX_MEMORY_START &&
	       start < MICRONUX_MEMORY_END &&
	       size <= MICRONUX_MEMORY_END - start;
}

static void signal_handler(int signal_number)
{
	(void)signal_number;
	++signal_count;
}

static void timer_handler(int signal_number)
{
	(void)signal_number;
	++timer_count;
}

static int install_handler(int signal_number, void (*handler)(int))
{
	struct sigaction action;

	memset(&action, 0, sizeof(action));
	action.sa_handler = handler;
	if (sigemptyset(&action.sa_mask) != 0)
		return -1;
	return sigaction(signal_number, &action, NULL);
}

static int test_signal_returns(void)
{
	unsigned int iteration;

	if (install_handler(SIGUSR1, signal_handler) != 0)
		return fail("sigaction", SIGUSR1);

	signal_count = 0;
	for (iteration = 0; iteration < SIGNAL_ITERATIONS; ++iteration) {
		if (raise(SIGUSR1) != 0)
			return fail("raise", iteration);
		if ((unsigned int)signal_count != iteration + 1U)
			return fail("signal-count", iteration);
	}

	printf("MICRONUX:M5:SIGNALS pass count=%u\n", SIGNAL_ITERATIONS);
	return 0;
}

static int test_interval_timer(void)
{
	struct itimerval timer;
	struct itimerval stopped;

	if (install_handler(SIGALRM, timer_handler) != 0)
		return fail("sigaction", SIGALRM);

	memset(&timer, 0, sizeof(timer));
	timer.it_value.tv_usec = 5000;
	timer.it_interval.tv_usec = 5000;
	timer_count = 0;
	if (setitimer(ITIMER_REAL, &timer, NULL) != 0)
		return fail("setitimer-start", 0);

	while ((unsigned int)timer_count < TIMER_ITERATIONS)
		(void)pause();

	memset(&stopped, 0, sizeof(stopped));
	if (setitimer(ITIMER_REAL, &stopped, NULL) != 0)
		return fail("setitimer-stop", 0);

	printf("MICRONUX:M5:TIMERS pass count=%u\n", TIMER_ITERATIONS);
	return 0;
}

static int test_exec_lifecycle(void)
{
	static const char child_program[] = "/usr/bin/micronux-exec-child";
	unsigned int iteration;

	for (iteration = 0; iteration < EXEC_ITERATIONS; ++iteration) {
		char *const arguments[] = {
			(char *)child_program,
			NULL,
		};
		char *const environment[] = {
			(char *)"PATH=/bin:/usr/bin",
			NULL,
		};
		pid_t child = -1;
		int status;
		pid_t waited;
		int spawn_result;

		spawn_result = posix_spawn(&child, child_program, NULL, NULL,
					   arguments, environment);
		if (spawn_result != 0) {
			errno = spawn_result;
			return fail("posix-spawn", iteration);
		}

		do {
			waited = waitpid(child, &status, 0);
		} while (waited < 0 && errno == EINTR);
		if (waited != child)
			return fail("waitpid", iteration);
		if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
			return fail("child-status", (unsigned long)status);

	}

	printf("MICRONUX:M5:EXEC pass count=%u\n", EXEC_ITERATIONS);
	return 0;
}

static int test_memory(void)
{
	unsigned char *blocks[MEMORY_BLOCKS];
	size_t sizes[MEMORY_BLOCKS];
	const size_t total_bytes = (size_t)MEMORY_KIB * 1024U;
	const size_t base_size = total_bytes / MEMORY_BLOCKS;
	unsigned int block;
	int result = 0;

	memset(blocks, 0, sizeof(blocks));
	for (block = 0; block < MEMORY_BLOCKS; ++block) {
		const unsigned char pattern = (unsigned char)(0x5aU + block * 37U);
		const size_t size = block == MEMORY_BLOCKS - 1U ?
			total_bytes - base_size * block : base_size;

		sizes[block] = size;
		blocks[block] = malloc(size);
		if (blocks[block] == NULL) {
			result = fail("malloc", block);
			break;
		}
		if (!address_in_linux_memory(blocks[block], size)) {
			result = fail("memory-range", block);
			break;
		}
		memset(blocks[block], pattern, size);
	}

	if (result == 0) {
		for (block = 0; block < MEMORY_BLOCKS; ++block) {
			const unsigned char pattern =
				(unsigned char)(0x5aU + block * 37U);
			size_t offset;

			for (offset = 0; offset < sizes[block]; ++offset) {
				if (blocks[block][offset] != pattern) {
					result = fail("memory-verify", block);
					break;
				}
			}
			if (result != 0)
				break;
		}
	}

	for (block = MEMORY_BLOCKS; block > 0; --block)
		free(blocks[block - 1U]);
	if (result != 0)
		return result;

	printf("MICRONUX:M5:MEMORY pass kib=%u blocks=%u\n",
	       MEMORY_KIB, MEMORY_BLOCKS);
	return 0;
}

__attribute__((noinline))
static int stack_canary(unsigned int depth, uint32_t seed)
{
	volatile uint32_t head = seed ^ UINT32_C(0xa5a55a5a);
	volatile unsigned char frame[STACK_FRAME_BYTES];
	volatile uint32_t tail = seed ^ UINT32_C(0x5a5aa5a5);
	unsigned int index;
	int result = 0;

	for (index = 0; index < STACK_FRAME_BYTES; ++index)
		frame[index] = (unsigned char)(seed + index);
	if (depth > 0U)
		result = stack_canary(depth - 1U, seed + UINT32_C(0x1020304));
	for (index = 0; index < STACK_FRAME_BYTES; ++index) {
		if (frame[index] != (unsigned char)(seed + index))
			result = -1;
	}
	if (head != (seed ^ UINT32_C(0xa5a55a5a)) ||
	    tail != (seed ^ UINT32_C(0x5a5aa5a5)))
		result = -1;
	return result;
}

static int test_stack(void)
{
	volatile uint32_t marker = UINT32_C(0xc001d00d);

	if (!address_in_linux_memory((const void *)&marker, sizeof(marker)))
		return fail("stack-range", (unsigned long)(uintptr_t)&marker);
	if (stack_canary(STACK_DEPTH, UINT32_C(0x13579bdf)) != 0)
		return fail("stack-canary", STACK_DEPTH);
	if (marker != UINT32_C(0xc001d00d))
		return fail("stack-marker", marker);

	printf("MICRONUX:M5:STACK pass depth=%u frame_bytes=%u\n",
	       STACK_DEPTH, STACK_FRAME_BYTES);
	return 0;
}

static void test_console(void)
{
	uint32_t checksum = UINT32_C(0x6d696372);
	unsigned int line;

	for (line = 0; line < CONSOLE_LINES; ++line) {
		checksum ^= checksum << 13;
		checksum ^= checksum >> 17;
		checksum ^= checksum << 5;
		printf("MICRONUX:M5:CONSOLE seq=%u checksum=%08" PRIx32 "\n",
		       line, checksum);
	}
}

static uint64_t elapsed_milliseconds(const struct timespec *start,
				     const struct timespec *end)
{
	const int64_t seconds = end->tv_sec - start->tv_sec;
	const int64_t nanoseconds = end->tv_nsec - start->tv_nsec;

	return (uint64_t)(seconds * 1000 + nanoseconds / 1000000);
}

int main(int argc, char **argv)
{
	struct timespec start;
	struct timespec end;

	(void)argv;

	if (argc != 1)
		return fail("arguments", (unsigned long)argc);

	setvbuf(stdout, NULL, _IONBF, 0);
	printf("MICRONUX:M5:SELFTEST begin exec=%u signals=%u timers=%u "
	       "memory_kib=%u stack_depth=%u console=%u\n",
	       EXEC_ITERATIONS, SIGNAL_ITERATIONS, TIMER_ITERATIONS,
	       MEMORY_KIB, STACK_DEPTH, CONSOLE_LINES);
	if (clock_gettime(CLOCK_MONOTONIC, &start) != 0)
		return fail("clock-start", 0);
	if (test_signal_returns() != 0 ||
	    test_interval_timer() != 0 ||
	    test_exec_lifecycle() != 0 ||
	    test_memory() != 0 ||
	    test_stack() != 0)
		return 1;
	test_console();
	if (clock_gettime(CLOCK_MONOTONIC, &end) != 0)
		return fail("clock-end", 0);

	printf("MICRONUX:M5:PASS exec=%u signals=%u timers=%u memory_kib=%u "
	       "stack_depth=%u console=%u elapsed_ms=%" PRIu64 "\n",
	       EXEC_ITERATIONS, SIGNAL_ITERATIONS, TIMER_ITERATIONS,
	       MEMORY_KIB, STACK_DEPTH, CONSOLE_LINES,
	       elapsed_milliseconds(&start, &end));
	return 0;
}
