# M7 Linux-owned Kit C display

Status: **complete on the Waveshare Kit C JD9365 panel**

MicroNUX now transfers the initialized 10.1-inch, 800x1280 JD9365 MIPI-DSI
display from the ESP-IDF loader to Linux. Linux owns persistent scanout,
framebuffer access, the framebuffer console, and backlight control after
handoff. The production path is framebuffer-only; hardware diagnostic-pattern
selection is read-only because the ESP32-P4 revision-1.3 VPG-to-DPI transition
can leave the panel blue. No loader callback or FreeRTOS task remains active.

## Ownership transfer

The loader performs the controller-specific panel initialization because the
upstream Linux tree does not contain this ESP32-P4/JD9365 bring-up path. It
leaves DPI/framebuffer mode selected, quiesces the ESP-IDF one-shot transfer,
and publishes a four-descriptor ring as a validated handoff template. I2C is
left in a stable hardware state and all software ownership is dropped; the
loader does not restart scanout or enable a hardware VPG pattern.

Linux validates the complete ring, claims the channel, copies the first
descriptor's transfer parameters into the DW-GDMA channel registers, and sets
source and destination multiblock mode to hardware reload (`CFG_LO=0x5`). The
loader routes GDMA source 24 to CLIC input 18, which Linux receives as IRQ 3.
Each block-done interrupt increments the frame counter and reloads the DSI
bridge raw-word counter. A 50 microsecond high-resolution timer only monitors
the bridge underrun latch; it does not restart frames. Linux begins scanout
immediately and remains the sole controller.

The fbdev write path uses one 512-byte bounce buffer per system call and a
2 microsecond PSRAM-bus gap between bursts. This keeps ordinary writes bounded
without exposing the reserved framebuffer through `mmap()`. Three consecutive
2,048,000-byte writes completed in 0.529 seconds on hardware while frame IRQs
continued, with zero GDMA errors and zero bridge underruns.

A versioned, CRC-protected handoff structure at `0x49f00000` records:

- ABI version and required ownership flags;
- 800x1280 RGB565 geometry and 1,600-byte stride;
- framebuffer address and exact 2,048,000-byte size;
- the 256-byte, four-entry GDMA descriptor ring and channel number; and
- backlight I2C address, register, and last brightness.

Linux validates every field, the CRC, all address ranges, each descriptor's
source, DSI FIFO destination, circular link, and valid/last/interrupt bits
before registering the device. A malformed or stale contract fails closed
without exposing a framebuffer.

The accepted hardware handoff was:

```text
MICRONUX:M7:DSI-HANDOFF state=ready owner=linux-pending pattern=framebuffer dma=descriptor-ring channel=0 rearm=linux fb=[48040a80,48234a80) desc=[4ff3ba80,4ff3bb80) i2c=transferred contract=49f00000 crc32=8c6a97eb
MICRONUX:M7:IRQ source=24 matrix=500d6060 clic=18 handoff=armed
MICRONUX:M7:DSI-LINUX state=ready owner=linux fb=fb0 resolution=800x1280 format=rgb565 dma=ch0:auto-reload event=block-done-irq irq=3 health_poll_us=50 enable_delay_ms=0 underrun=monitored write_chunk=512 write_gap_us=2 backlight=linux mmap=denied
```

## Linux interfaces

The fixed-mode fbdev driver exposes:

| Interface | Purpose |
| --- | --- |
| `/dev/fb0` | 800x1280 RGB565 framebuffer; ordinary `mmap()` is denied |
| `/sys/bus/platform/devices/500a0000.display/ownership` | read-only ownership, DMA channel, frame IRQ, and mmap policy |
| `/sys/bus/platform/devices/500a0000.display/pattern` | read-only `framebuffer`; hardware VPG switching is disabled |
| `/sys/bus/platform/devices/500a0000.display/scanout` | live frame-counter delta, DMA error, bridge-underrun count, and sampled channel-enable state |
| `/sys/bus/platform/devices/500a0000.display/diagnostics` | raw handoff, descriptor, GDMA, and bridge-underrun state for privileged diagnosis |
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
framebuffer range, read/write its rounded descriptor page, and write only the
4 KiB DSI FIFO MMIO page. Other DMA channels receive no access to those
ranges. CPU PMP continues to exclude the loader/display reservation from
U-mode. The accepted marker was:

```text
MICRONUX:M7:DMA-PMS state=pass region0=[4ff80000,4ff82000) sdmmc=rw:00000001 display=ch0:r:00000006:w:0000000c fb=[48040000,48235000) desc=[4ff3b000,4ff3c000) fifo=[50105000,50106000) other=deny
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
workload. Its display phase writes the complete 2,048,000-byte framebuffer,
requires a positive `scanout` frame-counter delta with no GDMA error or bridge
underrun, verifies that hardware test-pattern writes are rejected, and changes
then restores brightness.
This avoids treating successful RAM readback as proof of panel scanout. It
also avoids counting the interactive shell's command-parser storage as a
kernel leak while retaining the original 16 KiB loss limit.

## Accepted artifacts and hardware result

The 2026-08-09 clean build and physical three-boot gate produced:

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| Linux `Image` | 6,025,008 B | `14181d0ad0fff782c5f039e25f863323851f83730818a2ef6223e7d524124f35` |
| Device tree | 2,453 B | `3c31c2d81ad6c2a4017d20fc8364732eb829097291150a4894609b383f39a703` |
| Metadata | 128 B | `b7f494fc602ad08db3699ede37e3461af8b877246052959977228b02044ec529` |
| ESP-IDF loader | 278,464 B | `f6f4ef6974e74278430ddacb58abafd9fbe7392113169d73458bd5ff68025786` |

The Linux image leaves 266,448 bytes in the fixed 6 MiB partition. The bFLT
W^X audit passed all 14 userspace executables. Every boot returned identical
arena accounting and general memory:

```text
M7 arena boot 1/3 passed: reserved=472 mapped=402 free=1576 arenas=5 mem_kib=8176->8176
M7 arena boot 2/3 passed: reserved=472 mapped=402 free=1576 arenas=5 mem_kib=8176->8176
M7 arena boot 3/3 passed: reserved=472 mapped=402 free=1576 arenas=5 mem_kib=8168->8168
```

Each boot also passed controlled SD writes, C6 association/DHCP and external
ping, paced framebuffer writes with zero underruns, rejection of unsafe
pattern switching, brightness change/restore, 19 U-mode
fault cases, W^X, arena reuse, supervisor admission, timeout/output limits,
and post-fault device-service liveness.
