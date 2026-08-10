# Linux patch organization

MicroNUX carries three explicitly ordered Linux 6.12.27 patch series. This
separates generally useful ESP32-P4 enablement from board/peripheral work and
from the security model that is intentionally specific to MicroNUX.

| Series | Count | Scope | Submission status |
| --- | ---: | --- | --- |
| `patches-platform/linux` | 6 | RV32 NOMMU boot, CLIC/CLINT, restart, instruction-cache synchronization, USB console | Candidate material; split the initial multi-subsystem bring-up patch further before posting upstream |
| `patches-peripherals/linux` | 16 | DW MMC/IDMAC, dual SD/C6 slots, ESP-Hosted integration, and the Linux-owned Kit C DSI handoff framebuffer | Experimental driver series; preserve order until fixups are squashed and the remaining P4 CLIC-dependent polling paths can be evaluated for interrupt-driven operation |
| `patches-isolation/linux` | 10 | reserved user pool, per-process arenas, uaccess, PMP, resource limits, bFLT W^X | MicroNUX policy; not proposed as generic Linux behavior |

The Buildroot configurations apply the platform and peripheral series in that
order. Only `micronux_esp32p4_isolation_defconfig` applies the isolation series.
This prevents M7 policy from silently becoming part of older board profiles
and makes the upstream boundary inspectable in the build configuration.

Each directory has independent `0001` numbering and `[PATCH n/N]` subjects.
Every patch retains provenance and a `Signed-off-by` line. Validate naming,
metadata, counts, ordering, configuration boundaries, and a complete manifest
hash with:

```sh
python3 scripts/check-linux-patch-series.py
```

The patch series are **review-separated**, not declared upstream-ready. Before
submission, maintainers must still:

1. rebase the candidate series onto the target maintainer tree;
2. split the initial platform patch by subsystem;
3. squash incremental descriptor/debug fixups into coherent changes;
4. replace MicroNUX-only markers and assumptions with documented bindings;
5. run the appropriate Linux build, sparse, device-tree, and maintainer tests;
6. submit each subsystem series to the correct maintainers.

This distinction is deliberate: a clean directory name is not evidence that a
patch meets an upstream subsystem's acceptance bar.
