// SPDX-License-Identifier: MIT

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#define MICRONUX_DISPLAY_HANDOFF_ADDRESS UINT32_C(0x49f00000)
#define MICRONUX_DISPLAY_HANDOFF_MAGIC UINT32_C(0x4d4e5844)
#define MICRONUX_DISPLAY_HANDOFF_ABI_VERSION UINT16_C(2)
#define MICRONUX_DISPLAY_BUFFER_COUNT UINT32_C(3)
#define MICRONUX_DISPLAY_OWNER_LINUX_PENDING UINT32_C(1)
#define MICRONUX_DISPLAY_BACKBUFFER_POOL_START UINT32_C(0x49300000)
#define MICRONUX_DISPLAY_BACKBUFFER_ADDRESS UINT32_C(0x49300000)
#define MICRONUX_DISPLAY_SPAREBUFFER_ADDRESS UINT32_C(0x49500000)
#define MICRONUX_DISPLAY_BACKBUFFER_POOL_END UINT32_C(0x49700000)
#define MICRONUX_DSI_FIFO_ADDRESS UINT32_C(0x50105000)
#define MICRONUX_DSI_FIFO_WINDOW_START UINT32_C(0x50105000)
#define MICRONUX_DSI_FIFO_WINDOW_END UINT32_C(0x50106000)

#define MICRONUX_DISPLAY_FLAG_ACTIVE (UINT32_C(1) << 0)
#define MICRONUX_DISPLAY_FLAG_RGB565 (UINT32_C(1) << 1)
#define MICRONUX_DISPLAY_FLAG_DMA_IRQ_REARM (UINT32_C(1) << 2)
#define MICRONUX_DISPLAY_FLAG_I2C_TRANSFERRED (UINT32_C(1) << 3)
/* The write-only controller ACKed PWM-zero and BL_ENABLE-clear commands. */
#define MICRONUX_DISPLAY_FLAG_BACKLIGHT_BLANKED (UINT32_C(1) << 4)
#define MICRONUX_DISPLAY_FLAG_TRIPLE_BUFFER (UINT32_C(1) << 5)
#define MICRONUX_DISPLAY_FLAG_LOADER_FRONT_VALID (UINT32_C(1) << 6)
/* The DSI host VPG is active and bridge DPI input is disabled at handoff. */
#define MICRONUX_DISPLAY_FLAG_HOST_VPG_ACTIVE (UINT32_C(1) << 7)

#define MICRONUX_DISPLAY_COLD_HANDOFF_ABI_VERSION UINT16_C(3)
#define MICRONUX_DISPLAY_COLD_HANDOFF_SIZE UINT16_C(0xc0)
#define MICRONUX_DISPLAY_COLD_BOOT_MODE UINT32_C(1)
#define MICRONUX_DISPLAY_COLD_OWNER_LINUX_PENDING UINT32_C(1)
#define MICRONUX_DISPLAY_COLD_PANEL_PROFILE_JD9365_WAVESHARE_10_1 \
    UINT32_C(1)
#define MICRONUX_DISPLAY_COLD_PIXEL_FORMAT_RGB565_LE UINT32_C(1)
#define MICRONUX_DISPLAY_COLD_PANEL_PAYLOAD_CRC32 UINT32_C(0xcea07f9b)
#define MICRONUX_DISPLAY_COLD_QUIESCE_SEQUENCE_ID UINT32_C(1)

#define MICRONUX_DISPLAY_V3_FLAG_RGB565 (UINT32_C(1) << 0)
#define MICRONUX_DISPLAY_V3_FLAG_LINUX_COLD_INIT (UINT32_C(1) << 1)
#define MICRONUX_DISPLAY_V3_FLAG_PWM_ZERO_WRITE_ACKED (UINT32_C(1) << 2)
#define MICRONUX_DISPLAY_V3_FLAG_RESET_PREPARE_WRITE_ACKED (UINT32_C(1) << 3)
#define MICRONUX_DISPLAY_V3_FLAG_RESET_ASSERT_WRITE_ACKED (UINT32_C(1) << 4)
#define MICRONUX_DISPLAY_V3_FLAG_HOST_BRIDGE_RESET (UINT32_C(1) << 5)
#define MICRONUX_DISPLAY_V3_FLAG_DPHY_LDO_DISABLED (UINT32_C(1) << 6)
#define MICRONUX_DISPLAY_V3_FLAG_GDMA_QUIESCED (UINT32_C(1) << 7)
#define MICRONUX_DISPLAY_V3_FLAG_TRIPLE_BUFFER_RESERVED (UINT32_C(1) << 8)
#define MICRONUX_DISPLAY_V3_FLAG_DESC_POOL_RESERVED (UINT32_C(1) << 9)
#define MICRONUX_DISPLAY_V3_FLAG_DMA_PMS_READY (UINT32_C(1) << 10)
#define MICRONUX_DISPLAY_V3_FLAG_GDMA_IRQ_ROUTE_READY (UINT32_C(1) << 11)
#define MICRONUX_DISPLAY_V3_FLAG_I2C_RELEASED (UINT32_C(1) << 12)
#define MICRONUX_DISPLAY_V3_FLAG_RUNTIME_IRQ_REARM_REQUIRED \
    (UINT32_C(1) << 13)
#define MICRONUX_DISPLAY_V3_REQUIRED_FLAGS UINT32_C(0x00003fff)

