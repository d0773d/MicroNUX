# M6 Storage and Peripheral Bring-up

Status: **simultaneous microSD and ESP32-C6 networking stable; physical Kit C
JD9365 loader scanout proven; Linux ownership completed in M7**

M6 starts with the Waveshare board's onboard microSD interface. Storage can be
isolated from the minimal USB console and from the ESP32-C6 wireless transport,
so it is a useful first test of peripheral ownership under NOMMU Linux. The
default M6 storage path is read-only at the userspace gate. A separate opt-in
write gate creates, verifies, and deletes one fixed test file without
formatting the card. Together they prove power, pin mux, controller ownership,
block discovery, repeatable reads, filesystem remounts, and bounded writes.

## Hardware contract

| Function | ESP32-P4 resource |
| --- | --- |
| Card power | LDO channel 4 at 3.3 V |
| SDMMC clock | GPIO43 |
| SDMMC command | GPIO44 |
| SDMMC data 0-3 | GPIO39, GPIO40, GPIO41, GPIO42 |
| Controller | DesignWare MSHC at `0x50083000` |
| Physical interrupt route | source 23, core-0 matrix `0x500d605c`, parked on disabled CLIC input 31 |
| Linux IRQ placeholder | CLIC input 17, requested and disabled by the MMC driver |

The ESP32-C6 SDIO link uses slot 1 and a different pin group. Isolated storage
and networking profiles remain available for diagnosis. The accepted combined
profile prepares both slots, then one Linux DesignWare MSHC driver owns the
controller and serializes microSD and C6 requests. The C6 remains on its
factory firmware.

The ESP-IDF loader enables LDO4, establishes the SDMMC clock/reset state,
installs the dedicated I/O mux, and quiesces the controller before Linux takes
ownership. Because the accepted Linux path polls synchronously, it parks
physical source 23 on otherwise-unused CLIC input 31 and leaves that input
disabled. The device tree retains IRQ 17 only as a placeholder that the
generic MMC driver can request and disable; the SD line cannot enter Linux.

## The working compromise

The initial PIO implementation could issue commands and transfer individual
sectors, but larger block-layer reads depended on FIFO watermark events that
did not complete reliably. Using ordinary IDMAC buffers was also unsafe:
normal Linux buffers live in cached external PSRAM, and this NOMMU port does
not yet implement a general non-coherent DMA API for the ESP32-P4 cache.

M6 contains that problem inside the ESP32-P4 DesignWare MMC platform quirk:

1. The loader removes a fixed 8 KiB internal-SRAM window from its heap.
2. Linux uses one 4 KiB page for IDMAC descriptors and one as a data bounce
   buffer.
3. ESP32-P4 descriptors occupy 64 bytes. Linux writes the standard first four
   words, including the chained terminal descriptor with a null next pointer.
4. The controller receives the cacheable/bus alias, while the CPU copies
   through the uncached alias.
5. Requests are limited to one scatterlist entry and 4 KiB.
6. The initialized IDMAC engine remains active between ordinary requests;
   controller resets remain in the existing error-recovery paths.
7. The physical SD source is parked on disabled CLIC input 31. The driver's
   placeholder IRQ 17 is also disabled, and the driver invokes the generic
   DesignWare interrupt handler while polling command and data status every
   100 microseconds, with a 100 ms diagnostic threshold.

| Purpose | Controller/bus address | Linux CPU address | Size |
| --- | ---: | ---: | ---: |
| IDMAC descriptors | `0x4ff80000` | `0x8ff80000` | 4 KiB |
| Data bounce buffer | `0x4ff81000` | `0x8ff81000` | 4 KiB |
| Reserved interval | `0x4ff80000` | - | 8 KiB |

The loader and the stalled second-core stack both remain outside this
interval. Linux's regular block buffers stay in PSRAM and never become DMA
targets. The driver has bounce-copy handling for both transfer directions.
Writes are accepted only through the explicit, fixed-path gate below.

This mode deliberately spends CPU time and caps each request at 4 KiB. It is a
correctness-first storage path, not a throughput design. The unreliable CLIC
delivery is an observation from the connected ESP32-P4 revision 1.3 board and
current MicroNUX interrupt port; it is not presented as an official silicon
erratum. A future interrupt-driven or direct-PSRAM DMA path needs a separate
hardware gate and a real architecture-wide cache-maintenance contract.

## Revision 1.3 CLIC and console compromise

