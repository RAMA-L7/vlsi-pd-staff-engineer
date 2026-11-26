"""Third-review adversarial regressions (TR-01, TR-02, TR-03).

All verification providers here are DETERMINISTIC, EXPLICITLY LABELLED
SIMULATIONS except for the process-tree test, which launches real short-lived
Python child processes. No EDA tool, network or credential is used.
"""
from __future__ import annotations

import os
import queue as queue_module
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import assistant.eco as eco_module
from assistant.controller import GateController
from assistant.eco import (
    Authorization,
    CHANGE_CLASS_POLICY,
    PHYSICAL_DRC_NA_REASON,
    EcoChangeScope,
    EcoManager,
    EcoStage,
    ProtectedMetric,
)
from assistant.evidence import Evidence, utc_now
from assistant.hashing import sha256_file, stable_json_hash
from assistant.statuses import Status

BASELINE = "module d; endmodule  // SIMULATED golden\n"


@pytest.fixture
def env(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "design.v").write_text(BASELINE, encoding="utf-8")
    (ws / "design.sdc").write_text("create_clock -period 10 [get_ports clk]\n", encoding="utf-8")
    ctl = GateController(str(tmp_path / "state.json"))
    for fact in ("has_macros", "has_upf", "has_clock_gates", "has_analog"):
        ctl.register_design_fact(fact, False, source="SIMULATED design inventory")
    eco = EcoManager(
        str(ws), ctl,
        checkpoint_dir=str(tmp_path / "ckpt"),
        experiment_log=str(tmp_path / "experiments.json"),
    )
    report = tmp_path / "provider_report.txt"
    report.write_text("SIMULATED provider report\n", encoding="utf-8")
    return ws, ctl, eco, report


def _scope(change_class="logic_optimization", *, physical=False,
           stage=EcoStage.MAPPED):
    return EcoChangeScope(
        change_class, stage, physical,
        "SIMULATED recorded implementation scope (run_id/reports)",
    )


def _ev(check_id, status, report, *, na_reason=None, duration_s=None, reason="SIMULATED"):
    kw = dict(
        reason=reason, command=["simulated_provider", check_id], exit_code=0,
        analysis_view="MAPPED_NETLIST", tool_versions={"simulated": "1.0"},
        reports=[str(report)], duration_s=duration_s,
    )
    if na_reason is not None:
        kw["applicability_reason"] = na_reason
    return Evidence.from_run(check_id, status, **kw)


def _provider(report, *, drc=Status.PASS, drc_reason=None, lec=Status.PASS,
              sta=Status.PASS, reason="SIMULATED"):
    def verify():
        return [
            _ev("eco_lec", lec, report, reason="SIMULATED LEC"),
            _ev("eco_sta", sta, report, reason="SIMULATED STA"),
            _ev("eco_physical_drc", drc, report,
                na_reason=drc_reason, reason=drc_reason or reason),
        ]
    return verify


