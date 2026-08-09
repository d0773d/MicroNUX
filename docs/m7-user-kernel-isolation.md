# M7 User/Kernel Isolation Results

Status: **WP0-WP4 proven on hardware; per-process containment active**

M7 is converting the ESP32-P4 revision-1.3, machine-mode, single-core NOMMU
baseline into a system where a faulty bFLT child can be terminated without
being able to corrupt Linux or another userspace process. The work is
deliberately gated in layers. WP3 proved the kernel boundary for a shared user
pool; WP4 now gives each userspace `mm_struct` its own hardware-enforced arena.

## Frozen baseline

| Contract | Measured value |
| --- | --- |
| ESP-IDF | v6.0.1, commit `8c19b156084a0753687347cca1f5355782893533` |
| Linux | 6.12.27, RV32IMAC, M-mode, NOMMU, one HP core |
| Linux RAM | `[0x48400000,0x49f00000)`, 27 MiB |
| Candidate user pool | `[0x49700000,0x49f00000)`, 8 MiB |
| Candidate kernel RAM | `[0x48400000,0x49700000)`, 19 MiB |
| Loader reserve | `[0x48000000,0x48400000)`, 4 MiB |
| Comms reserve | `[0x49f00000,0x4a000000)`, 1 MiB |
| M7 Image | 5,810,912 bytes, SHA-256 `9bb6555fff500fcaffe18232f1be1015485a291cfc5907c112011ae7ab1e5de8` |
| M7 DTB | 2,147 bytes, SHA-256 `a820ab4da5722a71457adae47e25934c863f34f16fcbced747ef13d6390647db` |
| M7 rootfs | 1,512,960 bytes, SHA-256 `597553ae7b1d7d2b5dad384bacfba8d3022a9dad4255977519db452e9a64a4c2` |

The dedicated reservation leaves 19 MiB in the general Linux allocator. The
M7 kernel reported 13,020 KiB available during boot and a repeatable 8,804 KiB
`MemFree` after the shell and device service were ready. The WP4 pool baseline
was 465 pages reserved, 394 pages mapped, 1,583 pages free, and five live
arenas on every measured boot.

The automated baseline executes three independent ROM-reset boots, records all
16 PMP address/configuration entries before and after the loader handoff setup,
requires identical kernel and DTB hashes, verifies bFLT v4 `FLAT_FLAG_RAM`,
runs the checked 4 MiB allocation, and probes device-service recovery. Each
measured child run consumed 8 KiB and returned control to the shell.

## Reproducible early U-mode denial

The tracked patch under `loader/patches/esp-idf-v6.0.1/` applies only to the
exact ESP-IDF commit above. `scripts/m7-prepare-idf.ps1` creates a task-local
worktree, refuses a modified global installation or wrong revision, verifies
the patched source hash, and proves that the patch can be reversed. Both the
bootloader and application compile databases must reference that verified
source before flashing is allowed.

On ESP32-P4 revision 1.3, the hardware readback before Linux handoff was stable
across three resets:

| PMP entries | Address/config | U-mode result |
| --- | --- | --- |
| 0, 1 | `27fffffc/18`, `3ff0fffc/18` | CPU-control ranges denied |
| 2 | `4fc0fffc/18` | ROM denied |
| 3, 4 | `4ff00000/00`, `4ffc0000/08` | internal RAM denied |
| 5 | `00000000/00` | free |
| 6 | `41fffffc/18` | cached flash denied |
| 7-10 | `00000000/00` | free for Linux return rules |
| 11 | `5010bffc/18` | LP memory denied |
| 12 | `00000000/00` | free for Linux direct-alias denial |
| 13, 14 | added as `48400000/00`, `49f00000/0f` | unlocked broad Linux handoff window |
| 15 | `5007fffc/18` | peripheral MMIO denied |

The accepted loader is 273,008 bytes with SHA-256
`c352d61e0d0af3e82f0f1657e24f88deca99d537908ec0d8062c21c51d65ca67`.
It emits:

```text
MICRONUX:M7:PMP baseline=pass early-deny=pass handoff=13-14-unlocked overlay=13-14 linux=[48400000,49f00000)
```

The unchanged Linux payload then booted on all three resets, retained its
payload hashes, completed the 4 MiB bFLT stress, and recovered the M8 device
service. The patch is reversible through the P4 ROM download path and does not
modify or replace the factory ESP32-C6 firmware.

