#!/usr/bin/env python3
"""Fail if a bFLT image cannot be split at an ESP32-P4 PMP boundary."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


HEADER_SIZE = 44
PMP_GRANULE = 128
FLAT_FLAG_RAM = 0x1


def validate(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    with path.open("rb") as binary:
        header = binary.read(HEADER_SIZE)
    if len(header) < 4 or header[:4] != b"bFLT":
        return False
    if len(header) != HEADER_SIZE:
        raise ValueError(f"{path}: truncated bFLT header")

    fields = struct.unpack(">10I", header[4:])
    revision, entry, data_start = fields[:3]
    flags = fields[8]
    if revision != 4 or flags & FLAT_FLAG_RAM == 0:
        raise ValueError(f"{path}: expected bFLT v4 FLAT_FLAG_RAM")
    if entry < HEADER_SIZE or entry >= data_start:
        raise ValueError(f"{path}: invalid entry/data boundary")
    if data_start % PMP_GRANULE != 0:
        raise ValueError(
            f"{path}: data_start 0x{data_start:x} is not "
            f"{PMP_GRANULE}-byte aligned"
        )
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", type=Path, required=True)
    args = parser.parse_args()
    root = args.tree.resolve()
    if not root.is_dir():
        parser.error(f"target tree does not exist: {root}")

    checked = sum(validate(path) for path in root.rglob("*"))
    if checked == 0:
        raise ValueError(f"{root}: no bFLT images found")
    print(
        "MICRONUX:M7:BFLT-WX-AUDIT "
        f"state=pass files={checked} granule={PMP_GRANULE}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