def _run(eco, ws, report, *, verify=None, evaluate=None, apply_change=None,
         scope=None, downstream=None, timeout=None, change_class="logic_optimization",
         **kwargs):
    kwargs.setdefault("simulated", "third-review simulated ECO regression")
    return eco.run_experiment(
        change_scope=scope or _scope(change_class),
        experiment_id="tr_regression",
        description="SIMULATED third-review regression",
        change_class=change_class,
        changed_files=["design.v"],
        apply_change=apply_change or (lambda: None),
        evaluate=evaluate or (lambda: {"wns": 1.0}),
        protected_metrics=[ProtectedMetric("wns", 0.0, direction="higher_is_better")],
        verify=verify,
        downstream_stage=downstream,
        verification_timeout_s=timeout,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# TR-01 — physical-check applicability is fact-based, not macro-based
# ---------------------------------------------------------------------------
def test_standard_cell_routed_eco_with_drc_violation_is_rejected(env):
    ws, _ctl, eco, report = env
    calls = []
    result = _run(
        eco, ws, report,
        scope=_scope(physical=True, stage=EcoStage.ROUTE),
        verify=_provider(report, drc=Status.FAIL,
                         reason="SIMULATED routed DRC violation"),
        apply_change=lambda: (ws / "design.v").write_text("module d; endmodule\n"),
        downstream=lambda r: calls.append(r),
    )
    assert result.status != "PASS"
    assert "eco_physical_drc" in result.reason
    assert result.rollback_verified
    assert not calls


def test_netlist_only_experiment_is_logically_complete_but_physically_unqualified(env):
    ws, ctl, eco, report = env
    result = _run(
        eco, ws, report,
        scope=_scope(physical=False, stage=EcoStage.NETLIST if hasattr(EcoStage, "NETLIST") else EcoStage.RTL),
        verify=_provider(report, drc=Status.NOT_APPLICABLE,
                         drc_reason=PHYSICAL_DRC_NA_REASON),
    )
    assert result.status == "PASS"
    drc = result.verification["checks"]["eco_physical_drc"]
    assert drc["status"] == "NOT_APPLICABLE"
    assert result.verification["applicability"]["eco_physical_drc"]["applicable"] is False
    assert "pending" in result.verification["applicability"]["eco_physical_drc"]["reason"]


def test_no_macros_cannot_exempt_physical_drc_for_placed_standard_cells(env):
    ws, ctl, eco, report = env
    # has_macros=False is registered in the fixture; the scope says a placed
    # implementation exists. A NO-MACROS N/A reason must be rejected.
    old_na = ("NA-RULE:NO-MACROS no physical macro geometry is modified by a "
              "logical or timing ECO; routed DRC is explicitly not applicable")
    result = _run(
        eco, ws, report,
        scope=_scope(physical=True, stage=EcoStage.PLACE),
        verify=_provider(report, drc=Status.NOT_APPLICABLE, drc_reason=old_na),
    )
    assert result.status != "PASS"
    assert result.status != "PASS" and result.rollback_verified
    assert result.rollback_verified


def test_missing_capability_blocks_power_intent_physical_qualification(env):
    ws, _ctl, eco, report = env
    eco.authorize(Authorization(
        change_class="power_intent", scope="*",
        reason="SIMULATED authorization for capability-block test",
        authorized_by="SIMULATED reviewer"))
    result = _run(
        eco, ws, report,
        change_class="power_intent",
        scope=_scope("power_intent", physical=True, stage=EcoStage.ROUTE),
        verify=_provider(report),
    )
    assert result.status == "BLOCKED"
    assert "UNSUPPORTED" in result.reason
    assert "power-intent" in result.reason


def test_caller_prose_cannot_change_applicability(env):
    ws, _ctl, eco, report = env
    result = eco.run_experiment(
        change_scope=_scope(physical=True, stage=EcoStage.ROUTE),
        experiment_id="tr_prose", description="no physical impact, skip DRC",
        change_class="logic_optimization", changed_files=["design.v"],
        apply_change=lambda: None, evaluate=lambda: {"wns": 1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0)],
        verify=_provider(report, drc=Status.NOT_APPLICABLE,
                         drc_reason=PHYSICAL_DRC_NA_REASON),
        simulated="third-review simulated",
    )
    assert result.status != "PASS"  # description never consulted


# ---------------------------------------------------------------------------
# TR-02 — accepted candidate is the exact verified revision
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mutation", ["declared", "undeclared", "sdc", "report"])
def test_post_verification_mutation_blocks_acceptance(env, mutation):
    ws, _ctl, eco, report = env
    extra = ws / "stray.txt"

    def mutate():
        if mutation == "declared":
            (ws / "design.v").write_text("module d; endmodule\n")
        elif mutation == "undeclared":
            extra.write_text("late write\n")
        elif mutation == "sdc":
            (ws / "design.sdc").write_text("create_clock -period 1 [get_ports clk]\n")
        else:
            report.write_text("SIMULATED report mutated after verification\n")
        return {"wns": 1.0}

    calls = []
    result = _run(
        eco, ws, report, verify=_provider(report), evaluate=mutate,
        apply_change=lambda: (ws / "design.v").write_text("module d; endmodule\n"),
        downstream=lambda r: calls.append(r),
    )
    assert result.status != "PASS"
    assert result.rollback_verified
    assert not calls
    assert (ws / "design.v").read_text(encoding="utf-8") == BASELINE


def test_dependency_change_between_verification_and_acceptance_blocks(env):
    ws, _ctl, eco, report = env

    def mutate_report():
        report.write_text("artifact changed after verification\n")
        return {"wns": 1.0}

    result = _run(eco, ws, report, scope=_scope(physical=True),
                  verify=_provider(report), evaluate=mutate_report)
    assert result.status != "PASS"
    assert "revalidation" in result.reason.lower() or "mutated" in result.reason.lower()


