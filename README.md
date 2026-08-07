# MicroNUX

MicroNUX is an experimental 32-bit RISC-V NOMMU Linux platform for the
ESP32-P4. The reference target is the Waveshare ESP32-P4-Module-DEV-KIT with
32 MiB PSRAM, 16 MiB SPI flash, and an ESP32-C6 wireless coprocessor.

The project is exploring how far a deliberately constrained Linux system can
be taken on ESP32-P4 hardware. It is not intended to provide conventional
desktop Linux compatibility or MMU-backed process isolation.

## Current status

Milestone M0 is complete. The reference board, silicon revision, boot console,
memory baseline, loader SDK, and first-write safety policy are frozen.

Next: M1 will produce a reproducible RV32 NOMMU Linux and BusyBox shell under
QEMU before hardware-specific kernel work begins.

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

## Project policy

- Keep builds reproducible and record upstream revisions and configuration.
- Make the first hardware path as small as possible: loader, timer, interrupts,
  UART, initramfs, and shell.
- Preserve the factory flash image before the first firmware write.
- Do not burn security eFuses during development.
- Treat ESP32-P4 silicon revisions as distinct platform profiles where required.

## License

No project license has been selected yet. Until a license is added, normal
copyright restrictions apply even though the repository is public.
