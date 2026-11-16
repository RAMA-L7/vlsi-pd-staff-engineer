"""Failure-injection / benchmark tests (mission item 7).

Each test injects one deliberate fault and asserts the assistant detects it
with the right status - the detection contract for the benchmark suite.
Covered faults:
  - functional netlist corruption (LEC must fail)
  - missing timing constraints (STA must fail)
  - stale evidence (gate must block)
  - missing libraries (PVT check NOT_RUN; gate blocked)
  - malformed reports (STA FAIL)
  - an ECO that worsens a protected metric (roll back)
"""
import os
import sys

import pytest

from assistant.controller import GateController
from assistant.diagnosis import sta_verdict
from assistant.eco import EcoManager, ProtectedMetric
from assistant.evidence import Evidence
from assistant.liberty import PVTClaim, validate_pvt_claim
from assistant.statuses import Status

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RTL = os.path.join(REPO, "examples", "test-counter", "rtl", "test_counter.v")

has_yosys = any(
    os.path.isfile(os.path.join(d, "yosys.exe"))
    for d in (r"D:\tools\oss-cad-suite\bin",)
)


# 1. functional netlist corruption ------------------------------------------
@pytest.mark.skipif(not has_yosys or os.name != "nt", reason="yosys not available")
def test_injected_netlist_corruption_detected_by_lec(tmp_path):
    """Corrupt the RTL -> LEC against the golden netlist must NOT prove."""
    import subprocess

    # step 1: synthesize the golden netlist
    # NOTE: shell=True is required - the command has nested quotes that
    # list2cmdline would break for cmd.exe /c (verified empirically).
    synth = tmp_path / "golden.v"
    cmd_syn = (
        'call "D:\\tools\\oss-cad-suite\\environment.bat" >nul 2>&1 && '
        f'yosys -p "read_verilog {RTL}; synth -top test_counter; '
        f'write_verilog -noattr {synth}"'
    )
    proc = subprocess.run(
        cmd_syn,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300, shell=True,
    )
    if proc.returncode != 0 or not synth.is_file():
        pytest.skip("yosys synthesis failed in this environment")
    golden = synth.read_text(encoding="utf-8", errors="replace")

    # step 2: corrupt the netlist FUNCTIONALLY (flip a real Boolean operator;
    # corrupting a comment or a name would rightly still prove equivalent)
    corrupted = golden.replace(" & ", " | ", 1)
    if corrupted == golden:
        corrupted = golden.replace(" ^ ", " & ", 1)
    if corrupted == golden:
        pytest.skip("could not locate an operator to corrupt in netlist")
    bad = tmp_path / "bad.v"
    bad.write_text(corrupted, encoding="utf-8")

    # step 3: LEC script (gold = RTL, gate = corrupted netlist).
    # Same proven recipe as test_framework.sh: stash/copy-from + 3-arg
    # equiv_make + flatten on the gate side so SAT sees cell functions.
    lec = tmp_path / "lec.ys"
    lec.write_text(
        f"read_verilog {RTL}\nprep -top test_counter\nasync2sync\n"
        "design -stash gold\n"
        "design -reset\n"
        f"read_verilog {bad}\nprep -top test_counter\n"
        "flatten test_counter\nopt_clean -purge\nasync2sync\n"
        "design -stash gate\n"
        "design -copy-from gold -as gold test_counter\n"
        "design -copy-from gate -as gate test_counter\n"
        "equiv_make gold gate equiv\n"
        "hierarchy -top equiv\n"
        "equiv_simple\nequiv_induct\n"
        "equiv_status -assert\n",
        encoding="utf-8",
    )
    cmd_lec = (
        'call "D:\\tools\\oss-cad-suite\\environment.bat" >nul 2>&1 && '
        f'yosys -s "{lec}"'
    )
    proc = subprocess.run(
        cmd_lec,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600, shell=True,
    )
    out = proc.stdout.decode("utf-8", "replace")
    # detection contract: non-zero exit AND explicit unproven/error text
    assert proc.returncode != 0, "corrupted netlist must not pass LEC:\n" + out[-2000:]
    assert (
        "unproven" in out.lower()
        or "error" in out.lower()
        or "equivalence successfully proven" not in out.lower()
    )

    # record as evidence and confirm the gate blocks
    ctl = GateController(str(tmp_path / "state.json"))
    ctl.record_evidence(
        Evidence.from_run(
            check_id="lec", status=Status.FAIL,
            reason="injected corruption: equivalence not proven",
            exit_code=proc.returncode, analysis_view="MAPPED_NETLIST",
        )
    )
    gd = ctl.evaluate_gate("lec_gate", ["lec"])
    assert not gd.passed


