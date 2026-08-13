# M7 ESP32-P4 User/Kernel Isolation Plan

Status: **implementation plan; not yet a security claim**

This document is an execution brief for the agent implementing M7 isolation.
It turns the current single-core, machine-mode, NOMMU platform into a system
where a faulty user-compiled bFLT program can be terminated without corrupting
or crashing the kernel.

Do not describe MicroNUX as safe for arbitrary uploaded programs until every
required hardware gate in this document passes. Preserve unrelated work in the
repository, especially the current M6 networking changes, and add the M7 work
incrementally.

## Desired result

The first assurance level is **kernel containment**:

- U-mode can access only a dedicated user-memory pool in PSRAM.
- The kernel, loader memory, internal SRAM, ROM, CPU-control registers, and
  peripheral MMIO are inaccessible from U-mode.
- A user instruction/load/store protection fault kills the process with a
  signal; it does not panic or reset the kernel.
- A user pointer supplied to a syscall is accepted only when its complete,
  overflow-checked range lies in permitted user memory.
- User mappings cannot escape the dedicated pool or cause kernel memory to be
  exposed through a direct device mapping.

The second assurance level is **per-process containment**:

- Each `mm_struct` receives one contiguous physical arena within the user pool.
- Only the arena belonging to the task returning to U-mode is enabled by PMP.
- A process cannot access the kernel, the loader, MMIO, or another process's
  arena.

The first level is useful with MicroNUX's existing serialized process contract,
but programs can still corrupt the shell or another program sharing the pool.
Only the second level justifies a process-isolation claim.

## Non-goals

- Adding an MMU, paging, swap, memory overcommit, or copy-on-write `fork()`.
- Supporting arbitrary ELF binaries or dynamic loaders. The executable format
  remains bounded, all-in-RAM bFLT.
- Enabling SMP during the isolation work. HP core 1 must remain stalled.
- Providing W^X in the first containment release. The existing RISC-V signal
  return path executes a trampoline from the user stack, so the initial user
  arena remains RWX.
- Treating PMP as protection against vulnerabilities in trusted M-mode kernel
  code. Syscall reduction and least privilege reduce that attack surface but do
  not replace correctness.

## Verified baseline

These facts were checked against the repository and the installed toolchains on
2026-08-08:

| Item | Baseline |
| --- | --- |
| Board | Waveshare ESP32-P4-Module-DEV-KIT |
| Silicon | ESP32-P4 revision 1.3/ECO2, reported revision `103` |
| Loader | ESP-IDF v6.0.1, target `esp32p4` |
| Kernel | Linux 6.12.27, RV32IMAC, machine mode, NOMMU, single core |
| Userspace | Buildroot 2025.02.16, uClibc-ng, BusyBox, bFLT |
| PMP granularity | 128 bytes on ESP32-P4 |
| Current Linux window | `[0x48400000, 0x49f00000)`, 27 MiB, U-mode RWX |
| Current loader reserve | `[0x48000000, 0x48400000)`, 4 MiB |
| Current comms reserve | `[0x49f00000, 0x4a000000)`, 1 MiB |
| Current process contract | `posix_spawn()`/exec/wait, serialized bounded jobs |

The M5 `micronux-selftest` and `micronux-exec-child` artifacts are already bFLT
version 4 `ram gotpic` executables. Keep an automated header check so a future
toolchain change cannot silently produce the split text/data layout.

### Current PMP problem

`loader/main/micronux_loader.c` programs entries 13 and 14 as a locked TOR
pair granting U-mode RWX over the complete Linux interval. This lets any user
program overwrite the kernel.

On revision 1.3, ESP-IDF's
`components/esp_hw_support/port/esp32p4/cpu_region_protect.c` selects
`esp_cpu_configure_region_protection_rev_less_than_v3()`. With
`CONFIG_ESP_SYSTEM_MEMPROT` disabled, the application startup leaves the
following effective layout before `app_main()`:

| Entry | Current role | Current U-mode consequence |
| ---: | --- | --- |
| 0 | CPU subsystem, locked RW | CPU/interrupt-control access allowed |
| 1 | CPU peripherals, locked RW | CPU peripheral access allowed |
| 2 | I/D ROM, locked RX | ROM read/execute allowed |
| 3/4 | internal RAM TOR pair, entry 4 locked RWX | loader/internal RAM access allowed |
| 5 | unused | available if readback confirms it |
| 6 | cached flash, locked RX | flash read/execute allowed |
| 7-10 | unused | reserved by this plan for the Linux overlay |
| 11 | LP memory, locked RWX | LP memory access allowed |
| 12 | unused | keep as a spare after readback confirms it |
| 13/14 | MicroNUX Linux TOR pair, locked RWX | all Linux RAM allowed |
| 15 | peripheral range, locked RW | peripheral MMIO allowed |

