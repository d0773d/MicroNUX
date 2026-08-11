#!/usr/bin/env python3
"""Run the interactive MicroNUX M9.1 display and touch hardware gate."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import serial


DISPLAY_SYSFS = "/sys/bus/platform/devices/500a0000.display"
USB_RESET_ARM = Path(__file__).with_name("usb-reset-arm.py")


@dataclass(frozen=True)
class CommandResult:
    output: str
    return_code: int


def rom_reset(port: str) -> None:
    subprocess.run(
        [sys.executable, str(USB_RESET_ARM), "--port", port],
        check=False,
    )
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


def open_serial(port: str, timeout: float) -> serial.Serial:
    deadline = time.monotonic() + timeout
    while True:
        try:
            device = serial.Serial()
            device.port = port
            device.baudrate = 115200
            device.timeout = 0.05
            device.write_timeout = 10.0
            device.dsrdtr = False
            device.rtscts = False
            device.dtr = False
            device.rts = False
            device.open()
            device.reset_input_buffer()
            return device
        except serial.SerialException:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def wait_for_shell(device: serial.Serial, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    captured = bytearray()
    ready = "MICRONUX:M6:COMBINED:SHELL ready"

    while time.monotonic() < deadline:
        chunk = device.read(4096)
        if not chunk:
            continue
        captured.extend(chunk)
        sys.stdout.write(chunk.decode("utf-8", errors="replace"))
        sys.stdout.flush()
        text = captured.decode("utf-8", errors="replace")
        if ready in text and re.search(r"(?:^|\r?\n)/ # $", text):
            time.sleep(0.2)
            device.reset_output_buffer()
            return text
        if any(marker in text for marker in ("Kernel panic", "Oops:", "BUG:")):
            raise RuntimeError("kernel failure observed while waiting for shell")

    raise TimeoutError("Linux USB shell did not become ready")


def wait_for_prompt(device: serial.Serial, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    captured = bytearray()

    while time.monotonic() < deadline:
        device.write(b"\n")
        device.flush()
        sample_deadline = min(deadline, time.monotonic() + 0.5)
        while time.monotonic() < sample_deadline:
            chunk = device.read(4096)
            if not chunk:
                continue
            captured.extend(chunk)
            sys.stdout.write(chunk.decode("utf-8", errors="replace"))
            sys.stdout.flush()
            text = captured.decode("utf-8", errors="replace")
            if re.search(r"(?:^|\r?\n)/ # $", text):
                return text
            if any(marker in text for marker in ("Kernel panic", "Oops:", "BUG:")):
                raise RuntimeError("kernel failure observed after USB reconnect")

    raise TimeoutError("Linux USB shell did not return after reconnect")


def run_command(
    device: serial.Serial, command: str, label: str, timeout: float
) -> CommandResult:
    token = f"MICRONUX_M9_{label}_{time.monotonic_ns()}"
    payload = (
        f"echo {token}:BEGIN; {command}; "
        f"m9_rc=$?; echo {token}:END:$m9_rc\n"
    )
    device.write(payload.encode("ascii"))
    device.flush()
    deadline = time.monotonic() + timeout
    captured = bytearray()
    end_pattern = re.compile(re.escape(token) + r":END:(\d+)")

    while time.monotonic() < deadline:
        chunk = device.read(4096)
        if not chunk:
            continue
        captured.extend(chunk)
        sys.stdout.write(chunk.decode("utf-8", errors="replace"))
        sys.stdout.flush()
        text = captured.decode("utf-8", errors="replace")
        match = end_pattern.search(text)
        if match is not None:
            return CommandResult(text, int(match.group(1)))

    raise TimeoutError(f"serial command timed out: {label}")


def fail(stage: str, detail: str) -> int:
    print(f"MICRONUX:M9:HARDWARE-TEST:FAIL stage={stage} detail={detail}")
    return 1


def run_preflight(device: serial.Serial, boot_log: str) -> int:
    if "Linux version 6.12.27" not in boot_log:
        return fail("boot-markers", "kernel-version-missing")
    if "MICRONUX:M9:TOUCH state=ready product=9271" not in boot_log:
        return fail("touch-probe", "ready-marker-missing")
    if "MICRONUX:M7:DSI-SCANOUT state=ready" not in boot_log:
        return fail("scanout", "ready-marker-missing")

    check = run_command(
        device, "micronux-display-test check", "CHECK", 30.0
    )
    if check.return_code != 0:
        return fail("interface-check", f"rc-{check.return_code}")
    if "MICRONUX:M9:DISPLAY-TEST:PASS mode=check" not in check.output:
        return fail("interface-check", "pass-marker-missing")
    if "ready product=9271" not in check.output or "errors=0" not in check.output:
        return fail("touch-status", "product-or-error-status")

    presentation = run_command(
        device,
        f"D={DISPLAY_SYSFS}; "
        "echo MICRONUX:M9:DIAGNOSTICS:BEFORE; cat $D/diagnostics; "
        "micronux-display-test draw 3 & m9_pid=$!; "
        "sleep 4; echo MICRONUX:M9:DIAGNOSTICS:DURING; cat $D/diagnostics; "
        "echo MICRONUX:M9:SHELL state=responsive; "
        "wait $m9_pid; m9_draw_rc=$?; "
        "echo MICRONUX:M9:DRAW rc=$m9_draw_rc; "
        "echo MICRONUX:M9:DIAGNOSTICS:AFTER; cat $D/diagnostics",
        "PRESENTATION",
        45.0,
    )
    if presentation.return_code != 0:
        return fail("presentation", f"rc-{presentation.return_code}")
    for marker in (
        "MICRONUX:M9:SHELL state=responsive",
        "MICRONUX:M9:DRAW rc=0",
        "MICRONUX:M9:DISPLAY-TEST:PASS mode=draw console=restored",
    ):
        if marker not in presentation.output:
            return fail("presentation", f"missing-{marker}")

    underruns = [
        int(value)
        for value in re.findall(r"\bunderruns=(\d+)\b", presentation.output)
    ]
    if len(underruns) < 3:
        return fail("underrun", "diagnostics-missing")
    if underruns[-3:] != [0, 0, 0]:
        return fail(
            "underrun",
            f"before-{underruns[-3]}-during-{underruns[-2]}-after-{underruns[-1]}",
        )

    signal = run_command(
        device,
        f"D={DISPLAY_SYSFS}; "
        "echo MICRONUX:M9:SIGNAL-DIAGNOSTICS:BEFORE; cat $D/diagnostics; "
        "micronux-display-test draw 30 & m9_pid=$!; sleep 4; "
        "kill -TERM $m9_pid; wait $m9_pid; m9_draw_rc=$?; "
        "echo MICRONUX:M9:SIGNAL rc=$m9_draw_rc; "
        "echo MICRONUX:M9:SIGNAL-DIAGNOSTICS:AFTER; cat $D/diagnostics; "
        "echo MICRONUX:M9:SIGNAL-SHELL state=responsive",
        "SIGNAL",
        45.0,
    )
    if signal.return_code != 0:
        return fail("signal-recovery", f"rc-{signal.return_code}")
    for marker in (
        "MICRONUX:M9:SIGNAL rc=143",
        "MICRONUX:M9:SIGNAL-SHELL state=responsive",
    ):
        if marker not in signal.output:
            return fail("signal-recovery", f"missing-{marker}")
    signal_underruns = [
        int(value)
        for value in re.findall(r"\bunderruns=(\d+)\b", signal.output)
    ]
    if len(signal_underruns) < 2:
        return fail("signal-underrun", "diagnostics-missing")
    if signal_underruns[-2:] != [0, 0]:
        return fail(
            "signal-underrun",
            f"before-{signal_underruns[-2]}-after-{signal_underruns[-1]}",
        )

    print(
        "MICRONUX:M9:PREFLIGHT:PASS "
        "touch=9271 shell=responsive console=restored "
        "signal=restored underruns=0"
    )
    return 0


def run_touch(device: serial.Serial, point_timeout_ms: int) -> int:
    touch = run_command(
        device,
        f"micronux-display-test touch {point_timeout_ms}",
        "TOUCH",
        point_timeout_ms * 5 / 1000.0 + 45.0,
    )
    if touch.return_code != 0:
        return fail("five-point-touch", f"rc-{touch.return_code}")
    if "MICRONUX:M9:DISPLAY-TEST:PASS mode=touch points=5" not in touch.output:
        return fail("five-point-touch", "pass-marker-missing")
    if touch.output.count("MICRONUX:M9:TOUCH-TARGET:PASS") != 5:
        return fail("five-point-touch", "point-count")

    diagnostics = run_command(
        device, f"cat {DISPLAY_SYSFS}/diagnostics", "TOUCH_DIAGNOSTICS", 20.0
    )
    if diagnostics.return_code != 0:
        return fail("touch-diagnostics", f"rc-{diagnostics.return_code}")
    values = re.findall(r"\bunderruns=(\d+)\b", diagnostics.output)
    if not values or int(values[-1]) != 0:
        return fail("touch-underrun", values[-1] if values else "missing")

    print(
        "MICRONUX:M9:TOUCH-GATE:PASS "
        "points=5 console=restored underruns=0"
    )
    return 0


def run_soak(device: serial.Serial, soak_seconds: int, sample_seconds: int) -> int:
    sample_count = soak_seconds // sample_seconds + 1
    commands = [f"D={DISPLAY_SYSFS}"]
    for index in range(sample_count):
        commands.extend(
            (
                f"echo MICRONUX:M9:SOAK-SAMPLE index={index}",
                "cat $D/diagnostics",
                "cat $D/scanout",
                "cat $D/touch",
                f"echo MICRONUX:M9:SOAK-SHELL state=responsive index={index}",
            )
        )
        if index + 1 < sample_count:
            commands.append(f"sleep {sample_seconds}")
    command = "; ".join(commands)
    soak = run_command(
        device,
        command,
        "SOAK",
        soak_seconds + 60.0,
    )
    if soak.return_code != 0:
        return fail("soak", f"rc-{soak.return_code}")
    samples = re.findall(
        r"^MICRONUX:M9:SOAK-SAMPLE index=(\d+)\r?$",
        soak.output,
        flags=re.MULTILINE,
    )
    shells = re.findall(
        r"^MICRONUX:M9:SOAK-SHELL state=responsive index=(\d+)\r?$",
        soak.output,
        flags=re.MULTILINE,
    )
    expected = [str(index) for index in range(sample_count)]
    if samples != expected:
        return fail("soak", "sample-count")
    if shells != expected:
        return fail("soak", "shell-count")
    underruns = [
        int(value) for value in re.findall(r"\bunderruns=(\d+)\b", soak.output)
    ]
    if not underruns or any(value != 0 for value in underruns):
        return fail("soak-underrun", "nonzero-or-missing")
    errors = re.findall(r"\berrors=([0-9a-fA-F]{8})\b", soak.output)
    if len(errors) < sample_count or any(value != "00000000" for value in errors):
        return fail("soak-dma", "nonzero-or-missing")
    if len(re.findall(r"^running frames=", soak.output, re.MULTILINE)) != sample_count:
        return fail("soak-scanout", "not-running")
    if len(
        re.findall(r"^ready product=9271", soak.output, re.MULTILINE)
    ) != sample_count:
        return fail("soak-touch", "not-ready")

    print(
        "MICRONUX:M9:SOAK:PASS "
        f"seconds={soak_seconds} samples={sample_count} "
        "shell=responsive scanout=running underruns=0 errors=0"
    )
    return 0


def run_disconnect(
    port: str, device: serial.Serial, disconnect_seconds: int
) -> tuple[int, serial.Serial]:
    reset_policy = run_command(
        device,
        "cat /sys/kernel/micronux/usb_reset",
        "DISCONNECT_POLICY",
        20.0,
    )
    if reset_policy.return_code != 0 or "disabled" not in reset_policy.output:
        return fail("disconnect-policy", "reset-protection-not-active"), device

    before = run_command(
        device,
        f"cat {DISPLAY_SYSFS}/diagnostics",
        "DISCONNECT_BEFORE",
        20.0,
    )
    if before.return_code != 0:
        return fail("disconnect-before", f"rc-{before.return_code}"), device

    device.dtr = False
    device.rts = False
    device.close()
    print(
        "MICRONUX:M9:USB-DISCONNECT "
        f"state=closed duration_seconds={disconnect_seconds}"
    )
    time.sleep(disconnect_seconds)

    device = open_serial(port, 30.0)
    reconnect_log = wait_for_prompt(device, 30.0)
    after = run_command(
        device,
        f"cat {DISPLAY_SYSFS}/diagnostics; "
        "echo MICRONUX:M9:USB-RECONNECT shell=responsive",
        "DISCONNECT_AFTER",
        20.0,
    )
    if after.return_code != 0:
        return fail("disconnect-after", f"rc-{after.return_code}"), device
    if "MICRONUX:M9:USB-RECONNECT shell=responsive" not in after.output:
        return fail("disconnect-after", "shell-marker-missing"), device
    if "Linux version" in reconnect_log or "MICRONUX:M3:BOOT" in reconnect_log:
        return fail("disconnect-after", "unexpected-reboot"), device

    before_frames = [
        int(value) for value in re.findall(r"\bframes=(\d+)\b", before.output)
    ]
    after_frames = [
        int(value) for value in re.findall(r"\bframes=(\d+)\b", after.output)
    ]
    if (
        not before_frames
        or not after_frames
        or after_frames[-1] <= before_frames[-1]
    ):
        return fail("disconnect-after", "scanout-did-not-continue"), device

    diagnostics = before.output + after.output
    underruns = [
        int(value) for value in re.findall(r"\bunderruns=(\d+)\b", diagnostics)
    ]
    errors = re.findall(r"\berrors=([0-9a-fA-F]{8})\b", diagnostics)
    if len(underruns) < 2 or any(value != 0 for value in underruns[-2:]):
        return fail("disconnect-underrun", "nonzero-or-missing"), device
    if len(errors) < 2 or any(value != "00000000" for value in errors[-2:]):
        return fail("disconnect-dma", "nonzero-or-missing"), device

    print(
        "MICRONUX:M9:USB-RECONNECT:PASS "
        f"disconnected_seconds={disconnect_seconds} linux=retained "
        "shell=respawned scanout=running underruns=0 errors=0"
    )
    return 0, device


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM14")
    parser.add_argument(
        "--mode",
        choices=("preflight", "touch", "soak", "disconnect"),
        required=True,
    )
    parser.add_argument("--point-timeout-ms", type=int, default=60000)
    parser.add_argument("--soak-seconds", type=int, default=240)
    parser.add_argument("--sample-seconds", type=int, default=15)
    parser.add_argument("--disconnect-seconds", type=int, default=15)
    args = parser.parse_args()

    if args.point_timeout_ms < 5000 or args.point_timeout_ms > 120000:
        parser.error("--point-timeout-ms must be between 5000 and 120000")
    if args.soak_seconds < 60 or args.soak_seconds > 900:
        parser.error("--soak-seconds must be between 60 and 900")
    if args.sample_seconds < 5 or args.sample_seconds > 60:
        parser.error("--sample-seconds must be between 5 and 60")
    if args.sample_seconds > args.soak_seconds:
        parser.error("--sample-seconds cannot exceed --soak-seconds")
    if args.disconnect_seconds < 5 or args.disconnect_seconds > 120:
        parser.error("--disconnect-seconds must be between 5 and 120")

    device: serial.Serial | None = None
    try:
        rom_reset(args.port)
        device = open_serial(args.port, 30.0)
        boot_log = wait_for_shell(device, 120.0)
        if args.mode == "preflight":
            return run_preflight(device, boot_log)
        if args.mode == "touch":
            return run_touch(device, args.point_timeout_ms)
        if args.mode == "disconnect":
            result, device = run_disconnect(
                args.port, device, args.disconnect_seconds
            )
            return result
        return run_soak(device, args.soak_seconds, args.sample_seconds)
    except (
        serial.SerialException,
        subprocess.CalledProcessError,
        RuntimeError,
        TimeoutError,
    ) as error:
        return fail("serial", str(error).replace(" ", "-"))
    finally:
        if device is not None:
            device.dtr = False
            device.rts = False
            device.close()


if __name__ == "__main__":
    sys.exit(main())