The physical gate also exposed a return-path interaction on the connected
ESP32-P4 revision 1.3. An interrupt-form `mret` restores the CLIC level, but a
direct interrupt-form return to userspace re-enters in machine mode. A task
switch during interrupt exit made the original frame-based test insufficient:
the newly selected task could carry an exception-form frame even though the
hart still had an active CLIC level. MicroNUX therefore performs a two-stage
return for every U-mode return. The first synthetic interrupt-form `mret`
restores MPIL 0 into a machine-mode trampoline with interrupts disabled, and
the trampoline performs the normal exception-form return to userspace.
Machine-mode returns continue restoring their saved context directly.

The USB Serial/JTAG peripheral source also does not re-arm reliably through
the current Linux CLIC IRQ path. Its driver checks the peripheral interrupt
status once per kernel tick, then uses the normal RX/TX status handler. It
does not poll the FIFO count directly. These are board-and-port observations,
not claims of an official silicon erratum.

The first device-tree profile uses a 4-bit bus, an 80 MHz controller input
clock, a 20 MHz card limit, polling card detection, and disables MMC/eMMC and
SDIO card types. Only removable SD memory is in the M6 storage contract.

## Storage acceptance gates

`scripts/m6.ps1` builds and flashes the loader and M6 image, then performs
three independent ROM-reset boots. On every boot the target probe:

1. requires `/dev/mmcblk0` after boot-time card discovery;
2. reads the first 1 MiB twice through the block layer;
3. requires both SHA-256 hashes to match;
4. mounts partition 1 as read-only VFAT when it is present and then unmounts
   it; and
5. runs the complete M5 NOMMU stress regression.

The host requires stable card size, card sample hash, kernel hash, and DTB
hash across all boots. The default probe never formats the card, mounts it
writable, or issues a block write.

The inserted 29.7 GiB `SK32G` card passed the clean-build, three-ROM-reset
hardware gate with these native Linux markers on every boot:

```text
mmc0: new SDHC card at address aaaa
mmcblk0: mmc0:aaaa SK32G 29.7 GiB
 mmcblk0: p1
MICRONUX:M6:STORAGE begin mode=idmac-sram-poll access=read-only
MICRONUX:M6:STORAGE raw-pass sectors=62333952 bytes=1048576 sha256=6158c8c683a1c1a66950c4e6593af64b0356cc52702e76ca00af1bdff5978c49
MICRONUX:M6:STORAGE mount=vfat-ro
MICRONUX:M6:STORAGE:PASS mode=idmac-sram-poll access=read-only
```

This is execution on the physical ESP32-P4, not QEMU emulation. The milestone
storage slice passed with:

- three identical 62,333,952-sector card reports;
- three identical first-1-MiB sample hashes of
  `6158c8c683a1c1a66950c4e6593af64b0356cc52702e76ca00af1bdff5978c49`;
- read-only VFAT mounts on all three boots;
- M5 elapsed times of 689, 687, and 687 ms; and
- free-memory readings of 19,796 KiB before each M5 run and 19,788 KiB
  afterward;
- a 3,647,560-byte kernel with SHA-256
  `4a5a485a2b345e73799058ac5f2ddb8818ad5cce29c76a8f35a9370b572aca29`
  and DTB SHA-256
  `12fd3a00b4774fbe8b1620c489b21b862396e7ca11ce179e1aa6a4d11ea8d926`.

The separate one-boot write gate is opt-in:

```powershell
.\scripts\m6.ps1 -Port COM14 -Boots 1 -WriteTest
```

It records the first 1 MiB card hash, mounts partition 1 read-write, creates
only `/mnt/sd/MICNUXW.TST`, calls `sync`, remounts read-only, verifies the
41-byte payload and SHA-256, remounts read-write to delete that file, then
finishes read-only. It refuses multi-boot use. The inserted `SK32G` card passed
with the same pre/post first-1-MiB hash:
`6158c8c683a1c1a66950c4e6593af64b0356cc52702e76ca00af1bdff5978c49`.
The test file was absent afterward. No gate formats, repartitions, or writes
outside that one pathname.

The controller-only diagnostic remains available when no card is inserted:

```powershell
.\scripts\m6.ps1 -Port COM14 -Boots 3 -ControllerOnly
```

Omitting `-ControllerOnly` restores the strict default and requires a stable
card size and sample hash on every boot.

## ESP32-C6 networking profile

