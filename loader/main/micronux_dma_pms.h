// SPDX-License-Identifier: MIT

#pragma once

#include "esp_err.h"

/*
 * Install and read back the M7 DMA allowlist. Earlier profiles keep the
 * ESP-IDF reset policy so their already-proven handoff remains unchanged.
 */
esp_err_t micronux_dma_pms_prepare(void);
