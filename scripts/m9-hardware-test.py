#!/usr/bin/env python3
"""Run the interactive MicroNUX M9.2 display and touch hardware gate."""

from __future__ import annotations

import argparse
import hashlib
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import serial


DISPLAY_SYSFS = "/sys/bus/platform/devices/500a0000.display"
DISCONNECT_BOUNDARY_PATH = "/tmp/m9-disconnect-boundary"
USB_RESET_ARM = Path(__file__).with_name("usb-reset-arm.py")
KERNEL_LOAD_ADDRESS = 0x48400000
DISPLAY_POOL_ADDRESS = 0x49300000
EMPTY_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb924"
    "27ae41e4649b934ca495991b7852b855"
)
FAILURE_MARKERS = (
    "Kernel panic",
    "Oops:",
    "BUG:",
    "MICRONUX:M9:DISPLAY-FAULT",
    "state=REJECTED_NO_WRITES",
    "state=FAILED_QUIESCENT",
    "state=FAILED_UNVERIFIED",
    "REMOVE_UNVERIFIED",
)
SHELL_PROMPT_RE = re.compile(r"(?:^|\r?\n)(?:/ # |micronux# )$")
SCANOUT_LINE_RE = re.compile(
    r"^running abi=3 state=RUNTIME_REVEALED "
    r"frames=(?P<before>\d+)->(?P<after>\d+) "
    r"error=(?P<error>[0-9a-fA-F]{8}) underruns=(?P<underruns>\d+) "
    r"chen=(?P<chen>[01]) faults=(?P<faults>[0-9a-fA-F]+) "
    r"host-errors=(?P<host0>[0-9a-fA-F]{8}):"
    r"(?P<host1>[0-9a-fA-F]{8}) frame-ack=on "
    r"clock=auto lp=enabled backlight-gate=on buffers=(?P<buffers>\d+) "
    r"front=(?P<front>-?\d+) queued=(?P<queued>-?\d+) "
    r"back=(?P<back>-?\d+) rearm=(?P<rearms>\d+)/"
    r"(?P<rearm_failures>\d+) flips=(?P<flip_requests>\d+)/"
    r"(?P<flip_completions>\d+) generation=(?P<generation>\d+) "
    r"commit=(?P<commit>\d+) guards=ok "
    r"guard-errors=(?P<guard_errors>\d+) "
    r"arm-to-irq-ns=(?P<arm_to_irq_last>\d+)/"
    r"(?P<arm_to_irq_max>\d+) "
    r"arm-to-irq-over20ms=(?P<arm_to_irq_over20ms>\d+) "
    r"rearm-ns=(?P<rearm_last>\d+)/(?P<rearm_max>\d+) "
    r"fifo-irq=(?P<fifo_irq_last>\d+)/(?P<fifo_irq_min>\d+)/"
    r"(?P<fifo_irq_zero>\d+) "
    r"fifo-poll=(?P<fifo_poll_last>\d+)/(?P<fifo_poll_min>\d+)/"
    r"(?P<fifo_poll_zero>\d+) "
    r"bridge-filler=(?P<bridge_filler>[0-9a-fA-F]{8}) "
    r"bridge-misc=(?P<bridge_misc>[0-9a-fA-F]{8}) "
    r"i2c=active-serialized\r?$",
    re.MULTILINE,
)
DIAGNOSTICS_LINE_RE = re.compile(
    r"^abi=3 state=RUNTIME_REVEALED "
    r"frames=(?P<frames>\d+) "
    r"faults=(?P<faults>[0-9a-fA-F]+) "
    r"error=(?P<error>[0-9a-fA-F]{8}) "
    r"host-errors=(?P<host0>[0-9a-fA-F]{8}):"
    r"(?P<host1>[0-9a-fA-F]{8}) underruns=(?P<underruns>\d+) "
    r"buffers=(?P<buffers>\d+) front=(?P<front>-?\d+) "
    r"queued=(?P<queued>-?\d+) back=(?P<back>-?\d+) "
    r"rearm=(?P<rearms>\d+)/(?P<rearm_failures>\d+) "
    r"flips=(?P<flip_requests>\d+)/(?P<flip_completions>\d+) "
    r"generation=(?P<generation>\d+) commit=(?P<commit>\d+) "
    r"guards=ok guard-errors=(?P<guard_errors>\d+) policy=ok "
    r"arm-to-irq-ns=(?P<arm_to_irq_last>\d+)/"
    r"(?P<arm_to_irq_max>\d+) "
    r"arm-to-irq-over20ms=(?P<arm_to_irq_over20ms>\d+) "
    r"rearm-ns=(?P<rearm_last>\d+)/(?P<rearm_max>\d+) "
    r"fifo-irq=(?P<fifo_irq_last>\d+)/(?P<fifo_irq_min>\d+)/"
    r"(?P<fifo_irq_zero>\d+) "
    r"fifo-poll=(?P<fifo_poll_last>\d+)/(?P<fifo_poll_min>\d+)/"
    r"(?P<fifo_poll_zero>\d+) "
    r"bridge-filler=(?P<bridge_filler>[0-9a-fA-F]{8}) "
    r"bridge-misc=(?P<bridge_misc>[0-9a-fA-F]{8}) "
    r"i2c=active-serialized physical-panel-state=unobserved\r?$",
    re.MULTILINE,
)
OWNERSHIP_LINE_RE = re.compile(
    r"^linux abi=3 mode=native-cold-init fb0 buffers=3 dma-channel=0 "
    r"frame-irq=(?P<irq>\d+) rearm=explicit mmap=denied "
    r"i2c=active-serialized\r?$",
    re.MULTILINE,
)
TOUCH_LINE_RE = re.compile(
    r"^(?:unavailable|ready product=9271 address=0x(?:5d|14) mode=poll "
    r"interval_ms=10 reads=\d+ errors=0 down=[01])\r?$",
    re.MULTILINE,
)
RUNTIME_STATUS_RE = re.compile(
    r"pattern=framebuffer boot_ready=1 native_state=RUNTIME_REVEALED "
    r"brightness=63 bl_power=0 actual_brightness=63",
)
DISCONNECT_UPTIME_RE = re.compile(
    r"^MICRONUX:M9:USB-DISCONNECT:"
    r"(?P<phase>BEFORE-POSTHASH|BOUNDARY-PREHASH|"
    r"BOUNDARY-POSTHASH|AFTER-PREHASH):UPTIME "
    r"(?P<uptime>[0-9]+(?:\.[0-9]+)?) "
    r"[0-9]+(?:\.[0-9]+)?\r?\n"
    r"(?=abi=3 state=RUNTIME_REVEALED frames=)",
    re.MULTILINE,
)
DIAGNOSTICS_PHASE_RE = re.compile(
    r"^MICRONUX:M9:DIAGNOSTICS phase=(?P<phase>[A-Z0-9-]+)\r?$",
    re.MULTILINE,
)
MIN_DISCONNECT_PROGRESS_HZ = 10.0
MAX_DISCONNECT_PROGRESS_HZ = 25.0
MAX_REARM_NS = 1_000_000


@dataclass(frozen=True)
class CommandResult:
    output: str
    return_code: int


def failure_marker(output: str) -> str | None:
    return next((marker for marker in FAILURE_MARKERS if marker in output), None)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_boot_payload(boot_log: str, artifact_dir: Path) -> str | None:
    image = artifact_dir / "Image"
    dtb = artifact_dir / "esp32p4-micronux.dtb"
    metadata = artifact_dir / "metadata.bin"
    for artifact in (image, dtb, metadata):
        if not artifact.is_file():
            return f"artifact-missing-{artifact.name}"

    kernel_matches = re.findall(
        r"MICRONUX:M3:KERNEL .* file=(\d+) memory=(\d+) "
        r"sha256=([0-9a-f]{64})",
        boot_log,
    )
    dtb_matches = re.findall(
        r"MICRONUX:M3:DTB .* size=(\d+) sha256=([0-9a-f]{64})",
        boot_log,
    )
    if len(kernel_matches) != 1:
        return f"kernel-marker-count-{len(kernel_matches)}"
    if len(dtb_matches) != 1:
        return f"dtb-marker-count-{len(dtb_matches)}"

    kernel_file_size, kernel_memory_size, kernel_digest = kernel_matches[0]
    dtb_size, dtb_digest = dtb_matches[0]
    logged_file_size = int(kernel_file_size)
    logged_memory_size = int(kernel_memory_size)
    logged_dtb_size = int(dtb_size)
    if logged_file_size != image.stat().st_size:
        return "kernel-size-mismatch"
    if logged_memory_size < logged_file_size:
        return "kernel-memory-size-invalid"
    if KERNEL_LOAD_ADDRESS + logged_memory_size > DISPLAY_POOL_ADDRESS:
        return "kernel-memory-overlaps-display-pool"
    if logged_dtb_size != dtb.stat().st_size:
        return "dtb-size-mismatch"
    if metadata.stat().st_size != 128:
        return "metadata-size-mismatch"

    metadata_bytes = metadata.read_bytes()
    metadata_fields = tuple(
        int.from_bytes(metadata_bytes[offset:offset + 4], "little")
        for offset in range(0, 32, 4)
    )
    (
        metadata_magic,
        metadata_abi,
        metadata_size,
        metadata_flags,
        metadata_load,
        metadata_file_size,
        metadata_memory_size,
        metadata_dtb_size,
    ) = metadata_fields
    if (
        metadata_magic != 0x33584E4D
        or metadata_abi != 1
        or metadata_size != 128
        or metadata_flags != 1
        or metadata_load != KERNEL_LOAD_ADDRESS
        or metadata_file_size != logged_file_size
        or metadata_memory_size != logged_memory_size
        or metadata_dtb_size != logged_dtb_size
    ):
        return "metadata-payload-contract-mismatch"
    if metadata_load + metadata_memory_size > DISPLAY_POOL_ADDRESS:
        return "metadata-memory-overlaps-display-pool"
    if kernel_digest != sha256_file(image):
        return "kernel-sha256-mismatch"
    if dtb_digest != sha256_file(dtb):
        return "dtb-sha256-mismatch"
    return None


