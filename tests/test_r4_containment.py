"""R4 remediation regression: containment-primitive hardening (A5-B / A5-C).

These tests are written against the R4 API in ``assistant/runner.py`` and are
expected to FAIL on the vulnerable R3 baseline (missing symbols/fields, plain
WSL error, argv[0]-only WSL detection, single-oracle liveness confirmation) and
PASS after the R4 fix.

Oracles are independent of the code under test:
  * PID liveness uses raw ``OpenProcess``/``GetExitCodeProcess`` (Windows) or
    ``os.kill(pid, 0)`` + ``/proc`` state (POSIX), NOT runner's snapshot helpers.
  * Late mutation uses real files written by real threads after a real delay.
  * Workspace hashing is done by a local helper over a temp directory.

Run:
    <PY> -m pytest tests/test_r4_containment.py -s -v
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

from assistant.runner import (
    CONTAINMENT_TREE_CONFIRMED,
    CONTAINMENT_TREE_UNCONFIRMED,
    CONTAINMENT_UNSUPPORTED,
    REASON_WSL_UNCONFIRMABLE,
    ThreadContainment,
    run_managed,
)

IS_WINDOWS = os.name == "nt"


# ---------------------------------------------------------------------------
# Independent oracles / helpers
# ---------------------------------------------------------------------------

def _hash_dir(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root)).replace("\\", "/")
            out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _pid_alive(pid: int | None) -> bool:
    """Independent PID oracle (does not import runner's confirmation helpers)."""
    if not pid or pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32")
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        k32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


CHILD_TEMPLATE = """\
import os, sys, time
open({pidfile!r}, "w", encoding="utf-8").write(str(os.getpid()))
time.sleep({delay})
open({sentinel!r}, "w", encoding="utf-8").write("LATE-WRITE")
"""


WRAPPER_TEMPLATE = """\
import os, subprocess, sys, time
child = {child!r}
kw = {{}}
if os.name == "nt":
    kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
else:
    kw["start_new_session"] = True
p = subprocess.Popen([sys.executable, child], **kw)
time.sleep({stay})
"""


def _build_tree(tmp_path: Path, *, stay: float, delay: float):
    d = tmp_path / "tree"
    d.mkdir(parents=True, exist_ok=True)
    sentinel = d / "LATE.txt"
    pidfile = d / "child.pid"
    child = d / "child.py"
    wrapper = d / "wrapper.py"
    child.write_text(
        CHILD_TEMPLATE.format(pidfile=str(pidfile), sentinel=str(sentinel), delay=delay),
        encoding="utf-8",
    )
    wrapper.write_text(
        WRAPPER_TEMPLATE.format(child=str(child), stay=stay), encoding="utf-8"
    )
    return [sys.executable, str(wrapper)], sentinel, pidfile


# ---------------------------------------------------------------------------
# A5-B -- WSL classification must be UNAMBIGUOUS in the API
# ---------------------------------------------------------------------------

def test_wsl_result_exposes_unambiguous_contract(tmp_path):
    """containment/error/combined log must all name the WSL reason (A5-B)."""
    fake_wsl = tmp_path / "wsl.exe"
    shutil.copy2(sys.executable, fake_wsl)
    cmd = [str(fake_wsl), "-c", "import time; time.sleep(10)"]
    result = run_managed(
        cmd, log_dir=str(tmp_path / "logs"), name="wsl-api", timeout=0.4,
    )

    assert result.containment == CONTAINMENT_UNSUPPORTED
    assert result.wsl_uncontained is True
    assert result.containment_reason == REASON_WSL_UNCONFIRMABLE
    assert result.is_host_contained is False
    assert result.authorization_safe is False
    assert result.ran is False
    assert REASON_WSL_UNCONFIRMABLE in result.error

    combined = Path(result.combined_log).read_text(encoding="utf-8", errors="replace")
    assert f"containment_reason={REASON_WSL_UNCONFIRMABLE}" in combined
    assert "wsl_uncontained=True" in combined
    assert REASON_WSL_UNCONFIRMABLE in combined


def test_wsl_orphan_path_keeps_canonical_token(tmp_path):
    """R3's WSL+orphan branch dropped the WSL marker; R4 must keep it."""
    d = tmp_path / "wsltree"
    d.mkdir(parents=True, exist_ok=True)
    pidfile = d / "child.pid"
    child = d / "child.py"
    child.write_text(
        f"import os, time\nopen({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(5)\n",
        encoding="utf-8",
    )
    wrapper = d / "wrapper.py"
    wrapper.write_text(
        f"""import os, subprocess, sys, time
kw = {{}}
if os.name == 'nt':
    kw['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
else:
    kw['start_new_session'] = True
subprocess.Popen([sys.executable, {str(child)!r}], **kw)
time.sleep(0.4)
""",
        encoding="utf-8",
    )
    # Real interpreter runs the wrapper; a trailing wsl.exe token makes this a
    # WSL-classified invocation without needing a runnable copied interpreter.
    result = run_managed(
        [sys.executable, str(wrapper), "wsl.exe"], log_dir=str(tmp_path / "logs"),
        name="wsl-orphan", deadline=time.monotonic() + 10.0,
    )
    assert result.containment == CONTAINMENT_UNSUPPORTED
    assert result.containment_reason == REASON_WSL_UNCONFIRMABLE
    assert result.wsl_uncontained is True
    assert "wrapper_exit_orphan" in result.error
    assert REASON_WSL_UNCONFIRMABLE in result.error, (
        "WSL orphan must still name the unconfirmable reason"
    )


def test_indirect_wsl_invocation_still_classified_unsupported(tmp_path):
    """A wsl.exe token anywhere in argv must trigger classification (A5-B)."""
    # argv[0] is python; a later token is wsl.exe. R3 checked only argv[0] and
    # would wrongly report containment=clean for this invocation.
    cmd = [sys.executable, "-c", "import time; time.sleep(0.15)", "wsl.exe"]
    result = run_managed(
        cmd, log_dir=str(tmp_path / "logs"), name="wsl-indirect",
        deadline=time.monotonic() + 10.0,
    )
    assert result.containment == CONTAINMENT_UNSUPPORTED
    assert result.containment_reason == REASON_WSL_UNCONFIRMABLE
    assert result.wsl_uncontained is True
    assert result.authorization_safe is False


# ---------------------------------------------------------------------------
# A5-C -- ThreadContainment registry / bounded wait / re-hash / fail closed
# ---------------------------------------------------------------------------

def test_thread_registry_tracks_worker_lifetimes(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    tc = ThreadContainment(snapshot_fn=lambda: _hash_dir(ws))
    stop = threading.Event()

    def worker():
        while not stop.wait(0.01):
            pass

    t = threading.Thread(target=worker, name="tracked-worker")
    t.start()
    try:
        tc.register(t, name="tracked-worker")
        assert len(tc.workers()) == 1
        active = tc.active_workers()
        assert len(active) == 1
        assert active[0].name == "tracked-worker"
        assert active[0].alive is True
    finally:
        stop.set()
        t.join(timeout=2)
    tc.unregister(t)
    assert tc.workers() == ()


def test_thread_registry_detects_late_write_after_bounded_wait(tmp_path):
    """A worker writing AFTER rollback must be caught by the re-hash."""
    ws = tmp_path / "ws"
    ws.mkdir()
    tc = ThreadContainment(snapshot_fn=lambda: _hash_dir(ws))

    def worker():
        time.sleep(0.25)
        (ws / "LATE_WORKER_WRITE.txt").write_text("late", encoding="utf-8")

    t = threading.Thread(target=worker, name="late-writer")
    t.start()
    tc.register(t, name="late-writer")
    before = _hash_dir(ws)  # post-rollback baseline: empty workspace

    result = tc.quiesce(before=before, wait_s=1.0)

    assert result.contained is False
    assert "LATE_WORKER_WRITE.txt" in result.late_mutations
    assert result.uncontained_workers == (), "worker finished inside the bound"
    assert 0.20 <= result.waited_s < 1.2
    assert t.is_alive() is False  # independent liveness oracle


def test_thread_registry_reports_worker_still_alive(tmp_path):
    """A worker outliving the bound must surface in uncontained_workers."""
    ws = tmp_path / "ws"
    ws.mkdir()
    tc = ThreadContainment(snapshot_fn=lambda: _hash_dir(ws))
    stop = threading.Event()

    def worker():
        while not stop.wait(0.02):
            pass

    t = threading.Thread(target=worker, name="stubborn-worker")
    t.start()
    tc.register(t, name="stubborn-worker")
    before = _hash_dir(ws)
    try:
        result = tc.quiesce(before=before, wait_s=0.3)
        assert result.contained is False
        assert "stubborn-worker" in result.uncontained_workers
        assert result.late_mutations == ()
        assert result.waited_s < 1.0, "wait must be bounded by wait_s"
        assert t.is_alive() is True, "independent oracle: worker really survived"
    finally:
        stop.set()
        t.join(timeout=2)


# ---------------------------------------------------------------------------
# Liveness-confirmation hardening + tree regressions (independent oracles)
# ---------------------------------------------------------------------------

def test_tree_termination_confirmed_with_independent_pid_oracle(tmp_path):
    """Regression: whole tree killed and independently confirmed dead."""
    cmd, sentinel, pidfile = _build_tree(tmp_path, stay=5.0, delay=2.0)
    result = run_managed(
        cmd, log_dir=str(tmp_path / "logs"), name="tree-r4", timeout=0.4,
    )
    child_pid = _read_pid(pidfile)
    assert result.timed_out is True
    assert result.containment == CONTAINMENT_TREE_CONFIRMED
    assert result.containment_reason == "tree_terminated_and_confirmed"
    assert result.wsl_uncontained is False
    assert result.is_host_contained is True
    assert child_pid is not None and child_pid in result.descendant_pids
    assert _pid_alive(result.pid) is False
    assert _pid_alive(child_pid) is False
    time.sleep(2.5)
    assert not sentinel.exists(), "descendant wrote its sentinel after return"


def test_independent_alive_probe_blocks_false_confirmation(tmp_path):
    """A PID reported alive by the independent oracle must stay a survivor."""
    cmd, _sentinel, _pidfile = _build_tree(tmp_path, stay=5.0, delay=2.0)
    result = run_managed(
        cmd, log_dir=str(tmp_path / "logs"), name="alive-probe",
        timeout=0.4, confirm_grace_s=0.3,
        pid_alive_probe=lambda pid: True,  # everything still "alive"
    )
    assert result.containment == CONTAINMENT_TREE_UNCONFIRMED
    assert "termination_unconfirmed" in result.error
    assert result.ran is False
    assert result.authorization_safe is False


def test_empty_snapshot_cannot_prove_termination(tmp_path):
    """An empty PID snapshot plus a live independent oracle must fail closed."""
    result = run_managed(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        log_dir=str(tmp_path / "logs"), name="empty-snap",
        timeout=0.3, confirm_grace_s=0.3,
        pid_probe=lambda: {}, pid_alive_probe=lambda pid: True,
    )
    assert result.containment == CONTAINMENT_TREE_UNCONFIRMED
    assert "termination_unconfirmed" in result.error
    assert result.is_host_contained is False
