// SPDX-License-Identifier: MIT
#pragma once

#include <stddef.h>
#include <stdint.h>

#define MICRONUX_PAYLOAD_MAGIC UINT32_C(0x33584E4D)
#define MICRONUX_PAYLOAD_ABI_VERSION UINT32_C(1)
#define MICRONUX_PAYLOAD_FLAG_DTB_PRESENT (UINT32_C(1) << 0)

typedef struct {
    uint32_t magic;
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t flags;

    uint32_t kernel_load_vaddr;
    uint32_t kernel_file_size;
    uint32_t kernel_memory_size;
    uint32_t dtb_size;

    uint8_t kernel_sha256[32];
    uint8_t dtb_sha256[32];
    uint32_t reserved[7];
    uint32_t crc32;
} micronux_payload_v1_t;

_Static_assert(sizeof(micronux_payload_v1_t) == 128,
               "M3 payload manifest ABI changed");
_Static_assert(offsetof(micronux_payload_v1_t, crc32) == 124,
               "M3 payload manifest CRC coverage changed");
