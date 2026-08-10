// SPDX-License-Identifier: MIT

#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>

#include "sdkconfig.h"
#include "esp_cache.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_lcd_mipi_dsi.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_ldo_regulator.h"
#include "esp_log.h"
#include "esp_private/periph_ctrl.h"
#include "esp_rom_crc.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "hal/mipi_dsi_ll.h"
#include "soc/i2c_reg.h"
#include "soc/soc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "micronux_mipi_dsi.h"
#include "micronux_panel_guard.h"

static const char *const TAG = "micronux_dsi";

#if CONFIG_MICRONUX_MIPI_DSI || CONFIG_MICRONUX_MIPI_DSI_PROBE
#define MICRONUX_DSI_I2C_PORT 0
#define MICRONUX_DSI_I2C_SDA GPIO_NUM_7
#define MICRONUX_DSI_I2C_SCL GPIO_NUM_8
#define MICRONUX_DSI_BACKLIGHT_ADDRESS UINT16_C(0x45)
#endif

#if CONFIG_MICRONUX_MIPI_DSI_PROBE
static esp_err_t probe_display_adapter(void)
{
    i2c_master_bus_handle_t bus = NULL;
    const i2c_master_bus_config_t bus_config = {
        .i2c_port = MICRONUX_DSI_I2C_PORT,
        .sda_io_num = MICRONUX_DSI_I2C_SDA,
        .scl_io_num = MICRONUX_DSI_I2C_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };

    esp_err_t result = i2c_new_master_bus(&bus_config, &bus);
    if (result != ESP_OK) {
        ESP_LOGE(TAG,
                 "MICRONUX:M6:DSI state=probe adapter=unknown"
                 " reason=i2c-bus error=%s dphy=off writes=0",
                 esp_err_to_name(result));
        return ESP_OK;
    }

    result = i2c_master_probe(bus, MICRONUX_DSI_BACKLIGHT_ADDRESS, 50);
    const esp_err_t release_result = i2c_del_master_bus(bus);
    if (release_result != ESP_OK) {
        ESP_LOGE(TAG,
                 "MICRONUX:M6:DSI state=probe adapter=unknown"
                 " reason=i2c-release error=%s dphy=off writes=0",
                 esp_err_to_name(release_result));
        return ESP_OK;
    }

    if (result == ESP_OK) {
        ESP_LOGI(TAG,
                 "MICRONUX:M6:DSI state=probe adapter=present address=0x45"
                 " identity=label-required dphy=off writes=0");
    } else {
        ESP_LOGW(TAG,
                 "MICRONUX:M6:DSI state=probe adapter=absent address=0x45"
                 " error=%s dphy=off writes=0",
                 esp_err_to_name(result));
    }
    return ESP_OK;
}
#endif

#if CONFIG_MICRONUX_MIPI_DSI

#if !CONFIG_MICRONUX_MIPI_PANEL_UNSELECTED

#if CONFIG_MICRONUX_MIPI_PANEL_JD9365_800_1280
#include "esp_lcd_jd9365_10_1.h"
#elif CONFIG_MICRONUX_MIPI_PANEL_ILI9881C_720_1280
#include "esp_lcd_ili9881c.h"
#elif CONFIG_MICRONUX_MIPI_PANEL_HX8394_720_1280
#include "esp_lcd_hx8394.h"
#elif CONFIG_MICRONUX_MIPI_PANEL_EK79007_1024_600
#include "esp_lcd_ek79007.h"
#endif

#define MICRONUX_DSI_LANES 2U
#define MICRONUX_DSI_PHY_LDO_CHANNEL 3
#define MICRONUX_DSI_PHY_MILLIVOLTS 2500
#define MICRONUX_DSI_BACKLIGHT_REGISTER UINT8_C(0x96)
#define MICRONUX_DSI_LOADER_PSRAM_START UINT32_C(0x48000000)
#define MICRONUX_DSI_LOADER_PSRAM_END UINT32_C(0x48400000)
#define MICRONUX_DSI_INTERNAL_SRAM_START UINT32_C(0x4ff00000)
#define MICRONUX_DSI_INTERNAL_SRAM_END UINT32_C(0x50000000)
#define MICRONUX_DSI_NONCACHE_OFFSET UINT32_C(0x40000000)
#define MICRONUX_DSI_DMA_DESCRIPTOR_ALIGNMENT UINT32_C(64)
#define MICRONUX_DSI_DMA_DESCRIPTOR_COUNT UINT32_C(4)

