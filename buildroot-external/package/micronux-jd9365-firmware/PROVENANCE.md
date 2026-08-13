<!-- SPDX-License-Identifier: Apache-2.0 -->

# Provenance: Waveshare JD9365 10.1-inch v2 panel program

This data artifact is derived from the source file that declares
`SPDX-License-Identifier: Apache-2.0` in the pinned Waveshare component used by
MicroNUX:

- component: `waveshare/esp_lcd_jd9365_10_1` version `2.0.0`;
- repository: `git://github.com/waveshareteam/Waveshare-ESP32-components.git`;
- commit: `9daffb7168b3c093a330da700988a22092294eef`;
- repository path: `display/lcd/esp_lcd_jd9365_10_1/esp_lcd_jd9365_10_1.c`;
- source-file SHA-256: `c80f8ae4771adf46adabbcf3fedca73262504f068387b713c358476cb1629585`.

The artifact preserves the four writes performed immediately before the
vendor loop (`E0=00`, `36=00`, `3A=55`, `80=01`) and all 200 active records in
`vendor_specific_init_default`, in source order and with their parameter bytes
and delays unchanged. Commented-out examples are not records. The preceding
DCS software reset and diagnostic DCS ID read are control flow, not part of the
command table, and therefore are not encoded here.

The JSON representation and generated binary are provided under Apache-2.0;
see `LICENSE`. The generator and verifier are an independent, deterministic
serialization implementation and carry the same license for a simple package
licensing boundary.

The reproducible package build depends only on the tracked JSON, generator,
tests, and provenance values. It does not depend on an ignored ESP-IDF managed
component cache. When the pinned upstream source is locally available, pass it
to `test_firmware.py --pinned-source <path>` to additionally recheck its file
hash and compare all 200 vendor records byte-for-byte.
