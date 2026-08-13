# M3 Minimal ESP32-P4 Linux Platform

Status: **complete** (hardware-verified on 2026-08-07)

M3 is the first MicroNUX milestone that executes Linux on the physical
ESP32-P4. The reference Waveshare ESP32-P4-Module-DEV-KIT, connected through
its native USB Serial/JTAG port as `COM14`, completed three independent
ROM-reset boots. Each boot reached the embedded `/init` process with the same
kernel and device-tree hashes and without an unhandled trap, panic, or timer
stall.

The platform implementation is intentionally narrow. It establishes the
machine-mode trap path, CLIC interrupt controller, CLINT timer, polling early
console, reset hook, minimal device tree, loader handoff, and an initramfs
sentinel. A full interactive console remains M4 work.

## Pinned build

| Component | Version or contract |
| --- | --- |
| ESP-IDF loader | ESP-IDF v6.0.1 |
| Buildroot | 2025.02.16, archive SHA-256 `15305e3d366eeaf4a5ecaf2ed42f685fd6af7fe5dbf1f62e1de5f46ee83225e2` |
| Linux | 6.12.27 plus the MicroNUX ESP32-P4 patch |
| Userspace | BusyBox and uClibc-ng, RV32 `ilp32` NOMMU |
| Kernel ISA | `rv32imac_zicsr_zifencei`, one HP core |
| Reference silicon | ESP32-P4 revision 1.3, reported by ESP-IDF as revision `103` |

The Linux build is reproducible and uses 16 parallel jobs:

```powershell
wsl.exe -d Ubuntu -- bash -lc "MICRONUX_JOBS=16 /mnt/c/Users/d0773/Documents/ChatGPT/MicroNUX/scripts/m3-build.sh"
```

On the development machine, its Buildroot output directory is
`/home/d0773d/.cache/micronux/m3/output-2025.02.16`. The complete build,
loader flash, payload flash, and three-boot gate can be repeated with:

```powershell
.\scripts\m3.ps1 -Port COM14 -Boots 3
```

The PowerShell entry point rejects ESP-IDF versions other than v6.0.1. Linux
is built by Buildroot under WSL; the loader is built by Ninja with 16 jobs.

## Boot and payload contract

The loader validates a 128-byte, little-endian M3 payload manifest before it
touches the Linux execution window. The manifest covers its own ABI and CRC,
the kernel load address, file and memory spans, DTB size, and SHA-256 digests
for both payloads. The loader then tests the target PSRAM, reads the flash
partitions, zeroes the kernel file-to-memory tail, synchronizes the data and
instruction caches, and verifies the final addresses.

Linux receives the standard RISC-V boot registers:

- `a0 = 0`, the boot hart ID;
- `a1 = <DTB address>`, a valid flattened device tree below the Linux window.

The assembly handoff uses `a2` internally for the entry address, executes
`fence rw, rw` and `fence.i`, and jumps to `0x48400000`. HP core 1 is stalled
before the jump, so the Linux view is deliberately single-core.

## PSRAM ownership and PMP

| Virtual interval | Size | Owner and policy |
| --- | ---: | --- |
| `0x48000000`-`0x483fffff` | 4 MiB | ESP-IDF loader, DTB, and handoff state; excluded from Linux |
| `0x48400000`-`0x49efffff` | 27 MiB | Linux RAM; exact locked PMP TOR window, user-mode RWX |
| `0x49f00000`-`0x49ffffff` | 1 MiB | Future loader/coprocessor communications reserve; excluded from Linux |

ESP-IDF installs locked PMP entries before `app_main()`. Its default W^X map
does not make a kernel allocated from PSRAM heap executable, and its existing
locked entries cannot be rewritten. M3 therefore disables ESP-IDF system
memory protection and programs the unused entries 13 and 14 as an exact,
locked TOR window from `0x48400000` through, but not including, `0x49f00000`.
Entry 13 establishes the lower bound; entry 14 grants RWX. This is also what
allows NOMMU user programs to execute while keeping the loader and future
communications reserve inaccessible from U-mode.

The device tree describes 28 MiB starting at `0x48400000` and marks the final
1 MiB as `reserved-memory`, leaving the same 27 MiB usable interval. The DTB
also describes the loader range as reserved so the ownership contract remains
explicit even though that range lies below the Linux memory node.

## Traps and interrupts

ESP32-P4 uses CLIC mode rather than the conventional RISC-V direct or vectored
trap modes. M3 handles the revision-1.3 behavior by:

- aligning every possible early trap target and `handle_exception` to 64
  bytes;
- setting `mtvec.MODE` to CLIC mode (`3`);
- preserving the complete extended `mcause` value in `pt_regs` for `mret`;
- presenting only the architectural interrupt flag and six-bit cause number
  to generic Linux exception, signal, and IRQ code;
- initializing the memory-mapped CLIC at `0x20800000`, using a four-byte
  interrupt stride, three level bits, and threshold zero;
- masking and unmasking CLIC interrupt-enable bytes instead of the standard
  `mie` CSR bits.

