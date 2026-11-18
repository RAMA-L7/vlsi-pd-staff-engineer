"""SR-01 regression suite: ECO verification controls acceptance and rollback.

All providers in this file are DETERMINISTIC SIMULATED providers.  No real EDA
tool, network service or live inference is used; evidence is constructed in
memory with a local simulated report file so provenance binding is real.
"""
from pathlib import Path

import pytest

from assistant.controller import GateController
from assistant.eco import CHANGE_CLASS_POLICY, EcoManager, ProtectedMetric
from assistant.evidence import Evidence
from assistant.hashing import sha256_file
from assistant.statuses import Status

BASELINE = "module d; endmodule  // SIMULATED golden RTL\n"
CORRUPT = "module d; assign broken = 1'bx; endmodule  // SIMULATED corruption\n"
DRC_NA = CHANGE_CLASS_POLICY["logic_optimization"][2].na_reason


@pytest.fixture
def eco_env(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "design.v").write_text(BASELINE, encoding="utf-8")
    ctl = GateController(str(tmp_path / "state.json"))
    # SIMULATED design facts backing the registered N/A policy. The gate
    # controller requires corroborating facts for every NOT_APPLICABLE record,
    # even in labelled simulated mode.
    for fact in ("has_macros", "has_upf", "has_clock_gates", "has_analog"):
        ctl.register_design_fact(fact, False, source="unit-test simulated design")
    eco = EcoManager(
        str(ws), ctl,
        checkpoint_dir=str(tmp_path / "ckpt"),
        experiment_log=str(tmp_path / "experiments.json"),
    )
    return ws, ctl, eco


def _report(tmp_path: Path) -> str:
    report = tmp_path / "simulated_eco_check.txt"
    report.write_text("SIMULATED provider report: deterministic test artifact\n",
                      encoding="utf-8")
    return str(report)


def _evidence(tmp_path: Path, check_id: str, status: Status,
              *, reason: str = "SIMULATED verdict", exit_code: int = 0,
              duration_s: float = 0.01, na_reason: str | None = None) -> Evidence:
    """Build one fully bound evidence record from a simulated provider."""
    kwargs = dict(
        reason=reason,
        command=["simulated_provider", "--check", check_id],
        exit_code=exit_code,
        analysis_view="MAPPED_NETLIST",
        tool_versions={"simulated_provider": "1.0"},
        reports=[_report(tmp_path)],
        duration_s=duration_s,
    )
    if na_reason is not None:
        kwargs["applicability_reason"] = na_reason
    return Evidence.from_run(check_id, status, **kwargs)


def simulated_provider(tmp_path: Path, *, lec_status: Status = Status.PASS,
                       sta_status: Status = Status.PASS,
                       drc_status: Status = Status.NOT_APPLICABLE,
                       drc_reason: str | None = None,
                       lec_reason: str = "SIMULATED LEC: equivalent",
                       lec_duration: float = 0.01):
    """Deterministic simulated check provider for the registered ECO policy."""
    def verify():
        return [
            _evidence(tmp_path, "eco_lec", lec_status, reason=lec_reason,
                      exit_code=0 if lec_status is Status.PASS else 1,
                      duration_s=lec_duration),
            _evidence(tmp_path, "eco_sta", sta_status,
                      reason="SIMULATED STA: WNS improved"),
            _evidence(tmp_path, "eco_physical_drc", drc_status,
                      na_reason=drc_reason or DRC_NA),
        ]
    return verify


def _run(eco, ws, tmp_path, *, verify=None, evaluate=None, apply_change=None,
         change_class="logic_optimization", **kwargs):
    # Every provider in this module is a labelled deterministic simulation:
    # opt out of strict tool provenance explicitly, never silently.
    kwargs.setdefault("simulated", "unit-test simulated ECO verification provider")
    return eco.run_experiment(
        experiment_id="eco_sr01",
        description="SIMULATED ECO experiment",
        change_class=change_class,
        changed_files=["design.v"],
        apply_change=apply_change or (lambda: None),
        evaluate=evaluate or (lambda: {"wns": 0.5}),
        protected_metrics=[ProtectedMetric("wns", 0.0, direction="higher_is_better")],
        verify=verify,
        **kwargs,
    )


