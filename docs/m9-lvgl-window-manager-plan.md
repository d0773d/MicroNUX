# M9 Optional LVGL Window and Session Manager Plan

Status: **TESTING**

Current section: **M9.1 - Linux input and presentation foundation (TESTING)**

Last status update: **2026-08-10**

## Live implementation status

This document is the authoritative M9 implementation ledger. A feature moves
through `PLANNED`, `IMPLEMENTING`, `VALIDATING`, `TESTING`, and `COMPLETED`.
`COMPLETED` means the implementation exists, its required validation and tests
passed, and the evidence is recorded here. A work-package section is completed
only after every required feature and its exit criterion are completed.

| Work package | Section status | Current evidence |
| --- | --- | --- |
| M9.0 Contract freeze | **COMPLETED** | Plan approved for implementation on 2026-08-10; architecture commit `b3d011f` |
| M9.1 Linux input and presentation | **TESTING** | Touch and transition gates pass. A 240-second soak proved stable DSI/DMA, touch, and shell operation. Linux now protects against USB host-close resets and the 15-second disconnect/reconnect hardware gate passes; final human visual confirmation is pending |
| M9.2 Optional LVGL service | **PLANNED** | Starts only after the M9.1 Linux gate passes |
| M9.3 UI-v1 and native C SDK | **PLANNED** | Starts only after the M9.2 service gate passes |
| M9.4 Window and session manager | **PLANNED** | Starts only after the M9.3 ABI gate passes |
| M9.5 Ignite target separation | **PLANNED** | Starts only after the M9.4 lifecycle gate passes |
| M9.6 Language and shell surfaces | **PLANNED** | Starts only after the M9.5 dependency gate passes |
| M9.7 Packaging and physical acceptance | **PLANNED** | Final integration and physical gate |

This document is the implementation contract for the optional MicroNUX GUI.
It separates LVGL from applications, keeps Linux as the sole device owner,
and preserves a complete terminal-only MicroNUX build.

## Purpose

M9 adds an optional embedded GUI without turning MicroNUX into a desktop Linux
distribution. The GUI is a trusted Linux userspace service built around LVGL.
It presents one foreground, full-screen application at a time and provides
system overlays, input routing, lifecycle management, resource limits, and
fault recovery.

Applications do not own LVGL, the framebuffer, the touchscreen, DMA, or MIPI
DSI. Native C, Ignite for MicroNUX, shell tools, and future language runtimes
all use the same versioned local UI service.

## Locked architecture decisions

1. Linux remains the sole post-handoff owner of the display, touchscreen,
   backlight, input routing, and all other persistent peripherals.
2. LVGL is optional. A terminal-only MicroNUX image contains no LVGL library,
   daemon, fonts, themes, or GUI draw buffers.
3. A GUI-enabled MicroNUX image contains LVGL only inside the trusted
   `micronux-guid` process.
4. Ordinary applications never receive `lv_obj_t *`, `lv_display_t *`, LVGL
   callbacks, framebuffer mappings, input device descriptors, MMIO, or DMA
   objects.
5. Applications use a versioned, language-neutral local protocol at
   `/run/micronux/ui-v1.sock`.
6. UI-v1 is versioned independently from LVGL. An internal LVGL upgrade must
   not require application recompilation when UI-v1 remains compatible.
7. Ignite has two deployment flavors that share one language, compiler,
   bytecode, VM core, garbage collector, and package format:

   - standalone Ignite firmware embeds its own LVGL backend; and
   - Ignite for MicroNUX has no LVGL, ESP-LVGL, or ESP-IDF dependency and uses
     UI-v1 when the optional GUI service is available.

8. The first manager supports one foreground, full-screen application plus
   trusted system overlays. Overlapping, draggable desktop windows are not an
   M9 requirement.
9. The first implementation uses LVGL 9.5.0. The MicroNUX UI-v1 boundary must
   still permit a future internal LVGL upgrade.
10. GUI failure must fail back to a working USB serial terminal and, where the
    kernel console interface permits it, the framebuffer console.

## Supported operating modes

| Mode | LVGL location | Display behavior |
| --- | --- | --- |
| Standalone Ignite firmware | Embedded in the ESP-IDF firmware | Firmware owns display and touch; Linux is absent |
| Terminal-only MicroNUX | Not installed | Linux framebuffer console and USB shell |
| GUI-enabled MicroNUX | Only inside `micronux-guid` | `micronux-guid` owns the GUI session through Linux drivers |
| Ignite for MicroNUX | Not linked | Uses UI-v1 if `micronux-guid` is installed and enabled |
| Native MicroNUX C application | Not linked | Uses `libmicronux-ui` and UI-v1 |
| Other MicroNUX language | Not linked | Uses a C binding or UI-v1 directly |