This is revision-specific platform code, not a claim of compatibility with
older ESP32-P4 CLIC layouts. Espressif's interrupt-allocation documentation
also notes that ESP32-P4 interrupt handling is CLIC-based.

## Clocksource

The P4 CLINT block is at `0x20000000`, with `mtimecmp` at offset `0x4000`,
`mtimectl` at `0x4010`, and `mtime` at `0xbff8`. Bit 0 of `mtimectl` enables the
counter. The loader measures the live counter against `esp_timer` for 100 ms
before handoff and refuses unsupported silicon separately.

The three final boots measured 359,997,090 Hz, 359,997,080 Hz, and
359,997,100 Hz. The device-tree clock contract is exactly 360,000,000 Hz, and
Linux registered:

```text
clint: clint@20000000: timer running at 360000000 Hz
```

The `/init` sentinel repeatedly executes BusyBox `sleep`, so reaching and
remaining in PID 1 also exercises timer interrupts and scheduling rather than
only proving straight-line kernel execution.

## Console and reset

The board's attached `COM14` port is the ESP32-P4 native USB Serial/JTAG
controller, not UART0 routed through an external USB-to-UART bridge. The M3
early console therefore polls the inherited USB Serial/JTAG FIFO at
`0x500d2000` and is selected with:

```text
earlycon=esp32p4usb,mmio,0x500d2000
```

UART0 remains described at `0x500ca000` for future driver work, but using it
for M3 output would require the separate GPIO37/GPIO38 electrical path and
would not appear on `COM14`.

The reset driver registers a high-priority restart handler and calls the
revision-1.3 ESP32-P4 ROM software-reset entry at `0x4fc00094`. M3 provides
restart, not a board power-off implementation.

## Deliberate compromises

These compromises are required for the current platform to work correctly and
are part of the M3 contract:

- Linux runs in machine mode without an SBI or MMU and has no demand paging,
  copy-on-write `fork()`, or MMU-backed process isolation.
- The 27 MiB Linux interval is RWX. PMP separates it from loader and reserved
  memory but does not enforce W^X inside Linux. M7 owns stronger isolation.
- ESP-IDF system memory protection is disabled because its locked heap-PSRAM
  policy prevents execution of the loaded kernel.
- ESP-IDF interrupt and task watchdogs are disabled before handoff. FreeRTOS
  would otherwise stop servicing them and reset a healthy Linux kernel after
  takeover. Linux watchdog policy is not implemented yet.
- Only HP core 0 runs Linux. HP core 1 is explicitly stalled.
- The USB console is polling and output-only. It can block on host
  backpressure and is not the full tty needed for the M4 shell.
- The device tree exposes only the CPU, CLIC, CLINT, memory reservations,
  UART0, and native USB console. Storage, networking, DMA, and the ESP32-C6
  transport are intentionally absent.
- Secure boot, flash encryption, and security eFuse changes remain out of
  scope during development.

## Hardware acceptance record

The final artifacts flashed and verified by `esptool` were:

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| Linux `Image` | 3,274,112 bytes | `f13c8661e74e876eae941ce3942cb0d4978a01140672e6cb9f9b9dd380c3dafb` |
| In-memory kernel span | 3,491,624 bytes | covered by the image manifest |
| `esp32p4-micronux.dtb` | 1,417 bytes | `617c7edfd3bf327c864d594303b239ceaec3cbc46bbcd68a9270166eb26f5fd8` |
| `rootfs.cpio` | 498,176 bytes | `9f90afbbe12b3648ce639b4474b3351f22d719ff63f70d78d69ccd636b0591e5` |
| M3 payload manifest | 128 bytes | `477f63d58301e1c333df2714a895029ab45950a5a7374795ddb3dbb6ec0e3170` |
| ESP-IDF loader application | 195,920 bytes | `9e75248a3c830ca269cafa5e5fd6f9788f497fe4ef1abfc6435810080c2ea60d` |

All three final boots reported manifest CRC `ba77a649`, handoff CRC
`cfb2e061`, identical payload hashes, and `Run /init as init process` at
0.280684 s, 0.280552 s, and 0.280617 s. The automated gate rejects missing
PMP, CLIC/timer, Linux, or init markers and rejects `MICRONUX:M3:FAIL`, kernel
panic, oops, BUG, unhandled-trap, and timer-stall output.

Reference material:

- [ESP32-P4 revision 1.3 Technical Reference Manual](https://documentation.espressif.com/esp32-p4-chip-revision-v1.3_technical_reference_manual_en.pdf)
- [ESP-IDF v6.0 ESP32-P4 get-started guide](https://docs.espressif.com/projects/esp-idf/en/v6.0/esp32p4/get-started/)
- [ESP-IDF ESP32-P4 interrupt allocation](https://docs.espressif.com/projects/esp-idf/en/stable/esp32p4/api-reference/system/intr_alloc.html)
- [Waveshare ESP32-P4-Module-DEV-KIT wiki](https://www.waveshare.com/wiki/ESP32-P4-Module-DEV-KIT)
