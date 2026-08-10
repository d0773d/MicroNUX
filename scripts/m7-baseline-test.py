#!/usr/bin/env python3
"""Capture and verify the ESP32-P4 M7 isolation baseline on hardware."""

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


EXPECTED_PRE_PMP = {
    0: (0x27FFFFFC, 0x9B),
    1: (0x3FF0FFFC, 0x9B),
    2: (0x4FC0FFFC, 0x9D),
    3: (0x4FF00000, 0x80),
    4: (0x4FFC0000, 0x8F),
    5: (0x00000000, 0x00),
    6: (0x41FFFFFC, 0x9D),
    7: (0x00000000, 0x00),
    8: (0x00000000, 0x00),
    9: (0x00000000, 0x00),
    10: (0x00000000, 0x00),
    11: (0x5010BFFC, 0x9F),
    12: (0x00000000, 0x00),
    13: (0x00000000, 0x00),
    14: (0x00000000, 0x00),
    15: (0x5007FFFC, 0x9B),
}

EXPECTED_POST_PMP = dict(EXPECTED_PRE_PMP)
EXPECTED_POST_PMP[13] = (0x48400000, 0x80)
EXPECTED_POST_PMP[14] = (0x49F00000, 0x8F)

EXPECTED_EARLY_DENY_PRE_PMP = dict(EXPECTED_PRE_PMP)
for _entry in (0, 1, 2, 6, 11, 15):
    _address, _config = EXPECTED_EARLY_DENY_PRE_PMP[_entry]
    EXPECTED_EARLY_DENY_PRE_PMP[_entry] = (_address, 0x18)
EXPECTED_EARLY_DENY_PRE_PMP[3] = (0x4FF00000, 0x00)
EXPECTED_EARLY_DENY_PRE_PMP[4] = (0x4FFC0000, 0x08)

EXPECTED_EARLY_DENY_POST_PMP = dict(EXPECTED_EARLY_DENY_PRE_PMP)
EXPECTED_EARLY_DENY_POST_PMP[13] = (0x48400000, 0x00)
EXPECTED_EARLY_DENY_POST_PMP[14] = (0x49F00000, 0x0F)

PMP_PROFILES = {
    "baseline": (EXPECTED_PRE_PMP, EXPECTED_POST_PMP),
    "early-deny": (EXPECTED_EARLY_DENY_PRE_PMP, EXPECTED_EARLY_DENY_POST_PMP),
}

REQUIRED_MARKERS = (
    "MICRONUX:M3:BOOT target=esp32p4 revision=103 cores=2",
    "MICRONUX:M7:PMP-DUMP phase=pre entries=16",
    "MICRONUX:M7:PMP-DUMP phase=post entries=16",
    "MICRONUX:M6:COMBINED:SHELL ready",
    "MICRONUX:M8:SERVICE state=ready abi=1.0",
    "MICRONUX:M5:SIGNALS pass count=64",
    "MICRONUX:M5:TIMERS pass count=32",
    "MICRONUX:M5:EXEC pass count=64",
    "MICRONUX:M5:MEMORY pass kib=4096",
    "MICRONUX:M5:STACK pass depth=8",
    "MICRONUX:M5:PASS",
    "MICRONUX:M7:SELFTEST:RC=0",
    "MICRONUX:M7:SERVICE:RC=0",
    "MICRONUX:M7:BASELINE:DONE",
)

FORBIDDEN_MARKERS = (
    "MICRONUX:M3:FAIL",
    "MICRONUX:M5:FAIL",
    "Kernel panic",
    "Oops:",
    "BUG:",
    "Unhandled",
    "watchdog reset",
)