The two LVGL deployments are independent. Standalone Ignite firmware and
`micronux-guid` may begin on the same LVGL version, but neither links to nor
loads code from the other.

## Accepted baseline

M9 builds on the accepted M7 and M8 contracts:

- the loader initializes the 10.1-inch JD9365 panel as 800x1280 RGB565 over
  two MIPI DSI lanes;
- Linux validates the loader handoff, owns `/dev/fb0`, runs circular DW-GDMA
  scanout, monitors underruns, and owns the backlight;
- the framebuffer is exactly 2,048,000 bytes with a 1,600-byte stride;
- application framebuffer `mmap()` is denied;
- Linux paces ordinary framebuffer writes to protect PSRAM scanout bandwidth;
- the framebuffer console is 100x80 characters and USB `ttyGS0` remains the
  recovery shell; and
- M8 establishes the local `SOCK_SEQPACKET`, `SO_PEERCRED`, versioning,
  nonblocking wait, bounded-client, and supervised-service patterns that M9
  must repeat.

The Kit C display uses a GT9271 touch controller according to the Waveshare
hardware documentation. Linux touch ownership and the exact board interrupt,
reset, and coordinate-transform contract are not yet implemented and belong
to M9.1.

## System architecture

```text
10.1-inch display, GT9271 touch, backlight
                    |
             Linux kernel drivers
       fbdev / input / I2C / backlight / DSI
                    |
             micronux-guid
       +-----------------------------+
       | LVGL 9.5 renderer           |
       | application session manager |
       | input and focus router      |
       | system overlay              |
       | policy and resource limits  |
       +-----------------------------+
                    |
        /run/micronux/ui-v1.sock
           |          |          |
    libmicronux-ui  Ignite    future bindings
           |       for Linux         |
       native C                 other languages
```

LVGL supplies widgets, styles, layouts, rendering, animations, input event
processing, and invalidated-area tracking. `micronux-guid` supplies the
operating-system policy that LVGL does not: sessions, foreground ownership,
focus, application lifecycle, authorization, quotas, cleanup, and recovery.

## Process and ownership boundaries

### Kernel

The kernel owns all physical interfaces. M9 may add or configure Linux drivers
for the GT9271, board I2C wiring, input events, and safe display presentation.
It must not add a general-purpose userspace MMIO, DMA, or framebuffer-mapping
escape hatch.

### `micronux-guid`

`micronux-guid` is the only process that:

- initializes the MicroNUX LVGL instance;
- opens the GUI display and touch interfaces;
- creates LVGL objects;
- runs LVGL timers and rendering;
- receives raw Linux input events;
- assigns foreground focus; and
- renders trusted system overlays.

The daemon uses one serialized event loop for client sockets, input readiness,
LVGL timers, and display completion. No client callback is executed inside the
daemon. Untrusted code cannot be dynamically loaded into it.

### Ordinary applications

Each application is a separate Linux userspace process with its existing
MicroNUX NOMMU arena and execution policy. It owns only a UI-v1 connection and
opaque handles scoped to that connection. Disconnecting destroys the session
and every object and resource associated with it.

Applications cannot:

- open the GUI-owned framebuffer or raw input device;
- address another application's objects;
- render above the trusted system overlay;
- claim focus or system capabilities in a request;
- install an LVGL event callback; or
- retain UI ownership after exit.

### Trusted GUI extensions

A specialized widget that cannot be represented by UI-v1 may be implemented
as a reviewed, build-time C module inside `micronux-guid`. Such a module may
use raw LVGL 9.5 APIs. M9 does not provide arbitrary runtime plugins or
`dlopen()` support for application-supplied LVGL code.

## Display and rendering design

`micronux-guid` creates one LVGL display at 800x1280 RGB565. Its flush callback
writes only invalidated rectangles through the accepted Linux framebuffer
path and calls LVGL's flush-completion function only after the destination
buffer is safe for reuse.

The initial prototype will benchmark two partial draw buffers at 16, 32, and
64 scanlines. For example, two 800x32 RGB565 buffers require 102,400 bytes,
compared with 2,048,000 bytes for one additional full-screen buffer. The final
buffer height is selected from measured frame time, scheduler responsiveness,
memory use, and DSI underrun evidence.

M9 will not allocate a full-screen surface per application. The session
manager keeps application LVGL object trees and activates the foreground
screen. Bounded image and canvas resources are owned by the daemon and charged
to the creating session.

The implementation must preserve the current denial of application
framebuffer `mmap()`. If positioned framebuffer writes cannot meet the
acceptance gate, the fix belongs in a narrow Linux presentation interface or
driver operation, not in direct application mappings.

