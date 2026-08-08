# MicroNUX

MicroNUX is an experimental 32-bit RISC-V NOMMU Linux platform for the
ESP32-P4. The reference target is the Waveshare ESP32-P4-Module-DEV-KIT with
32 MiB PSRAM, 16 MiB SPI flash, and an ESP32-C6 wireless coprocessor.

The project is exploring how far a deliberately constrained Linux system can
be taken on ESP32-P4 hardware. It is not intended to provide conventional
desktop Linux compatibility or MMU-backed process isolation.

## Current status

Milestones M0 through M4 are complete. The pinned `rv32imac`/`ilp32` NOMMU
image boots a reduced BusyBox bFLT shell with 32 MiB RAM under QEMU. On the
physical ESP32-P4 revision 1.3, the M3 loader validates and loads Linux and its
device tree from flash, installs an exact PMP memory window, and hands off to a
single-core machine-mode kernel. Linux initializes the revision-correct CLIC
trap path and 360 MHz CLINT timer, transitions from polling early output to the
interrupt-driven native USB `ttyGS0` console, mounts its initramfs pseudo
filesystems, and starts an interactive BusyBox shell. The M4 artifacts passed
three independent ROM-reset boots with identical hashes, 20,544 KiB free, and
working `cat`, `free`, and `uname` commands. M5 NOMMU hardening is next.

## Design baseline

- ESP32-P4 revision 1.3/ECO2 reference silicon
- RV32 NOMMU Linux, initially on one HP core
- ESP-IDF v6.0.1 second-stage loader
- Linux image loaded into external PSRAM
- BusyBox with uClibc-ng userspace
- Native USB Serial/JTAG as the first hardware output path
- ESP32-C6 networking treated as a later coprocessor-backed service

NOMMU constraints are part of the platform contract: no demand paging, no
copy-on-write `fork()`, limited process isolation, and a tightly controlled
userspace image and workload.

## Documentation

- [Hardware contract](docs/hardware.md)
- [Milestone roadmap](docs/roadmap.md)
- [M1 QEMU build and test](docs/m1-qemu.md)
- [M2 ESP32-P4 loader and handoff](docs/m2-loader.md)
- [M3 ESP32-P4 Linux platform](docs/m3-platform.md)
- [M4 ESP32-P4 hardware shell](docs/m4-shell.md)

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
