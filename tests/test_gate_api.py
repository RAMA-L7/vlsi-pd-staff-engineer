"""SR-03: targeted tests for the central gate API (all 14 sub-findings).

All evidence is simulated in-process (no live EDA, no credentials). Each test
exercises one newly fixed behaviour of ``assistant.controller``.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

import assistant.controller as ctrl
from assistant.controller import (
    APPLICABILITY_RULES,
    ApplicabilityError,
    CheckSpec,
    GateController,
    GateDefinitionError,
    GateSpec,
    GateValidationError,
    StateError,
    Waiver,
    validate_applicability_for_check,
    validate_gate_submission,
)
from assistant.evidence import (
    VIEW_CONTRACTS,
    BindingContext,
    DependencySet,
    Evidence,
    StaleOutputError,
    ViewContract,
    artifact_path,
    contract_compatible,
    post_fill_proof_ok,
    prepare_run_output_dir,
    revalidate_evidence,
)
from assistant.statuses import Status


# --------------------------------------------------------------------------
# Simulated outcome validators (labelled; no external tools involved)
# --------------------------------------------------------------------------
def _verdict_ok(ev: Evidence, outcome: dict) -> bool:
    return outcome.get("verdict") == "PASS" and ev.status is Status.PASS


def _internal_ok(ev: Evidence, outcome: dict) -> bool:
    return outcome.get("proof") == "complete" and ev.status is Status.PASS


ctrl.register_outcome_validator("testapi_sta", _verdict_ok)
ctrl.register_outcome_validator("testapi_lec", _verdict_ok)
ctrl.register_outcome_validator("testapi_internal", _internal_ok)


#: Validated design facts backing each NA-RULE (simulated inventory, no EDA).
_NA_FACTS = {
    "has_upf": False,
    "has_macros": False,
    "has_clock_gates": False,
    "has_analog": False,
    "is_combinational": True,
}


@pytest.fixture
def ctl(tmp_path):
    c = GateController(str(tmp_path / "state.json"))
    for name, value in _NA_FACTS.items():
        c.register_design_fact(name, value, source="simulated file inventory")
    return c


def _sim(controller, ev, **kw):
    """Record deliberately minimal SIMULATED evidence (explicit strict opt-out)."""
    kw.setdefault(
        "simulated",
        "unit-test: minimal simulated evidence (no real EDA tool provenance)",
    )
    return controller.record_evidence(ev, strict=False, **kw)


def _sim_result(controller, check_id, status, reason, **kw):
    """Record a deliberately minimal SIMULATED check result (strict opt-out)."""
    kw.setdefault(
        "simulated",
        "unit-test: minimal simulated check result (no real EDA tool provenance)",
    )
    return controller.record_check_result(check_id, status, reason, strict=False, **kw)


def _pass_ev(check="sta", **kw):
    kw.setdefault("reason", "meets target")
    kw.setdefault("exit_code", 0)
    kw.setdefault("analysis_view", "ROUTED_EXTRACTED")
    return Evidence.from_run(check_id=check, status=Status.PASS, **kw)


def _genuine_pass(controller, tmp_path, check="sta", **kw):
    """Record an authoritative PASS with complete strict provenance.

    Unlike :func:`_sim`, no ``simulated`` label is used, so the record's
    ``evidence_class`` stays TOOL and the gate can report a genuine CLEAN_PASS.
    """
    rpt = tmp_path / f"{check}.rpt"
    rpt.write_text("slack 0.10 ns", encoding="utf-8")
    kw.setdefault("reason", "meets target")
    kw.setdefault("exit_code", 0)
    kw.setdefault("analysis_view", "ROUTED_EXTRACTED")
    kw.setdefault("command", [check])
    kw.setdefault("tool_versions", {check: "1.0"})
    kw.setdefault("reports", [str(rpt)])
    _ev = Evidence.from_run(check_id=check, status=Status.PASS, **kw)
    # R6.2: attach a REAL TRUSTED proof so the gate keeps its security purpose.
    try:
        import r62_proof_helper as _ph
        _d, _cmd = _ph.proof_and_command()
        if _d is not None:
            _ev.execution_proof = _d
            object.__setattr__(_ev, "command", list(_cmd))
    except Exception:
        pass
    return controller.record_evidence(_ev)


def _fail_ev(check="antenna", **kw):
    kw.setdefault("reason", "violations found")
    kw.setdefault("exit_code", 0)
    kw.setdefault("analysis_view", "ROUTED_EXTRACTED")
    return Evidence.from_run(check_id=check, status=Status.FAIL, **kw)


def _tool_spec(check, validator="testapi_sta"):
    return CheckSpec(
        check_id=check, execution_kind="tool", engineering_validator=validator
    )


# --------------------------------------------------------------------------
# SF1 - one authoritative validation boundary shared by API/CLI/runner/ECO
# --------------------------------------------------------------------------
def test_sf1_authoritative_boundary_is_exported_and_strict_provenance(tmp_path):
    assert callable(validate_gate_submission)
    sig = inspect.signature(validate_gate_submission)
    for name in ("spec", "design_facts", "engineering_outcome", "strict_provenance"):
        assert name in sig.parameters

    minimal = _pass_ev("sta")
    assert validate_gate_submission(minimal, strict_provenance=True)

    rpt = tmp_path / "sta.rpt"
    rpt.write_text("slack 0.10 ns", encoding="utf-8")
    complete = Evidence.from_run(
        "sta", Status.PASS, "meets target", command=["sta", "-report", str(rpt)],
        exit_code=0, analysis_view="ROUTED_EXTRACTED",
        tool_versions={"sta": "1.0"}, reports=[str(rpt)],
    )
    assert validate_gate_submission(complete, strict_provenance=True) == []


def test_sf1_direct_api_routes_through_same_boundary(ctl):
    # A bad NA reason is rejected through record_evidence (not just the CLI).
    ev = Evidence.from_run(
        "upf_check", Status.NOT_APPLICABLE, "",
        applicability_reason="NA-RULE:NO-UPF short",
    )
    with pytest.raises(ApplicabilityError):
        _sim(ctl,ev)
    assert ctl.latest_evidence("upf_check") is None


# --------------------------------------------------------------------------
# SF2 - PASS with exit_code=None never yields a clean PASS
# --------------------------------------------------------------------------
def test_sf2_pass_with_none_exit_code_is_blocked(ctl):
    _sim(ctl,_pass_ev("sta", exit_code=None))
    gd = ctl.evaluate_gate("g", ["sta"])
    assert not gd.passed
    assert gd.checks["sta"]["status"] == Status.FAIL.value
    assert "exit_code=None" in gd.checks["sta"]["reason"]


# --------------------------------------------------------------------------
# SF3 - empty required-check list is rejected
# --------------------------------------------------------------------------
def test_sf3_empty_required_checks_rejected(ctl):
    with pytest.raises(GateDefinitionError):
        ctl.evaluate_gate("g", [])
    with pytest.raises(GateDefinitionError):
        ctl.register_gate(GateSpec("EMPTY", ()))


# --------------------------------------------------------------------------
# SF6 - registered gate specifications; callers cannot omit required checks
# --------------------------------------------------------------------------
def test_sf6_registered_spec_prevents_omitting_required_checks(ctl):
    spec = GateSpec(
        "SIGN", ("sta", "lec"),
        checks={"sta": _tool_spec("sta"), "lec": _tool_spec("lec", "testapi_lec")},
    )
    ctl.register_gate(spec)
    _sim_result(ctl,
        "sta", Status.PASS, "meets", exit_code=0,
        analysis_view="ROUTED_EXTRACTED", engineering_outcome={"verdict": "PASS"},
    )
    # Caller tries to pass with only one of the two required checks.
    gd = ctl.evaluate_gate("SIGN", ["sta"])
    assert not gd.passed
    assert "lec" in gd.reason

    _sim_result(ctl,
        "lec", Status.PASS, "equivalent", exit_code=0,
        analysis_view="MAPPED_NETLIST", engineering_outcome={"verdict": "PASS"},
    )
    assert ctl.evaluate_gate("SIGN", ["sta"]).passed


# --------------------------------------------------------------------------
# SF4 - upstream gates are re-evaluated, not trusted from historical PASS
# --------------------------------------------------------------------------
def test_sf4_upstream_reevaluated_on_changed_inputs(ctl, tmp_path):
    sdc = tmp_path / "d.sdc"
    sdc.write_text("create_clock -period 7", encoding="utf-8")
    ctl.register_input("sdc", str(sdc))
    _sim(ctl,_pass_ev("sta"))
    assert ctl.evaluate_gate("g1", ["sta"]).passed

    sdc.write_text("create_clock -period 5", encoding="utf-8")
    gd = ctl.evaluate_gate("g2", ["sta"], upstream_gates=["g1"])
    assert not gd.passed
    assert "upstream gate g1" in gd.reason
    assert "re-evaluation" in gd.reason


# --------------------------------------------------------------------------
# SF8 - dependency cycles are detected
# --------------------------------------------------------------------------
def test_sf8_dependency_cycle_detected(ctl):
    ctl.register_gate(GateSpec(
        "A", ("sta",), upstream=("B",), checks={"sta": _tool_spec("sta")}
    ))
    ctl.register_gate(GateSpec(
        "B", ("sta",), upstream=("A",), checks={"sta": _tool_spec("sta")}
    ))
    with pytest.raises(GateDefinitionError, match="cycle"):
        ctl.evaluate_gate("A", ["sta"])


# --------------------------------------------------------------------------
# SF9 - input registrations persist safely across restart
# --------------------------------------------------------------------------
def test_sf9_input_registration_persists_across_restart(tmp_path):
    state = str(tmp_path / "state.json")
    rtl = tmp_path / "d.v"
    rtl.write_text("v1", encoding="utf-8")
    c1 = GateController(state)
    c1.register_input("rtl", str(rtl))

    c2 = GateController(state)
    assert "rtl" in c2.report()["inputs"]
    _sim(c2, _pass_ev("lec"))
    assert c2.evaluate_gate("g", ["lec"]).passed

    rtl.write_text("v2", encoding="utf-8")
    assert not c2.evaluate_gate("g", ["lec"]).passed  # stale detected after restart


# --------------------------------------------------------------------------
# SF5 - waiver expiry is enforced
# --------------------------------------------------------------------------
def test_sf5_waiver_expiry_enforced(ctl):
    _sim(ctl,_fail_ev("antenna"))
    ctl.add_waiver(Waiver(
        check_id="antenna", scope="g", reason="expired long ago waiver",
        approved_by="rv", expires_utc="2020-01-01T00:00:00Z",
    ))
    gd = ctl.evaluate_gate("g", ["antenna"])
    assert not gd.passed

    active = Waiver(
        check_id="antenna", scope="g", reason="waiver valid for a long time",
        approved_by="rv", expires_utc="2999-01-01T00:00:00Z",
    )
    ctl.add_waiver(active)
    assert ctl.evaluate_gate("g", ["antenna"]).passed


# --------------------------------------------------------------------------
# SF10 - waiver identity, scope, policy and input revision are enforced
# --------------------------------------------------------------------------
def test_sf10_waiver_identity_scope_policy_revision(ctl, tmp_path):
    rtl = tmp_path / "d.v"
    rtl.write_text("v1", encoding="utf-8")
    ctl.register_input("rtl", str(rtl))
    _sim(ctl,_fail_ev("antenna"))

    with pytest.raises(ValueError):
        ctl.add_waiver(Waiver(
            check_id="antenna", scope="g", reason="temporary waiver reason here",
            approved_by="rv", policy="unknown_policy",
        ))

    w = Waiver(
        check_id="antenna", scope="g", reason="temporary waiver reason here",
        approved_by="rv", policy="tool_gap",
    )
    ctl.add_waiver(w)
    with pytest.raises(ValueError):
        ctl.add_waiver(w)  # duplicate identity
    assert ctl.waivers_for("g", "other_check") == []  # identity/scope mismatch
    assert ctl.evaluate_gate("g", ["antenna"]).passed  # revision still matches

    rtl.write_text("v2", encoding="utf-8")
    _sim(ctl,_fail_ev("antenna"))  # fresh evidence, bound to v2
    gd = ctl.evaluate_gate("g", ["antenna"])
    assert not gd.passed  # waiver no longer applies to the new input revision


# --------------------------------------------------------------------------
# SF11 - unresolved functional proof failures are explicitly non-waivable
# --------------------------------------------------------------------------
def test_sf11_non_waivable_functional_proof_failure(ctl):
    _sim(ctl,Evidence.from_run(
        "formal_lec", Status.FAIL, "equivalence proof failed",
        exit_code=0, analysis_view="MAPPED_NETLIST",
    ))
    ctl.add_waiver(Waiver(
        check_id="formal_lec", scope="g", reason="attempt to waive proof failure",
        approved_by="rv", policy="temporary_risk_acceptance",
    ))
    gd = ctl.evaluate_gate("g", ["formal_lec"])
    assert not gd.passed
    assert "non-waivable" in gd.reason
    assert gd.checks["formal_lec"]["non_waivable"] is True


# --------------------------------------------------------------------------
# SF12 - clean PASS distinguished from waived disposition (machine + human)
# --------------------------------------------------------------------------
def test_sf12_disposition_distinguishes_clean_from_waived_pass(ctl, tmp_path):
    # F-01: a genuine tool-derived PASS is required for a CLEAN_PASS; simulated
    # evidence can only produce a SIMULATED_PASS disposition.
    _genuine_pass(ctl, tmp_path, "sta")
    clean = ctl.evaluate_gate("g", ["sta"])
    assert clean.disposition == "CLEAN_PASS"
    assert clean.to_dict()["disposition"] == "CLEAN_PASS"
    assert clean.qualified is True

    _sim(ctl,_fail_ev("antenna"))
    ctl.add_waiver(Waiver(
        check_id="antenna", scope="g", reason="reporting-only waiver reason",
        approved_by="rv",
    ))
    waived = ctl.evaluate_gate("g", ["sta", "antenna"])
    assert waived.passed
    assert waived.disposition == "WAIVED_PASS"
    assert "waived" in waived.reason.lower()
    assert "WAIVED_PASS" in waived.human_summary
    assert ctl.report()["last_gate_disposition"] == "WAIVED_PASS"


# --------------------------------------------------------------------------
# SF13 - applicability decided from validated design facts, not reason string
# --------------------------------------------------------------------------
def test_sf13_applicability_requires_validated_design_fact(ctl):
    ctl.register_design_fact("has_upf", True, source="file inventory")
    bad = Evidence.from_run(
        "upf_check", Status.NOT_APPLICABLE, "",
        applicability_reason="NA-RULE:NO-UPF design has no power intent file in scope",
    )
    with pytest.raises(ApplicabilityError):
        _sim(ctl,bad)

    ctl.register_design_fact("has_upf", False, source="file inventory")
    good = Evidence.from_run(
        "upf_check", Status.NOT_APPLICABLE, "",
        applicability_reason="NA-RULE:NO-UPF design has no power intent file in scope",
    )
    _sim(ctl,good)
    assert ctl.evaluate_gate("lp", ["upf_check"]).passed


def test_sf13_strict_boundary_rejects_factless_na():
    ev = Evidence.from_run(
        "upf_check", Status.NOT_APPLICABLE, "",
        applicability_reason="NA-RULE:NO-UPF design has no power intent file in scope",
    )
    # U-A: reason-only N/A is rejected outright (raises) in the boundary.
    with pytest.raises(ApplicabilityError, match="validated design facts"):
        validate_gate_submission(ev, strict_provenance=True)


# --------------------------------------------------------------------------
# SF14 - cancellation blocks all new work until a valid restart
# --------------------------------------------------------------------------
def test_sf14_cancellation_blocks_new_work_until_restart(ctl):
    ctl.cancel("operator request: environment changed")
    with pytest.raises(StateError):
        ctl.register_input("rtl", "x.v")
    with pytest.raises(StateError):
        ctl.register_design_fact("has_upf", False)
    with pytest.raises(StateError):
        ctl.add_waiver(Waiver(
            check_id="x", scope="g", reason="a sufficiently long waiver reason",
            approved_by="rv",
        ))
    with pytest.raises(StateError):
        ctl.record_llm_summary("advisory")
    with pytest.raises(StateError):
        _sim(ctl,_pass_ev("sta"))
    with pytest.raises(StateError):
        ctl.evaluate_gate("g", ["sta"])
    # The old cancellation escape hatch no longer works.
    with pytest.raises(StateError):
        _sim(ctl,_pass_ev("sta"), allow_while_cancelled=True)

    ctl.restart(reason="environment restored", operator="op")
    assert ctl.run_status == "ACTIVE"
    assert ctl.report()["restart_history"][-1]["reason"] == "environment restored"
    _sim(ctl,_pass_ev("sta"))
    assert ctl.evaluate_gate("g", ["sta"]).passed


# --------------------------------------------------------------------------
# SF7 - tool-executed vs pure internal checks
# --------------------------------------------------------------------------
def test_sf7_tool_check_requires_exit0_and_engineering_outcome(ctl):
    ctl.register_check_spec(_tool_spec("synth_opt"))
    with pytest.raises(GateValidationError):
        _sim_result(ctl,
            "synth_opt", Status.PASS, "claims pass", exit_code=1,
            analysis_view="MAPPED_NETLIST", engineering_outcome={"verdict": "PASS"},
        )
    with pytest.raises(GateValidationError):
        _sim_result(ctl,
            "synth_opt", Status.PASS, "no outcome", exit_code=0,
            analysis_view="MAPPED_NETLIST",
        )
    _sim_result(ctl,
        "synth_opt", Status.PASS, "mapped cleanly", exit_code=0,
        analysis_view="MAPPED_NETLIST", engineering_outcome={"verdict": "PASS"},
    )
    assert ctl.evaluate_gate("g", ["synth_opt"]).passed


def test_sf7_internal_check_requires_validator_and_contract(ctl):
    ctl.register_check_spec(CheckSpec(
        "int_check", execution_kind="internal",
        validator_type="testapi_internal", evidence_contract="parsed invariant proof",
    ))
    # Internal checks legitimately have no process exit code, but must supply
    # a validated outcome through the explicit validator contract.
    _sim_result(ctl,
        "int_check", Status.PASS, "invariant proven", exit_code=None,
        engineering_outcome={"proof": "complete"},
    )
    assert ctl.evaluate_gate("g", ["int_check"]).passed

    # Omitting the parsed outcome is refused.
    with pytest.raises(GateValidationError):
        _sim_result(ctl,"int_check", Status.PASS, "no outcome", exit_code=None)


# --------------------------------------------------------------------------
# SF6 (inputs/views) - registered specs enforce inputs and analysis views
# --------------------------------------------------------------------------
def test_sf6_gate_spec_enforces_inputs_and_view(ctl, tmp_path):
    ctl.register_gate(GateSpec(
        "VIEW_GATE", ("sta",), required_inputs=("sdc",),
        required_view="ROUTED_EXTRACTED",
        checks={"sta": CheckSpec(
            "sta", execution_kind="tool", required_view="ROUTED_EXTRACTED",
            engineering_validator="testapi_sta",
        )},
    ))
    _sim_result(ctl,
        "sta", Status.PASS, "meets", exit_code=0,
        analysis_view="ROUTED_EXTRACTED", engineering_outcome={"verdict": "PASS"},
    )
    gd = ctl.evaluate_gate("VIEW_GATE", ["sta"])
    assert not gd.passed
    assert "unregistered inputs" in gd.reason

    sdc = tmp_path / "d.sdc"
    sdc.write_text("create_clock", encoding="utf-8")
    ctl.register_input("sdc", str(sdc))
    _sim_result(ctl,
        "sta", Status.PASS, "meets", exit_code=0,
        analysis_view="ROUTED_EXTRACTED", engineering_outcome={"verdict": "PASS"},
    )
    assert ctl.evaluate_gate("VIEW_GATE", ["sta"]).passed

    # A gate requiring routed extraction cannot be satisfied by an estimate.
    _sim_result(ctl,
        "sta", Status.PASS, "estimated", exit_code=0,
        analysis_view="GR_ESTIMATE", engineering_outcome={"verdict": "PASS"},
    )
    gd = ctl.evaluate_gate("VIEW_GATE", ["sta"])
    assert not gd.passed
    assert "view mismatch" in gd.checks["sta"]["reason"]


# --------------------------------------------------------------------------
# Semantics preserved: INCONCLUSIVE is never converted to PASS
# --------------------------------------------------------------------------
def test_inconclusive_never_converted_to_pass(ctl):
    _sim(ctl,Evidence.from_run(
        "formal_lec", Status.INCONCLUSIVE, "timed out before verdict", exit_code=0
    ))
    gd = ctl.evaluate_gate("g", ["formal_lec"])
    assert not gd.passed
    assert gd.checks["formal_lec"]["status"] == Status.INCONCLUSIVE.value


# ==========================================================================
# SR-04/SR-05 wiring: revalidation at evaluation time (simulated evidence)
# ==========================================================================
def _bound_pass(tmp_path, *, view="ROUTED_EXTRACTED", design="A", corner="TT",
                deps=None, reports=(), provenance=""):
    """Build a fully bound, simulated PASS evidence record with real tmp files."""
    ctx = BindingContext(
        run_id="run1", design=design, stage="sta", tool_execution_id="exec1",
        analysis_view=view, corner=corner,
    )
    return Evidence.from_run(
        "sta", Status.PASS, "meets target", command=["sta"], exit_code=0,
        analysis_view=view, tool_versions={"sta": "1.0"}, reports=reports,
        dependencies=deps, binding_context=ctx, extraction_provenance=provenance,
    )


def test_sr04_modified_dependency_blocks_evaluation(ctl, tmp_path):
    rtl = tmp_path / "top.v"
    rtl.write_text("module top; endmodule", encoding="utf-8")
    deps = DependencySet()
    deps.add_file("rtl", "top", str(rtl))
    _sim(ctl,_bound_pass(tmp_path, deps=deps))

    assert ctl.evaluate_gate("g", ["sta"]).passed

    rtl.write_text("module top; wire x; endmodule", encoding="utf-8")
    gd = ctl.evaluate_gate("g", ["sta"])
    assert not gd.passed
    problems = gd.checks["sta"].get("revalidation_problems", [])
    assert any("dependency modified" in p for p in problems)
    assert gd.checks["sta"]["status"] == Status.NOT_RUN.value


def test_sr04_modified_and_deleted_artifacts_block(ctl, tmp_path):
    rpt = tmp_path / "sta.rpt"
    rpt.write_text("slack 0.10", encoding="utf-8")
    _sim(ctl,_bound_pass(tmp_path, reports=[str(rpt)]))
    assert ctl.evaluate_gate("g", ["sta"]).passed

    rpt.write_text("slack 99.00", encoding="utf-8")
    gd = ctl.evaluate_gate("g", ["sta"])
    assert not gd.passed
    assert any("artifact modified" in p for p in gd.checks["sta"]["revalidation_problems"])

    rpt.unlink()
    gd = ctl.evaluate_gate("g", ["sta"])
    assert not gd.passed
    assert any("artifact missing" in p for p in gd.checks["sta"]["revalidation_problems"])


def test_sr04_expected_dependency_manifest_refuses_rebind(ctl, tmp_path):
    first = tmp_path / "first.v"
    second = tmp_path / "second.v"
    first.write_text("v1", encoding="utf-8")
    second.write_text("v2", encoding="utf-8")
    expected = DependencySet()
    expected.add_file("rtl", "top", str(first))
    bound = DependencySet()
    bound.add_file("rtl", "top", str(second))

    ctl.register_gate(GateSpec(
        "DEP_GATE", ("sta",),
        expected_dependencies=expected.to_dict(),
        checks={"sta": _tool_spec("sta")},
    ))
    _sim_result(ctl,
        "sta", Status.PASS, "ok", exit_code=0, analysis_view="ROUTED_EXTRACTED",
        engineering_outcome={"verdict": "PASS"}, dependencies=bound,
    )
    gd = ctl.evaluate_gate("DEP_GATE", ["sta"])
    assert not gd.passed
    problems = gd.checks["sta"].get("revalidation_problems", [])
    assert any("rebind" in p or "input hash changed" in p for p in problems)


def test_sr04_expected_dependencies_required_when_missing(ctl, tmp_path):
    rtl = tmp_path / "top.v"
    rtl.write_text("v1", encoding="utf-8")
    expected = DependencySet()
    expected.add_file("rtl", "top", str(rtl))
    ctl.register_gate(GateSpec(
        "NEED_DEPS", ("sta",),
        expected_dependencies=expected.to_dict(),
        checks={"sta": _tool_spec("sta")},
    ))
    _sim_result(ctl,
        "sta", Status.PASS, "ok", exit_code=0, analysis_view="ROUTED_EXTRACTED",
        engineering_outcome={"verdict": "PASS"},
    )
    gd = ctl.evaluate_gate("NEED_DEPS", ["sta"])
    assert not gd.passed
    assert any("no dependency manifest" in p for p in gd.checks["sta"]["revalidation_problems"])


def test_sr04_cross_design_evidence_cannot_pass(ctl, tmp_path):
    expected = BindingContext(run_id="run1", design="design_A")
    ctl.register_gate(GateSpec(
        "DESIGN_GATE", ("sta",), expected_context=expected.to_dict(),
        checks={"sta": _tool_spec("sta")},
    ))
    _sim_result(ctl,
        "sta", Status.PASS, "ok", exit_code=0, analysis_view="ROUTED_EXTRACTED",
        engineering_outcome={"verdict": "PASS"},
        binding_context=BindingContext(run_id="run1", design="design_B"),
    )
    gd = ctl.evaluate_gate("DESIGN_GATE", ["sta"])
    assert not gd.passed
    assert any("design: mismatch" in p for p in gd.checks["sta"]["revalidation_problems"])

    _sim_result(ctl,
        "sta", Status.PASS, "ok", exit_code=0, analysis_view="ROUTED_EXTRACTED",
        engineering_outcome={"verdict": "PASS"},
        binding_context=BindingContext(run_id="run1", design="design_A"),
    )
    assert ctl.evaluate_gate("DESIGN_GATE", ["sta"]).passed


def test_sr04_cross_corner_evidence_cannot_pass(ctl, tmp_path):
    expected = BindingContext(run_id="run1", design="A", corner="TT")
    ctl.register_gate(GateSpec(
        "CORNER_GATE", ("sta",), expected_context=expected.to_dict(),
        checks={"sta": _tool_spec("sta")},
    ))
    _sim_result(ctl,
        "sta", Status.PASS, "ok", exit_code=0, analysis_view="ROUTED_EXTRACTED",
        engineering_outcome={"verdict": "PASS"},
        binding_context=BindingContext(run_id="run1", design="A", corner="SS"),
    )
    gd = ctl.evaluate_gate("CORNER_GATE", ["sta"])
    assert not gd.passed
    assert any("corner: mismatch" in p for p in gd.checks["sta"]["revalidation_problems"])


def test_sr05_physical_database_cannot_satisfy_extracted_timing(ctl):
    routed = VIEW_CONTRACTS["ROUTED_EXTRACTED"]
    assert not contract_compatible(routed, VIEW_CONTRACTS["PHYSICAL_DATABASE"])
    ctl.register_gate(GateSpec(
        "ROUTE_GATE", ("sta",), view_contract=routed.to_dict(),
        checks={"sta": _tool_spec("sta")},
    ))
    _sim_result(ctl,
        "sta", Status.PASS, "database only", exit_code=0,
        analysis_view="PHYSICAL_DATABASE", engineering_outcome={"verdict": "PASS"},
    )
    gd = ctl.evaluate_gate("ROUTE_GATE", ["sta"])
    assert not gd.passed
    assert "view contract mismatch" in gd.checks["sta"]["reason"]


def test_sr05_gr_estimate_cannot_satisfy_routed_extraction(ctl):
    routed = VIEW_CONTRACTS["ROUTED_EXTRACTED"]
    assert not contract_compatible(routed, VIEW_CONTRACTS["GR_ESTIMATE"])
    ctl.register_gate(GateSpec(
        "ROUTE_GATE", ("sta",), view_contract=routed.to_dict(),
        checks={"sta": _tool_spec("sta")},
    ))
    _sim_result(ctl,
        "sta", Status.PASS, "estimated", exit_code=0,
        analysis_view="GR_ESTIMATE", engineering_outcome={"verdict": "PASS"},
    )
    gd = ctl.evaluate_gate("ROUTE_GATE", ["sta"])
    assert not gd.passed
    assert "view contract mismatch" in gd.checks["sta"]["reason"]

    _sim_result(ctl,
        "sta", Status.PASS, "routed", exit_code=0,
        analysis_view="ROUTED_EXTRACTED", engineering_outcome={"verdict": "PASS"},
    )
    assert ctl.evaluate_gate("ROUTE_GATE", ["sta"]).passed


def test_sr05_post_fill_requires_filled_extracted_proven_proof(ctl):
    ctl.register_gate(GateSpec(
        "FILL_GATE", ("sta",),
        view_contract=VIEW_CONTRACTS["FILL_EXTRACTED"].to_dict(),
        require_post_fill_proof=True, require_extraction_provenance=True,
        checks={"sta": _tool_spec("sta")},
    ))
    # Routed-but-unfilled extraction is not a post-fill proof.
    _sim_result(ctl,
        "sta", Status.PASS, "routed", exit_code=0,
        analysis_view="ROUTED_EXTRACTED", engineering_outcome={"verdict": "PASS"},
        extraction_provenance="deck_v1",
    )
    gd = ctl.evaluate_gate("FILL_GATE", ["sta"])
    assert not gd.passed
    # Blocked either by the fill-stage contract or by the post-fill proof rule.
    assert (
        "view contract mismatch" in gd.checks["sta"]["reason"]
        or "post-fill proof required" in gd.checks["sta"]["reason"]
    )

    # Filled extraction without deck provenance is still not sufficient.
    _sim_result(ctl,
        "sta", Status.PASS, "filled", exit_code=0,
        analysis_view="FILL_EXTRACTED", engineering_outcome={"verdict": "PASS"},
    )
    gd = ctl.evaluate_gate("FILL_GATE", ["sta"])
    assert not gd.passed
    assert "post-fill proof required" in gd.checks["sta"]["reason"]

    # Filled, extracted, analysed AND deck-provenant proof passes.
    _sim_result(ctl,
        "sta", Status.PASS, "filled", exit_code=0,
        analysis_view="FILL_EXTRACTED", engineering_outcome={"verdict": "PASS"},
        extraction_provenance="deck_v1",
    )
    assert ctl.evaluate_gate("FILL_GATE", ["sta"]).passed


def test_sr04_artifacts_outside_isolated_output_dir_block(ctl, tmp_path):
    outdir = prepare_run_output_dir(tmp_path, "runA")
    outside = tmp_path / "stale.rpt"
    outside.write_text("slack 0.1", encoding="utf-8")
    ctl.register_gate(GateSpec(
        "OUT_GATE", ("sta",), expected_output_dir=outdir,
        checks={"sta": _tool_spec("sta")},
    ))
    _sim_result(ctl,
        "sta", Status.PASS, "ok", exit_code=0, analysis_view="ROUTED_EXTRACTED",
        engineering_outcome={"verdict": "PASS"}, reports=[str(outside)],
    )
    gd = ctl.evaluate_gate("OUT_GATE", ["sta"])
    assert not gd.passed
    assert any("outside run output directory" in p for p in gd.checks["sta"]["revalidation_problems"])

    inside = Path(artifact_path(outdir, "sta.rpt"))
    inside.write_text("slack 0.1", encoding="utf-8")
    _sim_result(ctl,
        "sta", Status.PASS, "ok", exit_code=0, analysis_view="ROUTED_EXTRACTED",
        engineering_outcome={"verdict": "PASS"}, reports=[str(inside)],
    )
    assert ctl.evaluate_gate("OUT_GATE", ["sta"]).passed


def test_sr04_prepared_output_dir_rejects_stale_reuse(ctl, tmp_path):
    out = ctl.prepare_output_dir("run1", base=tmp_path)
    p = Path(artifact_path(out, "old.rpt"))
    p.write_text("stale", encoding="utf-8")
    with pytest.raises(StaleOutputError):
        ctl.prepare_output_dir("run1", base=tmp_path)


# ==========================================================================
# U-A / U-B — final audit fixes (simulated evidence)
# ==========================================================================
def test_ub_direct_api_rejects_provenance_free_pass_by_default(ctl):
    """Direct record_evidence() cannot record a PASS with no provenance."""
    with pytest.raises(GateValidationError, match="command"):
        ctl.record_evidence(_pass_ev("sta"))  # strict=True default, no opt-out
    assert ctl.latest_evidence("sta") is None
    assert not ctl.evaluate_gate("g", ["sta"]).passed


def test_ub_unlabelled_strict_false_is_refused(ctl):
    """An unlabelled strict=False cannot silently bypass validation."""
    with pytest.raises(GateValidationError, match="simulated"):
        ctl.record_evidence(_pass_ev("sta"), strict=False)
    assert ctl.latest_evidence("sta") is None


def test_ub_strict_default_accepts_full_provenance(tmp_path):
    """Strict mode works with real (simulated-file) provenance, no opt-out."""
    c = GateController(str(tmp_path / "state.json"))
    rpt = tmp_path / "sta.rpt"
    rpt.write_text("slack 0.10", encoding="utf-8")
    ev = Evidence.from_run(
        "sta", Status.PASS, "meets target", command=["sta"], exit_code=0,
        analysis_view="ROUTED_EXTRACTED", tool_versions={"sta": "1.0"},
        reports=[str(rpt)],
    )
    c.record_evidence(ev)  # strict default; must succeed without simulated label
    assert c.evaluate_gate("g", ["sta"]).passed


def test_ub_minimal_evidence_requires_explicit_label(ctl):
    """The labelled opt-out is the ONLY supported way to record minimal evidence."""
    _sim(ctl, _pass_ev("sta"))
    assert ctl.latest_evidence("sta") is not None
    assert ctl.evaluate_gate("g", ["sta"]).passed


def test_ua_nonwaivable_formal_lec_cannot_be_not_applicable(ctl):
    ev = Evidence.from_run(
        "formal_lec", Status.NOT_APPLICABLE, "",
        applicability_reason="NA-RULE:NO-UPF design has no power intent file in scope",
    )
    # The fact exists, but the CHECK is non-waivable -> still refused.
    with pytest.raises(ApplicabilityError, match="non-waivable"):
        _sim(ctl, ev)
    assert ctl.latest_evidence("formal_lec") is None
    problems = validate_gate_submission(ev, design_facts=ctl.design_facts)
    assert any("non-waivable" in p for p in problems)


def test_ua_persisted_nonwaivable_na_is_blocked_at_evaluation(ctl):
    """Defense in depth: a legacy N/A record for a proof check cannot PASS."""
    ev = Evidence.from_run(
        "formal_lec", Status.NOT_APPLICABLE, "",
        applicability_reason="NA-RULE:NO-MACROS design contains no hard macros",
    )
    ctl._state["evidence"][ev.evidence_id] = ev.to_dict()
    ctl._state["check_index"].setdefault("formal_lec", []).append(ev.evidence_id)
    gd = ctl.evaluate_gate("g", ["formal_lec"])
    assert not gd.passed
    assert "non-waivable" in gd.reason
    assert gd.checks["formal_lec"]["non_waivable"] is True


def test_ua_nonwaivable_check_accepts_genuine_pass_only(ctl, tmp_path):
    # F-01: use an authoritative (strict) PASS, not a simulated one.
    rpt = tmp_path / "lec.rpt"
    rpt.write_text("equivalence proven", encoding="utf-8")
    ctl.record_evidence(
        Evidence.from_run(
            "formal_lec", Status.PASS, "equivalence proven",
            command=["lec"], exit_code=0, analysis_view="MAPPED_NETLIST",
            tool_versions={"lec": "1.0"}, reports=[str(rpt)],
        ),
        engineering_outcome={"verdict": "PASS"},
        check_spec=_tool_spec("formal_lec", "testapi_lec"),
    )
    gd = ctl.evaluate_gate("g", ["formal_lec"])
    assert gd.passed
    assert gd.disposition == "CLEAN_PASS"


def test_ua_valid_na_disposition_is_not_clean_pass(ctl):
    ev = Evidence.from_run(
        "upf_check", Status.NOT_APPLICABLE, "",
        applicability_reason="NA-RULE:NO-UPF design has no power intent file in scope",
    )
    _sim(ctl, ev)
    gd = ctl.evaluate_gate("lp", ["upf_check"])
    assert gd.passed
    assert gd.disposition == "NOT_APPLICABLE"
    assert gd.to_dict()["disposition"] == "NOT_APPLICABLE"
    assert "NOT_APPLICABLE" in gd.human_summary
    assert ctl.report()["last_gate_disposition"] == "NOT_APPLICABLE"


def test_ua_cli_applicability_helper_requires_facts_and_check_id(ctl):
    reason = "NA-RULE:NO-UPF design has no power intent file in scope"
    normalized = validate_applicability_for_check(reason, "upf_check", ctl.design_facts)
    assert normalized.startswith("NA-RULE:NO-UPF")

    with pytest.raises(ApplicabilityError, match="design facts"):
        validate_applicability_for_check(reason, "upf_check", {})
    with pytest.raises(ApplicabilityError, match="non-waivable"):
        validate_applicability_for_check(reason, "formal_lec", ctl.design_facts)
    with pytest.raises(ApplicabilityError, match="check_id"):
        validate_applicability_for_check(reason, "", ctl.design_facts)


def test_ua_controller_helper_records_validated_na_for_check(ctl):
    """CLI-facing controller helper records a validated N/A for a target check."""
    ev = ctl.mark_not_applicable_for_check(
        "upf_check",
        "NA-RULE:NO-UPF design has no power intent file in scope",
    )
    assert ev.status is Status.NOT_APPLICABLE
    assert ctl.latest_evidence("upf_check") is not None
    gd = ctl.evaluate_gate("lp", ["upf_check"])
    assert gd.passed
    assert gd.disposition == "NOT_APPLICABLE"

    with pytest.raises(ApplicabilityError, match="non-waivable"):
        ctl.mark_not_applicable_for_check(
            "formal_lec",
            "NA-RULE:NO-MACROS design contains no hard macros",
        )


# ==========================================================================
# F-01 (P1): simulated evidence must be visible and non-authoritative
# ==========================================================================
def test_f01_simulated_pass_is_persisted_and_nonauthoritative(tmp_path):
    """The exact simulated label is persisted; the gate never reports CLEAN_PASS."""
    state = tmp_path / "state.json"
    ctl = GateController(str(state))
    label = "SIMULATED-LABEL"
    ev = _sim(ctl, _pass_ev("sta"), simulated=label)

    assert ev.evidence_class == "SIMULATED"
    assert ev.simulated_by == label
    raw_text = state.read_text(encoding="utf-8")
    stored = json.loads(raw_text)["evidence"][ev.evidence_id]
    assert stored["evidence_class"] == "SIMULATED"
    assert stored["simulated_by"] == label
    assert label in raw_text  # exact label is retained in state

    gd = ctl.evaluate_gate("G", ["sta"])
    assert gd.passed and gd.decision == "PASS"  # reporting continuity
    assert gd.disposition == "SIMULATED_PASS"
    assert gd.disposition != "CLEAN_PASS"
    assert gd.qualified is False
    assert gd.simulated_checks == ["sta"]
    assert "SIMULATED_PASS" in gd.human_summary
    assert "NON-AUTHORITATIVE" in gd.human_summary
    assert gd.to_dict()["simulated_checks"] == ["sta"]
    assert gd.to_dict()["qualified"] is False
    assert ctl.report()["last_gate_disposition"] == "SIMULATED_PASS"
    assert gd.checks["sta"]["simulated_by"] == label


def test_f01_provenance_round_trips_through_dict_and_state(tmp_path):
    """to_dict/from_dict and state save/load preserve the classification."""
    ev = _pass_ev("sta")
    ev.mark_simulated("EXACT-LABEL-123")
    data = ev.to_dict()
    assert data["evidence_class"] == "SIMULATED"
    assert data["simulated_by"] == "EXACT-LABEL-123"

    restored = Evidence.from_dict(data)
    assert restored.evidence_class == "SIMULATED"
    assert restored.simulated_by == "EXACT-LABEL-123"
    assert restored.is_simulated is True

    state = tmp_path / "state.json"
    c1 = GateController(str(state))
    c1.record_evidence(restored, strict=False, simulated="EXACT-LABEL-123")
    raw = json.loads(state.read_text(encoding="utf-8"))
    assert raw["evidence"][ev.evidence_id]["evidence_class"] == "SIMULATED"
    assert raw["evidence_provenance"][ev.evidence_id]["simulated_by"] == "EXACT-LABEL-123"

    c2 = GateController(str(state))  # fresh controller loads persisted state
    gd = c2.evaluate_gate("G", ["sta"])
    assert gd.disposition == "SIMULATED_PASS"
    assert gd.qualified is False


def test_f01_tampered_evidence_record_cannot_strip_simulated_class(tmp_path):
    """Editing one serialized evidence record cannot forge a clean PASS."""
    state = tmp_path / "state.json"
    c1 = GateController(str(state))
    ev = _sim(c1, _pass_ev("sta"), simulated="SIMULATED-LABEL")
    eid = ev.evidence_id

    raw = json.loads(state.read_text(encoding="utf-8"))
    raw["evidence"][eid]["evidence_class"] = "TOOL"       # attacker rewrite
    raw["evidence"][eid]["simulated_by"] = ""             # attacker rewrite
    state.write_text(json.dumps(raw), encoding="utf-8")

    c2 = GateController(str(state))
    gd = c2.evaluate_gate("G", ["sta"])
    assert gd.disposition == "SIMULATED_PASS"  # independent provenance map wins
    assert gd.qualified is False
    assert "sta" in gd.simulated_checks


def test_f01_simulated_pass_cannot_authorize_downstream_stage(ctl, tmp_path):
    """A SIMULATED_PASS is not qualified to authorize a downstream stage."""
    _sim(ctl, _pass_ev("sta"))
    ok, reasons = ctl.can_enter_stage("next", [("G", ["sta"])])
    assert not ok
    assert any("SIMULATED_PASS" in r for r in reasons)

    # A genuine tool-derived PASS still authorizes the stage.
    c2 = GateController(str(tmp_path / "state2.json"))
    _genuine_pass(c2, tmp_path, "sta")
    ok2, reasons2 = c2.can_enter_stage("next", [("G", ["sta"])])
    assert ok2, reasons2


def test_f01_simulated_pass_with_valid_validator_is_still_nonqualified(ctl):
    """Even a parsed engineering outcome cannot qualify simulated evidence."""
    ctl.register_gate(GateSpec(
        "SIM_SIGN", ("sta",), checks={"sta": _tool_spec("sta")},
    ))
    _sim_result(
        ctl, "sta", Status.PASS, "meets", exit_code=0,
        analysis_view="ROUTED_EXTRACTED",
        engineering_outcome={"verdict": "PASS"},
    )
    gd = ctl.evaluate_gate("SIM_SIGN", ["sta"])
    assert gd.passed
    assert gd.disposition == "SIMULATED_PASS"
    assert gd.qualified is False


# ==========================================================================
# F-07 (P1): NO-MACROS cannot justify physical-verification N/A
# ==========================================================================
_NO_MACROS_DRC = (
    "NA-RULE:NO-MACROS design contains no hard macros; routed DRC is "
    "therefore explicitly not applicable"
)


def test_f07_no_macros_cannot_exempt_physical_checks(tmp_path):
    """Rule-to-check binding closes NO-MACROS on every physical check."""
    ctl = GateController(str(tmp_path / "state.json"))
    ctl.register_design_fact("has_macros", False, source="simulated inventory")
    ctl.register_design_fact(
        "eco_physical_implementation_present", True, source="routed DEF/GDS inventory"
    )
    # The rule remains documented/available for macro-specific checks.
    assert "NA-RULE:NO-MACROS" in APPLICABILITY_RULES

    for check in ("eco_physical_drc", "eco_physical_lvs", "sta", "gates"):
        with pytest.raises(ApplicabilityError, match="cannot justify"):
            validate_applicability_for_check(_NO_MACROS_DRC, check, ctl.design_facts)
        with pytest.raises(ApplicabilityError, match="cannot justify"):
            ctl.mark_not_applicable_for_check(check, _NO_MACROS_DRC)

    # The controller's single authoritative boundary is covered too.
    ev = Evidence.from_run(
        "eco_physical_drc", Status.NOT_APPLICABLE, "",
        applicability_reason=_NO_MACROS_DRC,
    )
    with pytest.raises(ApplicabilityError, match="cannot justify"):
        ctl.record_evidence(ev)
    assert ctl.latest_evidence("eco_physical_drc") is None


def test_f07_tampered_na_record_for_physical_drc_is_blocked(ctl):
    """A serialized legacy NO-MACROS N/A record is rejected at evaluation."""
    ctl.register_design_fact(
        "eco_physical_implementation_present", True, source="routed DEF/GDS"
    )
    ev = Evidence.from_run(
        "eco_physical_drc", Status.NOT_APPLICABLE, "",
        applicability_reason=_NO_MACROS_DRC,
    )
    ctl._state["evidence"][ev.evidence_id] = ev.to_dict()
    ctl._state["check_index"].setdefault("eco_physical_drc", []).append(ev.evidence_id)
    gd = ctl.evaluate_gate("DRC", ["eco_physical_drc"])
    assert not gd.passed
    assert "invalid NOT_APPLICABLE" in gd.reason
    assert "cannot justify" in gd.checks["eco_physical_drc"]["reason"]


def test_f07_macro_specific_no_macros_na_still_valid(ctl):
    """Guard: a genuinely macro-specific N/A is not broken by the binding."""
    reason = (
        "NA-RULE:NO-MACROS design contains no hard macros; macro antenna "
        "checks are therefore not applicable"
    )
    ev = ctl.mark_not_applicable_for_check("macro_antenna", reason)
    assert ev.status is Status.NOT_APPLICABLE
    # R6.2: add a proof-bearing check so the gate can authorize production.
    from pathlib import Path as _Path
    _genuine_pass(ctl, _Path(str(ctl.state_path)).parent, "macro_proof")
    gd = ctl.evaluate_gate("MACRO", ["macro_antenna", "macro_proof"])
    assert gd.passed
    assert gd.disposition == "NOT_APPLICABLE"
    assert gd.qualified is True


def test_f07_physical_check_na_requires_physical_scope_fact(ctl):
    """Guard: only the physical-scope rule with a corroborating fact can exempt."""
    physical_na = (
        "NA-RULE:NO-PHYSICAL-IMPLEMENTATION no placed or routed physical "
        "implementation for this netlist-only candidate"
    )
    ctl.register_design_fact(
        "eco_physical_implementation_present", True, source="routed DEF/GDS"
    )
    with pytest.raises(ApplicabilityError, match="contradicts"):
        ctl.mark_not_applicable_for_check("eco_physical_drc", physical_na)

    ctl.register_design_fact(
        "eco_physical_implementation_present", False, source="RTL-only inventory"
    )
    ctl.mark_not_applicable_for_check("eco_physical_drc", physical_na)
    gd = ctl.evaluate_gate("DRC", ["eco_physical_drc"])
    assert gd.passed
    assert gd.disposition == "NOT_APPLICABLE"


# ==========================================================================
# Diagnostics guard: synthetic ECO digests are not registered file inputs
# ==========================================================================
def test_synthetic_revision_digest_is_not_a_stale_file_input(ctl):
    """ECO __*-revision__ digests must not hide a genuine FAIL verdict.

    These opaque digests are verified by the ECO revalidation path, not by the
    controller's file-input hash registry. Genuine registered-input staleness
    is still enforced (no check is weakened).
    """
    ev = _pass_ev("eco_lec")
    ev.input_hashes = {
        "__candidate_revision__": "R1",
        "__dependencies__": "D1",
    }
    assert ev.stale_inputs({}) == []
    assert ev.is_stale({}) is False

    ev.input_hashes["rtl"] = "H1"
    assert ev.stale_inputs({"rtl": "H1"}) == []
    assert ev.stale_inputs({"rtl": "H2"}) == ["rtl"]
    assert ev.is_stale({"rtl": "H2"}) is True

