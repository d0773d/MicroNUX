#!/usr/bin/env python3
"""Validate the ordered MicroNUX Linux patch-series boundaries."""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOARD = ROOT / "buildroot-external" / "board" / "micronux"
CONFIGS = ROOT / "buildroot-external" / "configs"
SERIES = (
    ("patches-platform", 6),
    ("patches-peripherals", 17),
    ("patches-isolation", 10),
)
ISOLATION_CONFIGS = {
    CONFIGS / "micronux_esp32p4_isolation_defconfig",
    CONFIGS / "micronux_esp32p4_gui_foundation_defconfig",
}
SUBJECT_RE = re.compile(r"^Subject: \[PATCH (\d+)/(\d+)\] ", re.MULTILINE)


def fail(message: str) -> None:
    raise SystemExit(f"MICRONUX:PATCH-SERIES state=fail reason={message}")


def validate_series(directory: str, expected_count: int) -> list[str]:
    patch_dir = BOARD / directory / "linux"
    patches = sorted(patch_dir.glob("*.patch"))
    if len(patches) != expected_count:
        fail(f"{directory}-count-{len(patches)}-expected-{expected_count}")

    digests: list[str] = []
    for expected_index, patch in enumerate(patches, 1):
        prefix = f"{expected_index:04d}-"
        if not patch.name.startswith(prefix):
            fail(f"{directory}-order-{patch.name}-expected-{prefix}")

        content = patch.read_text(encoding="utf-8")
        subject = SUBJECT_RE.search(content)
        if subject is None:
            fail(f"{directory}-subject-{patch.name}")
        if int(subject.group(1)) != expected_index:
            fail(f"{directory}-subject-index-{patch.name}")
        if int(subject.group(2)) != expected_count:
            fail(f"{directory}-subject-total-{patch.name}")
        if "Signed-off-by:" not in content:
            fail(f"{directory}-signoff-{patch.name}")
        if "diff --git a/" not in content:
            fail(f"{directory}-diff-{patch.name}")

        digest = hashlib.sha256(patch.read_bytes()).hexdigest()
        digests.append(f"{directory}/linux/{patch.name} {digest}")

    return digests


def validate_config_boundaries() -> None:
    isolation_token = "board/micronux/patches-isolation"
    for config in sorted(CONFIGS.glob("micronux_esp32p4*_defconfig")):
        content = config.read_text(encoding="utf-8")
        has_isolation = isolation_token in content
        if config in ISOLATION_CONFIGS and not has_isolation:
            fail(f"isolation-config-missing-series-{config.name}")
        if config not in ISOLATION_CONFIGS and has_isolation:
            fail(f"isolation-series-leaked-{config.name}")
        for required in ("patches-platform", "patches-peripherals"):
            if f"board/micronux/{required}" not in content:
                fail(f"{config.name}-missing-{required}")


def main() -> int:
    old_monolith = BOARD / "patches" / "linux"
    if old_monolith.exists() and any(old_monolith.glob("*.patch")):
        fail("legacy-monolithic-directory-not-empty")

    manifest: list[str] = []
    for directory, expected_count in SERIES:
        manifest.extend(validate_series(directory, expected_count))
    validate_config_boundaries()

    manifest_hash = hashlib.sha256(("\n".join(manifest) + "\n").encode()).hexdigest()
    print(
        "MICRONUX:PATCH-SERIES state=pass "
        "platform=6 peripherals=17 isolation=10 total=33 "
        f"manifest_sha256={manifest_hash}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
