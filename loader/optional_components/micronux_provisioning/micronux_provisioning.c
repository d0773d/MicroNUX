// SPDX-License-Identifier: MIT

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sdkconfig.h"
#include "esp_err.h"
#include "esp_event.h"
#include "esp_hosted.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "esp_wifi_default.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "mbedtls/platform_util.h"
#include "network_provisioning/manager.h"
#include "network_provisioning/scheme_ble.h"
#include "network_provisioning/scheme_softap.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "protocomm_security.h"
#include "protocomm_ble.h"
#include "esp_srp.h"
#include "qrcode.h"

#define MICRONUX_PROV_SUCCESS_BIT BIT0
#define MICRONUX_PROV_WINDOW_SECONDS 120U
#define MICRONUX_PROV_SESSION_MAX_SECONDS 300U
#define MICRONUX_PROV_POLL_MS 1000U
#define MICRONUX_PROV_CLIENT_ACK_MS 1000U
#define MICRONUX_PROV_POP_LENGTH 16U
#define MICRONUX_PROV_SALT_LENGTH 16

static const char *TAG = "micronux-prov";
static const char *PROV_USERNAME = "micronux";
static const char *PROV_NVS_NAMESPACE = "micronux";
static const char *PROV_NVS_POP_KEY = "prov_pop";

typedef enum {
    MICRONUX_PROV_TRANSPORT_BLE,
    MICRONUX_PROV_TRANSPORT_SOFTAP,
} micronux_prov_transport_t;

typedef struct {
    esp_netif_t *sta_netif;
    esp_netif_t *ap_netif;
    EventGroupHandle_t events;
    volatile bool client_connected;
    bool wifi_initialized;
} micronux_prov_context_t;

static micronux_prov_context_t s_prov;

static const char *transport_name(micronux_prov_transport_t transport)
{
    return transport == MICRONUX_PROV_TRANSPORT_BLE ? "ble" : "softap";
}

static void provisioning_event_handler(void *arg, esp_event_base_t event_base,
                                       int32_t event_id, void *event_data)
{
    (void)arg;

    if (event_base == NETWORK_PROV_EVENT) {
        switch (event_id) {
        case NETWORK_PROV_START:
            ESP_LOGI(TAG, "MICRONUX:M6:PROV service=started");
            break;
        case NETWORK_PROV_WIFI_CRED_RECV:
            /* Deliberately do not print the SSID or password. */
            ESP_LOGI(TAG, "MICRONUX:M6:PROV credentials=received redacted=yes");
            break;
        case NETWORK_PROV_WIFI_CRED_FAIL: {
            const network_prov_wifi_sta_fail_reason_t reason =
                *(network_prov_wifi_sta_fail_reason_t *)event_data;
            ESP_LOGW(TAG, "MICRONUX:M6:PROV credentials=rejected reason=%s",
                     reason == NETWORK_PROV_WIFI_STA_AUTH_ERROR
                         ? "authentication"
                         : "access-point-not-found");
            const esp_err_t err =
                network_prov_mgr_reset_wifi_sm_state_on_failure();
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "provisioning retry reset failed: %s",
                         esp_err_to_name(err));
            }
            break;
        }
        case NETWORK_PROV_WIFI_CRED_SUCCESS:
            ESP_LOGI(TAG,
                     "MICRONUX:M6:PROV credentials=accepted storage=c6-nvs");
            xEventGroupSetBits(s_prov.events, MICRONUX_PROV_SUCCESS_BIT);
            break;
        default:
            break;
        }
        return;
    }

    if (event_base == PROTOCOMM_TRANSPORT_BLE_EVENT) {
        if (event_id == PROTOCOMM_TRANSPORT_BLE_CONNECTED) {
            s_prov.client_connected = true;
        } else if (event_id == PROTOCOMM_TRANSPORT_BLE_DISCONNECTED) {
            s_prov.client_connected = false;
        }
        return;
    }

    if (event_base == WIFI_EVENT) {
        if (event_id == WIFI_EVENT_AP_STACONNECTED) {
            s_prov.client_connected = true;
        } else if (event_id == WIFI_EVENT_AP_STADISCONNECTED) {
            s_prov.client_connected = false;
        }
    }
}

