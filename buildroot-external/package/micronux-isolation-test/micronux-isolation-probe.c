// SPDX-License-Identifier: MIT

#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define MICRONUX_USER_START ((uintptr_t)UINT32_C(0x49700000))
#define MICRONUX_USER_END ((uintptr_t)UINT32_C(0x49f00000))
#define PROBE_BYTES ((size_t)1024U * 1024U)

static int fail(const char *stage, uintptr_t detail)
{
	const int saved_errno = errno;

	printf("MICRONUX:M7:POOL-PROBE:FAIL stage=%s detail=%08" PRIxPTR
	       " errno=%d\n", stage, detail, saved_errno);
	return 1;
}

static int range_in_pool(uintptr_t start, uintptr_t end)
{
	return start >= MICRONUX_USER_START && start < MICRONUX_USER_END &&
	       end > start && end <= MICRONUX_USER_END;
}

static int verify_maps(unsigned int *count)
{
	char line[256];
	FILE *maps;

	*count = 0;
	maps = fopen("/proc/self/maps", "r");
	if (maps == NULL)
		return fail("maps-open", 0);

	while (fgets(line, sizeof(line), maps) != NULL) {
		uintptr_t start;
		uintptr_t end;

		if (sscanf(line, "%" SCNxPTR "-%" SCNxPTR, &start, &end) != 2)
			continue;
		if (!range_in_pool(start, end)) {
			(void)fclose(maps);
			return fail("vma-range", start);
		}
		++*count;
	}
	if (ferror(maps)) {
		(void)fclose(maps);
		return fail("maps-read", *count);
	}
	if (fclose(maps) != 0)
		return fail("maps-close", *count);
	if (*count == 0U)
		return fail("maps-empty", 0);
	return 0;
}

static int print_accounting(void)
{
	char line[192];
	FILE *pool;

	pool = fopen("/proc/micronux_user_pool", "r");
	if (pool == NULL)
		return fail("pool-open", 0);
	if (fgets(line, sizeof(line), pool) == NULL) {
		(void)fclose(pool);
		return fail("pool-read", 0);
	}
	if (fclose(pool) != 0)
		return fail("pool-close", 0);
	if (strncmp(line, "MICRONUX:M7:POOL ",
		    sizeof("MICRONUX:M7:POOL ") - 1U) != 0)
		return fail("pool-marker", 0);
	fputs(line, stdout);
	return 0;
}

int main(int argc, char **argv)
{
	volatile uint32_t stack_marker = UINT32_C(0x4d375550);
	unsigned char *mapping;
	unsigned int vma_count;
	size_t offset;

	(void)argv;
	setvbuf(stdout, NULL, _IONBF, 0);
	if (argc != 1)
		return fail("arguments", (uintptr_t)argc);
	if (!range_in_pool((uintptr_t)&stack_marker,
			   (uintptr_t)&stack_marker + sizeof(stack_marker)))
		return fail("stack", (uintptr_t)&stack_marker);

	mapping = mmap(NULL, PROBE_BYTES, PROT_READ | PROT_WRITE,
		       MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (mapping == MAP_FAILED)
		return fail("mmap", PROBE_BYTES);
	if (!range_in_pool((uintptr_t)mapping,
			   (uintptr_t)mapping + PROBE_BYTES))
		return fail("mmap-range", (uintptr_t)mapping);
	for (offset = 0; offset < PROBE_BYTES; ++offset) {
		if (mapping[offset] != 0U)
			return fail("mmap-zero", (uintptr_t)&mapping[offset]);
	}
	memset(mapping, 0xa5, PROBE_BYTES);

	if (verify_maps(&vma_count) != 0 || print_accounting() != 0)
		return 1;
	if (munmap(mapping, PROBE_BYTES) != 0)
		return fail("munmap", (uintptr_t)mapping);

	printf("MICRONUX:M7:POOL-PROBE:PASS vmas=%u mmap=%08" PRIxPTR
	       " bytes=%zu stack=%08" PRIxPTR "\n",
	       vma_count, (uintptr_t)mapping, PROBE_BYTES,
	       (uintptr_t)&stack_marker);
	return 0;
}
