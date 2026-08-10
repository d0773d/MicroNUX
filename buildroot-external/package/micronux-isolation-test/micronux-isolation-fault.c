// SPDX-License-Identifier: MIT

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <signal.h>
#include <spawn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

#define MICRONUX_USER_END ((uintptr_t)UINT32_C(0x49f00000))

struct fault_case {
	const char *name;
	const char *operation;
	uintptr_t address;
	int signal_number;
};

struct pointer_case {
	const char *name;
	uintptr_t address;
	size_t size;
};

static const struct fault_case fault_cases[] = {
	{ "privilege-csr", "csr", 0, SIGILL },
	{ "clint-read", "read", UINT32_C(0x02000000), SIGSEGV },
	{ "mmio-read", "read", UINT32_C(0x50000000), SIGSEGV },
	{ "loader-direct-read", "read", UINT32_C(0x88000000), SIGSEGV },
	{ "loader-direct-write", "write", UINT32_C(0x88000000), SIGSEGV },
	{ "loader-direct-exec", "exec", UINT32_C(0x88000000), SIGSEGV },
	{ "loader-read", "read", UINT32_C(0x48000000), SIGSEGV },
	{ "kernel-read", "read", UINT32_C(0x48400000), SIGSEGV },
	{ "kernel-write", "write", UINT32_C(0x48400000), SIGSEGV },
	{ "kernel-exec", "exec", UINT32_C(0x48400000), SIGSEGV },
	{ "loader-write", "write", UINT32_C(0x48000000), SIGSEGV },
	{ "loader-exec", "exec", UINT32_C(0x48000000), SIGSEGV },
	{ "clint-write", "write", UINT32_C(0x02000000), SIGSEGV },
	{ "clint-exec", "exec", UINT32_C(0x02000000), SIGSEGV },
	{ "mmio-write", "write", UINT32_C(0x50000000), SIGSEGV },
	{ "mmio-exec", "exec", UINT32_C(0x50000000), SIGSEGV },
	{ "text-write", "text-write", 0, SIGSEGV },
	{ "data-exec", "data-exec", 0, SIGSEGV },
	{ "stack-exec", "stack-exec", 0, SIGSEGV },
};

static volatile uint32_t data_instruction __attribute__((aligned(4))) =
	UINT32_C(0x00008067); /* ret */
static volatile sig_atomic_t signal_returned;

static const struct pointer_case pointer_cases[] = {
	{ "kernel", UINT32_C(0x48400000), 1U },
	{ "loader", UINT32_C(0x48000000), 1U },
	{ "clint", UINT32_C(0x02000000), 1U },
	{ "mmio", UINT32_C(0x50000000), 1U },
	{ "below-pool", UINT32_C(0x496fffff), 1U },
	{ "cross-pool-end", UINT32_C(0x49efffff), 2U },
	{ "overflow", UINT32_C(0xfffffffe), 4U },
};

static int fail(const char *stage, const char *name, unsigned long detail)
{
	const int saved_errno = errno;

	printf("MICRONUX:M7:ISOLATION-FAULT:FAIL stage=%s case=%s "
	       "detail=%lu errno=%d\n", stage, name, detail, saved_errno);
	return 1;
}

__attribute__((noinline, used))
static void text_write_target(void)
{
	__asm__ __volatile__("" ::: "memory");
}

static void signal_handler(int signal_number)
{
	if (signal_number == SIGUSR1)
		signal_returned = 1;
}

__attribute__((noinline, noreturn))
static void perform_fault(const char *operation, uintptr_t address)
{
	volatile uint32_t *const pointer = (volatile uint32_t *)address;

	if (strcmp(operation, "csr") == 0) {
		unsigned long status;

		__asm__ __volatile__("csrr %0, mstatus" : "=r"(status));
		(void)status;
	} else if (strcmp(operation, "read") == 0) {
		volatile uint32_t value = *pointer;

		(void)value;
	} else if (strcmp(operation, "write") == 0) {
		*pointer = UINT32_C(0x4d375750);
	} else if (strcmp(operation, "exec") == 0) {
		void (*const target)(void) = (void (*)(void))address;

		target();
	} else if (strcmp(operation, "text-write") == 0) {
		volatile uint32_t *const target =
			(volatile uint32_t *)(uintptr_t)&text_write_target;

		*target = UINT32_C(0x00008067);
	} else if (strcmp(operation, "data-exec") == 0) {
		void (*const target)(void) =
			(void (*)(void))(uintptr_t)&data_instruction;

		target();
	} else if (strcmp(operation, "stack-exec") == 0) {
		volatile uint32_t stack_instruction __attribute__((aligned(4))) =
			UINT32_C(0x00008067);
		void (*const target)(void) =
			(void (*)(void))(uintptr_t)&stack_instruction;

		target();
	} else {
		_exit(64);
	}

	/* Reaching here means the protection boundary did not fault. */
	_exit(77);
}

