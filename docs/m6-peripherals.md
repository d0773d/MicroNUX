# M6 Storage and Peripheral Bring-up

Status: **implementation complete; removable-media gate pending**

M6 starts with the onboard microSD interface because it can be isolated from
the minimal USB console and from the ESP32-C6 wireless transport. The first
implementation is deliberately read-only and PIO-only. It establishes the
power, pin-mux, interrupt, controller, block-device, and userspace contracts
before any higher-risk DMA or filesystem-write path is considered.

## microSD hardware contract

The Waveshare ESP32-P4-Module-DEV-KIT routes the onboard slot as follows:

| Function | ESP32-P4 resource |
| --- | --- |
| Card power | LDO channel 4 at 3.3 V |
| SDMMC clock | GPIO43 |
| SDMMC command | GPIO44 |
| SDMMC data 0-3 | GPIO39, GPIO40, GPIO41, GPIO42 |
| Controller | DesignWare MSHC at `0x50083000` |
| Interrupt route | source 23, core-0 matrix `0x500d605c`, CLIC input 17 |

The ESP32-C6 SDIO link uses a different pin group and is not enabled or
modified by this milestone.

The ESP-IDF loader enables LDO4, establishes the SDMMC clock/reset state,
quiesces the IDF interrupt and internal DMA state, and installs the dedicated
I/O mux. Immediately before the Linux jump it quiesces the controller again
and transfers source 23 to Linux CLIC input 17. This prevents an IDF handler
from observing a Linux-owned controller or an uninitialized IDF card slot.

## Why the first driver is PIO-only

Linux runs directly from cached external PSRAM without an MMU. The initial
platform does not yet have a proven bidirectional cache-maintenance and DMA
ownership contract for ESP32-P4 peripherals. Letting the MSHC internal DMA
engine access userspace or block-layer buffers would therefore risk stale
cache lines or silent memory corruption.

The ESP32-P4 device match adds a narrow `no DMA` quirk to the generic
DesignWare MMC driver. Linux consequently uses its existing FIFO PIO path.
This costs throughput and CPU time, but it makes buffer ownership explicit
and testable. DMA remains out of scope until a shared PSRAM/cache contract has
its own hardware acceptance gate.

The first DT profile uses a 4-bit bus, an 80 MHz controller input clock, a
20 MHz card limit, polling card detection, and disables MMC/eMMC and SDIO
card types. Only removable SD memory is in the M6 storage contract.

## Read-only acceptance gate

`scripts/m6.ps1` builds and flashes the loader and M6 image, then performs
three independent ROM-reset boots. On every boot the target probe:

1. requires `/dev/mmcblk0` after boot-time card discovery;
2. reads the first 1 MiB twice through the block layer;
3. requires both SHA-256 hashes to match;
4. optionally mounts partition 1 as read-only VFAT and unmounts it; and
5. runs the complete M5 NOMMU stress regression.

The host requires stable card size, card sample hash, kernel hash, and DTB
hash across all boots. The probe never formats the card, mounts it writable,
or issues a block write.

## Hardware evidence

The physical ESP32-P4 revision 1.3 board boots Linux 6.12.27 and binds the
controller with these markers:

```text
MICRONUX:M6:SDMMC power=ldo4 voltage_mv=3300 slot=0 width=4 clock_hz=80000000 pins=43,44,39,40,41,42
MICRONUX:M6:IRQ source=23 matrix=500d605c clic=17 dma=off handoff=armed
Synopsys Designware Multimedia Card Interface Driver
Using PIO mode.
DW MMC controller at irq 4, 32 bit, 512 deep fifo
mmc_host mmc0: card is polling.
```

This is native execution on the connected board, not QEMU emulation. The
initial controller boot used an empty slot, so it correctly ended with
`MICRONUX:M6:STORAGE:FAIL stage=no-card`. M6 remains open until a removable
card passes the repeatable read gate.

The controller-only gate then passed three consecutive ROM-reset boots with
the final rebuilt image. All three boots reported the same kernel and DTB
hashes, completed the full M5 regression in 692, 693, and 692 ms, and retained
20,012 KiB free from a 20,036 KiB baseline. The artifact and payload evidence
was:

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| Linux `Image` | 3,647,560 B | `a25284576e6451f89adac48c12f4e6e804adb063842b8d2434617c0717587c80` |
| initramfs | 694,784 B | `aa673b5f45ffd0babcbf8c9ccd113b6610a3b9ad15677aa30a7fcd0e1ed3e0ce` |
| DTB | 1,818 B | `12fd3a00b4774fbe8b1620c489b21b862396e7ca11ce179e1aa6a4d11ea8d926` |

This partial gate can be reproduced without weakening the normal media gate:

```powershell
.\scripts\m6.ps1 -Port COM14 -Boots 3 -ControllerOnly
```

Omitting `-ControllerOnly` restores the strict default and requires a stable
card size and sample hash on every boot.

## MIPI-DSI display sub-track

MIPI display support is feasible, but continuous scanout has the same DMA and
cache-coherency risk as storage DMA and also depends on the exact attached
panel timing, reset, and backlight circuit. It is staged separately:

1. **M6-D0: electrical proof.** Configure DSI PHY, DBI/DPI timing, reset, and
   backlight for one named panel, then show a controller-generated color-bar
   or pattern. No Linux framebuffer claim is made at this stage.
2. **M6-D1: scanout ownership.** Reserve a fixed framebuffer, define cache
   maintenance and DW-GDMA ownership, and prove a stable loader-driven image
   over resets without corrupting the Linux memory window.
3. **M6-D2: Linux console.** Hand the frozen mode and framebuffer to Linux and
   add a minimal simple-framebuffer console if continuous scanout is stable.
4. **Later evaluation.** Consider a native DRM/KMS and DSI driver only after
   the minimal path is reliable and its maintenance cost is understood.

A compatible panel or adapter and its exact model are required before M6-D0
can be implemented and verified.
