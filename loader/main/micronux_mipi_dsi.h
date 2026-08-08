// SPDX-License-Identifier: MIT

#pragma once

#include "esp_err.h"

/*
 * Prepare the optional loader-owned MIPI-DSI diagnostic. When disabled this
 * is a side-effect-free no-op. An enabled but unselected profile fails before
 * any display rail, D-PHY, or backlight is powered.
 */
esp_err_t micronux_mipi_dsi_prepare(void);
