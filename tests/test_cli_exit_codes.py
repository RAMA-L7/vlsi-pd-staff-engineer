"""SR-06: subprocess-level CLI exit-code contract.

Every test invokes ``python -m assistant <cmd>`` in a real child process and
asserts the *actual* process exit code, not a return value from ``main()``.
This is the only way to catch a ``sys.exit(0)`` that hides an engineering
failure from shell automation.  All inputs live in ``tmp_path``.

Contract (documented in assistant/cli.py):

======  =====================================================================
  code  meaning
======  =====================================================================
    0   validation succeeded under declared policy / inspection command ran
    1   engineering FAIL / BLOCKED / INCONCLUSIVE / UNSUPPORTED / NOT_RUN
    2   invalid input (missing/malformed file, bad provenance, bad definition)
    3   prohibited force-pass / bypass refused
    4   prohibited bypass unexpectedly succeeded
  124   validation tool timed out
  125   validation tool could not be launched
======  =====================================================================
"""
import json
import subprocess
import sys
from pathlib import Path

from assistant.cli import (
    EXIT_BYPASS_REFUSED,
    EXIT_BYPASS_SUCCEEDED,
    EXIT_OK,
    EXIT_TIMEOUT,
    EXIT_TOOL_LAUNCH,
    EXIT_USAGE,
    EXIT_VALIDATION_FAILED,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args):
    """Real process: python -m assistant <args>."""
    return subprocess.run(
        [sys.executable, "-m", "assistant", *[str(a) for a in args]],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
    )


def stdout_json(proc):
    """Structured JSON on stdout must survive every exit code."""
    assert proc.stdout.strip(), "expected structured JSON on stdout: " + proc.stderr
    return json.loads(proc.stdout)


def write_sta_report(tmp_path, wns, name="sta.rpt"):
    """A clocked STA report with a parseable worst-slack number."""
    path = tmp_path / name
    path.write_text(
        f"create_clock clk\nworst_slack: {wns}\nslack (MET) {wns}\n",
        encoding="utf-8",
    )
    return path


LIB_NAME = "sky130_fd_sc_hd__tt_025C_1v80.lib"


def write_lib(tmp_path, voltage=1.8, temp=25.0):
    path = tmp_path / LIB_NAME
    path.write_text(
        'library(x) {\n  operating_conditions ("tt_025C_1v80") {\n'
        f"    nom_voltage : {voltage:.6f};\n"
        f"    nom_temperature : {temp:.6f};\n"
        "  }\n}\n",
        encoding="utf-8",
    )
    return path


