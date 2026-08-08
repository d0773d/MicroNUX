# M6 Storage and Peripheral Bring-up

Status: **microSD storage slice complete; other M6 peripheral sub-tracks remain**

M6 starts with the Waveshare board's onboard microSD interface. Storage can be
isolated from the minimal USB console and from the ESP32-C6 wireless transport,
so it is a useful first test of peripheral ownership under NOMMU Linux. The
accepted M6 storage path is read-only at the userspace gate. It proves power,
pin mux, controller ownership, block discovery, repeatable reads, and a
read-only filesystem mount before enabling any card writes.

## Hardware contract

| Function | ESP32-P4 resource |
| --- | --- |
| Card power | LDO channel 4 at 3.3 V |
| SDMMC clock | GPIO43 |
| SDMMC command | GPIO44 |
| SDMMC data 0-3 | GPIO39, GPIO40, GPIO41, GPIO42 |
| Controller | DesignWare MSHC at `0x50083000` |
| Physical interrupt route | source 23, core-0 matrix `0x500d605c`, parked on disabled CLIC input 31 |
| Linux IRQ placeholder | CLIC input 17, requested and disabled by the MMC driver |

The ESP32-C6 SDIO link uses a different pin group and is not enabled or
modified by this milestone.

The ESP-IDF loader enables LDO4, establishes the SDMMC clock/reset state,
installs the dedicated I/O mux, and quiesces the controller before Linux takes
ownership. Because the accepted Linux path polls synchronously, it parks
physical source 23 on otherwise-unused CLIC input 31 and leaves that input
disabled. The device tree retains IRQ 17 only as a placeholder that the
generic MMC driver can request and disable; the SD line cannot enter Linux.

## The working compromise

The initial PIO implementation could issue commands and transfer individual
sectors, but larger block-layer reads depended on FIFO watermark events that
did not complete reliably. Using ordinary IDMAC buffers was also unsafe:
normal Linux buffers live in cached external PSRAM, and this NOMMU port does
not yet implement a general non-coherent DMA API for the ESP32-P4 cache.

M6 contains that problem inside the ESP32-P4 DesignWare MMC platform quirk:

1. The loader removes a fixed 8 KiB internal-SRAM window from its heap.
2. Linux uses one 4 KiB page for IDMAC descriptors and one as a data bounce
   buffer.
3. ESP32-P4 descriptors occupy 64 bytes. Linux writes the standard first four
   words, including the chained terminal descriptor with a null next pointer.
4. The controller receives the cacheable/bus alias, while the CPU copies
   through the uncached alias.
5. Requests are limited to one scatterlist entry and 4 KiB.
6. The initialized IDMAC engine remains active between ordinary requests;
   controller resets remain in the existing error-recovery paths.
7. The physical SD source is parked on disabled CLIC input 31. The driver's
   placeholder IRQ 17 is also disabled, and the driver invokes the generic
   DesignWare interrupt handler while polling command and data status every
   100 microseconds, with a 100 ms diagnostic threshold.

| Purpose | Controller/bus address | Linux CPU address | Size |
| --- | ---: | ---: | ---: |
| IDMAC descriptors | `0x4ff80000` | `0x8ff80000` | 4 KiB |
| Data bounce buffer | `0x4ff81000` | `0x8ff81000` | 4 KiB |
| Reserved interval | `0x4ff80000` | - | 8 KiB |

The loader and the stalled second-core stack both remain outside this
interval. Linux's regular block buffers stay in PSRAM and never become DMA
targets. The driver has bounce-copy handling for both transfer directions,
but card writes are not part of the M6 acceptance contract and are not tested.

This mode deliberately spends CPU time and caps each request at 4 KiB. It is a
correctness-first storage path, not a throughput design. The unreliable CLIC
delivery is an observation from the connected ESP32-P4 revision 1.3 board and
current MicroNUX interrupt port; it is not presented as an official silicon
erratum. A future interrupt-driven or direct-PSRAM DMA path needs a separate
hardware gate and a real architecture-wide cache-maintenance contract.

