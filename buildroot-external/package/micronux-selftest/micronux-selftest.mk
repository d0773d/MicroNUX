################################################################################
#
# micronux-selftest
#
################################################################################

MICRONUX_SELFTEST_VERSION = 1.0
MICRONUX_SELFTEST_SITE = $(BR2_EXTERNAL_MICRONUX_PATH)/package/micronux-selftest
MICRONUX_SELFTEST_SITE_METHOD = local
MICRONUX_SELFTEST_LICENSE = MIT
MICRONUX_SELFTEST_LICENSE_FILES = LICENSE

define MICRONUX_SELFTEST_BUILD_CMDS
	$(TARGET_CC) $(TARGET_CFLAGS) -D_GNU_SOURCE -std=c11 -Os \
		-Wall -Wextra -Werror \
		$(@D)/micronux-selftest.c -o $(@D)/micronux-selftest \
		$(TARGET_LDFLAGS) -lrt
	$(TARGET_CC) $(TARGET_CFLAGS) -std=c11 -Os -Wall -Wextra -Werror \
		$(@D)/micronux-exec-child.c -o $(@D)/micronux-exec-child \
		$(TARGET_LDFLAGS)
endef

define MICRONUX_SELFTEST_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/micronux-selftest \
		$(TARGET_DIR)/usr/bin/micronux-selftest
	$(INSTALL) -D -m 0755 $(@D)/micronux-exec-child \
		$(TARGET_DIR)/usr/bin/micronux-exec-child
endef

$(eval $(generic-package))
