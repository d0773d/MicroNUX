################################################################################
#
# micronux-netctl
#
################################################################################

MICRONUX_NETCTL_VERSION = 1.0
MICRONUX_NETCTL_SITE = $(BR2_EXTERNAL_MICRONUX_PATH)/package/micronux-netctl
MICRONUX_NETCTL_SITE_METHOD = local
MICRONUX_NETCTL_LICENSE = MIT
MICRONUX_NETCTL_LICENSE_FILES = LICENSE

define MICRONUX_NETCTL_BUILD_CMDS
	$(TARGET_CC) $(TARGET_CFLAGS) -std=c11 -Os -Wall -Wextra -Werror \
		$(@D)/micronux-netctl.c -o $(@D)/micronux-netctl \
		$(TARGET_LDFLAGS)
endef

define MICRONUX_NETCTL_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/micronux-netctl \
		$(TARGET_DIR)/usr/bin/micronux-netctl
endef

$(eval $(generic-package))
