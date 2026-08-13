#!/usr/bin/env python3
"""Validate and package the M3 Linux Image and DTB for the ESP-IDF loader."""

from __future__ import annotations

import argparse
import hashlib
import struct
import zlib
from pathlib import Path


MANIFEST_MAGIC = 0x33584E4D
MANIFEST_ABI = 1
MANIFEST_SIZE = 128
MANIFEST_FLAG_DTB = 1
KERNEL_LOAD_ADDRESS = 0x48400000
COMMS_RESERVE_ADDRESS = 0x49F00000
RISCV_IMAGE_MAGIC = b"RISCV\0\0\0"
FDT_MAGIC = b"\xd0\x0d\xfe\xed"


def parse_address(value: str) -> int:
    return int(value, 0)


def read_image(
    path: Path, max_memory_end: int = COMMS_RESERVE_ADDRESS
) -> tuple[bytes, int]:
    image = path.read_bytes()
    if len(image) < 64:
        raise ValueError(f"Linux Image is too short: {len(image)} bytes")

    text_offset, memory_size = struct.unpack_from("<QQ", image, 8)
    if image[48:56] != RISCV_IMAGE_MAGIC:
        raise ValueError("Linux Image has no RISC-V Image header magic")
    if text_offset != 0:
        raise ValueError(f"NOMMU Image text offset must be zero, got {text_offset:#x}")
    if memory_size < len(image):
        raise ValueError(
            f"Image memory span {memory_size:#x} is smaller than file {len(image):#x}"
        )
    if KERNEL_LOAD_ADDRESS + memory_size > max_memory_end:
        raise ValueError(
            f"Image memory span ends at {KERNEL_LOAD_ADDRESS + memory_size:#010x}, "
            f"past the configured boundary {max_memory_end:#010x}"
        )
    return image, memory_size


def read_dtb(path: Path) -> bytes:
    dtb = path.read_bytes()
    if len(dtb) < 40 or dtb[:4] != FDT_MAGIC:
        raise ValueError("input is not a flattened device tree")
    declared_size = struct.unpack_from(">I", dtb, 4)[0]
    if declared_size != len(dtb):
        raise ValueError(
            f"DTB header size {declared_size} does not match file size {len(dtb)}"
        )
    return dtb


def make_manifest(image: bytes, memory_size: int, dtb: bytes) -> bytes:
    prefix = struct.pack(
        "<8I",
        MANIFEST_MAGIC,
        MANIFEST_ABI,
        MANIFEST_SIZE,
        MANIFEST_FLAG_DTB,
        KERNEL_LOAD_ADDRESS,
        len(image),
        memory_size,
        len(dtb),
    )
    payload = (
        prefix
        + hashlib.sha256(image).digest()
        + hashlib.sha256(dtb).digest()
        + bytes(7 * 4)
    )
    if len(payload) != MANIFEST_SIZE - 4:
        raise AssertionError(f"manifest payload is {len(payload)} bytes")
    return payload + struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--dtb", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--max-memory-end",
        type=parse_address,
        default=COMMS_RESERVE_ADDRESS,
        help="exclusive virtual-address ceiling for the Image memory span",
    )
    args = parser.parse_args()

    image, memory_size = read_image(args.image, args.max_memory_end)
    dtb = read_dtb(args.dtb)
    manifest = make_manifest(image, memory_size, dtb)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(manifest)

    print(
        "M3 payload: "
        f"load={KERNEL_LOAD_ADDRESS:#010x} "
        f"file={len(image)} memory={memory_size} dtb={len(dtb)} "
        f"manifest={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
