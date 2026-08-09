#!/usr/bin/env python3
"""Prove simultaneous microSD and ESP32-C6 networking on MicroNUX M6."""

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
    "MICRONUX:M6:SDMMC power=ldo4 voltage_mv=3300 slot=0 width=4",
    "MICRONUX:M6:NET transport=sdio slot=1 width=4",
    "MICRONUX:M6:SDMMC profile=dual slots=0,1 arbitration=linux-serialized",
    "Linux version 6.12.27",
    "ESP32-P4 dual-slot arbitration enabled for microSD and C6 SDIO",
    "mmcblk0:",
    "MCU firmware version: 2.11.5",
    "MICRONUX:M6:COMBINED:STORAGE state=ready name=mmcblk0",
    "MICRONUX:M6:COMBINED:IFACE state=ready name=ethsta0",
    "MICRONUX:M6:COMBINED:SHELL ready console=ttyGS0 network=nonblocking",
    "MICRONUX:M6:STORAGE:PASS",
    "MICRONUX:M6:COMBINED:STORAGE:RC=0",
    "MICRONUX:M6:NET:ONLINE state=ready name=ethsta0",
    "MICRONUX:M6:COMBINED:ONLINE:RC=0",
    "MICRONUX:M6:COMBINED:STORAGE-STRESS:RC=0",
    "MICRONUX:M6:COMBINED:NETWORK-STRESS:RC=0",
    "MICRONUX:M6:NET:DOWN state=disconnected name=ethsta0",
    "MICRONUX:M6:COMBINED:DOWN:RC=0",
    "MICRONUX:M6:COMBINED:RECONNECT:RC=0",
    "MICRONUX:M6:COMBINED:RECONNECT-NETWORK:RC=0",
    "MICRONUX:M6:COMBINED:DONE",
)

FORBIDDEN_MARKERS = (
    "MICRONUX:M3:FAIL",
    "MICRONUX:M4:FAIL",
    "MICRONUX:M5:FAIL",
    "MICRONUX:M6:FAIL",
    "MICRONUX:M6:COMBINED:FAIL",
    "MICRONUX:M6:COMBINED:SOAK:STALL",
    "MICRONUX:M6:STORAGE:FAIL",
    "Kernel panic",
    "Oops:",
    "BUG:",
    "Unhandled",
    "Drop invalid aggregate pkt",
)


def output_lines(log: str) -> list[str]:
    """Return device output without the interactive shell's echoed command."""
    return [
        line
        for line in log.splitlines()
        if not line.startswith(("/ # ", "micronux# "))
    ]


def marker_seen(log: str, marker: str) -> bool:
    return any(marker in line for line in output_lines(log))


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


def probe_command(write_test: bool, soak_cycles: int) -> str:
    storage_arg = " --write-test" if write_test else ""
    storage_stress = "".join(
        "dd if=/dev/mmcblk0 of=/dev/null bs=4096 count=64 2>/dev/null || RC=1; "
        for _ in range(8)
    )
    network_stress = "".join(
        "/bin/busybox ping 1.1.1.1 || RC=1; " for _ in range(12)
    )
    soak = ""
    if soak_cycles:
        cycle_arguments = " ".join(str(cycle) for cycle in range(1, soak_cycles + 1))
        soak = (
            f"/usr/bin/micronux-combined-soak {cycle_arguments}; "
            "echo MICRONUX:M6:COMBINED:SOAK:RC=$?; "
        )
    return (
        f"/usr/bin/micronux-storage-test{storage_arg}; "
        "echo MICRONUX:M6:COMBINED:STORAGE:RC=$?; "
        "for D in /sys/bus/sdio/devices/*; do "
        "echo MICRONUX:M6:COMBINED:SDIO path=${D##*/}; "
        "for F in vendor device class modalias; do "
        "printf 'MICRONUX:M6:COMBINED:SDIO %s=' $F; cat $D/$F; "
        "done; done; "
        "/usr/bin/micronux-online; "
        "echo MICRONUX:M6:COMBINED:ONLINE:RC=$?; "
        "ip -4 addr show dev ethsta0; ip route; cat /etc/resolv.conf; "
        "echo MICRONUX:M6:COMBINED:HASH-BEFORE; "
        "dd if=/dev/mmcblk0 bs=4096 count=256 2>/dev/null | sha256sum; "
        "(RC=0; "
        f"{storage_stress}"
        "echo MICRONUX:M6:COMBINED:STORAGE-STRESS:RC=$RC) & "
        "(RC=0; "
        f"{network_stress}"
        "echo MICRONUX:M6:COMBINED:NETWORK-STRESS:RC=$RC) & "
        "wait; "
        "echo MICRONUX:M6:COMBINED:HASH-AFTER; "
        "dd if=/dev/mmcblk0 bs=4096 count=256 2>/dev/null | sha256sum; "
        "echo MICRONUX:M6:COMBINED:HASH-AFTER:RC=$?; "
        "echo MICRONUX:M6:COMBINED:DOWN:BEGIN; "
        "/usr/bin/micronux-netctl down; "
        "echo MICRONUX:M6:COMBINED:DOWN:RC=$?; "
        "/bin/busybox sleep 5; "
        "/usr/bin/micronux-online; "
        "echo MICRONUX:M6:COMBINED:RECONNECT:RC=$?; "
        "/bin/busybox ping 1.1.1.1; "
        "echo MICRONUX:M6:COMBINED:RECONNECT-NETWORK:RC=$?; "
        "echo MICRONUX:M6:COMBINED:HASH-RECONNECT; "
        "dd if=/dev/mmcblk0 bs=4096 count=256 2>/dev/null | sha256sum; "
        f"{soak}"
        "echo MICRONUX:M6:COMBINED:DONE\n"
    )


