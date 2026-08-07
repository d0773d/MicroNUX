#!/usr/bin/env python3
"""Reset the M3 board and validate Linux reaches the embedded initramfs."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time

import serial


REQUIRED_MARKERS = (
    "MICRONUX:M3:BOOT",
    "MICRONUX:M3:CLINT",
    "MICRONUX:M3:MANIFEST",
    "MICRONUX:M3:KERNEL",
    "MICRONUX:M3:DTB",
    "MICRONUX:M3:JUMP",
    "MICRONUX:M3:PMP entries=13,14 linux=[48400000,49f00000) config=8f",
    "Linux version 6.12.27",
    "timer running at 360000000 Hz",
    "Run /init as init process",
)

FORBIDDEN_MARKERS = (
    "MICRONUX:M3:FAIL",
    "Kernel panic",
    "Oops:",
    "BUG:",
    "Unhandled",
    "timer stall",
)


def rom_reset(port: str) -> None:
    """Enter the ROM loader, identify the P4, and leave it in hard reset."""
    subprocess.run(
        [
            sys.executable,
            "-m",
            "esptool",
            "--chip",
            "esp32p4",
            "--port",
            port,
            "--before",
            "default-reset",
            "--after",
            "hard-reset",
            "chip-id",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def reset_and_capture(port: str, timeout: float, reset: bool = True) -> str:
    if reset:
        # Entering the ROM before the hard reset remains reliable after Linux
        # has replaced IDF and no watchdog is available to recycle the chip.
        rom_reset(port)

    device = serial.Serial()
    device.port = port
    device.baudrate = 115200
    device.timeout = 0.1
    device.dtr = False
    device.rts = False
    device.open()

    try:
        device.reset_input_buffer()
        deadline = time.monotonic() + timeout
        init_seen_at: float | None = None
        captured = bytearray()
        while time.monotonic() < deadline:
            chunk = device.read(device.in_waiting or 1)
            if chunk:
                captured.extend(chunk)
                text = captured.decode("utf-8", errors="replace")
                if "Run /init as init process" in text and init_seen_at is None:
                    init_seen_at = time.monotonic()
            if init_seen_at is not None and time.monotonic() - init_seen_at >= 2.0:
                break
        return captured.decode("utf-8", errors="replace")
    finally:
        device.dtr = False
        device.rts = False
        device.close()


def milestone_lines(log: str) -> list[str]:
    useful = (
        "MICRONUX:M3:",
        "Linux version",
        "timer running at",
        "initramfs",
        "Run /init",
    )
    return [line.strip() for line in log.splitlines() if any(x in line for x in useful)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--boots", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--skip-reset", action="store_true")
    args = parser.parse_args()
    if args.boots < 1:
        parser.error("--boots must be at least 1")
    if args.skip_reset and args.boots != 1:
        parser.error("--skip-reset is diagnostic-only and requires --boots 1")

    expected_kernel_hash: str | None = None
    expected_dtb_hash: str | None = None
    for boot in range(1, args.boots + 1):
        log = reset_and_capture(args.port, args.timeout, reset=not args.skip_reset)
        print(f"--- M3 boot {boot}/{args.boots} ---")
        lines = milestone_lines(log)
        print("\n".join(lines) if lines else "(no M3/Linux milestones captured)")

        missing = [marker for marker in REQUIRED_MARKERS if marker not in log]
        forbidden = [marker for marker in FORBIDDEN_MARKERS if marker in log]
        if missing or forbidden:
            print("--- complete serial log ---", file=sys.stderr)
            print(log, file=sys.stderr)
            if missing:
                print(f"M3 boot {boot} missing: {', '.join(missing)}", file=sys.stderr)
            if forbidden:
                print(f"M3 boot {boot} rejected: {', '.join(forbidden)}", file=sys.stderr)
            return 1

        kernel_match = re.search(r"MICRONUX:M3:KERNEL .* sha256=([0-9a-f]{64})", log)
        dtb_match = re.search(r"MICRONUX:M3:DTB .* sha256=([0-9a-f]{64})", log)
        if kernel_match is None or dtb_match is None:
            print(f"M3 boot {boot} did not report payload hashes", file=sys.stderr)
            return 1

        kernel_hash = kernel_match.group(1)
        dtb_hash = dtb_match.group(1)
        if expected_kernel_hash is None:
            expected_kernel_hash = kernel_hash
            expected_dtb_hash = dtb_hash
        elif kernel_hash != expected_kernel_hash or dtb_hash != expected_dtb_hash:
            print(f"M3 payload changed across reset boot {boot}", file=sys.stderr)
            return 1

    print(
        f"M3 hardware gate passed: boots={args.boots} "
        f"kernel_sha256={expected_kernel_hash} dtb_sha256={expected_dtb_hash}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