## Input design

M9.1 will probe and bind the Kit C GT9271 through Linux. The preferred path is
the applicable upstream Goodix input driver plus an ESP32-P4 device-tree node.
If the accepted kernel lacks a required compatible or ESP32-P4 I2C behavior, a
minimal reviewable patch will be added rather than moving touch into the
loader or GUI application.

The Linux input layer reports contacts to `micronux-guid`. The daemon maps the
accepted portrait orientation into LVGL coordinates and routes semantic events
only to the foreground session. The first public UI contract requires one
primary pointer. The kernel may retain multitouch data for a later UI revision.
No display rotation is required in M9.

## Window and session behavior

The word "window" means an isolated application scene, not an overlapping PC
window. One application scene is active at a time.

`micronux-guid` maintains:

- one trusted system layer;
- zero or one active foreground application screen;
- bounded inactive application sessions;
- one focus owner;
- one virtual keyboard owned by the system layer; and
- one bounded event queue per application.

### Start

When enabled, the daemon acquires the display session, prevents framebuffer
console output from racing GUI rendering, initializes LVGL, creates the system
layer, opens UI-v1, and shows a minimal launcher or status scene.

### Launch

A launcher or shell request starts an application through the existing
MicroNUX job policy. The application opens UI-v1, negotiates an ABI version,
creates its scene, commits it, and requests presentation. Policy—not a client
claim—decides whether that session may become foreground.

### Switch

To switch applications, the manager:

1. sends a bounded suspend lifecycle event to the current foreground client;
2. stops routing input to it;
3. hides its LVGL screen;
4. activates the next screen;
5. assigns focus; and
6. sends a resume lifecycle event.

A client that fails to acknowledge a lifecycle event cannot stall the manager.

### Exit or fault

On clean exit, socket loss, protocol violation, resource-limit violation, or
process fault, the manager removes the session's objects, events, images,
timers, and focus. It then activates the launcher, previous valid application,
or recovery scene.

### Stop or disable

The daemon closes application sessions, releases input and display ownership,
restores the framebuffer console where supported, and leaves USB `ttyGS0`
available. Repeated daemon crashes enter a terminal-first safe mode instead of
an endless GUI restart loop.

## UI-v1 application contract

The public endpoint is an `AF_UNIX` `SOCK_SEQPACKET` socket:

```text
/run/micronux/ui-v1.sock
```

The protocol uses fixed-width little-endian headers, an ABI major/minor pair,
a request identifier, bounded payload lengths, and stable status values. Major
version mismatch fails closed. The daemon derives identity and granted
capabilities from `SO_PEERCRED`; it never trusts a capability mask supplied by
the client.

### Initial operation groups

| Group | Representative operations |
| --- | --- |
| Session | `HELLO`, `INFO`, `OPEN`, `CLOSE`, `REQUEST_FOREGROUND` |
| Transaction | `BEGIN`, `COMMIT`, `ABORT` |
| Object | `CREATE`, `DELETE`, `SET_PROPERTY`, `GET_PROPERTY` |
| Event | `SUBSCRIBE`, `UNSUBSCRIBE`, `NEXT_EVENT` |
| Resource | `CREATE_IMAGE`, `CREATE_FONT_REF`, `RELEASE_RESOURCE` |
| Lifecycle | `ACK_SUSPEND`, `ACK_RESUME`, `REQUEST_CLOSE` |
| System-only | notification, overlay, launcher, and administration operations |

The exact numeric wire values and structures are frozen during M9.3 before
client code is merged.

### Initial object set

UI-v1 will cover the existing declarative Ignite UI set where practical:

- screen and container;
- label and button;
- slider, switch, checkbox, bar, and spinner;
- dropdown, list, table, tabs, and modal;
- text field and text area;
- image; and
- a bounded canvas/drawing-command object.

An unsupported object or property returns a stable `UNSUPPORTED` result. It
must not silently fall back to a different widget.

### Handles and transactions

Objects and resources use opaque fixed-width handles. A handle is meaningful
only within its owning connection and includes generation protection against
stale reuse.

Clients may group creates and property changes inside a transaction. `COMMIT`
applies the validated batch and invalidates the necessary LVGL regions. A
rejected transaction leaves the previous visible scene intact.

### Events

Initial events include:

- clicked, pressed, released, and value changed;
- text changed and submitted;
- focus gained and lost;
- foreground, suspend, resume, and close requested;
- resource pressure; and
- service shutdown or GUI unavailable.

Events carry handles and bounded values, not pointers. Event queues have fixed
depths and explicit coalescing rules for high-frequency value changes. A slow
client cannot exhaust daemon memory or block rendering.

