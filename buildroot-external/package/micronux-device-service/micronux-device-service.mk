################################################################################
#
# micronux-device-service
#
################################################################################

MICRONUX_DEVICE_SERVICE_VERSION = 1.0
MICRONUX_DEVICE_SERVICE_SITE = $(BR2_EXTERNAL_MICRONUX_PATH)/package/micronux-device-service
MICRONUX_DEVICE_SERVICE_SITE_METHOD = local
MICRONUX_DEVICE_SERVICE_LICENSE = MIT
MICRONUX_DEVICE_SERVICE_LICENSE_FILES = LICENSE
MICRONUX_DEVICE_SERVICE_INSTALL_STAGING = YES

MICRONUX_DEVICE_SERVICE_CFLAGS = $(TARGET_CFLAGS) -D_GNU_SOURCE -std=c11 \
	-Os -Wall -Wextra -Werror -I$(@D)/include

define MICRONUX_DEVICE_SERVICE_BUILD_CMDS
	$(TARGET_CC) $(MICRONUX_DEVICE_SERVICE_CFLAGS) -c \
		$(@D)/libmicronux.c -o $(@D)/libmicronux.o
	$(TARGET_AR) rcs $(@D)/libmicronux.a $(@D)/libmicronux.o
	$(TARGET_CC) $(MICRONUX_DEVICE_SERVICE_CFLAGS) \
		$(@D)/micronux-deviced.c -o $(@D)/micronux-deviced \
		$(TARGET_LDFLAGS) -lrt
	$(TARGET_CC) $(MICRONUX_DEVICE_SERVICE_CFLAGS) \
		$(@D)/micronux-device.c $(@D)/libmicronux.a \
		-o $(@D)/micronux-device $(TARGET_LDFLAGS)
	$(TARGET_CC) $(MICRONUX_DEVICE_SERVICE_CFLAGS) \
		$(@D)/micronux-device-native.c $(@D)/libmicronux.a \
		-o $(@D)/micronux-device-native $(TARGET_LDFLAGS)
	$(TARGET_CC) $(MICRONUX_DEVICE_SERVICE_CFLAGS) \
		$(@D)/micronux-device-selftest.c $(@D)/libmicronux.a \
		-o $(@D)/micronux-device-selftest $(TARGET_LDFLAGS)
endef

define MICRONUX_DEVICE_SERVICE_INSTALL_STAGING_CMDS
	$(INSTALL) -D -m 0644 $(@D)/include/micronux/device.h \
		$(STAGING_DIR)/usr/include/micronux/device.h
	$(INSTALL) -D -m 0644 $(@D)/libmicronux.a \
		$(STAGING_DIR)/usr/lib/libmicronux.a
endef

define MICRONUX_DEVICE_SERVICE_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/micronux-deviced \
		$(TARGET_DIR)/usr/sbin/micronux-deviced
	$(INSTALL) -D -m 0755 $(@D)/micronux-deviced-supervise \
		$(TARGET_DIR)/usr/sbin/micronux-deviced-supervise
	$(INSTALL) -D -m 0755 $(@D)/micronux-device \
		$(TARGET_DIR)/usr/bin/micronux-device
	$(INSTALL) -D -m 0755 $(@D)/micronux-device-native \
		$(TARGET_DIR)/usr/bin/micronux-device-native
	$(INSTALL) -D -m 0755 $(@D)/micronux-device-selftest \
		$(TARGET_DIR)/usr/bin/micronux-device-selftest
endef

$(eval $(generic-package))
