# M2 - ESP32-P4 loader and kernel handoff

Status: **complete**

M2 proves the boundary between an ESP-IDF second-stage environment and future
NOMMU Linux platform code on the physical Waveshare ESP32-P4-Module-DEV-KIT.
It does not claim that Linux itself executes on ESP32-P4 yet.

The loader copies the exact M1 Linux `Image` into external PSRAM and verifies
its content. It then transfers control to a small diagnostic entry point whose
instructions are also mapped from PSRAM. That diagnostic independently checks
the handoff block and emits the acceptance token. Replacing the diagnostic
entry with a viable ESP32-P4 Linux entry is M3.

## Frozen inputs

| Input | Contract |
|---|---|
| Loader SDK | ESP-IDF v6.0.1 |
| Board | Waveshare ESP32-P4-Module-DEV-KIT |
| Silicon tested | ESP32-P4 revision v1.3 (`revision=103`) |
| PSRAM | 32 MiB, configured at 200 MHz |
| SPI NOR flash | 16 MiB |
| M1 Linux `Image` size | 4,748,064 bytes |
| M1 Linux `Image` SHA-256 | `f42ac72f6f24027dfa344fc96ce840a0aabce24da8a291882a8b27f74b222587` |
| Console | UART0, 115200 8N1 |

The loader refuses to hand off if PSRAM capacity, buffer integrity, partition
size, image hash, or PSRAM address translation differs from this contract.

## Flash layout

| Offset | Size | Purpose |
|---:|---:|---|
| `0x002000` | IDF generated | second-stage bootloader |
| `0x008000` | IDF generated | partition table |
| `0x010000` | 1,984 KiB | factory loader application |
| `0x200000` | 6 MiB | pinned M1 Linux `Image` |
| `0x800000` | 2 MiB | future root filesystem |
| `0xA00000` | 64 KiB | future metadata |

The M1 image is programmed as raw bytes at `0x200000`. Linux does not need a
flash driver for M2 because ESP-IDF reads the partition before the handoff.

## Handoff ABI v1

The packed-by-contract, naturally aligned handoff block is 204 bytes. Its
magic is `0x584e554d` (the little-endian bytes spell `MUNX`), and its CRC-32
covers the first 200 bytes. Compile-time assertions freeze both sizes.

The block records:

- ABI version and feature flags;
- boot hart and ESP32-P4 silicon revision;
- CPU and APB clock rates;
- UART console type, instance, and baud rate;
- physical PSRAM capacity and allocator observations;
- kernel loader virtual address, PSRAM physical offset, size, flash offset,
  and SHA-256;
- device-tree address/size placeholders;
- a bounded reserved-region table; and
- the M2 diagnostic entry virtual address and PSRAM physical offset.

M2's temporary diagnostic register contract is:

```text
a0 = boot hart ID (0)
a1 = pointer to micronux_handoff_v1_t in internal SRAM
a2 = diagnostic entry virtual address (trampoline input only)
```

The IRAM trampoline executes `fence rw,rw`, `fence.i`, then `jr a2`. Before it
jumps, the loader flushes the console, stalls the other HP core, suspends the
FreeRTOS scheduler, and disables local interrupts. The UART, cache/MMU, and
PSRAM mappings remain live and are explicitly inherited by the diagnostic.

This is not the final Linux boot protocol. M3 must provide a real flattened
device tree, pass its address in `a1` as required by the RISC-V Linux boot ABI,
and expose any separate MicroNUX metadata through the device tree or a reserved
memory contract. It must also replace the diagnostic entry rather than jumping
the M1 image blindly: the QEMU image currently contains a `virt` platform
kernel, not ESP32-P4 platform support.

## Build, flash, and test

Prerequisites are the pinned M1 output produced by `scripts/m1.ps1`, ESP-IDF
v6.0.1 at `C:\esp\v6.0.1\esp-idf`, Python with pyserial, and the board's CH343
UART port. From PowerShell at the repository root:

```powershell
.\scripts\m2.ps1 -Port COM13 -Boots 3
```

Replace `COM13` with the currently enumerated CH343 port. The script:

1. verifies the cached M1 image hash;
2. configures the loader with ESP-IDF v6.0.1;
3. builds it with `ninja -j 16`;
4. flashes the IDF bootloader, partition table, application, and M1 image; and
5. performs three EN hard-reset boots and compares the handoff CRC and token.

To retest an already built and flashed board without writing flash:

```powershell
.\scripts\m2.ps1 -Port COM13 -Boots 3 -SkipBuild -SkipFlash
```

In this milestone, “cold boot” means the external EN reset sequence: execution
restarts in mask ROM and the second-stage loader reinitializes PSRAM. The test
does not switch off carrier power.

## Measured result

The ESP-IDF application binary is 199,040 bytes (`0x30980`), leaving 90% of its
1,984 KiB application partition free. The physical acceptance run produced the
same handoff CRC and diagnostic token on all three resets:

```text
MICRONUX:M2:BOOT target=esp32p4 revision=103 cores=2
MICRONUX:M2:PSRAM size=33554432 heap=33438208 free=33434104 largest=33030144
MICRONUX:M2:PSRAM_TEST bytes=4748064 result=pass
MICRONUX:M2:KERNEL size=4748064 sha256=f42ac72f6f24027dfa344fc96ce840a0aabce24da8a291882a8b27f74b222587
MICRONUX:M2:HANDOFF abi=1 size=204 crc32=224b5089 kernel_paddr=00030a80 entry_paddr=00017904
MICRONUX:M2:JUMP a0=0 a1=0x4ff1181c entry=48007904
MICRONUX:M2:ENTRY hart=0 handoff=0x4ff1181c crc32=224b5089
MICRONUX:M2:PASS token=6f7900c8
M2 repeated-handoff test passed: boots=3 crc32=224b5089 token=6f7900c8
```

Loader virtual addresses can vary when the allocation or link layout changes;
the acceptance contract is correct address translation plus a stable handoff
for identical firmware and hardware, not those particular offsets forever.

## Exit criteria

- [x] Initialize and test the physical 32 MiB PSRAM.
- [x] Load and SHA-256-verify the pinned M1 Linux image from flash.
- [x] Freeze a versioned, checksummed loader handoff structure.
- [x] Stall the unused core and disable scheduler/interrupt activity.
- [x] Execute the transfer trampoline and diagnostic entry from inherited RAM.
- [x] Repeat the complete ROM-to-entry path over three EN-reset boots.
- [x] Record the exact image hash, firmware size, handoff CRC, and pass token.
