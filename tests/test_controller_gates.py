"""Gate controller tests: the full matrix of blocking/bypass/staleness rules.

Covers the mission's required failure-injection cases at the controller level:
stale reports, changed constraints, missing artifacts, malformed output,
tool failure, formal timeout, and attempted gate bypass.
"""
import json

import pytest

from assistant.controller import (
    APPLICABILITY_RULES,
    ApplicabilityError,
    GateBypassError,
    GateController,
    GateValidationError,
    StateError,
    Waiver,
)
from assistant.evidence import Evidence
from assistant.statuses import Status


#: Validated design facts backing every NA-RULE used below. These are
#: simulated inventory facts (no live EDA), but they are real registered facts
#: so N/A decisions are honest rather than free-text.
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
    """Record deliberately minimal SIMULATED evidence.

    U-B: strict provenance is the default. These unit tests use hand-built
    evidence with no real tool invocation, so they opt out explicitly with a
    descriptive ``simulated`` label rather than silently bypassing validation.
    """
    kw.setdefault(
        "simulated",
        "unit-test: minimal simulated evidence (no real EDA tool provenance)",
    )
    return controller.record_evidence(ev, strict=False, **kw)


def _pass_ev(check="sta", **kw):
    kw.setdefault("reason", "meets target")
    kw.setdefault("exit_code", 0)
    kw.setdefault("analysis_view", "ROUTED_EXTRACTED")
    return Evidence.from_run(check_id=check, status=Status.PASS, **kw)


# ------------------------------------------------------------- happy path
def test_gate_passes_when_all_required_checks_pass(ctl):
    _sim(ctl,_pass_ev("synth"))
    _sim(ctl,_pass_ev("lec"))
    gd = ctl.evaluate_gate("g1", ["synth", "lec"])
    assert gd.passed
    assert gd.decision == "PASS"
    assert "all required checks PASS" == gd.reason


def test_missing_evidence_blocks_gate(ctl):
    gd = ctl.evaluate_gate("g1", ["sta"])
    assert not gd.passed
    assert "NOT_RUN" in gd.reason


# ------------------------------------------------------- stale / changed inputs
def test_changed_constraint_invalidates_evidence(ctl, tmp_path):
    sdc = tmp_path / "design.sdc"
    sdc.write_text("create_clock -period 7 [get_ports clk]", encoding="utf-8")
    ctl.register_input("sdc", str(sdc))
    _sim(ctl,_pass_ev("sta"))

    # gate passes while constraint unchanged
    assert ctl.evaluate_gate("sta_gate", ["sta"]).passed

    # constraint changes -> evidence stale -> gate blocked
    sdc.write_text("create_clock -period 5 [get_ports clk]", encoding="utf-8")
    gd = ctl.evaluate_gate("sta_gate", ["sta"])
    assert not gd.passed
    assert "stale" in gd.reason.lower()
    entry = gd.checks["sta"]
    assert entry["status"] == Status.NOT_RUN.value
    assert entry.get("stale") is True
    assert "sdc" in entry["reason"]


def test_rtl_change_invalidates_downstream_check(ctl, tmp_path):
    rtl = tmp_path / "d.v"
    rtl.write_text("module d; endmodule", encoding="utf-8")
    ctl.register_input("rtl", str(rtl))
    _sim(ctl,_pass_ev("lec"))
    rtl.write_text("module d; wire x; endmodule", encoding="utf-8")
    gd = ctl.evaluate_gate("lec_gate", ["lec"])
    assert not gd.passed


def test_stale_evidence_recorded_but_new_evidence_recovers(ctl, tmp_path):
    rtl = tmp_path / "d.v"
    rtl.write_text("v1", encoding="utf-8")
    ctl.register_input("rtl", str(rtl))
    _sim(ctl,_pass_ev("lec"))
    rtl.write_text("v2", encoding="utf-8")
    assert not ctl.evaluate_gate("g", ["lec"]).passed
    # re-run produces fresh evidence bound to the new hash
    _sim(ctl,_pass_ev("lec"))
    assert ctl.evaluate_gate("g", ["lec"]).passed


# ------------------------------------------------------- missing artifacts
def test_pass_with_missing_report_is_rejected(ctl, tmp_path):
    ev = _pass_ev("sta", reports=[str(tmp_path / "sta.rpt")])
    _sim(ctl,ev)
    gd = ctl.evaluate_gate("g", ["sta"])
    assert not gd.passed
    assert "missing report artifacts" in gd.checks["sta"]["reason"]


# ------------------------------------------------------- tool failure
def test_pass_with_nonzero_exit_is_rejected(ctl):
    ev = _pass_ev("lec")
    ev.exit_code = 1
    _sim(ctl,ev)
    gd = ctl.evaluate_gate("g", ["lec"])
    assert not gd.passed
    assert "exit_code=1" in gd.checks["lec"]["reason"]