def verify_runtime_identity(device: serial.Serial) -> str | None:
    identity = run_command(
        device,
        'm9_boot_a="$(cat /proc/sys/kernel/random/boot_id)"; '
        'm9_uptime_a="$(cat /proc/uptime)"; '
        'echo "MICRONUX:M9.2:RUNTIME sample=a boot_id=$m9_boot_a"; '
        'echo "MICRONUX:M9.2:RUNTIME sample=a uptime=$m9_uptime_a"; '
        "sleep 1; "
        'm9_boot_b="$(cat /proc/sys/kernel/random/boot_id)"; '
        'm9_uptime_b="$(cat /proc/uptime)"; '
        'echo "MICRONUX:M9.2:RUNTIME sample=b boot_id=$m9_boot_b"; '
        'echo "MICRONUX:M9.2:RUNTIME sample=b uptime=$m9_uptime_b"',
        "RUNTIME_IDENTITY",
        20.0,
    )
    if identity.return_code != 0:
        return f"command-rc-{identity.return_code}"
    boot_samples = re.findall(
        r"^MICRONUX:M9\.2:RUNTIME sample=([ab]) boot_id="
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
        r"[0-9a-f]{4}-[0-9a-f]{12})\r?$",
        identity.output,
        re.MULTILINE,
    )
    uptime_samples = re.findall(
        r"^MICRONUX:M9\.2:RUNTIME sample=([ab]) uptime="
        r"([0-9]+(?:\.[0-9]+)?) [0-9]+(?:\.[0-9]+)?\r?$",
        identity.output,
        re.MULTILINE,
    )
    if [sample for sample, _ in boot_samples] != ["a", "b"]:
        return "boot-id-samples-invalid"
    boot_ids = [boot_id for _, boot_id in boot_samples]
    if boot_ids[0] != boot_ids[1]:
        return "boot-id-changed"
    if [sample for sample, _ in uptime_samples] != ["a", "b"]:
        return "uptime-samples-invalid"
    uptimes = [uptime for _, uptime in uptime_samples]
    if float(uptimes[1]) <= float(uptimes[0]):
        return "uptime-not-advancing"
    print(
        "MICRONUX:M9.2:RUNTIME state=verified "
        f"boot_id={boot_ids[0]} uptime={uptimes[0]}->{uptimes[1]}"
    )
    return None


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


def close_serial_quietly(device: serial.Serial) -> None:
    try:
        device.dtr = False
    except (serial.SerialException, OSError):
        pass
    try:
        device.rts = False
    except (serial.SerialException, OSError):
        pass
    try:
        device.close()
    except (serial.SerialException, OSError):
        pass


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
        if ready in text and SHELL_PROMPT_RE.search(text):
            time.sleep(0.2)
            device.reset_output_buffer()
            return text
        marker = failure_marker(text)
        if marker is not None:
            raise RuntimeError(
                f"kernel failure observed while waiting for shell: {marker}"
            )

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
            if SHELL_PROMPT_RE.search(text):
                return text
            marker = failure_marker(text)
            if marker is not None:
                raise RuntimeError(
                    f"kernel failure observed after USB reconnect: {marker}"
                )

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


def reconnect_prompt_complete(output: str, response_start: int) -> bool:
    return SHELL_PROMPT_RE.search(output[response_start:]) is not None


def reopen_reconnect_serial(
    port: str, device: serial.Serial, deadline: float
) -> serial.Serial:
    close_serial_quietly(device)
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
    captured = ""
    quote_closers = (b'"\n', b"'\n")
    closer_index = 0

    while time.monotonic() < deadline:
        try:
            captured += capture_passive_output(
                device, min(0.75, max(0.0, deadline - time.monotonic()))
            )
        except (serial.SerialException, OSError):
            device = reopen_reconnect_serial(port, device, deadline)
            continue

        if re.search(r"(?:^|\r?\n)> $", captured[-2048:]):
            payload = quote_closers[closer_index % len(quote_closers)]
            closer_index += 1
        else:
            payload = b"\n"
        response_start = len(captured)
        try:
            written = device.write(payload)
            if written != len(payload):
                raise serial.SerialTimeoutException(
                    "short write while recovering USB shell"
                )
            captured += capture_passive_output(
                device, min(0.75, max(0.0, deadline - time.monotonic()))
            )
            if reconnect_prompt_complete(captured, response_start):
                return device, captured
        except (serial.SerialException, OSError):
            try:
                captured += capture_passive_output(
                    device, min(0.5, max(0.0, deadline - time.monotonic()))
                )
            except (serial.SerialException, OSError):
                pass
            if reconnect_prompt_complete(captured, response_start):
                return device, captured
            device = reopen_reconnect_serial(port, device, deadline)

    close_serial_quietly(device)
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
        marker = failure_marker(text)
        if marker is not None:
            raise RuntimeError(f"kernel failure observed during {label}: {marker}")
        match = end_pattern.search(text)
        if match is not None:
            return CommandResult(text, int(match.group(1)))

    raise TimeoutError(f"serial command timed out: {label}")


def fail(stage: str, detail: str) -> int:
    print(f"MICRONUX:M9:HARDWARE-TEST:FAIL stage={stage} detail={detail}")
    return 1


def scanout_contract_problem(
    output: str,
    minimum: int = 1,
    require_flip: bool = False,
    require_progress: bool = False,
) -> str | None:
    samples = scanout_samples(output)
    if len(samples) < minimum:
        return f"scanout-contract-count-{len(samples)}-expected-{minimum}"
    for index, values in enumerate(samples):
        if values["after"] <= values["before"]:
            return "scanout-frame-stalled"
        if (
            values["error"]
            or values["underruns"]
            or values["chen"] != 1
            or values["faults"]
            or values["host0"]
            or values["host1"]
            or values["guard_errors"]
        ):
            return "scanout-health-nonzero"
        if values["buffers"] != 3:
            return "scanout-buffer-count"
        front = values["front"]
        queued = values["queued"]
        back = values["back"]
        if front not in range(3) or back not in range(3) or front == back:
            return "scanout-front-back-role"
        if queued != -1 and (
            queued not in range(3) or len({front, queued, back}) != 3
        ):
            return "scanout-queued-role"
        if values["rearms"] < 1 or values["rearm_failures"]:
            return "scanout-rearm-counter"
        if values["arm_to_irq_over20ms"]:
            return "scanout-arm-to-irq-over20ms"
        if values["rearm_max"] >= MAX_REARM_NS:
            return "scanout-rearm-latency"
        if values["fifo_poll_zero"]:
            return "scanout-fifo-poll-zero"
        if values["bridge_filler"]:
            return "scanout-bridge-filler"
        if values["bridge_misc"] != 0x00003201:
            return "scanout-bridge-misc"
        if values["flip_completions"] > values["flip_requests"]:
            return "scanout-flip-counter"
        if values["generation"] < 1:
            return "scanout-generation"
        if values["commit"] < 1:
            return "scanout-commit"
        if index:
            previous = samples[index - 1]
            for counter in (
                "before",
                "after",
                "rearms",
                "flip_requests",
                "flip_completions",
                "generation",
                "commit",
                "arm_to_irq_over20ms",
                "fifo_irq_zero",
                "fifo_poll_zero",
            ):
                if values[counter] < previous[counter]:
                    return f"scanout-{counter}-regressed"
    if require_flip and (
        len(samples) < 2
        or samples[-1]["flip_completions"]
        <= samples[0]["flip_completions"]
        or samples[-1]["commit"] <= samples[0]["commit"]
    ):
        return "scanout-flip-not-observed"
    if require_progress and (
        len(samples) < 2
        or samples[-1]["after"] <= samples[0]["after"]
    ):
        return "scanout-progress-not-observed"
    return None


def scanout_samples(output: str) -> list[dict[str, int]]:
    hexadecimal = {
        "error",
        "faults",
        "host0",
        "host1",
        "bridge_filler",
        "bridge_misc",
    }
    return [
        {
            key: int(value, 16) if key in hexadecimal else int(value)
            for key, value in match.groupdict().items()
        }
        for match in SCANOUT_LINE_RE.finditer(output)
    ]


def diagnostics_samples(output: str) -> list[dict[str, int]]:
    hexadecimal = {
        "error",
        "faults",
        "host0",
        "host1",
        "bridge_filler",
        "bridge_misc",
    }
    return [
        {
            key: int(value, 16) if key in hexadecimal else int(value)
            for key, value in match.groupdict().items()
        }
        for match in DIAGNOSTICS_LINE_RE.finditer(output)
    ]


def diagnostics_phase_groups(
    output: str,
) -> list[tuple[str, list[dict[str, int]]]]:
    markers = list(DIAGNOSTICS_PHASE_RE.finditer(output))
    groups: list[tuple[str, list[dict[str, int]]]] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(output)
        groups.append(
            (
                marker.group("phase"),
                diagnostics_samples(output[marker.end():end]),
            )
        )
    return groups


def diagnostics_phase_problem(
    output: str,
    expected_phases: tuple[str, ...],
) -> str | None:
    groups = diagnostics_phase_groups(output)
    actual_phases = tuple(phase for phase, _ in groups)
    if actual_phases != expected_phases:
        return "diagnostics-phase-order"

    samples: list[tuple[str, dict[str, int]]] = []
    for phase, phase_samples in groups:
        if len(phase_samples) != 1:
            return f"diagnostics-phase-{phase.lower()}-count-{len(phase_samples)}"
        sample = phase_samples[0]
        if sample["fifo_poll_zero"]:
            return f"diagnostics-phase-{phase.lower()}-fifo-poll-zero"
        samples.append((phase, sample))

    if samples and samples[0][1]["arm_to_irq_over20ms"]:
        return (
            f"diagnostics-phase-{samples[0][0].lower()}-"
            "arm-to-irq-over20ms-nonzero"
        )

    for index in range(1, len(samples)):
        previous_phase, previous = samples[index - 1]
        phase, current = samples[index]
        for counter in (
            "arm_to_irq_over20ms",
            "fifo_irq_zero",
            "fifo_poll_zero",
        ):
            if current[counter] < previous[counter]:
                return (
                    f"diagnostics-phase-{previous_phase.lower()}-to-"
                    f"{phase.lower()}-{counter.replace('_', '-')}-regressed"
                )
        if current["arm_to_irq_over20ms"] != previous["arm_to_irq_over20ms"]:
            return (
                f"diagnostics-phase-{previous_phase.lower()}-to-"
                f"{phase.lower()}-arm-to-irq-over20ms-increased"
            )
    return None


