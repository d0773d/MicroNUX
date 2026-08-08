#!/usr/bin/env python3
"""Reset the M6 network profile and prove native ESP32-C6 SDIO discovery."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import time
from pathlib import Path

import serial


REQUIRED_MARKERS = (
    "MICRONUX:M3:BOOT",
    "MICRONUX:M3:JUMP",
    "MICRONUX:M6:DSI state=disabled reason=profile-off",
    "MICRONUX:M6:NET transport=sdio slot=1 width=4",
    "Linux version 6.12.27",
    "ESP32-P4 synchronous-polled I/O enabled",
    "DW MMC controller at irq",
    "MCU firmware version: 2.11.5",
    "MICRONUX:M6:NET:DRIVER stack=esp-hosted-fg mode=builtin transport=sdio bluetooth=off",
    "MICRONUX:M6:NET:IFACE state=ready name=ethsta0",
    "MICRONUX:M6:NET:SHELL ready",
    "MICRONUX:M6:NET:PROBE:DONE",
    "MICRONUX:M6:NET:UP name=ethsta0",
    "MICRONUX:M6:NET:UP:RC=0",
    "MICRONUX:M6:NET:CONTROL:DONE",
)

FORBIDDEN_MARKERS = (
    "MICRONUX:M3:FAIL",
    "MICRONUX:M4:FAIL",
    "MICRONUX:M6:NET:FAIL",
    "Kernel panic",
    "Oops:",
    "BUG:",
    "Unhandled",
    "Drop invalid aggregate pkt",
)

CONTROL_LINE = (
    "for D in /sys/bus/sdio/devices/*; do "
    "echo MICRONUX:M6:NET:SDIO path=${D##*/}; "
    "for F in vendor device class modalias; do "
    "printf 'MICRONUX:M6:NET:SDIO %s=' $F; cat $D/$F; "
    "done; done; echo MICRONUX:M6:NET:PROBE:DONE; "
    "micronux-netctl up; echo MICRONUX:M6:NET:UP:RC=$?; "
    "ip link show ethsta0; echo MICRONUX:M6:NET:CONTROL:DONE\n"
)


def rom_reset(port: str) -> None:
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


def capture_boot(port: str, timeout: float) -> str:
    rom_reset(port)
    device = serial.Serial()
    device.port = port
    device.baudrate = 115200
    device.timeout = 0.1
    device.write_timeout = 10.0
    device.dtr = False
    device.rts = False
    device.open()

    try:
        device.reset_input_buffer()
        deadline = time.monotonic() + timeout
        captured = bytearray()
        probe_sent = False
        done_seen_at: float | None = None

        while time.monotonic() < deadline:
            chunk = device.read(4096)
            if chunk:
                captured.extend(chunk)
            text = captured.decode("utf-8", errors="replace")

            if (
                not probe_sent
                and "MICRONUX:M6:NET:SHELL ready" in text
                and re.search(
                    r"mmc0: new(?: .+)? SDIO card at address [0-9a-f]{4}", text
                )
            ):
                time.sleep(0.2)
                device.write(CONTROL_LINE.encode("ascii"))
                device.flush()
                probe_sent = True

            control_done = re.search(
                r"(?:^|\r?\n)MICRONUX:M6:NET:CONTROL:DONE\r?(?:\n|$)", text
            )
            if probe_sent and control_done is not None:
                if done_seen_at is None:
                    done_seen_at = time.monotonic()
                elif time.monotonic() - done_seen_at >= 0.4:
                    break

            if any(marker in text for marker in FORBIDDEN_MARKERS):
                time.sleep(0.2)
                break

        return captured.decode("utf-8", errors="replace")
    finally:
        device.dtr = False
        device.rts = False
        device.cancel_read()
        device.cancel_write()
        device.close()


def artifact_record(directory: Path) -> str:
    entries = []
    for name in ("Image", "esp32p4-micronux.dtb", "metadata.bin", "rootfs.cpio"):
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(f"missing M6 network artifact: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(f"{name}={path.stat().st_size}B:{digest}")
    return " ".join(entries)


def payload_hashes(log: str) -> tuple[str, str]:
    kernel_match = re.search(r"MICRONUX:M3:KERNEL .* sha256=([0-9a-f]{64})", log)
    dtb_match = re.search(r"MICRONUX:M3:DTB .* sha256=([0-9a-f]{64})", log)
    if kernel_match is None or dtb_match is None:
        raise ValueError("boot did not report payload hashes")
    return kernel_match.group(1), dtb_match.group(1)


def sdio_identity(log: str) -> tuple[tuple[str, str, str, str, str], ...]:
    card = re.search(
        r"mmc0: new(?: .+)? SDIO card at address ([0-9a-f]{4})", log
    )
    functions = re.findall(
        r"MICRONUX:M6:NET:SDIO path=([^\r\n ]+)\r?\n"
        r"MICRONUX:M6:NET:SDIO vendor=([^\r\n ]+)\r?\n"
        r"MICRONUX:M6:NET:SDIO device=([^\r\n ]+)\r?\n"
        r"MICRONUX:M6:NET:SDIO class=([^\r\n ]+)\r?\n"
        r"MICRONUX:M6:NET:SDIO modalias=([^\r\n ]+)",
        log,
    )
    if card is None or not functions:
        raise ValueError("C6 SDIO card/function identity was incomplete")
    return tuple(functions)


def control_identity(log: str) -> str:
    mac_pattern = r"([0-9a-f]{2}(?::[0-9a-f]{2}){5})"
    up = re.search(
        rf"MICRONUX:M6:NET:UP name=ethsta0 mac={mac_pattern}", log
    )
    links = re.findall(rf"link/ether {mac_pattern}", log)
    flag_sets = re.findall(r"ethsta0: <([^>]+)>", log)
    if up is None or not links or not flag_sets:
        raise ValueError("C6 RPC/link identity was incomplete")
    if up.group(1) != links[-1]:
        raise ValueError("C6 RPC/link MAC addresses did not agree")
    required_flags = {"UP", "LOWER_UP"}
    if not required_flags.issubset(set(flag_sets[-1].split(","))):
        raise ValueError("ethsta0 did not reach UP,LOWER_UP")
    return up.group(1)


def milestone_lines(log: str) -> list[str]:
    return [
        line.strip()
        for line in log.splitlines()
        if any(
            marker in line
            for marker in (
                "MICRONUX:M3:KERNEL",
                "MICRONUX:M3:DTB",
                "MICRONUX:M6:DSI",
                "MICRONUX:M6:NET",
                "Linux version",
                "synchronous-polled I/O",
                "DW MMC controller",
                "mmc0:",
                "console [ttyGS0]",
                "MCU firmware version",
            )
        )
    ]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--boots", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.boots < 1:
        parser.error("--boots must be at least 1")

    try:
        artifacts = artifact_record(args.artifact_dir.resolve())
    except OSError as error:
        print(error, file=sys.stderr)
        return 1

    expected_payload: tuple[str, str] | None = None
    expected_identity: tuple[tuple[str, str, str, str, str], ...] | None = None
    expected_mac: str | None = None

    for boot in range(1, args.boots + 1):
        log = capture_boot(args.port, args.timeout)
        print(f"--- M6 network boot {boot}/{args.boots} ---")
        lines = milestone_lines(log)
        print("\n".join(lines) if lines else "(no network/Linux milestones captured)")

        missing = [marker for marker in REQUIRED_MARKERS if marker not in log]
        forbidden = [marker for marker in FORBIDDEN_MARKERS if marker in log]
        if missing or forbidden:
            print("--- complete serial log ---", file=sys.stderr)
            print(log, file=sys.stderr)
            if missing:
                print(f"network boot {boot} missing: {', '.join(missing)}", file=sys.stderr)
            if forbidden:
                print(f"network boot {boot} rejected: {', '.join(forbidden)}", file=sys.stderr)
            return 1

        try:
            payload = payload_hashes(log)
            identity = sdio_identity(log)
            mac = control_identity(log)
        except ValueError as error:
            print(f"network boot {boot}: {error}", file=sys.stderr)
            return 1

        if expected_payload is None:
            expected_payload = payload
            expected_identity = identity
            expected_mac = mac
        elif (
            payload != expected_payload
            or identity != expected_identity
            or mac != expected_mac
        ):
            print(f"network identity changed across reset boot {boot}", file=sys.stderr)
            return 1

    assert (
        expected_payload is not None
        and expected_identity is not None
        and expected_mac is not None
    )
    print(f"M6 network artifacts: {artifacts}")
    identity_text = ",".join(
        f"{path}:{vendor}:{device}:{device_class}:{modalias}"
        for path, vendor, device, device_class, modalias in expected_identity
    )
    print(
        "M6 C6 SDIO/RPC/link gate passed: "
        f"boots={args.boots} functions={identity_text} "
        f"mac={expected_mac} "
        f"kernel_sha256={expected_payload[0]} dtb_sha256={expected_payload[1]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