def test_zero_exit_alone_does_not_prove_correctness(ctl):
    # exit 0 but recorded FAIL stays FAIL -> gate blocked
    ev = Evidence.from_run(
        check_id="sta", status=Status.FAIL, reason="unconstrained paths",
        exit_code=0, analysis_view="ROUTED_EXTRACTED",
    )
    _sim(ctl,ev)
    gd = ctl.evaluate_gate("g", ["sta"])
    assert not gd.passed
    assert gd.checks["sta"]["status"] == Status.FAIL.value


# ------------------------------------------------------- formal timeout
def test_formal_timeout_blocks_gate(ctl, tmp_path):
    log = tmp_path / "lec_timeout.log"
    log.write_text("timeout after 600s; equiv_status never ran", encoding="utf-8")
    ev = Evidence.from_run(
        check_id="formal_lec",
        status=Status.INCONCLUSIVE,
        reason="formal run timed out before a verdict",
        command=["yosys", "-s", "lec.ys"],
        exit_code=None,
        analysis_view="MAPPED_NETLIST",
        logs=[str(log)],
    )
    _sim(ctl,ev)
    gd = ctl.evaluate_gate("lec_gate", ["formal_lec"])
    assert not gd.passed
    assert gd.checks["formal_lec"]["status"] == Status.INCONCLUSIVE.value
    # log preserved
    assert gd.checks["formal_lec"]


# ------------------------------------------------------- malformed output
def test_malformed_report_evidence_blocks(ctl, tmp_path):
    rpt = tmp_path / "garbage.rpt"
    rpt.write_text("\x00\x01 not a report", encoding="utf-8", errors="replace")
    ev = Evidence.from_run(
        check_id="sta",
        status=Status.FAIL,
        reason="malformed report: no slack tokens parseable",
        exit_code=0,
        analysis_view="ROUTED_EXTRACTED",
        reports=[str(rpt)],
    )
    _sim(ctl,ev)
    gd = ctl.evaluate_gate("g", ["sta"])
    assert not gd.passed


# ------------------------------------------------------- bypass attempts
def test_force_pass_refused(ctl):
    _sim(ctl,_pass_ev("sta"))
    with pytest.raises(GateBypassError):
        ctl.force_pass("g1", justification="please")


def test_authorize_from_summary_refused(ctl):
    ctl.record_llm_summary("everything looks fine, let it pass")
    with pytest.raises(GateBypassError):
        ctl.authorize_from_summary("g1")
    # and the summary cannot change the outcome either
    gd = ctl.evaluate_gate("g1", ["sta"])
    assert not gd.passed
    assert gd.advisory_summaries == 1


def test_llm_summary_never_authenticates_gate_even_with_passing_evidence_absent(ctl):
    ctl.record_llm_summary("trust me")
    gd = ctl.evaluate_gate("g1", ["anything"])
    assert not gd.passed


# ------------------------------------------------------- NOT_APPLICABLE rules
def test_na_requires_validated_reason(ctl):
    ev = Evidence.from_run(
        check_id="upf_check", status=Status.NOT_APPLICABLE, reason=""
    )
    ev.applicability_reason = "not needed"
    with pytest.raises(ApplicabilityError):
        ctl.mark_not_applicable(ev)

    ev2 = Evidence.from_run(
        check_id="upf_check", status=Status.NOT_APPLICABLE, reason=""
    )
    ev2.applicability_reason = "NA-RULE:NO-UPF design has no UPF/CPF power intent file in scope"
    ctl.mark_not_applicable(ev2)
    gd = ctl.evaluate_gate("lp_gate", ["upf_check"])
    assert gd.passed  # validated N/A never blocks


def test_na_rejects_unknown_rule(ctl):
    ev = Evidence.from_run(
        check_id="x", status=Status.NOT_APPLICABLE, reason=""
    )
    ev.applicability_reason = "NA-RULE:BOGUS this rule does not exist at all"
    with pytest.raises(ApplicabilityError):
        ctl.mark_not_applicable(ev)


def test_na_rule_registry_known():
    assert "NA-RULE:NO-UPF" in APPLICABILITY_RULES
    assert "NA-RULE:NO-MACROS" in APPLICABILITY_RULES


# --------------------------- NA validation at EVERY recording boundary (gap 1)
def test_record_evidence_direct_na_empty_reason_raises_and_not_recorded(ctl):
    """Direct record_evidence() with an empty NA reason must fail, not PASS."""
    ev = Evidence.from_run(
        check_id="upf_check", status=Status.NOT_APPLICABLE, reason="",
        applicability_reason="",
    )
    with pytest.raises(ApplicabilityError):
        _sim(ctl,ev)
    assert ctl.latest_evidence("upf_check") is None
    # No valid evidence -> required check falls back to NOT_RUN -> gate blocked.
    gd = ctl.evaluate_gate("lp_gate", ["upf_check"])
    assert not gd.passed
    assert gd.checks["upf_check"]["status"] == Status.NOT_RUN.value


