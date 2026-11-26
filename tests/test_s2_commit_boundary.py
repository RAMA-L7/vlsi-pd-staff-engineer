"""S2: precise durable commit-boundary semantics (reader-side reconciliation).

These tests lock down the meaning of a durable PASS record when the run-level
``state.json`` says CANCELLED. They are read-only with respect to both durable
artifacts and change no authorization path.
"""
from __future__ import annotations

import hashlib
import json

from assistant.commit_boundary import (
    reconcile_experiment_log,
    reconcile_record,
)

PASS_SIM = {
    "status": "PASS", "qualification_class": "SIMULATED", "cancelled": False,
    "transaction_token": "tok-1", "invalidation_kind": None,
}
FAIL_UNQUAL = {
    "status": "FAIL", "qualification_class": "UNQUALIFIED", "cancelled": False,
    "transaction_token": "tok-2",
}


def _write(tmp_path, records, run_status):
    exp = tmp_path / "exp.json"
    state = tmp_path / "state.json"
    exp.write_text(json.dumps({"experiments": records}), encoding="utf-8")
    state.write_text(json.dumps({"run_status": run_status}), encoding="utf-8")
    return exp, state


def _hashes(*paths):
    return tuple(hashlib.sha256(p.read_bytes()).hexdigest() for p in paths)


def test_pre_boundary_active_pass_is_effective_and_not_contradiction():
    r = reconcile_record(PASS_SIM, "ACTIVE")
    assert r.raw_status == "PASS"
    assert r.effective_status == "PASS"
    assert r.effective_qualified is True
    assert r.contradiction is False


def test_post_boundary_cancel_supersedes_pass_record():
    """The exact residual: PASS + cancelled=false vs state.json CANCELLED."""
    r = reconcile_record(PASS_SIM, "CANCELLED")
    assert r.raw_status == "PASS"            # historical fact preserved
    assert r.raw_cancelled is False          # commit-time observation preserved
    assert r.durable_run_status == "CANCELLED"
    assert r.effective_status == "CANCELLED"
    assert r.effective_qualified is False
    assert r.contradiction is True
    assert "post-boundary artifact" in r.interpretation
    assert "no downstream authority" in r.interpretation


def test_pre_boundary_closure_non_pass_with_cancelled_state():
    r = reconcile_record(FAIL_UNQUAL, "CANCELLED")
    assert r.effective_status == "CANCELLED"
    assert r.effective_qualified is False
    assert r.contradiction is False          # no PASS to contradict


def test_cancelled_marker_preserved_on_qualified_record():
    r = reconcile_record({**PASS_SIM, "cancelled": True}, "ACTIVE")
    assert r.raw_cancelled is True
    assert r.effective_qualified is True     # helper does not re-derive authority


def test_unknown_or_missing_run_status_fails_closed(tmp_path):
    r = reconcile_record(PASS_SIM, "<unreadable>")
    assert r.effective_qualified is False
    assert r.contradiction is False
    exp, state = _write(tmp_path, [PASS_SIM], "ACTIVE")
    state.unlink()
    out = reconcile_experiment_log(exp, state)
    assert out["durable_run_status"] == "<unreadable>"
    assert out["downstream_run_status_ok"] is False
    assert out["effective_qualified_pass_count"] == 0


def test_reconcile_experiment_log_counts_and_contradictions(tmp_path):
    exp, state = _write(tmp_path, [PASS_SIM, FAIL_UNQUAL], "CANCELLED")
    out = reconcile_experiment_log(exp, state)
    assert out["durable_run_status"] == "CANCELLED"
    assert out["raw_qualified_pass_count"] == 1
    assert out["effective_qualified_pass_count"] == 0
    assert out["contradiction_count"] == 1
    assert out["contradictions"][0]["transaction_token"] == "tok-1"
    assert out["downstream_run_status_ok"] is False
    assert "GateController" in out["authorization_note"]


def test_helper_is_strictly_read_only(tmp_path):
    exp, state = _write(tmp_path, [PASS_SIM], "CANCELLED")
    before = _hashes(exp, state)
    reconcile_experiment_log(exp, state)
    assert _hashes(exp, state) == before


def test_missing_exp_json_is_handled(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"run_status": "CANCELLED"}), encoding="utf-8")
    out = reconcile_experiment_log(tmp_path / "exp.json", state)
    assert out["records"] == []
    assert out["contradiction_count"] == 0
    assert out["durable_run_status"] == "CANCELLED"