The low-numbered locked grants are as important as the broad Linux grant.
Adding a Linux-RAM overlay alone would still let U-mode reprogram interrupt,
timer, cache, or peripheral state and destabilize the machine. PMP entries must
be audited from hardware rather than inferred only from source.

### Linux-specific problems

- Generic Linux 6.12 `access_ok()` returns true on NOMMU. Because the kernel
  runs in M-mode and bypasses unlocked PMP permissions, a syscall such as
  `read(fd, kernel_address, length)` would otherwise make the trusted kernel
  write to the supplied kernel address.
- The all-in-RAM branch of `fs/binfmt_flat.c` creates one initial contiguous
  mapping for text, data, BSS, relocation workspace, and stack. Runtime
  anonymous `mmap()` allocations are separate, however, so protecting only the
  initial bFLT mapping would break large `malloc()` calls or require allowing
  unsafe gaps containing kernel allocations.
- The ESP32-P4 CLIC return patch adds a two-stage `mret` for interrupted
  userspace. Any PMP return hook must run before the first-stage CLIC `mret`.

## Architecture

### Physical ownership

Split the existing Linux interval into a kernel-owned range and a dedicated
user pool. The first measurement candidate is:

```text
0x48000000  loader reserve, 4 MiB
0x48400000  kernel-owned Linux RAM
    ...
0x49700000  candidate user pool, 8 MiB
0x49f00000  comms reserve, 1 MiB
0x4a000000  end of PSRAM
```

`[0x49700000, 0x49f00000)` is a starting measurement, not a value to freeze
without testing. It would leave 19 MiB for the kernel and 8 MiB for all user
arenas. Measure the storage build, the networking build when ready, the M5
4 MiB allocation, peak boot memory, and the maximum simultaneous shell/job
footprint. The final boundaries must be page aligned and therefore also satisfy
the P4's 128-byte PMP granularity.

Describe the pool as reserved memory in the M7 device tree so the ordinary
buddy allocator cannot place kernel pages inside it. Give it a MicroNUX-specific
compatible string and initialize a dedicated page-granularity allocator from
that node. Do not use the general DMA pool as the user allocator.

### User mapping ownership

All private NOMMU userspace mappings must be allocated from the dedicated pool,
including:

- the bFLT image mapping;
- BSS, heap, and stack capacity;
- anonymous mappings used by uClibc `malloc()`;
- any copied private file mapping that is intentionally supported.

No pool address may be returned to the buddy allocator, and no buddy-allocated
address may be returned as a U-mode mapping. Add an explicit allocation-origin
check to every NOMMU free, shrink, unmap, exec-failure, and `exit_mmap()` path
touched by the implementation. Preserve `mmap_pages_allocated` accounting.

For the first kernel-containment checkpoint, different processes may receive
separate allocations from the same globally visible user pool. For the final
checkpoint, allocate one contiguous arena per `mm_struct` and suballocate all
of that process's VMAs from it. Store at least these fields in the RISC-V NOMMU
`mm_context_t`:

```c
unsigned long user_lo;
unsigned long user_hi;
bool user_bounds_valid;
```

An out-of-line arena structure may hold the free-range/bitmap state. The arena
must be established before the bFLT loader's first checked user copy and must
be released exactly once after the final VMA teardown. `vfork()`/`CLONE_VM`
temporarily shares the parent's `mm`; it must not create a second owner for the
same arena. Unsupported `fork()` behavior must remain rejected. Zero the full
arena before setting `user_bounds_valid`; otherwise PMP would make unallocated
gaps and data left by a previous process readable. Clear bounds before an arena
is returned for reuse.

Require `FLAT_FLAG_RAM` at exec time. Reject split text/data bFLT and any image
whose checked header arithmetic, relocation count, stack request, or configured
memory budget cannot fit within its assigned arena. Do not form bounds with
unchecked additions.

### Dynamic PMP overlay

Reserve entries 7-10 for an unlocked, per-hart TOR overlay. ESP32-P4 gives the
lowest-numbered matching entry the highest priority, so this overlay takes
priority over the existing locked entry-14 Linux allow window:

