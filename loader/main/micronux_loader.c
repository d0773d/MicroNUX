// SPDX-License-Identifier: MIT

#include <inttypes.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_attr.h"
#include "esp_chip_info.h"
#include "esp_cpu.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mmu_map.h"
#include "esp_partition.h"
#include "esp_private/esp_clk.h"
#include "esp_psram.h"
#include "esp_rom_crc.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal/mmu_types.h"
#include "sha/sha_core.h"

#include "micronux_handoff.h"

#define MICRONUX_LINUX_PARTITION_SUBTYPE 0x40
#define MICRONUX_KERNEL_SIZE UINT32_C(4748064)
#define MICRONUX_CONSOLE_TYPE_UART UINT32_C(1)
#define MICRONUX_CONSOLE_BAUD UINT32_C(115200)
#define MICRONUX_DIAGNOSTIC_TOKEN UINT32_C(0x4D325041)

static const char *const TAG = "micronux_m2";

static const uint8_t EXPECTED_KERNEL_SHA256[32] = {
    0xf4, 0x2a, 0xc7, 0x2f, 0x6f, 0x24, 0x02, 0x7d,
    0xfa, 0x34, 0x4f, 0xc9, 0x6c, 0xe8, 0x40, 0xa0,
    0xaa, 0xbc, 0xe2, 0x4d, 0xa8, 0xa2, 0x91, 0x88,
    0x2a, 0x8b, 0x27, 0xf7, 0x4b, 0x22, 0x25, 0x87,
};

static DRAM_ATTR micronux_handoff_v1_t s_handoff;

extern void micronux_handoff_jump(uint32_t boot_hart_id,
                                  const micronux_handoff_v1_t *handoff,
                                  uintptr_t entry_vaddr)
    __attribute__((noreturn));

static void fail(const char *reason) __attribute__((noreturn));
static void micronux_diagnostic_entry(uint32_t boot_hart_id,
                                      const micronux_handoff_v1_t *handoff)
    __attribute__((noreturn, noinline));

static void fail(const char *reason)
{
    ESP_LOGE(TAG, "MICRONUX:M2:FAIL reason=%s", reason);
    abort();
}

static void digest_to_hex(const uint8_t digest[32], char output[65])
{
    static const char HEX[] = "0123456789abcdef";

    for (size_t i = 0; i < 32; ++i) {
        output[i * 2] = HEX[digest[i] >> 4];
        output[i * 2 + 1] = HEX[digest[i] & 0x0f];
    }
    output[64] = '\0';
}

static bool test_kernel_buffer(uint32_t *buffer, size_t size)
{
    const size_t words = size / sizeof(*buffer);

    for (size_t i = 0; i < words; ++i) {
        buffer[i] = UINT32_C(0xA5A55A5A) ^ (uint32_t)i * UINT32_C(0x9E3779B1);
    }
    for (size_t i = 0; i < words; ++i) {
        const uint32_t expected = UINT32_C(0xA5A55A5A) ^
                                  (uint32_t)i * UINT32_C(0x9E3779B1);
        if (buffer[i] != expected) {
            ESP_LOGE(TAG,
                     "PSRAM mismatch at word %zu: expected=%08" PRIx32
                     " actual=%08" PRIx32,
                     i, expected, buffer[i]);
            return false;
        }
    }
    return true;
}