# -- required regression -----------------------------------------------------
def test_simulated_wns_gain_with_lec_fail_never_passes(eco_env, tmp_path):
    """REQUIRED REGRESSION (simulated): WNS improves, LEC reports FAIL.

    The experiment must not PASS, the golden file must be restored byte for
    byte, and no downstream stage may be invoked.
    """
    ws, ctl, eco = eco_env
    before = sha256_file(ws / "design.v")
    downstream_calls = []

    def corrupt():
        (ws / "design.v").write_text(CORRUPT, encoding="utf-8")

    result = _run(
        eco, ws, tmp_path,
        verify=simulated_provider(
            tmp_path, lec_status=Status.FAIL,
            lec_reason="SIMULATED LEC FAIL: functional corruption introduced",
        ),
        evaluate=lambda: {"wns": 0.95},  # improved WNS
        apply_change=corrupt,
        downstream_stage=lambda accepted: downstream_calls.append(accepted),
    )

    assert result.status != "PASS"
    assert result.status == "FAIL"
    assert "eco_lec" in result.reason and "FAIL" in result.reason
    assert result.rollback_performed and result.rollback_verified
    assert sha256_file(ws / "design.v") == before
    assert (ws / "design.v").read_text(encoding="utf-8") == BASELINE
    assert downstream_calls == []
    # The controller gate built from the recorded evidence is BLOCKED.
    gate = ctl.evaluate_gate("post_eco", ["eco_lec", "eco_sta"])
    assert gate.decision == "BLOCKED"
    assert "eco_lec: FAIL" in gate.reason
    # Failed evidence is still preserved (with IDs) in the persistent history.
    assert any(e["status"] == "FAIL" and e["evidence_ids"]
               and "eco_lec" in e["reason"] for e in eco.history())


# -- verify(): status, absence, exceptions, timeout, cancellation -------------
@pytest.mark.parametrize("status", [
    Status.FAIL, Status.INCONCLUSIVE, Status.UNSUPPORTED,
    Status.NOT_RUN, Status.NOT_APPLICABLE,
])
def test_non_pass_required_status_rejects_and_rolls_back(eco_env, tmp_path, status):
    ws, _ctl, eco = eco_env
    before = sha256_file(ws / "design.v")

    result = _run(
        eco, ws, tmp_path,
        verify=simulated_provider(tmp_path, lec_status=status),
        apply_change=lambda: (ws / "design.v").write_text(CORRUPT, encoding="utf-8"),
        evaluate=lambda: {"wns": 1.0},
    )
    assert result.status != "PASS"
    assert result.rollback_verified
    assert sha256_file(ws / "design.v") == before


def test_missing_required_check_evidence_rejects_and_rolls_back(eco_env, tmp_path):
    ws, _ctl, eco = eco_env
    before = sha256_file(ws / "design.v")

    def partial_verify():
        return [
            _evidence(tmp_path, "eco_lec", Status.PASS),
            _evidence(tmp_path, "eco_sta", Status.PASS),
            # eco_physical_drc omitted - not silently optional
        ]

    result = _run(
        eco, ws, tmp_path, verify=partial_verify,
        apply_change=lambda: (ws / "design.v").write_text(CORRUPT, encoding="utf-8"),
    )
    assert result.status != "PASS"
    assert "eco_physical_drc" in result.reason
    assert result.rollback_verified
    assert sha256_file(ws / "design.v") == before


def test_empty_verify_rejects(eco_env, tmp_path):
    ws, _ctl, eco = eco_env
    result = _run(eco, ws, tmp_path, verify=lambda: [])
    assert result.status != "PASS"
    assert result.rollback_verified


def test_verification_is_not_optional(eco_env, tmp_path):
    _ws, _ctl, eco = eco_env
    result = _run(eco, _ws, tmp_path, verify=None)
    assert result.status != "PASS"
    assert "verification is required" in result.reason


def test_verify_exception_rejects_and_rolls_back(eco_env, tmp_path):
    ws, _ctl, eco = eco_env
    before = sha256_file(ws / "design.v")

    def crashing_verify():
        (ws / "design.v").write_text(CORRUPT, encoding="utf-8")
        raise RuntimeError("SIMULATED verifier crash")

    result = _run(eco, ws, tmp_path, verify=crashing_verify)
    assert result.status != "PASS"
    assert "verify() raised RuntimeError" in result.reason
    assert result.rollback_verified
    assert sha256_file(ws / "design.v") == before


def test_verify_timeout_exception_rejects_and_rolls_back(eco_env, tmp_path):
    ws, _ctl, eco = eco_env
    before = sha256_file(ws / "design.v")

    def timed_out_verify():
        (ws / "design.v").write_text(CORRUPT, encoding="utf-8")
        raise TimeoutError("SIMULATED verifier timeout")

    result = _run(eco, ws, tmp_path, verify=timed_out_verify)
    assert result.status != "PASS"
    assert "TimeoutError" in result.reason
    assert result.rollback_verified
    assert sha256_file(ws / "design.v") == before


