################################################################################
#
# micronux-display-test
#
################################################################################

MICRONUX_DISPLAY_TEST_VERSION = 1.0
MICRONUX_DISPLAY_TEST_SITE = $(BR2_EXTERNAL_MICRONUX_PATH)/package/micronux-display-test
MICRONUX_DISPLAY_TEST_SITE_METHOD = local
MICRONUX_DISPLAY_TEST_LICENSE = MIT
MICRONUX_DISPLAY_TEST_LICENSE_FILES = LICENSE

define MICRONUX_DISPLAY_TEST_BUILD_CMDS
	$(TARGET_CC) $(TARGET_CFLAGS) -D_GNU_SOURCE -std=c11 -Os \
		-Wall -Wextra -Werror \
		$(@D)/micronux-display-test.c \
		-o $(@D)/micronux-display-test $(TARGET_LDFLAGS)
endef

define MICRONUX_DISPLAY_TEST_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/micronux-display-test \
		$(TARGET_DIR)/usr/bin/micronux-display-test
endef

$(eval $(generic-package))