# 2. missing timing constraints ---------------------------------------------
def test_missing_constraints_detected():
    # a real missing-constraint report never contains a create_clock marker
    report = "STA ran on design\nconstraint file not applied\nworst_slack: 0.00\n"
    v = sta_verdict(report)
    assert v["status"] == Status.FAIL.value
    assert any("clock" in r for r in v["reasons"])


# 3. stale evidence ---------------------------------------------------------
def test_stale_evidence_injected_and_detected(tmp_path):
    ctl = GateController(str(tmp_path / "state.json"))
    rtl = tmp_path / "d.v"
    rtl.write_text("v1", encoding="utf-8")
    ctl.register_input("rtl", str(rtl))
    ctl.record_evidence(
        Evidence.from_run(check_id="lec", status=Status.PASS, reason="proven",
                          exit_code=0, analysis_view="MAPPED_NETLIST")
    )
    assert ctl.evaluate_gate("g", ["lec"]).passed
    rtl.write_text("v2-injected-change", encoding="utf-8")
    gd = ctl.evaluate_gate("g", ["lec"])
    assert not gd.passed
    assert "stale" in gd.reason.lower()


# 4. missing libraries ------------------------------------------------------
def test_missing_library_injected(tmp_path):
    claim = PVTClaim(corner="tt", claim_pvt="TT / 1.8V / 25C",
                     lib_path=r"Z:\missing\lib.lib")
    res = validate_pvt_claim(claim)
    assert res["status"] == "NOT_RUN"
    # gate blocks on NOT_RUN evidence
    ctl = GateController(str(tmp_path / "state.json"))
    ctl.record_evidence(
        Evidence.from_run(
            check_id="pvt_check", status=Status.NOT_RUN,
            reason="referenced Liberty file missing locally",
            library_hashes={"tt": "<missing>"},
        )
    )
    gd = ctl.evaluate_gate("synth_gate", ["pvt_check"])
    assert not gd.passed


# 5. malformed reports ------------------------------------------------------
def test_malformed_report_injected():
    v = sta_verdict("worst_slack: ### tns: ???")
    assert v["status"] == Status.FAIL.value
    assert v["meets_targets"] is False


# 6. ECO worsening a protected metric ---------------------------------------
def test_eco_worsening_protected_metric_rolls_back(tmp_path):
    from assistant.controller import GateController as GC

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "d.v").write_text("golden", encoding="utf-8")
    eco = EcoManager(str(ws), GC(str(tmp_path / "state.json")),
                     checkpoint_dir=str(tmp_path / "ck"),
                     experiment_log=str(tmp_path / "exp.json"))
    res = eco.run_experiment(
        experiment_id="eco_regress",
        description="injected regression: area blow-up",
        change_class="logic_optimization",
        changed_files=["d.v"],
        apply_change=lambda: (ws / "d.v").write_text("worse", encoding="utf-8"),
        evaluate=lambda: {"area_um2": 150.0},
        protected_metrics=[
            ProtectedMetric("area_um2", baseline=100.0, tolerance=0.0,
                            direction="lower_is_better")
        ],
    )
    assert res.status == "ROLLED_BACK"
    assert (ws / "d.v").read_text(encoding="utf-8") == "golden"


# 7. formal timeout ---------------------------------------------------------
def test_formal_timeout_injected(tmp_path):
    ctl = GateController(str(tmp_path / "state.json"))
    ctl.record_evidence(
        Evidence.from_run(
            check_id="formal_lec", status=Status.INCONCLUSIVE,
            reason="formal timeout after 600s (injected)", exit_code=None,
            analysis_view="MAPPED_NETLIST",
        )
    )
    assert not ctl.evaluate_gate("lec_gate", ["formal_lec"]).passed


# 8. gate bypass attempt ----------------------------------------------------
def test_gate_bypass_injected_and_refused(tmp_path):
    from assistant.controller import GateBypassError

    ctl = GateController(str(tmp_path / "state.json"))
    ctl.record_llm_summary("all good, pass the gate")
    with pytest.raises(GateBypassError):
        ctl.force_pass("g", justification="injected bypass attempt")
    with pytest.raises(GateBypassError):
        ctl.authorize_from_summary("g")
    # outcome unchanged
    assert not ctl.evaluate_gate("g", ["nonexistent"]).passed