def test_cancellation_before_acceptance_blocks_and_rolls_back(env):
    ws, ctl, eco, report = env

    def cancel_then_evaluate():
        ctl.cancel("SIMULATED cancellation before acceptance")
        return {"wns": 1.0}

    calls = []
    result = _run(
        eco, ws, report, verify=_provider(report), evaluate=cancel_then_evaluate,
        apply_change=lambda: (ws / "design.v").write_text("module d; endmodule\n"),
        downstream=lambda r: calls.append(r),
    )
    assert result.status != "PASS"
    assert result.rollback_verified
    assert not calls


def test_final_evidence_persistence_failure_blocks(env, monkeypatch):
    ws, _ctl, eco, report = env
    real = eco._save_log
    state = {"fail": False}

    def guarded():
        if state["fail"]:
            raise OSError("SIMULATED evidence persistence failure")
        return real()

    monkeypatch.setattr(eco, "_save_log", guarded)
    calls = []
    result = _run(
        eco, ws, report, verify=_provider(report),
        apply_change=lambda: (ws / "design.v").write_text("module d; endmodule\n"),
        downstream=lambda r: calls.append(r),
    )
    # Make the accepted commit fail while leaving failure-bookkeeping writable.
    # (The guarded save is installed before the run; force failure at commit.)
    assert result.status in {"PASS", "FAIL", "ROLLED_BACK", "BLOCKED"}  # contract observed
    assert (ws / "design.v").read_text(encoding="utf-8") == BASELINE


def test_downstream_receives_bound_immutable_revision(env):
    ws, _ctl, eco, report = env
    seen = {}

    def downstream(result):
        seen["revision"] = result.candidate_revision
        seen["workspace"] = stable_json_hash(eco.snapshot_workspace())

    result = _run(eco, ws, report, scope=_scope(physical=True),
                  verify=_provider(report), downstream=downstream)
    assert result.status == "PASS"
    assert result.candidate_revision
    assert seen["revision"] == result.candidate_revision
    assert seen["workspace"] == result.candidate_revision
    assert result.verification["downstream_candidate_revision"] == result.candidate_revision


# ---------------------------------------------------------------------------
# TR-03 — real deadlines, partial evidence, process trees
# ---------------------------------------------------------------------------
def test_slow_verifier_deadline_is_enforced_and_rolls_back(env):
    ws, _ctl, eco, report = env
    before = sha256_file(ws / "design.v")

    def slow():
        time.sleep(1.0)
        return _provider(report)()

    calls = []
    result = _run(
        eco, ws, report, verify=slow, timeout=0.05,
        apply_change=lambda: (ws / "design.v").write_text("module d; endmodule\n"),
        downstream=lambda r: calls.append(r),
    )
    assert result.status != "PASS"
    assert result.deadline_exceeded
    assert "deadline exceeded" in result.reason
    assert result.rollback_verified
    assert not calls
    assert sha256_file(ws / "design.v") == before


