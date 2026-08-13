################################################################################
#
# micronux-ignitevm
#
################################################################################

MICRONUX_IGNITEVM_VERSION = 0.1.0
MICRONUX_IGNITEVM_SITE = $(BR2_EXTERNAL_MICRONUX_PATH)/package/micronux-ignitevm
MICRONUX_IGNITEVM_SITE_METHOD = local
MICRONUX_IGNITEVM_LICENSE = MIT (MicroNUX adapter), Proprietary (IgniteVM)
MICRONUX_IGNITEVM_LICENSE_FILES = LICENSE
MICRONUX_IGNITEVM_DEPENDENCIES = cjson micronux-device-service

MICRONUX_IGNITEVM_SRC = $(MICRONUX_IGNITEVM_SOURCE_DIR)/firmware/components
MICRONUX_IGNITEVM_INCLUDES = \
	-I$(MICRONUX_IGNITEVM_SRC)/bsp_firewall/include \
	-I$(MICRONUX_IGNITEVM_SRC)/board_resource_manager/include \
	-I$(MICRONUX_IGNITEVM_SRC)/event_queue/include \
	-I$(MICRONUX_IGNITEVM_SRC)/gc/include \
	-I$(MICRONUX_IGNITEVM_SRC)/ignite_vm/include \
	-I$(MICRONUX_IGNITEVM_SRC)/input_events/include \
	-I$(MICRONUX_IGNITEVM_SRC)/native_ffi/include \
	-I$(MICRONUX_IGNITEVM_SRC)/package_loader/include \
	-I$(MICRONUX_IGNITEVM_SRC)/runtime_scheduler/include \
	-I$(STAGING_DIR)/usr/include/cjson \
	-I$(STAGING_DIR)/usr/include
MICRONUX_IGNITEVM_SOURCES = \
	$(@D)/micronux-ignite.c \
	$(MICRONUX_IGNITEVM_SRC)/bsp_firewall/bsp_firewall.c \
	$(MICRONUX_IGNITEVM_SRC)/event_queue/event_queue.c \
	$(MICRONUX_IGNITEVM_SRC)/gc/gc.c \
	$(MICRONUX_IGNITEVM_SRC)/ignite_vm/ignite_decimal.c \
	$(MICRONUX_IGNITEVM_SRC)/ignite_vm/ignite_packet_codec.c \
	$(MICRONUX_IGNITEVM_SRC)/ignite_vm/ignite_structured_data.c \
	$(MICRONUX_IGNITEVM_SRC)/ignite_vm/ignite_typed_wire.c \
	$(MICRONUX_IGNITEVM_SRC)/ignite_vm/ignite_vm.c \
	$(MICRONUX_IGNITEVM_SRC)/input_events/input_events.c \
	$(MICRONUX_IGNITEVM_SRC)/native_ffi/native_hardware.c \
	$(MICRONUX_IGNITEVM_SRC)/package_loader/package_loader.c
MICRONUX_IGNITEVM_CFLAGS = $(TARGET_CFLAGS) -D_GNU_SOURCE -std=c11 -Os \
	-Wall -Wextra -Werror -Wno-sign-compare -ffunction-sections \
	-fdata-sections $(MICRONUX_IGNITEVM_INCLUDES)

define MICRONUX_IGNITEVM_BUILD_CMDS
	test -n "$(MICRONUX_IGNITEVM_SOURCE_DIR)"
	test -f "$(MICRONUX_IGNITEVM_SRC)/ignite_vm/ignite_vm.c"
	python3 "$(MICRONUX_IGNITEVM_SOURCE_DIR)/tools/ignite_native_compile.py" \
		$(@D)/apps/device-status.ignite $(@D)/device-status.bytecode \
		--emit-asm $(@D)/device-status.asm
	python3 "$(MICRONUX_IGNITEVM_SOURCE_DIR)/tools/igpk_pack.py" \
		$(@D)/apps/device-status.manifest.json $(@D)/device-status.bytecode \
		$(@D)/device-status.igpk
	python3 "$(MICRONUX_IGNITEVM_SOURCE_DIR)/tools/ignite_native_compile.py" \
		$(@D)/apps/device-fault.ignite $(@D)/device-fault.bytecode \
		--emit-asm $(@D)/device-fault.asm
	python3 "$(MICRONUX_IGNITEVM_SOURCE_DIR)/tools/igpk_pack.py" \
		$(@D)/apps/device-fault.manifest.json $(@D)/device-fault.bytecode \
		$(@D)/device-fault.igpk
	$(TARGET_CC) $(MICRONUX_IGNITEVM_CFLAGS) \
		$(MICRONUX_IGNITEVM_SOURCES) \
		-o $(@D)/micronux-ignite $(TARGET_LDFLAGS) \
		-Wl,--gc-sections -L$(STAGING_DIR)/usr/lib -lcjson \
		$(STAGING_DIR)/usr/lib/libmicronux.a
endef

define MICRONUX_IGNITEVM_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/micronux-ignite \
		$(TARGET_DIR)/usr/bin/micronux-ignite
	$(INSTALL) -D -m 0644 $(@D)/device-status.igpk \
		$(TARGET_DIR)/usr/share/micronux/ignite/device-status.igpk
	$(INSTALL) -D -m 0644 $(@D)/device-fault.igpk \
		$(TARGET_DIR)/usr/share/micronux/ignite/device-fault.igpk
	$(INSTALL) -D -m 0644 $(@D)/apps/device-status.ignite \
		$(TARGET_DIR)/usr/share/micronux/ignite/device-status.ignite
	$(INSTALL) -D -m 0644 $(@D)/apps/device-fault.ignite \
		$(TARGET_DIR)/usr/share/micronux/ignite/device-fault.ignite
endef

$(eval $(generic-package))
