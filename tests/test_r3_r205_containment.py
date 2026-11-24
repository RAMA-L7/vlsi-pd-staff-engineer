"""R3 regression for R2-05 / R2-08: managed execution and containment.

These tests use REAL short-lived child + grandchild processes, OS-level
sentinels and PID enumeration.  They are written against the ``run_managed``
API added to ``assistant/runner.py`` in R3 and therefore FAIL on the vulnerable
baseline (where that API does not exist / no descendant confirmation exists).

Run:
    <PY> -m pytest tests/test_r3_r205_containment.py -s -v

Every containment assertion is backed by printed PID evidence (root PID,
grandchild PID, survivor list, containment classification).
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest

from assistant.runner import (
    CONTAINMENT_TREE_CONFIRMED,
    CONTAINMENT_TREE_UNCONFIRMED,
    CONTAINMENT_WRAPPER_ORPHAN,
    CONTAINMENT_UNSUPPORTED,
    run_command,
    run_managed,
)

IS_WINDOWS = os.name == "nt"

WORKER_TEMPLATE = """\
import os, sys, time
sentinel = {sentinel!r}
pidfile = {pidfile!r}
print("PARTIAL-MARKER", flush=True)
with open(pidfile, "w", encoding="utf-8") as f:
    f.write(str(os.getpid()))
time.sleep({delay})
with open(sentinel, "w", encoding="utf-8") as f:
    f.write("LATE-WRITE")
"""

WRAPPER_TEMPLATE = """\
import os, subprocess, sys, time
child = {child!r}
pidfile = {pidfile!r}
detached = {detached!r}
kw = {{}}
if detached and os.name == "nt":
    kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
proc = subprocess.Popen([sys.executable, child], **kw)
with open(pidfile, "w", encoding="utf-8") as f:
    f.write(str(proc.pid))