The selected M6 combined regression then passed another three ROM resets with
artifact-to-boot hash equality enforced. Every boot retained the same 1 MiB
microSD sample hash while storage and network traffic ran concurrently. The
router reconnect holdoff recovered on attempts 5, 9, and 6, all within the
ten-attempt/five-second policy. A final M8 gate under the same loader passed
the shell, native C, real IgniteVM bytecode, peer-credential policy,
nonblocking wait, killed-client, killed-service, and raw-MMIO-denial checks.

## Dedicated NOMMU user pool

The M7 Buildroot profile now reserves `[0x49700000,0x49f00000)` with a
`micronux,esp32p4-user-pool` device-tree contract. A kernel configuration gate
requires ESP32-P4 M-mode, NOMMU, one core, reserved memory, and bFLT. Its
page-granularity first-fit allocator has no fallback to the kernel buddy
allocator, zeroes allocations before use, rejects invalid and double frees,
and exposes root-only accounting through `/proc/micronux_user_pool`.

The NOMMU mapping path sends copied private mappings and anonymous libc heap
mappings to that allocator. File-backed shared/direct mappings are rejected.
The one apparent exception is uClibc's `MAP_SHARED | MAP_ANONYMOUS` convention:
the NOMMU kernel already implements that no-`fork()` case as a private copied
mapping, so it is accepted and still allocated from the pool. Rejecting it
would prevent BusyBox from allocating its first heap block.

The bFLT loader requires uncompressed v4 `FLAT_FLAG_RAM` executables and checks
monotonic header fields, relocation file extent, overflow-safe data/BSS/stack
math, and the complete initial mapping against the 8 MiB budget before
`begin_new_exec()`. The M7 probe verifies its stack and every `/proc/self/maps`
VMA lie inside the pool, allocates and checks one MiB of zero-filled anonymous
memory, and reports allocator accounting.

The WP2 acceptance run produced identical results across three ROM-reset
boots:

```text
M7 pool boot 1/3 passed: used=394 free=1654 mem_kib=9036->9028
M7 pool boot 2/3 passed: used=394 free=1654 mem_kib=9036->9028
M7 pool boot 3/3 passed: used=394 free=1654 mem_kib=9036->9028
```

Each boot ran the probe, the M5 process/fault/stress gate, and eight additional
probe executions. Pool accounting returned exactly to baseline after every
run and was identical after every reset. The general allocator delta remained
8 KiB, below the 16 KiB gate.

The same payload then passed the three-boot combined microSD/C6 regression.
The 1 MiB card sample remained
`6158c8c683a1c1a66950c4e6593af64b0356cc52702e76ca00af1bdff5978c49` while
storage and network traffic overlapped. Router reconnects completed on
attempts 5, 1, and 7, within the ten-attempt/five-second policy. Every boot
also retained the exact Kit C JD9365 scanout marker.

Reproduce the complete build, loader/payload flash, and pool gate with:

```powershell
.\scripts\m7.ps1 -Port COM14 -Boots 3 -Flash -Test `
    -ConfirmExactKitC -ConfirmPmpChange
