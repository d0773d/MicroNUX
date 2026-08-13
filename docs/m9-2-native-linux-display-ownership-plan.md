# M9.2 Native Linux Display Ownership and Scanout Stabilization Plan

Short name: **YamUI Display Architecture Port**

Status: **IMPLEMENTING**

Current section: **M9.2.6 - Final integration and physical acceptance (TESTING)**

Implementation authorization: **ACTIVE - user-started goal**

Last status update: **2026-08-12**

## Status discipline

This document is the implementation ledger for M9.2. Every feature and work
package moves through these states in order:

`PLANNED` -> `IMPLEMENTING` -> `VALIDATING` -> `TESTING` -> `COMPLETED`

`COMPLETED` means the implementation exists, the required static validation
and hardware tests passed, and the evidence is recorded in this file. A work
package is completed only after every required feature and its exit criterion
are completed. Machine telemetry must never be described as visual proof of
the panel.

| Work package | Section status | Purpose |
| --- | --- | --- |
| M9.2.0 Contract and evidence freeze | **COMPLETED** | Baselines, hashes, rollback, candidate profile, memory map, and downstream renumbering are frozen below |
| M9.2.1 Handoff and observability contract | **TESTING** | ABI-v2 adopt-live evidence is retained as historical; patches 36-43 localize false software gates, preserve runtime-fault evidence, and reset telemetry at the visible epoch; patch 44 adds the evidence-based derated bandwidth profile |
| M9.2.2 Kernel-owned display pool | **TESTING** | The protected three-buffer map and ownership states are implemented; physical alias and workload tests remain open |
| M9.2.3 IRQ-rearm scanout and page flipping | **TESTING** | The Patch-41 IDF-equivalent fast rearm reached sustained machine-visible runtime, but the panel later went black with its backlight enabled; Patch 44 physical retest remains open |
| M9.2.4 Scanout stabilization gate | **TESTING - PATCH 29 PHYSICALLY REJECTED** | The exact automatic-clock candidate was flashed and read back, then failed dark after only one 512-byte GDMA transfer; no adopt-live candidate is accepted |
| M9.2.5 Ownership fallback and policy parity | **IMPLEMENTING / TESTING - NATIVE COLD-INIT** | Patches 30-44 implement ABI-v3 cold init, guarded reveal, fast one-shot rearm, black underflow filler, visible-epoch telemetry, runtime-fault evidence preservation, and the 60-MHz/1000-Mbps bandwidth profile |
| M9.2.6 Final integration and acceptance | **TESTING** | The 7/44/10 series, 1126-case model, strict source/object gates, clean M7/M9 builds, no-flash verifier, and artifact audits pass. Patch 44 is unflashed; exact readback and all runtime/user-observed visual gates remain open |

### Current Patch-44 candidate (pre-flash)

Patch 40 reached `RUNTIME_REVEALED`, but the user later observed a uniform
light-cyan panel while `/dev/fb0` still contained the correct nonuniform status
page. Patch 41 changed the bridge underflow filler to black and made recurring
one-shot DMA rearm match the ESP-IDF callback hot path. Its exact snapshots
`out/m9/hardware-runs/20260812T165750Z-snapshot-b5890811aa40-a0ce448a.log`
(SHA-256 `1f4cdef27fbe31af6314eea4b0d7b64f540c43e2ce6f73582900f10ed299051f`)
and `out/m9/hardware-runs/20260812T165952Z-snapshot-b5890811aa40-1ad22055.log`
(SHA-256 `a2098935b52eb9af39244d19a174c7ec19f67ac668872e467e58640e4e2cd02c`)
showed correct framebuffer content, zero reported host/GDMA/guard faults, and a
17,298-ns maximum IRQ-entry-to-CHEN rearm, but seven arm-to-IRQ intervals above
20 ms. The user subsequently observed a black panel with the backlight still
enabled. That is a physical rejection, not a successful fail-dark result.

Patch 42 resets cumulative telemetry after the final dark health check and
immediately before reveal. Patch 43 preserves the exact runtime evidence before
containment. Patch 44 applies the narrow bandwidth correction supported by the
ESP32-P4 LCD guidance: the ABI-v3 native path uses a 60-MHz DPI clock and 1000
Mbps per DSI lane while ABI v2 remains 80 MHz/1500 Mbps. The loader build also
uses 200-MHz PSRAM, PSRAM XIP, performance optimization, and the ESP-IDF
v6.0.1-compatible 256-KiB L2/64-byte cache-line combination.

The current ordered series is 7 platform + 44 peripheral + 10 isolation
patches, total 61, with manifest SHA-256
`9571ba5f78edc65675ca066652df583a9b0842b41a3baa209820e9696123805d`.
The 1126-case model passes with `bandwidth-profile=60mhz-1000mbps` and
`physical-long-run=pending`. Patch 44 SHA-256 is
`bcf953f4706e57944aae4d6a5423f5d3b38a0ab4b3354818d00c457022138307`;
the applied driver source is
`927530e5c4d2e9162af12b0f57b01b11ce1d7948626a8f2ddac3e8afa2d6cbb6`.

The clean M9 build contract is
`db0a3f95a19333c2ed6eaa4c31a8e372defdb68adbdbd87c01a894b221fd6d5b`.
Its Image is 6,164,592 bytes with SHA-256
`863d90fd73c48c79cd097fdee80e44c21807d54fbde55d6d0becaaba9630eced`;
the loader is 233,952 bytes with SHA-256
`260442c5d3d53e3e91a2eacc27fe3391e7ddd8d3dbf6dc3c756968f0d2a071c8`.
The Windows verifier ended with `Nothing was flashed.` The independent clean
M7 regression retained ABI v2, used the same audited driver source/object,
passed its 14-file bFLT W^X audit, and produced contract
`ae90cbfc2be005258c9d08a9ba5579561dd89ee5f122b9deeeb9e7e665bb279a`.
Patch 44 therefore clears pre-flash integration only. Exact flash/readback, the
ordered disconnect/preflight/stress/touch/soak sequence, continuous optical
observation, and a user-confirmed absence of cyan, black, flicker, tearing, and
stale content remain mandatory.

## Milestone insertion note

This document assigns **M9.2** to native display ownership and stabilization.
The existing `m9-lvgl-window-manager-plan.md` originally used M9.2 for the
optional LVGL service. Its downstream stages are now renumbered M9.3 through
M9.8, and its status table names this native-display milestone as the explicit
prerequisite. This file is authoritative for M9.2; the older file remains
authoritative for the later optional GUI stages.

## Desired result

MicroNUX shall own the Kit C display through a native Linux driver after the
loader relinquishes it dark and quiescent. The driver shall reproduce the
proven low-level scanout behavior
used by YamUI's active Waveshare BSP path while preserving Linux ownership,
fault containment, terminal recovery, and application isolation.

The completed path shall provide:

- stable 800x1280 RGB565 scanout on the JD9365 panel;
- deterministic Linux ownership of GDMA, the DSI bridge, DSI host, D-PHY,
  display I2C controls, backlight, framebuffer memory, and interrupts;
- one-frame completion handling that explicitly rearms scanout and selects the
  next complete framebuffer;
- kernel-owned front/back/spare buffering in the first production candidate;
- a stable Linux framebuffer/page-flip interface that does not depend on LVGL;
- fail-dark behavior when the driver cannot prove a valid source;
- retained USB serial recovery and framebuffer-console restoration; and
- sufficient diagnostics to distinguish framebuffer, PSRAM/GDMA, bridge,
  host/PHY, handoff, and panel-side failures without pretending to observe
  photons.

LVGL, the window manager, IgniteVM integration, and application UI APIs begin
only after this milestone passes.

## Why this plan exists

The panel has repeatedly entered a solid light-blue/cyan state or shown cyan
during source transitions. At other times Linux remains alive while the
framebuffer hash is stable, GDMA source progress continues, and the recorded
DMA, bridge, and host error counters remain clean. Those facts do not support
a simple framebuffer overflow or PSRAM exhaustion explanation, and they do not
prove that the panel is receiving valid pixels.

YamUI drives the same board and display through a materially different runtime
contract. It lets the Waveshare BSP and ESP-IDF retain display ownership for
the firmware lifetime, rearms a complete framebuffer transfer from the
completion ISR, and uses three full framebuffers for tear-avoidant page flips.
The rejected ABI-v2 MicroNUX candidates transferred raw ESP-IDF-created state
into Linux and attempted to restart that inherited source. That adopt-live
seam failed through patch 29. The current ABI-v3 source leaves the display
reset, clock-off, dark-commanded, and quiescent so Linux initializes the entire
display stack itself.

This plan ports ownership, explicit frame rearming, and front/back/spare buffer
ownership as one coherent display architecture. A one-buffer build remains a
diagnostic mode for fault isolation, but it is not the production target and
does not gate implementation of the triple-buffer candidate.

## Verified reference baselines

### YamUI active baseline

The source reference is YamUI `main` commit
`de1b723b9fc237e63e96fc82ce2975aa8b1a2ad4`.

YamUI's ESP-IDF 5.5.2 version is historical reference evidence only. M9.2
must not build against, copy an implementation from, or introduce a runtime
dependency on ESP-IDF 5.5.x. The MicroNUX loader implementation and every
firmware build gate use pinned ESP-IDF v6.0.1 commit
`8c19b156084a0753687347cca1f5355782893533`.

The active display path is:

```text
kc_touch_display_init()
    -> bsp_display_start_with_config()
    -> Waveshare esp32_p4_platform BSP
    -> esp_lcd JD9365 DPI panel
    -> ESP-IDF DW-GDMA / DSI bridge / DSI host / D-PHY
```

The verified configuration is:

| Item | YamUI active value |
| --- | --- |
| ESP-IDF | 5.5.2 on `main` |
| Waveshare platform | 1.0.6 |
| Waveshare JD9365 | 1.0.6 |
| ESP LVGL adapter | 0.1.4 |
| Panel format | 800x1280 RGB565 |
| DPI clock | 80 MHz |
| DSI link | two lanes at 1500 Mbps per lane |
| Horizontal timing | HBP 20, HSW 20, HFP 40 |
| Vertical timing | VBP 10, VSW 4, VFP 30 |
| D-PHY supply | LDO channel 3 at 2.5 V |
| Full framebuffers | three PSRAM buffers, 2,048,000 bytes each |
| Partial draw buffer | 800x50 RGB565, 80,000 bytes |
| Tear mode | `ESP_LV_ADAPTER_TEAR_AVOID_MODE_TRIPLE_PARTIAL` |

The known active path is the BSP RGB565/1500-Mbps path. The unused fallback
code that selects RGB888 and the generic component's 1200-Mbps default are not
the reference contract for this work.

LVGL is not the reason that pixels reach the screen. LVGL renders changed
areas, while the BSP, adapter, framebuffer selection, cache synchronization,
GDMA, DSI hardware, and completion callbacks maintain scanout. This milestone
ports the latter behavior without importing LVGL.

### Historical pre-M9.2 MicroNUX baseline

| Item | Frozen pre-M9.2 value |
| --- | --- |
| Loader | ESP-IDF v6.0.1, target `esp32p4` |
| Kernel | Linux 6.12.27, RV32 NOMMU, machine mode |
| Panel format | 800x1280 RGB565 |
| DPI/link profile | restored 80 MHz / 1500 Mbps per lane baseline |
| Full framebuffers | one retained 2,048,000-byte PSRAM framebuffer |
| Display reserve | 4 MiB at the start of PSRAM |
| Linux scanout | custom descriptor-ring handoff and hardware reload policy |
| Runtime owner | Linux after the loader stops and publishes the handoff |
| Recovery | USB serial shell, fbcon/status restoration, fail-dark hooks |

The historical single framebuffer is allocated directly in reserved PSRAM. Linux writes
through its uncached alias, and GDMA reads the physical PSRAM address directly
into the DSI bridge. Internal SRAM holds only small descriptor/control data;
there is no internal-RAM full-frame bounce buffer.

The historical 4 MiB display reserve cannot hold three 2,048,000-byte frames.
Triple buffering therefore required the measured memory-map change now present
in the ABI-v3 candidate.

### Current ABI-v3 native candidate

The current source architecture is cold-init, not adopt-live:

1. The ESP-IDF v6.0.1 loader commands PWM zero and the safe reset sequence,
   verifies the reset/clock/PMS/IRQ facts it can read, releases I2C, and
   publishes the 192-byte ABI-v3 contract without initializing DSI or drawing a
   splash.
2. Linux probes in exact state `PROBED_QUIESCENT`. A root-owned `cold_init`
   request performs native I2C/LDO, JD9365 command programming, D-PHY, DSI host,
   bridge, GDMA, IRQ, and deterministic-black triple-buffer setup.
3. Linux reaches `SCANOUT_QUALIFIED_QUIESCENT` only after strict clean-frame
   qualification. The backlight and touch devices are not yet exposed.
4. `/init` renders the complete tty1 status page, rechecks the state, and makes
   the one-shot `boot_ready` request.
5. The driver proves that rendered commit was presented plus four fresh
   same-front completions, reacquires I2C, sends control `0x17` while PWM is
   zero, sends PWM 63, registers backlight, publishes `RUNTIME_REVEALED`, and
   registers touch asynchronously.

Linux owns three 2,048,000-byte RGB565 buffers and one marked-last descriptor
per buffer. Each transfer-done IRQ selects only a complete queued buffer and
rearms one frame; no internal-SRAM full-frame bounce buffer exists. Runtime
VPG is intentionally absent from ABI v3. All software diagnostics label the
external panel state unobserved because acknowledged write-only I2C commands
and internal counters cannot prove emitted light or pixels.

### Frozen pre-M9.2 implementation boundary

The implementation started from MicroNUX commit
`b5890811aa4072b21285da5a89a4fc86d4ad90cb` on branch
`codex/m9-lvgl-window-manager`. The pre-existing dirty display/fault work is
preserved; unrelated remote attachments and the untracked M7 isolation plan
are explicitly outside the M9.2 commit scope.

| Artifact or contract | Frozen pre-M9.2 evidence |
| --- | --- |
| Linux Image | 6,098,864 bytes; SHA-256 `932bbdc5c4d26d89a1105ba7f0361c612c58ce351bb5a7f4b865c6e2902321ff` |
| M9 DTB | 2,607 bytes; SHA-256 `dc620a59f4031f0cd7316eb27e73a843f97a31918c324067c9bdea793b3182cb` |
| metadata | SHA-256 `425e7ca437b5f7326bdd85dd3e7ea5f49f628ef5ed5ef70e97373105a5e7fa18` |
| rootfs | SHA-256 `e044debe6665e4c26ebe488891962f6a0f9dd67ab33d7ce5492ef207dd257ed5` |
| Linux config | SHA-256 `4c7bc00ebf4234dec58b13414d71f21818791cf8522a97fbb8f41981ecaef058` |
| vmlinux | SHA-256 `24e5513ee6fc0b11e91f7cbffc7862c9f168a27249736e1344a21e8b3a2757d8` |
| display test | SHA-256 `fc2eac9aa60a01fe016320a638d6c8a8e48f5e62e9b8e87655e622e2b1663c83` |
| source contract | `450b06648f7438b43c80d2514a82c63475b26bc62593d09778afc8fc8f042146` |
| Linux patch manifest | 7 platform + 22 peripheral + 10 isolation patches; SHA-256 `88a18e03b721a2e36781703df99cc2f281860d404924827cc0c7f7bd230b2c57` |
| current pre-M9.2 loader | 281,648 bytes; SHA-256 `24db26404ec42444454bc3e970e4961529f5364c5941bc053cf884588877b118` |
| proven rollback loader | 281,344 bytes; SHA-256 `5f9697a8d8c0d04780132e4690f5378e72e7b58795dd11ccecea7ad3b475e949` |

The rollback loader is retained at
`build/loader-m7-early-deny-patched/micronux_m3_loader.bin`. The pre-change
machine logs are retained as `out/m9/vendor-timing-preflight.log` and
`out/m9/vendor-timing-disconnect-600s.log`; they record the one-buffer,
four-descriptor auto-reload path, its exact resource addresses, advancing
GDMA/frame state, and zero recorded DMA/bridge/host errors. They are baseline
evidence only and do not validate the new implementation or panel photons.

