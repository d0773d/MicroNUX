# MicroNUX ESP-IDF v6.0.1 patches

These patches apply only to the exact upstream ESP-IDF commit
`8c19b156084a0753687347cca1f5355782893533` (`v6.0.1`). They are applied to a
disposable Git worktree under `build/`; the installed SDK is never modified.
The verified patched-source SHA-256 values are:

- `cpu_region_protect.c`:
  `26c4c6a1fed3aa64ef7b331fab905f54bb33674fe71db561412a157dc9db131f`;
- `esp_lcd_panel_dpi.c`:
  `f5f5ce836267020d72f5f9a7591647cc6606d8729fc26a12197889d03b3eeeb1`;
- `esp_lcd_mipi_dsi.h`:
  `eb60e0441b65229424eb55f9ffae4638186d4785a0279296c1b53c452dc44ba6`.

`0001-esp32p4-deny-u-mode-platform-regions.patch` changes the pre-v3 ESP32-P4
bootloader/application PMP setup as soon as the patch is applied. Patch
application is the early-boot opt-in because ESP-IDF's bootloader subproject
does not import project Kconfig symbols. The separate
`CONFIG_MICRONUX_M7_EARLY_UMODE_DENY=y` option enables the loader's mandatory
CSR audit and is required by the M7 build wrapper.
Affected platform entries become unlocked regions with no U-mode R/W/X bits.
The M-mode loader and Linux kernel continue to bypass those permissions.

`0002-lcd-add-dpi-circular-handoff.patch` adds an exact-version integration API
that quiesces the loader-created DPI transfer, constructs a four-descriptor
ring as a bounded handoff template, and returns the framebuffer, descriptors,
and channel to MicroNUX. The loader leaves DPI/framebuffer mode selected and
publishes a versioned, CRC-protected handoff. Linux validates the ring, then
programs the channel's hardware reload mode itself. This patch deliberately
does not claim to be a general ESP-IDF API: its semantics are narrow and
pinned to the source digests above.

The M7 preparation script must verify the source commit, check that both
patches apply cleanly, apply each once, and verify all resulting source digests
before building. A mismatched or partly patched source tree is a hard failure.