static esp_err_t load_or_create_pop(char pop[MICRONUX_PROV_POP_LENGTH + 1])
{
    static const char alphabet[] = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
    nvs_handle_t handle;
    esp_err_t err = nvs_open(PROV_NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (err != ESP_OK) {
        return err;
    }

    size_t length = MICRONUX_PROV_POP_LENGTH + 1;
    err = nvs_get_str(handle, PROV_NVS_POP_KEY, pop, &length);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        uint8_t random[MICRONUX_PROV_POP_LENGTH];
        esp_fill_random(random, sizeof(random));
        for (size_t i = 0; i < MICRONUX_PROV_POP_LENGTH; ++i) {
            pop[i] = alphabet[random[i] & 31U];
        }
        pop[MICRONUX_PROV_POP_LENGTH] = '\0';
        mbedtls_platform_zeroize(random, sizeof(random));

        err = nvs_set_str(handle, PROV_NVS_POP_KEY, pop);
        if (err == ESP_OK) {
            err = nvs_commit(handle);
        }
    } else if (err == ESP_OK &&
               length != MICRONUX_PROV_POP_LENGTH + 1) {
        err = ESP_ERR_INVALID_SIZE;
    }

    nvs_close(handle);
    return err;
}

static esp_err_t make_service_name(char *service_name, size_t length)
{
    uint8_t mac[6];
    esp_err_t err = esp_wifi_get_mac(WIFI_IF_STA, mac);
    if (err != ESP_OK) {
        return err;
    }
    if (snprintf(service_name, length, "MICRONUX_%02X%02X%02X",
                 mac[3], mac[4], mac[5]) >= (int)length) {
        return ESP_ERR_INVALID_SIZE;
    }
    return ESP_OK;
}

static void print_qr(const char *service_name, const char *pop,
                     micronux_prov_transport_t transport)
{
    char payload[192];
    const int written = snprintf(
        payload, sizeof(payload),
        "{\"ver\":\"v1\",\"name\":\"%s\",\"username\":\"%s\","
        "\"pop\":\"%s\",\"transport\":\"%s\"}",
        service_name, PROV_USERNAME, pop, transport_name(transport));
    if (written < 0 || written >= (int)sizeof(payload)) {
        ESP_LOGE(TAG, "provisioning QR payload overflow");
        return;
    }

    ESP_LOGI(TAG,
             "Scan this Security 2 QR code with the Espressif provisioning app");
    esp_qrcode_config_t config = ESP_QRCODE_CONFIG_DEFAULT();
    esp_qrcode_generate(&config, payload);
    mbedtls_platform_zeroize(payload, sizeof(payload));
}

static network_prov_mgr_config_t manager_config(
    micronux_prov_transport_t transport)
{
    network_prov_mgr_config_t config = {
        .scheme = transport == MICRONUX_PROV_TRANSPORT_BLE
                      ? network_prov_scheme_ble
                      : network_prov_scheme_softap,
        .scheme_event_handler = NETWORK_PROV_EVENT_HANDLER_NONE,
        .app_event_handler = NETWORK_PROV_EVENT_HANDLER_NONE,
        .network_prov_wifi_conn_cfg = {
            .wifi_conn_attempts = 3,
        },
    };
    return config;
}

static esp_err_t stop_manager(bool manager_started)
{
    if (manager_started) {
        network_prov_mgr_stop_provisioning();
        network_prov_mgr_wait();
    }
    return network_prov_mgr_deinit();
}