typedef struct {
    const char *name;
    uint16_t width;
    uint16_t height;
    uint16_t lane_mbps;
} micronux_mipi_profile_t;

#if CONFIG_MICRONUX_MIPI_PANEL_JD9365_800_1280
static const micronux_mipi_profile_t s_profile = {
    .name = "jd9365-800x1280",
    .width = 800,
    .height = 1280,
    .lane_mbps = 1500,
};
#elif CONFIG_MICRONUX_MIPI_PANEL_ILI9881C_720_1280
static const micronux_mipi_profile_t s_profile = {
    .name = "ili9881c-720x1280",
    .width = 720,
    .height = 1280,
    .lane_mbps = 1000,
};
#elif CONFIG_MICRONUX_MIPI_PANEL_HX8394_720_1280
static const micronux_mipi_profile_t s_profile = {
    .name = "hx8394-720x1280",
    .width = 720,
    .height = 1280,
    .lane_mbps = 700,
};
#elif CONFIG_MICRONUX_MIPI_PANEL_EK79007_1024_600
static const micronux_mipi_profile_t s_profile = {
    .name = "ek79007-1024x600",
    .width = 1024,
    .height = 600,
    .lane_mbps = 1000,
};
#else
static const micronux_mipi_profile_t s_profile = {
    .name = "unselected",
};
#endif

static esp_ldo_channel_handle_t s_phy_ldo;
static esp_lcd_dsi_bus_handle_t s_dsi_bus;
static esp_lcd_panel_io_handle_t s_panel_io;
static esp_lcd_panel_handle_t s_panel;
static i2c_master_bus_handle_t s_i2c_bus;
static i2c_master_dev_handle_t s_backlight;
static micronux_display_dma_policy_t s_dma_policy;
static bool s_dma_policy_ready;

static esp_err_t write_backlight_register(uint8_t reg, uint8_t value)
{
    const uint8_t command[] = {reg, value};
    return i2c_master_transmit(s_backlight, command, sizeof(command), 50);
}

static void release_backlight_bus(void)
{
    if (s_backlight != NULL) {
        (void)i2c_master_bus_rm_device(s_backlight);
        s_backlight = NULL;
    }
    if (s_i2c_bus != NULL) {
        (void)i2c_del_master_bus(s_i2c_bus);
        s_i2c_bus = NULL;
    }
}

static esp_err_t release_backlight_device(void)
{
    if (s_backlight == NULL) {
        return ESP_OK;
    }

    const esp_err_t result = i2c_master_bus_rm_device(s_backlight);
    if (result == ESP_OK) {
        s_backlight = NULL;
    }
    return result;
}

static uintptr_t dma_bus_address(uintptr_t address)
{
    if (address >= UINT32_C(0x80000000)) {
        return address - MICRONUX_DSI_NONCACHE_OFFSET;
    }
    return address;
}

static uint32_t align_down_4k(uint32_t address)
{
    return address & ~UINT32_C(0xfff);
}

static uint32_t align_up_4k(uint32_t address)
{
    return (address + UINT32_C(0xfff)) & ~UINT32_C(0xfff);
}

static esp_err_t prepare_backlight_off(void)
{
    const i2c_master_bus_config_t bus_config = {
        .i2c_port = MICRONUX_DSI_I2C_PORT,
        .sda_io_num = MICRONUX_DSI_I2C_SDA,
        .scl_io_num = MICRONUX_DSI_I2C_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_RETURN_ON_ERROR(i2c_new_master_bus(&bus_config, &s_i2c_bus), TAG,
                        "create display I2C bus");

    const i2c_device_config_t device_config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = MICRONUX_DSI_BACKLIGHT_ADDRESS,
        .scl_speed_hz = 100000,
    };
    esp_err_t ret = i2c_master_bus_add_device(
        s_i2c_bus, &device_config, &s_backlight);
    if (ret != ESP_OK) {
        release_backlight_bus();
        return ret;
    }

    ESP_GOTO_ON_ERROR(write_backlight_register(0x95, 0x11), fail, TAG,
                      "enable display rail stage 1");
    ESP_GOTO_ON_ERROR(write_backlight_register(0x95, 0x17), fail, TAG,
                      "enable display rail stage 2");
    ESP_GOTO_ON_ERROR(
        write_backlight_register(MICRONUX_DSI_BACKLIGHT_REGISTER, 0),
        fail, TAG, "turn backlight off");
    vTaskDelay(pdMS_TO_TICKS(1000));
    return ESP_OK;

fail:
    if (s_backlight != NULL) {
        /* Best effort: a partial rail sequence must never leave light on. */
        (void)write_backlight_register(MICRONUX_DSI_BACKLIGHT_REGISTER, 0);
    }
    release_backlight_bus();
    return ret;
}

