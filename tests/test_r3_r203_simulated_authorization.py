"""R3 / R2-03 exploit regression — simulated evidence must never authorize
production (downstream) execution.

Baseline reproduced by the lead: an upstream gate decision can be
``decision=PASS, disposition=SIMULATED_PASS, qualified=False`` while the
downstream gate re-evaluation keys only on ``re_dec.passed`` (which is True
for that decision).  A genuinely tool-derived PASS on the downstream gate
then produces a clean qualified downstream PASS.

Every "exploit_*" test below MUST fail on the vulnerable code and pass after
the fix; the "guard_*" tests must pass on both (they protect against an
over-broad fix that would weaken valid N-A / genuine tool PASS). EXT-05
refines the waiver guard: a waived FAIL still reports WAIVED_PASS for workflow
continuity but is non-authorizing (qualified=False), so the guard asserts the
weaker, correct authorization property instead.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from assistant.cli import EXIT_OK, EXIT_VALIDATION_FAILED
from assistant.controller import GateController, Waiver
from assistant.evidence import Evidence
from assistant.statuses import Status

REPO_ROOT = Path(__file__).resolve().parents[1]
SIM_LABEL = "R2-03-exploit: caller-labelled simulated (no real tool provenance)"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _sim_pass(check_id: str) -> Evidence:
    """Minimal caller-simulated PASS (explicit strict opt-out at record time)."""
    return Evidence.from_run(
        check_id=check_id,
        status=Status.PASS,
        reason="meets target",
        exit_code=0,
        analysis_view="ROUTED_EXTRACTED",
    )


def _genuine_pass(tmp_path: Path, check_id: str) -> Evidence:
    """Authoritative PASS with complete strict tool provenance."""
    rpt = tmp_path / f"{check_id}_real.rpt"
    rpt.write_text("slack 0.10 ns\n", encoding="utf-8")
    _gv = Evidence.from_run(
        check_id=check_id,
        status=Status.PASS,
        reason="meets target",
        command=[check_id, "--run-real-tool"],
        exit_code=0,
        analysis_view="ROUTED_EXTRACTED",
        tool_versions={check_id: "1.0"},
        reports=[str(rpt)],
    )
    try:
        import r62_proof_helper as _ph
        _d, _cmd = _ph.proof_and_command(check_id=check_id)
        if _d is not None:
            _gv.execution_proof = _d
            object.__setattr__(_gv, "command", list(_cmd))
    except Exception:
        pass
    return _gv


def _seed(tmp_path: Path):
    """UP is authorized only by simulated PASS; DOWN is a genuine tool PASS.

    Returns ``(controller, state_path, simulated_upstream_evidence_id)``.  The
    upstream decision is evaluated once so it is persisted in gate_history.
    """
    state = tmp_path / "state.json"
    ctl = GateController(str(state))
    sim_ev = ctl.record_evidence(
        _sim_pass("sta_up"), strict=False, simulated=SIM_LABEL
    )
    ctl.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    up = ctl.evaluate_gate("UP", ["sta_up"])
    assert up.disposition == "SIMULATED_PASS" and up.qualified is False
    return ctl, state, sim_ev.evidence_id


def _exploit_downstream(ctl):
    """Evaluate the real downstream gate that depends on simulated UP."""
    return ctl.evaluate_gate("DOWN", ["sta_down"], upstream_gates=["UP"])


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "assistant", *[str(a) for a in args]],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )


# ---------------------------------------------------------------------------
# 1. simulated upstream into real downstream (core exploit)
# ---------------------------------------------------------------------------
def test_exploit_simulated_upstream_cannot_authorize_real_downstream(tmp_path):
    ctl, _, _ = _seed(tmp_path)
    gd = _exploit_downstream(ctl)
    assert gd.disposition == "BLOCKED", (
        f"EXPLOIT: simulated upstream produced downstream {gd.disposition}"
        f"/qualified={gd.qualified}/decision={gd.decision}"
    )
    assert gd.qualified is False
    assert gd.passed is False
    up_check = gd.checks.get("upstream:UP", {})
    assert up_check.get("status") == "BLOCKED"
    assert up_check.get("disposition") == "SIMULATED_PASS"
    assert "SIMULATED_PASS" in gd.reason


def test_exploit_simulated_upstream_blocks_can_enter_stage(tmp_path):
    ctl, _, _ = _seed(tmp_path)
    ok, reasons = ctl.can_enter_stage(
        "production", [("DOWN", ["sta_down"])], upstream=["UP"]
    )
    assert not ok, f"EXPLOIT: can_enter_stage authorized simulated upstream: {reasons}"
    assert any("SIMULATED_PASS" in r for r in reasons)


# ---------------------------------------------------------------------------
# 2. serialized state tamper — caller forges upstream gate_history fields
# ---------------------------------------------------------------------------
def test_exploit_serialized_gate_history_tamper_cannot_authorize(tmp_path):
    ctl, state, _ = _seed(tmp_path)
    _exploit_downstream(ctl)

    raw = json.loads(state.read_text(encoding="utf-8"))
    forged = 0
    for entry in raw["gate_history"]:
        if entry.get("gate_id") == "UP":
            entry["decision"] = "PASS"
            entry["disposition"] = "CLEAN_PASS"
            entry["qualified"] = True
            entry["simulated_checks"] = []
            forged += 1
    assert forged >= 1
    state.write_text(json.dumps(raw), encoding="utf-8")

    c2 = GateController(str(state))
    gd = _exploit_downstream(c2)
    assert gd.disposition == "BLOCKED", (
        f"EXPLOIT: forged gate_history authorized downstream: {gd.disposition}"
    )
    assert gd.qualified is False


# ---------------------------------------------------------------------------
# 3. forged provenance — caller strips the marker from the evidence record
# ---------------------------------------------------------------------------
def test_exploit_forged_provenance_in_evidence_record_cannot_authorize(tmp_path):
    ctl, state, eid = _seed(tmp_path)

    raw = json.loads(state.read_text(encoding="utf-8"))
    raw["evidence"][eid]["evidence_class"] = "TOOL"
    raw["evidence"][eid]["simulated_by"] = ""
    state.write_text(json.dumps(raw), encoding="utf-8")

    c2 = GateController(str(state))
    # Independent provenance map still classifies the record as simulated.
    up = c2.evaluate_gate("UP", ["sta_up"])
    assert up.disposition == "SIMULATED_PASS" and up.qualified is False
    gd = _exploit_downstream(c2)
    assert gd.disposition == "BLOCKED", (
        f"EXPLOIT: forged evidence provenance authorized downstream: "
        f"{gd.disposition}"
    )
    assert gd.qualified is False


def test_exploit_forged_provenance_map_cannot_authorize(tmp_path):
    """Tampering with the provenance map alone cannot strip classification."""
    ctl, state, eid = _seed(tmp_path)

    raw = json.loads(state.read_text(encoding="utf-8"))
    raw["evidence_provenance"][eid]["evidence_class"] = "TOOL"
    raw["evidence_provenance"][eid]["simulated_by"] = ""
    state.write_text(json.dumps(raw), encoding="utf-8")

    c2 = GateController(str(state))
    up = c2.evaluate_gate("UP", ["sta_up"])
    assert up.disposition == "SIMULATED_PASS" and up.qualified is False
    gd = _exploit_downstream(c2)
    assert gd.disposition == "BLOCKED"
    assert gd.qualified is False


# ---------------------------------------------------------------------------
# 4. CLI exit code + gate-report agreement
# ---------------------------------------------------------------------------
def test_exploit_cli_gate_evaluate_exits_nonzero_for_simulated_upstream(tmp_path):
    ctl, state, _ = _seed(tmp_path)  # simulator path can only seed via direct API

    proc = run_cli(
        "gate-evaluate", "--state", str(state), "--gate", "DOWN",
        "--required", "sta_down", "--upstream", "UP",
    )
    assert proc.returncode == EXIT_VALIDATION_FAILED, (
        f"EXPLOIT: CLI authorized simulated upstream (rc={proc.returncode}): "
        f"{proc.stdout}"
    )
    data = json.loads(proc.stdout)
    assert data["disposition"] == "BLOCKED"
    assert data["qualified"] is False

    rep = run_cli("gate-report", "--state", str(state))
    assert rep.returncode == EXIT_OK
    report = json.loads(rep.stdout)
    assert report["last_gate_disposition"] == "BLOCKED"
    assert report["gate_history"][-1]["qualified"] is False


# ---------------------------------------------------------------------------
# 5. restarted controller state
# ---------------------------------------------------------------------------
def test_exploit_restarted_controller_keeps_downstream_nonqualified(tmp_path):
    ctl, state, _ = _seed(tmp_path)
    assert _exploit_downstream(ctl).disposition == "BLOCKED"

    c2 = GateController(str(state))  # fresh controller loads persisted state
    gd = _exploit_downstream(c2)
    assert gd.disposition == "BLOCKED", (
        f"EXPLOIT: restart re-qualified simulated upstream: {gd.disposition}"
    )
    assert gd.qualified is False
    assert c2.report()["last_gate_disposition"] == "BLOCKED"
    assert c2.report()["gate_history"][-1]["qualified"] is False


# ---------------------------------------------------------------------------
# guards — these must keep passing (do not weaken real authorizations)
# ---------------------------------------------------------------------------
def test_guard_genuine_tool_upstream_pass_still_authorizes(tmp_path):
    state = tmp_path / "state.json"
    ctl = GateController(str(state))
    ctl.record_evidence(_genuine_pass(tmp_path, "sta_up"))
    ctl.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    up = ctl.evaluate_gate("UP", ["sta_up"])
    assert up.disposition == "CLEAN_PASS" and up.qualified is True
    gd = _exploit_downstream(ctl)
    assert gd.disposition == "CLEAN_PASS"
    assert gd.qualified is True
    ok, reasons = ctl.can_enter_stage(
        "production", [("DOWN", ["sta_down"])], upstream=["UP"]
    )
    assert ok, reasons


def test_guard_waived_upstream_never_authorizes(tmp_path):
    """EXT-05: a waived FAIL reports WAIVED_PASS but is not qualified."""
    state = tmp_path / "state.json"
    ctl = GateController(str(state))
    ctl.record_evidence(
        Evidence.from_run(
            "antenna", Status.FAIL, "waived finding",
            exit_code=0, analysis_view="ROUTED_EXTRACTED",
        ),
        strict=False, simulated="unit-test waived finding",
    )
    ctl.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    ctl.add_waiver(Waiver(
        check_id="antenna", scope="UP", reason="documented tool gap in this corner",
        approved_by="reviewer", policy="tool_gap",
    ))
    ctl.record_evidence(_genuine_pass(tmp_path, "up_proof"))
    up = ctl.evaluate_gate("UP", ["antenna", "up_proof"])
    assert up.disposition == "WAIVED_PASS"
    assert up.qualified is False  # waived check is non-authorizing (B-03)
    gd = _exploit_downstream(ctl)
    assert gd.qualified is False  # a non-qualified upstream cannot authorize DOWN


def test_guard_valid_not_applicable_upstream_still_authorizes(tmp_path):
    state = tmp_path / "state.json"
    ctl = GateController(str(state))
    facts = {"has_upf": False}
    ctl.register_design_fact("has_upf", False, source="inventory")
    ctl.mark_not_applicable_for_check(
        "upf_check",
        "NA-RULE:NO-UPF design has no UPF/CPF power-intent file in scope",
        design_facts=facts,
    )
    ctl.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    ctl.record_evidence(_genuine_pass(tmp_path, "up_proof"))
    up = ctl.evaluate_gate("UP", ["upf_check", "up_proof"])
    assert up.disposition == "NOT_APPLICABLE" and up.qualified is True
    gd = _exploit_downstream(ctl)
    assert gd.qualified is True
