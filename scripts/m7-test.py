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
    "MICRONUX:M7:JOB-SUPERVISOR state=ready uid=1000 gid=1000 "
    "admission=root-owned seccomp=allowlist",
    "MICRONUX:M7:WARMUP:PASS",
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
    "MICRONUX:M7:JOB-ADMISSION:PASS internal=denied writable=denied",
    "MICRONUX:M7:JOB-POLICY uid=1000 gid=1000 caps=zero "
    "no_new_privs=1 seccomp=allowlist",
    "MICRONUX:M7:JOB-CONTRACT:PASS uid=1000 gid=1000 caps=zero "
    "seccomp=2 devices=restricted fd=16 nproc=4 arena_kib=6144",
    "MICRONUX:M7:POOL-TEST:JOB-CONTRACT:RC=0",
    "MICRONUX:M7:POOL-TEST:JOB-WALL:RC=124",
    "MICRONUX:M7:POOL-TEST:JOB-OUTPUT:RC=125",
    "MICRONUX:M7:JOB-LIVENESS:PASS",
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
    "MICRONUX:M7:JOB-SUPERVISOR:FAIL",
    "MICRONUX:M7:WARMUP:FAIL",
    "MICRONUX:M7:JOB-ADMISSION:FAIL",
    "MICRONUX:M7:JOB-CONTRACT:FAIL",
    "MICRONUX:M7:JOB-LIVENESS:FAIL",
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


def warmup_command() -> str:
    repeats = " ".join(str(index) for index in range(1, 9))
    return (
        "echo MICRONUX:M7:WARMUP:BEGIN; RC=0; "
        "install -o root -g root -m 0755 /usr/libexec/micronux-job-test "
        "/opt/micronux/apps/micronux-job-test || RC=1; "
        "/usr/sbin/micronux-run -q -- /usr/libexec/micronux-job-test "
        "contract >/dev/null 2>&1 && RC=1; "
        "install -o root -g root -m 0777 /usr/libexec/micronux-job-test "
        "/opt/micronux/apps/micronux-job-unsafe || RC=1; "
        "/usr/sbin/micronux-run -q -- "
        "/opt/micronux/apps/micronux-job-unsafe contract "
        ">/dev/null 2>&1 && RC=1; "
        "rm -f /opt/micronux/apps/micronux-job-unsafe; "
        "/usr/bin/micronux-isolation-probe >/dev/null 2>&1 || RC=1; "
        "/usr/bin/micronux-isolation-fault >/dev/null 2>&1 || RC=1; "
        "/usr/bin/micronux-selftest >/dev/null 2>&1 || RC=1; "
        "for I in "
        f"{repeats}"
        "; do /usr/bin/micronux-isolation-probe >/dev/null 2>&1 || RC=1; done; "
        "/usr/bin/micronux-arena-test >/dev/null 2>&1 || RC=1; "
        "/usr/sbin/micronux-run -- "
        "/opt/micronux/apps/micronux-job-test contract "
        ">/dev/null 2>&1 || RC=1; "
        "/usr/sbin/micronux-run -q -- "
        "/opt/micronux/apps/micronux-job-test spin "
        ">/dev/null 2>&1; [ $? -eq 124 ] || RC=1; "
        "/usr/sbin/micronux-run -q -- "
        "/opt/micronux/apps/micronux-job-test flood "
        ">/dev/null 2>&1; [ $? -eq 125 ] || RC=1; "
        "/usr/bin/micronux-device api >/dev/null 2>&1 || RC=1; "
        "sleep 1; if [ \"$RC\" -eq 0 ]; then "
        "echo MICRONUX:M7:WARMUP:PASS; else "
        "echo MICRONUX:M7:WARMUP:FAIL rc=$RC; fi\n"
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
        "ADMISSION=0; /usr/sbin/micronux-run -q -- "
        "/usr/libexec/micronux-job-test contract >/dev/null 2>&1 "
        "&& ADMISSION=1; "
        "install -o root -g root -m 0777 /usr/libexec/micronux-job-test "
        "/opt/micronux/apps/micronux-job-unsafe || ADMISSION=1; "
        "/usr/sbin/micronux-run -q -- "
        "/opt/micronux/apps/micronux-job-unsafe contract >/dev/null 2>&1 "
        "&& ADMISSION=1; "
        "rm -f /opt/micronux/apps/micronux-job-unsafe; "
        "if [ \"$ADMISSION\" -eq 0 ]; then "
        "echo MICRONUX:M7:JOB-ADMISSION:PASS "
        "internal=denied writable=denied; else "
        "echo MICRONUX:M7:JOB-ADMISSION:FAIL rc=$ADMISSION; fi; "
        "/usr/sbin/micronux-run -- "
        "/opt/micronux/apps/micronux-job-test contract; "
        "echo MICRONUX:M7:POOL-TEST:JOB-CONTRACT:RC=$?; "
        "/usr/sbin/micronux-run -q -- "
        "/opt/micronux/apps/micronux-job-test spin; "
        "echo MICRONUX:M7:POOL-TEST:JOB-WALL:RC=$?; "
        "/usr/sbin/micronux-run -q -- "
        "/opt/micronux/apps/micronux-job-test flood; "
        "echo MICRONUX:M7:POOL-TEST:JOB-OUTPUT:RC=$?; "
        "if /usr/bin/micronux-device api >/dev/null; then "
        "echo MICRONUX:M7:JOB-LIVENESS:PASS; else "
        "echo MICRONUX:M7:JOB-LIVENESS:FAIL; fi; "
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
    warmup_sent = False
    command_sent = False
    done_seen_at: float | None = None

    try:
        device.reset_input_buffer()
        while time.monotonic() < deadline:
            captured.extend(device.read(4096))
            text = captured.decode("utf-8", errors="replace")
            if (
                not warmup_sent
                and "MICRONUX:M6:COMBINED:SHELL ready" in text
                and "MICRONUX:M7:POOL state=ready" in text
                and "MICRONUX:M8:SERVICE state=ready" in text
            ):
                time.sleep(0.2)
                device.write(warmup_command().encode("ascii"))
                device.flush()
                warmup_sent = True
            if (
                warmup_sent
                and not command_sent
                and marker_seen(text, "MICRONUX:M7:WARMUP:PASS")
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
    runner = directory / "micronux-run"
    helper = directory / "micronux-job-exec"
    job_test = directory / "micronux-job-test"
    for path in (image, dtb, probe, fault, arena, runner, helper, job_test):
        if not path.is_file():
            raise FileNotFoundError(path)

    for binary in (probe, fault, arena, runner, helper, job_test):
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


def job_policy_results(log: str) -> None:
    wall = re.search(
        r"MICRONUX:M7:JOB result=killed reason=wall signal=9 "
        r"output=(\d+) duration_ms=(\d+)",
        log,
    )
    output = re.search(
        r"MICRONUX:M7:JOB result=killed reason=output signal=9 "
        r"output=(\d+) duration_ms=(\d+)",
        log,
    )
    if wall is None or output is None:
        raise ValueError("missing supervisor kill results")
    wall_bytes, wall_ms = int(wall.group(1)), int(wall.group(2))
    output_bytes, output_ms = int(output.group(1)), int(output.group(2))
    if wall_bytes > 32768 or wall_ms < 2000 or wall_ms > 4000:
        raise ValueError(
            f"invalid wall policy result: bytes={wall_bytes} ms={wall_ms}"
        )
    if output_bytes <= 32768 or output_ms >= 2000:
        raise ValueError(
            f"invalid output policy result: bytes={output_bytes} ms={output_ms}"
        )


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
            job_policy_results(log)
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
