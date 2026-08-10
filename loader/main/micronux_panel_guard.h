// SPDX-License-Identifier: MIT

#pragma once

/*
 * Suppress the pinned Waveshare panel constructors' internal I2C power and
 * full-brightness writes. MicroNUX sequences those registers explicitly before
 * construction and enables bounded brightness only after every safety gate.
 */
void micronux_panel_guard_begin(void);
void micronux_panel_guard_end(void);
