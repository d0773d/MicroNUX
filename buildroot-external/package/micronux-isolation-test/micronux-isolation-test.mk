################################################################################
#
# micronux-isolation-test
#
################################################################################

MICRONUX_ISOLATION_TEST_VERSION = 1.0
MICRONUX_ISOLATION_TEST_SITE = $(BR2_EXTERNAL_MICRONUX_PATH)/package/micronux-isolation-test
MICRONUX_ISOLATION_TEST_SITE_METHOD = local
MICRONUX_ISOLATION_TEST_LICENSE = MIT
MICRONUX_ISOLATION_TEST_LICENSE_FILES = LICENSE

define MICRONUX_ISOLATION_TEST_BUILD_CMDS
	$(TARGET_CC) $(TARGET_CFLAGS) -D_GNU_SOURCE -std=c11 -Os \
		-Wall -Wextra -Werror \
		$(@D)/micronux-isolation-probe.c \
		-o $(@D)/micronux-isolation-probe $(TARGET_LDFLAGS)
	$(TARGET_CC) $(TARGET_CFLAGS) -D_GNU_SOURCE -std=c11 -Os \
		-Wall -Wextra -Werror \
		$(@D)/micronux-isolation-fault.c \
		-o $(@D)/micronux-isolation-fault $(TARGET_LDFLAGS)
endef

define MICRONUX_ISOLATION_TEST_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/micronux-isolation-probe \
		$(TARGET_DIR)/usr/bin/micronux-isolation-probe
	$(INSTALL) -D -m 0755 $(@D)/micronux-isolation-fault \
		$(TARGET_DIR)/usr/bin/micronux-isolation-fault
endef

$(eval $(generic-package))
