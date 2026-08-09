// SPDX-License-Identifier: MIT

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "i2c_bus.h"

#include "micronux_panel_guard.h"

static bool s_panel_constructor_guard;

void micronux_panel_guard_begin(void)
{
    s_panel_constructor_guard = true;
}

void micronux_panel_guard_end(void)
{
    s_panel_constructor_guard = false;
}

esp_err_t __real_i2c_bus_write_bytes(i2c_bus_device_handle_t dev_handle,
                                     uint8_t mem_address, size_t data_len,
                                     const uint8_t *data);

esp_err_t __wrap_i2c_bus_write_bytes(i2c_bus_device_handle_t dev_handle,
                                     uint8_t mem_address, size_t data_len,
                                     const uint8_t *data)
{
    if (s_panel_constructor_guard) {
        return ESP_OK;
    }
    return __real_i2c_bus_write_bytes(dev_handle, mem_address, data_len, data);
}
