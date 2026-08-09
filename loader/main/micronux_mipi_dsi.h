// SPDX-License-Identifier: MIT

#pragma once

#include "esp_err.h"

/*
 * Prepare the optional loader-owned MIPI-DSI diagnostic. When disabled this
 * is a side-effect-free no-op. The attachment probe performs no target writes
 * and releases I2C before handoff. An enabled but unselected electrical profile
 * fails before any display rail, D-PHY, or backlight is powered.
 */
esp_err_t micronux_mipi_dsi_prepare(void);