```

## Per-return PMP and bounded uaccess

The generic RISC-V startup path normally installs entry 0 as an all-address
RWX NAPOT region. Entry 0 has highest priority on ESP32-P4, so that grant
silently shadowed both the loader baseline and every later rule. The M7 kernel
now omits that bootstrap grant when isolation is selected and preserves the
audited loader map.

Before every CLIC first-stage return to U-mode, Linux revokes the previous
grant, writes the current `mm_struct` bounds, and reads every changed CSR back.
Entries 7-9 form a cached-address TOR ladder that denies below the authorized
interval, grants the interval RWX, and denies above it through `0x4fc00000`.
Entry 12 TOR-denies the upper/direct-address half through `0xffffff80`, and
entry 13 NAPOT-denies its final 128-byte granule. Missing, malformed, or
unverifiable bounds trigger a best-effort deny-all transition and kernel panic
instead of a U-mode return.

NOMMU `access_ok()` uses the same bounds and overflow-safe subtraction. It
rejects complete and crossing kernel, loader, CLINT, MMIO, below-pool, and
overflowing ranges, while retaining the standard zero-length operation at the
exclusive upper bound.

The clean payload passed three independent ROM resets on ESP32-P4 revision
1.3:

```text
M7 pool boot 1/3 passed: used=394 free=1654 mem_kib=8916->8908
M7 pool boot 2/3 passed: used=394 free=1654 mem_kib=8916->8912
M7 pool boot 3/3 passed: used=394 free=1654 mem_kib=8916->8908
```

Each boot proved an illegal M-mode CSR read raises `SIGILL`; cached loader and
kernel read/write/execute attempts raise `SIGSEGV`; direct-alias loader
read/write/execute attempts raise `SIGSEGV`; and CLINT/MMIO read/write/execute
attempts raise `SIGSEGV`. Seven invalid syscall-pointer classes were rejected
in both copy directions with `EFAULT`. The suite then reran the M5 process,
signal, timer, allocation, and console gate plus repeated process teardown.
Pool accounting returned to `394/1654` every time.

## Per-process arenas

Each NOMMU `mm_struct` now owns one contiguous, grow-only arena inside the
8 MiB user pool. The bFLT loader reserves the exact initial mapping at the
start of the largest free extent. Anonymous and copied-private mappings first
reuse free pages within that arena and may then extend only through immediately
adjacent unowned pages. An arena never grows across a page owned by another
process; an allocation that cannot remain contiguous fails with `ENOMEM` even
if smaller, non-contiguous extents remain elsewhere in the pool.

The allocator maintains a global reservation bitmap, a page-owner table, and
a mapped-page bitmap for each arena. It zeroes an initial arena before
publishing valid bounds, zeroes every later mapping before returning it, keeps
freed VMA pages reserved to the owning `mm_struct`, and clears the PMP bounds
before releasing an arena for reuse. Ownership inconsistencies fail closed
instead of clearing pages attributed to a different process.

The U-return hook now loads `user_lo` and `user_hi` from the current
`mm_struct`; it no longer grants the complete pool. Generic string uaccess
helpers also cap their scan at the current `user_hi`, which lets normal exec
argument copying work without relaxing the range check. The negative test
discovers the exact upper bound through zero-length syscalls, requires a
two-byte buffer crossing that bound to return `EFAULT`, accepts only a
zero-length operation exactly at `user_hi`, and rejects `user_hi + 1`.

The arena-specific hardware test keeps a parent and child alive
simultaneously, exchanges their known stack addresses through inherited pipe
descriptors, and verifies each receives `SIGSEGV` when reading the other's
address. It then runs 32 write/re-exec/reuse cycles at the same address and
requires zero-filled memory each time. A deliberately corrupt bFLT relocation
tests exec-failure teardown and exact accounting recovery. The final clean
payload passed three independent ROM resets:

```text
M7 arena boot 1/3 passed: reserved=465 mapped=394 free=1583 arenas=5 mem_kib=8804->8792
M7 arena boot 2/3 passed: reserved=465 mapped=394 free=1583 arenas=5 mem_kib=8804->8792
M7 arena boot 3/3 passed: reserved=465 mapped=394 free=1583 arenas=5 mem_kib=8804->8792
```

Every boot also passed the one MiB fragmentation regression, all 16 hardware
fault cases, both directions of syscall-pointer validation, eight repeated
probe executions, and the M5 4 MiB allocation/process/signal/timer/console
suite. The sibling addresses were `0x499c6e0c` and `0x499eee80`; the 32 reuse
cycles consistently reused `0x499ef000`. Pool accounting returned exactly to
baseline and general `MemFree` remained within the 16 KiB tolerance.

## Exact guarantee and remaining work

WP4 establishes **per-process CPU containment**: U-mode code can access only
the arena belonging to the `mm_struct` currently returning to userspace. It
cannot directly read, write, or execute kernel RAM, loader RAM, direct aliases,
protected platform regions, or another live process's arena. The M-mode kernel
remains fully trusted and is not constrained by these unlocked PMP
permissions.

This is not yet the complete untrusted-application profile. Arena pages remain
RWX, and PMP does not act as an IOMMU for peripheral DMA. Application jobs also
still originate from the root shell without the dedicated identity,
capability/device policy, and resource supervisor required by WP5. WP6 audits
every enabled DMA path and either implements the planned W^X split or retains
it as an explicit measured limitation.