typedef struct {
    uint32_t magic;
    uint16_t abi_version;
    uint16_t struct_size;
    uint32_t flags;
    uint32_t width;
    uint32_t height;
    uint32_t stride;
    uint32_t pixel_clock_hz;
    uint32_t lane_bit_rate_mbps;
    uint32_t data_lanes;
    uint32_t buffer_count;
    uint32_t ownership_state;
    uint32_t hsync_pulse_width;
    uint32_t hsync_back_porch;
    uint32_t hsync_front_porch;
    uint32_t vsync_pulse_width;
    uint32_t vsync_back_porch;
    uint32_t vsync_front_porch;
    uint32_t framebuffer_address[MICRONUX_DISPLAY_BUFFER_COUNT];
    uint32_t framebuffer_size;
    uint32_t dma_descriptor_address;
    uint32_t dma_descriptor_size;
    uint32_t dma_descriptor_count;
    uint32_t dma_channel;
    uint32_t dsi_fifo_address;
    uint32_t i2c_address;
    uint32_t display_control_register;
    uint32_t backlight_register;
    uint32_t backlight_brightness;
    uint32_t crc32;
} micronux_display_handoff_v2_t;

typedef struct {
    uint32_t magic;
    uint16_t abi_version;
    uint16_t struct_size;
    uint32_t flags;
    uint32_t boot_mode;
    uint32_t ownership_state;
    uint32_t silicon_revision;
    uint32_t panel_profile_id;
    uint32_t width;
    uint32_t height;
    uint32_t stride;
    uint32_t pixel_format;
    uint32_t pixel_clock_hz;
    uint32_t lane_bit_rate_mbps;
    uint32_t data_lanes;
    uint32_t buffer_count;
    uint32_t hsync_pulse_width;
    uint32_t hsync_back_porch;
    uint32_t hsync_front_porch;
    uint32_t vsync_pulse_width;
    uint32_t vsync_back_porch;
    uint32_t vsync_front_porch;
    uint32_t framebuffer_address[MICRONUX_DISPLAY_BUFFER_COUNT];
    uint32_t framebuffer_size;
    uint32_t dma_descriptor_address;
    uint32_t dma_descriptor_size;
    uint32_t dma_descriptor_count;
    uint32_t dma_channel;
    uint32_t dsi_fifo_address;
    uint32_t gdma_irq_source;
    uint32_t gdma_clic_irq;
    uint32_t i2c_port;
    uint32_t i2c_sda_gpio;
    uint32_t i2c_scl_gpio;
    uint32_t i2c_rate_hz;
    uint32_t i2c_address;
    uint32_t display_control_register;
    uint32_t backlight_register;
    uint32_t display_control_reset_assert_command;
    uint32_t display_control_reset_prepare_command;
    uint32_t display_control_reveal_command;
    uint32_t backlight_brightness;
    uint32_t reset_hold_ms;
    uint32_t pwm_zero_settle_ms;
    uint32_t panel_payload_crc32;
    uint32_t quiesce_sequence_id;
    uint32_t crc32;
} micronux_display_handoff_v3_t;

_Static_assert(sizeof(micronux_display_handoff_v3_t) ==
                   MICRONUX_DISPLAY_COLD_HANDOFF_SIZE,
               "ABI-v3 display handoff size");
_Static_assert(offsetof(micronux_display_handoff_v3_t,
                        framebuffer_address) == 0x54,
               "ABI-v3 framebuffer offset");
_Static_assert(offsetof(micronux_display_handoff_v3_t,
                        gdma_irq_source) == 0x78,
               "ABI-v3 IRQ source offset");
_Static_assert(offsetof(micronux_display_handoff_v3_t,
                        panel_payload_crc32) == 0xb4,
               "ABI-v3 panel payload CRC offset");
_Static_assert(offsetof(micronux_display_handoff_v3_t,
                        quiesce_sequence_id) == 0xb8,
               "ABI-v3 quiesce sequence offset");
_Static_assert(offsetof(micronux_display_handoff_v3_t, crc32) == 0xbc,
               "ABI-v3 CRC offset");

typedef struct {
    uint32_t frontbuffer_start;
    uint32_t frontbuffer_end;
    uint32_t backbuffer_pool_start;
    uint32_t backbuffer_pool_end;
    uint32_t descriptor_start;
    uint32_t descriptor_end;
    uint32_t fifo_start;
    uint32_t fifo_end;
    uint32_t dma_channel;
} micronux_display_dma_policy_t;

/*
 * Prepare the optional loader-owned MIPI-DSI diagnostic. When disabled this
 * is a side-effect-free no-op. The attachment probe performs no target writes
 * and releases I2C before handoff. An enabled but unselected electrical profile
 * fails before any display rail, D-PHY, or backlight is powered.
 */
esp_err_t micronux_mipi_dsi_prepare(void);

/* Run the Linux-free ESP-IDF display baseline indefinitely. */
void micronux_mipi_dsi_standalone_run(void);

/* Update the loader splash with a monotonic, real-work progress value. */
void micronux_mipi_dsi_progress(uint8_t percent);

/*
 * Blank the backlight, switch the DSI host to its hardware VPG, quiesce the
 * loader's framebuffer DMA, and publish the bounded ownership contract.
 * Linux starts its framebuffer producer while VPG remains the active source,
 * then performs the v6.0.1 VPG-to-DPI source switch while still dark.
 */
esp_err_t micronux_mipi_dsi_handoff(void);

/* Cold ABI-v3 is split so route/PMS verification gates the staged contract. */
esp_err_t micronux_mipi_dsi_cold_early_dark(void);
void micronux_mipi_dsi_cold_fail_cleanup(void);
esp_err_t micronux_mipi_dsi_cold_prepare(void);
esp_err_t micronux_mipi_dsi_cold_handoff(void);
esp_err_t micronux_mipi_dsi_cold_stage(void);
bool micronux_mipi_dsi_cold_staged(void);
void micronux_mipi_dsi_cold_commit(void);

/* Return the exact DMA ranges published by a successful display handoff. */
bool micronux_mipi_dsi_dma_policy(micronux_display_dma_policy_t *policy);