static esp_err_t create_selected_panel(void)
{
#if CONFIG_MICRONUX_MIPI_PANEL_JD9365_800_1280
    esp_lcd_dpi_panel_config_t dpi_config =
        JD9365_800_1280_PANEL_60HZ_DPI_CONFIG(LCD_COLOR_FMT_RGB565);
    dpi_config.num_fbs = 1;
    jd9365_vendor_config_t vendor_config = {
        .mipi_config = {
            .dsi_bus = s_dsi_bus,
            .dpi_config = &dpi_config,
            .lane_num = MICRONUX_DSI_LANES,
        },
    };
#elif CONFIG_MICRONUX_MIPI_PANEL_ILI9881C_720_1280
    esp_lcd_dpi_panel_config_t dpi_config =
        ILI9881C_720_1280_PANEL_60HZ_DPI_CONFIG(LCD_COLOR_FMT_RGB565);
    dpi_config.num_fbs = 1;
    ili9881c_vendor_config_t vendor_config = {
        .mipi_config = {
            .dsi_bus = s_dsi_bus,
            .dpi_config = &dpi_config,
            .lane_num = MICRONUX_DSI_LANES,
        },
    };
#elif CONFIG_MICRONUX_MIPI_PANEL_HX8394_720_1280
    esp_lcd_dpi_panel_config_t dpi_config =
        HX8394_720_1280_PANEL_30HZ_DPI_CONFIG(LCD_COLOR_FMT_RGB565);
    dpi_config.num_fbs = 1;
    hx8394_vendor_config_t vendor_config = {
        .mipi_config = {
            .dsi_bus = s_dsi_bus,
            .dpi_config = &dpi_config,
            .lane_num = MICRONUX_DSI_LANES,
        },
    };
#elif CONFIG_MICRONUX_MIPI_PANEL_EK79007_1024_600
    esp_lcd_dpi_panel_config_t dpi_config =
        EK79007_1024_600_PANEL_60HZ_CONFIG_CF(LCD_COLOR_FMT_RGB565);
    dpi_config.num_fbs = 1;
    ek79007_vendor_config_t vendor_config = {
        .mipi_config = {
            .dsi_bus = s_dsi_bus,
            .dpi_config = &dpi_config,
            .lane_num = MICRONUX_DSI_LANES,
        },
    };
#else
    return ESP_ERR_INVALID_STATE;
#endif

#if !CONFIG_MICRONUX_MIPI_PANEL_UNSELECTED
    const esp_lcd_panel_dev_config_t panel_config = {
        .reset_gpio_num = GPIO_NUM_NC,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16,
        .vendor_config = &vendor_config,
    };
    esp_err_t result;
    micronux_panel_guard_begin();
#if CONFIG_MICRONUX_MIPI_PANEL_JD9365_800_1280
    result = esp_lcd_new_panel_jd9365(s_panel_io, &panel_config, &s_panel);
#elif CONFIG_MICRONUX_MIPI_PANEL_ILI9881C_720_1280
    result = esp_lcd_new_panel_ili9881c(s_panel_io, &panel_config, &s_panel);
#elif CONFIG_MICRONUX_MIPI_PANEL_HX8394_720_1280
    result = esp_lcd_new_panel_hx8394(s_panel_io, &panel_config, &s_panel);
#elif CONFIG_MICRONUX_MIPI_PANEL_EK79007_1024_600
    result = esp_lcd_new_panel_ek79007(s_panel_io, &panel_config, &s_panel);
#endif
    micronux_panel_guard_end();
    ESP_RETURN_ON_ERROR(result, TAG, "create selected MIPI panel");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(s_panel), TAG,
                        "reset MIPI panel");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(s_panel), TAG,
                        "initialize MIPI panel");
    return ESP_OK;
