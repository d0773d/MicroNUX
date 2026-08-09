# MicroNUX ESP-IDF v6.0.1 patches

These patches apply only to the exact upstream ESP-IDF commit
`8c19b156084a0753687347cca1f5355782893533` (`v6.0.1`). They are applied to a
disposable Git worktree under `build/`; the installed SDK is never modified.
The patched `cpu_region_protect.c` SHA-256 is
`26c4c6a1fed3aa64ef7b331fab905f54bb33674fe71db561412a157dc9db131f`.

`0001-esp32p4-deny-u-mode-platform-regions.patch` changes the pre-v3 ESP32-P4
bootloader/application PMP setup as soon as the patch is applied. Patch
application is the early-boot opt-in because ESP-IDF's bootloader subproject
does not import project Kconfig symbols. The separate
`CONFIG_MICRONUX_M7_EARLY_UMODE_DENY=y` option enables the loader's mandatory
CSR audit and is required by the M7 build wrapper.
Affected platform entries become unlocked regions with no U-mode R/W/X bits.
The M-mode loader and Linux kernel continue to bypass those permissions.

The M7 preparation script must verify the source commit, check that the patch
applies cleanly, apply it once, and verify the resulting source digest before
building. A mismatched or partly patched source tree is a hard failure.