The networking image uses Espressif's legacy ESP-Hosted-FG Linux driver,
pinned at commit `1df17f74d62eede4127785ae9414e03e48e62ebd` and adapted to the
factory ESP-Hosted-MCU wire protocol. Bluetooth is excluded. The loader resets
the C6 and prepares SDIO slot 1 at 20 MHz; Linux discovers these two functions:

| Function | Vendor/device | Class | Purpose |
| --- | --- | --- | --- |
| `mmc0:0001:1` | `0x0092:0x6666` | `0x00` | ESP-Hosted data/RPC function |
| `mmc0:0001:2` | `0x0092:0x7777` | `0x02` | non-data companion function |

`micronux-netctl` is the deliberately small userspace control plane:

```sh
micronux-netctl mac
micronux-netctl up
micronux-netctl status
micronux-netctl wait 20
micronux-netctl forget
micronux-netctl connect "SSID" "PASSWORD"
micronux-online
```

`up` initializes station mode through `/dev/esps0`, reads the C6 MAC, assigns
it to `ethsta0`, starts Wi-Fi, requests association using the configuration
already stored in C6 NVS, and raises the Linux interface. It never receives a
password from the loader, and it does not wait for association or DHCP.
`status` queries the C6's station association directly through
`esp_wifi_sta_get_ap_info()` rather than treating Linux `LOWER_UP` as evidence
of connectivity. `wait` polls that state and requires two continuous seconds
of association before succeeding. The pserial reader consumes each TLV at its
declared length, decodes `StaDisconnected` events, and ignores unrelated event
or response frames while waiting for the matching RPC message ID and UID. One
absolute 10-second deadline bounds each exchange even if events continue
arriving. `forget`
invokes the factory firmware's
`esp_wifi_restore()` RPC and requires a P4 reboot; this deliberately erases the
C6's saved Wi-Fi configuration so the provisioning loader will advertise on
the next boot. `connect` remains a direct diagnostic that sends station
credentials and requests association. Passwords are not stored in the
initramfs, but an interactive command is echoed on the serial console; use a
test network rather than a valuable credential.