### Capabilities and quotas

The initial capability classes are application presentation/events, trusted
system overlay, and GUI administration. The public names and bit assignments
are frozen with the wire contract.

Each session has limits for:

- objects and nesting depth;
- text bytes;
- image and canvas bytes;
- transactions and message size;
- queued events;
- animations and timers; and
- inactive-session residency.

Cross-session handles, malformed messages, unauthorized system operations,
and over-limit requests fail without changing visible state.

## Native C and other language access

Native C applications link the static `libmicronux-ui.a` and include
`<micronux/ui.h>`. The library performs version negotiation, wire validation,
request serialization, status-to-`errno` mapping, and `poll()`-compatible event
waiting. It does not link LVGL.

C++, Rust, Zig, or another compiled language may call this C ABI. A bounded VM
or interpreter may implement a host binding against the same C ABI or the
documented UI-v1 messages. A language runtime must still fit the MicroNUX
NOMMU executable, memory, syscall, and W^X policies.

Shell commands are administration and orchestration clients. Intended uses
include status, enable/disable, launch, close, notification, and occasional
property updates. Shell scripts are not a per-frame rendering API.

## Ignite deployment boundary

Ignite remains one language pipeline:

```text
.ignite source -> direct Ignite compiler -> Ignite bytecode -> C VM
```

Its deployment targets are:

| Target | UI backend | Forbidden dependency |
| --- | --- | --- |
| Standalone Ignite firmware | Local embedded LVGL 9.5 backend | None; this target intentionally contains LVGL |
| Ignite for MicroNUX | UI-v1 host adapter | LVGL, `esp_lvgl_port`, ESP-IDF display/touch code |

`.igniteui` remains a backend-neutral declarative manifest aligned with named
handlers in `.ignite`. Standalone firmware converts it to local LVGL objects.
Ignite for MicroNUX converts it to UI-v1 transactions and stores opaque UI-v1
handles. Existing tap and value-change events are translated into the same VM
event behavior.

The MicroNUX Ignite runner must continue to execute non-GUI packages when the
GUI is absent. A GUI operation with no service returns a typed, recoverable
`GUI_UNAVAILABLE` result. It is not a VM fault and does not cause Ignite to
load LVGL.

Build validation must reject any direct or transitive LVGL, ESP-LVGL, or
ESP-IDF GUI dependency in the MicroNUX Ignite artifact.

## Packaging and lifecycle

### Stage 1: reproducible build profiles

The first implementation adds a Buildroot `micronux-gui` package and a
GUI-enabled defconfig. The accepted terminal-only profile remains buildable
and unchanged in behavior. The GUI package contains the daemon, LVGL, its
configuration, bounded fonts/themes, supervisor, CLI, C library, and headers.

Proposed administration commands are:

```sh
micronux-gui status
micronux-gui enable
micronux-gui disable
micronux-gui restart
micronux-gui launch APP
micronux-gui close APP
micronux-gui notify TEXT
```

### Stage 2: installable signed package

The current fixed initramfs cannot provide a truthful runtime install/remove
experience by itself. After MicroNUX gains a persistent signed package layer,
the same GUI payload may be installed from microSD without rebuilding the
kernel image. Package removal must restore the terminal-first configuration.

Stage 2 is not allowed to delay the Stage 1 GUI architecture and hardware
gate, but M9 documentation and layouts must not prevent it.

## Work packages

### M9.0 - Contract freeze

Section status: **COMPLETED**

Deliverables:

- [x] **COMPLETED** - this approved plan;
- [x] **COMPLETED** - the explicit three-mode LVGL ownership matrix;
- [x] **COMPLETED** - the two-flavor Ignite dependency boundary;
- [x] **COMPLETED** - an initial memory and image-size baseline; and
- [x] **COMPLETED** - a testable list of non-goals and completion gates.

Exit criterion: the plan is committed and approved before implementation
begins.

### M9.1 - Linux input and presentation foundation

Section status: **TESTING**

Deliverables:

- [x] **COMPLETED** - GT9271 Linux probe and input events;
- [x] **COMPLETED** - accepted reset, interrupt, and coordinate-transform
  device-tree contract;
- [x] **COMPLETED** - a safe foreground GUI ownership transition for fbcon and
  `/dev/fb0`;
- [x] **COMPLETED** - positioned, paced partial-rectangle presentation; and
- [ ] **TESTING** - a no-LVGL C diagnostic that proves display and primary
  touch input.

Validation evidence recorded 2026-08-10:

- `scripts/check-linux-patch-series.py` passed the ordered 33-patch contract
  with manifest SHA-256
  `8b81443dd04ba72ced44bbced7e0641b707d089948ab86c89977d7f4b9af0ba4`;
