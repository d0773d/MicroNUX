// SPDX-License-Identifier: MIT

#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>

#include "sdkconfig.h"
#include "esp_cache.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_lcd_mipi_dsi.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_ldo_regulator.h"
#include "esp_log.h"
#include "esp_private/periph_ctrl.h"
#include "esp_rom_crc.h"
#include "esp_rom_sys.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "hal/mipi_dsi_ll.h"
#include "hal/mipi_dsi_brg_ll.h"
#include "hal/mipi_dsi_host_ll.h"
#include "hal/mipi_dsi_phy_ll.h"
#include "soc/dw_gdma_struct.h"
#include "soc/hp_sys_clkrst_reg.h"
#include "soc/i2c_reg.h"
#include "soc/pmu_reg.h"
#include "soc/soc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "micronux_mipi_dsi.h"
#include "micronux_panel_guard.h"

static const char *const TAG = "micronux_dsi";

#if CONFIG_MICRONUX_MIPI_DSI || CONFIG_MICRONUX_MIPI_DSI_PROBE || \
    CONFIG_MICRONUX_M9_JD9365_COLD_RELINQUISH
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

#if CONFIG_MICRONUX_M9_JD9365_COLD_RELINQUISH

#define MICRONUX_COLD_FRAME_WIDTH UINT32_C(800)
#define MICRONUX_COLD_FRAME_HEIGHT UINT32_C(1280)
#define MICRONUX_COLD_FRAME_STRIDE UINT32_C(1600)
#define MICRONUX_COLD_FRAME_SIZE UINT32_C(2048000)
#define MICRONUX_COLD_PIXEL_CLOCK_HZ UINT32_C(60000000)
#define MICRONUX_COLD_LANE_BIT_RATE_MBPS UINT32_C(1000)
#define MICRONUX_COLD_DATA_LANES UINT32_C(2)
#define MICRONUX_COLD_HSYNC_PULSE UINT32_C(20)
#define MICRONUX_COLD_HSYNC_BACK_PORCH UINT32_C(20)
#define MICRONUX_COLD_HSYNC_FRONT_PORCH UINT32_C(40)
#define MICRONUX_COLD_VSYNC_PULSE UINT32_C(4)
#define MICRONUX_COLD_VSYNC_BACK_PORCH UINT32_C(10)
#define MICRONUX_COLD_VSYNC_FRONT_PORCH UINT32_C(30)
#define MICRONUX_COLD_PMS_PAGE_SIZE UINT32_C(4096)
#define MICRONUX_COLD_FRONT_ALIGNMENT MICRONUX_COLD_PMS_PAGE_SIZE
#define MICRONUX_COLD_DESCRIPTOR_ALIGNMENT MICRONUX_COLD_PMS_PAGE_SIZE
#define MICRONUX_COLD_DESCRIPTOR_SIZE UINT32_C(192)
#define MICRONUX_COLD_DESCRIPTOR_BACKING_SIZE MICRONUX_COLD_PMS_PAGE_SIZE
#define MICRONUX_COLD_DMA_CHANNEL UINT32_C(0)
#define MICRONUX_COLD_GDMA_IRQ_SOURCE UINT32_C(24)
#define MICRONUX_COLD_GDMA_CLIC_IRQ UINT32_C(18)
#define MICRONUX_COLD_GDMA_CHANNEL_COUNT UINT32_C(4)
#define MICRONUX_COLD_GDMA_ROUTE_REG UINT32_C(0x500d6060)
#define MICRONUX_COLD_INTERRUPT_MAP_MASK UINT32_C(0x3f)
#define MICRONUX_COLD_PSRAM_START UINT32_C(0x48000000)
#define MICRONUX_COLD_PSRAM_END UINT32_C(0x48400000)
#define MICRONUX_COLD_INTERNAL_SRAM_START UINT32_C(0x4ff00000)
#define MICRONUX_COLD_INTERNAL_SRAM_END UINT32_C(0x4ffc0000)
#define MICRONUX_COLD_SD_DMA_START UINT32_C(0x4ff80000)
#define MICRONUX_COLD_SD_DMA_END UINT32_C(0x4ff82000)
#define MICRONUX_COLD_NONCACHE_OFFSET UINT32_C(0x40000000)
#define MICRONUX_COLD_DISPLAY_CONTROL_REGISTER UINT8_C(0x95)
#define MICRONUX_COLD_BACKLIGHT_REGISTER UINT8_C(0x96)
#define MICRONUX_COLD_DISPLAY_RESET_ASSERT_COMMAND UINT8_C(0x11)
#define MICRONUX_COLD_DISPLAY_RESET_PREPARE_COMMAND UINT8_C(0x13)
#define MICRONUX_COLD_DISPLAY_REVEAL_COMMAND UINT8_C(0x17)
#define MICRONUX_COLD_BACKLIGHT_BRIGHTNESS UINT32_C(63)
#define MICRONUX_COLD_PWM_ZERO_SETTLE_MS UINT32_C(100)
#define MICRONUX_COLD_RESET_HOLD_MS UINT32_C(10)
#define MICRONUX_COLD_GDMA_POLL_LIMIT UINT32_C(10000)

_Static_assert((MICRONUX_COLD_FRAME_SIZE % MICRONUX_COLD_PMS_PAGE_SIZE) == 0,
               "cold framebuffer must occupy whole PMS pages");
_Static_assert(MICRONUX_COLD_DESCRIPTOR_SIZE <=
                   MICRONUX_COLD_DESCRIPTOR_BACKING_SIZE,
               "published descriptors must fit their isolated backing page");

static void *s_cold_frontbuffer;
static void *s_cold_descriptors;
static micronux_display_dma_policy_t s_cold_dma_policy;
static bool s_cold_dma_policy_ready;
static bool s_cold_external_command_sequence_ready;
static bool s_cold_quiescence_ready;
static bool s_cold_handoff_ready;
static bool s_cold_contract_staged;

static uintptr_t cold_dma_bus_address(uintptr_t address)
{
    if (address >= UINT32_C(0x80000000)) {
        return address - MICRONUX_COLD_NONCACHE_OFFSET;
    }
    return address;
}

static uint32_t cold_align_down_4k(uint32_t address)
{
    return address & ~UINT32_C(0xfff);
}

static uint32_t cold_align_up_4k(uint32_t address)
{
    return (address + UINT32_C(0xfff)) & ~UINT32_C(0xfff);
}

static void cold_delay_at_least_ms(uint32_t milliseconds)
{
    vTaskDelay(pdMS_TO_TICKS(milliseconds) + 2U);
}

static esp_err_t cold_cache_writeback(void *address, size_t size)
{
    return esp_cache_msync(address, size,
                           ESP_CACHE_MSYNC_FLAG_DIR_C2M |
                               ESP_CACHE_MSYNC_FLAG_TYPE_DATA |
                               ESP_CACHE_MSYNC_FLAG_UNALIGNED);
}

static esp_err_t cold_cache_writeback_invalidate(void *address, size_t size)
{
    return esp_cache_msync(address, size,
                           ESP_CACHE_MSYNC_FLAG_DIR_C2M |
                               ESP_CACHE_MSYNC_FLAG_TYPE_DATA |
                               ESP_CACHE_MSYNC_FLAG_INVALIDATE |
                               ESP_CACHE_MSYNC_FLAG_UNALIGNED);
}

static esp_err_t cold_invalidate_contract(void)
{
    micronux_display_handoff_v3_t *const handoff =
        (micronux_display_handoff_v3_t *)MICRONUX_DISPLAY_HANDOFF_ADDRESS;

    memset(handoff, 0, sizeof(*handoff));
    ESP_RETURN_ON_ERROR(cold_cache_writeback(handoff, sizeof(*handoff)), TAG,
                        "invalidate prior display contract");
    return ESP_OK;
}

static esp_err_t cold_write_adapter(i2c_master_dev_handle_t device,
                                    uint8_t reg, uint8_t value)
{
    const uint8_t command[] = {reg, value};
    return i2c_master_transmit(device, command, sizeof(command), 50);
}

static esp_err_t cold_release_i2c(i2c_master_bus_handle_t bus,
                                  i2c_master_dev_handle_t device)
{
    ESP_RETURN_ON_ERROR(i2c_master_bus_rm_device(device), TAG,
                        "release cold display device");
    ESP_RETURN_ON_ERROR(i2c_del_master_bus(bus), TAG,
                        "release cold display I2C bus");
    return ESP_OK;
}

