#!/usr/bin/env python3
"""Exercise the MicroNUX device-service ABI on physical ESP32-P4 hardware."""

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
    "MICRONUX:M8:SERVICE state=ready abi=1.0",
    "MICRONUX:M8:SHELL:API:RC=0",
    "MICRONUX:M8:SHELL:LIST:RC=0",
    "MICRONUX:M8:NATIVE:PASS abi=1.0",
    "MICRONUX:M8:SHELL:NATIVE:RC=0",
    "MICRONUX:M8:IGNITE:PASS abi=1.0 uid=65534",
    "MICRONUX:M8:SHELL:IGNITE:RC=0",
    "MICRONUX:M8:IGNITE:FAULT fault=bad operand",
    "MICRONUX:M8:SHELL:IGNITE-FAULT:RC=1",
    "MICRONUX:M8:SHELL:IGNITE-RECOVERY:RC=0",
    "MICRONUX:M8:PERMISSION:PASS uid=65534 observe=allowed admin=denied",
    "MICRONUX:M8:AUDIT uid=65534 op=4 decision=deny",
    "MICRONUX:M8:SHELL:PERMISSION:RC=0",
    "MICRONUX:M8:RAW-MMIO:PASS devmem=absent devkmem=absent",
    "MICRONUX:M8:CONCURRENT:RC=0",
    "MICRONUX:M8:WAIT:RC=75",
    "MICRONUX:M8:APP-CRASH:RC=0",
    "MICRONUX:M8:SERVICE restart status=137",
    "MICRONUX:M8:SERVICE-RECOVERY:RC=0",
    "MICRONUX:M8:DONE",
)

FORBIDDEN_MARKERS = (
    "MICRONUX:M8:SERVICE:FAIL",
    "MICRONUX:M3:FAIL",
    "Kernel panic",
    "Oops:",
    "BUG:",
    "Unhandled",
)


def output_lines(log: str) -> list[str]:
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


def test_command() -> str:
    return (
        "/usr/bin/micronux-device api; "
        "echo MICRONUX:M8:SHELL:API:RC=$?; "
        "/usr/bin/micronux-device list --json; "
        "echo MICRONUX:M8:SHELL:LIST:RC=$?; "
        "/usr/bin/micronux-device-native; "
        "echo MICRONUX:M8:SHELL:NATIVE:RC=$?; "
        "/usr/bin/micronux-ignite; "
        "echo MICRONUX:M8:SHELL:IGNITE:RC=$?; "
        "/usr/bin/micronux-ignite "
        "/usr/share/micronux/ignite/device-fault.igpk; "
        "echo MICRONUX:M8:SHELL:IGNITE-FAULT:RC=$?; "
        "/usr/bin/micronux-device api; "
        "echo MICRONUX:M8:SHELL:IGNITE-RECOVERY:RC=$?; "
        "/usr/bin/micronux-device-selftest; "
        "echo MICRONUX:M8:SHELL:PERMISSION:RC=$?; "
        "if [ ! -e /dev/mem ] && [ ! -e /dev/kmem ]; then "
        "echo MICRONUX:M8:RAW-MMIO:PASS devmem=absent devkmem=absent; "
        "else echo MICRONUX:M8:RAW-MMIO:FAIL; fi; "
        "/bin/busybox ip link set ethsta0 down; "
        "(/usr/bin/micronux-device wait network 2000; "
        "echo MICRONUX:M8:WAIT:RC=$?) & "
        "wait_pid=$!; "
        "/usr/bin/micronux-device api; "
        "echo MICRONUX:M8:CONCURRENT:RC=$?; wait $wait_pid; "
        "/usr/bin/micronux-device wait network 10000 >/dev/null 2>&1 & "
        "app_pid=$!; /bin/busybox sleep 1; "
        "/bin/busybox kill -KILL $app_pid; /bin/busybox sleep 1; "
        "/usr/bin/micronux-device api; "
        "echo MICRONUX:M8:APP-CRASH:RC=$?; "
        "service_pid=$(/bin/busybox cat /run/micronux/deviced.pid); "
        "/bin/busybox kill -KILL $service_pid; /bin/busybox sleep 3; "
        "/usr/bin/micronux-device api; "
        "echo MICRONUX:M8:SERVICE-RECOVERY:RC=$?; "
        "echo MICRONUX:M8:DONE\n"
    )


