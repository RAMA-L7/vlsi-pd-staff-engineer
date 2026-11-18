"""Controlled ECO experiment tests: authorization, rollback, budget, metrics.

Verification helpers below are deterministic simulated providers - no real EDA
tool or live inference is invoked in these unit tests.
"""
from pathlib import Path

import pytest

from assistant.controller import GateController, RESTRICTED_CHANGE_CLASSES
from assistant.eco import (
    Authorization,
    EcoAuthorizationError,
    EcoBudgetExceeded,
    EcoManager,
    ProtectedMetric,
)
from assistant.evidence import Evidence
from assistant.statuses import Status

# Explicit inapplicable policy reason registered in assistant.eco.
DRC_NA_REASON = (
    "NA-RULE:NO-MACROS no physical macro geometry is modified by a logical "
    "or timing ECO; routed DRC is explicitly not applicable"
)


def passing_verify(directory: Path):
    """Deterministic simulated provider: all registered checks acceptable."""
    def verify():
        report = directory / "simulated_eco_checks.txt"
        report.write_text("SIMULATED check report: LEC equivalent, STA clean\n",
                          encoding="utf-8")
        return [
            Evidence.from_run(
                check_id="eco_lec", status=Status.PASS,
                reason="SIMULATED LEC: designs equivalent",
                command=["simulated_lec", "--equiv"], exit_code=0,
                analysis_view="MAPPED_NETLIST",
                tool_versions={"simulated_lec": "1.0"},
                reports=[str(report)], duration_s=0.01,
            ),
            Evidence.from_run(
                check_id="eco_sta", status=Status.PASS,
                reason="SIMULATED STA: timing clean",
                command=["simulated_sta", "--report"], exit_code=0,
                analysis_view="MAPPED_NETLIST",
                tool_versions={"simulated_sta": "1.0"},
                reports=[str(report)], duration_s=0.01,
            ),
            Evidence.from_run(
                check_id="eco_physical_drc", status=Status.NOT_APPLICABLE,
                reason="explicitly inapplicable",
                applicability_reason=DRC_NA_REASON,
            ),
        ]
    return verify


@pytest.fixture
def env(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "design.v").write_text("module d; endmodule\n", encoding="utf-8")
    ctl = GateController(str(tmp_path / "state.json"))
    # SIMULATED design facts corroborating the registered N/A policy reason.
    # The controller requires fact-backed applicability even for labelled
    # simulated evidence; this fixture states the simulated design has no
    # macros, UPF, clock gates or analog content.
    ctl.register_design_fact("has_macros", False, source="unit-test simulated design")
    ctl.register_design_fact("has_upf", False, source="unit-test simulated design")
    ctl.register_design_fact("has_clock_gates", False, source="unit-test simulated design")
    ctl.register_design_fact("has_analog", False, source="unit-test simulated design")
    eco = EcoManager(str(ws), ctl,
                     checkpoint_dir=str(tmp_path / "ckpt"),
                     experiment_log=str(tmp_path / "experiments.json"))
    return ws, ctl, eco


def test_restricted_change_without_authorization_is_blocked(env):
    _ws, _ctl, eco = env
    res = eco.run_experiment(
        experiment_id="eco1",
        description="pipeline the counter",
        change_class="interface_latency",
        changed_files=["design.v"],
        apply_change=lambda: None,
        evaluate=lambda: {"wns": 1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0, direction="higher_is_better")],
    )
    assert res.status == "BLOCKED"
    assert "authorization" in res.reason


def test_nonrestricted_change_runs_without_authorization(env, tmp_path):
    ws, _ctl, eco = env
    res = eco.run_experiment(
        experiment_id="eco_upsize",
        description="upsized one gate",
        change_class="logic_optimization",
        changed_files=["design.v"],
        apply_change=lambda: ws.joinpath("design.v").write_text(
            "module d; wire x; endmodule\n", encoding="utf-8"),
        evaluate=lambda: {"wns": 0.2},
        protected_metrics=[ProtectedMetric("wns", 0.0, direction="higher_is_better")],
        verify=passing_verify(tmp_path),
        simulated="unit-test simulated ECO provider",
    )
    assert res.status == "PASS"
    assert not res.rollback_performed


