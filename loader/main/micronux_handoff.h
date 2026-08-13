// SPDX-License-Identifier: MIT
#pragma once

#include <stddef.h>
#include <stdint.h>

#define MICRONUX_HANDOFF_MAGIC UINT32_C(0x584E554D)
#define MICRONUX_HANDOFF_ABI_VERSION UINT32_C(1)
#define MICRONUX_HANDOFF_MAX_REGIONS 4U

#define MICRONUX_HANDOFF_FLAG_PSRAM_READY (UINT32_C(1) << 0)
#define MICRONUX_HANDOFF_FLAG_KERNEL_VERIFIED (UINT32_C(1) << 1)
#define MICRONUX_HANDOFF_FLAG_ENTRY_IN_PSRAM (UINT32_C(1) << 2)
#define MICRONUX_HANDOFF_FLAG_DTB_PRESENT (UINT32_C(1) << 3)

#define MICRONUX_REGION_KERNEL UINT32_C(1)
#define MICRONUX_REGION_LOADER UINT32_C(2)
#define MICRONUX_REGION_DTB UINT32_C(3)
#define MICRONUX_REGION_COMMS UINT32_C(4)

typedef struct {
    uint32_t psram_paddr;
    uint32_t loader_vaddr;
    uint32_t size;
    uint32_t type;
} micronux_reserved_region_v1_t;

typedef struct {
    uint32_t magic;
    uint32_t abi_version;
    uint32_t struct_size;
    uint32_t flags;

    uint32_t boot_hart_id;
    uint32_t silicon_revision;
    uint32_t cpu_hz;
    uint32_t apb_hz;

    uint32_t console_type;
    uint32_t console_port;
    uint32_t console_baud;
    uint32_t reserved0;

    uint32_t psram_size;
    uint32_t psram_free;
    uint32_t psram_largest_block;
    uint32_t reserved1;

    uint32_t kernel_loader_vaddr;
    uint32_t kernel_psram_paddr;
    uint32_t kernel_size;
    uint32_t kernel_flash_offset;
    uint8_t kernel_sha256[32];

    uint32_t dtb_loader_vaddr;
    uint32_t dtb_psram_paddr;
    uint32_t dtb_size;
    uint32_t reserved_region_count;
    micronux_reserved_region_v1_t reserved_regions[MICRONUX_HANDOFF_MAX_REGIONS];

    uint32_t diagnostic_entry_vaddr;
    uint32_t diagnostic_entry_psram_paddr;
    uint32_t crc32;
} micronux_handoff_v1_t;

_Static_assert(sizeof(micronux_reserved_region_v1_t) == 16,
               "M2 reserved-region ABI changed");
_Static_assert(sizeof(micronux_handoff_v1_t) == 204,
               "M2 handoff ABI changed");
_Static_assert(offsetof(micronux_handoff_v1_t, crc32) == 200,
               "M2 handoff CRC coverage changed");