| Entry | Address value | Mode/permission | U-mode result |
| ---: | --- | --- | --- |
| 7 | Linux lower bound | OFF | lower bound for entry 8 |
| 8 | current `user_lo` | TOR, no RWX, unlocked | deny kernel-side Linux RAM |
| 9 | current `user_hi` | TOR, RWX, unlocked | allow the current arena |
| 10 | Linux upper bound | TOR, no RWX, unlocked | deny remaining Linux RAM |

The conceptual ranges are:

```text
[0x48400000, user_lo)  U-mode denied
[user_lo, user_hi)     U-mode RWX
[user_hi, 0x49f00000)  U-mode denied
```

PMP CSR address fields use address bits shifted right by two. Bounds must also
obey the P4's 128-byte granularity; page-aligned arena allocations meet this
requirement. Do not set `PMP_L` on entries 7-10. With `L=0`, their permissions
apply to U-mode while the M-mode kernel bypasses them.

Program the overlay on every M-to-U return, not only in `__switch_to()`. An
exec may replace the current `mm` without a task switch. Add a P4-specific hook
to the U-mode branch of `ret_from_exception` while interrupts are disabled and
before the existing CLIC first-stage `mret`. The helper may use `current->mm`
and must fail closed:

- no `mm`, invalid bounds, reversed bounds, misalignment, or out-of-pool bounds
  means deny the complete Linux interval;
- revoke entry 9's allow permission before changing addresses;
- write and read back the bounds/configuration;
- enable entry 9 RWX last;
- finish with the required memory/instruction synchronization;
- never alter locked entries through a whole-CSR write without preserving their
  fields.

The first static-pool checkpoint may set `user_lo`/`user_hi` to the complete
pool. The final checkpoint supplies the current process arena instead.

### Early ESP-IDF PMP baseline

The U-mode denial for non-Linux address ranges must be installed before any
locked permissive entries are created. It is too late to fix entries 0 and 1
from `app_main()`.

Maintain a tracked ESP-IDF v6.0.1 patch under a loader-owned patch directory
and apply it to a disposable or task-local IDF checkout during the M7 build. Do
not make the only copy of the change inside the global `C:\esp\v6.0.1\esp-idf`
installation.

For the revision-less-than-v3 application path, convert regions that U-mode
must never access into matching PMP regions with no R/W/X permission and no
lock bit. M-mode IDF and Linux code can still access those regions because
ordinary PMP permissions are bypassed by M-mode when `L=0`. Reset inherited
bootloader permission bits before installing each deny entry. In particular,
deny U-mode access to:

- CPU subsystem and CPU peripheral regions;
- internal IRAM/DRAM;
- I/D ROM and cached flash unless a measured userspace ABI later proves a
  read-only dependency;
- LP memory and LP peripherals;
- the general peripheral/MMIO range.

Keep entries 7-10 writable and unused for Linux. Keep entries 13/14 as the
locked broad Linux handoff window during the first implementation; the dynamic
lower-number overlay constrains U-mode inside it. Entry 14 remains RWX for the
M-mode NOMMU kernel.

Add a loader-time CSR audit that reads all 16 PMP entries on the boot hart and
fails the handoff if:

- an expected U-deny entry is permissive or locked unexpectedly;
- entries 7-10 are not writable/free;
- entries 13/14 do not exactly describe the Linux interval;
- any configured boundary differs from the compiled memory contract.

Print one stable machine-readable boot marker summarizing the contract; avoid
printing a line on every context switch.

### Syscall user-pointer validation

Override the RISC-V NOMMU `access_ok()` behavior for the ESP32-P4 isolation
configuration. The check must use the current `mm_context_t` bounds and must be
overflow safe. Conceptually:

```c
if (!user_bounds_valid)
    return false;
if (addr < user_lo || addr > user_hi)
    return false;
return size <= user_hi - addr;
```

Define and test the desired zero-length behavior explicitly. An address equal
to `user_hi` can only be accepted for a zero-length operation. During the
static-pool checkpoint, these bounds are the whole user pool; during the final
checkpoint, they are the process arena.

Wire the architecture override before `asm-generic/uaccess.h` supplies its
always-true NOMMU default. Ensure the bFLT loader establishes bounds before
calling `clear_user()`, `copy_to_user()`, or any helper that invokes
`access_ok()`.

