# MicroNUX Milestone Roadmap

## M0 - Reference hardware contract

Status: **complete**

- Freeze the reference board and silicon revision.
- Verify flash, PSRAM, clock, console, and wireless-coprocessor link.
- Pin ESP-IDF and host-tool versions.
- Establish the factory-flash backup and recovery policy.

Exit artifact: [hardware contract](hardware.md).

## M1 - Reproducible NOMMU Linux under QEMU

Status: **complete**

- Pin Linux, Buildroot, BusyBox, uClibc-ng, and QEMU revisions.
- Define the RV32 ISA and ABI contract.
- Produce a deterministic kernel and initramfs build.
- Boot to an interactive BusyBox shell under QEMU.
- Exercise NOMMU-safe process creation and basic filesystem operations.

Exit criterion: one documented command builds and boots the shell from a clean
checkout.

## M2 - ESP-IDF loader and kernel handoff

Status: **complete**

- Initialize and test external PSRAM with ESP-IDF v6.0.1.
- Load and validate the Linux image without requiring Linux flash drivers.
- Define a versioned handoff block for memory, clocks, console, silicon revision,
  device tree, and reserved regions.
- Quiesce or explicitly transfer every loader-owned peripheral.
- Jump to the kernel with a documented RISC-V register contract.

Exit criterion: the loader reliably reaches a diagnostic kernel entry point in
PSRAM and reports the same handoff data on every cold boot.

Exit artifact: [M2 loader and handoff report](m2-loader.md). The automated gate
uses an EN hard reset, which restarts at the P4 ROM and reinitializes the IDF
boot path; it does not remove power from the carrier.

## M3 - Minimal ESP32-P4 Linux platform

Status: **complete**

- Add early output through the board's native USB Serial/JTAG port.
- Implement revision-correct traps and CLIC interrupt handling.
- Add the system timer and monotonic clocksource.
- Add reset control and the minimal device tree.
- Reserve loader and communication memory correctly.

Exit criterion: the kernel boots far enough to mount its initramfs without
unhandled traps or timer stalls.

Exit artifact: [M3 platform and hardware acceptance report](m3-platform.md).
The final image passed three ROM-reset boots with identical kernel and DTB
hashes and reached `/init` in approximately 0.281 seconds on every boot.

## M4 - First hardware shell

Status: **complete**

- Boot single-core Linux from the ESP-IDF loader.
- Mount the read-only initramfs.
- Start an interactive BusyBox shell on the native USB Serial/JTAG console.
- Record cold-boot time, free memory, and kernel/initramfs sizes.

Exit criterion: repeatable hardware boots reach a usable shell without manual
intervention after reset.

Exit artifact: [M4 hardware shell and acceptance report](m4-shell.md). The
final image passed three ROM-reset boots, reached the shell in 2.52-2.53
seconds, reported 20,544 KiB free, and completed `cat`, `free`, and `uname`
child commands over the native USB console.

## M5 - NOMMU hardening

Status: **complete**

- Audit programs for `vfork()`/`execve()` and NOMMU-safe allocation behavior.
- Add memory-pressure, repeated-exec, timer, and console stress tests.
- Detect stack exhaustion, memory corruption, and loader-region overlap.
- Document supported and unsupported Unix behavior.

Exit criterion: the baseline test suite survives repeated cold boots and an
extended stress run within a fixed memory budget.

Exit artifact: [M5 NOMMU hardening and hardware acceptance report](m5-hardening.md).
The final image passed three ROM-reset boots with stable payload hashes. Each
boot completed the fixed stress contract in 687 ms and retained 20,372 KiB
free from a 20,400 KiB baseline.

## M6 - Storage, networking, and peripherals

Status: **storage/network complete; loader display proof complete; Linux display pending**

- Add storage only after its pin mux and DMA behavior are frozen.
- Integrate ESP32-C6 networking through a narrow, documented transport.
- Evaluate Ethernet, USB, microSD, display, and other board peripherals
  independently.
- Keep optional drivers out of the minimal boot configuration.

Exit criterion: selected services work without destabilizing the minimal shell
or violating reserved-memory boundaries.

