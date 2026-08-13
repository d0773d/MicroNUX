# M7 Linux-owned Kit C display

Status: **historical M7 acceptance; Patch-41 ABI-v3 runtime physically rejected
after a black/backlit panel failure; current Patch-44 candidate is unflashed
with runtime and visual validation pending**

This report preserves the hardware evidence for the accepted M7 single-buffer
implementation. That implementation transferred the initialized 10.1-inch,
800x1280 JD9365 MIPI-DSI display from the ESP-IDF loader to Linux and gave
Linux persistent scanout, framebuffer-console, and backlight ownership. It is
not the current source architecture: M9.2 uses an ABI-v3 cold-relinquish
contract, then lets Linux initialize the complete display stack and own three
framebuffers with one marked-last descriptor rearmed transactionally from each
transfer-done interrupt. That source remains **IMPLEMENTING / TESTING**.
Patch 36 passed exact flash/readback, then its GDMA enable-register diagnostic
failed closed to a headless shell. Patch 37 applied the corrective semantic
comparison and passed exact flash/readback under
`build/m9-readback/20260812T120632185Z-2993c451`. Its passive snapshot
`out/m9/hardware-runs/20260812T120919Z-snapshot-b5890811aa40-0611024a.log`
(SHA-256
`52ebc5ed26487e68419411f73cd263566e3fb8a47920e1b67f33f150d21d94b6`)
reached GDMA `CONFIGURED`, then the dark `SCANOUT_INITIALIZING` policy rejected
its correctly released I2C state. Existing containment published
`FAILED_QUIESCENT`, preserved a headless shell, and recorded
`optical-state=unobserved`. Patch 38 corrects that narrow policy defect and adds
decision-point diagnostics. It passed exact flash/readback under
`build/m9-readback/20260812T125425532Z-95d60702`; `readback.json` has SHA-256
`702ba99d9ca84bf25593301c300575a9706294dcae62f8db2ef982336e337a46`.
Its sealed passive snapshot
`out/m9/hardware-runs/20260812T125714Z-snapshot-b5890811aa40-3670b4ef.log`
has SHA-256
`1c43ac04f307de252342fdc4cdc49d3aa6f18520be5cca254d4e7df294b677ac`.
The driver reached GDMA `CONFIGURED`, then failed closed at
`arm-readback/descriptor-channel-readback`: `cfg=3 chen=1
cfglo=0000000f cfghi=0a020001 llp=1 sar=48031500 ctrlhi=c0108840`, with
all captured fault status zero. GDMA had fetched the one-shot head
(`SAR=front+0x500`) and advanced live LLP to the terminal descriptor's
`0 | memory-port` value `1`; the post-enable head-LLP equality was therefore a
false rejection. Containment again published `FAILED_QUIESCENT`, preserved the
headless shell, and recorded `optical-state=unobserved`. Patch 39 moved
software-owned arm validation before CHEN and physically reached clean frame
progress, then failed only an inactive optional video-shadow equality. Patch 40
removes that invalid acceptance predicate. See
[M9.2 Native Linux Display Ownership](m9-2-native-linux-display-ownership-plan.md).

The later Patch-41 runtime proved that framebuffer bytes, frame counters,
guards, and fast IRQ rearm could remain healthy while the glass still failed.
The user observed a black panel with its backlight enabled, so machine telemetry
did not satisfy the optical gate. Patches 42 and 43 improve visible-epoch and
fault evidence. Patch 44 then derates only ABI v3 to 60-MHz DPI and 1000 Mbps
per lane; ABI v2 remains 80 MHz/1500 Mbps. The clean Patch-44 M7 regression
retained `required-handoff-abi = <2>`, the four legacy MMIO resources, and no
native firmware/cold resources. Its source/object hashes match M9 at
`927530e5c4d2e9162af12b0f57b01b11ce1d7948626a8f2ddac3e8afa2d6cbb6`
and `77ebf40b5901073cbe4bf3d1c64bea8532d1090fc7c0b787298c582b7909a7b0`.
This is build evidence only; Patch 44 remains unflashed.

The completed patch-37 M7 regression retained ABI v2 and passed its 7/37/10
series manifest
`65f4bbb6d94a37ff7e5fb3eacadb1dd7e3bad0a2beb0ee9759caa9e9d39ecc25`,
all 716 shared display-model cases, the audited driver source gate, and the
14-file bFLT W^X audit. Its rebuilt artifacts include the 6,090,800-byte Image
`f27cbcc4a225e6075f2dc295acaf477f29dc289947ada9821d67c1b7fc88f630`
and 2,610-byte ABI-v2 DTB
`42e3ac2fadcbeda59a13ee3cfd4afb490607e13185b7db48b2c3ee1fd00a4fe6`.
This is a build regression result, not a new M7 or M9 physical acceptance run.