static esp_err_t cold_execute_external_command_sequence(bool log_success)
{
    i2c_master_bus_handle_t bus = NULL;
    i2c_master_dev_handle_t device = NULL;
    const i2c_master_bus_config_t bus_config = {
        .i2c_port = MICRONUX_DSI_I2C_PORT,
        .sda_io_num = MICRONUX_DSI_I2C_SDA,
        .scl_io_num = MICRONUX_DSI_I2C_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    const i2c_device_config_t device_config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = MICRONUX_DSI_BACKLIGHT_ADDRESS,
        .scl_speed_hz = 100000,
    };

    esp_err_t result = i2c_new_master_bus(&bus_config, &bus);
    if (result != ESP_OK) {
        return result;
    }
    result = i2c_master_bus_add_device(bus, &device_config, &device);
    if (result != ESP_OK) {
        (void)i2c_del_master_bus(bus);
        return result;
    }

    result = cold_write_adapter(device,
                                MICRONUX_COLD_BACKLIGHT_REGISTER, 0);
    if (result == ESP_OK) {
        result = cold_write_adapter(
            device, MICRONUX_COLD_DISPLAY_CONTROL_REGISTER,
            MICRONUX_COLD_DISPLAY_RESET_PREPARE_COMMAND);
    }
    if (result == ESP_OK) {
        cold_delay_at_least_ms(MICRONUX_COLD_PWM_ZERO_SETTLE_MS);
        result = cold_write_adapter(device,
                                    MICRONUX_COLD_DISPLAY_CONTROL_REGISTER,
                                    MICRONUX_COLD_DISPLAY_RESET_ASSERT_COMMAND);
    }
    if (result == ESP_OK) {
        cold_delay_at_least_ms(MICRONUX_COLD_RESET_HOLD_MS);
    }

    if (result != ESP_OK) {
        (void)cold_write_adapter(device,
                                 MICRONUX_COLD_BACKLIGHT_REGISTER, 0);
        (void)cold_write_adapter(device,
                                 MICRONUX_COLD_DISPLAY_CONTROL_REGISTER,
                                 MICRONUX_COLD_DISPLAY_RESET_ASSERT_COMMAND);
        cold_delay_at_least_ms(MICRONUX_COLD_RESET_HOLD_MS);
    }
    const esp_err_t release_result = cold_release_i2c(bus, device);
    if (result == ESP_OK) {
        result = release_result;
    }
    if (result != ESP_OK) {
        return result;
    }

    if (log_success) {
        ESP_LOGI(TAG,
                 "MICRONUX:M9.2:COLD-EXTERNAL state=ready"
                 " pwm-zero-write=acked reset-prepare-write=acked"
                 " pwm-zero-settle=elapsed reset-assert-write=acked"
                 " reset-hold=elapsed i2c=released");
    }
    return ESP_OK;
}

esp_err_t micronux_mipi_dsi_cold_early_dark(void)
{
    ESP_RETURN_ON_ERROR(cold_invalidate_contract(), TAG,
                        "invalidate prior display contract");
    ESP_RETURN_ON_ERROR(cold_execute_external_command_sequence(true), TAG,
                        "execute early cold display command sequence");
    s_cold_external_command_sequence_ready = true;
    return ESP_OK;
}

void micronux_mipi_dsi_cold_fail_cleanup(void)
{
    (void)cold_invalidate_contract();
    (void)cold_execute_external_command_sequence(false);
}

static esp_err_t cold_quiesce_gdma(void)
{
    PERIPH_RCC_ATOMIC() {
        REG_SET_BIT(HP_SYS_CLKRST_SOC_CLK_CTRL0_REG,
                    HP_SYS_CLKRST_REG_GDMA_CPU_CLK_EN_M);
        REG_SET_BIT(HP_SYS_CLKRST_SOC_CLK_CTRL1_REG,
                    HP_SYS_CLKRST_REG_GDMA_SYS_CLK_EN_M);
        REG_CLR_BIT(HP_SYS_CLKRST_HP_RST_EN0_REG,
                    HP_SYS_CLKRST_REG_RST_EN_GDMA_M);
    }

    for (uint32_t channel = 0;
         channel < MICRONUX_COLD_GDMA_CHANNEL_COUNT; ++channel) {
        DW_GDMA.ch[channel].int_st_ena0.val = 0;
        DW_GDMA.ch[channel].int_st_ena1.val = 0;
        DW_GDMA.ch[channel].int_sig_ena0.val = 0;
        DW_GDMA.ch[channel].int_sig_ena1.val = 0;
        DW_GDMA.chen0.val = UINT32_C(0x100) << channel;

        uint32_t attempt = 0;
        while ((DW_GDMA.chen0.val & (UINT32_C(1) << channel)) != 0 &&
               attempt++ < MICRONUX_COLD_GDMA_POLL_LIMIT) {
            esp_rom_delay_us(1);
        }
        if ((DW_GDMA.chen0.val & (UINT32_C(1) << channel)) != 0) {
            DW_GDMA.chen1.val = UINT32_C(0x101) << channel;
            attempt = 0;
            while ((DW_GDMA.chen1.val &
                    (UINT32_C(0x101) << channel)) != 0 &&
                   attempt++ < MICRONUX_COLD_GDMA_POLL_LIMIT) {
                esp_rom_delay_us(1);
            }
        }
        if ((DW_GDMA.chen0.val & (UINT32_C(1) << channel)) != 0 ||
            (DW_GDMA.chen1.val & (UINT32_C(0x101) << channel)) != 0) {
            return ESP_ERR_TIMEOUT;
        }
        DW_GDMA.ch[channel].int_clr0.val = UINT32_MAX;
        DW_GDMA.ch[channel].int_clr1.val = UINT32_MAX;
    }

    PERIPH_RCC_ATOMIC() {
        REG_SET_BIT(HP_SYS_CLKRST_HP_RST_EN0_REG,
                    HP_SYS_CLKRST_REG_RST_EN_GDMA_M);
        REG_CLR_BIT(HP_SYS_CLKRST_SOC_CLK_CTRL1_REG,
                    HP_SYS_CLKRST_REG_GDMA_SYS_CLK_EN_M);
        REG_CLR_BIT(HP_SYS_CLKRST_SOC_CLK_CTRL0_REG,
                    HP_SYS_CLKRST_REG_GDMA_CPU_CLK_EN_M);
    }

    if ((REG_READ(HP_SYS_CLKRST_HP_RST_EN0_REG) &
         HP_SYS_CLKRST_REG_RST_EN_GDMA_M) == 0 ||
        (REG_READ(HP_SYS_CLKRST_SOC_CLK_CTRL0_REG) &
         HP_SYS_CLKRST_REG_GDMA_CPU_CLK_EN_M) != 0 ||
        (REG_READ(HP_SYS_CLKRST_SOC_CLK_CTRL1_REG) &
         HP_SYS_CLKRST_REG_GDMA_SYS_CLK_EN_M) != 0) {
        return ESP_ERR_INVALID_STATE;
    }
    return ESP_OK;
}

static esp_err_t cold_quiesce_dsi(void)
{
    PERIPH_RCC_ATOMIC() {
        REG_SET_BIT(HP_SYS_CLKRST_SOC_CLK_CTRL1_REG,
                    HP_SYS_CLKRST_REG_DSI_SYS_CLK_EN_M);
        REG_SET_BIT(HP_SYS_CLKRST_PERI_CLK_CTRL03_REG,
                    HP_SYS_CLKRST_REG_MIPI_DSI_DPHY_CFG_CLK_EN_M |
                        HP_SYS_CLKRST_REG_MIPI_DSI_DPHY_PLL_REFCLK_EN_M);
        REG_CLR_BIT(HP_SYS_CLKRST_HP_RST_EN0_REG,
                    HP_SYS_CLKRST_REG_RST_EN_DSI_BRG_M);
    }

    dsi_host_dev_t *const host = MIPI_DSI_LL_GET_HOST(0);
    dsi_brg_dev_t *const bridge = MIPI_DSI_LL_GET_BRG(0);
    mipi_dsi_brg_ll_force_enable_reg_clock(bridge, true);
    mipi_dsi_brg_ll_enable_dpi_output(bridge, false);
    mipi_dsi_brg_ll_update_dpi_config(bridge);
    mipi_dsi_brg_ll_enable_interrupt(bridge, UINT32_MAX, false);
    mipi_dsi_brg_ll_clear_interrupt_status(bridge, UINT32_MAX);
    mipi_dsi_brg_ll_enable(bridge, false);
    mipi_dsi_host_ll_dpi_set_pattern_type(host, MIPI_DSI_PATTERN_NONE);
    mipi_dsi_host_ll_enable_video_mode(host, false);
    host->int_msk0.val = UINT32_MAX;
    host->int_msk1.val = UINT32_MAX;
    host->lpclk_ctrl.val = 0;
    mipi_dsi_phy_ll_force_pll(host, false);
    mipi_dsi_phy_ll_enable_clock_lane(host, false);
    mipi_dsi_phy_ll_power_on_off(host, false);
    host->phy_rstz.phy_rstz = 0;
    mipi_dsi_host_ll_power_on_off(host, false);

    if (bridge->dpi_misc_config.dpi_en != 0 ||
        bridge->en.dsi_en != 0 || host->vid_mode_cfg.vpg_en != 0 ||
        host->lpclk_ctrl.val != 0 || host->pwr_up.shutdownz != 0 ||
        host->phy_rstz.phy_shutdownz != 0 ||
        host->phy_rstz.phy_enableclk != 0 ||
        host->phy_rstz.phy_forcepll != 0 ||
        host->phy_rstz.phy_rstz != 0) {
        return ESP_ERR_INVALID_STATE;
    }

    mipi_dsi_brg_ll_enable_ref_clock(bridge, false);
    mipi_dsi_brg_ll_force_enable_reg_clock(bridge, false);
    PERIPH_RCC_ATOMIC() {
        REG_SET_BIT(HP_SYS_CLKRST_HP_RST_EN0_REG,
                    HP_SYS_CLKRST_REG_RST_EN_DSI_BRG_M);
        REG_CLR_BIT(HP_SYS_CLKRST_PERI_CLK_CTRL03_REG,
                    HP_SYS_CLKRST_REG_MIPI_DSI_DPICLK_EN_M |
                        HP_SYS_CLKRST_REG_MIPI_DSI_DPHY_CFG_CLK_EN_M |
                        HP_SYS_CLKRST_REG_MIPI_DSI_DPHY_PLL_REFCLK_EN_M);
        REG_CLR_BIT(HP_SYS_CLKRST_SOC_CLK_CTRL1_REG,
                    HP_SYS_CLKRST_REG_DSI_SYS_CLK_EN_M);
    }

    if ((REG_READ(HP_SYS_CLKRST_HP_RST_EN0_REG) &
         HP_SYS_CLKRST_REG_RST_EN_DSI_BRG_M) == 0 ||
        (REG_READ(HP_SYS_CLKRST_PERI_CLK_CTRL03_REG) &
         (HP_SYS_CLKRST_REG_MIPI_DSI_DPICLK_EN_M |
          HP_SYS_CLKRST_REG_MIPI_DSI_DPHY_CFG_CLK_EN_M |
          HP_SYS_CLKRST_REG_MIPI_DSI_DPHY_PLL_REFCLK_EN_M)) != 0 ||
        (REG_READ(HP_SYS_CLKRST_SOC_CLK_CTRL1_REG) &
         HP_SYS_CLKRST_REG_DSI_SYS_CLK_EN_M) != 0) {
        return ESP_ERR_INVALID_STATE;
    }
    return ESP_OK;
}

