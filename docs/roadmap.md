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

Status: **complete; Linux-owned display closure landed in the M7 profile**

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
800x1280 over two 1500-Mbps lanes. The loader-owned result remains the M6
electrical proof. The historically accepted M7 profile transferred a
single-buffer circular handoff, I2C/backlight state, and framebuffer ownership
to Linux. Current M9.2 source replaces adopt-live with an ABI-v3 dark/quiescent
contract. Linux initializes the display stack, owns three protected buffers,
and rearms one complete frame transactionally from each completion interrupt.
The patch-36 diagnostic candidate passed exact flash/readback and then failed
closed at the GDMA enable-register readback while Linux continued headless.
Patch 37 corrected that documented-field comparison and passed exact
flash/readback under `build/m9-readback/20260812T120632185Z-2993c451`. Its
sealed passive snapshot
`out/m9/hardware-runs/20260812T120919Z-snapshot-b5890811aa40-0611024a.log`
(SHA-256
`52ebc5ed26487e68419411f73cd263566e3fb8a47920e1b67f33f150d21d94b6`)
reached GDMA `CONFIGURED`, then the dark `SCANOUT_INITIALIZING` policy rejected
the correctly released I2C state and safely published `FAILED_QUIESCENT` with a
headless shell. Patch 38 corrected that narrow policy defect and then passed
exact flash/readback, but its diagnostic snapshot exposed a false post-enable
head-LLP equality after GDMA had already fetched the descriptor. Patch 39 moved
that validation before CHEN and physically reached four clean frames, then
failed only the inactive optional video-shadow equality. Patch 40 removes that
invalid acceptance predicate. Its series/model, strict object/style checks,
independent audit, fresh M7/M9 builds, no-flash, and artifact gates pass. Patch
40 is unflashed, so runtime and physical acceptance remain open. Every recorded
hardware run still reports the optical state as unobserved.

Later physical evidence supersedes that interim status. Patch 41 reached
`RUNTIME_REVEALED` with the correct framebuffer hash, zero reported display
faults, and a 17,298-ns maximum fast-rearm interval, yet the panel eventually
went black while its backlight remained enabled. Patches 42 and 43 establish a
visible telemetry epoch and preserve fault evidence. Patch 44 derates only the
ABI-v3 native path to 60-MHz DPI and 1000 Mbps/lane and enables the compatible
200-MHz-PSRAM/XIP plus 256-KiB-L2/64-byte-cache profile. The 7/44/10 series,
1126-case model, clean M7/M9 builds, no-flash verifier, and artifact audit pass.
Patch 44 is unflashed; exact readback and long-run optical acceptance remain
open.

## M7 - Isolation, SMP, and upstream evaluation

Status: **complete; WP0-WP6 and post-WP6 evaluations proven**

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
selftests, and repeated teardown with stable accounting. A root-owned
supervisor now launches admitted jobs as locked UID/GID 1000 with zero
capabilities, `no_new_privs`, a seccomp allowlist, peripheral restrictions,
and measured process, descriptor, memory, time, and output limits. Three more
reset boots proved non-yielding/output-flood termination, shell and device
service liveness, and exact pool/general-memory recovery. WP6 adds a
128-byte-granule RX/RW bFLT split, fixed read-only signal trampoline,
kernel-to-user write checks, physical W^X fault tests, and fail-closed DMA
permissions for the active SDMMC and display channels. The exact Kit C display
is now Linux-owned as `/dev/fb0`, a 100x80 framebuffer console, sysfs pattern
control, live scanout/diagnostic state, and a standard backlight device;
framebuffer `mmap()` is denied. The display DMA boundary covers only its
framebuffer reads, descriptor read/write access, and DSI FIFO writes. Three
reset boots passed the complete SD/C6/display/isolation workload with identical
arena accounting and `MemFree`.

SMP is explicitly deferred: the compile-only two-hart image exceeds the fixed
partition and disables the per-hart isolation contract. At M7 acceptance the
  Linux changes were review-separated into 6 platform, 17 peripheral, and 10
  isolation patches. The current tree has 7 platform, 40 peripheral, and 10
  isolation patches, 57 total; both layouts are categorized for review rather
than claimed upstream-ready. See the
[SMP evaluation](m7-smp-evaluation.md), [patch organization](linux-patch-organization.md),
and [Linux-owned display report](m7-linux-display.md).

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

## M9 - Native display stabilization and optional GUI

Status: **M9.2 native display ownership IMPLEMENTING / TESTING; GUI work blocked on it**

M9.1 established the Linux framebuffer-console, backlight, and GT9271 input
foundation, but every bounded ABI-v2 adopt-live correction through patch 29
failed on the physical panel. M9.2 now uses ABI v3: the loader publishes a
dark, reset, clock-off relinquish contract, and Linux performs the complete
I2C/LDO, JD9365, D-PHY, DSI host, bridge, GDMA, interrupt, framebuffer, touch,
and backlight lifecycle. Patches 30 through 34 implement cold probe through
safe reveal, patch 35 validates the handoff's CLIC hardware IRQ through
Linux's virtual IRQ mapping, patch 36 records the complete GDMA enable
readback, patch 37 compares all documented ESP32-P4 rev-1.3 fields while
ignoring only undefined positions at setup, runtime health, and teardown,
patch 38 admits the physically released I2C state during dark scanout
initialization while adding decision-point diagnostics, and patch 39 validates
the DMA-arm transaction before transferring descriptor/LLP ownership to GDMA.
Linux owns three protected RGB565 buffers and rearms one marked-last
descriptor per completed frame.

