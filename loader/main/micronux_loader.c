// SPDX-License-Identifier: MIT

#include <inttypes.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_attr.h"
#include "esp_cache.h"
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
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hal/mmu_types.h"
#include "riscv/csr.h"
#include "sha/sha_core.h"

#include "micronux_handoff.h"
#include "micronux_payload.h"

#define MICRONUX_LINUX_PARTITION_SUBTYPE 0x40
#define MICRONUX_DTB_PARTITION_SUBTYPE 0x41
#define MICRONUX_METADATA_PARTITION_SUBTYPE 0x42

#define MICRONUX_PSRAM_VADDR UINT32_C(0x48000000)
#define MICRONUX_KERNEL_VADDR UINT32_C(0x48400000)
#define MICRONUX_COMMS_VADDR UINT32_C(0x49F00000)
#define MICRONUX_PSRAM_END UINT32_C(0x4A000000)
#define MICRONUX_LOADER_RESERVE_SIZE UINT32_C(0x00400000)
#define MICRONUX_COMMS_RESERVE_SIZE UINT32_C(0x00100000)
#define MICRONUX_KERNEL_ALIGNMENT UINT32_C(0x00400000)
#define MICRONUX_CACHE_ALIGNMENT UINT32_C(128)

#define MICRONUX_CONSOLE_TYPE_USB_SERIAL_JTAG UINT32_C(2)
#define MICRONUX_CONSOLE_BAUD UINT32_C(115200)

#define ESP32P4_CLINT_BASE UINT32_C(0x20000000)
#define ESP32P4_CLINT_MTIMECTL (ESP32P4_CLINT_BASE + UINT32_C(0x4010))
#define ESP32P4_CLINT_MTIMELO (ESP32P4_CLINT_BASE + UINT32_C(0xBFF8))
#define ESP32P4_CLINT_MTIMEHI (ESP32P4_CLINT_BASE + UINT32_C(0xBFFC))
#define ESP32P4_MTIMECTL_ENABLE (UINT32_C(1) << 0)

#define MICRONUX_PMP_LOWER_BOUND_ENTRY 13
#define MICRONUX_PMP_LINUX_ENTRY 14
#define MICRONUX_PMP_LOWER_BOUND_CONFIG PMP_L
#define MICRONUX_PMP_LINUX_CONFIG \
    (PMP_L | PMP_TOR | PMP_R | PMP_W | PMP_X)

#define ESP32P4_CLIC_CONFIG UINT32_C(0x20800000)
#define ESP32P4_CLIC_THRESHOLD UINT32_C(0x20800008)
#define ESP32P4_CLIC_IE_BASE UINT32_C(0x20801001)
#define ESP32P4_CLIC_STRIDE UINT32_C(4)
#define ESP32P4_CLIC_INTERRUPT_COUNT 32U
#define ESP32P4_CLIC_NLBITS_MASK (UINT32_C(0xF) << 1)
#define ESP32P4_CLIC_NLBITS_3 (UINT32_C(3) << 1)

/* USB Serial/JTAG is peripheral interrupt source 22 on ESP32-P4. */
#define ESP32P4_USB_SERIAL_JTAG_INT_ENA UINT32_C(0x500D2010)
#define ESP32P4_USB_SERIAL_JTAG_INT_CLR UINT32_C(0x500D2014)
#define ESP32P4_CORE0_USB_SERIAL_JTAG_INT_MAP UINT32_C(0x500D6058)
#define ESP32P4_INTERRUPT_MAP_MASK UINT32_C(0x3F)
#define MICRONUX_USB_SERIAL_JTAG_CLIC_ID UINT32_C(16)

static const char *const TAG = "micronux_m3";
static DRAM_ATTR micronux_handoff_v1_t s_handoff;
static DRAM_ATTR micronux_payload_v1_t s_payload;

extern void micronux_handoff_jump(uint32_t boot_hart_id,
                                  const void *dtb,
                                  uintptr_t entry_vaddr)
    __attribute__((noreturn));

static void fail(const char *reason) __attribute__((noreturn));

static inline uint32_t read_reg32(uint32_t address)
{
    return *(volatile uint32_t *)(uintptr_t)address;
}