static esp_err_t cold_disable_dphy_ldo(void)
{
    REG_CLR_BIT(PMU_EXT_LDO_P0_0P2A_REG, PMU_0P2A_XPD_0_M);
    return (REG_READ(PMU_EXT_LDO_P0_0P2A_REG) & PMU_0P2A_XPD_0_M) == 0
               ? ESP_OK
               : ESP_ERR_INVALID_STATE;
}

static bool cold_descriptors_are_zero(void)
{
    const uint8_t *const bytes = (const uint8_t *)s_cold_descriptors;
    for (size_t index = 0; index < MICRONUX_COLD_DESCRIPTOR_BACKING_SIZE;
         ++index) {
        if (bytes[index] != 0) {
            return false;
        }
    }
    return true;
}

static esp_err_t cold_reserve_dma_memory(void)
{
    s_cold_frontbuffer = heap_caps_aligned_alloc(
        MICRONUX_COLD_FRONT_ALIGNMENT, MICRONUX_COLD_FRAME_SIZE,
        MALLOC_CAP_SPIRAM | MALLOC_CAP_DMA | MALLOC_CAP_8BIT);
    s_cold_descriptors = heap_caps_aligned_alloc(
        MICRONUX_COLD_DESCRIPTOR_ALIGNMENT,
        MICRONUX_COLD_DESCRIPTOR_BACKING_SIZE,
        MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA | MALLOC_CAP_8BIT);
    if (s_cold_frontbuffer == NULL || s_cold_descriptors == NULL) {
        return ESP_ERR_NO_MEM;
    }

    const uintptr_t front =
        cold_dma_bus_address((uintptr_t)s_cold_frontbuffer);
    const uintptr_t front_end = front + MICRONUX_COLD_FRAME_SIZE;
    const uintptr_t descriptors =
        cold_dma_bus_address((uintptr_t)s_cold_descriptors);
    const uintptr_t descriptors_end =
        descriptors + MICRONUX_COLD_DESCRIPTOR_BACKING_SIZE;
    const uintptr_t back_end =
        MICRONUX_DISPLAY_BACKBUFFER_ADDRESS + MICRONUX_COLD_FRAME_SIZE;
    const uintptr_t spare_end =
        MICRONUX_DISPLAY_SPAREBUFFER_ADDRESS + MICRONUX_COLD_FRAME_SIZE;

    if (front_end < front || front < MICRONUX_COLD_PSRAM_START ||
        front_end > MICRONUX_COLD_PSRAM_END ||
        (front & (MICRONUX_COLD_FRONT_ALIGNMENT - 1U)) != 0 ||
        descriptors_end < descriptors ||
        descriptors < MICRONUX_COLD_INTERNAL_SRAM_START ||
        descriptors_end > MICRONUX_COLD_INTERNAL_SRAM_END ||
        (descriptors < MICRONUX_COLD_SD_DMA_END &&
         MICRONUX_COLD_SD_DMA_START < descriptors_end) ||
        (descriptors & (MICRONUX_COLD_DESCRIPTOR_ALIGNMENT - 1U)) != 0 ||
        back_end > MICRONUX_DISPLAY_SPAREBUFFER_ADDRESS ||
        spare_end > MICRONUX_DISPLAY_BACKBUFFER_POOL_END) {
        return ESP_ERR_INVALID_STATE;
    }

    memset(s_cold_frontbuffer, 0, MICRONUX_COLD_FRAME_SIZE);
    memset(s_cold_descriptors, 0, MICRONUX_COLD_DESCRIPTOR_BACKING_SIZE);
    ESP_RETURN_ON_ERROR(
        cold_cache_writeback(s_cold_frontbuffer, MICRONUX_COLD_FRAME_SIZE),
        TAG, "flush cold frontbuffer");
    ESP_RETURN_ON_ERROR(
        cold_cache_writeback(s_cold_descriptors,
                             MICRONUX_COLD_DESCRIPTOR_BACKING_SIZE),
        TAG, "flush cold descriptor storage");
    ESP_RETURN_ON_FALSE(cold_descriptors_are_zero(), ESP_ERR_INVALID_STATE,
                        TAG, "verify cleared cold descriptor storage");

    s_cold_dma_policy = (micronux_display_dma_policy_t) {
        .frontbuffer_start = cold_align_down_4k((uint32_t)front),
        .frontbuffer_end = cold_align_up_4k((uint32_t)front_end),
        .backbuffer_pool_start = MICRONUX_DISPLAY_BACKBUFFER_POOL_START,
        .backbuffer_pool_end = MICRONUX_DISPLAY_BACKBUFFER_POOL_END,
        .descriptor_start = cold_align_down_4k((uint32_t)descriptors),
        .descriptor_end = cold_align_up_4k((uint32_t)descriptors_end),
        .fifo_start = MICRONUX_DSI_FIFO_WINDOW_START,
        .fifo_end = MICRONUX_DSI_FIFO_WINDOW_END,
        .dma_channel = MICRONUX_COLD_DMA_CHANNEL,
    };
    s_cold_dma_policy_ready = true;
    ESP_LOGI(TAG,
             "MICRONUX:M9.2:COLD-MEMORY state=ready front=%08" PRIxPTR
             " bytes=%" PRIu32 " desc=%08" PRIxPTR
             " desc_bytes=%" PRIu32 " desc_page_bytes=%" PRIu32
             " descriptors=zero",
             front, MICRONUX_COLD_FRAME_SIZE, descriptors,
             MICRONUX_COLD_DESCRIPTOR_SIZE,
             MICRONUX_COLD_DESCRIPTOR_BACKING_SIZE);
    return ESP_OK;
}

static bool cold_reset_and_clock_state_matches(void)
{
    const uint32_t reset = REG_READ(HP_SYS_CLKRST_HP_RST_EN0_REG);
    const uint32_t clocks0 = REG_READ(HP_SYS_CLKRST_SOC_CLK_CTRL0_REG);
    const uint32_t clocks1 = REG_READ(HP_SYS_CLKRST_SOC_CLK_CTRL1_REG);
    const uint32_t peri03 = REG_READ(HP_SYS_CLKRST_PERI_CLK_CTRL03_REG);

    return (reset & HP_SYS_CLKRST_REG_RST_EN_DSI_BRG_M) != 0 &&
           (reset & HP_SYS_CLKRST_REG_RST_EN_GDMA_M) != 0 &&
           (clocks0 & HP_SYS_CLKRST_REG_GDMA_CPU_CLK_EN_M) == 0 &&
           (clocks1 & (HP_SYS_CLKRST_REG_GDMA_SYS_CLK_EN_M |
                       HP_SYS_CLKRST_REG_DSI_SYS_CLK_EN_M)) == 0 &&
           (peri03 & (HP_SYS_CLKRST_REG_MIPI_DSI_DPICLK_EN_M |
                      HP_SYS_CLKRST_REG_MIPI_DSI_DPHY_CFG_CLK_EN_M |
                      HP_SYS_CLKRST_REG_MIPI_DSI_DPHY_PLL_REFCLK_EN_M)) == 0 &&
           (REG_READ(PMU_EXT_LDO_P0_0P2A_REG) & PMU_0P2A_XPD_0_M) == 0;
}

esp_err_t micronux_mipi_dsi_cold_prepare(void)
{
    ESP_RETURN_ON_FALSE(s_cold_external_command_sequence_ready,
                        ESP_ERR_INVALID_STATE, TAG,
                        "early cold display command sequence incomplete");
    ESP_RETURN_ON_ERROR(cold_reserve_dma_memory(), TAG,
                        "reserve cold display DMA memory");
    ESP_RETURN_ON_ERROR(cold_quiesce_gdma(), TAG, "quiesce display GDMA");
    ESP_RETURN_ON_ERROR(cold_quiesce_dsi(), TAG, "quiesce DSI host/bridge");
    ESP_RETURN_ON_ERROR(cold_disable_dphy_ldo(), TAG,
                        "disable D-PHY LDO");
    ESP_RETURN_ON_FALSE(cold_reset_and_clock_state_matches(),
                        ESP_ERR_INVALID_STATE, TAG,
                        "verify cold display reset and clock state");

    s_cold_quiescence_ready = true;
    ESP_LOGI(TAG,
             "MICRONUX:M9.2:COLD-PREPARE state=ready abi=3 panel=jd9365"
             " display-init=none splash=none source=none gdma=quiesced"
             " host-bridge=reset dphy-ldo=off");
    return ESP_OK;
}

