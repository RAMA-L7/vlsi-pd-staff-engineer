"""S2: deterministic multi-process boundary cases with captured rc/output.

Each case is orchestrated by ``_s2_scratch/s2_boundary_harness.py`` in fresh
OS processes. Coordination is marker-based (no timing sleeps). The harness
writes ``suite_result.json`` (including every child's exit code and the fresh
verifier's rc) into the per-case run directory.

No security test is weakened, skipped or suppressed; these are additive.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "_s2_scratch" / "s2_boundary_harness.py"


def _run_case(case: str, tmp_path: Path):
    work = tmp_path / case
    work.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "ASSISTANT_LEDGER_KEY": "S2-COMMIT-BOUNDARY-KEY-20261126",
           "PYTHONDONTWRITEBYTECODE": "1"}
    cp = subprocess.run(
        [sys.executable, "-B", str(HARNESS), "run", case, str(work), str(ROOT)],
        cwd=str(work), env=env, capture_output=True, text=True, timeout=180,
    )
    result_path = work / "suite_result.json"
    assert result_path.exists(), cp.stdout + cp.stderr
    obs = json.loads(result_path.read_text(encoding="utf-8"))["results"][0]
    return cp, obs, work


def _verdict(obs):
    return obs["verify"]["payload"] or {}


def test_crash_after_replace_residual(tmp_path):
    cp, obs, work = _run_case("crash_after_replace", tmp_path)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert 91 in obs["child_rcs"]                      # writer os._exit(91)
    assert obs["verify"]["rc"] == 0
    v = _verdict(obs)
    assert v["durable_run_status"] == "CANCELLED"
    assert v["raw_qualified_pass_count"] == 1          # durable PASS artifact
    assert v["records"][0]["cancelled"] is False
    assert v["fresh_gate_qualified"] is False
    assert v["fresh_downstream_allowed"] is False
    rec = v["reconciliation"]
    assert rec["contradiction_count"] == 1
    assert rec["effective_qualified_pass_count"] == 0
    assert rec["downstream_run_status_ok"] is False


def test_pre_lock_cancel_blocks_pass(tmp_path):
    cp, obs, work = _run_case("pre_lock_cancel", tmp_path)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    v = _verdict(obs)
    assert v["durable_run_status"] == "CANCELLED"
    assert v["raw_qualified_pass_count"] == 0
    assert v["records"][0]["status"] != "PASS"
    assert v["fresh_downstream_allowed"] is False
    assert v["reconciliation"]["contradiction_count"] == 0


def test_post_lock_cancel_coexists_with_pass(tmp_path):
    cp, obs, work = _run_case("post_lock_cancel", tmp_path)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert obs["child_rcs"] == [0, 0]
    v = _verdict(obs)
    assert v["durable_run_status"] == "CANCELLED"
    assert v["raw_qualified_pass_count"] == 1
    assert v["fresh_downstream_allowed"] is False
    assert v["reconciliation"]["contradiction_count"] == 1
    assert v["reconciliation"]["effective_qualified_pass_count"] == 0


def test_concurrent_cancel_commit_serializes_by_lock(tmp_path):
    cp, obs, work = _run_case("concurrent_cancel_commit", tmp_path)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    v = _verdict(obs)
    assert v["raw_qualified_pass_count"] == 1          # commit wins the lock
    assert v["durable_run_status"] == "CANCELLED"
    assert v["fresh_downstream_allowed"] is False
    assert v["reconciliation"]["contradiction_count"] == 1


def test_lock_recovery_after_holder_crash(tmp_path):
    cp, obs, work = _run_case("lock_recovery", tmp_path)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert obs["child_rcs"] == [23]                    # holder os._exit(23)
    assert obs["lock_recovery_cancel"]["ok"] is True
    assert obs["lock_recovery_cancel"]["run_status"] == "CANCELLED"
    v = _verdict(obs)
    assert v["durable_run_status"] == "CANCELLED"
    assert v["fresh_downstream_allowed"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
