# M8 Linux Device Service and Application ABI

Status: **ABI v1 representative workflow complete**

## Boundary

`micronux-deviced` is a trusted Linux userspace policy service. It discovers
the microSD block device and C6-backed network interface only through Linux
device nodes and sysfs. It does not map peripheral registers, own DMA, or run
an alternate ESP-IDF device stack after handoff.

Ordinary applications use the local `AF_UNIX` `SOCK_SEQPACKET` endpoint at
`/run/micronux/device-v1.sock`. The endpoint is deliberately local; no
credential or device-control protocol is exposed over the network.

## ABI v1

The v1 wire contract uses one fixed 24-byte request and one fixed 28-byte
response. Both begin with the little-endian `MNUX` magic, ABI major/minor, and
a request identifier. A major-version mismatch fails with a version error.
The public C contract is installed as `<micronux/device.h>` with the static
library `libmicronux.a`.

Initial operations are:

| Operation | Required capability | Behavior |
| --- | --- | --- |
| `INFO` | none | Return ABI, peer capabilities, and device flags. |
| `LIST` | `observe` | Return microSD, network-presence, and link flags. |
| `WAIT` | `observe` | Wait up to 300 seconds for selected real device flags. |
| `ADMIN_PROBE` | `admin` | Acceptance-only policy check; it does not touch hardware. |

The daemon derives capabilities from Linux `SO_PEERCRED`; it never trusts a
capability claimed in an application request. UID 0 currently receives
`observe`, `control`, and `admin`. Other UIDs receive only `observe`.
Unauthorized operations return the stable `denied` wire status, which
`libmicronux` maps to `EACCES`.

`WAIT` is implemented as state in the daemon's `poll()` loop. It does not
sleep the service process, so other clients can query the ABI or device state
while one caller waits. A disconnected or crashed client loses only its
socket and cannot retain device ownership.

## Application surfaces

The shell client uses the same C library intended for native applications:

```sh
micronux-device api
micronux-device list
micronux-device list --json
micronux-device wait network 5000
```

`micronux-device-native` is the first SDK-linked example. `micronux-ignite`
is the Linux runner for the real IgniteVM C runtime. The included
`device-status.ignite` source follows the existing native language pipeline:

```text
device-status.ignite -> Ignite direct compiler -> Ignite bytecode -> IGPK
                     -> userspace IgniteVM C runtime -> libmicronux
                     -> /run/micronux/device-v1.sock
```

The `network.status()` host callback calls `micronux_device_get_info()` and
`micronux_device_list()`; it never opens sysfs, an SDIO device, or hardware
registers itself. Before bytecode begins, the runner drops from root to numeric
UID/GID 65534. `SO_PEERCRED` therefore grants the VM only `observe`, regardless
of any claim a package could make. The VM still applies the IGPK permission
mask to native opcodes before a host callback can run. Read-only
`network.status()` requires no package permission; future control bindings must
pass both the Ignite package permission and the Linux service capability.

The public MicroNUX tree contains the adapter and test packages, not a copy of
the private IgniteVM source. Set `MICRONUX_IGNITEVM_SOURCE_DIR` or use the
PowerShell wrapper with the source checkout explicitly:

```powershell
.\scripts\m8.ps1 -Port COM14 -IgniteVmSourceDir C:\Code\ignitevm-foundation
```

The build pins and records the IgniteVM Git commit. The validated host-support
checkpoint is `e062a32` (`Add portable IgniteVM host support`). The current
portable runner accepts CRC-validated unsigned development IGPKs and rejects
signed packages because a MicroNUX trust-anchor backend has not yet been
wired; production package signing remains a fail-closed follow-up.

## Recovery and raw-access policy

`micronux-deviced-supervise` runs without `fork()` inside the daemon and
restarts it after an unexpected exit. The daemon recreates its socket and PID
file on each start. The combined Linux profile disables `CONFIG_DEVMEM`, keeps
kernel modules disabled, and therefore provides no `/dev/mem`, `/dev/kmem`, or
module-loading route to raw peripheral ownership.

## Hardware acceptance

The hardware acceptance gate proves:

1. shell and native clients report ABI 1.0 and identical device flags;
2. an unprivileged process can observe but receives `EACCES` for admin;
3. a network wait can time out while an unrelated ABI query succeeds;
4. killing a waiting client leaves the service responsive;
5. killing the service causes a supervised restart and a new client succeeds;
6. raw memory device nodes remain absent; and
7. the existing microSD/C6 simultaneous regression still passes.

All seven checks passed on ESP32-P4 revision 1.3. The same run additionally
proved the Ignite surface:

- shell and native C reported ABI 1.0 and device flags `0x00000003`;
- direct-compiled `network.status()` ran in the real VM as UID 65534, received
  capability `0x00000001` (`observe`), and stored the callback result in VM
  global 0;
- an intentional integer divide-by-zero produced the bounded VM fault
  `bad operand`, exited with status 1, and a new service query succeeded;
- killing a waiting client did not affect the daemon;
- killing the daemon produced supervisor status 137 and a new client
  succeeded after restart; and
- `/dev/mem` and `/dev/kmem` remained absent.

The accepted payload and application artifacts are:

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| `Image` | 5,761,632 | `58addbe9410ba137860f6ee4877b0e7431b882a052b595feb7751b0ebbaa86e4` |
| `rootfs.cpio` | 1,459,712 | `c10dd29932dd2837fc28ca1e98a0f36b2147f9898b61c593fc12ac67fd6a4f82` |
| `micronux-ignite` | 231,288 | `4735ffcbc94dc03201d10a00cfeb7770d66b7c48874e7e5bd884ac098365935b` |
| `device-status.igpk` | 343 | `adb9ae403c8624b5c26a5e00131a80684f5833eab2577053261503efc9bb1a2b` |
| `device-fault.igpk` | 356 | `3559d58d555124e93c2ae708dfa3e7225bdf0afc5bdef8fb22cd18af92e2efdf` |

After the M8 gate, the full microSD/C6 test retained SD sample SHA-256
`6158c8c683a1c1a66950c4e6593af64b0356cc52702e76ca00af1bdff5978c49`
and recovered the router reconnect holdoff on attempt 5. The safe MIPI
attachment-probe loader remained installed and reported adapter address
`0x45`, D-PHY off, and zero target writes.