Audit all MicroNUX-added drivers and syscall-facing code for:

- direct dereferences of `__user` pointers;
- `raw_copy_*_user()` or unchecked `_copy_*_user()` calls;
- IOCTLs carrying nested pointers;
- arithmetic performed before a size/range overflow check;
- direct PFN/device `mmap()` paths.

Normal paths must use checked `get_user()`, `put_user()`, `copy_from_user()`,
`copy_to_user()`, `clear_user()`, or an equally strict audited helper. A failed
check must return `-EFAULT`, never attempt the M-mode access.

### Fault and scheduling behavior

Confirm that instruction, load, and store access faults originating in U-mode
reach the normal RISC-V signal path and result in `SIGSEGV`/`SEGV_ACCERR`. The
P4 CLIC cause masking must retain the standard exception cause values. A fault
originating in M-mode remains a kernel defect and may panic.

Because PMP CSRs are per hart, this design is conditional on the current
single-core contract. If SMP is evaluated later, every hart capable of running
userspace needs the same return hook, correct migration behavior, and a
per-hart readback test.

## Implementation work packages

Complete the packages in order. Do not start policy hardening and call it
isolation before the hardware and syscall-pointer gates pass.

### WP0 - Baseline capture and contract freeze

1. Record `git status --short` and preserve unrelated modified/untracked files.
2. Build the current stable M6 storage image and retain hashes, image sizes,
   `MemFree`, peak allocation behavior, and the three-reset hardware result.
3. Add a temporary loader CSR dump and capture all 16 entry address/config
   values on revision 1.3. Remove or reduce the verbose dump after the contract
   is encoded in the automated audit.
4. Confirm the M5 and shell bFLT files report `ram`; record their header sizes,
   stack sizes, and peak runtime mapping usage.
5. Measure the minimum viable kernel/user split. Start with the 8 MiB candidate
   only if the measured kernel headroom remains acceptable.

Exit gate: the exact boot-hart PMP state and a justified user-pool size are
recorded. No containment claim is made.

### WP1 - Reproducible early PMP denial

1. Add a version-pinned ESP-IDF v6.0.1 patch for the revision-less-than-v3 PMP
   setup.
2. Add a reproducible preparation/build step that refuses the wrong IDF tag or
   an already mismatched source tree.
3. Leave entries 7-10 unlocked/free and deny non-Linux U-mode regions as
   described above.
4. Extend `prepare_pmp_for_linux()` with the complete readback audit and stable
   marker.
5. Boot the unchanged Linux image and rerun the complete M5/M6 regression.

Exit gate: Linux still boots and all existing features work in M-mode, while
the hardware readback proves there are no unintended U-mode grants outside the
Linux interval.

### WP2 - Dedicated NOMMU user pool

1. Add the M7 device-tree reserved-memory node and a small P4-specific pool
   allocator with page-aligned contiguous allocation and deterministic free.
2. Route all supported private/copy NOMMU user mappings through this pool.
3. Route every associated unmap, partial shrink, exec-failure, and exit path to
   the matching pool free operation; keep mapping accounting balanced.
4. Reject direct device/PFN mappings and unsupported shared mappings from the
   untrusted execution profile.
5. Enforce all-in-RAM bFLT and checked image/relocation/stack limits.
6. Confirm the M5 4 MiB allocation and repeated exec test still pass without
   allocating U-visible pages from kernel RAM.

Exit gate: every U-visible VMA is wholly inside the reserved pool, every pool
allocation is represented by a VMA/region, and repeated process teardown
returns the pool and kernel memory counters to their baseline tolerances.

### WP3 - Static-pool PMP containment

1. Add an isolation Kconfig option depending on `SOC_ESP32P4`, `RISCV_M_MODE`,
   `!MMU`, and the single-core platform.
2. Implement entry 7-10 CSR helpers and the fail-closed U-return hook.
3. Initially expose the complete user pool as the current bounds.
4. Add the RISC-V NOMMU `access_ok()` override using those bounds.
5. Verify the existing CLIC interrupt-return trampoline remains correct.
6. Run the malicious-pointer and memory-access tests below.

Exit gate: a user program cannot read, write, or execute kernel/loader/MMIO
addresses, malformed syscall pointers return `EFAULT`, and the shell remains
usable after each child fault. This is the first valid **kernel containment**
claim, with the explicit limitation that all user processes share the pool.

### WP4 - Per-process arenas

