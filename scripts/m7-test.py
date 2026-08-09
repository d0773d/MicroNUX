#!/usr/bin/env python3
"""Exercise the MicroNUX M7 per-mm arena gate on ESP32-P4 hardware."""

from __future__ import annotations

import argparse
import hashlib
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

import serial


REQUIRED_MARKERS = (
    "MICRONUX:M3:BOOT",
    "MICRONUX:M3:JUMP",
    "MICRONUX:M7:PMP baseline=pass early-deny=pass "
    "handoff=13-14-unlocked overlay=13-14",
    "MICRONUX:M7:PMP cached=7-9 direct=12-13 mode=per-mm+tor+napot "
    "state=ready first=[",
    "MICRONUX:M7:POOL state=ready range=[49700000,49f00000) pages=2048 "
    "zero=on-arena+allocate ownership=per-mm",
    "Linux version 6.12.27",
    "MICRONUX:M6:COMBINED:SHELL ready console=ttyGS0 network=nonblocking",
    "MICRONUX:M8:SERVICE state=ready abi=1.0",
    "MICRONUX:M7:POOL-PROBE:PASS",
    "MICRONUX:M7:POOL-TEST:PROBE:RC=0",
    "MICRONUX:M7:FAULTS pass count=16 privilege=U memory_signal=11",
    "MICRONUX:M7:UACCESS:PASS",
    "MICRONUX:M7:ISOLATION-FAULT:PASS",
    "MICRONUX:M7:POOL-TEST:FAULT:RC=0",
    "MICRONUX:M5:PASS",
    "MICRONUX:M7:POOL-TEST:SELFTEST:RC=0",
    "MICRONUX:M7:POOL-TEST:REPEAT:RC=0",
    "MICRONUX:M7:ARENA:SIBLING:PASS",
    "MICRONUX:M7:ARENA:REUSE:PASS cycles=32",
    "MICRONUX:M7:ARENA:EXEC-FAIL:PASS",
    "MICRONUX:M7:ARENA:PASS ownership=per-mm vfork=pass signals=pass",
    "MICRONUX:M7:POOL-TEST:ARENA:RC=0",
    "MICRONUX:M7:POOL-TEST:DONE",
)

FORBIDDEN_MARKERS = (
    "MICRONUX:M3:FAIL",
    "MICRONUX:M6:FAIL",
    "MICRONUX:M7:PMP-AUDIT state=fail",
    "MICRONUX:M7:PMP-OVERLAY state=fail",
    "MICRONUX:M7:POOL state=fail",
    "MICRONUX:M7:POOL state=exhausted",
    "MICRONUX:M7:POOL-PROBE:FAIL",
    "MICRONUX:M7:ISOLATION-FAULT:FAIL",
    "MICRONUX:M7:ARENA:FAIL",
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
    repeats = " ".join(str(index) for index in range(1, 9))
    return (
        "echo MICRONUX:M7:POOL-TEST:BEGIN; "
        "echo MICRONUX:M7:POOL-TEST:MEM-BEFORE; "
        "grep '^MemFree:' /proc/meminfo; "
        "echo MICRONUX:M7:POOL-TEST:ACCOUNT-BEFORE; "
        "cat /proc/micronux_user_pool; "
        "/usr/bin/micronux-isolation-probe; "
        "echo MICRONUX:M7:POOL-TEST:PROBE:RC=$?; "
        "/usr/bin/micronux-isolation-fault; "
        "echo MICRONUX:M7:POOL-TEST:FAULT:RC=$?; "
        "/usr/bin/micronux-selftest; "
        "echo MICRONUX:M7:POOL-TEST:SELFTEST:RC=$?; "
        "RC=0; for I in "
        f"{repeats}"
        "; do /usr/bin/micronux-isolation-probe >/dev/null || RC=1; done; "
        "echo MICRONUX:M7:POOL-TEST:REPEAT:RC=$RC; "
        "/usr/bin/micronux-arena-test; "
        "echo MICRONUX:M7:POOL-TEST:ARENA:RC=$?; "
        "echo MICRONUX:M7:POOL-TEST:ACCOUNT-AFTER; "
        "cat /proc/micronux_user_pool; "
        "echo MICRONUX:M7:POOL-TEST:MEM-AFTER; "
        "grep '^MemFree:' /proc/meminfo; "
        "echo MICRONUX:M7:POOL-TEST:DONE\n"
    )


def capture_boot(port: str, timeout: float) -> str:
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
                and "MICRONUX:M7:POOL state=ready" in text
                and "MICRONUX:M8:SERVICE state=ready" in text
            ):
                time.sleep(0.2)
                device.write(test_command().encode("ascii"))
                device.flush()
                command_sent = True
            if marker_seen(text, "MICRONUX:M7:POOL-TEST:DONE"):
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


