################################################################################
#
# micronux-job-supervisor
#
################################################################################

MICRONUX_JOB_SUPERVISOR_VERSION = 1.0
MICRONUX_JOB_SUPERVISOR_SITE = $(BR2_EXTERNAL_MICRONUX_PATH)/package/micronux-job-supervisor
MICRONUX_JOB_SUPERVISOR_SITE_METHOD = local
MICRONUX_JOB_SUPERVISOR_LICENSE = MIT
MICRONUX_JOB_SUPERVISOR_LICENSE_FILES = LICENSE
MICRONUX_JOB_SUPERVISOR_DEPENDENCIES = micronux-device-service
MICRONUX_JOB_SUPERVISOR_CFLAGS = $(TARGET_CFLAGS) -D_GNU_SOURCE -std=c11 \
	-Os -Wall -Wextra -Werror -I$(@D) -I$(STAGING_DIR)/usr/include

define MICRONUX_JOB_SUPERVISOR_USERS
	micronux-job 1000 micronux-job 1000 * - - - MicroNUX sandboxed job
endef

define MICRONUX_JOB_SUPERVISOR_BUILD_CMDS
	$(TARGET_CC) $(MICRONUX_JOB_SUPERVISOR_CFLAGS) \
		$(@D)/micronux-run.c -o $(@D)/micronux-run \
		$(TARGET_LDFLAGS) -lrt
	$(TARGET_CC) $(MICRONUX_JOB_SUPERVISOR_CFLAGS) \
		$(@D)/micronux-job-exec.c -o $(@D)/micronux-job-exec \
		$(TARGET_LDFLAGS)
	$(TARGET_CC) $(MICRONUX_JOB_SUPERVISOR_CFLAGS) \
		$(@D)/micronux-job-test.c \
		$(STAGING_DIR)/usr/lib/libmicronux.a \
		-o $(@D)/micronux-job-test $(TARGET_LDFLAGS)
endef

define MICRONUX_JOB_SUPERVISOR_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0750 $(@D)/micronux-run \
		$(TARGET_DIR)/usr/sbin/micronux-run
	$(INSTALL) -D -m 0750 $(@D)/micronux-job-exec \
		$(TARGET_DIR)/usr/libexec/micronux-job-exec
	$(INSTALL) -D -m 0755 $(@D)/micronux-job-test \
		$(TARGET_DIR)/usr/libexec/micronux-job-test
	$(INSTALL) -d -m 0755 $(TARGET_DIR)/opt/micronux/apps
endef

$(eval $(generic-package))