def test_verify_duration_timeout_rejects_and_rolls_back(eco_env, tmp_path):
    ws, _ctl, eco = eco_env
    before = sha256_file(ws / "design.v")
    result = _run(
        eco, ws, tmp_path,
        verify=simulated_provider(tmp_path, lec_duration=9.0),
        verification_timeout_s=1.0,
        apply_change=lambda: (ws / "design.v").write_text(CORRUPT, encoding="utf-8"),
    )
    assert result.status != "PASS"
    assert "timed out" in result.reason
    assert result.rollback_verified
    assert sha256_file(ws / "design.v") == before


def test_cancellation_during_verification_rejects_and_rolls_back(eco_env, tmp_path):
    ws, ctl, eco = eco_env
    before = sha256_file(ws / "design.v")
    ctl.cancel("SIMULATED cancellation")

    result = _run(
        eco, ws, tmp_path, verify=simulated_provider(tmp_path),
        apply_change=lambda: (ws / "design.v").write_text(CORRUPT, encoding="utf-8"),
    )
    assert result.status != "PASS"
    assert "CANCELLED" in result.reason
    assert result.rollback_verified
    assert sha256_file(ws / "design.v") == before


def test_stale_reused_evidence_is_not_fresh(eco_env, tmp_path):
    ws, ctl, eco = eco_env
    before = sha256_file(ws / "design.v")
    old_lec = _evidence(tmp_path, "eco_lec", Status.PASS, reason="old run")
    ctl.record_evidence(
        old_lec, strict=False, simulated="unit-test stale simulated evidence"
    )

    def reused():
        return [old_lec,
                _evidence(tmp_path, "eco_sta", Status.PASS),
                _evidence(tmp_path, "eco_physical_drc", Status.NOT_APPLICABLE,
                          na_reason=DRC_NA)]

    result = _run(
        eco, ws, tmp_path, verify=reused,
        apply_change=lambda: (ws / "design.v").write_text(CORRUPT, encoding="utf-8"),
    )
    assert result.status != "PASS"
    assert "fresh" in result.reason.lower()
    assert result.rollback_verified
    assert sha256_file(ws / "design.v") == before


# -- explicit inapplicable policy --------------------------------------------
def test_inapplicable_check_must_be_declared_not_applicable(eco_env, tmp_path):
    ws, _ctl, eco = eco_env
    before = sha256_file(ws / "design.v")
    result = _run(
        eco, ws, tmp_path,
        verify=simulated_provider(tmp_path, drc_status=Status.PASS),
        apply_change=lambda: (ws / "design.v").write_text(CORRUPT, encoding="utf-8"),
    )
    assert result.status != "PASS"
    assert "inapplicable" in result.reason
    assert result.rollback_verified
    assert sha256_file(ws / "design.v") == before


def test_inapplicable_reason_must_match_registered_policy(eco_env, tmp_path):
    ws, _ctl, eco = eco_env
    before = sha256_file(ws / "design.v")
    wrong_na = ("NA-RULE:NO-ANALOG SIMULATED wrong inapplicability reason for "
                "this registered change class")
    result = _run(
        eco, ws, tmp_path,
        verify=simulated_provider(tmp_path, drc_reason=wrong_na),
        apply_change=lambda: (ws / "design.v").write_text(CORRUPT, encoding="utf-8"),
    )
    assert result.status != "PASS"
    assert "registered policy" in result.reason
    assert result.rollback_verified
    assert sha256_file(ws / "design.v") == before


def test_no_op_policy_is_explicit_and_allows_no_file_change(eco_env, tmp_path):
    _ws, _ctl, eco = eco_env
    assert eco.run_experiment(
        experiment_id="noop", description="SIMULATED no change",
        change_class="no_op", changed_files=["design.v"],
        apply_change=lambda: None, evaluate=lambda: {"wns": 0.0},
        protected_metrics=[ProtectedMetric("wns", 0.0)], verify=None,
    ).status == "PASS"

    ws, _ctl, eco2 = eco_env
    result = eco2.run_experiment(
        experiment_id="noop_bad", description="SIMULATED disguised change",
        change_class="no_op", changed_files=["design.v"],
        apply_change=lambda: (ws / "design.v").write_text(CORRUPT, encoding="utf-8"),
        evaluate=lambda: {"wns": 0.0},
        protected_metrics=[ProtectedMetric("wns", 0.0)], verify=None,
    )
    assert result.status != "PASS"
    assert "no_op policy" in result.reason
    assert (ws / "design.v").read_text(encoding="utf-8") == BASELINE


def test_unknown_change_class_is_blocked(eco_env, tmp_path):
    ws, _ctl, eco = eco_env
    result = _run(eco, ws, tmp_path, change_class="not_registered",
                  verify=simulated_provider(tmp_path))
    assert result.status == "BLOCKED"
    assert "policy" in result.reason