def test_record_evidence_direct_na_unknown_rule_raises(ctl):
    ev = Evidence.from_run(
        check_id="x", status=Status.NOT_APPLICABLE, reason="",
        applicability_reason="NA-RULE:BOGUS this rule is not in the registry at all",
    )
    with pytest.raises(ApplicabilityError):
        _sim(ctl,ev)
    assert ctl.latest_evidence("x") is None


def test_record_evidence_direct_na_valid_reason_succeeds_and_gate_passes(ctl):
    ev = Evidence.from_run(
        check_id="upf_check", status=Status.NOT_APPLICABLE, reason="",
        applicability_reason="NA-RULE:NO-UPF design has no UPF/CPF power intent file in scope",
    )
    recorded = _sim(ctl,ev)
    assert recorded.applicability_reason.startswith("NA-RULE:NO-UPF")
    gd = ctl.evaluate_gate("lp_gate", ["upf_check"])
    assert gd.passed
    assert gd.checks["upf_check"]["status"] == Status.NOT_APPLICABLE.value


def test_direct_na_evidence_cannot_bypass_gate_to_pass(ctl):
    """The historical bypass (record NA directly with no reason) is closed."""
    ev = Evidence(
        check_id="upf_check", status=Status.NOT_APPLICABLE, reason="",
        applicability_reason="",
    )
    with pytest.raises(ApplicabilityError):
        _sim(ctl,ev)
    assert not ctl.evaluate_gate("lp_gate", ["upf_check"]).passed


def test_valid_na_non_blocking_alongside_passing_checks(ctl):
    _sim(ctl,_pass_ev("sta"))
    _sim(ctl,Evidence.from_run(
        check_id="upf_check", status=Status.NOT_APPLICABLE, reason="",
        applicability_reason="NA-RULE:NO-UPF design has no UPF power intent file in scope",
    ))
    assert ctl.evaluate_gate("g", ["sta", "upf_check"]).passed


def test_mark_not_applicable_and_direct_record_share_validation(ctl):
    """Both entry points reject the same invalid reason (no divergent logic)."""
    bad = "NA-RULE:NO-UPF short"  # too short to justify the rule
    ev1 = Evidence.from_run(
        check_id="a", status=Status.NOT_APPLICABLE, reason="",
        applicability_reason=bad,
    )
    with pytest.raises(ApplicabilityError):
        ctl.mark_not_applicable(ev1)
    ev2 = Evidence.from_run(
        check_id="b", status=Status.NOT_APPLICABLE, reason="",
        applicability_reason=bad,
    )
    with pytest.raises(ApplicabilityError):
        _sim(ctl,ev2)


# ------------------------------------------------------- waivers
def test_waiver_is_scoped_recorded_visible(ctl):
    ev = Evidence.from_run(
        check_id="antenna", status=Status.FAIL, reason="violations found",
        exit_code=0, analysis_view="ROUTED_EXTRACTED",
    )
    _sim(ctl,ev)
    # no waiver -> blocked
    assert not ctl.evaluate_gate("route_gate", ["antenna"]).passed

    ctl.add_waiver(
        Waiver(
            check_id="antenna",
            scope="route_gate",
            reason="temporary: antenna deck not available locally, tracked as known gap",
            approved_by="reviewer@example",
        )
    )
    gd = ctl.evaluate_gate("route_gate", ["antenna"])
    assert gd.passed
    # waiver visible in the decision
    assert gd.waivers and gd.waivers[0]["check_id"] == "antenna"


def test_waiver_scope_does_not_leak_to_other_gate(ctl):
    ev = Evidence.from_run(
        check_id="antenna", status=Status.FAIL, reason="violations", exit_code=0
    )
    _sim(ctl,ev)
    ctl.add_waiver(
        Waiver(check_id="antenna", scope="route_gate",
               reason="scoped waiver for route gate only", approved_by="rv")
    )
    assert not ctl.evaluate_gate("signoff_gate", ["antenna"]).passed


def test_waiver_requires_reason_and_approver(ctl):
    with pytest.raises(ValueError):
        ctl.add_waiver(Waiver(check_id="x", scope="g", reason="short", approved_by="a"))
    with pytest.raises(ValueError):
        ctl.add_waiver(
            Waiver(check_id="x", scope="", reason="long enough reason here", approved_by="a")
        )
    with pytest.raises(ValueError):
        ctl.add_waiver(
            Waiver(check_id="x", scope="g", reason="long enough reason here", approved_by="")
        )