static esp_err_t run_transport(micronux_prov_transport_t transport,
                               const char *service_name, const char *pop,
                               bool *provisioned)
{
    char *salt = NULL;
    char *verifier = NULL;
    int verifier_length = 0;
    bool bt_started = false;
    bool manager_initialized = false;
    bool manager_started = false;
    esp_err_t err = ESP_OK;

    *provisioned = false;
    s_prov.client_connected = false;
    xEventGroupClearBits(s_prov.events, MICRONUX_PROV_SUCCESS_BIT);

    if (transport == MICRONUX_PROV_TRANSPORT_BLE) {
        err = esp_hosted_bt_controller_init();
        if (err != ESP_OK) {
            goto cleanup;
        }
        err = esp_hosted_bt_controller_enable();
        if (err != ESP_OK) {
            esp_hosted_bt_controller_deinit(false);
            goto cleanup;
        }
        bt_started = true;
    }

    err = esp_srp_gen_salt_verifier(
        PROV_USERNAME, strlen(PROV_USERNAME), pop, strlen(pop), &salt,
        MICRONUX_PROV_SALT_LENGTH, &verifier, &verifier_length);
    if (err != ESP_OK) {
        goto cleanup;
    }
    network_prov_security2_params_t security = {
        .salt = salt,
        .salt_len = MICRONUX_PROV_SALT_LENGTH,
        .verifier = verifier,
        .verifier_len = verifier_length,
    };

    err = network_prov_mgr_init(manager_config(transport));
    if (err != ESP_OK) {
        goto cleanup;
    }
    manager_initialized = true;

    err = network_prov_mgr_disable_auto_stop(MICRONUX_PROV_CLIENT_ACK_MS);
    if (err != ESP_OK) {
        goto cleanup;
    }

    err = network_prov_mgr_start_provisioning(
        NETWORK_PROV_SECURITY_2, &security, service_name, NULL);
    if (err != ESP_OK) {
        goto cleanup;
    }
    manager_started = true;

    ESP_LOGI(TAG,
             "MICRONUX:M6:PROV transport=%s window=%us security=2 c6-firmware=unchanged",
             transport_name(transport), MICRONUX_PROV_WINDOW_SECONDS);
    print_qr(service_name, pop, transport);

    uint32_t idle_seconds = 0;
    for (uint32_t elapsed = 0;
         elapsed < MICRONUX_PROV_SESSION_MAX_SECONDS &&
         idle_seconds < MICRONUX_PROV_WINDOW_SECONDS;
         ++elapsed) {
        const EventBits_t bits = xEventGroupWaitBits(
            s_prov.events, MICRONUX_PROV_SUCCESS_BIT, pdFALSE, pdTRUE,
            pdMS_TO_TICKS(MICRONUX_PROV_POLL_MS));
        if ((bits & MICRONUX_PROV_SUCCESS_BIT) != 0) {
            *provisioned = true;
            break;
        }
        if (s_prov.client_connected) {
            idle_seconds = 0;
        } else {
            ++idle_seconds;
        }
    }

    if (!*provisioned) {
        ESP_LOGI(TAG,
                 "MICRONUX:M6:PROV transport=%s result=idle-timeout idle=%us",
                 transport_name(transport), idle_seconds);
    }

cleanup:
    if (manager_initialized) {
        const esp_err_t stop_err = stop_manager(manager_started);
        if (err == ESP_OK && stop_err != ESP_OK) {
            err = stop_err;
        }
    }
    if (bt_started) {
        const esp_err_t disable_err = esp_hosted_bt_controller_disable();
        const esp_err_t deinit_err = esp_hosted_bt_controller_deinit(false);
        if (err == ESP_OK && disable_err != ESP_OK) {
            err = disable_err;
        }
        if (err == ESP_OK && deinit_err != ESP_OK) {
            err = deinit_err;
        }
    }
    if (salt != NULL) {
        mbedtls_platform_zeroize(salt, MICRONUX_PROV_SALT_LENGTH);
        free(salt);
    }
    if (verifier != NULL) {
        mbedtls_platform_zeroize(verifier, verifier_length);
        free(verifier);
    }
    return err;
}

static void teardown_hosted(void)
{
    if (s_prov.wifi_initialized) {
        const esp_err_t stop_err = esp_wifi_stop();
        if (stop_err != ESP_OK && stop_err != ESP_ERR_WIFI_NOT_STARTED) {
            ESP_LOGW(TAG, "Wi-Fi stop failed during handoff: %s",
                     esp_err_to_name(stop_err));
        }
        const esp_err_t wifi_err = esp_wifi_deinit();
        if (wifi_err != ESP_OK) {
            ESP_LOGW(TAG, "Wi-Fi deinit failed during handoff: %s",
                     esp_err_to_name(wifi_err));
        }
        s_prov.wifi_initialized = false;
    }
    if (s_prov.ap_netif != NULL) {
        esp_netif_destroy_default_wifi(s_prov.ap_netif);
        s_prov.ap_netif = NULL;
    }
    if (s_prov.sta_netif != NULL) {
        esp_netif_destroy_default_wifi(s_prov.sta_netif);
        s_prov.sta_netif = NULL;
    }
    if (esp_hosted_deinit() != ESP_OK) {
        ESP_LOGW(TAG, "ESP-Hosted deinit failed during handoff");
    }
    if (s_prov.events != NULL) {
        vEventGroupDelete(s_prov.events);
        s_prov.events = NULL;
    }
}

