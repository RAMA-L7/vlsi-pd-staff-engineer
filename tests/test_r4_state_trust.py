"""Round 4 / state-trust regression tests (Agent 6).

Establishes the authoritative trust boundary for gate authorization:

* AUTHORITATIVE state: evidence records (+ their integrity binding), gate
  definitions / registered gate specs, and provenance.
* DESCRIPTIVE (cached) state: ``gate_history`` and last-decision summaries.
  These are NEVER trusted to supply ``required_checks`` for authorization.

The ``test_exploit_*`` tests below MUST fail on the vulnerable baseline
(``controller.py`` before this remediation) and pass after the fix.  The
``test_guard_*`` tests must pass on both; they protect legitimate tool PASS,
validated NOT_APPLICABLE and the reporting form of waivers from an over-broad
fix.  EXT-05 refines the waiver guard: WAIVED_PASS stays visible but is non-
authorizing (qualified=False), so that assertion was tightened.

Threat model (honest): the attacker can write ``state.json``.  The integrity
binding is a lightweight HMAC whose key is stored in the same file, so a
sophisticated attacker who understands the scheme can recompute it.  These
tests model the realistic tamper (flip/rename cached fields) which the binding
is designed to detect; the residual is documented in AGENT6_REPORT.md.
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
SIM_LABEL = "R4-state-trust: caller-labelled simulated (no real tool provenance)"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _report(tmp_path: Path, name: str) -> str:
    p = tmp_path / f"{name}.rpt"
    p.write_text("slack 0.10 ns\n", encoding="utf-8")
    return str(p)


def _genuine_pass(tmp_path: Path, check_id: str) -> Evidence:
    """Authoritative PASS with complete strict tool provenance."""
    _gv = Evidence.from_run(
        check_id=check_id,
        status=Status.PASS,
        reason="meets target",
        command=[check_id, "--run-real-tool"],
        exit_code=0,
        analysis_view="ROUTED_EXTRACTED",
        tool_versions={check_id: "1.0"},
        reports=[_report(tmp_path, check_id)],
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


def _sim_pass(check_id: str) -> Evidence:
    """Minimal caller-simulated PASS (explicit strict opt-out at record time)."""
    return Evidence.from_run(
        check_id=check_id,
        status=Status.PASS,
        reason="meets target",
        exit_code=0,
        analysis_view="ROUTED_EXTRACTED",
    )


def _seed_up_down(tmp_path: Path):
    """UP is authorized only by a simulated PASS; DOWN has a genuine PASS.

    Also records a *second, genuine* check ``sta_down`` so a tamperer has a
    genuine-evidence candidate to substitute into UP's required-check list
    (the exact A3-F1 precondition).
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


def _write_state(state: Path, raw: dict) -> None:
    state.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "assistant", *[str(a) for a in args]],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )


def _forge_up_required_checks(state: Path, checks) -> None:
    """A3-F1: rewrite the cached upstream required-check list in gate_history."""
    raw = json.loads(state.read_text(encoding="utf-8"))
    forged = 0
    for entry in raw["gate_history"]:
        if entry.get("gate_id") == "UP":
            entry["required_checks"] = list(checks)
            entry["required_view"] = None
            entry["upstream_gates"] = []
            forged += 1
    assert forged >= 1
    _write_state(state, raw)


# ---------------------------------------------------------------------------
# A3-F1 -- cached required_checks must never re-qualify an upstream gate
# ---------------------------------------------------------------------------
def test_exploit_required_checks_tamper_cannot_requalify_upstream(tmp_path):
    ctl, state, _ = _seed_up_down(tmp_path)
    # Attacker substitutes a check backed by genuine evidence ("sta_down") for
    # UP's real (simulated) check ("sta_up").
    _forge_up_required_checks(state, ["sta_down"])

    c2 = GateController(str(state))
    gd = c2.evaluate_gate("DOWN", ["sta_down"], upstream_gates=["UP"])
    assert gd.qualified is False, (
        "EXPLOIT: forged cached required_checks re-qualified upstream: "
        f"disposition={gd.disposition}/qualified={gd.qualified}"
    )
    assert gd.disposition == "BLOCKED"
    up_check = gd.checks.get("upstream:UP", {})
    assert up_check.get("status") == "BLOCKED"
    assert up_check.get("disposition") == "SIMULATED_PASS"