The frozen Patch-39 source passed the historical 7/39/10 series checker with 56
patches and manifest
`0f993160af2ce5bb03d791a3708065972764111a7da05a8b5272a03306a9db74`,
the 920-case shared model, and the independent semantic audit. Patch 39's exact
patch SHA-256, commit, post-source SHA-256, and W=1 warnings-as-errors object
SHA-256 are
`d8c774f42019dddeba6020ec019b4fd61474dec3c2913c33b5c2ad191deabe94`,
`99fcff145f84b22c8996e7cab8ce9a77abe472e7`,
`68378f2ca532ec36020a3a99a57c2b8a909b694cb11d613764427acd76c7de0c`,
and `b86cbf98fe2e2d2b894928049d9110cafedb290fbd1b2257285e06143997331a`.
Strict checkpatch is 0/0/0, fuzz-zero application passes, and the independent
audit reports `PATCH39 INDEPENDENT SEMANTIC AUDIT: CLEAR — no blocker.` The
clean Patch-39 M7 ABI-v2 regression passes source contract
`112e8a0afef2f14151a8fcc9e5f8c054e671370d47b8ae042d87a15e52b00ca4`
and the 14-file bFLT W^X audit. Its retained stdout log is
`out/build-logs/m7-patch39-final-rerun-20260812T063011.stdout.log`, SHA-256
`4ccc716b3ca1d9c9de013eb59e852fd0d876d58cd4f222792fce89610242d382`.
The exact Image/DTB/metadata/rootfs results are 6,090,800 B
`cc421483552187e8bfcfe6bd48c601e9f7e5d9a90792b4d49884960912b624fc`,
2,610 B
`42e3ac2fadcbeda59a13ee3cfd4afb490607e13185b7db48b2c3ee1fd00a4fe6`,
128 B
`e602c1e6ea76c82f6ca20efdc16ee6d270b6f1fb52220f193894d101df6f8fe1`,
and 1,795,584 B
`a45407b14685b3ceca45e5dd647d3db98286b0a48cc43128be502b64b2235ffe`;
the payload ends at `0x48a0cd08` below the display pool. This is a build
regression, not new physical acceptance. The fresh Patch-39 M9 full build
passes source contract
`e9c1e789fe87a5a7735c62785ff0113bca61c9d924e964c0a39ce30d69ab5fba`;
retained stdout
`build/patch39-m9-build/m9-full-final-20260812T062855.stdout.log` has SHA-256
`66767316f4a0d528ca1ac036a2066b9ce0569ff597c542b035b91e2538ee6678`.
The no-flash verifier passes with the 246,640-byte loader at
`e314b558d923e8fa0175f9d4eb692ce5728eac3175053ca47c53775a0dbf9104`,
reports `Nothing was flashed`, and retained stdout
`build/patch39-m9-build/m9-noflash-final-20260812T063226.stdout.log` has SHA-256
`773a31cdc8ae7223f3bea628a1cbd7f23e1d9e1be0dea62037e2dd6138010586`.
The seven-artifact audit also passed. Patch 39 then passed exact flash/readback
under `build/m9-readback/20260812T134841478Z-1a4e56fe`; `readback.json` has
SHA-256
`691fd9ac949de313a74c5591870bd3a95769baeae7f8c7be7d22dd429be61c09`.
Its sealed passive snapshot
`out/m9/hardware-runs/20260812T135132Z-snapshot-b5890811aa40-2b712727.log`
has SHA-256
`9f4cb506b955c9e0b2fbfbbaf3927271de3eef40df7b0e1b0a67a3991d3228ac`.
Linux completed four frames (`generation=4 frames=4 rearm=5/0 guards=1`) with
zero host, bridge, GDMA, DMA, or software faults. Exact programmed
`VID_MODE_CFG=0000ff02` was healthy; the optional shadow mirror remained
`active=00000000` because its shadow bank was disabled. That equality alone
made policy false, so containment published `FAILED_QUIESCENT` and retained the
headless shell. The log records `optical-state=unobserved`.