static int child_main(const char *operation, const char *address_text)
{
	char *end = NULL;
	unsigned long parsed;

	errno = 0;
	parsed = strtoul(address_text, &end, 16);
	if (errno != 0 || end == address_text || *end != '\0' ||
	    parsed > UINT32_MAX)
		return fail("child-address", operation, parsed);
	perform_fault(operation, (uintptr_t)parsed);
}

static int test_faults(const char *program)
{
	char *const environment[] = { (char *)"PATH=/bin:/usr/bin", NULL };
	size_t index;

	for (index = 0; index < sizeof(fault_cases) / sizeof(fault_cases[0]);
	     ++index) {
		const struct fault_case *const test = &fault_cases[index];
		char address[16];
		char *arguments[] = {
			(char *)program,
			(char *)"--child",
			(char *)test->operation,
			address,
			NULL,
		};
		pid_t child = -1;
		pid_t waited;
		int spawn_result;
		int status;

		(void)snprintf(address, sizeof(address), "%08" PRIxPTR,
			       test->address);
		spawn_result = posix_spawn(&child, program, NULL, NULL, arguments,
					 environment);
		if (spawn_result != 0) {
			errno = spawn_result;
			return fail("spawn", test->name, index);
		}
		do {
			waited = waitpid(child, &status, 0);
		} while (waited < 0 && errno == EINTR);
		if (waited != child)
			return fail("wait", test->name, status);
		if (!WIFSIGNALED(status) ||
		    WTERMSIG(status) != test->signal_number)
			return fail("signal", test->name, status);
		printf("MICRONUX:M7:FAULT case=%s signal=%d\n", test->name,
		       WTERMSIG(status));
	}

	printf("MICRONUX:M7:FAULTS pass count=%zu privilege=U memory_signal=%d\n",
	       sizeof(fault_cases) / sizeof(fault_cases[0]), SIGSEGV);
	return 0;
}

static int expect_efault(int descriptor, int reading,
			 const struct pointer_case *test)
{
	void *const pointer = (void *)test->address;
	ssize_t result;

	errno = 0;
	if (reading)
		result = read(descriptor, pointer, test->size);
	else
		result = write(descriptor, pointer, test->size);
	if (result != -1 || errno != EFAULT)
		return fail(reading ? "read-uaccess" : "write-uaccess",
			    test->name, (unsigned long)result);
	return 0;
}

static int zero_length_read_allowed(int descriptor, uintptr_t address)
{
	ssize_t result;

	errno = 0;
	result = read(descriptor, (void *)address, 0);
	if (result == 0)
		return 1;
	if (result == -1 && errno == EFAULT)
		return 0;
	return -1;
}

