# MicroNUX

MicroNUX is an experimental 32-bit RISC-V NOMMU Linux platform for the
ESP32-P4. The reference target is the Waveshare ESP32-P4-Module-DEV-KIT with
32 MiB PSRAM, 16 MiB SPI flash, and an ESP32-C6 wireless coprocessor.

The project is exploring how far a deliberately constrained Linux system can
be taken on ESP32-P4 hardware. It is not intended to provide conventional
desktop Linux compatibility or MMU-backed process isolation.

## Current status

Milestones M0 through M5 are complete. The pinned `rv32imac`/`ilp32` NOMMU
image boots a reduced BusyBox bFLT shell with 32 MiB RAM under QEMU. On the
physical ESP32-P4 revision 1.3, the M3 loader validates and loads Linux and its
device tree from flash, installs an exact PMP memory window, and hands off to a
single-core machine-mode kernel. Linux initializes the revision-correct CLIC
trap path and 360 MHz CLINT timer, transitions from polling early output to the
native USB `ttyGS0` console, mounts its initramfs pseudo
filesystems, and starts an interactive BusyBox shell. The M5 hardening image
passed three independent ROM-reset boots with identical hashes. Each boot
completed 64 `posix_spawn()`/exec/wait lifecycles, signal and timer return
tests, a checked 4 MiB allocation, explicit stack canaries, and a 64-record
console integrity burst while retaining 20,372 KiB free. The M6 microSD slice
passed three ROM-reset read-only boots and an opt-in
write/remount/verify/delete gate using a synchronous-polled IDMAC path with
internal-SRAM bounce memory. ESP32-C6 networking is also live through the
factory ESP-Hosted-MCU firmware: three boots produced stable SDIO function
identities, C6 firmware `2.11.5`, MAC `b0:a6:04:8a:d3:78`, and an
`UP,LOWER_UP` Linux interface. An optional P4-only loader profile now compiles
Espressif phone provisioning over BLE with SoftAP fallback, mandatory Security
2, and C6-resident Wi-Fi credentials. Physical phone onboarding passed: the
loader accepted credentials over BLE, stored them through the C6 boundary,
restarted the P4, and handed the provisioned C6 to Linux on the next boot.
Linux then obtained a DHCP lease and default route and reached both `1.1.1.1`
and `example.com`. `micronux-netctl status` and `wait` now query the C6's
actual station association, while `micronux-online` provides a bounded
ten-attempt association-and-DHCP recovery path with a five-second cooldown
between attempts, without delaying the normal
shell boot. That complete online gate passed across three ROM resets, including
recovery from C6 `NO_AP_FOUND` and `CONNECTION_FAIL` events. No C6 firmware is
built or flashed. The ten-attempt policy also passed a fresh three-reset gate:
one boot connected immediately and two recovered on attempt 3 after the
router rejected earlier reconnects. Four exact MIPI-DSI
controller profiles compile behind a default-off power-safety gate; physical
color-bar verification awaits the attached panel's controller label. On
revision 1.3, USB status is serviced once per kernel tick and interrupted
userspace returns through a two-stage
CLIC `mret` sequence.

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
