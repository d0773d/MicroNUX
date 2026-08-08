#!/usr/bin/env python3
"""Reset the M4 board and prove the native USB console is interactive."""

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
    "MICRONUX:M4:IRQ source=22 matrix=500d6058 clic=16 handoff=armed",
    "Linux version 6.12.27",
    "console [ttyGS0] enabled",
    "USB Serial/JTAG ttyGS0",
    "MICRONUX:M4:SHELL ready console=ttyGS0",
    "MICRONUX:M4:PROBE:BEGIN",
    "MICRONUX:M4:MEMINFO",
    "MICRONUX:M4:FREE",
    "MICRONUX:M4:UNAME",
    "MICRONUX:M4:PROBE:DONE",
)

FORBIDDEN_MARKERS = (
    "MICRONUX:M3:FAIL",
    "MICRONUX:M4:FAIL",
    "Kernel panic",
    "Oops:",
    "BUG:",
    "Unhandled",
    "unhandled signal",
    "timer stall",
    "can't open /dev/ttyGS0",
)

PROBE_LINES = (
    "TAG=BEGIN; echo MICRONUX:M4:PROBE:$TAG\n",
    "TAG=MEMINFO; echo MICRONUX:M4:$TAG\n",
    "cat /proc/meminfo\n",
    "TAG=FREE; echo MICRONUX:M4:$TAG\n",
    "free\n",
    "TAG=UNAME; echo MICRONUX:M4:$TAG\n",
    "uname -a\n",
    "TAG=DONE; echo MICRONUX:M4:PROBE:$TAG\n",
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


def reset_probe_and_capture(port: str, timeout: float) -> str:
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
        captured = bytearray()
        probe_sent = False
        done_seen_at: float | None = None

        while time.monotonic() < deadline:
            chunk = device.read(device.in_waiting or 1)
            if chunk:
                captured.extend(chunk)
            text = captured.decode("utf-8", errors="replace")

            if not probe_sent and "MICRONUX:M4:SHELL ready" in text:
                time.sleep(0.2)
                for line in PROBE_LINES:
                    device.write(line.encode("ascii"))
                    device.flush()
                    time.sleep(0.05)
                probe_sent = True

            if probe_sent and "MICRONUX:M4:PROBE:DONE" in text:
                if done_seen_at is None:
                    done_seen_at = time.monotonic()
                elif time.monotonic() - done_seen_at >= 0.4:
                    break

        return captured.decode("utf-8", errors="replace")
    finally:
        device.dtr = False
        device.rts = False
        device.close()


def artifact_record(directory: Path) -> str:
    entries = []
    for name in ("Image", "rootfs.cpio", "esp32p4-micronux.dtb", "metadata.bin"):
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(f"missing M4 artifact: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(f"{name}={path.stat().st_size}B:{digest}")
    return " ".join(entries)


def milestone_lines(log: str) -> list[str]:
    useful = (
        "MICRONUX:M3:KERNEL",
        "MICRONUX:M3:DTB",
        "MICRONUX:M4:",
        "Linux version",
        "console [ttyGS0]",
        "USB Serial/JTAG ttyGS0",
        "MemFree:",
        "Memory:",
        "GNU/Linux",
    )
    return [line.strip() for line in log.splitlines() if any(x in line for x in useful)]


def parse_measurements(log: str) -> tuple[float, float, int]:
    init_match = re.search(r"\[\s*([0-9]+(?:\.[0-9]+)?)\] Run /init as init process", log)
    jump_match = re.search(r"I \(([0-9]+)\) micronux_m3: MICRONUX:M3:JUMP", log)
    mem_match = re.search(r"(?m)^MemFree:\s+([0-9]+) kB\r?$", log)
    if init_match is None or jump_match is None or mem_match is None:
        raise ValueError("boot did not report loader time, init time, and MemFree")
    init_time = float(init_match.group(1))
    shell_time = int(jump_match.group(1)) / 1000.0 + init_time
    return init_time, shell_time, int(mem_match.group(1))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--boots", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.boots < 1:
        parser.error("--boots must be at least 1")

    try:
        artifacts = artifact_record(args.artifact_dir.resolve())
    except (OSError, FileNotFoundError) as error:
        print(error, file=sys.stderr)
        return 1

    expected_kernel_hash: str | None = None
    expected_dtb_hash: str | None = None
    init_times: list[float] = []
    shell_times: list[float] = []
    free_memory: list[int] = []

    for boot in range(1, args.boots + 1):
        log = reset_probe_and_capture(args.port, args.timeout)
        print(f"--- M4 boot {boot}/{args.boots} ---")
        lines = milestone_lines(log)
        print("\n".join(lines) if lines else "(no M4/Linux milestones captured)")

        missing = [marker for marker in REQUIRED_MARKERS if marker not in log]
        forbidden = [marker for marker in FORBIDDEN_MARKERS if marker in log]
        if missing or forbidden:
            print("--- complete serial log ---", file=sys.stderr)
            print(log, file=sys.stderr)
            if missing:
                print(f"M4 boot {boot} missing: {', '.join(missing)}", file=sys.stderr)
            if forbidden:
                print(f"M4 boot {boot} rejected: {', '.join(forbidden)}", file=sys.stderr)
            return 1

        kernel_match = re.search(r"MICRONUX:M3:KERNEL .* sha256=([0-9a-f]{64})", log)
        dtb_match = re.search(r"MICRONUX:M3:DTB .* sha256=([0-9a-f]{64})", log)
        if kernel_match is None or dtb_match is None:
            print(f"M4 boot {boot} did not report payload hashes", file=sys.stderr)
            return 1

        kernel_hash = kernel_match.group(1)
        dtb_hash = dtb_match.group(1)
        if expected_kernel_hash is None:
            expected_kernel_hash = kernel_hash
            expected_dtb_hash = dtb_hash
        elif kernel_hash != expected_kernel_hash or dtb_hash != expected_dtb_hash:
            print(f"M4 payload changed across reset boot {boot}", file=sys.stderr)
            return 1

        try:
            init_time, shell_time, mem_free = parse_measurements(log)
        except ValueError as error:
            print(f"M4 boot {boot}: {error}", file=sys.stderr)
            return 1
        init_times.append(init_time)
        shell_times.append(shell_time)
        free_memory.append(mem_free)

    print(f"M4 artifacts: {artifacts}")
    print(
        f"M4 hardware gate passed: boots={args.boots} "
        f"init_time_s={','.join(f'{value:.2f}' for value in init_times)} "
        f"reset_to_shell_s={','.join(f'{value:.2f}' for value in shell_times)} "
        f"mem_free_kb={','.join(str(value) for value in free_memory)} "
        f"kernel_sha256={expected_kernel_hash} dtb_sha256={expected_dtb_hash}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