static void micronux_diagnostic_entry(uint32_t boot_hart_id,
                                      const micronux_handoff_v1_t *handoff)
{
    const uint32_t calculated_crc = esp_rom_crc32_le(
        0, (const uint8_t *)handoff, offsetof(micronux_handoff_v1_t, crc32));
    const bool valid = handoff->magic == MICRONUX_HANDOFF_MAGIC &&
                       handoff->abi_version == MICRONUX_HANDOFF_ABI_VERSION &&
                       handoff->struct_size == sizeof(*handoff) &&
                       handoff->crc32 == calculated_crc &&
                       boot_hart_id == handoff->boot_hart_id;

    esp_rom_printf("\nMICRONUX:M2:ENTRY hart=%" PRIu32
                   " handoff=%p crc32=%08" PRIx32 "\n",
                   boot_hart_id, handoff, handoff->crc32);

    if (valid) {
        const uint32_t token = handoff->crc32 ^ MICRONUX_DIAGNOSTIC_TOKEN;
        esp_rom_printf("MICRONUX:M2:PASS token=%08" PRIx32 "\n", token);
    } else {
        esp_rom_printf("MICRONUX:M2:FAIL reason=handoff-validation\n");
    }

    for (;;) {
        __asm__ volatile("wfi");
    }
}