def capture(port: str, timeout: float) -> str:
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
    captured = bytearray()
    deadline = time.monotonic() + timeout
    command_sent = False
    done_seen_at: float | None = None

    try:
        device.reset_input_buffer()
        while time.monotonic() < deadline:
            captured.extend(device.read(4096))
            text = captured.decode("utf-8", errors="replace")
            if (
                not command_sent
                and "MICRONUX:M6:COMBINED:SHELL ready" in text
                and "MICRONUX:M8:SERVICE state=ready" in text
            ):
                time.sleep(0.2)
                device.write(test_command().encode("ascii"))
                device.flush()
                command_sent = True
            if marker_seen(text, "MICRONUX:M8:DONE"):
                if done_seen_at is None:
                    done_seen_at = time.monotonic()
                elif time.monotonic() - done_seen_at >= 0.5:
                    break
            if any(marker_seen(text, marker) for marker in FORBIDDEN_MARKERS):
                time.sleep(0.2)
                break
        return captured.decode("utf-8", errors="replace")
    finally:
        device.dtr = False
        device.rts = False
        device.cancel_read()
        device.cancel_write()
        device.close()


def artifact_digest(directory: Path) -> tuple[str, tuple[str, str]]:
    records = []
    digests: dict[str, str] = {}
    for name in ("Image", "esp32p4-micronux.dtb", "metadata.bin", "rootfs.cpio"):
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        digests[name] = digest
        records.append(f"{name}={path.stat().st_size}B:{digest}")
    return " ".join(records), (
        digests["Image"],
        digests["esp32p4-micronux.dtb"],
    )


def payload_hashes(log: str) -> tuple[str, str]:
    kernel = re.search(r"MICRONUX:M3:KERNEL .* sha256=([0-9a-f]{64})", log)
    dtb = re.search(r"MICRONUX:M3:DTB .* sha256=([0-9a-f]{64})", log)
    if kernel is None or dtb is None:
        raise ValueError("boot did not report payload hashes")
    return kernel.group(1), dtb.group(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--expect-m7-early-deny", action="store_true")
    parser.add_argument(
        "--expect-mipi-profile",
        choices=("jd9365",),
        help="require the exact Kit C loader scanout profile",
    )
    args = parser.parse_args()

    try:
        artifacts, artifact_payload = artifact_digest(args.artifact_dir.resolve())
        log = capture(args.port, args.timeout)
    except (OSError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        return 1
    if args.log is not None:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        args.log.write_text(log, encoding="utf-8")

    missing = [marker for marker in REQUIRED_MARKERS if not marker_seen(log, marker)]
    if args.expect_m7_early_deny and not marker_seen(
        log,
        "MICRONUX:M7:PMP baseline=pass early-deny=pass "
        "handoff=13-14-unlocked overlay=13-14",
    ):
        missing.append(
            "MICRONUX:M7:PMP baseline=pass early-deny=pass "
            "handoff=13-14-unlocked overlay=13-14"
        )
    if args.expect_mipi_profile == "jd9365" and not marker_seen(
        log,
        "MICRONUX:M6:DSI state=ready profile=jd9365-800x1280 "
        "resolution=800x1280 lanes=2 lane_mbps=1500 dpi_mhz=80 "
        "format=rgb565 "
        "pattern=framebuffer",
    ):
        missing.append("MICRONUX:M6:DSI exact Kit C JD9365 profile")
    forbidden = [marker for marker in FORBIDDEN_MARKERS if marker_seen(log, marker)]
    try:
        booted_payload = payload_hashes(log)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    if booted_payload != artifact_payload:
        print(
            f"booted payload {booted_payload} does not match artifacts "
            f"{artifact_payload}",
            file=sys.stderr,
        )
        return 1
    print("\n".join(line for line in output_lines(log) if "MICRONUX:M8:" in line))
    if missing or forbidden:
        print("--- complete serial log ---", file=sys.stderr)
        print(log, file=sys.stderr)
        if missing:
            print(f"M8 missing: {', '.join(missing)}", file=sys.stderr)
        if forbidden:
            print(f"M8 rejected: {', '.join(forbidden)}", file=sys.stderr)
        return 1

    print(f"M8 artifacts: {artifacts}")
    print("M8 device-service gate passed: abi=1.0 policy=peer-credentials nonblocking=pass recovery=pass raw-mmio=denied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