# -- metric validation -------------------------------------------------------
@pytest.mark.parametrize("bad", [
    float("nan"), float("inf"), float("-inf"), "abc", None, True,
])
def test_non_finite_or_malformed_metrics_reject(eco_env, tmp_path, bad):
    ws, _ctl, eco = eco_env
    before = sha256_file(ws / "design.v")
    result = _run(
        eco, ws, tmp_path, verify=simulated_provider(tmp_path),
        evaluate=lambda: {"wns": bad},
        apply_change=lambda: (ws / "design.v").write_text(CORRUPT, encoding="utf-8"),
    )
    assert result.status != "PASS"
    assert result.rollback_verified
    assert sha256_file(ws / "design.v") == before


def test_missing_protected_metric_rejects(eco_env, tmp_path):
    ws, _ctl, eco = eco_env
    result = _run(eco, ws, tmp_path, verify=simulated_provider(tmp_path),
                  evaluate=lambda: {"other": 1.0})
    assert result.status != "PASS"
    assert "missing" in result.reason


# -- paths, unlisted files, rollback proof -----------------------------------
def test_changed_file_path_escape_is_blocked(eco_env, tmp_path):
    ws, _ctl, eco = eco_env
    outside = tmp_path / "outside.v"
    result = eco.run_experiment(
        experiment_id="escape", description="SIMULATED path escape",
        change_class="logic_optimization", changed_files=["../outside.v"],
        apply_change=lambda: outside.write_text("escaped", encoding="utf-8"),
        evaluate=lambda: {"wns": 1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0)],
        verify=simulated_provider(tmp_path),
    )
    assert result.status == "BLOCKED"
    assert "path" in result.reason.lower()
    assert not outside.exists()


def test_rollback_checkpoint_path_escape_is_refused(eco_env, tmp_path):
    _ws, _ctl, eco = eco_env
    foreign = tmp_path / "foreign_ckpt"
    foreign.mkdir()
    with pytest.raises(ValueError, match="escapes"):
        eco.rollback(str(foreign))


def test_unlisted_file_modification_is_rejected_and_reverted(eco_env, tmp_path):
    ws, _ctl, eco = eco_env
    before = sha256_file(ws / "design.v")

    def apply():
        (ws / "design.v").write_text(CORRUPT, encoding="utf-8")
        (ws / "unlisted.log").write_text("SIMULATED unlisted change\n", encoding="utf-8")

    result = _run(eco, ws, tmp_path, verify=simulated_provider(tmp_path),
                  apply_change=apply)
    assert result.status != "PASS"
    assert "unlisted" in result.reason
    assert result.rollback_verified
    assert sha256_file(ws / "design.v") == before
    assert not (ws / "unlisted.log").exists()


def test_failed_rollback_verification_is_never_pass(eco_env, tmp_path, monkeypatch):
    ws, _ctl, eco = eco_env

    def broken_restore(*_args, **_kwargs):
        raise OSError("SIMULATED restore failure")

    monkeypatch.setattr(eco, "_restore_from_manifest", broken_restore)
    result = _run(
        eco, ws, tmp_path, verify=simulated_provider(tmp_path),
        evaluate=lambda: {"wns": -1.0},  # forces metric rollback
        apply_change=lambda: (ws / "design.v").write_text(CORRUPT, encoding="utf-8"),
    )
    assert result.status != "PASS"
    assert result.rollback_verified is False
    assert result.rollback_performed is False
    assert "rollback FAILED" in result.reason
    assert (ws / "design.v").read_text(encoding="utf-8") == CORRUPT


# -- persistent history ------------------------------------------------------
def test_history_persists_failures_with_evidence_ids_and_survives_restart(eco_env, tmp_path):
    ws, ctl, eco = eco_env
    failed = _run(
        eco, ws, tmp_path,
        verify=simulated_provider(tmp_path, lec_status=Status.FAIL),
        apply_change=lambda: (ws / "design.v").write_text(CORRUPT, encoding="utf-8"),
    )
    assert failed.status != "PASS"
    (ws / "design.v").write_text(BASELINE, encoding="utf-8")
    passed = _run(eco, ws, tmp_path, verify=simulated_provider(tmp_path))
    assert passed.status == "PASS"

    history = eco.history()
    assert len(history) == 2
    failed_entry = next(e for e in history if e["status"] != "PASS")
    assert failed_entry["evidence_ids"]
    assert failed_entry["reason"]
    assert failed_entry["change_class"] == "logic_optimization"

    restarted = EcoManager(
        str(ws), GateController(str(tmp_path / "state.json")),
        checkpoint_dir=str(tmp_path / "ckpt"),
        experiment_log=str(tmp_path / "experiments.json"),
    )
    assert len(restarted.history()) == 2
    assert any(e["status"] != "PASS" and e["evidence_ids"]
               for e in restarted.history())