def diagnostics_contract_problem(
    output: str,
    minimum: int = 1,
    require_flip: bool = False,
    require_progress: bool = False,
) -> str | None:
    samples = diagnostics_samples(output)
    if len(samples) < minimum:
        return f"diagnostics-contract-count-{len(samples)}-expected-{minimum}"
    for index, values in enumerate(samples):
        if (
            values["faults"]
            or values["error"]
            or values["host0"]
            or values["host1"]
            or values["underruns"]
            or values["rearm_failures"]
            or values["guard_errors"]
        ):
            return "diagnostics-health-nonzero"
        if values["buffers"] != 3:
            return "diagnostics-buffer-count"
        front = values["front"]
        queued = values["queued"]
        back = values["back"]
        if front not in range(3) or back not in range(3) or front == back:
            return "diagnostics-front-back-role"
        if queued != -1 and (
            queued not in range(3) or len({front, queued, back}) != 3
        ):
            return "diagnostics-queued-role"
        if values["frames"] < 1 or values["generation"] < 1:
            return "diagnostics-progress-counter"
        if values["rearms"] < 1 or values["commit"] < 1:
            return "diagnostics-runtime-counter"
        if values["arm_to_irq_over20ms"]:
            return "diagnostics-arm-to-irq-over20ms"
        if values["rearm_max"] >= MAX_REARM_NS:
            return "diagnostics-rearm-latency"
        if values["fifo_poll_zero"]:
            return "diagnostics-fifo-poll-zero"
        if values["bridge_filler"]:
            return "diagnostics-bridge-filler"
        if values["bridge_misc"] != 0x00003201:
            return "diagnostics-bridge-misc"
        if values["flip_completions"] > values["flip_requests"]:
            return "diagnostics-flip-counter"
        if index:
            previous = samples[index - 1]
            for counter in (
                "frames",
                "rearms",
                "flip_requests",
                "flip_completions",
                "generation",
                "commit",
                "arm_to_irq_over20ms",
                "fifo_irq_zero",
                "fifo_poll_zero",
            ):
                if values[counter] < previous[counter]:
                    return f"diagnostics-{counter}-regressed"
    if require_flip and (
        len(samples) < 2
        or samples[-1]["flip_completions"]
        <= samples[0]["flip_completions"]
        or samples[-1]["commit"] <= samples[0]["commit"]
    ):
        return "diagnostics-flip-not-observed"
    if require_progress and (
        len(samples) < 2
        or samples[-1]["frames"] <= samples[0]["frames"]
    ):
        return "diagnostics-progress-not-observed"
    return None


def display_health_problem(
    output: str,
    minimum: int = 1,
    require_flip: bool = False,
    require_progress: bool = False,
) -> str | None:
    marker = failure_marker(output)
    if marker is not None:
        return f"terminal-failure-{marker.replace(' ', '-')}"
    diagnostics_problem = diagnostics_contract_problem(
        output, minimum, require_flip, require_progress
    )
    if diagnostics_problem is not None:
        return diagnostics_problem
    return scanout_contract_problem(
        output, minimum, require_flip, require_progress
    )


def disconnect_progress_rate(
    start_output: str,
    start_phase: str,
    end_output: str,
    end_phase: str,
) -> tuple[str | None, float | None]:
    start_uptimes = [
        float(match.group("uptime"))
        for match in DISCONNECT_UPTIME_RE.finditer(start_output)
        if match.group("phase") == start_phase
    ]
    end_uptimes = [
        float(match.group("uptime"))
        for match in DISCONNECT_UPTIME_RE.finditer(end_output)
        if match.group("phase") == end_phase
    ]
    start_groups = [
        samples
        for phase, samples in diagnostics_phase_groups(start_output)
        if phase == start_phase
    ]
    end_groups = [
        samples
        for phase, samples in diagnostics_phase_groups(end_output)
        if phase == end_phase
    ]
    if len(start_uptimes) != 1:
        return f"{start_phase.lower()}-uptime-count-{len(start_uptimes)}", None
    if len(end_uptimes) != 1:
        return f"{end_phase.lower()}-uptime-count-{len(end_uptimes)}", None
    if len(start_groups) != 1 or len(start_groups[0]) != 1:
        return (
            f"{start_phase.lower()}-diagnostics-count-"
            f"{sum(len(samples) for samples in start_groups)}",
            None,
        )
    if len(end_groups) != 1 or len(end_groups[0]) != 1:
        return (
            f"{end_phase.lower()}-diagnostics-count-"
            f"{sum(len(samples) for samples in end_groups)}",
            None,
        )

    elapsed = end_uptimes[0] - start_uptimes[0]
    progress_delta = end_groups[0][0]["frames"] - start_groups[0][0]["frames"]
    if elapsed <= 0:
        return f"{start_phase.lower()}-{end_phase.lower()}-uptime-order", None
    if progress_delta <= 0:
        return f"{start_phase.lower()}-{end_phase.lower()}-progress-order", None
    progress_hz = progress_delta / elapsed
    if not MIN_DISCONNECT_PROGRESS_HZ <= progress_hz <= MAX_DISCONNECT_PROGRESS_HZ:
        return (
            f"{start_phase.lower()}-{end_phase.lower()}-progress-hz-"
            f"{progress_hz:.3f}",
            progress_hz,
        )
    return None, progress_hz


def runtime_status_problem(output: str, minimum: int = 1) -> str | None:
    count = len(RUNTIME_STATUS_RE.findall(output))
    if count < minimum:
        return f"runtime-status-count-{count}-expected-{minimum}"
    return None


def wait_for_touch_terminal(device: serial.Serial) -> str | None:
    result = run_command(
        device,
        f"D={DISPLAY_SYSFS}; m9_touch_wait=0; m9_touch_state=registering; "
        'while [ "$m9_touch_wait" -lt 20 ]; do '
        'm9_touch_state="$(cat "$D/touch" 2>/dev/null)" || exit 2; '
        'if [ "$m9_touch_state" != registering ]; then break; fi; '
        'm9_touch_wait=$((m9_touch_wait + 1)); sleep 1; done; '
        'printf "%s\\n" "$m9_touch_state"; '
        'test "$m9_touch_state" != registering',
        "TOUCH_TERMINAL",
        30.0,
    )
    if result.return_code != 0:
        return f"touch-terminal-command-rc-{result.return_code}"
    if TOUCH_LINE_RE.search(result.output) is None:
        return "touch-terminal-state-invalid"
    return None


def diagnostics_phase_shell(
    phase: str,
    uptime_phase: str | None = None,
) -> str:
    command = f'echo "MICRONUX:M9:DIAGNOSTICS phase={phase}"; '
    if uptime_phase is not None:
        command += (
            f'echo -n "MICRONUX:M9:USB-DISCONNECT:{uptime_phase}:UPTIME "; '
            "cat /proc/uptime; "
        )
    return command + 'cat "$D/diagnostics"; '


def framebuffer_hash_shell(phase: str, variable: str) -> str:
    return (
        f'unset {variable}; '
        'if m9_fb_line="$(/bin/busybox sha256sum /dev/fb0)"; then '
        f'{variable}="${{m9_fb_line%% *}}"; '
        f'echo "MICRONUX:M9:FB-SHA256 phase={phase} '
        f'sha256=${variable}"; fi; '
    )


def framebuffer_hash_problem(
    output: str, phases: tuple[str, ...]
) -> str | None:
    hashes: list[tuple[str, str]] = []
    for phase in phases:
        matches = re.findall(
            rf"^MICRONUX:M9:FB-SHA256 phase={re.escape(phase)} "
            r"sha256=([0-9a-f]{64})\r?$",
            output,
            re.MULTILINE,
        )
        if len(matches) != 1:
            return f"phase-{phase}-count-{len(matches)}"
        if matches[0] == EMPTY_SHA256:
            return f"phase-{phase}-empty"
        hashes.append((phase, matches[0]))
    reference_phase, reference = hashes[0]
    for phase, value in hashes[1:]:
        if value != reference:
            return f"signature-changed-{reference_phase}-to-{phase}"
    return None