Current artifact: [M6 storage and peripheral bring-up](m6-peripherals.md). The
onboard microSD and factory ESP32-C6 now run simultaneously under one
Linux-owned DesignWare controller with serialized dual-slot arbitration. The
clean combined candidate passed 20-cycle and 120-cycle concurrent storage and
network soaks plus three independent ROM-reset boots, retaining the same raw
SD sample hash throughout. The ESP32-C6 factory firmware exposes a stable
ESP-Hosted SDIO/RPC link and `ethsta0`;
the optional P4-hosted provisioning loader has been flashed and physically
validated through its stored-credential/Linux-handoff path. BLE and SoftAP
onboarding use mandatory Security 2 and keep Wi-Fi credentials in C6 NVS.
`micronux-netctl up` now requests association with those saved credentials,
and `micronux-netctl forget` provides an explicit C6-NVS reset path. BLE
Security 2 phone provisioning, C6-NVS persistence, the automatic P4 restart,
saved-credential association, DHCP, default routing, external IPv4, and DNS
all passed on hardware. `micronux-netctl status` and `wait` now expose the
factory C6's true association state, and the explicit `micronux-online` command
retries association and DHCP up to ten times with a five-second inter-attempt
cooldown without making shell boot wait on Wi-Fi. The combined gates
reproduced the router's reconnect holdoff and recovered as late as attempt 7.
MIPI-D0 has four compiled exact-controller color-bar profiles behind a
default-off power gate. Kit C was identified as the 10.1-inch JD9365 panel;
the exact profile read ID `93 65 04` and produced visible vertical bars at
800x1280 over two 1500-Mbps lanes. Scanout is still loader-owned. Persistent
Linux scanout, backlight ownership, and a Linux console remain separate gates.

## M7 - Isolation, SMP, and upstream evaluation

Status: **in progress; WP4 per-process CPU containment proven**

- Use PMP to protect critical kernel, loader, and coprocessor regions where
  practical.
- Measure whether a second HP core provides a net benefit under NOMMU limits.
- Split experimental board code from patches suitable for upstream submission.
- Publish reproducible results, limitations, and maintenance expectations.

Exit criterion: decide, from measurements, which isolation, SMP, and upstream
paths MicroNUX will support.

Current artifact: [M7 user/kernel isolation results](m7-user-kernel-isolation.md).
The version-pinned ESP-IDF v6.0.1 early-PMP patch and loader audit passed three
independent hardware resets on revision 1.3. A separate M7 Linux profile now
reserves an 8 MiB user pool and gives each `mm_struct` a contiguous,
zero-on-allocation arena with no fallback to the kernel allocator. Linux now
replaces the unlocked loader handoff before every U-mode return with a
read-back-verified PMP boundary around the current arena, and NOMMU
`access_ok()` enforces the same bounds. Three reset boots passed 16
privilege/read/write/execute fault cases, cross-process address probes,
malformed syscall-pointer checks, arena reuse and failed-exec recovery, M5
selftests, and repeated teardown with stable accounting. This proves
per-process CPU containment. Job policy, DMA isolation, and W^X remain open.

## M8 - Linux device services and applications

Status: **ABI v1 representative workflow complete**

- Make Linux the sole persistent owner of every peripheral after loader
  handoff.
- Define one versioned device/service API shared by shell commands, IgniteVM,
  and native C applications.
- Add nonblocking file-descriptor and event-wait behavior for long-running
  device operations.
- Package bounded IgniteVM device bindings with explicit capabilities.
- Provide a NOMMU native C SDK that links applications to the userspace ABI,
  not to kernel internals.
- Reject raw MMIO, kernel hooks, unrestricted device mappings, and alternate
  post-handoff hardware runtimes.

Exit criterion: one representative storage, networking, display, or GPIO
workflow runs through a shell command, an Ignite package, and a native C
program using the same permission checks and Linux-owned device path while
unrelated tasks remain schedulable.

Architecture contract: [Linux device ownership and application model](device-ownership-and-applications.md).

Current artifact: [M8 Linux device service and application ABI](m8-device-services.md).
The physical gate completed the representative network/device-status workflow
through `micronux-device`, a `libmicronux` native C program, and direct-compiled
Ignite bytecode running in the real userspace C VM. The unprivileged VM saw
only `observe`, a slow wait did not stall other clients, raw memory devices
were absent, an intentional VM fault left the service responsive, a killed
service restarted, and the subsequent microSD/C6 combined regression passed.
