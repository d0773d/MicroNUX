/* SPDX-License-Identifier: MIT */

#ifndef MICRONUX_DEVICE_PROTOCOL_H
#define MICRONUX_DEVICE_PROTOCOL_H

#include <stdint.h>

#include "micronux/device.h"

#define MICRONUX_DEVICE_MAGIC UINT32_C(0x584e554d)
#define MICRONUX_DEVICE_MAX_WAIT_MS UINT32_C(300000)

enum micronux_device_operation {
	MICRONUX_DEVICE_OP_INFO = 1,
	MICRONUX_DEVICE_OP_LIST = 2,
	MICRONUX_DEVICE_OP_WAIT = 3,
	MICRONUX_DEVICE_OP_ADMIN_PROBE = 4,
};

enum micronux_device_wire_status {
	MICRONUX_DEVICE_STATUS_OK = 0,
	MICRONUX_DEVICE_STATUS_VERSION = -1,
	MICRONUX_DEVICE_STATUS_DENIED = -2,
	MICRONUX_DEVICE_STATUS_INVALID = -3,
	MICRONUX_DEVICE_STATUS_TIMEOUT = -4,
	MICRONUX_DEVICE_STATUS_IO = -5,
	MICRONUX_DEVICE_STATUS_BUSY = -6,
};

struct micronux_device_request {
	uint32_t magic;
	uint16_t abi_major;
	uint16_t abi_minor;
	uint32_t request_id;
	uint32_t operation;
	uint32_t value;
	uint32_t timeout_ms;
};

struct micronux_device_response {
	uint32_t magic;
	uint16_t abi_major;
	uint16_t abi_minor;
	uint32_t request_id;
	int32_t status;
	uint32_t capabilities;
	uint32_t devices;
	uint32_t value;
};

_Static_assert(sizeof(struct micronux_device_request) == 24,
	"MicroNUX device request ABI changed");
_Static_assert(sizeof(struct micronux_device_response) == 28,
	"MicroNUX device response ABI changed");

#endif
