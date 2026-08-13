# Linux patch organization

MicroNUX carries three explicitly ordered Linux 6.12.27 patch series. This
separates generally useful ESP32-P4 enablement from board/peripheral work and
from the security model that is intentionally specific to MicroNUX.

| Series | Count | Scope | Submission status |
| --- | ---: | --- | --- |
| `patches-platform/linux` | 8 | RV32 NOMMU boot, CLIC/CLINT, restart, instruction-cache synchronization, nonblocking USB console | Candidate material; split the initial multi-subsystem bring-up patch further before posting upstream |
| `patches-peripherals/linux` | 44 | DW MMC/IDMAC, dual SD/C6 slots, ESP-Hosted integration, and the Linux-owned Kit C DSI framebuffer and polled GT9271 input | Experimental driver series; preserve order until fixups are squashed and the remaining P4 CLIC-dependent polling paths can be evaluated for interrupt-driven operation |
| `patches-isolation/linux` | 10 | reserved user pool, per-process arenas, uaccess, PMP, resource limits, bFLT W^X | MicroNUX policy; not proposed as generic Linux behavior |

The Buildroot configurations apply the platform and peripheral series in that
order. Only `micronux_esp32p4_isolation_defconfig` applies the isolation series.
This prevents M7 policy from silently becoming part of older board profiles
and makes the upstream boundary inspectable in the build configuration.

The current ordered tree is 7 platform + 44 peripheral + 10 isolation patches,
61 total. Peripheral patches 30 through 44 are the M9.2 ABI-v3 native-display
sequence: fail-closed cold probe, I2C/LDO setup, JD9365 programming, qualified
triple-buffer scanout, post-render reveal, correct CLIC hardware-IRQ
validation, exact GDMA-readback diagnostics, and documented-field GDMA enable
validation, decision-point scanout diagnostics, and phase-correct DMA-arm
validation, followed by removal of an invalid inactive video-shadow acceptance
predicate, followed by fast one-shot rearm, visible-epoch telemetry,
runtime-fault evidence preservation, and a derated 60-MHz/1000-Mbps native
bandwidth profile. Patch 36 was flashed and
read back exactly, then its sealed boot log reached fail-dark containment and a
working headless shell after the P4 returned reserved-one values in the GDMA
interrupt-enable registers. Corrective patch 37 also passed exact flash/readback
at `build/m9-readback/20260812T120632185Z-2993c451`; its passive snapshot
`out/m9/hardware-runs/20260812T120919Z-snapshot-b5890811aa40-0611024a.log`
(SHA-256
`52ebc5ed26487e68419411f73cd263566e3fb8a47920e1b67f33f150d21d94b6`)
reached GDMA `CONFIGURED`, then exposed the deterministic released-I2C policy
bug during `SCANOUT_INITIALIZING` and failed safely to `FAILED_QUIESCENT` with a
headless shell. The log records `optical-state=unobserved`.

The current tip is Patch 44, not Patch 40. Its SHA-256 is
`bcf953f4706e57944aae4d6a5423f5d3b38a0ab4b3354818d00c457022138307` and
its applied source SHA-256 is
`927530e5c4d2e9162af12b0f57b01b11ce1d7948626a8f2ddac3e8afa2d6cbb6`.
The ordered 7/44/10 manifest is
`9571ba5f78edc65675ca066652df583a9b0842b41a3baa209820e9696123805d`.
Clean M7/M9 builds and the Windows no-flash verifier pass, but Patch 44 is
unflashed and has no runtime or optical acceptance claim.

