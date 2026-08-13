#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Generate and validate the bounded MicroNUX JD9365 command artifact."""

import argparse
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path


MAGIC = b"MNJD9365"
FORMAT_VERSION = 1
FLAGS = 0x7  # DCS records, delay after write, write-only
HEADER = struct.Struct("<8sHHHHIIIHH")
RECORD_HEADER = struct.Struct("<BBH")

MAX_RECORDS = 512
MAX_PAYLOAD_BYTES = 65536
MAX_PARAM_BYTES = 64
MAX_DELAY_MS = 5000


class FirmwareFormatError(ValueError):
    """The source or encoded firmware violates the format contract."""


def _integer(value, field):
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError as exc:
            raise FirmwareFormatError("invalid {}: {!r}".format(field, value)) from exc
    raise FirmwareFormatError("invalid {} type".format(field))


def _record(item, index):
    command = _integer(item.get("command"), "record {} command".format(index))
    delay_ms = _integer(item.get("delay_ms"), "record {} delay".format(index))
    data_text = item.get("data")
    if not isinstance(data_text, str):
        raise FirmwareFormatError("record {} data must be a hex string".format(index))
    try:
        data = bytes.fromhex(data_text)
    except ValueError as exc:
        raise FirmwareFormatError("record {} data is not hexadecimal".format(index)) from exc
    if not 0 <= command <= 0xFF:
        raise FirmwareFormatError("record {} command is out of range".format(index))
    if len(data) > MAX_PARAM_BYTES:
        raise FirmwareFormatError("record {} has too many parameter bytes".format(index))
    if not 0 <= delay_ms <= MAX_DELAY_MS:
        raise FirmwareFormatError("record {} delay is out of range".format(index))
    return {"command": command, "data": data, "delay_ms": delay_ms}


def normalize_source(document):
    if document.get("schema") != "micronux-jd9365-command-source-v1":
        raise FirmwareFormatError("unknown source schema")
    if document.get("license") != "Apache-2.0":
        raise FirmwareFormatError("source must retain its Apache-2.0 license")

    prelude_items = document.get("prelude")
    vendor_items = document.get("vendor_records")
    if not isinstance(prelude_items, list) or not isinstance(vendor_items, list):
        raise FirmwareFormatError("prelude and vendor_records must be arrays")

    expected = document.get("profile", {})
    if len(prelude_items) != expected.get("prelude_records"):
        raise FirmwareFormatError("prelude record count mismatch")
    if len(vendor_items) != expected.get("vendor_records"):
        raise FirmwareFormatError("vendor record count mismatch")

    records = [
        _record(item, index)
        for index, item in enumerate(prelude_items + vendor_items)
    ]
    if not 0 < len(records) <= MAX_RECORDS:
        raise FirmwareFormatError("total record count is out of range")
    return records, len(prelude_items)


def load_source(path):
    with Path(path).open("r", encoding="utf-8") as stream:
        return json.load(stream)


def build_blob(document):
    records, prelude_count = normalize_source(document)
    payload_parts = []
    for record in records:
        data = record["data"]
        payload_parts.append(
            RECORD_HEADER.pack(record["command"], len(data), record["delay_ms"])
            + data
        )
    payload = b"".join(payload_parts)
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise FirmwareFormatError("record payload is too large")

    payload_crc32 = zlib.crc32(payload) & 0xFFFFFFFF
    max_params = max(len(record["data"]) for record in records)
    max_delay = max(record["delay_ms"] for record in records)
    header = HEADER.pack(
        MAGIC,
        FORMAT_VERSION,
        HEADER.size,
        len(records),
        prelude_count,
        len(payload),
        payload_crc32,
        FLAGS,
        max_params,
        max_delay,
    )
    return header + payload


def parse_blob(blob):
    if len(blob) < HEADER.size:
        raise FirmwareFormatError("truncated header")
    (
        magic,
        version,
        header_bytes,
        record_count,
        prelude_count,
        payload_bytes,
        payload_crc32,
        flags,
        encoded_max_params,
        encoded_max_delay,
    ) = HEADER.unpack_from(blob)

    if magic != MAGIC or version != FORMAT_VERSION or header_bytes != HEADER.size:
        raise FirmwareFormatError("unsupported header")
    if flags != FLAGS:
        raise FirmwareFormatError("unsupported flags")
    if not 0 < record_count <= MAX_RECORDS or prelude_count > record_count:
        raise FirmwareFormatError("record count is out of range")
    if payload_bytes > MAX_PAYLOAD_BYTES or len(blob) != header_bytes + payload_bytes:
        raise FirmwareFormatError("payload length mismatch")

    payload = blob[header_bytes:]
    if zlib.crc32(payload) & 0xFFFFFFFF != payload_crc32:
        raise FirmwareFormatError("payload CRC32 mismatch")

    records = []
    offset = 0
    for index in range(record_count):
        if len(payload) - offset < RECORD_HEADER.size:
            raise FirmwareFormatError("record {} header is truncated".format(index))
        command, param_bytes, delay_ms = RECORD_HEADER.unpack_from(payload, offset)
        offset += RECORD_HEADER.size
        if param_bytes > MAX_PARAM_BYTES or delay_ms > MAX_DELAY_MS:
            raise FirmwareFormatError("record {} exceeds format bounds".format(index))
        if len(payload) - offset < param_bytes:
            raise FirmwareFormatError("record {} data is truncated".format(index))
        data = payload[offset:offset + param_bytes]
        offset += param_bytes
        records.append({"command": command, "data": data, "delay_ms": delay_ms})
    if offset != len(payload):
        raise FirmwareFormatError("trailing payload bytes")

    actual_max_params = max(len(record["data"]) for record in records)
    actual_max_delay = max(record["delay_ms"] for record in records)
    if encoded_max_params != actual_max_params or encoded_max_delay != actual_max_delay:
        raise FirmwareFormatError("encoded maxima do not match records")

    return {
        "record_count": record_count,
        "prelude_count": prelude_count,
        "payload_bytes": payload_bytes,
        "payload_crc32": payload_crc32,
        "flags": flags,
        "max_params": actual_max_params,
        "max_delay_ms": actual_max_delay,
        "records": records,
    }


def main():
    directory = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=directory / "jd9365-waveshare-10.1-v2.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=directory / "jd9365-waveshare-10.1-v2.bin",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that the existing output exactly matches regenerated bytes",
    )
    args = parser.parse_args()

    blob = build_blob(load_source(args.source))
    decoded = parse_blob(blob)
    if args.check:
        try:
            existing = args.output.read_bytes()
        except OSError as exc:
            parser.error("cannot read {}: {}".format(args.output, exc))
        if existing != blob:
            parser.error("{} is not the deterministic generator output".format(args.output))
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(blob)

    print(
        "records={} prelude={} bytes={} payload_crc32={:08x} sha256={}".format(
            decoded["record_count"],
            decoded["prelude_count"],
            len(blob),
            decoded["payload_crc32"],
            hashlib.sha256(blob).hexdigest(),
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