def capture_boot(port: str, timeout: float, write_test: bool, soak_cycles: int) -> str:
    rom_reset(port)
    device = serial.Serial(
        port=port,
        baudrate=115200,
        timeout=0.1,
        write_timeout=10.0,
        dsrdtr=False,
        rtscts=False,
    )
    device.dtr = False
    device.rts = False

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
                and "MICRONUX:M6:COMBINED:SHELL ready" in text
                and "mmcblk0:" in text
                and re.search(
                    r"mmc1: new(?: .+)? SDIO card at address [0-9a-f]{4}", text
                )
            ):
                time.sleep(0.3)
                device.write(probe_command(write_test, soak_cycles).encode("ascii"))
                device.flush()
                probe_sent = True

            if marker_seen(text, "MICRONUX:M6:COMBINED:DONE"):
                if done_seen_at is None:
                    done_seen_at = time.monotonic()
                elif time.monotonic() - done_seen_at >= 0.5:
                    break

            rejected = next(
                (marker for marker in FORBIDDEN_MARKERS if marker_seen(text, marker)),
                None,
            )
            if rejected is not None:
                print(f"capture stopped on forbidden marker: {rejected}", file=sys.stderr)
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
            raise FileNotFoundError(f"missing M6 combined artifact: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(f"{name}={path.stat().st_size}B:{digest}")
    return " ".join(entries)


def payload_hashes(log: str) -> tuple[str, str]:
    kernel = re.search(r"MICRONUX:M3:KERNEL .* sha256=([0-9a-f]{64})", log)
    dtb = re.search(r"MICRONUX:M3:DTB .* sha256=([0-9a-f]{64})", log)
    if kernel is None or dtb is None:
        raise ValueError("boot did not report payload hashes")
    return kernel.group(1), dtb.group(1)


def identities(log: str) -> tuple[str, str]:
    sd = re.search(r"mmc0: new(?: .+)? (SDHC|SDXC|SD) card", log)
    sdio = re.search(r"mmc1: new(?: .+)? SDIO card at address ([0-9a-f]{4})", log)
    if sd is None or sdio is None:
        raise ValueError("microSD/SDIO logical-host identity was incomplete")
    return sd.group(1), sdio.group(1)


def sample_hashes(log: str) -> tuple[str, str, str]:
    before = re.search(
        r"MICRONUX:M6:COMBINED:HASH-BEFORE\r?\n([0-9a-f]{64})", log
    )
    after = re.search(
        r"MICRONUX:M6:COMBINED:HASH-AFTER\r?\n([0-9a-f]{64})", log
    )
    reconnect = re.search(
        r"MICRONUX:M6:COMBINED:HASH-RECONNECT\r?\n([0-9a-f]{64})", log
    )
    if before is None or after is None or reconnect is None:
        raise ValueError("concurrent storage hashes were missing")
    if before.group(1) != after.group(1) or before.group(1) != reconnect.group(1):
        raise ValueError("microSD sample changed during concurrent/reconnect traffic")
    return before.group(1), after.group(1), reconnect.group(1)


def milestone_lines(log: str) -> list[str]:
    return [
        line.strip()
        for line in output_lines(log)
        if any(
            marker in line
            for marker in (
                "MICRONUX:M3:KERNEL",
                "MICRONUX:M3:DTB",
                "MICRONUX:M6:SDMMC",
                "MICRONUX:M6:DSI",
                "MICRONUX:M6:NET",
                "MICRONUX:M6:COMBINED",
                "MICRONUX:M6:STORAGE",
                "dual-slot arbitration",
                "mmc0:",
                "mmc1:",
                "mmcblk0:",
                "MCU firmware version",
            )
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--boots", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=420.0)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--write-test", action="store_true")
    parser.add_argument("--soak-cycles", type=int, default=0)
    parser.add_argument(
        "--expect-mipi-adapter",
        choices=("present", "absent"),
        help="accept the read-only MIPI attachment-probe loader and require this result",
    )
    args = parser.parse_args()
    if args.boots < 1:
        parser.error("--boots must be at least 1")
    if args.write_test and args.boots != 1:
        parser.error("--write-test requires --boots 1")
    if args.soak_cycles < 0 or args.soak_cycles > 600:
        parser.error("--soak-cycles must be between 0 and 600")
    if args.soak_cycles and args.boots != 1:
        parser.error("--soak-cycles requires --boots 1")

    try:
        artifacts = artifact_record(args.artifact_dir.resolve())
    except OSError as error:
        print(error, file=sys.stderr)
        return 1

    expected_payload: tuple[str, str] | None = None
    expected_identity: tuple[str, str] | None = None
    expected_sample: str | None = None

    for boot in range(1, args.boots + 1):
        log = capture_boot(args.port, args.timeout, args.write_test, args.soak_cycles)
        print(f"--- M6 combined boot {boot}/{args.boots} ---")
        print("\n".join(milestone_lines(log)) or "(no combined milestones captured)")

        display_marker = (
            "MICRONUX:M6:DSI state=disabled reason=profile-off"
            if args.expect_mipi_adapter is None
            else (
                "MICRONUX:M6:DSI state=probe "
                f"adapter={args.expect_mipi_adapter} address=0x45"
            )
        )
        missing = [marker for marker in REQUIRED_MARKERS if not marker_seen(log, marker)]
        if not marker_seen(log, display_marker):
            missing.append(display_marker)
        forbidden = [
            marker for marker in FORBIDDEN_MARKERS if marker_seen(log, marker)
        ]
        if args.soak_cycles:
            soak_marker = (
                f"MICRONUX:M6:COMBINED:SOAK:PASS cycles={args.soak_cycles}"
            )
            if not marker_seen(log, soak_marker):
                missing.append(soak_marker)
            if not marker_seen(log, "MICRONUX:M6:COMBINED:SOAK:RC=0"):
                missing.append("MICRONUX:M6:COMBINED:SOAK:RC=0")
        if missing or forbidden:
            print("--- complete serial log ---", file=sys.stderr)
            print(log, file=sys.stderr)
            if missing:
                print(f"combined boot {boot} missing: {', '.join(missing)}", file=sys.stderr)
            if forbidden:
                print(f"combined boot {boot} rejected: {', '.join(forbidden)}", file=sys.stderr)
            return 1

        try:
            payload = payload_hashes(log)
            identity = identities(log)
            sample, _, _ = sample_hashes(log)
        except ValueError as error:
            print(f"combined boot {boot}: {error}", file=sys.stderr)
            return 1

        if expected_payload is None:
            expected_payload = payload
            expected_identity = identity
            expected_sample = sample
        elif (
            payload != expected_payload
            or identity != expected_identity
            or sample != expected_sample
        ):
            print(f"combined identity changed across reset boot {boot}", file=sys.stderr)
            return 1

    assert expected_payload and expected_identity and expected_sample
    print(f"M6 combined artifacts: {artifacts}")
    print(
        "M6 simultaneous storage/network gate passed: "
        f"boots={args.boots} sd={expected_identity[0]} "
        f"sdio_address={expected_identity[1]} sample_sha256={expected_sample} "
        f"kernel_sha256={expected_payload[0]} dtb_sha256={expected_payload[1]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