def artifact_hashes(directory: Path) -> tuple[str, str]:
    image = directory / "Image"
    dtb = directory / "esp32p4-micronux.dtb"
    probe = directory / "micronux-isolation-probe"
    fault = directory / "micronux-isolation-fault"
    arena = directory / "micronux-arena-test"
    for path in (image, dtb, probe, fault, arena):
        if not path.is_file():
            raise FileNotFoundError(path)

    for binary in (probe, fault, arena):
        header = binary.read_bytes()[:44]
        if len(header) != 44 or header[:4] != b"bFLT":
            raise ValueError(f"{binary.name} is not bFLT")
        fields = struct.unpack(">10I", header[4:])
        if fields[0] != 4 or fields[8] & 0x1 == 0:
            raise ValueError(
                f"{binary.name} must be bFLT v4 FLAT_FLAG_RAM"
            )
    return (
        hashlib.sha256(image.read_bytes()).hexdigest(),
        hashlib.sha256(dtb.read_bytes()).hexdigest(),
    )


def boot_hashes(log: str) -> tuple[str, str]:
    kernel = re.search(r"MICRONUX:M3:KERNEL .* sha256=([0-9a-f]{64})", log)
    dtb = re.search(r"MICRONUX:M3:DTB .* sha256=([0-9a-f]{64})", log)
    if kernel is None or dtb is None:
        raise ValueError("boot did not report payload hashes")
    return kernel.group(1), dtb.group(1)


def accounting(log: str, phase: str) -> tuple[int, int, int, int]:
    match = re.search(
        rf"MICRONUX:M7:POOL-TEST:ACCOUNT-{phase}\r?\n"
        r"MICRONUX:M7:POOL range=\[49700000,49f00000\) "
        r"pages=2048 reserved=(\d+) mapped=(\d+) free=(\d+) arenas=(\d+)",
        log,
    )
    if match is None:
        raise ValueError(f"missing pool accounting for {phase.lower()}")
    reserved, mapped, free, arenas = (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        int(match.group(4)),
    )
    if reserved + free != 2048 or mapped > reserved or arenas > reserved:
        raise ValueError(f"invalid pool accounting for {phase.lower()}")
    return reserved, mapped, free, arenas


def free_memory(log: str, phase: str) -> int:
    match = re.search(
        rf"MICRONUX:M7:POOL-TEST:MEM-{phase}\r?\nMemFree:\s+(\d+) kB",
        log,
    )
    if match is None:
        raise ValueError(f"missing MemFree for {phase.lower()}")
    return int(match.group(1))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--boots", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--expect-mipi-profile", choices=("jd9365",))
    args = parser.parse_args()
    if args.boots < 1:
        parser.error("--boots must be at least 1")

    try:
        expected_payload = artifact_hashes(args.artifact_dir.resolve())
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1

    complete_log: list[str] = []
    expected_accounting: tuple[int, int, int, int] | None = None
    for boot in range(1, args.boots + 1):
        log = ""
        try:
            log = capture_boot(args.port, args.timeout)
            complete_log.append(log)
            if args.log is not None:
                args.log.parent.mkdir(parents=True, exist_ok=True)
                args.log.write_text("\n".join(complete_log), encoding="utf-8")
            payload = boot_hashes(log)
            before = accounting(log, "BEFORE")
            after = accounting(log, "AFTER")
            memory_before = free_memory(log, "BEFORE")
            memory_after = free_memory(log, "AFTER")
        except (OSError, subprocess.CalledProcessError, ValueError) as error:
            if log:
                print(log, file=sys.stderr)
            print(f"M7 arena boot {boot}: {error}", file=sys.stderr)
            return 1

        missing = [marker for marker in REQUIRED_MARKERS if not marker_seen(log, marker)]
        if args.expect_mipi_profile == "jd9365" and not marker_seen(
            log,
            "MICRONUX:M6:DSI state=ready profile=jd9365-800x1280 "
            "resolution=800x1280 lanes=2 lane_mbps=1500 format=rgb565 "
            "pattern=vertical-bars",
        ):
            missing.append("MICRONUX:M6:DSI exact Kit C JD9365 profile")
        forbidden = [marker for marker in FORBIDDEN_MARKERS if marker_seen(log, marker)]
        if missing or forbidden or payload != expected_payload or before != after:
            print(log, file=sys.stderr)
            print(
                f"M7 arena boot {boot}: missing={missing} forbidden={forbidden} "
                f"payload={payload} expected={expected_payload} "
                f"pool_before={before} pool_after={after}",
                file=sys.stderr,
            )
            return 1
        if memory_after + 16 < memory_before:
            print(
                f"M7 arena boot {boot}: kernel memory loss exceeds 16 KiB: "
                f"{memory_before}->{memory_after}",
                file=sys.stderr,
            )
            return 1
        if expected_accounting is None:
            expected_accounting = before
        elif before != expected_accounting:
            print(f"M7 arena accounting changed across boot {boot}", file=sys.stderr)
            return 1
        print(
            f"M7 arena boot {boot}/{args.boots} passed: "
            f"reserved={before[0]} mapped={before[1]} free={before[2]} "
            f"arenas={before[3]} mem_kib={memory_before}->{memory_after}"
        )

    if args.log is not None:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        args.log.write_text("\n".join(complete_log), encoding="utf-8")
    print(
        f"M7 per-mm arena gate passed: boots={args.boots} "
        f"range=[49700000,49f00000) payload={expected_payload[0]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