PMP_PATTERN = re.compile(
    r"MICRONUX:M7:PMP phase=(pre|post) entry=([0-9]+) "
    r"addr=([0-9a-f]{8}) config=([0-9a-f]{2})"
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


def target_command() -> str:
    return (
        "echo MICRONUX:M7:BASELINE:BEGIN; "
        "grep '^MemFree:' /proc/meminfo; "
        "/usr/bin/micronux-selftest; "
        "echo MICRONUX:M7:SELFTEST:RC=$?; "
        "grep '^MemFree:' /proc/meminfo; "
        "/usr/bin/micronux-device api >/dev/null; "
        "echo MICRONUX:M7:SERVICE:RC=$?; "
        "echo MICRONUX:M7:BASELINE:DONE\n"
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
            if not command_sent and "MICRONUX:M6:COMBINED:SHELL ready" in text:
                time.sleep(3.0)
                device.write(target_command().encode("ascii"))
                device.flush()
                command_sent = True
            if marker_seen(text, "MICRONUX:M7:BASELINE:DONE"):
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


def parse_pmp(log: str) -> dict[str, dict[int, tuple[int, int]]]:
    phases: dict[str, dict[int, tuple[int, int]]] = {"pre": {}, "post": {}}
    for phase, entry_text, address_text, config_text in PMP_PATTERN.findall(log):
        entry = int(entry_text)
        value = (int(address_text, 16), int(config_text, 16))
        if entry in phases[phase] and phases[phase][entry] != value:
            raise ValueError(f"conflicting {phase} PMP entry {entry}")
        phases[phase][entry] = value
    return phases


def check_bflt(path: Path) -> str:
    header = path.read_bytes()[:44]
    if len(header) != 44 or header[:4] != b"bFLT":
        raise ValueError(f"{path.name} is not a complete bFLT image")
    revision, entry, data_start, data_end, bss_end, stack_size, reloc_start, reloc_count, flags, build_date = struct.unpack(
        ">10I", header[4:]
    )
    if revision != 4 or not flags & 0x1:
        raise ValueError(
            f"{path.name} must be bFLT v4 with FLAT_FLAG_RAM, got revision={revision} flags=0x{flags:x}"
        )
    return (
        f"{path.name}=v{revision}:ram:flags=0x{flags:x}:entry=0x{entry:x}:"
        f"data=0x{data_start:x}-0x{data_end:x}:bss_end=0x{bss_end:x}:"
        f"stack={stack_size}:relocs={reloc_count}:build={build_date}"
    )


def artifact_record(directory: Path, loader_image: Path) -> str:
    records = []
    for path in (
        loader_image,
        directory / "Image",
        directory / "esp32p4-micronux.dtb",
        directory / "metadata.bin",
        directory / "rootfs.cpio",
    ):
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        records.append(f"{path.name}={path.stat().st_size}B:{digest}")
    return " ".join(records)


def payload_hashes(log: str) -> tuple[str, str]:
    kernel_match = re.search(r"MICRONUX:M3:KERNEL .* sha256=([0-9a-f]{64})", log)
    dtb_match = re.search(r"MICRONUX:M3:DTB .* sha256=([0-9a-f]{64})", log)
    if kernel_match is None or dtb_match is None:
        raise ValueError("boot did not report kernel and DTB hashes")
    return kernel_match.group(1), dtb_match.group(1)


def verify_boot(
    log: str,
    boot: int,
    max_memory_loss_kib: int,
    expected_pre_pmp: dict[int, tuple[int, int]],
    expected_post_pmp: dict[int, tuple[int, int]],
    pmp_profile: str,
) -> tuple[int, int, dict[str, dict[int, tuple[int, int]]], tuple[str, str]]:
    pmp = parse_pmp(log)
    missing = [marker for marker in REQUIRED_MARKERS if not marker_seen(log, marker)]
    forbidden = [marker for marker in FORBIDDEN_MARKERS if marker_seen(log, marker)]
    handoff_config = "0f lock=off" if pmp_profile == "early-deny" else "8f lock=on"
    handoff_marker = (
        "MICRONUX:M3:PMP entries=13,14 linux=[48400000,49f00000) "
        f"config={handoff_config}"
    )
    if not marker_seen(log, handoff_marker):
        missing.append(f"exact {pmp_profile} Linux handoff marker")
    if pmp_profile == "early-deny" and not marker_seen(
        log,
        "MICRONUX:M7:PMP baseline=pass early-deny=pass "
        "handoff=13-14-unlocked overlay=13-14 "
        "linux=[48400000,49f00000)",
    ):
        missing.append("early U-mode deny audit marker")
    if pmp["pre"] != expected_pre_pmp:
        missing.append(f"exact pre-PMP map: {pmp['pre']}")
    if pmp["post"] != expected_post_pmp:
        missing.append(f"exact post-PMP map: {pmp['post']}")

    memory = [
        int(value)
        for value in re.findall(r"(?m)^MemFree:\s+([0-9]+) kB\r?$", log)
    ]
    if len(memory) < 2:
        missing.append("two MemFree measurements")
        memory_before = 0
        memory_after = 0
    else:
        memory_before = memory[0]
        memory_after = memory[-1]
        memory_loss = memory_before - memory_after
        if memory_loss > max_memory_loss_kib:
            missing.append(
                f"memory loss <= {max_memory_loss_kib} KiB "
                f"(observed {memory_loss} KiB)"
            )

    if missing or forbidden:
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if forbidden:
            details.append(f"rejected: {', '.join(forbidden)}")
        raise ValueError(f"boot {boot}: {'; '.join(details)}")

    return memory_before, memory_after, pmp, payload_hashes(log)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--loader-image", type=Path, required=True)
    parser.add_argument("--boots", type=int, default=3)
    parser.add_argument(
        "--pmp-profile", choices=tuple(PMP_PROFILES), default="baseline"
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--max-memory-loss-kib", type=int, default=512)
    parser.add_argument("--log", type=Path)
    args = parser.parse_args()
    if args.boots < 1:
        parser.error("--boots must be at least 1")
    if args.max_memory_loss_kib < 0:
        parser.error("--max-memory-loss-kib cannot be negative")

    try:
        artifact_dir = args.artifact_dir.resolve()
        artifacts = artifact_record(artifact_dir, args.loader_image.resolve())
        bflt = (
            check_bflt(artifact_dir / "micronux-selftest"),
            check_bflt(artifact_dir / "micronux-exec-child"),
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"M7 baseline setup failed: {error}", file=sys.stderr)
        return 1

    expected_hashes: tuple[str, str] | None = None
    memory_measurements: list[tuple[int, int]] = []
    logs: list[str] = []
    expected_pre_pmp, expected_post_pmp = PMP_PROFILES[args.pmp_profile]

    for boot in range(1, args.boots + 1):
        try:
            log = capture(args.port, args.timeout)
            memory_before, memory_after, pmp, hashes = verify_boot(
                log,
                boot,
                args.max_memory_loss_kib,
                expected_pre_pmp,
                expected_post_pmp,
                args.pmp_profile,
            )
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            print(f"M7 baseline failed: {error}", file=sys.stderr)
            if "log" in locals():
                print("--- complete serial log ---", file=sys.stderr)
                print(log, file=sys.stderr)
            return 1

        if expected_hashes is None:
            expected_hashes = hashes
        elif hashes != expected_hashes:
            print(f"M7 baseline failed: payload changed on boot {boot}", file=sys.stderr)
            return 1

        logs.append(log)
        memory_measurements.append((memory_before, memory_after))
        print(f"--- M7 boot {boot}/{args.boots} ---")
        for phase in ("pre", "post"):
            compact_map = ",".join(
                f"{entry}:{address:08x}/{config:02x}"
                for entry, (address, config) in sorted(pmp[phase].items())
            )
            print(f"MICRONUX:M7:PMP-VERIFIED phase={phase} map={compact_map}")
        print(
            f"MICRONUX:M7:BOOT-VERIFIED boot={boot} "
            f"memory_before_kib={memory_before} memory_after_kib={memory_after} "
            f"memory_loss_kib={memory_before - memory_after}"
        )

    if args.log is not None:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        combined_log = "".join(
            f"\n===== M7 BOOT {boot}/{args.boots} =====\n{log}"
            for boot, log in enumerate(logs, start=1)
        )
        args.log.write_text(combined_log, encoding="utf-8")

    assert expected_hashes is not None
    memory_summary = ",".join(
        f"{before}->{after}" for before, after in memory_measurements
    )
    print(f"M7 artifacts: {artifacts}")
    print(f"M7 bFLT: {' '.join(bflt)}")
    print(
        f"M7 memory: boots={memory_summary} "
        f"limit={args.max_memory_loss_kib}KiB"
    )
    print(
        f"M7 baseline passed: boots={args.boots} silicon=rev1.3 pmp=16 "
        f"profile={args.pmp_profile} "
        f"return-space=7-10,12 handoff=13-14 stress=4MiB service=recovered "
        f"kernel_sha256={expected_hashes[0]} dtb_sha256={expected_hashes[1]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
