// SPDX-License-Identifier: MIT

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#define MICRONUX_DISPLAY_HANDOFF_ADDRESS UINT32_C(0x49f00000)
#define MICRONUX_DISPLAY_HANDOFF_MAGIC UINT32_C(0x4d4e5844)
#define MICRONUX_DISPLAY_HANDOFF_ABI_VERSION UINT16_C(1)
#define MICRONUX_DSI_FIFO_ADDRESS UINT32_C(0x50105000)
#define MICRONUX_DSI_FIFO_WINDOW_START UINT32_C(0x50105000)
#define MICRONUX_DSI_FIFO_WINDOW_END UINT32_C(0x50106000)

#define MICRONUX_DISPLAY_FLAG_ACTIVE (UINT32_C(1) << 0)
#define MICRONUX_DISPLAY_FLAG_RGB565 (UINT32_C(1) << 1)
#define MICRONUX_DISPLAY_FLAG_DMA_RING (UINT32_C(1) << 2)
#define MICRONUX_DISPLAY_FLAG_I2C_TRANSFERRED (UINT32_C(1) << 3)
#define MICRONUX_DISPLAY_FLAG_BACKLIGHT_BLANKED (UINT32_C(1) << 4)

typedef struct {
    uint32_t magic;
    uint16_t abi_version;
    uint16_t struct_size;
    uint32_t flags;
    uint32_t width;
    uint32_t height;
    uint32_t stride;
    uint32_t framebuffer_address;
    uint32_t framebuffer_size;
    uint32_t dma_descriptor_address;
    uint32_t dma_descriptor_size;
    uint32_t dma_channel;
    uint32_t i2c_address;
    uint32_t backlight_register;
    uint32_t backlight_brightness;
    uint32_t crc32;
} micronux_display_handoff_v1_t;

typedef struct {
    uint32_t framebuffer_start;
    uint32_t framebuffer_end;
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

/*
 * Blank the backlight, quiesce loader scanout, publish the bounded ownership
 * contract, and leave DPI/framebuffer mode ready for Linux to restart.
 */
esp_err_t micronux_mipi_dsi_handoff(void);

/* Return the exact DMA ranges published by a successful display handoff. */
bool micronux_mipi_dsi_dma_policy(micronux_display_dma_policy_t *policy);
