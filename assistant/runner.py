"""Command execution with mandatory log preservation (including on failure).

Nothing in this module interprets exit codes as proof of correctness: a zero
exit code only means the process returned 0.  Verdicts come from parsers in
the calling checks.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field


@dataclass
class RunResult:
    command: list[str]
    exit_code: int | None
    timed_out: bool
    duration_s: float
    stdout_log: str
    stderr_log: str
    combined_log: str
    cwd: str
    error: str = ""

    @property
    def ran(self) -> bool:
        return self.error == "" and not self.timed_out


def run_command(
    command: list[str] | str,
    *,
    cwd: str | os.PathLike[str] | None = None,
    log_dir: str | os.PathLike[str],
    name: str,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> RunResult:
    """Run *command*, teeing all output to files under *log_dir*.

    Logs are written even when the command fails, times out or cannot start,
    so failed runs keep their evidence.  On timeout the process is killed and
    ``timed_out=True`` with ``exit_code=None``.
    """
    if isinstance(command, str):
        cmd_list = [command] if os.name == "nt" else ["/bin/bash", "-lc", command]
    else:
        cmd_list = list(command)
    log_dir = str(log_dir)
    os.makedirs(log_dir, exist_ok=True)
    out_path = os.path.join(log_dir, f"{name}.stdout.log")
    err_path = os.path.join(log_dir, f"{name}.stderr.log")
    comb_path = os.path.join(log_dir, f"{name}.log")

    started = time.time()
    error = ""
    exit_code: int | None = None
    timed_out = False

    with open(out_path, "wb") as fo, open(err_path, "wb") as fe:
        try:
            proc = subprocess.Popen(
                cmd_list,
                cwd=str(cwd) if cwd else None,
                stdout=fo,
                stderr=fe,
                env=env,
                shell=False,
            )
        except OSError as exc:  # command could not start: preserve the error
            error = f"spawn_failed: {exc}"
            fe.write(error.encode("utf-8", "replace"))
            fe.flush()
        else:
            try:
                exit_code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    error = "kill_failed"
                exit_code = None
    duration = time.time() - started

    # Build a combined log (best effort; preserves failure output).
    parts: list[str] = []
    for p in (out_path, err_path):
        try:
            with open(p, "rb") as f:
                parts.append(f.read())
        except OSError:
            pass
    header = (
        f"$ {' '.join(cmd_list)}\n"
        f"# cwd={cwd or os.getcwd()} exit_code={exit_code} "
        f"timed_out={timed_out} duration_s={duration:.3f}\n"
    ).encode("utf-8", "replace")
    with open(comb_path, "wb") as f:
        f.write(header)
        for i, part in enumerate(parts):
            if i:
                f.write(b"\n----- stderr -----\n")
            f.write(part)

    return RunResult(
        command=cmd_list,
        exit_code=exit_code,
        timed_out=timed_out,
        duration_s=duration,
        stdout_log=out_path,
        stderr_log=err_path,
        combined_log=comb_path,
        cwd=str(cwd or os.getcwd()),
        error=error,
    )
