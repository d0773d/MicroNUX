# M4 First ESP32-P4 Hardware Shell

Status: **complete** (hardware-verified on 2026-08-07)

M4 turns the M3 output-only boot into a two-way Linux console. The reference
Waveshare ESP32-P4-Module-DEV-KIT, connected through its native USB
Serial/JTAG port as `COM14`, completed three ROM-reset boots with identical
payload hashes. Every boot reached an interactive BusyBox Hush prompt and ran
`cat`, `free`, and `uname` as child processes without a panic.

## Pinned configuration

| Component | M4 contract |
| --- | --- |
| Loader | ESP-IDF v6.0.1, single HP core handoff |
| Linux | 6.12.27 plus the MicroNUX ESP32-P4 patches |
| Buildroot | 2025.02.16 |
| Userspace | BusyBox 1.37.0, uClibc-ng, bFLT, RV32 `ilp32` NOMMU |
| Kernel ISA | `rv32imac_zicsr_zifencei` |
| Console | Native USB Serial/JTAG, `ttyGS0`, nominal 115200 serial setting |
| Reference silicon | ESP32-P4 revision 1.3 |

## Build, flash, and test

From a PowerShell prompt in the repository, the complete M4 build, loader
build, flash, and three-reset acceptance test is:

```powershell
.\scripts\m4.ps1 -Port COM14 -Boots 3
```

The script requires ESP-IDF v6.0.1, builds Linux under WSL with 16 jobs,
flashes only the declared M4 addresses, and rejects a missing shell marker,
payload hash drift, mount failure, kernel panic, oops, BUG, unhandled signal,
or timer stall.

To attach an interactive terminal after the image is running:

```powershell
.\scripts\m4-console.ps1 -Port COM14
```

Press `Ctrl+]` to close the terminal. Only one process may own `COM14`, so the
console must be closed before flashing or running the automated hardware gate.
The observed Windows port number is a development-machine detail, not part of
the board contract.

## Console interrupt handoff

The native controller is at `0x500d2000`. During handoff, the loader disables
the ESP-IDF peripheral interrupt enable, clears stale status, and maps
ESP32-P4 peripheral source 22 through core 0's interrupt-matrix register at
`0x500d6058` to CLIC hardware interrupt 16. It reads the mapping back and
refuses to jump if the transfer did not stick.

Linux initially polls the same FIFO through
`earlycon=esp32p4usb,mmio,0x500d2000`. The device tree then binds the existing
Espressif USB Serial/JTAG UART driver, extended with the ESP32-P4 compatible,
to CLIC hardware interrupt 16. Linux assigns virtual IRQ 3 and registers
`ttyGS0` as the full console:

```text
500d2000.serial: ttyGS0 at MMIO 0x500d2000 (irq = 3) is a Espressif USB Serial/JTAG
esp32s3-acm 500d2000.serial: USB Serial/JTAG ttyGS0 at 0x500d2000 irq 3
```

The upstream driver keeps its historical `esp32s3-acm` implementation name;
the hardware type and device-tree compatible identify the shared P4
Serial/JTAG register layout. External P4 CLIC IDs 16 through 47 use ordinary
level IRQ handling rather than hart-local per-CPU handling.

## Initramfs and shell

The initramfs is embedded in the kernel and contains no persistent writable
root storage. Linux unpacks it into RAM, starts `/init`, and mounts `devtmpfs`
on `/dev` plus `proc` on `/proc`. `/init` then uses BusyBox `setsid` and
`cttyhack` to make dynamically allocated `/dev/ttyGS0` the controlling
terminal before starting interactive Hush.

The M4 BusyBox fragment adds only the applets needed for the acceptance shell:
`cat`, `free`, `mount`, `setsid`, and `cttyhack`. Existing M1/M3 fragments
provide the shell, `uname`, and the timer sentinel tools. There is no package
manager, persistent root filesystem, login manager, networking service, or
general-purpose storage stack yet.

## PSRAM instruction-cache coherency

NOMMU RISC-V signal delivery writes a two-instruction `rt_sigreturn`
trampoline onto the process stack. The ESP32-P4 stack lives in external PSRAM.
The architectural `fence.i` instruction synchronizes the CPU pipeline but did
not invalidate the P4 external-memory L1 instruction cache. After a child
process exited, Hush could therefore fetch stale data at the trampoline and
trap two bytes into the intended 32-bit instruction.

The M4 platform cache hook now writes dirty core-0 L1 data-cache lines into
the shared L2, invalidates the core-0 L1 instruction cache through the
ESP32-P4 ROM cache API, and finally executes `fence.i`. The ROM entry addresses
are identical in the ESP-IDF v6.0.1 revision-1 and later P4 ROM linker maps.
The final test deliberately executes multiple external applets, exercising
child exit, `SIGCHLD`, and `rt_sigreturn` on every boot.

## Hardware acceptance record

The reproducible final artifacts are:

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| Linux `Image` | 3,315,456 bytes | `3e538ab55facec4be2e8f9356993c96256b5f198e4e3dd44c77db8b23d6b4d4f` |
| In-memory kernel span | 3,532,600 bytes | covered by the manifest |
| `rootfs.cpio` | 536,576 bytes | `5f96c667ab636e41913353a7fd8a74ae772fa1d1cd1d9b1a641f60b06292866d` |
| `esp32p4-micronux.dtb` | 1,477 bytes | `df5effc9b4db5930d3cc3c1df77d447f8a0104b196e2ea668d97b6a6a35d8199` |
| M4 payload manifest | 128 bytes | `81deef71afe2348366444a9b969df79d10e5c6fd200966f9c678c16f7447f712` |

| Boot | Linux `/init` time | ROM reset to shell | `MemFree` after probes |
| ---: | ---: | ---: | ---: |
| 1 | 0.37 s | 2.52 s | 20,544 KiB |
| 2 | 0.37 s | 2.53 s | 20,544 KiB |
| 3 | 0.37 s | 2.52 s | 20,544 KiB |

The reset-to-shell figure combines the ESP-IDF elapsed timestamp at kernel
handoff with Linux's timestamp at `/init`; the ready marker follows
immediately. The gate resets through the native USB ROM path. It is a full ROM
and loader restart, not removal of power from the carrier.

## Deliberate M4 compromises

- Linux remains single-core, machine-mode, NOMMU, and RWX within its PMP-owned
  27 MiB interval.
- The initramfs is an immutable build input but is unpacked into writable RAM;
  changes disappear on reset and there is no persistent root storage.
- Only the native USB console, timer, reset, and minimal pseudo filesystems are
  supported. M6 owns storage, networking, display, and other peripherals.
- The terminal is an unrestricted root shell with no authentication. It is a
  development console, not a production security boundary.
- USB host backpressure can still delay console output, and resetting the P4
  can require a terminal application to reopen the Windows COM port.

Reference material:

- [ESP-IDF ESP32-P4 interrupt allocation](https://docs.espressif.com/projects/esp-idf/en/stable/esp32p4/api-reference/system/intr_alloc.html)
- [ESP-IDF ESP32-P4 console configuration](https://docs.espressif.com/projects/esp-idf/en/stable/esp32p4/api-reference/system/console.html)
- [ESP32-P4 revision 1.3 Technical Reference Manual](https://documentation.espressif.com/esp32-p4-chip-revision-v1.3_technical_reference_manual_en.pdf)
- [Waveshare ESP32-P4-Module-DEV-KIT wiki](https://www.waveshare.com/wiki/ESP32-P4-Module-DEV-KIT)