#endif
}

#endif /* !CONFIG_MICRONUX_MIPI_PANEL_UNSELECTED */

#endif /* CONFIG_MICRONUX_MIPI_DSI */

esp_err_t micronux_mipi_dsi_prepare(void)
{
#if CONFIG_MICRONUX_MIPI_DSI_PROBE
    return probe_display_adapter();
#elif !CONFIG_MICRONUX_MIPI_DSI
    ESP_LOGI(TAG, "MICRONUX:M6:DSI state=disabled reason=profile-off");
    return ESP_OK;
#elif CONFIG_MICRONUX_MIPI_PANEL_UNSELECTED
    ESP_LOGE(TAG,
             "MICRONUX:M6:DSI state=refused reason=panel-unselected power=off");
    return ESP_ERR_INVALID_STATE;
#else
    ESP_RETURN_ON_ERROR(prepare_backlight_off(), TAG,
                        "prepare backlight controller");

    const esp_ldo_channel_config_t ldo_config = {
        .chan_id = MICRONUX_DSI_PHY_LDO_CHANNEL,
        .voltage_mv = MICRONUX_DSI_PHY_MILLIVOLTS,
    };
    ESP_RETURN_ON_ERROR(esp_ldo_acquire_channel(&ldo_config, &s_phy_ldo),
                        TAG, "power MIPI D-PHY");

    const esp_lcd_dsi_bus_config_t bus_config = {
        .bus_id = 0,
        .num_data_lanes = MICRONUX_DSI_LANES,
        .phy_clk_src = MIPI_DSI_PHY_CLK_SRC_DEFAULT,
        .lane_bit_rate_mbps = s_profile.lane_mbps,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_dsi_bus(&bus_config, &s_dsi_bus), TAG,
                        "create MIPI DSI bus");

    const esp_lcd_dbi_io_config_t io_config = {
        .virtual_channel = 0,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_dbi(
                            s_dsi_bus, &io_config, &s_panel_io),
                        TAG, "create MIPI DBI command channel");
    ESP_RETURN_ON_ERROR(create_selected_panel(), TAG,
                        "create selected MIPI panel");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_disp_on_off(s_panel, true), TAG,
                        "turn MIPI panel on");
    void *framebuffer = NULL;
    ESP_RETURN_ON_ERROR(esp_lcd_dpi_panel_get_frame_buffer(
                            s_panel, 1, &framebuffer),
                        TAG, "locate MIPI framebuffer");
    const size_t framebuffer_size =
        (size_t)s_profile.width * s_profile.height * sizeof(uint16_t);
    const uintptr_t framebuffer_start = (uintptr_t)framebuffer;
    const uintptr_t framebuffer_end = framebuffer_start + framebuffer_size;
    if (framebuffer_end < framebuffer_start ||
        framebuffer_start < MICRONUX_DSI_LOADER_PSRAM_START ||
        framebuffer_end > MICRONUX_DSI_LOADER_PSRAM_END) {
        ESP_LOGE(TAG,
                 "MICRONUX:M6:DSI state=failed reason=framebuffer-ownership"
                 " fb=%p bytes=%zu loader=[%08" PRIx32 ",%08" PRIx32 ")",
                 framebuffer, framebuffer_size,
                 MICRONUX_DSI_LOADER_PSRAM_START,
                 MICRONUX_DSI_LOADER_PSRAM_END);
        return ESP_ERR_INVALID_STATE;
    }

    const uint8_t brightness =
        (uint8_t)((UINT32_C(255) * CONFIG_MICRONUX_MIPI_BACKLIGHT_PERCENT) /
                  UINT32_C(100));
    esp_err_t result = write_backlight_register(
        MICRONUX_DSI_BACKLIGHT_REGISTER, brightness);
    if (result != ESP_OK) {
        release_backlight_bus();
        ESP_RETURN_ON_ERROR(result, TAG, "enable MIPI backlight");
    }
    result = release_backlight_device();
    if (result != ESP_OK) {
        release_backlight_bus();
        ESP_RETURN_ON_ERROR(result, TAG,
                            "release loader backlight device");
    }

    ESP_LOGI(TAG,
             "MICRONUX:M6:DSI state=ready profile=%s resolution=%ux%u"
             " lanes=%u lane_mbps=%u format=rgb565 pattern=framebuffer"
             " fb=%p bytes=%zu ownership=loader backlight=%u"
             " i2c=retained-for-linux",
             s_profile.name, s_profile.width, s_profile.height,
             MICRONUX_DSI_LANES, s_profile.lane_mbps,
             framebuffer, framebuffer_size,
             CONFIG_MICRONUX_MIPI_BACKLIGHT_PERCENT);
    return ESP_OK;