esp_err_t micronux_provisioning_run(void)
{
    esp_err_t err = nvs_flash_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "P4 NVS init failed without erase: %s",
                 esp_err_to_name(err));
        return err;
    }
    err = esp_netif_init();
    if (err != ESP_OK) {
        return err;
    }
    err = esp_event_loop_create_default();
    if (err != ESP_OK) {
        return err;
    }
    s_prov.events = xEventGroupCreate();
    if (s_prov.events == NULL) {
        return ESP_ERR_NO_MEM;
    }

    err = esp_hosted_connect_to_slave();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "factory ESP32-C6 did not answer over SDIO");
        teardown_hosted();
        return err;
    }

    s_prov.sta_netif = esp_netif_create_default_wifi_sta();
    s_prov.ap_netif = esp_netif_create_default_wifi_ap();
    if (s_prov.sta_netif == NULL || s_prov.ap_netif == NULL) {
        teardown_hosted();
        return ESP_ERR_NO_MEM;
    }

    wifi_init_config_t wifi_config = WIFI_INIT_CONFIG_DEFAULT();
    err = esp_wifi_init(&wifi_config);
    if (err != ESP_OK) {
        teardown_hosted();
        return err;
    }
    s_prov.wifi_initialized = true;
    err = esp_wifi_set_storage(WIFI_STORAGE_FLASH);
    if (err != ESP_OK) {
        teardown_hosted();
        return err;
    }

    network_prov_mgr_config_t check_config =
        manager_config(MICRONUX_PROV_TRANSPORT_SOFTAP);
    err = network_prov_mgr_init(check_config);
    if (err != ESP_OK) {
        teardown_hosted();
        return err;
    }
    bool already_provisioned = false;
    err = network_prov_mgr_is_wifi_provisioned(&already_provisioned);
    const esp_err_t check_deinit_err = network_prov_mgr_deinit();
    if (err == ESP_OK) {
        err = check_deinit_err;
    }
    if (err != ESP_OK) {
        teardown_hosted();
        return err;
    }
    if (already_provisioned) {
        ESP_LOGI(TAG,
                 "MICRONUX:M6:PROV state=provisioned action=linux-handoff credentials=redacted");
        teardown_hosted();
        return ESP_OK;
    }

    char pop[MICRONUX_PROV_POP_LENGTH + 1] = {0};
    char service_name[24];
    err = load_or_create_pop(pop);
    if (err == ESP_OK) {
        err = make_service_name(service_name, sizeof(service_name));
    }
    if (err != ESP_OK) {
        mbedtls_platform_zeroize(pop, sizeof(pop));
        teardown_hosted();
        return err;
    }

    err = esp_event_handler_register(NETWORK_PROV_EVENT, ESP_EVENT_ANY_ID,
                                     provisioning_event_handler, NULL);
    if (err == ESP_OK) {
        err = esp_event_handler_register(PROTOCOMM_TRANSPORT_BLE_EVENT,
                                         ESP_EVENT_ANY_ID,
                                         provisioning_event_handler, NULL);
    }
    if (err == ESP_OK) {
        err = esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                         provisioning_event_handler, NULL);
    }

    bool provisioned = false;
    if (err == ESP_OK) {
        err = run_transport(MICRONUX_PROV_TRANSPORT_BLE, service_name, pop,
                            &provisioned);
        if (err != ESP_OK) {
            ESP_LOGW(TAG,
                     "MICRONUX:M6:PROV transport=ble result=unavailable fallback=softap error=%s",
                     esp_err_to_name(err));
        }
        if (!provisioned) {
            err = run_transport(MICRONUX_PROV_TRANSPORT_SOFTAP, service_name,
                                pop, &provisioned);
        }
    }

    esp_event_handler_unregister(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                 provisioning_event_handler);
    esp_event_handler_unregister(PROTOCOMM_TRANSPORT_BLE_EVENT,
                                 ESP_EVENT_ANY_ID,
                                 provisioning_event_handler);
    esp_event_handler_unregister(NETWORK_PROV_EVENT, ESP_EVENT_ANY_ID,
                                 provisioning_event_handler);
    mbedtls_platform_zeroize(pop, sizeof(pop));
    teardown_hosted();

    if (err != ESP_OK) {
        return err;
    }
    if (provisioned) {
        ESP_LOGI(TAG,
                 "MICRONUX:M6:PROV result=success action=p4-restart credentials=redacted");
        vTaskDelay(pdMS_TO_TICKS(250));
        esp_restart();
    }

    ESP_LOGW(TAG,
             "MICRONUX:M6:PROV result=deferred action=linux-handoff retry=next-boot");
    return ESP_OK;
}
