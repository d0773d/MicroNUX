# M9 Optional LVGL Window and Session Manager Plan

Status: **architecture plan; implementation not started**

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

Deliverables:

- this approved plan;
- the explicit three-mode LVGL ownership matrix;
- the two-flavor Ignite dependency boundary;
- an initial memory and image-size baseline; and
- a testable list of non-goals and completion gates.

Exit criterion: the plan is committed and approved before implementation
begins.

### M9.1 - Linux input and presentation foundation

Deliverables:

- GT9271 Linux probe and input events;
- accepted reset, interrupt, and coordinate-transform device-tree contract;
- a safe foreground GUI ownership transition for fbcon and `/dev/fb0`;
- positioned, paced partial-rectangle presentation; and
- a no-LVGL C diagnostic that proves display and primary touch input.

Exit criterion: the diagnostic draws a target, receives correctly transformed
touch, returns to the terminal cleanly, and produces zero DSI underruns while
USB shell and unrelated tasks remain responsive.

### M9.2 - Optional LVGL service

Deliverables:

- Buildroot package and GUI defconfig;
- pinned LVGL 9.5.0 configuration;
- partial-buffer display port;
- Linux input port;
- serialized event loop;
- supervisor and safe-mode behavior; and
- minimal system/recovery scene.

Exit criterion: the service starts, renders, receives touch, stops, restarts,
and restores terminal access. The terminal-only image contains no LVGL
artifact.

### M9.3 - UI-v1 and native C SDK

Deliverables:

- frozen UI-v1 headers and protocol documentation;
- `SO_PEERCRED` authorization and capability mapping;
- session quotas and bounded parser;
- object, transaction, resource, and event operations;
- `<micronux/ui.h>` and `libmicronux-ui.a`;
- CLI inspection client; and
- protocol unit, malformed-input, and multi-client tests.

Exit criterion: an unprivileged native C application creates an interactive
scene and receives events without LVGL headers, framebuffer access, or special
device permissions.

### M9.4 - Window and session manager

Deliverables:

- foreground lifecycle;
- focus and input routing;
- trusted system overlay;
- virtual keyboard;
- launcher/recovery scene;
- suspend/resume/close behavior; and
- deterministic cleanup after client faults.

Exit criterion: two applications can be launched and switched one at a time;
only the foreground application receives input; killing either client returns
to a valid scene without restarting Linux.

### M9.5 - Ignite target separation and MicroNUX adapter

Deliverables:

- explicit standalone-firmware and MicroNUX build targets;
- shared compiler, bytecode, VM, GC, and package contracts;
- retained firmware LVGL backend;
- LVGL-free MicroNUX UI-v1 backend;
- `.igniteui` object/event translation;
- typed GUI-unavailable behavior; and
- negative dependency checks for the MicroNUX artifact.

Exit criterion: the same representative `.ignite` and `.igniteui` sources run
through embedded LVGL on standalone firmware and through UI-v1 on MicroNUX.
The MicroNUX binary contains no LVGL, ESP-LVGL, or ESP-IDF GUI symbol.

### M9.6 - Language and shell surfaces

Deliverables:

- complete public C example;
- documented foreign-function and direct-protocol binding rules;
- shell status and lifecycle commands;
- bounded canvas example; and
- API compatibility and automation-output tests.

Exit criterion: C, Ignite for MicroNUX, and a protocol-level test client create
equivalent scenes under the same permission and quota rules.

### M9.7 - Packaging, security, and physical acceptance

Deliverables:

- GUI enable/disable/restart workflow;
- crash-loop safe mode;
- terminal and GUI image-size comparison;
- memory, CPU, input-latency, and frame-time measurements;
- security and fault-injection tests; and
- combined display, touch, microSD, C6 network, and USB-console soak.

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
