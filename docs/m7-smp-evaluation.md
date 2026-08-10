# M7 SMP evaluation

Status: **deferred; the M7 release remains single-core**

MicroNUX can compile Linux 6.12.27 as a two-hart RV32 NOMMU kernel, but that
kernel is not safe or flashable under the current M7 contract. SMP is therefore
an evaluated future path, not a hidden or partially enabled release feature.

The ESP32-P4 has two high-performance RISC-V cores, and Linux supports RISC-V
NOMMU SMP with its spin-wait boot protocol. The relevant hardware mechanisms
also exist: each P4 core has local CLIC interrupts, and the chip provides
software interrupts suitable for inter-processor calls. See the
[ESP32-P4 TRM](https://documentation.espressif.com/esp32-p4_technical_reference_manual_en.pdf)
and [ESP-IDF inter-processor call documentation](https://docs.espressif.com/projects/esp-idf/en/v6.0.1/esp32p4/api-reference/system/ipc.html).

## Reproducible compile result

Run the production build first, then run:

```sh
./scripts/m7-build.sh
./scripts/m7-smp-evaluate.sh
```

The evaluator copies the already patched Linux source into a separate cache,
leaves the accepted M7 build untouched, enables `CONFIG_SMP=y`, selects two
CPUs and spin-wait boot, and compiles `Image` plus `vmlinux`. Its machine-readable
report is written to `out/m7/smp-evaluation.txt`.

The 2026-08-09 evaluation produced:

| Measurement | M7 single-core | SMP experiment | Difference |
| --- | ---: | ---: | ---: |
| Linux `Image` | 6,024,944 B | 6,480,936 B | +455,992 B (+7.568%) |
| `vmlinux` file | 6,922,244 B | 7,455,156 B | +532,912 B |
| linked text | 3,658,576 B | 4,063,838 B | +405,262 B |
| linked data | 2,358,388 B | 2,410,984 B | +52,596 B |
| linked BSS | 247,941 B | 243,353 B | -4,588 B |

The experimental image exceeds the fixed 6 MiB Linux partition by 189,480
bytes. It is intentionally not packaged or flashed.

## Why SMP is deferred

The size failure is fixable later; the protection failure is the deciding
issue now:

1. `CONFIG_MICRONUX_ESP32P4_ISOLATION` deliberately depends on `!SMP` because
   PMP state is private to each hart. Enabling SMP currently disables the
   proven user-pool, per-process arena, uaccess, and W^X protection profile.
2. The loader installs the audited early PMP baseline only on hart 0 and then
   stalls hart 1. A two-hart handoff must install and verify the same baseline
   independently on both harts before either can run Linux userspace.
3. The M7 device tree describes only CPU 0. A safe SMP tree must describe hart
   1, its local interrupt controller, and both CLINT software/timer interrupt
   routes.
4. MicroNUX has no SBI. The loader therefore must place both harts into the
   Linux spin-wait entry protocol deterministically and prove restart and panic
   behavior without leaving FreeRTOS state active on either core.
5. Process migration and simultaneous U-mode returns need destructive tests
   proving that each hart revokes the previous PMP window before granting the
   current process arena. The complete WP6 fault suite must pass on both harts.

Because the experimental kernel removes the boundary the project just proved,
runtime throughput measurements would not be comparable to the release design.
MicroNUX makes no SMP speedup claim from this compile-only result.

## Revisit gate

SMP can be reconsidered only after all of these are implemented:

- a dual-hart loader handoff with per-hart PMP readback;
- complete CPU 1, CLIC, CLINT timer, and IPI device-tree/kernel support;
- SMP-compatible per-process PMP programming and cross-hart cache fencing;
- a partition/layout decision for the larger image;
- single-core versus dual-core measurements for native C and IgniteVM compute,
  mixed SD/C6/display I/O, latency, memory, and power;
- the full isolation suite, sustained peripheral soak, and three clean boots.

Until that gate is met, keeping HP core 1 stalled is a security property, not
an unfinished default.
