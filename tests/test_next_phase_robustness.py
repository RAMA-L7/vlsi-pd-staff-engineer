"""Workstream 5 — targeted regression / mutation-style robustness tests.

Adversarial edge cases for the remaining framework risk areas: failure
propagation, artifact integrity, acceptance enforcement, provenance, resume
behaviour and mutation testing of the persisted gate ledger.

Every provider in this module is a DETERMINISTIC, EXPLICITLY LABELLED SIMULATION.
No EDA tool, WSL session, network, credential or live inference is used.  All
files live under pytest ``tmp_path``.  These tests ADD coverage; they do not
weaken or replace any assertion in the existing 304-test suite.

Six adversarial angles are exercised:

* corrupt a hashed report *after* recording and reload the ledger from disk;
* modify a pinned dependency after recording and reload;
* mutate the persisted submissions/verdict fields (forged tool PASS, forged
  non-waivable N/A) and prove evaluation fails closed even after deserialisation;
* try to launder stale/invalid evidence with a valid waiver;
* waiver-expiry boundary (one microsecond before / exactly at expiry);
* resume with a modified stage-script hash (acceptance policy model);
* ECO rollback that silently leaves an extra file (mutation of the restore
  routine) plus the nested-extra-file cleanup happy path;
* CLI exit-code contract on a corrupted ledger and on a corrupted artifact;
* can_enter_stage failure propagation (untested public API).
"""
from __future__ import annotations

import datetime as _dt
import json
import subprocess
import sys
import types
import uuid
from pathlib import Path

import pytest

import assistant.controller as ctrl
from assistant.controller import (
    CheckSpec,
    GateController,
    GateSpec,
    Waiver,
)
from assistant.eco import CHANGE_CLASS_POLICY, EcoManager, ProtectedMetric
from assistant.eco import EcoChangeScope, EcoStage, PHYSICAL_DRC_NA_REASON
from assistant.evidence import DependencySet, Evidence
from assistant.hashing import sha256_file
from assistant.statuses import Status

REPO = Path(__file__).resolve().parent.parent

#: Labelled simulated validator. Registered under a unique name so this module
#: never depends on the import order of other test files that register their own
#: validators into the global OUTCOME_VALIDATORS registry.
VALIDATOR = "ws5_robustness_verdict"
SIM_LABEL = "WS5-labelled simulated provider (no real EDA, credentials or inference)"


def _verdict_ok(ev: Evidence, outcome: dict) -> bool:
    return outcome.get("verdict") == "PASS" and ev.status is Status.PASS


ctrl.register_outcome_validator(VALIDATOR, _verdict_ok)


# --------------------------------------------------------------------------- helpers
def _report(tmp_path: Path, name: str, text: str = "SIMULATED report: slack 0.25\n") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _record(
    ctl: GateController,
    check: str,
    status: Status,
    *,
    reports: list[Path],
    reason: str = "SIMULATED: deterministic verdict",
    exit_code: int = 0,
    view: str = "ROUTED_EXTRACTED",
    deps: DependencySet | None = None,
    ctx=None,
    spec: CheckSpec | None = None,
    outcome: dict | None = None,
) -> Evidence:
    """Record one fully-provenanced SIMULATED check result (strict boundary)."""
    _proof = None
    _cmd = ["simulated_ws5_provider", "--check", check]
    try:
        import r62_proof_helper as _ph
        _proof, _cmd = _ph.proof_and_command(check_id=check)
    except Exception:
        _proof = None
    return ctl.record_check_result(
        check,
        status,
        reason,
        command=list(_cmd),
        exit_code=exit_code,
        analysis_view=view,
        tool_versions={"simulated_ws5_provider": "1.0"},
        reports=[str(r) for r in reports],
        dependencies=deps,
        binding_context=ctx,
        engineering_outcome=(
            outcome if outcome is not None else {"verdict": status.value}
        ),
        spec=spec,
        execution_proof=_proof,
    )