The patch-36 image passed exact flash/readback. Its sealed passive snapshot
recorded `stena0=sigena0=07ffffe6`, both channel-1 enable words and both common
enable words as `ffffffff`, and `chen=00000000`; the driver then published
`FAILED_UNVERIFIED stage=gdma-irq error=-5`, contained the display path, and
continued with the USB shell in headless mode. Patch 37 then passed exact
flash/readback at `build/m9-readback/20260812T120632185Z-2993c451`. Its sealed
snapshot
`out/m9/hardware-runs/20260812T120919Z-snapshot-b5890811aa40-0611024a.log`
(SHA-256
`52ebc5ed26487e68419411f73cd263566e3fb8a47920e1b67f33f150d21d94b6`)
logged GDMA `CONFIGURED`, then exposed the released-I2C policy bug at
`SCANOUT_INITIALIZING`, reached `FAILED_QUIESCENT`, and continued headless. It
records `optical-state=unobserved`, so neither run is optical evidence.

Patch 38 has exact patch SHA-256
`8471fcb6b9ec9656b82dc44c29a790c9f70a1d306a16d4b8b34fe7f04f96f177`,
commit `397b8bed56b6165251011f0109ded884d1bd0fe2`, applied-source SHA-256
`3aa68ea94d60385476fd3b5817f493cfeaeb38d915c4ee643bdda54d5273f65e`,
and W=1 object SHA-256
`55736ed8b0df502b9dc1c31440a596f7b5c81797bc4020b8d373cffa14f3f666`.
It passed exact flash/readback under
`build/m9-readback/20260812T125425532Z-95d60702`; `readback.json` has SHA-256
`702ba99d9ca84bf25593301c300575a9706294dcae62f8db2ef982336e337a46`.
The sealed snapshot
`out/m9/hardware-runs/20260812T125714Z-snapshot-b5890811aa40-3670b4ef.log`
has SHA-256
`1c43ac04f307de252342fdc4cdc49d3aa6f18520be5cca254d4e7df294b677ac`.
It reached GDMA `CONFIGURED`, then failed closed at
`arm-readback/descriptor-channel-readback` with `cfg=3 chen=1
cfglo=0000000f cfghi=0a020001 llp=1 sar=48031500 ctrlhi=c0108840` and no
captured fault bits. GDMA had already fetched the head (`SAR=front+0x500`) and
advanced live LLP to the terminal descriptor's `0 | memory-port` value `1`;
the post-enable head-LLP equality was a false rejection. Containment reached
`FAILED_QUIESCENT`, preserved headless Linux, and did not observe the panel.

Patch 39 moves exact descriptor/configuration/head-LLP validation before CHEN
and retains only channel-active plus error/status checks after enable. Its exact
patch SHA-256, commit, post-source SHA-256, and object SHA-256 are
`d8c774f42019dddeba6020ec019b4fd61474dec3c2913c33b5c2ad191deabe94`,
`99fcff145f84b22c8996e7cab8ce9a77abe472e7`,
`68378f2ca532ec36020a3a99a57c2b8a909b694cb11d613764427acd76c7de0c`,
and `b86cbf98fe2e2d2b894928049d9110cafedb290fbd1b2257285e06143997331a`.
Strict checkpatch 0/0/0, fuzz-zero application, W=1 `-Werror`, and the
independent semantic audit are clear. The historical 7/39/10 series had 56 patches with
manifest
`0f993160af2ce5bb03d791a3708065972764111a7da05a8b5272a03306a9db74`,
and the frozen shared model passed 920 cases. The clean M7 ABI-v2 regression passed
source contract
`112e8a0afef2f14151a8fcc9e5f8c054e671370d47b8ae042d87a15e52b00ca4`
and its 14-file bFLT W^X audit; retained log
`out/build-logs/m7-patch39-final-rerun-20260812T063011.stdout.log` has SHA-256
`4ccc716b3ca1d9c9de013eb59e852fd0d876d58cd4f222792fce89610242d382`.
The fresh M9 full build passes source contract
`e9c1e789fe87a5a7735c62785ff0113bca61c9d924e964c0a39ce30d69ab5fba`;
retained stdout
`build/patch39-m9-build/m9-full-final-20260812T062855.stdout.log` has SHA-256
`66767316f4a0d528ca1ac036a2066b9ce0569ff597c542b035b91e2538ee6678`.
The no-flash verifier passes with the 246,640-byte loader at
`e314b558d923e8fa0175f9d4eb692ce5728eac3175053ca47c53775a0dbf9104`,
reports `Nothing was flashed`, and retained stdout
`build/patch39-m9-build/m9-noflash-final-20260812T063226.stdout.log` has SHA-256
`773a31cdc8ae7223f3bea628a1cbd7f23e1d9e1be0dea62037e2dd6138010586`.
The seven-artifact audit passed. Patch 39 subsequently passed exact
flash/readback under `build/m9-readback/20260812T134841478Z-1a4e56fe`;
`readback.json` has SHA-256
`691fd9ac949de313a74c5591870bd3a95769baeae7f8c7be7d22dd429be61c09`.
Its sealed snapshot
`out/m9/hardware-runs/20260812T135132Z-snapshot-b5890811aa40-2b712727.log`
has SHA-256
`9f4cb506b955c9e0b2fbfbbaf3927271de3eef40df7b0e1b0a67a3991d3228ac`.
Linux completed four frames (`generation=4 frames=4 rearm=5/0 guards=1`) with
no host, bridge, GDMA, DMA, or software faults. Exact programmed
`VID_MODE_CFG=0000ff02` passed; the disabled optional shadow bank remained
`active=00000000`, and that equality alone made policy false. Containment
published `FAILED_QUIESCENT`; the log records `optical-state=unobserved`.

