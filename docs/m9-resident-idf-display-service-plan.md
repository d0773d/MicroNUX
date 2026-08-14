# M9 Resident ESP-IDF Display Service Plan

Status: **PROPOSED — REVIEW REQUIRED; DO NOT IMPLEMENT YET**

## Decision

Keep the proven ESP-IDF v6.0.1 display pipeline alive for the entire device
runtime. Linux remains the application operating system and framebuffer
producer, but it no longer programs the JD9365, MIPI D-PHY, DSI host, DSI
bridge, or display DW-GDMA channel.

The intended ownership model is:

```text
Linux userspace / fbcon / later LVGL
                 |
                 v
       Linux framebuffer producer
                 |
        shared-memory frame mailbox
                 |
                 v
 resident ESP-IDF v6 display service
                 |
       ESP-IDF DPI + DW-GDMA driver
                 |
       DSI bridge / host / D-PHY
                 |
              JD9365
```

The governing rule is:

> Linux may deliver a new frame late, or not at all, but the resident ESP-IDF
> service must continue refreshing the last accepted frame.

## Why this architecture

The Linux-free baseline has already demonstrated the exact hardware profile:

- ESP32-P4 revision 1.3/ECO2;
- JD9365, 800x1280 RGB565;
- 80 MHz DPI clock;
- two DSI lanes at 1500 Mbps per lane;
- LDO channel 3 at 2.5 V; and
- ESP-IDF-owned continuous scanout without a crash or boot loop through the
  observed five-minute interval.

The custom Linux driver successfully initialized and refreshed one-shot
frames, but its experimental transition to direct continuous reload observed
the display DMA channel disabled and deliberately entered fail-dark state.
The resident-service design removes that custom transition. It retains the
same ESP-IDF descriptor, callback, cache, and rearm lifecycle used by the
working Waveshare/YamUI family of implementations.

This is not an admission that Linux cannot render graphics. Linux remains the
source of content. It stops being the owner of the timing-critical scanout
engine.

## Mandatory implementation branch

No implementation work begins on the current branch.

After this plan is approved, create a new branch before changing code:

```text
codex/m9-resident-idf-display-service
```

The branch must start from a recorded, clean commit that contains the accepted
Linux-free ESP-IDF display baseline and its build/flash tooling. Existing
experimental Linux continuous-reload patches remain historical evidence and
must not silently become the new base.

## CPU and runtime boundary

The ESP32-P4 has two HP RISC-V cores. The proposed first implementation uses:

| Resource | Owner |
| --- | --- |
| HP core 0 | Linux, initially single-core as today |
| HP core 1 | Resident ESP-IDF display service |
| DSI host, bridge, D-PHY, display DW-GDMA | ESP-IDF display service only |
| JD9365 command channel and display backlight control | ESP-IDF display service only |
| Linux framebuffer producer and user-facing graphics ABI | Linux |
| Shared frame pool and mailbox | Explicitly shared by contract |
| microSD slot 0 | Disabled until display acceptance completes |
| ESP32-C6 SDIO | Disabled during coexistence bring-up; restored separately |

The chip also has a third, low-power RISC-V core. It is a separate 40 MHz LP
subsystem processor, not a third interchangeable HP core. The MIPI-DSI,
display DW-GDMA, high-speed PSRAM/cache path, and the existing ESP-IDF DPI
driver belong to the HP system. The first resident-display implementation
therefore does not move the display service onto the LP core. The LP core
remains available for a later, independently scoped low-power monitor or
watchdog role after its memory, interrupt, and mailbox boundaries are proven.

Running current Linux on one HP core is not identified as the cause of the
continuous-transition failure. The same Linux core completed hundreds of
one-shot frames and rendered the status page before the direct-reload
transition. The failure occurred when the display channel was reprogrammed:
the first qualification observed the channel disabled, no generation advance,
and no reported DMA/DSI/bridge error. A second Linux HP core could improve
general scheduling throughput, but it would not correct an invalid or
unaccepted DW-GDMA reload transaction. Earlier CPU-rearmed scanout could be
more sensitive to single-core scheduling delays; the resident ESP-IDF service
removes that per-frame Linux deadline instead of relying on Linux SMP to meet
it.

This dual-runtime arrangement is not assumed safe merely because two cores
exist. ESP-IDF normally controls chip-wide interrupt, clock, cache, timer, and
scheduler facilities. Segment 1 must prove that the pinned ESP-IDF v6.0.1
runtime can be confined to core 1 while Linux boots on core 0. If it cannot be
confined without shared-global conflicts, stop and redesign the service as a
smaller IDF-derived resident monitor. Do not hide the failure with register
workarounds.

