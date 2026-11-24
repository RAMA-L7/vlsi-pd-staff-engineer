"""R3 regression for R2-01: deadline/cancellation as irreversible invalidation.

All providers and clocks in this file are SIMULATED / injected. No real EDA
tool, no live inference and no external service is used. A deterministic fake
monotonic clock is installed into ``assistant.eco.time`` so that expiry can be
triggered at an exact acceptance boundary without wall-clock sleeps or races
that depend on scheduling luck.

These are exploit-style tests. The ones marked "EXPLOIT" FAIL against the
vulnerable baseline (a durable ``status=PASS, deadline_exceeded=False`` record)
and PASS after the fix. The ones marked "GUARD" must hold both before and after
(no existing gate is weakened).
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import assistant.eco as eco_mod
from assistant.controller import GateController
from assistant.eco import (
    PHYSICAL_DRC_NA_REASON,
    EcoChangeScope,
    EcoManager,
    EcoStage,
    ExperimentResult,
    ProtectedMetric,
)
from assistant.evidence import Evidence
from assistant.statuses import Status

SIM = "SIMULATED R2-01 acceptance-lifecycle regression"
BASELINE = "module d; endmodule\n"


class FakeClock:
    """Deterministic monotonic clock injected into assistant.eco.time."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self._time_calls = 0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        # Keep checkpoint directory names unique across repeated runs.
        self._time_calls += 1
        return self.now + self._time_calls * 0.01

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def advance(self, seconds: float = 1.0) -> None:
        self.now += seconds


@pytest.fixture
def env(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "design.v").write_text(BASELINE, encoding="utf-8")
    ctl = GateController(str(tmp_path / "state.json"))
    for fact in ("has_macros", "has_upf", "has_clock_gates", "has_analog"):
        ctl.register_design_fact(fact, False, source="SIMULATED inventory")
    eco = EcoManager(
        str(ws), ctl,
        checkpoint_dir=str(tmp_path / "ckpt"),
        experiment_log=str(tmp_path / "exp.json"),
    )
    rep = tmp_path / "sim_report.txt"
    rep.write_text("SIMULATED check report: all checks clean\n", encoding="utf-8")
    clock = FakeClock()
    monkeypatch.setattr(eco_mod, "time", clock)
    return SimpleNamespace(
        ws=ws, ctl=ctl, eco=eco, rep=rep, clock=clock,
        monkeypatch=monkeypatch, tmp_path=tmp_path,
    )


def _ev(rep, cid, status=Status.PASS, na=None):
    kw = dict(
        reason="SIMULATED", command=["sim"], exit_code=0,
        analysis_view="MAPPED_NETLIST", tool_versions={"sim": "1"},
        reports=[str(rep)],
    )
    if na:
        kw["applicability_reason"] = na
    return Evidence.from_run(cid, status, **kw)


def _provider(rep, *, drc=Status.PASS):
    def verify():
        return [
            _ev(rep, "eco_lec"),
            _ev(rep, "eco_sta"),
            _ev(rep, "eco_physical_drc", drc,
                None if drc is Status.PASS else PHYSICAL_DRC_NA_REASON),
        ]
    return verify


def _scope():
    return EcoChangeScope(
        "logic_optimization", EcoStage.MAPPED, True, "SIMULATED recorded scope"
    )


def _run(e, **kw):
    kw.setdefault("simulated", SIM)
    return e.eco.run_experiment(
        change_scope=kw.pop("scope", _scope()),
        experiment_id=kw.pop("experiment_id", "r2-01"),
        description="SIMULATED R2-01 regression",
        change_class="logic_optimization",
        changed_files=["design.v"],
        apply_change=kw.pop("apply_change", lambda: None),
        evaluate=kw.pop("evaluate", lambda: {"wns": 1.0}),
        protected_metrics=[ProtectedMetric("wns", 0.0)],
        verify=kw.pop("verify", None),
        downstream_stage=kw.pop("downstream", None),
        verification_timeout_s=kw.pop("timeout", 0.5),
        **kw,
    )


def _hook_save(e, predicate, action):
    """Wrap eco._save_log; run ``action(mgr)`` when ``predicate(mgr)`` is true."""
    original = e.eco._save_log

    def hooked():
        if predicate(e.eco):
            action(e.eco)
        return original()

    e.monkeypatch.setattr(e.eco, "_save_log", hooked)
    return original


def _on_pass_commit(mgr):
    return bool(mgr._history) and mgr._history[-1].get("status") == "PASS"


