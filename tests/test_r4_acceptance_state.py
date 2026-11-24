"""R4 acceptance-state exploits: one authoritative, irreversible terminal state.

All providers/clocks here are SIMULATED or injected. No real EDA tool, no live
inference endpoint and no external service is used.

The EXPLOIT tests in this module FAIL against the vulnerable baseline and PASS
after the fix:

  * A1-F1  deadline/cancellation crossing during the FINAL post-downstream
           integrity re-hash (``eco.py:2152``) left a durable ``PASS / False``
           record because the last guard check was at ``eco.py:2134``.
  * A4-B   a re-entrant acceptance committed a nested PASS; the outer
           ``_amend_last`` amended ``history[-1]`` (the nested record), leaving
           ``[('outer','PASS'), ('outer','INCONCLUSIVE')]`` on disk.

The fix makes the guard the single source of truth (its latched reason is the
authority) and stamps every durable record with a unique ``transaction_token``
so an amendment always targets THIS transaction's own record.
"""
from __future__ import annotations

import time
from collections import defaultdict
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

SIM = "SIMULATED R4 acceptance-state regression"
BASELINE = "module d; endmodule\n"
MUTANT = "module d; assign x = 1'b1; // MUTANT BY REENTRANT DOWNSTREAM\n"


class FakeClock:
    """Deterministic monotonic clock injected into ``assistant.eco.time``."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self._time_calls = 0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        self._time_calls += 1
        return self.now + self._time_calls * 0.01

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def advance(self, seconds: float = 1.0) -> None:
        self.now += seconds


def _make_env(tmp_path, monkeypatch=None):
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
    if monkeypatch is not None:
        # Real-clock tests deliberately DO NOT inject this.
        monkeypatch.setattr(eco_mod, "time", clock)
    return SimpleNamespace(
        ws=ws, ctl=ctl, eco=eco, rep=rep, clock=clock,
        monkeypatch=monkeypatch, tmp_path=tmp_path,
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    return _make_env(tmp_path, monkeypatch)


@pytest.fixture
def real_env(tmp_path):
    """No monkeypatch of ``eco_mod.time``: a genuinely monotonic clock."""
    return _make_env(tmp_path, None)


def _ev(rep, cid, status=Status.PASS, na=None):
    kw = dict(
        reason="SIMULATED", command=["sim"], exit_code=0,
        analysis_view="MAPPED_NETLIST", tool_versions={"sim": "1"},
        reports=[str(rep)],
    )
    if na:
        kw["applicability_reason"] = na
    return Evidence.from_run(cid, status, **kw)


def _provider(rep):
    def verify():
        return [
            _ev(rep, "eco_lec"),
            _ev(rep, "eco_sta"),
            _ev(rep, "eco_physical_drc", Status.PASS),
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
        experiment_id=kw.pop("experiment_id", "r4"),
        description="SIMULATED R4 acceptance-state regression",
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


# -- durable-state invariants -------------------------------------------------

def _assert_single_terminal(e, result, *, deadline=None, cancelled=False):
    hist = e.eco.history()
    assert len(hist) == 1, f"expected ONE durable record, got {hist}"
    rec = hist[0]
    assert rec["status"] != "PASS", f"durable PASS survived invalidation: {rec}"
    assert rec["status"] == result.status, (rec["status"], result.status)
    assert result.status != "PASS", f"returned PASS after invalidation: {result}"
    if deadline is True:
        assert rec["deadline_exceeded"] is True, rec
        assert result.deadline_exceeded is True, result
    if cancelled:
        assert "cancel" in rec["reason"].lower()
        assert "cancel" in result.reason.lower()


def _assert_no_contradiction(e):
    """No two durable records may claim conflicting terminal states.

    - A unique ``transaction_token`` must appear at most once (record identity).
    - One ``experiment_id`` may never have both a PASS and a non-PASS record.
    """
    hist = e.eco.history()
    toks = [r.get("transaction_token", "") for r in hist if r.get("transaction_token")]
    assert len(toks) == len(set(toks)), f"duplicate transaction tokens: {toks}"
    by_id: dict[str, set[str]] = defaultdict(set)
    for r in hist:
        by_id[r["experiment_id"]].add(r["status"])
    for eid, statuses in by_id.items():
        assert not ("PASS" in statuses and len(statuses) > 1), (
            f"contradictory durable records for {eid!r}: {sorted(statuses)} in {hist}"
        )


# ---------------------------------------------------------------------------
# A1-F1: deadline crossing during the FINAL integrity re-hash
# ---------------------------------------------------------------------------

def test_expiry_during_final_rehash_fake_clock(env):
    """EXPLOIT (fake clock): expiry inside the final re-hash must invalidate."""
    e = env
    original = e.eco._accepted_integrity_violations

    def slow_rehash(*args, **kwargs):
        e.clock.advance(1.0)  # deadline (0.5 s) crosses during the re-hash
        return original(*args, **kwargs)

    e.monkeypatch.setattr(e.eco, "_accepted_integrity_violations", slow_rehash)
    result = _run(e, verify=_provider(e.rep), downstream=lambda _r: None)
    _assert_single_terminal(e, result, deadline=True)
    _assert_no_contradiction(e)


def test_expiry_during_final_rehash_real_clock(real_env):
    """EXPLOIT (REAL monotonic clock, no time monkeypatch): same final window.

    Mirrors the lead reproduction: ``time.sleep`` in the re-hash against a
    0.25 s deadline.  Vulnerable code returns and persists PASS/False.
    """
    e = real_env
    original = e.eco._accepted_integrity_violations

    def slow_rehash(*args, **kwargs):
        time.sleep(0.6)
        return original(*args, **kwargs)

    e.eco._accepted_integrity_violations = slow_rehash
    result = _run(e, verify=_provider(e.rep), downstream=lambda _r: None, timeout=0.25)
    _assert_single_terminal(e, result, deadline=True)
    _assert_no_contradiction(e)


def test_cancellation_during_final_rehash(env):
    """EXPLOIT: operator cancel landing during the final re-hash."""
    e = env
    original = e.eco._accepted_integrity_violations

    def cancelling_rehash(*args, **kwargs):
        e.ctl.cancel("SIMULATED cancel during final rehash")
        return original(*args, **kwargs)

    e.monkeypatch.setattr(e.eco, "_accepted_integrity_violations", cancelling_rehash)
    result = _run(e, verify=_provider(e.rep), downstream=lambda _r: None)
    _assert_single_terminal(e, result, cancelled=True)
    _assert_no_contradiction(e)


# ---------------------------------------------------------------------------
# Deadline BETWEEN the re-hash and the final persist
# ---------------------------------------------------------------------------

def test_deadline_between_rehash_and_final_persist(env):
    """EXPLOIT: deadline crosses after a clean re-hash, before final persist.

    The downstream callback raises, so the tail takes the
    ``_amend_last(accepted)`` persist path.  We advance the clock right before
    that persist.  Vulnerable code leaves a durable PASS (and, via the outer
    exception handler, an appended contradictory record); the fix rewrites the
    transaction's own record to one INCONCLUSIVE/True terminal.
    """
    e = env
    original_amend = e.eco._commit
    state = {"advanced": False}

    def hooked_amend(result, *a, **k):
        if not state["advanced"]:
            state["advanced"] = True
            e.clock.advance(1.0)
        return original_amend(result, *a, **k)

    e.monkeypatch.setattr(e.eco, "_commit", hooked_amend)

    def downstream(_result):
        raise RuntimeError("SIMULATED downstream failure -> final persist path")

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    assert state["advanced"]
    _assert_single_terminal(e, result, deadline=True)
    _assert_no_contradiction(e)


# ---------------------------------------------------------------------------
# A4-B: re-entrant acceptance and record identity
# ---------------------------------------------------------------------------

def _reentrant_mutation_run(e, outer_id="r4-outer", nested_id="r4-nested"):
    """Outer commits PASS; nested commits PASS; outer re-hash sees MUTANT."""
    seen = {}

    def downstream(_result):
        (e.ws / "design.v").write_text(MUTANT, encoding="utf-8")
        try:
            nested = _run(e, experiment_id=nested_id, verify=_provider(e.rep))
            seen["nested_status"] = nested.status
        except Exception as exc:  # noqa: BLE001
            seen["error"] = f"{type(exc).__name__}: {exc}"

    outer = _run(e, experiment_id=outer_id, verify=_provider(e.rep), downstream=downstream)
    return outer, seen


def test_reentrant_nested_record_survives_and_outer_is_invalidated(env):
    """EXPLOIT (A4-B): the outer amendment must not destroy the nested record."""
    e = env
    outer, seen = _reentrant_mutation_run(e)
    assert outer.status != "PASS"
    assert seen.get("nested_status") == "PASS", seen
    hist = e.eco.history()
    by_id = {r["experiment_id"]: r for r in hist}
    # The nested transaction is independent: its record must survive intact.
    assert by_id.get("r4-nested", {}).get("status") == "PASS", hist
    # The outer transaction must have exactly one terminal record, not a PASS.
    outer_records = [r for r in hist if r["experiment_id"] == "r4-outer"]
    assert len(outer_records) == 1, f"outer has {len(outer_records)} records: {hist}"
    assert outer_records[0]["status"] != "PASS", hist
    _assert_no_contradiction(e)


def test_pass_to_inconclusive_replaces_own_record(env):
    """EXPLOIT (PASS -> INCONCLUSIVE): transition must replace the own record."""
    e = env
    outer, seen = _reentrant_mutation_run(e)
    assert outer.status != "PASS"
    hist = e.eco.history()
    outer_records = [r for r in hist if r["experiment_id"] == "r4-outer"]
    assert len(outer_records) == 1 and outer_records[0]["status"] == "INCONCLUSIVE", hist
    assert outer_records[0].get("late_mutation_detected") is True, hist
    # The bound candidate revision must still be recorded on the single terminal.
    assert outer_records[0].get("candidate_revision"), hist
    _assert_no_contradiction(e)


def test_duplicate_acceptance_same_transaction_id_is_rejected(env):
    """EXPLOIT: re-entering with the SAME transaction id cannot accept twice."""
    e = env
    seen = {}

    def downstream(_result):
        try:
            nested = _run(e, experiment_id="r4-dup", verify=_provider(e.rep))
            seen["nested_status"] = nested.status
        except Exception as exc:  # noqa: BLE001
            seen["error"] = f"{type(exc).__name__}: {exc}"

    result = _run(e, experiment_id="r4-dup", verify=_provider(e.rep), downstream=downstream)
    assert result.status == "PASS"
    assert seen.get("nested_status") != "PASS", seen
    records = [r for r in e.eco.history() if r["experiment_id"] == "r4-dup"]
    assert len(records) == 1, f"duplicate acceptance persisted: {e.eco.history()}"
    _assert_no_contradiction(e)


def test_inconclusive_to_pass_reacceptance_is_forbidden(env):
    """EXPLOIT (INCONCLUSIVE -> PASS): invalidation is irreversible.

    The mutation terminal must latch the guard so that a re-entrant
    ``_commit(PASS)`` attempted while the guard is still active is refused.
    Vulnerable code does not latch on mutation, so the forged PASS is accepted.
    """
    e = env
    seen = {}
    original_amend = e.eco._commit

    def hooked_amend(result, *a, **k):
        if result.status != "PASS" and not seen.get("tried"):
            seen["tried"] = True
            forged = ExperimentResult(
                experiment_id="r4-inv", change_class="logic_optimization",
                description="SIMULATED re-accept attempt", status="PASS",
                reason="SIMULATED forged re-acceptance after invalidation",
                deadline_exceeded=False,
            )
            try:
                e.eco._commit(forged)
                seen["forged_committed"] = True
            except Exception as exc:  # noqa: BLE001
                seen["error"] = f"{type(exc).__name__}: {exc}"
        return original_amend(result, *a, **k)

    e.monkeypatch.setattr(e.eco, "_commit", hooked_amend)

    def downstream(_result):
        (e.ws / "design.v").write_text(MUTANT, encoding="utf-8")

    result = _run(e, experiment_id="r4-inv", verify=_provider(e.rep), downstream=downstream)
    assert seen.get("tried") is True, "mutation terminal never went through amend"
    assert not seen.get("forged_committed"), f"INCONCLUSIVE->PASS succeeded: {seen}"
    assert result.status != "PASS"
    assert all(r["status"] != "PASS" for r in e.eco.history()), e.eco.history()
    _assert_no_contradiction(e)


# ---------------------------------------------------------------------------
# Contradictory durable record detection
# ---------------------------------------------------------------------------

def test_no_contradictory_durable_pass_plus_inconclusive(env):
    """EXPLOIT: A4-B concrete contradiction must not be durable."""
    e = env
    outer, _seen = _reentrant_mutation_run(e)
    hist = e.eco.history()
    statuses = [(r["experiment_id"], r["status"]) for r in hist]
    assert not any(s == "PASS" and eid == "r4-outer" for eid, s in statuses), statuses
    assert ("r4-outer", "INCONCLUSIVE") in statuses, statuses
    _assert_no_contradiction(e)


def test_clean_control_has_one_pass_with_unique_token(env):
    """CONTROL (holds before and after): a clean run has one PASS record."""
    e = env
    result = _run(e, experiment_id="r4-clean", verify=_provider(e.rep))
    assert result.status == "PASS"
    hist = e.eco.history()
    assert len(hist) == 1, hist
    _assert_no_contradiction(e)
