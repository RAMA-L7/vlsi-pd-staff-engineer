"""R3 regression for R2-04: the final-acceptance mutation window.

All verification providers are EXPLICITLY LABELLED SIMULATED. No real EDA tool,
no live inference and no external service is used. The tests drive a bounded ECO
acceptance and then inject a mutation in the window between the last pre-commit
snapshot and the downstream consumer (the window described by R2-04 / F-11).

EXPLOIT tests FAIL against the vulnerable baseline (the mutation is consumed by
downstream while a stale ``status=PASS`` record is returned) and PASS after the
fix, which binds downstream to a verified immutable accepted snapshot and
re-hashes immediately after the callback, failing closed on any mutation.

GUARD tests hold both before and after (no existing gate is weakened).
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import assistant.eco as eco_mod
from assistant.controller import GateController
from assistant.eco import (
    EcoChangeScope,
    EcoManager,
    EcoStage,
    ProtectedMetric,
)
from assistant.evidence import Evidence
from assistant.hashing import sha256_file, stable_json_hash
from assistant.statuses import Status

SIM = "SIMULATED R3 R2-04 acceptance-mutation regression"
ACCEPTED = "module d; endmodule  // accepted candidate\n"
MUTANT = "module d; assign x = 1'b0;  // MUTATED BY DAEMON WRITER\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "design.v").write_text(ACCEPTED, encoding="utf-8")
    ctl = GateController(str(tmp_path / "state.json"))
    for fact in ("has_macros", "has_upf", "has_clock_gates", "has_analog"):
        ctl.register_design_fact(fact, False, source="SIMULATED inventory")
    eco = EcoManager(
        str(ws), ctl,
        checkpoint_dir=str(tmp_path / "ckpt"),
        experiment_log=str(tmp_path / "exp.json"),
    )
    rep = tmp_path / "sim_report.txt"
    rep.write_text("SIMULATED clean verification report\n", encoding="utf-8")
    return SimpleNamespace(
        ws=ws, ctl=ctl, eco=eco, rep=rep, tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )


def _ev(rep, cid):
    return Evidence.from_run(
        cid, Status.PASS, reason="SIMULATED", command=["sim"], exit_code=0,
        analysis_view="MAPPED_NETLIST", tool_versions={"sim": "1"},
        reports=[str(rep)],
    )


def _provider(rep):
    def verify():
        return [_ev(rep, cid) for cid in ("eco_lec", "eco_sta", "eco_physical_drc")]
    return verify


def _scope():
    return EcoChangeScope(
        "logic_optimization", EcoStage.MAPPED, True, "SIMULATED recorded scope"
    )


def _run(e, **kw):
    kw.setdefault("simulated", SIM)
    return e.eco.run_experiment(
        change_scope=kw.pop("scope", _scope()),
        experiment_id=kw.pop("experiment_id", "r2-04"),
        description="SIMULATED R2-04 acceptance-mutation regression",
        change_class="logic_optimization",
        changed_files=["design.v"],
        apply_change=kw.pop("apply_change", lambda: None),
        evaluate=kw.pop("evaluate", lambda: {"wns": 1.0}),
        protected_metrics=[ProtectedMetric("wns", 0.0)],
        verify=kw.pop("verify", None),
        downstream_stage=kw.pop("downstream", None),
        verification_timeout_s=kw.pop("timeout", None),
        **kw,
    )


def _immutable_design(e, result):
    """Read design.v from the immutable accepted snapshot bound to ``result``."""
    dirpath = getattr(result, "immutable_snapshot_dir", "") or ""
    if not dirpath:
        return None
    candidate = Path(dirpath) / "design.v"
    return candidate.read_text(encoding="utf-8") if candidate.is_file() else None


def _hash_tree(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""
        for name in files:
            rel = f"{rel_dir}/{name}".lstrip("/")
            out[rel] = sha256_file(os.path.join(dirpath, name))
    return out


def _assert_fail_closed(e, result):
    """One terminal, non-PASS record; mutation flagged; non-qualifying."""
    hist = e.eco.history()
    assert len(hist) == 1, f"expected one durable record, got {hist}"
    rec = hist[-1]
    assert rec["status"] != "PASS", f"durable PASS survived mutation: {rec}"
    assert result.status != "PASS", f"returned PASS after mutation: {result}"
    assert rec["status"] == result.status, (rec["status"], result.status)
    assert rec["late_mutation_detected"] is True, rec
    assert result.late_mutation_detected is True, result
    assert rec["qualification_class"] == "NON_QUALIFYING", rec


# ---------------------------------------------------------------------------
# GUARD: a clean run must still accept and bind an immutable snapshot.
# ---------------------------------------------------------------------------
def test_clean_run_binds_immutable_accepted_snapshot(env):
    e = env
    seen = {}

    def downstream(result):
        seen["dir"] = getattr(result, "immutable_snapshot_dir", "")
        seen["bytes"] = _immutable_design(e, result)

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    assert result.status == "PASS"
    assert len(e.eco.history()) == 1 and e.eco.history()[-1]["status"] == "PASS"
    # The immutable copy is a distinct, verified snapshot of the accepted bytes.
    assert seen["dir"], "no immutable accepted snapshot was handed to downstream"
    assert seen["bytes"] == ACCEPTED
    assert stable_json_hash(_hash_tree(seen["dir"])) == result.candidate_revision


def test_guard_mutation_before_final_acceptance_is_rejected(env):
    """GUARD: mutation between revalidation and the final snapshot is caught."""
    e = env
    original = e.eco._pre_acceptance_revalidation

    def wrapped(**kwargs):
        original(**kwargs)
        (e.ws / "design.v").write_text(MUTANT, encoding="utf-8")

    e.monkeypatch.setattr(e.eco, "_pre_acceptance_revalidation", wrapped)
    result = _run(e, verify=_provider(e.rep))
    assert result.status != "PASS"
    assert "candidate changed" in result.reason


# ---------------------------------------------------------------------------
# EXPLOIT: mutation in the window after the last pre-downstream snapshot.
# ---------------------------------------------------------------------------
def test_mutation_immediately_before_downstream_is_detected(env):
    """EXPLOIT: a writer mutates design.v between the pre-check and downstream."""
    e = env
    original_call = eco_mod.EcoManager._call_downstream
    seen = {}

    def hooked_call(_self, downstream_stage, result):
        # Simulated daemon writer: lands AFTER the last pre-downstream snapshot.
        (e.ws / "design.v").write_text(MUTANT, encoding="utf-8")
        seen["live_bytes"] = (e.ws / "design.v").read_text(encoding="utf-8")
        seen["immutable_bytes"] = _immutable_design(e, result)
        return original_call(downstream_stage, result)

    e.monkeypatch.setattr(eco_mod.EcoManager, "_call_downstream", hooked_call)
    result = _run(e, verify=_provider(e.rep), downstream=lambda _r: None)
    _assert_fail_closed(e, result)
    # Downstream must only ever see the immutable accepted bytes, never MUTANT.
    assert seen.get("immutable_bytes") == ACCEPTED
    assert seen.get("live_bytes") == MUTANT


def test_mutation_during_downstream_is_detected(env):
    """EXPLOIT: the downstream callback itself mutates design.v then reads it."""
    e = env
    seen = {}

    def downstream(result):
        (e.ws / "design.v").write_text(MUTANT, encoding="utf-8")
        seen["live_bytes"] = (e.ws / "design.v").read_text(encoding="utf-8")
        seen["immutable_bytes"] = _immutable_design(e, result)

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    _assert_fail_closed(e, result)
    assert seen.get("immutable_bytes") == ACCEPTED
    assert seen.get("live_bytes") == MUTANT


def test_daemon_concurrent_writer_during_downstream_is_detected(env):
    """EXPLOIT: a background daemon writer mutates design.v during downstream."""
    e = env
    go = threading.Event()
    done = threading.Event()
    seen = {}

    def writer():
        go.wait(timeout=5)
        (e.ws / "design.v").write_text(MUTANT, encoding="utf-8")
        done.set()

    thread = threading.Thread(target=writer, name="simulated-daemon-writer", daemon=True)
    thread.start()

    def downstream(result):
        seen["immutable_bytes"] = _immutable_design(e, result)
        go.set()
        assert done.wait(timeout=5), "daemon writer did not complete during downstream"

    try:
        result = _run(e, verify=_provider(e.rep), downstream=downstream)
    finally:
        go.set()
        thread.join(timeout=5)
    assert not thread.is_alive(), "daemon writer did not finish"
    _assert_fail_closed(e, result)
    assert seen.get("immutable_bytes") == ACCEPTED
    assert (e.ws / "design.v").read_text(encoding="utf-8") == MUTANT


def test_downstream_receives_only_immutable_accepted_revision(env):
    """EXPLOIT: a daemon mutates the live file; downstream reads the bound copy."""
    e = env
    seen = {}

    def downstream(result):
        # A cooperating consumer uses the immutable snapshot bound to the
        # accepted result; the live workspace may be mutated concurrently.
        seen["immutable_bytes"] = _immutable_design(e, result)
        seen["live_before"] = (e.ws / "design.v").read_text(encoding="utf-8")
        (e.ws / "design.v").write_text(MUTANT, encoding="utf-8")

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    assert seen.get("immutable_bytes") == ACCEPTED, (
        "downstream did not receive the immutable accepted revision"
    )
    assert seen.get("live_before") == ACCEPTED
    _assert_fail_closed(e, result)


def test_immutable_snapshot_hash_matches_bound_dependency_identity(env):
    """The immutable copy is bound to the same revision identity as the evidence."""
    e = env
    seen = {}

    def downstream(result):
        seen["rev"] = result.candidate_revision
        seen["deps"] = result.dependency_revision
        seen["dir"] = getattr(result, "immutable_snapshot_dir", "")
        seen["rev_in_verification"] = result.verification.get("candidate_revision")

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    assert result.status == "PASS"
    assert seen["dir"]
    assert stable_json_hash(_hash_tree(seen["dir"])) == seen["rev"]
    assert seen["rev_in_verification"] == seen["rev"]
    assert seen["deps"] == result.dependency_revision
