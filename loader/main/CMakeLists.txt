idf_component_register(
    SRCS
        "micronux_loader.c"
        "micronux_dma_pms.c"
        "micronux_mipi_dsi.c"
        "micronux_panel_guard.c"
        "handoff_jump.S"
    INCLUDE_DIRS "."
    PRIV_REQUIRES
        esp_mm
        esp_partition
        esp_psram
        esp_timer
        esp_hw_support
        esp_hal_security
        esp_driver_gpio
        esp_driver_i2c
        esp_driver_sdmmc
        esp_lcd
        espressif__i2c_bus
        heap
        esp_rom
        esp_system
        freertos
        mbedtls
)

# The pinned Waveshare panel constructors perform their own unbounded I2C
# backlight writes. MicroNUX wraps that legacy helper only while a constructor
# runs, then owns the adapter power/backlight sequence itself.
target_link_libraries(${COMPONENT_LIB} INTERFACE
    "-Wl,--wrap=i2c_bus_create"
    "-Wl,--wrap=i2c_bus_device_create"
    "-Wl,--wrap=i2c_bus_device_delete"
    "-Wl,--wrap=i2c_bus_write_bytes"
)
