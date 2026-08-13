/* SPDX-License-Identifier: MIT */

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <unistd.h>

#include "micronux/device.h"

#define UNPRIVILEGED_UID 65534

int main(void)
{
	struct micronux_device_info info;
	uint32_t devices;

	if (getuid() != 0) {
		fprintf(stderr, "micronux-device-selftest: must start as root\n");
		return 1;
	}
	if (micronux_device_get_info(&info) < 0 ||
		info.abi_major != MICRONUX_DEVICE_ABI_MAJOR ||
		(info.capabilities & MICRONUX_DEVICE_CAP_ADMIN) == 0 ||
		micronux_device_admin_probe() < 0 ||
		micronux_device_list(&devices) < 0) {
		perror("micronux-device-selftest: root contract");
		return 1;
	}
	if (setuid(UNPRIVILEGED_UID) < 0) {
		perror("micronux-device-selftest: setuid");
		return 1;
	}
	if (micronux_device_get_info(&info) < 0 ||
		(info.capabilities & MICRONUX_DEVICE_CAP_OBSERVE) == 0 ||
		(info.capabilities & MICRONUX_DEVICE_CAP_ADMIN) != 0 ||
		micronux_device_list(&devices) < 0) {
		perror("micronux-device-selftest: observer contract");
		return 1;
	}
	errno = 0;
	if (micronux_device_admin_probe() == 0 || errno != EACCES) {
		fprintf(stderr,
			"micronux-device-selftest: admin request did not fail closed\n");
		return 1;
	}
	printf("MICRONUX:M8:PERMISSION:PASS uid=%u observe=allowed "
		"admin=denied devices=0x%08lx\n", UNPRIVILEGED_UID,
		(unsigned long)devices);
	return 0;
}
