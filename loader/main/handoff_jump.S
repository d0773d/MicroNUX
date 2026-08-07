/* SPDX-License-Identifier: MIT */

    .section .iram1,"ax",@progbits
    .align  2
    .global micronux_handoff_jump
    .type   micronux_handoff_jump, @function

/*
 * a0: boot hart ID
 * a1: pointer to micronux_handoff_v1_t
 * a2: diagnostic entry virtual address in executable PSRAM
 */
micronux_handoff_jump:
    fence   rw, rw
    fence.i
    jr      a2

    .size micronux_handoff_jump, .-micronux_handoff_jump
