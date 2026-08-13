// SPDX-License-Identifier: MIT

#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>

#include "sdkconfig.h"
#include "esp_err.h"
#include "esp_log.h"
#include "soc/dma_pms_reg.h"
#include "soc/soc.h"

#include "micronux_dma_pms.h"
#include "micronux_mipi_dsi.h"

static const char *const TAG = "micronux_dma";

#if CONFIG_MICRONUX_M7_EARLY_UMODE_DENY

#define MICRONUX_DMA_REGION_COUNT 32U
#define MICRONUX_DMA_REGION0_MASK UINT32_C(1)
#define MICRONUX_DMA_REGION1_MASK (UINT32_C(1) << 1)
#define MICRONUX_DMA_REGION2_MASK (UINT32_C(1) << 2)
#define MICRONUX_DMA_REGION3_MASK (UINT32_C(1) << 3)
#define MICRONUX_DMA_REGION4_MASK (UINT32_C(1) << 4)
#define MICRONUX_DMA_WINDOW_START UINT32_C(0x4ff80000)
#define MICRONUX_DMA_WINDOW_END UINT32_C(0x4ff82000)
#define MICRONUX_DMA_EMPTY_LOW UINT32_C(0xfffff000)
#define MICRONUX_DMA_EMPTY_HIGH UINT32_C(0)
#define MICRONUX_DMA_ADDR_MASK PMS_DMA_REGION0_LOW_M

/*
 * Revision 1.3 has a documented hole between the RMT read and write
 * permission registers. Keep the generated register names explicit rather
 * than deriving addresses from the DMA-master enumeration.
 */
static const uint32_t s_permission_registers[] = {
    PMS_DMA_GDMA_CH0_R_PMS_REG,
    PMS_DMA_GDMA_CH0_W_PMS_REG,
    PMS_DMA_GDMA_CH1_R_PMS_REG,
    PMS_DMA_GDMA_CH1_W_PMS_REG,
    PMS_DMA_GDMA_CH2_R_PMS_REG,
    PMS_DMA_GDMA_CH2_W_PMS_REG,
    PMS_DMA_GDMA_CH3_R_PMS_REG,
    PMS_DMA_GDMA_CH3_W_PMS_REG,
    PMS_DMA_AHB_PDMA_ADC_R_PMS_REG,
    PMS_DMA_AHB_PDMA_ADC_W_PMS_REG,
    PMS_DMA_AHB_PDMA_I2S0_R_PMS_REG,
    PMS_DMA_AHB_PDMA_I2S0_W_PMS_REG,
    PMS_DMA_AHB_PDMA_I2S1_R_PMS_REG,
    PMS_DMA_AHB_PDMA_I2S1_W_PMS_REG,
    PMS_DMA_AHB_PDMA_I2S2_R_PMS_REG,
    PMS_DMA_AHB_PDMA_I2S2_W_PMS_REG,
    PMS_DMA_AHB_PDMA_I3C_MST_R_PMS_REG,
    PMS_DMA_AHB_PDMA_I3C_MST_W_PMS_REG,
    PMS_DMA_AHB_PDMA_UHCI0_R_PMS_REG,
    PMS_DMA_AHB_PDMA_UHCI0_W_PMS_REG,
    PMS_DMA_AHB_PDMA_RMT_R_PMS_REG,
    PMS_DMA_AHB_PDMA_RMT_W_PMS_REG,
    PMS_DMA_AXI_PDMA_LCDCAM_R_PMS_REG,
    PMS_DMA_AXI_PDMA_LCDCAM_W_PMS_REG,
    PMS_DMA_AXI_PDMA_GPSPI2_R_PMS_REG,
    PMS_DMA_AXI_PDMA_GPSPI2_W_PMS_REG,
    PMS_DMA_AXI_PDMA_GPSPI3_R_PMS_REG,
    PMS_DMA_AXI_PDMA_GPSPI3_W_PMS_REG,
    PMS_DMA_AXI_PDMA_PARLIO_R_PMS_REG,
    PMS_DMA_AXI_PDMA_PARLIO_W_PMS_REG,
    PMS_DMA_AXI_PDMA_AES_R_PMS_REG,
    PMS_DMA_AXI_PDMA_AES_W_PMS_REG,
    PMS_DMA_AXI_PDMA_SHA_R_PMS_REG,
    PMS_DMA_AXI_PDMA_SHA_W_PMS_REG,
    PMS_DMA_DMA2D_JPEG_PMS_R_REG,
    PMS_DMA_DMA2D_JPEG_PMS_W_REG,
    PMS_DMA_USB_PMS_R_REG,
    PMS_DMA_USB_PMS_W_REG,
    PMS_DMA_GMAC_PMS_R_REG,
    PMS_DMA_GMAC_PMS_W_REG,
    PMS_DMA_SDMMC_PMS_R_REG,
    PMS_DMA_SDMMC_PMS_W_REG,
    PMS_DMA_USBOTG11_PMS_R_REG,
    PMS_DMA_USBOTG11_PMS_W_REG,
    PMS_DMA_TRACE0_PMS_R_REG,
    PMS_DMA_TRACE0_PMS_W_REG,
    PMS_DMA_TRACE1_PMS_R_REG,
    PMS_DMA_TRACE1_PMS_W_REG,
    PMS_DMA_L2MEM_MON_PMS_R_REG,
    PMS_DMA_L2MEM_MON_PMS_W_REG,
    PMS_DMA_SPM_MON_PMS_R_REG,
    PMS_DMA_SPM_MON_PMS_W_REG,
    PMS_DMA_H264_PMS_R_REG,
    PMS_DMA_H264_PMS_W_REG,
    PMS_DMA_DMA2D_PPA_PMS_R_REG,
    PMS_DMA_DMA2D_PPA_PMS_W_REG,
    PMS_DMA_DMA2D_DUMMY_PMS_R_REG,
    PMS_DMA_DMA2D_DUMMY_PMS_W_REG,
    PMS_DMA_AHB_PDMA_DUMMY_R_PMS_REG,
    PMS_DMA_AHB_PDMA_DUMMY_W_PMS_REG,
    PMS_DMA_AXI_PDMA_DUMMY_R_PMS_REG,
    PMS_DMA_AXI_PDMA_DUMMY_W_PMS_REG,
};

