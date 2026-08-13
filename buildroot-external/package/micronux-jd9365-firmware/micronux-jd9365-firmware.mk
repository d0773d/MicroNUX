################################################################################
#
# micronux-jd9365-firmware
#
################################################################################

MICRONUX_JD9365_FIRMWARE_VERSION = 2.0.0-1
MICRONUX_JD9365_FIRMWARE_SITE = $(BR2_EXTERNAL_MICRONUX_PATH)/package/micronux-jd9365-firmware
MICRONUX_JD9365_FIRMWARE_SITE_METHOD = local
MICRONUX_JD9365_FIRMWARE_LICENSE = Apache-2.0
MICRONUX_JD9365_FIRMWARE_LICENSE_FILES = LICENSE
MICRONUX_JD9365_FIRMWARE_DEPENDENCIES = host-python3

define MICRONUX_JD9365_FIRMWARE_BUILD_CMDS
	PYTHONDONTWRITEBYTECODE=1 $(HOST_DIR)/bin/python3 \
		$(@D)/generate_firmware.py \
		--source $(@D)/jd9365-waveshare-10.1-v2.json \
		--output $(@D)/jd9365-waveshare-10.1-v2.bin
	PYTHONDONTWRITEBYTECODE=1 $(HOST_DIR)/bin/python3 \
		$(@D)/test_firmware.py \
		--source $(@D)/jd9365-waveshare-10.1-v2.json \
		--binary $(@D)/jd9365-waveshare-10.1-v2.bin
	PYTHONDONTWRITEBYTECODE=1 $(HOST_DIR)/bin/python3 \
		$(@D)/generate_firmware.py \
		--source $(@D)/jd9365-waveshare-10.1-v2.json \
		--output $(@D)/jd9365-waveshare-10.1-v2.bin --check
endef

define MICRONUX_JD9365_FIRMWARE_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0644 \
		$(@D)/jd9365-waveshare-10.1-v2.bin \
		$(TARGET_DIR)/lib/firmware/micronux/jd9365-waveshare-10.1-v2.bin
	$(INSTALL) -D -m 0644 $(@D)/LICENSE \
		$(TARGET_DIR)/lib/firmware/micronux/jd9365-waveshare-10.1-v2.LICENSE
	$(INSTALL) -D -m 0644 $(@D)/PROVENANCE.md \
		$(TARGET_DIR)/lib/firmware/micronux/jd9365-waveshare-10.1-v2.provenance.txt
endef

$(eval $(generic-package))
