// SPDX-License-Identifier: MIT

#pragma once

#include "sdkconfig.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#if CONFIG_MICRONUX_C6_PROVISIONING
esp_err_t micronux_provisioning_run(void);
#else
static inline esp_err_t micronux_provisioning_run(void)
{
    return ESP_OK;
}
#endif

#ifdef __cplusplus
}
#endif