print("WRAPPER-STARTED", flush=True)
time.sleep({stay})
"""


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32")
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        k32.CloseHandle(h)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _evidence(label: str, result, grandchild_pid: int | None) -> None:
    print(
        f"EVIDENCE {label}: root_pid={result.pid} "
        f"grandchild_pid={grandchild_pid} "
        f"descendant_pids={list(result.descendant_pids)} "
        f"survivors_in_detail={result.termination_detail[-160:]!r} "
        f"containment={result.containment!r} ran={result.ran} "
        f"timed_out={result.timed_out} cancelled={result.cancelled} "
        f"exit_code={result.exit_code} duration_s={result.duration_s:.3f}",
        flush=True,
    )


def _build_tree(tmp_path: Path, *, detached: bool, stay: float, delay: float):
    d = tmp_path / "tree"
    d.mkdir(parents=True, exist_ok=True)
    worker = d / "worker.py"
    wrapper = d / "wrapper.py"
    sentinel = d / "LATE_WRITE_SENTINEL.txt"
    gchild_pidfile = d / "grandchild.pid"
    worker.write_text(
        WORKER_TEMPLATE.format(sentinel=str(sentinel), pidfile=str(gchild_pidfile), delay=delay),
        encoding="utf-8",
    )
    wrapper.write_text(
        WRAPPER_TEMPLATE.format(child=str(worker), pidfile=str(gchild_pidfile),
                                detached=detached, stay=stay),
        encoding="utf-8",
    )
    return [sys.executable, str(wrapper)], sentinel, gchild_pidfile


# ---------------------------------------------------------------------------

def test_child_grandchild_tree_terminated_and_confirmed(tmp_path):
    """Timeout must kill child+grandchild and CONFIRM both PIDs are dead."""
    cmd, sentinel, gchild_pidfile = _build_tree(
        tmp_path, detached=False, stay=5.0, delay=2.0,
    )
    wall_start = time.monotonic()
    result = run_managed(cmd, log_dir=str(tmp_path / "logs"), name="tree", timeout=0.4)
    wall = time.monotonic() - wall_start
    gpid = _read_pid(gchild_pidfile)
    _evidence("child+grandchild", result, gpid)

    assert result.timed_out is True
    assert result.containment == CONTAINMENT_TREE_CONFIRMED
    assert result.ran is False
    assert result.exit_code is None
    assert gpid is not None
    assert gpid in result.descendant_pids, "grandchild was never observed as a descendant"
    assert _pid_alive(result.pid) is False, "root PID still alive"
    assert _pid_alive(gpid) is False, "grandchild PID still alive"
    time.sleep(2.5)
    assert not sentinel.exists(), "grandchild wrote its sentinel AFTER managed run returned"
    # measured duration uses the monotonic clock and tracks wall time
    assert 0.3 <= result.duration_s < 3.0
    assert abs(result.duration_s - wall) < 1.0


def test_cancellation_midflight_terminates_whole_tree(tmp_path):
    """Operator cancellation mid-flight must terminate+confirm, not wait for exit."""
    cmd, sentinel, gchild_pidfile = _build_tree(
        tmp_path, detached=False, stay=5.0, delay=2.0,
    )
    fired_at = {"t": None}

    def cancelled() -> bool:
        if fired_at["t"] is None and time.monotonic() >= CANCEL_AT[0]:
            fired_at["t"] = time.monotonic()
        return fired_at["t"] is not None

    CANCEL_AT = [time.monotonic() + 0.3]
    result = run_managed(
        cmd, log_dir=str(tmp_path / "logs"), name="cancel",
        deadline=time.monotonic() + 30.0, cancelled=cancelled,
    )
    gpid = _read_pid(gchild_pidfile)
    _evidence("cancel-midflight", result, gpid)

    assert result.cancelled is True
    assert "cancelled" in result.error
    assert result.containment == CONTAINMENT_TREE_CONFIRMED
    assert result.ran is False
    assert result.timed_out is False, "cancellation must not be mislabelled as timeout"
    assert gpid is not None and gpid in result.descendant_pids
    assert _pid_alive(result.pid) is False
    assert _pid_alive(gpid) is False
    time.sleep(2.5)
    assert not sentinel.exists(), "grandchild survived cancellation and wrote its sentinel"


def test_wrapper_exit_orphan_detected(tmp_path):
    """Wrapper exits cleanly but leaves a DETACHED descendant -> orphan, not ran."""
    cmd, sentinel, gchild_pidfile = _build_tree(
        tmp_path, detached=True, stay=0.25, delay=1.5,
    )
    result = run_managed(
        cmd, log_dir=str(tmp_path / "logs"), name="orphan",
        deadline=time.monotonic() + 10.0,
    )
    gpid = _read_pid(gchild_pidfile)
    _evidence("wrapper-exit-orphan", result, gpid)

    assert result.containment == CONTAINMENT_WRAPPER_ORPHAN
    assert result.ran is False, "wrapper-exit orphan must never be reported as ran"
    assert result.timed_out is False
    assert gpid is not None
    assert gpid in result.descendant_pids, "orphan was not observed before wrapper exit"
    assert _pid_alive(gpid) is False, "orphan PID survived managed run"
    time.sleep(2.0)
    assert not sentinel.exists(), "orphaned grandchild wrote its sentinel after clean wrapper exit"


@pytest.mark.skipif(not IS_WINDOWS, reason="DETACHED_PROCESS is a Windows creation flag")
def test_detached_process_contained_on_timeout(tmp_path):
    """A DETACHED grandchild spawned by a live wrapper is still contained."""
    cmd, sentinel, gchild_pidfile = _build_tree(
        tmp_path, detached=True, stay=5.0, delay=2.0,
    )
    result = run_managed(cmd, log_dir=str(tmp_path / "logs"), name="detached", timeout=0.4)
    gpid = _read_pid(gchild_pidfile)
    _evidence("detached-timeout", result, gpid)

    assert result.timed_out is True
    assert result.containment == CONTAINMENT_TREE_CONFIRMED
    assert result.ran is False
    assert gpid is not None and gpid in result.descendant_pids
    assert _pid_alive(gpid) is False
    time.sleep(2.5)
    assert not sentinel.exists()


def test_termination_failure_injection_is_reported_unconfirmed(tmp_path):
    """A PID that cannot be killed must be reported, never claimed as prevented."""
    cmd, sentinel, gchild_pidfile = _build_tree(
        tmp_path, detached=False, stay=5.0, delay=2.0,
    )
    FAKE_PID = 9_999_991

    # The wrapper publishes its own PID at startup; the injected probe then
    # fabricates an un-killable descendant so confirmation MUST fail honestly.
    wrapper_dir = tmp_path / "tree"
    wrapper_script = wrapper_dir / "wrapper.py"
    original = wrapper_script.read_text(encoding="utf-8")
    original = original.replace(
        'with open(pidfile, "w", encoding="utf-8") as f:\n    f.write(str(proc.pid))',
        'with open(pidfile, "w", encoding="utf-8") as f:\n    f.write(str(proc.pid))\n'
        'with open(r"' + str(wrapper_dir / "root.pid") + '", "w", encoding="utf-8") as f:\n'
        '    f.write(str(os.getpid()))',
    )
    wrapper_script.write_text(original, encoding="utf-8")
    root_pidfile = wrapper_dir / "root.pid"

    def injected_probe() -> dict[int, int]:
        snap = real_snapshot()
        try:
            root = int(root_pidfile.read_text(encoding="utf-8").strip())
            snap[FAKE_PID] = root
        except (OSError, ValueError):
            pass
        return snap

    from assistant import runner as _runner
    real_snapshot = _runner._snapshot_pids

    result = run_managed(
        cmd, log_dir=str(tmp_path / "logs"), name="injected", timeout=0.4,
        pid_probe=injected_probe, confirm_grace_s=1.0,
    )
    _evidence("termination-failure-injection", result, _read_pid(gchild_pidfile))
    print(f"EVIDENCE injected fake_pid={FAKE_PID}", flush=True)

    assert result.containment == CONTAINMENT_TREE_UNCONFIRMED
    assert result.ran is False
    assert "termination_unconfirmed" in result.error
    assert str(FAKE_PID) in result.error, "unconfirmed survivor PID must be reported explicitly"
    assert "survivors" in result.termination_detail


def test_partial_output_preserved_on_termination(tmp_path):
    """Output written before a timeout kill must survive in the log files."""
    cmd, sentinel, gchild_pidfile = _build_tree(
        tmp_path, detached=False, stay=5.0, delay=2.0,
    )
    result = run_managed(cmd, log_dir=str(tmp_path / "logs"), name="partial", timeout=0.4)
    _evidence("partial-output", result, _read_pid(gchild_pidfile))

    assert result.timed_out is True
    assert os.path.isfile(result.stdout_log)
    assert os.path.isfile(result.stderr_log)
    assert os.path.isfile(result.combined_log)
    stdout = Path(result.stdout_log).read_text(encoding="utf-8", errors="replace")
    assert "PARTIAL-MARKER" in stdout, "partial stdout was lost on termination"
    combined = Path(result.combined_log).read_text(encoding="utf-8", errors="replace")
    assert f"containment={CONTAINMENT_TREE_CONFIRMED}" in combined
    assert "timed_out=True" in combined


def test_wsl_descendants_classified_unsupported(tmp_path):
    """A wsl.exe tool cannot be confirmed from the host -> unsupported, not ran."""
    fake_wsl = tmp_path / "wsl.exe"
    shutil.copy2(sys.executable, fake_wsl)
    cmd = [str(fake_wsl), "-c", "import time; time.sleep(10)"]
    result = run_managed(cmd, log_dir=str(tmp_path / "logs"), name="wsl", timeout=0.4)
    _evidence("wsl-classification", result, None)

    assert result.containment == CONTAINMENT_UNSUPPORTED
    assert result.ran is False
    assert "wsl" in result.error.lower()


def test_legacy_run_command_defaults_preserved(tmp_path):
    """Non-managed callers keep their exact prior observable contract."""
    result = run_command(
        [sys.executable, "-c", "print('legacy-ok')"],
        log_dir=str(tmp_path / "logs"), name="legacy",
    )
    assert result.exit_code == 0
    assert result.ran is True
    assert result.containment == "none"
    assert result.cancelled is False
    assert result.termination == "none"
    assert "legacy-ok" in Path(result.combined_log).read_text(encoding="utf-8")