def _on_terminal_amend(mgr):
    return bool(mgr._history) and mgr._history[-1].get("status") == "INCONCLUSIVE"


def _assert_single_terminal(e, result, *, deadline=None, cancelled=False):
    hist = e.eco.history()
    assert len(hist) == 1, f"expected one durable record, got {hist}"
    rec = hist[-1]
    assert rec["status"] != "PASS", f"durable PASS survived invalidation: {rec}"
    assert rec["status"] == result.status, (rec["status"], result.status)
    assert result.status != "PASS", f"returned PASS after invalidation: {result}"
    if deadline is True:
        assert rec["deadline_exceeded"] is True
        assert result.deadline_exceeded is True
    if cancelled:
        assert "cancel" in rec["reason"].lower()
        assert "cancel" in result.reason.lower()


# ---------------------------------------------------------------------------
# EXPLOIT: expiry / cancellation during persistence (the reproduced baseline)
# ---------------------------------------------------------------------------

def test_expiry_during_persistence_no_downstream_is_irreversible(env):
    """EXPLOIT: deadline passes inside _save_log, no downstream callback."""
    e = env
    _hook_save(e, _on_pass_commit, lambda mgr: e.clock.advance(1.0))
    result = _run(e, verify=_provider(e.rep))
    _assert_single_terminal(e, result, deadline=True)


def test_expiry_during_persistence_with_downstream_has_single_record(env):
    """EXPLOIT: no PASS(False) followed by INCONCLUSIVE(True)."""
    e = env
    called = []
    # R5: the single authoritative commit is the LAST act, so a clock advance
    # inside _save_log occurs after downstream. Advance during the downstream
    # callback instead — the valid pre-authorization boundary.
    def downstream(_r):
        called.append(_r)
        e.clock.advance(1.0)
    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    _assert_single_terminal(e, result, deadline=True)
    # Downstream receives the in-memory candidate before final persistence;
    # the result is never durably PASS and no further downstream is authorized.
    assert len(called) == 1


def test_cancellation_during_persistence_is_irreversible(env):
    """EXPLOIT: operator cancel lands inside _save_log."""
    e = env
    _hook_save(e, _on_pass_commit,
               lambda mgr: e.ctl.cancel("SIMULATED cancel inside persistence"))
    result = _run(e, verify=_provider(e.rep))
    _assert_single_terminal(e, result, cancelled=True)


# ---------------------------------------------------------------------------
# EXPLOIT: expiry / cancellation inside the downstream callback
# ---------------------------------------------------------------------------

def test_expiry_inside_downstream_is_irreversible(env):
    """EXPLOIT: deadline passes while the downstream callback is running."""
    e = env

    def downstream(_result):
        e.clock.advance(1.0)

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    _assert_single_terminal(e, result, deadline=True)


def test_cancellation_inside_downstream_is_irreversible(env):
    """EXPLOIT: operator cancel while downstream is running."""
    e = env

    def downstream(_result):
        e.ctl.cancel("SIMULATED cancel inside downstream")

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    _assert_single_terminal(e, result, cancelled=True)


def test_downstream_failure_after_expiry_is_not_a_pass_record(env):
    """EXPLOIT: downstream raises after expiry -> no PASS/downstream_failed."""
    e = env

    def downstream(_result):
        e.clock.advance(1.0)
        raise RuntimeError("SIMULATED downstream failure after expiry")

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    _assert_single_terminal(e, result, deadline=True)


# ---------------------------------------------------------------------------
# EXPLOIT: persistence failure + retry must retain the terminal state
# ---------------------------------------------------------------------------

def test_terminal_amend_survives_persistence_failure_and_retry(env):
    """EXPLOIT: first amend write fails; retry must still persist non-PASS."""
    e = env
    original = e.eco._save_log
    state = {"commit_expired": False, "amend_failed": False}

    def hooked():
        if _on_pass_commit(e.eco) and not state["commit_expired"]:
            state["commit_expired"] = True
            e.clock.advance(1.0)
        if _on_terminal_amend(e.eco) and not state["amend_failed"]:
            state["amend_failed"] = True
            raise OSError("SIMULATED transient persist failure on amend")
        return original()

    e.monkeypatch.setattr(e.eco, "_save_log", hooked)
    result = _run(e, verify=_provider(e.rep))
    assert state["commit_expired"] and state["amend_failed"]
    _assert_single_terminal(e, result, deadline=True)


# ---------------------------------------------------------------------------
# EXPLOIT: reentrancy cannot restore PASS
# ---------------------------------------------------------------------------

