#!/usr/bin/env python3
"""Model-test the M9.2 display handoff and buffer ownership contract."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import importlib.util
import re
import struct
import sys
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# ABI v2 remains modeled until the shared M7 artifact is migrated. M9 cold mode
# must never accept its 124-byte live/adopt contract as ABI v3 cold relinquish.
HANDOFF_FORMAT = struct.Struct("<IHH29I")
HANDOFF_FIELDS = (
    "magic",
    "abi_version",
    "struct_size",
    "flags",
    "width",
    "height",
    "stride",
    "pixel_clock_hz",
    "lane_bit_rate_mbps",
    "data_lanes",
    "buffer_count",
    "ownership_state",
    "hsync_pulse_width",
    "hsync_back_porch",
    "hsync_front_porch",
    "vsync_pulse_width",
    "vsync_back_porch",
    "vsync_front_porch",
    "framebuffer0",
    "framebuffer1",
    "framebuffer2",
    "framebuffer_size",
    "descriptor_address",
    "descriptor_size",
    "descriptor_count",
    "dma_channel",
    "dsi_fifo_address",
    "i2c_address",
    "display_control_register",
    "backlight_register",
    "backlight_brightness",
    "crc32",
)

MAGIC = 0x4D4E5844
ABI_VERSION = 2
REQUIRED_FLAGS = 0xFF
FRAME_SIZE = 2_048_000
# ABI v2 retains the legacy 8-byte-aligned loader front. ABI v3 publishes
# page-isolated DMA resources so Linux can validate exact PMS pages.
FRAME_ADDRESSES = (0x48040A80, 0x49300000, 0x49500000)
COLD_PAGE_SIZE = 0x1000
COLD_FRAME_ADDRESSES = (0x48041000, 0x49300000, 0x49500000)
LOADER_PSRAM = (0x48000000, 0x48400000)
DISPLAY_POOL = (0x49300000, 0x49700000)
DESCRIPTOR_SRAM = (0x4FF00000, 0x4FFC0000)
SD_DMA_SRAM = (0x4FF80000, 0x4FF82000)
DSI_FIFO = 0x50105000
DSI_FIFO_WINDOW = (DSI_FIFO, DSI_FIFO + COLD_PAGE_SIZE)
DESCRIPTOR_SIZE = 64
DESCRIPTOR_COUNT = 3
DESCRIPTOR_BLOCK_SIZE = 0x0003E7FF
DESCRIPTOR_CTRL_LO = 0x001E1B41
DESCRIPTOR_CTRL_HI = 0x400F87C0
DW_GDMA_BLOCK_TRANSFER_LIST = 3
CHANNEL_CFG = (
    DW_GDMA_BLOCK_TRANSFER_LIST | (DW_GDMA_BLOCK_TRANSFER_LIST << 2),
    0x0A020001,
)
GDMA_STATUS0_DEFINED_MASK = 0xFA3F7FFB
GDMA_STATUS1_DEFINED_MASK = 0x0000000F
GDMA_COMMON_DEFINED_MASK = 0x001FFF8F
GDMA_ACTIVE_ENABLE_VALUES = (
    0x023F7FE2,
    0x0000000F,
    0x023F7FE2,
    0x0000000F,
    0x001FFF8F,
    0x001FFF8F,
)
GDMA_TEARDOWN_ENABLE_VALUES = (
    0x02000000,
    0x0000000F,
    0x02000000,
    0x0000000F,
    0x001FFE80,
    0x001FFE80,
)
GDMA_PATCH36_HARDWARE_VALUES = (
    0x07FFFFE6,
    0xFFFFFFFF,
    0x07FFFFE6,
    0xFFFFFFFF,
    0xFFFFFFFF,
    0xFFFFFFFF,
)
NATIVE_PROGRAMMED_VIDEO_POLICY = (
    (1 << 14) | sum(1 << bit for bit in range(8, 14)) | (1 << 15) | 2
)
LEGACY_ACTIVE_MIRROR_VIDEO_POLICY = (
    (1 << 8) | sum(1 << bit for bit in range(2, 8)) | (1 << 9) | 2
)
DISPLAY_OWNER_LINUX_PENDING = 1
DISPLAY_TIMING = (20, 20, 40, 4, 10, 30)
DISPLAY_SLOT_SIZE = 0x00200000
FRAMEBUFFER_ALIGNMENT = 8
COLD_FRAMEBUFFER_ALIGNMENT = COLD_PAGE_SIZE
PSRAM = (0x48000000, 0x4A000000)
LINUX_RAM = (0x48400000, 0x4A000000)
KERNEL_GENERAL_RAM = (0x48400000, 0x49300000)
KERNEL_LOAD_ADDRESS = KERNEL_GENERAL_RAM[0]
USER_POOL = (0x49700000, 0x49F00000)
COMMS_POOL = (0x49F00000, 0x4A000000)

COLD_RELINQUISH_FORMAT = struct.Struct("<IHH46I")
COLD_RELINQUISH_FIELDS = (
    "magic",
    "abi_version",
    "struct_size",
    "flags",
    "boot_mode",
    "ownership_state",
    "silicon_revision",
    "panel_profile_id",
    "width",
    "height",
    "stride",
    "pixel_format",
    "pixel_clock_hz",
    "lane_bit_rate_mbps",
    "data_lanes",
    "buffer_count",
    "hsync_pulse_width",
    "hsync_back_porch",
    "hsync_front_porch",
    "vsync_pulse_width",
    "vsync_back_porch",
    "vsync_front_porch",
    "framebuffer0",
    "framebuffer1",
    "framebuffer2",
    "framebuffer_size",
    "descriptor_address",
    "descriptor_size",
    "descriptor_count",
    "dma_channel",
    "dsi_fifo_address",
    "gdma_irq_source",
    "gdma_clic_irq",
    "i2c_port",
    "i2c_sda_gpio",
    "i2c_scl_gpio",
    "i2c_rate_hz",
    "i2c_address",
    "display_control_register",
    "backlight_register",
    "display_control_reset_assert_command",
    "display_control_reset_prepare_command",
    "display_control_reveal_command",
    "backlight_brightness",
    "reset_hold_ms",
    "pwm_zero_settle_ms",
    "panel_payload_crc32",
    "quiesce_sequence_id",
    "crc32",
)
COLD_ABI_VERSION = 3
COLD_BOOT_MODE = 1
COLD_OWNER_LINUX_PENDING = DISPLAY_OWNER_LINUX_PENDING
COLD_SILICON_REVISION = 103
COLD_PANEL_PROFILE_JD9365_WAVESHARE_10_1 = 1
COLD_PIXEL_FORMAT_RGB565_LE = 1
COLD_FLAG_RGB565 = 1 << 0
COLD_FLAG_LINUX_COLD_INIT = 1 << 1
COLD_FLAG_PWM_ZERO_WRITE_ACKED = 1 << 2
COLD_FLAG_RESET_PREPARE_WRITE_ACKED = 1 << 3
COLD_FLAG_RESET_ASSERT_WRITE_ACKED = 1 << 4
COLD_FLAG_HOST_BRIDGE_RESET = 1 << 5
COLD_FLAG_DPHY_LDO_DISABLED = 1 << 6
COLD_FLAG_GDMA_QUIESCED = 1 << 7
COLD_FLAG_TRIPLE_BUFFER_RESERVED = 1 << 8
COLD_FLAG_DESC_POOL_RESERVED = 1 << 9
COLD_FLAG_DMA_PMS_READY = 1 << 10
COLD_FLAG_GDMA_IRQ_ROUTE_READY = 1 << 11
COLD_FLAG_I2C_RELEASED = 1 << 12
COLD_FLAG_RUNTIME_IRQ_REARM_REQUIRED = 1 << 13
COLD_REQUIRED_FLAGS = 0x00003FFF
COLD_ALLOWED_FLAGS = COLD_REQUIRED_FLAGS
COLD_FLAG_VALUES = (
    COLD_FLAG_RGB565,
    COLD_FLAG_LINUX_COLD_INIT,
    COLD_FLAG_PWM_ZERO_WRITE_ACKED,
    COLD_FLAG_RESET_PREPARE_WRITE_ACKED,
    COLD_FLAG_RESET_ASSERT_WRITE_ACKED,
    COLD_FLAG_HOST_BRIDGE_RESET,
    COLD_FLAG_DPHY_LDO_DISABLED,
    COLD_FLAG_GDMA_QUIESCED,
    COLD_FLAG_TRIPLE_BUFFER_RESERVED,
    COLD_FLAG_DESC_POOL_RESERVED,
    COLD_FLAG_DMA_PMS_READY,
    COLD_FLAG_GDMA_IRQ_ROUTE_READY,
    COLD_FLAG_I2C_RELEASED,
    COLD_FLAG_RUNTIME_IRQ_REARM_REQUIRED,
)
COLD_DISPLAY_CONTROL_RESET_ASSERT_COMMAND = 0x11
COLD_DISPLAY_CONTROL_RESET_PREPARE_COMMAND = 0x13
COLD_DISPLAY_CONTROL_REVEAL_COMMAND = 0x17
COLD_DESCRIPTOR_ADDRESS = 0x4FF3C000
COLD_DESCRIPTOR_PUBLISHED_SIZE = DESCRIPTOR_SIZE * DESCRIPTOR_COUNT
COLD_DESCRIPTOR_BACKING_SIZE = COLD_PAGE_SIZE
COLD_GDMA_IRQ_SOURCE = 24
COLD_GDMA_CLIC_IRQ = 18
COLD_I2C_PORT = 0
COLD_I2C_SDA_GPIO = 7
COLD_I2C_SCL_GPIO = 8
COLD_I2C_RATE_HZ = 100_000
COLD_RESET_HOLD_MS = 10
COLD_PWM_ZERO_SETTLE_MS = 100
COLD_PANEL_PAYLOAD_CRC32 = 0xCEA07F9B
COLD_QUIESCE_SEQUENCE_ID = 1
COLD_DMA_PMS_READ_MASK = (1 << 1) | (1 << 2) | (1 << 3)
COLD_DMA_PMS_WRITE_MASK = (1 << 3) | (1 << 4)

assert COLD_RELINQUISH_FORMAT.size == 0xC0
assert sum(COLD_FLAG_VALUES) == COLD_REQUIRED_FLAGS
COLD_RELINQUISH_OFFSETS = dict(
    zip(
        COLD_RELINQUISH_FIELDS,
        (0x00, 0x04, 0x06, *range(0x08, 0xC0, 4)),
        strict=True,
    )
)
assert COLD_RELINQUISH_OFFSETS["flags"] == 0x08
assert COLD_RELINQUISH_OFFSETS["boot_mode"] == 0x0C
assert COLD_RELINQUISH_OFFSETS["framebuffer0"] == 0x54
assert COLD_RELINQUISH_OFFSETS["gdma_irq_source"] == 0x78
assert COLD_RELINQUISH_OFFSETS["panel_payload_crc32"] == 0xB4
assert COLD_RELINQUISH_OFFSETS["quiesce_sequence_id"] == 0xB8
assert COLD_RELINQUISH_OFFSETS["crc32"] == 0xBC


class ContractError(ValueError):
    """A modeled handoff was rejected before display ownership changed."""


def gdma_enable_semantics_valid(
    values: tuple[int, ...], expected: tuple[int, ...]
) -> bool:
    masks = (
        GDMA_STATUS0_DEFINED_MASK,
        GDMA_STATUS1_DEFINED_MASK,
        GDMA_STATUS0_DEFINED_MASK,
        GDMA_STATUS1_DEFINED_MASK,
        GDMA_COMMON_DEFINED_MASK,
        GDMA_COMMON_DEFINED_MASK,
    )
    return all(
        actual & mask == required
        for actual, mask, required in zip(values, masks, expected, strict=True)
    )


@dataclasses.dataclass
class Descriptor:
    sar: int = FRAME_ADDRESSES[0]
    sar_hi: int = 0
    dar: int = DSI_FIFO
    dar_hi: int = 0
    llp: int = 1
    llp_hi: int = 0
    block_size: int = DESCRIPTOR_BLOCK_SIZE
    control_low: int = DESCRIPTOR_CTRL_LO
    control: int = DESCRIPTOR_CTRL_HI


@dataclasses.dataclass(frozen=True)
class ColdHardwareState:
    """Raw-state facts Linux must revalidate before its first MMIO write."""

    silicon_revision: int = COLD_SILICON_REVISION
    gdma_linux_irq_data_available: bool = True
    gdma_linux_virq: int = 3
    gdma_linux_hwirq: int = COLD_GDMA_CLIC_IRQ
    gdma_reset_asserted: bool = True
    dsi_module_reset_asserted: bool = True
    gdma_cpu_clock_enabled: bool = False
    gdma_sys_clock_enabled: bool = False
    dsi_sys_clock_enabled: bool = False
    dphy_config_clock_enabled: bool = False
    dphy_pll_ref_clock_enabled: bool = False
    dpi_clock_enabled: bool = False
    i2c_apb_clock_enabled: bool = False
    dphy_ldo_enabled: bool = False
    gdma_irq_route: int = COLD_GDMA_CLIC_IRQ
    dma_pms_regions: tuple[tuple[int, int], ...] = (
        (COLD_FRAME_ADDRESSES[0], COLD_FRAME_ADDRESSES[0] + FRAME_SIZE),
        DISPLAY_POOL,
        (
            COLD_DESCRIPTOR_ADDRESS,
            COLD_DESCRIPTOR_ADDRESS + COLD_DESCRIPTOR_BACKING_SIZE,
        ),
        DSI_FIFO_WINDOW,
    )
    dma_pms_read_mask: int = COLD_DMA_PMS_READ_MASK
    dma_pms_write_mask: int = COLD_DMA_PMS_WRITE_MASK
    descriptor_backing: bytes = bytes(COLD_DESCRIPTOR_BACKING_SIZE)


def default_values() -> dict[str, int]:
    return {
        "magic": MAGIC,
        "abi_version": ABI_VERSION,
        "struct_size": HANDOFF_FORMAT.size,
        "flags": REQUIRED_FLAGS,
        "width": 800,
        "height": 1280,
        "stride": 1600,
        "pixel_clock_hz": 80_000_000,
        "lane_bit_rate_mbps": 1500,
        "data_lanes": 2,
        "buffer_count": 3,
        "ownership_state": DISPLAY_OWNER_LINUX_PENDING,
        "hsync_pulse_width": DISPLAY_TIMING[0],
        "hsync_back_porch": DISPLAY_TIMING[1],
        "hsync_front_porch": DISPLAY_TIMING[2],
        "vsync_pulse_width": DISPLAY_TIMING[3],
        "vsync_back_porch": DISPLAY_TIMING[4],
        "vsync_front_porch": DISPLAY_TIMING[5],
        "framebuffer0": FRAME_ADDRESSES[0],
        "framebuffer1": FRAME_ADDRESSES[1],
        "framebuffer2": FRAME_ADDRESSES[2],
        "framebuffer_size": FRAME_SIZE,
        "descriptor_address": 0x4FF3BA80,
        "descriptor_size": DESCRIPTOR_SIZE * DESCRIPTOR_COUNT,
        "descriptor_count": DESCRIPTOR_COUNT,
        "dma_channel": 0,
        "dsi_fifo_address": DSI_FIFO,
        "i2c_address": 0x45,
        "display_control_register": 0x95,
        "backlight_register": 0x96,
        "backlight_brightness": 63,
        "crc32": 0,
    }


def make_handoff(**overrides: int) -> bytes:
    values = default_values()
    values.update(overrides)
    values["crc32"] = 0
    unpacked = [values[field] for field in HANDOFF_FIELDS]
    provisional = HANDOFF_FORMAT.pack(*unpacked)
    values["crc32"] = zlib.crc32(provisional[:-4]) & 0xFFFFFFFF
    return HANDOFF_FORMAT.pack(*(values[field] for field in HANDOFF_FIELDS))


def cold_default_values() -> dict[str, int]:
    return {
        "magic": MAGIC,
        "abi_version": COLD_ABI_VERSION,
        "struct_size": COLD_RELINQUISH_FORMAT.size,
        "flags": COLD_REQUIRED_FLAGS,
        "boot_mode": COLD_BOOT_MODE,
        "ownership_state": COLD_OWNER_LINUX_PENDING,
        "silicon_revision": COLD_SILICON_REVISION,
        "panel_profile_id": COLD_PANEL_PROFILE_JD9365_WAVESHARE_10_1,
        "width": 800,
        "height": 1280,
        "stride": 1600,
        "pixel_format": COLD_PIXEL_FORMAT_RGB565_LE,
        "pixel_clock_hz": 60_000_000,
        "lane_bit_rate_mbps": 1000,
        "data_lanes": 2,
        "buffer_count": 3,
        "hsync_pulse_width": DISPLAY_TIMING[0],
        "hsync_back_porch": DISPLAY_TIMING[1],
        "hsync_front_porch": DISPLAY_TIMING[2],
        "vsync_pulse_width": DISPLAY_TIMING[3],
        "vsync_back_porch": DISPLAY_TIMING[4],
        "vsync_front_porch": DISPLAY_TIMING[5],
        "framebuffer0": COLD_FRAME_ADDRESSES[0],
        "framebuffer1": COLD_FRAME_ADDRESSES[1],
        "framebuffer2": COLD_FRAME_ADDRESSES[2],
        "framebuffer_size": FRAME_SIZE,
        "descriptor_address": COLD_DESCRIPTOR_ADDRESS,
        "descriptor_size": COLD_DESCRIPTOR_PUBLISHED_SIZE,
        "descriptor_count": DESCRIPTOR_COUNT,
        "dma_channel": 0,
        "dsi_fifo_address": DSI_FIFO,
        "gdma_irq_source": COLD_GDMA_IRQ_SOURCE,
        "gdma_clic_irq": COLD_GDMA_CLIC_IRQ,
        "i2c_port": COLD_I2C_PORT,
        "i2c_sda_gpio": COLD_I2C_SDA_GPIO,
        "i2c_scl_gpio": COLD_I2C_SCL_GPIO,
        "i2c_rate_hz": COLD_I2C_RATE_HZ,
        "i2c_address": 0x45,
        "display_control_register": 0x95,
        "backlight_register": 0x96,
        "display_control_reset_assert_command":
            COLD_DISPLAY_CONTROL_RESET_ASSERT_COMMAND,
        "display_control_reset_prepare_command":
            COLD_DISPLAY_CONTROL_RESET_PREPARE_COMMAND,
        "display_control_reveal_command":
            COLD_DISPLAY_CONTROL_REVEAL_COMMAND,
        "backlight_brightness": 63,
        "reset_hold_ms": COLD_RESET_HOLD_MS,
        "pwm_zero_settle_ms": COLD_PWM_ZERO_SETTLE_MS,
        "panel_payload_crc32": COLD_PANEL_PAYLOAD_CRC32,
        "quiesce_sequence_id": COLD_QUIESCE_SEQUENCE_ID,
        "crc32": 0,
    }


def make_cold_relinquish(**overrides: int) -> bytes:
    values = cold_default_values()
    values.update(overrides)
    values["crc32"] = 0
    provisional = COLD_RELINQUISH_FORMAT.pack(
        *(values[field] for field in COLD_RELINQUISH_FIELDS)
    )
    values["crc32"] = zlib.crc32(provisional[:-4]) & 0xFFFFFFFF
    return COLD_RELINQUISH_FORMAT.pack(
        *(values[field] for field in COLD_RELINQUISH_FIELDS)
    )


def reject(reason: str) -> None:
    raise ContractError(reason)


def validate_kernel_memory_span(memory_size: int) -> None:
    """Apply the M7/M9 Image-header memory ceiling used before any load/zero."""

    kernel_end = KERNEL_LOAD_ADDRESS + memory_size
    if memory_size <= 0 or kernel_end > KERNEL_GENERAL_RAM[1]:
        reject("kernel-memory-span")


def validate_cold_relinquish(
    blob: bytes,
    hardware: ColdHardwareState,
) -> None:
    """Validate M9's ABI-v3 contract before Linux mutates display state."""

    if len(blob) < 8:
        reject("structure-size")
    magic, abi_version, struct_size = struct.unpack_from("<IHH", blob)
    if magic != MAGIC:
        reject("magic")
    if abi_version != COLD_ABI_VERSION:
        reject("abi-version")
    if struct_size != COLD_RELINQUISH_FORMAT.size:
        reject("declared-size")
    if len(blob) != COLD_RELINQUISH_FORMAT.size:
        reject("structure-size")
    values = dict(
        zip(
            COLD_RELINQUISH_FIELDS,
            COLD_RELINQUISH_FORMAT.unpack(blob),
            strict=True,
        )
    )
    if zlib.crc32(blob[:-4]) & 0xFFFFFFFF != values["crc32"]:
        reject("crc32")

    flags = values["flags"]
    if flags & ~COLD_ALLOWED_FLAGS:
        reject("unknown-flags")
    if flags & COLD_REQUIRED_FLAGS != COLD_REQUIRED_FLAGS:
        reject("required-flags")

    if values["boot_mode"] != COLD_BOOT_MODE:
        reject("boot-mode")
    if values["ownership_state"] != COLD_OWNER_LINUX_PENDING:
        reject("ownership-state")
    profile = (
        values["silicon_revision"],
        values["panel_profile_id"],
        values["width"],
        values["height"],
        values["stride"],
        values["pixel_format"],
        values["pixel_clock_hz"],
        values["lane_bit_rate_mbps"],
        values["data_lanes"],
        values["buffer_count"],
        values["hsync_pulse_width"],
        values["hsync_back_porch"],
        values["hsync_front_porch"],
        values["vsync_pulse_width"],
        values["vsync_back_porch"],
        values["vsync_front_porch"],
    )
    if profile != (
        COLD_SILICON_REVISION,
        COLD_PANEL_PROFILE_JD9365_WAVESHARE_10_1,
        800,
        1280,
        1600,
        COLD_PIXEL_FORMAT_RGB565_LE,
        60_000_000,
        1000,
        2,
        3,
        *DISPLAY_TIMING,
    ):
        reject("panel-profile")
    if values["height"] > 0xFFFFFFFF // values["stride"]:
        reject("frame-size-overflow")
    if values["height"] * values["stride"] != FRAME_SIZE:
        reject("frame-size")
    if values["framebuffer_size"] != FRAME_SIZE:
        reject("published-frame-size")

    addresses = tuple(values[f"framebuffer{index}"] for index in range(3))
    front_end = addresses[0] + FRAME_SIZE
    if (
        front_end > 0xFFFFFFFF
        or addresses[0] < LOADER_PSRAM[0]
        or front_end > LOADER_PSRAM[1]
    ):
        reject("loader-front-bounds")
    if addresses[1:] != COLD_FRAME_ADDRESSES[1:]:
        reject("display-pool-layout")
    ranges: list[tuple[int, int]] = []
    for index, address in enumerate(addresses):
        end = address + FRAME_SIZE
        if end > 0xFFFFFFFF:
            reject("frame-address-overflow")
        if address % COLD_FRAMEBUFFER_ALIGNMENT:
            reject("framebuffer-alignment")
        if index and (address < DISPLAY_POOL[0] or end > DISPLAY_POOL[1]):
            reject("display-pool-bounds")
        if index and end > address + DISPLAY_SLOT_SIZE:
            reject("display-slot-bounds")
        ranges.append((address, end))
    for index, first in enumerate(ranges):
        for second in ranges[index + 1 :]:
            if first[0] < second[1] and second[0] < first[1]:
                reject("frame-overlap")

    descriptor_page_end = values["descriptor_address"] + COLD_DESCRIPTOR_BACKING_SIZE
    if descriptor_page_end > 0xFFFFFFFF:
        reject("descriptor-overflow")
    if (
        values["descriptor_address"] < DESCRIPTOR_SRAM[0]
        or descriptor_page_end > DESCRIPTOR_SRAM[1]
        or values["descriptor_address"] % COLD_PAGE_SIZE
        or values["descriptor_size"] != COLD_DESCRIPTOR_PUBLISHED_SIZE
        or values["descriptor_count"] != DESCRIPTOR_COUNT
    ):
        reject("descriptor-bounds")
    if (
        values["descriptor_address"] < SD_DMA_SRAM[1]
        and descriptor_page_end > SD_DMA_SRAM[0]
    ):
        reject("descriptor-sd-dma-overlap")
    if values["dma_channel"] != 0:
        reject("dma-channel")
    if not hardware.gdma_linux_irq_data_available:
        reject("gdma-linux-irq-data")
    if hardware.gdma_linux_hwirq != values["gdma_clic_irq"]:
        reject("gdma-linux-hwirq")
    if (
        values["dsi_fifo_address"] != DSI_FIFO
        or values["gdma_irq_source"] != COLD_GDMA_IRQ_SOURCE
        or values["gdma_clic_irq"] != COLD_GDMA_CLIC_IRQ
        or values["i2c_port"] != COLD_I2C_PORT
        or values["i2c_sda_gpio"] != COLD_I2C_SDA_GPIO
        or values["i2c_scl_gpio"] != COLD_I2C_SCL_GPIO
        or values["i2c_rate_hz"] != COLD_I2C_RATE_HZ
        or values["i2c_address"] != 0x45
        or values["display_control_register"] != 0x95
        or values["backlight_register"] != 0x96
        or values["display_control_reset_assert_command"]
        != COLD_DISPLAY_CONTROL_RESET_ASSERT_COMMAND
        or values["display_control_reset_prepare_command"]
        != COLD_DISPLAY_CONTROL_RESET_PREPARE_COMMAND
        or values["display_control_reveal_command"]
        != COLD_DISPLAY_CONTROL_REVEAL_COMMAND
        or values["backlight_brightness"] != 63
        or values["reset_hold_ms"] != COLD_RESET_HOLD_MS
        or values["pwm_zero_settle_ms"] != COLD_PWM_ZERO_SETTLE_MS
        or values["panel_payload_crc32"] != COLD_PANEL_PAYLOAD_CRC32
        or values["quiesce_sequence_id"] != COLD_QUIESCE_SEQUENCE_ID
    ):
        reject("peripheral-contract")

    if hardware.silicon_revision != values["silicon_revision"]:
        reject("silicon-readback")
    if not hardware.gdma_reset_asserted or not hardware.dsi_module_reset_asserted:
        reject("display-reset-clock-state")
    if (
        hardware.gdma_cpu_clock_enabled
        or hardware.gdma_sys_clock_enabled
        or hardware.dsi_sys_clock_enabled
        or hardware.dphy_config_clock_enabled
        or hardware.dphy_pll_ref_clock_enabled
        or hardware.dpi_clock_enabled
    ):
        reject("display-reset-clock-state")
    if hardware.dphy_ldo_enabled:
        reject("dphy-ldo-enabled")
    if hardware.i2c_apb_clock_enabled:
        reject("i2c-not-released")
    if hardware.gdma_irq_route != values["gdma_clic_irq"]:
        reject("gdma-irq-route")
    expected_pms_regions = (
        (addresses[0], front_end),
        DISPLAY_POOL,
        (values["descriptor_address"], descriptor_page_end),
        DSI_FIFO_WINDOW,
    )
    if (
        hardware.dma_pms_regions != expected_pms_regions
        or hardware.dma_pms_read_mask != COLD_DMA_PMS_READ_MASK
        or hardware.dma_pms_write_mask != COLD_DMA_PMS_WRITE_MASK
    ):
        reject("dma-pms-policy")
    if (
        len(hardware.descriptor_backing) != COLD_DESCRIPTOR_BACKING_SIZE
        or any(hardware.descriptor_backing)
    ):
        reject("descriptor-storage-not-zero")


def validate_handoff(
    blob: bytes,
    descriptors: list[Descriptor],
    channel_cfg: tuple[int, int] = CHANNEL_CFG,
) -> None:
    if len(blob) != HANDOFF_FORMAT.size:
        reject("structure-size")
    values = dict(zip(HANDOFF_FIELDS, HANDOFF_FORMAT.unpack(blob), strict=True))
    if values["magic"] != MAGIC:
        reject("magic")
    if values["abi_version"] != ABI_VERSION:
        reject("abi-version")
    if values["struct_size"] != HANDOFF_FORMAT.size:
        reject("declared-size")
    if zlib.crc32(blob[:-4]) & 0xFFFFFFFF != values["crc32"]:
        reject("crc32")
    if values["flags"] & REQUIRED_FLAGS != REQUIRED_FLAGS:
        reject("required-flags")
    profile = (
        values["width"],
        values["height"],
        values["stride"],
        values["pixel_clock_hz"],
        values["lane_bit_rate_mbps"],
        values["data_lanes"],
        values["buffer_count"],
        values["ownership_state"],
        values["hsync_pulse_width"],
        values["hsync_back_porch"],
        values["hsync_front_porch"],
        values["vsync_pulse_width"],
        values["vsync_back_porch"],
        values["vsync_front_porch"],
    )
    if profile != (
        800, 1280, 1600, 80_000_000, 1500, 2, 3,
        DISPLAY_OWNER_LINUX_PENDING, *DISPLAY_TIMING,
    ):
        reject("panel-profile")
    if values["height"] > 0xFFFFFFFF // values["stride"]:
        reject("frame-size-overflow")
    if values["height"] * values["stride"] != FRAME_SIZE:
        reject("frame-size")
    if values["framebuffer_size"] != FRAME_SIZE:
        reject("published-frame-size")

    addresses = tuple(values[f"framebuffer{index}"] for index in range(3))
    front_end = addresses[0] + FRAME_SIZE
    if addresses[0] < LOADER_PSRAM[0] or front_end > LOADER_PSRAM[1]:
        reject("loader-front-bounds")
    if addresses[1:] != FRAME_ADDRESSES[1:]:
        reject("display-pool-layout")
    ranges: list[tuple[int, int]] = []
    for index, address in enumerate(addresses):
        end = address + FRAME_SIZE
        if end > 0xFFFFFFFF:
            reject("frame-address-overflow")
        if address % FRAMEBUFFER_ALIGNMENT:
            reject("framebuffer-alignment")
        if index and (address < DISPLAY_POOL[0] or end > DISPLAY_POOL[1]):
            reject("display-pool-bounds")
        if index and end > address + DISPLAY_SLOT_SIZE:
            reject("display-slot-bounds")
        ranges.append((address, end))
    for index, first in enumerate(ranges):
        for second in ranges[index + 1 :]:
            if first[0] < second[1] and second[0] < first[1]:
                reject("frame-overlap")

    descriptor_end = values["descriptor_address"] + values["descriptor_size"]
    if descriptor_end > 0xFFFFFFFF:
        reject("descriptor-overflow")
    if (
        values["descriptor_address"] < DESCRIPTOR_SRAM[0]
        or descriptor_end > DESCRIPTOR_SRAM[1]
        or values["descriptor_address"] % DESCRIPTOR_SIZE
        or values["descriptor_size"] != DESCRIPTOR_SIZE * DESCRIPTOR_COUNT
        or values["descriptor_count"] != DESCRIPTOR_COUNT
    ):
        reject("descriptor-bounds")
    if len(descriptors) != DESCRIPTOR_COUNT:
        reject("descriptor-count")
    for descriptor in descriptors:
        if descriptor.sar != addresses[0] or descriptor.sar_hi:
            reject("descriptor-source")
        if descriptor.dar != DSI_FIFO or descriptor.dar_hi:
            reject("descriptor-destination")
        if descriptor.llp & ~0x3F or descriptor.llp_hi:
            reject("descriptor-link")
        if descriptor.block_size != DESCRIPTOR_BLOCK_SIZE:
            reject("descriptor-block-size")
        if descriptor.control_low != DESCRIPTOR_CTRL_LO:
            reject("descriptor-control-low")
        if descriptor.control != DESCRIPTOR_CTRL_HI:
            reject("descriptor-control-high")
    if channel_cfg != CHANNEL_CFG:
        reject("dma-channel-config")
    if values["dma_channel"] >= 4:
        reject("dma-channel")
    if (
        values["dsi_fifo_address"] != DSI_FIFO
        or values["i2c_address"] != 0x45
        or values["display_control_register"] != 0x95
        or values["backlight_register"] != 0x96
        or values["backlight_brightness"] > 255
    ):
        reject("peripheral-contract")


