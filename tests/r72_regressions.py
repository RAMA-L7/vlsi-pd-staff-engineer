"""R7.2 no-weakening regressions: R6.3 proof binding and R7.1 check_index.

These mirror the pre-existing R6.3/R7.1 exploit shapes and assert they still
fail closed after the transaction refactor.  Run with the venv interpreter:

    python -m pytest tests/r72_regressions.py -q
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from assistant.controller import GateController, StateError
from assistant.evidence import Evidence
from assistant.r6_authority import authoritative_qualification
from assistant.statuses import Status

from r63_binding_helper import reset_trust, trusted_proof_for

BENIGN = [os.sys.executable, "-c", "print('ok')"]
KEY = "r72-regressions-key"


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


def _evidence(check_id: str, tmp_path: Path, proof) -> Evidence:
    ev = Evidence.from_run(
        check_id=check_id, status=Status.PASS, reason="bound tool execution",
        command=list(BENIGN), exit_code=0, analysis_view="ROUTED_EXTRACTED",
        tool_versions={"sta": "1.0"}, reports=[_report(tmp_path)],
    )
    ev.execution_proof = proof
    return ev


def _record(tmp_path: Path, check_id: str, *, nonce=None):
    ctl = GateController(str(tmp_path / "state.json"))
    proof = trusted_proof_for(BENIGN, check_id=check_id, nonce=nonce)
    ev = _evidence(check_id, tmp_path, proof)
    ctl.record_evidence(ev)
    return ctl, ev.evidence_id, proof


def test_r71_index_substitution_still_fails_closed(tmp_path):
    ctl, eid, _ = _record(tmp_path, "eco_sta")
    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    raw["check_index"].setdefault("eco_lvs", []).append(eid)
    (tmp_path / "state.json").write_text(
        json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8"
    )
    fresh = GateController(str(tmp_path / "state.json"))
    gd = fresh.evaluate_gate("STA", ["eco_sta", "eco_lvs"])
    assert gd.qualified is False and gd.decision == "BLOCKED"
    assert any("check_index" in p for p in fresh.verify_ledger_integrity())


def test_r63_proof_for_other_check_cannot_authorize(tmp_path):
    proof = trusted_proof_for(BENIGN, check_id="eco_sta", nonce="r72-wrong-check")
    ctl = GateController(str(tmp_path / "state.json"))
    ev = _evidence("eco_lvs", tmp_path, proof)
    ctl.record_evidence(ev)
    assert authoritative_qualification(ctl, [ev.evidence_id]) is False
    assert ctl.evaluate_gate("X", ["eco_lvs"]).qualified is False


def test_r63_replayed_nonce_denied_after_transaction_refactor(tmp_path):
    proof = trusted_proof_for(BENIGN, check_id="eco_sta", nonce="r72-replay")
    ctl = GateController(str(tmp_path / "state.json"))
    first = _evidence("eco_sta", tmp_path, proof)
    ctl.record_evidence(first)
    assert authoritative_qualification(ctl, [first.evidence_id]) is True
    second = _evidence("eco_sta", tmp_path, proof)
    ctl.record_evidence(second)
    assert authoritative_qualification(ctl, [second.evidence_id]) is False


def test_r71_recovery_preserves_binding_through_transaction(tmp_path):
    ctl, _eid, _ = _record(tmp_path, "eco_sta")
    assert ctl.evaluate_gate("G", ["eco_sta"]).qualified is True
    ctl.cancel("r72 recovery")
    with pytest.raises(StateError):
        ctl.record_evidence(_evidence(
            "eco_lvs", tmp_path, trusted_proof_for(BENIGN, check_id="eco_lvs")
        ))
    ctl.restart("r72 restart")
    fresh = GateController(str(tmp_path / "state.json"))
    assert fresh.verify_ledger_integrity() == []
    assert fresh.evaluate_gate("G", ["eco_sta"]).qualified is True