static inline void write_reg32(uint32_t address, uint32_t value)
{
    *(volatile uint32_t *)(uintptr_t)address = value;
}

static uint64_t read_mtime(void)
{
    uint32_t high_before;
    uint32_t low;
    uint32_t high_after;

    do {
        high_before = read_reg32(ESP32P4_CLINT_MTIMEHI);
        low = read_reg32(ESP32P4_CLINT_MTIMELO);
        high_after = read_reg32(ESP32P4_CLINT_MTIMEHI);
    } while (high_before != high_after);

    return ((uint64_t)high_after << 32) | low;
}

static void characterize_clint(void)
{
    const uint32_t control_before = read_reg32(ESP32P4_CLINT_MTIMECTL);
    write_reg32(ESP32P4_CLINT_MTIMECTL,
                control_before | ESP32P4_MTIMECTL_ENABLE);

    const int64_t time_before_us = esp_timer_get_time();
    const uint64_t count_before = read_mtime();
    esp_rom_delay_us(100000);
    const uint64_t count_after = read_mtime();
    const int64_t elapsed_us = esp_timer_get_time() - time_before_us;
    const uint64_t delta = count_after - count_before;
    const uint64_t estimated_hz = elapsed_us > 0
        ? delta * UINT64_C(1000000) / (uint64_t)elapsed_us
        : 0;

    ESP_LOGI(TAG,
             "MICRONUX:M3:CLINT control_before=%08" PRIx32
             " control_after=%08" PRIx32 " delta=%" PRIu64
             " elapsed_us=%" PRId64 " estimated_hz=%" PRIu64,
             control_before, read_reg32(ESP32P4_CLINT_MTIMECTL), delta,
             elapsed_us, estimated_hz);
}

static void fail(const char *reason)
{
    ESP_LOGE(TAG, "MICRONUX:M3:FAIL reason=%s", reason);
    abort();
}

