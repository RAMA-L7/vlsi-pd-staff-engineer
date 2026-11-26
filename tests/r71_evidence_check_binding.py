"""R7.1 regressions: evidence-to-check authorization binding (A7-P0-01).

Covers: index tamper/substitution/deletion, proof-for-X-on-evidence-Y, duplicate
evidence reuse, dangling index refs, replay, concurrency, recovery, downstream
consistency and P0-A/B/C regressions. Run with the venv interpreter:

    python -m pytest tests/r71_evidence_check_binding.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import pytest

from assistant.controller import GateController, StateError
from assistant.evidence import Evidence
from assistant.r6_authority import (
    authoritative_qualification, decode_aware_execution_risk, get_trust,
    r6_execution_guard,
)
from assistant.r61_identity import is_unsupported_image
from r63_binding_helper import trusted_proof_for, reset_trust

BENIGN = [sys.executable, "-c", "print('ok')"]
DOMAIN_CHECK_INDEX = "check_index/v1"


@pytest.fixture(autouse=True)
def _stable_key(monkeypatch):
    monkeypatch.setenv("ASSISTANT_LEDGER_KEY", "r71-binding-external-key")
    reset_trust()
    yield
    reset_trust()


def _report(tmp_path: Path) -> str:
    p = tmp_path / (uuid.uuid4().hex + ".rpt")
    p.write_text("slack 0.05 ns\n", encoding="utf-8")
    return str(p)


def _evidence(check_id: str, proof, tmp_path: Path, *, nonce=None) -> Evidence:
    ev = Evidence.from_run(
        check_id=check_id, status="PASS", reason="bound tool execution",
        command=list(BENIGN), exit_code=0, analysis_view="ROUTED_EXTRACTED",
        tool_versions={"sta": "1.0"}, reports=[_report(tmp_path)],
    )
    ev.execution_proof = proof
    return ev


def _record(tmp_path: Path, check_id: str, *, proof_check_id=None, nonce=None):
    c = GateController(str(tmp_path / "state.json"))
    proof = trusted_proof_for(BENIGN, check_id=proof_check_id or check_id, nonce=nonce)
    ev = _evidence(check_id, proof, tmp_path)
    c.record_evidence(ev)
    return c, ev.evidence_id, proof


# ---------------------------------------------------------------------------
# 1. Original P0: unauthenticated index substitution (fresh + child + downstream)
# ---------------------------------------------------------------------------
def test_p0_index_substitution_fails_closed_everywhere(tmp_path):
    ctl, eid, _ = _record(tmp_path, "eco_sta")
    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    raw["check_index"].setdefault("eco_lvs", []).append(eid)
    (tmp_path / "state.json").write_text(
        json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8"
    )

    fresh = GateController(str(tmp_path / "state.json"))
    gd = fresh.evaluate_gate(
        "STA", ["eco_sta", "eco_lvs"], stage="route",
        required_view="ROUTED_EXTRACTED",
    )
    assert gd.qualified is False
    assert gd.decision == "BLOCKED"
    problems = fresh.verify_ledger_integrity()
    assert problems, "tampered index must produce a ledger problem"
    assert any(DOMAIN_CHECK_INDEX in p for p in problems), problems

    code = (
        "import json,sys;sys.path.insert(0,r'%s');"
        "from assistant.controller import GateController;"
        "c=GateController(sys.argv[1]);"
        "g=c.evaluate_gate('STA',['eco_sta','eco_lvs'],"
        "stage='route',required_view='ROUTED_EXTRACTED');"
        "print(json.dumps({'q':g.qualified,'d':g.decision,'i':c.verify_ledger_integrity()}))"
    ) % str(Path(__file__).resolve().parent.parent)
    cp = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "state.json")],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "ASSISTANT_LEDGER_KEY": "r71-binding-external-key"},
    )
    child = json.loads(cp.stdout.strip().splitlines()[-1])
    assert child["q"] is False and child["d"] == "BLOCKED" and child["i"]

    allowed, why = fresh.can_enter_stage(
        "ROUTE", [("STA", ["eco_sta", "eco_lvs"])]
    )
    assert allowed is False and why


# ---------------------------------------------------------------------------
# 2. Proof side: a proof issued for X cannot authorize evidence for Y
# ---------------------------------------------------------------------------
def test_proof_for_other_check_cannot_authorize(tmp_path):
    proofs = trusted_proof_for(BENIGN, check_id="eco_sta", nonce="r71-proof-x")
    ctl = GateController(str(tmp_path / "state.json"))
    ev = _evidence("eco_lvs", proofs, tmp_path)
    ctl.record_evidence(ev)
    eid = ev.evidence_id
    assert authoritative_qualification(ctl, [eid]) is False
    gd = ctl.evaluate_gate("X", ["eco_lvs"])
    assert gd.qualified is False


# ---------------------------------------------------------------------------
# 3/4. Occupancy + dangling reference
# ---------------------------------------------------------------------------
def test_duplicate_evidence_for_two_checks_denied(tmp_path):
    ctl, eid, proof = _record(tmp_path, "eco_sta")
    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    raw["check_index"].setdefault("eco_lvs", []).append(eid)
    (tmp_path / "state.json").write_text(
        json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8"
    )
    fresh = GateController(str(tmp_path / "state.json"))
    gd = fresh.evaluate_gate("BOTH", ["eco_sta", "eco_lvs"])
    assert gd.qualified is False and gd.decision == "BLOCKED"
    assert "eco_lvs" in gd.checks and gd.checks["eco_lvs"]["status"] == "NOT_RUN"


def test_dangling_index_reference_is_clean_blocked(tmp_path):
    ctl = GateController(str(tmp_path / "state.json"))
    proof = trusted_proof_for(BENIGN, check_id="eco_sta")
    ev = _evidence("eco_sta", proof, tmp_path)
    ctl.record_evidence(ev)
    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    raw["check_index"]["eco_sta"].append("missing-evidence-id")
    (tmp_path / "state.json").write_text(
        json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8"
    )
    fresh = GateController(str(tmp_path / "state.json"))
    gd = fresh.evaluate_gate("G", ["eco_sta"])  # must not raise KeyError
    assert gd.decision == "BLOCKED" and gd.qualified is False
    assert any("missing-evidence" in p for p in fresh.verify_ledger_integrity())


def test_deleted_index_binding_detected(tmp_path):
    ctl, eid, _ = _record(tmp_path, "eco_sta")
    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    raw["check_index"].pop("eco_sta", None)
    (tmp_path / "state.json").write_text(
        json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8"
    )
    fresh = GateController(str(tmp_path / "state.json"))
    problems = fresh.verify_ledger_integrity()
    assert any("deleted-evidence" in p for p in problems), problems
    assert fresh.evaluate_gate("G", ["eco_sta"]).qualified is False


# ---------------------------------------------------------------------------
# 5/6. Replay + concurrency
# ---------------------------------------------------------------------------
def test_replayed_proof_nonce_denied(tmp_path):
    proof = trusted_proof_for(BENIGN, check_id="eco_sta", nonce="r71-replay-1")
    ctl = GateController(str(tmp_path / "state.json"))
    first = _evidence("eco_sta", proof, tmp_path)
    ctl.record_evidence(first)
    assert authoritative_qualification(ctl, [first.evidence_id]) is True
    second = _evidence("eco_sta", proof, tmp_path)
    ctl.record_evidence(second)
    assert authoritative_qualification(ctl, [second.evidence_id]) is False


def test_concurrent_claims_one_winner(tmp_path):
    proof = trusted_proof_for(BENIGN, check_id="eco_sta", nonce="r71-race-1")
    ctl = GateController(str(tmp_path / "state.json"))
    a = _evidence("eco_sta", proof, tmp_path)
    b = _evidence("eco_sta", proof, tmp_path)
    ctl.record_evidence(a)
    ctl.record_evidence(b)
    barrier = threading.Barrier(2)
    results: dict[str, bool] = {}

    def run(key, eid):
        barrier.wait()
        results[key] = authoritative_qualification(
            GateController(str(tmp_path / "state.json")), [eid]
        )

    threads = [
        threading.Thread(target=run, args=(k, e))
        for k, e in (("a", a.evidence_id), ("b", b.evidence_id))
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert set(results) == {"a", "b"}
    assert sum(results.values()) <= 1, results


# ---------------------------------------------------------------------------
# 7/8. Recovery + downstream consistency
# ---------------------------------------------------------------------------
def test_recovery_preserves_binding(tmp_path):
    ctl, eid, _ = _record(tmp_path, "eco_sta")
    assert ctl.evaluate_gate("G", ["eco_sta"]).qualified is True
    ctl.cancel("r71 recovery")
    with pytest.raises(StateError):
        ctl.record_evidence(
            _evidence("eco_lvs", trusted_proof_for(BENIGN, check_id="eco_lvs"), tmp_path)
        )
    ctl.restart("r71 restart")
    fresh = GateController(str(tmp_path / "state.json"))
    assert fresh.verify_ledger_integrity() == []
    assert fresh.evaluate_gate("G", ["eco_sta"]).qualified is True


def test_downstream_gate_consistency(tmp_path):
    ctl, eid, _ = _record(tmp_path, "eco_sta")
    gd = ctl.evaluate_gate("UP", ["eco_sta"])
    assert gd.qualified is True
    allowed, why = ctl.can_enter_stage("ROUTE", [("UP", ["eco_sta"])])
    assert allowed is True and not why
    raw = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    raw["check_index"]["eco_lvs"] = [eid]
    (tmp_path / "state.json").write_text(
        json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8"
    )
    ctl2 = GateController(str(tmp_path / "state.json"))
    assert ctl2.can_enter_stage("ROUTE", [("UP", ["eco_sta", "eco_lvs"])])[0] is False


# ---------------------------------------------------------------------------
# 9. P0-A/B/C regressions (unchanged semantics)
# ---------------------------------------------------------------------------
def test_p0a_cancelled_controller_blocks_new_work(tmp_path):
    c = GateController(str(tmp_path / "s.json"))
    c.cancel("r71")
    with pytest.raises(StateError):
        c.record_evidence(
            _evidence("eco_sta", trusted_proof_for(BENIGN, check_id="eco_sta"), tmp_path)
        )


def test_p0b_wsl_identity_unsupported():
    name = "".join(chr(x) for x in [119, 115, 108, 46, 101, 120, 101])
    assert is_unsupported_image(name)[0] is True


def test_p0c_shell_guard_blocks():
    shell = [sys.executable, "-c", "subprocess.run(['cmd.exe','/c','echo pwn'])"]
    with pytest.raises(RuntimeError):
        r6_execution_guard(shell)
    assert decode_aware_execution_risk(shell)[0] is True
