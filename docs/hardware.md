# MicroNUX M0 Hardware Contract

Status: **M0 complete - reference hardware frozen**
Recorded: 2026-08-07

## Reference target

The MicroNUX reference board is the
[Waveshare ESP32-P4-Module-DEV-KIT](https://docs.waveshare.com/ESP32-P4-Module-DEV-KIT).
Its core hardware contract is:

| Item | Contract |
|---|---|
| Main SoC | ESP32-P4, dual HP RISC-V cores plus LP core |
| Wireless coprocessor | ESP32-C6 connected to the P4 over SDIO |
| PSRAM | 32 MiB |
| SPI NOR flash | 16 MiB |
| Loader SDK | ESP-IDF v6.0.1, target `esp32p4` |
| Linux execution model | 32-bit RISC-V NOMMU; single HP core for the first hardware shell |
| Primary console | UART0 at 115200 8N1, P4 TX GPIO37 / RX GPIO38 through CH343P |
| Primary boot path | ROM -> ESP-IDF second-stage loader -> Linux image in PSRAM |

The Waveshare A/B/C product suffixes appear to select accessory bundles rather
than a different P4 module contract. Any board-revision-specific GPIO or PHY
difference must still be checked against the actual carrier revision before a
driver is enabled.

## Address contract

ESP-IDF v6.0.1 defines the ESP32-P4 external-RAM address aperture as
`0x48000000` through `0x4BFFFFFF` (64 MiB). With the board's 32 MiB device, the
initial MicroNUX RAM candidate is:

```text
0x48000000 - 0x49FFFFFF   32 MiB external PSRAM
```

The internal L2 SRAM aperture is:

```text
0x4FF00000 - 0x4FFBFFFF   768 KiB internal SRAM
```

The loader must prove the actual mapped, cacheable PSRAM range before this
becomes the Linux `mem=`/device-tree RAM declaration. Linux must reserve the
loader, handoff block, ROM/IDF-owned regions, and any communication buffers
instead of treating the whole aperture as freely allocatable.

## Live probe evidence

A non-destructive probe of the selected reference board verified:

- ESP32-P4 silicon revision v1.3 (`esp32p4-eco2-20240710` ROM)
- two HP cores and one LP core
- 40 MHz crystal
- 16 MiB SPI NOR flash
- 32 MiB PSRAM detected and tested at 200 MHz
- application CPU configured at 360 MHz
- factory YamUI application and bootloader built with ESP-IDF v5.5.2-dirty
- UART console on GPIO37/GPIO38 through the CH343 interface
- ESP32-C6 link using SDIO slot 1, four-bit mode at 40 MHz

The user confirmed that this attached device is the linked
`ESP32-P4-Module-DEV-KIT`. This probe supersedes the earlier observation of a
different attached Waveshare Touch-LCD-3.5 board.

## Frozen boot-critical pin map

| Function | ESP32-P4 GPIO | Evidence |
|---|---:|---|
| UART0 TX | 37 | carrier schematic and live boot log |
| UART0 RX | 38 | carrier schematic and live boot log |
| Boot strap/button | 35 | carrier schematic |
| ESP32-C6 SDIO CLK | 18 | live ESP-Hosted initialization |
| ESP32-C6 SDIO CMD | 19 | live ESP-Hosted initialization |
| ESP32-C6 SDIO D0-D3 | 14, 15, 16, 17 | live ESP-Hosted initialization |
| ESP32-C6 reset | 54 | live ESP-Hosted initialization |

UART0 at 115200 8N1 is the only console required for the first Linux shell.
Storage, Ethernet, display, camera, and USB pins will be frozen in their own
driver milestones so their mux and DMA conflicts are reviewed at the point of
use.

## Silicon and interrupt-controller risk

The local ESP-IDF v6.0.1 source distinguishes the standard CLIC implementation
used by ESP32-P4 revision 2 and later. The attached revision 1.3/ECO2 device
must therefore not inherit a revision-2 CLIC assumption. M3 needs a boot-time
revision check and an explicitly verified interrupt path. If the selected
Module-DEV-KIT has newer silicon, MicroNUX may need separate ECO2 and revision-2+
platform profiles.

## Flash safety gate

The selected reference board reports:

- secure boot disabled;
- flash encryption disabled; and
- `SPI_BOOT_CRYPT_CNT` equal to zero.

Before the first MicroNUX write, read the entire 16 MiB factory flash into a
local backup, record its SHA-256 hash, and verify that a sample can be read
back. Then review the MicroNUX partition offsets and rehearse ROM download-mode
recovery. No eFuse change is part of the development plan. All M0 inspection
was read-only apart from resetting the board to collect its boot log.

## Toolchain contract

- Loader and hardware diagnostics: ESP-IDF v6.0.1
- ROM inspection: esptool v5.3.0
- Linux root filesystem: Buildroot, uClibc-ng, and BusyBox on a Linux/WSL2 host
- Linux ABI: RV32 NOMMU `rv32imac_zicsr_zifencei` with soft-float `ilp32`;
  Linux must not be exposed to `F` or `D` in the initial profile
- Build outputs must record source revisions, configuration files, and hashes

## M0 exit criteria

- [x] Confirm the linked Module-DEV-KIT as the MicroNUX reference board.
- [x] Pin ESP-IDF loader version and ESP32-P4 target.
- [x] Verify the selected board's SoC revision, flash size, PSRAM size/speed, and console pins.
- [x] Verify that secure boot and flash encryption are disabled.
- [x] Freeze a full-flash backup and hash check as the first-write prerequisite.
- [x] Freeze the boot-critical carrier pin map from the current vendor schematic and live log.

## Sources

- [Waveshare board documentation](https://docs.waveshare.com/ESP32-P4-Module-DEV-KIT)
- [Waveshare resources and documents](https://docs.waveshare.com/ESP32-P4-Module-DEV-KIT/Resources-And-Documents)
- [Waveshare carrier schematic](https://files.waveshare.com/wiki/ESP32-P4-Module-DEV-KIT/ESP32-P4-Module-DEV-KIT.pdf)
- [Waveshare ESP32-P4 example repository](https://github.com/waveshareteam/ESP32-P4-Platform)
- [Espressif ESP32-P4 datasheet](https://documentation.espressif.com/esp32-p4_datasheet_en.html)
- Local ESP-IDF v6.0.1 source tree at `C:\esp\v6.0.1\esp-idf`
