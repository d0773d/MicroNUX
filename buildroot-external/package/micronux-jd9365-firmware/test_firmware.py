#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0

"""Host-side contract tests for the MicroNUX JD9365 firmware artifact."""

import argparse
import copy
import hashlib
import re
import sys
import unittest
from pathlib import Path

import generate_firmware as firmware


EXPECTED_SOURCE_SHA256 = "c80f8ae4771adf46adabbcf3fedca73262504f068387b713c358476cb1629585"
EXPECTED_SOURCE_COMMIT = "9daffb7168b3c093a330da700988a22092294eef"
EXPECTED_SOURCE_PATH = "display/lcd/esp_lcd_jd9365_10_1/esp_lcd_jd9365_10_1.c"
EXPECTED_RECORD_COUNT = 204
EXPECTED_PRELUDE_COUNT = 4
EXPECTED_VENDOR_COUNT = 200
EXPECTED_PAYLOAD_BYTES = 1020
EXPECTED_TOTAL_BYTES = 1052
EXPECTED_PAYLOAD_CRC32 = 0xCEA07F9B
EXPECTED_BLOB_SHA256 = "5f5d5d5fde2471130c3508160763235320854beb561dda049d066475e2bbf0f3"

SOURCE_JSON = None
BINARY = None
PINNED_SOURCE = None


def _active_vendor_records(source_text):
    marker = "static const jd9365_lcd_init_cmd_t vendor_specific_init_default[]"
    start = source_text.index(marker)
    end = source_text.index("\n};", start)
    table = source_text[start:end]
    table = re.sub(r"/\*.*?\*/", "", table, flags=re.DOTALL)
    table = re.sub(r"//[^\r\n]*", "", table)
    pattern = re.compile(
        r"\{\s*(0x[0-9a-fA-F]{2})\s*,\s*"
        r"\(uint8_t\[\]\)\s*\{\s*(0x[0-9a-fA-F]{2})\s*\}\s*,\s*"
        r"(\d+)\s*,\s*(\d+)\s*\}"
    )
    records = []
    for command, parameter, data_bytes, delay_ms in pattern.findall(table):
        if int(data_bytes) != 1:
            raise AssertionError("pinned table contains a non-one-byte record")
        records.append((int(command, 0), bytes([int(parameter, 0)]), int(delay_ms)))
    return records


class FirmwareArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = firmware.load_source(SOURCE_JSON)
        cls.generated = firmware.build_blob(cls.document)
        cls.decoded = firmware.parse_blob(cls.generated)
        cls.checked_in = BINARY.read_bytes()

    def test_pinned_source_provenance_and_vendor_records(self):
        metadata = self.document["source"]
        self.assertEqual(metadata["commit"], EXPECTED_SOURCE_COMMIT)
        self.assertEqual(metadata["path"], EXPECTED_SOURCE_PATH)
        self.assertEqual(metadata["source_file_sha256"], EXPECTED_SOURCE_SHA256)

        records, prelude_count = firmware.normalize_source(self.document)
        represented_vendor = [
            (record["command"], record["data"], record["delay_ms"])
            for record in records[prelude_count:]
        ]
        self.assertEqual(len(represented_vendor), EXPECTED_VENDOR_COUNT)
        if PINNED_SOURCE is not None:
            source_bytes = PINNED_SOURCE.read_bytes()
            self.assertEqual(
                hashlib.sha256(source_bytes).hexdigest(), EXPECTED_SOURCE_SHA256
            )
            pinned_vendor = _active_vendor_records(source_bytes.decode("utf-8"))
            self.assertEqual(len(pinned_vendor), EXPECTED_VENDOR_COUNT)
            self.assertEqual(represented_vendor, pinned_vendor)

    def test_record_count_length_crc_and_hash(self):
        self.assertEqual(self.decoded["record_count"], EXPECTED_RECORD_COUNT)
        self.assertEqual(self.decoded["prelude_count"], EXPECTED_PRELUDE_COUNT)
        self.assertEqual(
            self.decoded["record_count"] - self.decoded["prelude_count"],
            EXPECTED_VENDOR_COUNT,
        )
        self.assertEqual(self.decoded["payload_bytes"], EXPECTED_PAYLOAD_BYTES)
        self.assertEqual(len(self.generated), EXPECTED_TOTAL_BYTES)
        self.assertEqual(self.decoded["payload_crc32"], EXPECTED_PAYLOAD_CRC32)
        self.assertEqual(hashlib.sha256(self.generated).hexdigest(), EXPECTED_BLOB_SHA256)
        self.assertEqual(self.decoded["max_params"], 1)
        self.assertEqual(self.decoded["max_delay_ms"], 120)

    def test_exact_prelude_and_delays(self):
        records = self.decoded["records"]
        prelude = [
            (record["command"], record["data"], record["delay_ms"])
            for record in records[:EXPECTED_PRELUDE_COUNT]
        ]
        self.assertEqual(
            prelude,
            [
                (0xE0, b"\x00", 0),
                (0x36, b"\x00", 0),
                (0x3A, b"\x55", 0),
                (0x80, b"\x01", 0),
            ],
        )
        delayed = [
            (index, record["command"], record["data"], record["delay_ms"])
            for index, record in enumerate(records)
            if record["delay_ms"]
        ]
        self.assertEqual(
            delayed,
            [(202, 0x11, b"\x00", 120), (203, 0x29, b"\x00", 20)],
        )
        self.assertEqual(sum(record["delay_ms"] for record in records), 140)

    def test_generator_is_deterministic_and_binary_is_current(self):
        self.assertEqual(self.generated, firmware.build_blob(self.document))
        self.assertEqual(self.checked_in, self.generated)

    def test_parser_rejects_crc_corruption(self):
        corrupted = bytearray(self.generated)
        corrupted[-1] ^= 0x01
        with self.assertRaisesRegex(firmware.FirmwareFormatError, "CRC32"):
            firmware.parse_blob(bytes(corrupted))

    def test_generator_enforces_record_parameter_and_delay_bounds(self):
        too_many = copy.deepcopy(self.document)
        required_vendor_count = firmware.MAX_RECORDS + 1 - EXPECTED_PRELUDE_COUNT
        too_many["vendor_records"] = (
            too_many["vendor_records"]
            + [too_many["vendor_records"][0]]
            * (required_vendor_count - len(too_many["vendor_records"]))
        )
        too_many["profile"]["vendor_records"] = required_vendor_count
        with self.assertRaisesRegex(firmware.FirmwareFormatError, "record count"):
            firmware.build_blob(too_many)

        too_wide = copy.deepcopy(self.document)
        too_wide["vendor_records"][0]["data"] = "00" * (firmware.MAX_PARAM_BYTES + 1)
        with self.assertRaisesRegex(firmware.FirmwareFormatError, "parameter"):
            firmware.build_blob(too_wide)

        too_slow = copy.deepcopy(self.document)
        too_slow["vendor_records"][0]["delay_ms"] = firmware.MAX_DELAY_MS + 1
        with self.assertRaisesRegex(firmware.FirmwareFormatError, "delay"):
            firmware.build_blob(too_slow)


def main():
    global SOURCE_JSON, BINARY, PINNED_SOURCE

    directory = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=directory / "jd9365-waveshare-10.1-v2.json",
    )
    parser.add_argument(
        "--binary",
        type=Path,
        default=directory / "jd9365-waveshare-10.1-v2.bin",
    )
    parser.add_argument(
        "--pinned-source",
        type=Path,
        default=None,
        help=(
            "optional upstream source snapshot for a full record-by-record "
            "provenance audit"
        ),
    )
    args = parser.parse_args()
    SOURCE_JSON = args.source.resolve()
    BINARY = args.binary.resolve()
    PINNED_SOURCE = (
        args.pinned_source.resolve() if args.pinned_source is not None else None
    )

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(FirmwareArtifactTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print(
            "MICRONUX:JD9365-FIRMWARE-TEST state=pass tests={} records={} crc32={:08x}".format(
                result.testsRun,
                EXPECTED_RECORD_COUNT,
                EXPECTED_PAYLOAD_CRC32,
            )
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
