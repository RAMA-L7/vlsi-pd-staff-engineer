"""R3 regression for R2-05 / R2-08: managed execution and containment.

These tests use REAL short-lived child + grandchild processes, OS-level
sentinels and PID enumeration.  They are written against the ``run_managed``
API added to ``assistant/runner.py`` in R3 and therefore FAIL on the vulnerable
baseline (where that API does not exist / no descendant confirmation exists).

Run:
    <PY> -m pytest tests/test_r3_r205_containment.py -s -v

Every containment assertion is backed by printed PID evidence (root PID,
grandchild PID, survivor list, containment classification).

R-01 deterministic late-write gate: the worker publishes its own PID
immediately and then blocks on an on-disk gate.  ``delay`` is only a bounded
maximum wait for that gate; the LATE-WRITE sentinel is emitted ONLY when the
test opens the gate after ``run_managed`` returns.  This decouples PID
observation (short, bounded ``stay``) from sentinel timing, so no wall-clock
delay can neutralise ``assert not sentinel.exists()``: a surviving orphan
writes as soon as the gate is opened and the assertion fails as it did on the
frozen baseline.
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


@pytest.fixture(autouse=True)
def _isolated_identity_roots():
    """Keep each test's identity monitor scoped to its own process tree."""
    from assistant import r61_identity

    r61_identity.clear_roots()
    yield
    r61_identity.clear_roots()


WORKER_TEMPLATE = """\
import os, sys, time
sentinel = {sentinel!r}
pidfile = {pidfile!r}
ready = {ready!r}
gate = {gate!r}
print("PARTIAL-MARKER", flush=True)
# Single-writer PID provenance: the worker alone records its own PID, now.
with open(pidfile, "w", encoding="utf-8") as f:
    f.write(str(os.getpid()))
# Deterministic publication marker (R-02): written only after the durable PID.
with open(ready, "w", encoding="utf-8") as f:
    f.write(str(os.getpid()))
# Deterministic late-write gate.  The sentinel is written ONLY after the test
# opens `gate`; `delay` is merely a bounded cap on how long the worker waits,
# so it cannot turn into a wall-clock mask for a real orphan write.  A killed
# worker never reaches the write even if the gate is opened later.
_limit = time.monotonic() + {delay}
while not os.path.exists(gate):
    if time.monotonic() >= _limit:
        break
    time.sleep(0.01)
if os.path.exists(gate):
    with open(sentinel, "w", encoding="utf-8") as f:
        f.write("LATE-WRITE")
"""