static const uint32_t s_gdma_read_permission_registers[] = {
    PMS_DMA_GDMA_CH0_R_PMS_REG,
    PMS_DMA_GDMA_CH1_R_PMS_REG,
    PMS_DMA_GDMA_CH2_R_PMS_REG,
    PMS_DMA_GDMA_CH3_R_PMS_REG,
};

static const uint32_t s_gdma_write_permission_registers[] = {
    PMS_DMA_GDMA_CH0_W_PMS_REG,
    PMS_DMA_GDMA_CH1_W_PMS_REG,
    PMS_DMA_GDMA_CH2_W_PMS_REG,
    PMS_DMA_GDMA_CH3_W_PMS_REG,
};

static void write_region(uint32_t region, uint32_t low, uint32_t high)
{
    /*
     * The P4 PMS high register is the 4 KiB-aligned end boundary. Passing
     * end - 1 would discard the low 12 bits and exclude the final page.
     */
    REG_WRITE(PMS_DMA_REGION0_LOW_REG + region * 8U,
              low & MICRONUX_DMA_ADDR_MASK);
    REG_WRITE(PMS_DMA_REGION0_HIGH_REG + region * 8U,
              high & MICRONUX_DMA_ADDR_MASK);
}

static bool region_matches(uint32_t region, uint32_t low, uint32_t high)
{
    return REG_READ(PMS_DMA_REGION0_LOW_REG + region * 8U) ==
               (low & MICRONUX_DMA_ADDR_MASK) &&
           REG_READ(PMS_DMA_REGION0_HIGH_REG + region * 8U) ==
               (high & MICRONUX_DMA_ADDR_MASK);
}

#endif