Linux must not bring the second HP core online while the resident service owns
it. SMP remains disabled until this architecture is deliberately replaced.

## Display and memory contract

### Buffers

Use three full RGB565 buffers in a reserved PSRAM pool:

- one `front` buffer continuously scanned by ESP-IDF;
- one Linux `render` buffer;
- one `spare` buffer available for the next ownership exchange.

Each frame is exactly:

```text
800 * 1280 * 2 = 2,048,000 bytes
```

Exact addresses are not frozen by this plan. Segment 2 must derive them from
the final Linux Image/DTB/reservation map, prove alignment, prove that all
three frames fit, and prove they do not overlap the kernel, DTB, resident
service state, descriptors, mailbox, or general Linux memory.

Linux must never write the current ESP-IDF front buffer. ESP-IDF must never
promote a buffer until Linux has completed rendering and cache publication.

### Cache ownership

For every submitted frame:

1. Linux renders only into its current render buffer.
2. Linux completes the required cache writeback for the modified range.
3. Linux publishes the request fields.
4. Linux executes a release barrier.
5. Linux increments the request generation as the final request write.
6. ESP-IDF observes the generation with an acquire operation.
7. ESP-IDF validates the complete request before using the frame.
8. ESP-IDF promotes the buffer only at a driver-supported frame boundary.
9. ESP-IDF publishes completion generation and the new buffer roles.

The first implementation uses full-frame cache synchronization. Dirty-region
optimization is a later performance segment, not part of initial correctness.

## Mailbox ABI

Create a versioned, fixed-size shared-memory ABI with compile-time offset and
size assertions in both environments. Use fixed-width little-endian fields and
no pointers.

Minimum header:

```text
magic
ABI version
structure size
feature flags
service state
fault code
boot generation
CRC or integrity field for immutable boot contract
```

Minimum frame request:

```text
request generation
buffer index
physical framebuffer address
byte length
stride
pixel format
damage rectangle (full frame in the first version)
content sequence
```

Minimum completion/status:

```text
accepted generation
presented generation
front/render/spare buffer indexes
refresh counter
last frame-boundary timestamp
rejected-request count and reason
DSI/DMA/bridge/panel fault counters
heartbeat
```

Only generations are ownership commit points. A partially written request is
ignored. Unknown ABI versions, sizes, flags, addresses, formats, or buffer
roles are rejected while the last valid front continues refreshing.

The mailbox does not contain arbitrary register-write commands. Linux cannot
use it to program the DSI, panel, backlight, clocks, or GDMA.

## Notification mechanism

Start with correctness over latency:

1. Linux publishes a request in shared memory.
2. Linux sends one dedicated inter-core notification.
3. The resident service validates and queues the request.
4. ESP-IDF performs the swap using its supported DPI-panel callback or buffer
   operation.
5. The resident service publishes completion and optionally notifies Linux.

The exact ESP32-P4 inter-core interrupt source and routing must be selected
from the pinned ESP-IDF v6.0.1 source and revision-1.3 interrupt map. Polling
may be used only for the earliest coexistence proof. Production refresh must
not depend on mailbox polling cadence, and a lost notification must be
recoverable by comparing generations.

## Failure behavior

### Linux failure or delay

The resident service repeats the last accepted front forever. Linux restart,
userspace crash, scheduler delay, SDIO timeout, or a malformed request must not
stop scanout.

### Invalid frame request

Reject it, increment a reason-specific counter, leave buffer roles unchanged,
and continue showing the last valid frame.

### Resident display fault

Record the exact error class and preserve a frozen diagnostic record. Attempt
only bounded recovery supported by the ESP-IDF driver. If safe recovery is not
possible, command PWM zero and disable the backlight gate. Never expose the
panel's cyan underflow filler as a normal fallback.

### Linux boot failure

The resident service retains a deterministic ESP-IDF status page stating that
Linux has not connected. The display remains useful for boot diagnosis.

### Watchdog and heartbeat

Linux monitors the service heartbeat but does not reset the display engine on
one missed sample. The resident service monitors request progress without
requiring Linux activity. Watchdog policy must be explicitly partitioned so
one runtime cannot reset a healthy peer without a recorded reason.

## Segmented implementation

