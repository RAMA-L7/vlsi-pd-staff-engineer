"""CLI `gate-record` provenance binding and completeness enforcement.

These tests drive the real CLI via ``python -m assistant`` in a subprocess, so
they exercise the exact argument parsing, validation-before-write behaviour,
and state JSON that automation sees. All state lives in ``tmp_path`` only.
"""
import json
import subprocess
import sys
from pathlib import Path

from assistant.controller import GateController
from assistant.hashing import sha256_file

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "assistant", *[str(a) for a in args]],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )


def record_cmd(state, check, status, *extras, reason="meets target"):
    """Build a `gate-record` argv; *extras* are raw CLI tokens (repeatable)."""
    return [
        "gate-record",
        "--state", str(state),
        "--check", check,
        "--status", status,
        "--reason", reason,
        *[str(x) for x in extras],
    ]


# ------------------------------------------------------------------- negatives
def test_cli_substantive_requires_report_log_or_artifact(tmp_path):
    state = tmp_path / "state.json"
    proc = run_cli(*record_cmd(
        state, "sta", "PASS",
        "--command", "sta -exit", "--exit-code", "0",
        "--view", "ROUTED_EXTRACTED", "--tool-version", "sta=3.1",
    ))
    assert proc.returncode != 0
    assert not state.exists(), "no state file may be written on validation failure"
    blob = (proc.stdout + proc.stderr).lower()
    assert "report" in blob and "error" in blob


def test_cli_substantive_requires_tool_version(tmp_path):
    rpt = tmp_path / "sta.rpt"
    rpt.write_text("WNS: 0.5", encoding="utf-8")
    state = tmp_path / "state.json"
    proc = run_cli(*record_cmd(
        state, "sta", "PASS",
        "--command", "sta -exit", "--exit-code", "0",
        "--view", "ROUTED_EXTRACTED", "--report", str(rpt),
    ))
    assert proc.returncode != 0
    assert not state.exists()
    assert "tool version" in (proc.stdout + proc.stderr).lower()


def test_cli_nonexistent_report_path_fails(tmp_path):
    state = tmp_path / "state.json"
    proc = run_cli(*record_cmd(
        state, "sta", "PASS",
        "--command", "sta -exit", "--exit-code", "0",
        "--view", "ROUTED_EXTRACTED",
        "--report", str(tmp_path / "does_not_exist.rpt"),
        "--tool-version", "sta=3.1",
    ))
    assert proc.returncode != 0
    assert not state.exists()
    assert "error" in (proc.stdout + proc.stderr).lower()


def test_cli_invalid_na_reason_empty_fails(tmp_path):
    state = tmp_path / "state.json"
    proc = run_cli(*record_cmd(
        state, "upf_check", "NOT_APPLICABLE",
        "--na-reason", "",
        reason="not needed",
    ))
    assert proc.returncode != 0
    assert not state.exists()
    assert "applicability" in (proc.stdout + proc.stderr).lower()


def test_cli_invalid_na_reason_unknown_rule_fails(tmp_path):
    state = tmp_path / "state.json"
    proc = run_cli(*record_cmd(
        state, "upf_check", "NOT_APPLICABLE",
        "--na-reason", "NA-RULE:BOGUS this rule is not in the registry at all",
        reason="not needed",
    ))
    assert proc.returncode != 0
    assert not state.exists()


def test_cli_na_valid_reason_succeeds_with_validated_design_fact(tmp_path, monkeypatch):
    # R6: cross-process evidence verification requires the external trust root.
    monkeypatch.setenv("ASSISTANT_LEDGER_KEY", "r6-cli-cross-process-key")
    state = tmp_path / "state.json"
    proc = run_cli(*record_cmd(
        state, "upf_check", "NOT_APPLICABLE",
        "--na-reason", "NA-RULE:NO-UPF design has no UPF/CPF power intent file in scope",
        "--design-fact", "has_upf=False",
        reason="not needed",
    ))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(state.read_text(encoding="utf-8"))
    ev = next(iter(data["evidence"].values()))
    assert ev["status"] == "NOT_APPLICABLE"
    assert ev["applicability_reason"].startswith("NA-RULE:NO-UPF")
    assert data["design_facts"]["has_upf"]["value"] is False

    # R6.3: add a proof-bearing PASS so the N/A gate can authorize production;
    # the N/A disposition assertions below are unchanged.
    import r63_cli_proof_helper as _ph
    pf = str(tmp_path / "proof.json")
    _ph.write_proof_file(pf, check_id='na_proof')
    rpt = tmp_path / "na_proof.rpt"
    rpt.write_text("WNS: 0.5", encoding="utf-8")
    rec2 = run_cli(*record_cmd(
        state, "na_proof", "PASS", "--command", "sta -exit", "--exit-code", "0",
        "--view", "ROUTED_EXTRACTED", "--report", str(rpt),
        "--tool-version", "sta=3.1", "--proof-file", pf,
    ))
    assert rec2.returncode == 0, rec2.stdout + rec2.stderr
    # A validated N/A is non-blocking but is NOT a clean PASS.
    ev2 = run_cli("gate-evaluate", "--state", state, "--gate", "lp_gate",
                  "--required", "upf_check,na_proof")
    assert ev2.returncode == 0, ev2.stdout + ev2.stderr
    result = json.loads(ev2.stdout)
    assert result["decision"] == "PASS"
    assert result["disposition"] == "NOT_APPLICABLE"