- a clean `scripts/m9-build.sh` integration build passed with Linux 6.12.27,
  `CONFIG_INPUT_EVDEV=y`, the M7 isolation contract retained, bFLT W^X
  validation passing, and no LVGL artifact in the M9.1 image;
- the generated DTB contains the polling-only Kit C touch contract at address
  `0x5d`, 10 ms polling, and unrotated 800x1280 coordinates. The Waveshare Kit
  C wiring has no connected GT9271 reset or interrupt GPIO, so their deliberate
  absence and Linux polling are part of the accepted contract;
- `micronux-display-test` cross-compiled with `-Werror` as a 90,160-byte bFLT
  executable and exposes check, draw, terminal restoration, and five-point
  touch acceptance markers; and
- the complete Linux image is 6,090,608 bytes, below the 6 MiB partition limit
  by 200,848 bytes.

Physical testing still required before any item or this section may be marked
`COMPLETED`:

- probe/status and event-device check on the connected GT9271;
- visible positioned draw and five-point primary-touch test;
- zero DSI underruns during the test;
- responsive USB shell and unrelated background task during presentation; and
- clean restoration of the framebuffer console after normal exit and signal.

Physical test evidence recorded 2026-08-10, iteration 1:

- GT9271 probe, `/dev/input/event0`, product ID, 800x1280 ranges, and zero touch
  transport errors passed;
- the USB shell remained responsive during a three-second full-frame draw and
  the diagnostic restored the framebuffer console;
- DSI diagnostics changed from `underruns=0` to `underruns=4`, so the physical
  presentation gate failed and the affected features returned to
  `IMPLEMENTING`; and
- no completion box was checked. The next iteration adds bounded userspace
  write chunks and an explicit inter-chunk gap before rebuilding and repeating
  the same measurement.

Physical test evidence recorded 2026-08-10, iteration 2:

- the rebuilt 6,090,608-byte image passed the 33-patch manifest, bFLT W^X,
  partition-size, and no-LVGL gates, then flash read-back verification passed;
- GT9271 readiness, `/dev/input/event0`, zero touch errors, the framebuffer
  interface contract, foreground draw, concurrent USB-shell responsiveness,
  and framebuffer-console restoration all passed again;
- the 256-byte/100-microsecond paced writer completed without the NOMMU timer
  stall seen in the first pacing attempt, but DSI diagnostics changed from
  `underruns=0` to `underruns=5`; and
- the presentation features remain `IMPLEMENTING`. Iteration 3 reduces the
  instantaneous framebuffer write burst to 64 bytes while retaining the
  100-microsecond inter-burst gap, then repeats the same zero-underrun gate.

Physical test evidence recorded 2026-08-10, iteration 3:

- the 64-byte/100-microsecond writer passed cross-compilation, the complete M9
  build gates, flash read-back verification, and the physical interface checks;
- an added mid-presentation diagnostic proved `underruns=0` before and after
  the full-frame paced write while the foreground pattern remained visible;
- the counter changed from `underruns=0` to `underruns=6` only after
  `KDSETMODE(KD_TEXT)` caused fbcon to redraw through its kernel drawing path;
  and
- the root cause is therefore the ownership transition rather than the paced
  presentation path. Iteration 4 brackets fbcon restoration with the standard
  fbdev blank API, and the Linux driver stops scanout while blanked, accepts the
  console redraw, then cleanly restarts scanout before restoring the backlight.

Physical test evidence recorded 2026-08-10, iteration 4:

- the complete clean Linux 6.12.27 build passed with all 33 patches, manifest
  SHA-256
  `77a4abb56b34519e5cb8e1e7329363fe4e2a4a6660202aa06242db94ca286002`,
  bFLT W^X enforcement, no LVGL artifact, and a 6,090,608-byte image;
- image, DTB, and metadata flash read-back verification passed on ESP32-P4
  revision 1.3;
- GT9271 readiness, `/dev/input/event0`, product 9271, 800x1280 coordinates,
  zero transport errors, and the framebuffer interface contract passed;
- the foreground diagnostic completed its 64-byte/100-microsecond paced draw,
  the USB shell remained responsive, and fbcon restoration completed through
  the kernel-owned blank/redraw/restart transition; and
- DSI diagnostics remained `underruns=0` before presentation, during the
  visible pattern, and after console restoration. The automated preflight
  emitted `MICRONUX:M9:PREFLIGHT:PASS`; the section advances to `TESTING` for
  its remaining five-point touch and visual-confirmation gates.

Physical test evidence recorded 2026-08-10, iteration 5:

- the interactive diagnostic accepted all five targets in the required order:
  top-left `79,81`, top-right `720,95`, bottom-right `718,1207`, bottom-left
  `91,1198`, and center `393,641`;
- every press was within the 96-pixel acceptance radius, each release was
  observed before advancing, the diagnostic emitted
  `MICRONUX:M9:DISPLAY-TEST:PASS mode=touch points=5`, and fbcon returned;
- post-touch DSI diagnostics reported `underruns=0`, no DMA error, and active
  scanout, producing `MICRONUX:M9:TOUCH-GATE:PASS`; and
- the GT9271 input/event feature and the polling-only unrotated 800x1280
  device-tree contract are now `COMPLETED`. The ownership, presentation, and
  diagnostic features remain `TESTING` until signal-recovery and final visual
  confirmation pass.

Physical test evidence recorded 2026-08-10, iteration 6:

- the recovery test launched a 30-second foreground diagnostic, delivered
  SIGTERM after the pattern became visible, and observed the expected exit code
  143 from the installed signal handler;
- the framebuffer console and USB shell returned, scanout remained active with
  no DMA error, and DSI diagnostics stayed `underruns=0` before and after the
  forced exit;
- the complete preflight emitted `MICRONUX:M9:PREFLIGHT:PASS` with
  `console=restored`, `signal=restored`, and `underruns=0`; and
- safe foreground ownership and recovery are now `COMPLETED`. Positioned
  presentation and the no-LVGL diagnostic remain `TESTING` only for the final
  human visual-confirmation gate.

Physical test evidence recorded 2026-08-10, iteration 7:

- the user confirmed that touch worked as expected and that all five visual
  targets could be followed successfully;
- the full-screen color pattern visibly painted from top to bottom. This is
  expected for the current deliberately paced single-buffer row writer and is
  not itself a failure;
- after loader progress reached 100%, the panel flickered rapidly between light
  blue and black before the framebuffer terminal appeared; after remaining at
  the terminal for approximately two minutes, the panel changed to solid light
  blue; and
- the delayed visual state is not accepted as stable presentation. M9.1 and
  positioned presentation return to `IMPLEMENTING`; the completed touch,
  device-tree, and ownership-transition results remain completed. A timed
  serial-attached soak now samples DSI underruns, DMA errors, scanout progress,
  touch health, and shell responsiveness while the visual failure is reproduced.
- the first soak invocation captured one healthy sample, then stopped because
  this minimal BusyBox `hush` does not support the runner's arithmetic loop.
  The test harness is being changed to emit pre-expanded sample commands; this
  harness failure is not counted as a MicroNUX runtime result.

Physical test evidence recorded 2026-08-10, iteration 8:

- the corrected 240-second soak completed all 17 samples from 0 through 240
  seconds while scanout advanced from frame 533 to frame 17,204;
- every sample reported active GDMA scanout, `underruns=0`, DMA
  `errors=00000000`, ready GT9271 touch with zero transport errors, and a
  responsive USB shell;
- the panel changed to the loader's solid light-blue framebuffer exactly when
  the host test completed and closed COM14, ruling out a spontaneous display
  or DMA timeout during the observed interval; and
- inspection found that PID 1 exited after the USB interactive shell received
  a terminal hangup. That caused Linux to reboot whenever the host terminal
  disconnected. The init shell is now being supervised and respawned, and a
  disconnect/reconnect hardware gate is being added before visual stability is
  accepted.

Physical test evidence recorded 2026-08-10, iteration 9:

- a deliberate host-close test captured reset reason `CHIP_USB_UART_RESET`,
  proving that native USB Serial/JTAG CDC close reset the entire ESP32-P4 and
  exposed the loader's light-blue framebuffer; the DSI controller did not fail;
- Linux now sets the ESP32-P4 USB-UART chip-reset-disable bit during machine
  restart initialization and exposes the root-writable policy at
  `/sys/kernel/micronux/usb_reset`; normal runtime state is `disabled`, while
  the flash workflow explicitly writes `enable` immediately before esptool;
- the complete 34-patch Linux series passed with manifest SHA-256
  `2b1d82b01553d9ae65beebcf8b3a5c41300774d3c1b4abb18afa743e0858aee9`, and a
  clean M9 build passed with Linux 6.12.27, the bFLT W^X audit, LVGL absent,
  and a 6,090,608-byte image;
- that exact image was written and hash-verified on the connected ESP32-P4;
  boot reported `USB serial reset-on-disconnect disabled`, and sysfs confirmed
  `disabled`; and
- the automated gate closed COM14 for 15 seconds, then reconnected without a
  Linux or loader boot marker. The same boot's scanout advanced from frame 487
  to frame 1,537, the shell returned, and both DSI underruns and DMA errors
  remained zero. Machine validation passed; human confirmation that the panel
  stayed on the MicroNUX terminal/status screen is the remaining visual gate.