The selected memory change preserves the kernel load address and all existing
user/comms addresses: the loader-created front remains in
`0x48000000..0x48400000`, while Linux reserves a new 4 MiB back/spare pool at
`0x49300000..0x49700000`. Back is fixed at `0x49300000`, spare at
`0x49500000`, and each 2 MiB slot contains one 2,048,000-byte frame plus a
49,152-byte guard/alignment remainder. Linux initializes and monitors both
fixed-slot guards. The loader-created front has variable placement inside its
separate 4 MiB reserved/no-map area, so it is protected by exact framebuffer
bounds and the reservation rather than by writing across adjacent loader heap
memory. Linux general RAM loses 4 MiB; this is an explicit build-and-workload
validation item rather than an assumed-free cost.

## Locked architecture decisions

1. **Linux remains the sole post-handoff device owner.** ESP-IDF and FreeRTOS
   shall not run beside Linux as a second runtime hardware owner.
2. **M9.2 contains no LVGL dependency.** Tests use fbcon and a small native C
   diagnostic.
3. **Port behavior, not runtime objects.** No `esp_lcd_*`, FreeRTOS, BSP, heap
   capability, ISR handle, or IDF object pointer crosses into Linux.
4. **The first production candidate ports the complete buffer-ownership and
   frame-lifecycle architecture.** It retains the 80-MHz, 1500-Mbps, RGB565,
   panel-command, reset, backlight, and current DSI policy baseline unless a
   work-package gate explicitly authorizes otherwise.
5. **Triple buffering is foundational, not an LVGL feature.** A one-buffer
   build exists only as a selectable diagnostic that can distinguish page-flip
   state from GDMA/DSI state; it is not the accepted final configuration.
6. **No raw framebuffer access is granted to ordinary applications.** Existing
   NOMMU isolation, denied framebuffer `mmap()`, checked writes, and device
   ownership rules remain in force.
7. **No source is stopped while the backlight may be lit.** Transitions remain
   dark-settled, source-verified, primed, and revealed in that order.
8. **Unexpected scanout failure is fail-dark.** Automatic recovery is added
   only after a physical recovery sequence is proven not to expose cyan or
   destroy the last valid source.
9. **Machine and visual evidence stay separate.** Clean counters and stable
   framebuffer hashes are necessary but never sufficient proof of the glass.
10. **YamUI source is a provenance reference.** Kernel implementation is
    written natively under a GPL-2.0-only-compatible boundary; Apache-2.0
    ESP-IDF/Waveshare implementation files are not pasted into the kernel.

## Target architecture

### Production stage: front/back/spare

```text
fbcon / native presentation API
                |
                v
       render or copy into back framebuffer
                |
                v
       queue one complete framebuffer selection
                |
                v
 transfer-complete IRQ: front <- queued; old front -> spare
                |
                v
       rearm the selected complete framebuffer
```

Page selection occurs only at a full-frame completion boundary. A writer must
never modify the front buffer currently being scanned. The first triple-buffer
implementation does not need LVGL or a window manager.

The IRQ handler shall mirror the relevant IDF lifecycle: acknowledge the
completion, verify that no DMA or bridge fault occurred, select the current or
queued complete-frame descriptor, and explicitly re-enable the channel for the
next frame. The custom circular descriptor and source/destination hardware
auto-reload behavior are removed from this candidate.

### Diagnostic stage: one buffer

The same driver may be built or booted in a one-buffer diagnostic mode. That
mode uses the identical marked-last descriptor and completion IRQ rearm but
does not page flip. It exists only to answer whether a failure belongs to the
buffer-ownership/flip layer or remains in GDMA, the bridge, DSI, or the panel.
It must not become the accepted production path merely because it is simpler.

## Failure hypotheses and discriminators

| Hypothesis | Evidence that supports it | M9.2 discriminator |
| --- | --- | --- |
| Custom Linux DMA/rearm lifecycle | MicroNUX diverges from the active YamUI/IDF ISR-rearm model | M9.2.3 replaces it with explicit per-frame rearm |
| Front-buffer modification or partial presentation | The pre-M9.2 single-buffer scanout could read memory while software updated it | Triple-buffer ownership prevents writes to the selected front buffer |
| IDF-to-Linux ownership seam | YamUI retains one owner for its lifetime | Retest the exact source switch with the corrected ESP-IDF automatic clock-lane state; use Linux cold-init only if that bounded correction fails |
| PSRAM/AXI starvation | RGB565 scanout reads PSRAM continuously; Linux also executes and allocates there | Sticky underrun/FIFO/SAR telemetry plus controlled idle and load tests |
| Cache or framebuffer corruption | Direct PSRAM scanout and mixed cached/uncached access exist | Full-frame hash, bounds, alias, and descriptor validation |
| Host/PHY/panel synchronization | Cyan can occur while upstream counters look healthy | VPG and source-transition evidence, DSI state snapshots, optical gate |
| Panel timing/lane margin | Link runs at the board's high-rate reference profile | Not changed in the first candidate; tested only after lifecycle isolation |
| PSRAM exhaustion or C overflow | Would corrupt bounds/state or trigger allocation failures | Fixed reserved bounds, memory accounting, guards, and full-frame hashes |

No hypothesis is accepted as the root cause until its discriminator fails or
passes on the exact physical candidate.

## Work packages

### M9.2.0 - Contract and evidence freeze

Section status: **COMPLETED**

Deliverables:

- [x] **COMPLETED** - record the MicroNUX HEAD, dirty-worktree scope, loader,
  Image, DTB, patch-manifest, source-contract, and SHA-256 hashes;
- [x] **COMPLETED** - record the exact YamUI commit, IDF/component versions,
  active BSP configuration, framebuffer count, and frame-completion behavior;
- [x] **COMPLETED** - preserve a verified bootable rollback loader and Linux
  payload without rebuilding or overwriting those artifacts;
- [x] **COMPLETED** - renumber the later LVGL milestones so this document becomes
  M9.2 and the optional GUI remains downstream;
- [x] **COMPLETED** - freeze the first production candidate as 80 MHz, RGB565,
  two lanes at 1500 Mbps, three protected framebuffers, and the explicit
  completion/rearm lifecycle, with no timing or DSI policy experiment; and
- [x] **COMPLETED** - capture a pre-change serial/diagnostic baseline, including
  framebuffer hash, descriptor bounds, GDMA SAR/status, bridge underrun/FIFO
  state, DSI host/PHY state, uptime, boot ID, and memory pressure.

Exit criterion: the exact before/after boundary, rollback, and first candidate
are reproducible. No code has been flashed merely to gather missing facts.

### M9.2.1 - Handoff and observability contract

Section status: **TESTING - ABI-v2 ADOPT-LIVE REJECTED; ABI-v3 SUPERSEDES IT**

The ABI-v2 deliverables and evidence in M9.2.1 through M9.2.4 document the
rejected adopt-live investigation. ABI v3 supersedes their loader-initialized
handoff semantics while retaining the Linux-owned pool, IRQ-rearm, diagnostics,
and fail-closed principles. No ABI-v2 result validates the current candidate.

Deliverables:

- [ ] **TESTING** - define a new versioned display handoff that carries only
  immutable hardware facts and Linux-adoptable resources;
- [ ] **TESTING** - include panel format/timing, framebuffer address/size/stride,
  DMA channel, DSI FIFO destination, clock/link profile, backlight state,
  ownership state, structure size, flags, and CRC;
- [ ] **TESTING** - remove the contract assumption that Linux must continue a
  loader-created circular descriptor ring;
- [ ] **TESTING** - keep the loader splash valid until the backlight-off write
  succeeds and the physical dark-settle interval completes;
- [ ] **TESTING** - make Linux reject version, CRC, bounds, alignment, panel
  profile, memory-map, or channel mismatches before touching the source;
- [ ] **TESTING** - expose cumulative and current diagnostics for frame
  completions, rearm attempts/failures, descriptor state, GDMA SAR/status,
  bridge underrun/FIFO state, host status, D-PHY state, source generation,
  backlight state, and fault containment;
- [ ] **TESTING** - preserve a single serialized reader for read-to-clear DSI
  host status registers and software-latch their evidence; and
- [ ] **TESTING** - add build/test markers that prove the exact handoff version
  and driver implementation were included in the artifacts.

Validation:

- [x] **COMPLETED** - compile the loader against pinned ESP-IDF v6.0.1 target
  `esp32p4` and reject another IDF revision;
- [x] **COMPLETED** - apply the Linux patch series to the exact Linux 6.12.27
  source and compile the display driver with warnings treated as errors;
- [x] **COMPLETED** - unit-test valid, truncated, wrong-version, wrong-CRC,
  overflowing, out-of-range, and misaligned handoffs; and
- [ ] **TESTING** - verify every rejected handoff remains dark and leaves the
  USB serial recovery path usable.

Exit criterion: Linux can claim or reject the display deterministically without
depending on an ESP-IDF object or undocumented descriptor-ring lifetime.

### M9.2.2 - Kernel-owned display pool

Section status: **TESTING**

Deliverables:

- [ ] **TESTING** - measure the kernel, user pool, comms, loader, framebuffer,
  and peak workload memory budgets before selecting a display-pool address;
- [ ] **TESTING** - choose and document one protected fixed memory-map design
  across the retained loader-front reservation and the contiguous back/spare
  pool; do not assume that adding the 4 MiB back/spare reservation is harmless
  to the existing Linux/PMP memory map;
- [ ] **TESTING** - reserve three DMA-aligned 2,048,000-byte RGB565 buffers
  across those protected regions plus descriptor, metadata, alignment, and
  guard space;
- [ ] **TESTING** - update device-tree, loader bounds, PMP/isolation, DMA policy,
  handoff, and memory documentation so U-mode cannot access the display pool;
- [ ] **TESTING** - give each framebuffer a stable physical address and a Linux
  uncached mapping suitable for CPU writes and GDMA reads;
- [ ] **TESTING** - create explicit `front`, `back`, and `spare` ownership states
  with no state in which a writer can modify the selected front buffer;
- [ ] **TESTING** - initialize all defined fixed-slot guards and all three
  buffers deterministically and prevent stale loader or prior-boot pixels from
  being revealed; and
- [ ] **PLANNED** - retain an optional one-buffer diagnostic configuration
  without maintaining a separate driver implementation.

Validation:

- [ ] **TESTING** - prove every frame and guard lies wholly inside the protected
  display pool and no pair overlaps;
- [ ] **TESTING** - prove cached/uncached aliases resolve to the expected
  physical storage without requiring a missing cache flush;
- [ ] **TESTING** - prove the revised memory map leaves the measured kernel,
  user pool, comms, storage, networking, and application headroom intact;
- [ ] **TESTING** - prove failed pool validation prevents display claim and
  remains fail-dark; and
- [ ] **TESTING** - verify deterministic allocation and hashes across repeated
  builds and three boots.

Exit criterion: Linux owns three protected, non-overlapping, DMA-addressable
framebuffers with explicit front/back/spare state and sufficient measured
system memory headroom.

### M9.2.3 - IRQ-rearm scanout and page flipping

Section status: **TESTING**

Deliverables:

- [ ] **TESTING** - create one marked-last full-frame GDMA descriptor contract
  for each framebuffer in kernel-owned, validated internal SRAM;
- [ ] **TESTING** - program the selected front framebuffer as the exact PSRAM
  source and the DSI bridge FIFO as the fixed destination with the validated
  width, burst, handshake, flow-control, and 2,048,000-byte block contract;
- [ ] **TESTING** - remove the current circular descriptor and source/destination
  hardware auto-reload behavior from the M9.2 candidate;
- [ ] **TESTING** - implement the completion IRQ path that acknowledges and
  validates a full transfer, selects the current or queued complete frame, and
  explicitly rearms the channel;
- [ ] **TESTING** - rotate front/back/spare ownership only after the selected
  descriptor and channel rearm succeed;
- [ ] **TESTING** - leave the old valid front selected when a queued flip is
  late, invalid, interrupted, or fails validation;
- [ ] **TESTING** - define a Linux-native presentation contract suitable for
  fbcon and future GUI clients, using standard fbdev behavior where practical;
- [ ] **TESTING** - add wait/flip timeout, repeated queue, shutdown, and
  client-exit behavior without exposing MMIO or descriptors;
- [ ] **TESTING** - retain dark startup, complete status rendering into a
  non-front buffer, queued selection, multiple clean completions, and final
  backlight reveal ordering;
- [ ] **TESTING** - make missed completion, DMA error, bridge underrun, invalid
  ownership, host fatal status, or impossible generation transition latch a
  fault and preserve/fail-dark the source; and
- [ ] **TESTING** - keep dirty-rectangle DMA2D acceleration outside the first
  correctness patch; triple buffering must work with CPU rendering alone.

Validation:

- [ ] **TESTING** - prove one and only one front descriptor is selected and
  source bounds never leave that framebuffer;
- [ ] **TESTING** - prove every accepted completion is paired with one rearm and
  no completion storm, lost rearm, or wrap creates a false pass;
- [ ] **TESTING** - prove the front buffer hash remains unchanged while selected
  and all rendering targets only back or spare;
- [ ] **TESTING** - prove a failed/late flip leaves the old front scanning and
  never exposes a partial frame;
- [ ] **PLANNED** - run the same frame engine in one-buffer diagnostic mode to
  verify that the optional discriminator does not use another DMA path;
- [ ] **TESTING** - prove a test-injected failure while physically dark is
  contained without relighting an unverified source;
- [ ] **TESTING** - measure PSRAM bandwidth, CPU cost, frame cadence, and memory
  headroom with one and three buffers; and
- [ ] **TESTING** - rebuild the exact ABI-v3 loader/Linux artifacts and verify
  every recorded hash before flash.

Exit criterion: the triple-buffer candidate boots to a complete status frame
with deterministic ownership rotation, advancing explicit rearm generations,
and zero driver, DMA, bridge, host, or containment faults. This is a machine
gate, not yet a visual stability claim.

#### Historical ABI-v2 M9.2 implementation evidence - 2026-08-11

This preserved validation boundary was split between completed static/build
validation and its then-active physical Kit C gate:

- pinned ESP-IDF `v6.0.1` commit
  `8c19b156084a0753687347cca1f5355782893533` accepted exactly two tracked
  MicroNUX patches and reproduced all three patched-source digests;
- the ABI-v2 Kit-C loader compiled successfully for `esp32p4` with all required
  triple-buffer, panel-reset, dark-handoff, forced-HS, exact panel-timing, and
  Linux-rearm markers; `micronux_m3_loader.bin` is 281,840 bytes with SHA-256
  `77a6d1ba1674b6d986478da693d7e6772a8309041d100178ed329ef155a6a986`;
- the loader ELF is 5,768,016 bytes with SHA-256
  `4d5bd56396e58b112ac3d49f17ee04c8fca76f539935f43e3f72537f985c2256`;
- Linux patch 23 was regenerated from the exact post-patch-22 source as
  postimage commit `871d5d146acb535990eaef52a5347a5b08997ff6`; it changes 662 lines added and
  96 removed and reverse-applies exactly to that postimage. The latest audit
  added exact ownership/porch validation, deterministic GDMA block/control/
  handshake configuration, explicit 8-byte framebuffer alignment validation,
  deterministic splash copies in both Linux-owned slots, fault-latched front
  preservation, monitored 49,152-byte back/spare slot guards, and live
  bridge-FIFO depth diagnostics.
  Patch SHA-256 is
  `9240a153b15f237f7a60f312589af849be74ffa0b2c31409278bb68139eec984`;
- official Linux v6.12 `checkpatch.pl --strict --no-tree --show-types` reports
  `0 errors, 0 warnings, 0 checks` for patch 23;
- the ordered Linux series checker reports 7 platform + 23 peripheral + 10
  isolation patches, total 40, with manifest SHA-256
  `52b1c331bfcc6363a6cf5f842a2f7aa52b89c129b8c76f4a7d43f7a84f4ac411`;
- `scripts/m9-2-display-contract-test.py` passes 93 cases covering valid and
  rejected ABI contracts, CRC/version/profile/bounds/alignment failures,
  exact ownership/timing/DMA configuration, descriptor upper address words,
  the contiguous 32 MiB PSRAM budget with 15 MiB general Linux headroom,
  fixed-slot guard bounds, normal and replaced page flips, stopped startup
  selection, transactional failed-rearm and fault-latched front preservation,
  both M7/M9 protected-memory DT bindings, exact display-GDMA permission masks,
  reliable
  delayed-commit rescheduling, and dark-preserving driver removal;
