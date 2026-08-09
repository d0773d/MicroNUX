# M7 User/Kernel Isolation Results

Status: **WP0 and the early-deny portion of WP1 proven on hardware; no kernel
containment claim yet**

M7 is converting the ESP32-P4 revision-1.3, machine-mode, single-core NOMMU
baseline into a system where a faulty bFLT child can be terminated without
being able to corrupt Linux. The work is deliberately gated in layers. The
results below prove the loader-side PMP prerequisite, not final isolation.

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
| Kernel payload SHA-256 | `58addbe9410ba137860f6ee4877b0e7431b882a052b595feb7751b0ebbaa86e4` |
| DTB payload SHA-256 | `4b694fd169a84bd5ac157415bc2c14e918b54a9ee3dbfb96760afe7bb6f34f16` |

The existing combined networking/storage image reported approximately
15,484 KiB free before the 4 MiB bFLT allocation. Reserving 8 MiB therefore
leaves an estimated 7 MiB of general kernel headroom while moving user
allocations out of that allocator. The M7 build must measure this estimate on
hardware before the boundary becomes final.

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
| 7-10 | `00000000/00` | free for the dynamic Linux overlay |
| 11 | `5010bffc/18` | LP memory denied |
| 12 | `00000000/00` | spare |
| 13, 14 | added as `48400000/80`, `49f00000/8f` | broad Linux handoff window |
| 15 | `5007fffc/18` | peripheral MMIO denied |

The accepted loader is 273,104 bytes with SHA-256
`ad8e8227f404afa6c208a2f42c0b2718287f6e782d729875e3f48238fd7b0e53`.
It emits:

```text
MICRONUX:M7:PMP baseline=pass early-deny=pass overlay=7-10-free linux=[48400000,49f00000)
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

## Exact guarantee and remaining work

The early deny map removes unintended U-mode access to CPU control, internal
RAM, ROM/flash, LP memory, and peripheral MMIO. It deliberately leaves the
locked entry-14 Linux interval in place so the M-mode NOMMU kernel can boot.
That entry currently grants U-mode RWX over all Linux RAM, including the
kernel. Therefore MicroNUX does **not** yet claim kernel containment or safety
for arbitrary uploaded programs.

The next required gates are:

1. Reserve the measured 8 MiB pool and route every U-visible mapping through
   its deterministic allocator.
2. Program unlocked PMP entries 7-10 before every M-to-U return so only valid
   user bounds override the broad Linux window.
3. Make RISC-V NOMMU `access_ok()` reject every complete or crossing range
   outside those bounds.
4. Prove read, write, execute, MMIO, malformed-syscall-pointer, teardown, and
   repeated-fault behavior on the physical P4.
5. Narrow the shared pool to one zeroed arena per `mm_struct`, then add job
   privilege/resource policy, DMA audits, and the documented W^X limitation.

Only those destructive hardware gates can advance the documented guarantee
from an early-loader prerequisite to shared-pool kernel containment and then
per-process containment.
