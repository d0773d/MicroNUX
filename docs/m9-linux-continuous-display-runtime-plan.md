# M9 Linux Continuous Display Runtime Plan

Status: **ACTIVE - PATCH47 FIXED-FRONT RELOAD BUILT, READY FOR FLASH TEST**
Owner: Linux remains the sole runtime display owner
Baseline: Patch45, M9 microSD slot 0 disabled, C6 SDIO slot 1 retained
Last physical checkpoint: status page remained visible for 19 minutes, then two
10-second color-bar tests passed and restored the status page correctly

## Segmented restart (current work)

The earlier Patch46-Patch50 continuous-refresh experiment is historical
evidence only. It is not the base for new work. The device was restored from
the exact Patch45 readback, and all new segments start from that accepted
one-shot image.

Each segment changes one behavior and becomes the next baseline only after its
own build, readback, machine, and optical checks pass. A failed segment is
rolled back immediately; fixes are not stacked onto a failed image.

1. **Patch45 baseline - accepted.** CPU one-shot rearm remains active, M9
   microSD remains disabled, the status screen is stable, and two color-bar to
   status transitions passed.
2. **Patch46 reload-plan preparation - accepted.** The exact direct-register
   transaction was validated without GDMA writes. Hardware reported
   `CONTINUOUS-PREP state=VALIDATED active-scanout=one-shot
   hardware-writes=0`, and the status page remained visible.
3. **Patch47 continuous fixed-front enable - build validation complete.** The
   validated plan is applied while dark, four autonomous SAR wraps are required
   before reveal, block-done/CPU rearm are removed, and one immutable status
   front repeats in hardware. Clean M9 and isolated M7/ABI-v2 regressions pass;
   flashing and physical testing remain.
4. **Frame-boundary switching - pending Patch47 optical acceptance.** Add one controlled buffer
   switch without weakening repeat-last-frame behavior.
5. **Runtime workloads - pending fixed-front acceptance.** Add CPU, network,
   C6 SDIO, and
   storage load separately. M9 microSD remains disabled throughout this
   investigation.

Patch47 has an explicit non-goal: it does not add runtime page flips or mutable
front-buffer rendering. Those remain closed until fixed-front refresh passes
the connected and autonomous optical gates.

## Historical Patch46-Patch50 experiment

Patch46 was flashed and all four regions read back exactly in
`build/m9-readback/20260812T235843834Z-8fe7a1f5`. The first ABI-v3 boot then
rejected the post-I2C continuous runtime policy with `-EIO` before backlight
reveal and entered `FAILED_QUIESCENT`. This is a successful fail-dark safety
response, not a runtime display pass. Patch47 adds read-only diagnostics on
that already-failing branch; it does not relax policy or change register
programming.

## Objective

Keep the last valid frame continuously visible even when Linux userspace,
storage, networking, USB, or another kernel path is delayed.

The governing rule is:

> Late work may delay a visual update, but it must never interrupt refresh of
> the last valid frame.

Linux continues to own the framebuffer, DSI host, DSI bridge, GDMA channel,
panel controls, backlight, interrupts, clocks, and fault policy. LVGL remains a
separate userspace library and application layer; it is not linked into the
kernel.

## Current architecture and observed weakness

The current ABI-v3 driver uses one full-frame GDMA descriptor. At every frame
completion, GDMA stops and the Linux interrupt handler publishes the completed
front buffer, rewrites the descriptor and LLP, and enables the channel again.

```text
current frame -> completion IRQ -> Linux rearm -> next frame
```

This design has a per-frame service deadline. Long shared-bus transactions or
late source delivery can empty the DSI bridge FIFO. The bridge then emits its
fallback pixel value. Earlier firmware used the reset fallback value, which
appeared light cyan; the current driver programs black fallback so starvation
fails dark instead.

Evidence collected before this plan:

- framebuffer contents and guard words remained correct during failures;
- display rearm work measured in microseconds, but some arm-to-completion gaps
  exceeded 100 ms;
- microSD and C6 SDIO paths produced roughly 100 ms MMC timeouts;
- disabling only M9 microSD slot 0 produced an unattended status-screen run of
  more than one hour;
- the retained C6 SDIO slot still produced a 100 ms timeout during probe;
- Patch44 retains the conservative 60 MHz pixel clock, 1000 Mbps DSI lanes,
  RGB565, PSRAM XIP/cache configuration, black filler, and telemetry.

The evidence points to bandwidth or transaction-latency contention, not bad
RAM, framebuffer corruption, or a conventional buffer overflow. Continuous
refresh removes the CPU rearm deadline, but it does not by itself create more
PSRAM bandwidth. Both dimensions remain measurable acceptance concerns.

## Runtime model

The MicroNUX kernel is single-core, `SMP=n`, `PREEMPT_NONE`, and `HZ=100`.
Multiple processes run through scheduler time slicing, while DMA engines and
peripherals operate concurrently with the CPU. A particular driver can still
perform long synchronous work, and concurrent masters can contend for PSRAM or
the shared interconnect.

Continuous display refresh must therefore be autonomous in GDMA. Linux should
only prepare new content and request a frame-boundary switch. If Linux is late,
hardware must continue scanning out the existing front buffer.

## Non-goals and fixed boundaries

- Do not move permanent display ownership into the loader or a second core.
- Do not compile LVGL into the kernel.
- Do not re-enable M9 microSD while the display A/B investigation is active.
- Do not change M7 ABI-v2 display or two-slot MMC behavior.
- Do not combine initial continuous-refresh work with page flips, dirty-region
  rendering, MMC repair, scheduler policy, or undocumented QoS writes.
- Do not claim a visual pass from counters alone.

## Segmented implementation

### Segment 1 - Continuous fixed-front refresh

Status: **PRODUCT PATCH PASSES SOURCE, MODEL, COMPILE, M9/M7 BUILD, AND
NO-FLASH GATES**

Goal: make GDMA continuously repeat one prevalidated front buffer without a
per-frame CPU rearm. No page flip is permitted in this segment.

Implementation boundary:

1. Preserve the existing cold setup, black-buffer verification, DSI policy,
   bridge setup, source guards, backlight sequencing, and fail-dark handling.
2. Configure direct-register source and destination `RELOAD` mode for the
   selected fixed front buffer. Do not use a descriptor ring.
3. Validate the complete topology before enabling the channel.
4. Disable block-done generation and signaling. A DW-GDMA auto-reload channel
   stalls between blocks when block completion is enabled until software
   clears the event, which would recreate the deadline this segment removes.
5. Remove recurring descriptor/LLP/CHEN writes and normal per-frame completion
   handling. Retain only error/ECC/common interrupt signaling.
6. Treat any unexpected channel stop, descriptor error, DSI error, guard
   failure, or underrun as a contained display fault.
7. Expose an exact `scanout-mode=continuous-fixed-front` runtime token.

Proven controller design:

- use DW-GDMA source and destination `RELOAD` multiblock mode, encoded as
  channel `CFG_LO=0x00000005`;
- program the selected front buffer into the channel SAR, the DSI FIFO into
  DAR, and retain the verified block size and transfer-control fields;
- clear the transfer-control interrupt-on-block bit and remove block-done from
  both status-enable and signal-enable masks, so block boundaries cannot wait
  for Linux;
- perform the one-shot-to-reload transition only after the userspace status
  commit and four-frame same-front requalification have succeeded, while the
  backlight is still at PWM zero and before the reveal control write;
- stop rendering/page-flip publication for this segment after the fixed front
  is selected.

Progress monitoring must not participate in refresh:

- ESP32-P4 rev-1.3 exposes no DSI-bridge VSYNC interrupt; its bridge interrupt
  bank contains only FIFO underrun on this silicon revision;