def boot_contract_problem(boot_log: str) -> str | None:
    marker = failure_marker(boot_log)
    if marker is not None:
        return f"terminal-failure-{marker.replace(' ', '-')}"
    if "Linux version 6.12.27" not in boot_log:
        return "kernel-version-missing"

    ordered_markers = (
        "MICRONUX:M9.2:COLD-EXTERNAL state=ready",
        "MICRONUX:M9.2:COLD-MEMORY state=ready",
        "MICRONUX:M9.2:COLD-PREPARE state=ready abi=3 panel=jd9365",
        "MICRONUX:M9.2:COLD-HANDOFF state=ready",
        "MICRONUX:M9.2:COLD-STAGE state=ready abi=3 size=0x00c0",
        "MICRONUX:M9.2:COLD-PROBE state=PROBED_QUIESCENT abi=3",
        "MICRONUX:M9.2:COLD-INIT state=START trigger=sysfs",
        "MICRONUX:M9.2:COLD-INIT state=LDO_READY",
        "MICRONUX:M9.2:COLD-INIT stage=native-dsi state=READY",
        "MICRONUX:M9.2:COLD-INIT stage=panel-release state=READY",
        "MICRONUX:M9.2:COLD-INIT stage=panel-commands state=COMPLETE",
        "MICRONUX:M9.2:COLD-INIT state=PANEL_PROGRAMMED_QUIESCENT",
        "MICRONUX:M9.2:COLD-INIT state=SCANOUT_INITIALIZING",
        "MICRONUX:M9.2:COLD-INIT stage=memory state=READY",
        "MICRONUX:M9.2:COLD-INIT stage=dpi-bridge state=CONFIGURED",
        "MICRONUX:M9.2:COLD-INIT stage=gdma-irq state=CONFIGURED",
        "MICRONUX:M9.2:COLD-INIT "
        "stage=scanout-qualification state=PASSED",
        "MICRONUX:M9.2:COLD-INIT state=SCANOUT_QUALIFIED_QUIESCENT",
        "MICRONUX:M9.2:REVEAL state=REVEALING trigger=boot_ready",
        "MICRONUX:M9.2:REVEAL state=QUALIFIED source=userspace-status",
        "MICRONUX:M9.2:REVEAL stage=control state=ACKED command=0x17 pwm=0",
        "MICRONUX:M9.2:REVEAL state=RUNTIME_REVEALED boot-ready=1",
        "MICRONUX:M7:FB-CONSOLE state=ready tty=tty1 role=status "
        "usb=ttyGS0 reveal=userspace-ready native-state=RUNTIME_REVEALED",
    )
    position = -1
    for expected in ordered_markers:
        found = boot_log.find(expected, position + 1)
        if found < 0:
            return f"missing-or-out-of-order-{expected.split()[0]}-{expected.split()[1]}"
        position = found

    required_fragments = (
        "pwm-zero-write=acked reset-prepare-write=acked "
        "pwm-zero-settle=elapsed reset-assert-write=acked "
        "reset-hold=elapsed i2c=released",
        "flags=0x00003fff route=ready dma-pms=ready "
        "panel-payload-crc=cea07f9b contract=invalid",
        "firmware=validated records=204 dcs-packets=205",
        "ctrl-hi=c0108840 host=video frame-bta=enabled lp=all "
        "lpclk=00000003",
        "frames=4 same-front=yes control-command=0x17-acked "
        "pwm-command=63-acked brightness=63 backlight=registered "
        "i2c=active-serialized",
        "physical-panel-state=unobserved",
    )
    for fragment in required_fragments:
        if fragment not in boot_log:
            return f"boot-contract-fragment-{fragment.split()[0]}"
    return None


def run_preflight(device: serial.Serial, boot_log: str) -> int:
    problem = boot_contract_problem(boot_log)
    if problem is not None:
        return fail("boot-markers", problem)

    check = run_command(
        device, "micronux-display-test check", "CHECK", 30.0
    )
    if check.return_code != 0:
        return fail("interface-check", f"rc-{check.return_code}")
    if "MICRONUX:M9:DISPLAY-TEST:PASS mode=check" not in check.output:
        return fail("interface-check", "pass-marker-missing")
    if (
        "ready product=9271" not in check.output
        and "touch=unavailable" not in check.output
        and "input=unavailable" not in check.output
    ):
        return fail("touch-status", "ready-or-unavailable-marker-missing")
    if "ready product=9271" in check.output and "errors=0" not in check.output:
        return fail("touch-status", "ready-status-has-errors")

    sysfs = run_command(
        device,
        f"D={DISPLAY_SYSFS}; B=/sys/class/backlight/micronux-backlight; "
        'test "$(cat "$D/native_state")" = RUNTIME_REVEALED && '
        'test "$(cat "$D/pattern")" = framebuffer && '
        'test "$(cat "$D/boot_ready")" = 1 && '
        'test "$(cat "$B/brightness")" = 63 && '
        'test "$(cat "$B/bl_power")" = 0 && '
        'test "$(cat "$B/actual_brightness")" = 63 && '
        '{ cat "$D/native_state"; cat "$D/ownership"; '
        'cat "$D/diagnostics"; cat "$D/scanout"; cat "$D/touch"; '
        'echo "MICRONUX:M9:NATIVE-RUNTIME pattern=$(cat "$D/pattern") '
        'boot_ready=$(cat "$D/boot_ready") '
        'native_state=$(cat "$D/native_state") '
        'brightness=$(cat "$B/brightness") bl_power=$(cat "$B/bl_power") '
        'actual_brightness=$(cat "$B/actual_brightness")"; }',
        "DISPLAY_SYSFS",
        20.0,
    )
    if sysfs.return_code != 0:
        return fail("display-sysfs", f"rc-{sysfs.return_code}")
    if OWNERSHIP_LINE_RE.search(sysfs.output) is None:
        return fail("display-sysfs", "native-ownership-missing")
    if TOUCH_LINE_RE.search(sysfs.output) is None:
        return fail("display-sysfs", "touch-state-invalid")
    status_problem = runtime_status_problem(sysfs.output)
    if status_problem is not None:
        return fail("display-sysfs", status_problem)
    sysfs_problem = display_health_problem(sysfs.output)
    if sysfs_problem is not None:
        return fail("display-sysfs", sysfs_problem)

    vpg_unavailable = run_command(
        device,
        f"D={DISPLAY_SYSFS}; B=/sys/class/backlight/micronux-backlight; "
        + framebuffer_hash_shell("vpg-reject-before", "m9_fb_before")
        +
        'cat "$D/diagnostics"; cat "$D/scanout"; '
        'if [ ! -e "$D/vpg_test_ms" ]; then '
        'm9_vpg_unavailable=1; else m9_vpg_unavailable=0; fi; '
        "sleep 1; "
        + framebuffer_hash_shell("vpg-reject-after", "m9_fb_after")
        +
        'cat "$D/diagnostics"; cat "$D/scanout"; '
        'echo "MICRONUX:M9:VPG-UNAVAILABLE absent=$m9_vpg_unavailable '
        'pattern=$(cat "$D/pattern") boot_ready=$(cat "$D/boot_ready") '
        'native_state=$(cat "$D/native_state") '
        'brightness=$(cat "$B/brightness") bl_power=$(cat "$B/bl_power") '
        'actual_brightness=$(cat "$B/actual_brightness")"; '
        'test "$m9_vpg_unavailable" = 1 && '
        'test "$m9_fb_before" = "$m9_fb_after"',
        "VPG_UNAVAILABLE",
        20.0,
    )
    if vpg_unavailable.return_code != 0:
        return fail("native-vpg-unavailable", f"rc-{vpg_unavailable.return_code}")
    framebuffer_problem = framebuffer_hash_problem(
        vpg_unavailable.output, ("vpg-reject-before", "vpg-reject-after")
    )
    if framebuffer_problem is not None:
        return fail("native-vpg-unavailable", framebuffer_problem)
    if "MICRONUX:M9:VPG-UNAVAILABLE absent=1" not in vpg_unavailable.output:
        return fail("native-vpg-unavailable", "absence-marker-missing")
    status_problem = runtime_status_problem(vpg_unavailable.output)
    if status_problem is not None:
        return fail("native-vpg-unavailable", status_problem)
    health_problem = display_health_problem(
        vpg_unavailable.output, minimum=2, require_progress=True
    )
    if health_problem is not None:
        return fail("native-vpg-unavailable", health_problem)

    presentation = run_command(
        device,
        f"D={DISPLAY_SYSFS}; B=/sys/class/backlight/micronux-backlight; "
        + framebuffer_hash_shell("presentation-before", "m9_fb_before")
        +
        "echo MICRONUX:M9:DIAGNOSTICS:BEFORE; "
        "cat $D/diagnostics; cat $D/scanout; "
        "micronux-display-test draw 3 & m9_pid=$!; "
        "sleep 1; echo MICRONUX:M9:DIAGNOSTICS:DURING; "
        "cat $D/diagnostics; cat $D/scanout; "
        "echo MICRONUX:M9:SHELL state=responsive; "
        "wait $m9_pid; m9_draw_rc=$?; "
        "echo MICRONUX:M9:DRAW rc=$m9_draw_rc; "
        + framebuffer_hash_shell("presentation-after", "m9_fb_after")
        +
        "echo MICRONUX:M9:DIAGNOSTICS:AFTER; "
        "cat $D/diagnostics; cat $D/scanout; "
        'echo "MICRONUX:M9:PRESENTATION:RESTORE '
        'pattern=$(cat $D/pattern) boot_ready=$(cat $D/boot_ready) '
        'native_state=$(cat $D/native_state) '
        'brightness=$(cat $B/brightness) bl_power=$(cat $B/bl_power) '
        'actual_brightness=$(cat $B/actual_brightness)"; '
        'test "$(cat $D/pattern)" = framebuffer && '
        'test "$(cat $D/boot_ready)" = 1 && '
        'test "$(cat $D/native_state)" = RUNTIME_REVEALED && '
        'test "$(cat $B/brightness)" = 63 && '
        'test "$(cat $B/bl_power)" = 0 && '
        'test "$(cat $B/actual_brightness)" = 63 && '
        'test "$m9_fb_before" = "$m9_fb_after"',
        "PRESENTATION",
        45.0,
    )
    if presentation.return_code != 0:
        return fail("presentation", f"rc-{presentation.return_code}")
    framebuffer_problem = framebuffer_hash_problem(
        presentation.output,
        ("presentation-before", "presentation-after"),
    )
    if framebuffer_problem is not None:
        return fail("presentation-framebuffer", framebuffer_problem)
    for marker in (
        "MICRONUX:M9:SHELL state=responsive",
        "MICRONUX:M9:DRAW rc=0",
        "MICRONUX:M9:DISPLAY-TEST:PASS mode=draw console=restored",
    ):
        if marker not in presentation.output:
            return fail("presentation", f"missing-{marker}")
    status_problem = runtime_status_problem(presentation.output)
    if status_problem is not None:
        return fail("presentation", status_problem)
    health_problem = display_health_problem(
        presentation.output,
        minimum=3,
        require_flip=True,
        require_progress=True,
    )
    if health_problem is not None:
        return fail("presentation-health", health_problem)

    signal = run_command(
        device,
        f"D={DISPLAY_SYSFS}; B=/sys/class/backlight/micronux-backlight; "
        + framebuffer_hash_shell("signal-before", "m9_fb_before")
        +
        "echo MICRONUX:M9:SIGNAL-DIAGNOSTICS:BEFORE; "
        "cat $D/diagnostics; cat $D/scanout; "
        "micronux-display-test draw 30 & m9_pid=$!; sleep 4; "
        "kill -TERM $m9_pid; wait $m9_pid; m9_draw_rc=$?; "
        "echo MICRONUX:M9:SIGNAL rc=$m9_draw_rc; "
        + framebuffer_hash_shell("signal-after", "m9_fb_after")
        +
        "echo MICRONUX:M9:SIGNAL-DIAGNOSTICS:AFTER; "
        "cat $D/diagnostics; cat $D/scanout; "
        'echo "MICRONUX:M9:SIGNAL:RESTORE '
        'pattern=$(cat $D/pattern) boot_ready=$(cat $D/boot_ready) '
        'native_state=$(cat $D/native_state) '
        'brightness=$(cat $B/brightness) bl_power=$(cat $B/bl_power) '
        'actual_brightness=$(cat $B/actual_brightness)"; '
        'test "$(cat $D/pattern)" = framebuffer && '
        'test "$(cat $D/boot_ready)" = 1 && '
        'test "$(cat $D/native_state)" = RUNTIME_REVEALED && '
        'test "$(cat $B/brightness)" = 63 && '
        'test "$(cat $B/bl_power)" = 0 && '
        'test "$(cat $B/actual_brightness)" = 63 && '
        'test "$m9_fb_before" = "$m9_fb_after" && '
        "echo MICRONUX:M9:SIGNAL-SHELL state=responsive",
        "SIGNAL",
        45.0,
    )
    if signal.return_code != 0:
        return fail("signal-recovery", f"rc-{signal.return_code}")
    framebuffer_problem = framebuffer_hash_problem(
        signal.output, ("signal-before", "signal-after")
    )
    if framebuffer_problem is not None:
        return fail("signal-framebuffer", framebuffer_problem)
    for marker in (
        "MICRONUX:M9:SIGNAL rc=143",
        "MICRONUX:M9:SIGNAL-SHELL state=responsive",
    ):
        if marker not in signal.output:
            return fail("signal-recovery", f"missing-{marker}")
    status_problem = runtime_status_problem(signal.output)
    if status_problem is not None:
        return fail("signal-recovery", status_problem)
    health_problem = display_health_problem(
        signal.output,
        minimum=2,
        require_flip=True,
        require_progress=True,
    )
    if health_problem is not None:
        return fail("signal-health", health_problem)

    print(
        "MICRONUX:M9:PREFLIGHT:PASS "
        "touch=ready-or-unavailable shell=responsive console=restored "
        "abi=3 mode=native-cold-init buffers=3 scanout=one-shot-explicit-rearm "
        "rearm-failures=0 signal=restored underruns=0 host-errors=0 "
        "frame-ack=enabled clock=auto lp=enabled backlight-gate=enabled "
        "vpg=unavailable "
        "brightness=63 brightness-source=cached-last-acked-write "
        "physical-panel-state=unobserved machine=pass visual=required"
    )
    return 0