def test_authorized_latency_change_runs(env, tmp_path):
    ws, _ctl, eco = env
    eco.authorize(
        Authorization(
            change_class="interface_latency",
            scope="*",
            reason="arch approved: latency change for timing closure experiment",
            authorized_by="architect@example",
        )
    )
    res = eco.run_experiment(
        experiment_id="eco_pipe",
        description="add pipeline stage",
        change_class="interface_latency",
        changed_files=["design.v"],
        apply_change=lambda: ws.joinpath("design.v").write_text(
            "module d; reg r; endmodule\n", encoding="utf-8"),
        evaluate=lambda: {"wns": 0.4},
        protected_metrics=[ProtectedMetric("wns", 0.0, direction="higher_is_better")],
        verify=passing_verify(tmp_path),
        simulated="unit-test simulated ECO provider",
    )
    assert res.status == "PASS"


def test_authorization_requires_reason_and_author():
    with pytest.raises(ValueError):
        Authorization(change_class="rtl_behavior", scope="*",
                       reason="short", authorized_by="a")
    with pytest.raises(ValueError):
        Authorization(change_class="rtl_behavior", scope="*",
                       reason="long enough reason for change", authorized_by="")
    with pytest.raises(ValueError):
        Authorization(change_class="rtl_behavior", scope="",
                       reason="long enough reason for change", authorized_by="arch")


def test_rollback_restores_file_when_protected_metric_regresses(env, tmp_path):
    ws, _ctl, eco = env
    original = ws.joinpath("design.v").read_text(encoding="utf-8")
    res = eco.run_experiment(
        experiment_id="eco_bad",
        description="change that worsens WNS",
        change_class="logic_optimization",
        changed_files=["design.v"],
        apply_change=lambda: ws.joinpath("design.v").write_text(
            "module d; wire worse; endmodule\n", encoding="utf-8"),
        evaluate=lambda: {"wns": -0.5},
        protected_metrics=[
            ProtectedMetric("wns", 0.0, tolerance=0.0, direction="higher_is_better")
        ],
        verify=passing_verify(tmp_path),
        simulated="unit-test simulated ECO provider",
    )
    assert res.status == "ROLLED_BACK"
    assert res.rollback_performed
    assert res.rollback_verified
    assert ws.joinpath("design.v").read_text(encoding="utf-8") == original
    assert "wns" in res.reason


def test_missing_metric_after_change_rolls_back(env, tmp_path):
    ws, _ctl, eco = env
    res = eco.run_experiment(
        experiment_id="eco_missing",
        description="metric vanished",
        change_class="logic_optimization",
        changed_files=["design.v"],
        apply_change=lambda: None,
        evaluate=lambda: {},
        protected_metrics=[ProtectedMetric("wns", 0.0, direction="higher_is_better")],
        verify=passing_verify(tmp_path),
        simulated="unit-test simulated ECO provider",
    )
    assert res.status == "ROLLED_BACK"
    assert "missing" in res.reason


def test_failed_change_application_rolls_back(env):
    ws, _ctl, eco = env
    original = ws.joinpath("design.v").read_text(encoding="utf-8")

    def boom():
        ws.joinpath("design.v").write_text("partial", encoding="utf-8")
        raise RuntimeError("write error midway")

    res = eco.run_experiment(
        experiment_id="eco_boom",
        description="apply fails",
        change_class="logic_optimization",
        changed_files=["design.v"],
        apply_change=boom,
        evaluate=lambda: {"wns": 1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0, direction="higher_is_better")],
    )
    assert res.status == "FAIL"
    assert res.rollback_performed
    assert ws.joinpath("design.v").read_text(encoding="utf-8") == original


