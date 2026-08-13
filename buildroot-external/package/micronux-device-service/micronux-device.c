/* SPDX-License-Identifier: MIT */

#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "micronux/device.h"

static const char *state(bool ready)
{
	return ready ? "ready" : "missing";
}

static int error_exit(const char *operation)
{
	int saved_errno = errno;

	fprintf(stderr, "micronux-device: %s: %s\n", operation,
		strerror(saved_errno));
	if (saved_errno == EACCES)
		return 77;
	if (saved_errno == ETIMEDOUT)
		return 75;
	return 1;
}

static void print_devices(uint32_t devices, bool json)
{
	bool storage = (devices & MICRONUX_DEVICE_STORAGE_READY) != 0;
	bool network = (devices & MICRONUX_DEVICE_NETWORK_PRESENT) != 0;
	bool network_up = (devices & MICRONUX_DEVICE_NETWORK_UP) != 0;

	if (json) {
		printf("{\"abi\":\"%u.%u\",\"storage\":\"%s\","
			"\"network\":\"%s\",\"network_link\":\"%s\","
			"\"flags\":%lu}\n",
			MICRONUX_DEVICE_ABI_MAJOR, MICRONUX_DEVICE_ABI_MINOR,
			state(storage), network ? "present" : "missing",
			network_up ? "up" : "down", (unsigned long)devices);
	} else {
		printf("storage=%s network=%s network_link=%s flags=0x%08lx\n",
			state(storage), network ? "present" : "missing",
			network_up ? "up" : "down", (unsigned long)devices);
	}
}

static int parse_timeout(const char *text, uint32_t *timeout_ms)
{
	char *end;
	unsigned long value;

	errno = 0;
	value = strtoul(text, &end, 10);
	if (errno != 0 || *text == '\0' || *end != '\0' ||
		value == 0 || value > 300000) {
		errno = EINVAL;
		return -1;
	}
	*timeout_ms = (uint32_t)value;
	return 0;
}

static void usage(const char *program)
{
	fprintf(stderr,
		"usage: %s api\n"
		"       %s list [--json]\n"
		"       %s wait storage|network TIMEOUT_MS\n"
		"       %s admin-test\n",
		program, program, program, program);
}

int main(int argc, char **argv)
{
	struct micronux_device_info info;
	uint32_t devices;
	uint32_t timeout_ms;
	uint32_t mask;

	if (argc == 2 && strcmp(argv[1], "api") == 0) {
		if (micronux_device_get_info(&info) < 0)
			return error_exit("api");
		printf("abi=%u.%u capabilities=0x%08lx devices=0x%08lx\n",
			info.abi_major, info.abi_minor,
			(unsigned long)info.capabilities,
			(unsigned long)info.devices);
		return 0;
	}
	if ((argc == 2 || argc == 3) && strcmp(argv[1], "list") == 0) {
		bool json = argc == 3 && strcmp(argv[2], "--json") == 0;

		if (argc == 3 && !json) {
			usage(argv[0]);
			return 64;
		}
		if (micronux_device_list(&devices) < 0)
			return error_exit("list");
		print_devices(devices, json);
		return 0;
	}
	if (argc == 4 && strcmp(argv[1], "wait") == 0) {
		if (strcmp(argv[2], "storage") == 0)
			mask = MICRONUX_DEVICE_STORAGE_READY;
		else if (strcmp(argv[2], "network") == 0)
			mask = MICRONUX_DEVICE_NETWORK_UP;
		else {
			usage(argv[0]);
			return 64;
		}
		if (parse_timeout(argv[3], &timeout_ms) < 0)
			return error_exit("timeout");
		if (micronux_device_wait(mask, timeout_ms, &devices) < 0)
			return error_exit("wait");
		print_devices(devices, false);
		return 0;
	}
	if (argc == 2 && strcmp(argv[1], "admin-test") == 0) {
		if (micronux_device_admin_probe() < 0)
			return error_exit("admin-test");
		puts("admin=allowed");
		return 0;
	}
	usage(argv[0]);
	return 64;
}