Each segment changes one architectural property. A segment becomes the next
baseline only after its own static, machine, and optical gates pass. Never
stack a proposed fix on a failed segment.

### Segment 0 — Preserve the standalone reference

Goal: make the current ESP-IDF-only success reproducible.

- commit the standalone profile, script, documentation, and exact hashes;
- repeat a guarded flash/readback;
- observe hardware color bars followed by the status/burn-in page;
- run connected and terminal-closed observations;
- require at least 60 minutes continuously visible with heartbeat progress;
- preserve a known-good recovery image and flash command.

Exit gate: ESP-IDF-only display is an accepted reference, not merely a
five-minute machine-state result.

### Segment 1 — Prove dual-runtime coexistence with no shared frames

Goal: boot Linux on core 0 while ESP-IDF continues showing its own immutable
status page on core 1.

- pin the resident display task, display ISR, tick/timer support, and required
  callbacks to core 1;
- reserve core 1 from Linux and keep Linux SMP disabled;
- define which chip-global clocks, resets, interrupt routes, timers, caches,
  and memory allocators remain owned by the resident runtime;
- prevent Linux drivers from probing the DSI, bridge, D-PHY, display GDMA,
  panel I2C device, or backlight;
- boot Linux without giving it a framebuffer device yet;
- keep microSD and C6 SDIO disabled;
- prove Linux shell activity and CPU stress do not affect the immutable ESP-IDF
  status page.

Exit gate: Linux remains functional while the ESP-IDF status page is
continuously visible for 60 minutes, with zero resets, underruns, display
faults, or cross-runtime ownership violations.

### Segment 2 — Add read-only mailbox and heartbeat

Goal: prove communication without changing the displayed frame.

- reserve and publish the mailbox region in loader handoff and DT;
- add matching C ABI definitions and host-side layout tests;
- Linux reads service state, heartbeat, refresh count, and immutable display
  facts;
- Linux sends a `PING` generation that the service acknowledges;
- reject malformed generations, flags, and addresses in synthetic tests;
- do not permit frame submission yet.

Exit gate: generations remain monotonic during a 60-minute Linux stress run;
display content remains the resident service's immutable page.

### Segment 3 — Submit one fixed Linux frame

Goal: display one Linux-rendered frame without transferring hardware ownership.

- reserve and validate the three-buffer PSRAM pool;
- expose one Linux render buffer through a minimal framebuffer producer;
- render the deterministic MicroNUX status page;
- perform full-frame cache publication;
- submit one request and wait for accepted/presented generations;
- ESP-IDF swaps to the submitted buffer at a supported frame boundary;
- freeze updates after the first presentation.

Exit gate: exact expected status image appears and remains visible for 60
minutes. If Linux is stopped after submission, the same frame remains visible.

### Segment 4 — Controlled frame updates

Goal: introduce buffer rotation without tearing or scanout interruption.

- enforce explicit front/render/spare roles;
- submit a known sequence: status page, color bars, status page;
- reject reuse of a buffer still owned by ESP-IDF;
- test late, duplicate, skipped, and malformed generations;
- if Linux misses an update deadline, repeat the existing front;
- keep updates low-frequency until correctness is accepted.

Exit gate: at least 1,000 controlled swaps, zero role violations, zero tearing,
zero black/cyan fallback, and correct final-frame persistence.

### Segment 5 — Linux framebuffer and fbcon integration

Goal: make ordinary Linux console rendering use the service protocol.

- register a Linux framebuffer whose backing storage is the render buffer, not
  the live front;
- coalesce writes and submit bounded updates;
- keep console rendering out of interrupt context;
- expose mailbox/service health through read-only sysfs;
- preserve a recovery route when the service reports unavailable.

Exit gate: Linux boots to the status console, interactive terminal output
updates correctly, and closing either COM port does not affect refresh.

### Segment 6 — LVGL userspace

Goal: add LVGL above the Linux framebuffer/update ABI.

- LVGL remains a userspace library, not a kernel component;
- render into the Linux-owned back buffer;
- submit completed updates through the same ABI as fbcon;
- begin with full-frame submissions;
- add dirty rectangles only after full-frame correctness and bandwidth are
  measured.

Exit gate: UI updates, touch input, console switching, and last-frame
persistence pass without changing low-level display ownership.

### Segment 7 — Restore other peripherals independently

Restore one subsystem at a time:

1. C6 SDIO;
2. networking workloads;
3. microSD, only after its timeout behavior is corrected;
4. storage and network stress together.

