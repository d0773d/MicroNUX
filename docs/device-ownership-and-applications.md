# Linux Device Ownership and Application Model

Status: **accepted architecture decision**

Date: **2026-08-08**

## Decision

Linux is the sole device controller for MicroNUX. After the loader transfers
control, the Linux kernel and trusted Linux system services own every device,
including pin muxing, MMIO, interrupts, DMA, clocks, storage, networking,
display, input, USB, GPIO, UART, I2C, SPI, and coprocessor transports.

The loader may perform only the initialization, validation, provisioning, and
electrical setup needed before Linux can boot. It must quiesce or explicitly
transfer every resource before handoff. Loader diagnostics are bring-up tools,
not an application-facing device API and not a second device runtime beside
Linux.

This decision is a project invariant. A milestone may not bypass it for
convenience. Changing it requires a new explicit architecture decision.

Current display implementation status (2026-08-12): ABI-v3 Linux-native cold
initialization remains under physical test. Patch 41 reached Linux-owned
runtime with correct framebuffer data and fast transactional rearm, but the
user later observed a black panel with the backlight enabled, so it was
physically rejected. Patch 44 retains sole Linux ownership and changes only the
native bandwidth/cache profile (60-MHz DPI, 1000 Mbps/lane, 200-MHz PSRAM XIP,
256-KiB L2 with 64-byte lines). Its static, model, clean M7/M9 build, artifact,
and no-flash gates pass; it is unflashed and has no physical acceptance claim.

## One device API, three application surfaces

MicroNUX will expose device behavior once through Linux. Shell tools,
IgniteVM, and native C programs must use the same underlying kernel drivers and
trusted device services rather than implementing separate hardware paths.

```text
hardware
   |
Linux drivers: MMIO, IRQ, DMA, clocks, pin mux, transport
   |
versioned Linux device and service APIs
   |-------------------|-------------------|
shell commands       IgniteVM bindings   native C SDK
```

### Shell commands

Users can inspect and control the board with ordinary commands. A command is a
small userspace client of a Linux device node, socket, filesystem interface, or
trusted MicroNUX service. It is not a privileged shortcut around the driver.

Examples of the intended shape are:

```sh
micronux-net status
micronux-display brightness 60
micronux-gpio read status-led
micronux-device list
```

Commands must support scripting, stable exit codes, machine-readable output
where useful, and redaction of credentials and other secrets.

### IgniteVM

IgniteVM runs as a Linux userspace runtime. Ignite packages call bounded,
typed bindings backed by the same device and service APIs as shell commands.
The VM, bytecode, garbage collector, and packages never access peripheral MMIO,
interrupt controllers, DMA descriptors, or kernel pointers directly.

Device bindings must be asynchronous where operations can take time. Expected
hardware and network failures return typed, recoverable results. Package
metadata must declare required capabilities before a package can use devices
such as networking, storage, display, GPIO, or input.

Ignite remains one native language pipeline:

```text
.ignite source -> direct Ignite compiler -> Ignite bytecode -> userspace C VM
```

### Native C

User C code is compiled as a MicroNUX userspace program. The current executable
contract is NOMMU bFLT with uClibc-ng; a future SDK may provide a stable
`libmicronux` over Linux syscalls, file descriptors, sockets, and versioned
device-service protocols.

"Linking into MicroNUX" means linking against this userspace ABI and SDK.
"Hooking into MicroNUX" means registering through an explicit userspace event,
service, or plugin API. It does not mean patching kernel memory, installing an
arbitrary kernel callback, or linking an untrusted application into the kernel.

New kernel drivers remain trusted operating-system code. They are reviewed,
built into an approved kernel image, and retain the Linux kernel's applicable
GPL-2.0-only licensing requirements. They are not installed by ordinary
applications.

## Required Linux interfaces

MicroNUX should prefer existing Linux interfaces when they fit:

- file descriptors with blocking and nonblocking modes;
- `poll()` or an equivalent event wait primitive;
- sockets for networking;
- narrowly scoped device nodes and IOCTLs;
- read-only status through suitable filesystem interfaces; and
- a versioned local RPC protocol for policy-heavy board services.

A trusted userspace service may coordinate device policy, but it accesses
hardware only through Linux drivers. The service is part of the operating
system boundary, not a way for applications to claim the device.

Long operations must not stall the whole operating system. Drivers should use
interrupts, DMA, wait queues, and state machines where the hardware permits.
Only the requesting task waits; the shell, IgniteVM, and other native programs
remain schedulable.