Patch 40 removes exactly the three-line `DSI_HOST_NATIVE_VIDEO_POLICY_ACT`
composite and two-line runtime equality: zero additions and five deletions. It
retains the active-register offset, component masks, snapshot read, and failure
diagnostics, and still requires `VID_MODE_CFG=0000ff02` plus all other 40
predicates. It changes no write, teardown, reveal, or ABI-v2 behavior. Its exact
patch SHA-256, commit, post-source SHA-256, and W=1 object SHA-256 are
`04df043b79760379cc8f4d0d74847d892e3895bc1b55eba3b90af23695647ed1`,
`0fac40ee31c1af165ea94a82a7b0d905d8da4861`,
`13088b8fefe6cf8ca415615e2cf8e9e62a6f3d972d2f010c49965139924a5164`,
and `845e536b031622f29cc8d1e157c1e284e453f8fde8d053a2dba20182368dcdd1`.
The independent audit is clear. The current 7/40/10 checker passes all 57
patches with manifest
`f9318e1a6e7480f1105ec5a435ad71d2754bb6d91a79bcc471747249128ed983`,
and the shared model passes 963 cases. The clean isolated Patch-40 M7 ABI-v2
regression passes source contract
`6b1d5d730c7370f364fed80c507f8ba8454c52b9ee247a22cde96a54233d8206`.
Retained stdout
`out/m7/build-logs/20260812T072708Z-patch40-clean.stdout.log` has SHA-256
`9bddb74e3734cd977bd3b98a28cab29e75a626e6483219ccf88cca418fc47c05`.
Its driver source/object gates match the Patch-40 hashes above, and its 14-file,
128-byte-granule bFLT W^X audit passes. Exact Image, DTB, metadata, and rootfs
SHA-256 values are
`ca34bd104c670427bc7567991056eb9f423cd875290bd238050b384c71454f27`,
`42e3ac2fadcbeda59a13ee3cfd4afb490607e13185b7db48b2c3ee1fd00a4fe6`,
`a1f0c6047b453846831eca90ab6d6675a3933e369fa479d138c4331b97741728`,
and `1721822da26deba72d2952af1d5bade5c9d8b7eeb8d6d6c4616a83c2ff8ba3ae`.
Metadata CRC32 is `901bf054`, and the payload ends at `0x48a0cd08`. This was
build-only: no COM access, reset, or flash occurred, and it is not new physical
acceptance. The fresh M9 build passes source contract
`4f21d0c9e47cc0c003ccbfe9db8c558e18fcf1cf82cceb1e50319661d6ce2686`;
full-build and no-flash stdout SHA-256 values are
`0c15ef3f2c0e40f531b77a12cdf1ec5731272005b2488d96789348bd2319cb6e`
and `4b64a06e8d65391d5fab3182cdf864fb815a455fe06e6be91c0cf9724f7f3e6e`.
The no-flash verifier reports `Nothing was flashed`, and the seven-artifact
audit passes. Patch 40 is unflashed, so its runtime and user-observed visual
validation remain pending.

No loader callback or FreeRTOS task remains active after either handoff. The
historical M7 production path was framebuffer-only. The native ABI-v3 M9.2
runtime also exposes only framebuffer scanout and deliberately omits the VPG
sysfs control.

## Historical M7 ownership transfer

The historical M7 loader performed the controller-specific panel
initialization because the
upstream Linux tree does not contain this ESP32-P4/JD9365 bring-up path. It
leaves DPI/framebuffer mode selected, blanks the backlight, quiesces the
ESP-IDF one-shot transfer, and publishes a four-descriptor ring as a validated
handoff template. I2C is left in a stable hardware state and all software
ownership is dropped; no loader task, callback, or hardware VPG pattern remains
active after the jump.

Linux validates the complete ring, claims the channel, copies the first
descriptor's transfer parameters into the DW-GDMA registers, and starts
hardware reload (`CFG_LO=0x5`). The loader routes GDMA source 24 to CLIC input
18, which Linux receives as IRQ 3.
Each block-done interrupt increments the frame counter and reloads the DSI
bridge raw-word counter. A 50 millisecond timer monitors the bridge underrun
latch and source progress; it does not restart frames. Linux waits for the first
confirmed block-done interrupt before restoring the backlight, so an inactive
bridge is never presented as the panel's blue fallback. Linux is the sole
steady-state controller.

The fbdev write path uses one 512-byte bounce buffer per system call and a
2 microsecond PSRAM-bus gap between bursts. This keeps ordinary writes bounded
without exposing the reserved framebuffer through `mmap()`. Three consecutive
2,048,000-byte writes completed in 0.529 seconds on hardware while frame IRQs
continued, with zero GDMA errors and zero bridge underruns.

A versioned, CRC-protected handoff structure at `0x49f00000` records:

- ABI version and required ownership flags;
- 800x1280 RGB565 geometry and 1,600-byte stride;
- framebuffer address and exact 2,048,000-byte size;
- the 256-byte, four-entry GDMA descriptor ring and channel number;
- a required flag stating that the loader blanked the backlight; and
- backlight I2C address, register, and last brightness.

Linux validates every field, the CRC, all address ranges, each descriptor's
source, DSI FIFO destination, circular link, and valid/last/interrupt bits
before registering the device. A malformed or stale contract fails closed
without exposing a framebuffer.

The historical accepted hardware handoff was:

```text
MICRONUX:M7:DSI-BLANK state=ready backlight=off gate=disabled settle_ms=100 restore=linux-after-status-ready
MICRONUX:M7:DSI-HANDOFF state=ready owner=linux-pending pattern=framebuffer dma=descriptor-ring channel=0 rearm=linux-after-status-ready fb=[48040a80,48234a80) desc=[4ff3ba80,4ff3bb80) i2c=transferred contract=49f00000 crc32=e19656a9
MICRONUX:M7:IRQ source=24 matrix=500d6060 clic=18 handoff=armed
MICRONUX:M7:DSI-LINUX state=ready owner=linux fb=fb0 resolution=800x1280 format=rgb565 dma=ch0:auto-reload event=block-done-irq irq=3 health_poll_ms=50 enable_delay_ms=0 underrun=monitored write_chunk=512 write_gap_us=2 backlight=linux mmap=denied
MICRONUX:M7:DSI-SCANOUT state=ready handoff=blanked-restart stable-frames=4 scanout=hardware-reload-running backlight=restored reveal=userspace-ready frame-ack=disabled clock=forced-hs lp=disabled
```

## Historical M7 Linux interfaces

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

## Boot splash and local status console

The loader renders a dependency-free RGB565 splash directly into the
ESP-IDF-allocated scanout buffer before cache writeback and backlight enable.
It shows `MICRONUX`, `BOOTING LINUX`, and a percentage bar while the kernel
starts. The value is stage-weighted rather than time-based: 10% follows
manifest validation, 20% follows DTB validation, 25–30% covers kernel-buffer
allocation and PSRAM testing, 30–80% is calculated from kernel bytes actually
read from flash, 85–90% brackets SHA-256 verification, 92% follows address-map
validation, and 100% is drawn only after handoff construction and cache
synchronization. There is no artificial boot delay. Each update writes back
only the bar and percentage rows using ESP-IDF's unaligned cache-sync path.
The splash has no decoder, filesystem, task, or callback dependency and is
part of the same framebuffer handed to Linux.

After storage and the C6-backed network interface have been probed, `/init`
writes a compact status page to `/dev/tty1`. It reports the fixed display
mode, storage and network presence, `SYSTEM READY`, and the USB shell name.
The kernel command line intentionally keeps only `ttyGS0` as its logging and
interactive console; this prevents boot-log scrolling from competing with
DSI scanout while preserving the visible tty1 status page. The accepted live
health sample after rendering the page was:

```text
running frames=3812->3816 error=00000000 underruns=0 chen=1 faults=0 host-errors=00000000:00000000 frame-ack=off clock=forced-hs lp=disabled
  3:       3818  RISC-V INTC  18 Edge      500a0000.display
63
```

The corresponding acceptance markers are:

```text
MICRONUX:M7:SPLASH state=ready title=MICRONUX resolution=800x1280 format=rgb565 progress=0 mode=staged
MICRONUX:M7:SPLASH progress=100 state=visible
MICRONUX:M7:FB-CONSOLE state=ready tty=tty1 role=status usb=ttyGS0 reveal=userspace-ready cursor=steady
```

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

The 2026-08-10 accepted Linux artifacts and current physically gated loader
are:

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| Linux `Image` | 6,025,008 B | `7ab85bdc6a0a7761db2f326c5798b697206ec11952b109cac2925589663ab776` |
| Device tree | 2,453 B | `3c31c2d81ad6c2a4017d20fc8364732eb829097291150a4894609b383f39a703` |
| Metadata | 128 B | `d747b1bea5585ecdac07a88ec6ec6a08f891a62c945a329b4783358a6c3756a6` |
| ESP-IDF loader | 281,152 B | `61bec5aadf74bab502a3f73ae14dbe8ef74c67cbc0a3fba2633458db6e9beb36` |

The Linux image leaves 266,448 bytes in the fixed 6 MiB partition. The bFLT
W^X audit passed all 14 userspace executables. The staged-progress loader
passed one complete physical M7 gate, including the mandatory visible 100%
marker and zero display underruns. The previously accepted three-boot workload
returned identical arena accounting and general memory:

```text
M7 arena boot 1/3 passed: reserved=472 mapped=402 free=1576 arenas=5 mem_kib=8176->8176
M7 arena boot 2/3 passed: reserved=472 mapped=402 free=1576 arenas=5 mem_kib=8176->8176
M7 arena boot 3/3 passed: reserved=472 mapped=402 free=1576 arenas=5 mem_kib=8176->8176
```

Each boot also passed controlled SD writes, C6 association/DHCP and external
ping, paced framebuffer writes with zero underruns, rejection of unsafe
pattern switching, brightness change/restore, 19 U-mode
fault cases, W^X, arena reuse, supervisor admission, timeout/output limits,
and post-fault device-service liveness.