def test_three_failures_trigger_reassessment(env, tmp_path):
    _ws, _ctl, eco = env
    for i in range(3):
        res = eco.run_experiment(
            experiment_id=f"eco_{i}",
            description="failing attempt",
            change_class="logic_optimization",
            changed_files=["design.v"],
            apply_change=lambda: None,
            evaluate=lambda: {"wns": -1.0},
            protected_metrics=[ProtectedMetric("wns", 0.0, direction="higher_is_better")],
            verify=passing_verify(tmp_path),
            simulated="unit-test simulated ECO provider",
        )
        assert res.status == "ROLLED_BACK"
    assert eco.consecutive_failures == 3
    assert eco.reassessment_required()
    # 4th attempt is refused - reassessment, not "it's upstream"
    with pytest.raises(EcoBudgetExceeded) as exc:
        eco.run_experiment(
            experiment_id="eco_3",
            description="should be refused",
            change_class="logic_optimization",
            changed_files=["design.v"],
            apply_change=lambda: None,
            evaluate=lambda: {"wns": 1.0},
            protected_metrics=[ProtectedMetric("wns", 0.0, direction="higher_is_better")],
            verify=passing_verify(tmp_path),
            simulated="unit-test simulated ECO provider",
        )
    assert "reassessment" in str(exc.value).lower()
    assert "upstream" in str(exc.value).lower()


def test_success_resets_failure_budget(env, tmp_path):
    _ws, _ctl, eco = env
    for _ in range(2):
        eco.run_experiment(
            experiment_id="f", description="fail", change_class="logic_optimization",
            changed_files=["design.v"], apply_change=lambda: None,
            evaluate=lambda: {"wns": -1.0},
            protected_metrics=[ProtectedMetric("wns", 0.0, direction="higher_is_better")],
            verify=passing_verify(tmp_path),
            simulated="unit-test simulated ECO provider",
        )
    assert eco.consecutive_failures == 2
    eco.run_experiment(
        experiment_id="ok", description="pass", change_class="logic_optimization",
        changed_files=["design.v"], apply_change=lambda: None,
        evaluate=lambda: {"wns": 1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0, direction="higher_is_better")],
        verify=passing_verify(tmp_path),
        simulated="unit-test simulated ECO provider",
    )
    assert eco.consecutive_failures == 0


def test_experiment_records_fresh_evidence(env, tmp_path):
    ws, ctl, eco = env
    verify_evidence = passing_verify(tmp_path)
    res = eco.run_experiment(
        experiment_id="eco_ev",
        description="with verification",
        change_class="logic_optimization",
        changed_files=["design.v"],
        apply_change=lambda: None,
        evaluate=lambda: {"wns": 0.5},
        protected_metrics=[ProtectedMetric("wns", 0.0, direction="higher_is_better")],
        verify=verify_evidence,
        simulated="unit-test simulated ECO provider",
    )
    assert res.status == "PASS"
    assert len(res.evidence_ids) == 3
    assert ctl.latest_evidence("eco_lec") is not None
    assert ctl.latest_evidence("eco_sta") is not None
    assert ctl.latest_evidence("eco_physical_drc").status is Status.NOT_APPLICABLE


def test_constraint_relaxation_class_is_restricted():
    # relaxing timing exceptions/constraints must be authorization-gated
    assert "timing_exception" in RESTRICTED_CHANGE_CLASSES
    assert "verification_exclusion" in RESTRICTED_CHANGE_CLASSES
    assert "clock_definition" in RESTRICTED_CHANGE_CLASSES
    assert "power_intent" in RESTRICTED_CHANGE_CLASSES
    assert "rtl_behavior" in RESTRICTED_CHANGE_CLASSES
    assert "interface_latency" in RESTRICTED_CHANGE_CLASSES
