#!/usr/bin/env python3
"""Run the interactive MicroNUX M9.1 display and touch hardware gate."""

from __future__ import annotations

import argparse
import re
import shlex
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


def open_serial(
    port: str,
    timeout: float,
    clear_input: bool = True,
    write_timeout: float = 10.0,
) -> serial.Serial:
    deadline = time.monotonic() + timeout
    while True:
        try:
            device = serial.Serial()
            device.port = port
            device.baudrate = 115200
            device.timeout = 0.05
            device.write_timeout = write_timeout
            device.dsrdtr = False
            device.rtscts = False
            device.dtr = False
            device.rts = False
            device.open()
            if clear_input:
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
        if any(
            marker in text
            for marker in (
                "Kernel panic",
                "Oops:",
                "BUG:",
                "MICRONUX:M9:DISPLAY-FAULT",
            )
        ):
            raise RuntimeError("kernel failure observed while waiting for shell")

    raise TimeoutError("Linux USB shell did not become ready")


def wait_for_prompt(device: serial.Serial, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    captured = bytearray()

    while time.monotonic() < deadline:
        device.write(b"\n")
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
            if any(
                marker in text
                for marker in (
                    "Kernel panic",
                    "Oops:",
                    "BUG:",
                    "MICRONUX:M9:DISPLAY-FAULT",
                )
            ):
                raise RuntimeError("kernel failure observed after USB reconnect")

    raise TimeoutError("Linux USB shell did not return after reconnect")


def capture_passive_output(device: serial.Serial, duration: float) -> str:
    deadline = time.monotonic() + duration
    captured = bytearray()

    while time.monotonic() < deadline:
        chunk = device.read(4096)
        if not chunk:
            continue
        captured.extend(chunk)
        sys.stdout.write(chunk.decode("utf-8", errors="replace"))
        sys.stdout.flush()
    return captured.decode("utf-8", errors="replace")


def reconnect_probe_complete(output: str, token: str) -> bool:
    marker = re.search(
        rf"(?:^|\r?\n){re.escape(token)}\r?\n", output
    )
    return marker is not None and re.search(
        r"(?:^|\r?\n)/ # $", output[marker.end() :]
    ) is not None


def reopen_reconnect_serial(
    port: str, device: serial.Serial, deadline: float
) -> serial.Serial:
    try:
        device.close()
    except (serial.SerialException, OSError):
        pass
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Linux USB endpoint did not reactivate")
    time.sleep(min(0.25, remaining))
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Linux USB endpoint did not reactivate")
    return open_serial(
        port,
        remaining,
        clear_input=False,
        write_timeout=1.0,
    )


def recover_reconnect_prompt(
    port: str, device: serial.Serial, timeout: float
) -> tuple[serial.Serial, str]:
    deadline = time.monotonic() + timeout
    token = f"MICRONUX_M9_RECONNECT_{time.monotonic_ns()}"
    captured = ""
    cancel_partial_line = False

    while time.monotonic() < deadline:
        try:
            captured += capture_passive_output(
                device, min(0.75, max(0.0, deadline - time.monotonic()))
            )
        except (serial.SerialException, OSError):
            device = reopen_reconnect_serial(port, device, deadline)
            cancel_partial_line = True
            continue
        if reconnect_probe_complete(captured, token):
            return device, captured

        payload = b"\x03\n" if cancel_partial_line else f"echo {token}\n".encode(
            "ascii"
        )
        try:
            written = device.write(payload)
            if written != len(payload):
                raise serial.SerialTimeoutException(
                    "short write while recovering USB shell"
                )
            cancel_partial_line = False
        except (serial.SerialException, OSError):
            try:
                captured += capture_passive_output(
                    device, min(0.5, max(0.0, deadline - time.monotonic()))
                )
            except (serial.SerialException, OSError):
                pass
            if reconnect_probe_complete(captured, token):
                return device, captured
            device = reopen_reconnect_serial(port, device, deadline)
            cancel_partial_line = True

    raise TimeoutError("Linux USB shell did not become writable after reconnect")


def run_command(
    device: serial.Serial, command: str, label: str, timeout: float
) -> CommandResult:
    token = f"MICRONUX_M9_{label}_{time.monotonic_ns()}"
    payload = (
        f"echo {token}:BEGIN; {command}; "
        f"m9_rc=$?; echo {token}:END:$m9_rc\n"
    )
    written = device.write(payload.encode("ascii"))
    if written != len(payload):
        raise serial.SerialTimeoutException(f"short serial write: {label}")
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


def display_health_problem(output: str) -> str | None:
    faults = re.findall(r"\bfaults=([0-9a-fA-F]+)\b", output)
    underruns = [
        int(value) for value in re.findall(r"\bunderruns=(\d+)\b", output)
    ]
    errors = re.findall(r"\berrors=([0-9a-fA-F]{8})\b", output)
    host_ints = re.findall(
        r"\bhost-int=([0-9a-fA-F]{8}):([0-9a-fA-F]{8})\b", output
    )
    frame_acks = re.findall(r"\bframe-ack=(\d+)\b", output)
    continuous_hs = re.findall(r"\bcontinuous-hs=(\d+)\b", output)
    lp_disabled = re.findall(r"\blp-disabled=(\d+)\b", output)

    if not faults or any(int(value, 16) for value in faults):
        return "fault-latched-or-missing"
    if not underruns or any(value != 0 for value in underruns):
        return "underrun-nonzero-or-missing"
    if not errors or any(value != "00000000" for value in errors):
        return "dma-error-nonzero-or-missing"
    if not host_ints or any(
        int(status0, 16) or int(status1, 16)
        for status0, status1 in host_ints
    ):
        return "dsi-host-int-nonzero-or-missing"
    if not frame_acks or any(value != "0" for value in frame_acks):
        return "frame-ack-enabled-or-missing"
    if not continuous_hs or any(value != "1" for value in continuous_hs):
        return "continuous-hs-disabled-or-missing"
    if not lp_disabled or any(value != "1" for value in lp_disabled):
        return "video-lp-enabled-or-missing"
    if not re.search(r"^running frames=.*$", output, re.MULTILINE):
        return "scanout-not-running"
    return None


def run_preflight(device: serial.Serial, boot_log: str) -> int:
    if "Linux version 6.12.27" not in boot_log:
        return fail("boot-markers", "kernel-version-missing")
    if "MICRONUX:M9:TOUCH state=ready product=9271" not in boot_log:
        return fail("touch-probe", "ready-marker-missing")
    if (
        "MICRONUX:M7:DSI-SCANOUT state=waiting "
        "trigger=userspace-boot-ready scanout=stopped backlight=off"
        not in boot_log
    ):
        return fail("scanout", "boot-gate-marker-missing")
    if (
        "MICRONUX:M7:DSI-SCANOUT state=ready handoff=blanked-restart "
        "stable-frames=4 scanout=hardware-reload-running "
        "backlight=restored reveal=userspace-ready frame-ack=disabled "
        "clock=forced-hs lp=disabled"
        not in boot_log
    ):
        return fail("scanout", "ready-marker-missing")
    if (
        "MICRONUX:M7:FB-CONSOLE state=ready tty=tty1 role=status "
        "usb=ttyGS0 reveal=userspace-ready cursor=steady"
        not in boot_log
    ):
        return fail("scanout", "status-reveal-marker-missing")
    if "lane_mbps=1500 dpi_mhz=80" not in boot_log:
        return fail("display-timing", "waveshare-profile-marker-missing")
    if "lane_clock=forced-hs video_lp=disabled" not in boot_log:
        return fail("display-link", "continuous-hs-marker-missing")
    if (
        "MICRONUX:M7:DSI-BLANK state=ready backlight=off "
        "settle_ms=100 restore=linux-after-status-ready"
        not in boot_log
    ):
        return fail("display-handoff", "dark-settle-marker-missing")

    check = run_command(
        device, "micronux-display-test check", "CHECK", 30.0
    )
    if check.return_code != 0:
        return fail("interface-check", f"rc-{check.return_code}")
    if "MICRONUX:M9:DISPLAY-TEST:PASS mode=check" not in check.output:
        return fail("interface-check", "pass-marker-missing")
    if "ready product=9271" not in check.output or "errors=0" not in check.output:
        return fail("touch-status", "product-or-error-status")

    sysfs = run_command(
        device,
        f"D={DISPLAY_SYSFS}; "
        'test -w "$D/vpg_test_ms" && '
        'test "$(cat "$D/pattern")" = framebuffer && '
        'test "$(cat "$D/boot_ready")" = 1',
        "DISPLAY_SYSFS",
        20.0,
    )
    if sysfs.return_code != 0:
        return fail("display-sysfs", f"rc-{sysfs.return_code}")

    presentation = run_command(
        device,
        f"D={DISPLAY_SYSFS}; B=/sys/class/backlight/micronux-backlight; "
        "echo MICRONUX:M9:DIAGNOSTICS:BEFORE; "
        "cat $D/diagnostics; cat $D/scanout; "
        "micronux-display-test draw 3 & m9_pid=$!; "
        "sleep 1; echo MICRONUX:M9:DIAGNOSTICS:DURING; "
        "cat $D/diagnostics; cat $D/scanout; "
        "echo MICRONUX:M9:SHELL state=responsive; "
        "wait $m9_pid; m9_draw_rc=$?; "
        "echo MICRONUX:M9:DRAW rc=$m9_draw_rc; "
        "echo MICRONUX:M9:DIAGNOSTICS:AFTER; "
        "cat $D/diagnostics; cat $D/scanout; "
        'echo "MICRONUX:M9:PRESENTATION:RESTORE '
        'pattern=$(cat $D/pattern) boot_ready=$(cat $D/boot_ready) '
        'bl_power=$(cat $B/bl_power) '
        'actual_brightness=$(cat $B/actual_brightness)"; '
        'test "$(cat $D/pattern)" = framebuffer && '
        'test "$(cat $D/boot_ready)" = 1 && '
        'test "$(cat $B/bl_power)" = 0 && '
        'test "$(cat $B/actual_brightness)" -gt 0',
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
    if not re.search(
        r"MICRONUX:M9:PRESENTATION:RESTORE pattern=framebuffer "
        r"boot_ready=1 bl_power=0 actual_brightness=([1-9]\d*)",
        presentation.output,
    ):
        return fail("presentation", "post-restore-source-or-backlight")
    if len(
        re.findall(r"^running frames=.*$", presentation.output, re.MULTILINE)
    ) < 3:
        return fail("presentation", "scanout-not-running-before-during-after")

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
    host_ints = re.findall(
        r"\bhost-int=([0-9a-fA-F]{8}):([0-9a-fA-F]{8})\b",
        presentation.output,
    )
    if len(host_ints) < 3 or any(
        int(status0, 16) or int(status1, 16)
        for status0, status1 in host_ints[-3:]
    ):
        return fail("dsi-host-int", "nonzero-or-missing")
    frame_acks = re.findall(r"\bframe-ack=(\d+)\b", presentation.output)
    if len(frame_acks) < 3 or any(value != "0" for value in frame_acks[-3:]):
        return fail("frame-ack", "enabled-or-missing")
    continuous_hs = re.findall(r"\bcontinuous-hs=(\d+)\b", presentation.output)
    if len(continuous_hs) < 3 or any(value != "1" for value in continuous_hs[-3:]):
        return fail("continuous-hs", "disabled-or-missing")
    lp_disabled = re.findall(r"\blp-disabled=(\d+)\b", presentation.output)
    if len(lp_disabled) < 3 or any(value != "1" for value in lp_disabled[-3:]):
        return fail("video-lp", "enabled-or-missing")
    raw_policy = "host=00000002 active=00000000 lpclk=00000001"
    if presentation.output.count(raw_policy) < 3:
        return fail("display-registers", "continuous-hs-policy-mismatch")

    signal = run_command(
        device,
        f"D={DISPLAY_SYSFS}; B=/sys/class/backlight/micronux-backlight; "
        "echo MICRONUX:M9:SIGNAL-DIAGNOSTICS:BEFORE; "
        "cat $D/diagnostics; cat $D/scanout; "
        "micronux-display-test draw 30 & m9_pid=$!; sleep 4; "
        "kill -TERM $m9_pid; wait $m9_pid; m9_draw_rc=$?; "
        "echo MICRONUX:M9:SIGNAL rc=$m9_draw_rc; "
        "echo MICRONUX:M9:SIGNAL-DIAGNOSTICS:AFTER; "
        "cat $D/diagnostics; cat $D/scanout; "
        'echo "MICRONUX:M9:SIGNAL:RESTORE '
        'pattern=$(cat $D/pattern) boot_ready=$(cat $D/boot_ready) '
        'bl_power=$(cat $B/bl_power) '
        'actual_brightness=$(cat $B/actual_brightness)"; '
        'test "$(cat $D/pattern)" = framebuffer && '
        'test "$(cat $D/boot_ready)" = 1 && '
        'test "$(cat $B/bl_power)" = 0 && '
        'test "$(cat $B/actual_brightness)" -gt 0 && '
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
    if not re.search(
        r"MICRONUX:M9:SIGNAL:RESTORE pattern=framebuffer "
        r"boot_ready=1 bl_power=0 actual_brightness=([1-9]\d*)",
        signal.output,
    ):
        return fail("signal-recovery", "post-restore-source-or-backlight")
    if len(re.findall(r"^running frames=.*$", signal.output, re.MULTILINE)) < 2:
        return fail("signal-recovery", "scanout-not-running-before-after")
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
    signal_host_ints = re.findall(
        r"\bhost-int=([0-9a-fA-F]{8}):([0-9a-fA-F]{8})\b",
        signal.output,
    )
    if len(signal_host_ints) < 2 or any(
        int(status0, 16) or int(status1, 16)
        for status0, status1 in signal_host_ints[-2:]
    ):
        return fail("signal-dsi-host-int", "nonzero-or-missing")
    signal_frame_acks = re.findall(r"\bframe-ack=(\d+)\b", signal.output)
    if len(signal_frame_acks) < 2 or any(
        value != "0" for value in signal_frame_acks[-2:]
    ):
        return fail("signal-frame-ack", "enabled-or-missing")
    signal_continuous_hs = re.findall(r"\bcontinuous-hs=(\d+)\b", signal.output)
    if len(signal_continuous_hs) < 2 or any(
        value != "1" for value in signal_continuous_hs[-2:]
    ):
        return fail("signal-continuous-hs", "disabled-or-missing")
    signal_lp_disabled = re.findall(r"\blp-disabled=(\d+)\b", signal.output)
    if len(signal_lp_disabled) < 2 or any(
        value != "1" for value in signal_lp_disabled[-2:]
    ):
        return fail("signal-video-lp", "enabled-or-missing")

    print(
        "MICRONUX:M9:PREFLIGHT:PASS "
        "touch=9271 shell=responsive console=restored "
        "signal=restored underruns=0 host-errors=0 frame-ack=disabled "
        "clock=forced-hs lp=disabled machine=pass visual=required"
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
        device,
        f"cat {DISPLAY_SYSFS}/diagnostics; cat {DISPLAY_SYSFS}/scanout",
        "TOUCH_DIAGNOSTICS",
        20.0,
    )
    if diagnostics.return_code != 0:
        return fail("touch-diagnostics", f"rc-{diagnostics.return_code}")
    values = re.findall(r"\bunderruns=(\d+)\b", diagnostics.output)
    if not values or int(values[-1]) != 0:
        return fail("touch-underrun", values[-1] if values else "missing")
    host_ints = re.findall(
        r"\bhost-int=([0-9a-fA-F]{8}):([0-9a-fA-F]{8})\b",
        diagnostics.output,
    )
    if not host_ints or any(int(value, 16) for value in host_ints[-1]):
        return fail("touch-dsi-host-int", "nonzero-or-missing")
    if not re.search(
        r"^running frames=.* frame-ack=off clock=forced-hs lp=disabled\r?$",
        diagnostics.output,
        re.MULTILINE,
    ):
        return fail("touch-scanout", "not-running-or-fault-latched")

    print(
        "MICRONUX:M9:TOUCH-GATE:PASS "
        "points=5 console=restored underruns=0"
    )
    return 0


def run_vpg(device: serial.Serial, duration_ms: int) -> int:
    print(
        "MICRONUX:M9:VPG:VISUAL-REQUIRED "
        "expect=vertical-bars-then-status no-cyan-transition"
    )
    result = run_command(
        device,
        f'D={DISPLAY_SYSFS}; B=/sys/class/backlight/micronux-backlight; '
        'm9_boot="$(cat /proc/sys/kernel/random/boot_id)"; '
        'm9_brightness="$(cat "$B/brightness")"; '
        'm9_power="$(cat "$B/bl_power")"; '
        'm9_actual="$(cat "$B/actual_brightness")"; '
        f'echo {duration_ms} >"$D/vpg_test_ms" & m9_vpg_pid=$!; '
        'sleep 1; '
        'echo "MICRONUX:M9:VPG:DURING pattern=$(cat "$D/pattern") '
        'actual_brightness=$(cat "$B/actual_brightness")"; '
        'cat "$D/diagnostics"; '
        'wait $m9_vpg_pid; m9_vpg_rc=$?; '
        'echo "MICRONUX:M9:VPG:AFTER rc=$m9_vpg_rc '
        'pattern=$(cat "$D/pattern") '
        'actual_brightness=$(cat "$B/actual_brightness")"; '
        "/bin/busybox dmesg | /bin/busybox grep 'MICRONUX:M9:VPG state='; "
        'cat "$D/diagnostics"; cat "$D/scanout"; '
        'test $m9_vpg_rc -eq 0 && '
        'test "$(cat "$D/pattern")" = framebuffer && '
        'test "$m9_boot" = "$(cat /proc/sys/kernel/random/boot_id)" && '
        'test "$m9_brightness" = "$(cat "$B/brightness")" && '
        'test "$m9_power" = "$(cat "$B/bl_power")" && '
        'test "$m9_actual" = "$(cat "$B/actual_brightness")" && '
        'test "$m9_actual" -gt 0',
        "VPG",
        duration_ms / 1000.0 + 30.0,
    )
    if result.return_code != 0:
        return fail("vpg", f"rc-{result.return_code}")
    if not re.search(
        r"MICRONUX:M9:VPG:DURING pattern=vertical-bars "
        r"actual_brightness=([1-9]\d*)",
        result.output,
    ):
        return fail("vpg", "vertical-bars-marker-missing")
    during = re.search(
        r"MICRONUX:M9:VPG:DURING pattern=vertical-bars "
        r"actual_brightness=[1-9]\d*\r?\n"
        r"chen=([0-9a-fA-F]{8})",
        result.output,
    )
    if not during or int(during.group(1), 16) == 0:
        return fail("vpg", "producer-not-retained")
    if "host=00010002 active=00000000 lpclk=00000001" not in result.output:
        return fail("vpg", "vpg-register-policy-mismatch")
    if not re.search(
        r"MICRONUX:M9:VPG:AFTER rc=0 pattern=framebuffer "
        r"actual_brightness=([1-9]\d*)",
        result.output,
    ):
        return fail("vpg", "framebuffer-restore-marker-missing")
    if (
        "transition=dark-switched-revealed" not in result.output
        or "transition=dark-switched-primed-revealed" not in result.output
    ):
        return fail("vpg", "dark-transition-marker-missing")
    if result.output.count(
        "host=00000002 active=00000000 lpclk=00000001"
    ) < 1:
        return fail("vpg", "framebuffer-register-policy-mismatch")
    host_ints = re.findall(
        r"\bhost-int=([0-9a-fA-F]{8}):([0-9a-fA-F]{8})\b",
        result.output,
    )
    if len(host_ints) < 2 or any(
        int(status0, 16) or int(status1, 16)
        for status0, status1 in host_ints[-2:]
    ):
        return fail("vpg-dsi-host-int", "nonzero-or-missing")
    if not re.search(
        r"^running frames=.* frame-ack=off clock=forced-hs lp=disabled\r?$",
        result.output,
        re.MULTILINE,
    ):
        return fail("vpg", "scanout-not-restored")

    print(
        "MICRONUX:M9:VPG:AUTOMATED-PASS "
        f"duration_ms={duration_ms} source=restored producer=continuous "
        "linux=retained host-errors=0"
    )
    return 0


def run_soak(device: serial.Serial, soak_seconds: int, sample_seconds: int) -> int:
    sample_count = soak_seconds // sample_seconds + 1
    commands = [
        f"D={DISPLAY_SYSFS}",
        "B=/sys/class/backlight/micronux-backlight",
    ]
    for index in range(sample_count):
        commands.extend(
            (
                f"echo MICRONUX:M9:SOAK-SAMPLE index={index}",
                "cat $D/diagnostics",
                "cat $D/scanout",
                "cat $D/touch",
                f'echo "MICRONUX:M9:SOAK-STATUS index={index} '
                'pattern=$(cat $D/pattern) boot_ready=$(cat $D/boot_ready) '
                'bl_power=$(cat $B/bl_power) '
                'actual_brightness=$(cat $B/actual_brightness)"',
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
    statuses = re.findall(
        r"^MICRONUX:M9:SOAK-STATUS index=(\d+) pattern=framebuffer "
        r"boot_ready=1 bl_power=0 actual_brightness=[1-9]\d*\r?$",
        soak.output,
        flags=re.MULTILINE,
    )
    if statuses != expected:
        return fail("soak", "source-or-backlight-state")
    underruns = [
        int(value) for value in re.findall(r"\bunderruns=(\d+)\b", soak.output)
    ]
    if not underruns or any(value != 0 for value in underruns):
        return fail("soak-underrun", "nonzero-or-missing")
    errors = re.findall(r"\berrors=([0-9a-fA-F]{8})\b", soak.output)
    if len(errors) < sample_count or any(value != "00000000" for value in errors):
        return fail("soak-dma", "nonzero-or-missing")
    host_ints = re.findall(
        r"\bhost-int=([0-9a-fA-F]{8}):([0-9a-fA-F]{8})\b",
        soak.output,
    )
    if len(host_ints) != sample_count or any(
        int(status0, 16) or int(status1, 16)
        for status0, status1 in host_ints
    ):
        return fail("soak-dsi-host-int", "nonzero-or-missing")
    frame_acks = re.findall(r"\bframe-ack=(\d+)\b", soak.output)
    if len(frame_acks) != sample_count or any(value != "0" for value in frame_acks):
        return fail("soak-frame-ack", "enabled-or-missing")
    continuous_hs = re.findall(r"\bcontinuous-hs=(\d+)\b", soak.output)
    if len(continuous_hs) != sample_count or any(
        value != "1" for value in continuous_hs
    ):
        return fail("soak-continuous-hs", "disabled-or-missing")
    lp_disabled = re.findall(r"\blp-disabled=(\d+)\b", soak.output)
    if len(lp_disabled) != sample_count or any(value != "1" for value in lp_disabled):
        return fail("soak-video-lp", "enabled-or-missing")
    if len(
        re.findall(
            r"^running frames=.* frame-ack=off clock=forced-hs lp=disabled\r?$",
            soak.output,
            re.MULTILINE,
        )
    ) != sample_count:
        return fail("soak-scanout", "not-running")
    if len(
        re.findall(r"^ready product=9271", soak.output, re.MULTILINE)
    ) != sample_count:
        return fail("soak-touch", "not-ready")

    print(
        "MICRONUX:M9:SOAK:PASS "
        f"seconds={soak_seconds} samples={sample_count} "
        "shell=responsive scanout=running underruns=0 errors=0 host-errors=0 "
        "frame-ack=disabled clock=forced-hs lp=disabled"
    )
    return 0


def run_disconnect(
    port: str, device: serial.Serial, disconnect_seconds: int
) -> tuple[int, serial.Serial]:
    boundary_path = "/tmp/m9-disconnect-boundary"
    kmsg_path = "/tmp/m9-disconnect-kmsg"
    boundary_delay = disconnect_seconds + 1
    reopen_delay = disconnect_seconds + 3
    status_re = (
        r"pattern=framebuffer boot_ready=1 bl_power=0 "
        r"actual_brightness=([1-9]\d*)"
    )
    boot_id_re = (
        r"([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})"
    )

    print(
        "MICRONUX:M9:USB-DISCONNECT:VISUAL-REQUIRED "
        f"expect=status-remains-visible-no-cyan seconds={disconnect_seconds}"
    )
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
        f'D={DISPLAY_SYSFS}; B=/sys/class/backlight/micronux-backlight; '
        'm9_boot="$(cat /proc/sys/kernel/random/boot_id)"; '
        'echo "MICRONUX:M9:USB-DISCONNECT:BEFORE boot_id=$m9_boot"; '
        'cat "$D/diagnostics"; cat "$D/scanout"; '
        'echo "MICRONUX:M9:USB-DISCONNECT:STATUS-BEFORE '
        'pattern=$(cat $D/pattern) boot_ready=$(cat $D/boot_ready) '
        'bl_power=$(cat $B/bl_power) '
        'actual_brightness=$(cat $B/actual_brightness)"; '
        'test "$(cat $D/pattern)" = framebuffer && '
        'test "$(cat $D/boot_ready)" = 1 && '
        'test "$(cat $B/bl_power)" = 0 && '
        'test "$(cat $B/actual_brightness)" -gt 0',
        "DISCONNECT_BEFORE",
        20.0,
    )
    if before.return_code != 0:
        return fail("disconnect-before", f"rc-{before.return_code}"), device
    if not re.search(
        r"MICRONUX:M9:USB-DISCONNECT:STATUS-BEFORE " + status_re,
        before.output,
    ):
        return fail("disconnect-before", "source-or-backlight"), device
    before_problem = display_health_problem(before.output)
    if before_problem is not None:
        return fail("disconnect-before", before_problem), device
    before_boot_ids = re.findall(
        r"MICRONUX:M9:USB-DISCONNECT:BEFORE boot_id=" + boot_id_re,
        before.output,
    )
    if not before_boot_ids:
        return fail("disconnect-before", "boot-id-missing"), device

    boundary_script = (
        "trap '' HUP; "
        f"sleep {boundary_delay}; "
        f"D={DISPLAY_SYSFS}; B=/sys/class/backlight/micronux-backlight; "
        'm9_boot="$(cat /proc/sys/kernel/random/boot_id)"; '
        'echo "MICRONUX:M9:USB-DISCONNECT:BOUNDARY:BEGIN '
        'boot_id=$m9_boot"; '
        'echo -n "MICRONUX:M9:USB-DISCONNECT:BOUNDARY:UPTIME "; '
        'cat /proc/uptime; '
        'cat "$D/diagnostics"; cat "$D/scanout"; '
        'echo "MICRONUX:M9:USB-DISCONNECT:STATUS-BOUNDARY '
        'pattern=$(cat $D/pattern) boot_ready=$(cat $D/boot_ready) '
        'bl_power=$(cat $B/bl_power) '
        'actual_brightness=$(cat $B/actual_brightness)"; '
        'echo "MICRONUX:M9:USB-DISCONNECT:BOUNDARY:COMPLETE"'
    )
    boundary_arm = run_command(
        device,
        f"rm -f {boundary_path}; "
        "/bin/busybox setsid /bin/busybox sh -c "
        f"{shlex.quote(boundary_script)} </dev/null "
        f">{boundary_path} 2>&1 & "
        'echo "MICRONUX:M9:USB-DISCONNECT:BOUNDARY:ARMED '
        f'delay_seconds={boundary_delay} pid=$!"',
        "DISCONNECT_BOUNDARY_ARM",
        20.0,
    )
    if boundary_arm.return_code != 0 or not re.search(
        r"^MICRONUX:M9:USB-DISCONNECT:BOUNDARY:ARMED "
        rf"delay_seconds={boundary_delay} pid=\d+\r?$",
        boundary_arm.output,
        re.MULTILINE,
    ):
        return fail("disconnect-boundary", "arm-failed"), device

    device.dtr = False
    device.rts = False
    device.close()
    print(
        "MICRONUX:M9:USB-DISCONNECT "
        f"state=closed duration_seconds={disconnect_seconds} "
        f"boundary_delay_seconds={boundary_delay} "
        f"reopen_delay_seconds={reopen_delay}"
    )
    closed_at = time.monotonic()
    time.sleep(reopen_delay)

    opened_at = time.monotonic()
    device = open_serial(
        port, 30.0, clear_input=False, write_timeout=1.0
    )
    passive_deadline = time.monotonic() + 30.0
    while True:
        try:
            passive_log = capture_passive_output(device, 2.0)
            break
        except (serial.SerialException, OSError):
            device = reopen_reconnect_serial(port, device, passive_deadline)
    device, prompt_log = recover_reconnect_prompt(port, device, 30.0)
    reconnect_log = passive_log + prompt_log
    print(
        "MICRONUX:M9:USB-RECONNECT:TRANSPORT "
        f"passive_bytes={len(passive_log.encode('utf-8'))} "
        f"captured_bytes={len(reconnect_log.encode('utf-8'))} "
        f"host_closed_seconds={opened_at - closed_at:.3f} shell=writable"
    )

    kmsg_capture = run_command(
        device,
        f"rm -f {kmsg_path}; "
        "/bin/busybox timeout 2 /bin/busybox cat /proc/kmsg "
        f">{kmsg_path}; "
        'm9_kmsg_rc=$?; echo "MICRONUX:M9:USB-RECONNECT:KMSG '
        'rc=$m9_kmsg_rc"; '
        'case "$m9_kmsg_rc" in 0|124|143) true;; *) false;; esac',
        "DISCONNECT_KMSG_CAPTURE",
        10.0,
    )
    kmsg = run_command(
        device,
        'while IFS= read -r m9_line; do case "$m9_line" in '
        '*MICRONUX:M9:*|*esp32p4-dsi*) printf "%s\\n" "$m9_line";; '
        f"esac; done <{kmsg_path}",
        "DISCONNECT_KMSG_READ",
        20.0,
    )
    boundary = run_command(
        device,
        f"cat {boundary_path}",
        "DISCONNECT_BOUNDARY_READ",
        20.0,
    )
    after_id = run_command(
        device,
        'echo -n "MICRONUX:M9:USB-DISCONNECT:AFTER boot_id="; '
        "cat /proc/sys/kernel/random/boot_id",
        "DISCONNECT_AFTER_ID",
        20.0,
    )
    after_diagnostics = run_command(
        device,
        f"cat {DISPLAY_SYSFS}/diagnostics",
        "DISCONNECT_AFTER_DIAGNOSTICS",
        20.0,
    )
    after_scanout = run_command(
        device,
        f"cat {DISPLAY_SYSFS}/scanout",
        "DISCONNECT_AFTER_SCANOUT",
        20.0,
    )
    after_status = run_command(
        device,
        f'D={DISPLAY_SYSFS}; B=/sys/class/backlight/micronux-backlight; '
        'echo "MICRONUX:M9:USB-DISCONNECT:STATUS-AFTER '
        'pattern=$(cat $D/pattern) boot_ready=$(cat $D/boot_ready) '
        'bl_power=$(cat $B/bl_power) '
        'actual_brightness=$(cat $B/actual_brightness)"',
        "DISCONNECT_AFTER_STATUS",
        20.0,
    )
    if after_id.return_code != 0:
        return fail("disconnect-evidence", "after-boot-id-command-failed"), device
    after_boot_ids = re.findall(
        r"MICRONUX:M9:USB-DISCONNECT:AFTER boot_id=" + boot_id_re,
        after_id.output,
    )
    if not after_boot_ids:
        return fail("disconnect-evidence", "after-boot-id-missing"), device
    if before_boot_ids[-1] != after_boot_ids[-1]:
        print(
            "MICRONUX:M9:USB-DISCONNECT:CLASSIFICATION "
            "phase=disconnect result=fail reason=unexpected-reboot"
        )
        return fail("disconnect-after", "unexpected-reboot"), device

    evidence_results = (
        kmsg_capture,
        kmsg,
        boundary,
        after_diagnostics,
        after_scanout,
        after_status,
    )
    if any(result.return_code != 0 for result in evidence_results):
        return fail("disconnect-evidence", "post-command-failed"), device
    if "MICRONUX:M9:USB-DISCONNECT:BOUNDARY:COMPLETE" not in boundary.output:
        return fail("disconnect-boundary", "evidence-incomplete"), device

    boundary_boot_ids = re.findall(
        r"MICRONUX:M9:USB-DISCONNECT:BOUNDARY:BEGIN boot_id=" + boot_id_re,
        boundary.output,
    )
    if not boundary_boot_ids:
        return fail("disconnect-boundary", "boot-id-missing"), device
    if before_boot_ids[-1] != boundary_boot_ids[-1]:
        print(
            "MICRONUX:M9:USB-DISCONNECT:CLASSIFICATION "
            "phase=disconnect result=fail reason=unexpected-reboot"
        )
        return fail("disconnect-after", "unexpected-reboot"), device

    before_frames = [
        int(value) for value in re.findall(r"\bframes=(\d+)\b", before.output)
    ]
    boundary_frames = [
        int(value) for value in re.findall(r"\bframes=(\d+)\b", boundary.output)
    ]
    boundary_problem = display_health_problem(boundary.output)
    boundary_status_ok = re.search(
        r"MICRONUX:M9:USB-DISCONNECT:STATUS-BOUNDARY " + status_re,
        boundary.output,
    )
    if (
        boundary_problem is not None
        or boundary_status_ok is None
        or not before_frames
        or not boundary_frames
        or boundary_frames[-1] <= before_frames[-1]
    ):
        detail = boundary_problem or (
            "source-or-backlight"
            if boundary_status_ok is None
            else "scanout-did-not-continue"
        )
        print(
            "MICRONUX:M9:USB-DISCONNECT:CLASSIFICATION "
            f"phase=disconnect result=fail reason={detail}"
        )
        return fail("disconnect-failure", detail), device

    after_output = (
        after_id.output
        + after_diagnostics.output
        + after_scanout.output
        + after_status.output
    )
    after_frames = [
        int(value) for value in re.findall(r"\bframes=(\d+)\b", after_output)
    ]
    after_problem = display_health_problem(after_output)
    after_status_ok = re.search(
        r"MICRONUX:M9:USB-DISCONNECT:STATUS-AFTER " + status_re,
        after_status.output,
    )
    if (
        after_problem is not None
        or after_status_ok is None
        or not after_frames
        or after_frames[-1] <= boundary_frames[-1]
    ):
        evidence = reconnect_log + kmsg.output + after_output
        detail = after_problem or (
            "source-or-backlight"
            if after_status_ok is None
            else "scanout-did-not-continue"
        )
        if "MICRONUX:M9:DISPLAY-FAULT" in evidence:
            detail = "display-fault-after-usb-reopen"
        print(
            "MICRONUX:M9:USB-DISCONNECT:CLASSIFICATION "
            f"phase=reconnect result=fail reason={detail}"
        )
        return fail("reconnect-driver-fault", detail), device

    print(
        "MICRONUX:M9:USB-DISCONNECT:CLASSIFICATION "
        "phase=disconnect-and-reconnect result=pass "
        "boundary=healthy reconnect=healthy"
    )
    print(
        "MICRONUX:M9:USB-RECONNECT:PASS "
        f"disconnected_seconds={disconnect_seconds} linux=retained "
        "shell=responsive scanout=running underruns=0 errors=0 "
        "host-errors=0 frame-ack=disabled clock=forced-hs lp=disabled "
        "machine=pass visual=required"
    )
    return 0, device


def run_snapshot(device: serial.Serial, snapshot_seconds: int) -> int:
    captured = capture_passive_output(device, snapshot_seconds)
    print(
        "MICRONUX:M9:SNAPSHOT "
        f"state=passive-capture-complete bytes={len(captured.encode('utf-8'))}"
    )
    if any(
        marker in captured
        for marker in (
            "Kernel panic",
            "Oops:",
            "BUG:",
            "MICRONUX:M9:DISPLAY-FAULT",
        )
    ):
        return fail("snapshot-passive", "kernel-failure-observed")

    wait_for_prompt(device, 30.0)
    snapshot = run_command(
        device,
        'echo -n "MICRONUX:M9:SNAPSHOT boot_id="; '
        "cat /proc/sys/kernel/random/boot_id; "
        'echo -n "MICRONUX:M9:SNAPSHOT uptime="; cat /proc/uptime; '
        f"cat {DISPLAY_SYSFS}/diagnostics; "
        f"cat {DISPLAY_SYSFS}/scanout; "
        f"cat {DISPLAY_SYSFS}/touch",
        "SNAPSHOT",
        30.0,
    )
    if snapshot.return_code != 0:
        return fail("snapshot", f"rc-{snapshot.return_code}")
    faults = re.findall(r"\bfaults=([0-9a-fA-F]+)\b", snapshot.output)
    if not faults or int(faults[-1], 16) != 0:
        return fail("snapshot", "display-fault-latched")
    print(
        "MICRONUX:M9:SNAPSHOT:CAPTURED "
        "reset=not-requested evidence=machine-state visual=unverified"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM14")
    parser.add_argument(
        "--mode",
        choices=("preflight", "touch", "vpg", "soak", "disconnect", "snapshot"),
        required=True,
    )
    parser.add_argument("--point-timeout-ms", type=int, default=60000)
    parser.add_argument("--soak-seconds", type=int, default=240)
    parser.add_argument("--sample-seconds", type=int, default=15)
    parser.add_argument("--disconnect-seconds", type=int, default=600)
    parser.add_argument("--vpg-ms", type=int, default=5000)
    parser.add_argument("--snapshot-seconds", type=int, default=2)
    args = parser.parse_args()

    if args.point_timeout_ms < 5000 or args.point_timeout_ms > 120000:
        parser.error("--point-timeout-ms must be between 5000 and 120000")
    if args.soak_seconds < 60 or args.soak_seconds > 900:
        parser.error("--soak-seconds must be between 60 and 900")
    if args.sample_seconds < 5 or args.sample_seconds > 60:
        parser.error("--sample-seconds must be between 5 and 60")
    if args.sample_seconds > args.soak_seconds:
        parser.error("--sample-seconds cannot exceed --soak-seconds")
    if args.disconnect_seconds < 5 or args.disconnect_seconds > 900:
        parser.error("--disconnect-seconds must be between 5 and 900")
    if args.vpg_ms < 2000 or args.vpg_ms > 10000:
        parser.error("--vpg-ms must be between 2000 and 10000")
    if args.snapshot_seconds < 1 or args.snapshot_seconds > 30:
        parser.error("--snapshot-seconds must be between 1 and 30")

    device: serial.Serial | None = None
    try:
        if args.mode == "snapshot":
            device = open_serial(args.port, 30.0, clear_input=False)
            return run_snapshot(device, args.snapshot_seconds)

        if args.mode == "preflight":
            print(
                "MICRONUX:M9:PREFLIGHT:VISUAL-REQUIRED "
                "expect=clean-loader-to-status-and-draw-to-status-transitions"
            )
        elif args.mode == "disconnect":
            print(
                "MICRONUX:M9:USB-DISCONNECT:VISUAL-REQUIRED "
                "expect=clean-loader-to-status-then-status-remains-no-cyan"
            )
        rom_reset(args.port)
        device = open_serial(args.port, 30.0)
        boot_log = wait_for_shell(device, 120.0)
        if args.mode == "preflight":
            return run_preflight(device, boot_log)
        if args.mode == "touch":
            return run_touch(device, args.point_timeout_ms)
        if args.mode == "vpg":
            return run_vpg(device, args.vpg_ms)
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
