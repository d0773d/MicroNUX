// SPDX-License-Identifier: MIT

#include <inttypes.h>
#include <stddef.h>

#include "esp_chip_info.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_psram.h"

#include "micronux_mipi_dsi.h"

#define MICRONUX_STANDALONE_PSRAM_SIZE (32U * 1024U * 1024U)
#define MICRONUX_STANDALONE_SILICON_REVISION UINT32_C(103)

static const char *const TAG = "micronux_idf_display";

void app_main(void)
{
    esp_chip_info_t chip_info = {0};
    esp_chip_info(&chip_info);

    ESP_LOGI(TAG,
             "MICRONUX:IDF-DISPLAY state=start owner=esp-idf"
             " target=esp32p4 revision=%" PRIu32
             " linux=disabled sdmmc=disabled c6=disabled",
             chip_info.revision);
    ESP_ERROR_CHECK(chip_info.revision ==
                            MICRONUX_STANDALONE_SILICON_REVISION
                        ? ESP_OK
                        : ESP_ERR_NOT_SUPPORTED);
    ESP_ERROR_CHECK(esp_psram_get_size() == MICRONUX_STANDALONE_PSRAM_SIZE
                        ? ESP_OK
                        : ESP_ERR_INVALID_SIZE);
    ESP_ERROR_CHECK(micronux_mipi_dsi_prepare());
    micronux_mipi_dsi_standalone_run();
    ESP_ERROR_CHECK(ESP_ERR_INVALID_STATE);
}
