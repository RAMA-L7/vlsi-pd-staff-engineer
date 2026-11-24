"""Round-2 exploit regressions (F-01..F-11). All providers are labelled SIMULATED.

Each regression encodes an exploit reproduced by the independent audit. They
must FAIL against the vulnerable baseline and PASS against the fixed code.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from assistant.controller import GateController
from assistant.eco import Authorization
from assistant.eco import (
    PHYSICAL_DRC_NA_REASON,
    EcoChangeScope,
    EcoManager,
    EcoStage,
    ProtectedMetric,
)
from assistant.evidence import Evidence
from assistant.hashing import sha256_file, stable_json_hash
from assistant.statuses import Status

BASELINE = "module d; endmodule\n"


@pytest.fixture
def env(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "design.v").write_text(BASELINE, encoding="utf-8")
    (ws / "design.sdc").write_text("create_clock -period 10 [get_ports clk]\n", encoding="utf-8")
    ctl = GateController(str(tmp_path / "state.json"))
    for f in ("has_macros", "has_upf", "has_clock_gates", "has_analog"):
        ctl.register_design_fact(f, False, source="SIMULATED inventory")
    eco = EcoManager(str(ws), ctl, checkpoint_dir=str(tmp_path/"ckpt"),
                     experiment_log=str(tmp_path/"exp.json"))
    rep = tmp_path / "r.txt"; rep.write_text("SIMULATED report\n")
    return ws, ctl, eco, rep


def _ev(rep, cid, status=Status.PASS, na=None):
    kw = dict(reason="SIMULATED", command=["sim"], exit_code=0,
              analysis_view="MAPPED_NETLIST", tool_versions={"sim":"1"}, reports=[str(rep)])
    if na: kw["applicability_reason"] = na
    return Evidence.from_run(cid, status, **kw)


def _provider(rep, *, drc=Status.PASS):
    def verify():
        return [_ev(rep, "eco_lec"), _ev(rep, "eco_sta"),
                _ev(rep, "eco_physical_drc", drc,
                    None if drc is Status.PASS else PHYSICAL_DRC_NA_REASON)]
    return verify


def _scope(change_class="logic_optimization", *, physical=False, stage=EcoStage.MAPPED,
           source="SIMULATED recorded scope"):
    return EcoChangeScope(change_class, stage, physical, source)


def _run(eco, ws, rep, **kw):
    kw.setdefault("simulated", "round-2 exploit regression")
    return eco.run_experiment(
        change_scope=kw.pop("scope", _scope()),
        experiment_id="r2", description="SIMULATED round-2 regression",
        change_class=kw.pop("change_class", "logic_optimization"),
        changed_files=["design.v"],
        apply_change=kw.pop("apply_change", lambda: None),
        evaluate=kw.pop("evaluate", lambda: {"wns": 1.0}),
        protected_metrics=[ProtectedMetric("wns", 0.0)],
        verify=kw.pop("verify", None),
        downstream_stage=kw.pop("downstream", None),
        verification_timeout_s=kw.pop("timeout", None), **kw,
    )


def test_F02_verifier_mutates_rtl_before_return_is_rejected(env):
    ws, _ctl, eco, rep = env
    def verify():
        out = _provider(rep, drc=Status.NOT_APPLICABLE)()
        (ws / "design.v").write_text("module d; assign x=1; endmodule\n")
        return out
    calls = []
    r = _run(eco, ws, rep, scope=_scope(physical=False, stage=EcoStage.RTL),
             verify=verify, downstream=lambda _: calls.append(1))
    assert r.status != "PASS"
    assert r.rollback_verified
    assert not calls
    assert (ws / "design.v").read_text(encoding="utf-8") == BASELINE


def test_F03_deadline_expires_during_evaluate_is_rejected(env):
    ws, _ctl, eco, rep = env
    def slow_eval():
        time.sleep(0.35)
        return {"wns": 1.0}
    r = _run(eco, ws, rep, verify=_provider(rep), evaluate=slow_eval, timeout=0.08)
    assert r.status != "PASS"
    assert r.deadline_exceeded


def test_F04_late_worker_write_does_not_yield_clean_rollback(env):
    ws, _ctl, eco, rep = env
    extra = ws / "late.txt"
    def late_worker(ctx):
        time.sleep(0.2)
        extra.write_text("late\n")
        return _provider(rep)()
    r = _run(eco, ws, rep, verify=late_worker, timeout=0.05)
    assert r.status != "PASS"
    # Either the write is detected (late_mutation_detected) or the worker is
    # explicitly reported as uncontained; never a clean rollback + PASS.
    if extra.exists() or r.worker_uncontained:
        assert r.late_mutation_detected or r.worker_uncontained or not r.rollback_verified


def test_F05_physical_artifacts_contradict_netlist_scope(env):
    ws, _ctl, eco, rep = env
    (ws / "routed.def").write_text("SIMULATED routed database\n")
    r = _run(eco, ws, rep, scope=_scope(physical=False, stage=EcoStage.RTL),
             verify=_provider(rep, drc=Status.NOT_APPLICABLE))
    assert r.status == "BLOCKED"
    assert "physical" in r.reason.lower()


def test_F06_scope_change_class_mismatch_is_blocked(env):
    ws, _ctl, eco, rep = env
    eco.authorize(Authorization(change_class="power_intent", scope="*",
        reason="SIMULATED authorization for scope-mismatch regression",
        authorized_by="SIMULATED lead"))
    r = _run(eco, ws, rep, change_class="power_intent",
             scope=_scope("logic_optimization", physical=True), verify=_provider(rep))
    assert r.status == "BLOCKED"
    assert "F-06" in r.reason


def test_F08_cancellation_before_acceptance_is_rejected(env):
    ws, ctl, eco, rep = env
    def cancel():
        ctl.cancel("SIMULATED cancel at acceptance")
        return {"wns": 1.0}
    calls = []
    r = _run(eco, ws, rep, verify=_provider(rep), evaluate=cancel,
             downstream=lambda _: calls.append(1))
    assert r.status != "PASS"
    assert r.rollback_verified
    assert not calls


def test_F09_downstream_failure_does_not_create_contradictory_records(env):
    ws, _ctl, eco, rep = env
    def boom(_result):
        raise RuntimeError("SIMULATED downstream failure")
    r = _run(eco, ws, rep, scope=_scope(physical=True), verify=_provider(rep), downstream=boom)
    assert r.status == "PASS"
    assert r.downstream_failed
    hist = eco.history()
    assert len(hist) == 1
    assert hist[-1]["downstream_failed"] is True


def test_F11_downstream_sees_only_accepted_revision(env):
    ws, _ctl, eco, rep = env
    seen = {}
    def downstream(result):
        seen["revision"] = result.candidate_revision
        seen["workspace"] = stable_json_hash(eco.snapshot_workspace())
    r = _run(eco, ws, rep, scope=_scope(physical=True), verify=_provider(rep), downstream=downstream)
    assert r.status == "PASS"
    assert seen["revision"] == r.candidate_revision == seen["workspace"]


def test_F01_simulated_evidence_is_not_qualified():
    tmp = Path(tempfile.mkdtemp())
    (tmp/"r.txt").write_text("sim")
    ctl = GateController(str(tmp/"s.json"))
    ev = Evidence.from_run("sta", Status.PASS, reason="SIMULATED", command=["sim"],
                           exit_code=0, analysis_view="MAPPED_NETLIST",
                           tool_versions={"sim":"1"}, reports=[str(tmp/"r.txt")])
    ctl.record_evidence(ev, strict=False, simulated="SIMULATED-LABEL")
    d = ctl.evaluate_gate("G", ["sta"])
    assert not getattr(d, "qualified", False)
