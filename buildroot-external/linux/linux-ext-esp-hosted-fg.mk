################################################################################
#
# ESP-Hosted-FG Linux extension
#
################################################################################

LINUX_EXTENSIONS += esp-hosted-fg

define ESP_HOSTED_FG_PREPARE_KERNEL
	rm -rf $(LINUX_DIR)/drivers/net/wireless/esp-hosted-fg
	mkdir -p $(LINUX_DIR)/drivers/net/wireless/esp-hosted-fg/include
	cp -dpfr $(ESP_HOSTED_FG_DIR)/esp_hosted_fg/host/linux/host_driver/esp32/. \
		$(LINUX_DIR)/drivers/net/wireless/esp-hosted-fg/
	cp -dpfr $(ESP_HOSTED_FG_DIR)/esp_hosted_fg/common/include/. \
		$(LINUX_DIR)/drivers/net/wireless/esp-hosted-fg/include/
endef