static int test_uaccess(void)
{
	struct pointer_case cross_arena = { "cross-arena-hi", 0, 2U };
	struct pointer_case invalid_zero = { "zero-outside-arena", 0, 0U };
	unsigned char valid_byte = 0;
	uintptr_t current_hi;
	uintptr_t high;
	uintptr_t low;
	int sink;
	int source;
	size_t index;

	source = open("/dev/zero", O_RDONLY);
	if (source < 0)
		return fail("open", "zero", 0);
	sink = open("/dev/null", O_WRONLY);
	if (sink < 0) {
		(void)close(source);
		return fail("open", "null", 0);
	}

	for (index = 0;
	     index < sizeof(pointer_cases) / sizeof(pointer_cases[0]); ++index) {
		if (expect_efault(source, 1, &pointer_cases[index]) != 0 ||
		    expect_efault(sink, 0, &pointer_cases[index]) != 0) {
			(void)close(sink);
			(void)close(source);
			return 1;
		}
	}
	low = (uintptr_t)&valid_byte;
	high = MICRONUX_USER_END;
	if (low >= high || zero_length_read_allowed(source, low) != 1) {
		(void)close(sink);
		(void)close(source);
		return fail("zero-length", "valid-user", (uintptr_t)&valid_byte);
	}
	while (low < high) {
		const uintptr_t middle = low + (high - low + 1U) / 2U;
		const int allowed = zero_length_read_allowed(source, middle);

		if (allowed < 0) {
			(void)close(sink);
			(void)close(source);
			return fail("zero-length", "search", middle);
		}
		if (allowed != 0)
			low = middle;
		else
			high = middle - 1U;
	}
	current_hi = low;
	cross_arena.address = current_hi - 1U;
	if (expect_efault(source, 1, &cross_arena) != 0 ||
	    expect_efault(sink, 0, &cross_arena) != 0) {
		(void)close(sink);
		(void)close(source);
		return 1;
	}
	if (read(source, (void *)current_hi, 0) != 0 ||
	    write(sink, (void *)current_hi, 0) != 0) {
		(void)close(sink);
		(void)close(source);
		return fail("zero-length", "arena-hi", current_hi);
	}
	invalid_zero.address = current_hi + 1U;
	if (expect_efault(source, 1, &invalid_zero) != 0 ||
	    expect_efault(sink, 0, &invalid_zero) != 0) {
		(void)close(sink);
		(void)close(source);
		return 1;
	}
	if (close(sink) != 0 || close(source) != 0)
		return fail("close", "uaccess", 0);

	{
		static const char marker[] = "MICRONUX:M7:UACCESS:PASS\n";

		if (write(STDOUT_FILENO, marker, sizeof(marker) - 1) !=
		    (ssize_t)(sizeof(marker) - 1))
			return fail("marker", "uaccess", 0);
	}
	printf("MICRONUX:M7:UACCESS pass cases=%zu directions=2 overflow=pass "
	       "cross_hi=pass zero_hi=%08" PRIxPTR " zero_outside=pass\n",
	       sizeof(pointer_cases) / sizeof(pointer_cases[0]), current_hi);
	return 0;
}

static int test_wx(void)
{
	struct sigaction action;
	void *mapping;
	int sink;
	int source;
	ssize_t result;

	source = open("/dev/zero", O_RDONLY);
	if (source < 0)
		return fail("wx-open", "zero", 0);
	sink = open("/dev/null", O_WRONLY);
	if (sink < 0) {
		(void)close(source);
		return fail("wx-open", "null", 0);
	}

	errno = 0;
	result = read(source, (void *)(uintptr_t)&text_write_target, 1);
	if (result != -1 || errno != EFAULT) {
		(void)close(sink);
		(void)close(source);
		return fail("wx-code-copy", "read-text", (unsigned long)result);
	}
	result = write(sink, (const void *)(uintptr_t)&text_write_target, 1);
	if (result != 1) {
		(void)close(sink);
		(void)close(source);
		return fail("wx-code-read", "write-text", (unsigned long)result);
	}
	if (close(sink) != 0 || close(source) != 0)
		return fail("wx-close", "devices", 0);

	errno = 0;
	mapping = mmap(NULL, 4096, PROT_READ | PROT_WRITE | PROT_EXEC,
		       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (mapping != MAP_FAILED || errno != EACCES) {
		if (mapping != MAP_FAILED)
			(void)munmap(mapping, 4096);
		return fail("wx-mmap", "executable", (unsigned long)mapping);
	}

	memset(&action, 0, sizeof(action));
	action.sa_handler = signal_handler;
	if (sigemptyset(&action.sa_mask) != 0 ||
	    sigaction(SIGUSR1, &action, NULL) != 0 || raise(SIGUSR1) != 0 ||
	    signal_returned != 1)
		return fail("wx-signal", "fixed-trampoline", signal_returned);

	return 0;
}

int main(int argc, char **argv)
{
	setvbuf(stdout, NULL, _IONBF, 0);
	if (argc == 4 && strcmp(argv[1], "--child") == 0)
		return child_main(argv[2], argv[3]);
	if (argc != 1)
		return fail("arguments", "parent", argc);

	if (test_uaccess() != 0 || test_wx() != 0 || test_faults(argv[0]) != 0)
		return 1;
	puts("MICRONUX:M7:WX pass text_write=SIGSEGV data_exec=SIGSEGV "
	     "stack_exec=SIGSEGV signal=return mmap_exec=EACCES "
	     "code_copy=EFAULT");
	puts("MICRONUX:M7:ISOLATION-FAULT:PASS");
	return 0;
}