@dataclasses.dataclass
class OwnershipModel:
    front: int = 0
    render: int = 1
    queued: int | None = None
    active: bool = True
    requests: int = 0
    completions: int = 0
    rearm_attempts: int = 0
    rearm_failures: int = 0
    generation: int = 0
    faulted: bool = False

    def assert_valid(self) -> None:
        roles = (self.front, self.render)
        if any(role not in range(3) for role in roles):
            raise AssertionError("role-out-of-range")
        if self.active and self.front == self.render:
            raise AssertionError("front-render-alias")
        if self.queued is not None:
            if self.queued not in range(3) or self.queued == self.front:
                raise AssertionError("queued-front-alias")
            if self.active and self.queued == self.render:
                raise AssertionError("queued-render-alias")

    def queue_render(self) -> None:
        if self.faulted:
            return
        rendered = self.render
        if not self.active:
            if self.queued != rendered:
                self.requests += 1
            self.queued = rendered
            return
        self.queued = rendered
        self.requests += 1
        self.render = next(
            index for index in range(3)
            if index != self.front and index != self.queued
        )
        self.assert_valid()

    def start(self) -> None:
        if self.active:
            raise AssertionError("already-active")
        self.front = self.queued if self.queued is not None else self.front
        self.queued = None
        if self.render == self.front:
            self.render = next(index for index in range(3) if index != self.front)
        self.active = True
        self.generation += 1
        self.assert_valid()

    def complete(self, rearm_success: bool) -> None:
        before = (self.front, self.render, self.queued)
        self.rearm_attempts += 1
        if not rearm_success:
            self.rearm_failures += 1
            if (self.front, self.render, self.queued) != before:
                raise AssertionError("failed-rearm-mutated-roles")
            self.assert_valid()
            return
        if self.faulted:
            self.generation += 1
            if (self.front, self.render, self.queued) != before:
                raise AssertionError("faulted-rearm-mutated-roles")
            self.assert_valid()
            return
        if self.queued is not None:
            self.front = self.queued
            self.queued = None
            self.completions += 1
        self.generation += 1
        self.assert_valid()


def expect_reject(
    name: str,
    blob: bytes,
    descriptors: list[Descriptor],
    expected_reason: str,
    channel_cfg: tuple[int, int] = CHANNEL_CFG,
) -> None:
    try:
        validate_handoff(blob, descriptors, channel_cfg)
    except ContractError as error:
        if str(error) != expected_reason:
            raise AssertionError(f"{name}: {error} != {expected_reason}") from error
        return
    raise AssertionError(f"{name}: contract unexpectedly accepted")


def expect_cold_reject(
    name: str,
    blob: bytes,
    hardware: ColdHardwareState,
    expected_reason: str,
) -> None:
    try:
        validate_cold_relinquish(blob, hardware)
    except ContractError as error:
        if str(error) != expected_reason:
            raise AssertionError(f"{name}: {error} != {expected_reason}") from error
        return
    raise AssertionError(f"{name}: cold contract unexpectedly accepted")


def test_cold_contract() -> int:
    hardware = ColdHardwareState()
    validate_cold_relinquish(make_cold_relinquish(), hardware)
    tests = 1

    # Linux may allocate virq 3 for raw CLIC hwirq 18. Only the latter is part
    # of the ABI-v3 contract and the interrupt-matrix route.
    mapped_hardware = dataclasses.replace(
        hardware,
        gdma_linux_virq=3,
        gdma_linux_hwirq=COLD_GDMA_CLIC_IRQ,
    )
    if mapped_hardware.gdma_linux_virq == mapped_hardware.gdma_linux_hwirq:
        raise AssertionError("cold-linux-irq-model-not-distinct")
    validate_cold_relinquish(make_cold_relinquish(), mapped_hardware)
    tests += 1

    blob_cases = (
        (
            "truncated",
            make_cold_relinquish()[:-1],
            "structure-size",
        ),
        (
            "wrong-magic",
            make_cold_relinquish(magic=0),
            "magic",
        ),
        (
            "legacy-abi2",
            make_handoff(),
            "abi-version",
        ),
        (
            "unknown-version",
            make_cold_relinquish(abi_version=4),
            "abi-version",
        ),
        (
            "declared-size",
            make_cold_relinquish(struct_size=COLD_RELINQUISH_FORMAT.size + 4),
            "declared-size",
        ),
        (
            "wrong-boot-mode",
            make_cold_relinquish(boot_mode=0),
            "boot-mode",
        ),
        (
            "legacy-abi2-flags",
            make_cold_relinquish(flags=REQUIRED_FLAGS),
            "required-flags",
        ),
        (
            "unknown-flag",
            make_cold_relinquish(flags=COLD_REQUIRED_FLAGS | (1 << 14)),
            "unknown-flags",
        ),
        (
            "wrong-owner",
            make_cold_relinquish(ownership_state=0),
            "ownership-state",
        ),
        (
            "wrong-profile",
            make_cold_relinquish(pixel_clock_hz=69_907_200),
            "panel-profile",
        ),
        (
            "wrong-silicon",
            make_cold_relinquish(silicon_revision=102),
            "panel-profile",
        ),
        (
            "wrong-panel-profile-id",
            make_cold_relinquish(panel_profile_id=2),
            "panel-profile",
        ),
        (
            "wrong-pixel-format",
            make_cold_relinquish(pixel_format=2),
            "panel-profile",
        ),
        (
            "wrong-timing",
            make_cold_relinquish(hsync_front_porch=41),
            "panel-profile",
        ),
        (
            "front-out-of-range",
            make_cold_relinquish(framebuffer0=0x47F00000),
            "loader-front-bounds",
        ),
        (
            "fixed-pool-layout",
            make_cold_relinquish(framebuffer1=0x49200000),
            "display-pool-layout",
        ),
        (
            "misaligned-framebuffer",
            make_cold_relinquish(
                framebuffer0=COLD_FRAME_ADDRESSES[0] + FRAMEBUFFER_ALIGNMENT
            ),
            "framebuffer-alignment",
        ),
        (
            "wrong-frame-size",
            make_cold_relinquish(framebuffer_size=FRAME_SIZE - 2),
            "published-frame-size",
        ),
        (
            "descriptor-overflow",
            make_cold_relinquish(descriptor_address=0xFFFFFFC0),
            "descriptor-overflow",
        ),
        (
            "descriptor-misaligned",
            make_cold_relinquish(descriptor_address=0x4FF3BA60),
            "descriptor-bounds",
        ),
        (
            "descriptor-beyond-dma-sram",
            make_cold_relinquish(descriptor_address=DESCRIPTOR_SRAM[1]),
            "descriptor-bounds",
        ),
        (
            "descriptor-count",
            make_cold_relinquish(descriptor_count=2),
            "descriptor-bounds",
        ),
        (
            "descriptor-overlaps-sd-dma",
            make_cold_relinquish(descriptor_address=SD_DMA_SRAM[0]),
            "descriptor-sd-dma-overlap",
        ),
        (
            "descriptor-size",
            make_cold_relinquish(descriptor_size=DESCRIPTOR_SIZE * 2),
            "descriptor-bounds",
        ),
        (
            "wrong-dma-channel",
            make_cold_relinquish(dma_channel=1),
            "dma-channel",
        ),
        (
            "wrong-fifo",
            make_cold_relinquish(dsi_fifo_address=DSI_FIFO + 4),
            "peripheral-contract",
        ),
        (
            "wrong-i2c-address",
            make_cold_relinquish(i2c_address=0x46),
            "peripheral-contract",
        ),
        (
            "wrong-irq-source",
            make_cold_relinquish(gdma_irq_source=23),
            "peripheral-contract",
        ),
        (
            "wrong-clic-irq",
            make_cold_relinquish(gdma_clic_irq=17),
            "gdma-linux-hwirq",
        ),
        (
            "wrong-i2c-port",
            make_cold_relinquish(i2c_port=1),
            "peripheral-contract",
        ),
        (
            "wrong-i2c-sda",
            make_cold_relinquish(i2c_sda_gpio=6),
            "peripheral-contract",
        ),
        (
            "wrong-i2c-scl",
            make_cold_relinquish(i2c_scl_gpio=9),
            "peripheral-contract",
        ),
        (
            "wrong-i2c-rate",
            make_cold_relinquish(i2c_rate_hz=400_000),
            "peripheral-contract",
        ),
        (
            "wrong-reset-assert-command",
            make_cold_relinquish(display_control_reset_assert_command=0x10),
            "peripheral-contract",
        ),
        (
            "wrong-reset-prepare-command",
            make_cold_relinquish(display_control_reset_prepare_command=0x12),
            "peripheral-contract",
        ),
        (
            "wrong-reveal-command",
            make_cold_relinquish(display_control_reveal_command=0x16),
            "peripheral-contract",
        ),
        (
            "wrong-reset-hold",
            make_cold_relinquish(reset_hold_ms=11),
            "peripheral-contract",
        ),
        (
            "wrong-settle-time",
            make_cold_relinquish(pwm_zero_settle_ms=99),
            "peripheral-contract",
        ),
        (
            "wrong-panel-payload-crc",
            make_cold_relinquish(panel_payload_crc32=0),
            "peripheral-contract",
        ),
        (
            "wrong-sequence-id",
            make_cold_relinquish(quiesce_sequence_id=2),
            "peripheral-contract",
        ),
        (
            "wrong-default-brightness",
            make_cold_relinquish(backlight_brightness=64),
            "peripheral-contract",
        ),
    )
    for name, blob, expected_reason in blob_cases:
        expect_cold_reject(name, blob, hardware, expected_reason)
        tests += 1

    for index, flag in enumerate(COLD_FLAG_VALUES):
        expect_cold_reject(
            f"missing-required-flag-{index}",
            make_cold_relinquish(flags=COLD_REQUIRED_FLAGS & ~flag),
            hardware,
            "required-flags",
        )
        tests += 1

    corrupted = bytearray(make_cold_relinquish())
    corrupted[-1] ^= 0x80
    expect_cold_reject("bad-crc", bytes(corrupted), hardware, "crc32")
    tests += 1

    provisional_flags = COLD_REQUIRED_FLAGS & ~(
        COLD_FLAG_DMA_PMS_READY | COLD_FLAG_GDMA_IRQ_ROUTE_READY
    )
    for name, flag in (
        ("dma-pms-not-final", COLD_FLAG_DMA_PMS_READY),
        ("irq-route-not-final", COLD_FLAG_GDMA_IRQ_ROUTE_READY),
    ):
        expect_cold_reject(
            name,
            make_cold_relinquish(flags=provisional_flags | flag),
            hardware,
            "required-flags",
        )
        tests += 1

    hardware_cases = (
        (
            "missing-linux-irq-data",
            dataclasses.replace(hardware, gdma_linux_irq_data_available=False),
            "gdma-linux-irq-data",
        ),
        (
            "wrong-linux-hwirq",
            dataclasses.replace(hardware, gdma_linux_hwirq=17),
            "gdma-linux-hwirq",
        ),
        (
            "silicon-readback",
            dataclasses.replace(hardware, silicon_revision=102),
            "silicon-readback",
        ),
        (
            "gdma-reset-released",
            dataclasses.replace(hardware, gdma_reset_asserted=False),
            "display-reset-clock-state",
        ),
        (
            "dsi-module-reset-released",
            dataclasses.replace(hardware, dsi_module_reset_asserted=False),
            "display-reset-clock-state",
        ),
        (
            "gdma-cpu-clock-live",
            dataclasses.replace(hardware, gdma_cpu_clock_enabled=True),
            "display-reset-clock-state",
        ),
        (
            "gdma-system-clock-live",
            dataclasses.replace(hardware, gdma_sys_clock_enabled=True),
            "display-reset-clock-state",
        ),
        (
            "dsi-system-clock-live",
            dataclasses.replace(hardware, dsi_sys_clock_enabled=True),
            "display-reset-clock-state",
        ),
        (
            "dphy-config-clock-live",
            dataclasses.replace(hardware, dphy_config_clock_enabled=True),
            "display-reset-clock-state",
        ),
        (
            "dphy-pll-clock-live",
            dataclasses.replace(hardware, dphy_pll_ref_clock_enabled=True),
            "display-reset-clock-state",
        ),
        (
            "dpi-clock-live",
            dataclasses.replace(hardware, dpi_clock_enabled=True),
            "display-reset-clock-state",
        ),
        (
            "ldo-live",
            dataclasses.replace(hardware, dphy_ldo_enabled=True),
            "dphy-ldo-enabled",
        ),
        (
            "i2c-retained",
            dataclasses.replace(hardware, i2c_apb_clock_enabled=True),
            "i2c-not-released",
        ),
        (
            "wrong-irq-route-readback",
            dataclasses.replace(hardware, gdma_irq_route=17),
            "gdma-irq-route",
        ),
        (
            "wrong-pms-window",
            dataclasses.replace(
                hardware,
                dma_pms_regions=hardware.dma_pms_regions[:-1]
                + ((DSI_FIFO, DSI_FIFO + 2 * COLD_PAGE_SIZE),),
            ),
            "dma-pms-policy",
        ),
        (
            "wrong-pms-read-mask",
            dataclasses.replace(hardware, dma_pms_read_mask=0),
            "dma-pms-policy",
        ),
        (
            "wrong-pms-write-mask",
            dataclasses.replace(hardware, dma_pms_write_mask=0),
            "dma-pms-policy",
        ),
        (
            "inherited-descriptor",
            dataclasses.replace(
                hardware,
                descriptor_backing=b"\x01"
                + bytes(COLD_DESCRIPTOR_BACKING_SIZE - 1),
            ),
            "descriptor-storage-not-zero",
        ),
        (
            "truncated-descriptor-storage",
            dataclasses.replace(
                hardware,
                descriptor_backing=bytes(COLD_DESCRIPTOR_BACKING_SIZE - 1),
            ),
            "descriptor-storage-not-zero",
        ),
    )
    for name, mutated_hardware, expected_reason in hardware_cases:
        expect_cold_reject(
            name,
            make_cold_relinquish(),
            mutated_hardware,
            expected_reason,
        )
        tests += 1
    return tests


def test_contract() -> int:
    descriptors = [Descriptor() for _ in range(3)]
    validate_handoff(make_handoff(), descriptors)
    tests = 1
    cases = (
        ("truncated", make_handoff()[:-1], descriptors, "structure-size"),
        ("wrong-version", make_handoff(abi_version=3), descriptors, "abi-version"),
        ("missing-triple-buffer-flag", make_handoff(flags=REQUIRED_FLAGS & ~(1 << 5)), descriptors, "required-flags"),
        ("missing-host-vpg-flag", make_handoff(flags=REQUIRED_FLAGS & ~(1 << 7)), descriptors, "required-flags"),
        ("wrong-profile", make_handoff(pixel_clock_hz=69_907_200), descriptors, "panel-profile"),
        ("wrong-timing", make_handoff(hsync_front_porch=41), descriptors, "panel-profile"),
        ("wrong-owner", make_handoff(ownership_state=0), descriptors, "panel-profile"),
        ("height-overflow", make_handoff(height=0xFFFFFFFF), descriptors, "panel-profile"),
        ("address-overflow", make_handoff(descriptor_address=0xFFFFFFC0), descriptors, "descriptor-overflow"),
        ("out-of-range", make_handoff(framebuffer1=0x49200000), descriptors, "display-pool-layout"),
        ("misaligned-framebuffer", make_handoff(framebuffer0=0x48040A82), descriptors, "framebuffer-alignment"),
        ("misaligned", make_handoff(descriptor_address=0x4FF3BA60), descriptors, "descriptor-bounds"),
        (
            "descriptor-beyond-dma-sram",
            make_handoff(descriptor_address=DESCRIPTOR_SRAM[1]),
            descriptors,
            "descriptor-bounds",
        ),
    )
    for case in cases:
        expect_reject(*case)
        tests += 1
    corrupted = bytearray(make_handoff())
    corrupted[-1] ^= 0x80
    expect_reject("bad-crc", bytes(corrupted), descriptors, "crc32")
    tests += 1
    high_source = [Descriptor() for _ in range(3)]
    high_source[1].sar_hi = 1
    expect_reject("high-source", make_handoff(), high_source, "descriptor-source")
    tests += 1
    high_link = [Descriptor() for _ in range(3)]
    high_link[2].llp_hi = 1
    expect_reject("high-link", make_handoff(), high_link, "descriptor-link")
    tests += 1
    bad_block = [Descriptor() for _ in range(3)]
    bad_block[0].block_size -= 1
    expect_reject("bad-block", make_handoff(), bad_block, "descriptor-block-size")
    tests += 1
    expect_reject(
        "bad-channel-config", make_handoff(), descriptors,
        "dma-channel-config", (CHANNEL_CFG[0], 0),
    )
    tests += 1
    expect_reject(
        "reload-mode-channel-config", make_handoff(), descriptors,
        "dma-channel-config", (0x00000005, CHANNEL_CFG[1]),
    )
    return tests + 1


def test_gdma_enable_semantics() -> int:
    masks = (
        GDMA_STATUS0_DEFINED_MASK,
        GDMA_STATUS1_DEFINED_MASK,
        GDMA_STATUS0_DEFINED_MASK,
        GDMA_STATUS1_DEFINED_MASK,
        GDMA_COMMON_DEFINED_MASK,
        GDMA_COMMON_DEFINED_MASK,
    )
    if masks != (
        0xFA3F7FFB,
        0x0000000F,
        0xFA3F7FFB,
        0x0000000F,
        0x001FFF8F,
        0x001FFF8F,
    ):
        raise AssertionError("gdma-defined-masks")
    tests = 1

    if GDMA_ACTIVE_ENABLE_VALUES != (
        0x023F7FE2,
        0x0000000F,
        0x023F7FE2,
        0x0000000F,
        0x001FFF8F,
        0x001FFF8F,
    ):
        raise AssertionError("gdma-active-enable-values")
    tests += 1
    if GDMA_TEARDOWN_ENABLE_VALUES != (
        0x02000000,
        0x0000000F,
        0x02000000,
        0x0000000F,
        0x001FFE80,
        0x001FFE80,
    ):
        raise AssertionError("gdma-teardown-enable-values")
    tests += 1
    if GDMA_PATCH36_HARDWARE_VALUES != (
        0x07FFFFE6,
        0xFFFFFFFF,
        0x07FFFFE6,
        0xFFFFFFFF,
        0xFFFFFFFF,
        0xFFFFFFFF,
    ):
        raise AssertionError("gdma-patch36-hardware-values")
    tests += 1

    patch36_delta = tuple(
        actual ^ expected
        for actual, expected in zip(
            GDMA_PATCH36_HARDWARE_VALUES,
            GDMA_ACTIVE_ENABLE_VALUES,
            strict=True,
        )
    )
    if patch36_delta != (
        0x05C08004,
        0xFFFFFFF0,
        0x05C08004,
        0xFFFFFFF0,
        0xFFE00070,
        0xFFE00070,
    ):
        raise AssertionError("gdma-patch36-hardware-delta")
    tests += 1
    if any(delta & mask for delta, mask in zip(patch36_delta, masks, strict=True)):
        raise AssertionError("gdma-patch36-defined-bit-delta")
    tests += 1
    if GDMA_PATCH36_HARDWARE_VALUES == GDMA_ACTIVE_ENABLE_VALUES:
        raise AssertionError("gdma-patch36-exact-equality-would-pass")
    tests += 1
    if not gdma_enable_semantics_valid(
        GDMA_PATCH36_HARDWARE_VALUES, GDMA_ACTIVE_ENABLE_VALUES
    ):
        raise AssertionError("gdma-patch36-semantic-readback")
    tests += 1
    if not gdma_enable_semantics_valid(
        GDMA_ACTIVE_ENABLE_VALUES, GDMA_ACTIVE_ENABLE_VALUES
    ):
        raise AssertionError("gdma-active-clean-readback")
    tests += 1

    teardown_raw = tuple(
        expected | (~mask & 0xFFFFFFFF)
        for expected, mask in zip(
            GDMA_TEARDOWN_ENABLE_VALUES, masks, strict=True
        )
    )
    if teardown_raw != (
        0x07C08004,
        0xFFFFFFFF,
        0x07C08004,
        0xFFFFFFFF,
        0xFFFFFEF0,
        0xFFFFFEF0,
    ):
        raise AssertionError("gdma-teardown-reserved-one-readback")
    tests += 1
    if not gdma_enable_semantics_valid(
        teardown_raw, GDMA_TEARDOWN_ENABLE_VALUES
    ):
        raise AssertionError("gdma-teardown-semantic-readback")
    tests += 1

    for index, bit in enumerate((1, 0, 1, 0, 0, 0)):
        mutated = list(GDMA_PATCH36_HARDWARE_VALUES)
        mutated[index] ^= 1 << bit
        if gdma_enable_semantics_valid(tuple(mutated), GDMA_ACTIVE_ENABLE_VALUES):
            raise AssertionError(f"gdma-active-defined-bit-{index}")
        tests += 1
    active_defined_zero = list(GDMA_PATCH36_HARDWARE_VALUES)
    active_defined_zero[0] |= 1
    if gdma_enable_semantics_valid(
        tuple(active_defined_zero), GDMA_ACTIVE_ENABLE_VALUES
    ):
        raise AssertionError("gdma-active-defined-zero")
    tests += 1

    for index, bit in ((0, 1), (2, 1), (4, 0), (5, 0)):
        mutated = list(teardown_raw)
        mutated[index] |= 1 << bit
        if gdma_enable_semantics_valid(
            tuple(mutated), GDMA_TEARDOWN_ENABLE_VALUES
        ):
            raise AssertionError(f"gdma-teardown-writable-bit-{index}")
        tests += 1

    # Patch 38 hardware evidence captured after channel enable.  The engine
    # fetched the terminal one-shot descriptor, advanced SAR by 0x500, and
    # exposed the terminal link's memory-port bit instead of the programmed
    # head LLP.  Those hardware-owned values must not invalidate the stable
    # post-enable channel/error check introduced by Patch 39.
    patch38_arm_snapshot = {
        "top": 0x00000000,
        "status0": 0x00000000,
        "status1": 0x00000000,
        "common": 0x00000000,
        "cfg": 0x00000003,
        "chen": 0x00000001,
        "cfg_lo": 0x0000000F,
        "cfg_hi": 0x0A020001,
        "llp": 0x00000001,
        "sar": 0x48031500,
        "ctrl_hi": 0xC0108840,
    }
    if patch38_arm_snapshot["llp"] != 0x00000001:
        raise AssertionError("patch38-arm-fixture-terminal-llp")
    tests += 1
    if patch38_arm_snapshot["sar"] - 0x500 != 0x48031000:
        raise AssertionError("patch38-arm-fixture-sar-progress")
    tests += 1
    if tuple(
        patch38_arm_snapshot[field]
        for field in ("cfg", "chen", "cfg_lo", "cfg_hi", "ctrl_hi")
    ) != (0x3, 0x1, 0x0000000F, 0x0A020001, 0xC0108840):
        raise AssertionError("patch38-arm-fixture-programming")
    tests += 1
    if patch38_arm_snapshot["llp"] == (
        COLD_DESCRIPTOR_ADDRESS | 0x1
    ):
        raise AssertionError("patch38-arm-fixture-old-llp-check-would-pass")
    tests += 1
    if not (
        patch38_arm_snapshot["chen"] & 0x1
        and not patch38_arm_snapshot["status0"]
        and not patch38_arm_snapshot["status1"]
        and not patch38_arm_snapshot["common"]
        and not patch38_arm_snapshot["top"]
    ):
        raise AssertionError("patch39-arm-fixture-stable-post-enable")
    tests += 1
    return tests


def test_native_active_mirror_semantics() -> int:
    if NATIVE_PROGRAMMED_VIDEO_POLICY != 0x0000FF02:
        raise AssertionError("native-programmed-video-policy")
    tests = 1
    if LEGACY_ACTIVE_MIRROR_VIDEO_POLICY != 0x000003FE:
        raise AssertionError("legacy-active-mirror-video-policy")
    tests += 1

    # Patch 39 hardware completed four clean frame transfers before the
    # qualification window rejected only the inactive optional shadow mirror.
    patch39_qualification_snapshot = {
        "generation": 4,
        "frames": 4,
        "faults": 0,
        "dma_error": 0x00000000,
        "rearm_attempts": 5,
        "rearm_failures": 0,
        "guards_valid": 1,
        "host_raw0": 0x00000000,
        "host_raw1": 0x00000000,
        "host_sticky0": 0x00000000,
        "host_sticky1": 0x00000000,
        "host_mode": 0x00000000,
        "host_vid": 0x0000FF02,
        "host_active": 0x00000000,
        "host_lpclk": 0x00000003,
        "bridge_raw": 0x00000000,
        "bridge_underruns": 0,
        "gdma_top": 0x00000000,
        "gdma_status0": 0x00000000,
        "gdma_status1": 0x00000000,
        "gdma_common": 0x00000000,
        "gdma_cfg": 0x00000003,
        "gdma_chen": 0x00000001,
        "gdma_cfg_lo": 0x0000000F,
        "gdma_cfg_hi": 0x0A020001,
        "gdma_llp": 0x00000001,
        "gdma_sar": 0x480C4A00,
        "descriptor_ctrl_hi": 0xC0108840,
    }
    if tuple(patch39_qualification_snapshot.values()) != (
        4, 4, 0, 0x00000000, 5, 0, 1,
        0x00000000, 0x00000000, 0x00000000, 0x00000000,
        0x00000000, 0x0000FF02, 0x00000000, 0x00000003,
        0x00000000, 0, 0x00000000, 0x00000000, 0x00000000,
        0x00000000, 0x00000003, 0x00000001, 0x0000000F,
        0x0A020001, 0x00000001, 0x480C4A00, 0xC0108840,
    ):
        raise AssertionError("patch39-active-zero-hardware-fixture")
    tests += 1
    if not (
        patch39_qualification_snapshot["generation"] == 4
        and patch39_qualification_snapshot["frames"] == 4
        and patch39_qualification_snapshot["rearm_attempts"] == 5
        and not patch39_qualification_snapshot["rearm_failures"]
        and patch39_qualification_snapshot["guards_valid"]
        and not patch39_qualification_snapshot["faults"]
        and not patch39_qualification_snapshot["dma_error"]
        and not patch39_qualification_snapshot["host_raw0"]
        and not patch39_qualification_snapshot["host_raw1"]
        and not patch39_qualification_snapshot["host_sticky0"]
        and not patch39_qualification_snapshot["host_sticky1"]
        and not patch39_qualification_snapshot["bridge_raw"]
        and not patch39_qualification_snapshot["bridge_underruns"]
        and not patch39_qualification_snapshot["gdma_top"]
        and not patch39_qualification_snapshot["gdma_status0"]
        and not patch39_qualification_snapshot["gdma_status1"]
        and not patch39_qualification_snapshot["gdma_common"]
    ):
        raise AssertionError("patch39-active-zero-health-evidence")
    tests += 1

    def video_policy_valid(require_active_mirror: bool, host_vid: int) -> bool:
        return host_vid == NATIVE_PROGRAMMED_VIDEO_POLICY and (
            not require_active_mirror
            or patch39_qualification_snapshot["host_active"]
            == LEGACY_ACTIVE_MIRROR_VIDEO_POLICY
        )

    if video_policy_valid(True, patch39_qualification_snapshot["host_vid"]):
        raise AssertionError("patch39-active-zero-old-policy-would-pass")
    tests += 1
    if not video_policy_valid(False, patch39_qualification_snapshot["host_vid"]):
        raise AssertionError("patch40-active-zero-programmed-policy")
    tests += 1
    if video_policy_valid(False, NATIVE_PROGRAMMED_VIDEO_POLICY ^ 1):
        raise AssertionError("patch40-programmed-policy-weakened")
    return tests + 1