# ------------------------------------------------------- upstream gating
def test_upstream_gate_must_pass_first(ctl):
    _sim(ctl,_pass_ev("sta"))
    # g2 requires upstream g1 which has no PASS history
    gd = ctl.evaluate_gate("g2", ["sta"], upstream_gates=["g1"])
    assert not gd.passed
    assert "upstream gate g1 not PASS" in gd.reason
    # pass g1, then g2 evaluates cleanly
    ctl.evaluate_gate("g1", ["sta"])
    gd2 = ctl.evaluate_gate("g2", ["sta"], upstream_gates=["g1"])
    assert gd2.passed


# ------------------------------------------------------- view enforcement
def test_estimate_cannot_satisfy_routed_gate(ctl):
    ev = _pass_ev("parasitics", analysis_view="GR_ESTIMATE")
    _sim(ctl,ev)
    gd = ctl.evaluate_gate("signoff_gate", ["parasitics"],
                           required_view="ROUTED_EXTRACTED")
    assert not gd.passed
    assert "view mismatch" in gd.checks["parasitics"]["reason"]
    # same evidence satisfies a GR-level gate
    assert ctl.evaluate_gate("gr_gate", ["parasitics"],
                             required_view="GR_ESTIMATE").passed


# ------------------------------------------------------- cancel / restart
def test_cancel_blocks_and_restart_preserves_evidence(ctl):
    _sim(ctl,_pass_ev("sta"))
    ctl.cancel("operator request: environment changed")
    with pytest.raises(StateError):
        ctl.evaluate_gate("g", ["sta"])
    with pytest.raises(StateError):
        _sim(ctl,_pass_ev("lec"))
    ctl.restart()
    assert ctl.run_status == "ACTIVE"
    # evidence preserved across cancel/restart
    assert ctl.latest_evidence("sta") is not None
    assert ctl.evaluate_gate("g", ["sta"]).passed


def test_state_persists_across_instances(tmp_path):
    path = str(tmp_path / "state.json")
    c1 = GateController(path)
    _sim(c1,_pass_ev("sta"))
    c2 = GateController(path)
    assert c2.latest_evidence("sta") is not None
    assert c2.evaluate_gate("g", ["sta"]).passed


def test_report_surfaces_waivers_and_summaries(ctl):
    _sim(ctl,_pass_ev("sta"))
    ctl.record_llm_summary("advisory text")
    ctl.add_waiver(
        Waiver(check_id="sta", scope="g", reason="waiver for reporting only",
               approved_by="rv")
    )
    rep = ctl.report()
    assert rep["waivers"] and rep["advisory_summaries"]
    assert rep["run_status"] == "ACTIVE"


def test_gate_history_records_decisions(ctl):
    _sim(ctl,_pass_ev("sta"))
    ctl.evaluate_gate("g", ["sta"], stage="sta_stage")
    rep = ctl.report()
    assert rep["gate_history"][-1]["gate_id"] == "g"
    assert rep["gate_history"][-1]["decision"] == "PASS"
    # persisted as valid JSON
    with open(ctl.state_path, "r", encoding="utf-8") as f:
        json.load(f)


# ------------------------------------------------------- final audit U-A/U-B
def test_direct_record_requires_provenance_by_default(ctl):
    """U-B: no provenance-free PASS through the direct API by accident."""
    with pytest.raises(GateValidationError):
        ctl.record_evidence(_pass_ev("sta"))
    assert ctl.latest_evidence("sta") is None
    assert not ctl.evaluate_gate("g", ["sta"]).passed


def test_unlabelled_strict_false_is_refused(ctl):
    """U-B: the opt-out must carry an explicit simulated label."""
    with pytest.raises(GateValidationError, match="simulated"):
        ctl.record_evidence(_pass_ev("sta"), strict=False)


def test_nonwaivable_check_cannot_be_not_applicable(ctl):
    """U-A: functional proof checks require genuine PASS, never N/A."""
    ev = Evidence.from_run(
        check_id="formal_lec",
        status=Status.NOT_APPLICABLE,
        reason="",
        applicability_reason="NA-RULE:NO-MACROS design contains no hard macros",
    )
    with pytest.raises(ApplicabilityError, match="non-waivable"):
        _sim(ctl, ev)
    assert ctl.latest_evidence("formal_lec") is None


def test_validated_na_disposition_distinct_from_clean_pass(ctl):
    """U-A: a validated N/A gate is never reported as CLEAN_PASS."""
    ev = Evidence.from_run(
        check_id="upf_check",
        status=Status.NOT_APPLICABLE,
        reason="",
        applicability_reason="NA-RULE:NO-UPF design has no UPF power intent file in scope",
    )
    _sim(ctl, ev)
    gd = ctl.evaluate_gate("lp_gate", ["upf_check"])
    assert gd.passed
    assert gd.disposition == "NOT_APPLICABLE"
    assert gd.to_dict()["disposition"] == "NOT_APPLICABLE"
