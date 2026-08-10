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

- the loader MIPI color-bar path is only an electrical diagnostic; the M7
  profile now transfers persistent scanout and backlight control to Linux;
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
representative workflow. M7 separately closes persistent display ownership:
Linux validates the loader contract, owns circular scanout as `/dev/fb0`,
attaches the framebuffer console, and owns pattern and backlight controls.
Direct framebuffer `mmap()` remains denied, and ordinary jobs remain blocked
from raw display MMIO and DMA.

Display ownership does not yet provide the three application surfaces with a
versioned drawing/control API. A future display service, shell command,
IgniteVM binding, and native C wrapper must repeat the M8 permission,
nonblocking, fault-recovery, and versioning checks rather than inheriting them
from the low-level driver gate.
