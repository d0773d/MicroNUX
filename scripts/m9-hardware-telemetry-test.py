#!/usr/bin/env python3
"""Exercise M9 hardware-harness telemetry parsers with synthetic output."""

from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "scripts" / "m9-hardware-test.py"


def load_harness_parsers() -> dict[str, object]:
    tree = ast.parse(HARNESS.read_text(encoding="utf-8"), filename=str(HARNESS))
    assignments = {
        "SCANOUT_LINE_RE",
        "DIAGNOSTICS_LINE_RE",
        "DISCONNECT_UPTIME_RE",
        "DIAGNOSTICS_PHASE_RE",
        "MIN_DISCONNECT_PROGRESS_HZ",
        "MAX_DISCONNECT_PROGRESS_HZ",
        "MAX_REARM_NS",
    }
    functions = {
        "scanout_samples",
        "scanout_contract_problem",
        "diagnostics_samples",
        "diagnostics_phase_groups",
        "diagnostics_phase_problem",
        "diagnostics_contract_problem",
        "disconnect_progress_rate",
        "diagnostics_phase_shell",
        "framebuffer_hash_shell",
    }
    selected: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in assignments
            for target in node.targets
        ):
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in functions:
            selected.append(node)
    namespace: dict[str, object] = {"re": re}
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    exec(compile(module, str(HARNESS), "exec"), namespace)
    missing = (assignments | functions) - namespace.keys()
    if missing:
        raise AssertionError(f"parser-extraction-missing:{sorted(missing)}")
    return namespace


def diagnostics_line(
    frames: int = 100,
    *,
    faults: int = 0,
    error: int = 0,
    host0: int = 0,
    host1: int = 0,
    underruns: int = 0,
    rearms: int | None = None,
    rearm_failures: int = 0,
    generation: int | None = None,
    commit: int = 1,
    guard_errors: int = 0,
    over20: int = 0,
    rearm_max: int = 140_000,
    fifo_irq_zero: int = 0,
    fifo_poll_zero: int = 0,
    filler: int = 0,
    misc: int = 0x00003201,
) -> str:
    if rearms is None:
        rearms = frames + 1
    if generation is None:
        generation = frames
    return (
        "abi=3 state=RUNTIME_REVEALED "
        f"frames={frames} faults={faults:x} "
        f"error={error:08x} host-errors={host0:08x}:{host1:08x} "
        f"underruns={underruns} buffers=3 front=0 queued=-1 back=1 "
        f"rearm={rearms}/{rearm_failures} flips=1/1 "
        f"generation={generation} commit={commit} guards=ok "
        f"guard-errors={guard_errors} policy=ok "
        f"arm-to-irq-ns=14550000/14590000 arm-to-irq-over20ms={over20} "
        f"rearm-ns=95000/{rearm_max} fifo-irq=256/0/{fifo_irq_zero} "
        f"fifo-poll=384/16/{fifo_poll_zero} bridge-filler={filler:08x} "
        f"bridge-misc={misc:08x} i2c=active-serialized "
        "physical-panel-state=unobserved\n"
    )


def scanout_line(
    before: int = 96,
    after: int = 100,
    *,
    over20: int = 0,
    rearm_max: int = 140_000,
    fifo_irq_zero: int = 0,
    fifo_poll_zero: int = 0,
    filler: int = 0,
    misc: int = 0x00003201,
) -> str:
    return (
        "running abi=3 state=RUNTIME_REVEALED "
        f"frames={before}->{after} error=00000000 underruns=0 chen=1 "
        "faults=0 host-errors=00000000:00000000 frame-ack=on "
        "clock=auto lp=enabled backlight-gate=on buffers=3 "
        f"front=0 queued=-1 back=1 rearm={after + 1}/0 flips=1/1 "
        f"generation={after} commit=1 guards=ok guard-errors=0 "
        f"arm-to-irq-ns=14550000/14590000 arm-to-irq-over20ms={over20} "
        f"rearm-ns=95000/{rearm_max} fifo-irq=256/0/{fifo_irq_zero} "
        f"fifo-poll=384/16/{fifo_poll_zero} bridge-filler={filler:08x} "
        f"bridge-misc={misc:08x} i2c=active-serialized\n"
    )


def phase_block(
    phase: str,
    frames: int,
    *,
    uptime: float | None = None,
    over20: int = 0,
    fifo_irq_zero: int = 0,
    fifo_poll_zero: int = 0,
) -> str:
    output = f"MICRONUX:M9:DIAGNOSTICS phase={phase}\n"
    if uptime is not None:
        output += (
            f"MICRONUX:M9:USB-DISCONNECT:{phase}:UPTIME "
            f"{uptime:.2f} 0.00\n"
        )
    return output + diagnostics_line(
        frames,
        over20=over20,
        fifo_irq_zero=fifo_irq_zero,
        fifo_poll_zero=fifo_poll_zero,
    )


