# M5 NOMMU Hardening and Stress Gate

Status: **complete** (hardware-verified on 2026-08-07)

M5 defines the Unix subset that MicroNUX can support honestly without an MMU
and turns it into a repeatable hardware gate. The final image passed three
consecutive ROM-reset boots on the ESP32-P4 revision 1.3 reference board. Each
boot ran the complete target self-test, returned to a usable shell, preserved
the same payload hashes, and stayed inside the fixed memory-loss budget.

## Supported process model

MicroNUX supports a deliberately narrow process lifecycle:

1. The parent calls `posix_spawn()` for a known bFLT executable.
2. uClibc-ng performs the NOMMU-safe spawn and exec transition.
3. The parent immediately reaps that child with `waitpid()`.

The M5 test uses a dedicated `/usr/bin/micronux-exec-child` executable that
does nothing except exit successfully. This isolates loader, exec, signal
return, and reap behavior from shell parsing and application work. The test
serializes child lifecycles; concurrent general-purpose workloads are not part
of the contract.

The following conventional Unix assumptions remain unsupported:

- `fork()` with copy-on-write address spaces
- independent per-process virtual address spaces or MMU isolation
- application use of raw `vfork()` as a general process API
- demand paging, memory overcommit, swap, and recoverable page faults
- unbounded process churn or arbitrary dynamically installed software

`posix_spawn()` is therefore the supported compromise for launching a bounded,
known executable. It does not make NOMMU behave like a conventional MMU-backed
Linux system.

## Fixed stress contract

The target package builds `micronux-selftest` and its minimal exec child with
strict warnings. The host gate checks exact markers and rejects any panic,
oops, BUG, unhandled trap or signal, timer stall, stack-smashing report, M3/M4
failure, incomplete console record, or payload-hash drift.

| Test | Per-boot contract | What is checked |
| --- | ---: | --- |
| Process lifecycle | 64 iterations | `posix_spawn()`, bFLT exec, normal exit, and `waitpid()` |
| Signal return | 64 deliveries | Synchronous `SIGUSR1` handlers return to userspace |
| Interval timer | 32 deliveries | Repeating 5 ms `SIGALRM` and timer cancellation |
| Allocation pressure | 4,096 KiB | 32 blocks, complete pattern write/read, then free |
| Stack integrity | depth 8, 128 bytes/frame | Explicit head, tail, frame, and caller canaries |
| Console integrity | 64 records | Every ordered sequence and xorshift checksum on the host |
| Memory budget | at most 512 KiB loss | `/proc/meminfo` `MemFree` before and after the test |
| Reset stability | 3 boots | ROM reset, loader handoff, shell recovery, and stable hashes |

Across the acceptance gate this produces 192 complete process lifecycles, 192
synchronous signal returns, 96 timer deliveries, and 192 checksum-verified
console records. Sixty-four serialized exec cycles per boot is the supported
baseline established by repeatable cold-boot testing; it is not a claim of
unlimited process churn.

All tested heap blocks and the userspace stack marker must lie inside the
loader-owned Linux memory interval `[0x48400000, 0x49f00000)`. This catches an
allocation or stack crossing into loader-reserved memory. The 4 MiB allocation
is fully verified before it is released rather than merely reserved.

The target userspace does not provide a usable libssp stack-protector runtime.
M5 consequently uses explicit volatile stack and frame canaries instead of
claiming compiler stack-protector coverage. These canaries validate this
bounded test path; they are not general memory-safety protection.

## Console backpressure hardening

Stress testing exposed a transmit race in the ESP32 USB Serial/JTAG console
driver. When the hardware TX FIFO was busy, the transmit path could return
without enabling the TX-empty interrupt, leaving already queued bytes with no
future event to drain them. The MicroNUX Linux patch now arms the TX-empty
interrupt before returning from that busy path. The regular interrupt-driven
driver remains in use; M5 does not replace it with a synchronous polling path.