- the contract model now binds GDMA `CFG_LO=0x0000000f`, the exact
  source/destination linked-list mode inherited from pinned ESP-IDF v6.0.1,
  and has a focused negative test that rejects the prior reload-mode value
  `0x00000005`;
- eight synthetic hardware-harness parser cases require monotonic rearm/flip/
  generation counters, reject role aliasing, guard corruption, and an invalid
  bridge-FIFO value, and require an observed completed page flip for
  presentation, signal, and touch gates;
- the exact Linux 6.12.27 postimage source has SHA-256
  `8828bb86de7ba0da91bae60e88fd351163fb0220f2410b93277b4d845429fd58`;
  its display object compiles with `W=1 KCFLAGS=-Werror` and has SHA-256
  `2f24af54bf6eac2dba7ed065fde8d63069ce02670294e644ef5e6e5bcee2a037`;
- the full Buildroot build passes with source contract
  `6005d9f4aa906fac2459cde08c6812d64f1555ea5bbfe59dde7198a65024468c`,
  reports `lvgl=absent`, and emits a 6,098,864-byte Image within the 6,291,456-byte
  partition limit;
- all seven recorded artifact hashes recompute exactly: Image
  `48a2ebfdb085d3805eaafa6dd5a5a9ec89bc0c9f34b4667ffe10ea40988811e4`,
  DTB `862798942dc80f67ed5ef0b76dc8d91b6f93805711ac271955db42e1f059fd66`,
  metadata `2989b15260d99d9b3338cd19f1d340fb7f7767dc335bd796c12f9471ccd109ed`,
  rootfs `e044debe6665e4c26ebe488891962f6a0f9dd67ab33d7ce5492ef207dd257ed5`,
  Linux config `4c7bc00ebf4234dec58b13414d71f21818791cf8522a97fbb8f41981ecaef058`,
  vmlinux `66ea2ed3189d506cad5712902f1fa95a7b31e32bf90b16e8578393df4f125456`,
  and display test
  `fc2eac9aa60a01fe016320a638d6c8a8e48f5e62e9b8e87655e622e2b1663c83`;
- on 2026-08-11 the first frozen M9.2 candidate was flashed to Kit C and passed
  the fail-closed readback gate:
  `scripts/m9.ps1` reads one exact flash range from the loader app through the
  metadata tail, extracts the four written payload spans, compares each
  readback SHA-256 with its host artifact, preserves the raw readback plus a
  JSON manifest, and emits `MICRONUX:M9.2:FLASH-READBACK state=pass` only when
  all four match. Readback evidence is preserved under
  `build/m9-readback/20260811T234303853Z-bb94b3f3`; loader, Image, DTB, and
  metadata readback hashes all match that candidate's frozen values. The
  initial run reached this verified state but then exposed a host-only
  PowerShell formatting error in the PASS print. The expression was
  parenthesized correctly and its parser/output regression check passes without
  touching the board;
- the first candidate was then rejected by its actual boot result. The user
  observed a black screen, and the no-reset snapshot recorded
  `esp32p4-micronux-dsi 500a0000.display: invalid loader display handoff: -22`
  before the driver registered `/dev/fb0`, display sysfs, or the backlight.
  Linux and the USB shell remained alive. The sealed snapshot is
  `out/m9/hardware-runs/20260811T234846Z-snapshot-b5890811aa40-a47b2b32.log`
  with SHA-256
  `387895438127fe59fd3df4be5aa922f18b4a984694ae0645dab18efc16deb4db`;
  the reset-based loader/boot capture is
  `out/m9/hardware-runs/20260811T235153Z-preflight-b5890811aa40-89a678da.log`
  with SHA-256
  `383952c31ad16c80f20f3c8bdda23359cf6971755b7e6c84a50833a03b7242ea`;
- the boot capture proves that the loader published a valid ABI-v2 contract
  with three buffers, `dma=irq-rearm`, and CRC32 `f8f287a3`. Reconstructing the
  exact logged structure reproduces that CRC. Pinned ESP-IDF v6.0.1 configures
  both GDMA source and destination as linked-list transfers, so inherited
  `CFG_LO` is `3 | (3 << 2) = 0x0000000f`. The rejected Linux candidate
  incorrectly required and programmed reload/reload `0x00000005`. The
  corrected patch, regression, and build above change only that proven
  contract mismatch;
- the corrected candidate was flashed on 2026-08-11 and its complete
  10,420,352-byte readback passed. Evidence is preserved under
  `build/m9-readback/20260812T010009004Z-5576cdc3`; loader SHA-256
  `77a6d1ba1674b6d986478da693d7e6772a8309041d100178ed329ef155a6a986`,
  Image `48a2ebfdb085d3805eaafa6dd5a5a9ec89bc0c9f34b4667ffe10ea40988811e4`,
  DTB `862798942dc80f67ed5ef0b76dc8d91b6f93805711ac271955db42e1f059fd66`,
  and metadata
  `2989b15260d99d9b3338cd19f1d340fb7f7767dc335bd796c12f9471ccd109ed`
  all match the frozen host artifacts;
- the first corrected no-reset snapshot passes on boot ID
  `8cf67808-f98f-42b2-811c-8c6b3a8497ae`. Linux accepts ABI v2 with
  `cfg=0000000f:0a020001`, reaches the status reveal, completes one page flip,
  advances frames/rearms/generations, reports a valid bridge FIFO, and retains
  zero faults, rearm failures, DMA errors, bridge underruns, host errors, and
  guard errors. Both quiescent `/dev/fb0` reads equal
  `d38565f64fba5402ab8b767f77648a01ba685479491b1d104b5a3eacc304601c`.
  The sealed transcript is
  `out/m9/hardware-runs/20260812T010301Z-snapshot-b5890811aa40-9474ef5d.log`
  with SHA-256
  `e539a18ff0b94a79e86363bf9b43af2556695e7d76bed6ab363b9c3dbd43655e`;
- the same exact corrected candidate failed its physical gate. The user observed
  a slight cyan flash between the loader and status page, followed by a solid
  cyan screen after idle. No reset or VPG source transition was issued before
  capture. Merely attaching COM14 for a no-reset snapshot coincided with the
  status page returning during the passive-capture interval. The same boot ID
  remained active at uptime 327 seconds with front buffer 1 fixed, no queued
  frame, the expected stable framebuffer SHA-256, advancing frames/rearms/
  generations, valid guards and bridge FIFO, and zero DMA, bridge, host,
  watchdog, or ownership faults. The sealed recovery-after-attach transcript is
  `out/m9/hardware-runs/20260812T010812Z-snapshot-b5890811aa40-b222fa4e.log`
  with SHA-256
  `8fee6399543ec998a63be235accd63cdb1208a5a775a55b88ee195f61e52940c`.
  This is a visual rejection plus a healthy post-attach machine snapshot, not a
  machine observation of the pixels while they were cyan. The user subsequently
  reported that the same rejected build remained on the status page without
  another cyan transition through the latest observation. That strengthens the
  recoverable link/panel synchronization classification but does not convert the
  rejected candidate into a pass;
- `scripts/m9-hardware-test.py --mode stress` now combines the existing
  controlled SD write/remount/hash/delete probe, Wi-Fi association and traffic,
  concurrent SD/network soak, 4 MiB M5 memory load, paced framebuffer writes,
  a signal-driven console restore, and seven ordered display/memory snapshots.
  It requires a completed page flip, stable restored framebuffer hash, all
  triple-buffer roles and generations valid, no fault/underrun/DMA/host/guard
  error, and at most 512 KiB post-run `MemFree`/`MemAvailable` loss. Its shell
  command and seven-stage acceptance parser pass host validation;
- every reset-based M9 hardware mode now requires exactly one loader-reported
  kernel and DTB record and compares their sizes and SHA-256 values with the
  selected artifact directory before testing. Host parser cases pass for the
  exact payload, a rejected kernel digest, and duplicate boot evidence;
- every reset-based mode also records two one-second-separated boot-ID/uptime
  samples and requires the same boot ID with strictly advancing uptime. Host
  parser cases pass for the valid state and reject a changed boot or stalled
  uptime;
- `--mode disconnect-vpg` now performs the fresh disconnected soak and bounded
  VPG test in one harness process without another reset. It compares the
  detached boundary boot ID with the reconnected ID before VPG and with the
  post-VPG ID afterward; its host parser proves the same-boot success path;
- `scripts/m9-run-logged.py` now wraps any hardware mode without changing its
  serial behavior, streams the child byte output to both the console and an
  exclusive timestamped log, records the exact Git state plus harness/loader/
  Image/DTB/metadata/source-contract hashes, preserves the child exit status,
  fsyncs the transcript, and writes a SHA-256 sidecar. A no-device `--help` run
  produced and sealed a valid transcript;
- the then-current host-script SHA-256 values were `c38f4dd7341c40340bdddf16871399d6a25ccb220feb884ffbb7f9cfd51741f7`
  for `m9.ps1`, `d3bba46cae99b4ac7636df528247e27e758e0d746a7192a435864a441de88e67`
  for `m9-hardware-test.py`, and
  `f6d9ce4188809bef7973d6ddfeefd471a54a5ff69c71d145d95b6a1faf75e7e1`
  for `m9-run-logged.py`;
- Python, PowerShell, Bash, patch-series, and repository diff checks pass; and
- the corrected M9.2 artifact is flashed, readback-verified, and passes its
  initial machine snapshot. Workload headroom measurements, repeated boots,
  the disconnected/VPG sequence, touch, and every corrected-candidate visual
  gate remain open.

The corrected flash/readback and first snapshot are **COMPLETED**. Stress,
logging, and remaining hardware paths are **IMPLEMENTED / TESTING** until the
candidate is fully exercised on Kit C and physically observed.

### M9.2.4 - Scanout stabilization gate

Section status: **TESTING**

The completed patch-28/29 items below are historical rejection evidence. Run
the still-open items against only the ABI-v3 cold-init candidate.

Required machine tests:

- [x] **COMPLETED - PATCH 28 REJECTED ARTIFACT** - flash and read back the exact
  patch-28 loader, Image, DTB, and metadata;
- [x] **COMPLETED - PATCH 29 REJECTED ARTIFACT** - build, flash, and read back
  the exact automatic-clock correction with `LPCLK_CTRL=0x3`;
- [ ] **IMPLEMENTING - COLD-INIT CANDIDATE** - finish the exact ABI-v3 build,
  then flash and read back its loader, Image, DTB, and metadata;
- [ ] **TESTING** - verify the expected boot ID, uptime progression, handoff
  version, source mode, front/back/spare generations, rearm progression, and
  exact artifact hashes;
- [ ] **TESTING** - verify stable full-frame content hashes and ownership states
  before and after normal graphics, signal-driven graphics, page flips, and
  console restoration;
- [ ] **TESTING** - complete a fresh same-boot 600-second USB-disconnected idle
  interval and prove the same Linux boot and advancing scanout after reconnect;
- [ ] **TESTING** - exercise SD writes, network traffic, USB attach/detach,
  framebuffer updates, page flips, and memory pressure while recording
  underrun, FIFO, SAR, host, PHY, rearm, ownership, and memory counters;
- [ ] **TESTING** - repeat cold/ROM reset and warm reset boot acceptance for at
  least three independent boots; and
- [ ] **TESTING** - preserve timestamped run logs containing artifact hashes,
  boot IDs, boundary snapshots, and final classifications.

Required physical tests:

- [ ] **TESTING** - observe loader-to-status with no black hang, cyan screen, or
  cyan flicker;
- [ ] **TESTING** - observe repeated complete-frame flips without tearing,
  partial frames, cyan, or stale content;
- [ ] **TESTING** - observe normal diagnostic-to-status and signal-driven
  diagnostic-to-status restoration without cyan;
- [ ] **TESTING** - observe the complete 600-second disconnected idle interval
  with no cyan, flicker, blanking, or corrupted status page; and
- [ ] **TESTING** - verify five-point touch and shell responsiveness on the same
  accepted candidate.

Exact reproducible hardware sequence from the repository root is below. The
first command needs fresh authorization because the ABI-v3 loader, Image, DTB,
and metadata differ from every rejected adopt-live candidate. After successful
flash/readback, run fresh reset-based `disconnect`, `preflight`, `stress`,
`touch`, and `soak` gates in that order:

```powershell
$py = 'C:\Espressif\python_env\idf6.0_py3.11_env\Scripts\python.exe'
.\scripts\m9.ps1 -SkipLinuxBuild -Flash -ConfirmExactKitC -Port COM13
& $py .\scripts\m9-run-logged.py -- --port COM14 --artifact-dir out\m9 --mode disconnect --disconnect-seconds 600
& $py .\scripts\m9-run-logged.py -- --port COM14 --artifact-dir out\m9 --mode preflight
& $py .\scripts\m9-run-logged.py -- --port COM14 --artifact-dir out\m9 --mode stress --stress-cycles 3
& $py .\scripts\m9-run-logged.py -- --port COM14 --artifact-dir out\m9 --mode touch
& $py .\scripts\m9-run-logged.py -- --port COM14 --artifact-dir out\m9 --mode soak --soak-seconds 600 --sample-seconds 15
```

The disconnected run must remain first after flash/readback. These five logged
runs each begin from a fresh ROM reset and exceed the three-boot minimum. A
human-observed power-cycle boot remains separately required for the cold-boot
visual gate. ABI v3 deliberately omits `vpg_test_ms`; preflight must verify that
the attribute is absent without attempting a source transition.

If no camera or human is watching the panel, record
`MACHINE-PASS / VISUAL-UNVERIFIED`; do not complete the section.

Exit criterion: every machine and physical item passes on the exact
triple-buffer artifact set.

### M9.2.5 - Ownership fallback and policy parity

Section status: **IMPLEMENTING - NATIVE LINUX COLD-INIT**

This section records the selected fallback. The ABI-v2 triple-buffer IRQ-rearm
engine failed at the inherited-source ownership seam, so patches 30 through 38
implement the only current M9 candidate: native Linux cold initialization.

- [x] **COMPLETED** - capture the failure without reset before any VPG or source
  transition can resynchronize it;
- [x] **NOT REQUIRED** - rerun the same frame engine in one-buffer diagnostic
  mode. The rejected idle state already had one fixed, unmodified front buffer,
  no queued flip, a stable full-frame hash, and the same per-frame descriptor
  rearm that a one-buffer mode would exercise;
- [x] **COMPLETED** - classify whether progress stopped in framebuffer memory,
  ownership rotation, GDMA, bridge/FIFO, DSI host, D-PHY, or only at the
  unobservable panel/glass. Framebuffer ownership, initial GDMA setup, and the
  bridge remained bounded, while the repeatable failure localized to the DSI
  host payload/FIFO boundary (`DPI_PLD_WR_ERR`); the panel/glass remains
  unobservable in software;
- [x] **COMPLETED - REJECTED** - test one coherent YamUI/IDF compatibility profile while
  retaining the same frame engine, triple-buffer pool, and 80/1500 timings;
- [x] **COMPLETED** - record automatic clock-lane, LP blanking, and frame-ACK
  differences explicitly rather than silently combining them with timing;
- [x] **COMPLETED - REJECTED** - correct automatic clock-lane control to retain both
  `PHY_TXREQUESTCLKHS` and `AUTO_CLKLANE_CTRL`, require raw `LPCLK_CTRL=0x3`
  in every policy gate, and retest without changing timing, buffers, or error
  masks;
- [ ] **IMPLEMENTED / TESTING** - publish a versioned, fail-closed loader relinquish
  contract recording ACKed PWM-zero, reset-prepare, and reset-assert commands,
  binding the DSI/GDMA reset-and-clock state, exact PMS windows, and IRQ route,
  with no M9 fallback to the inherited-source ABI-v2 path;