def test_memory_budget() -> int:
    mib = 1024 * 1024
    layout = (
        ("loader", *LOADER_PSRAM),
        ("kernel", *KERNEL_GENERAL_RAM),
        ("display", *DISPLAY_POOL),
        ("user", *USER_POOL),
        ("comms", *COMMS_POOL),
    )
    assert layout[0][1] == PSRAM[0]
    assert all(first[2] == second[1] for first, second in zip(layout, layout[1:]))
    assert layout[-1][2] == PSRAM[1]
    assert tuple((end - start) // mib for _, start, end in layout) == (
        4, 15, 4, 8, 1,
    )
    assert sum(end - start for _, start, end in layout) == 32 * mib
    assert LINUX_RAM[1] - LINUX_RAM[0] == 28 * mib
    assert (
        (DISPLAY_POOL[1] - DISPLAY_POOL[0])
        + (USER_POOL[1] - USER_POOL[0])
        + (COMMS_POOL[1] - COMMS_POOL[0])
    ) == 13 * mib
    assert KERNEL_GENERAL_RAM[1] - KERNEL_GENERAL_RAM[0] == 15 * mib
    assert FRAME_ADDRESSES[1:] == (
        DISPLAY_POOL[0], DISPLAY_POOL[0] + DISPLAY_SLOT_SIZE,
    )
    assert DISPLAY_SLOT_SIZE - FRAME_SIZE == 49_152

    # Image header memory_size, rather than the smaller file size, bounds BSS
    # zeroing. Ending exactly at the display pool is valid; one byte beyond is
    # rejected before the loader allocates, test-writes, loads, or zeroes it.
    validate_kernel_memory_span(KERNEL_GENERAL_RAM[1] - KERNEL_LOAD_ADDRESS)
    for memory_size in (
        KERNEL_GENERAL_RAM[1] - KERNEL_LOAD_ADDRESS + 1,
        0xFFFFFFFF,
    ):
        try:
            validate_kernel_memory_span(memory_size)
        except ContractError as error:
            assert str(error) == "kernel-memory-span"
        else:
            raise AssertionError("kernel memory crossed the display-pool boundary")

    required_dts = (
        "memory@48400000 {",
        "reg = <0x48400000 0x01c00000>;",
        "loader@48000000 {",
        "reg = <0x48000000 0x00400000>;",
        "display_pool: display-pool@49300000",
        "reg = <0x49300000 0x00400000>;",
        "user_pool: user-pool@49700000",
        "reg = <0x49700000 0x00800000>;",
        "comms@49f00000 {",
        "reg = <0x49f00000 0x00100000>;",
    )
    for stage in ("m7", "m9"):
        path = (
            ROOT / "buildroot-external" / "board" / "micronux"
            / f"dts-{stage}" / "espressif" / "esp32p4-micronux.dts"
        )
        text = path.read_text(encoding="utf-8")
        for token in required_dts:
            if token not in text:
                raise AssertionError(f"memory-map-missing:{stage}:{token}")
        compact = " ".join(text.split())
        display_pool = (
            "display_pool: display-pool@49300000 { "
            "reg = <0x49300000 0x00400000>; no-map; };"
        )
        if display_pool not in compact:
            raise AssertionError(f"memory-map-display-pool-not-no-map:{stage}")
    return 12


def test_ownership() -> int:
    tests = 0
    normal = OwnershipModel()
    normal.queue_render()
    assert (normal.front, normal.queued, normal.render) == (0, 1, 2)
    normal.complete(True)
    assert (normal.front, normal.queued, normal.render) == (1, None, 2)
    assert (normal.requests, normal.completions, normal.generation) == (1, 1, 1)
    tests += 1

    replacement = OwnershipModel()
    replacement.queue_render()
    replacement.queue_render()
    assert (replacement.front, replacement.queued, replacement.render) == (0, 2, 1)
    replacement.complete(True)
    assert (replacement.front, replacement.queued, replacement.render) == (2, None, 1)
    assert (replacement.requests, replacement.completions) == (2, 1)
    tests += 1

    failed = OwnershipModel()
    failed.queue_render()
    before = (failed.front, failed.queued, failed.render)
    failed.complete(False)
    assert (failed.front, failed.queued, failed.render) == before
    assert (failed.rearm_attempts, failed.rearm_failures, failed.completions) == (1, 1, 0)
    tests += 1

    idle = OwnershipModel()
    idle.complete(True)
    assert (idle.front, idle.queued, idle.render) == (0, None, 1)
    assert (idle.rearm_attempts, idle.generation) == (1, 1)
    tests += 1

    startup = OwnershipModel(active=False)
    startup.queue_render()
    assert (startup.front, startup.queued, startup.render) == (0, 1, 1)
    startup.start()
    assert (startup.front, startup.queued, startup.render) == (1, None, 0)
    tests += 1

    contained = OwnershipModel()
    contained.queue_render()
    before = (contained.front, contained.queued, contained.render)
    contained.faulted = True
    contained.queue_render()
    contained.complete(True)
    assert (contained.front, contained.queued, contained.render) == before
    assert (contained.rearm_attempts, contained.completions, contained.generation) == (1, 0, 1)
    tests += 1
    return tests


def test_hardware_parser() -> int:
    """Execute the real ABI-v3 parsers without importing serial or hardware."""
    path = ROOT / "scripts" / "m9-hardware-test.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assignment_names = {
        "FAILURE_MARKERS",
        "SHELL_PROMPT_RE",
        "SCANOUT_LINE_RE",
        "DIAGNOSTICS_LINE_RE",
        "OWNERSHIP_LINE_RE",
        "TOUCH_LINE_RE",
        "RUNTIME_STATUS_RE",
        "MAX_REARM_NS",
    }
    function_names = {
        "failure_marker",
        "scanout_contract_problem",
        "scanout_samples",
        "diagnostics_samples",
        "diagnostics_contract_problem",
        "display_health_problem",
        "runtime_status_problem",
    }
    selected: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in assignment_names
            for target in node.targets
        ):
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in function_names:
            selected.append(node)
    namespace: dict[str, object] = {"re": re}
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    exec(compile(module, str(path), "exec"), namespace)
    missing = (assignment_names | function_names) - namespace.keys()
    if missing:
        raise AssertionError(f"hardware-parser-extraction-missing:{sorted(missing)}")

    tests = 0

    def check(condition: bool, reason: str) -> None:
        nonlocal tests
        if not condition:
            raise AssertionError(reason)
        tests += 1

    scanout_problem = namespace["scanout_contract_problem"]
    scanout_samples = namespace["scanout_samples"]
    diagnostics_problem = namespace["diagnostics_contract_problem"]
    diagnostics_samples = namespace["diagnostics_samples"]
    health_problem = namespace["display_health_problem"]
    failure_marker = namespace["failure_marker"]
    runtime_problem = namespace["runtime_status_problem"]
    ownership_re = namespace["OWNERSHIP_LINE_RE"]
    touch_re = namespace["TOUCH_LINE_RE"]
    prompt_re = namespace["SHELL_PROMPT_RE"]

    def scanout_line(
        before: int, after: int, front: int, queued: int, back: int,
        rearms: int, flip_requests: int, flip_completions: int,
        generation: int, commit: int,
    ) -> str:
        return (
            "running abi=3 state=RUNTIME_REVEALED "
            f"frames={before}->{after} error=00000000 underruns=0 "
            "chen=1 faults=0 host-errors=00000000:00000000 frame-ack=on "
            "clock=auto lp=enabled backlight-gate=on buffers=3 "
            f"front={front} queued={queued} back={back} rearm={rearms}/0 "
            f"flips={flip_requests}/{flip_completions} "
            f"generation={generation} commit={commit} guards=ok "
            "guard-errors=0 arm-to-irq-ns=14550000/14590000 "
            "arm-to-irq-over20ms=0 rearm-ns=95000/140000 "
            "fifo-irq=256/8/0 fifo-poll=384/16/0 "
            "bridge-filler=00000000 bridge-misc=00003201 "
            "i2c=active-serialized\n"
        )

    def diagnostics_line(
        frames: int, front: int, queued: int, back: int, rearms: int,
        flip_requests: int, flip_completions: int, generation: int,
        commit: int,
    ) -> str:
        return (
            "abi=3 state=RUNTIME_REVEALED "
            f"frames={frames} faults=0 "
            "error=00000000 host-errors=00000000:00000000 underruns=0 "
            f"buffers=3 front={front} queued={queued} back={back} "
            f"rearm={rearms}/0 flips={flip_requests}/{flip_completions} "
            f"generation={generation} commit={commit} guards=ok "
            "guard-errors=0 policy=ok "
            "arm-to-irq-ns=14550000/14590000 arm-to-irq-over20ms=0 "
            "rearm-ns=95000/140000 fifo-irq=256/8/0 "
            "fifo-poll=384/16/0 bridge-filler=00000000 "
            "bridge-misc=00003201 i2c=active-serialized "
            "physical-panel-state=unobserved\n"
        )

    scanout_first = scanout_line(10, 14, 0, -1, 1, 20, 1, 1, 14, 1)
    scanout_second = scanout_line(14, 18, 1, -1, 2, 24, 2, 2, 18, 2)
    diagnostics_first = diagnostics_line(14, 0, -1, 1, 20, 1, 1, 14, 1)
    diagnostics_second = diagnostics_line(18, 1, -1, 2, 24, 2, 2, 18, 2)
    scanout = scanout_first + scanout_second
    diagnostics = diagnostics_first + diagnostics_second
    health = scanout + diagnostics

    check(len(scanout_samples(scanout)) == 2, "hardware-scanout-sample-count")
    check(
        scanout_problem(
            scanout, minimum=2, require_flip=True, require_progress=True
        ) is None,
        "hardware-scanout-valid",
    )
    check(
        len(diagnostics_samples(diagnostics)) == 2,
        "hardware-diagnostics-sample-count",
    )
    check(
        diagnostics_problem(
            diagnostics, minimum=2, require_flip=True, require_progress=True
        ) is None,
        "hardware-diagnostics-valid",
    )
    check(
        health_problem(
            health, minimum=2, require_flip=True, require_progress=True
        ) is None,
        "hardware-health-valid",
    )

    scanout_negatives = (
        (scanout_first.replace("error=00000000", "error=00000001"),
         "scanout-health-nonzero"),
        (scanout_first.replace("underruns=0", "underruns=1"),
         "scanout-health-nonzero"),
        (scanout_first.replace("chen=1", "chen=0"),
         "scanout-health-nonzero"),
        (scanout_first.replace("faults=0", "faults=1"),
         "scanout-health-nonzero"),
        (scanout_first.replace("00000000:00000000", "00000001:00000000"),
         "scanout-health-nonzero"),
        (scanout_first.replace("rearm=20/0", "rearm=20/1"),
         "scanout-rearm-counter"),
        (scanout_first.replace("arm-to-irq-over20ms=0",
                               "arm-to-irq-over20ms=1"),
         "scanout-arm-to-irq-over20ms"),
        (scanout_first.replace("rearm-ns=95000/140000",
                               "rearm-ns=95000/1000000"),
         "scanout-rearm-latency"),
        (scanout_first.replace("bridge-filler=00000000",
                               "bridge-filler=00003fff"),
         "scanout-bridge-filler"),
        (scanout_first.replace("bridge-misc=00003201",
                               "bridge-misc=00003200"),
         "scanout-bridge-misc"),
        (scanout_first.replace("buffers=3", "buffers=2"),
         "scanout-buffer-count"),
        (scanout_first.replace("front=0 queued=-1 back=1",
                               "front=0 queued=-1 back=0"),
         "scanout-front-back-role"),
        (scanout_first.replace("front=0 queued=-1 back=1",
                               "front=0 queued=0 back=1"),
         "scanout-queued-role"),
        (scanout_first.replace("flips=1/1", "flips=1/2"),
         "scanout-flip-counter"),
        (scanout_first.replace("generation=14", "generation=0"),
         "scanout-generation"),
        (scanout_first.replace("commit=1", "commit=0"),
         "scanout-commit"),
    )
    for fixture, expected in scanout_negatives:
        check(scanout_problem(fixture) == expected, f"hardware-{expected}")

    rearm_regression = scanout_second + scanout_line(
        18, 22, 1, -1, 2, 23, 2, 2, 22, 2
    )
    check(
        scanout_problem(rearm_regression, minimum=2)
        == "scanout-rearms-regressed",
        "hardware-scanout-rearms-regression",
    )
    progress_regression = scanout_second + scanout_line(
        13, 17, 1, -1, 2, 24, 2, 2, 18, 2
    )
    check(
        scanout_problem(progress_regression, minimum=2)
        == "scanout-before-regressed",
        "hardware-scanout-progress-regression",
    )
    no_flip = scanout_first + scanout_line(
        14, 18, 1, -1, 2, 24, 1, 1, 18, 1
    )
    check(
        scanout_problem(no_flip, minimum=2, require_flip=True)
        == "scanout-flip-not-observed",
        "hardware-scanout-missing-flip",
    )
    no_progress = scanout_first + scanout_line(
        10, 14, 1, -1, 2, 24, 2, 2, 18, 2
    )
    check(
        scanout_problem(no_progress, minimum=2, require_progress=True)
        == "scanout-progress-not-observed",
        "hardware-scanout-missing-progress",
    )
    for old, new in (
        ("state=RUNTIME_REVEALED", "state=REVEALING"),
        ("frame-ack=on", "frame-ack=off"),
        ("i2c=active-serialized", "i2c=released"),
    ):
        check(
            scanout_problem(scanout_first.replace(old, new))
            == "scanout-contract-count-0-expected-1",
            f"hardware-scanout-reject-{new}",
        )

    diagnostics_negatives = (
        (diagnostics_first.replace("policy=ok", "policy=bad"),
         "diagnostics-contract-count-0-expected-1"),
        (diagnostics_first.replace("guard-errors=0", "guard-errors=1"),
         "diagnostics-health-nonzero"),
        (diagnostics_first.replace("rearm=20/0", "rearm=0/0"),
         "diagnostics-runtime-counter"),
        (diagnostics_first.replace("arm-to-irq-over20ms=0",
                                   "arm-to-irq-over20ms=1"),
         "diagnostics-arm-to-irq-over20ms"),
        (diagnostics_first.replace("rearm-ns=95000/140000",
                                   "rearm-ns=95000/1000000"),
         "diagnostics-rearm-latency"),
        (diagnostics_first.replace("bridge-filler=00000000",
                                   "bridge-filler=00003fff"),
         "diagnostics-bridge-filler"),
        (diagnostics_first.replace("bridge-misc=00003201",
                                   "bridge-misc=00003200"),
         "diagnostics-bridge-misc"),
        (diagnostics_first.replace("flips=1/1", "flips=1/2"),
         "diagnostics-flip-counter"),
    )
    for fixture, expected in diagnostics_negatives:
        check(diagnostics_problem(fixture) == expected, f"hardware-{expected}")
    diagnostics_regression = diagnostics_second + diagnostics_line(
        17, 1, -1, 2, 24, 2, 2, 18, 2
    )
    check(
        diagnostics_problem(diagnostics_regression, minimum=2)
        == "diagnostics-frames-regressed",
        "hardware-diagnostics-regression",
    )
    check(
        health_problem(health + "Kernel panic")
        == "terminal-failure-Kernel-panic",
        "hardware-terminal-failure",
    )
    check(failure_marker("prefix Oops: suffix") == "Oops:",
          "hardware-failure-marker")

    ownership = (
        "linux abi=3 mode=native-cold-init fb0 buffers=3 dma-channel=0 "
        "frame-irq=18 rearm=explicit mmap=denied "
        "i2c=active-serialized\n"
    )
    check(ownership_re.search(ownership) is not None, "hardware-ownership-valid")
    check(
        ownership_re.search(ownership.replace("abi=3", "abi=2")) is None,
        "hardware-ownership-reject-abi2",
    )
    for touch in (
        "ready product=9271 address=0x5d mode=poll interval_ms=10 "
        "reads=7 errors=0 down=0\n",
        "ready product=9271 address=0x14 mode=poll interval_ms=10 "
        "reads=8 errors=0 down=1\n",
        "unavailable\n",
    ):
        check(touch_re.search(touch) is not None, "hardware-touch-valid")
    for touch in (
        "registering\n",
        "ready product=9271 address=0x45 mode=poll interval_ms=10 "
        "reads=7 errors=0 down=0\n",
        "ready product=9271 address=0x5d mode=poll interval_ms=20 "
        "reads=7 errors=0 down=0\n",
        "ready product=9271 address=0x5d mode=poll interval_ms=10 "
        "reads=7 errors=1 down=0\n",
    ):
        check(touch_re.search(touch) is None, "hardware-touch-invalid")
    clean_snapshot_touch = (
        "ready product=9271 address=0x5d mode=poll interval_ms=10 "
        "reads=0 errors=0 down=0\r\n"
    )
    check(
        touch_re.search("> " + clean_snapshot_touch) is None,
        "hardware-touch-continuation-prefix-rejected",
    )
    check(
        touch_re.search(clean_snapshot_touch) is not None,
        "hardware-touch-clean-after-printf-escape",
    )
    check(prompt_re.search("\r\n/ # ") is not None, "hardware-prompt-busybox")
    check(prompt_re.search("\nmicronux# ") is not None, "hardware-prompt-custom")
    check(prompt_re.search("\nroot# ") is None, "hardware-prompt-invalid")
    runtime = (
        "pattern=framebuffer boot_ready=1 native_state=RUNTIME_REVEALED "
        "brightness=63 bl_power=0 actual_brightness=63"
    )
    check(runtime_problem(runtime) is None, "hardware-runtime-status-valid")
    check(
        runtime_problem(runtime.replace("brightness=63", "brightness=0", 1))
        == "runtime-status-count-0-expected-1",
        "hardware-runtime-status-invalid",
    )
    return tests