def run_touch(device: serial.Serial, point_timeout_ms: int) -> int:
    touch = run_command(
        device,
        f"D={DISPLAY_SYSFS}; B=/sys/class/backlight/micronux-backlight; "
        "m9_touch_ready=0; m9_touch_wait=0; "
        'while [ "$m9_touch_wait" -lt 20 ]; do '
        'm9_touch_state="$(cat "$D/touch")"; '
        'printf "%s\\n" "$m9_touch_state"; '
        "if printf '%s\\n' \"$m9_touch_state\" | /bin/busybox grep -Eq "
        "'^ready product=9271 address=0x(5d|14) mode=poll interval_ms=10 "
        "reads=[0-9]+ errors=0 down=[01]$'; then "
        "m9_touch_ready=1; break; fi; "
        "m9_touch_wait=$((m9_touch_wait + 1)); sleep 1; done; "
        'echo "MICRONUX:M9:TOUCH-READY ready=$m9_touch_ready '
        'wait_seconds=$m9_touch_wait"; '
        "m9_touch_rc=125; "
        'if [ "$m9_touch_ready" = 1 ]; then '
        'cat "$D/diagnostics"; cat "$D/scanout"; '
        + framebuffer_hash_shell("touch-before", "m9_fb_before")
        + f"micronux-display-test touch {point_timeout_ms}; "
        "m9_touch_rc=$?; "
        + framebuffer_hash_shell("touch-after", "m9_fb_after")
        +
        'cat "$D/diagnostics"; cat "$D/scanout"; cat "$D/touch"; '
        "fi; "
        'echo "MICRONUX:M9:NATIVE-RUNTIME '
        'pattern=$(cat "$D/pattern") boot_ready=$(cat "$D/boot_ready") '
        'native_state=$(cat "$D/native_state") '
        'brightness=$(cat "$B/brightness") bl_power=$(cat "$B/bl_power") '
        'actual_brightness=$(cat "$B/actual_brightness")"; '
        'test "$m9_touch_ready" = 1 && '
        'test "$m9_touch_rc" -eq 0 && '
        'test "$m9_fb_before" = "$m9_fb_after" && '
        'test "$(cat "$D/pattern")" = framebuffer && '
        'test "$(cat "$D/boot_ready")" = 1 && '
        'test "$(cat "$D/native_state")" = RUNTIME_REVEALED && '
        'test "$(cat "$B/brightness")" = 63 && '
        'test "$(cat "$B/bl_power")" = 0 && '
        'test "$(cat "$B/actual_brightness")" = 63',
        "TOUCH",
        point_timeout_ms * 5 / 1000.0 + 65.0,
    )
    if touch.return_code != 0:
        return fail("five-point-touch", f"rc-{touch.return_code}")
    framebuffer_problem = framebuffer_hash_problem(
        touch.output, ("touch-before", "touch-after")
    )
    if framebuffer_problem is not None:
        return fail("touch-framebuffer", framebuffer_problem)
    if "MICRONUX:M9:DISPLAY-TEST:PASS mode=touch points=5" not in touch.output:
        return fail("five-point-touch", "pass-marker-missing")
    if touch.output.count("MICRONUX:M9:TOUCH-TARGET:PASS") != 5:
        return fail("five-point-touch", "point-count")
    ready_samples = re.findall(
        r"^ready product=9271 address=0x(?:5d|14) mode=poll interval_ms=10 "
        r"reads=\d+ errors=0 down=[01]\r?$",
        touch.output,
        re.MULTILINE,
    )
    if not ready_samples or "MICRONUX:M9:TOUCH-READY ready=1" not in touch.output:
        return fail("five-point-touch", "ready-timeout")
    status_problem = runtime_status_problem(touch.output)
    if status_problem is not None:
        return fail("touch-runtime", status_problem)
    health_problem = display_health_problem(
        touch.output,
        minimum=2,
        require_flip=True,
        require_progress=True,
    )
    if health_problem is not None:
        return fail("touch-health", health_problem)

    print(
        "MICRONUX:M9:TOUCH-GATE:PASS "
        "points=5 touch=ready console=restored abi=3 "
        "state=RUNTIME_REVEALED buffers=3 scanout=one-shot-explicit-rearm "
        "frame-ack=enabled faults=0 underruns=0 host-errors=0 "
        "brightness=63 brightness-source=cached-last-acked-write "
        "physical-panel-state=unobserved visual=required"
    )
    return 0


def run_soak(device: serial.Serial, soak_seconds: int, sample_seconds: int) -> int:
    sample_count = soak_seconds // sample_seconds + 1
    commands = [
        f"D={DISPLAY_SYSFS}",
        "B=/sys/class/backlight/micronux-backlight",
        "m9_soak_rc=0",
    ]
    for index in range(sample_count):
        commands.extend(
            (
                f"echo MICRONUX:M9:SOAK-SAMPLE index={index}",
                "cat $D/diagnostics",
                "cat $D/scanout",
                "cat $D/touch",
                framebuffer_hash_shell(
                    f"soak-{index}", "m9_fb_hash"
                ).removesuffix("; "),
                f'echo "MICRONUX:M9:SOAK-STATUS index={index} '
                'pattern=$(cat $D/pattern) boot_ready=$(cat $D/boot_ready) '
                'native_state=$(cat $D/native_state) '
                'brightness=$(cat $B/brightness) bl_power=$(cat $B/bl_power) '
                'actual_brightness=$(cat $B/actual_brightness)"',
                '[ "$(cat $D/pattern)" = framebuffer ] || m9_soak_rc=1',
                '[ "$(cat $D/boot_ready)" = 1 ] || m9_soak_rc=1',
                '[ "$(cat $D/native_state)" = RUNTIME_REVEALED ] || '
                "m9_soak_rc=1",
                '[ "$(cat $B/brightness)" = 63 ] || m9_soak_rc=1',
                '[ "$(cat $B/bl_power)" = 0 ] || m9_soak_rc=1',
                '[ "$(cat $B/actual_brightness)" = 63 ] || m9_soak_rc=1',
                f"echo MICRONUX:M9:SOAK-SHELL state=responsive index={index}",
            )
        )
        if index + 1 < sample_count:
            commands.append(f"sleep {sample_seconds}")
    commands.append('[ "$m9_soak_rc" -eq 0 ]')
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
    framebuffer_problem = framebuffer_hash_problem(
        soak.output,
        tuple(f"soak-{index}" for index in range(sample_count)),
    )
    if framebuffer_problem is not None:
        return fail("soak-framebuffer", framebuffer_problem)
    statuses = re.findall(
        r"^MICRONUX:M9:SOAK-STATUS index=(\d+) pattern=framebuffer "
        r"boot_ready=1 native_state=RUNTIME_REVEALED brightness=63 "
        r"bl_power=0 actual_brightness=63\r?$",
        soak.output,
        flags=re.MULTILINE,
    )
    if statuses != expected:
        return fail("soak", "source-or-backlight-state")
    if len(diagnostics_samples(soak.output)) != sample_count:
        return fail("soak-diagnostics", "sample-count")
    if len(scanout_samples(soak.output)) != sample_count:
        return fail("soak-scanout", "sample-count")
    touch_samples = list(TOUCH_LINE_RE.finditer(soak.output))
    if len(touch_samples) != sample_count:
        return fail("soak-touch", "ready-or-unavailable-sample-count")
    health_problem = display_health_problem(
        soak.output, minimum=sample_count, require_progress=True
    )
    if health_problem is not None:
        return fail("soak-health", health_problem)

    print(
        "MICRONUX:M9:SOAK:PASS "
        f"seconds={soak_seconds} samples={sample_count} "
        "shell=responsive framebuffer=stable touch=ready-or-unavailable "
        "abi=3 state=RUNTIME_REVEALED buffers=3 scanout=one-shot-explicit-rearm "
        "faults=0 underruns=0 host-errors=0 guards=ok "
        "frame-ack=enabled clock=auto lp=enabled backlight-gate=enabled "
        "brightness=63 brightness-source=cached-last-acked-write "
        "physical-panel-state=unobserved visual=required"
    )
    return 0