## Protection boundary

Ordinary applications must not receive:

- `/dev/mem`, `/dev/kmem`, or unrestricted physical mappings;
- raw peripheral or interrupt-controller MMIO;
- unrestricted DMA buffers or descriptors;
- direct pin-mux, clock-tree, or reset-register access;
- arbitrary kernel modules, kernel symbols, callbacks, or function hooks; or
- a private ESP-IDF/Arduino device runtime running beside Linux after handoff.

Shell, IgniteVM, and native C access must converge on the same authorization,
resource limits, error behavior, and auditing. A feature is not complete if it
works through one application surface by bypassing that contract.

## Driver migration rule

Loader-owned bring-up code becomes a Linux driver or trusted Linux service
before it is exposed as a general application feature. In particular:

- the loader MIPI color-bar path is only an electrical diagnostic. The
  historical M7 profile transferred initialized display and backlight state to
  Linux; the current M9.2 source instead relinquishes the display dark and
  quiescent through ABI v3 so Linux initializes and owns I2C/LDO, panel, D-PHY,
  DSI, bridge, GDMA, interrupts, triple buffers, touch, and backlight. Its
  native runtime exposes no VPG switch;
- the provisioning loader may prepare C6 credentials before boot, but Linux
  owns normal SDIO networking, connection state, retry policy, and application
  network access;
- loader I2C setup does not constitute a public I2C API; and
- GPIO, UART, I2C, SPI, USB, input, and future peripherals require Linux
  ownership before shell, IgniteVM, or C exposure.

## Compatibility contract

Application-facing APIs must be versioned independently from internal driver
implementation. Device names, capability identifiers, request structures,
events, error values, and CLI output intended for automation form public
contracts. The kernel, service, command, Ignite binding, and C header must be
updated together when a contract changes.

The initial implementation may be deliberately small. It must still establish
one canonical path rather than multiple incompatible shortcuts.

## Acceptance criteria

This architecture is realized when:

1. Linux owns every persistent peripheral after loader handoff.
2. The same representative device workflow can be completed from a shell
   command, an Ignite package, and a native C application.
3. All three surfaces use the same versioned device/service API and permission
   checks.
4. A slow device operation blocks only its caller while unrelated tasks run.
5. Attempts to access raw MMIO, kernel memory, or unauthorized devices fail
   closed.
6. An application crash or IgniteVM fault cannot leave device ownership outside
   Linux or prevent the driver/service from recovering the resource.

## ABI v1 realization

The M8 hardware gate on 2026-08-09 satisfied these criteria for the first
representative workflow, Linux-owned microSD/C6 device-status observation:

| Criterion | Physical evidence |
| --- | --- |
| Linux ownership | Linux enumerated `mmcblk0` and C6-backed `ethsta0`; the loader attachment probe left MIPI D-PHY off and performed zero target writes. The later M7 display contract transfers initialized Kit C scanout to Linux-owned `fb0` and backlight devices. |
| Three surfaces | Shell, native C, and direct-compiled Ignite bytecode returned the same ABI 1.0 device state. |
| One API and policy | All three used `libmicronux` and `/run/micronux/device-v1.sock`; `SO_PEERCRED` granted the Ignite runner only `observe`. |
| Caller-only blocking | A two-second device wait timed out while an unrelated ABI query completed successfully. |
| Fail closed | UID 65534 was denied `ADMIN_PROBE`; `/dev/mem`, `/dev/kmem`, and loadable modules were absent. |
| Fault recovery | A killed waiting client, an intentional IgniteVM fault, and a killed daemon all left or returned the service to a responsive state. |

This closes the application-boundary acceptance gate for the read-only
representative workflow. M7 separately proved persistent display ownership
with a historical circular handoff and hardware reload. Current M9.2 source
keeps that Linux-only boundary but replaces adopt-live with an ABI-v3 cold
contract. Linux is intended to initialize the complete stack, run
front/back/spare scanout with transactional per-frame IRQ rearm, render the
tty status page while dark, and reveal only after a one-shot `boot_ready`
request reaches `RUNTIME_REVEALED`. Direct framebuffer `mmap()` remains denied,
and ordinary jobs remain blocked from raw display MMIO and DMA. The patch-36
diagnostic artifacts passed exact flash/readback, then the driver rejected the
GDMA enable-register tuple, contained the display path, and kept the USB shell
available in headless mode. Corrective patch 37 subsequently passed exact
flash/readback under `build/m9-readback/20260812T120632185Z-2993c451`. Its
passive snapshot
`out/m9/hardware-runs/20260812T120919Z-snapshot-b5890811aa40-0611024a.log`
(SHA-256
`52ebc5ed26487e68419411f73cd263566e3fb8a47920e1b67f33f150d21d94b6`)
reached GDMA `CONFIGURED`, then deterministically rejected scanout qualification
because `SCANOUT_INITIALIZING` did not admit the physically released I2C state.
Containment reached `FAILED_QUIESCENT` and kept the USB shell headless. The log
records `optical-state=unobserved`, so this is not optical evidence.