esp_err_t micronux_dma_pms_prepare(void)
{
#if !CONFIG_MICRONUX_M7_EARLY_UMODE_DENY
    return ESP_OK;
#else
    for (size_t index = 0;
         index < sizeof(s_permission_registers) /
                     sizeof(s_permission_registers[0]);
         ++index) {
        REG_WRITE(s_permission_registers[index], 0);
    }

    micronux_display_dma_policy_t display = {0};
    const bool display_active = micronux_mipi_dsi_dma_policy(&display);
    if (display_active && display.dma_channel >= 4) {
        ESP_LOGE(TAG,
                 "MICRONUX:M7:DMA-PMS state=fail"
                 " reason=display-channel channel=%" PRIu32,
                 display.dma_channel);
        return ESP_ERR_INVALID_STATE;
    }

    write_region(0, MICRONUX_DMA_WINDOW_START,
                 MICRONUX_DMA_WINDOW_END);
    if (display_active) {
        write_region(1, display.frontbuffer_start,
                     display.frontbuffer_end);
        write_region(2, display.backbuffer_pool_start,
                     display.backbuffer_pool_end);
        write_region(3, display.descriptor_start,
                     display.descriptor_end);
        write_region(4, display.fifo_start,
                     display.fifo_end);
    }
    for (uint32_t region = display_active ? 5U : 1U;
         region < MICRONUX_DMA_REGION_COUNT;
         ++region) {
        write_region(region, MICRONUX_DMA_EMPTY_LOW,
                     MICRONUX_DMA_EMPTY_HIGH);
    }

    REG_WRITE(PMS_DMA_SDMMC_PMS_R_REG, MICRONUX_DMA_REGION0_MASK);
    REG_WRITE(PMS_DMA_SDMMC_PMS_W_REG, MICRONUX_DMA_REGION0_MASK);
    if (display_active) {
        REG_WRITE(s_gdma_read_permission_registers[display.dma_channel],
                  MICRONUX_DMA_REGION1_MASK |
                      MICRONUX_DMA_REGION2_MASK |
                      MICRONUX_DMA_REGION3_MASK);
        REG_WRITE(s_gdma_write_permission_registers[display.dma_channel],
                  MICRONUX_DMA_REGION3_MASK |
                      MICRONUX_DMA_REGION4_MASK);
    }
    __asm__ __volatile__("fence iorw, iorw" ::: "memory");

    if (!region_matches(0, MICRONUX_DMA_WINDOW_START,
                        MICRONUX_DMA_WINDOW_END)) {
        ESP_LOGE(TAG,
                 "MICRONUX:M7:DMA-PMS state=fail reason=region0"
                 " low=%08" PRIx32 " high=%08" PRIx32,
                 REG_READ(PMS_DMA_REGION0_LOW_REG),
                 REG_READ(PMS_DMA_REGION0_HIGH_REG));
        return ESP_ERR_INVALID_STATE;
    }
    if (display_active &&
        (!region_matches(1, display.frontbuffer_start,
                         display.frontbuffer_end) ||
         !region_matches(2, display.backbuffer_pool_start,
                         display.backbuffer_pool_end) ||
         !region_matches(3, display.descriptor_start,
                         display.descriptor_end) ||
         !region_matches(4, display.fifo_start,
                         display.fifo_end))) {
        ESP_LOGE(TAG,
                 "MICRONUX:M7:DMA-PMS state=fail"
                 " reason=display-regions");
        return ESP_ERR_INVALID_STATE;
    }
    for (uint32_t region = display_active ? 5U : 1U;
         region < MICRONUX_DMA_REGION_COUNT;
         ++region) {
        if (!region_matches(region, MICRONUX_DMA_EMPTY_LOW,
                            MICRONUX_DMA_EMPTY_HIGH)) {
            ESP_LOGE(TAG,
                     "MICRONUX:M7:DMA-PMS state=fail reason=region"
                     " index=%" PRIu32,
                     region);
            return ESP_ERR_INVALID_STATE;
        }
    }

    for (size_t index = 0;
         index < sizeof(s_permission_registers) /
                     sizeof(s_permission_registers[0]);
         ++index) {
        const uint32_t reg = s_permission_registers[index];
        uint32_t expected = 0;
        if (reg == PMS_DMA_SDMMC_PMS_R_REG ||
            reg == PMS_DMA_SDMMC_PMS_W_REG) {
            expected = MICRONUX_DMA_REGION0_MASK;
        } else if (display_active &&
                   reg == s_gdma_read_permission_registers[
                              display.dma_channel]) {
            expected = MICRONUX_DMA_REGION1_MASK |
                       MICRONUX_DMA_REGION2_MASK |
                       MICRONUX_DMA_REGION3_MASK;
        } else if (display_active &&
                   reg == s_gdma_write_permission_registers[
                              display.dma_channel]) {
            expected = MICRONUX_DMA_REGION3_MASK |
                       MICRONUX_DMA_REGION4_MASK;
        }

        if (REG_READ(reg) != expected) {
            ESP_LOGE(TAG,
                     "MICRONUX:M7:DMA-PMS state=fail reason=permission"
                     " reg=%08" PRIx32 " expected=%08" PRIx32
                     " actual=%08" PRIx32,
                     reg, expected, REG_READ(reg));
            return ESP_ERR_INVALID_STATE;
        }
    }

    if (display_active) {
        ESP_LOGI(TAG,
                 "MICRONUX:M7:DMA-PMS state=pass"
                 " region0=[%08" PRIx32 ",%08" PRIx32 ")"
                 " sdmmc=rw:%08" PRIx32
                 " display=ch%" PRIu32 ":r:%08" PRIx32 ":w:%08" PRIx32
                 " front=[%08" PRIx32 ",%08" PRIx32 ")"
                 " back-pool=[%08" PRIx32 ",%08" PRIx32 ")"
                 " desc=[%08" PRIx32 ",%08" PRIx32 ")"
                 " fifo=[%08" PRIx32 ",%08" PRIx32 ") other=deny",
                 MICRONUX_DMA_WINDOW_START, MICRONUX_DMA_WINDOW_END,
                 MICRONUX_DMA_REGION0_MASK, display.dma_channel,
                 MICRONUX_DMA_REGION1_MASK | MICRONUX_DMA_REGION2_MASK |
                     MICRONUX_DMA_REGION3_MASK,
                 MICRONUX_DMA_REGION3_MASK | MICRONUX_DMA_REGION4_MASK,
                 display.frontbuffer_start, display.frontbuffer_end,
                 display.backbuffer_pool_start, display.backbuffer_pool_end,
                 display.descriptor_start, display.descriptor_end,
                 display.fifo_start, display.fifo_end);
    } else {
        ESP_LOGI(TAG,
                 "MICRONUX:M7:DMA-PMS state=pass"
                 " region0=[%08" PRIx32 ",%08" PRIx32 ")"
                 " sdmmc=rw:%08" PRIx32 " other=deny",
                 MICRONUX_DMA_WINDOW_START, MICRONUX_DMA_WINDOW_END,
                 MICRONUX_DMA_REGION0_MASK);
    }
    return ESP_OK;
#endif
}
