#!/usr/bin/env python3
"""Reset the M2 board repeatedly and validate the diagnostic handoff log."""

from __future__ import annotations

import argparse
import re
import sys
import time

import serial


REQUIRED_MARKERS = (
    "MICRONUX:M2:BOOT",
    "MICRONUX:M2:PSRAM ",
    "MICRONUX:M2:PSRAM_TEST",
    "MICRONUX:M2:KERNEL",
    "MICRONUX:M2:HANDOFF",
    "MICRONUX:M2:JUMP",
    "MICRONUX:M2:ENTRY",
    "MICRONUX:M2:PASS",
)


def reset_and_capture(port: str, timeout: float) -> str:
    device = serial.Serial()
    device.port = port
    device.baudrate = 115200
    device.timeout = 0.1
    device.dtr = False
    device.rts = False
    device.open()

    try:
        device.reset_input_buffer()
        device.dtr = False  # GPIO0 high: select normal SPI boot.
        device.rts = True   # EN low.
        time.sleep(0.1)
        device.rts = False  # EN high.

        deadline = time.monotonic() + timeout
        captured = bytearray()
        while time.monotonic() < deadline:
            chunk = device.read(device.in_waiting or 1)
            if chunk:
                captured.extend(chunk)
                text = captured.decode("utf-8", errors="replace")
                if "MICRONUX:M2:PASS" in text or "MICRONUX:M2:FAIL" in text:
                    time.sleep(0.1)
                    captured.extend(device.read(device.in_waiting))
                    return captured.decode("utf-8", errors="replace")
        return captured.decode("utf-8", errors="replace")
    finally:
        device.dtr = False
        device.rts = False
        device.close()


def marker_lines(log: str) -> list[str]:
    return [line.strip() for line in log.splitlines() if "MICRONUX:M2:" in line]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--boots", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    if args.boots < 1:
        parser.error("--boots must be at least 1")

    expected_crc: str | None = None
    expected_token: str | None = None

    for boot in range(1, args.boots + 1):
        log = reset_and_capture(args.port, args.timeout)
        lines = marker_lines(log)
        print(f"--- M2 boot {boot}/{args.boots} ---")
        print("\n".join(lines) if lines else "(no MicroNUX markers captured)")

        missing = [marker for marker in REQUIRED_MARKERS if marker not in log]
        if missing:
            print("--- complete serial log ---", file=sys.stderr)
            print(log, file=sys.stderr)
            print(f"M2 boot {boot} missing markers: {', '.join(missing)}", file=sys.stderr)
            return 1
        if "MICRONUX:M2:FAIL" in log:
            print(f"M2 boot {boot} reported failure", file=sys.stderr)
            return 1

        crc_match = re.search(r"MICRONUX:M2:HANDOFF .*crc32=([0-9a-fA-F]{8})", log)
        token_match = re.search(r"MICRONUX:M2:PASS token=([0-9a-fA-F]{8})", log)
        if crc_match is None or token_match is None:
            print(f"M2 boot {boot} did not expose its CRC/token", file=sys.stderr)
            return 1

        crc = crc_match.group(1).lower()
        token = token_match.group(1).lower()
        if expected_crc is None:
            expected_crc = crc
            expected_token = token
        elif crc != expected_crc or token != expected_token:
            print(
                f"M2 handoff changed across resets: "
                f"expected crc={expected_crc} token={expected_token}, "
                f"got crc={crc} token={token}",
                file=sys.stderr,
            )
            return 1

    print(
        f"M2 repeated-handoff test passed: boots={args.boots} "
        f"crc32={expected_crc} token={expected_token}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
