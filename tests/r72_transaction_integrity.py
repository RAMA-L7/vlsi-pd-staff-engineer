"""R7.2 transaction-integrity regressions (T1-T6 + amendment A1-A6).

Every state mutation must be one cross-process transaction: lock, fresh read,
mutate, validate, fsync+replace.  These tests exercise the exact C4/C3 attack
shapes plus lock/persistence failure paths.  Run with the venv interpreter:

    python -m pytest tests/r72_transaction_integrity.py -q
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

import pytest

from assistant import controller as ctlmod
from assistant.controller import (
    GateController,
    LedgerIntegrityError,
    Waiver,
    ledger_critical_section,
)
from assistant.evidence import Evidence
from assistant.r6_authority import authoritative_qualification
from assistant.statuses import Status

from r63_binding_helper import reset_trust, trusted_proof_for

BENIGN = [os.sys.executable, "-c", "print('ok')"]
KEY = "r72-transaction-integrity-key"


@pytest.fixture(autouse=True)
def _stable_key(monkeypatch):
    monkeypatch.setenv("ASSISTANT_LEDGER_KEY", KEY)
    reset_trust()
    yield
    reset_trust()


def _report(tmp_path: Path) -> str:
    p = tmp_path / (uuid.uuid4().hex + ".rpt")
    p.write_text("slack 0.05 ns\n", encoding="utf-8")
    return str(p)


def _pass_ev(check_id: str, tmp_path: Path, *, nonce=None, proof_check_id=None) -> Evidence:
    proof = trusted_proof_for(
        BENIGN, check_id=proof_check_id or check_id, nonce=nonce
    )
    ev = Evidence.from_run(
        check_id=check_id, status=Status.PASS, reason="bound tool execution",
        command=list(BENIGN), exit_code=0, analysis_view="ROUTED_EXTRACTED",
        tool_versions={"sta": "1.0"}, reports=[_report(tmp_path)],
    )
    ev.execution_proof = proof
    return ev


def _fail_ev(check_id: str, tmp_path: Path) -> Evidence:
    return Evidence.from_run(
        check_id=check_id, status=Status.FAIL, reason="concurrent blocking FAIL",
        command=list(BENIGN), exit_code=1, analysis_view="ROUTED_EXTRACTED",
        tool_versions={"sta": "1.0"}, reports=[_report(tmp_path)],
    )


def _record_pass(ctl: GateController, check_id: str, tmp_path: Path, *, nonce=None):
    ev = _pass_ev(check_id, tmp_path, nonce=nonce)
    ctl.record_evidence(ev)
    return ev.evidence_id


# ---------------------------------------------------------------------------
# C4 — a stale snapshot must never overwrite a durable FAIL (T1/T3/A1)
# ---------------------------------------------------------------------------
def test_c4_stale_snapshot_cannot_erase_durable_fail(tmp_path):
    state = str(tmp_path / "state.json")
    owner = GateController(state)
    sta = _record_pass(owner, "eco_sta", tmp_path)
    lvs = _record_pass(owner, "eco_lvs", tmp_path)
    before = GateController(state).evaluate_gate("STA", ["eco_sta", "eco_lvs"])
    assert before.qualified is True

    # A controller loaded BEFORE the FAIL commit holds a stale snapshot.
    stale = GateController(state)
    fresh = GateController(state)
    fresh.record_evidence(_fail_ev("eco_lvs", tmp_path))
    # Old code: stale._save() replaces the whole state and erases the FAIL.
    stale.record_evidence(
        Evidence.from_run(
            check_id="eco_unrelated", status=Status.PASS, reason="late writer",
            command=list(BENIGN), exit_code=0, analysis_view="ROUTED_EXTRACTED",
            tool_versions={"sta": "1.0"}, reports=[_report(tmp_path)],
        )
    )

    durable = json.loads(Path(state).read_text(encoding="utf-8"))
    lvs_ids = durable["check_index"]["eco_lvs"]
    statuses = [durable["evidence"][eid]["status"] for eid in lvs_ids]
    assert "FAIL" in statuses, "concurrent FAIL was lost to a stale overwrite"
    assert any(durable["evidence"][eid]["status"] == "FAIL"
               for eid in lvs_ids if eid != lvs)

    after = GateController(state).evaluate_gate("STA", ["eco_sta", "eco_lvs"])
    assert after.qualified is False
    assert after.decision == "BLOCKED"
    assert "eco_lvs" in after.reason


def test_fail_is_preserved_by_every_transaction_and_evaluates_blocked(tmp_path):
    state = str(tmp_path / "state.json")
    ctl = GateController(state)
    _record_pass(ctl, "eco_sta", tmp_path)
    fail = _fail_ev("eco_lvs", tmp_path)
    ctl.record_evidence(fail)

    # A direct attempt to remove the FAIL through _mutate must be refused.
    with pytest.raises(LedgerIntegrityError) as exc:
        ctl._mutate(lambda fresh: fresh["evidence"].pop(fail.evidence_id, None))
    assert "FAIL" in str(exc.value)
    durable = json.loads(Path(state).read_text(encoding="utf-8"))
    assert fail.evidence_id in durable["evidence"]
    assert GateController(state).evaluate_gate(
        "STA", ["eco_sta", "eco_lvs"]
    ).qualified is False


# ---------------------------------------------------------------------------
# C3 — one nonce can authorize at most one gate (T4)
# ---------------------------------------------------------------------------
def test_c3_one_nonce_cannot_double_qualify(tmp_path):
    state = str(tmp_path / "state.json")
    owner = GateController(state)
    nonce = "r72-c3-shared-nonce"
    owner.record_evidence(_pass_ev("eco_sta", tmp_path, nonce=nonce))
    owner.record_evidence(_pass_ev("eco_lvs", tmp_path, nonce=nonce))

    # Both controllers load the SAME pre-consumption snapshot.
    a = GateController(state)
    b = GateController(state)
    barrier = threading.Barrier(2)
    outcomes: dict[str, bool] = {}

    def run(name, ctl, gate, check):
        barrier.wait()
        outcomes[name] = ctl.evaluate_gate(gate, [check]).qualified

    threads = [
        threading.Thread(target=run, args=("a", a, "G1", "eco_sta")),
        threading.Thread(target=run, args=("b", b, "G2", "eco_lvs")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert set(outcomes) == {"a", "b"}
    assert sum(outcomes.values()) <= 1, outcomes
    durable = json.loads(Path(state).read_text(encoding="utf-8"))
    owners = set((durable.get("used_proof_nonces") or {}).values())
    assert len(owners) <= 1, "one nonce gained two durable owners"


def test_nonce_claim_is_atomic_and_idempotent(tmp_path):
    state = str(tmp_path / "state.json")
    ctl = GateController(state)
    e1 = _record_pass(ctl, "eco_sta", tmp_path, nonce="r72-atomic-nonce")
    e2 = _record_pass(ctl, "eco_lvs", tmp_path, nonce="r72-atomic-nonce")
    other = GateController(state)

    assert ctl.consume_proof_nonces({"r72-atomic-nonce": e1}) is True
    assert other.consume_proof_nonces({"r72-atomic-nonce": e2}) is False
    assert ctl.consume_proof_nonces({"r72-atomic-nonce": e1}) is True  # idempotent
    durable = json.loads(Path(state).read_text(encoding="utf-8"))
    assert durable["used_proof_nonces"]["r72-atomic-nonce"] == e1


# ---------------------------------------------------------------------------
# Concurrent writers / lock re-entrancy (T1/T2)
# ---------------------------------------------------------------------------
def test_concurrent_state_writers_lose_nothing(tmp_path):
    state = str(tmp_path / "state.json")
    GateController(state)
    checks = [f"r72_check_{i}" for i in range(6)]
    barrier = threading.Barrier(len(checks))
    ids: dict[str, str] = {}

    def write(check):
        c = GateController(state)
        ev = Evidence.from_run(
            check_id=check, status=Status.PASS, reason="concurrent writer",
            command=list(BENIGN), exit_code=0, analysis_view="ROUTED_EXTRACTED",
            tool_versions={"sta": "1.0"}, reports=[_report(tmp_path)],
        )
        barrier.wait()
        c.record_evidence(ev, strict=False,
                          simulated="r72 concurrency test (no EDA provenance)")
        ids[check] = ev.evidence_id

    threads = [threading.Thread(target=write, args=(c,)) for c in checks]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    durable = json.loads(Path(state).read_text(encoding="utf-8"))
    assert set(ids) == set(checks)
    for check, eid in ids.items():
        assert eid in durable["evidence"], f"lost concurrent write for {check}"
        assert eid in durable["check_index"][check]


def test_ledger_lock_is_reentrant(tmp_path):
    state = str(tmp_path / "state.json")
    with ledger_critical_section(state):
        with ledger_critical_section(state):
            assert os.path.exists(str(state) + ".lock")
    # The depth counter must be fully unwound.
    lock = ctlmod._CROSS_PROCESS_LOCKS.get(
        os.path.realpath(os.path.abspath(state)) + ".lock", 0
    )
    assert lock == 0


def test_lock_failure_fails_closed_without_write(tmp_path, monkeypatch):
    state = str(tmp_path / "state.json")
    ctl = GateController(state)

    def boom(*_args, **_kwargs):
        raise OSError("simulated lock open failure")

    monkeypatch.setattr(ctlmod, "open", boom, raising=False)
    with pytest.raises(LedgerIntegrityError):
        ctl.register_design_fact("x", 1, source="r72")
    assert not os.path.exists(state), "no state may be written without the lock"


# ---------------------------------------------------------------------------
# Crash / persistence failures (T5)
# ---------------------------------------------------------------------------
def test_fsync_precedes_replace_and_failed_replace_writes_nothing(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    ctl = GateController(str(state))
    orig_replace = ctlmod.os.replace
    orig_fsync = ctlmod.os.fsync
    calls: list[str] = []
    fail = {"on": False}

    def tracked_fsync(fd):
        calls.append("fsync")
        return orig_fsync(fd)

    def tracked_replace(src, dst):
        calls.append("replace")
        if fail["on"]:
            raise OSError("simulated crash before replace")
        return orig_replace(src, dst)

    monkeypatch.setattr(ctlmod.os, "fsync", tracked_fsync)
    monkeypatch.setattr(ctlmod.os, "replace", tracked_replace)

    waiver = Waiver(
        check_id="eco_sta", scope="g",
        reason="r72 persistence test with a sufficiently long reason",
        approved_by="r72", policy="tool_gap",
    )
    ctl.add_waiver(waiver)
    assert "fsync" in calls and "replace" in calls
    assert calls.index("fsync") < calls.index("replace")

    before = state.read_text(encoding="utf-8")
    fail["on"] = True
    with pytest.raises(OSError):
        ctl.cancel("r72 crash")
    assert state.read_text(encoding="utf-8") == before, "failed replace changed state"
    assert ctl.run_status == "ACTIVE", "in-memory snapshot must roll back too"


def test_crash_after_replace_leaves_new_state_intact(tmp_path):
    state = tmp_path / "state.json"
    ctl = GateController(str(state))
    ctl.cancel("r72 after-replace")
    assert GateController(str(state)).run_status == "CANCELLED"
    ctl.restart("r72 restart", operator="r72")
    fresh = GateController(str(state))
    assert fresh.run_status == "ACTIVE"
    assert fresh.report()["restart_history"][-1]["operator"] == "r72"


# ---------------------------------------------------------------------------
# A1 / T5/T6 — fresh-manager authorization, no stale Boolean
# ---------------------------------------------------------------------------
def test_fresh_manager_rederives_authorization_from_durable_state(tmp_path):
    state = str(tmp_path / "state.json")
    owner = GateController(state)
    nonce = "r72-fresh-manager-nonce"
    eid = _record_pass(owner, "eco_sta", tmp_path, nonce=nonce)
    assert GateController(state).evaluate_gate("G", ["eco_sta"]).qualified is True

    # A stale manager is created BEFORE another writer consumes this nonce.
    stale = GateController(state)
    other_eid = _record_pass(
        GateController(state), "eco_lvs", tmp_path, nonce=nonce
    )
    assert stale.durable_state_is_current() is False
    # Authorization reads fresh durable state: the nonce now belongs to eid,
    # so a decision for another_eid must fail closed.
    assert authoritative_qualification(stale, [other_eid]) is False
    assert authoritative_qualification(GateController(state), [eid]) is True


def test_gate_definition_write_never_clobbers_a_concurrent_nonce_claim(tmp_path):
    state = str(tmp_path / "state.json")
    owner = GateController(state)
    eid = _record_pass(owner, "eco_sta", tmp_path, nonce="r72-def-nonce")
    late = GateController(state)  # pre-commit snapshot
    GateController(state).evaluate_gate("WINNER", ["eco_sta"])
    durable = json.loads(Path(state).read_text(encoding="utf-8"))
    assert durable["used_proof_nonces"]["r72-def-nonce"] == eid
    # The late controller's definition write must reload fresh state first.
    late._record_gate_definition("LOSER", ["eco_sta"], None, ())
    final = json.loads(Path(state).read_text(encoding="utf-8"))
    assert final["used_proof_nonces"] == durable["used_proof_nonces"]
    assert "LOSER" in final["gate_definitions"]