def stress_snapshot_shell(stage: str) -> str:
    return (
        f'echo "MICRONUX:M9.2:STRESS:SNAPSHOT stage={stage}"; '
        'cat "$D/diagnostics"; cat "$D/scanout"; cat "$D/touch"; '
        f'echo "MICRONUX:M9.2:STRESS:MEM stage={stage}"; '
        "/bin/busybox grep -E '^(MemFree|MemAvailable):' /proc/meminfo"
    )


def run_stress(device: serial.Serial, stress_cycles: int) -> int:
    cycle_arguments = " ".join(
        str(cycle) for cycle in range(1, stress_cycles + 1)
    )
    commands = [
        f"D={DISPLAY_SYSFS}",
        "B=/sys/class/backlight/micronux-backlight",
        "m9_stress_rc=0",
        framebuffer_hash_shell(
            "stress-before", "m9_fb_before"
        ).removesuffix("; "),
        stress_snapshot_shell("before"),
        "micronux-display-test draw 300 & m9_draw_pid=$!",
        "sleep 1",
        stress_snapshot_shell("graphics"),
        (
            "/usr/bin/micronux-storage-test --write-test; "
            "m9_storage_rc=$?; "
            '[ "$m9_storage_rc" -eq 0 ] || m9_stress_rc=1; '
            'echo "MICRONUX:M9.2:STRESS:WORKLOAD '
            'stage=storage rc=$m9_storage_rc"'
        ),
        stress_snapshot_shell("storage"),
        (
            "/usr/bin/micronux-online; m9_online_rc=$?; "
            '[ "$m9_online_rc" -eq 0 ] || m9_stress_rc=1; '
            'echo "MICRONUX:M9.2:STRESS:WORKLOAD '
            'stage=network-online rc=$m9_online_rc"'
        ),
        stress_snapshot_shell("network"),
        (
            f"/usr/bin/micronux-combined-soak {cycle_arguments}; "
            "m9_io_rc=$?; "
            '[ "$m9_io_rc" -eq 0 ] || m9_stress_rc=1; '
            'echo "MICRONUX:M9.2:STRESS:WORKLOAD '
            f'stage=sd-network cycles={stress_cycles} rc=$m9_io_rc"'
        ),
        stress_snapshot_shell("io"),
        (
            "/usr/bin/micronux-selftest; m9_memory_rc=$?; "
            '[ "$m9_memory_rc" -eq 0 ] || m9_stress_rc=1; '
            'echo "MICRONUX:M9.2:STRESS:WORKLOAD '
            'stage=memory-pressure kib=4096 rc=$m9_memory_rc"'
        ),
        stress_snapshot_shell("memory"),
        "/bin/busybox kill -TERM $m9_draw_pid 2>/dev/null || :",
        "wait $m9_draw_pid; m9_draw_rc=$?",
        '[ "$m9_draw_rc" -eq 143 ] || m9_stress_rc=1',
        'echo "MICRONUX:M9.2:STRESS:WORKLOAD '
        'stage=graphics-restore rc=$m9_draw_rc"',
        "sleep 1",
        framebuffer_hash_shell(
            "stress-after", "m9_fb_after"
        ).removesuffix("; "),
        stress_snapshot_shell("after"),
        (
            'echo "MICRONUX:M9.2:STRESS:STATUS '
            'pattern=$(cat $D/pattern) boot_ready=$(cat $D/boot_ready) '
            'native_state=$(cat $D/native_state) '
            'brightness=$(cat $B/brightness) bl_power=$(cat $B/bl_power) '
            'actual_brightness=$(cat $B/actual_brightness)"'
        ),
        '[ "$m9_fb_before" = "$m9_fb_after" ] || m9_stress_rc=1',
        '[ "$(cat $D/pattern)" = framebuffer ] || m9_stress_rc=1',
        '[ "$(cat $D/boot_ready)" = 1 ] || m9_stress_rc=1',
        '[ "$(cat $D/native_state)" = RUNTIME_REVEALED ] || '
        "m9_stress_rc=1",
        '[ "$(cat $B/brightness)" = 63 ] || m9_stress_rc=1',
        '[ "$(cat $B/bl_power)" = 0 ] || m9_stress_rc=1',
        '[ "$(cat $B/actual_brightness)" = 63 ] || m9_stress_rc=1',
        '[ "$m9_stress_rc" -eq 0 ]',
    ]
    result = run_command(
        device,
        "; ".join(commands),
        "STRESS",
        520.0,
    )
    if result.return_code != 0:
        return fail("stress", f"rc-{result.return_code}")

    framebuffer_problem = framebuffer_hash_problem(
        result.output, ("stress-before", "stress-after")
    )
    if framebuffer_problem is not None:
        return fail("stress-framebuffer", framebuffer_problem)

    expected_stages = (
        "before",
        "graphics",
        "storage",
        "network",
        "io",
        "memory",
        "after",
    )
    snapshots = re.findall(
        r"^MICRONUX:M9\.2:STRESS:SNAPSHOT stage=([a-z-]+)\r?$",
        result.output,
        re.MULTILINE,
    )
    if tuple(snapshots) != expected_stages:
        return fail("stress-snapshots", "missing-duplicate-or-out-of-order")

    memory_samples = re.findall(
        r"^MICRONUX:M9\.2:STRESS:MEM stage=([a-z-]+)\r?\n"
        r"MemFree:\s+(\d+) kB\r?\n"
        r"MemAvailable:\s+(\d+) kB\r?$",
        result.output,
        re.MULTILINE,
    )
    if tuple(sample[0] for sample in memory_samples) != expected_stages:
        return fail("stress-memory", "missing-duplicate-or-out-of-order")
    before_free = int(memory_samples[0][1])
    after_free = int(memory_samples[-1][1])
    before_available = int(memory_samples[0][2])
    after_available = int(memory_samples[-1][2])
    if before_free - after_free > 512:
        return fail("stress-memory", "memfree-loss-over-512-kib")
    if before_available - after_available > 512:
        return fail("stress-memory", "memavailable-loss-over-512-kib")

    required_markers = (
        "MICRONUX:M6:STORAGE:PASS "
        "mode=idmac-sram-poll access=write-tested cleanup=pass",
        "MICRONUX:M6:NET:ONLINE state=ready name=ethsta0",
        f"MICRONUX:M6:COMBINED:SOAK:PASS cycles={stress_cycles}",
        "MICRONUX:M5:PASS exec=64 signals=64 timers=32 "
        "memory_kib=4096",
        "MICRONUX:M9.2:STRESS:WORKLOAD stage=storage rc=0",
        "MICRONUX:M9.2:STRESS:WORKLOAD stage=network-online rc=0",
        "MICRONUX:M9.2:STRESS:WORKLOAD "
        f"stage=sd-network cycles={stress_cycles} rc=0",
        "MICRONUX:M9.2:STRESS:WORKLOAD "
        "stage=memory-pressure kib=4096 rc=0",
        "MICRONUX:M9.2:STRESS:WORKLOAD stage=graphics-restore rc=143",
    )
    for marker in required_markers:
        if marker not in result.output:
            return fail("stress-workload", f"missing-{marker}")
    if not re.search(
        r"^MICRONUX:M9\.2:STRESS:STATUS pattern=framebuffer "
        r"boot_ready=1 native_state=RUNTIME_REVEALED brightness=63 "
        r"bl_power=0 actual_brightness=63\r?$",
        result.output,
        re.MULTILINE,
    ):
        return fail("stress-restore", "source-or-backlight-state")

    health_problem = display_health_problem(
        result.output,
        minimum=len(expected_stages),
        require_flip=True,
        require_progress=True,
    )
    if health_problem is not None:
        return fail("stress-health", health_problem)
    if len(diagnostics_samples(result.output)) != len(expected_stages):
        return fail("stress-diagnostics", "snapshot-count")
    if len(scanout_samples(result.output)) != len(expected_stages):
        return fail("stress-scanout", "snapshot-count")
    if len(list(TOUCH_LINE_RE.finditer(result.output))) != len(expected_stages):
        return fail("stress-touch", "ready-or-unavailable-snapshot-count")

    print(
        "MICRONUX:M9.2:STRESS:PASS "
        f"cycles={stress_cycles} sd=write-tested network=traffic "
        "usb=attached framebuffer=paced page-flip=complete "
        "memory_kib=4096 memory_loss_limit_kib=512 "
        f"memfree_kib={before_free}->{after_free} "
        f"memavailable_kib={before_available}->{after_available} "
        "abi=3 state=RUNTIME_REVEALED touch=ready-or-unavailable "
        "buffers=3 scanout=one-shot-explicit-rearm "
        "faults=0 underruns=0 host-errors=0 guards=ok "
        "frame-ack=enabled clock=auto lp=enabled "
        "brightness=63 brightness-source=cached-last-acked-write "
        "physical-panel-state=unobserved visual=required"
    )
    return 0