Patch 38 admits only the physically released I2C state at that dark boundary
and adds read-only decision-point diagnostics. Its exact patch SHA-256 is
`8471fcb6b9ec9656b82dc44c29a790c9f70a1d306a16d4b8b34fe7f04f96f177`,
commit is `397b8bed56b6165251011f0109ded884d1bd0fe2`, applied source SHA-256 is
`3aa68ea94d60385476fd3b5817f493cfeaeb38d915c4ee643bdda54d5273f65e`,
and W=1 object SHA-256 is
`55736ed8b0df502b9dc1c31440a596f7b5c81797bc4020b8d373cffa14f3f666`.
It passed exact four-region flash/readback under
`build/m9-readback/20260812T125425532Z-95d60702`; `readback.json` has SHA-256
`702ba99d9ca84bf25593301c300575a9706294dcae62f8db2ef982336e337a46`.
The sealed passive snapshot
`out/m9/hardware-runs/20260812T125714Z-snapshot-b5890811aa40-3670b4ef.log`
has SHA-256
`1c43ac04f307de252342fdc4cdc49d3aa6f18520be5cca254d4e7df294b677ac`.
It passed the complete native setup through GDMA `CONFIGURED`, then failed
closed at `reason=arm-readback detail=descriptor-channel-readback error=-5`.
The decisive tuple was `cfg=3 chen=1 cfglo=0000000f cfghi=0a020001
llp=00000001 sar=48031500 ctrlhi=c0108840`, with every host, bridge, and GDMA
fault status zero. GDMA had fetched the one-shot head and advanced live LLP to
the terminal descriptor's `0 | memory-port` value `1`; `SAR=48031500`
(`front+0x500`) independently proves that fetch. The old post-enable
`LLP == head | memory-port` comparison was therefore a false rejection, not a
programming failure. Containment reached `FAILED_QUIESCENT`, preserved the
headless shell, and recorded `optical-state=unobserved`.

Patch 39 validates descriptor `CTRL_HI`, channel configuration, head LLP, LLP
high zero, disabled CHEN, and clean status before enabling the one-shot engine;
after CHEN it validates only channel-active and error/status safety, without
comparing hardware-owned descriptor or LLP state. Its exact patch SHA-256 is
`d8c774f42019dddeba6020ec019b4fd61474dec3c2913c33b5c2ad191deabe94`,
commit is `99fcff145f84b22c8996e7cab8ce9a77abe472e7`, post-source SHA-256 is
`68378f2ca532ec36020a3a99a57c2b8a909b694cb11d613764427acd76c7de0c`,
and standalone/in-tree W=1 warnings-as-errors object SHA-256 is
`b86cbf98fe2e2d2b894928049d9110cafedb290fbd1b2257285e06143997331a`.
Strict checkpatch is 0/0/0, fuzz-zero application passes, and the independent
audit result is `PATCH39 INDEPENDENT SEMANTIC AUDIT: CLEAR — no blocker.` The
historical 920-case model and 7/39/10 series check passed. The frozen 56-patch manifest
SHA-256 is
`0f993160af2ce5bb03d791a3708065972764111a7da05a8b5272a03306a9db74`.
The clean M7 ABI-v2 regression passes source contract
`112e8a0afef2f14151a8fcc9e5f8c054e671370d47b8ae042d87a15e52b00ca4`
and its 14-file bFLT W^X audit; retained log
`out/build-logs/m7-patch39-final-rerun-20260812T063011.stdout.log` has SHA-256
`4ccc716b3ca1d9c9de013eb59e852fd0d876d58cd4f222792fce89610242d382`.
The fresh M9 full build passes source contract
`e9c1e789fe87a5a7735c62785ff0113bca61c9d924e964c0a39ce30d69ab5fba`;
retained stdout
`build/patch39-m9-build/m9-full-final-20260812T062855.stdout.log` has SHA-256
`66767316f4a0d528ca1ac036a2066b9ce0569ff597c542b035b91e2538ee6678`.
The no-flash verifier passes with the 246,640-byte loader at
`e314b558d923e8fa0175f9d4eb692ce5728eac3175053ca47c53775a0dbf9104`;
retained stdout
`build/patch39-m9-build/m9-noflash-final-20260812T063226.stdout.log` has SHA-256
`773a31cdc8ae7223f3bea628a1cbd7f23e1d9e1be0dea62037e2dd6138010586`
and it reports `Nothing was flashed`. The independent artifact audit passes all
seven hashes and their metadata/DTB/rootfs/driver identities. Subsequent Patch-39
physical evidence and the current Patch-40 integration follow. The digest
identifies source ordering and bytes; it does not observe panel light or pixels.

