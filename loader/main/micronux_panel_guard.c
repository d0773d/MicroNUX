// SPDX-License-Identifier: MIT

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "i2c_bus.h"

#include "micronux_panel_guard.h"

static bool s_panel_constructor_guard;
static uint8_t s_guard_bus_token;
static uint8_t s_guard_device_token;

void micronux_panel_guard_begin(void)
{
    s_panel_constructor_guard = true;
}

void micronux_panel_guard_end(void)
{
    s_panel_constructor_guard = false;
}

i2c_bus_handle_t __real_i2c_bus_create(i2c_port_t port,
                                       const i2c_config_t *conf);

i2c_bus_handle_t __wrap_i2c_bus_create(i2c_port_t port,
                                       const i2c_config_t *conf)
{
    if (s_panel_constructor_guard) {
        return &s_guard_bus_token;
    }
    return __real_i2c_bus_create(port, conf);
}

i2c_bus_device_handle_t __real_i2c_bus_device_create(
    i2c_bus_handle_t bus_handle, uint8_t dev_addr, uint32_t clk_speed);

i2c_bus_device_handle_t __wrap_i2c_bus_device_create(
    i2c_bus_handle_t bus_handle, uint8_t dev_addr, uint32_t clk_speed)
{
    if (s_panel_constructor_guard && bus_handle == &s_guard_bus_token) {
        return &s_guard_device_token;
    }
    return __real_i2c_bus_device_create(bus_handle, dev_addr, clk_speed);
}

esp_err_t __real_i2c_bus_device_delete(
    i2c_bus_device_handle_t *p_dev_handle);

esp_err_t __wrap_i2c_bus_device_delete(
    i2c_bus_device_handle_t *p_dev_handle)
{
    if (s_panel_constructor_guard && p_dev_handle != NULL &&
        *p_dev_handle == &s_guard_device_token) {
        *p_dev_handle = NULL;
        return ESP_OK;
    }
    return __real_i2c_bus_device_delete(p_dev_handle);
}

esp_err_t __real_i2c_bus_write_bytes(i2c_bus_device_handle_t dev_handle,
                                     uint8_t mem_address, size_t data_len,
                                     const uint8_t *data);

esp_err_t __wrap_i2c_bus_write_bytes(i2c_bus_device_handle_t dev_handle,
                                     uint8_t mem_address, size_t data_len,
                                     const uint8_t *data)
{
    if (s_panel_constructor_guard && dev_handle == &s_guard_device_token) {
        return ESP_OK;
    }
    return __real_i2c_bus_write_bytes(dev_handle, mem_address, data_len, data);
}
