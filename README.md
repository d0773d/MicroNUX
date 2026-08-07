# MicroNUX

MicroNUX is an experimental 32-bit RISC-V NOMMU Linux platform for the
ESP32-P4. The reference target is the Waveshare ESP32-P4-Module-DEV-KIT with
32 MiB PSRAM, 16 MiB SPI flash, and an ESP32-C6 wireless coprocessor.

The project is exploring how far a deliberately constrained Linux system can
be taken on ESP32-P4 hardware. It is not intended to provide conventional
desktop Linux compatibility or MMU-backed process isolation.

## Current status

Milestones M0 through M2 are complete. The pinned `rv32imac`/`ilp32` NOMMU
image boots a reduced BusyBox bFLT shell with 32 MiB RAM under QEMU. On the
physical ESP32-P4, the M2 loader tests PSRAM, loads that exact image from flash,
verifies its SHA-256, and reaches a PSRAM-resident diagnostic entry point with
a stable handoff over three ROM-reset boots.

Linux has not executed on the ESP32-P4 yet. M3 is the first ESP32-P4 Linux
platform work: early UART, traps/interrupts, timer, reset, and device tree.

## Design baseline

- ESP32-P4 revision 1.3/ECO2 reference silicon
- RV32 NOMMU Linux, initially on one HP core
- ESP-IDF v6.0.1 second-stage loader
- Linux image loaded into external PSRAM
- BusyBox with uClibc-ng userspace
- UART0 as the first boot and shell console
- ESP32-C6 networking treated as a later coprocessor-backed service

NOMMU constraints are part of the platform contract: no demand paging, no
copy-on-write `fork()`, limited process isolation, and a tightly controlled
userspace image and workload.

## Documentation

- [Hardware contract](docs/hardware.md)
- [Milestone roadmap](docs/roadmap.md)
- [M1 QEMU build and test](docs/m1-qemu.md)
- [M2 ESP32-P4 loader and handoff](docs/m2-loader.md)

## Project policy

- Keep builds reproducible and record upstream revisions and configuration.
- Make the first hardware path as small as possible: loader, timer, interrupts,
  UART, initramfs, and shell.
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