def test_exploit_unregistered_gate_fails_closed_on_upstream_reeval(tmp_path):
    """An upstream with cached history but no authoritative definition fails closed."""
    state = tmp_path / "state.json"
    ctl = GateController(str(state))
    ctl.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    # Forge a gate_history entry for UP that looks qualified, backed by genuine
    # evidence, but NEVER evaluate UP through the API -> no gate definition.
    raw = json.loads(state.read_text(encoding="utf-8"))
    raw["gate_history"].append({
        "gate_id": "UP",
        "stage": "",
        "required_checks": ["sta_down"],
        "required_view": None,
        "upstream_gates": [],
        "decision": "PASS",
        "disposition": "CLEAN_PASS",
        "qualified": True,
        "checks": {},
    })
    _write_state(state, raw)

    c2 = GateController(str(state))
    gd = c2.evaluate_gate("DOWN", ["sta_down"], upstream_gates=["UP"])
    assert gd.qualified is False, (
        "EXPLOIT: upstream with no authoritative definition authorized downstream"
    )
    assert gd.disposition == "BLOCKED"
    assert "no authoritative" in gd.reason or gd.checks.get("upstream:UP", {}).get(
        "status") == "BLOCKED"


def test_exploit_downstream_cli_exit_with_tampered_upstream(tmp_path):
    ctl, state, _ = _seed_up_down(tmp_path)
    _forge_up_required_checks(state, ["sta_down"])

    proc = run_cli(
        "gate-evaluate", "--state", str(state), "--gate", "DOWN",
        "--required", "sta_down", "--upstream", "UP",
    )
    assert proc.returncode == EXIT_VALIDATION_FAILED, (
        f"EXPLOIT: CLI authorized tampered upstream (rc={proc.returncode}): "
        f"{proc.stdout}"
    )
    data = json.loads(proc.stdout)
    assert data["qualified"] is False
    assert data["disposition"] == "BLOCKED"


# ---------------------------------------------------------------------------
# A3-F2 -- provenance integrity binding
# ---------------------------------------------------------------------------
def test_exploit_both_provenance_markers_flip_cannot_relabel_simulated(tmp_path):
    ctl, state, eid = _seed_up_down(tmp_path)

    raw = json.loads(state.read_text(encoding="utf-8"))
    # Flip BOTH markers in the same unprotected state.json.
    raw["evidence"][eid]["evidence_class"] = "TOOL"
    raw["evidence"][eid]["simulated_by"] = ""
    raw["evidence_provenance"][eid]["evidence_class"] = "TOOL"
    raw["evidence_provenance"][eid]["simulated_by"] = ""
    _write_state(state, raw)

    c2 = GateController(str(state))
    up = c2.evaluate_gate("UP", ["sta_up"])
    assert up.disposition == "SIMULATED_PASS", (
        "EXPLOIT: flipping both markers made simulated evidence authoritative: "
        f"disposition={up.disposition}/qualified={up.qualified}"
    )
    assert up.qualified is False
    gd = c2.evaluate_gate("DOWN", ["sta_down"], upstream_gates=["UP"])
    assert gd.qualified is False and gd.disposition == "BLOCKED"


def test_exploit_missing_provenance_fails_closed(tmp_path):
    ctl, state, eid = _seed_up_down(tmp_path)

    raw = json.loads(state.read_text(encoding="utf-8"))
    # Remove all provenance: the independent map entry AND the evidence's own
    # markers. On the baseline nothing is marked simulated -> CLEAN_PASS.
    raw["evidence_provenance"].pop(eid, None)
    raw["evidence"][eid].pop("evidence_class", None)
    raw["evidence"][eid].pop("simulated_by", None)
    _write_state(state, raw)

    c2 = GateController(str(state))
    up = c2.evaluate_gate("UP", ["sta_up"])
    assert up.qualified is False, (
        "EXPLOIT: evidence with no provenance was treated as authoritative"
    )
    assert up.disposition == "SIMULATED_PASS"
    assert "sta_up" in up.simulated_checks


