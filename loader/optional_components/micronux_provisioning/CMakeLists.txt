idf_component_register(
    SRCS "micronux_provisioning.c"
    REQUIRES
        bt
        esp_event
        esp_netif
        esp_wifi
        nvs_flash
        protocomm
    PRIV_REQUIRES
        esp_hosted
        esp_wifi_remote
        freertos
        mbedtls
        network_provisioning
        qrcode
)

# nvs_flash is also private to the Wi-Fi stack. Keep the P4 proof store as an
# explicit API dependency under MINIMAL_BUILD so its headers cannot be pruned.
target_link_libraries(${COMPONENT_LIB} PRIVATE idf::nvs_flash)