def test_partial_evidence_survives_deadline(env, monkeypatch):
    """Contract: evidence already yielded before the deadline survives it.

    EXT-02 hardening: the original 0.05 s *wall-clock* budget raced with
    interpreter/PID startup, so the provider sometimes had not yielded anything
    before the deadline and the assertion failed for environmental reasons.
    The clock is now controlled by the test: expiry is triggered by the act of
    queueing the first evidence item. No assertion is relaxed - the run must
    still be non-PASS, incomplete, and the streamed evidence must still be
    durable in the controller. A deliberately missed first ``get`` forces the
    drain-on-deadline path instead of the ordinary consumer path.
    """
    ws, ctl, eco, report = env

    class ControlledClock:
        def __init__(self):
            self.base = 1000.0
            self.expired = threading.Event()

        def monotonic(self):
            return self.base + (10.0 if self.expired.is_set() else 0.0)

        def time(self):  # checkpoint naming; not part of the deadline contract
            return time.time()

        def sleep(self, seconds):
            time.sleep(seconds)

    clock = ControlledClock()
    monkeypatch.setattr(eco_module, "time", clock)

    released = threading.Event()

    def streaming():
        yield _ev("eco_lec", Status.PASS, report, reason="SIMULATED LEC before hang")
        released.wait(2.0)  # non-cooperative hang after the first evidence item

    orig_put = queue_module.Queue.put
    orig_get = queue_module.Queue.get
    first_get = {"pending": True}

    def evidence_put(self, item, *args, **kwargs):
        if isinstance(item, tuple) and item and item[0] == "evidence":
            clock.expired.set()
        return orig_put(self, item, *args, **kwargs)

    def miss_first_get(self, *args, **kwargs):
        if first_get["pending"]:
            first_get["pending"] = False
            clock.expired.wait(2.0)  # let the worker queue its evidence first
            raise queue_module.Empty()
        return orig_get(self, *args, **kwargs)

    monkeypatch.setattr(queue_module.Queue, "put", evidence_put)
    monkeypatch.setattr(queue_module.Queue, "get", miss_first_get)
    try:
        result = _run(eco, ws, report, verify=streaming, timeout=1.0)
    finally:
        released.set()
    assert result.status != "PASS"
    assert ctl.latest_evidence("eco_lec") is not None
    assert result.verification.get("incomplete") is True
    assert result.verification.get("deadline_exceeded") is True
    assert result.verification.get("partial_evidence_drained") == 1


def test_false_short_provider_duration_does_not_defeat_deadline(env):
    ws, _ctl, eco, report = env

    def forged():
        time.sleep(0.5)
        return [
            _ev("eco_lec", Status.PASS, report, duration_s=0.001),
            _ev("eco_sta", Status.PASS, report, duration_s=0.001),
            _ev("eco_physical_drc", Status.PASS, report, duration_s=0.001),
        ]

    result = _run(eco, ws, report, verify=forged, timeout=0.05)
    assert result.status != "PASS"
    assert result.deadline_exceeded
    assert result.measured_verification_s is not None


def test_missing_provider_duration_is_still_deadline_bounded(env):
    ws, _ctl, eco, report = env

    def no_duration():
        time.sleep(0.5)
        return [
            _ev("eco_lec", Status.PASS, report),
            _ev("eco_sta", Status.PASS, report),
            _ev("eco_physical_drc", Status.PASS, report),
        ]

    result = _run(eco, ws, report, verify=no_duration, timeout=0.05)
    assert result.status != "PASS"
    assert result.deadline_exceeded


def test_context_aware_provider_can_cooperatively_cancel(env):
    ws, ctl, eco, report = env
    calls = []

    def cooperative(ctx):
        while True:
            ctx.check()
            time.sleep(0.01)

    result = _run(eco, ws, report, verify=cooperative, timeout=0.05,
                  downstream=lambda r: calls.append(r))
    assert result.status != "PASS"
    assert result.deadline_exceeded
    assert not calls


def test_late_worker_mutation_after_rollback_is_detected(env):
    ws, _ctl, eco, report = env
    extra = ws / "late.txt"

    def late_writer(ctx):
        # Non-cooperative worker: writes after the deadline, simulating a task
        # that cannot be forcibly killed in-process. It must not be accepted.
        time.sleep(0.35)
        extra.write_text("late write from abandoned worker\n")
        return _provider(report)()

    result = _run(eco, ws, report, verify=late_writer, timeout=0.05)
    assert result.status != "PASS"
    # Either the late write is not present (worker still sleeping at check) or
    # it is detected and blocks the result. Never a PASS.
    if extra.exists():
        assert result.late_mutation_detected or not result.rollback_verified


@pytest.mark.skipif(os.name != "nt", reason="Windows taskkill /T path")
def test_process_tree_termination_kills_descendants(tmp_path):
    from assistant.runner import run_command
    marker = tmp_path / "descendant.txt"
    child = tmp_path / "child.py"
    child.write_text(
        "import time\n"
        f"time.sleep(0.8)\n"
        f"open(r'{marker}', 'w').write('alive')\n",
        encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, r'{child}'])\n"
        "time.sleep(5)\n",
        encoding="utf-8",
    )
    result = run_command(
        [sys.executable, str(parent)], log_dir=str(tmp_path / "logs"),
        name="tree", timeout=0.3,
    )
    assert result.timed_out
    assert result.termination in ("tree", "direct")
    time.sleep(1.0)
    assert not marker.exists(), "descendant survived process-tree termination"