Patch 38 corrects only that dark-state policy defect and adds read-only
decision-point diagnostics. Its exact flash/readback is retained under
`build/m9-readback/20260812T125425532Z-95d60702`; `readback.json` has SHA-256
`702ba99d9ca84bf25593301c300575a9706294dcae62f8db2ef982336e337a46`.
The sealed passive snapshot
`out/m9/hardware-runs/20260812T125714Z-snapshot-b5890811aa40-3670b4ef.log`
has SHA-256
`1c43ac04f307de252342fdc4cdc49d3aa6f18520be5cca254d4e7df294b677ac`.
It reached GDMA `CONFIGURED`, then failed closed at
`arm-readback/descriptor-channel-readback` with exact tuple `cfg=3 chen=1
cfglo=0000000f cfghi=0a020001 llp=1 sar=48031500 ctrlhi=c0108840`; all host,
bridge, and GDMA fault status was zero. The one-shot engine had consumed the
head (`SAR=front+0x500`) and advanced live LLP to the terminal descriptor's
`0 | memory-port` value `1`, so the old post-enable head-LLP equality falsely
rejected a successful fetch. Containment reached `FAILED_QUIESCENT`, retained
the headless shell, and recorded `optical-state=unobserved`.

Patch 39 validates software-owned descriptor, configuration, head-LLP, CHEN,
and clean-status state before enable, then checks only channel-active and fault
status after enable. Its exact patch/commit/post-source/object SHA-256 evidence
is `d8c774f42019dddeba6020ec019b4fd61474dec3c2913c33b5c2ad191deabe94`,
`99fcff145f84b22c8996e7cab8ce9a77abe472e7`,
`68378f2ca532ec36020a3a99a57c2b8a909b694cb11d613764427acd76c7de0c`,
and `b86cbf98fe2e2d2b894928049d9110cafedb290fbd1b2257285e06143997331a`.
Strict checkpatch 0/0/0, fuzz-zero application, W=1 `-Werror`, and the
independent semantic audit are clear. The historical 920-case model and 7/39/10
series check passed with frozen 56-patch manifest
`0f993160af2ce5bb03d791a3708065972764111a7da05a8b5272a03306a9db74`.
The clean M7 ABI-v2 regression passes source contract
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
zero host, bridge, GDMA, DMA, or software faults and exact programmed
`VID_MODE_CFG=0000ff02`. Policy failed solely because the disabled optional
shadow mirror remained `active=00000000`; containment published
`FAILED_QUIESCENT` and `optical-state=unobserved`.

Patch 40 removes only that three-line composite and two-line equality (zero
additions, five deletions), retains the active-register/component diagnostics,
and still requires `VID_MODE_CFG=0000ff02` plus the other 40 predicates. It
changes no write, teardown, reveal, or ABI-v2 behavior. Its
patch/commit/post-source/object SHA-256 evidence is
`04df043b79760379cc8f4d0d74847d892e3895bc1b55eba3b90af23695647ed1`,
`0fac40ee31c1af165ea94a82a7b0d905d8da4861`,
`13088b8fefe6cf8ca415615e2cf8e9e62a6f3d972d2f010c49965139924a5164`,
and `845e536b031622f29cc8d1e157c1e284e453f8fde8d053a2dba20182368dcdd1`.
The audit is clear. The 7/40/10 checker passes all 57 patches with manifest
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
The no-flash verifier reports `Nothing was flashed`, and the seven-artifact
audit passes. Patch 40 has not been flashed; runtime and physical acceptance
remain open. See
[M9.2 Native Linux Display Ownership](m9-2-native-linux-display-ownership-plan.md).

Display ownership does not yet provide the three application surfaces with a
versioned drawing/control API. A future display service, shell command,
IgniteVM binding, and native C wrapper must repeat the M8 permission,
nonblocking, fault-recovery, and versioning checks rather than inheriting them
from the low-level driver gate.