Patch 40 removes exactly the three-line active-shadow composite and two-line
runtime equality (zero additions, five deletions). It retains active-register
and component diagnostics, exact `VID_MODE_CFG=0000ff02`, and the other 40
policy predicates; it changes no write, teardown, reveal, or ABI-v2 behavior.
Its patch SHA-256, commit, post-source SHA-256, and W=1 object SHA-256 are
`04df043b79760379cc8f4d0d74847d892e3895bc1b55eba3b90af23695647ed1`,
`0fac40ee31c1af165ea94a82a7b0d905d8da4861`,
`13088b8fefe6cf8ca415615e2cf8e9e62a6f3d972d2f010c49965139924a5164`,
and `845e536b031622f29cc8d1e157c1e284e453f8fde8d053a2dba20182368dcdd1`.
The independent audit is clear. The current 7/40/10 checker passes all 57
patches with manifest
`f9318e1a6e7480f1105ec5a435ad71d2754bb6d91a79bcc471747249128ed983`,
and the model passes 963 cases. The clean isolated Patch-40 M7 ABI-v2 regression
passes source contract
`6b1d5d730c7370f364fed80c507f8ba8454c52b9ee247a22cde96a54233d8206`.
Retained stdout
`out/m7/build-logs/20260812T072708Z-patch40-clean.stdout.log` has SHA-256
`9bddb74e3734cd977bd3b98a28cab29e75a626e6483219ccf88cca418fc47c05`.
Its driver source/object gates match the Patch-40 hashes above, and its 14-file,
128-byte-granule bFLT W^X audit passes. Exact Image, DTB, metadata, and rootfs
SHA-256 values are
`ca34bd104c670427bc7567991056eb9f423cd875290bd238050b384c71454f27`,
`42e3ac2fadcbeda59a13ee3cfd4afb490607e13185b7db48b2c3ee1fd00a4fe6`,
`a1f0c6047b453846831eca90ab6d6675a3933e369fa479d138c4331b97741728`,
and `1721822da26deba72d2952af1d5bade5c9d8b7eeb8d6d6c4616a83c2ff8ba3ae`.
Metadata CRC32 is `901bf054`, and the payload ends at `0x48a0cd08`. This was
build-only: no COM access, reset, or flash occurred, and it is not new physical
acceptance. The fresh M9 build passes source contract
`4f21d0c9e47cc0c003ccbfe9db8c558e18fcf1cf82cceb1e50319661d6ce2686`;
full-build and no-flash stdout hashes are
`0c15ef3f2c0e40f531b77a12cdf1ec5731272005b2488d96789348bd2319cb6e`
and `4b64a06e8d65391d5fab3182cdf864fb815a455fe06e6be91c0cf9724f7f3e6e`.
The no-flash verifier reports `Nothing was flashed`; the seven-artifact audit
passes. Patch 40 has not been flashed, so runtime and visual acceptance remain
pending.

The runtime handshake is
`PROBED_QUIESCENT` -> `cold_init` ->
`SCANOUT_QUALIFIED_QUIESCENT` -> tty status render -> `boot_ready` ->
`RUNTIME_REVEALED`. Native ABI v3 intentionally has no VPG interface. Current
source/static/build gates and exact flash/readbacks do not prove external panel
output: Patch-40 machine testing, soak, touch, and the
no-black/no-cyan visual gate remain open.
Acceptance runs fresh `disconnect`, `preflight`, `stress`,
`touch`, and `soak` modes, in that order, without a VPG transition. LVGL, the
optional window/session manager, UI-v1, and Ignite GUI bindings begin only
after that physical gate.

Current artifacts:

- [M9.2 Native Linux Display Ownership and Scanout Stabilization Plan](m9-2-native-linux-display-ownership-plan.md)
- [M9 Optional LVGL Window and Session Manager Plan](m9-lvgl-window-manager-plan.md)