static size_t align_up(size_t value, size_t alignment)
{
    return (value + alignment - 1U) & ~(alignment - 1U);
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
        buffer[i] = UINT32_C(0xA5A55A5A) ^
                    (uint32_t)i * UINT32_C(0x9E3779B1);
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

static const esp_partition_t *find_partition(esp_partition_subtype_t subtype,
                                             const char *label)
{
    return esp_partition_find_first(ESP_PARTITION_TYPE_DATA, subtype, label);
}

static void validate_manifest(const esp_partition_t *metadata_partition)
{
    if (metadata_partition == NULL ||
        metadata_partition->size < sizeof(s_payload)) {
        fail("metadata-partition");
    }

    ESP_ERROR_CHECK(esp_partition_read(metadata_partition, 0, &s_payload,
                                       sizeof(s_payload)));
    const uint32_t calculated_crc = esp_rom_crc32_le(
        0, (const uint8_t *)&s_payload,
        offsetof(micronux_payload_v1_t, crc32));

    if (s_payload.magic != MICRONUX_PAYLOAD_MAGIC ||
        s_payload.abi_version != MICRONUX_PAYLOAD_ABI_VERSION ||
        s_payload.struct_size != sizeof(s_payload) ||
        s_payload.crc32 != calculated_crc) {
        fail("metadata-validation");
    }
    if (s_payload.flags != MICRONUX_PAYLOAD_FLAG_DTB_PRESENT ||
        s_payload.kernel_load_vaddr != MICRONUX_KERNEL_VADDR ||
        s_payload.kernel_file_size == 0 ||
        s_payload.kernel_memory_size < s_payload.kernel_file_size ||
        s_payload.dtb_size < 40) {
        fail("metadata-contract");
    }

    const uint64_t kernel_end = (uint64_t)s_payload.kernel_load_vaddr +
                                s_payload.kernel_memory_size;
    if (kernel_end > MICRONUX_COMMS_VADDR) {
        fail("kernel-memory-span");
    }

    ESP_LOGI(TAG,
             "MICRONUX:M3:MANIFEST abi=%" PRIu32 " crc32=%08" PRIx32
             " kernel_file=%" PRIu32 " kernel_memory=%" PRIu32
             " dtb=%" PRIu32,
             s_payload.abi_version, s_payload.crc32,
             s_payload.kernel_file_size, s_payload.kernel_memory_size,
             s_payload.dtb_size);
}

static void verify_hash(const char *name, const void *data, size_t size,
                        const uint8_t expected[32])
{
    uint8_t actual[32];
    char actual_hex[65];

    esp_sha(SHA2_256, data, size, actual);
    if (memcmp(actual, expected, sizeof(actual)) != 0) {
        digest_to_hex(actual, actual_hex);
        ESP_LOGE(TAG, "%s SHA-256 mismatch: %s", name, actual_hex);
        fail("payload-sha256");
    }
}

static void prepare_clic_for_linux(void)
{
    for (unsigned int interrupt = 0;
         interrupt < ESP32P4_CLIC_INTERRUPT_COUNT; ++interrupt) {
        *(volatile uint8_t *)(uintptr_t)(ESP32P4_CLIC_IE_BASE +
            interrupt * ESP32P4_CLIC_STRIDE) = 0;
    }

    const uint32_t config = read_reg32(ESP32P4_CLIC_CONFIG);
    write_reg32(ESP32P4_CLIC_CONFIG,
                (config & ~ESP32P4_CLIC_NLBITS_MASK) |
                ESP32P4_CLIC_NLBITS_3);
    write_reg32(ESP32P4_CLIC_THRESHOLD, 0);
}

static void prepare_usb_serial_jtag_for_linux(void)
{
    /*
     * Linux owns CLIC ID 16 (external slot 0).  Stop the IDF driver's
     * peripheral interrupts, clear stale endpoint status, then transfer the
     * source through the core-0 interrupt matrix.  Polling TX remains usable
     * by the early console throughout the handoff.
     */
    write_reg32(ESP32P4_USB_SERIAL_JTAG_INT_ENA, 0);
    write_reg32(ESP32P4_USB_SERIAL_JTAG_INT_CLR, UINT32_MAX);

    const uint32_t map = read_reg32(
        ESP32P4_CORE0_USB_SERIAL_JTAG_INT_MAP);
    write_reg32(ESP32P4_CORE0_USB_SERIAL_JTAG_INT_MAP,
        (map & ~ESP32P4_INTERRUPT_MAP_MASK) |
        MICRONUX_USB_SERIAL_JTAG_CLIC_ID);

    if ((read_reg32(ESP32P4_CORE0_USB_SERIAL_JTAG_INT_MAP) &
         ESP32P4_INTERRUPT_MAP_MASK) !=
        MICRONUX_USB_SERIAL_JTAG_CLIC_ID) {
        fail("usb-serial-jtag-route");
    }
}

static void prepare_pmp_for_linux(void)
{
    /*
     * ESP-IDF locks its platform PMP entries before app_main().  Entries 13
     * and 14 are unused on ESP32-P4 revision 1.3, so use them as a TOR pair
     * for the exact Linux-owned PSRAM interval.  Locking the pair makes the
     * U-mode contract deterministic: loader and comms reserves remain out of
     * reach while NOMMU Linux receives the RWX memory it requires.
     */
    PMP_RESET_AND_ENTRY_SET(MICRONUX_PMP_LOWER_BOUND_ENTRY,
                            MICRONUX_KERNEL_VADDR,
                            MICRONUX_PMP_LOWER_BOUND_CONFIG);
    PMP_RESET_AND_ENTRY_SET(MICRONUX_PMP_LINUX_ENTRY,
                            MICRONUX_COMMS_VADDR,
                            MICRONUX_PMP_LINUX_CONFIG);

    const uint32_t lower_config =
        PMP_ENTRY_CFG_READ(MICRONUX_PMP_LOWER_BOUND_ENTRY);
    const uint32_t linux_config =
        PMP_ENTRY_CFG_READ(MICRONUX_PMP_LINUX_ENTRY);
    const uint32_t lower_address =
        PMP_ENTRY_ADDR_READ(MICRONUX_PMP_LOWER_BOUND_ENTRY);
    const uint32_t linux_address =
        PMP_ENTRY_ADDR_READ(MICRONUX_PMP_LINUX_ENTRY);

    if (lower_config != MICRONUX_PMP_LOWER_BOUND_CONFIG ||
        linux_config != MICRONUX_PMP_LINUX_CONFIG ||
        lower_address != MICRONUX_KERNEL_VADDR ||
        linux_address != MICRONUX_COMMS_VADDR) {
        fail("linux-pmp-window");
    }

    ESP_LOGI(TAG,
             "MICRONUX:M3:PMP entries=%u,%u linux=[%08" PRIx32
             ",%08" PRIx32 ") config=%02" PRIx32,
             MICRONUX_PMP_LOWER_BOUND_ENTRY, MICRONUX_PMP_LINUX_ENTRY,
             lower_address, linux_address, linux_config);
}

void app_main(void)
{
    esp_chip_info_t chip_info = {0};
    esp_chip_info(&chip_info);

    ESP_LOGI(TAG,
             "MICRONUX:M3:BOOT target=esp32p4 revision=%" PRIu32
             " cores=%" PRIu32,
             chip_info.revision, chip_info.cores);
    if (chip_info.revision != 103) {
        fail("unsupported-silicon-revision");
    }

    characterize_clint();

    const size_t psram_size = esp_psram_get_size();
    const size_t psram_heap = heap_caps_get_total_size(MALLOC_CAP_SPIRAM);
    const size_t psram_free = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    const size_t psram_largest =
        heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM);

    ESP_LOGI(TAG,
             "MICRONUX:M3:PSRAM size=%zu heap=%zu free=%zu largest=%zu",
             psram_size, psram_heap, psram_free, psram_largest);
    if (psram_size != 32U * 1024U * 1024U) {
        fail("psram-capacity");
    }

    const esp_partition_t *metadata_partition = find_partition(
        (esp_partition_subtype_t)MICRONUX_METADATA_PARTITION_SUBTYPE,
        "metadata");
    validate_manifest(metadata_partition);

    const esp_partition_t *linux_partition = find_partition(
        (esp_partition_subtype_t)MICRONUX_LINUX_PARTITION_SUBTYPE, "linux");
    const esp_partition_t *dtb_partition = find_partition(
        (esp_partition_subtype_t)MICRONUX_DTB_PARTITION_SUBTYPE, "dtb");
    if (linux_partition == NULL ||
        linux_partition->size < s_payload.kernel_file_size) {
        fail("linux-partition");
    }
    if (dtb_partition == NULL || dtb_partition->size < s_payload.dtb_size) {
        fail("dtb-partition");
    }

    const size_t dtb_allocation_size =
        align_up(s_payload.dtb_size, MICRONUX_CACHE_ALIGNMENT);
    uint8_t *dtb = heap_caps_aligned_alloc(
        MICRONUX_CACHE_ALIGNMENT, dtb_allocation_size,
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (dtb == NULL || (uintptr_t)dtb >= MICRONUX_KERNEL_VADDR) {
        fail("dtb-buffer-allocation");
    }
    ESP_ERROR_CHECK(esp_partition_read(dtb_partition, 0, dtb,
                                       s_payload.dtb_size));
    memset(dtb + s_payload.dtb_size, 0,
           dtb_allocation_size - s_payload.dtb_size);
    if (dtb[0] != 0xd0 || dtb[1] != 0x0d ||
        dtb[2] != 0xfe || dtb[3] != 0xed) {
        fail("dtb-magic");
    }
    verify_hash("DTB", dtb, s_payload.dtb_size, s_payload.dtb_sha256);

    const size_t kernel_allocation_size =
        align_up(s_payload.kernel_memory_size, MICRONUX_CACHE_ALIGNMENT);
    uint8_t *kernel = heap_caps_aligned_alloc(
        MICRONUX_KERNEL_ALIGNMENT, kernel_allocation_size,
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (kernel == NULL) {
        fail("kernel-buffer-allocation");
    }
    if ((uintptr_t)kernel != s_payload.kernel_load_vaddr) {
        ESP_LOGE(TAG, "kernel allocation is %p, expected %08" PRIx32,
                 kernel, s_payload.kernel_load_vaddr);
        fail("kernel-load-address");
    }
    if (!test_kernel_buffer((uint32_t *)kernel, kernel_allocation_size)) {
        fail("psram-integrity");
    }
    ESP_ERROR_CHECK(esp_partition_read(linux_partition, 0, kernel,
                                       s_payload.kernel_file_size));
    memset(kernel + s_payload.kernel_file_size, 0,
           kernel_allocation_size - s_payload.kernel_file_size);
    verify_hash("kernel", kernel, s_payload.kernel_file_size,
                s_payload.kernel_sha256);

    esp_paddr_t kernel_paddr = 0;
    esp_paddr_t dtb_paddr = 0;
    mmu_target_t kernel_target = MMU_TARGET_FLASH0;
    mmu_target_t dtb_target = MMU_TARGET_FLASH0;
    ESP_ERROR_CHECK(esp_mmu_vaddr_to_paddr(
        kernel, &kernel_paddr, &kernel_target));
    ESP_ERROR_CHECK(esp_mmu_vaddr_to_paddr(dtb, &dtb_paddr, &dtb_target));
    if (kernel_target != MMU_TARGET_PSRAM0 ||
        dtb_target != MMU_TARGET_PSRAM0 ||
        kernel_paddr != MICRONUX_LOADER_RESERVE_SIZE ||
        dtb_paddr >= MICRONUX_LOADER_RESERVE_SIZE) {
        fail("psram-map-contract");
    }

    char kernel_sha_hex[65];
    char dtb_sha_hex[65];
    digest_to_hex(s_payload.kernel_sha256, kernel_sha_hex);
    digest_to_hex(s_payload.dtb_sha256, dtb_sha_hex);
    ESP_LOGI(TAG,
             "MICRONUX:M3:KERNEL vaddr=%p paddr=%08" PRIx32
             " file=%" PRIu32 " memory=%" PRIu32 " sha256=%s",
             kernel, kernel_paddr, s_payload.kernel_file_size,
             s_payload.kernel_memory_size, kernel_sha_hex);
    ESP_LOGI(TAG,
             "MICRONUX:M3:DTB vaddr=%p paddr=%08" PRIx32
             " size=%" PRIu32 " sha256=%s",
             dtb, dtb_paddr, s_payload.dtb_size, dtb_sha_hex);

    memset(&s_handoff, 0, sizeof(s_handoff));
    s_handoff.magic = MICRONUX_HANDOFF_MAGIC;
    s_handoff.abi_version = MICRONUX_HANDOFF_ABI_VERSION;
    s_handoff.struct_size = sizeof(s_handoff);
    s_handoff.flags = MICRONUX_HANDOFF_FLAG_PSRAM_READY |
                      MICRONUX_HANDOFF_FLAG_KERNEL_VERIFIED |
                      MICRONUX_HANDOFF_FLAG_ENTRY_IN_PSRAM |
                      MICRONUX_HANDOFF_FLAG_DTB_PRESENT;
    s_handoff.boot_hart_id = 0;
    s_handoff.silicon_revision = chip_info.revision;
    s_handoff.cpu_hz = esp_clk_cpu_freq();
    s_handoff.apb_hz = esp_clk_apb_freq();
    s_handoff.console_type = MICRONUX_CONSOLE_TYPE_USB_SERIAL_JTAG;
    s_handoff.console_port = 0;
    s_handoff.console_baud = MICRONUX_CONSOLE_BAUD;
    s_handoff.psram_size = (uint32_t)psram_size;
    s_handoff.psram_free = (uint32_t)psram_free;
    s_handoff.psram_largest_block = (uint32_t)psram_largest;
    s_handoff.kernel_loader_vaddr = (uint32_t)(uintptr_t)kernel;
    s_handoff.kernel_psram_paddr = kernel_paddr;
    s_handoff.kernel_size = s_payload.kernel_memory_size;
    s_handoff.kernel_flash_offset = linux_partition->address;
    memcpy(s_handoff.kernel_sha256, s_payload.kernel_sha256,
           sizeof(s_handoff.kernel_sha256));
    s_handoff.dtb_loader_vaddr = (uint32_t)(uintptr_t)dtb;
    s_handoff.dtb_psram_paddr = dtb_paddr;
    s_handoff.dtb_size = s_payload.dtb_size;
    s_handoff.reserved_region_count = 4;
    s_handoff.reserved_regions[0] = (micronux_reserved_region_v1_t) {
        .psram_paddr = 0,
        .loader_vaddr = MICRONUX_PSRAM_VADDR,
        .size = MICRONUX_LOADER_RESERVE_SIZE,
        .type = MICRONUX_REGION_LOADER,
    };
    s_handoff.reserved_regions[1] = (micronux_reserved_region_v1_t) {
        .psram_paddr = kernel_paddr,
        .loader_vaddr = (uint32_t)(uintptr_t)kernel,
        .size = s_payload.kernel_memory_size,
        .type = MICRONUX_REGION_KERNEL,
    };
    s_handoff.reserved_regions[2] = (micronux_reserved_region_v1_t) {
        .psram_paddr = dtb_paddr,
        .loader_vaddr = (uint32_t)(uintptr_t)dtb,
        .size = s_payload.dtb_size,
        .type = MICRONUX_REGION_DTB,
    };
    s_handoff.reserved_regions[3] = (micronux_reserved_region_v1_t) {
        .psram_paddr = MICRONUX_COMMS_VADDR - MICRONUX_PSRAM_VADDR,
        .loader_vaddr = MICRONUX_COMMS_VADDR,
        .size = MICRONUX_COMMS_RESERVE_SIZE,
        .type = MICRONUX_REGION_COMMS,
    };
    s_handoff.diagnostic_entry_vaddr = s_payload.kernel_load_vaddr;
    s_handoff.diagnostic_entry_psram_paddr = kernel_paddr;
    s_handoff.crc32 = esp_rom_crc32_le(
        0, (const uint8_t *)&s_handoff,
        offsetof(micronux_handoff_v1_t, crc32));

    ESP_ERROR_CHECK(esp_cache_msync(
        kernel, kernel_allocation_size,
        ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_TYPE_DATA));
    ESP_ERROR_CHECK(esp_cache_msync(
        kernel, kernel_allocation_size,
        ESP_CACHE_MSYNC_FLAG_DIR_M2C | ESP_CACHE_MSYNC_FLAG_TYPE_INST));
    ESP_ERROR_CHECK(esp_cache_msync(
        dtb, dtb_allocation_size,
        ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_TYPE_DATA));

    ESP_LOGI(TAG,
             "MICRONUX:M3:HANDOFF abi=%" PRIu32 " crc32=%08" PRIx32
             " linux=[%08" PRIx32 ",%08" PRIx32 ")"
             " comms=[%08" PRIx32 ",%08" PRIx32 ")",
             s_handoff.abi_version, s_handoff.crc32,
             MICRONUX_KERNEL_VADDR, MICRONUX_COMMS_VADDR,
             MICRONUX_COMMS_VADDR, MICRONUX_PSRAM_END);
    ESP_LOGI(TAG,
             "MICRONUX:M3:JUMP a0=0 a1=%p entry=%08" PRIx32,
             dtb, s_payload.kernel_load_vaddr);
    ESP_LOGI(TAG,
             "MICRONUX:M4:IRQ source=22 matrix=%08" PRIx32
             " clic=%" PRIu32 " handoff=armed",
             ESP32P4_CORE0_USB_SERIAL_JTAG_INT_MAP,
             MICRONUX_USB_SERIAL_JTAG_CLIC_ID);

    fflush(stdout);
    vTaskDelay(pdMS_TO_TICKS(100));

    prepare_pmp_for_linux();

    const int current_core = esp_cpu_get_core_id();
    const int other_core = current_core == 0 ? 1 : 0;
    esp_cpu_stall(other_core);
    vTaskSuspendAll();
    portDISABLE_INTERRUPTS();
    esp_cpu_intr_disable(UINT32_MAX);
    prepare_usb_serial_jtag_for_linux();
    prepare_clic_for_linux();

    micronux_handoff_jump(0, dtb, s_payload.kernel_load_vaddr);
}