Exit criterion: the diagnostic draws a target, receives correctly transformed
touch, returns to the terminal cleanly, and produces zero DSI underruns while
USB shell and unrelated tasks remain responsive.

### M9.2 - Optional LVGL service

Section status: **PLANNED**

Deliverables:

- [ ] **PLANNED** - Buildroot package and GUI defconfig;
- [ ] **PLANNED** - pinned LVGL 9.5.0 configuration;
- [ ] **PLANNED** - partial-buffer display port;
- [ ] **PLANNED** - Linux input port;
- [ ] **PLANNED** - serialized event loop;
- [ ] **PLANNED** - supervisor and safe-mode behavior; and
- [ ] **PLANNED** - minimal system/recovery scene.

Exit criterion: the service starts, renders, receives touch, stops, restarts,
and restores terminal access. The terminal-only image contains no LVGL
artifact.

### M9.3 - UI-v1 and native C SDK

Section status: **PLANNED**

Deliverables:

- [ ] **PLANNED** - frozen UI-v1 headers and protocol documentation;
- [ ] **PLANNED** - `SO_PEERCRED` authorization and capability mapping;
- [ ] **PLANNED** - session quotas and bounded parser;
- [ ] **PLANNED** - object, transaction, resource, and event operations;
- [ ] **PLANNED** - `<micronux/ui.h>` and `libmicronux-ui.a`;
- [ ] **PLANNED** - CLI inspection client; and
- [ ] **PLANNED** - protocol unit, malformed-input, and multi-client tests.

Exit criterion: an unprivileged native C application creates an interactive
scene and receives events without LVGL headers, framebuffer access, or special
device permissions.

### M9.4 - Window and session manager

Section status: **PLANNED**

Deliverables:

- [ ] **PLANNED** - foreground lifecycle;
- [ ] **PLANNED** - focus and input routing;
- [ ] **PLANNED** - trusted system overlay;
- [ ] **PLANNED** - virtual keyboard;
- [ ] **PLANNED** - launcher/recovery scene;
- [ ] **PLANNED** - suspend/resume/close behavior; and
- [ ] **PLANNED** - deterministic cleanup after client faults.

Exit criterion: two applications can be launched and switched one at a time;
only the foreground application receives input; killing either client returns
to a valid scene without restarting Linux.

### M9.5 - Ignite target separation and MicroNUX adapter

Section status: **PLANNED**

Deliverables:

- [ ] **PLANNED** - explicit standalone-firmware and MicroNUX build targets;
- [ ] **PLANNED** - shared compiler, bytecode, VM, GC, and package contracts;
- [ ] **PLANNED** - retained firmware LVGL backend;
- [ ] **PLANNED** - LVGL-free MicroNUX UI-v1 backend;
- [ ] **PLANNED** - `.igniteui` object/event translation;
- [ ] **PLANNED** - typed GUI-unavailable behavior; and
- [ ] **PLANNED** - negative dependency checks for the MicroNUX artifact.

Exit criterion: the same representative `.ignite` and `.igniteui` sources run
through embedded LVGL on standalone firmware and through UI-v1 on MicroNUX.
The MicroNUX binary contains no LVGL, ESP-LVGL, or ESP-IDF GUI symbol.

### M9.6 - Language and shell surfaces

Section status: **PLANNED**

Deliverables:

- [ ] **PLANNED** - complete public C example;
- [ ] **PLANNED** - documented foreign-function and direct-protocol binding
  rules;
- [ ] **PLANNED** - shell status and lifecycle commands;
- [ ] **PLANNED** - bounded canvas example; and
- [ ] **PLANNED** - API compatibility and automation-output tests.

Exit criterion: C, Ignite for MicroNUX, and a protocol-level test client create
equivalent scenes under the same permission and quota rules.

### M9.7 - Packaging, security, and physical acceptance

Section status: **PLANNED**

Deliverables:

- [ ] **PLANNED** - GUI enable/disable/restart workflow;
- [ ] **PLANNED** - crash-loop safe mode;
- [ ] **PLANNED** - terminal and GUI image-size comparison;
- [ ] **PLANNED** - memory, CPU, input-latency, and frame-time measurements;
- [ ] **PLANNED** - security and fault-injection tests; and
- [ ] **PLANNED** - combined display, touch, microSD, C6 network, and
  USB-console soak.

Exit criterion: every acceptance gate below passes on the reference Kit C and
the evidence is recorded in the repository.

## Validation ladder

### Host and build validation