void app_main(void)
{
    esp_chip_info_t chip_info = {0};
    esp_chip_info(&chip_info);

    ESP_LOGI(TAG,
             "MICRONUX:M2:BOOT target=esp32p4 revision=%" PRIu32
             " cores=%" PRIu32,
             chip_info.revision, chip_info.cores);

    const size_t psram_size = esp_psram_get_size();
    const size_t psram_heap = heap_caps_get_total_size(MALLOC_CAP_SPIRAM);
    const size_t psram_free = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    const size_t psram_largest =
        heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM);

    ESP_LOGI(TAG,
             "MICRONUX:M2:PSRAM size=%zu heap=%zu free=%zu largest=%zu",
             psram_size, psram_heap, psram_free, psram_largest);
    if (psram_size < 32U * 1024U * 1024U || psram_largest < MICRONUX_KERNEL_SIZE) {
        fail("psram-capacity");
    }

    uint8_t *kernel = heap_caps_aligned_alloc(
        128, MICRONUX_KERNEL_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (kernel == NULL) {
        fail("kernel-buffer-allocation");
    }
    if (!test_kernel_buffer((uint32_t *)kernel, MICRONUX_KERNEL_SIZE)) {
        fail("psram-integrity");
    }
    ESP_LOGI(TAG, "MICRONUX:M2:PSRAM_TEST bytes=%" PRIu32 " result=pass",
             MICRONUX_KERNEL_SIZE);

    const esp_partition_t *linux_partition = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA,
        (esp_partition_subtype_t)MICRONUX_LINUX_PARTITION_SUBTYPE,
        "linux");
    if (linux_partition == NULL || linux_partition->size < MICRONUX_KERNEL_SIZE) {
        fail("linux-partition");
    }
    ESP_ERROR_CHECK(esp_partition_read(
        linux_partition, 0, kernel, MICRONUX_KERNEL_SIZE));

    uint8_t kernel_sha256[32];
    esp_sha(SHA2_256, kernel, MICRONUX_KERNEL_SIZE, kernel_sha256);
    if (memcmp(kernel_sha256, EXPECTED_KERNEL_SHA256,
               sizeof(kernel_sha256)) != 0) {
        char actual_sha[65];
        digest_to_hex(kernel_sha256, actual_sha);
        ESP_LOGE(TAG, "kernel SHA-256 mismatch: %s", actual_sha);
        fail("kernel-sha256");
    }

    char kernel_sha_hex[65];
    digest_to_hex(kernel_sha256, kernel_sha_hex);
    ESP_LOGI(TAG,
             "MICRONUX:M2:KERNEL size=%" PRIu32 " sha256=%s",
             MICRONUX_KERNEL_SIZE, kernel_sha_hex);

    esp_paddr_t kernel_paddr = 0;
    mmu_target_t kernel_target = MMU_TARGET_FLASH0;
    ESP_ERROR_CHECK(esp_mmu_vaddr_to_paddr(
        kernel, &kernel_paddr, &kernel_target));
    if (kernel_target != MMU_TARGET_PSRAM0) {
        fail("kernel-not-in-psram");
    }

    const uintptr_t entry_vaddr = (uintptr_t)&micronux_diagnostic_entry;
    esp_paddr_t entry_paddr = 0;
    mmu_target_t entry_target = MMU_TARGET_FLASH0;
    ESP_ERROR_CHECK(esp_mmu_vaddr_to_paddr(
        (void *)entry_vaddr, &entry_paddr, &entry_target));
    if (entry_target != MMU_TARGET_PSRAM0) {
        fail("entry-not-in-psram");
    }

    memset(&s_handoff, 0, sizeof(s_handoff));
    s_handoff.magic = MICRONUX_HANDOFF_MAGIC;
    s_handoff.abi_version = MICRONUX_HANDOFF_ABI_VERSION;
    s_handoff.struct_size = sizeof(s_handoff);
    s_handoff.flags = MICRONUX_HANDOFF_FLAG_PSRAM_READY |
                      MICRONUX_HANDOFF_FLAG_KERNEL_VERIFIED |
                      MICRONUX_HANDOFF_FLAG_ENTRY_IN_PSRAM;
    s_handoff.boot_hart_id = 0;
    s_handoff.silicon_revision = chip_info.revision;
    s_handoff.cpu_hz = esp_clk_cpu_freq();
    s_handoff.apb_hz = esp_clk_apb_freq();
    s_handoff.console_type = MICRONUX_CONSOLE_TYPE_UART;
    s_handoff.console_port = 0;
    s_handoff.console_baud = MICRONUX_CONSOLE_BAUD;
    s_handoff.psram_size = (uint32_t)psram_size;
    s_handoff.psram_free = (uint32_t)psram_free;
    s_handoff.psram_largest_block = (uint32_t)psram_largest;
    s_handoff.kernel_loader_vaddr = (uint32_t)(uintptr_t)kernel;
    s_handoff.kernel_psram_paddr = kernel_paddr;
    s_handoff.kernel_size = MICRONUX_KERNEL_SIZE;
    s_handoff.kernel_flash_offset = linux_partition->address;
    memcpy(s_handoff.kernel_sha256, kernel_sha256, sizeof(kernel_sha256));
    s_handoff.reserved_region_count = 1;
    s_handoff.reserved_regions[0] = (micronux_reserved_region_v1_t) {
        .psram_paddr = kernel_paddr,
        .loader_vaddr = (uint32_t)(uintptr_t)kernel,
        .size = MICRONUX_KERNEL_SIZE,
        .type = MICRONUX_REGION_KERNEL,
    };
    s_handoff.diagnostic_entry_vaddr = (uint32_t)entry_vaddr;
    s_handoff.diagnostic_entry_psram_paddr = entry_paddr;
    s_handoff.crc32 = esp_rom_crc32_le(
        0, (const uint8_t *)&s_handoff,
        offsetof(micronux_handoff_v1_t, crc32));

    ESP_LOGI(TAG,
             "MICRONUX:M2:HANDOFF abi=%" PRIu32 " size=%" PRIu32
             " crc32=%08" PRIx32 " kernel_paddr=%08" PRIx32
             " entry_paddr=%08" PRIx32,
             s_handoff.abi_version, s_handoff.struct_size, s_handoff.crc32,
             s_handoff.kernel_psram_paddr,
             s_handoff.diagnostic_entry_psram_paddr);
    ESP_LOGI(TAG,
             "MICRONUX:M2:JUMP a0=%" PRIu32 " a1=%p entry=%08" PRIxPTR,
             s_handoff.boot_hart_id, &s_handoff, entry_vaddr);

    fflush(stdout);
    vTaskDelay(pdMS_TO_TICKS(100));

    const int current_core = esp_cpu_get_core_id();
    const int other_core = current_core == 0 ? 1 : 0;
    esp_cpu_stall(other_core);
    vTaskSuspendAll();
    portDISABLE_INTERRUPTS();
    esp_cpu_intr_disable(UINT32_MAX);

    micronux_handoff_jump(
        s_handoff.boot_hart_id, &s_handoff, entry_vaddr);
}
