#!/usr/bin/env python3
"""Run the M9 hardware gate while preserving an auditable host transcript."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_text(repo: Path, *arguments: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def argument_value(arguments: list[str], option: str) -> str | None:
    for index, argument in enumerate(arguments):
        if argument == option and index + 1 < len(arguments):
            return arguments[index + 1]
        if argument.startswith(option + "="):
            return argument.split("=", 1)[1]
    return None


def artifact_record(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    return {
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def emit(stream, log, payload: bytes) -> None:
    log.write(payload)
    log.flush()
    try:
        stream.write(payload)
        stream.flush()
    except (BrokenPipeError, OSError):
        pass


def marker(name: str, payload: dict[str, object]) -> bytes:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"MICRONUX:M9.2:{name} {encoded}\n".encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="log an exact m9-hardware-test.py run"
    )
    parser.add_argument("--log-file", type=Path)
    parser.add_argument(
        "harness_args",
        nargs=argparse.REMAINDER,
        help="arguments after -- are passed to m9-hardware-test.py",
    )
    args = parser.parse_args()
    harness_args = list(args.harness_args)
    if harness_args and harness_args[0] == "--":
        harness_args.pop(0)
    if not harness_args:
        parser.error("provide m9-hardware-test.py arguments after --")

    repo = Path(__file__).resolve().parent.parent
    harness = repo / "scripts" / "m9-hardware-test.py"
    wrapper = Path(__file__).resolve()
    if not harness.is_file():
        parser.error(f"missing hardware harness: {harness}")

    mode = argument_value(harness_args, "--mode") or "unknown"
    port = argument_value(harness_args, "--port") or "COM14"
    artifact_dir_argument = argument_value(harness_args, "--artifact-dir")
    if artifact_dir_argument is None:
        artifact_dir = repo / "out" / "m9"
    else:
        artifact_dir = Path(artifact_dir_argument)
        if not artifact_dir.is_absolute():
            artifact_dir = repo / artifact_dir
        artifact_dir = artifact_dir.resolve()
    start_utc = utc_now()
    run_id = uuid.uuid4().hex
    if args.log_file is None:
        safe_mode = "".join(
            character if character.isalnum() or character in "-_" else "-"
            for character in mode
        )
        head = git_text(repo, "rev-parse", "--short=12", "HEAD") or "no-git"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_path = (
            repo
            / "out"
            / "m9"
            / "hardware-runs"
            / f"{timestamp}-{safe_mode}-{head}-{run_id[:8]}.log"
        )
    else:
        log_path = args.log_file.resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    artifact_paths = {
        "loader": repo
        / "build"
        / "loader-m9-cold-jd9365-abi3"
        / "micronux_m3_loader.bin",
        "Image": artifact_dir / "Image",
        "dtb": artifact_dir / "esp32p4-micronux.dtb",
        "metadata": artifact_dir / "metadata.bin",
        "source_contract": artifact_dir / "SOURCE-CONTRACT",
    }
    metadata: dict[str, object] = {
        "schema": 1,
        "run_id": run_id,
        "start_utc": start_utc,
        "mode": mode,
        "port": port,
        "artifact_dir": str(artifact_dir),
        "argv": harness_args,
        "git_head": git_text(repo, "rev-parse", "HEAD"),
        "git_branch": git_text(repo, "branch", "--show-current"),
        "git_dirty": bool(git_text(repo, "status", "--porcelain=v1")),
        "harness_sha256": sha256_file(harness),
        "wrapper_sha256": sha256_file(wrapper),
        "host_artifacts": {
            name: artifact_record(path) for name, path in artifact_paths.items()
        },
    }

    started = time.monotonic()
    child: subprocess.Popen[bytes] | None = None
    exit_code = 127
    interrupted = False
    with log_path.open("xb") as log:
        emit(sys.stdout.buffer, log, marker("RUN-BEGIN", metadata))
        try:
            child = subprocess.Popen(
                [sys.executable, "-u", str(harness), *harness_args],
                cwd=repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
            assert child.stdout is not None
            while True:
                chunk = child.stdout.read(65536)
                if not chunk:
                    break
                emit(sys.stdout.buffer, log, chunk)
            exit_code = child.wait()
        except KeyboardInterrupt:
            interrupted = True
            exit_code = 130
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        except OSError as error:
            emit(
                sys.stdout.buffer,
                log,
                marker(
                    "RUN-ERROR",
                    {
                        "run_id": run_id,
                        "type": type(error).__name__,
                        "detail": str(error),
                    },
                ),
            )
        finally:
            end_record: dict[str, object] = {
                "run_id": run_id,
                "end_utc": utc_now(),
                "duration_seconds": round(time.monotonic() - started, 3),
                "exit_code": exit_code,
                "interrupted": interrupted,
            }
            emit(sys.stdout.buffer, log, marker("RUN-END", end_record))
            log.flush()
            os.fsync(log.fileno())

    log_digest = sha256_file(log_path)
    sidecar = Path(str(log_path) + ".sha256")
    with sidecar.open("x", encoding="ascii", newline="\n") as output:
        output.write(f"{log_digest}  {log_path.name}\n")
        output.flush()
        os.fsync(output.fileno())
    print(
        "MICRONUX:M9.2:RUN-LOG "
        f"state=sealed path={log_path} sha256={log_digest} exit_code={exit_code}"
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