def bind_to_sources() -> int:
    patches = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0023-video-fbdev-adopt-triple-buffer-irq-rearm-scanout.patch",
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0024-video-fbdev-match-yamui-display-policy.patch",
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0025-video-fbdev-qualify-initial-reveal-and-log-blocks.patch",
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0026-video-fbdev-tolerate-first-window-dpi-refill-residue.patch",
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0027-video-fbdev-require-dark-vpg-display-handoff.patch",
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0028-video-fbdev-disable-per-frame-dsi-bta.patch",
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0029-video-fbdev-request-hs-in-auto-clock-mode.patch",
    )
    cold_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0030-video-fbdev-add-ABI-v3-native-cold-probe.patch"
    )
    ldo_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0031-video-fbdev-add-native-I2C-and-LDO-cold-stage.patch"
    )
    panel_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0032-video-fbdev-program-native-DSI-panel-cold-stage.patch"
    )
    scanout_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0033-video-fbdev-start-native-DSI-scanout-cold-stage.patch"
    )
    reveal_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0034-video-fbdev-reveal-native-DSI-scanout-safely.patch"
    )
    native_irq_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0035-video-fbdev-validate-native-CLIC-hardware-IRQ.patch"
    )
    native_diagnostic_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0036-video-fbdev-diagnose-native-GDMA-readback.patch"
    )
    native_gdma_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0037-video-fbdev-validate-GDMA-enable-semantics.patch"
    )
    native_scanout_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0038-video-fbdev-diagnose-native-scanout-qualification.patch"
    )
    native_arm_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0039-video-fbdev-validate-native-DMA-arm-transaction.patch"
    )
    native_policy_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0040-video-fbdev-stop-gating-scanout-on-inactive-DSI-mirror.patch"
    )
    native_feed_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0041-video-fbdev-keep-native-one-shot-scanout-fed.patch"
    )
    visible_epoch_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0042-video-fbdev-start-telemetry-at-native-reveal.patch"
    )
    fault_evidence_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0043-video-fbdev-preserve-native-runtime-fault-evidence.patch"
    )
    bandwidth_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0044-video-fbdev-derate-native-DSI-for-PSRAM-bandwidth.patch"
    )
    sd_isolation_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0045-mmc-dw-allow-an-isolated-ESP32-P4-slot.patch"
    )
    continuous_prep_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-peripherals" / "linux"
        / "0046-video-fbdev-validate-native-continuous-reload-plan.patch"
    )
    usb_console_patch = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "patches-platform" / "linux"
        / "0008-serial-keep-esp32p4-usb-console-nonblocking.patch"
    )
    header = ROOT / "loader" / "main" / "micronux_mipi_dsi.h"
    loader_source = ROOT / "loader" / "main" / "micronux_mipi_dsi.c"
    loader_main = ROOT / "loader" / "main" / "micronux_loader.c"
    loader_defaults = ROOT / "loader" / "sdkconfig.defaults"
    idf_handoff_patch = (
        ROOT / "loader" / "patches" / "esp-idf-v6.0.1"
        / "0002-lcd-add-dpi-circular-handoff.patch"
    )
    dma_pms = ROOT / "loader" / "main" / "micronux_dma_pms.c"
    hardware_test = ROOT / "scripts" / "m9-hardware-test.py"
    hardware_telemetry_test = (
        ROOT / "scripts" / "m9-hardware-telemetry-test.py"
    )
    display_test = (
        ROOT / "buildroot-external" / "package" / "micronux-display-test"
        / "micronux-display-test.c"
    )
    packer = ROOT / "scripts" / "m3-pack.py"
    stage_builds = (ROOT / "scripts" / "m7-build.sh", ROOT / "scripts" / "m9-build.sh")
    rootfs_init = (
        ROOT / "buildroot-external" / "board" / "micronux"
        / "rootfs-m6-combined" / "init"
    )
    device_trees = (
        ROOT / "buildroot-external" / "board" / "micronux" / "dts-m7"
        / "espressif" / "esp32p4-micronux.dts",
        ROOT / "buildroot-external" / "board" / "micronux" / "dts-m9"
        / "espressif" / "esp32p4-micronux.dts",
    )
    patch_text = "\n".join(
        patch.read_text(encoding="utf-8") for patch in patches
    )
    added = "\n".join(
        line[1:] for line in patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    cold_patch_text = cold_patch.read_text(encoding="utf-8")
    cold_added = "\n".join(
        line[1:] for line in cold_patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    cold_added_compact = " ".join(cold_added.split())
    ldo_patch_text = ldo_patch.read_text(encoding="utf-8")
    ldo_added = "\n".join(
        line[1:] for line in ldo_patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    ldo_added_compact = " ".join(ldo_added.split())
    panel_patch_text = panel_patch.read_text(encoding="utf-8")
    panel_added = "\n".join(
        line[1:] for line in panel_patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    panel_added_compact = " ".join(panel_added.split())
    scanout_patch_text = scanout_patch.read_text(encoding="utf-8")
    scanout_added = "\n".join(
        line[1:] for line in scanout_patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    scanout_added_compact = " ".join(scanout_added.split())
    reveal_patch_text = reveal_patch.read_text(encoding="utf-8")
    reveal_added = "\n".join(
        line[1:] for line in reveal_patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    reveal_added_compact = " ".join(reveal_added.split())
    reveal_postimage_hunks = "\n".join(
        line[1:] for line in reveal_patch_text.splitlines()
        if line.startswith((" ", "+")) and not line.startswith("+++")
    )
    reveal_postimage_compact = " ".join(reveal_postimage_hunks.split())
    native_irq_patch_text = native_irq_patch.read_text(encoding="utf-8")
    native_irq_added = "\n".join(
        line[1:] for line in native_irq_patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    native_irq_added_compact = " ".join(native_irq_added.split())
    native_irq_postimage_hunks = "\n".join(
        line[1:] for line in native_irq_patch_text.splitlines()
        if line.startswith((" ", "+")) and not line.startswith("+++")
    )
    native_irq_postimage_compact = " ".join(
        native_irq_postimage_hunks.split()
    )
    native_diagnostic_patch_text = native_diagnostic_patch.read_text(
        encoding="utf-8"
    )
    native_diagnostic_added = "\n".join(
        line[1:] for line in native_diagnostic_patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    native_diagnostic_added_compact = " ".join(
        native_diagnostic_added.split()
    )
    native_diagnostic_removed = "\n".join(
        line[1:] for line in native_diagnostic_patch_text.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    native_diagnostic_removed_compact = " ".join(
        native_diagnostic_removed.split()
    )
    native_diagnostic_postimage_hunks = "\n".join(
        line[1:] for line in native_diagnostic_patch_text.splitlines()
        if line.startswith((" ", "+")) and not line.startswith("+++")
    )
    native_diagnostic_postimage_compact = " ".join(
        native_diagnostic_postimage_hunks.split()
    )
    native_gdma_patch_text = native_gdma_patch.read_text(encoding="utf-8")
    native_gdma_added = "\n".join(
        line[1:] for line in native_gdma_patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    native_gdma_added_compact = " ".join(native_gdma_added.split())
    native_gdma_removed = "\n".join(
        line[1:] for line in native_gdma_patch_text.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    native_gdma_removed_compact = " ".join(native_gdma_removed.split())
    native_gdma_postimage_hunks = "\n".join(
        line[1:] for line in native_gdma_patch_text.splitlines()
        if line.startswith((" ", "+")) and not line.startswith("+++")
    )
    native_gdma_postimage_compact = " ".join(
        native_gdma_postimage_hunks.split()
    )
    native_scanout_patch_text = native_scanout_patch.read_text(
        encoding="utf-8"
    )
    native_scanout_added = "\n".join(
        line[1:] for line in native_scanout_patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    native_scanout_added_compact = " ".join(
        native_scanout_added.split()
    )
    native_scanout_removed = "\n".join(
        line[1:] for line in native_scanout_patch_text.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    native_scanout_removed_compact = " ".join(
        native_scanout_removed.split()
    )
    native_scanout_postimage_hunks = "\n".join(
        line[1:] for line in native_scanout_patch_text.splitlines()
        if line.startswith((" ", "+")) and not line.startswith("+++")
    )
    native_scanout_postimage_compact = " ".join(
        native_scanout_postimage_hunks.split()
    )
    native_arm_patch_text = native_arm_patch.read_text(encoding="utf-8")
    native_arm_added = "\n".join(
        line[1:] for line in native_arm_patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    native_arm_added_compact = " ".join(native_arm_added.split())
    native_arm_removed = "\n".join(
        line[1:] for line in native_arm_patch_text.splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    native_arm_removed_compact = " ".join(native_arm_removed.split())
    native_arm_postimage_hunks = "\n".join(
        line[1:] for line in native_arm_patch_text.splitlines()
        if line.startswith((" ", "+")) and not line.startswith("+++")
    )
    native_arm_postimage_compact = " ".join(
        native_arm_postimage_hunks.split()
    )
    native_policy_patch_text = native_policy_patch.read_text(encoding="utf-8")
    native_policy_added_lines = tuple(
        line[1:] for line in native_policy_patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    native_policy_removed_lines = tuple(
        line[1:] for line in native_policy_patch_text.splitlines()
        if line.startswith("-") and not line.startswith("---") and line != "-- "
    )
    native_policy_postimage_hunks = "\n".join(
        line[1:] for line in native_policy_patch_text.splitlines()
        if line.startswith((" ", "+")) and not line.startswith("+++")
    )
    native_policy_postimage_compact = " ".join(
        native_policy_postimage_hunks.split()
    )
    native_feed_patch_text = native_feed_patch.read_text(encoding="utf-8")
    native_feed_added = "\n".join(
        line[1:] for line in native_feed_patch_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    native_feed_added_compact = " ".join(native_feed_added.split())
    native_feed_removed = "\n".join(
        line[1:] for line in native_feed_patch_text.splitlines()
        if line.startswith("-") and not line.startswith("---") and line != "-- "
    )
    native_feed_removed_compact = " ".join(native_feed_removed.split())
    native_feed_postimage_hunks = "\n".join(
        line[1:] for line in native_feed_patch_text.splitlines()
        if line.startswith((" ", "+")) and not line.startswith("+++")
    )
    native_feed_postimage_compact = " ".join(
        native_feed_postimage_hunks.split()
    )
    visible_epoch_patch_text = visible_epoch_patch.read_text(encoding="utf-8")
    fault_evidence_patch_text = fault_evidence_patch.read_text(encoding="utf-8")
    fault_evidence_added_compact = " ".join(
        "\n".join(
            line[1:] for line in fault_evidence_patch_text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ).split()
    )
    fault_evidence_postimage_compact = " ".join(
        "\n".join(
            line[1:] for line in fault_evidence_patch_text.splitlines()
            if line.startswith((" ", "+")) and not line.startswith("+++")
        ).split()
    )
    bandwidth_patch_text = bandwidth_patch.read_text(encoding="utf-8")
    bandwidth_added_compact = " ".join(
        "\n".join(
            line[1:] for line in bandwidth_patch_text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ).split()
    )
    bandwidth_removed_compact = " ".join(
        "\n".join(
            line[1:] for line in bandwidth_patch_text.splitlines()
            if line.startswith("-") and not line.startswith("---")
        ).split()
    )
    bandwidth_postimage_compact = " ".join(
        "\n".join(
            line[1:] for line in bandwidth_patch_text.splitlines()
            if line.startswith((" ", "+")) and not line.startswith("+++")
        ).split()
    )
    sd_isolation_patch_text = sd_isolation_patch.read_text(encoding="utf-8")
    sd_isolation_added_compact = " ".join(
        "\n".join(
            line[1:] for line in sd_isolation_patch_text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ).split()
    )
    continuous_prep_patch_text = continuous_prep_patch.read_text(
        encoding="utf-8"
    )
    continuous_prep_added_compact = " ".join(
        "\n".join(
            line[1:] for line in continuous_prep_patch_text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ).split()
    )
    continuous_prep_removed_compact = " ".join(
        "\n".join(
            line[1:] for line in continuous_prep_patch_text.splitlines()
            if line.startswith("-") and not line.startswith("---")
            and line != "-- "
        ).split()
    )
    continuous_prep_postimage_compact = " ".join(
        "\n".join(
            line[1:] for line in continuous_prep_patch_text.splitlines()
            if line.startswith((" ", "+")) and not line.startswith("+++")
        ).split()
    )
    usb_console_patch_text = usb_console_patch.read_text(encoding="utf-8")
    usb_console_added_compact = " ".join(
        "\n".join(
            line[1:] for line in usb_console_patch_text.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ).split()
    )
    usb_console_removed_compact = " ".join(
        "\n".join(
            line[1:] for line in usb_console_patch_text.splitlines()
            if line.startswith("-") and not line.startswith("---")
            and line != "-- "
        ).split()
    )
    usb_console_postimage_compact = " ".join(
        "\n".join(
            line[1:] for line in usb_console_patch_text.splitlines()
            if line.startswith((" ", "+")) and not line.startswith("+++")
        ).split()
    )
    header_text = header.read_text(encoding="utf-8")
    idf_handoff_text = idf_handoff_patch.read_text(encoding="utf-8")
    dma_pms_text = dma_pms.read_text(encoding="utf-8")
    dma_pms_compact = " ".join(dma_pms_text.split())
    hardware_test_text = hardware_test.read_text(encoding="utf-8")
    hardware_test_compact = " ".join(hardware_test_text.split())
    hardware_telemetry_test_text = hardware_telemetry_test.read_text(
        encoding="utf-8"
    )
    display_test_text = display_test.read_text(encoding="utf-8")
    display_test_compact = " ".join(display_test_text.split())
    loader_text = loader_source.read_text(encoding="utf-8")
    loader_compact = " ".join(loader_text.split())
    loader_main_text = loader_main.read_text(encoding="utf-8")
    loader_defaults_text = loader_defaults.read_text(encoding="utf-8")
    stage_e_tests = 0
    native_irq_tests = 0
    native_diagnostic_tests = 0
    native_gdma_tests = 0
    native_scanout_tests = 0
    native_arm_tests = 0
    native_policy_tests = 0
    native_feed_tests = 0
    visible_epoch_tests = 0
    fault_evidence_tests = 0
    bandwidth_tests = 0
    sd_isolation_tests = 0
    continuous_prep_tests = 0

    def stage_e_check(condition: bool, reason: str) -> None:
        nonlocal stage_e_tests
        if not condition:
            raise AssertionError(reason)
        stage_e_tests += 1

    def native_irq_check(condition: bool, reason: str) -> None:
        nonlocal native_irq_tests
        if not condition:
            raise AssertionError(reason)
        native_irq_tests += 1

    def native_diagnostic_check(condition: bool, reason: str) -> None:
        nonlocal native_diagnostic_tests
        if not condition:
            raise AssertionError(reason)
        native_diagnostic_tests += 1

    def native_gdma_check(condition: bool, reason: str) -> None:
        nonlocal native_gdma_tests
        if not condition:
            raise AssertionError(reason)
        native_gdma_tests += 1

    def native_scanout_check(condition: bool, reason: str) -> None:
        nonlocal native_scanout_tests
        if not condition:
            raise AssertionError(reason)
        native_scanout_tests += 1

    def native_arm_check(condition: bool, reason: str) -> None:
        nonlocal native_arm_tests
        if not condition:
            raise AssertionError(reason)
        native_arm_tests += 1

    def native_policy_check(condition: bool, reason: str) -> None:
        nonlocal native_policy_tests
        if not condition:
            raise AssertionError(reason)
        native_policy_tests += 1

    def native_feed_check(condition: bool, reason: str) -> None:
        nonlocal native_feed_tests
        if not condition:
            raise AssertionError(reason)
        native_feed_tests += 1

    def visible_epoch_check(condition: bool, reason: str) -> None:
        nonlocal visible_epoch_tests
        if not condition:
            raise AssertionError(reason)
        visible_epoch_tests += 1

    def fault_evidence_check(condition: bool, reason: str) -> None:
        nonlocal fault_evidence_tests
        if not condition:
            raise AssertionError(reason)
        fault_evidence_tests += 1

    def bandwidth_check(condition: bool, reason: str) -> None:
        nonlocal bandwidth_tests
        if not condition:
            raise AssertionError(reason)
        bandwidth_tests += 1

    def sd_isolation_check(condition: bool, reason: str) -> None:
        nonlocal sd_isolation_tests
        if not condition:
            raise AssertionError(reason)
        sd_isolation_tests += 1

    def continuous_prep_check(condition: bool, reason: str) -> None:
        nonlocal continuous_prep_tests
        if not condition:
            raise AssertionError(reason)
        continuous_prep_tests += 1

    def native_feed_require_order(
        text: str, tokens: tuple[str, ...], label: str
    ) -> None:
        position = -1
        for token in tokens:
            position = text.find(token, position + 1)
            native_feed_check(
                position >= 0,
                f"native-feed-{label}-order:{token}",
            )

    def native_arm_require_order(
        text: str, tokens: tuple[str, ...], label: str
    ) -> None:
        position = -1
        for token in tokens:
            position = text.find(token, position + 1)
            native_arm_check(
                position >= 0,
                f"native-arm-{label}-order:{token}",
            )

    def native_scanout_block(
        text: str, start: str, end: str, label: str
    ) -> str:
        start_index = text.find(start)
        end_index = text.find(end, start_index + len(start))
        native_scanout_check(
            start_index >= 0 and end_index > start_index,
            f"native-scanout-{label}-block-missing",
        )
        return text[start_index:end_index]

    def native_scanout_require_order(
        text: str, tokens: tuple[str, ...], label: str
    ) -> None:
        position = -1
        for token in tokens:
            position = text.find(token, position + 1)
            native_scanout_check(
                position >= 0,
                f"native-scanout-{label}-order:{token}",
            )

    def source_block(text: str, start: str, end: str, label: str) -> str:
        start_index = text.find(start)
        end_index = text.find(end, start_index + len(start))
        stage_e_check(
            start_index >= 0 and end_index > start_index,
            f"{label}-block-missing",
        )
        return text[start_index:end_index]

    def require_order(text: str, tokens: tuple[str, ...], label: str) -> None:
        position = -1
        for token in tokens:
            position = text.find(token, position + 1)
            if position < 0:
                raise AssertionError(f"{label}-order-missing:{token}")
        stage_e_check(True, f"{label}-order")

    expected_source_hashes = (
        (
            reveal_patch,
            "dc9f9902b5a787d052dfe61ea1ab418d30fadf2007df8ef1b40fae4cd913e068",
        ),
        (
            native_irq_patch,
            "9e3a416bc6d724a99e8da81f88fd8b19cd922ff9e145c2e45476534970b28c45",
        ),
        (
            native_diagnostic_patch,
            "448c70f2078a548c068aaa98687e79926a14a355e1279a542ae759a7ad60054e",
        ),
        (
            native_gdma_patch,
            "ce5ac16d98e10e518dc8dc74c8d88ada4a4bb87ad010459e95d39b79380a68d9",
        ),
        (
            native_scanout_patch,
            "ac27049f179f0e445095cf31ee8c37c7417c1a124c4c9f69dac9f9c7c072131f",
        ),
        (
            native_arm_patch,
            "ebc2f0a8800bd133eb18e6be19d0b1dc3d1ebbcc347db81c2b91454d1fdab880",
        ),
        (
            native_policy_patch,
            "f0443bda132ff473f498d3cdac18f885f01f00e2d07e65a5e9eb899cbfed5c4f",
        ),
        (
            native_feed_patch,
            "521c4c2eb8c37d51668850404d737010f66330bef1056ef106cfc4dd32bbdd8c",
        ),
        (
            visible_epoch_patch,
            "0d9c78583de6b0dedf794e12c29f4606768b5bc0b10f811cfb8f877c3898654c",
        ),
        (
            fault_evidence_patch,
            "57a993584b45628746bd6419ff52468567a32ed9fed1737083e79d325da78f54",
        ),
        (
            bandwidth_patch,
            "f2daf47a960a345097b88c0c7f5096f75bf174e926253c82f3f9a98d7a8deb98",
        ),
        (
            sd_isolation_patch,
            "88f7c7535573884b5980a896e63982ff31ef7db14ab319ea49fb6b6567ecfde9",
        ),
        (
            continuous_prep_patch,
            "f9305b344bd28f425c824affe3aa7e5ed42e6f8e3a1e48601c61ad691a920881",
        ),
        (
            usb_console_patch,
            "56cdb578e6d836451363fa68decda4afc16243f7dd1b14d18dc6c89aa9fd02eb",
        ),
        (
            display_test,
            "852bc9fb48b928205246a7cf2d85f2e6dbad155d2bfdd9fcfbcf470a5e5a63f5",
        ),
        (
            hardware_test,
            "194dfb8328011f152160fa68568589c7d39cbfedfddc3b2d4619d45ccc48219a",
        ),
        (
            hardware_telemetry_test,
            "f504f98dfbdd30c95449f8ac15930c4f9a844c2400f51631ec8b68bd80b199f5",
        ),
    )
    for source, expected_hash in expected_source_hashes:
        actual_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        stage_e_check(
            actual_hash == expected_hash,
            f"source-sha256:{source.name}:{actual_hash}",
        )
    stage_e_check(
        "Subject: [PATCH 8/8] serial: keep ESP32-P4 USB console nonblocking"
        in usb_console_patch_text,
        "usb-console-subject",
    )
    for token in (
        "if (!(readl_relaxed(port->membase + ESP32P4_USB_SERIAL_CONF) &",
        "ESP32P4_USB_SERIAL_DATA_FREE))",
        "if (!esp32s3_acm_tx_fifo_free(port))",
    ):
        stage_e_check(token in usb_console_added_compact, f"usb-console-add:{token}")
    for token in (
        "esp32s3_acm_put_char(port, c);",
        "esp32s3_acm_push(port);",
    ):
        stage_e_check(
            token in usb_console_postimage_compact,
            f"usb-console-postimage:{token}",
        )
    for token in (
        "while (!(readl_relaxed(port->membase + ESP32P4_USB_SERIAL_CONF)",
        "unsigned long timeout = jiffies + HZ;",
        "while (!esp32s3_acm_tx_fifo_free(port))",
    ):
        stage_e_check(
            token in usb_console_removed_compact,
            f"usb-console-remove:{token}",
        )
    required_added = (
        "MICRONUX_DISPLAY_ABI_VERSION\t2",
        "MICRONUX_DISPLAY_BUFFER_COUNT\t3",
        "MICRONUX_DISPLAY_OWNER_LINUX_PENDING 1",
        "MICRONUX_DISPLAY_HSYNC_PULSE\t20",
        "MICRONUX_DISPLAY_HSYNC_BACK_PORCH 20",
        "MICRONUX_DISPLAY_HSYNC_FRONT_PORCH 40",
        "MICRONUX_DISPLAY_VSYNC_PULSE\t4",
        "MICRONUX_DISPLAY_VSYNC_BACK_PORCH 10",
        "MICRONUX_DISPLAY_VSYNC_FRONT_PORCH 30",
        "MICRONUX_RENDER_COMMIT_MS\t20",
        "MICRONUX_FRAMEBUFFER_ALIGNMENT\t8",
        "MICRONUX_DMA_BLOCK_TRANSFER_SIZE 0x0003e7ff",
        "MICRONUX_DMA_DESCRIPTOR_CTRL_LO\t0x001e1b41",
        "MICRONUX_DMA_DESCRIPTOR_CTRL_HI\t0x400f87c0",
        "MICRONUX_DMA_CHANNEL_CFG_LO\t0x0000000f",
        "MICRONUX_DMA_CHANNEL_CFG_HI\t0x0a020001",
        "MICRONUX_DISPLAY_BACKLIGHT_DISABLED 0x13",
        "MICRONUX_DISPLAY_BACKLIGHT_ENABLED 0x17",
        "DSI_HOST_CLOCK_LANE_AUTO\t\t(DSI_HOST_PHY_TXREQUESTCLKHS |",
        "lpclk |= DSI_HOST_CLOCK_LANE_AUTO",
        "host &= ~(DSI_HOST_VPG_MASK | DSI_HOST_FRAME_BTA_ACK_EN)",
        "host |= DSI_HOST_LP_VIDEO_EN_MASK",
        "bool clock_auto, frame_ack_disabled, lp_enabled;",
        "frame_ack_disabled && clock_auto && lp_enabled ?",
        'clock_auto ? "auto" : "invalid"',
        'lp_enabled ? "enabled" : "invalid"',
        "backlight-gate=%s",
        "DSI_BRG_FIFO_FLOW_STATUS\t0x14",
        "bridge-fifo=%08x",
        "micronux_initialize_pool_guards(dsi)",
        "micronux_pool_guards_valid(dsi, true)",
        "micronux_copy_frame(dsi, 1, 0)",
        "micronux_copy_frame(dsi, 2, 0)",
        "DMA_LLI_SAR_HI",
        "DMA_LLI_DAR_HI",
        "DMA_LLI_LLP_HI",
        "a failed rearm leaves both the old front and queued frame",
        "next_index = !faulted && dsi->queued_index >= 0 ?",
        "if (!faulted && dsi->queued_index >= 0) {",
        "mod_delayed_work(system_wq, &dsi->render_work",
        "display remove could not confirm darkness; preserving source",
        "Qualify one grace and one strict frame window while still dark.",
        "MICRONUX:M9.2:REVEAL state=blocked",
        "qualification-windows=2",
        "DSI_HOST_INT_STATUS1_FB_STARTUP_GRACE DSI_HOST_INT_STATUS1_DPI_PATH",
        "forgive DPI-path refill residue only",
        "MICRONUX_DISPLAY_HOST_VPG_ACTIVE BIT(7)",
        "host &= ~(DSI_HOST_VPG_MASK | DSI_HOST_FRAME_BTA_ACK_EN)",
        "loader-source=vertical-bars",
        "loader-source=vpg",
    )
    for token in required_added:
        if token not in added:
            raise AssertionError(f"patch-binding-missing:{token}")
    arm = added.index("if (micronux_dma_arm_frame(dsi, next_index))")
    publish = added.index("dsi->front_index = dsi->queued_index;")
    if arm >= publish:
        raise AssertionError("patch-binding-role-publication-before-rearm")
    required_cold_patch = (
        "MICRONUX_DISPLAY_HANDOFF_V3_SIZE 0xc0",
        "MICRONUX_DISPLAY_V3_REQUIRED_FLAGS 0x00003fff",
        "MICRONUX_DISPLAY_COLD_RESET_ASSERT_COMMAND 0x11",
        "MICRONUX_DISPLAY_COLD_RESET_PREPARE_COMMAND 0x13",
        "MICRONUX_DISPLAY_COLD_REVEAL_COMMAND 0x17",
        "MICRONUX_DISPLAY_COLD_PWM_ZERO_SETTLE_MS 100",
        "MICRONUX_DISPLAY_COLD_PANEL_PAYLOAD_CRC32 0xcea07f9b",
        "u32 display_control_reset_assert_command;",
        "u32 display_control_reset_prepare_command;",
        "u32 display_control_reveal_command;",
        "u32 pwm_zero_settle_ms;",
        "u32 panel_payload_crc32;",
        "MICRONUX_DISPLAY_STATE_PROBED_QUIESCENT",
        "state=REJECTED_NO_WRITES",
        "state=PROBED_QUIESCENT abi=3 size=0x00c0 flags=0x00003fff",
        "payload-crc=cea07f9b",
        "commands=acked pwm-zero-write=acked reset-prepare-write=acked",
        "pwm-zero-settle=elapsed reset-assert-write=acked reset-hold=elapsed",
        "dsi-module-reset=asserted dphy-clocks=off ldo-control=off",
        "gdma-reset=asserted clocks=off",
        "descriptor-sd-dma-overlap",
        'of_property_read_bool(pool, "no-map")',
    )
    for token in required_cold_patch:
        if token not in cold_added_compact:
            raise AssertionError(f"cold-patch-binding-missing:{token}")
    required_cold_patch_layout = (
        "!IS_ALIGNED(handoff->framebuffer_address[i], MICRONUX_COLD_PAGE_SIZE)",
        "!IS_ALIGNED(handoff->dma_descriptor_address, MICRONUX_COLD_PAGE_SIZE)",
        "descriptor_page_end = (u64)handoff->dma_descriptor_address + "
        "MICRONUX_COLD_PAGE_SIZE",
        "for (i = 0; i < MICRONUX_COLD_PAGE_SIZE; i++)",
        "handoff->panel_payload_crc32 != "
        "MICRONUX_DISPLAY_COLD_PANEL_PAYLOAD_CRC32",
    )
    for token in required_cold_patch_layout:
        if token not in cold_added_compact:
            raise AssertionError(f"cold-patch-layout-binding-missing:{token}")
    required_ldo_patch = (
        "MICRONUX_DISPLAY_STATE_LDO_READY",
        "static int micronux_cold_initialize_ldo_stage",
        "micronux_cold_i2c_initialize(dsi)",
        "micronux_cold_reissue_quiescent_commands(dsi)",
        "micronux_cold_ldo_enable(dsi, &cal)",
        "micronux_cold_i2c_gate(dsi)",
        "DEVICE_ATTR_WO(cold_init)",
        "state=LDO_READY ldo-channel=3 ldo-unit=2 voltage-mv=2500",
        "i2c0=released-gated",
        "physical-panel-state=unobserved",
        "dsi-module-reset=asserted dphy-clocks=off gdma-reset=asserted",
        "retry=reboot-only optical-state=unobserved",
    )
    for token in required_ldo_patch:
        if token not in ldo_added_compact:
            raise AssertionError(f"ldo-patch-binding-missing:{token}")
    required_panel_patch = (
        "MICRONUX_DISPLAY_STATE_PANEL_PROGRAMMED_QUIESCENT",
        "MICRONUX_DISPLAY_STATE_FAILED_UNVERIFIED",
        "MICRONUX_DISPLAY_COLD_PHY_TIMEOUT_US 500000",
        "MICRONUX_DISPLAY_COLD_PACKET_TIMEOUT_US 100000",
        "MICRONUX_DISPLAY_COLD_PANEL_PARITY_MS 1100",
        "MICRONUX_PANEL_FIRMWARE_TOTAL_BYTES 1052",
        "MICRONUX_PANEL_FIRMWARE_RECORDS 204",
        "MICRONUX_PANEL_FIRMWARE_PAYLOAD_BYTES 1020",
        "payload_crc != dsi->cold_handoff.panel_payload_crc32",
        "crc = crc32_le(~0U, payload, payload_bytes) ^ ~0U",
        "micronux_panel_firmware_request(dsi, &panel)",
        "micronux_cold_dsi_enable(dsi)",
        "micronux_cold_release_panel(dsi)",
        "micronux_cold_program_panel(dsi, &panel)",
        "micronux_cold_dcs_write(dsi, 0x01, NULL, 0)",
        "readl_poll_timeout(dsi->host + DSI_HOST_PHY_STATUS",
        "readl_poll_timeout(dsi->host + DSI_HOST_CMD_PKT_STATUS",
        "state=PANEL_PROGRAMMED_QUIESCENT firmware=validated records=204",
        "bridge=disabled dpi=off gdma-reset=asserted gdma-clocks=off",
        "physical-panel-state=unobserved next=native-scanout",
        "i2c_released = i2c_gated && micronux_cold_i2c_released(dsi)",
        "contained = !pwm_ret && !prepare_ret && !reset_ret && i2c_released && display_blocks && ldo_off",
        "state = \"FAILED_UNVERIFIED\"",
        "retry = \"remove-containment-or-reboot\"",
        "cold_i2c_released = cold_i2c_off && micronux_cold_i2c_released(dsi)",
        "cold_contained = !pwm_ret && !prepare_ret && !reset_ret && cold_i2c_released && cold_dsi_off && cold_ldo_off",
        "cold_remove_state = cold_contained ? \"QUIESCENT\" : \"UNVERIFIED\"",
    )
    for token in required_panel_patch:
        if token not in panel_added_compact:
            raise AssertionError(f"panel-patch-binding-missing:{token}")
    request_index = panel_added_compact.index(
        "ret = micronux_panel_firmware_request(dsi, &panel)"
    )
    dsi_enable_index = panel_added_compact.index(
        "ret = micronux_cold_dsi_enable(dsi)"
    )
    panel_release_index = panel_added_compact.index(
        "ret = micronux_cold_release_panel(dsi)"
    )
    panel_program_index = panel_added_compact.index(
        "ret = micronux_cold_program_panel(dsi, &panel)"
    )
    if not (
        request_index < dsi_enable_index < panel_release_index < panel_program_index
    ):
        raise AssertionError("panel-patch-cold-stage-order")
    required_scanout_patch = (
        "MICRONUX_DISPLAY_STATE_SCANOUT_INITIALIZING",
        "MICRONUX_DISPLAY_STATE_SCANOUT_QUALIFIED_QUIESCENT",
        "MICRONUX_DISPLAY_STATE_REMOVING",
        "MICRONUX_NATIVE_DMA_DESCRIPTOR_CTRL_HI 0x40108840",
        "MICRONUX_NATIVE_DMA_DESCRIPTOR_ARMED 0xc0108840",
        "DW_GDMA_INT_STATUS0_RO_MASK BIT(25)",
        "DW_GDMA_INT_ECC_ERROR_MASK GENMASK(3, 0)",
        "DW_GDMA_INT_COMMON_RO_MASK 0x001ffe80",
        "bool cold_gdma_hp_mutated;",
        "state=CONFIGURED controller=enabled irq-master=masked",
        "The global IRQ master stays masked across this final clean snapshot.",
        "From this point every new source is delivered; never clear it blindly.",
        "initial_generation = READ_ONCE(dsi->source_generation)",
        "false, dsi->source_generation",
        "A completed one-shot can expose the exact end before ISR rearm.",
        "sar >= start && sar <= start + dsi->handoff.framebuffer_size",
        "Stop an immutable inactive-channel level source from retriggering.",
        "writel(1, dsi->gdma + DW_GDMA_CFG)",
        "Drain a renderer that passed the gate before it was closed.",
        "A drained renderer may have queued this work before releasing lock.",
        "HP clock/reset containment is safe even if GDMA was inaccessible.",
        "state=SCANOUT_QUALIFIED_QUIESCENT fb=fb%d registered=yes",
        "source=black scanout=advancing windows=2 frames=8 buffers=3",
        "ctrl-hi=c0108840 host=video frame-bta=enabled lp=all",
        "boot-ready=0 render-gate=open backlight=unregistered touch=unregistered",
    )
    for token in required_scanout_patch:
        if token not in scanout_added_compact:
            raise AssertionError(f"scanout-patch-binding-missing:{token}")

    baseline = scanout_added_compact.index(
        "WRITE_ONCE(dsi->fault_bits, 0)"
    )
    configure_gdma = scanout_added_compact.index(
        "ret = micronux_native_configure_gdma(dsi)"
    )
    masked_snapshot = scanout_added_compact.index(
        "The global IRQ master stays masked across this final clean snapshot."
    )
    irq_enable = scanout_added_compact.index(
        "writel(3, dsi->gdma + DW_GDMA_CFG)", masked_snapshot
    )
    arm_frame = scanout_added_compact.index(
        "ret = micronux_native_dma_arm_frame(dsi, dsi->front_index)",
        irq_enable,
    )
    if not (
        baseline < configure_gdma and
        masked_snapshot < irq_enable < arm_frame
    ):
        raise AssertionError("scanout-patch-irq-boundary-order")

    register_fb = scanout_added_compact.index(
        "ret = micronux_register_framebuffer(dsi)"
    )
    final_health = scanout_added_compact.index(
        "if (!micronux_native_final_status_clean(dsi))", register_fb
    )
    publish_ready = scanout_added_compact.index(
        "WRITE_ONCE(dsi->scanout_ready, true)", final_health
    )
    publish_state = scanout_added_compact.index(
        "MICRONUX_DISPLAY_STATE_SCANOUT_QUALIFIED_QUIESCENT", publish_ready
    )
    if not (register_fb < final_health < publish_ready < publish_state):
        raise AssertionError("scanout-patch-publication-order")

    gate_close = scanout_added_compact.index(
        "WRITE_ONCE(dsi->render_enabled, false)"
    )
    render_drain = scanout_added_compact.index(
        "mutex_lock(&dsi->render_lock)", gate_close
    )
    render_cancel = scanout_added_compact.index(
        "cancel_delayed_work_sync(&dsi->render_work)", render_drain
    )
    if not (gate_close < render_drain < render_cancel):
        raise AssertionError("scanout-patch-render-lifetime-order")

    remove_hunk = (
        "+\t\tmutex_lock(&dsi->mode_lock);\n"
        " \t\tstate = READ_ONCE(dsi->display_state);\n"
        "+\t\tWRITE_ONCE(dsi->display_state,\n"
        "+\t\t\t   MICRONUX_DISPLAY_STATE_REMOVING);"
    )
    if remove_hunk not in scanout_patch_text:
        raise AssertionError("scanout-patch-remove-state-order")

    stage_e_check(
        "Subject: [PATCH 34/46] video: fbdev: reveal native DSI scanout safely"
        in reveal_patch_text,
        "reveal-patch-subject",
    )
    required_reveal_patch = (
        "MICRONUX_FAULT_BACKLIGHT_CONTROL 5",
        "MICRONUX_DISPLAY_STATE_REVEALING",
        "MICRONUX_DISPLAY_STATE_RUNTIME_REVEALED",
        "MICRONUX_NATIVE_I2C_RELEASED",
        "MICRONUX_NATIVE_I2C_TRANSITION",
        "MICRONUX_NATIVE_I2C_ACTIVE_SERIALIZED",
        "wait_queue_head_t frame_wait;",
        "u64 buffer_commit_sequence[MICRONUX_DISPLAY_BUFFER_COUNT];",
        "u64 front_commit_sequence;",
        "u64 presented_commit_sequence;",
        "u64 dark_baseline_commit_sequence;",
        "bool boot_ready_consumed;",
        "bool boot_ready_file_created;",
        "bool native_runtime_group_created;",
        "bool native_backlight_registered;",
        "bool touch_registration_complete;",
        "bool native_preserve_source;",
        "bool native_dark_confirmed;",
        "bool native_reveal_may_be_lit;",
        "bool native_source_rearm_unverified;",
        "static bool micronux_native_runtime_policy_valid",
        "MICRONUX:M9.2:REVEAL state=REVEALING trigger=boot_ready",
        "MICRONUX:M9.2:REVEAL state=QUALIFIED source=userspace-status",
        "MICRONUX:M9.2:REVEAL stage=control state=ACKED command=0x17 pwm=0",
        "MICRONUX:M9.2:REVEAL state=RUNTIME_REVEALED boot-ready=1",
        "linux abi=3 mode=native-cold-init fb%d buffers=3 dma-channel=0 "
        "frame-irq=%d rearm=explicit mmap=denied i2c=active-serialized",
        "%s abi=3 state=%s frames=%u->%u error=%08x underruns=%u "
        "chen=%u faults=%lx host-errors=%08x:%08x frame-ack=on "
        "clock=auto lp=enabled backlight-gate=%s buffers=3",
        "abi=3 state=%s frames=%u faults=%lx error=%08x "
        "host-errors=%08x:%08x underruns=%u buffers=3",
        "policy=%s i2c=active-serialized physical-panel-state=unobserved",
        'return sysfs_emit(buf, "registering\\n")',
        "MICRONUX:M9.2:TOUCH state=ready product=%s address=0x%02x",
        "MICRONUX:M9.2:TOUCH state=unavailable error=%d mode=poll "
        "display-state=RUNTIME_REVEALED",
        "MICRONUX:M9.2:REVEAL state=FAILED_UNVERIFIED",
        "source=preserved scanout=%s darkness=not-confirmed",
        "source=stopped dsi-gdma=%s i2c0=%s ldo3-xpd=%s darkness=%s",
    )
    for token in required_reveal_patch:
        stage_e_check(
            token in reveal_added_compact,
            f"reveal-patch-binding-missing:{token}",
        )

    native_irq_check(
        native_irq_patch.name ==
        "0035-video-fbdev-validate-native-CLIC-hardware-IRQ.patch",
        "native-irq-patch-path",
    )
    native_irq_check(
        "Subject: [PATCH 35/46] video: fbdev: validate native CLIC hardware IRQ"
        in native_irq_patch_text,
        "native-irq-patch-subject",
    )
    required_native_irq_patch = (
        "#include <linux/irq.h>",
        "struct irq_data *irq_data;",
        "irq_data = irq_get_irq_data(dsi->irq)",
        'micronux_cold_reject(dsi, "gdma-linux-irq-data")',
        "irqd_to_hwirq(irq_data) != handoff->gdma_clic_irq",
        "state=mismatch linux-virq=%d hwirq=%lu expected=%u "
        "display-writes=0",
        "dsi->irq, (unsigned long)irqd_to_hwirq(irq_data)",
        'micronux_cold_reject(dsi, "gdma-linux-hwirq")',
    )
    for token in required_native_irq_patch:
        native_irq_check(
            token in native_irq_added_compact,
            f"native-irq-patch-binding-missing:{token}",
        )
    raw_irq_comparison = "handoff->gdma_clic_irq != dsi->irq"
    native_irq_check(
        f"-\t    {raw_irq_comparison} ||" in native_irq_patch_text,
        "native-irq-raw-virq-comparison-not-deleted",
    )
    native_irq_check(
        raw_irq_comparison not in native_irq_postimage_compact,
        "native-irq-raw-virq-comparison-retained",
    )
    native_irq_check(
        not any(
            token in native_irq_added_compact
            for token in ("writel(", "writeb(", "memcpy_toio(")
        ),
        "native-irq-diagnostic-mutates-hardware",
    )

    native_diagnostic_check(
        native_diagnostic_patch.name ==
        "0036-video-fbdev-diagnose-native-GDMA-readback.patch",
        "native-diagnostic-patch-path",
    )
    native_diagnostic_check(
        "Subject: [PATCH 36/46] video: fbdev: diagnose native GDMA readback"
        in native_diagnostic_patch_text,
        "native-diagnostic-patch-subject",
    )
    native_diagnostic_check(
        "index f0543ad..8e16257 100644" in native_diagnostic_patch_text,
        "native-diagnostic-patch-order-after-irq",
    )
    native_diagnostic_check(
        "drivers/video/fbdev/esp32p4-dsi.c |" in
        native_diagnostic_patch_text and
        native_diagnostic_patch_text.count("diff --git ") == 1,
        "native-diagnostic-patch-scope",
    )
    required_hp_readback = (
        "clocks0 = readl(dsi->hp_clkrst + HP_CLKRST_SOC_CLK_CTRL0);",
        "clocks1 = readl(dsi->hp_clkrst + HP_CLKRST_SOC_CLK_CTRL1);",
        "reset = readl(dsi->hp_clkrst + HP_CLKRST_HP_RST_EN0);",
    )
    for token in required_hp_readback:
        native_diagnostic_check(
            native_diagnostic_added.count(token) == 1,
            f"native-diagnostic-hp-read-count:{token}",
        )
    required_hp_predicates = (
        "if (!(clocks0 & HP_CLKRST_GDMA_CPU_CLK_EN) ||",
        "!(clocks1 & HP_CLKRST_GDMA_SYS_CLK_EN) ||",
        "(reset & HP_CLKRST_GDMA_RESET)) {",
    )
    for token in required_hp_predicates:
        native_diagnostic_check(
            token in native_diagnostic_added_compact,
            f"native-diagnostic-hp-predicate:{token}",
        )
    hp_diagnostic_order = (
        *required_hp_readback,
        *required_hp_predicates,
        'reason=hp-clock-reset-readback clk0=%08x clk1=%08x rst=%08x '
        'expected-clk0-set=%08x expected-clk1-set=%08x '
        'rst-clear-mask=%08x',
        "clocks0, clocks1, reset,",
        "(u32)HP_CLKRST_GDMA_CPU_CLK_EN,",
        "(u32)HP_CLKRST_GDMA_SYS_CLK_EN,",
        "(u32)HP_CLKRST_GDMA_RESET);",
    )
    position = -1
    for token in hp_diagnostic_order:
        position = native_diagnostic_added_compact.find(token, position + 1)
        native_diagnostic_check(
            position >= 0,
            f"native-diagnostic-hp-order:{token}",
        )
    hp_log = native_diagnostic_postimage_compact.find(
        "reason=hp-clock-reset-readback"
    )
    hp_eio = native_diagnostic_postimage_compact.find("return -EIO;", hp_log)
    native_diagnostic_check(
        hp_log >= 0 and hp_eio > hp_log,
        "native-diagnostic-hp-same-eio",
    )
    request_irq_diagnostic = (
        "ret = devm_request_irq(dsi->dev, dsi->irq, micronux_dma_irq, 0,",
        "if (ret) {",
        "reason=request-irq error=%d linux-virq=%d hwirq=%lu",
        "ret, dsi->irq,",
        "(unsigned long)dsi->cold_handoff.gdma_clic_irq);",
        "return ret;",
    )
    position = -1
    for token in request_irq_diagnostic:
        position = native_diagnostic_postimage_compact.find(
            token, position + 1
        )
        native_diagnostic_check(
            position >= 0,
            f"native-diagnostic-request-irq-order:{token}",
        )
    gdma_readbacks = (
        ("cfg", "dsi->gdma + DW_GDMA_CFG"),
        ("cfg_lo", "dsi->channel + DW_GDMA_CH_CFG_LO"),
        ("cfg_hi", "dsi->channel + DW_GDMA_CH_CFG_HI"),
        ("st_ena0", "dsi->channel + DW_GDMA_CH_INT_STATUS_ENA"),
        ("st_ena1", "dsi->channel + DW_GDMA_CH_INT_STATUS_ENA1"),
        ("sig_ena0", "dsi->channel + DW_GDMA_CH_INT_SIGNAL_ENA"),
        ("sig_ena1", "dsi->channel + DW_GDMA_CH_INT_SIGNAL_ENA1"),
        ("common_st_ena", "dsi->gdma + DW_GDMA_INT_STATUS_ENA"),
        ("common_sig_ena", "dsi->gdma + DW_GDMA_INT_SIGNAL_ENA"),
        ("chen", "dsi->gdma + DW_GDMA_CHEN"),
    )
    for local, register in gdma_readbacks:
        token = f"{local} = readl({register});"
        native_diagnostic_check(
            native_diagnostic_added.count(token) == 1,
            f"native-diagnostic-gdma-read-count:{local}",
        )
    required_gdma_predicates = (
        "if (cfg != 1 ||",
        "cfg_lo != MICRONUX_DMA_CHANNEL_CFG_LO ||",
        "cfg_hi != MICRONUX_DMA_CHANNEL_CFG_HI ||",
        "st_ena0 != (DW_GDMA_INT_NATIVE_SCANOUT_MASK | "
        "DW_GDMA_INT_STATUS0_RO_MASK) ||",
        "st_ena1 != DW_GDMA_INT_ECC_ERROR_MASK ||",
        "sig_ena0 != (DW_GDMA_INT_NATIVE_SCANOUT_MASK | "
        "DW_GDMA_INT_STATUS0_RO_MASK) ||",
        "sig_ena1 != DW_GDMA_INT_ECC_ERROR_MASK ||",
        "common_st_ena != DW_GDMA_INT_COMMON_VALID_MASK ||",
        "common_sig_ena != DW_GDMA_INT_COMMON_VALID_MASK ||",
        "(chen & GENMASK(3, 0))) {",
    )
    for token in required_gdma_predicates:
        native_diagnostic_check(
            token in native_diagnostic_added_compact,
            f"native-diagnostic-gdma-predicate:{token}",
        )
    gdma_log_tokens = (
        "reason=register-readback cfg=%08x cfglo=%08x cfghi=%08x "
        "stena0=%08x stena1=%08x sigena0=%08x sigena1=%08x "
        "common-stena=%08x common-sigena=%08x chen=%08x",
        "expected=00000001:0000000f:0a020001:023f7fe2:0000000f:"
        "023f7fe2:0000000f:001fff8f:001fff8f:chen[3:0]=0",
        "cfg, cfg_lo, cfg_hi, st_ena0, st_ena1, sig_ena0,",
        "sig_ena1, common_st_ena, common_sig_ena, chen);",
    )
    gdma_first_read = native_diagnostic_added_compact.find(
        "cfg = readl(dsi->gdma + DW_GDMA_CFG);"
    )
    position = gdma_first_read
    native_diagnostic_check(
        gdma_first_read >= 0,
        "native-diagnostic-gdma-order-start",
    )
    for token in (*required_gdma_predicates, *gdma_log_tokens):
        position = native_diagnostic_added_compact.find(token, position + 1)
        native_diagnostic_check(
            position >= 0,
            f"native-diagnostic-gdma-order:{token}",
        )
    gdma_log = native_diagnostic_postimage_compact.find(
        "reason=register-readback"
    )
    gdma_eio = native_diagnostic_postimage_compact.find(
        "return -EIO;", gdma_log
    )
    native_diagnostic_check(
        gdma_log >= 0 and gdma_eio > gdma_log,
        "native-diagnostic-gdma-same-eio",
    )
    native_diagnostic_check(
        all(
            token in native_diagnostic_removed_compact
            for token in (
                "readl(dsi->gdma + DW_GDMA_CFG) != 1 ||",
                "readl(dsi->channel + DW_GDMA_CH_CFG_LO) != "
                "MICRONUX_DMA_CHANNEL_CFG_LO ||",
                "readl(dsi->channel + DW_GDMA_CH_CFG_HI) != "
                "MICRONUX_DMA_CHANNEL_CFG_HI ||",
                "readl(dsi->gdma + DW_GDMA_INT_STATUS_ENA) != "
                "DW_GDMA_INT_COMMON_VALID_MASK ||",
                "readl(dsi->gdma + DW_GDMA_INT_SIGNAL_ENA) != "
                "DW_GDMA_INT_COMMON_VALID_MASK ||",
                "(readl(dsi->gdma + DW_GDMA_CHEN) & GENMASK(3, 0))",
            )
        ),
        "native-diagnostic-original-policy-not-replaced",
    )
    native_diagnostic_check(
        not any(
            token in text
            for text in (
                native_diagnostic_added_compact,
                native_diagnostic_removed_compact,
            )
            for token in (
                "writel(", "writeb(", "memcpy_toio(", "#define ",
                "DW_GDMA_INT_NATIVE_SCANOUT_MASK =",
                "DW_GDMA_INT_STATUS0_RO_MASK =",
                "DW_GDMA_INT_COMMON_VALID_MASK =",
            )
        ),
        "native-diagnostic-hardware-policy-change",
    )

    native_gdma_check(
        native_gdma_patch.name ==
        "0037-video-fbdev-validate-GDMA-enable-semantics.patch",
        "native-gdma-patch-path",
    )
    native_gdma_check(
        "Subject: [PATCH 37/46] video: fbdev: validate GDMA enable semantics"
        in native_gdma_patch_text,
        "native-gdma-patch-subject",
    )
    native_gdma_check(
        "index 8e16257..520b502 100644" in native_gdma_patch_text,
        "native-gdma-patch-order-after-diagnostic",
    )
    native_gdma_check(
        "drivers/video/fbdev/esp32p4-dsi.c |" in native_gdma_patch_text and
        native_gdma_patch_text.count("diff --git ") == 1,
        "native-gdma-patch-scope",
    )
    required_defined_masks = (
        "#define DW_GDMA_INT_STATUS0_DEFINED_MASK \\ "
        "(GENMASK(1, 0) | GENMASK(14, 3) | GENMASK(21, 16) | BIT(25) | "
        "\\ GENMASK(31, 27))",
        "#define DW_GDMA_INT_STATUS1_DEFINED_MASK GENMASK(3, 0)",
        "#define DW_GDMA_INT_COMMON_DEFINED_MASK "
        "DW_GDMA_INT_COMMON_VALID_MASK",
    )
    for token in required_defined_masks:
        native_gdma_check(
            token in native_gdma_added_compact,
            f"native-gdma-defined-mask:{token}",
        )
    native_gdma_check(
        "P4 rev 1.3 uses IDF's hw_ver1 view; compare every documented bit."
        in native_gdma_added_compact and
        "Undefined positions are reserved read-as-one fields on this silicon."
        in native_gdma_added_compact,
        "native-gdma-hw-ver1-rationale",
    )
    for token, expected_count in (
        ("DW_GDMA_INT_STATUS0_DEFINED_MASK", 7),
        ("DW_GDMA_INT_STATUS1_DEFINED_MASK", 7),
        ("DW_GDMA_INT_COMMON_DEFINED_MASK", 7),
    ):
        native_gdma_check(
            native_gdma_added.count(token) == expected_count,
            f"native-gdma-defined-mask-count:{token}",
        )

    configure_start = native_gdma_patch_text.index(
        "@@ -5437,14 +5444,20 @@ static int micronux_native_configure_gdma"
    )
    runtime_start = native_gdma_patch_text.index(
        "@@ -5519,7 +5532,8 @@ static bool micronux_native_i2c_policy_valid",
        configure_start,
    )
    teardown_start = native_gdma_patch_text.index(
        "@@ -5767,7 +5787,8 @@ micronux_native_requalify_front",
        runtime_start,
    )
    native_gdma_check(
        configure_start < runtime_start < teardown_start,
        "native-gdma-function-order",
    )

    def patch_postimage_compact(section: str) -> str:
        postimage = "\n".join(
            line[1:] for line in section.splitlines()
            if line.startswith((" ", "+")) and not line.startswith("+++")
        )
        return " ".join(postimage.split())

    configure_semantics = patch_postimage_compact(
        native_gdma_patch_text[configure_start:runtime_start]
    )
    runtime_semantics = patch_postimage_compact(
        native_gdma_patch_text[runtime_start:teardown_start]
    )
    teardown_semantics = patch_postimage_compact(
        native_gdma_patch_text[teardown_start:]
    )
    active_semantic_predicates = (
        "(st_ena0 & DW_GDMA_INT_STATUS0_DEFINED_MASK) != "
        "(DW_GDMA_INT_NATIVE_SCANOUT_MASK | DW_GDMA_INT_STATUS0_RO_MASK) ||",
        "(st_ena1 & DW_GDMA_INT_STATUS1_DEFINED_MASK) != "
        "DW_GDMA_INT_ECC_ERROR_MASK ||",
        "(sig_ena0 & DW_GDMA_INT_STATUS0_DEFINED_MASK) != "
        "(DW_GDMA_INT_NATIVE_SCANOUT_MASK | DW_GDMA_INT_STATUS0_RO_MASK) ||",
        "(sig_ena1 & DW_GDMA_INT_STATUS1_DEFINED_MASK) != "
        "DW_GDMA_INT_ECC_ERROR_MASK ||",
        "(common_st_ena & DW_GDMA_INT_COMMON_DEFINED_MASK) != "
        "DW_GDMA_INT_COMMON_VALID_MASK ||",
        "(common_sig_ena & DW_GDMA_INT_COMMON_DEFINED_MASK) != "
        "DW_GDMA_INT_COMMON_VALID_MASK ||",
    )
    runtime_semantic_predicates = tuple(
        token.replace(") != ", ") == ").removesuffix(" ||")
        + " &&"
        for token in active_semantic_predicates
    )
    for label, section, predicates in (
        ("configure", configure_semantics, active_semantic_predicates),
        ("runtime", runtime_semantics, runtime_semantic_predicates),
    ):
        for token in predicates:
            native_gdma_check(
                section.count(token) == 1,
                f"native-gdma-{label}-predicate:{token}",
            )
    teardown_semantic_predicates = (
        "(st_ena0 & DW_GDMA_INT_STATUS0_DEFINED_MASK) == "
        "DW_GDMA_INT_STATUS0_RO_MASK &&",
        "(st_ena1 & DW_GDMA_INT_STATUS1_DEFINED_MASK) == "
        "DW_GDMA_INT_ECC_ERROR_MASK &&",
        "(sig_ena0 & DW_GDMA_INT_STATUS0_DEFINED_MASK) == "
        "DW_GDMA_INT_STATUS0_RO_MASK &&",
        "(sig_ena1 & DW_GDMA_INT_STATUS1_DEFINED_MASK) == "
        "DW_GDMA_INT_ECC_ERROR_MASK &&",
        "(common_st_ena & DW_GDMA_INT_COMMON_DEFINED_MASK) == "
        "DW_GDMA_INT_COMMON_RO_MASK &&",
        "(common_sig_ena & DW_GDMA_INT_COMMON_DEFINED_MASK) == "
        "DW_GDMA_INT_COMMON_RO_MASK &&",
    )
    for token in teardown_semantic_predicates:
        native_gdma_check(
            teardown_semantics.count(token) == 1,
            f"native-gdma-teardown-predicate:{token}",
        )
    for label, section in (
        ("runtime", runtime_semantics),
        ("teardown", teardown_semantics),
    ):
        for local, register in gdma_readbacks[3:9]:
            token = f"{local} = readl({register});"
            native_gdma_check(
                section.count(token) == 1,
                f"native-gdma-{label}-read-count:{local}",
            )
    native_gdma_check(
        all(
            token in native_gdma_removed_compact
            for token in required_gdma_predicates[3:9]
        ),
        "native-gdma-patch36-exact-predicates-not-replaced",
    )
    native_gdma_check(
        not any(
            token in native_gdma_postimage_compact
            for token in (
                "st_ena0 != (DW_GDMA_INT_NATIVE_SCANOUT_MASK |",
                "common_st_ena != DW_GDMA_INT_COMMON_VALID_MASK",
                "readl(dsi->channel + DW_GDMA_CH_INT_STATUS_ENA) ==",
                "readl(dsi->gdma + DW_GDMA_INT_STATUS_ENA) ==",
            )
        ),
        "native-gdma-unmasked-equality-retained",
    )
    native_gdma_check(
        not any(
            token in text
            for text in (native_gdma_added_compact, native_gdma_removed_compact)
            for token in ("writel(", "writeb(", "memcpy_toio(")
        ),
        "native-gdma-write-policy-change",
    )
    native_gdma_check(
        not any(
            token in text
            for text in (native_gdma_added_compact, native_gdma_removed_compact)
            for token in (
                "reason=hp-clock-reset-readback",
                "reason=request-irq",
                "reason=register-readback",
                "expected=00000001:0000000f:0a020001:023f7fe2",
            )
        ) and
        "reason=register-readback" in native_gdma_postimage_compact and
        "expected=00000001:0000000f:0a020001:023f7fe2:0000000f:"
        "023f7fe2:0000000f:001fff8f:001fff8f:chen[3:0]=0"
        in native_gdma_postimage_compact,
        "native-gdma-diagnostic-change",
    )

    native_scanout_check(
        native_scanout_patch.name ==
        "0038-video-fbdev-diagnose-native-scanout-qualification.patch",
        "patch-path",
    )
    native_scanout_check(
        "From 397b8bed56b6165251011f0109ded884d1bd0fe2 "
        "Mon Sep 17 00:00:00 2001" in native_scanout_patch_text,
        "patch-commit",
    )
    native_scanout_check(
        "Subject: [PATCH 38/46] video: fbdev: diagnose native scanout "
        "qualification" in native_scanout_patch_text,
        "patch-subject",
    )
    native_scanout_check(
        "index 520b502..9773fdc 100644" in native_scanout_patch_text,
        "patch-order-after-gdma-semantics",
    )
    native_scanout_check(
        "drivers/video/fbdev/esp32p4-dsi.c |" in
        native_scanout_patch_text and
        native_scanout_patch_text.count("diff --git ") == 1,
        "patch-scope",
    )
    i2c_policy_hunk = native_scanout_patch_text[
        native_scanout_patch_text.index(
            "@@ -5508,8 +5576,10 @@ static bool "
            "micronux_native_i2c_policy_valid"
        ):
        native_scanout_patch_text.index(
            "@@ -5636,39 +5706,138 @@ static bool "
            "micronux_native_runtime_policy_valid"
        )
    ]
    i2c_policy_added = " ".join(
        line[1:] for line in i2c_policy_hunk.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ).split()
    i2c_policy_removed = " ".join(
        line[1:] for line in i2c_policy_hunk.splitlines()
        if line.startswith("-") and not line.startswith("---")
    ).split()
    i2c_policy_added_compact = " ".join(i2c_policy_added)
    i2c_policy_removed_compact = " ".join(i2c_policy_removed)
    native_scanout_check(
        i2c_policy_removed_compact ==
        "if (display_state == "
        "MICRONUX_DISPLAY_STATE_SCANOUT_QUALIFIED_QUIESCENT)",
        "qualified-old-condition",
    )
    native_scanout_check(
        i2c_policy_added_compact ==
        "/* Dark qualification intentionally keeps the external bus "
        "released. */ if (display_state == "
        "MICRONUX_DISPLAY_STATE_SCANOUT_INITIALIZING || display_state == "
        "MICRONUX_DISPLAY_STATE_SCANOUT_QUALIFIED_QUIESCENT)",
        "initializing-policy-only-delta",
    )
    i2c_policy_postimage = patch_postimage_compact(i2c_policy_hunk)
    native_scanout_require_order(
        i2c_policy_postimage,
        (
            "i2c_state = READ_ONCE(dsi->native_i2c_state)",
            "display_state == MICRONUX_DISPLAY_STATE_SCANOUT_INITIALIZING",
            "MICRONUX_DISPLAY_STATE_SCANOUT_QUALIFIED_QUIESCENT",
            "i2c_state == MICRONUX_NATIVE_I2C_RELEASED",
            "micronux_cold_i2c_released(dsi)",
            "display_state == MICRONUX_DISPLAY_STATE_REVEALING",
        ),
        "i2c-policy",
    )

    def dark_i2c_policy(state: str, i2c_state: str, released: bool) -> bool:
        return (
            state in ("SCANOUT_INITIALIZING", "SCANOUT_QUALIFIED_QUIESCENT")
            and i2c_state == "RELEASED"
            and released
        )

    for display_state in (
        "SCANOUT_INITIALIZING",
        "SCANOUT_QUALIFIED_QUIESCENT",
    ):
        for i2c_state in ("RELEASED", "TRANSITION", "ACTIVE_SERIALIZED"):
            for physically_released in (False, True):
                expected = i2c_state == "RELEASED" and physically_released
                native_scanout_check(
                    dark_i2c_policy(
                        display_state, i2c_state, physically_released
                    ) == expected,
                    "i2c-truth-table:"
                    f"{display_state}:{i2c_state}:{physically_released}",
                )

    snapshot_fields = (
        "host_raw0", "host_raw1", "host_sticky0", "host_sticky1",
        "bridge_raw", "bridge_underruns", "gdma_top", "gdma_status0",
        "gdma_status1", "gdma_common", "gdma_cfg", "gdma_chen",
        "gdma_cfg_lo", "gdma_cfg_hi", "gdma_llp", "gdma_sar",
        "descriptor_ctrl_hi", "generation", "frames", "last_dma_error",
        "rearm_attempts", "rearm_failures", "host_mode", "host_vid",
        "host_active", "host_lpclk", "fault_bits", "host_status_seen",
        "guards_valid", "runtime_policy_valid",
    )
    snapshot_struct = native_scanout_block(
        native_scanout_added_compact,
        "struct micronux_scanout_snapshot",
        "static void micronux_capture_scanout_snapshot",
        "snapshot-struct",
    )
    for field in snapshot_fields:
        native_scanout_check(
            len(re.findall(rf"\b{re.escape(field)}\b", snapshot_struct)) == 1,
            f"snapshot-field:{field}",
        )

    capture_snapshot = native_scanout_block(
        native_scanout_added_compact,
        "micronux_capture_scanout_snapshot(struct micronux_dsi *dsi, "
        "struct micronux_scanout_snapshot *snapshot, u8 index, "
        "u32 ignored_status1) {",
        "micronux_log_scanout_failure(struct micronux_dsi *dsi, "
        "const char *reason, const char *detail, int error, int window, "
        "int poll_error, u32 initial_generation, const struct "
        "micronux_scanout_snapshot *snapshot) {",
        "snapshot-capture",
    )
    native_scanout_check(
        capture_snapshot.count("micronux_sample_host_status_raw(") == 1,
        "host-raw-one-sample-per-snapshot",
    )
    native_scanout_require_order(
        capture_snapshot,
        (
            "memset(snapshot, 0, sizeof(*snapshot))",
            "snapshot->host_status_seen = "
            "micronux_sample_host_status_raw(dsi, ignored_status1, "
            "&snapshot->host_raw0, &snapshot->host_raw1)",
            "snapshot->host_sticky0 = READ_ONCE(dsi->host_error0)",
            "snapshot->host_sticky1 = READ_ONCE(dsi->host_error1)",
            "snapshot->runtime_policy_valid = "
            "micronux_native_runtime_policy_valid(dsi)",
        ),
        "snapshot-capture",
    )

    primary_diagnostic = (
        "MICRONUX:M9.2:COLD-INIT stage=scanout-qualification reason=%s "
        "detail=%s error=%d window=%d poll-error=%d "
        "initial-generation=%u generation=%u frames=%u faults=%lx "
        "dma-error=%08x rearm=%u/%u guards=%u runtime-policy=%u\\n"
    )
    register_diagnostic = (
        "MICRONUX:M9.2:COLD-INIT stage=scanout-qualification reason=%s "
        "snapshot=host raw=%08x:%08x sticky=%08x:%08x seen=%u "
        "mode=%08x vid=%08x active=%08x lpclk=%08x bridge-raw=%08x "
        "underruns=%u gdma=%08x:%08x:%08x:%08x cfg=%08x chen=%08x "
        "cfglo=%08x cfghi=%08x llp=%08x sar=%08x ctrlhi=%08x\\n"
    )
    for label, diagnostic in (
        ("summary", primary_diagnostic),
        ("registers", register_diagnostic),
    ):
        native_scanout_check(
            native_scanout_added_compact.count(diagnostic) == 1,
            f"diagnostic-format:{label}",
        )
    log_snapshot = native_scanout_block(
        native_scanout_added_compact,
        "micronux_log_scanout_failure(struct micronux_dsi *dsi, "
        "const char *reason, const char *detail, int error, int window, "
        "int poll_error, u32 initial_generation, const struct "
        "micronux_scanout_snapshot *snapshot) {",
        "struct micronux_scanout_snapshot snapshot",
        "failure-log",
    )
    native_scanout_require_order(
        log_snapshot,
        (
            primary_diagnostic,
            "reason, detail, error, window, poll_error, initial_generation",
            "snapshot->generation, snapshot->frames, snapshot->fault_bits",
            "snapshot->last_dma_error, snapshot->rearm_attempts",
            "snapshot->rearm_failures, snapshot->guards_valid",
            "snapshot->runtime_policy_valid",
            register_diagnostic,
            "reason, snapshot->host_raw0, snapshot->host_raw1",
            "snapshot->host_sticky0, snapshot->host_sticky1",
            "snapshot->gdma_top, snapshot->gdma_status0",
            "snapshot->gdma_status1, snapshot->gdma_common",
            "snapshot->gdma_llp, snapshot->gdma_sar",
            "snapshot->descriptor_ctrl_hi",
        ),
        "failure-log-fields",
    )

    phase_pairs = tuple(re.findall(
        r'micronux_log_scanout_failure\(dsi, "([^"]+)", "([^"]+)"',
        native_scanout_added_compact,
    ))
    expected_phase_pairs = (
        ("initial-precheck", "global-master-masked"),
        ("initial-precheck", "latched-status"),
        ("global-enable", "master-or-latched-fault"),
        ("arm-readback", "descriptor-channel-readback"),
        ("qualification-window", "window-health"),
    )
    native_scanout_check(
        phase_pairs == expected_phase_pairs,
        f"diagnostic-phase-details:{phase_pairs}",
    )
    native_scanout_check(
        {reason for reason, _ in phase_pairs} == {
            "initial-precheck", "global-enable", "arm-readback",
            "qualification-window",
        },
        "diagnostic-four-phases",
    )

    raw_sample = native_scanout_block(
        native_scanout_postimage_compact,
        "micronux_sample_host_status_raw(struct micronux_dsi *dsi",
        "static bool micronux_sample_host_status(struct micronux_dsi *dsi",
        "host-raw-sample",
    )
    native_scanout_check(
        raw_sample.count("readl(dsi->host + DSI_HOST_INT_STATUS1)") == 1,
        "host-raw-status1-register-read-count",
    )
    native_scanout_require_order(
        raw_sample,
        (
            "status1 = readl(dsi->host + DSI_HOST_INT_STATUS1)",
            "if (raw_status0) *raw_status0 = status0",
            "if (raw_status1) *raw_status1 = status1",
            "ignored1 = status1 & ignored_status1",
            "status1 &= ~ignored_status1",
        ),
        "host-raw-before-filter",
    )
    scanout_start_index = native_scanout_postimage_compact.find(
        "static int micronux_native_start_scanout"
    )
    native_scanout_check(
        scanout_start_index >= 0,
        "start-scanout-block-missing",
    )
    scanout_start = native_scanout_postimage_compact[scanout_start_index:]
    native_scanout_check(
        "micronux_sample_host_status(dsi" not in scanout_start and
        "micronux_sample_host_status_raw(dsi" not in scanout_start,
        "decision-bypasses-snapshot-host-sample",
    )
    native_scanout_require_order(
        scanout_start,
        (
            "micronux_capture_scanout_snapshot(dsi, &snapshot, "
            "dsi->front_index, 0)",
            "if (snapshot.gdma_cfg != 1)",
            'micronux_log_scanout_failure(dsi, "initial-precheck", '
            '"global-master-masked", -EIO',
            "return -EIO",
            "if (snapshot.host_status_seen || snapshot.host_sticky0",
            'micronux_log_scanout_failure(dsi, "initial-precheck", '
            '"latched-status", -EIO',
            "return -EIO",
            "writel(3, dsi->gdma + DW_GDMA_CFG)",
            "if (cfg != 3 || last_dma_error || faults)",
            'micronux_log_scanout_failure(dsi, "global-enable", '
            '"master-or-latched-fault", -EIO',
            "return -EIO",
            "ret = micronux_native_dma_arm_frame(dsi, dsi->front_index, "
            "&snapshot)",
            'micronux_log_scanout_failure(dsi, "arm-readback", '
            '"descriptor-channel-readback", ret',
            "return ret",
            "micronux_capture_scanout_snapshot(dsi, &snapshot, "
            "dsi->front_index, 0)",
            "if (ret || snapshot.host_status_seen || snapshot.host_sticky0",
            "error = ret ? ret : -EIO",
            'micronux_log_scanout_failure(dsi, "qualification-window", '
            '"window-health", error',
            "return error",
        ),
        "fail-dark-returns",
    )
    native_scanout_check(
        all(
            token not in text
            for text in (
                native_scanout_added_compact,
                native_scanout_removed_compact,
            )
            for token in ("writel(", "writeb(", "memcpy_toio(")
        ),
        "write-policy-change",
    )
    native_scanout_check(
        all(
            token not in text
            for text in (
                native_scanout_added_compact,
                native_scanout_removed_compact,
            )
            for token in (
                "micronux_cold_runtime_fail_quiescent",
                "micronux_native_fail_reveal_locked",
                "WRITE_ONCE(dsi->display_state",
                "WRITE_ONCE(dsi->native_dark_confirmed",
                "WRITE_ONCE(dsi->native_preserve_source",
            )
        ),
        "fail-dark-policy-change",
    )
    native_scanout_check(
        "return ret ? ret : -EIO" in native_scanout_removed_compact and
        "error = ret ? ret : -EIO" in native_scanout_added_compact and
        "return error" in native_scanout_added_compact,
        "qualification-error-preserved",
    )

    native_arm_check(
        native_arm_patch.name ==
        "0039-video-fbdev-validate-native-DMA-arm-transaction.patch",
        "patch-path",
    )
    native_arm_check(
        "From 99fcff145f84b22c8996e7cab8ce9a77abe472e7 "
        "Mon Sep 17 00:00:00 2001" in native_arm_patch_text,
        "patch-commit",
    )
    native_arm_check(
        "Subject: [PATCH 39/46] video: fbdev: validate native DMA arm "
        "transaction" in native_arm_patch_text,
        "patch-subject",
    )
    native_arm_check(
        "index 9773fdc..712066d 100644" in native_arm_patch_text,
        "patch-order-after-scanout-diagnostic",
    )
    native_arm_check(
        "drivers/video/fbdev/esp32p4-dsi.c |" in native_arm_patch_text and
        native_arm_patch_text.count("diff --git ") == 1,
        "patch-scope",
    )
    arm_hunk_scopes = re.findall(
        r"^@@ [^\n]* @@ ([^\n]+)$", native_arm_patch_text, re.MULTILINE
    )
    native_arm_check(
        len(arm_hunk_scopes) == 3 and all(
            scope.startswith("micronux_native_dma_arm_frame(")
            for scope in arm_hunk_scopes
        ),
        f"helper-only-hunks:{arm_hunk_scopes}",
    )
    for stage_build in stage_builds:
        native_policy_check(
            'NATIVE_DISPLAY_DRIVER_SHA256="b9a188dd12066b2c596b7223f593d734'
            'e43aebc12e4639fb6a715e6c845bdabe"' in
            stage_build.read_text(encoding="utf-8"),
            f"post-source-sha256:{stage_build.name}",
        )

    baseline_arm_start = scanout_added_compact.index(
        "static int micronux_native_dma_arm_frame"
    )
    baseline_arm_end = scanout_added_compact.index(
        "static irqreturn_t micronux_native_dma_irq", baseline_arm_start
    )
    baseline_arm = scanout_added_compact[
        baseline_arm_start:baseline_arm_end
    ]
    stable_programming = (
        "writel(MICRONUX_NATIVE_DMA_DESCRIPTOR_ARMED, descriptor + "
        "DMA_LLI_CTRL_HI)",
        "writel(MICRONUX_DMA_CHANNEL_CFG_LO, dsi->channel + "
        "DW_GDMA_CH_CFG_LO)",
        "writel(MICRONUX_DMA_CHANNEL_CFG_HI, dsi->channel + "
        "DW_GDMA_CH_CFG_HI)",
        "writel(descriptor_address | DW_GDMA_LLP_MEMORY_PORT, "
        "dsi->channel + DW_GDMA_CH_LLP)",
        "writel(0, dsi->channel + DW_GDMA_CH_LLP + sizeof(u32))",
    )
    native_arm_require_order(
        baseline_arm,
        stable_programming + ("wmb()",),
        "baseline-programming",
    )
    for token in stable_programming:
        native_arm_check(
            token not in native_arm_removed_compact,
            f"stable-programming-removed:{token}",
        )

    channel_enable = (
        "writel(channel | (channel << 8), dsi->gdma + DW_GDMA_CHEN)"
    )
    native_arm_check(
        native_arm_postimage_compact.count(channel_enable) == 1,
        "single-channel-enable",
    )
    enable_index = native_arm_postimage_compact.index(channel_enable)
    pre_enable = native_arm_postimage_compact[:enable_index]
    post_enable = native_arm_postimage_compact[enable_index:]
    native_arm_require_order(
        pre_enable,
        (
            "WRITE_ONCE(dsi->rearm_attempts, "
            "READ_ONCE(dsi->rearm_attempts) + 1)",
            "chen = readl(dsi->gdma + DW_GDMA_CHEN)",
            "if (chen & channel)",
            "return -EIO",
            "writel(MICRONUX_NATIVE_DMA_DESCRIPTOR_ARMED, descriptor + "
            "DMA_LLI_CTRL_HI)",
            "writel(MICRONUX_DMA_CHANNEL_CFG_LO",
            "writel(descriptor_address | DW_GDMA_LLP_MEMORY_PORT, "
            "dsi->channel + DW_GDMA_CH_LLP)",
            "writel(0, dsi->channel + DW_GDMA_CH_LLP + sizeof(u32))",
            "/* Verify software-owned programming before GDMA can "
            "consume it. */",
            "wmb()",
            "chen = readl(dsi->gdma + DW_GDMA_CHEN)",
            "ctrl_hi = readl(descriptor + DMA_LLI_CTRL_HI)",
            "cfg_lo = readl(dsi->channel + DW_GDMA_CH_CFG_LO)",
            "cfg_hi = readl(dsi->channel + DW_GDMA_CH_CFG_HI)",
            "llp = readl(dsi->channel + DW_GDMA_CH_LLP)",
            "llp_hi = readl(dsi->channel + DW_GDMA_CH_LLP + sizeof(u32))",
            "status0 = readl(dsi->channel + DW_GDMA_CH_INT_STATUS)",
            "status1 = readl(dsi->channel + DW_GDMA_CH_INT_STATUS1)",
            "common = readl(dsi->gdma + DW_GDMA_INT_COMMON_STATUS)",
            "top = readl(dsi->gdma + DW_GDMA_INT_STATUS)",
        ),
        "pre-enable-readback",
    )
    pre_enable_guard = (
        "if ((chen & channel) || "
        "ctrl_hi != MICRONUX_NATIVE_DMA_DESCRIPTOR_ARMED || "
        "cfg_lo != MICRONUX_DMA_CHANNEL_CFG_LO || "
        "cfg_hi != MICRONUX_DMA_CHANNEL_CFG_HI || "
        "llp != (descriptor_address | DW_GDMA_LLP_MEMORY_PORT) || "
        "llp_hi || (status0 & DW_GDMA_INT_NATIVE_SCANOUT_MASK) || "
        "(status1 & DW_GDMA_INT_ECC_ERROR_MASK) || "
        "(common & DW_GDMA_INT_COMMON_VALID_MASK) || "
        "(top & ~DW_GDMA_INT_TOP_ALLOWED))"
    )
    native_arm_check(
        pre_enable.count(pre_enable_guard) == 1,
        "exact-pre-enable-guard",
    )
    native_arm_check(
        pre_enable.count("readl(") == 11,
        "exact-pre-enable-read-count",
    )
    native_arm_require_order(
        native_arm_postimage_compact,
        (
            pre_enable_guard,
            "/* The one-shot engine owns descriptor and LLP state after "
            "this write. */",
            channel_enable,
        ),
        "ownership-boundary",
    )

    post_enable_reads = (
        "chen = readl(dsi->gdma + DW_GDMA_CHEN)",
        "status0 = readl(dsi->channel + DW_GDMA_CH_INT_STATUS)",
        "status1 = readl(dsi->channel + DW_GDMA_CH_INT_STATUS1)",
        "common = readl(dsi->gdma + DW_GDMA_INT_COMMON_STATUS)",
        "top = readl(dsi->gdma + DW_GDMA_INT_STATUS)",
    )
    native_arm_require_order(
        post_enable,
        (channel_enable,) + post_enable_reads,
        "post-enable-readback",
    )
    native_arm_check(
        post_enable.count("readl(") == len(post_enable_reads),
        "post-enable-stable-read-count",
    )
    post_enable_guard = (
        "if (!(chen & channel) || (status0 & DW_GDMA_INT_ERROR_MASK) || "
        "(status1 & DW_GDMA_INT_ECC_ERROR_MASK) || "
        "(common & DW_GDMA_INT_COMMON_VALID_MASK) || "
        "(top & ~DW_GDMA_INT_TOP_ALLOWED))"
    )
    native_arm_check(
        post_enable.count(post_enable_guard) == 1,
        "exact-post-enable-guard",
    )
    for unstable in (
        "descriptor_address", "descriptor +", "DMA_LLI_", "ctrl_hi",
        "cfg_lo", "cfg_hi", "llp", "sar",
    ):
        native_arm_check(
            unstable not in post_enable,
            f"post-enable-hardware-owned-read:{unstable}",
        )

    failure_increment = (
        "WRITE_ONCE(dsi->rearm_failures, "
        "READ_ONCE(dsi->rearm_failures) + 1)"
    )
    initial_failure = pre_enable[
        pre_enable.index("if (chen & channel)"):
        pre_enable.index(
            "writel(MICRONUX_NATIVE_DMA_DESCRIPTOR_ARMED"
        )
    ]
    pre_readback_failure = pre_enable[
        pre_enable.index(pre_enable_guard):
    ]
    post_readback_failure = post_enable[
        post_enable.index(post_enable_guard):
        post_enable.index("return 0")
    ]
    native_arm_check(
        native_arm_postimage_compact.count(failure_increment) == 3,
        "three-failure-paths",
    )
    for label, failure_path in (
        ("initial-channel-active", initial_failure),
        ("pre-enable-readback", pre_readback_failure),
        ("post-enable-readback", post_readback_failure),
    ):
        native_arm_check(
            failure_path.count(failure_increment) == 1 and
            failure_path.count("return -EIO") == 1,
            f"single-failure-increment:{label}",
        )
    native_arm_check(
        native_arm_postimage_compact.count(
            "WRITE_ONCE(dsi->rearm_attempts, "
            "READ_ONCE(dsi->rearm_attempts) + 1)"
        ) == 1,
        "single-arm-attempt-increment",
    )

    native_arm_check(
        native_scanout_added_compact.count(
            "micronux_native_dma_arm_frame(dsi, next_index, NULL)"
        ) == 1 and
        native_scanout_added_compact.count(
            "micronux_native_dma_arm_frame(dsi, dsi->front_index, "
            "&snapshot)"
        ) == 1,
        "shared-initial-and-irq-helper",
    )
    changed_arm_text = (
        native_arm_added_compact + " " + native_arm_removed_compact
    )
    for forbidden in (
        "DSI_HOST_VID_PKT_STATUS", "host_active",
        "micronux_native_runtime_policy_valid",
        "micronux_native_start_scanout", "qualification-window",
        "micronux_cold_runtime_fail_quiescent",
        "micronux_native_fail_reveal_locked",
        "WRITE_ONCE(dsi->display_state", "native_dark_confirmed",
        "native_preserve_source", "scanout_ready", "render_enabled",
    ):
        native_arm_check(
            forbidden not in changed_arm_text,
            f"policy-mutation:{forbidden}",
        )
    for sleeping in (
        "msleep", "usleep", "mutex_", "readl_poll_timeout",
        "schedule_work", "schedule_delayed_work",
    ):
        native_arm_check(
            sleeping not in changed_arm_text,
            f"shared-helper-sleeping-operation:{sleeping}",
        )

    native_policy_check(
        native_policy_patch.name ==
        "0040-video-fbdev-stop-gating-scanout-on-inactive-DSI-mirror.patch",
        "patch-path",
    )
    native_policy_check(
        "From 0fac40ee31c1af165ea94a82a7b0d905d8da4861 "
        "Mon Sep 17 00:00:00 2001" in native_policy_patch_text,
        "patch-commit",
    )
    native_policy_check(
        "Subject: [PATCH 40/46] video: fbdev: stop gating scanout on inactive "
        "DSI\n mirror" in native_policy_patch_text,
        "patch-subject",
    )
    native_policy_check(
        "index 712066d..a8bff0a 100644" in native_policy_patch_text,
        "patch-order-after-arm-transaction",
    )
    native_policy_check(
        native_policy_patch_text.count("diff --git ") == 1 and
        len(re.findall(r"^@@ ", native_policy_patch_text, re.MULTILINE)) == 2 and
        "drivers/video/fbdev/esp32p4-dsi.c | 5 -----" in
        native_policy_patch_text and
        "1 file changed, 5 deletions(-)" in native_policy_patch_text,
        "patch-scope",
    )
    native_policy_check(
        not native_policy_added_lines,
        f"zero-source-additions:{native_policy_added_lines}",
    )
    expected_policy_deletions = (
        "#define DSI_HOST_NATIVE_VIDEO_POLICY_ACT \\",
        "\t(DSI_HOST_FRAME_BTA_ACK_EN_ACT | DSI_HOST_LP_VIDEO_EN_ACT_MASK | \\",
        "\t DSI_HOST_VID_MODE_BURST)",
        "\t       readl(dsi->host + DSI_HOST_VID_MODE_CFG_ACT) ==",
        "\t\tDSI_HOST_NATIVE_VIDEO_POLICY_ACT &&",
    )
    native_policy_check(
        native_policy_removed_lines == expected_policy_deletions,
        f"exact-five-source-deletions:{native_policy_removed_lines}",
    )
    native_policy_check(
        "ESP-IDF v6.0.1 programs the primary video-mode register without "
        "enabling the optional video shadow bank." in
        " ".join(native_policy_patch_text.split()),
        "idf-primary-register-shadow-disabled-evidence",
    )
    native_policy_check(
        "readl(dsi->host + DSI_HOST_VID_MODE_CFG) == "
        "DSI_HOST_NATIVE_VIDEO_POLICY && "
        "readl(dsi->host + DSI_HOST_LPCLK_CTRL) == "
        "DSI_HOST_CLOCK_LANE_AUTO" in native_policy_postimage_compact and
        "DSI_HOST_NATIVE_VIDEO_POLICY_ACT" not in
        native_policy_postimage_compact,
        "programmed-policy-retained-active-equality-removed",
    )

    runtime_policy_start = scanout_added_compact.index(
        "static bool micronux_native_runtime_policy_valid(struct "
        "micronux_dsi *dsi) {"
    )
    runtime_policy_end = scanout_added_compact.index(
        "static int micronux_native_start_scanout", runtime_policy_start
    )
    runtime_policy = scanout_added_compact[
        runtime_policy_start:runtime_policy_end
    ]
    return_start = runtime_policy.index("return ") + len("return ")
    return_end = runtime_policy.index(";", return_start)
    pre_patch_return = runtime_policy[return_start:return_end]
    pre_patch_predicates = tuple(
        predicate.strip()
        for predicate in pre_patch_return.split("&&")
    )
    active_mirror_predicate = (
        "readl(dsi->host + DSI_HOST_VID_MODE_CFG_ACT) == "
        "DSI_HOST_NATIVE_VIDEO_POLICY_ACT"
    )
    native_policy_check(
        len(pre_patch_predicates) == 42 and
        pre_patch_predicates.count(active_mirror_predicate) == 1,
        f"prepatch-policy-predicates:{len(pre_patch_predicates)}",
    )
    active_mirror_clause = active_mirror_predicate + " &&"
    native_policy_check(
        pre_patch_return.count(active_mirror_clause) == 1,
        "single-active-mirror-clause",
    )
    post_patch_return = pre_patch_return.replace(active_mirror_clause, "", 1)
    post_patch_predicates = tuple(
        predicate.strip() for predicate in post_patch_return.split("&&")
    )
    active_index = pre_patch_predicates.index(active_mirror_predicate)
    native_policy_check(
        len(post_patch_predicates) == 41 and
        post_patch_predicates == (
            pre_patch_predicates[:active_index] +
            pre_patch_predicates[active_index + 1:]
        ),
        f"postpatch-policy-predicates:{len(post_patch_predicates)}",
    )
    native_policy_check(
        "readl(dsi->host + DSI_HOST_VID_MODE_CFG) == "
        "DSI_HOST_NATIVE_VIDEO_POLICY" in post_patch_predicates and
        NATIVE_PROGRAMMED_VIDEO_POLICY == 0x0000FF02,
        "exact-programmed-ff02-policy",
    )

    scanout_patch_compact = " ".join(scanout_patch_text.split())
    for token in (
        "#define DSI_HOST_VID_MODE_CFG_ACT 0x138",
        "#define DSI_HOST_FRAME_BTA_ACK_EN_ACT BIT(8)",
        "#define DSI_HOST_LP_VIDEO_EN_ACT_MASK (GENMASK(7, 2) | BIT(9))",
    ):
        native_policy_check(
            token in scanout_patch_compact,
            f"active-diagnostic-component-retained:{token}",
        )
    for token in (
        "u32 host_active",
        "snapshot->host_active = readl(dsi->host + "
        "DSI_HOST_VID_MODE_CFG_ACT)",
        "snapshot->host_vid, snapshot->host_active",
    ):
        native_policy_check(
            token in native_scanout_added_compact,
            f"active-snapshot-diagnostic-retained:{token}",
        )
    native_policy_check(
        "vid_shadow_ctrl" not in loader_text and
        "vid_shadow_ctrl" not in idf_handoff_text and
        "mipi_dsi_host_ll_dpi_enable_frame_ack(host, false)" in loader_text and
        "hal->host->vid_mode_cfg.vpg_en" in idf_handoff_text,
        "idf-primary-video-policy-without-shadow-control",
    )
    changed_policy_text = " ".join(
        (*native_policy_added_lines, *native_policy_removed_lines)
    )
    for forbidden in (
        "writel(", "writeb(", "memcpy_toio(",
        "micronux_cold_runtime_teardown", "micronux_cold_dsi_disable",
        "micronux_cold_ldo_disable", "micronux_native_off_first",
        "micronux_native_boot_ready_store", "micronux_native_fail_reveal_locked",
        "RUNTIME_REVEALED", "boot_display_ready", "backlight",
        "micronux_source_policy_valid", "MICRONUX_DISPLAY_ABI_VERSION_V2",
        "MICRONUX_DISPLAY_HANDOFF_ABI_VERSION",
    ):
        native_policy_check(
            forbidden not in changed_policy_text,
            f"forbidden-policy-delta:{forbidden}",
        )

    native_feed_check(
        native_feed_patch.name ==
        "0041-video-fbdev-keep-native-one-shot-scanout-fed.patch",
        "patch-path",
    )
    native_feed_check(
        "From 7d580199445835a1485e3e24d6686d44b12442fe "
        "Mon Sep 17 00:00:00 2001" in native_feed_patch_text,
        "patch-commit",
    )
    native_feed_check(
        "Subject: [PATCH 41/46] video: fbdev: keep native one-shot "
        "scanout fed" in native_feed_patch_text,
        "patch-subject",
    )
    native_feed_check(
        "index a8bff0a..460454f 100644" in native_feed_patch_text,
        "patch-order-after-native-policy",
    )
    native_feed_check(
        native_feed_patch_text.count("diff --git ") == 1 and
        "drivers/video/fbdev/esp32p4-dsi.c | 222 " in
        native_feed_patch_text and
        "1 file changed, 210 insertions(+), 12 deletions(-)" in
        native_feed_patch_text,
        "patch-scope",
    )

    for token in (
        "#define DSI_BRG_DPI_RSV_DATA 0x28",
        "#define DSI_BRG_DPI_RSV_DATA_MASK GENMASK(29, 0)",
        "#define DSI_BRG_NATIVE_DPI_RSV_DATA 0x00000000",
        "#define MICRONUX_NATIVE_ARM_TO_IRQ_SLOW_NS 20000000ULL",
        "#include <linux/sched/clock.h>",
    ):
        native_feed_check(
            token in native_feed_added_compact,
            f"constant-or-clock-binding:{token}",
        )

    bridge_start = native_feed_postimage_compact.index(
        "static int micronux_native_configure_dpi_bridge"
    )
    bridge_end_token = "underflow-filler=00000000 discard-vcnt=800"
    bridge_end = native_feed_postimage_compact.index(
        bridge_end_token, bridge_start
    ) + len(bridge_end_token)
    bridge_config = native_feed_postimage_compact[bridge_start:bridge_end]
    native_feed_require_order(
        bridge_config,
        (
            "writel(0, dsi->bridge + DSI_BRG_CLK_EN)",
            "writel(0, dsi->bridge + DSI_BRG_EN)",
            "writel(DSI_BRG_NATIVE_DPI_RSV_DATA, dsi->bridge + "
            "DSI_BRG_DPI_RSV_DATA)",
            "filler = readl(dsi->bridge + DSI_BRG_DPI_RSV_DATA) & "
            "DSI_BRG_DPI_RSV_DATA_MASK",
            "if (filler != DSI_BRG_NATIVE_DPI_RSV_DATA) return -EIO",
            "writel(DSI_BRG_NATIVE_BURST_LEN",
            "readl(dsi->bridge + DSI_BRG_DPI_RSV_DATA) & "
            "DSI_BRG_DPI_RSV_DATA_MASK) != DSI_BRG_NATIVE_DPI_RSV_DATA",
            "readl(dsi->bridge + DSI_BRG_DPI_MISC_CONFIG) != "
            "DSI_BRG_NATIVE_MISC_OFF",
            "underflow-filler=00000000 discard-vcnt=800",
        ),
        "black-filler-before-enable",
    )
    native_feed_require_order(
        scanout_added_compact,
        (
            "writel(DSI_BRG_NATIVE_BURST_LEN",
            "writel(DSI_BRG_NATIVE_MISC_OFF, dsi->bridge + "
            "DSI_BRG_DPI_MISC_CONFIG)",
            "writel(0, dsi->bridge + DSI_BRG_INT_ENA)",
            "writel(DSI_BRG_MODULE_EN, dsi->bridge + DSI_BRG_EN)",
        ),
        "baseline-bridge-enable-order-retained",
    )
    native_feed_check(
        "#define DSI_BRG_NATIVE_MISC_OFF 0x00003200" in
        scanout_added_compact and
        "#define DSI_BRG_NATIVE_MISC_ON 0x00003201" in
        scanout_added_compact and
        not any(
            token in native_feed_removed_compact
            for token in (
                "DSI_BRG_NATIVE_MISC_OFF", "DSI_BRG_NATIVE_MISC_ON",
                "DSI_BRG_INT_ENA",
            )
        ),
        "misc-and-interrupt-policy-retained",
    )

    runtime_policy_start = native_feed_patch_text.index(
        "@@ -5683,6 +5870,8 @@ static bool "
        "micronux_native_runtime_policy_valid"
    )
    runtime_policy_end = native_feed_patch_text.index(
        "@@ -5755,6 +5944,11 @@ micronux_capture_scanout_snapshot",
        runtime_policy_start,
    )
    feed_runtime_policy = patch_postimage_compact(
        native_feed_patch_text[runtime_policy_start:runtime_policy_end]
    )
    for token in (
        "!(readl(dsi->bridge + DSI_BRG_DPI_RSV_DATA) & "
        "DSI_BRG_DPI_RSV_DATA_MASK)",
    ):
        native_feed_check(
            token in feed_runtime_policy,
            f"runtime-policy:{token}",
        )
    native_feed_check(
        "readl(dsi->bridge + DSI_BRG_DPI_MISC_CONFIG) == "
        "DSI_BRG_NATIVE_MISC_ON" in scanout_added_compact and
        "DSI_BRG_NATIVE_MISC_ON" not in native_feed_removed_compact,
        "runtime-misc-policy-retained",
    )

    snapshot_start = runtime_policy_end
    snapshot_end = native_feed_patch_text.index(
        "@@ -5826,6 +6021,7 @@ static int micronux_native_start_scanout",
        snapshot_start,
    )
    feed_snapshot = patch_postimage_compact(
        native_feed_patch_text[snapshot_start:snapshot_end]
    )
    for token in (
        "snapshot->bridge_filler = readl(dsi->bridge + "
        "DSI_BRG_DPI_RSV_DATA) & DSI_BRG_DPI_RSV_DATA_MASK",
        "snapshot->bridge_misc = readl(dsi->bridge + "
        "DSI_BRG_DPI_MISC_CONFIG)",
        "bridge-filler=%08x bridge-misc=%08x",
        "snapshot->bridge_filler, snapshot->bridge_misc",
    ):
        native_feed_check(
            token in feed_snapshot,
            f"qualification-snapshot:{token}",
        )

    fast_rearm_start = native_feed_patch_text.index(
        "@@ -2145,10 +2198,44 @@ static int micronux_dma_start_frame"
    )
    fast_rearm_end = native_feed_patch_text.index(
        "@@ -2233,6 +2320,9 @@ micronux_native_dma_arm_frame",
        fast_rearm_start,
    )
    fast_rearm = patch_postimage_compact(
        native_feed_patch_text[fast_rearm_start:fast_rearm_end]
    )
    fast_rearm = fast_rearm[
        fast_rearm.index("static int micronux_native_dma_rearm_frame_fast"):
        fast_rearm.index("static int micronux_native_dma_arm_frame")
    ]
    native_feed_require_order(
        fast_rearm,
        (
            "if (index >= MICRONUX_DISPLAY_BUFFER_COUNT) return -EINVAL",
            "descriptor_address = dsi->handoff.dma_descriptor_address + "
            "index * MICRONUX_DMA_DESCRIPTOR_SIZE",
            "WRITE_ONCE(dsi->rearm_attempts, "
            "READ_ONCE(dsi->rearm_attempts) + 1)",
            "writel(MICRONUX_NATIVE_DMA_DESCRIPTOR_ARMED, descriptor + "
            "DMA_LLI_CTRL_HI)",
            "writel(descriptor_address | DW_GDMA_LLP_MEMORY_PORT, "
            "dsi->channel + DW_GDMA_CH_LLP)",
            "writel(0, dsi->channel + DW_GDMA_CH_LLP + sizeof(u32))",
            "wmb()",
            "writel(channel | (channel << 8), dsi->gdma + DW_GDMA_CHEN)",
            "arm_ns = local_clock()",
            "dsi->native_last_arm_ns = arm_ns",
        ),
        "fast-rearm-transaction",
    )
    native_feed_check(
        fast_rearm.count("writel(") == 4 and
        fast_rearm.count("wmb()") == 1 and
        fast_rearm.count("local_clock()") == 1,
        "fast-rearm-exact-operations",
    )
    for forbidden in (
        "readl(", "readl_poll", "DW_GDMA_CH_CFG_LO", "DW_GDMA_CH_CFG_HI",
        "rearm_failures", "failure_snapshot", "msleep", "usleep",
        "mutex_", "schedule_",
    ):
        native_feed_check(
            forbidden not in fast_rearm,
            f"fast-rearm-forbidden:{forbidden}",
        )

    native_feed_check(
        "micronux_native_dma_arm_frame(dsi, dsi->front_index, &snapshot)" in
        native_scanout_added_compact and
        "micronux_native_dma_arm_frame(dsi, dsi->front_index, &snapshot)" not in
        native_feed_removed_compact and
        "micronux_native_dma_arm_frame(dsi, next_index, NULL)" in
        native_feed_removed_compact and
        "micronux_native_dma_rearm_frame_fast(dsi, next_index, "
        "irq_entry_ns)" in native_feed_added_compact,
        "checked-initial-fast-recurring-split",
    )
    for token in (
        "chen = readl(dsi->gdma + DW_GDMA_CHEN)",
        "ctrl_hi = readl(descriptor + DMA_LLI_CTRL_HI)",
        "cfg_lo = readl(dsi->channel + DW_GDMA_CH_CFG_LO)",
        "cfg_hi = readl(dsi->channel + DW_GDMA_CH_CFG_HI)",
        "llp = readl(dsi->channel + DW_GDMA_CH_LLP)",
        "status0 = readl(dsi->channel + DW_GDMA_CH_INT_STATUS)",
    ):
        native_feed_check(
            token in native_arm_postimage_compact and
            token not in native_feed_removed_compact,
            f"checked-initial-readback-retained:{token}",
        )

    irq_start = native_feed_patch_text.index(
        "@@ -2263,14 +2353,42 @@ micronux_native_contain_inactive_irqs"
    )
    irq_end = native_feed_patch_text.index(
        "@@ -2440,12 +2571,25 @@ static enum hrtimer_restart "
        "micronux_dma_poll",
        irq_start,
    )
    feed_irq = patch_postimage_compact(
        native_feed_patch_text[irq_start:irq_end]
    )
    native_feed_require_order(
        feed_irq,
        (
            "irq_entry_ns = local_clock()",
            "fifo_depth = readl(dsi->bridge + DSI_BRG_FIFO_FLOW_STATUS) & "
            "DSI_BRG_FIFO_FLOW_DEPTH",
            "top = readl(dsi->gdma + DW_GDMA_INT_STATUS)",
            "READ_ONCE(dsi->scanout_frames) + 1)",
            "spin_lock_irqsave(&dsi->state_lock, flags)",
            "dsi->native_fifo_irq_last = fifo_depth",
            "micronux_native_dma_rearm_frame_fast(dsi, next_index, "
            "irq_entry_ns)",
        ),
        "irq-passive-telemetry-and-fast-rearm",
    )
    native_feed_require_order(
        scanout_added_compact,
        (
            "if (!(status0 & DW_GDMA_INT_DMA_TFR_DONE))",
            "WRITE_ONCE(dsi->scanout_frames, "
            "READ_ONCE(dsi->scanout_frames) + 1)",
            "spin_lock_irqsave(&dsi->state_lock, flags)",
            "micronux_native_dma_arm_frame(dsi, next_index)",
            "spin_unlock_irqrestore(&dsi->state_lock, flags)",
        ),
        "baseline-transfer-done-commit-order-retained",
    )
    native_feed_check(
        "if (!(status0 & DW_GDMA_INT_DMA_TFR_DONE))" not in
        native_feed_removed_compact,
        "transfer-done-gate-retained",
    )

    poll_start = irq_end
    poll_end = native_feed_patch_text.index(
        "@@ -3117,10 +3261,12 @@ static ssize_t scanout_show",
        poll_start,
    )
    feed_poll = patch_postimage_compact(
        native_feed_patch_text[poll_start:poll_end]
    )
    native_feed_check(
        feed_poll.count("readl(dsi->bridge + DSI_BRG_FIFO_FLOW_STATUS)") == 1
        and "if (native)" in feed_poll and
        "dsi->native_fifo_poll_last = fifo_depth" in feed_poll and
        "spin_lock_irqsave(&dsi->state_lock, flags)" in feed_poll,
        "watchdog-single-native-fifo-sample",
    )

    reset_start = feed_irq.index(
        "static void micronux_native_reset_telemetry"
    )
    reset_end = feed_irq.index(
        "static irqreturn_t micronux_native_dma_irq", reset_start
    )
    reset_telemetry = feed_irq[reset_start:reset_end]
    native_feed_check(reset_end > reset_start, "telemetry-reset-block")
    for token in (
        "spin_lock_irqsave(&dsi->state_lock, flags)",
        "if (reset_last_arm) dsi->native_last_arm_ns = 0",
        "dsi->native_fifo_irq_min = DSI_BRG_FIFO_FLOW_DEPTH",
        "dsi->native_fifo_poll_min = DSI_BRG_FIFO_FLOW_DEPTH",
        "spin_unlock_irqrestore(&dsi->state_lock, flags)",
    ):
        native_feed_check(
            token in reset_telemetry,
            f"telemetry-reset:{token}",
        )
    native_feed_require_order(
        native_feed_postimage_compact,
        (
            "micronux_native_reset_telemetry(dsi, true)",
            "micronux_capture_scanout_snapshot(dsi, &snapshot",
            "micronux_native_reset_telemetry(dsi, false)",
            "micronux_watchdog_seed_progress(dsi)",
            "WRITE_ONCE(dsi->scanout_ready, true)",
            "WRITE_ONCE(dsi->render_enabled, true)",
        ),
        "telemetry-reset-publication",
    )

    for token in (
        "arm-to-irq-ns=%llu/%llu",
        "arm-to-irq-over20ms=%u",
        "rearm-ns=%llu/%llu",
        "fifo-irq=%u/%u/%u",
        "fifo-poll=%u/%u/%u",
        "bridge-filler=%08x bridge-misc=%08x",
    ):
        native_feed_check(
            native_feed_added_compact.count(token) >= 2,
            f"scanout-diagnostics-token:{token}",
        )
    native_feed_check(
        native_feed_added_compact.count(
            "micronux_native_capture_telemetry_locked(dsi, &telemetry)"
        ) == 2 and
        native_feed_added_compact.count(
            "spin_unlock_irqrestore(&dsi->state_lock, flags)"
        ) >= 5,
        "sysfs-telemetry-copied-under-lock",
    )

    changed_feed_text = (
        native_feed_added_compact + " " + native_feed_removed_compact
    )
    for forbidden in (
        "MICRONUX_DISPLAY_ABI_VERSION_V2", "handoff_version == 2",
        "micronux_dma_arm_frame(dsi", "micronux_dma_irq",
        "micronux_dma_start_frame", "micronux_handoff_valid",
        "micronux_fb_blank", "micronux_native_boot_ready_store",
        "micronux_native_write_pwm", "micronux_cold_dsi_disable",
        "micronux_cold_ldo_disable", "micronux_native_i2c",
    ):
        native_feed_check(
            forbidden not in changed_feed_text,
            f"abi2-or-policy-mutation:{forbidden}",
        )
    native_feed_check(
        "long disconnected runs" in native_feed_patch_text and
        "physical-panel-state=unobserved" in
        native_feed_postimage_compact,
        "physical-long-run-still-unobserved",
    )

    visible_epoch_compact = " ".join(visible_epoch_patch_text.split())
    visible_epoch_check(
        "From 32df3ba16167162125fa41cf4f3476e3e801f361 "
        "Mon Sep 17 00:00:00 2001" in visible_epoch_patch_text and
        "Subject: [PATCH 42/46] video: fbdev: start telemetry at native reveal"
        in visible_epoch_patch_text and
        "index 460454f..2089446 100644" in visible_epoch_patch_text and
        "1 file changed, 5 insertions(+)" in visible_epoch_patch_text,
        "visible-epoch-identity",
    )
    require_order(
        visible_epoch_compact,
        (
            "Start the visible-runtime epoch at the final dark boundary.",
            "micronux_native_reset_telemetry(dsi, false)",
            "WRITE_ONCE(dsi->native_reveal_may_be_lit, true)",
            "smp_wmb()",
        ),
        "visible-epoch-final-dark-boundary",
    )
    visible_epoch_check(
        visible_epoch_patch_text.count("micronux_native_reset_telemetry") == 2
        and visible_epoch_patch_text.count("writel(") == 0,
        "visible-epoch-no-control-change",
    )

    fault_evidence_check(
        "From eb2d875645cc695622712d1383cd0b53483cc6ed "
        "Mon Sep 17 00:00:00 2001" in fault_evidence_patch_text and
        "Subject: [PATCH 43/46] video: fbdev: preserve native runtime fault "
        "evidence" in fault_evidence_patch_text and
        "index 2089446..0070e7e 100644" in fault_evidence_patch_text,
        "fault-evidence-identity",
    )
    require_order(
        fault_evidence_postimage_compact,
        (
            "micronux_capture_runtime_fault(dsi, state, faults, &record)",
            "micronux_cold_runtime_fail_quiescent",
            "micronux_native_fail_reveal_locked",
            "micronux_log_runtime_fault(dsi, &record)",
        ),
        "fault-capture-before-containment-log-after",
    )
    for token in (
        "struct micronux_runtime_fault_record",
        "__micronux_capture_scanout_snapshot(dsi, snapshot, index, "
        "ignored_status1, true)",
        "__micronux_capture_scanout_snapshot(dsi, snapshot, index, 0, false)",
        "trigger-faults=%lx faults=%lx classes=dma:%u,underrun:%u,stale:%u,"
        "host:%u,guard:%u,backlight:%u",
        "snapshot=host raw=%08x:%08x sticky=%08x:%08x",
        "bridge-filler=%08x bridge-misc=%08x underruns=%u",
        "gdma=%08x:%08x:%08x:%08x cfg=%08x chen=%08x",
    ):
        fault_evidence_check(
            token in fault_evidence_added_compact,
            f"fault-evidence-binding:{token}",
        )

    bandwidth_check(
        "From 8b48cd8ca8616f405449b82089ea5ca371449a1c "
        "Mon Sep 17 00:00:00 2001" in bandwidth_patch_text and
        "Subject: [PATCH 44/46] video: fbdev: derate native DSI for PSRAM "
        "bandwidth" in bandwidth_patch_text and
        "index 0070e7e..64b8dfa 100644" in bandwidth_patch_text,
        "bandwidth-patch-identity",
    )
    for old, new in (
        ("#define DSI_HOST_CLKMGR_VALUE 0x0000130a",
         "#define DSI_HOST_CLKMGR_VALUE 0x00000d07"),
        ("#define HP_CLKRST_DSI_DPI_CLK_DIV_VALUE 2",
         "#define HP_CLKRST_DSI_DPI_CLK_DIV_VALUE 3"),
        ("#define HP_CLKRST_DSI_DPI_CLOCK_VALUE 0x000002a3",
         "#define HP_CLKRST_DSI_DPI_CLOCK_VALUE 0x000003a3"),
        ("handoff->pixel_clock_hz != 80000000",
         "handoff->pixel_clock_hz != 60000000"),
        ("handoff->lane_bit_rate_mbps != 1500",
         "handoff->lane_bit_rate_mbps != 1000"),
        ("writel(47, dsi->host + DSI_HOST_VID_HSA_TIME)",
         "writel(42, dsi->host + DSI_HOST_VID_HSA_TIME)"),
        ("writel(47, dsi->host + DSI_HOST_VID_HBP_TIME)",
         "writel(42, dsi->host + DSI_HOST_VID_HBP_TIME)"),
        ("writel(2063, dsi->host + DSI_HOST_VID_HLINE_TIME)",
         "writel(1833, dsi->host + DSI_HOST_VID_HLINE_TIME)"),
    ):
        bandwidth_check(
            old in bandwidth_removed_compact and new in bandwidth_added_compact,
            f"bandwidth-replacement:{old}",
        )
    for token in (
        "micronux_cold_phy_write(dsi, 0x44, 0x54)",
        "micronux_cold_phy_write(dsi, 0x19, 0x30)",
        "micronux_cold_phy_write(dsi, 0x17, 0x00)",
        "micronux_cold_phy_write(dsi, 0x18, 0x11)",
        "micronux_cold_phy_write(dsi, 0x18, 0x81)",
        "lane-mbps=1000 lanes=2 pll-m=50 pll-n=1 range=2a",
        "source=f240m divider=4 dpi-hz=60000000",
    ):
        bandwidth_check(
            token in bandwidth_postimage_compact,
            f"bandwidth-idf-binding:{token}",
        )
    bandwidth_check(
        '"MICRONUX_DISPLAY_PIXEL_CLOCK_HZ 80000000"' not in
        bandwidth_patch_text and
        "micronux_handoff_valid" not in bandwidth_patch_text,
        "bandwidth-abi2-unchanged",
    )

    for stage_build in stage_builds:
        bandwidth_check(
            'NATIVE_DISPLAY_DRIVER_SHA256="b9a188dd12066b2c596b7223f593d734'
            'e43aebc12e4639fb6a715e6c845bdabe"' in
            stage_build.read_text(encoding="utf-8"),
            f"post-bandwidth-source-sha256:{stage_build.name}",
        )
    bandwidth_check(
        "MIN_DISCONNECT_PROGRESS_HZ = 10.0" in hardware_test_text and
        "MAX_DISCONNECT_PROGRESS_HZ = 25.0" in hardware_test_text and
        "== (None, 10.0)" in hardware_telemetry_test_text and
        "== (None, 25.0)" in hardware_telemetry_test_text,
        "hardware-continuous-progress-window",
    )

    sd_isolation_check(
        "From 836edacc6d2dc303638886b9a0e60abd1acc0ff6 "
        "Mon Sep 17 00:00:00 2001" in sd_isolation_patch_text and
        "Subject: [PATCH 45/46] mmc: dw_mmc: allow an isolated ESP32-P4 "
        "slot" in sd_isolation_patch_text and
        "index e87a490..f68733f 100644" in sd_isolation_patch_text and
        "1 file changed, 6 insertions(+), 2 deletions(-)" in
        sd_isolation_patch_text,
        "sd-isolation-patch-identity",
    )
    for token in (
        "if (!host->num_slots || (host->num_slots != "
        "ARRAY_SIZE(host->slots) && !of_property_read_bool("
        "host->dev->of_node, \"espressif,allow-single-slot\")))",
        "ESP32-P4 dual-slot arbitration enabled with %u active slot(s)",
        "host->num_slots",
    ):
        sd_isolation_check(
            token in sd_isolation_added_compact,
            f"sd-isolation-source-binding:{token}",
        )
    sd_isolation_check(
        "writel(" not in sd_isolation_patch_text and
        "drivers/video" not in sd_isolation_patch_text,
        "sd-isolation-no-display-or-mmio-change",
    )

    continuous_prep_check(
        hashlib.sha256(continuous_prep_patch.read_bytes()).hexdigest() ==
        "f9305b344bd28f425c824affe3aa7e5ed42e6f8e3a1e48601c61ad691a920881" and
        "From 07ad4eaede716bedf1951d7863f074e1ac2f0cec" in
        continuous_prep_patch_text and
        "Subject: [PATCH 46/46] video: fbdev: validate native continuous "
        "reload plan" in continuous_prep_patch_text and
        "1 file changed, 69 insertions(+)" in continuous_prep_patch_text,
        "continuous-prep-identity",
    )
    for token in (
        "#define MICRONUX_DMA_CHANNEL_RELOAD_CFG_LO 0x00000005",
        "#define MICRONUX_NATIVE_DMA_RELOAD_CTRL_HI 0x00108840",
        "struct micronux_native_reload_plan",
        "micronux_native_prepare_reload_plan",
        "transfer_bytes = ((u64)MICRONUX_DMA_BLOCK_TRANSFER_SIZE + 1) * 8",
        "transfer_bytes != dsi->handoff.framebuffer_size",
        "plan->source = dsi->handoff.framebuffer_address[front]",
        "plan->destination = dsi->handoff.dsi_fifo_address",
        "plan->config_lo = MICRONUX_DMA_CHANNEL_RELOAD_CFG_LO",
        "plan->interrupt_mask = DW_GDMA_INT_ERROR_MASK",
        "CONTINUOUS-PREP state=VALIDATED active-scanout=one-shot "
        "hardware-writes=0",
    ):
        continuous_prep_check(
            token in continuous_prep_added_compact,
            f"continuous-prep-binding:{token}",
        )
    prep_start = continuous_prep_postimage_compact.rindex(
        "static int micronux_native_prepare_reload_plan"
    )
    prep_end = continuous_prep_postimage_compact.index(
        "static bool native_window_ready", prep_start
    )
    prep_block = continuous_prep_postimage_compact[prep_start:prep_end]
    continuous_prep_check(
        not continuous_prep_removed_compact and
        "writel" not in prep_block and "readl" not in prep_block,
        "continuous-prep-no-runtime-or-mmio-change",
    )
    continuous_prep_check(
        "native_continuous_fixed_front" not in continuous_prep_added_compact and
        "start_continuous_fixed_front" not in continuous_prep_added_compact and
        "scanout-mode=continuous-fixed-front" not in
        continuous_prep_added_compact,
        "continuous-prep-one-shot-retained",
    )
    for stage_build in stage_builds:
        continuous_prep_check(
            'NATIVE_DISPLAY_DRIVER_SHA256="b9a188dd12066b2c596b7223f593d734'
            'e43aebc12e4639fb6a715e6c845bdabe"' in
            stage_build.read_text(encoding="utf-8"),
            f"continuous-prep-source-sha256:{stage_build.name}",
        )

    boot_reveal = source_block(
        reveal_added_compact,
        "static ssize_t micronux_native_boot_ready_store",
        "static void micronux_native_register_touch_work",
        "reveal-boot-ready",
    )
    require_order(
        boot_reveal,
        (
            "ret = kstrtobool(buf, &ready)",
            "if (!ready) return -EINVAL",
            "mutex_lock(&dsi->mode_lock)",
            "if (READ_ONCE(dsi->boot_ready_consumed))",
            "ret = -EALREADY",
            "MICRONUX_DISPLAY_STATE_SCANOUT_QUALIFIED_QUIESCENT",
            "ret = -EAGAIN",
            "WRITE_ONCE(dsi->boot_ready_consumed, true)",
            "WRITE_ONCE(dsi->native_dark_confirmed, false)",
            "WRITE_ONCE(dsi->native_preserve_source, true)",
            "smp_wmb()",
            "MICRONUX_DISPLAY_STATE_REVEALING",
        ),
        "reveal-one-shot-gate",
    )
    require_order(
        boot_reveal,
        (
            "cancel_delayed_work_sync(&dsi->render_work)",
            "mutex_lock(&dsi->render_lock)",
            "micronux_commit_render_locked(dsi)",
            "target_commit = dsi->queued_index >= 0",
            "target_commit <= dsi->dark_baseline_commit_sequence",
            "micronux_native_requalify_front(dsi, target_commit)",
        ),
        "reveal-render-requalification",
    )
    require_order(
        boot_reveal,
        (
            "WRITE_ONCE(dsi->native_i2c_transition_deadline",
            "smp_wmb()",
            "MICRONUX_NATIVE_I2C_TRANSITION",
            "micronux_cold_i2c_initialize(dsi)",
            "MICRONUX_NATIVE_I2C_ACTIVE_SERIALIZED",
            "micronux_native_runtime_policy_valid(dsi)",
        ),
        "reveal-i2c-transition",
    )
    require_order(
        boot_reveal,
        (
            "micronux_register_native_backlight(dsi)",
            "mutex_lock(&dsi->backlight->ops_lock)",
            "if (dsi->backlight->props.state)",
            "dsi->backlight->props.brightness = handoff->backlight_brightness",
            "dsi->backlight->props.power = FB_BLANK_POWERDOWN",
            "micronux_native_final_status_clean(dsi)",
            "WRITE_ONCE(dsi->native_reveal_may_be_lit, true)",
            "smp_wmb()",
            "micronux_native_write_control(dsi, reveal_command)",
            "WRITE_ONCE(dsi->backlight_gate_enabled, true)",
            "micronux_native_final_status_clean(dsi)",
            "micronux_native_write_pwm(dsi, handoff->backlight_brightness)",
            "WRITE_ONCE(dsi->applied_brightness",
            "dsi->backlight->props.power = FB_BLANK_UNBLANK",
            "micronux_native_final_status_clean(dsi)",
            "sysfs_create_group(&dsi->dev->kobj",
            "dsi->native_runtime_group_created = true",
            "micronux_native_final_status_clean(dsi)",
            "WRITE_ONCE(dsi->boot_display_ready, true)",
            "WRITE_ONCE(dsi->render_enabled, true)",
            "smp_wmb()",
            "MICRONUX_DISPLAY_STATE_RUNTIME_REVEALED",
            "MICRONUX:M9.2:REVEAL state=RUNTIME_REVEALED",
            "schedule_work(&dsi->touch_register_work)",
        ),
        "reveal-publication",
    )
    require_order(
        boot_reveal,
        (
            "if (READ_ONCE(dsi->native_reveal_may_be_lit))",
            'micronux_native_fail_reveal_locked(dsi, "boot-ready", ret, false)',
            "else",
            "micronux_native_unregister_runtime_objects(dsi)",
            'micronux_cold_runtime_fail_quiescent(dsi, "boot-ready-dark", ret)',
        ),
        "reveal-failure-dispatch",
    )

    irq_reveal_start = reveal_postimage_compact.find(
        "preserve = READ_ONCE(dsi->native_preserve_source)"
    )
    irq_reveal_end_token = "wake_up_all(&dsi->frame_wait)"
    irq_reveal_end = reveal_postimage_compact.find(
        irq_reveal_end_token, irq_reveal_start
    )
    stage_e_check(
        irq_reveal_start >= 0 and irq_reveal_end > irq_reveal_start,
        "reveal-irq-block-missing",
    )
    irq_reveal = reveal_postimage_compact[
        irq_reveal_start:irq_reveal_end + len(irq_reveal_end_token)
    ]
    require_order(
        irq_reveal,
        (
            "preserve = READ_ONCE(dsi->native_preserve_source)",
            "!READ_ONCE(dsi->native_dark_confirmed)",
            "micronux_native_contain_inactive_irqs(dsi, top)",
            "if ((top & ~DW_GDMA_INT_TOP_ALLOWED) && !preserve)",
            "dma_error =",
            "set_bit(MICRONUX_FAULT_DMA, &dsi->fault_bits)",
            "if (!(status0 & DW_GDMA_INT_DMA_TFR_DONE))",
            "WRITE_ONCE(dsi->native_source_rearm_unverified, true)",
            "schedule_work(&dsi->fault_work)",
        ),
        "reveal-irq-preserve",
    )
    require_order(
        irq_reveal,
        (
            "next_index = !faulted && !dma_error",
            "micronux_native_dma_arm_frame(dsi, next_index)",
            "if (!dma_error)",
            "dsi->presented_index = dsi->front_index",
            "dsi->presented_commit_sequence = dsi->front_commit_sequence",
            "if (!faulted && dsi->queued_index >= 0)",
            "dsi->front_index = dsi->queued_index",
            "dsi->front_commit_sequence = dsi->buffer_commit_sequence[next_index]",
            "dsi->queued_index = -1",
            "dsi->flip_completions++",
            "dsi->source_generation++",
            "wake_frame = true",
        ),
        "reveal-irq-transaction",
    )
    require_order(
        irq_reveal,
        (
            "if (rearm_failed)",
            "WRITE_ONCE(dsi->last_dma_error, BIT(30))",
            "WRITE_ONCE(dsi->cold_runtime_unverified, true)",
            "if (preserve)",
            "WRITE_ONCE(dsi->native_source_rearm_unverified, true)",
            "set_bit(MICRONUX_FAULT_DMA, &dsi->fault_bits)",
            "if (dma_error || rearm_failed)",
            "schedule_work(&dsi->fault_work)",
            "if (wake_frame)",
            "wake_up_all(&dsi->frame_wait)",
        ),
        "reveal-irq-failure-containment",
    )

    requalification = source_block(
        reveal_added_compact,
        "static bool native_target_ready",
        "micronux_native_off_first",
        "reveal-requalification",
    )
    require_order(
        requalification,
        (
            "dsi->presented_commit_sequence >= commit_sequence",
            "native_target_ready(dsi, target)",
            "presented = dsi->presented_index",
            "presented_commit = dsi->presented_commit_sequence",
            "base = dsi->source_generation",
            "presented_commit < target",
            "target <= dsi->dark_baseline_commit_sequence",
            "native_window_ready(dsi, base)",
            "generation = dsi->source_generation",
            "dsi->presented_index != presented",
            "dsi->presented_commit_sequence != presented_commit",
            "dsi->front_index != presented",
            "dsi->front_commit_sequence != presented_commit",
            "dsi->queued_index >= 0",
            "generation - base < MICRONUX_SCANOUT_STABLE_FRAMES",
            "micronux_native_final_status_clean(dsi)",
            "MICRONUX:M9.2:REVEAL state=QUALIFIED",
        ),
        "reveal-requalification",
    )

    off_first = source_block(
        reveal_added_compact,
        "micronux_native_off_first",
        "static void micronux_native_unregister_runtime_objects",
        "reveal-off-first",
    )
    require_order(
        off_first,
        (
            "MICRONUX_NATIVE_I2C_ACTIVE_SERIALIZED",
            "micronux_i2c_write_reg8(dsi, handoff->i2c_address",
            "handoff->backlight_register, 0)",
            "micronux_native_write_control(dsi, command)",
            "backlight->props.brightness = 0",
            "backlight->props.power = FB_BLANK_POWERDOWN",
            "if (*pwm_ret || *control_ret)",
            "msleep(handoff->pwm_zero_settle_ms)",
            "WRITE_ONCE(dsi->native_dark_confirmed, true)",
            "smp_wmb()",
            "WRITE_ONCE(dsi->native_preserve_source, false)",
        ),
        "reveal-off-first",
    )
    fail_reveal = source_block(
        reveal_added_compact,
        "static void micronux_native_fail_reveal_locked",
        "ret = device_create_file(dsi->dev, &dev_attr_boot_ready)",
        "reveal-failure",
    )
    require_order(
        fail_reveal,
        (
            "WRITE_ONCE(dsi->render_enabled, false)",
            "WRITE_ONCE(dsi->boot_display_ready, false)",
            "smp_wmb()",
            "MICRONUX_DISPLAY_STATE_REVEALING",
            "micronux_native_off_first(dsi, &pwm_ret, &control_ret)",
            "if (!dark && !force_teardown)",
            "micronux_native_unregister_runtime_objects(dsi)",
            "MICRONUX_DISPLAY_STATE_FAILED_UNVERIFIED",
            "source=preserved",
            "return",
            "micronux_native_unregister_runtime_objects(dsi)",
            "micronux_cold_runtime_teardown(dsi)",
            "micronux_cold_i2c_gate(dsi)",
            "micronux_cold_dsi_disable(dsi)",
            "micronux_cold_ldo_disable(dsi)",
        ),
        "reveal-failure-rollback",
    )

    vpg_hunk = source_block(
        reveal_patch_text,
        "@@ -2309,6 +2790,10 @@ static ssize_t vpg_test_ms_store",
        "@@ -2503,6 +2988,12 @@ static ssize_t ownership_show",
        "reveal-vpg",
    )
    require_order(
        vpg_hunk,
        (
            "MICRONUX_DISPLAY_ABI_VERSION_V3",
            "return -EOPNOTSUPP",
            "ret = kstrtou32(buf, 10, &duration_ms)",
        ),
        "reveal-vpg-rejection",
    )
    native_attrs = source_block(
        reveal_added_compact,
        "static struct attribute *micronux_native_runtime_attrs[] = {",
        "static const struct attribute_group micronux_native_runtime_group",
        "reveal-native-attributes",
    )
    for attribute in (
        "dev_attr_pattern",
        "dev_attr_ownership",
        "dev_attr_scanout",
        "dev_attr_diagnostics",
        "dev_attr_touch",
    ):
        stage_e_check(
            attribute in native_attrs,
            f"reveal-native-attribute-missing:{attribute}",
        )
    stage_e_check(
        "dev_attr_vpg_test_ms" not in native_attrs,
        "reveal-native-vpg-attribute-present",
    )
    stage_e_check(
        "dev_attr_boot_ready" not in native_attrs,
        "reveal-native-boot-ready-in-runtime-group",
    )

    publication_hunk = source_block(
        reveal_patch_text,
        "@@ -5238,6 +6040,10 @@ static int micronux_cold_initialize_scanout_stage",
        "@@ -5258,6 +6064,10 @@ static int micronux_cold_initialize_scanout_stage",
        "reveal-publication-file",
    )
    stage_d_publication = source_block(
        scanout_added_compact,
        "static int micronux_cold_initialize_scanout_stage",
        'micronux_cold_runtime_fail_quiescent(dsi, "stage-d-precondition", ret)',
        "scanout-stage-publication",
    )
    require_order(
        stage_d_publication,
        (
            "micronux_register_framebuffer(dsi)",
            "micronux_native_final_status_clean(dsi)",
            "micronux_watchdog_seed_progress(dsi)",
            "WRITE_ONCE(dsi->scanout_ready, true)",
            "WRITE_ONCE(dsi->render_enabled, true)",
            "MICRONUX_DISPLAY_STATE_SCANOUT_QUALIFIED_QUIESCENT",
        ),
        "scanout-stage-publication",
    )
    require_order(
        publication_hunk,
        (
            "ret = -EIO",
            "goto fail_publication",
            "device_create_file(dsi->dev, &dev_attr_boot_ready)",
            "dsi->boot_ready_file_created = true",
            "micronux_watchdog_seed_progress(dsi)",
            "WRITE_ONCE(dsi->scanout_ready, true)",
            "WRITE_ONCE(dsi->render_enabled, true)",
        ),
        "reveal-boot-file-publication",
    )
    publication_failure_hunk = source_block(
        reveal_patch_text,
        "@@ -5258,6 +6064,10 @@ static int micronux_cold_initialize_scanout_stage",
        "@@ -5324,9 +6134,13 @@ static int micronux_dsi_probe",
        "reveal-publication-failure",
    )
    require_order(
        publication_failure_hunk,
        (
            "fail_publication:",
            "if (dsi->boot_ready_file_created)",
            "device_remove_file(dsi->dev, &dev_attr_boot_ready)",
            "dsi->boot_ready_file_created = false",
            'micronux_cold_runtime_fail_quiescent(dsi, "publication-check", ret)',
        ),
        "reveal-boot-file-rollback",
    )
    remove_hunk_v3 = source_block(
        reveal_patch_text,
        "@@ -5535,6 +6349,8 @@ static void micronux_dsi_remove",
        "-- \n2.54.0.windows.1",
        "reveal-remove",
    )
    stage_e_check(
        remove_hunk in scanout_patch_text,
        "scanout-remove-state-order-for-reveal",
    )
    require_order(
        remove_hunk_v3,
        (
            "device_remove_file(&pdev->dev, &dev_attr_boot_ready)",
            "cancel_work_sync(&dsi->touch_register_work)",
            "mutex_lock(&dsi->mode_lock)",
            "WRITE_ONCE(dsi->display_state",
            "MICRONUX_DISPLAY_STATE_REVEALING",
            "MICRONUX_DISPLAY_STATE_RUNTIME_REVEALED",
            "READ_ONCE(dsi->native_preserve_source)",
            "micronux_native_off_first(dsi, &pwm_ret, &prepare_ret)",
            "micronux_native_unregister_runtime_objects(dsi)",
            "micronux_cold_runtime_teardown(dsi)",
            "micronux_cold_i2c_gate(dsi)",
        ),
        "reveal-remove-lifetime",
    )
    stage_d_remove = " ".join(
        source_block(
            scanout_patch_text,
            "@@ -4471,20 +5536,29 @@ static void micronux_dsi_remove",
            "-- \n2.54.0.windows.1",
            "scanout-remove-containment",
        ).split()
    )
    require_order(
        stage_d_remove,
        (
            "MICRONUX_DISPLAY_STATE_REMOVING",
            "micronux_cold_runtime_teardown(dsi)",
            "cold_dsi_off = micronux_cold_dsi_disable(dsi)",
            "cold_ldo_off = micronux_cold_ldo_disable(dsi)",
            "cold_i2c_released && cold_runtime_off &&",
            "cold_dsi_off && cold_ldo_off",
        ),
        "scanout-remove-containment",
    )
    required_header = (
        "MICRONUX_DISPLAY_HANDOFF_ABI_VERSION UINT16_C(2)",
        "MICRONUX_DISPLAY_BUFFER_COUNT UINT32_C(3)",
        "MICRONUX_DISPLAY_OWNER_LINUX_PENDING UINT32_C(1)",
        "MICRONUX_DISPLAY_BACKBUFFER_POOL_START UINT32_C(0x49300000)",
        "MICRONUX_DISPLAY_BACKBUFFER_POOL_END UINT32_C(0x49700000)",
        "MICRONUX_DISPLAY_FLAG_HOST_VPG_ACTIVE (UINT32_C(1) << 7)",
        "MICRONUX_DISPLAY_COLD_HANDOFF_ABI_VERSION UINT16_C(3)",
        "MICRONUX_DISPLAY_COLD_HANDOFF_SIZE UINT16_C(0xc0)",
        "MICRONUX_DISPLAY_COLD_PANEL_PAYLOAD_CRC32 UINT32_C(0xcea07f9b)",
        "MICRONUX_DISPLAY_V3_FLAG_PWM_ZERO_WRITE_ACKED",
        "MICRONUX_DISPLAY_V3_FLAG_RESET_PREPARE_WRITE_ACKED",
        "MICRONUX_DISPLAY_V3_FLAG_RESET_ASSERT_WRITE_ACKED",
        "MICRONUX_DISPLAY_V3_REQUIRED_FLAGS UINT32_C(0x00003fff)",
        "uint32_t display_control_reset_assert_command;",
        "uint32_t display_control_reset_prepare_command;",
        "uint32_t display_control_reveal_command;",
        "uint32_t pwm_zero_settle_ms;",
        "uint32_t panel_payload_crc32;",
        "panel_payload_crc32) == 0xb4",
        "crc32) == 0xbc",
    )
    for token in required_header:
        if token not in header_text:
            raise AssertionError(f"header-binding-missing:{token}")
    required_loader = (
        "MICRONUX_DSI_FRAMEBUFFER_ALIGNMENT UINT32_C(8)",
        "(MICRONUX_DSI_FRAMEBUFFER_ALIGNMENT - 1U)",
        "MICRONUX_DSI_CLOCK_LANE_FORCE_HS 0",
        "dpi_config.flags.disable_lp = 0",
        "mipi_dsi_host_ll_dpi_enable_frame_ack(host, false)",
        "frame_ack=disabled",
        "disable_backlight_gate()",
        "gate=disabled",
        "MIPI_DSI_PATTERN_BAR_VERTICAL",
        "MICRONUX:M9.2:DSI-HANDOFF-SOURCE state=ready",
        "MICRONUX_DISPLAY_FLAG_HOST_VPG_ACTIVE",
        "owner=linux-pending pattern=vertical-bars",
    )
    for token in required_loader:
        if token not in loader_text:
            raise AssertionError(f"loader-binding-missing:{token}")
    required_cold_loader = (
        "MICRONUX_COLD_PIXEL_CLOCK_HZ UINT32_C(60000000)",
        "MICRONUX_COLD_LANE_BIT_RATE_MBPS UINT32_C(1000)",
        "MICRONUX_COLD_PMS_PAGE_SIZE UINT32_C(4096)",
        "MICRONUX_COLD_FRONT_ALIGNMENT MICRONUX_COLD_PMS_PAGE_SIZE",
        "MICRONUX_COLD_DESCRIPTOR_ALIGNMENT MICRONUX_COLD_PMS_PAGE_SIZE",
        "MICRONUX_COLD_DESCRIPTOR_SIZE UINT32_C(192)",
        "MICRONUX_COLD_DESCRIPTOR_BACKING_SIZE MICRONUX_COLD_PMS_PAGE_SIZE",
        "MICRONUX_COLD_INTERNAL_SRAM_END UINT32_C(0x4ffc0000)",
        "MICRONUX_COLD_SD_DMA_START UINT32_C(0x4ff80000)",
        "MICRONUX_COLD_SD_DMA_END UINT32_C(0x4ff82000)",
        "(descriptors < MICRONUX_COLD_SD_DMA_END && "
        "MICRONUX_COLD_SD_DMA_START < descriptors_end)",
        "heap_caps_aligned_alloc( MICRONUX_COLD_FRONT_ALIGNMENT",
        "heap_caps_aligned_alloc( MICRONUX_COLD_DESCRIPTOR_ALIGNMENT, "
        "MICRONUX_COLD_DESCRIPTOR_BACKING_SIZE",
        "memset(s_cold_descriptors, 0, MICRONUX_COLD_DESCRIPTOR_BACKING_SIZE)",
        ".display_control_reset_assert_command = "
        "MICRONUX_COLD_DISPLAY_RESET_ASSERT_COMMAND",
        ".display_control_reset_prepare_command = "
        "MICRONUX_COLD_DISPLAY_RESET_PREPARE_COMMAND",
        ".display_control_reveal_command = "
        "MICRONUX_COLD_DISPLAY_REVEAL_COMMAND",
        ".pwm_zero_settle_ms = MICRONUX_COLD_PWM_ZERO_SETTLE_MS",
        ".panel_payload_crc32 = MICRONUX_DISPLAY_COLD_PANEL_PAYLOAD_CRC32",
        "panel-payload-crc=cea07f9b contract=invalid",
        "pwm-zero-write=acked reset-prepare-write=acked",
        "pwm-zero-settle=elapsed reset-assert-write=acked",
        "reset-hold=elapsed i2c=released",
        "ESP_CACHE_MSYNC_FLAG_INVALIDATE",
        "flush and invalidate staged cold display contract",
    )
    for token in required_cold_loader:
        if token not in loader_compact:
            raise AssertionError(f"cold-loader-binding-missing:{token}")
    for token in (
        "CONFIG_SPIRAM_SPEED_200M=y",
        "CONFIG_SPIRAM_XIP_FROM_PSRAM=y",
        "CONFIG_CACHE_L2_CACHE_256KB=y",
        "CONFIG_CACHE_L2_CACHE_LINE_64B=y",
        "CONFIG_COMPILER_OPTIMIZATION_PERF=y",
    ):
        bandwidth_check(
            token in loader_defaults_text,
            f"loader-bandwidth-config:{token}",
        )
    bandwidth_check(
        "CONFIG_CACHE_L2_CACHE_128KB=y" not in loader_defaults_text,
        "loader-stale-l2-cache-size",
    )
    sequence_start = loader_text.index(
        "static esp_err_t cold_execute_external_command_sequence"
    )
    sequence_end = loader_text.index(
        "esp_err_t micronux_mipi_dsi_cold_early_dark", sequence_start
    )
    command_sequence = loader_text[sequence_start:sequence_end]
    ordered_commands = (
        "MICRONUX_COLD_BACKLIGHT_REGISTER, 0",
        "MICRONUX_COLD_DISPLAY_RESET_PREPARE_COMMAND",
        "MICRONUX_COLD_PWM_ZERO_SETTLE_MS",
        "MICRONUX_COLD_DISPLAY_RESET_ASSERT_COMMAND",
        "MICRONUX_COLD_RESET_HOLD_MS",
        "cold_release_i2c(bus, device)",
    )
    positions = [command_sequence.index(token) for token in ordered_commands]
    if positions != sorted(positions):
        raise AssertionError("cold-loader-external-command-order")
    packer_compact = " ".join(packer.read_text(encoding="utf-8").split())
    required_packer = (
        "KERNEL_LOAD_ADDRESS = 0x48400000",
        "max_memory_end: int = COMMS_RESERVE_ADDRESS",
        "KERNEL_LOAD_ADDRESS + memory_size > max_memory_end",
        '"--max-memory-end"',
        "read_image(args.image, args.max_memory_end)",
    )
    for token in required_packer:
        if token not in packer_compact:
            raise AssertionError(f"kernel-packer-binding-missing:{token}")
    loader_main_compact = " ".join(loader_main_text.split())
    required_loader_memory = (
        "const uint64_t kernel_end = "
        "(uint64_t)s_payload.kernel_load_vaddr + "
        "s_payload.kernel_memory_size;",
        "#if CONFIG_MICRONUX_M7_EARLY_UMODE_DENY "
        "if (kernel_end > MICRONUX_DISPLAY_POOL_VADDR)",
        "#else if (kernel_end > MICRONUX_COMMS_VADDR)",
        'fail("kernel-memory-span")',
    )
    for token in required_loader_memory:
        if token not in loader_main_compact:
            raise AssertionError(f"kernel-loader-binding-missing:{token}")
    early = loader_main_text.index("micronux_mipi_dsi_cold_early_dark()")
    prepare = loader_main_text.index(
        "micronux_mipi_dsi_cold_prepare()", early
    )
    clint = loader_main_text.index("characterize_clint();", prepare)
    if not early < prepare < clint:
        raise AssertionError("cold-loader-early-prepare-order")
    stage = loader_main_text.index("micronux_mipi_dsi_cold_stage()")
    suspend = loader_main_text.index("vTaskSuspendAll();", stage)
    commit = loader_main_text.index("micronux_mipi_dsi_cold_commit();", suspend)
    jump = loader_main_text.index("micronux_handoff_jump(", commit)
    if not stage < suspend < commit < jump:
        raise AssertionError("cold-loader-final-commit-order")
    after_commit = loader_main_text[commit:jump]
    for forbidden in ("fail(", "ESP_LOG", "vTaskDelay", "esp_cache_msync", "fflush"):
        if forbidden in after_commit:
            raise AssertionError(f"cold-loader-post-commit-operation:{forbidden}")
    for stage_build in stage_builds:
        build_compact = " ".join(stage_build.read_text(encoding="utf-8").split())
        for token in (
            "DISPLAY_POOL_ADDRESS=$((0x49300000))",
            '--max-memory-end "${DISPLAY_POOL_ADDRESS}"',
        ):
            if token not in build_compact:
                raise AssertionError(
                    f"kernel-build-binding-missing:{stage_build.name}:{token}"
                )
    if loader_text.count(
        "mipi_dsi_host_ll_dpi_enable_frame_ack(host, false)"
    ) != 1:
        raise AssertionError("loader-frame-ack-disable-count")
    panel_construct = loader_text.index("result = esp_lcd_new_panel_jd9365(")
    frame_ack_disable = loader_text.index(
        "mipi_dsi_host_ll_dpi_enable_frame_ack(host, false)"
    )
    panel_init = loader_text.index("esp_lcd_panel_init(s_panel)")
    frame_ack_verify = loader_text.index(
        "!host->vid_mode_cfg.frame_bta_ack_en", frame_ack_disable
    )
    if not panel_construct < frame_ack_disable < panel_init < frame_ack_verify:
        raise AssertionError("loader-frame-ack-disable-order")
    blank = loader_text.index("disable_backlight_gate()")
    vpg = loader_text.index("MIPI_DSI_PATTERN_BAR_VERTICAL", blank)
    handoff = loader_text.index("esp_lcd_dpi_panel_prepare_handoff", vpg)
    if not blank < vpg < handoff:
        raise AssertionError("loader-vpg-handoff-order")
    required_idf_handoff = (
        "DPI handoff requires host VPG and disabled bridge DPI",
        "hal->host->vid_mode_cfg.vpg_en",
        "!hal->bridge->dpi_misc_config.dpi_en",
        "Host VPG disconnects bridge DPI",
    )
    for token in required_idf_handoff:
        if token not in idf_handoff_text:
            raise AssertionError(f"idf-handoff-binding-missing:{token}")
    required_pms = (
        "write_region(2, display.backbuffer_pool_start,",
        "expected = MICRONUX_DMA_REGION1_MASK | "
        "MICRONUX_DMA_REGION2_MASK | MICRONUX_DMA_REGION3_MASK;",
        "expected = MICRONUX_DMA_REGION3_MASK | MICRONUX_DMA_REGION4_MASK;",
    )
    for token in required_pms:
        if token not in dma_pms_compact:
            raise AssertionError(f"dma-pms-binding-missing:{token}")
    required_harness = (
        'SHELL_PROMPT_RE = re.compile(r"(?:^|\\r?\\n)(?:/ # |micronux# )$")',
        'r"^running abi=3 state=RUNTIME_REVEALED "',
        'r"^abi=3 state=RUNTIME_REVEALED "',
        'r"frames=(?P<before>\\d+)->(?P<after>\\d+) "',
        "frame-irq=(?P<irq>",
        "rearm=explicit",
        "frame-ack=on",
        "i2c=active-serialized",
        "physical-panel-state=unobserved",
        "def scanout_contract_problem(",
        "def diagnostics_contract_problem(",
        "def display_health_problem(",
        "def runtime_status_problem(",
        "def wait_for_touch_terminal(",
        'm9_boot_a="$(cat /proc/sys/kernel/random/boot_id)"',
        'm9_uptime_a="$(cat /proc/uptime)"',
        'echo "MICRONUX:M9.2:RUNTIME sample=a boot_id=$m9_boot_a"',
        'echo "MICRONUX:M9.2:RUNTIME sample=b uptime=$m9_uptime_b"',
        "m9_touch_state=registering",
        'if [ "$m9_touch_state" != registering ]; then break; fi',
        'test "$m9_touch_state" != registering',
        'if [ ! -e "$D/vpg_test_ms" ]; then',
        "MICRONUX:M9:VPG-UNAVAILABLE absent=",
        "MICRONUX:M9:VPG-UNAVAILABLE absent=1",
        '"preflight", "touch", "soak", "stress", "disconnect", "snapshot"',
        "def run_soak(",
        "def run_stress(",
        "def run_disconnect(",
        "def run_snapshot(",
        "recover_reconnect_prompt(",
        "capture_passive_output(",
        "framebuffer_hash_problem(",
        "require_flip=True",
        "require_progress=True",
    )
    for token in required_harness:
        stage_e_check(
            token in hardware_test_compact,
            f"hardware-test-binding-missing:{token}",
        )
    touch_status_printf = r'printf "%s\\n" "$m9_touch_state"'
    touch_filter_printf = r'''printf '%s\\n' \"$m9_touch_state\"'''
    stage_e_check(
        hardware_test_text.count(touch_status_printf) == 2,
        "hardware-test-touch-status-printf-escape",
    )
    stage_e_check(
        hardware_test_text.count(touch_filter_printf) == 1,
        "hardware-test-touch-filter-printf-escape",
    )
    stage_e_check(
        r'printf "%s\n" "$m9_touch_state"' not in hardware_test_text,
        "hardware-test-touch-status-single-escape",
    )
    stage_e_check(
        r'''printf '%s\n' \"$m9_touch_state\"''' not in hardware_test_text,
        "hardware-test-touch-filter-single-escape",
    )
    for stale in (
        "abi=2",
        "frame-ack=off",
        "frame-ack=disabled",
        'echo -n "MICRONUX:M9.2:RUNTIME sample=a boot_id="',
        "--vpg-ms",
        '"vpg",',
        'echo 1 >"$D/boot_ready"',
    ):
        stage_e_check(
            stale not in hardware_test_text,
            f"hardware-test-stale-contract:{stale}",
        )

    harness_main = source_block(
        hardware_test_compact,
        "def main() -> int:",
        'if __name__ == "__main__":',
        "hardware-main",
    )
    require_order(
        harness_main,
        (
            "contract_problem = boot_contract_problem(boot_log)",
            "identity_problem = verify_runtime_identity(device)",
            "touch_problem = wait_for_touch_terminal(device)",
            'if args.mode == "preflight"',
            "return run_preflight(device, boot_log)",
        ),
        "hardware-startup-terminal-touch",
    )
    snapshot_harness = source_block(
        hardware_test_compact,
        "def run_snapshot(",
        "def main() -> int:",
        "hardware-snapshot",
    )
    require_order(
        snapshot_harness,
        (
            "capture_passive_output(device, snapshot_seconds)",
            "failure_marker(captured)",
            "recover_reconnect_prompt(port, device, 45.0)",
            "failure_marker(recovered)",
            "wait_for_touch_terminal(device)",
            '"SNAPSHOT", 30.0',
            "framebuffer_hash_problem(",
            "display_health_problem( snapshot.output, minimum=2, "
            "require_progress=True",
            "MICRONUX:M9:SNAPSHOT:CAPTURED",
        ),
        "hardware-snapshot-behavior",
    )
    soak_harness = source_block(
        hardware_test_compact,
        "def run_soak(",
        "def stress_snapshot_shell(",
        "hardware-soak",
    )
    require_order(
        soak_harness,
        (
            "sample_count = soak_seconds // sample_seconds + 1",
            "MICRONUX:M9:SOAK-SAMPLE index=",
            "cat $D/diagnostics",
            "cat $D/scanout",
            "cat $D/touch",
            "framebuffer_hash_problem(",
            "display_health_problem( soak.output, minimum=sample_count, "
            "require_progress=True",
            "MICRONUX:M9:SOAK:PASS",
        ),
        "hardware-soak-behavior",
    )
    stress_harness = source_block(
        hardware_test_compact,
        "def run_stress(",
        "def run_disconnect(",
        "hardware-stress",
    )
    require_order(
        stress_harness,
        (
            'cycle_arguments = " ".join( str(cycle) for cycle in '
            "range(1, stress_cycles + 1)",
            "micronux-display-test draw 300",
            "micronux-storage-test --write-test",
            "/usr/bin/micronux-online",
            "/usr/bin/micronux-combined-soak",
            "/usr/bin/micronux-selftest",
            "kill -TERM $m9_draw_pid",
            "stress_snapshot_shell(\"after\")",
            "framebuffer_hash_problem(",
            "if before_free - after_free > 512",
            "if before_available - after_available > 512",
            "display_health_problem( result.output, "
            "minimum=len(expected_stages), require_flip=True, "
            "require_progress=True",
            "MICRONUX:M9.2:STRESS:PASS",
        ),
        "hardware-stress-behavior",
    )
    disconnect_harness = source_block(
        hardware_test_compact,
        "def run_disconnect(",
        "def run_snapshot(",
        "hardware-disconnect",
    )
    require_order(
        disconnect_harness,
        (
            "MICRONUX:M9:USB-DISCONNECT:BOUNDARY:BEGIN",
            "device.dtr = False",
            "device.rts = False",
            "device.close()",
            "time.sleep(reopen_delay)",
            "open_serial( port, 30.0, clear_input=False, write_timeout=1.0 )",
            "capture_passive_output(device, 2.0)",
            "recover_reconnect_prompt(port, device, 30.0)",
            "before_boot_ids[-1] != after_boot_ids[-1]",
            "display_health_problem( before_boundary_output, minimum=2, "
            "require_progress=True",
            "framebuffer_hash_problem(",
            "display_health_problem( full_health_output, minimum=3, "
            "require_progress=True",
            "MICRONUX:M9:USB-RECONNECT:PASS",
        ),
        "hardware-disconnect-behavior",
    )

    required_display_test = (
        "ioctl(fd, FBIOBLANK, blank)",
        "set_framebuffer_blank(FB_BLANK_POWERDOWN)",
        "ioctl(console_fd, KDSETMODE, KD_TEXT)",
        "set_framebuffer_blank(FB_BLANK_UNBLANK)",
        "ioctl(console_fd, KDSETMODE, KD_GRAPHICS)",
        "open_framebuffer(&fix, &var)",
        "touch_status_ready(status)",
        'strcmp(status, "unavailable\\n") != 0',
        "restore_console_state()",
        "(void)atexit(restore_console)",
        "(void)signal(SIGINT, handle_signal)",
        "(void)signal(SIGTERM, handle_signal)",
        "MICRONUX:M9:DISPLAY-TEST:PASS mode=check",
        "MICRONUX:M9:DISPLAY-TEST:PASS mode=draw console=restored",
        "MICRONUX:M9:DISPLAY-TEST:PASS mode=touch points=5",
    )
    for token in required_display_test:
        stage_e_check(
            token in display_test_compact,
            f"display-test-binding-missing:{token}",
        )
    stage_e_check(
        "boot_ready" not in display_test_text,
        "display-test-premature-boot-ready-gate",
    )
    restore_console = source_block(
        display_test_compact,
        "static int restore_console_state(void)",
        "static void restore_console(void)",
        "display-test-restore",
    )
    require_order(
        restore_console,
        (
            "set_framebuffer_blank(FB_BLANK_POWERDOWN)",
            "ioctl(console_fd, KDSETMODE, KD_TEXT)",
            "set_framebuffer_blank(FB_BLANK_UNBLANK)",
            "graphics_active = 0",
            "close(console_fd)",
        ),
        "display-test-restore",
    )
    stage_e_check(
        restore_console.count("&& !first_error") == 3,
        "display-test-restore-first-error",
    )
    acquire_graphics = source_block(
        display_test_compact,
        "static int acquire_graphics(void)",
        "static int acquire_lock(void)",
        "display-test-acquire",
    )
    require_order(
        acquire_graphics,
        (
            'open("/dev/tty1", O_RDWR | O_CLOEXEC)',
            "ioctl(console_fd, KDSETMODE, KD_GRAPHICS)",
            "graphics_active = 1",
            "set_framebuffer_blank(FB_BLANK_UNBLANK)",
        ),
        "display-test-acquire",
    )
    display_check = source_block(
        display_test_compact,
        "static int run_check(void)",
        "static int run_draw(unsigned int seconds)",
        "display-test-check",
    )
    require_order(
        display_check,
        (
            "open_framebuffer(&fix, &var)",
            "open_touch(touch_path, sizeof(touch_path))",
            "read_touch_status(status, sizeof(status))",
            "touch >= 0 && !touch_status_ready(status)",
            'touch < 0 && strcmp(status, "unavailable\\n") != 0',
            "MICRONUX:M9:DISPLAY-TEST:PASS mode=check",
        ),
        "display-test-check",
    )
    display_main = source_block(
        display_test_compact,
        "int main(int argc, char **argv)",
        "return result; }",
        "display-test-main",
    )
    require_order(
        display_main,
        (
            "acquire_lock()",
            "atexit(restore_console)",
            "signal(SIGINT, handle_signal)",
            "signal(SIGTERM, handle_signal)",
            "run_check()",
            "run_draw(value)",
            "run_touch(value)",
            "restore_console_state()",
        ),
        "display-test-main",
    )
    rootfs_init_text = rootfs_init.read_text(encoding="utf-8")
    required_rootfs = (
        "MICRONUX:M9.2:COLD-BOOT state=trigger action=cold_init",
        'echo 1 >"$display_sysfs/cold_init"',
        "SCANOUT_QUALIFIED_QUIESCENT",
        "display_reveal_allowed=0",
        'if [ "$display_reveal_allowed" -eq 1 ]',
        "reason=native-state-changed",
        "RUNTIME_REVEALED",
        "reason=framebuffer-device-timeout",
        "reason=status-render mode=headless",
        "reason=display-reveal mode=headless",
    )
    for token in required_rootfs:
        if token not in rootfs_init_text:
            raise AssertionError(f"rootfs-cold-boot-binding-missing:{token}")
    cold_trigger = rootfs_init_text.index(
        'if [ "$native_display_state" = PROBED_QUIESCENT ]'
    )
    cold_write = rootfs_init_text.index(
        'echo 1 >"$display_sysfs/cold_init"', cold_trigger
    )
    scanout_gate = rootfs_init_text.index(
        'if [ "$native_display_state" = SCANOUT_QUALIFIED_QUIESCENT ]',
        cold_write,
    )
    native_reveal_allow = rootfs_init_text.index(
        "display_reveal_allowed=1", scanout_gate
    )
    legacy_reveal_allow = rootfs_init_text.index(
        'if [ "$native_display" -eq 0 ] && [ -c /dev/fb0 ] && '
        '[ -c /dev/tty1 ]; then',
        native_reveal_allow,
    )
    framebuffer_gate = rootfs_init_text.index(
        'if [ "$display_reveal_allowed" -eq 1 ]', legacy_reveal_allow
    )
    render = rootfs_init_text.index("} >/dev/tty1", framebuffer_gate)
    pre_reveal_state = rootfs_init_text.index(
        'if [ "$native_display_state" != SCANOUT_QUALIFIED_QUIESCENT ]',
        render,
    )
    reveal_write = rootfs_init_text.index(
        'echo 1 >"$display_sysfs/boot_ready"', pre_reveal_state
    )
    revealed_state = rootfs_init_text.index(
        'if [ "$native_display_state" = RUNTIME_REVEALED ]', reveal_write
    )
    shell_ready = rootfs_init_text.index(
        "MICRONUX:M6:COMBINED:SHELL ready", revealed_state
    )
    if not (
        cold_trigger < cold_write < scanout_gate < native_reveal_allow <
        legacy_reveal_allow < framebuffer_gate < render < pre_reveal_state <
        reveal_write < revealed_state < shell_ready
    ):
        raise AssertionError("rootfs-cold-boot-order")
    display_block = rootfs_init_text[framebuffer_gate:shell_ready]
    if "exit 1" in display_block:
        raise AssertionError("rootfs-display-failure-kills-pid1")
    required_dts = (
        "display_pool: display-pool@49300000",
        "reg = <0x49300000 0x00400000>;",
        "user_pool: user-pool@49700000",
        "memory-region = <&display_pool>;",
    )
    for device_tree in device_trees:
        device_tree_text = device_tree.read_text(encoding="utf-8")
        for token in required_dts:
            if token not in device_tree_text:
                raise AssertionError(
                    f"dts-binding-missing:{device_tree.name}:{token}"
                )
    m9_dts_text = device_trees[1].read_text(encoding="utf-8")
    m9_dts_compact = " ".join(m9_dts_text.split())
    required_m9_dts = (
        "display_pool: display-pool@49300000 { "
        "reg = <0x49300000 0x00400000>; no-map; };",
        "interrupts = <18>;",
        "espressif,handoff-address = <0x49f00000>;",
        "espressif,required-handoff-abi = <3>;",
        "espressif,required-silicon-revision = <103>;",
        "espressif,uncached-offset = <0x40000000>;",
        'firmware-name = "micronux/jd9365-waveshare-10.1-v2.bin";',
        "memory-region = <&display_pool>;",
        'reg-names = "dsi-host", "dsi-bridge", "dma-pms", "gdma", '
        '"i2c", "intr-matrix", "gpio", "iomux", "hp-clkrst", "pmu", '
        '"efuse";',
    )
    required_m9_resources = (
        "<0x500a0000 0x00000800>",
        "<0x500a0800 0x00000104>",
        "<0x500a6000 0x00001000>",
        "<0x50081000 0x00001000>",
        "<0x500c4000 0x00001000>",
        "<0x500d6000 0x00001000>",
        "<0x500e0000 0x00001000>",
        "<0x500e1000 0x00001000>",
        "<0x500e6000 0x00001000>",
        "<0x50115000 0x00001000>",
        "<0x5012d000 0x00001000>",
    )
    for token in required_m9_dts + required_m9_resources:
        if token not in m9_dts_compact:
            raise AssertionError(f"cold-dts-binding-missing:{token}")

    m7_dts_text = device_trees[0].read_text(encoding="utf-8")
    sd_isolation_check(
        "espressif,dual-slot; espressif,allow-single-slot;" in
        m9_dts_compact,
        "sd-isolation-m9-opt-in",
    )
    sd_isolation_check(
        "microsd: slot@0 { reg = <0>; status = \"disabled\";" in
        m9_dts_compact,
        "sd-isolation-m9-slot0-disabled",
    )
    sd_isolation_check(
        "c6_sdio: slot@1 { reg = <1>; max-frequency = <20000000>; "
        "bus-width = <4>; non-removable;" in m9_dts_compact and
        "no-sd;" in m9_dts_compact,
        "sd-isolation-m9-slot1-retained",
    )
    sd_isolation_check(
        "espressif,allow-single-slot" not in m7_dts_text and
        'status = "disabled";' not in m7_dts_text,
        "sd-isolation-m7-unchanged",
    )

    firmware_dir = (
        ROOT / "buildroot-external" / "package" / "micronux-jd9365-firmware"
    )
    firmware_generator_path = firmware_dir / "generate_firmware.py"
    firmware_spec = importlib.util.spec_from_file_location(
        "micronux_jd9365_generate_firmware", firmware_generator_path
    )
    if firmware_spec is None or firmware_spec.loader is None:
        raise AssertionError("firmware-generator-import")
    firmware_generator = importlib.util.module_from_spec(firmware_spec)
    firmware_spec.loader.exec_module(firmware_generator)
    firmware_document = firmware_generator.load_source(
        firmware_dir / "jd9365-waveshare-10.1-v2.json"
    )
    firmware_blob = firmware_generator.build_blob(firmware_document)
    firmware_header = struct.Struct("<8sHHHHIIIHH")
    if len(firmware_blob) < firmware_header.size:
        raise AssertionError("firmware-binding-truncated-header")
    (
        firmware_magic,
        firmware_version,
        firmware_header_size,
        firmware_record_count,
        firmware_prelude_count,
        firmware_payload_size,
        firmware_payload_crc,
        firmware_flags,
        _,
        _,
    ) = firmware_header.unpack_from(firmware_blob)
    firmware_payload = firmware_blob[firmware_header_size:]
    expected_firmware_header = (
        b"MNJD9365",
        1,
        firmware_header.size,
        204,
        4,
        1020,
        COLD_PANEL_PAYLOAD_CRC32,
        0x7,
    )
    actual_firmware_header = (
        firmware_magic,
        firmware_version,
        firmware_header_size,
        firmware_record_count,
        firmware_prelude_count,
        firmware_payload_size,
        firmware_payload_crc,
        firmware_flags,
    )
    if actual_firmware_header != expected_firmware_header:
        raise AssertionError("firmware-binding-header")
    if (
        len(firmware_blob) != firmware_header_size + firmware_payload_size
        or zlib.crc32(firmware_payload) & 0xFFFFFFFF
        != COLD_PANEL_PAYLOAD_CRC32
    ):
        raise AssertionError("firmware-binding-payload")
    if hashlib.sha256(firmware_blob).hexdigest() != (
        "5f5d5d5fde2471130c3508160763235320854beb561dda049d066475e2bbf0f3"
    ):
        raise AssertionError("firmware-binding-sha256")
    firmware_test_text = (firmware_dir / "test_firmware.py").read_text(
        encoding="utf-8"
    )
    firmware_mk_text = (
        firmware_dir / "micronux-jd9365-firmware.mk"
    ).read_text(encoding="utf-8")
    required_firmware_test = (
        "EXPECTED_PAYLOAD_CRC32 = 0xCEA07F9B",
        'EXPECTED_BLOB_SHA256 = "5f5d5d5fde2471130c3508160763235320854beb561dda049d066475e2bbf0f3"',
        "if PINNED_SOURCE is not None:",
        '"optional upstream source snapshot for a full record-by-record "',
    )
    for token in required_firmware_test:
        if token not in firmware_test_text:
            raise AssertionError(f"firmware-test-binding-missing:{token}")
    for forbidden in ("managed_components", "--pinned-source"):
        if forbidden in firmware_mk_text:
            raise AssertionError(f"firmware-build-hidden-input:{forbidden}")
    defconfig_text = (
        ROOT
        / "buildroot-external"
        / "configs"
        / "micronux_esp32p4_gui_foundation_defconfig"
    ).read_text(encoding="utf-8")
    if "BR2_PACKAGE_MICRONUX_JD9365_FIRMWARE=y" not in defconfig_text:
        raise AssertionError("firmware-package-not-selected")
    return (
        len(required_added) + len(required_header) + len(required_pms)
        + len(required_loader) + len(required_harness)
        + stage_e_tests + native_irq_tests + native_diagnostic_tests
        + native_gdma_tests + native_scanout_tests + native_arm_tests
        + native_policy_tests + native_feed_tests + visible_epoch_tests
        + fault_evidence_tests + bandwidth_tests
        + sd_isolation_tests + continuous_prep_tests
        + len(required_dts) * len(device_trees)
        + len(required_cold_patch) + len(required_cold_patch_layout)
        + len(required_ldo_patch) + len(required_panel_patch) + 1
        + len(required_cold_loader) + len(required_packer)
        + len(required_loader_memory) + 2 * len(stage_builds)
        + len(required_m9_dts) + len(required_m9_resources)
        + len(required_firmware_test) + 13
    )


def main() -> int:
    tests = (
        test_contract() + test_cold_contract() + test_gdma_enable_semantics()
        + test_native_active_mirror_semantics()
        + test_memory_budget() + test_ownership() + test_hardware_parser()
        + bind_to_sources()
    )
    print(
        "MICRONUX:M9.2:MODEL state=pass "
        f"tests={tests} legacy_abi=2 cold_abi=3 cold_size=00c0 "
        "cold_flags=00003fff "
        "cold_page=1000 panel_payload_crc=cea07f9b kernel_end=49300000 "
        "buffers=3 rearm=fast-one-shot frame-irq=completion "
        "active-mirror=diagnostic-only policy-predicates=42 "
        "underflow-filler=black telemetry-epoch=reveal "
        "fault-evidence=preserved bandwidth-profile=60mhz-1000mbps "
        "m9-microsd=disabled c6-sdio=retained "
        "physical-long-run=pending"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, ContractError, OSError, struct.error) as error:
        raise SystemExit(f"MICRONUX:M9.2:MODEL state=fail reason={error}") from error