The start/connect separation and restore behavior follow the
[ESP-IDF 6.0 ESP32-C6 Wi-Fi API](https://docs.espressif.com/projects/esp-idf/en/v6.0/esp32c6/api-reference/network/esp_wifi.html).
After `up`, Linux obtains and applies a lease with:

```sh
udhcpc -f -q -n -i ethsta0 -s /usr/share/udhcpc/default.script -t 5 -T 2
```

The network profile enables Linux packet sockets for the DHCP raw-packet path
and includes the five BusyBox applets used by Buildroot's lease hook: `touch`,
`mktemp`, `rm`, `ifconfig`, and `route`. `micronux-online` combines these
pieces into a foreground recovery command: it retries the full saved-credential
association and DHCP sequence up to ten times with a five-second delay between
attempts. It is
never launched automatically, so a missing access point cannot hold up the
MicroNUX shell.

Build, flash, and run the three-boot hardware gate with:

```powershell
.\scripts\m6-network.ps1 -Port COM14 -Boots 3
```

The stricter online gate uses the same flash artifact and reset harness, then
also requires a lease, default route, external IPv4, and DNS on every boot:

```powershell
C:\Espressif\python_env\idf6.0_py3.11_env\Scripts\python.exe scripts\m6-network-test.py --port COM14 --boots 3 --timeout 420 --artifact-dir out\m6-network --online
```

On the connected board, all three boots reported factory firmware `2.11.5`,
the two identities above, stable MAC `b0:a6:04:8a:d3:78`, and final
`UP,LOWER_UP`. After the stream-parser correction, all three independent
ROM-reset boots also returned `MICRONUX:M6:NET:UP:RC=0`; no asynchronous event
was misidentified as the RPC response. The current P4 kernel image, including
saved-credential connect, status/wait, and bounded recovery support, is
5,325,288 bytes with SHA-256
`2d6a10eb4aa7b976a77e82985e1079727b983cb47efd2c661d670a14c3a87611`;
the DTB SHA-256 was
`08f814249e0c776d6bc26f92d3185d216220be2af1936599d78f97b15d2ced20`,
the metadata SHA-256 was
`d28bea963e9111db0214e03d1e185147ca12884f6eeb5fb15a50abdd697f3d98`,
and the embedded rootfs SHA-256 was
`d91d00fa9e5f44896b5d4ab73a741be2fb4a9869cf1e8fa62d4fdac961d652b3`.
On the first saved-credential test, the C6 contained a pre-existing
`AP-5GHz` configuration. The loader correctly skipped onboarding and Linux
successfully issued the connect RPC, but DHCP received no lease. That stored
network is therefore not accepted as an association/DHCP result. The C6
configuration was then cleared through `micronux-netctl forget`. The
Espressif Android app completed a BLE Security 2 session, the loader reported
`credentials=received`, `credentials=accepted storage=c6-nvs`, and restarted
the P4. The next boot reported `state=provisioned action=linux-handoff`.
Linux requested association without receiving the password, obtained
`192.168.0.47/24` from DHCP with default route `192.168.0.1`, reached
`1.1.1.1`, installed the DHCP resolvers, and resolved and reached
`example.com`.

The final `--online` gate passed three more independent ROM-reset boots. Boot
2 reached the network on its first attempt. Boots 1 and 3 exercised the retry
path and recovered on attempt 3 after C6 disconnect events including reason
201 (`NO_AP_FOUND`) and reason 205 (`CONNECTION_FAIL`). Every boot ended with
an IPv4 lease, default route, successful `1.1.1.1` ping, and successful DNS and
`example.com` ping.

After extending the policy to ten attempts with five seconds between retries,
a fresh three-reset `--online` gate passed on the updated image. Boot 1
connected on attempt 1. Boots 2 and 3 reproduced the router reconnect holdoff:
the first DHCP association dropped, attempt 2 reported reason 205
(`CONNECTION_FAIL`), and attempt 3 recovered. All three again passed DHCP,
default routing, external IPv4, and DNS. Only the P4 Linux, DTB, and metadata
partitions were written; the provisioning loader, C6 firmware, and C6 NVS were
not rewritten.

## Simultaneous microSD and ESP32-C6 acceptance

The combined profile is the accepted M6 storage/network baseline. The loader
prepares microSD slot 0 and C6 SDIO slot 1, but Linux takes sole persistent
ownership of the shared controller. The ESP32-P4 DesignWare driver keeps one
controller-wide request lock, switches slot-specific clock and bus state only
when ownership changes, completes polled transfers in process context, and
reloads the internal-SRAM IDMAC descriptor before each transfer. Neither the
loader nor a second runtime services storage or networking after handoff.

The combined target boots to the shell without waiting for Wi-Fi. Its explicit
test sequence performs a bounded microSD write/remount/verify/delete operation,
brings the saved C6 network online, runs storage and network traffic
concurrently, disconnects and reconnects Wi-Fi, and hashes the same raw card
sample before and after every phase. The reconnect command allows ten attempts
with five seconds between attempts.

Build the combined image with:

```powershell
wsl -d Ubuntu -- env MICRONUX_JOBS=16 bash /mnt/c/Users/d0773/Documents/ChatGPT/MicroNUX/scripts/m6-combined-build.sh
```

The final clean candidate, with temporary diagnostic watchdogs removed, has:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `Image` | 5,433,232 | `a0a59c1ba4162b2da1ade21e28d5c65f4fd22be6109aed674ae00c73b265160b` |
| `esp32p4-micronux.dtb` | 2,047 | `4b694fd169a84bd5ac157415bc2c14e918b54a9ee3dbfb96760afe7bb6f34f16` |
| `metadata.bin` | 128 | `b7c1e76498e39eb89e211315fe3268023d5be68297a355a146fb381eff3633a3` |
| `rootfs.cpio` | 881,664 | `300416f418cc5495854637ee5be16ad3dd7e05a234ad2d4f15f3525f413c7aa8` |

That exact candidate passed all final physical gates on ESP32-P4 revision 1.3:

- one 20-cycle simultaneous storage/network soak;
- one 120-cycle simultaneous storage/network endurance soak; and
- three independent ROM-reset boots with storage, SDIO discovery, online,
  concurrent traffic, disconnect, and reconnect checks on every boot.

Every phase retained the first-1-MiB microSD SHA-256
`6158c8c683a1c1a66950c4e6593af64b0356cc52702e76ca00af1bdff5978c49`.
The router's reconnect holdoff was exercised rather than hidden: clean-candidate
tests recovered as late as attempt 7, within the configured ten-attempt bound.
The C6 continued reporting factory firmware 2.11.5 and MAC
`b0:a6:04:8a:d3:78`; no C6 firmware or C6 NVS partition was flashed.

The three acceptance commands are:

```powershell
py -3 scripts\m6-combined-test.py --port COM14 --boots 1 --timeout 600 --artifact-dir out\m6-combined --write-test --soak-cycles 20
py -3 scripts\m6-combined-test.py --port COM14 --boots 1 --timeout 1800 --artifact-dir out\m6-combined --write-test --soak-cycles 120
py -3 scripts\m6-combined-test.py --port COM14 --boots 3 --timeout 420 --artifact-dir out\m6-combined
```

### P4-hosted phone provisioning

The optional provisioning loader profile uses Espressif Unified Provisioning
on the ESP32-P4. It does not build, replace, or flash ESP32-C6 firmware. The P4
temporarily owns the existing SDIO link, runs the NimBLE host and provisioning
manager, and uses the factory C6 as the Wi-Fi/Bluetooth controller through
ESP-Hosted 2.11.5 and Wi-Fi Remote 1.3.1.

The boot flow is deliberately bounded:

1. The P4 asks the C6 whether station credentials already exist.
2. A provisioned device tears ESP-Hosted down and continues to the ordinary
   Linux handoff without advertising a provisioning service.
3. An unprovisioned device advertises a Security 2 QR code over BLE for 120
   idle seconds, then offers the same service over SoftAP for 120 idle seconds.
   An attached phone keeps its window alive, with a five-minute hard limit per
   transport. A BLE setup error falls back directly to SoftAP.
4. The official Espressif provisioning app sends credentials inside an SRP6a
   authenticated, AES-GCM protected session. MicroNUX provisioning markers
   redact the SSID and password, and project code never prints the password.
5. Wi-Fi Remote stores the station configuration in C6 NVS. MicroNUX stops the
   provisioning manager, disables the remote Bluetooth controller, tears down
   SDIO ownership, scrubs the in-memory proof, and restarts the P4.
6. On the next boot the loader sees the C6 as provisioned and hands SDIO to
   Linux. Linux can use `micronux-netctl up` without receiving the password.

If both idle windows expire, the loader still boots Linux and retries
provisioning on the next reset. This prevents an absent phone or network from
holding the operating system indefinitely. A provisioning subsystem error is
also logged and handed off to Linux after cleanup instead of trapping boot.
The SoftAP itself has no WPA key so the Espressif app can join it directly;
application credentials are still rejected unless the client completes
mandatory Security 2 authentication.

The onboarding proof is unique per board and is generated from the P4 hardware
random source. This development profile stores that proof in ordinary P4 NVS
so it remains stable across resets and can be rendered as a QR code on the USB
console. That is intentionally not the production secret-storage contract.
Production hardware should inject the Security 2 salt/verifier and a printed
per-device QR code during manufacturing, then enable encrypted NVS or another
protected store. MicroNUX still does not burn security eFuses during
development.

Build-only is the default:

```powershell
.\scripts\m6-provision.ps1
```

Flashing requires two explicit P4-only switches:

```powershell
.\scripts\m6-provision.ps1 -Port COM14 -Flash -ConfirmP4
```

The script verifies the C6 target, SDIO slot and pin contract, VHCI transport,
and Security 2 configuration before compiling. Its flash path invokes only the
ESP32-P4 project image; it contains no C6 binary or C6 flashing command. The
profile compiles under ESP-IDF v6.0.1 and produces a `0xf7610`-byte loader,
leaving `0xf89f0` bytes free in the existing `0x1f0000` factory partition.
The profile was flashed to the connected ESP32-P4 revision 1.3 and booted with
the factory C6 firmware `2.11.5`. Both the bounded BLE-to-SoftAP idle-timeout
path and fresh BLE phone onboarding were exercised physically. Successful
onboarding stored the Wi-Fi configuration in C6 NVS, restarted the P4, and
produced the provisioned Linux handoff described above without modifying C6
firmware.

API and protocol choices follow the official
[ESP-IDF provisioning guide](https://docs.espressif.com/projects/esp-idf/en/v6.0/esp32p4/api-reference/provisioning/index.html),
[Network Provisioning component](https://components.espressif.com/components/espressif/network_provisioning/versions/1.2.4/readme),
and [ESP-IDF Provisioning Android app](https://github.com/espressif/esp-idf-provisioning-android).

## MIPI-DSI electrical-proof profile

Before selecting a controller, a separate attachment probe can check whether
the Waveshare adapter acknowledges at I2C address `0x45`. It performs only the
address phase: no register or DSI command is written, the panel rail and D-PHY
stay off, no scanout is created, and I2C is released before Linux handoff. An
ACK confirms attachment but cannot distinguish panel controllers.

Build-only is the default:

```powershell
.\scripts\m6-mipi-probe.ps1
```

Flashing requires explicit acknowledgement that the P4 will reset:

```powershell
.\scripts\m6-mipi-probe.ps1 -Port COM14 -Flash -ConfirmAttachmentProbe
py -3 scripts\m6-combined-test.py --port COM14 --boots 1 --timeout 420 --artifact-dir out\m6-combined --expect-mipi-adapter present
```

M6-D0 is implemented as a loader-owned, exact-controller diagnostic. It
enables ESP32-P4 D-PHY LDO channel 3 at 2.5 V, configures two DSI lanes, uses
the Waveshare backlight controller at I2C address `0x45`, and starts the
hardware vertical-color-bar generator. Backlight stays off until panel init,
pattern setup, and framebuffer ownership checks have succeeded. The pinned
Waveshare constructors normally write the adapter power and full-brightness
registers internally; the MicroNUX link guard suppresses those constructor
writes and applies the configured bounded brightness only after every gate.

| Profile | Resolution | Lane rate | Intended Waveshare panel |
| --- | ---: | ---: | --- |
| `jd9365` | 800 x 1280 | 1500 Mbps/lane | 8/10.1-inch DSI Touch A |
| `ili9881c` | 720 x 1280 | 1000 Mbps/lane | 7-inch DSI Touch A |
| `hx8394` | 720 x 1280 at 30 Hz | 700 Mbps/lane | 5-inch DSI Touch A |
| `ek79007` | 1024 x 600 | 1000 Mbps/lane | 7-inch DSI Touch C |

The profiles use the official
[Waveshare ESP32 components](https://github.com/waveshareteam/Waveshare-ESP32-components)
and [Espressif LCD component](https://github.com/espressif/esp-iot-solution)
implementations. All four selected branches and the unselected refusal branch
compile with ESP-IDF v6.0.1 and pinned component versions. Build-only is the
default. Exact-panel builds retain the already validated combined microSD/C6
profile so the diagnostic does not replace the M6 baseline:

```powershell
.\scripts\m6-mipi.ps1 -Panel jd9365
```

Kit C fixes the attached profile to Waveshare's 10.1-inch JD9365 panel.
Flashing still requires two explicit switches:

```powershell
.\scripts\m6-mipi.ps1 -Panel jd9365 -Port COM14 -Flash -ConfirmExactPanel
py -3 scripts\m6-combined-test.py --port COM14 --boots 3 --timeout 420 --artifact-dir out\m6-combined --expect-mipi-profile jd9365
```

The exact Kit C loader was flashed on revision-1.3 hardware. It read panel ID
`93 65 04`, reported the stable marker below, and the physical panel visibly
showed vertical color bars during the M6 electrical diagnostic:

```text
MICRONUX:M6:DSI state=ready profile=jd9365-800x1280 resolution=800x1280 lanes=2 lane_mbps=1500 format=rgb565 pattern=vertical-bars
```

Normal storage and networking loaders compile with MIPI disabled and must emit
`MICRONUX:M6:DSI state=disabled reason=profile-off` at the hardware gate.
Enabling DSI with the Kconfig `unselected` choice fails before any display rail,
D-PHY, or backlight is powered.

The framebuffer is RGB565 and must fit entirely in the loader-owned PSRAM
reservation `[0x48000000,0x48400000)`, which Linux excludes from ordinary
allocation. M6-D0 remains the electrical and timing proof; it is not an
emulator or an application-facing display path.

M6-D1 and the minimal M6-D2 console gate are now complete in the isolated M7
profile. The production loader leaves DPI/framebuffer mode selected, blanks the
backlight, quiesces callback-driven scanout, and publishes a versioned
descriptor-ring contract. Linux validates the contract, starts hardware reload
as `/dev/fb0`, confirms the first frame through the routed GDMA interrupt, then
restores and owns the 100x80 framebuffer console and backlight.
The runtime hardware-pattern switch is intentionally disabled on P4 revision
1.3 because returning from VPG can stall the external DPI stream. See
the [M7 Linux-owned Kit C display report](m7-linux-display.md) for the exact
ownership, DMA, security, and three-boot evidence.
