"""R4/A5-A regression: managed execution must be on a REAL production path.

The baseline defect (FINDINGS.md A5-A) was that ``VerificationContext.spawn()``
had ZERO call sites and ``run_managed`` was reachable only through it, so the
R2-08 claim "integrated into production spawn()" was inert.  These tests drive
the public ``EcoManager.run_experiment(verification_tools=...)`` production
entry point and assert that a managed tool actually ran, that uncontained
results fail closed, and that the R4 quiesce registry detects an uncooperative
worker.  No test calls ``run_managed`` directly as the subject under test.

Run:
    <PY> -m pytest tests/test_r4_managed_execution.py -s -v
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

from assistant.controller import GateController
from assistant.eco import (
    EcoChangeScope,
    EcoManager,
    EcoStage,
    ManagedToolSpec,
    ProtectedMetric,
    VerificationContext,
)
from assistant.runner import (
    CONTAINMENT_CLEAN,
    CONTAINMENT_TREE_CONFIRMED,
    CONTAINMENT_TREE_UNCONFIRMED,
    CONTAINMENT_UNSUPPORTED,
    REASON_WSL_UNCONFIRMABLE,
    RunResult,
)

CHECKS = ("eco_lec", "eco_sta", "eco_physical_drc")


# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------

def _make_logs(tmp_path: Path, name: str) -> tuple[str, str, str]:
    combined = tmp_path / "logs" / f"{name}.log"
    out = tmp_path / "logs" / f"{name}.stdout.log"
    err = tmp_path / "logs" / f"{name}.stderr.log"
    for path in (combined, out, err):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("managed tool output\n", encoding="utf-8")
    return str(combined), str(out), str(err)


def _result(tmp_path: Path, name: str, **kw) -> RunResult:
    combined, out, err = _make_logs(tmp_path, name)
    base = dict(
        command=[sys.executable, "-c", "print('ok')"],
        exit_code=0,
        timed_out=False,
        duration_s=0.01,
        stdout_log=out,
        stderr_log=err,
        combined_log=combined,
        cwd=str(tmp_path),
        containment=CONTAINMENT_CLEAN,
        containment_reason="clean_exit",
        error="",
    )
    base.update(kw)
    return RunResult(**base)


@pytest.fixture
def env(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    source = ws / "design.v"
    source.write_text("module d; endmodule\n", encoding="utf-8")
    ctl = GateController(str(tmp_path / "state.json"))
    eco = EcoManager(
        str(ws), ctl,
        checkpoint_dir=str(tmp_path / "ckpt"),
        experiment_log=str(tmp_path / "experiments.json"),
    )
    return ws, ctl, eco, source


def _scope(source: Path) -> EcoChangeScope:
    # Physical implementation present => all three checks are real managed
    # tools (no NOT_APPLICABLE shortcut), exercising the full production path.
    return EcoChangeScope(
        "logic_optimization", EcoStage.ROUTE, True, str(source)
    )


def _specs() -> list[ManagedToolSpec]:
    return [
        ManagedToolSpec(
            check_id=cid,
            command=[sys.executable, "-c", "print('managed-tool ok')"],
            reason=f"managed {cid}",
            timeout_s=2.0,
        )
        for cid in CHECKS
    ]


def _run(eco: EcoManager, source: Path, *, tools=None, verify=None, timeout=None):
    return eco.run_experiment(
        experiment_id="r4-managed-1",
        description="managed execution integration",
        change_class="logic_optimization",
        changed_files=["design.v"],
        apply_change=lambda: source.write_text(
            "module d; wire x; endmodule\n", encoding="utf-8"
        ),
        evaluate=lambda: {"wns": 0.2},
        protected_metrics=[ProtectedMetric("wns", 0.0, direction="higher_is_better")],
        verification_tools=tools,
        verify=verify,
        verification_timeout_s=timeout,
        change_scope=_scope(source),
    )


def _patch_runner(monkeypatch, result_factory):
    """Replace only runner.run_managed; spawn still imports it at call time."""
    import assistant.runner as runner

    calls: list[dict] = []

    def fake(command, **kw):
        calls.append({"command": command, **kw})
        return result_factory(kw.get("name", "tool"))

    monkeypatch.setattr(runner, "run_managed", fake)
    return calls


# ---------------------------------------------------------------------------
# A5-A -- a NON-TEST production code path invokes managed execution
# ---------------------------------------------------------------------------

def test_real_production_path_uses_managed_spawn(env, tmp_path, monkeypatch):
    ws, _ctl, eco, source = env
    calls = _patch_runner(monkeypatch, lambda name: _result(tmp_path, name))

    result = _run(eco, source, tools=_specs())

    # The call chain is run_experiment -> managed_tool_verification ->
    # VerificationContext.spawn -> runner.run_managed.  spawn is the ONLY
    # framework tool-launch API; these assertions prove it was really used by
    # the production entry point, not by a test calling run_managed directly.
    assert result.status == "PASS", result.reason
    assert len(calls) == len(CHECKS), calls
    assert {c.get("name") for c in calls} == set(CHECKS)
    assert all(c.get("deadline") is not None for c in calls)
    assert all(callable(c.get("cancelled")) for c in calls)
    assert all(c.get("cwd") == str(ws) for c in calls)
    assert result.verification["managed_execution"]["classification"] == (
        "hard-contained-direct-tree"
    )
    assert result.verification["managed_execution"]["authorization_safe"] is True
    for cid in CHECKS:
        evidence = _ctl.latest_evidence(cid)
        assert evidence is not None
        # Evidence binds the command that actually ran (returned by the runner),
        # not the declarative spec -- no fabricated provenance.
        assert evidence.command == [sys.executable, "-c", "print('ok')"]


def test_verification_tools_and_verify_are_mutually_exclusive(env):
    ws, _ctl, eco, source = env
    res = _run(eco, source, tools=_specs(), verify=lambda ctx: iter(()))
    assert res.status == "BLOCKED"
    assert "mutually exclusive" in res.reason


# ---------------------------------------------------------------------------
# A5-B -- authorization_safe=False and WSL uncontained fail closed
# ---------------------------------------------------------------------------

def test_authorization_safe_false_blocks_qualification(env, tmp_path, monkeypatch):
    ws, _ctl, eco, source = env
    calls = _patch_runner(monkeypatch, lambda name: _result(
        tmp_path, name,
        containment=CONTAINMENT_TREE_UNCONFIRMED,
        containment_reason="termination_unconfirmed",
        timed_out=True,
        error="termination_unconfirmed",
    ))

    result = _run(eco, source, tools=_specs())

    assert calls, "managed tool must have been attempted"
    assert result.status != "PASS"
    assert result.status == "INCONCLUSIVE"
    assert "authorization-safe" in result.reason
    assert "termination_unconfirmed" in result.reason
    assert eco.history()[-1]["status"] != "PASS"


def test_wsl_uncontained_result_fails_closed(env, tmp_path, monkeypatch):
    ws, _ctl, eco, source = env
    calls = _patch_runner(monkeypatch, lambda name: _result(
        tmp_path, name,
        containment=CONTAINMENT_UNSUPPORTED,
        containment_reason=REASON_WSL_UNCONFIRMABLE,
        wsl_uncontained=True,
        error=REASON_WSL_UNCONFIRMABLE,
    ))

    result = _run(eco, source, tools=_specs())

    assert calls
    assert result.status == "INCONCLUSIVE"
    assert REASON_WSL_UNCONFIRMABLE in result.reason
    assert result.rollback_verified is True or result.rollback_performed is True


# ---------------------------------------------------------------------------
# A5-C -- uncooperative worker detected via bounded quiesce + rehash
# ---------------------------------------------------------------------------

def test_late_uncooperative_worker_detected(env, monkeypatch):
    ws, _ctl, eco, source = env
    release = threading.Event()

    def stubborn():
        # Never cooperates: outlives the bounded quiesce window. Writes a file
        # only after release so the test itself never leaves a dangling writer.
        release.wait(timeout=5)
        (ws / "late.txt").write_text("late", encoding="utf-8")

    worker = threading.Thread(target=stubborn, name="eco-verifier", daemon=True)
    worker.start()
    from assistant.runner import ThreadContainment
    eco._tc = ThreadContainment(snapshot_fn=eco.snapshot_workspace)
    eco._tc.register(worker, name="eco-verifier")
    eco._active_verification_worker = worker
    ckpt = eco.checkpoint(["design.v"], "r4-quiesce")
    baseline = eco.snapshot_workspace()

    try:
        restored, detail = eco._verify_candidate_quiescent(
            ckpt, baseline, worker_wait_s=0.05, window_s=0.0,
        )
    finally:
        release.set()
        worker.join(timeout=2)

    assert eco._last_worker_uncontained is True
    assert "uncontained_workers" in detail
    assert restored is False, "uncooperative worker must make rollback non-qualifying"
    qr = getattr(eco, "_last_quiesce", None)
    assert qr is not None and qr.uncontained_workers


# ---------------------------------------------------------------------------
# Regression -- existing timeout / confirmed tree termination still works
# ---------------------------------------------------------------------------

def test_timeout_and_confirmed_tree_termination_still_work(tmp_path, monkeypatch):
    import assistant.runner as runner

    observed: list[RunResult] = []
    real_run_managed = runner.run_managed

    def capture(command, **kw):
        result = real_run_managed(command, **kw)
        observed.append(result)
        return result

    # Still the REAL runner: the wrapper only observes the result that the
    # production spawn() path consumes.
    monkeypatch.setattr(runner, "run_managed", capture)
    ctx = VerificationContext(
        deadline=None,
        cancelled=lambda: False,
    )
    sentinel = tmp_path / "LATE.txt"
    code = (
        "import time, sys\n"
        f"open({str(sentinel)!r}, 'w').write('READY')\n"
        "time.sleep(5)\n"
        f"open({str(sentinel)!r}, 'a').write('LATE')\n"
    )
    from assistant.eco import EcoTimeoutError, EcoVerificationError

    # A bounded timed-out run is refused either by the monotonic deadline check
    # or by the authorization gate (timed_out => authorization_safe=False).
    with pytest.raises((EcoTimeoutError, EcoVerificationError)) as exc:
        ctx.spawn(
            [sys.executable, "-c", code],
            log_dir=str(tmp_path / "logs"), name="timeout-tool",
            timeout=0.3, cwd=str(tmp_path),
        )
    assert "deadline" in str(exc.value) or "authorization-safe" in str(exc.value)

    assert observed, "the managed runner must have been invoked by spawn()"
    result = observed[-1]
    assert result.timed_out is True
    assert result.containment == CONTAINMENT_TREE_CONFIRMED
    assert sentinel.read_text(encoding="utf-8") == "READY"
