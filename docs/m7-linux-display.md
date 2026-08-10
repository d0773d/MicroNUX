# M7 Linux-owned Kit C display

Status: **complete on the Waveshare Kit C JD9365 panel**

MicroNUX now transfers the initialized 10.1-inch, 800x1280 JD9365 MIPI-DSI
display from the ESP-IDF loader to Linux. Linux owns persistent scanout,
framebuffer access, the framebuffer console, diagnostic-pattern selection,
and backlight control after handoff. No loader callback or FreeRTOS task
remains active.

## Ownership transfer

The loader performs the controller-specific panel initialization because the
upstream Linux tree does not contain this ESP32-P4/JD9365 bring-up path. It
then disconnects the DPI producer, stops the one-shot GDMA transfer, converts
the live descriptor into a non-interrupting circular list, and restarts only
the display GDMA channel. I2C is left in a stable hardware state and all
software ownership is dropped.

A versioned, CRC-protected handoff structure at `0x49f00000` records:

- ABI version and required ownership flags;
- 800x1280 RGB565 geometry and 1,600-byte stride;
- framebuffer address and exact 2,048,000-byte size;
- the 64-byte circular GDMA descriptor and channel number; and
- backlight I2C address, register, and last brightness.

Linux validates every field, the CRC, all address ranges, descriptor source,
DSI FIFO destination, circular link, valid/last/interrupt bits, and running
channel before registering the device. A malformed or stale contract fails
closed without exposing a framebuffer.

The accepted hardware handoff was:

```text
MICRONUX:M7:DSI-HANDOFF state=ready owner=linux-pending pattern=vertical-bars dma=circular channel=0 fb=[48040a80,48234a80) desc=[4ff3b5c0,4ff3b600) i2c=transferred contract=49f00000 crc32=3eac9698
MICRONUX:M7:DSI-LINUX state=ready owner=linux fb=fb0 resolution=800x1280 format=rgb565 dma=ch0:circular backlight=linux mmap=denied
```

## Linux interfaces

The fixed-mode fbdev driver exposes:

| Interface | Purpose |
| --- | --- |
| `/dev/fb0` | 800x1280 RGB565 framebuffer; ordinary `mmap()` is denied |
| `/sys/bus/platform/devices/500a0000.display/ownership` | read-only ownership, channel, circular-DMA, and mmap policy |
| `/sys/bus/platform/devices/500a0000.display/pattern` | `framebuffer`, `vertical`, `horizontal`, or `ber` |
| `/sys/class/backlight/micronux-backlight/brightness` | Linux-owned 0-255 backlight level |

The framebuffer console attached as a 100x80 color console. The native USB
`ttyGS0` console remains the primary automation and recovery path.

This is intentionally a fixed board driver, not a general DRM/KMS stack. It
does not implement runtime modesetting, hotplug, EDID, alternate panels,
rotation, acceleration, or unprivileged direct mapping. Applications should
eventually use a versioned MicroNUX display service rather than raw sysfs or
MMIO.

## DMA boundary

The P4 DMA permission controller grants the SDMMC master only its internal
bounce/descriptor region. The exact display GDMA channel can read the rounded
framebuffer range and read/write its rounded descriptor page; other DMA
channels receive no access to those ranges. CPU PMP continues to exclude the
loader/display reservation from U-mode. The accepted marker was:

```text
MICRONUX:M7:DMA-PMS state=pass region0=[4ff80000,4ff82000) sdmmc=rw:00000001 display=ch0:r:00000006:w:00000004 fb=[48040000,48235000) desc=[4ff3b000,4ff3c000) other=deny
```

This is a channel-scoped DMA boundary, not a general IOMMU. Each newly enabled
DMA master still requires a separate audit and fail-closed permission rule.

## Reproduce

Build, flash, and run the exact Kit C gate from PowerShell:

```powershell
.\scripts\m7.ps1 -Flash -Test -Boots 3 -ConfirmExactKitC -ConfirmPmpChange
```

For an already-built and flashed image, run the serial gate directly:

```powershell
& 'C:\Espressif\python_env\idf6.0_py3.11_env\Scripts\python.exe' `
  .\scripts\m7-test.py --loader-port COM13 --linux-port COM14 `
  --boots 3 --timeout 420 --artifact-dir .\out\m7 `
  --expect-mipi-profile jd9365 --log .\out\m7\m7-display-three-boot.log
```

The gate executes identical full workloads until allocator high-water state is
stable, then compares arena accounting and `MemFree` across another complete
workload. This avoids counting the interactive shell's command-parser storage
as a kernel leak while retaining the original 16 KiB loss limit.

## Accepted artifacts and hardware result

The 2026-08-09 clean build and physical three-boot gate produced:

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| Linux `Image` | 6,024,944 B | `e88a4789e62f066d8893b6f40b274e098d63015b5dac37814e7f6ea34c749fd9` |
| Device tree | 2,421 B | `86522efaf831c555b83218753c9a2449d4900784091beafc9e245a19d424feab` |
| Metadata | 128 B | `8c5354fcf4d40f46e9c80b645495777dcf362d63b4c5cd01b39e4b3a7692fedc` |
| ESP-IDF loader | 278,576 B | `4a9da9a53fea10812d4e67979ea755caedcf6ca85fd67beae149f1011867dcff` |

The Linux image leaves 266,512 bytes in the fixed 6 MiB partition. The bFLT
W^X audit passed all 14 userspace executables. Every boot returned identical
arena accounting and general memory:

```text
M7 arena boot 1/3 passed: reserved=472 mapped=402 free=1576 arenas=5 mem_kib=8176->8176
M7 arena boot 2/3 passed: reserved=472 mapped=402 free=1576 arenas=5 mem_kib=8176->8176
M7 arena boot 3/3 passed: reserved=472 mapped=402 free=1576 arenas=5 mem_kib=8176->8176
```

Each boot also passed controlled SD writes, C6 association/DHCP and external
ping, framebuffer and pattern checks, brightness change/restore, 19 U-mode
fault cases, W^X, arena reuse, supervisor admission, timeout/output limits,
and post-fault device-service liveness.