def write_targets(tmp_path, corners, name="targets.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"corners": corners}), encoding="utf-8")
    return path


def record_sta_pass(tmp_path, state, report):
    """Record a fully-provenanced PASS so gate-evaluate can reach PASS."""
    import r63_cli_proof_helper as _ph
    pf = str(tmp_path / "proof.json")
    _ph.write_proof_file(pf)
    return run_cli(
        "gate-record", "--state", state, "--check", "sta", "--status", "PASS",
        "--reason", "meets target", "--command", "sta -exit", "--exit-code", "0",
        "--view", "ROUTED_EXTRACTED", "--report", report,
        "--tool-version", "sta=3.1", "--proof-file", pf,
    )


# ------------------------------------------------------------------ constants
def test_exit_contract_constants_are_stable():
    assert EXIT_OK == 0
    assert EXIT_VALIDATION_FAILED == 1
    assert EXIT_USAGE == 2
    assert EXIT_BYPASS_REFUSED == 3
    assert EXIT_BYPASS_SUCCEEDED == 4
    assert EXIT_TIMEOUT == 124
    assert EXIT_TOOL_LAUNCH == 125


# ------------------------------------------------------------------------ STA
def test_sta_pass_exits_zero(tmp_path):
    report = write_sta_report(tmp_path, "0.50")
    proc = run_cli("sta", "--report", report, "--target-wns", "0.0")
    assert proc.returncode == EXIT_OK, proc.stdout + proc.stderr
    data = stdout_json(proc)
    assert data["status"] == "PASS"
    assert data["meets_targets"] is True


def test_sta_fail_exits_nonzero_with_json_preserved(tmp_path):
    report = write_sta_report(tmp_path, "-2.50")
    proc = run_cli("sta", "--report", report, "--target-wns", "0.0")
    assert proc.returncode == EXIT_VALIDATION_FAILED
    data = stdout_json(proc)
    assert data["status"] == "FAIL"
    assert data["meets_targets"] is False
    assert "reasons" in data


def test_sta_malformed_report_exits_nonzero(tmp_path):
    report = tmp_path / "empty.rpt"
    report.write_text("", encoding="utf-8")
    proc = run_cli("sta", "--report", report)
    assert proc.returncode == EXIT_VALIDATION_FAILED
    assert stdout_json(proc)["status"] == "FAIL"


# ------------------------------------------------------------------------ PVT
def test_pvt_pass_exits_zero(tmp_path):
    write_lib(tmp_path)
    targets = write_targets(tmp_path, {"func_tt": {
        "lib": LIB_NAME, "pvt": "TT / 1.80V / 25C", "available": True,
    }})
    proc = run_cli("validate-pvt", "--targets", targets, "--lib-dir", tmp_path)
    assert proc.returncode == EXIT_OK, proc.stdout + proc.stderr
    assert stdout_json(proc)["overall"] == "PASS"


def test_pvt_fail_exits_nonzero_with_json_preserved(tmp_path):
    write_lib(tmp_path)                       # real header: 1.80V / 25C
    targets = write_targets(tmp_path, {"func_tt": {
        "lib": LIB_NAME, "pvt": "TT / 1.00V / 25C", "available": True,
    }})
    proc = run_cli("validate-pvt", "--targets", targets, "--lib-dir", tmp_path)
    assert proc.returncode == EXIT_VALIDATION_FAILED
    data = stdout_json(proc)
    assert data["overall"] == "FAIL"
    assert data["corners"][0]["status"] == "FAIL"


def test_pvt_not_run_exits_nonzero(tmp_path):
    targets = write_targets(tmp_path, {"func_ss": {
        "lib": "no_such_ss.lib", "pvt": "SS / 1.40V / -40C", "available": True,
    }})
    proc = run_cli("validate-pvt", "--targets", targets, "--lib-dir", tmp_path)
    assert proc.returncode == EXIT_VALIDATION_FAILED
    data = stdout_json(proc)
    assert data["overall"] == "NOT_RUN"
    assert data["corners"][0]["status"] == "NOT_RUN"


# ----------------------------------------------------------------------- gate
def test_blocked_gate_exits_nonzero_with_json_preserved(tmp_path):
    state = tmp_path / "state.json"
    proc = run_cli("gate-evaluate", "--state", state, "--gate", "sta_gate",
                   "--required", "sta")
    assert proc.returncode == EXIT_VALIDATION_FAILED
    data = stdout_json(proc)
    assert data["decision"] == "BLOCKED"
    assert data["disposition"] == "BLOCKED"
    assert data["checks"]["sta"]["status"] == "NOT_RUN"


def test_passing_gate_exits_zero(tmp_path, monkeypatch):
    # R6: cross-process evidence verification requires the external trust root.
    monkeypatch.setenv("ASSISTANT_LEDGER_KEY", "r6-cli-cross-process-key")
    report = write_sta_report(tmp_path, "0.50")
    state = tmp_path / "state.json"
    rec = record_sta_pass(tmp_path, state, report)
    assert rec.returncode == EXIT_OK, rec.stdout + rec.stderr

    proc = run_cli("gate-evaluate", "--state", state, "--gate", "sta_gate",
                   "--required", "sta")
    assert proc.returncode == EXIT_OK, proc.stdout + proc.stderr
    data = stdout_json(proc)
    assert data["decision"] == "PASS"
    assert data["disposition"] == "CLEAN_PASS"


def test_empty_required_check_list_exits_nonzero(tmp_path):
    """An empty gate definition is invalid input, not a vacuous PASS."""
    state = tmp_path / "state.json"
    proc = run_cli("gate-evaluate", "--state", state, "--gate", "empty_gate",
                   "--required", "")
    assert proc.returncode == EXIT_USAGE
    assert stdout_json(proc)["error"] == "GateDefinitionError"


def test_cancelled_run_gate_exits_nonzero(tmp_path):
    state = tmp_path / "state.json"
    cancelled = run_cli("gate-cancel", "--state", state, "--reason", "halt")
    assert cancelled.returncode == EXIT_OK, cancelled.stdout + cancelled.stderr
    proc = run_cli("gate-evaluate", "--state", state, "--gate", "g",
                   "--required", "sta")
    assert proc.returncode == EXIT_USAGE
    assert stdout_json(proc)["error"] == "StateError"


# -------------------------------------------------------------- invalid input
def test_missing_report_file_exits_nonzero(tmp_path):
    proc = run_cli("sta", "--report", tmp_path / "does_not_exist.rpt")
    assert proc.returncode == EXIT_USAGE
    assert stdout_json(proc)["error"] == "FileNotFoundError"


def test_missing_targets_file_exits_nonzero(tmp_path):
    proc = run_cli("validate-pvt", "--targets", tmp_path / "does_not_exist.json")
    assert proc.returncode == EXIT_USAGE
    assert stdout_json(proc)["error"] == "FileNotFoundError"


def test_malformed_targets_file_exits_nonzero(tmp_path):
    targets = tmp_path / "bad.json"
    targets.write_text("{not json", encoding="utf-8")
    proc = run_cli("validate-pvt", "--targets", targets)
    assert proc.returncode == EXIT_USAGE
    assert stdout_json(proc)["error"] == "InvalidInput"


# ------------------------------------------------------------ force-pass / bypass
def test_force_pass_refusal_exits_nonzero(tmp_path):
    state = tmp_path / "state.json"
    for mode in ("force", "summary"):
        proc = run_cli("gate-bypass-attempt", "--state", state,
                       "--gate", "sta_gate", "--mode", mode)
        assert proc.returncode == EXIT_BYPASS_REFUSED, proc.stdout + proc.stderr
        data = stdout_json(proc)
        assert data["bypass"] == "REFUSED"
        assert data["error"]


# ------------------------------------------- inspection commands are distinct
def test_inspection_commands_exit_zero_even_on_failing_data(tmp_path):
    # A FAIL STA verdict is still a successful *diagnosis* (inspection).
    report = write_sta_report(tmp_path, "-2.50")
    diag = run_cli("diagnose", "--report", report)
    assert diag.returncode == EXIT_OK, diag.stdout + diag.stderr
    assert "disclaimer" in stdout_json(diag)

    # Inspecting a BLOCKED state is not itself a validation failure.
    state = tmp_path / "state.json"
    blocked = run_cli("gate-evaluate", "--state", state, "--gate", "sta_gate",
                      "--required", "sta")
    assert blocked.returncode == EXIT_VALIDATION_FAILED
    rep = run_cli("gate-report", "--state", state)
    assert rep.returncode == EXIT_OK, rep.stdout + rep.stderr
    assert stdout_json(rep)["last_gate_disposition"] == "BLOCKED"

    info = run_cli("info")
    assert info.returncode == EXIT_OK
    assert stdout_json(info)["llm_can_authorize_gates"] is False


# ------------------------------------------------------------------ baseline
def test_baseline_subcommand_forwards_design_and_propagates_exit(monkeypatch):
    """The `baseline` subparser accepts --design and passes it through.

    The baseline's own signal (timeout/spawn/incomplete) must still decide the
    shell exit code, so fake main() returns a sentinel and we assert it is
    propagated verbatim (SR-06 contract preserved).
    """
    import assistant.baseline as baseline_mod
    from assistant.cli import main as cli_main

    def option_value(argv, flag):
        return argv[argv.index(flag) + 1]

    captured = []

    def fake_main(argv):
        captured.append(list(argv))
        return 7

    monkeypatch.setattr(baseline_mod, "main", fake_main)

    assert cli_main(["baseline", "--design", "soc_ctrl", "--dry-run"]) == 7
    assert option_value(captured[-1], "--design") == "soc_ctrl"
    assert "--dry-run" in captured[-1]

    # Default matches assistant.baseline.main's own default.
    assert cli_main(["baseline"]) == 7
    assert option_value(captured[-1], "--design") == "test_counter"


# ------------------------------------------------------------------ preflight
def test_preflight_real_checker_succeeds_when_mandatory_present(tmp_path):
    """The real stage-00 checker needs no EDA tools for a clean PASS."""
    proc = run_cli("preflight", "--out", tmp_path / "preflight_out")
    assert proc.returncode == EXIT_OK, proc.stdout + proc.stderr
    data = stdout_json(proc)
    assert data["checker_exit_code"] == 0
    assert data["overall"] == "PASS"
    assert data["missing_mandatory"] == []
    assert data["checks"]


def test_preflight_simulated_missing_dependency_exits_usage(tmp_path):
    """A fake checker reports an exact missing mandatory dependency (no EDA)."""
    fake = tmp_path / "fake_stage00.py"
    fake.write_text(
        "import json, os, sys\n"
        "out = sys.argv[sys.argv.index('--out-dir') + 1]\n"
        "os.makedirs(out, exist_ok=True)\n"
        "report = {\n"
        "  'schema_version': 'simulated',\n"
        "  'base_dir': sys.argv[1],\n"
        "  'out_dir': out,\n"
        "  'overall': 'FAIL',\n"
        "  'mandatory_failures': 1,\n"
        "  'dependencies': {\n"
        "    'mandatory': [{'name': 'git', 'path': '/sim/git', 'ok': True}],\n"
        "    'optional': [],\n"
        "    'missing_mandatory': ['simulated_missing_tool'],\n"
        "    'missing_optional': [],\n"
        "    'raised_to_mandatory': [],\n"
        "  },\n"
        "  'checks': [{'name': 'tool_simulated_missing_tool_available',\n"
        "              'status': 'FAIL', 'mandatory': True,\n"
        "              'detail': 'NOT FOUND - simulated dependency missing'}],\n"
        "}\n"
        "with open(os.path.join(out, 'stage00_result.json'), 'w') as f:\n"
        "    json.dump(report, f)\n"
        "print('Stage-00 Pre-flight: FAIL')\n"
        "print('Missing mandatory dependencies: simulated_missing_tool')\n"
        "sys.exit(1)\n",
        encoding="utf-8",
    )
    proc = run_cli("preflight", "--checker", fake,
                   "--out", tmp_path / "preflight_out")
    assert proc.returncode == EXIT_USAGE, proc.stdout + proc.stderr
    data = stdout_json(proc)
    assert data["checker_exit_code"] == 1
    assert data["overall"] == "FAIL"
    assert data["missing_mandatory"] == ["simulated_missing_tool"]
    assert data["dependencies"]["missing_mandatory"] == ["simulated_missing_tool"]
    assert "simulated_missing_tool" in data["checker_stdout"]


def test_preflight_missing_config_exits_nonzero(tmp_path):
    """A missing config is invalid input (2), with structured JSON preserved."""
    proc = run_cli("preflight", "--config", tmp_path / "no_such.json",
                   "--out", tmp_path / "preflight_out")
    assert proc.returncode == EXIT_USAGE
    data = stdout_json(proc)
    assert data["error"] == "PreflightResultMissing"
    assert "no_such.json" in data["detail"]


# --------------------------------------------- U-A: applicability / N/A rules
NA_NO_UPF = "NA-RULE:NO-UPF design has no UPF/CPF power intent file in scope"


def _na_record(state, check, *extras, reason=NA_NO_UPF):
    return run_cli(
        "gate-record", "--state", state, "--check", check,
        "--status", "NOT_APPLICABLE", "--reason", "not needed",
        "--na-reason", reason, *[str(x) for x in extras],
    )


def test_cli_na_without_design_facts_rejected(tmp_path):
    """Reason-only N/A is refused and never creates a state file."""
    state = tmp_path / "na_no_facts.json"
    proc = _na_record(state, "upf_check")
    assert proc.returncode != 0
    assert not state.exists()
    assert stdout_json(proc)["error"] == "ApplicabilityError"


def test_cli_na_invalid_rule_rejected(tmp_path):
    state = tmp_path / "na_bad_rule.json"
    proc = _na_record(
        state, "upf_check", "--design-fact", "has_upf=False",
        reason="NA-RULE:BOGUS this rule is not registered at all",
    )
    assert proc.returncode != 0
    assert not state.exists()
    assert stdout_json(proc)["error"] == "ApplicabilityError"


def test_cli_na_non_waivable_formal_lec_rejected(tmp_path):
    """Functional-proof checks can never be N/A, even with matching facts."""
    state = tmp_path / "na_lec.json"
    proc = _na_record(state, "formal_lec", "--design-fact", "has_upf=False")
    assert proc.returncode != 0
    assert not state.exists()
    assert stdout_json(proc)["error"] == "ApplicabilityError"


def test_cli_na_registered_non_waivable_spec_rejected(tmp_path):
    """A registered spec with waivable=False also blocks N/A recording."""
    state = tmp_path / "spec_state.json"
    seed = (
        "from assistant.controller import CheckSpec, GateController\n"
        f"ctl = GateController({str(state)!r})\n"
        "ctl.register_check_spec(CheckSpec(\n"
        "    check_id='custom_functional_proof', execution_kind='internal',\n"
        "    validator_type='proof', evidence_contract='verified proof',\n"
        "    waivable=False))\n"
    )
    seeded = subprocess.run(
        [sys.executable, "-c", seed],
        cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=120,
    )
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr

    proc = _na_record(
        state, "custom_functional_proof", "--design-fact", "has_upf=False"
    )
    assert proc.returncode != 0
    assert stdout_json(proc)["error"] == "NonWaivableCheck"
    saved = json.loads(state.read_text(encoding="utf-8"))
    assert saved["evidence"] == {}


def test_cli_na_valid_fact_records_not_applicable_disposition(tmp_path, monkeypatch):
    # R6: cross-process evidence verification requires the external trust root.
    monkeypatch.setenv("ASSISTANT_LEDGER_KEY", "r6-cli-cross-process-key")
    state = tmp_path / "na_valid.json"
    proc = _na_record(state, "upf_check", "--design-fact", "has_upf=False")
    assert proc.returncode == EXIT_OK, proc.stdout + proc.stderr
    saved = json.loads(state.read_text(encoding="utf-8"))
    ev = next(iter(saved["evidence"].values()))
    assert ev["status"] == "NOT_APPLICABLE"
    assert saved["design_facts"]["has_upf"]["value"] is False

    # R6.3: add a proof-bearing PASS so the N/A gate can authorize production;
    # the N/A disposition assertions below are unchanged.
    import r63_cli_proof_helper as _ph
    pf = str(tmp_path / "proof.json")
    _ph.write_proof_file(pf)
    rpt = tmp_path / "na_proof.rpt"
    rpt.write_text("WNS: 0.5", encoding="utf-8")
    rec2 = run_cli("gate-record", "--state", state, "--check", "na_proof",
                   "--status", "PASS", "--reason", "meets target",
                   "--command", "sta -exit", "--exit-code", "0",
                   "--view", "ROUTED_EXTRACTED", "--report", str(rpt),
                   "--tool-version", "sta=3.1", "--proof-file", pf)
    assert rec2.returncode == EXIT_OK, rec2.stdout + rec2.stderr
    gate = run_cli("gate-evaluate", "--state", state, "--gate", "na_gate",
                   "--required", "upf_check,na_proof")
    assert gate.returncode == EXIT_OK, gate.stdout + gate.stderr
    data = stdout_json(gate)
    assert data["decision"] == "PASS"
    assert data["disposition"] == "NOT_APPLICABLE"
    assert data["disposition"] != "CLEAN_PASS"


def test_cli_substantive_pass_with_provenance_still_succeeds(tmp_path, monkeypatch):
    """Strict-by-default recording keeps real tool PASS evidence working."""
    # R6: cross-process evidence verification requires the external trust root.
    monkeypatch.setenv("ASSISTANT_LEDGER_KEY", "r6-cli-cross-process-key")
    report = write_sta_report(tmp_path, "0.50")
    state = tmp_path / "pass_state.json"
    rec = record_sta_pass(tmp_path, state, report)
    assert rec.returncode == EXIT_OK, rec.stdout + rec.stderr
    gate = run_cli("gate-evaluate", "--state", state, "--gate", "sta_gate",
                   "--required", "sta")
    assert gate.returncode == EXIT_OK, gate.stdout + gate.stderr
    assert stdout_json(gate)["decision"] == "PASS"
