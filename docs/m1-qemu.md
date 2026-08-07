# M1 - RV32 NOMMU Linux under QEMU

Status: **complete**

M1 proves the architecture-independent MicroNUX execution contract before any
ESP32-P4 platform code is introduced. QEMU's `virt` machine is not an ESP32-P4
model; it validates the 32-bit NOMMU kernel, bFLT userspace, constrained ISA,
initramfs, and process-execution path.

## Pinned stack

| Component | Version |
|---|---:|
| Buildroot | 2025.02.16 LTS |
| Linux | 6.12.27 |
| GCC | 13.4.0 |
| binutils | 2.43.1 |
| uClibc-ng | 1.0.57 |
| BusyBox | 1.37.0 |
| QEMU | 9.2.0 |

The Buildroot archive is verified with SHA-256 before extraction. Buildroot
then checks the hashes of all downloaded package sources.

## Target contract

- RV32 `I`, `M`, `A`, and `C` extensions
- `ilp32` soft-float ABI
- no `F` or `D` extension exposed to Linux
- no MMU
- Linux bFLT binary loader
- uClibc-ng and a reduced BusyBox bFLT userspace with a memory span no larger
  than 256 KiB
- one CPU and 32 MiB RAM
- cpio initramfs with Buildroot's pinned static `/dev` table and no emulated
  storage dependency
- Linux launches BusyBox Hush directly as PID 1 (`rdinit=/bin/sh`)
- unauthenticated root console for architecture bring-up only; conventional
  init and authentication are deliberately deferred

This is a hardware-compatible subset of the public ESP-IDF v6.0.1 ESP32-P4
compiler contract. The ABI deliberately uses `ilp32` instead of IDF's `ilp32f`:
Buildroot's RV32 NOMMU uClibc-ng port supports `ilp32` and `ilp32d`, while the P4
cannot execute the `D` extension required by `ilp32d`. Linux 6.12 also rejects
an `F`-but-not-`D` CPU and its FPU context path executes `D` instructions. The M1
profile therefore hides both floating-point extensions and compiles all Linux
code for `rv32imac`/`ilp32`. The loader-to-kernel handoff uses integer registers
only. F-only kernel support can be evaluated after the first hardware shell.

## Build and test

From PowerShell at the repository root:

```powershell
.\scripts\m1.ps1
```

The first build compiles the cross-toolchain and host QEMU and can take tens of
minutes. Outputs and downloads live on WSL's native filesystem under
`~/.cache/micronux/m1`; they are not written into the Git repository. Output
directories include a hash of the defconfig and MicroNUX BusyBox fragment, so
an ISA, ABI, or userspace change receives a clean output tree instead of
silently reusing an incompatible cross-toolchain.

For a Linux host with the standard Buildroot prerequisites:

```sh
./scripts/m1-build.sh
./scripts/m1-test.py
```

The test rejects a mismatched Buildroot/kernel configuration, checks that
BusyBox is a bFLT executable with a bounded in-memory span, boots QEMU with its
MMU and FPU extensions disabled, opens the bring-up root console, executes three
distinct child shell processes, validates `riscv32`, rejects allocation/fatal
warnings, and shuts the guest down. Success ends with:

```text
MICRONUX_M1_TEST_PASS
```

## Measured result

Clean profile ID: `a91c21807653`.

| Artifact | Size | SHA-256 |
|---|---:|---|
| Linux `Image` | 4,748,064 bytes | `f42ac72f6f24027dfa344fc96ce840a0aabce24da8a291882a8b27f74b222587` |
| `rootfs.cpio` | 497,664 bytes | `eb6eadcee8224dce8e27789a34eeb39a345655247be1272389f17ae7119f2e99` |
| BusyBox bFLT file | 196,392 bytes | included in `rootfs.cpio` |

The BusyBox bFLT header reports a 229,360-byte maximum memory span (BSS end
plus stack), placing each load in the 256 KiB allocator class. The clean QEMU
boot reported 26,804 KiB available from 32 MiB before starting PID 1.

## Exit criteria

- [x] Pin the complete Buildroot stack and source archive hash.
- [x] Build and boot the upstream RV32 NOMMU reference configuration.
- [x] Build the P4-compatible `rv32imac`/`ilp32` configuration.
- [x] Boot it with 32 MiB RAM and a cpio initramfs.
- [x] Pass the automated bFLT shell and child-exec test.
- [x] Record artifact hashes and measured image sizes.