The host waits for a complete newline-terminated PASS record before sending
the post-test probes. It also regenerates the seeded xorshift stream and checks
all 64 console sequence/checksum pairs, so observing only the first and last
line cannot produce a false pass.

## Build, flash, and test

From a PowerShell prompt in the repository, the complete reproducible M5 build,
isolated ESP-IDF loader build, flash, and three-reset gate is:

```powershell
.\scripts\m5.ps1 -Port COM14 -Boots 3
```

The Linux build uses Buildroot 2025.02.16 in a separate M5 output directory
and pins the source archive checksum, Linux 6.12.27, the Buildroot-generated
uClibc-ng toolchain, a fixed build timestamp, and the existing RV32
`ilp32`/NOMMU configuration. The loader build is isolated under `build/m5` and
requires ESP-IDF v6.0.1. `out/m5/SHA256SUMS` records the artifacts exercised by
the hardware gate.

Only one process may own the native USB port. Close an interactive console
before running the gate. Afterward, attach to the shell with:

```powershell
.\scripts\m4-console.ps1 -Port COM14
```

Press `Ctrl+]` to close the terminal. `COM14` is the port observed on the test
workstation, not part of the platform ABI.

## Hardware acceptance record

The final reproducible artifacts are:

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| Linux `Image` | 3,380,992 bytes | `a83d860ee337d4ccfdfc0583467c94339af8a7181fa9eb70887a4f3a8f16639f` |
| In-memory kernel span | 3,598,136 bytes | covered by the manifest |
| `rootfs.cpio` | 681,472 bytes | `2a50929047a206ec811b69efcb9d81d41c8534aba159400b9cb4f26411651eb9` |
| `esp32p4-micronux.dtb` | 1,477 bytes | `df5effc9b4db5930d3cc3c1df77d447f8a0104b196e2ea668d97b6a6a35d8199` |
| M5 payload manifest | 128 bytes | `a6eb556f9caa0b0161ae29f5e44656e7bc78cfd9c92bd4120d8093cec3d4d914` |
| `micronux-selftest` | 88,156 bytes | `ba23f22765bd6206c579dfe873213e5e5fa7b1ec1c47e755b7e9f8a7763c5cae` |
| `micronux-exec-child` | 56,416 bytes | `9a1f7a49859464f70f9e6ba34036628831e5713f8b9dc554dfb10bca7498aaeb` |

| Boot | Target stress time | `MemFree` before | `MemFree` after | Difference |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 687 ms | 20,400 KiB | 20,372 KiB | 28 KiB |
| 2 | 687 ms | 20,400 KiB | 20,372 KiB | 28 KiB |
| 3 | 687 ms | 20,400 KiB | 20,372 KiB | 28 KiB |

Every boot reported the same Linux and device-tree hashes shown above, reached
the native USB `ttyGS0` shell, passed all tests, and accepted a final shell
probe. The gate resets through the P4 ROM and loader path; it does not remove
power from the carrier.

## Remaining compromises

- Linux remains single-core, machine-mode, NOMMU, and RWX inside its PMP-owned
  27 MiB interval.
- The test demonstrates a bounded supported workload, not fault containment
  between processes or recovery from arbitrary memory corruption.
- The read-only build input is unpacked into a volatile RAM filesystem; there
  is no persistent storage or package manager.
- The console is an unauthenticated root development interface and not a
  security boundary.
- Storage, networking, display, USB host, and other optional peripherals stay
  outside the minimal image until M6 evaluates them independently.

Reference material:

- [ESP-IDF ESP32-P4 interrupt allocation](https://docs.espressif.com/projects/esp-idf/en/stable/esp32p4/api-reference/system/intr_alloc.html)
- [ESP-IDF ESP32-P4 console configuration](https://docs.espressif.com/projects/esp-idf/en/stable/esp32p4/api-reference/system/console.html)
- [Waveshare ESP32-P4-Module-DEV-KIT wiki](https://www.waveshare.com/wiki/ESP32-P4-Module-DEV-KIT)