## Revision 1.3 CLIC and console compromise

The physical gate also exposed a return-path interaction on the connected
ESP32-P4 revision 1.3. An interrupt-form `mret` restores the CLIC level, but a
direct interrupt-form return to userspace re-enters in machine mode. MicroNUX
therefore performs a two-stage return for userspace interrupted by the timer:
the first `mret` restores the CLIC level into a machine-mode trampoline with
interrupts disabled, and the trampoline performs the normal exception-form
return to userspace. Kernel returns and userspace exception returns remain
direct.

The USB Serial/JTAG peripheral source also does not re-arm reliably through
the current Linux CLIC IRQ path. Its driver checks the peripheral interrupt
status once per kernel tick, then uses the normal RX/TX status handler. It
does not poll the FIFO count directly. These are board-and-port observations,
not claims of an official silicon erratum.

The first device-tree profile uses a 4-bit bus, an 80 MHz controller input
clock, a 20 MHz card limit, polling card detection, and disables MMC/eMMC and
SDIO card types. Only removable SD memory is in the M6 storage contract.

## Read-only acceptance gate

`scripts/m6.ps1` builds and flashes the loader and M6 image, then performs
three independent ROM-reset boots. On every boot the target probe:

1. requires `/dev/mmcblk0` after boot-time card discovery;
2. reads the first 1 MiB twice through the block layer;
3. requires both SHA-256 hashes to match;
4. mounts partition 1 as read-only VFAT when it is present and then unmounts
   it; and
5. runs the complete M5 NOMMU stress regression.

The host requires stable card size, card sample hash, kernel hash, and DTB
hash across all boots. The probe never formats the card, mounts it writable,
or issues a block write.

The inserted 29.7 GiB `SK32G` card passed the clean-build, three-ROM-reset
hardware gate with these native Linux markers on every boot:

```text
mmc0: new SDHC card at address aaaa
mmcblk0: mmc0:aaaa SK32G 29.7 GiB
 mmcblk0: p1
MICRONUX:M6:STORAGE begin mode=idmac-sram-poll access=read-only
MICRONUX:M6:STORAGE raw-pass sectors=62333952 bytes=1048576 sha256=6158c8c683a1c1a66950c4e6593af64b0356cc52702e76ca00af1bdff5978c49
MICRONUX:M6:STORAGE mount=vfat-ro
MICRONUX:M6:STORAGE:PASS mode=idmac-sram-poll access=read-only
```

This is execution on the physical ESP32-P4, not QEMU emulation. The milestone
storage slice passed with:

- three identical 62,333,952-sector card reports;
- three identical first-1-MiB sample hashes of
  `6158c8c683a1c1a66950c4e6593af64b0356cc52702e76ca00af1bdff5978c49`;
- read-only VFAT mounts on all three boots;
- M5 elapsed times of 689, 687, and 687 ms; and
- free-memory readings of 19,796 KiB before each M5 run and 19,788 KiB
  afterward;
- a 3,647,560-byte kernel with SHA-256
  `4a5a485a2b345e73799058ac5f2ddb8818ad5cce29c76a8f35a9370b572aca29`
  and DTB SHA-256
  `12fd3a00b4774fbe8b1620c489b21b862396e7ca11ce179e1aa6a4d11ea8d926`.

No acceptance step formats the card, mounts it writable, or issues a block
write.

The controller-only diagnostic remains available when no card is inserted:

```powershell
.\scripts\m6.ps1 -Port COM14 -Boots 3 -ControllerOnly
```

Omitting `-ControllerOnly` restores the strict default and requires a stable
card size and sample hash on every boot.

## MIPI-DSI display sub-track

MIPI display support is feasible, but continuous scanout has the same DMA and
cache-coherency risk as storage DMA and depends on the exact attached panel
timing, reset, and backlight circuit. It is staged separately:

1. **M6-D0: electrical proof.** Configure DSI PHY, DBI/DPI timing, reset, and
   backlight for one named panel, then show a controller-generated color bar
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