def test_reentrant_amend_with_forged_pass_cannot_restore_pass(env):
    """EXPLOIT: downstream re-enters _amend_last() with a forged PASS."""
    e = env
    forged = ExperimentResult(
        experiment_id="forged", change_class="logic_optimization",
        description="SIMULATED forged result", status="PASS",
        reason="SIMULATED forged PASS", deadline_exceeded=False,
    )
    seen = {}

    def downstream(_result):
        e.clock.advance(1.0)
        try:
            e.eco._amend_last(forged)
        except Exception as exc:  # noqa: BLE001 - forged write must be refused
            seen["error"] = f"{type(exc).__name__}: {exc}"

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    _assert_single_terminal(e, result, deadline=True)
    assert "error" in seen, "re-entrant PASS persistence was not refused"
    assert all(r.get("experiment_id") != "forged" for r in e.eco.history())


def test_reentrant_run_experiment_refused_while_parent_invalidated(env):
    """EXPLOIT: a nested acceptance inside downstream cannot create PASS."""
    e = env
    seen = {}

    def downstream(_result):
        e.clock.advance(1.0)
        try:
            nested = _run(e, experiment_id="nested", verify=_provider(e.rep))
            seen["nested_status"] = nested.status
        except Exception as exc:  # noqa: BLE001 - nested acceptance must be refused
            seen["error"] = f"{type(exc).__name__}: {exc}"

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    _assert_single_terminal(e, result, deadline=True)
    assert seen.get("nested_status") != "PASS"
    assert all(r.get("experiment_id") != "nested" for r in e.eco.history())


# ---------------------------------------------------------------------------
# EXPLOIT: concurrent cancellation at a persistence race barrier
# ---------------------------------------------------------------------------

def test_race_barrier_cancel_at_persistence_is_detected(env):
    """EXPLOIT: a concurrent cancel landing in _save_log must invalidate."""
    e = env
    barrier = threading.Barrier(2)

    def canceller():
        barrier.wait(timeout=5)
        e.ctl.cancel("SIMULATED race cancel at persistence")

    thread = threading.Thread(target=canceller, name="simulated-canceller")
    thread.start()

    def at_pass_commit(_mgr):
        barrier.wait(timeout=5)

    _hook_save(e, _on_pass_commit, at_pass_commit)
    try:
        result = _run(e, verify=_provider(e.rep))
    finally:
        thread.join(timeout=5)
    assert not thread.is_alive(), "race canceller did not complete"
    _assert_single_terminal(e, result, cancelled=True)


# ---------------------------------------------------------------------------
# GUARDS: existing gates must not be weakened (hold before and after the fix)
# ---------------------------------------------------------------------------

def test_guard_expiry_during_evaluation_is_non_pass(env):
    e = env

    def slow_eval():
        e.clock.advance(1.0)
        return {"wns": 1.0}

    result = _run(e, verify=_provider(e.rep), evaluate=slow_eval)
    _assert_single_terminal(e, result, deadline=True)


def test_guard_expiry_during_final_revalidation_is_non_pass(env):
    e = env
    original = eco_mod.revalidate_evidence

    def injected(ev):
        e.clock.advance(1.0)
        return original(ev)

    e.monkeypatch.setattr(eco_mod, "revalidate_evidence", injected)
    result = _run(e, verify=_provider(e.rep))
    _assert_single_terminal(e, result, deadline=True)


def test_guard_cancellation_during_final_revalidation_is_non_pass(env):
    e = env
    original = eco_mod.revalidate_evidence

    def injected(ev):
        e.ctl.cancel("SIMULATED cancel in revalidation")
        return original(ev)

    e.monkeypatch.setattr(eco_mod, "revalidate_evidence", injected)
    result = _run(e, verify=_provider(e.rep))
    _assert_single_terminal(e, result, cancelled=True)


def test_guard_expiry_between_revalidation_and_commit_is_non_pass(env):
    e = env
    original = e.eco._pre_acceptance_revalidation

    def wrapped(**kwargs):
        original(**kwargs)
        e.clock.advance(1.0)

    e.monkeypatch.setattr(e.eco, "_pre_acceptance_revalidation", wrapped)
    result = _run(e, verify=_provider(e.rep))
    _assert_single_terminal(e, result, deadline=True)


def test_guard_clean_run_still_passes(env):
    """An unexpired, uncancelled simulated run must still accept normally."""
    e = env
    result = _run(e, verify=_provider(e.rep))
    assert result.status == "PASS"
    assert not result.deadline_exceeded
    hist = e.eco.history()
    assert len(hist) == 1 and hist[-1]["status"] == "PASS"