def _pass(ctl: GateController, check: str, tmp_path: Path, **kw) -> Evidence:
    tag = uuid.uuid4().hex[:8]
    return _record(
        ctl, check, Status.PASS,
        reports=[_report(tmp_path, f"{check}-{tag}.rpt")],
        **kw,
    )


def _tamper_state(path: Path, mutate) -> None:
    """Adversarially mutate the persisted ledger JSON (simulated corruption)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _run_cli(*args):
    """Real child process: python -m assistant <args> (no EDA involved)."""
    return subprocess.run(
        [sys.executable, "-m", "assistant", *[str(a) for a in args]],
        cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=120,
    )


def _gate_model() -> dict:
    """Load the pure SR-02 policy model from acceptance.py (no CLI side effects)."""
    src = (REPO / "regression" / "checks" / "acceptance.py").read_text(
        encoding="utf-8", errors="replace")
    start = src.index("# --- BEGIN STAGE GATING MODEL (SR-02)")
    end = src.index("# --- END STAGE GATING MODEL")
    ns: dict = {}
    exec(compile(src[start:end], "ws5_acceptance_model", "exec"), ns)
    return ns


# ===========================================================================
# 1. Artifact integrity / provenance across a ledger reload
# ===========================================================================
def _scope(change_class="logic_optimization", *, physical=False, stage=EcoStage.MAPPED):
    """Recorded implementation scope for simulated ECO tests."""
    if change_class not in CHANGE_CLASS_POLICY:
        change_class = "logic_optimization"  # let policy_for() block the unknown class
    return EcoChangeScope(change_class, stage, physical, "unit-test recorded implementation facts")


def test_corrupt_hashed_report_after_reload_blocks_without_silent_rebind(tmp_path):
    """A report corrupted after recording must block a freshly loaded controller.

    The recorded SHA-256 must NOT be silently rewritten to the corrupt file's
    hash: revalidation reports the problem and degrades the check to NOT_RUN.
    """
    state = tmp_path / "state.json"
    report = _report(tmp_path, "sta.rpt")
    ctl = GateController(str(state))
    _record(ctl, "sta", Status.PASS, reports=[report])
    assert ctl.evaluate_gate("g", ["sta"]).passed
    recorded_hash = ctl.latest_evidence("sta").reports[0]["sha256"]

    report.write_text("WS5 ADVERSARIAL CORRUPTION of hashed report\n", encoding="utf-8")
    reloaded = GateController(str(state))
    gd = reloaded.evaluate_gate("g", ["sta"])

    assert not gd.passed
    assert gd.checks["sta"]["status"] == Status.NOT_RUN.value
    assert any("artifact modified" in p
               for p in gd.checks["sta"]["revalidation_problems"])
    # No rebind: the stored hash still names the originally recorded content.
    assert reloaded.latest_evidence("sta").reports[0]["sha256"] == recorded_hash


def test_modified_pinned_dependency_after_reload_blocks_gate(tmp_path):
    """A pinned input changed after recording blocks after a fresh load."""
    state = tmp_path / "state.json"
    rtl = tmp_path / "top.v"
    rtl.write_text("module top; endmodule  // SIMULATED original\n", encoding="utf-8")
    deps = DependencySet()
    deps.add_file("rtl", "top", str(rtl))

    ctl = GateController(str(state))
    _record(ctl, "sta", Status.PASS,
            reports=[_report(tmp_path, "sta.rpt")], deps=deps)
    assert ctl.evaluate_gate("g", ["sta"]).passed

    rtl.write_text("module top; assign x = 1'b1; endmodule  // WS5 mutation\n",
                   encoding="utf-8")
    gd = GateController(str(state)).evaluate_gate("g", ["sta"])
    assert not gd.passed
    assert any("dependency modified" in p
               for p in gd.checks["sta"]["revalidation_problems"])


# ===========================================================================
# 2. Mutation tests of the persisted ledger (forged evidence)
# ===========================================================================
def test_forged_tool_pass_without_persisted_outcome_blocks(tmp_path):
    """Stripping the parsed engineering outcome from state must break the PASS.

    ``exit_code=0`` + a recorded PASS string are not sufficient; the tool spec
    re-check at evaluation time requires the originally validated outcome.
    """
    state = tmp_path / "state.json"
    spec = GateSpec(
        "G", ("sta",),
        checks={"sta": CheckSpec("sta", "tool", engineering_validator=VALIDATOR)},
    )
    ctl = GateController(str(state))
    ctl.register_gate(spec)
    _record(ctl, "sta", Status.PASS,
            reports=[_report(tmp_path, "sta.rpt")], spec=spec.checks["sta"])
    assert ctl.evaluate_gate("G", ["sta"]).passed

    _tamper_state(state, lambda d: d["submissions"].clear())
    gd = GateController(str(state)).evaluate_gate("G", ["sta"])
    assert not gd.passed
    assert "lacks a valid parsed engineering outcome" in gd.checks["sta"]["reason"]


def test_forged_tool_outcome_flip_from_pass_to_fail_blocks(tmp_path):
    """A tampered parsed outcome (PASS -> FAIL) is re-validated at evaluation."""
    state = tmp_path / "state.json"
    spec = GateSpec(
        "G", ("sta",),
        checks={"sta": CheckSpec("sta", "tool", engineering_validator=VALIDATOR)},
    )
    ctl = GateController(str(state))
    ctl.register_gate(spec)
    ev = _record(ctl, "sta", Status.PASS,
                 reports=[_report(tmp_path, "sta.rpt")], spec=spec.checks["sta"])
    assert ctl.evaluate_gate("G", ["sta"]).passed

    def forge(d):
        d["submissions"][ev.evidence_id]["engineering_outcome"] = {"verdict": "FAIL"}

    _tamper_state(state, forge)
    gd = GateController(str(state)).evaluate_gate("G", ["sta"])
    assert not gd.passed
    assert gd.checks["sta"]["status"] == Status.FAIL.value
    assert "lacks a valid parsed engineering outcome" in gd.checks["sta"]["reason"]


def test_forged_not_applicable_for_nonwaivable_check_from_disk_blocks(tmp_path):
    """A hand-written N/A record for formal_lec cannot PASS after deserialisation.

    This is the on-disk analogue of the existing in-memory UA defence-in-depth
    test: it proves the persisted status string round-trips through
    ``parse_status`` and is still converted to a hard-blocking FAIL.
    """
    state = tmp_path / "state.json"
    ctl = GateController(str(state))
    ctl.register_design_fact("has_macros", False, source=SIM_LABEL)
    reason = "NA-RULE:NO-MACROS design contains no hard macros (SIMULATED)"
    ev = Evidence(
        check_id="formal_lec", status=Status.NOT_APPLICABLE,
        reason=reason, applicability_reason=reason,
        command=["simulated_ws5_provider"], exit_code=0,
        analysis_view="MAPPED_NETLIST",
        tool_versions={"simulated_ws5_provider": "1.0"},
        reports=[str(_report(tmp_path, "lec.rpt"))],
    )
    # Write the forged record straight into the persisted ledger.
    ctl._state["evidence"][ev.evidence_id] = ev.to_dict()
    ctl._state["check_index"].setdefault("formal_lec", []).append(ev.evidence_id)
    ctl._save()

    gd = GateController(str(state)).evaluate_gate("g", ["formal_lec"])
    assert not gd.passed
    assert gd.checks["formal_lec"]["non_waivable"] is True
    assert gd.checks["formal_lec"]["status"] == Status.FAIL.value
    assert "non-waivable" in gd.reason


# ===========================================================================
# 3. Waivers must not launder invalid evidence; expiry boundary
# ===========================================================================
def test_waiver_cannot_launder_modified_artifact(tmp_path):
    """A valid waiver must never cover stale/invalid (revalidation-blocked) data."""
    state = tmp_path / "state.json"
    report = _report(tmp_path, "sta.rpt")
    ctl = GateController(str(state))
    _record(ctl, "sta", Status.PASS, reports=[report])
    ctl.add_waiver(Waiver(
        check_id="sta", scope="g",
        reason="SIMULATED reporting waiver with a sufficiently long reason",
        approved_by="ws5-robustness", policy="tool_gap",
    ))
    assert ctl.evaluate_gate("g", ["sta"]).passed  # clean baseline

    report.write_text("WS5 corruption after waiver was granted\n", encoding="utf-8")
    gd = ctl.evaluate_gate("g", ["sta"])
    assert not gd.passed
    assert gd.disposition == "BLOCKED"
    assert gd.checks["sta"].get("revalidation_problems")
    assert gd.waived_checks == []


def test_waiver_expiry_boundary_is_exact_and_fail_closed(tmp_path, monkeypatch):
    """At the exact expiry instant a waiver is expired (<=); 1us before it is live."""
    state = tmp_path / "state.json"
    ctl = GateController(str(state))
    _record(ctl, "antenna", Status.FAIL,
            reports=[_report(tmp_path, "drc.rpt")], exit_code=1)
    expiry_text = "2030-01-01T00:00:00Z"
    expiry_dt = _dt.datetime(2030, 1, 1, tzinfo=_dt.timezone.utc)
    ctl.add_waiver(Waiver(
        check_id="antenna", scope="g",
        reason="SIMULATED temporary risk acceptance with sufficient text",
        approved_by="ws5-robustness", policy="temporary_risk_acceptance",
        expires_utc=expiry_text,
    ))

    def freeze(now_utc):
        class _FrozenDateTime(_dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return now_utc
        monkeypatch.setattr(
            ctrl, "_dt",
            types.SimpleNamespace(datetime=_FrozenDateTime, timezone=_dt.timezone),
        )

    freeze(expiry_dt - _dt.timedelta(microseconds=1))
    just_before = ctl.evaluate_gate("g", ["antenna"])
    assert just_before.passed
    assert just_before.disposition == "WAIVED_PASS"

    freeze(expiry_dt)  # exact boundary: expiry <= now -> expired
    at_boundary = ctl.evaluate_gate("g", ["antenna"])
    assert not at_boundary.passed
    assert at_boundary.disposition == "BLOCKED"
    assert at_boundary.waived_checks == []

    # A malformed expiry injected into state is treated as expired, never active.
    _tamper_state(state, lambda d: d["waivers"][0].update(expires_utc="not-a-timestamp"))
    malformed = GateController(str(state)).evaluate_gate("g", ["antenna"])
    assert not malformed.passed


# ===========================================================================
# 4. Resume behaviour — modified stage script hash (acceptance policy model)
# ===========================================================================
def test_resume_with_modified_stage_script_hash_is_refused():
    """A prior PASS is not resumable when the stage script hash changed."""
    model = _gate_model()
    context = {
        "RTL": "rtl-h", "SDC": "sdc-h", "LIB": "lib-h", "PDK": "pdk-h",
        "DECK": "deck-h", "PDN": "pdn-h", "TOOLS": "tools-h", "CONFIG": "config-h",
    }
    # 11_extract is a stage with downstream consumers, so a changed script hash
    # both blocks its own resume and propagates to every consumer.
    prior = {
        "qualification": "QUALIFIED_PASS",
        "evidence_persisted": True,
        "context": dict(context),
        "script_hash": "script-11_extract",
    }

    ok, why = model["resume_allowed"](
        "11_extract", prior, context, "script-11_extract-WS5-TAMPERED", {}, set())
    assert not ok
    assert any("stage script changed" in w for w in why)

    ok_clean, why_clean = model["resume_allowed"](
        "11_extract", prior, context, "script-11_extract", {}, set())
    assert ok_clean, why_clean

    # Failure propagation: a stage that cannot resume on a changed script must
    # block every downstream consumer if it is forced to re-run and fails.
    blocked = set(model["blocked_by"]("11_extract"))
    assert {"12_sta", "13_gds", "15_lvs"} <= blocked


# ===========================================================================
# 5. ECO rollback — extra files
# ===========================================================================
def _eco_env(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    original = "module d; endmodule  // SIMULATED golden RTL\n"
    (ws / "design.v").write_text(original, encoding="utf-8")
    ctl = GateController(str(tmp_path / "state.json"))
    for fact in ("has_macros", "has_upf", "has_clock_gates", "has_analog"):
        ctl.register_design_fact(fact, False, source=SIM_LABEL)
    eco = EcoManager(
        str(ws), ctl,
        checkpoint_dir=str(tmp_path / "ckpt"),
        experiment_log=str(tmp_path / "experiments.json"),
    )
    return ws, ctl, eco, original


def _simulated_evidence(tmp_path, check_id, *, na_reason=None):
    report = _report(tmp_path, f"eco-{check_id}-{uuid.uuid4().hex[:8]}.rpt")
    return Evidence.from_run(
        check_id,
        Status.NOT_APPLICABLE if na_reason else Status.PASS,
        "SIMULATED ECO verdict",
        command=["simulated_ws5_provider", "--check", check_id],
        exit_code=0, analysis_view="MAPPED_NETLIST",
        tool_versions={"simulated_ws5_provider": "1.0"},
        reports=[str(report)],
        applicability_reason=na_reason or "",
    )


def test_eco_rollback_verification_detects_surviving_extra_file(tmp_path, monkeypatch):
    """Mutation: a faulty restore that leaves an extra file must not be a PASS.

    ``_rollback_and_verify`` re-hashes the whole workspace; an unexpected file
    is reported even when the (simulated) restore routine claims success.
    """
    ws, _ctl, eco, _original = _eco_env(tmp_path)
    extra = ws / "stray.v"

    def lazy_restore(_ckpt, _full):
        return ["design.v"]  # SIMULATED faulty restore: does not delete stray.v

    monkeypatch.setattr(eco, "_restore_from_manifest", lazy_restore)

    def apply():
        (ws / "design.v").write_text(
            "module d; assign broken = 1'bx; endmodule  // WS5 corruption\n",
            encoding="utf-8")
        extra.write_text("WS5 simulated stray file\n", encoding="utf-8")

    result = eco.run_experiment(change_scope=_scope("logic_optimization"), 
        experiment_id="ws5_extra_file", description="SIMULATED faulty-restore ECO",
        change_class="logic_optimization", changed_files=["design.v"],
        apply_change=apply,
        evaluate=lambda: {"wns": -1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0, direction="higher_is_better")],
        verify=lambda: [
            _simulated_evidence(tmp_path, "eco_lec"),
            _simulated_evidence(tmp_path, "eco_sta"),
            _simulated_evidence(
                tmp_path, "eco_physical_drc",
                na_reason=PHYSICAL_DRC_NA_REASON,
            ),
        ],
        simulated=SIM_LABEL,
    )
    assert result.status != "PASS"
    assert result.rollback_verified is False
    assert "FAILED verification" in result.reason
    assert "unlisted" in result.reason
    assert extra.exists()  # the guard caught the leftover; it was not cleaned here


def test_eco_rollback_removes_nested_extra_file_when_restore_succeeds(tmp_path):
    """Real restore removes a nested file created during an unlisted change."""
    ws, _ctl, eco, original = _eco_env(tmp_path)
    nested_file = ws / "deep" / "nested" / "stray.txt"

    def apply():
        (ws / "design.v").write_text(
            "module d; assign broken = 1'bx; endmodule  // WS5 corruption\n",
            encoding="utf-8")
        nested_file.parent.mkdir(parents=True, exist_ok=True)
        nested_file.write_text("WS5 simulated nested stray\n", encoding="utf-8")

    result = eco.run_experiment(change_scope=_scope("logic_optimization"), 
        experiment_id="ws5_nested", description="SIMULATED nested-extra-file ECO",
        change_class="logic_optimization", changed_files=["design.v"],
        apply_change=apply,
        evaluate=lambda: {"wns": 1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0, direction="higher_is_better")],
        verify=lambda: [
            _simulated_evidence(tmp_path, "eco_lec"),
            _simulated_evidence(tmp_path, "eco_sta"),
            _simulated_evidence(
                tmp_path, "eco_physical_drc",
                na_reason=PHYSICAL_DRC_NA_REASON,
            ),
        ],
        simulated=SIM_LABEL,
    )
    assert result.status != "PASS"          # unlisted file rejected
    assert "unlisted" in result.reason
    assert result.rollback_verified is True
    assert not nested_file.exists()
    assert sha256_file(ws / "design.v") == sha256_file(
        _write_expected(tmp_path, original))


def _write_expected(tmp_path, text):
    p = tmp_path / "expected-original.v"
    p.write_text(text, encoding="utf-8")
    return p


# ===========================================================================
# 6. CLI exit-code contract on corrupted ledger / corrupted artifact
# ===========================================================================
def test_cli_gate_evaluate_on_malformed_state_exits_usage_without_pass(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{ this is not valid JSON", encoding="utf-8")
    proc = _run_cli("gate-evaluate", "--state", state, "--gate", "g",
                    "--required", "sta")
    assert proc.returncode == 2, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data.get("error") == "InvalidInput"
    assert "decision" not in data
    assert data.get("disposition") != "CLEAN_PASS"


def test_cli_gate_evaluate_on_corrupted_artifact_exits_one_with_json(tmp_path):
    state = tmp_path / "state.json"
    report = _report(tmp_path, "sta.rpt")
    ctl = GateController(str(state))
    _record(ctl, "sta", Status.PASS, reports=[report])
    assert ctl.evaluate_gate("g", ["sta"]).passed

    report.write_text("WS5 adversarial post-record corruption\n", encoding="utf-8")
    proc = _run_cli("gate-evaluate", "--state", state, "--gate", "g",
                    "--required", "sta")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data.get("decision") == "BLOCKED"
    assert data.get("disposition") == "BLOCKED"
    problems = json.dumps(data["checks"]["sta"].get("revalidation_problems", []))
    assert "artifact modified" in problems


# ===========================================================================
# 7. Failure propagation through the untested can_enter_stage public API
# ===========================================================================
def test_can_enter_stage_propagates_upstream_block_and_recovers(tmp_path):
    """A blocked upstream gate prevents downstream stage entry; recovery works."""
    state = tmp_path / "state.json"
    ctl = GateController(str(state))
    upstream_report = _report(tmp_path, "upstream.rpt")
    _record(ctl, "sta", Status.FAIL, reports=[upstream_report], exit_code=1)
    _pass(ctl, "lea", tmp_path)
    ctl.evaluate_gate("UP", ["sta"])

    ok, reasons = ctl.can_enter_stage(
        "downstream_stage", [("DOWN", ["lea"])], upstream=("UP",))
    assert not ok
    assert any("UP" in r for r in reasons)

    # Fresh upstream PASS evidence rehabilitates the downstream stage.
    _pass(ctl, "sta", tmp_path)
    ctl.evaluate_gate("UP", ["sta"])
    ok2, reasons2 = ctl.can_enter_stage(
        "downstream_stage", [("DOWN", ["lea"])], upstream=("UP",))
    assert ok2, reasons2
    assert reasons2 == []
