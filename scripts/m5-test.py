#!/usr/bin/env python3
"""Reset the M5 board and run the native NOMMU hardening gate."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import time
from pathlib import Path

import serial


EXEC_ITERATIONS = 64
SIGNAL_ITERATIONS = 64
TIMER_ITERATIONS = 32
MEMORY_KIB = 4096
STACK_DEPTH = 8
CONSOLE_LINES = 64
DEFAULT_MAX_MEMORY_LOSS_KIB = 512

REQUIRED_MARKERS = (
    "MICRONUX:M3:BOOT",
    "MICRONUX:M3:JUMP",
    "MICRONUX:M4:IRQ source=22 matrix=500d6058 clic=16 handoff=armed",
    "Linux version 6.12.27",
    "console [ttyGS0] enabled",
    "USB Serial/JTAG ttyGS0",
    "MICRONUX:M4:SHELL ready console=ttyGS0",
    "MICRONUX:M5:BASELINE",
    "MICRONUX:M5:RUN",
    f"MICRONUX:M5:SIGNALS pass count={SIGNAL_ITERATIONS}",
    f"MICRONUX:M5:TIMERS pass count={TIMER_ITERATIONS}",
    f"MICRONUX:M5:EXEC pass count={EXEC_ITERATIONS}",
    f"MICRONUX:M5:MEMORY pass kib={MEMORY_KIB}",
    f"MICRONUX:M5:STACK pass depth={STACK_DEPTH}",
    f"MICRONUX:M5:CONSOLE seq={CONSOLE_LINES - 1}",
    "MICRONUX:M5:PASS",
    "MICRONUX:M5:AFTER",
    "MICRONUX:M5:PROBE:DONE",
)

FORBIDDEN_MARKERS = (
    "MICRONUX:M3:FAIL",
    "MICRONUX:M4:FAIL",
    "MICRONUX:M5:FAIL",
    "Kernel panic",
    "Oops:",
    "BUG:",
    "Unhandled",
    "unhandled signal",
    "timer stall",
    "stack smashing detected",
    "can't open /dev/ttyGS0",
)

START_LINES = (
    "TAG=BASELINE; echo MICRONUX:M5:$TAG\n",
    "cat /proc/meminfo\n",
    "TAG=RUN; echo MICRONUX:M5:$TAG\n",
    "/usr/bin/micronux-selftest\n",
)

FINISH_LINES = (
    "TAG=AFTER; echo MICRONUX:M5:$TAG\n",
    "cat /proc/meminfo\n",
    "TAG=DONE; echo MICRONUX:M5:PROBE:$TAG\n",
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


def write_lines(device: serial.Serial, lines: tuple[str, ...]) -> None:
    for line in lines:
        device.write(line.encode("ascii"))
        device.flush()
        time.sleep(0.05)


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
        started = False
        finished = False
        done_seen_at: float | None = None

        while time.monotonic() < deadline:
            chunk = device.read(device.in_waiting or 1)
            if chunk:
                captured.extend(chunk)
            text = captured.decode("utf-8", errors="replace")

            if not started and "MICRONUX:M4:SHELL ready" in text:
                time.sleep(0.2)
                write_lines(device, START_LINES)
                started = True

            if started and not finished and re.search(
                r"MICRONUX:M5:PASS .* elapsed_ms=[0-9]+\r?\n", text
            ):
                write_lines(device, FINISH_LINES)
                finished = True

            if finished and "MICRONUX:M5:PROBE:DONE" in text:
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
    for name in (
        "Image",
        "rootfs.cpio",
        "esp32p4-micronux.dtb",
        "metadata.bin",
        "micronux-selftest",
        "micronux-exec-child",
    ):
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(f"missing M5 artifact: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append(f"{name}={path.stat().st_size}B:{digest}")
    return " ".join(entries)


def milestone_lines(log: str) -> list[str]:
    lines = []
    for line in log.splitlines():
        if "MICRONUX:M5:CONSOLE" in line and not (
            "seq=0 " in line or f"seq={CONSOLE_LINES - 1} " in line
        ):
            continue
        if any(
            marker in line
            for marker in (
                "MICRONUX:M3:KERNEL",
                "MICRONUX:M3:DTB",
                "MICRONUX:M4:SHELL",
                "MICRONUX:M5:",
                "Linux version",
                "console [ttyGS0]",
                "USB Serial/JTAG ttyGS0",
                "MemFree:",
            )
        ):
            lines.append(line.strip())
    return lines


def payload_hashes(log: str) -> tuple[str, str]:
    kernel_match = re.search(r"MICRONUX:M3:KERNEL .* sha256=([0-9a-f]{64})", log)
    dtb_match = re.search(r"MICRONUX:M3:DTB .* sha256=([0-9a-f]{64})", log)
    if kernel_match is None or dtb_match is None:
        raise ValueError("boot did not report payload hashes")
    return kernel_match.group(1), dtb_match.group(1)


def measurements(log: str) -> tuple[int, int, int]:
    memory_matches = re.findall(r"(?m)^MemFree:\s+([0-9]+) kB\r?$", log)
    pass_match = re.search(
        rf"MICRONUX:M5:PASS exec={EXEC_ITERATIONS} "
        rf"signals={SIGNAL_ITERATIONS} timers={TIMER_ITERATIONS} "
        rf"memory_kib={MEMORY_KIB} stack_depth={STACK_DEPTH} "
        rf"console={CONSOLE_LINES} elapsed_ms=([0-9]+)",
        log,
    )
    if len(memory_matches) < 2 or pass_match is None:
        raise ValueError("self-test did not report memory and elapsed-time metrics")
    return int(memory_matches[0]), int(memory_matches[-1]), int(pass_match.group(1))


def verify_console(log: str) -> None:
    matches = re.findall(
        r"(?m)^MICRONUX:M5:CONSOLE seq=([0-9]+) checksum=([0-9a-f]{8})\r?$",
        log,
    )
    if len(matches) != CONSOLE_LINES:
        raise ValueError(
            f"console reported {len(matches)} complete lines, expected {CONSOLE_LINES}"
        )

    checksum = 0x6D696372
    for expected_sequence, (sequence_text, checksum_text) in enumerate(matches):
        checksum ^= (checksum << 13) & 0xFFFFFFFF
        checksum ^= checksum >> 17
        checksum ^= (checksum << 5) & 0xFFFFFFFF
        checksum &= 0xFFFFFFFF
        if int(sequence_text) != expected_sequence or int(checksum_text, 16) != checksum:
            raise ValueError(
                "console sequence/checksum mismatch at "
                f"line {expected_sequence}: seq={sequence_text} checksum={checksum_text}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--boots", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument(
        "--max-memory-loss-kib", type=int, default=DEFAULT_MAX_MEMORY_LOSS_KIB
    )
    args = parser.parse_args()
    if args.boots < 1:
        parser.error("--boots must be at least 1")
    if args.max_memory_loss_kib < 0:
        parser.error("--max-memory-loss-kib cannot be negative")

    try:
        artifacts = artifact_record(args.artifact_dir.resolve())
    except OSError as error:
        print(error, file=sys.stderr)
        return 1

    expected_hashes: tuple[str, str] | None = None
    elapsed_times: list[int] = []
    baseline_memory: list[int] = []
    final_memory: list[int] = []

    for boot in range(1, args.boots + 1):
        log = reset_probe_and_capture(args.port, args.timeout)
        print(f"--- M5 boot {boot}/{args.boots} ---")
        lines = milestone_lines(log)
        print("\n".join(lines) if lines else "(no M5/Linux milestones captured)")

        missing = [marker for marker in REQUIRED_MARKERS if marker not in log]
        forbidden = [marker for marker in FORBIDDEN_MARKERS if marker in log]
        if missing or forbidden:
            print("--- complete serial log ---", file=sys.stderr)
            print(log, file=sys.stderr)
            if missing:
                print(f"M5 boot {boot} missing: {', '.join(missing)}", file=sys.stderr)
            if forbidden:
                print(f"M5 boot {boot} rejected: {', '.join(forbidden)}", file=sys.stderr)
            return 1

        try:
            hashes = payload_hashes(log)
            memory_before, memory_after, elapsed_ms = measurements(log)
            verify_console(log)
        except ValueError as error:
            print(f"M5 boot {boot}: {error}", file=sys.stderr)
            return 1

        if expected_hashes is None:
            expected_hashes = hashes
        elif hashes != expected_hashes:
            print(f"M5 payload changed across reset boot {boot}", file=sys.stderr)
            return 1

        memory_loss = memory_before - memory_after
        if memory_loss > args.max_memory_loss_kib:
            print(
                f"M5 boot {boot}: memory loss {memory_loss} KiB exceeds "
                f"{args.max_memory_loss_kib} KiB",
                file=sys.stderr,
            )
            return 1

        elapsed_times.append(elapsed_ms)
        baseline_memory.append(memory_before)
        final_memory.append(memory_after)

    assert expected_hashes is not None
    print(f"M5 artifacts: {artifacts}")
    print(
        f"M5 hardware gate passed: boots={args.boots} "
        f"exec_per_boot={EXEC_ITERATIONS} "
        f"elapsed_ms={','.join(str(value) for value in elapsed_times)} "
        f"mem_before_kib={','.join(str(value) for value in baseline_memory)} "
        f"mem_after_kib={','.join(str(value) for value in final_memory)} "
        f"kernel_sha256={expected_hashes[0]} dtb_sha256={expected_hashes[1]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
