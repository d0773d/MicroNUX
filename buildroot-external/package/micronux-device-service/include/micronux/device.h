/* SPDX-License-Identifier: MIT */

#ifndef MICRONUX_DEVICE_H
#define MICRONUX_DEVICE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define MICRONUX_DEVICE_ABI_MAJOR UINT16_C(1)
#define MICRONUX_DEVICE_ABI_MINOR UINT16_C(0)
#define MICRONUX_DEVICE_SOCKET_PATH "/run/micronux/device-v1.sock"

enum micronux_device_capability {
	MICRONUX_DEVICE_CAP_OBSERVE = UINT32_C(1) << 0,
	MICRONUX_DEVICE_CAP_CONTROL = UINT32_C(1) << 1,
	MICRONUX_DEVICE_CAP_ADMIN = UINT32_C(1) << 2,
};

enum micronux_device_flag {
	MICRONUX_DEVICE_STORAGE_READY = UINT32_C(1) << 0,
	MICRONUX_DEVICE_NETWORK_PRESENT = UINT32_C(1) << 1,
	MICRONUX_DEVICE_NETWORK_UP = UINT32_C(1) << 2,
};

struct micronux_device_info {
	uint16_t abi_major;
	uint16_t abi_minor;
	uint32_t capabilities;
	uint32_t devices;
};

int micronux_device_get_info(struct micronux_device_info *info);
int micronux_device_list(uint32_t *devices);
int micronux_device_wait(uint32_t required_devices, uint32_t timeout_ms,
		uint32_t *devices);

/* Administrative policy probe used by the acceptance test. */
int micronux_device_admin_probe(void);

#ifdef __cplusplus
}
#endif

#endif