- [ ] **IMPLEMENTED / TESTING** - implement native Linux LDO, Waveshare reset/backlight
  I2C, D-PHY, DSI host, bridge, JD9365 command-table, GDMA, and interrupt
  initialization in explicit ordered stages. ABI-v3 probe, calibrated LDO/I2C,
  bounded command-mode DSI/panel programming, and qualified dark native
  scanout are implemented in patches 30-33; patch 34 implements post-render
  qualification, I2C reacquisition, backlight reveal, runtime ownership, and
  deferred touch registration; patch 35 validates the ABI's CLIC hardware IRQ
  against the hardware IRQ behind Linux's mapped virtual IRQ; patch 36 records
  the full GDMA enable-register tuple; patch 37 validates every documented
  hw_ver1 field at setup, runtime health, and teardown while ignoring only
  undefined positions; patch 38 admits only the physically released I2C state
  during dark scanout initialization while recording each scanout-start failure
  at its exact decision point; and patch 39 validates descriptor/configuration/
  head-LLP state before CHEN, then treats descriptor and LLP as hardware-owned;
- [ ] **PLANNED** - add stage-specific failure injection and prove every
  failure remains dark without leaving a hybrid loader/Linux hardware state;
- [ ] **IMPLEMENTED / TESTING** - command PWM zero and the safe reset sequence throughout
  cold init, reissue those commands before enabling the LDO, and reveal only
  after valid-source selection, clean-frame qualification, and health
  validation. Physical darkness remains an optical hardware gate, not an ABI
  readback claim;
- [ ] **TESTING** - bind the cold-init contract, ordering, state-machine,
  and negative cases into the host model and exact build/source-contract gates;
- [ ] **TESTING** - rerun the complete M9.2.4 gate after the authorized
  candidate, never accepting a machine-only result as physical success.

#### ABI-v3 cold-relinquish contract foundation

The cold-init candidate uses ABI version 3, boot mode 1 (`linux-cold-init`),
and the retained ownership state 1 (`linux-pending`). Its natural C layout is
exactly 192 bytes (`0xc0`) and is modeled as little-endian `<IHH46I>`. The
version and boot mode make its semantics incompatible with ABI v2. The frozen
offsets are:

```text
00 magic:u32       04 abi:u16          06 size:u16          08 flags:u32
0c boot_mode       10 ownership        14 silicon_revision  18 panel_profile_id
1c width           20 height           24 stride            28 pixel_format
2c pixel_clock_hz  30 lane_mbps        34 data_lanes        38 buffer_count
3c hsync_pulse     40 hsync_back       44 hsync_front       48 vsync_pulse
4c vsync_back      50 vsync_front      54 framebuffer0      58 framebuffer1
5c framebuffer2    60 framebuffer_size 64 descriptor_addr   68 descriptor_size
6c descriptor_cnt  70 dma_channel      74 dsi_fifo          78 gdma_irq_source
7c gdma_clic_irq   80 i2c_port         84 i2c_sda_gpio      88 i2c_scl_gpio
8c i2c_rate_hz     90 i2c_address      94 control_register  98 pwm_register
9c reset_assert_cmd a0 reset_prepare_cmd a4 reveal_cmd       a8 brightness
ac reset_hold_ms    b0 pwm_zero_settle   b4 payload_crc32    b8 sequence_id
bc crc32
```

The magic is `0x4d4e5844`, ABI is 3, size is `0xc0`, silicon revision is 103,
the JD9365 Waveshare 10.1 profile ID is 1, and RGB565 little-endian format ID is
1. The CRC is IEEE CRC-32 over bytes `0x00..0xbb`; the word at `0xbc` is not
included. ABI v3 requires the exact flag mask `0x00003fff` and permits no
unknown or optional bits:

| Bit | ABI-v3 meaning |
| --- | --- |
| 0 | RGB565 |
| 1 | Linux cold init |
| 2 | PWM-zero write ACKed |
| 3 | reset-prepare (`0x13`) write ACKed |
| 4 | reset-assert (`0x11`) write ACKed |
| 5 | DSI module reset asserted |
| 6 | D-PHY clocks and PMU LDO control disabled |
| 7 | GDMA reset asserted and clocks disabled |
| 8 | triple-buffer resources reserved |
| 9 | descriptor pool reserved |
| 10 | DMA PMS ready |
| 11 | GDMA IRQ route ready |
| 12 | display I2C released |
| 13 | runtime IRQ rearm required |

`DMA_PMS_READY` and `GDMA_IRQ_ROUTE_READY` appear only in the final published
contract. An intermediate contract missing either is never acceptable. Any
unknown bit, missing required bit, ABI-v2 version, or wrong boot mode makes M9
reject before its first display MMIO write; M9 does not fall back to adopt-live.

The immutable resource profile remains 800x1280 RGB565, 1600-byte stride,
80-MHz pixel clock, 1500 Mbps on two lanes, timing `20/20/40:4/10/30`, three
2,048,000-byte frames, GDMA channel 0, FIFO `0x50105000`, controller address
`0x45`, control register `0x95`, PWM register `0x96`, reset-assert/
reset-prepare/reveal commands `0x11`/`0x13`/`0x17`, 10-ms reset hold,
100-ms PWM-zero settle, and default brightness 63. GDMA routing is source 24
to CLIC IRQ 18. I2C port 0 uses GPIO7 SDA, GPIO8 SCL, and 100 kHz. The
panel-payload CRC is the canonical firmware payload CRC `0xcea07f9b`, and the
quiesce sequence ID is 1. The loader reserves but does not scan from a
4-KiB-aligned front resource in `0x48000000..0x48400000`; back and spare remain
fixed at `0x49300000` and `0x49500000`. It allocates a 4-KiB-aligned descriptor
page in the authoritative ESP-IDF DMA-capable internal-SRAM range
`0x4ff00000..0x4ffc0000`, outside the fixed SD-DMA reserve
`0x4ff80000..0x4ff82000`, clears the entire 4-KiB backing page, and publishes
only the 192 descriptor bytes and count 3 in the ABI. Linux validates all 4,096
backing bytes before rebuilding the three descriptors and initializes complete
framebuffer contents before selecting a source.

The RISC-V Image header `memory_size`, including BSS, is the authoritative
kernel span. For M7 and M9, the packer, build wrapper, and loader all enforce
`0x48400000 + memory_size <= 0x49300000` before allocation, test-write, load,
or zeroing. Image file size alone is not an acceptable overlap gate.

The external controller is write-only. Bits 2-4 mean that the loader completed
this ordered sequence with successful I2C transactions: PWM register `0x96`
to zero, control `0x95` to reset-prepare `0x13`, at least 100 ms settle,
control `0x95` to reset-assert `0x11`, at least 10 ms hold, then device and bus
release. They do not claim external register or physical-output readback.

Before mutation, Linux verifies only raw facts that remain readable: eFuse
silicon revision, DSI-module and GDMA reset controls, DSI/GDMA/I2C clock
controls, PMU LDO control, source-24-to-CLIC-18 routing, exact DMA PMS page
windows and masks, and the full zeroed descriptor backing page. It does not
pretend to read host/GDMA internals while their clocks are off and resets are
asserted, nor infer glass or backlight state from the write-only adapter.
Stage B must reissue the safe external command sequence before enabling the LDO
or changing display state. Any mismatch is a fail-closed `REJECTED_NO_WRITES`
probe result. The host model retains ABI-v2 tests for M7 while independently
proving that M9 rejects ABI v2 and every modeled readable-state mutation.

Current native cold-init validation evidence:

- the final combined host model passes 963 cases and binds the retained
  ABI-v2/M7 path, ABI-v3 contract, memory and ownership invariants, firmware,
  DTS, loader, patches 30-40, safe reveal and rollback, the ABI-v3 hardware
  parser, the patch-36 raw tuple, the patch-37 three-site semantic checks, the
  patch-38 dark-I2C/decision-point diagnostics and physical fixture, the
  patch-39 pre-enable/post-enable ownership split and physical fixture, the
  patch-40 active-mirror policy correction, and the fbdev test lifecycle.
  The exact model-source SHA-256 is
  `92f5212de5a3b6af97ccbd1428bd937bf0827c4cd4151cc9b78abcafb806ed87`;
- the ABI-v3 loader leaves DSI, GDMA, and LDO quiescent, publishes the exact
  cold contract only after PMS and IRQ-route readback, and contains no panel,
  splash, or scanout path. Its current 246,640-byte pinned-IDF v6.0.1 binary has
  SHA-256 `e314b558d923e8fa0175f9d4eb692ce5728eac3175053ca47c53775a0dbf9104`.
  The independently rebuilt legacy M7 loader remains ABI v2 and has SHA-256
  `a46518d0125a899bbd3608daa29a7bd43edaad52c43341ed33e101ae062e9de8`;
- the exact patch-37 hardware candidate used the preceding `[PATCH n/37]`
  subject packaging. Its patch-file hashes remain frozen as historical evidence:

  | Patch | `[PATCH n/37]` SHA-256 |
  | --- | --- |
  | 30 | `f80c259424a5ff76cf4506146857b5c85087d8eace8fb14b40ed17750f7bb9eb` |
  | 31 | `b71fc2a71e19b959e10b9f8fef28b448d7b3d29885a20b0fa8bbb0eeafaa5999` |
  | 32 | `62d8c1d33999910a5b7b1566bc735318488d5cf2c7d33509c014326647bbf4ac` |
  | 33 | `4dfd4a00367d73be1e89cb826078ecaa596c9482691efc131f03329d759d3df8` |
  | 34 | `cd552f09fb0cfcb021968fdb29465c66c838dc7effb72b5acb0c8fe4abec9763` |
  | 35 | `51e880b9805ef00fba7b3e924c80a6c34e72cb8222428c34e8e662dcf33dc70b` |
  | 36 | `3089ba5b6709b85091677fd0acf6a6533d67e9776bd770645f8379e32cf30623` |
  | 37 | `8c611fa187729769122a6ffb1ad67b68198a8d0df1ca74dff8392703c76faead` |

  That 7/37/10 series contained 54 patches with manifest SHA-256
  `65f4bbb6d94a37ff7e5fb3eacadb1dd7e3bad0a2beb0ee9759caa9e9d39ecc25`;
- the current post-renumber product patches are:

  | Patch | Native stage | Patch SHA-256 |
  | --- | --- | --- |
  | 30 | ABI-v3 fail-closed cold probe | `112d04909159eff3e6540f8c6f88b9a45a62fcfc08b94f684a922d75d84cabcf` |
  | 31 | native I2C and LDO setup | `870a8e50744e32f1bb573a534f5a23c06a297a2e93c462a8ba1b4ebd26cbc347` |
  | 32 | native DSI/JD9365 programming | `84aaa89206a94263ae9d81c0b6f1cd45d36b67940e4de45aaea25644d994eb82` |
  | 33 | native triple-buffer scanout and qualification | `9bad230d7140a2b6cf751aa545a22c458e97e4587a0c457d07c46b874bed302d` |
  | 34 | safe post-render reveal and runtime ownership | `a39505d7f706120f4273be1b57f915a88c6f1dd6c883799d855b90b3cea80872` |
  | 35 | map Linux virtual IRQ to the ABI-v3 CLIC hardware IRQ | `db8435aa7ec8d6fa82f807a1774c8d53a6e5fdc497b1b120bc02efc465751818` |
  | 36 | diagnose native GDMA enable-register readback | `eb08158fc589e9fdfb147666c3910e5f93f8466d7bcda55688ed6d9f108b6ef0` |
  | 37 | validate GDMA enable-register semantics | `4a3e83a17e8815c9976484530ffa3f20e643d2b42629e90e78c896098a87a6c8` |
  | 38 | diagnose native scanout qualification | `9a5a1615b12f8c34b115b8b34bf6b614624417d505580bc08a83a185f2708093` |
  | 39 | validate native DMA arm transaction | `57b7afcf1822d6b0d2137f029c085f9968e9a5946615f806ba909e8bd7f22bfb` |
  | 40 | stop gating scanout on inactive DSI mirror | `04df043b79760379cc8f4d0d74847d892e3895bc1b55eba3b90af23695647ed1` |

  Subject renumbering changes patch-file hashes but not their applied source.
  The current ordered 7/40/10 series contains 57 patches with manifest
  SHA-256
  `f9318e1a6e7480f1105ec5a435ad71d2754bb6d91a79bcc471747249128ed983`;
- patch 32 leaves the panel-programming boundary at exact state
  `PANEL_PROGRAMMED_QUIESCENT`: firmware and all 205 DCS packets are complete,
  but bridge DPI, the DPI clock, GDMA, framebuffer registration, IRQ, and
  illumination remain off. Its exact postimage SHA-256 is
  `c1877f0cdf2acc7a2a75ef55657c7ab12fd3c52783565c5e6b823f8a5859d3ce`.
  Strict checkpatch is 0/0/0 and the final W=1, warnings-as-errors object has
  SHA-256 `ae370b29dbafc3968b6235d9d0857ba1e599e9e4d7ddaae10f7b6f61e6a28e95`;
- patch 33 reaches exact state `SCANOUT_QUALIFIED_QUIESCENT` only after
  rebuilding three pinned-IDF one-frame descriptors (`CTRL_HI=0xc0108840`),
  initializing and verifying deterministic black triple buffers, starting
  stock-policy video with the GDMA interrupt master enabled only after a clean
  masked setup boundary, and passing two independent strict four-frame
  qualification windows. It registers fbdev and starts continuous health
  monitoring, but leaves backlight and touch unregistered and `boot_ready=0`.
  Its teardown closes and drains the render lifetime before cancellation,
  masks immutable interrupt sources, separates raw HP clock/reset containment
  from safe GDMA MMIO access, and publishes `REMOVING` before queued fault work
  can run. Its exact postimage SHA-256 is
  `6fab00aec20b280926ceed1e07ccbb70cd191a738783fcb47861b102554ba6c6`.
  Strict checkpatch is 0/0/0, exact fuzz-zero application passes, and direct
  and patch-applied W=1 warnings-as-errors objects are byte-identical at
  SHA-256 `2d98c9f5a69654269303fdabf8abc978c6bc235cc22254a6fe833ef94066d28b`;
- the rootfs now contains a bounded, fail-headless handshake from
  `PROBED_QUIESCENT` through `cold_init` to
  `SCANOUT_QUALIFIED_QUIESCENT`, and will not render or request `boot_ready`
  before `/dev/fb0` is registered. It latches that qualification, rechecks the
  native state after the complete tty1 render immediately before `boot_ready`,
  requires synchronous success to publish `RUNTIME_REVEALED`, and continues
  PID 1 in headless USB-shell mode on every render or reveal failure. The full
  build also requires the installed `/init` to be byte-identical to this
  model-bound source;
- patch 34 consumes `boot_ready` only from
  `SCANOUT_QUALIFIED_QUIESCENT`, commits the rendered status buffer, proves its
  presentation plus four fresh same-front completions, reacquires I2C through
  a serialized transition, and reveals from the retained, ACKed PWM-zero state
  in the order control `0x17` -> PWM 63. It then publishes
  `RUNTIME_REVEALED`, registers backlight, and defers touch registration.
  Failure rolls back toward PWM zero/control
  `0x13`, preserves a valid source when darkness cannot be confirmed, and
  requires reboot. The exact stage-E postimage SHA-256 is
  `523e43b8bbbc4495033eaedf0aafc2539a127c836b2586f9c13e9e54f17694de`.
  Strict checkpatch is 0/0/0 and its W=1 warnings-as-errors object SHA-256 is
  `e806298f10aac0309d8524377b9692c4af173773b2e1f054d4f1ac0209a5e881`;
- patch 35 corrects the IRQ namespace check discovered by the first ABI-v3
  hardware probe. It obtains `irq_data` for Linux virtual IRQ `dsi->irq` with
  `irq_get_irq_data()`, maps that value through `irqd_to_hwirq()`, and compares
  the resulting hardware IRQ to the ABI-v3 `gdma_clic_irq`. It does not alter
  native display programming or relax any other peripheral-contract check. In
  its frozen `[PATCH 35/35]` hardware-candidate packaging, its exact patch
  SHA-256 is
  `0afb7be2a0c6e53f2d67c0d5be7b58c5ff92c511afa53b10b1dba74bd1545d61`,
  final driver-source SHA-256 is
  `585c13ccc343737739d21711280c801cf608460a8ab45665076de714d136f5f9`,
  and warnings-as-errors object SHA-256 is
  `045259262ee9b4b793b3cc22efb73d6b57c2c50859987fc65ba2c510779df3e5`;