esp_err_t micronux_mipi_dsi_cold_handoff(void)
{
    ESP_RETURN_ON_FALSE(s_cold_external_command_sequence_ready &&
                            s_cold_quiescence_ready &&
                            s_cold_dma_policy_ready,
                        ESP_ERR_INVALID_STATE, TAG,
                        "cold display preparation incomplete");
    ESP_RETURN_ON_FALSE(cold_reset_and_clock_state_matches(),
                        ESP_ERR_INVALID_STATE, TAG,
                        "cold display state changed before handoff");
    ESP_RETURN_ON_FALSE(cold_descriptors_are_zero(), ESP_ERR_INVALID_STATE,
                        TAG, "cold descriptors changed before handoff");

    s_cold_handoff_ready = true;
    ESP_LOGI(TAG,
             "MICRONUX:M9.2:COLD-HANDOFF state=ready"
             " contract=invalid route=pending dma-pms=pending i2c=released");
    return ESP_OK;
}

esp_err_t micronux_mipi_dsi_cold_stage(void)
{
    ESP_RETURN_ON_FALSE(s_cold_handoff_ready && s_cold_dma_policy_ready,
                        ESP_ERR_INVALID_STATE, TAG,
                        "cold display handoff not ready");
    ESP_RETURN_ON_FALSE(cold_reset_and_clock_state_matches(),
                        ESP_ERR_INVALID_STATE, TAG,
                        "cold display state changed before publish");
    ESP_RETURN_ON_FALSE(cold_descriptors_are_zero(), ESP_ERR_INVALID_STATE,
                        TAG, "cold descriptors changed before publish");
    ESP_RETURN_ON_FALSE(
        (REG_READ(MICRONUX_COLD_GDMA_ROUTE_REG) &
         MICRONUX_COLD_INTERRUPT_MAP_MASK) == MICRONUX_COLD_GDMA_CLIC_IRQ,
        ESP_ERR_INVALID_STATE, TAG, "verify cold GDMA IRQ route");

    const uint32_t front = (uint32_t)cold_dma_bus_address(
        (uintptr_t)s_cold_frontbuffer);
    const uint32_t descriptors = (uint32_t)cold_dma_bus_address(
        (uintptr_t)s_cold_descriptors);
    micronux_display_handoff_v3_t contract = {
        .magic = MICRONUX_DISPLAY_HANDOFF_MAGIC,
        .abi_version = MICRONUX_DISPLAY_COLD_HANDOFF_ABI_VERSION,
        .struct_size = sizeof(contract),
        .flags = MICRONUX_DISPLAY_V3_REQUIRED_FLAGS,
        .boot_mode = MICRONUX_DISPLAY_COLD_BOOT_MODE,
        .ownership_state = MICRONUX_DISPLAY_COLD_OWNER_LINUX_PENDING,
        .silicon_revision = 103,
        .panel_profile_id =
            MICRONUX_DISPLAY_COLD_PANEL_PROFILE_JD9365_WAVESHARE_10_1,
        .width = MICRONUX_COLD_FRAME_WIDTH,
        .height = MICRONUX_COLD_FRAME_HEIGHT,
        .stride = MICRONUX_COLD_FRAME_STRIDE,
        .pixel_format = MICRONUX_DISPLAY_COLD_PIXEL_FORMAT_RGB565_LE,
        .pixel_clock_hz = MICRONUX_COLD_PIXEL_CLOCK_HZ,
        .lane_bit_rate_mbps = MICRONUX_COLD_LANE_BIT_RATE_MBPS,
        .data_lanes = MICRONUX_COLD_DATA_LANES,
        .buffer_count = MICRONUX_DISPLAY_BUFFER_COUNT,
        .hsync_pulse_width = MICRONUX_COLD_HSYNC_PULSE,
        .hsync_back_porch = MICRONUX_COLD_HSYNC_BACK_PORCH,
        .hsync_front_porch = MICRONUX_COLD_HSYNC_FRONT_PORCH,
        .vsync_pulse_width = MICRONUX_COLD_VSYNC_PULSE,
        .vsync_back_porch = MICRONUX_COLD_VSYNC_BACK_PORCH,
        .vsync_front_porch = MICRONUX_COLD_VSYNC_FRONT_PORCH,
        .framebuffer_address = {
            front,
            MICRONUX_DISPLAY_BACKBUFFER_ADDRESS,
            MICRONUX_DISPLAY_SPAREBUFFER_ADDRESS,
        },
        .framebuffer_size = MICRONUX_COLD_FRAME_SIZE,
        .dma_descriptor_address = descriptors,
        .dma_descriptor_size = MICRONUX_COLD_DESCRIPTOR_SIZE,
        .dma_descriptor_count = MICRONUX_DISPLAY_BUFFER_COUNT,
        .dma_channel = MICRONUX_COLD_DMA_CHANNEL,
        .dsi_fifo_address = MICRONUX_DSI_FIFO_ADDRESS,
        .gdma_irq_source = MICRONUX_COLD_GDMA_IRQ_SOURCE,
        .gdma_clic_irq = MICRONUX_COLD_GDMA_CLIC_IRQ,
        .i2c_port = MICRONUX_DSI_I2C_PORT,
        .i2c_sda_gpio = MICRONUX_DSI_I2C_SDA,
        .i2c_scl_gpio = MICRONUX_DSI_I2C_SCL,
        .i2c_rate_hz = 100000,
        .i2c_address = MICRONUX_DSI_BACKLIGHT_ADDRESS,
        .display_control_register =
            MICRONUX_COLD_DISPLAY_CONTROL_REGISTER,
        .backlight_register = MICRONUX_COLD_BACKLIGHT_REGISTER,
        .display_control_reset_assert_command =
            MICRONUX_COLD_DISPLAY_RESET_ASSERT_COMMAND,
        .display_control_reset_prepare_command =
            MICRONUX_COLD_DISPLAY_RESET_PREPARE_COMMAND,
        .display_control_reveal_command =
            MICRONUX_COLD_DISPLAY_REVEAL_COMMAND,
        .backlight_brightness = MICRONUX_COLD_BACKLIGHT_BRIGHTNESS,
        .reset_hold_ms = MICRONUX_COLD_RESET_HOLD_MS,
        .pwm_zero_settle_ms =
            MICRONUX_COLD_PWM_ZERO_SETTLE_MS,
        .panel_payload_crc32 = MICRONUX_DISPLAY_COLD_PANEL_PAYLOAD_CRC32,
        .quiesce_sequence_id =
            MICRONUX_DISPLAY_COLD_QUIESCE_SEQUENCE_ID,
    };
    contract.crc32 = esp_rom_crc32_le(
        0, (const uint8_t *)&contract,
        offsetof(micronux_display_handoff_v3_t, crc32));

    micronux_display_handoff_v3_t *const handoff =
        (micronux_display_handoff_v3_t *)MICRONUX_DISPLAY_HANDOFF_ADDRESS;
    handoff->magic = 0;
    memcpy((uint8_t *)handoff + sizeof(handoff->magic),
           (const uint8_t *)&contract + sizeof(contract.magic),
           sizeof(contract) - sizeof(contract.magic));
    /*
     * Invalidate after writeback so Linux cannot inherit a cache line whose
     * magic is still zero after the final uncached commit updates memory.
     */
    ESP_RETURN_ON_ERROR(
        cold_cache_writeback_invalidate(handoff, sizeof(*handoff)), TAG,
        "flush and invalidate staged cold display contract");
    s_cold_contract_staged = true;

    ESP_LOGI(TAG,
             "MICRONUX:M9.2:COLD-STAGE state=ready abi=3 size=0x%04x"
             " flags=0x%08" PRIx32 " route=ready dma-pms=ready"
             " panel-payload-crc=cea07f9b contract=invalid"
             " crc32=%08" PRIx32,
             (unsigned)sizeof(contract), contract.flags, contract.crc32);
    return ESP_OK;
}

bool micronux_mipi_dsi_cold_staged(void)
{
    return s_cold_contract_staged;
}

void micronux_mipi_dsi_cold_commit(void)
{
    volatile uint32_t *const uncached_magic =
        (volatile uint32_t *)(uintptr_t)(MICRONUX_DISPLAY_HANDOFF_ADDRESS +
                                        MICRONUX_COLD_NONCACHE_OFFSET);
    *uncached_magic = MICRONUX_DISPLAY_HANDOFF_MAGIC;
    __asm__ __volatile__("fence rw, rw" ::: "memory");
}

#else