# ------------------------------------------------------------------- positives
def test_cli_full_provenance_stored_and_gate_passes(tmp_path):
    rpt = tmp_path / "sta.rpt"
    log = tmp_path / "sta.log"
    art = tmp_path / "routed.spef"
    cfg = tmp_path / "design.yml"
    con = tmp_path / "design.sdc"
    lib = tmp_path / "tt.lib"
    pdk = tmp_path / "pdk.txt"
    inp = tmp_path / "d.v"
    for path, text in (
        (rpt, "WNS: 0.5\n"), (log, "run ok\n"), (art, "SPEF\n"),
        (cfg, "cfg\n"), (con, "create_clock\n"), (lib, "liberty\n"),
        (pdk, "pdk\n"), (inp, "module d; endmodule\n"),
    ):
        path.write_text(text, encoding="utf-8")

    state = tmp_path / "state.json"
    proc = run_cli(*record_cmd(
        state, "sta", "PASS",
        "--command", "sta -exit", "--exit-code", "0",
        "--view", "ROUTED_EXTRACTED",
        "--report", str(rpt), "--log", str(log), "--artifact", str(art),
        "--tool-version", "sta=3.1", "--tool-version", "opensta=2.6",
        "--input-hash", f"rtl={inp}", "--config-hash", f"cfg={cfg}",
        "--constraint-hash", f"sdc={con}", "--library-hash", f"tt={lib}",
        "--pdk-hash", f"sky130={pdk}",
    ))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    data = json.loads(state.read_text(encoding="utf-8"))
    records = list(data["evidence"].values())
    assert len(records) == 1
    ev = records[0]
    assert ev["tool_versions"] == {"sta": "3.1", "opensta": "2.6"}
    assert ev["reports"][0]["path"] == str(rpt)
    assert ev["reports"][0]["sha256"] == sha256_file(str(rpt))
    assert ev["logs"][0]["sha256"] == sha256_file(str(log))
    assert ev["artifacts"][0]["sha256"] == sha256_file(str(art))
    assert ev["input_hashes"]["rtl"] == sha256_file(str(inp))
    assert ev["config_hashes"]["cfg"] == sha256_file(str(cfg))
    assert ev["constraint_hashes"]["sdc"] == sha256_file(str(con))
    assert ev["library_hashes"]["tt"] == sha256_file(str(lib))
    assert ev["pdk_hashes"]["sky130"] == sha256_file(str(pdk))

    # Register the same inputs in-memory so the recorded input hashes are
    # anchors that match current values; then the gate evaluates cleanly.
    # (Input registration is intentionally in-memory, not persisted in state.)
    ctl = GateController(state)
    ctl.register_inputs({"rtl": str(inp)})
    gd = ctl.evaluate_gate("sta_gate", ["sta"])
    assert gd.passed, gd.reason
    assert gd.checks["sta"]["status"] == "PASS"


def test_cli_substantive_record_evaluates_pass_via_cli(tmp_path, monkeypatch):
    """No input-hash anchors -> pure CLI gate-evaluate also reaches PASS."""
    # R6: cross-process evidence verification requires the external trust root.
    monkeypatch.setenv("ASSISTANT_LEDGER_KEY", "r6-cli-cross-process-key")
    rpt = tmp_path / "sta.rpt"
    rpt.write_text("WNS: 0.5", encoding="utf-8")
    state = tmp_path / "state.json"
    import r63_cli_proof_helper as _ph
    pf = str(tmp_path / "proof.json")
    _ph.write_proof_file(pf, check_id='sta')
    rec = run_cli(*record_cmd(
        state, "sta", "PASS",
        "--command", "sta -exit", "--exit-code", "0",
        "--view", "ROUTED_EXTRACTED",
        "--report", str(rpt), "--tool-version", "sta=3.1",
        "--proof-file", pf,
    ))
    assert rec.returncode == 0, rec.stdout + rec.stderr
    ev = next(iter(json.loads(state.read_text(encoding="utf-8"))["evidence"].values()))
    assert ev["reports"][0]["sha256"] == sha256_file(str(rpt))

    evaluated = run_cli("gate-evaluate", "--state", state, "--gate", "sta_gate",
                        "--required", "sta")
    assert evaluated.returncode == 0, evaluated.stdout + evaluated.stderr
    assert json.loads(evaluated.stdout)["decision"] == "PASS"