- patch 36 adds diagnostic reads only: HP clock/reset state, `request_irq()`
  failure details, and one complete GDMA configuration tuple. It changes no
  write, comparison, return, reveal, or containment policy. The exact flashed
  `[PATCH 36/36]` file SHA-256 is
  `c84cd6fc2ef69d372339b79853480b8b77dd3ec1855e2dd0f1ae89a0b966ae86`,
  its applied driver-source SHA-256 is
  `7af3f6a8e6d965c9c0a0f1d14d85eed37ab95e62ea9734d3c09833282512aeeb`,
  and its W=1 warnings-as-errors object SHA-256 is
  `fb07a174d4d0c4d56474bc65379af6203f1cea086b074092e33f20e5bd4e036f`.
  Adding patch 37 renumbered only this patch's subject to `[PATCH 36/37]`,
  patch 38 renumbered it to `[PATCH 36/38]`, and patch 39 renumbered it to
  `[PATCH 36/39]`. The current patch-file
  SHA-256 is the table value above while its applied postimage remains the
  same;
- patch 37 replaces exact full-word GDMA interrupt-enable comparisons with
  semantic comparisons in all three consumers: initial configuration,
  `micronux_native_runtime_policy_valid()`, and teardown containment. Status-0
  uses documented-field mask `0xfa3f7ffb`, status-1 uses `0x0000000f`, and
  common enable uses `0x001fff8f`; setup/runtime still require documented
  values `0x023f7fe2`, `0x0000000f`, and `0x001fff8f`, while teardown requires
  `0x02000000`, `0x0000000f`, and `0x001ffe80`. Configuration, channel state,
  programming, diagnostics, and every documented bit remain strict; only
  undefined positions observed as one are ignored. Its commit is
  `39e452858f8e9ba130bf9594b75836527833d3e4`. Its frozen `[PATCH 37/37]`
  patch SHA-256 is
  `8c611fa187729769122a6ffb1ad67b68198a8d0df1ca74dff8392703c76faead`;
  subject renumbering for patches 38 and 39 produces the current table hash without
  changing its applied source. Its
  applied driver Git blob is `520b502e4c6c7782d1d0b2c2ec204edc5781e4b1`,
  applied driver-source SHA-256 is
  `3f6de2a2c67fd0bb172bd7d4927b886ee7d3a6fd7e2a2366a6d80c1ce55aed8d`,
  and W=1 warnings-as-errors object SHA-256 is
  `8dd3502af6ca0b8174a57d45ca204bcb5451838923a0dfee12a3e0c25ebe1dc5`;
- patch 38 makes one narrow policy correction: while the display is
  `SCANOUT_INITIALIZING`, runtime policy accepts only
  `MICRONUX_NATIVE_I2C_RELEASED` plus the existing physical clock-and-pin
  release predicate. `TRANSITION` and `ACTIVE_SERIALIZED` remain rejected in
  the dark state. It also records the clean precheck, global-enable, descriptor
  arm-readback, and qualification-window failure boundaries without changing
  hardware programming, success predicates, errors, or fail-dark teardown.
  Its commit is `397b8bed56b6165251011f0109ded884d1bd0fe2`; its exact flashed
  `[PATCH 38/38]` patch SHA-256 is
  `8471fcb6b9ec9656b82dc44c29a790c9f70a1d306a16d4b8b34fe7f04f96f177`,
  applied driver-source SHA-256 is
  `3aa68ea94d60385476fd3b5817f493cfeaeb38d915c4ee643bdda54d5273f65e`,
  and its standalone W=1 warnings-as-errors object SHA-256 is
  `55736ed8b0df502b9dc1c31440a596f7b5c81797bc4020b8d373cffa14f3f666`.
  The fresh full build's 153,576-byte in-tree object has SHA-256
  `a9439a55fbf0f47b4977f3a98e1e8ff14227c238a45a8ef97fe4c1fbe3aebb7d`,
  and the linked `vmlinux` contains the patch-38 phase strings and symbols.
  The independent semantic audit is clear, strict checkpatch is 0/0/0, and
  exact fuzz-zero application passes. The fresh full M9 build passes with source
  contract
  `2744f3880fb5c0ec8807beb7dcf2279fff104c68aa396a62e96028b68e765c4f`;
  the clean standalone M7 rerun passes with source contract
  `be29c1583b68ae18167e60ee444fba39233b00184aeb859f0784c21dfcb6be58`.
  The Windows no-flash verifier also passes with the 246,640-byte loader at
  SHA-256 `e314b558d923e8fa0175f9d4eb692ce5728eac3175053ca47c53775a0dbf9104`
  and explicitly reports `Nothing was flashed`. Patch 38 subsequently passed
  exact four-region flash/readback under
  `build/m9-readback/20260812T125425532Z-95d60702`; `readback.json` has SHA-256
  `702ba99d9ca84bf25593301c300575a9706294dcae62f8db2ef982336e337a46`;
- patch 39 makes the DMA-arm validation phase-correct. Before CHEN it requires
  the channel disabled, programs and exactly reads back descriptor `CTRL_HI`,
  channel `CFG_LO/CFG_HI`, head LLP and its zero high half, and requires clean
  channel/common/top status. After CHEN, descriptor and LLP are hardware-owned;
  only channel-active and error/status safety are validated. The shared initial
  and IRQ-rearm helper remains non-sleeping, with the IRQ caller passing a null
  diagnostic snapshot, and no active-mirror, runtime-health, qualification, or
  fail-dark policy is relaxed. Its commit is
  `99fcff145f84b22c8996e7cab8ce9a77abe472e7`, exact patch SHA-256 is
  `d8c774f42019dddeba6020ec019b4fd61474dec3c2913c33b5c2ad191deabe94`,
  applied driver-source SHA-256 is
  `68378f2ca532ec36020a3a99a57c2b8a909b694cb11d613764427acd76c7de0c`,
  and its standalone/in-tree W=1 warnings-as-errors object SHA-256 is
  `b86cbf98fe2e2d2b894928049d9110cafedb290fbd1b2257285e06143997331a`.
  Strict checkpatch is 0/0/0, exact fuzz-zero application and W=1
  `KCFLAGS=-Werror` pass, and the independent result is
  `PATCH39 INDEPENDENT SEMANTIC AUDIT: CLEAR — no blocker.` The historical
  7/39/10 series checker and 920-case model passed. The clean
  Patch-39 M7 ABI-v2 regression passes source contract
  `112e8a0afef2f14151a8fcc9e5f8c054e671370d47b8ae042d87a15e52b00ca4`,
  the 14-file bFLT W^X audit, and exact driver source/object gates
  `68378f2ca532ec36020a3a99a57c2b8a909b694cb11d613764427acd76c7de0c` /
  `b86cbf98fe2e2d2b894928049d9110cafedb290fbd1b2257285e06143997331a`.
  Its retained stdout log is
  `out/build-logs/m7-patch39-final-rerun-20260812T063011.stdout.log`, SHA-256
  `4ccc716b3ca1d9c9de013eb59e852fd0d876d58cd4f222792fce89610242d382`.
  Core outputs are Image 6,090,800 B
  (`cc421483552187e8bfcfe6bd48c601e9f7e5d9a90792b4d49884960912b624fc`),
  ABI-v2 DTB 2,610 B
  (`42e3ac2fadcbeda59a13ee3cfd4afb490607e13185b7db48b2c3ee1fd00a4fe6`),
  metadata 128 B
  (`e602c1e6ea76c82f6ca20efdc16ee6d270b6f1fb52220f193894d101df6f8fe1`),
  and rootfs 1,795,584 B
  (`a45407b14685b3ceca45e5dd647d3db98286b0a48cc43128be502b64b2235ffe`).
  The payload ends at `0x48a0cd08`, below the protected display pool. This is a
  build regression, not new M7 physical acceptance. The fresh Patch-39 M9 full
  build passes source contract
  `e9c1e789fe87a5a7735c62785ff0113bca61c9d924e964c0a39ce30d69ab5fba`.
  Its retained stdout/stderr logs are
  `build/patch39-m9-build/m9-full-final-20260812T062855.stdout.log`
  (`66767316f4a0d528ca1ac036a2066b9ce0569ff597c542b035b91e2538ee6678`)
  and `build/patch39-m9-build/m9-full-final-20260812T062855.stderr.log`
  (`5726aa86574e9795e42a63809ff9d933bd53e1cab608b094b4e310b1ef7c8a60`).
  The Windows no-flash verifier passes with the 246,640-byte loader SHA-256
  `e314b558d923e8fa0175f9d4eb692ce5728eac3175053ca47c53775a0dbf9104`
  and exact terminal:
  `M9 Linux and loader build validation passed. Nothing was flashed.` Its
  stdout/stderr logs are
  `build/patch39-m9-build/m9-noflash-final-20260812T063226.stdout.log`
  (`773a31cdc8ae7223f3bea628a1cbd7f23e1d9e1be0dea62037e2dd6138010586`)
  and `build/patch39-m9-build/m9-noflash-final-20260812T063226.stderr.log`
  (`5eafaa464b0d67805ad0f9f665d7b63cb4afce444b640a10a474e44be2068d5b`).
  The independent final audit passes all seven artifact hashes, metadata,
  13 ABI-v3 DTB checks, rootfs identities, driver identity, loader/config, and
  pinned ESP-IDF identity. Patch 39 then passed exact four-region
  flash/readback under
  `build/m9-readback/20260812T134841478Z-1a4e56fe`; `readback.json` has SHA-256
  `691fd9ac949de313a74c5591870bd3a95769baeae7f8c7be7d22dd429be61c09`.
  Its sealed passive snapshot
  `out/m9/hardware-runs/20260812T135132Z-snapshot-b5890811aa40-2b712727.log`
  has SHA-256
  `9f4cb506b955c9e0b2fbfbbaf3927271de3eef40df7b0e1b0a67a3991d3228ac`.
  Linux completed four frames with `generation=4`, `frames=4`, `rearm=5/0`,
  `guards=1`, and zero host, bridge, GDMA, DMA, or software fault status. Exact
  programmed policy was `vid=0000ff02`, while the optional active shadow mirror
  remained `active=00000000`. Runtime policy returned false solely on that
  inactive mirror and containment published `FAILED_QUIESCENT` with the
  headless shell available. The log records `optical-state=unobserved`; this is
  machine evidence of healthy frame progress, not visual panel evidence;
- patch 40 removes exactly the three-line
  `DSI_HOST_NATIVE_VIDEO_POLICY_ACT` composite and its two-line runtime
  equality: zero source additions and five source deletions. The active
  register offset, component masks, snapshot read, and failure log remain for
  diagnosis. Exact programmed `VID_MODE_CFG=0000ff02` and the other 40 runtime
  predicates remain required, for 41 predicates total; no write, teardown,
  reveal, or ABI-v2 behavior changes. This matches pinned ESP-IDF v6.0.1, which
  programs the primary register, never writes `vid_shadow_ctrl`, and leaves the
  optional video shadow bank disabled at its zero default. The product file is
  `buildroot-external/board/micronux/patches-peripherals/linux/0040-video-fbdev-stop-gating-scanout-on-inactive-DSI-mirror.patch`.
  Its commit is `0fac40ee31c1af165ea94a82a7b0d905d8da4861`, exact patch
  SHA-256 is
  `04df043b79760379cc8f4d0d74847d892e3895bc1b55eba3b90af23695647ed1`,
  post-source SHA-256 is
  `13088b8fefe6cf8ca415615e2cf8e9e62a6f3d972d2f010c49965139924a5164`,
  and W=1 warnings-as-errors object SHA-256 is
  `845e536b031622f29cc8d1e157c1e284e453f8fde8d053a2dba20182368dcdd1`.
  Strict checkpatch, fuzz-zero apply, compilation, and the independent semantic
  audit are `CLEAR`. The current 7/40/10 checker passes all 57 patches with the
  manifest above, and the shared model passes 963 cases;
- the clean isolated Patch-40 M7 ABI-v2 regression passes source contract
  `6b1d5d730c7370f364fed80c507f8ba8454c52b9ee247a22cde96a54233d8206`.
  Retained stdout
  `out/m7/build-logs/20260812T072708Z-patch40-clean.stdout.log` is 5,811,656 B
  with SHA-256
  `9bddb74e3734cd977bd3b98a28cab29e75a626e6483219ccf88cca418fc47c05`.
  Its exact in-tree driver source/object hashes are the Patch-40 values above.
  The ABI-v2/legacy DTB checks pass, as does the 14-file, 128-byte-granule bFLT
  W^X audit. The clean M7 artifacts are:

  | Artifact | Size | SHA-256 |
  | --- | ---: | --- |
  | `Image` | 6,090,800 B | `ca34bd104c670427bc7567991056eb9f423cd875290bd238050b384c71454f27` |
  | `esp32p4-micronux.dtb` | 2,610 B | `42e3ac2fadcbeda59a13ee3cfd4afb490607e13185b7db48b2c3ee1fd00a4fe6` |
  | `metadata.bin` | 128 B | `a1f0c6047b453846831eca90ab6d6675a3933e369fa479d138c4331b97741728` |
  | `rootfs.cpio` | 1,795,584 B | `1721822da26deba72d2952af1d5bade5c9d8b7eeb8d6d6c4616a83c2ff8ba3ae` |
  | `linux.config` | 48,544 B | `f07d9a0c6559ab6fdac08833dcddbdf883c70098f96cc7c42a2faeae9915b6d5` |
  | `vmlinux` | 6,992,296 B | `4b289d46bbe26e703c7606b21dfb3462be17b00fe0d6533665a9d3e7b3d38673` |

  `SHA256SUMS` has SHA-256
  `cf558e8432587699d4e03e6a9db65f1dfaa6bcf34bbb2848206559d424795a2e`,
  and all 18 entries rehash. Metadata CRC32 is `901bf054`; the payload ends at
  `0x48a0cd08`, below `0x49300000`. This was build-only: no COM access, reset,
  or flash occurred, and it is not new physical acceptance;
- the fresh Patch-40 M9 full build passes source contract
  `4f21d0c9e47cc0c003ccbfe9db8c558e18fcf1cf82cceb1e50319661d6ce2686`.
  Retained stdout
  `build/m9-patch40-build-20260812T142636539Z/m9-build.stdout.log` has SHA-256
  `0c15ef3f2c0e40f531b77a12cdf1ec5731272005b2488d96789348bd2319cb6e`.
  The Windows no-flash verifier retains stdout at
  `build/m9-patch40-noflash-20260812T143141516Z/m9-noflash.stdout.log`, SHA-256
  `4b64a06e8d65391d5fab3182cdf864fb815a455fe06e6be91c0cf9724f7f3e6e`,
  and terminates with `Nothing was flashed`. The exact 246,640-byte loader
  remains
  `e314b558d923e8fa0175f9d4eb692ce5728eac3175053ca47c53775a0dbf9104`;
- the final Patch-40 M9 artifact audit validates all seven current artifacts:

  | Artifact | Size | SHA-256 |
  | --- | ---: | --- |
  | `Image` | 6,164,592 B | `5fa57b2ad5da12182cc96f850e55e2cbb7dfae1291c77d92d697947e373eb7e0` |
  | `esp32p4-micronux.dtb` | 2,986 B | `4e0a9bad1fb894557da5b68d83f8a9c4cd8dd5fbc73466d1d512744b2f6c2e39` |
  | `metadata.bin` | 128 B | `77f7afdc0c46bc7d673d52ed93cfc2a4002258172b599468e9f88661583e5ee1` |
  | `rootfs.cpio` | 1,912,832 B | `ccc865e0cd601fc797dd33ef9e4c7a7b966d4399fcb618288bf02b66d3c73274` |
  | `linux.config` | 48,533 B | `4c7bc00ebf4234dec58b13414d71f21818791cf8522a97fbb8f41981ecaef058` |
  | `vmlinux` | 7,067,044 B | `c701652aad4b1924b25b87c4a7fe849418fbcebfc3bd49cfeb204a03ff66bb9b` |
  | `micronux-display-test` | 102,312 B | `f689fe1847a1ea05095abc0452051345329f3ff7723caa1574e4cfdb19bc4577` |

  Metadata CRC32 is `e1509e49`; embedded Image and DTB hashes match. The
  payload ends at `0x48a1ed08`, below the display pool at `0x49300000`. Patch
  40 has not been flashed; its runtime and user-observed visual acceptance are
  pending;