#endif
}

esp_err_t micronux_mipi_dsi_handoff(void)
{
#if !CONFIG_MICRONUX_M7_EARLY_UMODE_DENY || \
    !CONFIG_MICRONUX_MIPI_DSI || CONFIG_MICRONUX_MIPI_PANEL_UNSELECTED
    return ESP_OK;
#else
    if (s_panel == NULL) {
        ESP_LOGE(TAG,
                 "MICRONUX:M7:DSI-HANDOFF state=fail reason=panel-missing");
        return ESP_ERR_INVALID_STATE;
    }

    if (s_i2c_bus == NULL || s_backlight != NULL) {
        ESP_LOGE(TAG,
                 "MICRONUX:M7:DSI-HANDOFF state=fail"
                 " reason=i2c-not-transferable");
        return ESP_ERR_INVALID_STATE;
    }

    esp_lcd_dpi_panel_handoff_t dsi_handoff = {0};
    ESP_RETURN_ON_ERROR(
        esp_lcd_dpi_panel_prepare_handoff(s_panel, &dsi_handoff), TAG,
        "prepare descriptor-ring MIPI DPI handoff");

    const uintptr_t framebuffer_start =
        dma_bus_address((uintptr_t)dsi_handoff.frame_buffer);
    const uintptr_t framebuffer_end =
        framebuffer_start + dsi_handoff.frame_buffer_size;
    const uintptr_t descriptor_start =
        dma_bus_address((uintptr_t)dsi_handoff.dma_descriptor);
    const uintptr_t descriptor_end =
        descriptor_start + dsi_handoff.dma_descriptor_size;
    if (framebuffer_end < framebuffer_start ||
        framebuffer_start < MICRONUX_DSI_LOADER_PSRAM_START ||
        framebuffer_end > MICRONUX_DSI_LOADER_PSRAM_END ||
        descriptor_end < descriptor_start ||
        descriptor_start < MICRONUX_DSI_INTERNAL_SRAM_START ||
        descriptor_end > MICRONUX_DSI_INTERNAL_SRAM_END ||
        (descriptor_start &
         (MICRONUX_DSI_DMA_DESCRIPTOR_ALIGNMENT - 1U)) != 0 ||
        dsi_handoff.dma_descriptor_size !=
            MICRONUX_DSI_DMA_DESCRIPTOR_ALIGNMENT *
                MICRONUX_DSI_DMA_DESCRIPTOR_COUNT ||
        dsi_handoff.dma_channel < 0 || dsi_handoff.dma_channel > 3) {
        ESP_LOGE(TAG,
                 "MICRONUX:M7:DSI-HANDOFF state=fail"
                 " reason=resource-bounds fb=[%08" PRIxPTR ",%08" PRIxPTR
                 ") desc=[%08" PRIxPTR ",%08" PRIxPTR ") channel=%d",
                 framebuffer_start, framebuffer_end,
                 descriptor_start, descriptor_end,
                 dsi_handoff.dma_channel);
        return ESP_ERR_INVALID_STATE;
    }

    REG_WRITE(I2C_INT_ENA_REG(MICRONUX_DSI_I2C_PORT), 0);
    REG_WRITE(I2C_INT_CLR_REG(MICRONUX_DSI_I2C_PORT), UINT32_MAX);

    micronux_display_handoff_v1_t *const handoff =
        (micronux_display_handoff_v1_t *)
            MICRONUX_DISPLAY_HANDOFF_ADDRESS;
    *handoff = (micronux_display_handoff_v1_t) {
        .magic = MICRONUX_DISPLAY_HANDOFF_MAGIC,
        .abi_version = MICRONUX_DISPLAY_HANDOFF_ABI_VERSION,
        .struct_size = sizeof(*handoff),
        .flags = MICRONUX_DISPLAY_FLAG_ACTIVE |
                 MICRONUX_DISPLAY_FLAG_RGB565 |
                 MICRONUX_DISPLAY_FLAG_DMA_RING |
                 MICRONUX_DISPLAY_FLAG_I2C_TRANSFERRED,
        .width = s_profile.width,
        .height = s_profile.height,
        .stride = (uint32_t)s_profile.width * sizeof(uint16_t),
        .framebuffer_address = (uint32_t)framebuffer_start,
        .framebuffer_size = (uint32_t)dsi_handoff.frame_buffer_size,
        .dma_descriptor_address = (uint32_t)descriptor_start,
        .dma_descriptor_size =
            (uint32_t)dsi_handoff.dma_descriptor_size,
        .dma_channel = (uint32_t)dsi_handoff.dma_channel,
        .i2c_address = MICRONUX_DSI_BACKLIGHT_ADDRESS,
        .backlight_register = MICRONUX_DSI_BACKLIGHT_REGISTER,
        .backlight_brightness =
            (UINT32_C(255) * CONFIG_MICRONUX_MIPI_BACKLIGHT_PERCENT) /
            UINT32_C(100),
    };
    handoff->crc32 = esp_rom_crc32_le(
        0, (const uint8_t *)handoff,
        offsetof(micronux_display_handoff_v1_t, crc32));

    ESP_RETURN_ON_ERROR(
        esp_cache_msync(dsi_handoff.frame_buffer,
                        dsi_handoff.frame_buffer_size,
                        ESP_CACHE_MSYNC_FLAG_DIR_C2M |
                        ESP_CACHE_MSYNC_FLAG_TYPE_DATA),
        TAG, "flush MIPI framebuffer for handoff");
    ESP_RETURN_ON_ERROR(
        esp_cache_msync(handoff, sizeof(*handoff),
                        ESP_CACHE_MSYNC_FLAG_DIR_C2M |
                        ESP_CACHE_MSYNC_FLAG_TYPE_DATA |
                        ESP_CACHE_MSYNC_FLAG_UNALIGNED),
        TAG, "flush MIPI handoff contract");

    s_dma_policy = (micronux_display_dma_policy_t) {
        .framebuffer_start = align_down_4k((uint32_t)framebuffer_start),
        .framebuffer_end = align_up_4k((uint32_t)framebuffer_end),
        .descriptor_start = align_down_4k((uint32_t)descriptor_start),
        .descriptor_end = align_up_4k((uint32_t)descriptor_end),
        .fifo_start = MICRONUX_DSI_FIFO_WINDOW_START,
        .fifo_end = MICRONUX_DSI_FIFO_WINDOW_END,
        .dma_channel = (uint32_t)dsi_handoff.dma_channel,
    };
    s_dma_policy_ready = true;

    ESP_LOGI(TAG,
             "MICRONUX:M7:DSI-HANDOFF state=ready owner=linux-pending"
             " pattern=framebuffer dma=descriptor-ring channel=%d rearm=linux"
             " fb=[%08" PRIxPTR ",%08" PRIxPTR ")"
             " desc=[%08" PRIxPTR ",%08" PRIxPTR ") i2c=transferred"
             " contract=%08" PRIx32 " crc32=%08" PRIx32,
             dsi_handoff.dma_channel,
             framebuffer_start, framebuffer_end,
             descriptor_start, descriptor_end,
             MICRONUX_DISPLAY_HANDOFF_ADDRESS, handoff->crc32);
    return ESP_OK;
#endif
}

bool micronux_mipi_dsi_dma_policy(micronux_display_dma_policy_t *policy)
{
#if !CONFIG_MICRONUX_M7_EARLY_UMODE_DENY || \
    !CONFIG_MICRONUX_MIPI_DSI || CONFIG_MICRONUX_MIPI_PANEL_UNSELECTED
    (void)policy;
    return false;
#else
    if (!s_dma_policy_ready || policy == NULL) {
        return false;
    }
    *policy = s_dma_policy;
    return true;
#endif
}
