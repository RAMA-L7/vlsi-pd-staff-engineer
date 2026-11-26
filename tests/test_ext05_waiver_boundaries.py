"""EXT-05 / B-03: waiver boundary regression tests.

Policy under test (see assistant.controller "EXT-05 / B-03 -- explicit waiver
policy"): a waiver is a reporting decision, never an authorization grant.

* POSITIVE: a legitimate, scoped, MAC-bound waiver on a waivable check IS
  honored -- the gate reaches decision=PASS / disposition=WAIVED_PASS and the
  waiver is visible. The underlying FAIL stays visible and the gate is NOT
  production-qualified, so it cannot authorize a downstream stage.
* NEGATIVE: mandatory functional-proof / integrity / revalidation failures and
  cross-gate scope misuse can never be waived into a qualified PASS.
* GUARDS: genuine clean PASS and validated NOT_APPLICABLE still qualify, so the
  fix is not over-broad.

No live EDA, no credentials. All evidence is simulated/labelled; genuine PASS
uses a real TRUSTED proof from the managed boundary when available.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from assistant.controller import GateController, Waiver
from assistant.evidence import Evidence
from assistant.statuses import Status

import r62_proof_helper as ph


def _genuine_pass(ctl: GateController, tmp_path, check: str) -> Evidence:
    rpt = tmp_path / f"{check}_ext05.rpt"
    rpt.write_text("EXT-05 harness report slack 0.10 ns\n", encoding="utf-8")
    ev = Evidence.from_run(
        check_id=check, status=Status.PASS, reason="meets target", exit_code=0,
        analysis_view="ROUTED_EXTRACTED", command=list(ph.APPROVED_COMMAND),
        tool_versions={check: "1.0"}, reports=[str(rpt)],
    )
    proof, _cmd = ph.proof_and_command(check_id=check)
    if proof is not None:
        ev.execution_proof = proof
        object.__setattr__(ev, "command", list(_cmd))
    return ctl.record_evidence(ev)


def _sim_fail(ctl: GateController, check: str) -> None:
    ctl.record_evidence(
        Evidence.from_run(
            check_id=check, status=Status.FAIL, reason="violations found",
            exit_code=0, analysis_view="ROUTED_EXTRACTED",
        ),
        strict=False, simulated="EXT-05 harness simulated finding",
    )


@pytest.fixture
def ctl(tmp_path):
    return GateController(str(tmp_path / "state.json"))


# ---------------------------------------------------------------------------
# POSITIVE -- a legitimate, scoped waiver is honored (reporting PASS) but does
# not carry downstream authorization.
# ---------------------------------------------------------------------------
def test_positive_waivable_waiver_honored_but_non_authorizing(ctl, tmp_path):
    _genuine_pass(ctl, tmp_path, "eco_lvs")
    _sim_fail(ctl, "eco_sta")

    before = ctl.evaluate_gate("BOTH", ["eco_lvs", "eco_sta"])
    assert before.disposition == "BLOCKED" and before.qualified is False

    ctl.add_waiver(Waiver(
        check_id="eco_sta", scope="BOTH",
        reason="documented, approved tool gap for this corner",
        approved_by="reviewer", policy="tool_gap",
    ))
    after = ctl.evaluate_gate("BOTH", ["eco_lvs", "eco_sta"])
    # The waiver is honored: gate decision is PASS and the exception is visible.
    assert after.passed is True
    assert after.disposition == "WAIVED_PASS"
    assert after.waived_checks == ["eco_sta"]
    assert after.waivers and after.waivers[0]["check_id"] == "eco_sta"
    # The waived FAIL stays a FAIL and is non-authorizing (EXT-05 / B-03).
    assert after.checks["eco_sta"]["status"] == "FAIL"
    assert after.qualified is False
    ok, why = ctl.can_enter_stage("ROUTE", [("BOTH", ["eco_lvs", "eco_sta"])])
    assert ok is False
    assert any("WAIVED_PASS" in w for w in why)


# ---------------------------------------------------------------------------
# NEGATIVE -- mandatory / integrity / acceptance failures are hard blockers.
# ---------------------------------------------------------------------------
def test_negative_functional_proof_fail_cannot_be_waived(ctl, tmp_path):
    _genuine_pass(ctl, tmp_path, "eco_lvs")
    _sim_fail(ctl, "formal_lec")
    ctl.add_waiver(Waiver(
        check_id="formal_lec", scope="BOTH",
        reason="attempt to waive an unresolved equivalence proof failure",
        approved_by="reviewer", policy="temporary_risk_acceptance",
    ))
    gd = ctl.evaluate_gate("BOTH", ["eco_lvs", "formal_lec"])
    assert gd.disposition == "BLOCKED"
    assert gd.qualified is False
    assert gd.checks["formal_lec"]["non_waivable"] is True
    assert "non-waivable" in gd.reason
    ok, _ = ctl.can_enter_stage("ROUTE", [("BOTH", ["eco_lvs", "formal_lec"])])
    assert ok is False


def test_negative_integrity_revalidation_fail_cannot_be_waived(ctl, tmp_path):
    _genuine_pass(ctl, tmp_path, "eco_lvs")
    rpt = tmp_path / "eco_sta_ext05.rpt"
    rpt.write_text("EXT-05 artifact before corruption\n", encoding="utf-8")
    ev = Evidence.from_run(
        check_id="eco_sta", status=Status.FAIL, reason="violations found",
        exit_code=0, analysis_view="ROUTED_EXTRACTED",
        command=list(ph.APPROVED_COMMAND), tool_versions={"eco_sta": "1.0"},
        reports=[str(rpt)],
    )
    ctl.record_evidence(ev, strict=False, simulated="EXT-05 harness simulated finding")
    # Mutate the persistent artifact AFTER recording -> revalidation must block.
    rpt.write_text("EXT-05 artifact corrupted after recording\n", encoding="utf-8")
    ctl.add_waiver(Waiver(
        check_id="eco_sta", scope="BOTH",
        reason="attempt to waive a corrupted-artifact integrity failure",
        approved_by="reviewer", policy="tool_gap",
    ))
    gd = ctl.evaluate_gate("BOTH", ["eco_lvs", "eco_sta"])
    assert gd.qualified is False
    assert gd.waived_checks == []
    assert gd.checks["eco_sta"].get("revalidation_problems")
    ok, _ = ctl.can_enter_stage("ROUTE", [("BOTH", ["eco_lvs", "eco_sta"])])
    assert ok is False


def test_negative_cross_gate_scope_misuse_denied(ctl, tmp_path):
    _genuine_pass(ctl, tmp_path, "eco_lvs")
    _sim_fail(ctl, "eco_sta")
    ctl.add_waiver(Waiver(
        check_id="eco_sta", scope="SCOPE_A",
        reason="waiver scoped only to SCOPE_A, not to BOTH",
        approved_by="reviewer", policy="tool_gap",
    ))
    scoped = ctl.evaluate_gate("SCOPE_A", ["eco_lvs", "eco_sta"])
    assert scoped.disposition == "WAIVED_PASS"
    other = ctl.evaluate_gate("BOTH", ["eco_lvs", "eco_sta"])
    assert other.disposition == "BLOCKED"
    assert other.qualified is False
    ok, _ = ctl.can_enter_stage("ROUTE", [("BOTH", ["eco_lvs", "eco_sta"])])
    assert ok is False


def test_negative_forged_ledger_waiver_ignored(ctl, tmp_path):
    _genuine_pass(ctl, tmp_path, "eco_lvs")
    _sim_fail(ctl, "eco_sta")
    state_path = Path(ctl.state_path)
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    raw["waivers"].append(dict(
        waiver_id="FORGED-NO-MAC", check_id="eco_sta", scope="BOTH",
        reason="forged ledger-only waiver without an external-key MAC",
        approved_by="attacker", policy="temporary_risk_acceptance",
        created_utc="2026-11-25T00:00:00Z", expires_utc=None,
        input_revision={}, design_scope="",
    ))
    state_path.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
    fresh = GateController(str(state_path))
    gd = fresh.evaluate_gate("BOTH", ["eco_lvs", "eco_sta"])
    assert gd.disposition == "BLOCKED" and gd.qualified is False
    assert gd.waived_checks == []


def test_negative_expired_waiver_ignored(ctl, tmp_path):
    _genuine_pass(ctl, tmp_path, "eco_lvs")
    _sim_fail(ctl, "eco_sta")
    ctl.add_waiver(Waiver(
        check_id="eco_sta", scope="BOTH", reason="already expired waiver for this check",
        approved_by="reviewer", policy="tool_gap", expires_utc="2020-01-01T00:00:00Z",
    ))
    gd = ctl.evaluate_gate("BOTH", ["eco_lvs", "eco_sta"])
    assert gd.disposition == "BLOCKED" and gd.qualified is False
    assert gd.waived_checks == []


# ---------------------------------------------------------------------------
# GUARDS -- the fix must not remove legitimate non-waiver authorizations.
# ---------------------------------------------------------------------------
def test_guard_clean_pass_still_authorizes(ctl, tmp_path):
    _genuine_pass(ctl, tmp_path, "eco_lvs")
    gd = ctl.evaluate_gate("BOTH", ["eco_lvs"])
    assert gd.disposition == "CLEAN_PASS" and gd.qualified is True
    ok, why = ctl.can_enter_stage("ROUTE", [("BOTH", ["eco_lvs"])])
    assert ok is True, why


def test_guard_validated_na_still_authorizes(ctl, tmp_path):
    _genuine_pass(ctl, tmp_path, "eco_lvs")
    ctl.register_design_fact("has_upf", False, source="EXT-05 harness inventory")
    ctl.mark_not_applicable_for_check(
        "upf_check",
        "NA-RULE:NO-UPF design has no UPF/CPF power-intent file in scope",
        design_facts={"has_upf": False},
    )
    gd = ctl.evaluate_gate("BOTH", ["eco_lvs", "upf_check"])
    assert gd.disposition == "NOT_APPLICABLE" and gd.qualified is True
    ok, why = ctl.can_enter_stage("ROUTE", [("BOTH", ["eco_lvs", "upf_check"])])
    assert ok is True, why