- exact per-frame IRQ counters are therefore unavailable in autonomous mode;
- while the display is still dark, bounded high-rate polling must prove several
  SAR end-to-start wraps with CHEN asserted and all error registers clean;
- during runtime, the existing watchdog samples SAR, completed-block size,
  CHEN, FIFO depth, and fault registers. Multiple unchanged samples constitute
  a contained source-progress failure, but the watchdog never writes the
  normal refresh path;
- telemetry reports `refresh-progress` observations rather than pretending a
  timer sample is an exact frame-completion count.

Why a descriptor ring is explicitly rejected for Segment 1:

- ESP-IDF v6.0.1 contains an official one-item circular DW-GDMA test;
- hardware consumes the descriptor valid marker on its first traversal;
- the next traversal raises the invalid-block event unless software restores
  ownership and resumes the channel;
- therefore a circular link alone still has a CPU service deadline and does
  not satisfy the objective.

Primary local evidence is pinned to ESP-IDF v6.0.1 commit
`8c19b156084a0753687347cca1f5355782893533`:

- `components/esp_driver_dma/test_apps/dma/main/test_dw_gdma.c` proves three
  successive autonomous block-done events in `RELOAD` mode;
- the same test proves the one-item circular-list invalid-block behavior;
- `components/esp_hal_dma/include/hal/dw_gdma_types.h` defines reload as the
  controller's automatic transfer-configuration reload mode;
- `components/esp_driver_dma/src/dw_gdma.c` shows the exact source/destination
  multiblock programming used by the official driver.

The Synopsys DW_axi_dmac auto-reload contract adds an essential constraint:
when block completion is enabled, auto-reload pauses at the block boundary
until software clears the completion event. Segment 1 therefore uses reload
with block completion disabled, not the interrupt-enabled form used by the
ESP-IDF functional test. ESP32-P4 error interrupts remain enabled.

Research references:

- [Linux DW AXI DMAC register definitions](https://github.com/torvalds/linux/blob/master/drivers/dma/dw-axi-dmac/dw-axi-dmac.h)
- [Synopsys DW_axi_dmac databook mirror](https://picture.iczhiku.com/resource/eetop/wyKEEOUFJDggQnvB.pdf), section 2.13.2.2, "Auto Reloading"
- local ESP-IDF `components/soc/esp32p4/register/hw_ver1/soc/` headers for the
  exact rev-1.3 GDMA and DSI-bridge register layout

Verification:

- official/local ESP-IDF v6.0.1 GDMA descriptor semantics cited in the patch;
- exact patch apply and reverse-apply with zero fuzz;
- strict checkpatch with zero findings;
- RISC-V `W=1 KCFLAGS=-Werror` object build;
- static model proves normal refresh has no IRQ dependency, block-done is
  disabled, and the IRQ contains no recurring rearm writes;
- clean M9 build and exact flash/readback;
- fixed status frame remains continuously visible for at least 600 seconds;
- dark qualification proves at least four autonomous SAR wraps before reveal;
- runtime watchdog proves continuing source progress without writing refresh;
- zero display faults, underruns, guard errors, and unexpected channel stops;
- M9 microSD remains disabled and C6 SDIO remains independently observable;
- clean M7 ABI-v2 regression.

Rollback: restore the Patch45 one-shot implementation and its known telemetry.

Current incremental Patch47 evidence:

- isolated repository: `.tmp-m9-segment3-fixed-front`;
- commit: `2130ef8a261a3a3518df8955c04b82446cab732f`;
- product patch:
  `buildroot-external/board/micronux/patches-peripherals/linux/0047-video-fbdev-start-native-fixed-front-hardware-reload.patch`;
- product patch SHA-256:
  `933fb7ee65fb78e8a53593fee5546724773a608b8dc52cf9b33133d22a433dcc`;
- postimage source SHA-256:
  `857199c3dd121df000981366c11b364605edb3f0c8ca5360a2211f2a5335953f`;
- standalone RISC-V object SHA-256:
  `12e829ec1a1855c4de86614968b88e67a6542b7aa885d47e1b7bbfafd802ab37`;
- strict Linux checkpatch passes with zero errors, warnings, or checks;
- direct and fuzz-zero reconstructed postimages are byte-identical and compile
  to the same object;
- patch-series checker passes platform 8, peripherals 47, isolation 10,
  total 65, manifest
  `b0e70897d82ba8dc67232c44fa59e1065c6f4dbeda0b0f74d06902f2d963a4f9`;
- the display contract model passes 1,216 tests with
  `rearm=hardware-reload frame-irq=errors-only`;
- the synthetic telemetry parser passes 49 cases using SAR/progress samples;
- clean M9 build and Windows no-flash verification pass with source contract
  `ddf3a022b5f6b344a2776ac3ab8a02f759fb7081a0e412aa95870842cf52e010`;
- the packaged M9 Image is 6,164,592 bytes with SHA-256
  `ef591e8e0a6597c0b0389e50019756684977bddd0b7edfc367685a0ec305d488`,
  and all seven entries in `out/m9/SHA256SUMS` rehash exactly;
- the built M9 driver source/object match the frozen evidence at
  `857199c3dd121df000981366c11b364605edb3f0c8ca5360a2211f2a5335953f`
  and
  `12e829ec1a1855c4de86614968b88e67a6542b7aa885d47e1b7bbfafd802ab37`;
- the pinned ESP-IDF v6.0.1 loader verifier passes with a 233,952-byte loader,
  SHA-256
  `8b566ef86a46fd36fe970e3ca120b24c0b44a1677bb625bc9d5a63413cb3f7bc`,
  and explicitly reports that nothing was flashed;
- accepted M9 build stdout/stderr SHA-256 values are
  `5e0749d2ccda5f1f3136a36eb337fa66eb63240c5b5c2e37d2ab73d21d05fd7e`
  and
  `56e0c3802f1c68113547697b8f6b02ee7f02269206db7aedfb3b52dedcf3464f`;
- a clean M7 build in a new work directory passes its ABI-v2 gates and
  14-file bFLT W^X audit with source contract
  `8c8bab429c0534c8065d2e9a178f563050c43768e0e86b213b65ab0747282eb2`;
- the M7 build produced the same frozen driver source/object hashes as M9,
  all 18 entries in `out/m7/SHA256SUMS` rehash exactly, and its retained
  stdout/stderr SHA-256 values are
  `878b56cc94ba9630cbcba36fcc1e9db473e9043690b2408db2e8d70ca34a766a`
  and
  `c775fd331d13ccbaf1621fd123d3d1fd58ea78ca0a7bfd1d660e1ce54110add8`;
- flash/readback, runtime, and optical evidence remain pending.

Historical all-at-once prototype evidence (not the Patch47 product base):

- repository: `.tmp-m9-patch46-continuous-reload`;
- commit: `6a6422c56c5f28254cb0d905dd16a2ac31c96c06`;
- tree: `6ff6c099b3e6923e1ee73fe9f4401919de899079`;
- postimage source SHA-256:
  `8b368cdf21d82242fc735d1ac114120cc04bc58b6f9c91bdab6354c2180bd3f4`;
- product patch:
  `buildroot-external/board/micronux/patches-peripherals/linux/0046-video-fbdev-refresh-fixed-native-front-autonomously.patch`;
- product patch SHA-256:
  `3717f59104711643b47a5b6448806f40f4c4e36bf7d95867951b21aa2c83daf8`;
- standalone object SHA-256:
  `96db45f57f26a0a068f0acbdacd9d12368ef6e81fa546a60d8f0fab5d1e468b8`;
- exact kernel cross-compile with `W=1` and `-Werror` passed without modifying
  the accepted M9 build tree;
- strict Linux checkpatch passed with zero errors, warnings, or checks.
- patch-series checker passes platform 7, peripherals 46, isolation 10,
  total 63, manifest
  `ba26995b1ad0fec21574ec0de4727e358928fde0a9f51271b2446efa2bfd1536`;
- the display contract model passes 1,183 tests and reports
  `rearm=hardware-reload frame-irq=error-only`;
- the synthetic runtime telemetry parser passes 49 cases using SAR progress
  rather than the retired per-frame completion counter.
- the clean M9 Linux build and the supported Windows no-flash verifier pass
  with source contract
  `3c1f397abe4eb7c1abf392373e43779718c683309dd36a9ea0c477f3775048c4`;
- the packaged M9 Image is 6,164,592 bytes with SHA-256
  `00918368b101d8fe856d313ea621ee2433e2f2cdc9f36cebd355f99815dc647c`,
  and all seven entries in `out/m9/SHA256SUMS` rehash exactly;
- the built M9 driver source/object match the frozen evidence at
  `8b368cdf21d82242fc735d1ac114120cc04bc58b6f9c91bdab6354c2180bd3f4`
  and
  `96db45f57f26a0a068f0acbdacd9d12368ef6e81fa546a60d8f0fab5d1e468b8`;
- the pinned ESP-IDF v6.0.1 loader verifier passes with a 233,952-byte loader,
  SHA-256
  `260442c5d3d53e3e91a2eacc27fe3391e7ddd8d3dbf6dc3c756968f0d2a071c8`,
  and explicitly reports that nothing was flashed;
- a clean M7 build in a new work directory passes its ABI-v2 gates and
  14-file bFLT W^X audit with source contract
  `2d86453d07332694467923528ca0d6c04c834e65809d4a5fb73963083979d595`;
- the M7 build produced the same frozen driver source/object hashes as M9 and
  all 18 entries in `out/m7/SHA256SUMS` rehash exactly.

This evidence clears product integration, static review, clean M9/M7 builds,
and the pre-flash artifact gate. It does not claim runtime or optical success;
those gates begin only after exact flash/readback.

### Segment 2 — Safe frame-boundary switching

Status: **PENDING SEGMENT 1**

Goal: add double/triple-buffer updates without interrupting continuous refresh.

- userspace renders only into a non-front buffer;
- Linux validates and cache-synchronizes the completed buffer;
- a frame-boundary operation changes the next hardware-visible front buffer;
- if no valid buffer is queued, hardware repeats the current front forever;
- a late or invalid update is dropped or deferred, never shown partially;
- completed/front/render/spare roles and commit generations remain explicit;
- no buffer is reused until hardware ownership has ended.

Acceptance adds repeated flips, unchanged-frame repetition, malformed/late flip
tests, and optical no-tearing checks.

### Segment 3 — Remove routine full-frame copies

Status: **PENDING SEGMENT 2**

Goal: reduce PSRAM traffic produced by Linux rendering.

- memory-map validated back buffers to userspace where safe;
- cache-synchronize only modified ranges;
- use dirty rectangles supplied by the renderer;
- replace routine 2 MiB front-to-back copies with ownership changes;
- keep a bounded full-frame path only where correctness requires it;
- avoid framebuffer hashing during stability intervals because hashing itself
  is a full PSRAM read workload.

### Segment 4 — LVGL userspace integration

Status: **PENDING SEGMENT 3**

LVGL remains a userspace graphics layer:

```text
LVGL application -> framebuffer/update ABI -> Linux display driver
                 -> continuous GDMA -> DSI bridge -> panel
```

LVGL should render dirty regions into the current back buffer and submit one
completed update. The kernel owns validation, cache synchronization, buffer
roles, frame-boundary publication, and fault containment.

### Segment 5 — Repair C6 SDIO/MMC latency

Status: **PENDING; MAY RUN AFTER SEGMENT 1 EVIDENCE**

The current C6 SDIO probe can still execute a roughly 100 ms synchronous poll
and fail with `-110`. Repair it independently:

- start hardware work and complete asynchronously where the controller allows;
- do not hold broad locks across long polling windows;
- bound each attempt and move timeout handling to process context;
- use delayed retry with backoff rather than continuous rapid reprobe;
- publish a clean network-unavailable state after bounded failure;
- constrain transfer size/burst only when measurements justify it;
- retain slot isolation so an absent microSD card cannot participate.

### Segment 6 — Scheduling and interconnect tuning

Status: **ONLY IF MEASUREMENTS REQUIRE IT**

Evaluate voluntary/full kernel preemption or documented ESP32-P4 interconnect
priority controls only after continuous refresh and MMC cleanup. Preemption can
improve CPU responsiveness but cannot repair PSRAM bandwidth exhaustion.
Undocumented clock, QoS, or priority writes are prohibited.

## Required telemetry

Keep or add read-only evidence for:

- scanout mode and current front buffer;
- autonomous refresh-progress samples and SAR-wrap qualification count;
- exact frame/completion generations only for modes that actually receive
  hardware completion events;
- page-flip request/accept/complete/drop counters;
- GDMA channel state and unexpected stops;
- bridge FIFO depth samples and explicit underruns;
- DSI host, GDMA, ECC, common, guard, and backlight fault classes;
- MMC request duration, timeout count, retry count, and active slot;
- stable framebuffer role/commit information.

FIFO-zero samples are evidence, not standalone proof of an underrun. Optical
failure remains a release blocker even when software counters are clean.

## Acceptance sequence

Each segment is accepted independently in this order:

1. static source/model and exact patch checks;
2. clean M9 and M7 builds;
3. guarded M9 flash with exact readback;
4. fresh boot to the status page;
5. connected idle observation;
6. 600-second autonomous observation with the status page continuously visible;
7. controlled pattern/update test, when the segment supports updates;
8. stress and soak only after the preceding optical gate passes.

For every optical interval, record whether the panel shows the expected frame,
black, light cyan, flicker, tearing, or another artifact. A black fallback is a
contained symptom, not a successful display result.

## Decision point

If Segment 1 cannot keep a fixed front buffer continuously visible while its
descriptor topology and controller state remain valid, stop adding Linux-side
one-shot timing workarounds. Re-evaluate whether PSRAM bandwidth can satisfy
the panel continuously. A resident bare-metal display service remains the
fallback architecture, but it is not the current plan because Linux ownership
is still the stated goal.

## Working checklist

- [x] Temporarily disable M9 microSD slot 0.
- [x] Preserve C6 SDIO slot 1 and M7 two-slot behavior.
- [x] Complete a greater-than-one-hour fixed-status observation on Patch45.
- [x] Derive direct-register reload topology from ESP-IDF v6.0.1 and reject the
      descriptor-ring alternative.
- [x] Prove that block-done must be disabled to prevent auto-reload stalls and
      that rev-1.3 bridge VSYNC cannot replace it.
- [x] Freeze and model the non-stalling SAR/progress watchdog contract.
- [x] Freeze an isolated, style-clean, compiling Segment 1 prototype.
- [x] Implement Segment 1 as one isolated Linux patch.
- [x] Pass Segment 1 static and clean M9/M7 build gates.
- [x] Pass Segment 1 exact Patch46 flash/readback gate.
- [ ] Diagnose and correct the post-I2C dark runtime-policy rejection.
- [ ] Pass the 600-second connected and autonomous optical gates.
- [ ] Implement safe frame-boundary switching.
- [ ] Remove routine full-frame copies.
- [ ] Add LVGL userspace rendering.
- [ ] Repair C6 SDIO/MMC latency.
- [ ] Run final connected/disconnected/stress/touch/soak acceptance.