Each restoration repeats the autonomous 600-second and one-hour optical gates.
No display fix is bundled with an MMC or networking fix.

## Required validation

### Static and build gates

- pinned ESP-IDF v6.0.1 commit and ESP32-P4 revision-1.3 headers;
- exact Waveshare JD9365 component version and panel program;
- no Linux access to resident-owned MMIO ranges;
- no resident access outside its memory/MMIO contract;
- mailbox size, offsets, alignment, endian, and barriers tested in both builds;
- clean ESP-IDF build with warnings treated as errors for project sources;
- clean M9 Linux build and retained M7 ABI-v2 regression where applicable;
- exact artifact hashes and guarded flash/readback.

### Machine gates

- independent heartbeats from Linux and the resident service;
- monotonic refresh and mailbox generations;
- zero DSI host, bridge, DMA, cache, guard, or ownership faults;
- no ROM reset banner, panic, watchdog loop, or unexpected core restart;
- Linux continues scheduling while the service refreshes;
- service continues refreshing while Linux is deliberately stalled.

### Optical gates

Machine counters never replace visual acceptance. For each segment record:

- expected frame;
- black;
- cyan;
- flicker;
- tearing;
- stale but valid prior frame; or
- panel/backlight off.

Required sequence before broader integration:

1. fresh boot visual sequence;
2. 10-minute connected observation;
3. 10-minute terminal-closed observation;
4. one-hour unattended observation;
5. controlled color bars to status-page transition;
6. Linux CPU and PSRAM workload;
7. only then peripheral restoration.

## Security and isolation

- Linux userspace cannot map the mailbox control header writable except through
  the display driver.
- Linux userspace may map only the current render buffer, never the live front
  or resident code/state.
- The resident service validates every address against the fixed frame pool.
- No mailbox operation accepts arbitrary physical addresses or MMIO writes.
- PMP and Linux memory reservations must prevent accidental allocation over
  resident code, stacks, descriptors, buffers, or mailbox.
- A future secure design may authenticate resident firmware, but this plan
  does not change secure-boot or flash-encryption eFuses.

## Rollback

Maintain two independently flashable artifacts throughout implementation:

1. the accepted standalone ESP-IDF display baseline; and
2. the last accepted Linux MicroNUX image.

Every experimental flash records exact hashes and offsets. If a segment fails,
restore the immediately preceding accepted artifact. Do not modify eFuses,
boot security state, or factory calibration data.

## Explicit non-goals

- Do not port the Linux display register implementation into another custom
  bare-metal driver.
- Do not compile LVGL into the Linux kernel.
- Do not enable Linux SMP while core 1 hosts the service.
- Do not give Linux emergency register-write access to the resident display.
- Do not optimize dirty rectangles, DMA2D, or PPA before fixed-frame exchange
  is correct.
- Do not re-enable microSD during initial coexistence work.
- Do not claim success from UART or mailbox counters without visual evidence.

## Review decisions required before implementation

The reviewer should approve or change these points:

1. Dedicate HP core 1 to the resident ESP-IDF display service and keep Linux on
   HP core 0 with SMP disabled.
2. Keep DSI, display GDMA, JD9365 control, and backlight permanently outside
   Linux ownership.
3. Use three shared PSRAM frame buffers with explicit role ownership.
4. Use a versioned shared-memory mailbox plus recoverable inter-core
   notification.
5. Keep microSD and C6 SDIO disabled through Segments 0-4.
6. Require optical acceptance at every segment.
7. Create `codex/m9-resident-idf-display-service` only after approval, before
   any implementation edits.

## Reference sources

- [Waveshare ESP32-P4-Module-DEV-KIT documentation](https://docs.waveshare.com/ESP32-P4-Module-DEV-KIT)
- [Waveshare ESP32-P4-Platform repository](https://github.com/waveshareteam/ESP32-P4-Platform)
- [ESP-IDF v6 ESP32-P4 MIPI-DSI documentation](https://docs.espressif.com/projects/esp-idf/en/v6.0/esp32p4/api-reference/peripherals/lcd/dsi_lcd.html)
- [YamUI Waveshare display wrapper at the frozen reference commit](https://github.com/d0773d/yamui-device/blob/de1b723b9fc237e63e96fc82ce2975aa8b1a2ad4/components/kc_touch_display/src/kc_touch_display_waveshare_p4.c)
- Local pinned ESP-IDF v6.0.1 source at commit
  `8c19b156084a0753687347cca1f5355782893533`