- the final Patch-39 M9 artifact audit validates all seven frozen historical
  artifacts:

  | Artifact | Size | SHA-256 |
  | --- | ---: | --- |
  | `Image` | 6,164,592 B | `cb604a5eb2e9931aab3a6dfc7ff7640bd62264721e7f52147305fdf77592e612` |
  | `esp32p4-micronux.dtb` | 2,986 B | `4e0a9bad1fb894557da5b68d83f8a9c4cd8dd5fbc73466d1d512744b2f6c2e39` |
  | `metadata.bin` | 128 B | `c0679c60f450eed47197f3ca48a016936ed69352040e2aa909fa88b9eec77342` |
  | `rootfs.cpio` | 1,912,832 B | `ccc865e0cd601fc797dd33ef9e4c7a7b966d4399fcb618288bf02b66d3c73274` |
  | `linux.config` | 48,533 B | `4c7bc00ebf4234dec58b13414d71f21818791cf8522a97fbb8f41981ecaef058` |
  | `vmlinux` | 7,067,044 B | `d37be57be9cbd19474d9487b937fa5f6742a7c16314bf7ec4fee51f35131b346` |
  | `micronux-display-test` | 102,312 B | `f689fe1847a1ea05095abc0452051345329f3ff7723caa1574e4cfdb19bc4577` |

  Metadata CRC32 is `90446eba`; embedded Image and DTB hashes match. The
  payload ends at `0x48a1ed08`, below the display pool at `0x49300000`;
- the final patch-38 M9 artifact audit validates all seven frozen historical
  artifacts:

  | Artifact | Size | SHA-256 |
  | --- | ---: | --- |
  | `Image` | 6,164,592 B | `2be17be1c98292d61f9dc5244eb6f2b63eb7be4e14451c54a3f24338a0a747f5` |
  | `esp32p4-micronux.dtb` | 2,986 B | `4e0a9bad1fb894557da5b68d83f8a9c4cd8dd5fbc73466d1d512744b2f6c2e39` |
  | `metadata.bin` | 128 B | `06fa429011c29f9df537c369fe10f160b919f931f8a4e8aaac5320acc446f018` |
  | `rootfs.cpio` | 1,912,832 B | `ccc865e0cd601fc797dd33ef9e4c7a7b966d4399fcb618288bf02b66d3c73274` |
  | `linux.config` | 48,533 B | `4c7bc00ebf4234dec58b13414d71f21818791cf8522a97fbb8f41981ecaef058` |
  | `vmlinux` | 7,067,044 B | `8ef508a0f5f12eddfcf70d73a25f0e2887f9b9e64bf927f26c1ed74d58454288` |
  | `micronux-display-test` | 102,312 B | `f689fe1847a1ea05095abc0452051345329f3ff7723caa1574e4cfdb19bc4577` |

  Metadata CRC32 is `0147ef89`; its embedded Image and DTB hashes match. The
  payload loads at `0x48400000`, has file size 6,164,592 and memory size
  6,417,672, and ends at `0x48a1ed08`, below the display pool at `0x49300000`.
  The DTB audit confirms ABI 3, silicon `0x67`, handoff `0x49f00000`, the
  4-MiB no-map pool at `0x49300000`, and all 11 ordered cold-init resources.
  Rootfs identities are `/init`
  `28bd68e609e0defc4d934d7a0a7c860a446b015c30ac739b3b775cfc4b0003fe`,
  firmware
  `5f5d5d5fde2471130c3508160763235320854beb561dda049d066475e2bbf0f3`,
  license
  `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`,
  and provenance
  `7e22738646bec669a15b83b0f9570459fffde956b1f4a8f33c810bae97ec4e2f`;
- the fresh patch-38 M7 rerun completes its isolated build and 14-file bFLT W^X
  gate, and all 18 entries in its `SHA256SUMS` verify. Core artifacts are Image
  6,090,800 B
  (`a1e8f84da689bc24d3eb557b3eac06a9910b9360af84d2bcd7e7872a943d2cf5`),
  ABI-v2 DTB 2,610 B
  (`42e3ac2fadcbeda59a13ee3cfd4afb490607e13185b7db48b2c3ee1fd00a4fe6`),
  metadata 128 B
  (`5140610ccd3cc3b5eb15c0269a2fe6952d60198de71a3768e3529c131a83f2d6`),
  and rootfs 1,795,584 B
  (`a45407b14685b3ceca45e5dd647d3db98286b0a48cc43128be502b64b2235ffe`);
- ABI-v3 runtime sysfs deliberately omits `vpg_test_ms`. Preflight verifies
  absence and unchanged framebuffer/health state; no native VPG transition is
  part of acceptance;
- the prior clean patch-35 M9 build passed with source contract
  `cd5fd9426e85b7fa31cbfb99e81ebac210d5affdd6244a2b226885127084021b`.
  Its seven rehashed artifacts are: Image 6,156,400 bytes
  (`43ac78af9ace49de532a733905f625e9874207859e9c701796e315224f4be0d3`),
  DTB 2,986 bytes
  (`4e0a9bad1fb894557da5b68d83f8a9c4cd8dd5fbc73466d1d512744b2f6c2e39`),
  metadata
  (`e88097712dab2f61bd9822a880054483bd547020e6653b501ba6abe7059f31a8`),
  rootfs
  (`ccc865e0cd601fc797dd33ef9e4c7a7b966d4399fcb618288bf02b66d3c73274`),
  Linux config
  (`4c7bc00ebf4234dec58b13414d71f21818791cf8522a97fbb8f41981ecaef058`),
  vmlinux
  (`ea7f7c176186196dd9f63a912c84e31ffc6d43175a3793c68e03efffb56160c7`),
  and display test
  (`f689fe1847a1ea05095abc0452051345329f3ff7723caa1574e4cfdb19bc4577`).
  The packed kernel ends at `0x48a1cd08`, below the display pool;
- the prior clean patch-35 M7 regression passed with source contract
  `f50839126830663d380d8679979e3cc3843e6ce574fe6aaa57297e3729867fa2`,
  ABI-v2 DTB, the same audited driver postimage/object, and no M9 cold-init
  resources. The independent final preflash audit found no software or
  artifact blocker;
- the prior patch-37 M9 build passed with source contract
  `2ad7225219f37ac8052d67823b19ba6c302bdb9d20f916c4274e5bee2d847e09`.
  The exact `out/m9/SHA256SUMS` artifacts are:

  | Artifact | Size | SHA-256 |
  | --- | ---: | --- |
  | `Image` | 6,156,400 B | `50a6bdef0330f300b4295fad3199c1950e6beba6ac90084a16954dfbbe9ad7d2` |
  | `esp32p4-micronux.dtb` | 2,986 B | `4e0a9bad1fb894557da5b68d83f8a9c4cd8dd5fbc73466d1d512744b2f6c2e39` |
  | `metadata.bin` | 128 B | `e36b7ae5792014366f87689598fad0c7aa067dd0f98c9b414ee4acfd8ca259c1` |
  | `rootfs.cpio` | 1,912,832 B | `ccc865e0cd601fc797dd33ef9e4c7a7b966d4399fcb618288bf02b66d3c73274` |
  | `linux.config` | 48,533 B | `4c7bc00ebf4234dec58b13414d71f21818791cf8522a97fbb8f41981ecaef058` |
  | `vmlinux` | 7,058,696 B | `9d10df06a14d1a7f012f3811215d9fbb6e4f73163860e260657edbab6fa8fd98` |
  | `micronux-display-test` | 102,312 B | `f689fe1847a1ea05095abc0452051345329f3ff7723caa1574e4cfdb19bc4577` |

  The packed kernel still ends at `0x48a1cd08`, below the display pool. The
  complete series/model/build markers are retained in
  `out/build-logs/m9-patch37-final.stdout.log`;
- the fresh patch-37 M7 regression passes the same 7/37/10 manifest and
  716-case model, retains the ABI-v2 DTB and isolation profile, binds the exact
  patch-37 driver source, and passes the 14-file bFLT W^X audit. Its output is
  retained in `out/build-logs/m7-patch37-final.stdout.log` (SHA-256
  `33895fbaeac176abdfd2faf91395f105ffd5bd901bba452329d381d2205cc43f`).
  Core rebuilt artifacts are Image 6,090,800 bytes
  (`f27cbcc4a225e6075f2dc295acaf477f29dc289947ada9821d67c1b7fc88f630`),
  DTB 2,610 bytes
  (`42e3ac2fadcbeda59a13ee3cfd4afb490607e13185b7db48b2c3ee1fd00a4fe6`),
  metadata
  (`e6f01160d24ab0bb9b38421c53f6577229e7dd36dac0ba37f6a8f8ef15807f1c`),
  and rootfs
  (`a45407b14685b3ceca45e5dd647d3db98286b0a48cc43128be502b64b2235ffe`).
  This is a software regression, not a new M7 physical run;
- the first ABI-v3 patch-34 candidate passed exact flash/readback for loader,
  Image, DTB, and metadata. Evidence is preserved under
  `build/m9-readback/20260812T101129064Z-b989ae87`. Its fresh `disconnect` gate
  then rejected safely before native display access with
  `COLD-PROBE state=REJECTED_NO_WRITES reason=peripheral-contract
  display-writes=0`. The 192-byte ABI and CRC were valid. The failure was a
  software namespace error: ABI field `gdma_clic_irq=18` is a hardware IRQ,
  while `dsi->irq=3` is the Linux virtual IRQ assigned to that hardware source;
  the driver had compared those raw numbers. The sealed transcript is
  `out/m9/hardware-runs/20260812T101438Z-disconnect-b5890811aa40-17b22bf0.log`
  with SHA-256
  `213efc9aa9dcdaad9151eab1cf0837fc6f240055ee5bb62597dcc124fbe8f33e`.
  This run passed no runtime or physical acceptance gate;
- the corrected patch-35 candidate also passed exact flash/readback for the
  246,640-byte loader, Image, DTB, and metadata, with evidence under
  `build/m9-readback/20260812T105141468Z-8e3b183a`. Flash/readback proves only
  the programmed bytes. Corrected runtime testing, user-observed panel output,
  disconnect, preflight, stress, touch, and soak acceptance all remain open;
  internal register state and ACKed writes still cannot observe external panel
  light or pixels;
- the diagnostic patch-36 M9 build passed with source contract
  `611a295961cd8137bc3c407f054eea5ccf9b9a9914bd8e029a206e308a02fc69`
  and was then flashed with exact readback. Evidence is retained at
  `build/m9-readback/20260812T113127315Z-89b121f9/readback.json`; that JSON has
  SHA-256
  `8e981cd3cb1c98c2093fa8241847dbded5bf6f21954ec4f42373dfa063063746`.
  All four entries match: loader 246,640 bytes
  (`e314b558d923e8fa0175f9d4eb692ce5728eac3175053ca47c53775a0dbf9104`),
  Image 6,156,400 bytes
  (`ec72ade9bc53b9f451137a88aa61709172259da81b6372e57c522acc2bff54d9`),
  DTB 2,986 bytes
  (`4e0a9bad1fb894557da5b68d83f8a9c4cd8dd5fbc73466d1d512744b2f6c2e39`),
  and metadata 128 bytes
  (`5cec3d38105ba2e13ec859af31ebaf9051380d445d5a46e478ab06df354cae28`);
- the passive, no-reset patch-36 snapshot is sealed at
  `out/m9/hardware-runs/20260812T113412Z-snapshot-b5890811aa40-e010429e.log`
  with SHA-256
  `10fbd8afa3468858d37958e6b879723d40c783defe82abf6f8761216dcf47ce4`.
  Linux completed the ABI-v3 probe, I2C/LDO, D-PHY, panel programming, memory,
  bridge, and GDMA writes, then recorded this exact readback tuple:

  ```text
  cfg=00000001 cfglo=0000000f cfghi=0a020001
  stena0=07ffffe6 stena1=ffffffff
  sigena0=07ffffe6 sigena1=ffffffff
  common-stena=ffffffff common-sigena=ffffffff chen=00000000
  ```

  The defined fields match the requested active values, while undefined
  positions read as one. Because patch 36 still compared full words, it
  published `FAILED_UNVERIFIED stage=gdma-irq error=-5`, unregistered fbdev and
  the IRQ, ACKed PWM-zero/reset containment, cleared LDO3 XPD, and continued
  with `mode=headless` and the USB shell ready. This is the recorded fail-dark
  software outcome; the same log says `optical-state=unobserved` and therefore
  does not prove a black, lit, or otherwise correct physical panel;
- the corrective patch-37 candidate was flashed with exact readback under
  `build/m9-readback/20260812T120632185Z-2993c451`. Its passive, no-reset boot
  evidence is sealed at
  `out/m9/hardware-runs/20260812T120919Z-snapshot-b5890811aa40-0611024a.log`
  with SHA-256
  `52ebc5ed26487e68419411f73cd263566e3fb8a47920e1b67f33f150d21d94b6`.
  Linux passed the corrected GDMA semantic readback and logged
  `stage=gdma-irq state=CONFIGURED`, then deterministically rejected scanout
  qualification because `micronux_native_i2c_policy_valid()` did not yet admit
  the correctly released-and-gated I2C state while the display remained
  `SCANOUT_INITIALIZING`. Existing containment ACKed the external off commands,
  stopped and reset DSI/GDMA, released I2C, cleared LDO3 XPD, unregistered fbdev
  and the IRQ, published `FAILED_QUIESCENT`, and kept the USB shell available in
  headless mode. The log records `optical-state=unobserved`; it is not evidence
  of black, lit, or correct panel output;
- patch 38 corrects that exact state-policy defect and adds diagnostic snapshots
  without relaxing the dark-state I2C contract. Its four-region flash/readback
  passed exactly under
  `build/m9-readback/20260812T125425532Z-95d60702`; the retained
  `readback.json` has SHA-256
  `702ba99d9ca84bf25593301c300575a9706294dcae62f8db2ef982336e337a46`.
  Loader, Image, DTB, and metadata readback hashes all equal their host hashes.
  The passive, no-reset snapshot is sealed at
  `out/m9/hardware-runs/20260812T125714Z-snapshot-b5890811aa40-3670b4ef.log`
  with SHA-256
  `1c43ac04f307de252342fdc4cdc49d3aa6f18520be5cca254d4e7df294b677ac`.
  Linux completed I2C/LDO, D-PHY, all panel commands, memory, bridge, and GDMA
  configuration, then recorded:

  ```text
  reason=arm-readback detail=descriptor-channel-readback error=-5
  generation=0 frames=0 faults=0 dma-error=00000000 rearm=1/1 guards=1
  host raw=00000000:00000000 sticky=00000000:00000000
  mode=00000001 vid=0000ff02 active=00000000 lpclk=00000003
  bridge-raw=00000000 underruns=0
  gdma=00000000:00000000:00000000:00000000 cfg=00000003 chen=00000001
  cfglo=0000000f cfghi=0a020001 llp=00000001 sar=48031500 ctrlhi=c0108840
  ```

  Every captured fault status is zero, CHEN is active, configuration and armed
  `CTRL_HI` are exact, live LLP is `1`, and SAR has advanced to `0x48031500`
  (`front+0x500`). The terminal singly linked descriptor has zero next-address
  with memory-master bit 1, so GDMA had fetched the programmed head and advanced
  hardware-owned live LLP to `0 | 1`. Patch 38's post-enable
  `LLP == head | memory-port` predicate therefore rejected successful hardware
  consumption; it did not diagnose a descriptor-programming failure. Existing
  containment ACKed the external off commands, stopped and reset DSI/GDMA,
  released I2C, cleared LDO3 XPD, unregistered fbdev and IRQ, published
  `FAILED_QUIESCENT`, and kept the headless shell available. The snapshot says
  `optical-state=unobserved`, so it is not evidence of a black, lit, or correct
  panel;
