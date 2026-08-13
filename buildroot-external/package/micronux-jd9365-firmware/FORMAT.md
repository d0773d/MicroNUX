<!-- SPDX-License-Identifier: Apache-2.0 -->

# MicroNUX JD9365 command firmware format v1

The artifact is little-endian and has one 32-byte header followed immediately
by a variable-length record payload. It contains DCS writes and post-write
delays only. It is deliberately incapable of expressing MMIO, I2C, GPIO,
clock, reset, regulator, DMA, or interrupt operations.

## Header

| Offset | Size | Field | Required value |
| ---: | ---: | --- | --- |
| 0 | 8 | magic | ASCII `MNJD9365` |
| 8 | 2 | format version | `1` |
| 10 | 2 | header bytes | `32` |
| 12 | 2 | total record count | `1..512` |
| 14 | 2 | prelude record count | no greater than total count |
| 16 | 4 | payload bytes | no greater than 65,536 |
| 20 | 4 | payload CRC32 | IEEE CRC32 over the payload only |
| 24 | 4 | flags | `0x7`: DCS, delay-after-write, write-only |
| 28 | 2 | largest parameter count present | no greater than 64 |
| 30 | 2 | largest delay present, ms | no greater than 5,000 |

## Record

Each record is `command:u8`, `parameter_count:u8`, `delay_ms:u16le`, followed
by exactly `parameter_count` bytes. There is no padding. A consumer must reject
unknown versions or flags, size mismatches, CRC mismatches, trailing bytes,
out-of-range counts, or a header maximum that does not match the records.

For this profile, the first four records are the JD9365 page, MADCTL, COLMOD,
and two-lane prelude. The remaining 200 records are the pinned Waveshare vendor
program. Panel reset, optional ID read, D-PHY setup, DPI timing, and scanout
start are intentionally outside this artifact.
