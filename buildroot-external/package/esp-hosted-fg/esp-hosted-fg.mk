################################################################################
#
# esp-hosted-fg
#
################################################################################

ESP_HOSTED_FG_VERSION = 1df17f74d62eede4127785ae9414e03e48e62ebd
ESP_HOSTED_FG_SITE = https://github.com/espressif/esp-hosted/archive
ESP_HOSTED_FG_SOURCE = $(ESP_HOSTED_FG_VERSION).tar.gz
ESP_HOSTED_FG_LICENSE = GPL-2.0-only
ESP_HOSTED_FG_LICENSE_FILES = esp_hosted_fg/host/linux/host_driver/esp32/LICENSE

$(eval $(generic-package))