- patch 39 corrects only that software/hardware ownership boundary: exact arm
  readback occurs with CHEN disabled, and the post-enable phase no longer reads
  or compares descriptor/configuration/LLP state for acceptance. Its historical
  7/39/10 series checker, 920-case model, strict style/object checks, fuzz-zero
  apply, semantic audit, M7 ABI-v2 regression, and M9 build gates passed. Exact
  flash/readback under `build/m9-readback/20260812T134841478Z-1a4e56fe` and the
  sealed snapshot
  `out/m9/hardware-runs/20260812T135132Z-snapshot-b5890811aa40-2b712727.log`
  then bound the physical result recorded above: four clean frames, zero fault
  status, and a false policy result solely from the disabled shadow mirror at
  `active=00000000`. Containment reached `FAILED_QUIESCENT`; optical state was
  unobserved;
- patch 40 removes only that inactive-shadow composite and equality, while
  retaining its component diagnostics and the remaining 41 policy predicates.
  Its exact five-line deletion, 7/40/10 checker, 963-case model, strict
  style/object checks, independent audit, M7 ABI-v2 regression, and M9
  full/no-flash and artifact gates pass with the exact hashes above. It changes
  no write, teardown, reveal, or ABI-v2 behavior. Patch 40 remains unflashed;
  first boot, `disconnect`,
  `preflight`, `stress`, `touch`, `soak`, and user-observed optical acceptance
  remain required;
- the following compatibility-profile bullets are retained as historical
  evidence only; they do not validate the native cold-init candidate;
- the compatibility profile keeps the triple-buffer ABI v2, IRQ-rearm engine,
  RGB565 format, 80 MHz DPI clock, and 1500 Mbps two-lane D-PHY unchanged. It
  changes the link policy as one coherent set to the stock IDF/YamUI behavior:
  automatic clock lane, LP blanking enabled, and per-frame ACK enabled;
- the earlier compatibility path commanded PWM zero and a BL_ENABLE-low
  control value while the old splash was still scanning, then stopped the old
  source. Those successful writes were never external-output readback; physical
  darkness remained a device observation;
- the ordered 41-patch series passes with 24 peripheral patches and manifest
  SHA-256
  `39d9a882ea310d5d903d8bfea568ba353156e709bc1186ba80a6f064d111b701`;
- the host display model passes all 108 cases, including bindings that prevent
  the signal-restoration and soak gates from silently reverting to the rejected
  forced-HS/LP-disabled/frame-ACK-off policy. The full M9 build passes with
  source contract
  `e766ef8fa84fa9abb76c2a70e67556eae38d0e2b6028caa33dd60a571e4909a5`;
- the Linux Image is 6,098,928 bytes with SHA-256
  `a03acb25e8542f1146646b0e174c1bc74284c6ffef028bf93fa8025574b31e50`;
  and
- the matching loader is 281,936 bytes with SHA-256
  `b734330d57a9f8ca9b40d3667be357fb20d5849f0ed6e5f5a238f1869c9f36e9`;
  and
- the user authorized this compatibility candidate and the complete flash plus
  readback passed. Loader, Image, DTB, and metadata read back with exact host
  hashes `b734330d57a9f8ca9b40d3667be357fb20d5849f0ed6e5f5a238f1869c9f36e9`,
  `a03acb25e8542f1146646b0e174c1bc74284c6ffef028bf93fa8025574b31e50`,
  `862798942dc80f67ed5ef0b76dc8d91b6f93805711ac271955db42e1f059fd66`,
  and `27347ceb61b085d55f2d2fb197fc32eb74598ee8e458bcc45e44a0fa86dce3be`.
  Evidence is retained at
  `build/m9-readback/20260812T013828770Z-8f128181`.
- physical and COM14 testing rejected that exact runtime artifact before the
  backlight was revealed. The loader completed, Linux claimed ABI v2, and the
  IRQ-rearm engine started, but the boot-ready source guard returned
  `-EHOSTDOWN` (`-112`). `/init` then exited on `display-reveal`, the kernel
  panicked because PID 1 died, and `panic=1` restarted the board. Termite only
  exposed the pre-existing loop; it did not cause the reset;
- the rejected binary reported only an aggregate reveal failure, so the exact
  false predicate cannot be recovered from that transcript. Its initial
  stopped-to-framebuffer path used one four-frame qualification window, while
  its already-hardened VPG restore used a first grace window plus a second
  strict window. Patch 25 removes that asymmetry by reusing the two-window
  framebuffer qualification before the first reveal and emits every reveal
  predicate if validation still blocks;
- the rootfs no longer exits PID 1 when display reveal fails. It records
  `reason=display-reveal mode=headless`, keeps the backlight dark, and starts
  the USB recovery shell. Hardware acceptance still fails, but a display fault
  can no longer turn into a blind reboot loop;
- the corrected ordered 42-patch series passes with 25 peripheral patches and
  manifest SHA-256
  `1a79d7666434645b302428fcd03b2f336e3890e73e339ddd57f1292fcfb2c2e0`.
  Patch 25 applies to the exact patch-24 postimage and passes strict checkpatch
  with zero errors, warnings, or checks. The RISC-V driver object builds with
  `W=1`, and the host display model passes 112 cases;
- the corrected full M9 build passes with source contract
  `34e0af058e83c2662c6ce6d123a90cf0faccac1f034941978b590a554d2cf63e`.
  Its Image is 6,098,928 bytes with SHA-256
  `0bde7b5144ffe7f175027c963a644ce23ffa766d1fe2b872b86cdc114913343f`;
  DTB SHA-256 is
  `862798942dc80f67ed5ef0b76dc8d91b6f93805711ac271955db42e1f059fd66`;
  metadata SHA-256 is
  `40751382bac9416627e700ef5b016b16adc0a055999372b2421a65ad3aa4d7d4`;
  rootfs SHA-256 is
  `24d5a8c8c9bb841c5d885b2c55c7d0af60e58e429d5019b7ca2a7e48c38bbda3`;
  and vmlinux SHA-256 is
  `2910631eecd3801a7cc95364c900db8c60b15b65fb194f2a55bbe1f9b095d5b5`.
  All seven entries in `out/m9/SHA256SUMS` rehash exactly. The matching loader
  remains the already-flashed `b734330d...` compatibility loader.

The first compatibility artifact is statically validated, flashed,
readback-verified, and physically rejected at the boot reveal. The first
headless-safe correction was subsequently flashed and read back. It stopped
the reboot loop and reached a responsive `#` shell with the backlight kept
off. Its no-reset snapshot recorded advancing GDMA, zero bridge underruns, and
valid source policy, but DSI host ST1 bit 7 (`DPI_PLD_WR_ERR`) appeared at the
end of the first dark four-frame refill window. That status was latched as
fault bit 3, so the second strict window was never reached. The evidence log
is `out/m9/hardware-runs/20260812T021033Z-snapshot-b5890811aa40-fed38406.log`.

Patch 26 narrows the startup grace to the already-defined DPI-path pair,
bits 7 and 19, at only the first dark refill boundary. The following fresh
four-frame window still samples with mask zero, and any repeated status blocks
reveal and remains fail-dark. The resulting ordered 43-patch series passes
with 26 peripheral patches and manifest SHA-256
`4f0033892bcb1790f4f7675d8152710078550c9d38d827de64cd4c38b55acbcb`.
Patch 26 dry-applies to the exact patch-25 source and strict checkpatch reports
zero errors, warnings, or checks. The host display model passes 114 cases.

The full corrected build passes with source contract
`783da698cb15e2ed1d09f0d426734f770b9376afc52dd9842f487e1414089b99`.
Its Image is 6,098,928 bytes with SHA-256
`01fcfa56c9af1ba3d2574b46f13e999188864a49ea4e9bbd9303388a7ab65485`;
DTB SHA-256 is
`862798942dc80f67ed5ef0b76dc8d91b6f93805711ac271955db42e1f059fd66`;
metadata SHA-256 is
`930081b484c5b894766ae1d280e8101297d3eb87eddfa2682f2400674ed3e433`;
rootfs SHA-256 remains
`24d5a8c8c9bb841c5d885b2c55c7d0af60e58e429d5019b7ca2a7e48c38bbda3`;
and vmlinux SHA-256 is
`61c5d7f75ff12f4400473b37c182c6218528828f9853be4b11240f5c3507ea81`.
All seven artifact hashes pass. The complete flash plus readback also passes
with unchanged loader SHA-256
`b734330d57a9f8ca9b40d3667be357fb20d5849f0ed6e5f5a238f1869c9f36e9`.
Readback evidence is retained at
`build/m9-readback/20260812T021935364Z-b3be44ad`.

The exact no-reset capture rejected this candidate. The first grace and second
strict windows completed eight frame transfers with moving GDMA SAR and zero
bridge underrun, but ST1 bit 7 (`DPI_PLD_WR_ERR`) was present again at the
strict boundary. Linux latched display fault bit 3, stopped the source, kept
the backlight off, and continued to a responsive recovery shell without a
panic or reboot. This proves the status was not confined to the first refill
window; widening the mask again would weaken the runtime fault contract and is
not accepted. The sealed log is
`out/m9/hardware-runs/20260812T022354Z-snapshot-b5890811aa40-510849a2.log`
with SHA-256
`2b76f745fd618373312ee7e3b9e551473092ea0f5a7be41092b4196de5306092`.

The patch-26 evidence exposed a longer ownership seam rather than another
startup-grace problem. The loader turned the physical backlight off and then
stopped its framebuffer GDMA source while bridge DPI remained selected. The
DSI host therefore had no video source for the remainder of Linux boot. Pinned
ESP-IDF v6.0.1 implements its supported framebuffer/VPG source switch in this
order: disable bridge DPI and apply its update before enabling host VPG; on
restore, disable host VPG before enabling bridge DPI and applying its update.
MicroNUX now uses that exact v6.0.1 ordering while the panel is physically
dark. The loader selects vertical VPG before stopping framebuffer GDMA and
publishes the new ABI-v2 `HOST_VPG_ACTIVE` flag. Linux accepts the handoff only
when VPG is selected, bridge DPI is disabled, and the inherited GDMA channel is
stopped. It then arms its own framebuffer GDMA before switching VPG to bridge
DPI through the existing dark two-window qualification and reveal gate.

Patch 27 implements this versioned dark-VPG handoff. It applies to the exact
patch-26 postimage and strict Linux v6.12 `checkpatch.pl` reports zero errors,
warnings, or checks. The ordered series now passes with 7 platform, 27
peripheral, and 10 isolation patches, total 44, with manifest SHA-256
`4376b2ee3bd77c2dadf197dd3bc40d2329e8200b884b51f28149050c96010c56`.
The host ownership model passes 124 cases, including explicit rejection when
the loader omits the required `HOST_VPG_ACTIVE` flag and a source binding that
requires Linux to clear the complete VPG mode/orientation/enable field before
selecting framebuffer DPI. The exact patched Linux display source and object
have SHA-256 values
`830896d5488df2ad57e2f6cfcceee855462ed8fd148df30c5622712839e06e88`
and
`232581ea692f0fb01f231b361271cabbf4aca6e3e1bb28a9ceabaaf74ec028e4`.

The fresh full build passes with source contract
`7a5c24c2213a482330d70d902822416568bb87349a7a1445c334c168c81d846f`.
Its 6,098,928-byte Image has SHA-256
`bc182202fe30027dbf09cb2cbb89dfeef85cbb2d5c69b24036b400a4ea0a188b`;
DTB SHA-256 is
`862798942dc80f67ed5ef0b76dc8d91b6f93805711ac271955db42e1f059fd66`;
metadata SHA-256 is
`ba79fce9510dbfa9c7471c2a2c9a01da065893284d738e65f6c5488598a0c082`;
rootfs SHA-256 is
`24d5a8c8c9bb841c5d885b2c55c7d0af60e58e429d5019b7ca2a7e48c38bbda3`;
and vmlinux SHA-256 is
`a7922ed88724c0b2feaed5ff4938711fbc250c40c2851ad50f6532eac3f8e5ba`.
The loader was rebuilt only against pinned ESP-IDF v6.0.1 commit
`8c19b156084a0753687347cca1f5355782893533`. Its 282,784-byte binary has
SHA-256
`fde31247285b7fe2ade901300d106c1b6b9c2939035f2ba93fec3ac1fd99af3e`,
and the ELF contains both the dark VPG source-ready marker and the
`pattern=vertical-bars` handoff marker.

The user authorized this exact candidate and the complete flash plus readback
passed on 2026-08-11/12. Loader, Image, DTB, and metadata read back with the
same four host hashes above. Evidence is retained under
`build/m9-readback/20260812T035234069Z-bce60383`.

The first runtime gate rejected the candidate safely before the 600-second
disconnect interval. The loader selected host VPG, disabled bridge DPI, stopped
its framebuffer GDMA, and Linux accepted that exact dark source contract. Linux
then selected framebuffer DPI and completed both four-frame qualification
windows: the frame counter advanced to eight, GDMA SAR moved, the channel
remained enabled, and GDMA error plus bridge-underrun status remained zero.
The strict host sample nevertheless recorded DSI host INT_ST1 bit 7
(`DPI_PLD_WR_ERR`). The separately sampled command-packet status was
`0x00020009`; it describes the generic command/payload-write path and is not a
pair of video-pixel FIFO-full bits. The D-PHY data lanes were parked even though
the upstream producer was still healthy. Linux therefore stopped the source,
kept the backlight dark, and continued to a responsive shell without panic or
reboot. The sealed transcript is
`out/m9/hardware-runs/20260812T035527Z-disconnect-vpg-b5890811aa40-6738ee87.log`
with SHA-256
`4a08d80fe4a4b26a37b909ad763397997bea30469cc72d306bc32bca8e3e30c0`.

This result proves that the former multi-second no-source handoff gap is gone,
but it does not accept the compatibility link policy. The DesignWare status
definition says bit 7 means the DPI pixel payload FIFO became full and stored
data was corrupted; it must not be masked as harmless startup residue.
Patch 28 implements that single-variable test. It retains ABI v2, triple
buffering, per-frame IRQ rearm, 80 MHz/1500 Mbps timing, automatic clock-lane
operation, LP blanking, the dark VPG handoff, and every fail-dark gate, while
disabling only per-frame BTA acknowledgment. The loader applies and verifies
the same policy through the pinned ESP-IDF v6.0.1 host LL before panel init;
Linux clears the frame-ACK bit whenever framebuffer DPI is selected and treats
an enabled bit as an invalid source policy. No host-error mask, clock, timing,
lane, buffer, or qualification-delay change is combined with this test.

The ordered patch series now passes with 7 platform, 28 peripheral, and 10
isolation patches, total 45, with manifest SHA-256
`ef507eaed978c2be276a68a72142d7d6dbabeada445c06cc8ce4036727144606`.
Patch 28 applies to the exact patch-27 postimage with GNU `patch --fuzz=0`, and
Linux v6.12 strict `checkpatch.pl` reports zero errors, warnings, or checks.
The host display model passes 134 cases, including exact-positive automatic
clock-lane and complete LP-blanking checks plus the frame-ACK-disabled VPG raw
policy `host=0001bf02`. The build wrapper also now discards an
incomplete Linux patch tree after a failed build instead of reapplying the
series to partial state.

Before flash, the exact build passed with source contract
`07a24acfcf1303639d6297d78410dfe5fcbbae09d9c5cd25f55e63400741f9f5`.
Its 6,098,928-byte Image has SHA-256
`9bc75b6054bf8d943cf3d8364555c32bd4a26fba1b0028c78c520b0f12fb7a41`;
DTB SHA-256 is
`862798942dc80f67ed5ef0b76dc8d91b6f93805711ac271955db42e1f059fd66`;
metadata SHA-256 is
`657efd9f2bef79daf1a1de369d319628ffed1f2b1cc81e2adb91575efea96bd2`;
rootfs SHA-256 is
`24d5a8c8c9bb841c5d885b2c55c7d0af60e58e429d5019b7ca2a7e48c38bbda3`;
and vmlinux SHA-256 is
`7e0a7c3198c62e887f7ac53f7a7af9807db053fec14af8620b38e99b47856d39`.
All seven artifact hashes revalidate. The matching ESP-IDF v6.0.1 loader is
283,104 bytes with SHA-256
`95bb29ac3e601eb2d0cb7c5c105893eb939460e5dd489a2e7fb868208bb8c7e8`.
Both binaries contain their required `frame-ack=disabled` policy markers. The
user authorized this exact candidate and the full flash plus readback completed
on 2026-08-12. All four extracted spans match their host artifacts byte for
byte. Evidence is preserved under
`build/m9-readback/20260812T044649071Z-48ffa044`; its `readback.json` has
SHA-256 `d9713ed19226a7e95f4900d0b34531ced90bc401ff059a559766b56b68f60d2d`.
The patch-28 boot gate failed; its later disconnected/VPG, transition, stress,
touch, and cold/warm tests were therefore not run. Every cold-init acceptance
gate remains open. The untouched patch-28 first boot was physically black with the backlight
apparently off. A passive-first, no-reset COM14 capture then proved the intended
fail-dark path: Linux completed only one of four required startup frames,
reported an unchanged GDMA SAR, and latched DSI host INT_ST1 `0x00000080`
(`DPI_PLD_WR_ERR`) with `VID_PKT_STATUS=0x00020009` and PHY status
`0x000015bd`. The driver stopped the source, kept the backlight off, and retained
a responsive shell. The sealed transcript is
`out/m9/hardware-runs/20260812T045218Z-snapshot-b5890811aa40-a430255c.log`
with SHA-256
`f616c296d6e4fab488642506aff5459932a3034393a84148c606c02401ec0c7b`.
This rejects the frame-ACK-off adopt-live candidate and proves per-frame BTA was
not the root cause; no physical acceptance gate passed.

