# MicroNUX ESP-IDF v6.0.1 patches

These patches apply only to the exact upstream ESP-IDF commit
`8c19b156084a0753687347cca1f5355782893533` (`v6.0.1`). They are applied to a
disposable Git worktree under `build/`; the installed SDK is never modified.
The verified patched-source SHA-256 values are:

- `cpu_region_protect.c`:
  `6fef47b34a8fddd51823fba5bbd443e78435d7d34f3cc3937bb254a29fce63be`;
- `esp_lcd_panel_dpi.c`:
  `c817340fedf56ca6c6d74d634948ed39473df5766c371e0ca07f06e26f662264`;
- `esp_lcd_mipi_dsi.h`:
  `f81b73c3dc9d4b5f88e15ca638773e955a7657ca43a5495eda55f6d41b961d9d`.

`0001-esp32p4-deny-u-mode-platform-regions.patch` changes the pre-v3 ESP32-P4
bootloader/application PMP setup as soon as the patch is applied. Patch
application is the early-boot opt-in because ESP-IDF's bootloader subproject
does not import project Kconfig symbols. The separate
`CONFIG_MICRONUX_M7_EARLY_UMODE_DENY=y` option enables the loader's mandatory
CSR audit and is required by the M7 build wrapper.
Affected platform entries become unlocked regions with no U-mode R/W/X bits.
The M-mode loader and Linux kernel continue to bypass those permissions.

`0002-lcd-add-dpi-circular-handoff.patch` adds an exact-version integration API
that quiesces the loader-created DPI transfer, allocates three stopped
64-byte descriptors as a bounded handoff pool, and returns the loader front
framebuffer, descriptors, and channel to MicroNUX. Every descriptor is marked
last but deliberately invalid, unlinked, and free of block interrupts. The
caller must first use the ESP-IDF v6.0.1 pattern API to disable bridge DPI and
select host VPG. The handoff keeps that hardware-generated source active while
the framebuffer DMA is stopped, then publishes the ABI-v2, CRC-protected
resource contract. Linux validates the VPG source, stopped channel, and pool;
rewrites one descriptor for each front/back/spare framebuffer; and explicitly
rearms one complete-frame transfer from each transfer-done IRQ. The historical
filename is retained to avoid an unrelated patch-path migration; the patch no
longer constructs a circular ring or selects hardware auto-reload. This patch
does not claim to be a general ESP-IDF API: its semantics are narrow and pinned
to the source digests above.

The M7 preparation script must verify the source commit, check that both
patches apply cleanly, apply each once, and verify all resulting source digests
before building. A mismatched or partly patched source tree is a hard failure.
