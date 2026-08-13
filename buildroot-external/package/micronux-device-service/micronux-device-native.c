/* SPDX-License-Identifier: MIT */

#include <stdint.h>
#include <stdio.h>

#include "micronux/device.h"

int main(void)
{
	struct micronux_device_info info;
	uint32_t devices;

	if (micronux_device_get_info(&info) < 0 ||
		micronux_device_list(&devices) < 0) {
		perror("micronux-device-native");
		return 1;
	}
	printf("MICRONUX:M8:NATIVE:PASS abi=%u.%u capabilities=0x%08lx "
		"devices=0x%08lx\n", info.abi_major, info.abi_minor,
		(unsigned long)info.capabilities, (unsigned long)devices);
	return 0;
}