The post-failure source audit found that patches 24-28 did not actually test
the ESP-IDF/YamUI automatic clock-lane state. They cleared
`PHY_TXREQUESTCLKHS` and set only `AUTO_CLKLANE_CTRL`, producing
`LPCLK_CTRL=0x2`. Pinned ESP-IDF v6.0.1 sets both bits for AUTO, and upstream
Linux v6.12 independently always asserts the HS request while adding automatic
control for non-continuous clocking; both produce `0x3`. The two rejected
compatibility candidates both ended at `LPCLK_CTRL=0x2`, PHY `0x000015bd`
(clock lane plus both configured data lanes in STOPSTATE), and host ST1 bit 7.

Patch 29 is therefore the final bounded adopt-live correction before cold-init.
It changes only Linux source selection and exact-positive policy validation to
require `LPCLK_CTRL=0x3`; frame ACK remains disabled, LP blanking remains
enabled, and ABI v2, VPG handoff, triple buffers, IRQ rearm, 80 MHz/1500 Mbps,
qualification windows, and fail-dark containment remain unchanged. The
expected raw policies are framebuffer `host=0000bf02 active=00000000
lpclk=00000003` and VPG `host=0001bf02 lpclk=00000003`.

The exact static candidate is frozen. The series passes with 7 platform, 29
peripheral, and 10 isolation patches (46 total), manifest SHA-256
`aee0341ba984f3e6b864b134f6ff162bc1db51488e20cddb383942a1d5ef50ae`.
Patch 29 applies with zero fuzz to the exact patch-28 postimage, strict Linux
v6.12 checkpatch reports zero errors, warnings, or checks, and the host model
passes 135 cases. The fresh full build passes with source contract
`695074bcc780b014d7c673c69f83d12e789acac8b09a58ce684954e2716faaab`.
Its 6,098,928-byte Image has SHA-256
`908efab893ee3e03a4dd7dd6fe6f2b476041dacd4d12ea599ee4d6c50fa4d601`;
DTB SHA-256 is
`862798942dc80f67ed5ef0b76dc8d91b6f93805711ac271955db42e1f059fd66`;
metadata SHA-256 is
`6b56bbd5197dccf881f0425b9f7e46dad740c08382064ad6b9f21026415e9fca`;
rootfs SHA-256 is
`24d5a8c8c9bb841c5d885b2c55c7d0af60e58e429d5019b7ca2a7e48c38bbda3`;
vmlinux SHA-256 is
`ebb2a6f3a08f3c498d8b436ab7a553d8b2a5e8f883133505ff7f27085580be83`;
and the unchanged pinned-IDF loader remains 283,104 bytes with SHA-256
`95bb29ac3e601eb2d0cb7c5c105893eb939460e5dd489a2e7fb868208bb8c7e8`.
All seven artifact-manifest entries rehash exactly. The built driver source is
the exact expected patch-29 Git blob `8154876a2ad483f0e0c8eb9392c592796c74ba64`.
Independent artifact audit found no stale or mixed input: the source contract,
seven hashes, exact driver postimage/object, vmlinux markers, loader binary,
and pinned ESP-IDF v6.0.1 provenance agree. The user authorized this exact
candidate and the full flash plus readback passed. Loader, Image, DTB, and
metadata read back with the four expected hashes above. Evidence is preserved
under `build/m9-readback/20260812T052700888Z-9c29b1fd`.

The untouched Patch-29 boot was physically black with the backlight kept off.
A passive-first, no-reset capture proved that Linux programmed the corrected
framebuffer policy `host=0000bf02 active=00000000 lpclk=00000003`, but completed
only one transfer before the required four-frame gate. GDMA channel 0 was
enabled, its source address remained fixed at `0x49300200` after only 512 bytes
of progress, GDMA error and bridge-underrun status remained zero, and DSI host
INT_ST1 latched `0x00080000` (`DPI_BUFF_PLD_UNDER`). The driver contained the
fault, stopped the source, kept the backlight dark, and retained a responsive
shell. The sealed transcript is
`out/m9/hardware-runs/20260812T053031Z-snapshot-b5890811aa40-2393564f.log`
with SHA-256
`85c1a31e45183c51295b27405afaf46faea1a9dd987478b45bac90bb08f57864`.

This rejects the last bounded adopt-live correction. The failure is at the
loader-stopped/Linux-restarted display ownership seam, not an untested
automatic-clock encoding. No Patch-29 physical acceptance gate passed. M9.2.5
therefore proceeds only with a fail-closed ABI-v3 cold contract in which the
loader leaves the display dark and quiescent and Linux initializes the complete
stack from reset.

Ownership decision:

- **adopt-live - REJECTED:** loader-initialized ABI-v2 candidates through patch
  29 failed their physical boot gates; their evidence is historical only.
- **cold-init - SELECTED, NOT YET ACCEPTED:** the loader relinquishes the display
  dark and Linux initializes the complete display stack from a reset state.

Exit criterion: the exact ABI-v3 cold-init artifact passes every machine and
physical gate below.

### M9.2.6 - Final integration and acceptance

Section status: **IMPLEMENTING / TESTING**

Deliverables:

- [x] **COMPLETED** - update the roadmap and renumbered LVGL plan so optional GUI
  work depends on completed M9.2;
- [x] **COMPLETED** - document the selected Linux cold-init owner, memory
  map, buffers, descriptor lifecycle, interrupts, backlight ordering, and fault
  behavior;
- [x] **VALIDATED** - retain source-contract, patch-manifest, artifact hash,
  partition-size, no-LVGL, NOMMU isolation, and bFLT W^X build gates;
- [x] **COMPLETED** - document licenses and provenance for YamUI observations,
  ESP-IDF/Waveshare references, and the GPL-2.0-only Linux implementation;
- [ ] **TESTING** - preserve exact hardware logs outside committed secrets and
  summarize their hashes and pass/fail evidence in this ledger; and
- [ ] **PLANNED** - commit and push only the intended M9.2 files, excluding
  attachments, build products, temporary postimages, and unrelated dirty work.

Exit criterion: all required M9.2 work packages are completed, the exact
physical candidate passes, and the optional LVGL milestone can consume the
Linux display API without owning display hardware.

## Required stable markers

Names may be refined before implementation, but parsers must bind to a version
and fail closed. The intended evidence classes are:

The ABI-v2 `DSI-HANDOFF` and `CLAIM` examples below remain only for legacy M7
and rejected-candidate parsing. Current M9 acceptance requires the ABI-v3
`COLD-*` and `REVEAL` sequence and must reject ABI v2.

```text
MICRONUX:M9.2:DSI-HANDOFF state=ready abi=2 owner=linux-pending buffers=3 dma=irq-rearm timing=20/20/40:4/10/30 crc32=<hex>
MICRONUX:M9.2:COLD-EXTERNAL state=ready pwm-zero-write=acked reset-prepare-write=acked pwm-zero-settle=elapsed reset-assert-write=acked reset-hold=elapsed i2c=released
MICRONUX:M9.2:COLD-STAGE state=ready abi=3 flags=00003fff route=ready dma-pms=ready panel-payload-crc=cea07f9b contract=invalid crc32=<hex>
MICRONUX:M9.2:COLD-PROBE state=PROBED_QUIESCENT abi=3 size=0x00c0 flags=0x00003fff payload-crc=cea07f9b dsi-module-reset=asserted dphy-clocks=off ldo-control=off gdma-reset=asserted clocks=off commands=acked display-writes=0
MICRONUX:M9.2:COLD-PROBE state=REJECTED_NO_WRITES reason=<reason> display-writes=0
MICRONUX:M9.2:COLD-INIT stage=<ldo|panel-reset|dphy|host|panel-commands|bridge|gdma|qualified|reveal> state=<ready|fail>
MICRONUX:M9.2:REVEAL state=QUALIFIED source=userspace-status commit=<n> front=<n> frames=4 same-front=yes host-errors=0 gdma-errors=0 ecc-errors=0 common-errors=0 bridge-underruns=0 guards=verified external-command-history=pwm0-control13-acked physical-panel-state=unobserved
MICRONUX:M9.2:REVEAL state=RUNTIME_REVEALED boot-ready=1 source=userspace-status commit=<n> frames=4 same-front=yes control-command=0x17-acked pwm-command=63-acked brightness=63 backlight=registered i2c=active-serialized touch=registering health=monitored physical-panel-state=unobserved
MICRONUX:M9.2:CLAIM state=ready abi=2 owner=linux fb=fb0 size=2048000 buffers=3 dma=ch0:irq-rearm config=0000000f:0a020001 guards=pool-monitored
MICRONUX:M9.2:REARM state=ready generation=<n> frames=<n> failures=0
MICRONUX:M9.2:SCANOUT state=running source=framebuffer buffers=3 guards=ok guard-errors=0
MICRONUX:M9.2:FLIP state=complete front=<n> back=<n> spare=<n>
MICRONUX:M9.2:FAULT state=contained source=preserved backlight=off
MICRONUX:M9.2:PASS machine=pass visual=pass
```

Do not emit a high-rate line for every frame. Counters belong in diagnostics;
logs should record state transitions, faults, and bounded test summaries.

## Build and validation ladder

### Static and host validation

- preserve unrelated modified and untracked files;
- verify the ESP-IDF v6.0.1 commit before building the loader;
- apply the ordered Linux patches to the exact Linux 6.12.27 tree;
- run `git diff --check`, strict checkpatch on each new kernel patch, and the
  existing patch-series manifest checker;
- compile the display driver with warnings treated as errors;
- run handoff, descriptor, state-machine, parser, hash, and negative tests;
- verify the target rootfs contains the expected diagnostic and no LVGL;
- verify partition sizes and the exact source/artifact contract; and
- record SHA-256 for every flashable and test artifact.

### Emulator/model validation

Host models may test state transitions, bounds, generation counters, flip
ordering, timeout behavior, and fault injection. QEMU cannot validate the
ESP32-P4 GDMA handshake, DSI bridge, D-PHY, PSRAM arbitration, JD9365, or
physical backlight. Model passes therefore cannot replace Kit C testing.

### Physical Kit C validation

Run the exact `disconnect` -> `preflight` -> `stress` -> `touch` -> `soak`
sequence defined by M9.2.4 for the triple-buffer production candidate. Do not
invoke VPG: ABI v3 has no native VPG interface. Preserve a known-good rollback
and never combine an acceptance flash with unrelated loader, network, storage,
or GUI experiments.

## Completion criteria

M9.2 is complete only when:

- Linux is the sole post-handoff owner of the complete display path;
- the selected ABI-v3 cold-init path is versioned and reproducible;
- the custom circular/auto-reload experiment is no longer the accepted
  scanout mechanism;
- explicit frame completion and rearm remain healthy at idle and under load;
- front/back/spare ownership and page flips pass as part of the final accepted
  path;
- framebuffer, descriptors, DMA, bridge, host, PHY, memory, and backlight
  invariants remain valid;
- no visual cyan, flicker, unexplained black screen, partial frame, or failed
  console restoration occurs in the required physical tests;
- the 600-second disconnected visual and machine soak passes on the same boot;
- SD, network, touch, USB serial, fbcon, isolation, and user execution
  regressions pass;
- every exact artifact and test log is attributable by hash; and
- the documentation states what is proven, what remains unobservable in
  software, and which optional GUI milestone starts next.

## Explicit non-goals

- Adding LVGL, `micronux-guid`, a window manager, widgets, themes, or fonts.
- Refactoring IgniteVM or exposing the future UI-v1 protocol.
- Running ESP-IDF/FreeRTOS as a coprocessor runtime on the P4 beside Linux.
- Copying YamUI's unused RGB888 fallback path.
- Changing DPI clock, lane rate, pixel format, panel timings, or DSI link policy
  alongside the first frame-engine candidate.
- Calling a machine-only telemetry pass proof that the panel looked correct.
- Adding automatic panel recovery before a reset/reinitialization sequence is
  physically proven safe.
- Converting the complete display stack to DRM/KMS before the fbdev-based
  ownership and scanout contract is stable.
- Broad performance optimization before correctness and physical stability.

## Expected repository changes during implementation

Exact filenames and patch numbers must be chosen after inspecting the live
dirty worktree. Expected areas are:

- `loader/main/` - versioned display handoff and dark relinquish behavior;
- `loader/patches/esp-idf-v6.0.1/` - only if a pinned IDF handoff hook remains
  necessary;
- `buildroot-external/board/micronux/patches-peripherals/linux/` - small,
  ordered native Linux display patches;
- `buildroot-external/board/micronux/dts-m9/` - display pool/memory contract if
  triple buffering changes the reserved map;
- `buildroot-external/board/micronux/rootfs-m6-combined/init` - status/render
  gate only if its contract changes;
- `buildroot-external/package/micronux-display-test/` - no-LVGL display and
  touch diagnostic;
- `scripts/m9-build.sh`, `scripts/m9.ps1`, and
  `scripts/m9-hardware-test.py` plus `scripts/m9-run-logged.py` - source
  contracts, build, flash/readback, sealed logging, and hardware gates;
- `docs/m7-linux-display.md` - final low-level display ownership result;
- `docs/m9-lvgl-window-manager-plan.md` - milestone renumbering and dependency;
- `docs/roadmap.md` - M9.2 milestone insertion; and
- this document - live status and acceptance evidence.

Keep implementation patches small and grouped by contract, lifecycle,
buffering, diagnostics, and tests. Do not fold unrelated storage, networking,
provisioning, LVGL, or Ignite changes into M9.2.

## Primary references

- [ESP-IDF v6.0 ESP32-P4 MIPI DSI documentation](https://docs.espressif.com/projects/esp-idf/en/v6.0/esp32p4/api-reference/peripherals/lcd/dsi_lcd.html)
- [ESP-IDF v5.5.2 DPI panel implementation used by the YamUI baseline](https://github.com/espressif/esp-idf/blob/v5.5.2/components/esp_lcd/dsi/esp_lcd_panel_dpi.c)
- [YamUI active Waveshare display wrapper at the frozen commit](https://github.com/d0773d/yamui-device/blob/de1b723b9fc237e63e96fc82ce2975aa8b1a2ad4/components/kc_touch_display/src/kc_touch_display_waveshare_p4.c)
- [Official Waveshare ESP32-P4 components](https://github.com/waveshareteam/Waveshare-ESP32-components)
- [JD9365DA-H3 controller datasheet](https://dl.espressif.com/AE/esp-iot-solution/JD9365DA-H3_DS_V0.01_20200819.pdf)
- [Existing MicroNUX Linux display documentation](m7-linux-display.md)
- [Existing optional LVGL/window-manager plan](m9-lvgl-window-manager-plan.md)
- [MicroNUX device ownership and applications](device-ownership-and-applications.md)
- [MicroNUX roadmap](roadmap.md)

Local source anchors for implementation research:

- `C:\esp\v6.0.1\esp-idf` at the pinned ESP-IDF v6.0.1 source;
- `C:\Code\yamui-device` at the frozen YamUI source reference; and
- the generated Linux 6.12.27 source used by the M9 Buildroot target.