1. Build and test the UI-v1 encoder, decoder, client state machine, quotas, and
   malformed-message handling on the host where possible.
2. Build the terminal-only profile and prove no LVGL library, header-derived
   object, font, theme, or `micronux-guid` binary is present.
3. Build the GUI profile and record exact kernel, rootfs, and final flash-image
   deltas.
4. Audit the Ignite for MicroNUX link map and symbols for forbidden LVGL,
   ESP-LVGL, and ESP-IDF GUI dependencies.
5. Run existing M7 isolation and M8 device-service regressions before flashing.

### Protocol and fault validation

- reject unknown major ABI versions;
- reject truncated, oversized, malformed, and unauthorized requests;
- reject stale and cross-session handles;
- enforce all quotas without partial visible changes;
- allow a slow event client without blocking rendering or other clients;
- clean up after client crash, kill, timeout, and disconnect;
- restart the daemon without rebooting Linux;
- enter terminal safe mode after a bounded crash loop; and
- keep raw MMIO, DMA, framebuffer mapping, and input access unavailable to
  ordinary applications.

### Physical Kit C validation

- boot terminal-only MicroNUX and confirm the existing console path;
- boot GUI-enabled MicroNUX and confirm the system scene;
- verify primary touch coordinates across corners, center, edges, press,
  release, and drag;
- run native C and Ignite UI examples;
- switch and kill applications while observing recovery;
- exercise microSD and C6 networking during rendering and touch input;
- keep USB shell responsive throughout;
- run a minimum 30-minute combined soak;
- report zero DSI underruns and no stale backlight, focus, or input ownership;
- measure input-to-visible response, frame time, CPU, and memory instead of
  claiming unmeasured desktop performance; and
- stop/disable the GUI and confirm terminal recovery.

## Completion criteria

M9 is complete only when:

1. terminal-only MicroNUX contains no LVGL and retains its accepted behavior;
2. GUI-enabled MicroNUX contains one LVGL owner, `micronux-guid`;
3. Linux owns display, touch, backlight, and input at all times after handoff;
4. ordinary applications cannot access LVGL pointers, raw framebuffer input,
   MMIO, or DMA;
5. native C and Ignite for MicroNUX use the same UI-v1 policy and events;
6. Ignite for MicroNUX contains no direct or transitive LVGL/ESP-IDF GUI code;
7. standalone Ignite firmware retains its embedded LVGL backend;
8. foreground, overlay, focus, lifecycle, and cleanup behavior pass;
9. daemon and application faults recover without rebooting Linux;
10. display, touch, microSD, C6 networking, and USB console pass together; and
11. physical acceptance records zero DSI underruns.

## Explicit non-goals

M9 does not include:

- X11, Wayland, DRM desktop composition, or a Linux desktop environment;
- overlapping, draggable, or resizable application windows;
- one full-screen framebuffer per application;
- video playback or high-frame-rate gaming guarantees;
- arbitrary runtime LVGL plugins;
- application-owned display or touch drivers;
- raw LVGL pointers in the public ABI;
- LVGL embedded in Ignite for MicroNUX; or
- a claim that the current initramfs already supports runtime package install
  and removal.

## Expected repository changes during implementation

MicroNUX changes are expected under:

- `buildroot-external/package/micronux-gui/`;
- `buildroot-external/configs/` for the GUI profile;
- `buildroot-external/board/micronux/` for Linux input/display configuration
  and narrowly scoped patches;
- `scripts/` for M9 build and acceptance automation;
- `docs/` for the UI-v1 contract and evidence; and
- the existing MicroNUX Ignite adapter package for UI-v1 integration.

The Ignite repository may require target-selection, backend-abstraction,
package-capability, compiler/tooling, and validation changes. Those changes
must preserve the existing standalone firmware backend and must not copy LVGL
into the MicroNUX runtime.

Each work package should be implemented, tested, committed, and pushed as a
coherent checkpoint. Implementation must not skip forward past a failed
kernel, protocol, isolation, or hardware gate.

## Primary references

- [LVGL 9.5 documentation](https://docs.lvgl.io/9.5/)
- [LVGL display overview](https://docs.lvgl.io/9.5/main-modules/display/overview.html)
- [LVGL input overview](https://docs.lvgl.io/9.5/main-modules/indev/overview.html)
- [Waveshare ESP32-P4-Module-DEV-KIT](https://docs.waveshare.com/ESP32-P4-Module-DEV-KIT)
- [Waveshare Kit C product details](https://www.waveshare.com/esp32-p4-module-dev-kit.htm)
- [Linux device ownership and application model](device-ownership-and-applications.md)
- [M7 Linux-owned display](m7-linux-display.md)
- [M8 device service and application ABI](m8-device-services.md)
