# MicroNUX

MicroNUX is an experimental 32-bit RISC-V NOMMU Linux platform for the
ESP32-P4. The reference target is the Waveshare ESP32-P4-Module-DEV-KIT with
32 MiB PSRAM, 16 MiB SPI flash, and an ESP32-C6 wireless coprocessor.

The project is exploring how far a deliberately constrained Linux system can
be taken on ESP32-P4 hardware. It is not intended to provide conventional
desktop Linux compatibility or MMU-backed process isolation.

## Current status

Milestones M0 through M6, M7 WP0-WP6, and the M8 device-service ABI v1 slice
are complete on the reference board. The pinned `rv32imac`/`ilp32` NOMMU image
boots a reduced BusyBox bFLT shell under QEMU and on physical ESP32-P4
revision-1.3 hardware. The loader validates Linux and its device tree, installs
a fail-closed early PMP map, and hands off to a single-core machine-mode
kernel with native USB `ttyGS0` and a Linux framebuffer console.

Linux owns the microSD card, the factory-firmware ESP32-C6 over SDIO, and the
Kit C 10.1-inch JD9365 display after handoff. BLE/SoftAP provisioning uses
Espressif Security 2 and stores Wi-Fi credentials in C6 NVS. Normal shell boot
does not wait for Wi-Fi; `micronux-online` performs up to ten association and
DHCP attempts with a five-second inter-attempt delay. Combined SD/C6 testing
passed 20-cycle and 120-cycle soaks, three reset boots, controlled SD writes,
external IPv4/DNS, and router reconnect holdoff through attempt 7. MicroNUX
does not build or flash replacement C6 firmware.

The loader initializes the exact 800x1280, two-lane, 1500-Mbps/lane JD9365
panel, leaves DPI/framebuffer mode selected, blanks the backlight, quiesces its
one-shot transfer, and publishes a bounded, CRC-protected descriptor-ring
contract. Linux validates and claims it as `/dev/fb0`, starts DW-GDMA hardware
reload, confirms the first frame through the routed block-done IRQ, and only
then restores the backlight. Linux owns the 100x80 framebuffer console and
backlight control. The loader displays a built-in MicroNUX boot splash while
Linux starts, then `/init` replaces it with a local status console showing
display, storage, network, and USB-shell readiness. Kernel logs and the
interactive recovery shell remain on native USB `ttyGS0`. A monitored
50-microsecond timer only
checks the bridge underrun latch. Framebuffer writes are paced in 512-byte
bursts to protect PSRAM scanout bandwidth, while userspace `mmap()` and the
unsafe revision-1.3 hardware-pattern transition are denied. DMA permissions
grant only SDMMC and the exact display channel their bounded
buffers/descriptors plus the DSI FIFO page; all other channel access to those
regions is denied.

M7 routes userspace through a zero-on-allocation 8 MiB pool with one contiguous
arena per `mm_struct`. Every U-mode return installs and verifies a per-process
PMP boundary, `access_ok()` enforces the same interval, and bFLT text/data are
split into RX and RW regions at the P4's 128-byte PMP granule. Admitted native
or IgniteVM jobs run as locked UID/GID 1000 with zero capabilities,
`no_new_privs`, a seccomp allowlist, restricted devices, and fixed process,
descriptor, memory, time, and output budgets. Physical fault tests cover
privilege, read/write/execute, cross-process access, arena reuse, failed exec,
malformed pointers, and W^X.

The accepted M7 image is 6,025,008 bytes, leaving 266,448 bytes in the fixed
6 MiB partition. Its three-boot gate returned identical arena accounting and
`MemFree` on every boot while repeating SD, networking,
display, fault, W^X, and supervisor workloads. SMP was compile-evaluated and
is deliberately deferred because the two-hart image exceeds the partition and
the current per-hart PMP contract is not safe for process migration. The Linux
patch stack is review-separated into 6 platform, 16 peripheral, and 10
MicroNUX isolation patches.

Linux also exposes the local, versioned `micronux-deviced` ABI to shell tools,
native C, and the actual IgniteVM C runtime. Direct-compiled Ignite bytecode
runs unprivileged, receives only its declared service permissions, and cannot
claim MMIO, DMA, or kernel ownership directly.

## Design baseline

- ESP32-P4 revision 1.3/ECO2 reference silicon
- RV32 NOMMU Linux, initially on one HP core
- ESP-IDF v6.0.1 second-stage loader
- Linux image loaded into external PSRAM
- BusyBox with uClibc-ng userspace
- Native USB Serial/JTAG as the first hardware output path
- ESP32-C6 networking isolated as a coprocessor-backed SDIO profile
- Linux as the sole post-handoff device controller, with shell, IgniteVM, and
  native C applications sharing versioned Linux device/service APIs

NOMMU constraints are part of the platform contract: no demand paging, no
copy-on-write `fork()`, limited process isolation, and a tightly controlled
userspace image and workload.

## Documentation

- [Hardware contract](docs/hardware.md)
- [Linux device ownership and application model](docs/device-ownership-and-applications.md)
- [Milestone roadmap](docs/roadmap.md)
- [M1 QEMU build and test](docs/m1-qemu.md)
- [M2 ESP32-P4 loader and handoff](docs/m2-loader.md)
- [M3 ESP32-P4 Linux platform](docs/m3-platform.md)
- [M4 ESP32-P4 hardware shell](docs/m4-shell.md)
- [M5 NOMMU hardening and stress gate](docs/m5-hardening.md)
- [M6 storage and peripheral bring-up](docs/m6-peripherals.md)
- [M7 user/kernel isolation results](docs/m7-user-kernel-isolation.md)
- [M7 Linux-owned Kit C display](docs/m7-linux-display.md)
- [M7 SMP evaluation](docs/m7-smp-evaluation.md)
- [Linux patch organization](docs/linux-patch-organization.md)
- [M8 Linux device service and application ABI](docs/m8-device-services.md)

## Project policy

- Keep builds reproducible and record upstream revisions and configuration.
- Make the first hardware path as small as possible: loader, timer, interrupts,
  native USB console, initramfs, and shell.
- Preserve the factory flash image before the first firmware write.
- Do not burn security eFuses during development.
- Treat ESP32-P4 silicon revisions as distinct platform profiles where required.

## License

Original MicroNUX code and documentation are available under the
[MIT License](LICENSE).

Third-party components and material derived from upstream projects retain
their original licenses. In particular, Linux kernel-derived files must retain
their applicable GPL-2.0-only and Linux syscall-exception notices. The top-level
MIT license does not relicense those files.