1. Add arena ownership and bounds to the NOMMU `mm_context_t`.
2. Allocate one contiguous arena per new userspace `mm` from the pool.
3. Suballocate every mapping for that `mm` inside its arena; never widen an
   arena across a gap owned by another process.
4. Establish bounds before bFLT user copies, update them only during trusted
   exec setup, zero newly assigned arenas, and clear bounds before an arena can
   be reused.
5. Make the U-return hook load the current `mm` bounds.
6. Test the shell and child simultaneously: each must fault when touching a
   known address in the other's arena.
7. Recheck `vfork()`/`CLONE_VM`, exec failure, signal delivery, process exit,
   and PID/arena reuse for stale permissions or double free.

Exit gate: only the currently returning process's arena is enabled, and a
sibling-arena access produces `SIGSEGV` without affecting either the kernel or
the sibling. This is the **per-process containment** claim.

### WP5 - Execution policy and resource controls

After memory containment passes:

1. Add a dedicated non-root job UID/GID and launch user-compiled programs
   through a narrow supervisor rather than the root console.
2. Remove access to `/dev/mem`, `/dev/kmem`, raw MMIO devices, module loading,
   reboot controls, and unrestricted device IOCTLs.
3. Drop capabilities including `CAP_SYS_RAWIO`, `CAP_SYS_ADMIN`, and
   `CAP_SYS_MODULE`.
4. Apply measured limits for process count, file descriptors, stack, data,
   arena size, output volume, and wall/CPU time.
5. Evaluate a small seccomp allowlist only after checking Linux/uClibc support;
   do not make it a prerequisite if it destabilizes the minimal NOMMU build.
6. Kill a non-yielding job from the supervisor while verifying timer ticks and
   the shell remain live.

Exit gate: an admitted job has only the syscalls, devices, memory, runtime, and
output budget required by the documented application profile.

### WP6 - DMA and later W^X work

1. Keep the M6 microSD bounce-buffer rule: DMA descriptors and DMA buffers are
   kernel-owned internal SRAM, never arbitrary user addresses.
2. Apply the same rule to networking, display, USB host, and future devices.
   PMP constrains CPU accesses; it is not automatically an IOMMU for DMA.
3. Investigate ESP32-P4 peripheral access-management hardware as a separate
   defense-in-depth layer after the CPU PMP gate.
4. Move the signal trampoline to a fixed RX location or another audited return
   mechanism before splitting each arena into RX code and RW data/stack.

Exit gate: no enabled DMA engine can target unchecked user or kernel addresses;
W^X is either implemented and tested or remains an explicit documented
limitation.

## Expected repository changes

Names may be adjusted to match the final patch series, but keep the work
separable and additive:

- `loader/patches/esp-idf-v6.0.1/` - tracked early-PMP patch and version note.
- `loader/main/micronux_loader.c` - final PMP audit and boot marker.
- `buildroot-external/board/micronux/dts-m7/` - M7 memory ownership.
- `buildroot-external/board/micronux/linux-m7.config` - isolation options.
- `buildroot-external/configs/micronux_esp32p4_isolation_defconfig` - dedicated
  reproducible target.
- `buildroot-external/board/micronux/patches/linux/` - Linux allocator, PMP,
  uaccess, bFLT, and trap changes. Inspect existing numbering first; do not
  overwrite or renumber unrelated M6 patches in a dirty worktree.
- `buildroot-external/package/micronux-isolation-test/` - test-only bFLT
  programs and target orchestrator.
- `scripts/m7-build.sh`, `scripts/m7.ps1`, and `scripts/m7-test.py` - build,
  flash, reset, and hardware gate.
- `docs/m7-user-kernel-isolation.md` - completed measurements, guarantees, and
  limitations. This plan may be converted into that result document after the
  gates pass.

Prefer small Linux patches grouped by responsibility rather than one patch
mixing allocator, PMP, uaccess, policy, and tests. Keep Linux-derived changes
under GPL-2.0-only-compatible notices.

## Required negative tests

Build each destructive operation as a separate child so the parent supervisor
can check the exact wait status and continue:

| Test | Expected result |
| --- | --- |
| Read `0x48400000` or another kernel address | child receives `SIGSEGV` |
| Write a kernel address | child receives `SIGSEGV`; kernel hash/state stable |
| Jump to a kernel address | child receives `SIGSEGV` |
| Read/write loader reserve at `0x48000000` | child receives `SIGSEGV` |
| Read/write CPU subsystem/CLINT address | child receives `SIGSEGV` |
| Read/write general peripheral MMIO | child receives `SIGSEGV` |
| `read()` into a kernel address | syscall returns `-1`, `errno == EFAULT` |
| `write()` from a kernel address | syscall returns `-1`, `errno == EFAULT` |
| Buffer crossing `user_hi` | syscall returns `EFAULT` without partial kernel access |
| Oversized or overflowing bFLT header fields | exec returns `ENOEXEC`/`ENOMEM` |
| Non-RAM/split bFLT | exec rejected |
| Mapping beyond arena budget | `mmap()`/allocation fails cleanly |
| Access another process's arena | `SIGSEGV` after WP4 |
| Infinite loop | preempted and killed by supervisor; kernel tick remains live |
| Repeated fault/exec/exit cycle | no pool leak, stale permission, oops, or reset |

Also retain all M5 and enabled M6 tests. A protection test passes only when the
expected child status is observed and a subsequent shell probe succeeds.

Use stable markers such as:

```text
MICRONUX:M7:PMP baseline=pass overlay=7-10 pool=49700000-49f00000
MICRONUX:M7:UACCESS pass
MICRONUX:M7:FAULT read-kernel=SIGSEGV
MICRONUX:M7:FAULT write-kernel=SIGSEGV
MICRONUX:M7:FAULT exec-kernel=SIGSEGV
MICRONUX:M7:FAULT mmio=SIGSEGV
MICRONUX:M7:SYSCALL bad-pointer=EFAULT
MICRONUX:M7:ARENA sibling=SIGSEGV
MICRONUX:M7:PASS
```

The test parser must reject at least `Kernel panic`, `Oops`, `BUG`, unhandled
trap, watchdog reset, PMP audit failure, M3-M6 failure markers, unexpected
signals in the parent, payload hash drift, and memory/pool loss over the frozen
budget.

Run the complete M7 gate for three independent ROM-reset boots. QEMU can cover
generic allocator/uaccess behavior but cannot replace the revision-1.3 P4 PMP
priority, CLIC return, and MMIO-denial hardware tests.

## Completion criteria

M7 user/kernel isolation is complete only when:

- the ESP-IDF patch is versioned, reproducible, and not dependent on an
  untracked global SDK edit;
- hardware CSR readback proves the early U-mode deny map and dynamic overlay;
- every U-visible mapping originates from the dedicated user allocator;
- newly assigned per-process arenas are zeroed before U-mode can access them;
- `access_ok()` is fail-closed and the syscall bad-pointer tests return
  `EFAULT`;
- user memory/execute faults reliably kill only the child;
- the M5 and selected M6 regressions pass for three ROM-reset boots;
- memory and user-pool accounting return within the documented tolerance;
- the exact guarantee level is documented as either shared-pool kernel
  containment or per-process arena containment;
- unsupported direct mappings, DMA paths, privilege, SMP, and W^X limitations
  remain explicit.

## Primary references

- [MicroNUX M3 platform contract](m3-platform.md)
- [MicroNUX M5 NOMMU hardening contract](m5-hardening.md)
- [MicroNUX roadmap](roadmap.md)
- [RISC-V privileged architecture: machine-level ISA and PMP](https://docs.riscv.org/reference/isa/priv/machine.html)
- [ESP32-P4 revision 1.3 Technical Reference Manual](https://documentation.espressif.com/esp32-p4-chip-revision-v1.3_technical_reference_manual_en.pdf)
- [ESP-IDF v6.0 ESP32-P4 documentation](https://docs.espressif.com/projects/esp-idf/en/v6.0/esp32p4/)
- Installed ESP-IDF v6.0.1 source:
  `C:\esp\v6.0.1\esp-idf\components\esp_hw_support\port\esp32p4\cpu_region_protect.c`
- [Linux v6.12 RISC-V exception return](https://github.com/torvalds/linux/blob/v6.12/arch/riscv/kernel/entry.S)
- [Linux v6.12 generic access checking](https://github.com/torvalds/linux/blob/v6.12/include/asm-generic/access_ok.h)
- [Linux v6.12 bFLT loader](https://github.com/torvalds/linux/blob/v6.12/fs/binfmt_flat.c)
- [Linux v6.12 NOMMU memory manager](https://github.com/torvalds/linux/blob/v6.12/mm/nommu.c)