WRAPPER_TEMPLATE = """\
import os, subprocess, sys, time
child = {child!r}
detached = {detached!r}
rootpidfile = {rootpidfile!r}
kw = {{}}
if detached and os.name == "nt":
    kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
proc = subprocess.Popen([sys.executable, child], **kw)
# R-02: publish wrapper PID for liveness-bounded marker waiting.
with open(rootpidfile, "w", encoding="utf-8") as f:
    f.write(str(os.getpid()))
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


def _pid_gone(pid: int | None, timeout_s: float = 1.0) -> bool:
    """Bounded re-observation of an independent liveness oracle.

    ``run_managed`` already confirms termination with two stable polls.  Re-checking
    the same PID an instant later can observe an exited-but-not-yet-reaped handle
    (or a fast PID reuse), so give the durable "gone" state a bounded window.  The
    assertion is unchanged: the PID must be gone, only the observation is settled.
    """
    limit = time.monotonic() + timeout_s
    while _pid_alive(pid):
        if time.monotonic() >= limit:
            return False
        time.sleep(0.01)
    return True


def _awaiting_descendant_probe(pidfile: Path, latched: dict):
    """Block on the worker's deterministic publication marker, then snapshot.

    The Windows python-launcher re-exec chain has variable depth and its startup
    time is host-load dependent.  A fixed wall-clock bound (the prior 0.8 s)
    could expire before the worker published its PID, leaving the only
    post-timeout snapshot too early: the durable gpid was then absent from
    ``descendant_pids`` or the pidfile was still empty when the test read it.

    Synchronisation is on definite events, not a wall-clock guess:

      * the wrapper publishes its own PID to ``<tree>/root.pid`` just after
        ``Popen``; that process is the liveness authority for the wait;
      * the worker writes ``<pidfile>.ready`` immediately after writing
        ``pidfile`` (single writer, fixed order); the probe blocks until the
        marker exists, then until the PID is in a fresh OS snapshot.

    The wait ends fail-closed if the wrapper itself exits (it sleeps only for
    the test's ``stay`` seconds).  No larger arbitrary timeout, retry, skip,
    xfail or assertion change is introduced.
    """
    from assistant.runner import _snapshot_pids

    ready = Path(str(pidfile) + ".ready")
    wrapper_pidfile = pidfile.parent / "root.pid"

    def wrapper_alive() -> bool:
        wrapper = _read_pid(wrapper_pidfile)
        # Before the wrapper publishes, keep waiting rather than fail early.
        return _pid_alive(wrapper) if wrapper is not None else True

    def probe():
        snap = _snapshot_pids()
        if not latched["attempted"]:
            latched["attempted"] = True
            child = _read_pid(pidfile)
            while not ready.exists():
                if not wrapper_alive() or (child is not None and not _pid_alive(child)):
                    break  # fail closed: marker never published / worker died
                time.sleep(0.01)
                child = _read_pid(pidfile)
            child = _read_pid(pidfile)
            while not (child is not None and child in snap):
                if not wrapper_alive() or (child is not None and not _pid_alive(child)):
                    break  # fail closed: PID never became OS-observable
                time.sleep(0.01)
                snap = _snapshot_pids()
                child = _read_pid(pidfile)
            latched["observed"] = (
                ready.exists() and child is not None and child in snap
            )
        return snap

    return probe


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


def _gate_path(tmp_path: Path) -> Path:
    """Deterministic on-disk gate that releases the worker's sentinel write."""
    return tmp_path / "tree" / "SENTINEL_GATE"


def _open_gate(tmp_path: Path) -> None:
    """Release the sentinel write AFTER containment has been verified.

    The worker has already been confirmed dead at this point, so opening the
    gate cannot manufacture a write; it only proves the orphan is genuinely
    gone.  A containment defect that left the orphan alive would write here
    and the unchanged ``not sentinel.exists()`` assertion would fail.
    """
    gate = _gate_path(tmp_path)
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text("open", encoding="utf-8")


def _build_tree(tmp_path: Path, *, detached: bool, stay: float, delay: float):
    d = tmp_path / "tree"
    d.mkdir(parents=True, exist_ok=True)
    worker = d / "worker.py"
    wrapper = d / "wrapper.py"
    sentinel = d / "LATE_WRITE_SENTINEL.txt"
    gchild_pidfile = d / "grandchild.pid"
    worker_ready = Path(str(gchild_pidfile) + ".ready")
    root_pidfile = d / "root.pid"
    worker.write_text(
        WORKER_TEMPLATE.format(
            sentinel=str(sentinel), pidfile=str(gchild_pidfile),
            ready=str(worker_ready), gate=str(_gate_path(tmp_path)), delay=delay,
        ),
        encoding="utf-8",
    )
    wrapper.write_text(
        WRAPPER_TEMPLATE.format(child=str(worker), detached=detached, stay=stay,
                                rootpidfile=str(root_pidfile)),
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
    result = run_managed(
        cmd, log_dir=str(tmp_path / "logs"), name="tree", timeout=0.4,
        pid_probe=_awaiting_descendant_probe(gchild_pidfile, {"attempted": False}),
    )
    wall = time.monotonic() - wall_start
    gpid = _read_pid(gchild_pidfile)
    _evidence("child+grandchild", result, gpid)
    _open_gate(tmp_path)  # worker is confirmed dead; the gate cannot resurrect it

    assert result.timed_out is True
    assert result.containment == CONTAINMENT_TREE_CONFIRMED
    assert result.ran is False
    assert result.exit_code is None
    assert gpid is not None
    assert gpid in result.descendant_pids, "grandchild was never observed as a descendant"
    assert _pid_gone(result.pid) is True, "root PID still alive"
    assert _pid_gone(gpid) is True, "grandchild PID still alive"
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

    CANCEL_AT = [time.monotonic() + 1.0]
    result = run_managed(
        cmd, log_dir=str(tmp_path / "logs"), name="cancel",
        deadline=time.monotonic() + 30.0, cancelled=cancelled,
        pid_probe=_awaiting_descendant_probe(gchild_pidfile, {"attempted": False}),
    )
    gpid = _read_pid(gchild_pidfile)
    _evidence("cancel-midflight", result, gpid)
    _open_gate(tmp_path)  # worker is confirmed dead; the gate cannot resurrect it

    assert result.cancelled is True
    assert "cancelled" in result.error
    assert result.containment == CONTAINMENT_TREE_CONFIRMED
    assert result.ran is False
    assert result.timed_out is False, "cancellation must not be mislabelled as timeout"
    assert gpid is not None and gpid in result.descendant_pids
    assert _pid_gone(result.pid) is True
    assert _pid_gone(gpid) is True
    time.sleep(2.5)
    assert not sentinel.exists(), "grandchild survived cancellation and wrote its sentinel"


def test_wrapper_exit_orphan_detected(tmp_path):
    """Wrapper exits cleanly but leaves a DETACHED descendant -> orphan, not ran."""
    cmd, sentinel, gchild_pidfile = _build_tree(
        # stay stays bounded; `delay` is only the gate-wait cap, never a
        # wall-clock mask for the sentinel write (R-01).
        tmp_path, detached=True, stay=1.0, delay=2.0,
    )
    result = run_managed(
        cmd, log_dir=str(tmp_path / "logs"), name="orphan",
        deadline=time.monotonic() + 10.0,
        pid_probe=_awaiting_descendant_probe(gchild_pidfile, {"attempted": False}),
    )
    gpid = _read_pid(gchild_pidfile)
    _evidence("wrapper-exit-orphan", result, gpid)
    _open_gate(tmp_path)  # orphan is confirmed dead; a survivor would write here

    assert result.containment == CONTAINMENT_WRAPPER_ORPHAN
    assert result.ran is False, "wrapper-exit orphan must never be reported as ran"
    assert result.timed_out is False
    assert gpid is not None
    assert gpid in result.descendant_pids, "orphan was not observed before wrapper exit"
    assert _pid_gone(gpid) is True, "orphan PID survived managed run"
    time.sleep(2.0)
    assert not sentinel.exists(), "orphaned grandchild wrote its sentinel after clean wrapper exit"


@pytest.mark.skipif(not IS_WINDOWS, reason="DETACHED_PROCESS is a Windows creation flag")
def test_detached_process_contained_on_timeout(tmp_path):
    """A DETACHED grandchild spawned by a live wrapper is still contained."""
    cmd, sentinel, gchild_pidfile = _build_tree(
        tmp_path, detached=True, stay=5.0, delay=2.0,
    )
    result = run_managed(
        cmd, log_dir=str(tmp_path / "logs"), name="detached", timeout=0.4,
        pid_probe=_awaiting_descendant_probe(gchild_pidfile, {"attempted": False}),
    )
    gpid = _read_pid(gchild_pidfile)
    _evidence("detached-timeout", result, gpid)
    _open_gate(tmp_path)  # worker is confirmed dead; the gate cannot resurrect it

    assert result.timed_out is True
    assert result.containment == CONTAINMENT_TREE_CONFIRMED
    assert result.ran is False
    assert gpid is not None and gpid in result.descendant_pids
    assert _pid_gone(gpid) is True
    time.sleep(2.5)
    assert not sentinel.exists()


def test_termination_failure_injection_is_reported_unconfirmed(tmp_path):
    """A PID that cannot be killed must be reported, never claimed as prevented."""
    cmd, sentinel, gchild_pidfile = _build_tree(
        tmp_path, detached=False, stay=5.0, delay=2.0,
    )
    FAKE_PID = 9_999_991

    # The wrapper publishes its own PID to root.pid at startup; the injected
    # probe then fabricates an un-killable descendant so confirmation MUST fail
    # honestly.
    wrapper_dir = tmp_path / "tree"
    wrapper_script = wrapper_dir / "wrapper.py"
    original = wrapper_script.read_text(encoding="utf-8")
    original = original.replace(
        'proc = subprocess.Popen([sys.executable, child], **kw)',
        'proc = subprocess.Popen([sys.executable, child], **kw)\n'
        'with open(r"' + str(wrapper_dir / "root.pid") + '", "w", encoding="utf-8") as f:\n'
        '    f.write(str(os.getpid()))',
    )
    wrapper_script.write_text(original, encoding="utf-8")
    root_pidfile = wrapper_dir / "root.pid"

    def injected_probe() -> dict[int, int]:
        snap = real_snapshot()
        # The wrapper writes root.pid just after Popen; bound-wait for that
        # durable value so the fake un-killable descendant is always injected.
        limit = time.monotonic() + 1.0
        while True:
            try:
                root = int(root_pidfile.read_text(encoding="utf-8").strip())
                snap[FAKE_PID] = root
                break
            except (OSError, ValueError):
                if time.monotonic() >= limit:
                    break  # fail honestly: no fake PID injected for the caller to assert
                time.sleep(0.01)
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
    result = run_managed(cmd, log_dir=str(tmp_path / "logs"), name="partial", timeout=1.5)
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