Patch 39 passed exact flash/readback under
`build/m9-readback/20260812T134841478Z-1a4e56fe`; `readback.json` has SHA-256
`691fd9ac949de313a74c5591870bd3a95769baeae7f8c7be7d22dd429be61c09`.
Its sealed snapshot
`out/m9/hardware-runs/20260812T135132Z-snapshot-b5890811aa40-2b712727.log`
has SHA-256
`9f4cb506b955c9e0b2fbfbbaf3927271de3eef40df7b0e1b0a67a3991d3228ac`.
Linux completed four frames (`generation=4 frames=4 rearm=5/0 guards=1`) with
zero host, bridge, GDMA, DMA, or software faults and exact programmed
`VID_MODE_CFG=0000ff02`. Runtime policy failed solely because the optional,
disabled shadow mirror remained `active=00000000`; containment published
`FAILED_QUIESCENT`, retained the headless shell, and recorded
`optical-state=unobserved`.

Patch 40 removes only the three-line `DSI_HOST_NATIVE_VIDEO_POLICY_ACT`
composite and its two-line runtime equality (zero additions, five deletions).
Its product file is
`buildroot-external/board/micronux/patches-peripherals/linux/0040-video-fbdev-stop-gating-scanout-on-inactive-DSI-mirror.patch`.
The active-register offset, component masks, snapshot read, and diagnostic log
remain; exact programmed `VID_MODE_CFG=0000ff02` plus the other 40 predicates
remain mandatory. There are no write, teardown, reveal, or ABI-v2 changes. Its
patch SHA-256, commit, post-source SHA-256, and W=1 object SHA-256 are
`04df043b79760379cc8f4d0d74847d892e3895bc1b55eba3b90af23695647ed1`,
`0fac40ee31c1af165ea94a82a7b0d905d8da4861`,
`13088b8fefe6cf8ca415615e2cf8e9e62a6f3d972d2f010c49965139924a5164`,
and `845e536b031622f29cc8d1e157c1e284e453f8fde8d053a2dba20182368dcdd1`.
Strict checks and the independent semantic audit are clear. The current
7/40/10 checker passes all 57 patches with manifest
`f9318e1a6e7480f1105ec5a435ad71d2754bb6d91a79bcc471747249128ed983`,
and the shared model passes 963 cases. The clean isolated Patch-40 M7 ABI-v2
regression passes source contract
`6b1d5d730c7370f364fed80c507f8ba8454c52b9ee247a22cde96a54233d8206`.
Retained stdout
`out/m7/build-logs/20260812T072708Z-patch40-clean.stdout.log` has SHA-256
`9bddb74e3734cd977bd3b98a28cab29e75a626e6483219ccf88cca418fc47c05`.
Its driver source/object gates match the Patch-40 hashes above, and its 14-file,
128-byte-granule bFLT W^X audit passes. Exact Image, DTB, metadata, and rootfs
SHA-256 values are
`ca34bd104c670427bc7567991056eb9f423cd875290bd238050b384c71454f27`,
`42e3ac2fadcbeda59a13ee3cfd4afb490607e13185b7db48b2c3ee1fd00a4fe6`,
`a1f0c6047b453846831eca90ab6d6675a3933e369fa479d138c4331b97741728`,
and `1721822da26deba72d2952af1d5bade5c9d8b7eeb8d6d6c4616a83c2ff8ba3ae`.
Metadata CRC32 is `901bf054`, and the payload ends at `0x48a0cd08`. This was
build-only: no COM access, reset, or flash occurred, and it is not new physical
acceptance. The fresh M9 build passes source contract
`4f21d0c9e47cc0c003ccbfe9db8c558e18fcf1cf82cceb1e50319661d6ce2686`;
retained stdout has SHA-256
`0c15ef3f2c0e40f531b77a12cdf1ec5731272005b2488d96789348bd2319cb6e`.
The no-flash verifier's retained stdout has SHA-256
`4b64a06e8d65391d5fab3182cdf864fb815a455fe06e6be91c0cf9724f7f3e6e`
and reports `Nothing was flashed`; the seven-artifact audit passes. Patch 40
has not been flashed, so runtime and user-observed visual acceptance remain
pending.

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