def main() -> int:
    api = load_harness_parsers()
    phase_groups = api["diagnostics_phase_groups"]
    phase_problem = api["diagnostics_phase_problem"]
    diagnostics_problem = api["diagnostics_contract_problem"]
    scanout_problem = api["scanout_contract_problem"]
    progress_rate = api["disconnect_progress_rate"]
    phase_shell = api["diagnostics_phase_shell"]
    hash_shell = api["framebuffer_hash_shell"]
    tests = 0

    def check(condition: bool, reason: str) -> None:
        nonlocal tests
        if not condition:
            raise AssertionError(reason)
        tests += 1

    pair = ("A-PREHASH", "A-POSTHASH")
    pair_output = phase_block(pair[0], 100) + phase_block(pair[1], 101)
    check(
        [phase for phase, _ in phase_groups(pair_output)] == list(pair),
        "phase-groups-ordered",
    )
    check(phase_problem(pair_output, pair) is None, "phase-pair-valid")
    check(
        phase_problem(
            phase_block(pair[0], 100, fifo_irq_zero=7)
            + phase_block(pair[1], 101, fifo_irq_zero=8),
            pair,
        )
        is None,
        "fifo-irq-zero-increase-is-evidence",
    )
    check(
        phase_problem(pair_output.replace("\n", "\r\n"), pair) is None,
        "phase-crlf-valid",
    )
    snapshot_phases = (
        "SNAPSHOT-A-PREHASH",
        "SNAPSHOT-A-POSTHASH",
        "SNAPSHOT-B-PREHASH",
        "SNAPSHOT-B-POSTHASH",
    )
    snapshot_output = "".join(
        phase_block(phase, 100 + index)
        for index, phase in enumerate(snapshot_phases)
    )
    check(
        phase_problem(snapshot_output, snapshot_phases) is None,
        "snapshot-four-phase-valid",
    )
    snapshot_idle_increase = snapshot_output.replace(
        phase_block("SNAPSHOT-B-PREHASH", 102),
        phase_block("SNAPSHOT-B-PREHASH", 102, over20=1),
    )
    check(
        phase_problem(snapshot_idle_increase, snapshot_phases)
        == (
            "diagnostics-phase-snapshot-a-posthash-to-snapshot-b-prehash-"
            "arm-to-irq-over20ms-increased"
        ),
        "snapshot-settle-over20-increase-rejected",
    )
    check(
        phase_problem(pair_output, tuple(reversed(pair)))
        == "diagnostics-phase-order",
        "phase-order-rejected",
    )
    check(
        phase_problem(phase_block(pair[0], 100), pair)
        == "diagnostics-phase-order",
        "phase-missing-rejected",
    )
    check(
        phase_problem(pair_output + phase_block("EXTRA", 102), pair)
        == "diagnostics-phase-order",
        "phase-extra-rejected",
    )
    check(
        phase_problem(
            phase_block(pair[0], 100)
            + diagnostics_line(101)
            + phase_block(pair[1], 102),
            pair,
        )
        == "diagnostics-phase-a-prehash-count-2",
        "phase-duplicate-sample-rejected",
    )
    check(
        phase_problem(
            "MICRONUX:M9:DIAGNOSTICS phase=A-PREHASH\n"
            + phase_block(pair[1], 101),
            pair,
        )
        == "diagnostics-phase-a-prehash-count-0",
        "phase-missing-sample-rejected",
    )
    check(
        phase_problem(
            phase_block(pair[0], 100, over20=1)
            + phase_block(pair[1], 101, over20=1),
            pair,
        )
        == "diagnostics-phase-a-prehash-arm-to-irq-over20ms-nonzero",
        "first-visible-epoch-must-be-zero",
    )
    check(
        phase_problem(
            phase_block(pair[0], 100)
            + phase_block(pair[1], 101, over20=1),
            pair,
        )
        == (
            "diagnostics-phase-a-prehash-to-a-posthash-"
            "arm-to-irq-over20ms-increased"
        ),
        "hash-over20-increase-rejected",
    )
    check(
        phase_problem(
            phase_block(pair[0], 100, fifo_irq_zero=8)
            + phase_block(pair[1], 101, fifo_irq_zero=7),
            pair,
        )
        == "diagnostics-phase-a-prehash-to-a-posthash-fifo-irq-zero-regressed",
        "fifo-irq-regression-rejected",
    )
    check(
        phase_problem(
            phase_block(pair[0], 100, fifo_poll_zero=1)
            + phase_block(pair[1], 101, fifo_poll_zero=1),
            pair,
        )
        == "diagnostics-phase-a-prehash-fifo-poll-zero",
        "fifo-poll-first-nonzero-rejected",
    )
    check(
        phase_problem(
            phase_block(pair[0], 100)
            + phase_block(pair[1], 101, fifo_poll_zero=1),
            pair,
        )
        == "diagnostics-phase-a-posthash-fifo-poll-zero",
        "fifo-poll-later-nonzero-rejected",
    )

    six_phases = (
        "BEFORE-PREHASH",
        "BEFORE-POSTHASH",
        "BOUNDARY-PREHASH",
        "BOUNDARY-POSTHASH",
        "AFTER-PREHASH",
        "AFTER-POSTHASH",
    )
    six_output = "".join(
        phase_block(phase, 100 + index, fifo_irq_zero=index // 2)
        for index, phase in enumerate(six_phases)
    )
    check(
        phase_problem(six_output, six_phases) is None,
        "disconnect-six-phase-valid",
    )
    reconnect_increase = six_output.replace(
        phase_block("AFTER-PREHASH", 104, fifo_irq_zero=2),
        phase_block("AFTER-PREHASH", 104, over20=1, fifo_irq_zero=2),
    )
    check(
        phase_problem(reconnect_increase, six_phases)
        == (
            "diagnostics-phase-boundary-posthash-to-after-prehash-"
            "arm-to-irq-over20ms-increased"
        ),
        "reconnect-over20-increase-rejected",
    )

    check(diagnostics_problem(diagnostics_line()) is None, "diagnostics-valid")
    check(
        diagnostics_problem(diagnostics_line(fifo_irq_zero=7)) is None,
        "diagnostics-fifo-irq-zero-is-evidence",
    )
    check(
        diagnostics_problem(diagnostics_line(over20=1))
        == "diagnostics-arm-to-irq-over20ms",
        "diagnostics-over20-rejected",
    )
    check(
        diagnostics_problem(diagnostics_line(fifo_poll_zero=1))
        == "diagnostics-fifo-poll-zero",
        "diagnostics-fifo-poll-zero-rejected",
    )
    check(
        diagnostics_problem(diagnostics_line(rearm_max=1_000_000))
        == "diagnostics-rearm-latency",
        "diagnostics-rearm-limit-rejected",
    )
    check(
        diagnostics_problem(diagnostics_line(filler=1))
        == "diagnostics-bridge-filler",
        "diagnostics-filler-rejected",
    )
    check(
        diagnostics_problem(diagnostics_line(misc=0x3200))
        == "diagnostics-bridge-misc",
        "diagnostics-misc-rejected",
    )
    check(
        diagnostics_problem(diagnostics_line(faults=1))
        == "diagnostics-health-nonzero",
        "diagnostics-fault-rejected",
    )
    check(
        diagnostics_problem(diagnostics_line(host1=1))
        == "diagnostics-health-nonzero",
        "diagnostics-host-error-rejected",
    )
    check(
        diagnostics_problem(diagnostics_line(guard_errors=1))
        == "diagnostics-health-nonzero",
        "diagnostics-guard-error-rejected",
    )
    check(
        diagnostics_problem(
            diagnostics_line(100, fifo_irq_zero=2)
            + diagnostics_line(101, fifo_irq_zero=1)
        )
        == "diagnostics-fifo_irq_zero-regressed",
        "diagnostics-fifo-irq-regression-rejected",
    )
    check(scanout_problem(scanout_line()) is None, "scanout-valid")
    check(
        scanout_problem(scanout_line(fifo_irq_zero=7)) is None,
        "scanout-fifo-irq-zero-is-evidence",
    )
    check(
        scanout_problem(scanout_line(over20=1))
        == "scanout-arm-to-irq-over20ms",
        "scanout-over20-rejected",
    )
    check(
        scanout_problem(scanout_line(fifo_poll_zero=1))
        == "scanout-fifo-poll-zero",
        "scanout-fifo-poll-zero-rejected",
    )
    check(
        scanout_problem(scanout_line(rearm_max=1_000_000))
        == "scanout-rearm-latency",
        "scanout-rearm-limit-rejected",
    )
    check(
        scanout_problem(scanout_line(filler=1)) == "scanout-bridge-filler",
        "scanout-filler-rejected",
    )
    check(
        scanout_problem(scanout_line(misc=0x3200)) == "scanout-bridge-misc",
        "scanout-misc-rejected",
    )

    start_phase = "BEFORE-POSTHASH"
    end_phase = "BOUNDARY-PREHASH"
    start = phase_block(start_phase, 100, uptime=10.0)
    check(
        progress_rate(
            start,
            start_phase,
            phase_block(end_phase, 110, uptime=11.0),
            end_phase,
        )
        == (None, 10.0),
        "progress-lower-bound-valid",
    )
    check(
        progress_rate(
            start,
            start_phase,
            phase_block(end_phase, 125, uptime=11.0),
            end_phase,
        )
        == (None, 25.0),
        "progress-upper-bound-valid",
    )
    low_problem, _ = progress_rate(
        start,
        start_phase,
        phase_block(end_phase, 109, uptime=11.0),
        end_phase,
    )
    check(low_problem.endswith("progress-hz-9.000"), "progress-low-rejected")
    high_problem, _ = progress_rate(
        start,
        start_phase,
        phase_block(end_phase, 126, uptime=11.0),
        end_phase,
    )
    check(high_problem.endswith("progress-hz-26.000"), "progress-high-rejected")
    order_problem, _ = progress_rate(
        start,
        start_phase,
        phase_block(end_phase, 125, uptime=10.0),
        end_phase,
    )
    check(order_problem.endswith("uptime-order"), "uptime-order-rejected")
    progress_problem, _ = progress_rate(
        start,
        start_phase,
        phase_block(end_phase, 100, uptime=11.0),
        end_phase,
    )
    check(progress_problem.endswith("progress-order"), "progress-order-rejected")
    uptime_problem, _ = progress_rate(
        phase_block(start_phase, 100),
        start_phase,
        phase_block(end_phase, 168, uptime=11.0),
        end_phase,
    )
    check(uptime_problem.endswith("uptime-count-0"), "uptime-missing-rejected")
    duplicate_diagnostics = (
        phase_block(end_phase, 168, uptime=11.0) + diagnostics_line(169)
    )
    diagnostics_count_problem, _ = progress_rate(
        start,
        start_phase,
        duplicate_diagnostics,
        end_phase,
    )
    check(
        diagnostics_count_problem.endswith("diagnostics-count-2"),
        "progress-duplicate-diagnostics-rejected",
    )

    plain_phase_shell = phase_shell("SNAPSHOT-A-PREHASH")
    check(
        plain_phase_shell.index("DIAGNOSTICS phase=")
        < plain_phase_shell.index('cat "$D/diagnostics"'),
        "phase-shell-marker-before-diagnostics",
    )
    uptime_phase_shell = phase_shell("BOUNDARY-PREHASH", "BOUNDARY-PREHASH")
    check(
        uptime_phase_shell.index(":UPTIME")
        < uptime_phase_shell.index('cat "$D/diagnostics"'),
        "phase-shell-uptime-adjacent-before-diagnostics",
    )
    generated_hash_shell = hash_shell("snapshot-a", "m9_fb_before")
    check("sha256sum /dev/fb0" in generated_hash_shell, "hash-shell-targets-fb0")

    harness_tree = ast.parse(
        HARNESS.read_text(encoding="utf-8"), filename=str(HARNESS)
    )
    function_sources = {
        node.name: ast.get_source_segment(
            HARNESS.read_text(encoding="utf-8"), node
        )
        for node in harness_tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"run_snapshot", "run_disconnect"}
    }

    def ordered(source: str, tokens: tuple[str, ...]) -> bool:
        position = -1
        for token in tokens:
            position = source.find(token, position + 1)
            if position < 0:
                return False
        return True

    check(
        ordered(
            function_sources["run_snapshot"],
            (
                'diagnostics_phase_shell("SNAPSHOT-A-PREHASH")',
                'framebuffer_hash_shell("snapshot-a", "m9_fb_before")',
                'diagnostics_phase_shell("SNAPSHOT-A-POSTHASH")',
                'diagnostics_phase_shell("SNAPSHOT-B-PREHASH")',
                'framebuffer_hash_shell("snapshot-b", "m9_fb_after")',
                'diagnostics_phase_shell("SNAPSHOT-B-POSTHASH")',
            ),
        ),
        "snapshot-source-phase-order",
    )
    check(
        ordered(
            function_sources["run_disconnect"],
            (
                'diagnostics_phase_shell("BEFORE-PREHASH")',
                'framebuffer_hash_shell("disconnect-before", "m9_fb_hash")',
                'diagnostics_phase_shell("BEFORE-POSTHASH", "BEFORE-POSTHASH")',
                'diagnostics_phase_shell("BOUNDARY-PREHASH", "BOUNDARY-PREHASH")',
                'framebuffer_hash_shell("disconnect-boundary", "m9_fb_hash")',
                '"BOUNDARY-POSTHASH", "BOUNDARY-POSTHASH"',
                'diagnostics_phase_shell("AFTER-PREHASH", "AFTER-PREHASH")',
                'framebuffer_hash_shell("disconnect-after", "m9_fb_hash")',
                'diagnostics_phase_shell("AFTER-POSTHASH")',
            ),
        ),
        "disconnect-source-phase-order",
    )

    print(f"MICRONUX:M9:TELEMETRY-PARSER:PASS tests={tests}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