def run_disconnect(
    port: str, device: serial.Serial, disconnect_seconds: int
) -> tuple[int, serial.Serial]:
    boundary_delay = disconnect_seconds + 1
    reopen_delay = disconnect_seconds + 3
    status_re = (
        r"pattern=framebuffer boot_ready=1 "
        r"native_state=RUNTIME_REVEALED brightness=63 "
        r"bl_power=0 actual_brightness=63"
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
        + diagnostics_phase_shell("BEFORE-PREHASH")
        + framebuffer_hash_shell("disconnect-before", "m9_fb_hash")
        + diagnostics_phase_shell("BEFORE-POSTHASH", "BEFORE-POSTHASH")
        + 'cat "$D/scanout"; cat "$D/touch"; '
        'echo "MICRONUX:M9:USB-DISCONNECT:STATUS-BEFORE '
        'pattern=$(cat $D/pattern) boot_ready=$(cat $D/boot_ready) '
        'native_state=$(cat $D/native_state) '
        'brightness=$(cat $B/brightness) bl_power=$(cat $B/bl_power) '
        'actual_brightness=$(cat $B/actual_brightness)"; '
        'test "$(cat $D/pattern)" = framebuffer && '
        'test "$(cat $D/boot_ready)" = 1 && '
        'test "$(cat $D/native_state)" = RUNTIME_REVEALED && '
        'test "$(cat $B/brightness)" = 63 && '
        'test "$(cat $B/bl_power)" = 0 && '
        'test "$(cat $B/actual_brightness)" = 63',
        "DISCONNECT_BEFORE",
        20.0,
    )
    if before.return_code != 0:
        return fail("disconnect-before", f"rc-{before.return_code}"), device
    framebuffer_problem = framebuffer_hash_problem(
        before.output, ("disconnect-before",)
    )
    if framebuffer_problem is not None:
        return fail("disconnect-before-framebuffer", framebuffer_problem), device
    before_phase_problem = diagnostics_phase_problem(
        before.output,
        ("BEFORE-PREHASH", "BEFORE-POSTHASH"),
    )
    if before_phase_problem is not None:
        return fail("disconnect-before-telemetry", before_phase_problem), device
    if not re.search(
        r"MICRONUX:M9:USB-DISCONNECT:STATUS-BEFORE " + status_re,
        before.output,
    ):
        return fail("disconnect-before", "source-or-backlight"), device
    before_problem = display_health_problem(before.output)
    if before_problem is not None:
        return fail("disconnect-before", before_problem), device
    if len(list(TOUCH_LINE_RE.finditer(before.output))) != 1:
        return fail("disconnect-before", "touch-state-invalid"), device
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
        + diagnostics_phase_shell("BOUNDARY-PREHASH", "BOUNDARY-PREHASH")
        + framebuffer_hash_shell("disconnect-boundary", "m9_fb_hash")
        + diagnostics_phase_shell(
            "BOUNDARY-POSTHASH", "BOUNDARY-POSTHASH"
        )
        + 'cat "$D/scanout"; cat "$D/touch"; '
        'echo "MICRONUX:M9:USB-DISCONNECT:STATUS-BOUNDARY '
        'pattern=$(cat $D/pattern) boot_ready=$(cat $D/boot_ready) '
        'native_state=$(cat $D/native_state) '
        'brightness=$(cat $B/brightness) bl_power=$(cat $B/bl_power) '
        'actual_brightness=$(cat $B/actual_brightness)"; '
        'echo "MICRONUX:M9:USB-DISCONNECT:BOUNDARY:COMPLETE"'
    )
    boundary_arm = run_command(
        device,
        f"rm -f {DISCONNECT_BOUNDARY_PATH}; "
        "/bin/busybox setsid /bin/busybox sh -c "
        f"{shlex.quote(boundary_script)} </dev/null "
        f">{DISCONNECT_BOUNDARY_PATH} 2>&1 & "
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
    reconnect_failure = failure_marker(reconnect_log)
    if reconnect_failure is not None:
        return (
            fail(
                "reconnect-driver-fault",
                f"terminal-failure-{reconnect_failure.replace(' ', '-')}",
            ),
            device,
        )

    boundary = run_command(
        device,
        f"cat {DISCONNECT_BOUNDARY_PATH}",
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
    after_state = run_command(
        device,
        f'D={DISPLAY_SYSFS}; B=/sys/class/backlight/micronux-backlight; '
        + diagnostics_phase_shell("AFTER-PREHASH", "AFTER-PREHASH")
        + framebuffer_hash_shell("disconnect-after", "m9_fb_hash")
        + diagnostics_phase_shell("AFTER-POSTHASH")
        + 'cat "$D/touch"; '
        'echo "MICRONUX:M9:USB-DISCONNECT:STATUS-AFTER '
        'pattern=$(cat $D/pattern) boot_ready=$(cat $D/boot_ready) '
        'native_state=$(cat $D/native_state) '
        'brightness=$(cat $B/brightness) bl_power=$(cat $B/bl_power) '
        'actual_brightness=$(cat $B/actual_brightness)"',
        "DISCONNECT_AFTER_STATE",
        20.0,
    )
    after_scanout = run_command(
        device,
        f"cat {DISPLAY_SYSFS}/scanout",
        "DISCONNECT_AFTER_SCANOUT",
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
        boundary,
        after_state,
        after_scanout,
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

    before_scanout = scanout_samples(before.output)
    boundary_scanout = scanout_samples(boundary.output)
    disconnect_rate_problem, disconnect_progress_hz = disconnect_progress_rate(
        before.output,
        "BEFORE-POSTHASH",
        boundary.output,
        "BOUNDARY-PREHASH",
    )
    before_boundary_output = before.output + "\n" + boundary.output
    boundary_phase_problem = diagnostics_phase_problem(
        before_boundary_output,
        (
            "BEFORE-PREHASH",
            "BEFORE-POSTHASH",
            "BOUNDARY-PREHASH",
            "BOUNDARY-POSTHASH",
        ),
    )
    boundary_problem = display_health_problem(
        before_boundary_output, minimum=2, require_progress=True
    )
    boundary_framebuffer_problem = framebuffer_hash_problem(
        before_boundary_output,
        ("disconnect-before", "disconnect-boundary"),
    )
    boundary_status_ok = re.search(
        r"MICRONUX:M9:USB-DISCONNECT:STATUS-BOUNDARY " + status_re,
        boundary.output,
    )
    if (
        boundary_phase_problem is not None
        or boundary_problem is not None
        or disconnect_rate_problem is not None
        or boundary_framebuffer_problem is not None
        or boundary_status_ok is None
        or len(diagnostics_samples(before.output)) != 2
        or len(diagnostics_samples(boundary.output)) != 2
        or len(list(TOUCH_LINE_RE.finditer(boundary.output))) != 1
        or len(before_scanout) != 1
        or len(boundary_scanout) != 1
        or boundary_scanout[-1]["after"] <= before_scanout[-1]["after"]
    ):
        detail = (
            boundary_phase_problem
            or boundary_problem
            or disconnect_rate_problem
            or boundary_framebuffer_problem
            or (
                "source-or-backlight"
                if boundary_status_ok is None
                else "scanout-did-not-continue"
            )
        )
        print(
            "MICRONUX:M9:USB-DISCONNECT:CLASSIFICATION "
            f"phase=disconnect result=fail reason={detail}"
        )
        return fail("disconnect-failure", detail), device

    after_output = (
        after_id.output
        + after_state.output
        + after_scanout.output
    )
    after_scanout_samples = scanout_samples(after_scanout.output)
    reconnect_rate_problem, reconnect_progress_hz = disconnect_progress_rate(
        boundary.output,
        "BOUNDARY-POSTHASH",
        after_state.output,
        "AFTER-PREHASH",
    )
    full_health_output = "\n".join(
        (
            before.output,
            boundary.output,
            after_state.output,
            after_scanout.output,
        )
    )
    all_phase_problem = diagnostics_phase_problem(
        full_health_output,
        (
            "BEFORE-PREHASH",
            "BEFORE-POSTHASH",
            "BOUNDARY-PREHASH",
            "BOUNDARY-POSTHASH",
            "AFTER-PREHASH",
            "AFTER-POSTHASH",
        ),
    )
    after_problem = display_health_problem(
        full_health_output, minimum=3, require_progress=True
    )
    after_framebuffer_problem = framebuffer_hash_problem(
        "\n".join((before.output, boundary.output, after_state.output)),
        ("disconnect-before", "disconnect-boundary", "disconnect-after"),
    )
    after_status_ok = re.search(
        r"MICRONUX:M9:USB-DISCONNECT:STATUS-AFTER " + status_re,
        after_state.output,
    )
    if (
        all_phase_problem is not None
        or after_problem is not None
        or reconnect_rate_problem is not None
        or after_framebuffer_problem is not None
        or after_status_ok is None
        or len(diagnostics_samples(after_state.output)) != 2
        or len(list(TOUCH_LINE_RE.finditer(after_state.output))) != 1
        or len(after_scanout_samples) != 1
        or after_scanout_samples[-1]["after"] <= boundary_scanout[-1]["after"]
    ):
        evidence = reconnect_log + after_output
        detail = (
            all_phase_problem
            or after_problem
            or reconnect_rate_problem
            or after_framebuffer_problem
            or (
                "source-or-backlight"
                if after_status_ok is None
                else "scanout-did-not-continue"
            )
        )
        if "MICRONUX:M9:DISPLAY-FAULT" in evidence:
            detail = "display-fault-after-usb-reopen"
        print(
            "MICRONUX:M9:USB-DISCONNECT:CLASSIFICATION "
            f"phase=reconnect result=fail reason={detail}"
        )
        return fail("reconnect-driver-fault", detail), device

    phase_samples = [
        samples[0]
        for _, samples in diagnostics_phase_groups(full_health_output)
    ]
    print(
        "MICRONUX:M9:USB-DISCONNECT:CLASSIFICATION "
        "phase=disconnect-and-reconnect result=pass "
        "boundary=healthy reconnect=healthy "
        f"disconnect_progress_hz={disconnect_progress_hz:.3f} "
        f"reconnect_progress_hz={reconnect_progress_hz:.3f} "
        f"arm-to-irq-over20ms={phase_samples[0]['arm_to_irq_over20ms']}->"
        f"{phase_samples[-1]['arm_to_irq_over20ms']} "
        f"fifo-irq-zero={phase_samples[0]['fifo_irq_zero']}->"
        f"{phase_samples[-1]['fifo_irq_zero']} fifo-poll-zero=0"
    )
    print(
        "MICRONUX:M9:USB-RECONNECT:PASS "
        f"disconnected_seconds={disconnect_seconds} linux=retained "
        "shell=responsive framebuffer=stable abi=3 "
        "state=RUNTIME_REVEALED touch=ready-or-unavailable "
        "buffers=3 scanout=one-shot-explicit-rearm "
        "faults=0 dma-errors=0 underruns=0 host-errors=0 guards=ok "
        "frame-ack=enabled clock=auto lp=enabled backlight-gate=enabled "
        "brightness=63 brightness-source=cached-last-acked-write "
        "physical-panel-state=unobserved "
        "machine=pass visual=required"
    )
    return 0, device


def run_snapshot(
    port: str, device: serial.Serial, snapshot_seconds: int
) -> tuple[int, serial.Serial]:
    captured = capture_passive_output(device, snapshot_seconds)
    print(
        "MICRONUX:M9:SNAPSHOT "
        f"state=passive-capture-complete bytes={len(captured.encode('utf-8'))}"
    )
    passive_failure = failure_marker(captured)
    if passive_failure is not None:
        return (
            fail(
                "snapshot-passive",
                f"terminal-failure-{passive_failure.replace(' ', '-')}",
            ),
            device,
        )

    device, recovered = recover_reconnect_prompt(port, device, 45.0)
    captured += recovered
    print(
        "MICRONUX:M9:SNAPSHOT "
        f"state=prompt-recovered bytes={len(recovered.encode('utf-8'))}"
    )
    recovered_failure = failure_marker(recovered)
    if recovered_failure is not None:
        return (
            fail(
                "snapshot-reconnect",
                f"terminal-failure-{recovered_failure.replace(' ', '-')}",
            ),
            device,
        )

    touch_problem = wait_for_touch_terminal(device)
    if touch_problem is not None:
        return fail("snapshot-touch", touch_problem), device

    try:
        snapshot = run_command(
            device,
            'echo -n "MICRONUX:M9:SNAPSHOT boot_id="; '
            "cat /proc/sys/kernel/random/boot_id; "
            'echo -n "MICRONUX:M9:SNAPSHOT uptime="; cat /proc/uptime; '
            f'D={DISPLAY_SYSFS}; B=/sys/class/backlight/micronux-backlight; '
            'echo "MICRONUX:M9:NATIVE-RUNTIME '
            'pattern=$(cat "$D/pattern") boot_ready=$(cat "$D/boot_ready") '
            'native_state=$(cat "$D/native_state") '
            'brightness=$(cat "$B/brightness") '
            'bl_power=$(cat "$B/bl_power") '
            'actual_brightness=$(cat "$B/actual_brightness")"; '
            'cat "$D/ownership"; '
            + diagnostics_phase_shell("SNAPSHOT-A-PREHASH")
            + framebuffer_hash_shell("snapshot-a", "m9_fb_before")
            + diagnostics_phase_shell("SNAPSHOT-A-POSTHASH")
            + 'cat "$D/scanout"; sleep 1; '
            + diagnostics_phase_shell("SNAPSHOT-B-PREHASH")
            + framebuffer_hash_shell("snapshot-b", "m9_fb_after")
            + diagnostics_phase_shell("SNAPSHOT-B-POSTHASH")
            + 'cat "$D/scanout"; cat "$D/touch"; '
            + 'test "$m9_fb_before" = "$m9_fb_after" && '
            'test "$(cat "$D/pattern")" = framebuffer && '
            'test "$(cat "$D/boot_ready")" = 1 && '
            'test "$(cat "$D/native_state")" = RUNTIME_REVEALED && '
            'test "$(cat "$B/brightness")" = 63 && '
            'test "$(cat "$B/bl_power")" = 0 && '
            'test "$(cat "$B/actual_brightness")" = 63',
            "SNAPSHOT",
            30.0,
        )
    except (serial.SerialException, OSError, TimeoutError) as error:
        return fail("serial", str(error).replace(" ", "-")), device
    if snapshot.return_code != 0:
        return fail("snapshot", f"rc-{snapshot.return_code}"), device
    snapshot_phases = (
        "SNAPSHOT-A-PREHASH",
        "SNAPSHOT-A-POSTHASH",
        "SNAPSHOT-B-PREHASH",
        "SNAPSHOT-B-POSTHASH",
    )
    phase_problem = diagnostics_phase_problem(snapshot.output, snapshot_phases)
    if phase_problem is not None:
        return fail("snapshot-telemetry", phase_problem), device
    framebuffer_problem = framebuffer_hash_problem(
        snapshot.output, ("snapshot-a", "snapshot-b")
    )
    if framebuffer_problem is not None:
        return fail("snapshot-framebuffer", framebuffer_problem), device
    if OWNERSHIP_LINE_RE.search(snapshot.output) is None:
        return fail("snapshot", "native-ownership-missing"), device
    if TOUCH_LINE_RE.search(snapshot.output) is None:
        return fail("snapshot", "touch-state-invalid"), device
    status_problem = runtime_status_problem(snapshot.output)
    if status_problem is not None:
        return fail("snapshot", status_problem), device
    health_problem = display_health_problem(
        snapshot.output, minimum=2, require_progress=True
    )
    if health_problem is not None:
        return fail("snapshot-health", health_problem), device
    phase_samples = [
        samples[0]
        for _, samples in diagnostics_phase_groups(snapshot.output)
    ]
    print(
        "MICRONUX:M9:SNAPSHOT:CAPTURED "
        "reset=not-requested source=framebuffer abi=3 "
        "state=RUNTIME_REVEALED buffers=3 scanout=one-shot-explicit-rearm "
        "framebuffer=stable faults=0 dma-errors=0 underruns=0 "
        "host-errors=0 guards=ok frame-ack=enabled clock=auto lp=enabled "
        "brightness=63 brightness-source=cached-last-acked-write "
        f"arm-to-irq-over20ms={phase_samples[0]['arm_to_irq_over20ms']}->"
        f"{phase_samples[-1]['arm_to_irq_over20ms']} "
        f"fifo-irq-zero={phase_samples[0]['fifo_irq_zero']}->"
        f"{phase_samples[-1]['fifo_irq_zero']} fifo-poll-zero=0 "
        "physical-panel-state=unobserved "
        "evidence=machine-state visual=required"
    )
    return 0, device


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM14")
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "out" / "m9",
    )
    parser.add_argument(
        "--mode",
        choices=(
            "preflight",
            "touch",
            "soak",
            "stress",
            "disconnect",
            "snapshot",
        ),
        required=True,
    )
    parser.add_argument("--point-timeout-ms", type=int, default=60000)
    parser.add_argument("--soak-seconds", type=int, default=240)
    parser.add_argument("--sample-seconds", type=int, default=15)
    parser.add_argument("--disconnect-seconds", type=int, default=600)
    parser.add_argument("--snapshot-seconds", type=int, default=2)
    parser.add_argument("--stress-cycles", type=int, default=3)
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
    if args.snapshot_seconds < 1 or args.snapshot_seconds > 30:
        parser.error("--snapshot-seconds must be between 1 and 30")
    if args.stress_cycles < 1 or args.stress_cycles > 10:
        parser.error("--stress-cycles must be between 1 and 10")

    device: serial.Serial | None = None
    try:
        if args.mode == "snapshot":
            device = open_serial(
                args.port, 30.0, clear_input=False, write_timeout=1.0
            )
            result, device = run_snapshot(
                args.port, device, args.snapshot_seconds
            )
            return result

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
        elif args.mode == "stress":
            print(
                "MICRONUX:M9.2:STRESS:VISUAL-REQUIRED "
                "expect=graphics-and-status-remain-complete-no-cyan-no-tearing"
            )
        rom_reset(args.port)
        device = open_serial(args.port, 30.0)
        boot_log = wait_for_shell(device, 120.0)
        artifact_dir = args.artifact_dir.resolve()
        payload_problem = verify_boot_payload(boot_log, artifact_dir)
        if payload_problem is not None:
            return fail("boot-payload", payload_problem)
        print(
            "MICRONUX:M9.2:PAYLOAD state=verified "
            f"image={sha256_file(artifact_dir / 'Image')} "
            f"dtb={sha256_file(artifact_dir / 'esp32p4-micronux.dtb')} "
            f"metadata-host={sha256_file(artifact_dir / 'metadata.bin')}"
        )
        contract_problem = boot_contract_problem(boot_log)
        if contract_problem is not None:
            return fail("boot-markers", contract_problem)
        identity_problem = verify_runtime_identity(device)
        if identity_problem is not None:
            return fail("runtime-identity", identity_problem)
        touch_problem = wait_for_touch_terminal(device)
        if touch_problem is not None:
            return fail("touch-terminal", touch_problem)
        if args.mode == "preflight":
            return run_preflight(device, boot_log)
        if args.mode == "touch":
            return run_touch(device, args.point_timeout_ms)
        if args.mode == "disconnect":
            result, device = run_disconnect(
                args.port, device, args.disconnect_seconds
            )
            return result
        if args.mode == "stress":
            return run_stress(device, args.stress_cycles)
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
            close_serial_quietly(device)


if __name__ == "__main__":
    sys.exit(main())