esp_err_t micronux_mipi_dsi_cold_prepare(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t micronux_mipi_dsi_cold_early_dark(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

void micronux_mipi_dsi_cold_fail_cleanup(void)
{
}

esp_err_t micronux_mipi_dsi_cold_handoff(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t micronux_mipi_dsi_cold_stage(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}

bool micronux_mipi_dsi_cold_staged(void)
{
    return false;
}

void micronux_mipi_dsi_cold_commit(void)
{
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
#define MICRONUX_DSI_DISPLAY_CONTROL_REGISTER UINT8_C(0x95)
#define MICRONUX_DSI_BACKLIGHT_REGISTER UINT8_C(0x96)
#define MICRONUX_DSI_DISPLAY_RESET_ASSERTED UINT8_C(0x11)
#define MICRONUX_DSI_DISPLAY_RESET_RELEASED UINT8_C(0x13)
#define MICRONUX_DSI_DISPLAY_BACKLIGHT_ENABLED UINT8_C(0x17)
#define MICRONUX_DSI_LOADER_PSRAM_START UINT32_C(0x48000000)
#define MICRONUX_DSI_LOADER_PSRAM_END UINT32_C(0x48400000)
#define MICRONUX_DSI_INTERNAL_SRAM_START UINT32_C(0x4ff00000)
#define MICRONUX_DSI_INTERNAL_SRAM_END UINT32_C(0x4ffc0000)
#define MICRONUX_DSI_NONCACHE_OFFSET UINT32_C(0x40000000)
#define MICRONUX_DSI_DMA_DESCRIPTOR_ALIGNMENT UINT32_C(64)
#define MICRONUX_DSI_FRAMEBUFFER_ALIGNMENT UINT32_C(8)
#define MICRONUX_DSI_DMA_DESCRIPTOR_COUNT MICRONUX_DISPLAY_BUFFER_COUNT
#define MICRONUX_DSI_BACKLIGHT_OFF_SETTLE_MS UINT32_C(100)
#define MICRONUX_DSI_RESET_PREPARE_MS UINT32_C(5)
#define MICRONUX_DSI_RESET_ASSERT_MS UINT32_C(10)
#define MICRONUX_DSI_RESET_RELEASE_MS UINT32_C(130)
#if CONFIG_MICRONUX_MIPI_PANEL_JD9365_800_1280
#define MICRONUX_DSI_CLOCK_LANE_FORCE_HS 0
#define MICRONUX_DSI_LINK_LOG " lane_clock=auto video_lp=enabled frame_ack=disabled"
#else
#define MICRONUX_DSI_CLOCK_LANE_FORCE_HS 0
#define MICRONUX_DSI_LINK_LOG " lane_clock=auto video_lp=panel-default"
#endif
#define MICRONUX_SPLASH_BACKGROUND UINT16_C(0x0842)
#define MICRONUX_SPLASH_FOREGROUND UINT16_C(0xffff)
#define MICRONUX_SPLASH_ACCENT UINT16_C(0x05ff)
#define MICRONUX_SPLASH_BAR_HEIGHT 28U
#define MICRONUX_SPLASH_PROGRESS_GAP 36U
#define MICRONUX_SPLASH_PERCENT_GAP 18U

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
#if !CONFIG_MICRONUX_DISPLAY_STANDALONE
static micronux_display_dma_policy_t s_dma_policy;
static bool s_dma_policy_ready;
#endif
static void *s_framebuffer;
static size_t s_framebuffer_size;
static uint8_t s_progress_percent;
static uint16_t s_dpi_clock_mhz;
static esp_lcd_video_timing_t s_video_timing;
static esp_err_t write_backlight_register(uint8_t reg, uint8_t value)
{
    const uint8_t command[] = {reg, value};
    return i2c_master_transmit(s_backlight, command, sizeof(command), 50);
}

static void best_effort_display_dark(void)
{
    if (s_backlight == NULL) {
        return;
    }
    /* Either successful write independently prevents physical illumination. */
    (void)write_backlight_register(MICRONUX_DSI_DISPLAY_CONTROL_REGISTER,
                                   MICRONUX_DSI_DISPLAY_RESET_RELEASED);
    (void)write_backlight_register(MICRONUX_DSI_BACKLIGHT_REGISTER, 0);
}

#if !CONFIG_MICRONUX_DISPLAY_STANDALONE
static esp_err_t disable_backlight_gate(void)
{
    const esp_err_t pwm_result = write_backlight_register(
        MICRONUX_DSI_BACKLIGHT_REGISTER, 0);
    const esp_err_t gate_result = write_backlight_register(
        MICRONUX_DSI_DISPLAY_CONTROL_REGISTER,
        MICRONUX_DSI_DISPLAY_RESET_RELEASED);

    if (gate_result != ESP_OK) {
        best_effort_display_dark();
        return gate_result;
    }
    if (pwm_result != ESP_OK) {
        ESP_LOGW(TAG,
                 "backlight PWM blank failed (%s); hardware gate is disabled",
                 esp_err_to_name(pwm_result));
    }
    return ESP_OK;
}
#endif

static void delay_at_least_ms(uint32_t milliseconds)
{
    /* Two extra ticks cover truncation and an arbitrary current tick phase. */
    vTaskDelay(pdMS_TO_TICKS(milliseconds) + 2U);
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

#if !CONFIG_MICRONUX_DISPLAY_STANDALONE
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
#endif

typedef struct {
    char character;
    uint8_t rows[7];
} micronux_splash_glyph_t;

static const micronux_splash_glyph_t s_splash_glyphs[] = {
    {'%', {0x11, 0x02, 0x04, 0x08, 0x11, 0x00, 0x00}},
    {'0', {0x0e, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0e}},
    {'1', {0x04, 0x0c, 0x04, 0x04, 0x04, 0x04, 0x0e}},
    {'2', {0x0e, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1f}},
    {'3', {0x1e, 0x01, 0x01, 0x0e, 0x01, 0x01, 0x1e}},
    {'4', {0x02, 0x06, 0x0a, 0x12, 0x1f, 0x02, 0x02}},
    {'5', {0x1f, 0x10, 0x10, 0x1e, 0x01, 0x01, 0x1e}},
    {'6', {0x0e, 0x10, 0x10, 0x1e, 0x11, 0x11, 0x0e}},
    {'7', {0x1f, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08}},
    {'8', {0x0e, 0x11, 0x11, 0x0e, 0x11, 0x11, 0x0e}},
    {'9', {0x0e, 0x11, 0x11, 0x0f, 0x01, 0x01, 0x0e}},
    {'B', {0x1e, 0x11, 0x11, 0x1e, 0x11, 0x11, 0x1e}},
    {'C', {0x0e, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0e}},
    {'G', {0x0e, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0f}},
    {'I', {0x1f, 0x04, 0x04, 0x04, 0x04, 0x04, 0x1f}},
    {'L', {0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1f}},
    {'M', {0x11, 0x1b, 0x15, 0x15, 0x11, 0x11, 0x11}},
    {'N', {0x11, 0x19, 0x19, 0x15, 0x13, 0x13, 0x11}},
    {'O', {0x0e, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0e}},
    {'R', {0x1e, 0x11, 0x11, 0x1e, 0x14, 0x12, 0x11}},
    {'T', {0x1f, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04}},
    {'U', {0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0e}},
    {'X', {0x11, 0x11, 0x0a, 0x04, 0x0a, 0x11, 0x11}},
};

static void splash_fill_rect(uint16_t *framebuffer, uint32_t width,
                             uint32_t height, uint32_t x, uint32_t y,
                             uint32_t rect_width, uint32_t rect_height,
                             uint16_t color)
{
    if (x >= width || y >= height) {
        return;
    }
    if (rect_width > width - x) {
        rect_width = width - x;
    }
    if (rect_height > height - y) {
        rect_height = height - y;
    }
    for (uint32_t row = 0; row < rect_height; ++row) {
        uint16_t *const destination = framebuffer + (y + row) * width + x;
        for (uint32_t column = 0; column < rect_width; ++column) {
            destination[column] = color;
        }
    }
}

static const uint8_t *splash_glyph(char character)
{
    for (size_t index = 0;
         index < sizeof(s_splash_glyphs) / sizeof(s_splash_glyphs[0]);
         ++index) {
        if (s_splash_glyphs[index].character == character) {
            return s_splash_glyphs[index].rows;
        }
    }
    return NULL;
}

static uint32_t splash_text_width(const char *text, uint32_t scale)
{
    uint32_t characters = 0;
    while (text[characters] != '\0') {
        ++characters;
    }
    return characters == 0 ? 0 : (characters * 6U - 1U) * scale;
}

static void splash_draw_text(uint16_t *framebuffer, uint32_t width,
                             uint32_t height, uint32_t x, uint32_t y,
                             const char *text, uint32_t scale,
                             uint16_t color)
{
    while (*text != '\0') {
        const uint8_t *const rows = splash_glyph(*text);
        if (rows != NULL) {
            for (uint32_t row = 0; row < 7; ++row) {
                for (uint32_t column = 0; column < 5; ++column) {
                    if ((rows[row] & (UINT8_C(1) << (4U - column))) != 0) {
                        splash_fill_rect(framebuffer, width, height,
                                         x + column * scale,
                                         y + row * scale,
                                         scale, scale, color);
                    }
                }
            }
        }
        x += 6U * scale;
        ++text;
    }
}

static uint32_t splash_title_scale(uint32_t width)
{
    const uint32_t scale = width / 56U;
    return scale < 4U ? 4U : scale;
}

static uint32_t splash_group_height(uint32_t width)
{
    const uint32_t title_scale = splash_title_scale(width);
    const uint32_t subtitle_scale = title_scale / 3U;
    return 7U * title_scale + 34U + 7U * subtitle_scale +
           MICRONUX_SPLASH_PROGRESS_GAP + MICRONUX_SPLASH_BAR_HEIGHT +
           MICRONUX_SPLASH_PERCENT_GAP + 7U * subtitle_scale;
}

static uint32_t render_boot_progress(uint16_t *framebuffer, uint32_t width,
                                     uint32_t height, uint8_t percent)
{
    const uint32_t title_scale = splash_title_scale(width);
    const uint32_t subtitle_scale = title_scale / 3U;
    const uint32_t title_y = (height - splash_group_height(width)) / 2U;
    const uint32_t bar_width = width * 3U / 4U;
    const uint32_t bar_x = (width - bar_width) / 2U;
    const uint32_t bar_y = title_y + 7U * title_scale + 34U +
                           7U * subtitle_scale +
                           MICRONUX_SPLASH_PROGRESS_GAP;
    const uint32_t border = 3U;
    const uint32_t inner_width = bar_width - 2U * border;
    const uint32_t inner_height = MICRONUX_SPLASH_BAR_HEIGHT - 2U * border;
    const uint32_t filled_width = inner_width * percent / 100U;

    splash_fill_rect(framebuffer, width, height, bar_x, bar_y, bar_width,
                     MICRONUX_SPLASH_BAR_HEIGHT,
                     MICRONUX_SPLASH_FOREGROUND);
    splash_fill_rect(framebuffer, width, height, bar_x + border,
                     bar_y + border, inner_width, inner_height,
                     MICRONUX_SPLASH_BACKGROUND);
    splash_fill_rect(framebuffer, width, height, bar_x + border,
                     bar_y + border, filled_width, inner_height,
                     MICRONUX_SPLASH_ACCENT);

    char percent_text[5];
    size_t length = 0;
    if (percent == 100U) {
        percent_text[length++] = '1';
        percent_text[length++] = '0';
        percent_text[length++] = '0';
    } else {
        if (percent >= 10U) {
            percent_text[length++] = (char)('0' + percent / 10U);
        }
        percent_text[length++] = (char)('0' + percent % 10U);
    }
    percent_text[length++] = '%';
    percent_text[length] = '\0';

    const uint32_t percent_y = bar_y + MICRONUX_SPLASH_BAR_HEIGHT +
                               MICRONUX_SPLASH_PERCENT_GAP;
    const uint32_t percent_width =
        splash_text_width(percent_text, subtitle_scale);
    splash_fill_rect(framebuffer, width, height, 0, percent_y, width,
                     7U * subtitle_scale, MICRONUX_SPLASH_BACKGROUND);
    splash_draw_text(framebuffer, width, height,
                     (width - percent_width) / 2U, percent_y, percent_text,
                     subtitle_scale, MICRONUX_SPLASH_FOREGROUND);
    return bar_y;
}

static void render_boot_splash(uint16_t *framebuffer, uint32_t width,
                               uint32_t height)
{
    static const char title[] = "MICRONUX";
    static const char subtitle[] = "BOOTING LINUX";
    const uint32_t title_scale = splash_title_scale(width);
    const uint32_t subtitle_scale = title_scale / 3U;
    const uint32_t title_width = splash_text_width(title, title_scale);
    const uint32_t subtitle_width =
        splash_text_width(subtitle, subtitle_scale);
    const uint32_t title_height = 7U * title_scale;
    const uint32_t group_height = splash_group_height(width);
    const uint32_t title_x = (width - title_width) / 2U;
    const uint32_t title_y = (height - group_height) / 2U;

    splash_fill_rect(framebuffer, width, height, 0, 0, width, height,
                     MICRONUX_SPLASH_BACKGROUND);
    splash_draw_text(framebuffer, width, height, title_x, title_y, title,
                     title_scale, MICRONUX_SPLASH_FOREGROUND);
    splash_fill_rect(framebuffer, width, height, title_x,
                     title_y + title_height + 14U, title_width, 4U,
                     MICRONUX_SPLASH_ACCENT);
    splash_draw_text(framebuffer, width, height,
                     (width - subtitle_width) / 2U,
                     title_y + title_height + 34U, subtitle,
                     subtitle_scale, MICRONUX_SPLASH_ACCENT);

    (void)render_boot_progress(framebuffer, width, height, 0);
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

    /*
     * A P4 reset does not reset the external Waveshare display controller.
     * Clear its retained PWM before changing BL_ENABLE, then give the panel a
     * real active-low reset pulse while AVDD and IOVCC remain enabled.
     */
    ESP_GOTO_ON_ERROR(
        write_backlight_register(MICRONUX_DSI_BACKLIGHT_REGISTER, 0),
        fail, TAG, "turn backlight off");
    delay_at_least_ms(MICRONUX_DSI_BACKLIGHT_OFF_SETTLE_MS);
    ESP_GOTO_ON_ERROR(
        write_backlight_register(MICRONUX_DSI_DISPLAY_CONTROL_REGISTER,
                                 MICRONUX_DSI_DISPLAY_RESET_RELEASED),
        fail, TAG, "prepare display reset");
    delay_at_least_ms(MICRONUX_DSI_RESET_PREPARE_MS);
    ESP_GOTO_ON_ERROR(
        write_backlight_register(MICRONUX_DSI_DISPLAY_CONTROL_REGISTER,
                                 MICRONUX_DSI_DISPLAY_RESET_ASSERTED),
        fail, TAG, "assert display reset");
    delay_at_least_ms(MICRONUX_DSI_RESET_ASSERT_MS);
    ESP_GOTO_ON_ERROR(
        write_backlight_register(MICRONUX_DSI_DISPLAY_CONTROL_REGISTER,
                                 MICRONUX_DSI_DISPLAY_RESET_RELEASED),
        fail, TAG, "release display reset");
    delay_at_least_ms(MICRONUX_DSI_RESET_RELEASE_MS);
    ESP_LOGI(TAG,
             "MICRONUX:M9:PANEL-RESET state=ready pwm=off"
             " sequence=release-assert-release delays_ms=%" PRIu32
             ",%" PRIu32 ",%" PRIu32 " backlight-enable=off",
             MICRONUX_DSI_RESET_PREPARE_MS,
             MICRONUX_DSI_RESET_ASSERT_MS,
             MICRONUX_DSI_RESET_RELEASE_MS);
    return ESP_OK;

fail:
    /* A partial or ambiguous I2C sequence must have two ways to stay dark. */
    best_effort_display_dark();
    release_backlight_bus();
    return ret;
}

static esp_err_t create_selected_panel(void)
{
#if CONFIG_MICRONUX_MIPI_PANEL_JD9365_800_1280
    esp_lcd_dpi_panel_config_t dpi_config =
        JD9365_800_1280_PANEL_60HZ_DPI_CONFIG(LCD_COLOR_FMT_RGB565);
    /* Exact Waveshare Kit C profile; do not inherit an older component value. */
    dpi_config.dpi_clock_freq_mhz = 80;
    dpi_config.num_fbs = 1;
    dpi_config.flags.disable_lp = 0;
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

    s_dpi_clock_mhz = (uint16_t)dpi_config.dpi_clock_freq_mhz;
    s_video_timing = dpi_config.video_timing;

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
#if CONFIG_MICRONUX_MIPI_PANEL_JD9365_800_1280
    dsi_host_dev_t *const host = MIPI_DSI_LL_GET_HOST(0);
    ESP_RETURN_ON_FALSE(host != NULL, ESP_ERR_INVALID_STATE, TAG,
                        "locate MIPI DSI host");
    /*
     * esp_lcd_new_panel_dpi() enables per-frame BTA in ESP-IDF v6.0.1.
     * Clear it after panel construction and before dpi_panel_init() starts
     * GDMA, while the panel remains physically dark.
     */
    mipi_dsi_host_ll_dpi_enable_frame_ack(host, false);
#endif
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(s_panel), TAG,
                        "reset MIPI panel");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(s_panel), TAG,
                        "initialize MIPI panel");
#if CONFIG_MICRONUX_MIPI_PANEL_JD9365_800_1280
    ESP_RETURN_ON_FALSE(!host->vid_mode_cfg.frame_bta_ack_en,
                        ESP_ERR_INVALID_STATE, TAG,
                        "disable per-frame MIPI DSI BTA");
    ESP_LOGI(TAG,
             "MICRONUX:M9.2:LINK-POLICY state=ready"
             " lane_clock=auto video_lp=enabled frame_ack=disabled"
             " reason=bounded-frame-stream");
#endif
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
        .flags.clock_lane_force_hs = MICRONUX_DSI_CLOCK_LANE_FORCE_HS,
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
    render_boot_splash(framebuffer, s_profile.width, s_profile.height);
    ESP_RETURN_ON_ERROR(
        esp_cache_msync(framebuffer, framebuffer_size,
                        ESP_CACHE_MSYNC_FLAG_DIR_C2M |
                        ESP_CACHE_MSYNC_FLAG_TYPE_DATA),
        TAG, "flush initial MIPI framebuffer");
    s_framebuffer = framebuffer;
    s_framebuffer_size = framebuffer_size;
    s_progress_percent = 0;
    ESP_LOGI(TAG,
             "MICRONUX:M7:SPLASH state=ready title=MICRONUX"
             " resolution=%ux%u format=rgb565 progress=0 mode=staged",
             s_profile.width, s_profile.height);

    const uint8_t brightness =
        (uint8_t)((UINT32_C(255) * CONFIG_MICRONUX_MIPI_BACKLIGHT_PERCENT) /
                  UINT32_C(100));
    /* Stage PWM while BL_ENABLE is still low, then reveal as the final write. */
    esp_err_t result = write_backlight_register(
        MICRONUX_DSI_BACKLIGHT_REGISTER, brightness);
    if (result == ESP_OK) {
        result = write_backlight_register(
            MICRONUX_DSI_DISPLAY_CONTROL_REGISTER,
            MICRONUX_DSI_DISPLAY_BACKLIGHT_ENABLED);
    }
    if (result != ESP_OK) {
        best_effort_display_dark();
        release_backlight_bus();
        ESP_RETURN_ON_ERROR(result, TAG, "enable MIPI backlight");
    }
    ESP_LOGI(TAG,
             "MICRONUX:M6:DSI state=ready profile=%s resolution=%ux%u"
             " lanes=%u lane_mbps=%u dpi_mhz=%u"
             " format=rgb565 pattern=framebuffer"
             MICRONUX_DSI_LINK_LOG
             " fb=%p bytes=%zu ownership=loader backlight=%u"
             " i2c=retained-for-linux",
             s_profile.name, s_profile.width, s_profile.height,
             MICRONUX_DSI_LANES, s_profile.lane_mbps, s_dpi_clock_mhz,
             framebuffer, framebuffer_size,
             CONFIG_MICRONUX_MIPI_BACKLIGHT_PERCENT);
    return ESP_OK;
#endif
}

void micronux_mipi_dsi_progress(uint8_t percent)
{
#if !CONFIG_MICRONUX_MIPI_DSI || CONFIG_MICRONUX_MIPI_PANEL_UNSELECTED
    (void)percent;
#else
    if (s_framebuffer == NULL || percent <= s_progress_percent) {
        return;
    }
    if (percent > 100U) {
        percent = 100U;
    }

    const uint32_t first_row = render_boot_progress(
        s_framebuffer, s_profile.width, s_profile.height, percent);
    uint8_t *const flush_start =
        (uint8_t *)s_framebuffer +
        (size_t)first_row * s_profile.width * sizeof(uint16_t);
    const uint32_t progress_rows = MICRONUX_SPLASH_BAR_HEIGHT +
                                   MICRONUX_SPLASH_PERCENT_GAP +
                                   7U * (splash_title_scale(
                                       s_profile.width) / 3U);
    const size_t flush_size =
        (size_t)progress_rows * s_profile.width *
        sizeof(uint16_t);
    const esp_err_t result = esp_cache_msync(
        flush_start, flush_size,
        ESP_CACHE_MSYNC_FLAG_DIR_C2M |
        ESP_CACHE_MSYNC_FLAG_TYPE_DATA |
        ESP_CACHE_MSYNC_FLAG_UNALIGNED);
    if (result != ESP_OK) {
        ESP_LOGW(TAG,
                 "MICRONUX:M7:SPLASH progress=%u state=stale error=%s",
                 percent, esp_err_to_name(result));
        return;
    }
    s_progress_percent = percent;
    ESP_LOGI(TAG, "MICRONUX:M7:SPLASH progress=%u state=visible",
             percent);
#endif
}

void micronux_mipi_dsi_standalone_run(void)
{
#if !CONFIG_MICRONUX_DISPLAY_STANDALONE
    ESP_LOGE(TAG,
             "MICRONUX:IDF-DISPLAY state=refused reason=profile-disabled");
#else
    static const char title[] = "MICRONUX";
    static const char subtitle[] = "COLOR BURN IN";
    static const char state[] = "RUNNING";
    const uint32_t title_scale = splash_title_scale(s_profile.width);
    const uint32_t text_scale = title_scale / 3U;
    const uint32_t title_width = splash_text_width(title, title_scale);
    const uint32_t subtitle_width = splash_text_width(subtitle, text_scale);
    const uint32_t state_width = splash_text_width(state, text_scale);
    const uint32_t title_y = s_profile.height / 2U - 110U;
    const uint32_t heartbeat_size = 36U;
    const uint32_t heartbeat_x = (s_profile.width - heartbeat_size) / 2U;
    const uint32_t heartbeat_y = title_y + 190U;
    bool heartbeat_on = false;
    uint32_t seconds = 0;

    if (s_panel == NULL || s_framebuffer == NULL ||
        s_framebuffer_size == 0) {
        ESP_LOGE(TAG,
                 "MICRONUX:IDF-DISPLAY state=failed reason=display-missing");
        abort();
    }

    ESP_ERROR_CHECK(esp_lcd_dpi_panel_set_pattern(
        s_panel, MIPI_DSI_PATTERN_BAR_VERTICAL));
    ESP_LOGI(TAG,
             "MICRONUX:IDF-DISPLAY state=color-bars duration_s=5"
             " source=hardware-vpg");
    vTaskDelay(pdMS_TO_TICKS(5000));

    splash_fill_rect(s_framebuffer, s_profile.width, s_profile.height,
                     0, 0, s_profile.width, s_profile.height,
                     MICRONUX_SPLASH_BACKGROUND);
    splash_draw_text(s_framebuffer, s_profile.width, s_profile.height,
                     (s_profile.width - title_width) / 2U, title_y,
                     title, title_scale, MICRONUX_SPLASH_FOREGROUND);
    splash_draw_text(s_framebuffer, s_profile.width, s_profile.height,
                     (s_profile.width - subtitle_width) / 2U,
                     title_y + 100U, subtitle, text_scale,
                     MICRONUX_SPLASH_ACCENT);
    splash_draw_text(s_framebuffer, s_profile.width, s_profile.height,
                     (s_profile.width - state_width) / 2U,
                     title_y + 145U, state, text_scale,
                     MICRONUX_SPLASH_FOREGROUND);
    ESP_ERROR_CHECK(esp_cache_msync(
        s_framebuffer, s_framebuffer_size,
        ESP_CACHE_MSYNC_FLAG_DIR_C2M | ESP_CACHE_MSYNC_FLAG_TYPE_DATA));
    ESP_ERROR_CHECK(esp_lcd_dpi_panel_set_pattern(
        s_panel, MIPI_DSI_PATTERN_NONE));
    ESP_LOGI(TAG,
             "MICRONUX:IDF-DISPLAY state=running owner=esp-idf"
             " source=framebuffer resolution=%ux%u format=rgb565"
             " linux=disabled sdmmc=disabled c6=disabled",
             s_profile.width, s_profile.height);

    while (true) {
        heartbeat_on = !heartbeat_on;
        splash_fill_rect(s_framebuffer, s_profile.width, s_profile.height,
                         heartbeat_x, heartbeat_y,
                         heartbeat_size, heartbeat_size,
                         heartbeat_on ? MICRONUX_SPLASH_ACCENT :
                                        MICRONUX_SPLASH_BACKGROUND);
        uint8_t *const heartbeat =
            (uint8_t *)s_framebuffer +
            ((size_t)heartbeat_y * s_profile.width + heartbeat_x) *
                sizeof(uint16_t);
        ESP_ERROR_CHECK(esp_cache_msync(
            heartbeat,
            (size_t)heartbeat_size * s_profile.width * sizeof(uint16_t),
            ESP_CACHE_MSYNC_FLAG_DIR_C2M |
                ESP_CACHE_MSYNC_FLAG_TYPE_DATA |
                ESP_CACHE_MSYNC_FLAG_UNALIGNED));
        vTaskDelay(pdMS_TO_TICKS(1000));
        ++seconds;
        if (seconds % 60U == 0U) {
            ESP_LOGI(TAG,
                     "MICRONUX:IDF-DISPLAY state=running uptime_s=%" PRIu32,
                     seconds);
        }
    }
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
    if (s_framebuffer == NULL || s_framebuffer_size == 0) {
        ESP_LOGE(TAG,
                 "MICRONUX:M7:DSI-HANDOFF state=fail"
                 " reason=framebuffer-missing");
        return ESP_ERR_INVALID_STATE;
    }

    if (s_i2c_bus == NULL || s_backlight == NULL) {
        ESP_LOGE(TAG,
                 "MICRONUX:M7:DSI-HANDOFF state=fail"
                 " reason=i2c-not-transferable");
        return ESP_ERR_INVALID_STATE;
    }

    ESP_RETURN_ON_ERROR(disable_backlight_gate(), TAG,
                        "disable backlight gate for MIPI handoff");
    /*
     * I2C completion only proves that the adapter accepted brightness=0.
     * Keep the splash scanout valid until its LED current has settled, so the
     * panel's cyan no-video state cannot become visible during ownership
     * transfer.
     */
    /* One extra tick makes 100 ms a minimum despite tick-phase truncation. */
    delay_at_least_ms(MICRONUX_DSI_BACKLIGHT_OFF_SETTLE_MS);
    ESP_LOGI(TAG,
             "MICRONUX:M7:DSI-BLANK state=ready backlight=off gate=disabled"
             " settle_ms=%" PRIu32
             " restore=linux-after-status-ready",
             MICRONUX_DSI_BACKLIGHT_OFF_SETTLE_MS);

    /*
     * Keep the DSI host fed while the loader's framebuffer DMA is stopped.
     * ESP-IDF v6.0.1 switches to VPG by disabling bridge DPI output first,
     * then enabling the host pattern generator.  The backlight is already
     * physically dark, so the diagnostic bars are never exposed here.
     */
    ESP_RETURN_ON_ERROR(esp_lcd_dpi_panel_set_pattern(
                            s_panel, MIPI_DSI_PATTERN_BAR_VERTICAL),
                        TAG, "select dark VPG source for MIPI handoff");
    ESP_LOGI(TAG,
             "MICRONUX:M9.2:DSI-HANDOFF-SOURCE state=ready"
             " pattern=vertical-bars bridge=dpi-disabled backlight=off");

    esp_lcd_dpi_panel_handoff_t dsi_handoff = {0};
    ESP_RETURN_ON_ERROR(
        esp_lcd_dpi_panel_prepare_handoff(s_panel, &dsi_handoff), TAG,
        "prepare descriptor-pool MIPI DPI handoff");

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
        (framebuffer_start &
         (MICRONUX_DSI_FRAMEBUFFER_ALIGNMENT - 1U)) != 0 ||
        descriptor_end < descriptor_start ||
        descriptor_start < MICRONUX_DSI_INTERNAL_SRAM_START ||
        descriptor_end > MICRONUX_DSI_INTERNAL_SRAM_END ||
        (descriptor_start &
         (MICRONUX_DSI_DMA_DESCRIPTOR_ALIGNMENT - 1U)) != 0 ||
        dsi_handoff.dma_descriptor_size !=
            MICRONUX_DSI_DMA_DESCRIPTOR_ALIGNMENT *
                MICRONUX_DSI_DMA_DESCRIPTOR_COUNT ||
        dsi_handoff.dma_descriptor_count !=
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

    if (dsi_handoff.frame_buffer != s_framebuffer ||
        dsi_handoff.frame_buffer_size != s_framebuffer_size) {
        ESP_LOGE(TAG,
                 "MICRONUX:M7:DSI-HANDOFF state=fail"
                 " reason=framebuffer-changed");
        return ESP_ERR_INVALID_STATE;
    }

    const uintptr_t backbuffer_end =
        MICRONUX_DISPLAY_BACKBUFFER_ADDRESS + s_framebuffer_size;
    const uintptr_t sparebuffer_end =
        MICRONUX_DISPLAY_SPAREBUFFER_ADDRESS + s_framebuffer_size;
    if (backbuffer_end < MICRONUX_DISPLAY_BACKBUFFER_ADDRESS ||
        backbuffer_end > MICRONUX_DISPLAY_SPAREBUFFER_ADDRESS ||
        sparebuffer_end < MICRONUX_DISPLAY_SPAREBUFFER_ADDRESS ||
        sparebuffer_end > MICRONUX_DISPLAY_BACKBUFFER_POOL_END) {
        ESP_LOGE(TAG,
                 "MICRONUX:M9.2:DSI-HANDOFF state=fail"
                 " reason=triple-buffer-bounds bytes=%zu"
                 " pool=[%08" PRIx32 ",%08" PRIx32 ")",
                 s_framebuffer_size,
                 MICRONUX_DISPLAY_BACKBUFFER_POOL_START,
                 MICRONUX_DISPLAY_BACKBUFFER_POOL_END);
        return ESP_ERR_INVALID_STATE;
    }

    /* Keep fail-dark control until every fallible handoff check has passed. */
    ESP_RETURN_ON_ERROR(release_backlight_device(), TAG,
                        "release loader backlight device");

    REG_WRITE(I2C_INT_ENA_REG(MICRONUX_DSI_I2C_PORT), 0);
    REG_WRITE(I2C_INT_CLR_REG(MICRONUX_DSI_I2C_PORT), UINT32_MAX);

    micronux_display_handoff_v2_t *const handoff =
        (micronux_display_handoff_v2_t *)
            MICRONUX_DISPLAY_HANDOFF_ADDRESS;
    *handoff = (micronux_display_handoff_v2_t) {
        .magic = MICRONUX_DISPLAY_HANDOFF_MAGIC,
        .abi_version = MICRONUX_DISPLAY_HANDOFF_ABI_VERSION,
        .struct_size = sizeof(*handoff),
        .flags = MICRONUX_DISPLAY_FLAG_ACTIVE |
                 MICRONUX_DISPLAY_FLAG_RGB565 |
                 MICRONUX_DISPLAY_FLAG_DMA_IRQ_REARM |
                 MICRONUX_DISPLAY_FLAG_I2C_TRANSFERRED |
                 MICRONUX_DISPLAY_FLAG_BACKLIGHT_BLANKED |
                 MICRONUX_DISPLAY_FLAG_TRIPLE_BUFFER |
                 MICRONUX_DISPLAY_FLAG_LOADER_FRONT_VALID |
                 MICRONUX_DISPLAY_FLAG_HOST_VPG_ACTIVE,
        .width = s_profile.width,
        .height = s_profile.height,
        .stride = (uint32_t)s_profile.width * sizeof(uint16_t),
        .pixel_clock_hz = (uint32_t)s_dpi_clock_mhz * UINT32_C(1000000),
        .lane_bit_rate_mbps = s_profile.lane_mbps,
        .data_lanes = MICRONUX_DSI_LANES,
        .buffer_count = MICRONUX_DISPLAY_BUFFER_COUNT,
        .ownership_state = MICRONUX_DISPLAY_OWNER_LINUX_PENDING,
        .hsync_pulse_width = s_video_timing.hsync_pulse_width,
        .hsync_back_porch = s_video_timing.hsync_back_porch,
        .hsync_front_porch = s_video_timing.hsync_front_porch,
        .vsync_pulse_width = s_video_timing.vsync_pulse_width,
        .vsync_back_porch = s_video_timing.vsync_back_porch,
        .vsync_front_porch = s_video_timing.vsync_front_porch,
        .framebuffer_address = {
            (uint32_t)framebuffer_start,
            MICRONUX_DISPLAY_BACKBUFFER_ADDRESS,
            MICRONUX_DISPLAY_SPAREBUFFER_ADDRESS,
        },
        .framebuffer_size = (uint32_t)dsi_handoff.frame_buffer_size,
        .dma_descriptor_address = (uint32_t)descriptor_start,
        .dma_descriptor_size =
            (uint32_t)dsi_handoff.dma_descriptor_size,
        .dma_descriptor_count =
            (uint32_t)dsi_handoff.dma_descriptor_count,
        .dma_channel = (uint32_t)dsi_handoff.dma_channel,
        .dsi_fifo_address = MICRONUX_DSI_FIFO_ADDRESS,
        .i2c_address = MICRONUX_DSI_BACKLIGHT_ADDRESS,
        .display_control_register =
            MICRONUX_DSI_DISPLAY_CONTROL_REGISTER,
        .backlight_register = MICRONUX_DSI_BACKLIGHT_REGISTER,
        .backlight_brightness =
            (UINT32_C(255) * CONFIG_MICRONUX_MIPI_BACKLIGHT_PERCENT) /
            UINT32_C(100),
    };
    handoff->crc32 = esp_rom_crc32_le(
        0, (const uint8_t *)handoff,
        offsetof(micronux_display_handoff_v2_t, crc32));

    ESP_RETURN_ON_ERROR(
        esp_cache_msync(handoff, sizeof(*handoff),
                        ESP_CACHE_MSYNC_FLAG_DIR_C2M |
                        ESP_CACHE_MSYNC_FLAG_TYPE_DATA |
                        ESP_CACHE_MSYNC_FLAG_UNALIGNED),
        TAG, "flush MIPI handoff contract");

    s_dma_policy = (micronux_display_dma_policy_t) {
        .frontbuffer_start = align_down_4k((uint32_t)framebuffer_start),
        .frontbuffer_end = align_up_4k((uint32_t)framebuffer_end),
        .backbuffer_pool_start = MICRONUX_DISPLAY_BACKBUFFER_POOL_START,
        .backbuffer_pool_end = MICRONUX_DISPLAY_BACKBUFFER_POOL_END,
        .descriptor_start = align_down_4k((uint32_t)descriptor_start),
        .descriptor_end = align_up_4k((uint32_t)descriptor_end),
        .fifo_start = MICRONUX_DSI_FIFO_WINDOW_START,
        .fifo_end = MICRONUX_DSI_FIFO_WINDOW_END,
        .dma_channel = (uint32_t)dsi_handoff.dma_channel,
    };
    s_dma_policy_ready = true;

    ESP_LOGI(TAG,
             "MICRONUX:M9.2:DSI-HANDOFF state=ready abi=2"
             " owner=linux-pending pattern=vertical-bars"
              " buffers=3 dma=irq-rearm channel=%d"
              " timing=%" PRIu32 "/%" PRIu32 "/%" PRIu32
              ":%" PRIu32 "/%" PRIu32 "/%" PRIu32
              " rearm=linux-after-status-ready"
             " front=[%08" PRIxPTR ",%08" PRIxPTR ")"
             " back=%08" PRIx32 " spare=%08" PRIx32
             " desc=[%08" PRIxPTR ",%08" PRIxPTR ") i2c=transferred"
             " contract=%08" PRIx32 " crc32=%08" PRIx32,
              dsi_handoff.dma_channel,
              handoff->hsync_pulse_width, handoff->hsync_back_porch,
              handoff->hsync_front_porch, handoff->vsync_pulse_width,
              handoff->vsync_back_porch, handoff->vsync_front_porch,
             framebuffer_start, framebuffer_end,
             MICRONUX_DISPLAY_BACKBUFFER_ADDRESS,
             MICRONUX_DISPLAY_SPAREBUFFER_ADDRESS,
             descriptor_start, descriptor_end,
             MICRONUX_DISPLAY_HANDOFF_ADDRESS, handoff->crc32);
    return ESP_OK;
#endif
}

bool micronux_mipi_dsi_dma_policy(micronux_display_dma_policy_t *policy)
{
#if CONFIG_MICRONUX_M9_JD9365_COLD_RELINQUISH
    if (!s_cold_dma_policy_ready || policy == NULL) {
        return false;
    }
    *policy = s_cold_dma_policy;
    return true;
#elif !CONFIG_MICRONUX_M7_EARLY_UMODE_DENY || \
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
