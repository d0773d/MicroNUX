#!/usr/bin/env python3
"""Boot and verify the MicroNUX M1 RV32 NOMMU image under QEMU."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time


TIMEOUT_BOOT_SECONDS = 45
TIMEOUT_SHELL_SECONDS = 10
MAX_BUSYBOX_MEMORY_SPAN = 256 * 1024


def require_text(path: Path, required: tuple[str, ...]) -> None:
    text = path.read_text(encoding="utf-8")
    missing = [entry for entry in required if entry not in text]
    if missing:
        raise RuntimeError(f"{path} is missing required settings: {missing}")


def main() -> int:
    repo_dir = Path(__file__).resolve().parent.parent
    defconfig = (
        repo_dir
        / "buildroot-external/configs/micronux_qemu_rv32_nommu_defconfig"
    )
    busybox_fragment = (
        repo_dir / "buildroot-external/board/micronux/busybox-m1.config"
    )
    profile_hashes = b"".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode()
        for path in (defconfig, busybox_fragment)
    )
    config_id = hashlib.sha256(profile_hashes).hexdigest()[:12]
    work_dir = Path(
        os.environ.get("MICRONUX_M1_WORKDIR", Path.home() / ".cache/micronux/m1")
    )
    output_dir = work_dir / f"output-2025.02.16-{config_id}"
    image_dir = output_dir / "images"
    qemu = output_dir / "host/bin/qemu-system-riscv32"
    kernel = image_dir / "Image"
    initramfs = image_dir / "rootfs.cpio"
    busybox = output_dir / "target/bin/busybox"
    flthdr = output_dir / "host/bin/riscv32-buildroot-linux-uclibc-flthdr"

    for artifact in (qemu, kernel, initramfs, busybox, flthdr):
        if not artifact.is_file():
            raise RuntimeError(f"missing M1 artifact: {artifact}; run m1-build.sh first")

    buildroot_config = output_dir / ".config"
    require_text(
        buildroot_config,
        (
            "BR2_RISCV_32=y",
            "# BR2_RISCV_USE_MMU is not set",
            "BR2_RISCV_ISA_RVC=y",
            "# BR2_RISCV_ISA_RVF is not set",
            "BR2_RISCV_ABI_ILP32=y",
            "BR2_TOOLCHAIN_BUILDROOT_UCLIBC=y",
            "BR2_REPRODUCIBLE=y",
            "BR2_INIT_NONE=y",
            "BR2_ROOTFS_DEVICE_CREATION_STATIC=y",
            'BR2_ROOTFS_STATIC_DEVICE_TABLE="system/device_table_dev.txt"',
            'BR2_PACKAGE_BUSYBOX_CONFIG_FRAGMENT_FILES=',
            "# BR2_PACKAGE_IFUPDOWN_SCRIPTS is not set",
            "BR2_TARGET_ROOTFS_CPIO=y",
        ),
    )
    buildroot_config_text = buildroot_config.read_text(encoding="utf-8")
    for forbidden_setting in ("BR2_RISCV_ISA_RVF=y", "BR2_RISCV_ISA_RVD=y"):
        if forbidden_setting in buildroot_config_text:
            raise RuntimeError(f"forbidden ISA setting enabled: {forbidden_setting}")
    require_text(
        output_dir / "build/linux-6.12.27/.config",
        ("# CONFIG_MMU is not set", "CONFIG_BINFMT_FLAT=y"),
    )

    file_result = subprocess.run(
        ["file", str(busybox)], check=True, capture_output=True, text=True
    )
    if "BFLT executable" not in file_result.stdout:
        raise RuntimeError(f"BusyBox is not bFLT: {file_result.stdout.strip()}")

    flthdr_result = subprocess.run(
        [str(flthdr), "-p", str(busybox)],
        check=True,
        capture_output=True,
        text=True,
    )
    bss_match = re.search(r"BSS End:\s+0x([0-9a-fA-F]+)", flthdr_result.stdout)
    stack_match = re.search(r"Stack Size:\s+0x([0-9a-fA-F]+)", flthdr_result.stdout)
    if bss_match is None or stack_match is None:
        raise RuntimeError("could not read the BusyBox bFLT memory span")
    busybox_memory_span = int(bss_match.group(1), 16) + int(stack_match.group(1), 16)
    if busybox_memory_span > MAX_BUSYBOX_MEMORY_SPAN:
        raise RuntimeError(
            f"BusyBox bFLT memory span is {busybox_memory_span} bytes; "
            f"limit is {MAX_BUSYBOX_MEMORY_SPAN}"
        )

    command = [
        str(qemu),
        "-M",
        "virt",
        "-m",
        "32M",
        "-smp",
        "1",
        "-bios",
        "none",
        "-kernel",
        str(kernel),
        "-initrd",
        str(initramfs),
        "-append",
        "rdinit=/bin/sh console=ttyS0",
        "-cpu",
        "rv32,mmu=off,f=false,d=false,zfa=false",
        "-monitor",
        "none",
        "-nographic",
    ]

    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
    )
    assert process.stdin is not None
    assert process.stdout is not None

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    transcript = bytearray()

    def read_until(markers: tuple[bytes, ...], timeout: int) -> bytes:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for key, _ in selector.select(timeout=0.2):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    raise RuntimeError("QEMU exited before the expected marker")
                transcript.extend(chunk)
                sys.stdout.write(chunk.decode("utf-8", errors="replace"))
                sys.stdout.flush()
                if any(marker in transcript for marker in markers):
                    return bytes(transcript)
        expected = ", ".join(repr(marker.decode()) for marker in markers)
        raise TimeoutError(f"timed out waiting for {expected}")

    try:
        read_until((b"# ",), TIMEOUT_BOOT_SECONDS)
        process.stdin.write(
            b"echo MICRONUX_M1_SHELL_OK; "
            b"echo MICRONUX_ARCH=$(uname -m); "
            b"/bin/busybox sh -c 'echo MICRONUX_CHILD_1_PID=$$'; "
            b"/bin/busybox sh -c 'echo MICRONUX_CHILD_2_PID=$$'; "
            b"/bin/busybox sh -c 'echo MICRONUX_CHILD_3_PID=$$'; "
            b"printf 'MICRONUX_M1_%s\\n' DONE\n"
        )
        process.stdin.flush()
        result = read_until((b"MICRONUX_M1_DONE",), TIMEOUT_SHELL_SECONDS)

        required_output = (
            b"This architecture does not have kernel memory protection.",
            b"MICRONUX_M1_SHELL_OK",
            b"MICRONUX_ARCH=riscv32",
        )
        missing_output = [marker.decode() for marker in required_output if marker not in result]
        if missing_output:
            raise RuntimeError(f"guest output is missing markers: {missing_output}")
        child_pids = re.findall(rb"MICRONUX_CHILD_[123]_PID=([0-9]+)", result)
        if len(child_pids) != 3 or len(set(child_pids)) != 3:
            raise RuntimeError("three distinct child bFLT shells did not execute")
        forbidden_output = (
            b"This kernel does not support systems with F but not D",
            b"Oops - illegal instruction",
            b"Kernel panic",
            b"page allocation failure",
            b"Unable to allocate RAM",
            b"Allocation of length",
            b"bad number",
            b"can't execute",
            b"applet not found",
        )
        present_forbidden = [
            marker.decode() for marker in forbidden_output if marker in result
        ]
        if present_forbidden:
            raise RuntimeError(f"guest output contains fatal markers: {present_forbidden}")

        process.stdin.write(b"/sbin/poweroff -f\n")
        process.stdin.flush()
        read_until((b"System halted", b"reboot: Power down"), TIMEOUT_SHELL_SECONDS)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    print("MICRONUX_M1_TEST_PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, TimeoutError) as error:
        print(f"MICRONUX_M1_TEST_FAIL: {error}", file=sys.stderr)
        raise SystemExit(1) from error