# ---------------------------------------------------------------------------
# restart persistence
# ---------------------------------------------------------------------------
def test_exploit_restart_preserves_trust_boundary(tmp_path):
    ctl, state, eid = _seed_up_down(tmp_path)
    _forge_up_required_checks(state, ["sta_down"])
    raw = json.loads(state.read_text(encoding="utf-8"))
    raw["evidence"][eid]["evidence_class"] = "TOOL"
    raw["evidence"][eid]["simulated_by"] = ""
    raw["evidence_provenance"][eid]["evidence_class"] = "TOOL"
    raw["evidence_provenance"][eid]["simulated_by"] = ""
    _write_state(state, raw)

    # Cancel + restart, then evaluate from a fresh controller.
    c2 = GateController(str(state))
    c2.cancel("maintenance")
    c2.restart("operator approved restart")
    c3 = GateController(str(state))
    gd = c3.evaluate_gate("DOWN", ["sta_down"], upstream_gates=["UP"])
    assert gd.qualified is False and gd.disposition == "BLOCKED", (
        "EXPLOIT: restart re-qualified a tampered upstream"
    )
    assert c3.report()["last_gate_disposition"] == "BLOCKED"


# ---------------------------------------------------------------------------
# guards -- legitimate flows must not be weakened
# ---------------------------------------------------------------------------
def test_guard_genuine_tool_pass_still_authorizes(tmp_path):
    state = tmp_path / "state.json"
    ctl = GateController(str(state))
    ctl.record_evidence(_genuine_pass(tmp_path, "sta_up"))
    ctl.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    up = ctl.evaluate_gate("UP", ["sta_up"])
    assert up.disposition == "CLEAN_PASS" and up.qualified is True
    gd = ctl.evaluate_gate("DOWN", ["sta_down"], upstream_gates=["UP"])
    assert gd.disposition == "CLEAN_PASS" and gd.qualified is True
    ok, reasons = ctl.can_enter_stage(
        "production", [("DOWN", ["sta_down"])], upstream=["UP"]
    )
    assert ok, reasons


def test_guard_waiver_and_na_still_authorize(tmp_path):
    state = tmp_path / "state.json"
    ctl = GateController(str(state))
    ctl.register_design_fact("has_upf", False, source="inventory")
    ctl.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    # Validated NOT_APPLICABLE on an ancillary check.
    ctl.mark_not_applicable_for_check(
        "upf_check",
        "NA-RULE:NO-UPF design has no UPF/CPF power-intent file in scope",
        design_facts={"has_upf": False},
    )
    ctl.record_evidence(_genuine_pass(tmp_path, "up_proof"))
    up = ctl.evaluate_gate("UP", ["upf_check", "up_proof"])
    assert up.disposition == "NOT_APPLICABLE" and up.qualified is True
    gd = ctl.evaluate_gate("DOWN", ["sta_down"], upstream_gates=["UP"])
    assert gd.qualified is True

    # EXT-05: a valid waiver on a waivable FAIL keeps the reporting PASS but is
    # non-authorizing (qualified=False); validated N-A above still authorizes.
    c2 = GateController(str(tmp_path / "state2.json"))
    c2.record_evidence(
        Evidence.from_run(
            "antenna", Status.FAIL, "waived finding",
            exit_code=0, analysis_view="ROUTED_EXTRACTED",
        ),
        strict=False, simulated="unit-test waived finding",
    )
    c2.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    c2.add_waiver(Waiver(
        check_id="antenna", scope="UP", reason="documented tool gap in this corner",
        approved_by="reviewer", policy="tool_gap",
    ))
    c2.record_evidence(_genuine_pass(tmp_path, "up_proof2"))
    up2 = c2.evaluate_gate("UP", ["antenna", "up_proof2"])
    assert up2.disposition == "WAIVED_PASS" and up2.passed
    assert up2.qualified is False  # EXT-05: waived FAIL carries no authorization
    gd2 = c2.evaluate_gate("DOWN", ["sta_down"], upstream_gates=["UP"])
    assert gd2.qualified is False
