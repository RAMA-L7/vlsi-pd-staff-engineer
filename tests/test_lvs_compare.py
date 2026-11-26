"""Targeted regression tests for design-driven top-cell selection in lvs_compare.py.

Covers Milestone 1G: the comparator must resolve the top cell from
authoritative verification-time information (env REG_DESIGN, the run-dir
manifest, or a unique common root-cell detected in both netlists).  No design
name may be hard-coded; an unknown / ambiguous / one-sided top must return an
explicit FAIL / INCONCLUSIVE and never a PASS.

These tests exercise the real comparator as a subprocess (env handling, return
code and written report included) and never run any part of the 15-stage flow.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPARATOR = ROOT / "regression" / "checks" / "lvs_compare.py"
SYNTHETIC = ROOT / "regression" / "tests" / "lvs_synthetic"


def _netlist(top):
    """Minimal two-netlist-equivalent SPICE with an unambiguous top cell."""
    return (
        "* regression fixture\n"
        ".subckt INVX1 A Y VGND VNB VPB VPWR\n"
        "M0 Y A VGND VNB nshort w=0.42u l=0.15u\n"
        "M1 Y A VPWR VPB pshort w=0.84u l=0.15u\n"
        ".ends\n"
        ".subckt %s in1 in2 out VGND VPWR\n" % top
        + "X1 in1 nint VGND VGND VPWR VPWR INVX1\n"
        "X2 in2 out  VGND VGND VPWR VPWR INVX1\n"
        "X3 nint out VGND VGND VPWR VPWR INVX1\n"
        ".ends\n"
    )


def _write_run(tmp_path, layout_text, reference_text, manifest=None, design=None):
    """Create <run>/15_lvs/<extracted> + <run>/<reference>, run-shaped.

    ``manifest`` selects which authoritative run-dir hint is written:
    ``design_cdl``, ``acceptance_json`` or ``report_json``; ``design`` is the
    design name that hint must carry.
    """
    run = tmp_path / "run"
    lvs = run / "15_lvs"
    lvs.mkdir(parents=True)
    extracted = lvs / "15_lvs_extracted.cir"
    reference = run / "6_final_concat.cdl"
    extracted.write_text(layout_text, encoding="utf-8")
    reference.write_text(reference_text, encoding="utf-8")
    if manifest == "design_cdl":
        (run / ("%s_design.cdl" % design)).write_text(reference_text, encoding="utf-8")
    elif manifest in ("acceptance_json", "report_json"):
        fname = "acceptance.json" if manifest == "acceptance_json" else "report.json"
        (run / fname).write_text(
            json.dumps({"design": design}), encoding="utf-8")
    return extracted, reference


def _env(env_extra=None):
    env = os.environ.copy()
    env.pop("REG_DESIGN", None)  # tests are explicit about the design hint
    if env_extra:
        env.update(env_extra)
    return env


def _run_comparator(extracted, reference, out_path, env_extra=None):
    return subprocess.run(
        [sys.executable, str(COMPARATOR), str(extracted), str(reference), str(out_path)],
        capture_output=True, text=True, env=_env(env_extra),
    )


def _report(out_path, proc):
    if out_path.exists():
        return out_path.read_text(encoding="utf-8")
    return proc.stdout + proc.stderr


@pytest.mark.parametrize("source", ["env", "design_cdl", "acceptance_json", "report_json"])
def test_soc_ctrl_top_selected_from_manifest_or_env(tmp_path, source):
    netlist = _netlist("soc_ctrl")
    extracted, reference = _write_run(
        tmp_path, netlist, netlist,
        manifest=source if source != "env" else None, design="soc_ctrl")
    out = tmp_path / "lvs_compare.txt"
    env_extra = {"REG_DESIGN": "soc_ctrl"} if source == "env" else None
    proc = _run_comparator(extracted, reference, out, env_extra)
    txt = _report(out, proc)

    assert proc.returncode == 0, txt
    assert "LVS_TOP: SOC_CTRL" in txt
    assert "TOP CIRCUIT MISSING" not in txt
    assert "DEVICES: layout=6 schematic=6" in txt
    assert "TOPOLOGY_EQUIVALENT: True" in txt
    assert "LVS_VERDICT: PASS" in txt
    if source == "env":
        assert "LVS_TOP_SOURCE: env:REG_DESIGN" in txt
    else:
        assert "LVS_TOP_SOURCE: run-dir:" in txt


@pytest.mark.parametrize("source", ["env", "design_cdl"])
def test_test_counter_resolved_from_env_or_manifest(tmp_path, source):
    netlist = _netlist("test_counter")
    extracted, reference = _write_run(
        tmp_path, netlist, netlist,
        manifest=source if source != "env" else None, design="test_counter")
    out = tmp_path / "lvs_compare.txt"
    env_extra = {"REG_DESIGN": "test_counter"} if source == "env" else None
    proc = _run_comparator(extracted, reference, out, env_extra)
    txt = _report(out, proc)

    assert proc.returncode == 0, txt
    assert "LVS_TOP: TEST_COUNTER" in txt
    assert "LVS_TOP_SOURCE: %s" % (
        "env:REG_DESIGN" if source == "env" else "run-dir:<design>_design.cdl") in txt
    assert "TOP CIRCUIT MISSING" not in txt
    assert "DEVICES: layout=6 schematic=6" in txt
    assert "TOPOLOGY_EQUIVALENT: True" in txt
    assert "LVS_VERDICT: PASS" in txt


def test_no_hard_coded_fallback_in_comparator_source():
    src = COMPARATOR.read_text(encoding="utf-8")
    assert "TEST_COUNTER" not in src
    assert "DEFAULT_TOP" not in src
    assert "netlist-top-cell-detection" in src


def test_unique_common_root_detected_without_hint(tmp_path):
    """No env and no manifest: a unique common root is authoritative itself."""
    netlist = _netlist("NO_HINT_ROOT")
    extracted, reference = _write_run(tmp_path, netlist, netlist)
    out = tmp_path / "lvs_compare.txt"
    proc = _run_comparator(extracted, reference, out)
    txt = _report(out, proc)

    assert proc.returncode == 0, txt
    assert "LVS_TOP: NO_HINT_ROOT" in txt
    assert "LVS_TOP_SOURCE: netlist-top-cell-detection" in txt
    assert "TOPOLOGY_EQUIVALENT: True" in txt
    assert "LVS_VERDICT: PASS" in txt


def test_unknown_top_from_env_fails_explicitly(tmp_path):
    netlist = _netlist("soc_ctrl")
    extracted, reference = _write_run(tmp_path, netlist, netlist)
    out = tmp_path / "lvs_compare.txt"
    proc = _run_comparator(extracted, reference, out, {"REG_DESIGN": "NO_SUCH_TOP"})
    txt = _report(out, proc)

    assert proc.returncode == 1
    assert "LVS_TOP: NO_SUCH_TOP" in txt
    assert "TOP CIRCUIT MISSING" in txt
    assert "requested=NO_SUCH_TOP" in txt
    assert "LVS_VERDICT: FAIL" in txt
    assert "DEVICES:" not in txt  # no PASS-style summary is emitted


def test_top_missing_on_one_side_fails_explicitly(tmp_path):
    extracted, reference = _write_run(
        tmp_path, _netlist("soc_ctrl"), _netlist("SOME_OTHER_TOP"))
    out = tmp_path / "lvs_compare.txt"
    proc = _run_comparator(extracted, reference, out, {"REG_DESIGN": "soc_ctrl"})
    txt = _report(out, proc)

    assert proc.returncode == 1
    assert "TOP CIRCUIT MISSING" in txt
    assert "layout=True schematic=False" in txt
    assert "LVS_VERDICT: FAIL" in txt
    assert "DEVICES:" not in txt


def test_ambiguous_auto_detection_is_inconclusive_not_pass(tmp_path):
    # Two independent roots in both netlists, no env hint and no manifest:
    # auto-detection must refuse to guess rather than report a false PASS.
    netlist = _netlist("TOP_A") + _netlist("TOP_B")
    extracted, reference = _write_run(tmp_path, netlist, netlist)
    out = tmp_path / "lvs_compare.txt"
    proc = _run_comparator(extracted, reference, out)
    txt = _report(out, proc)

    assert proc.returncode == 1
    assert "LVS_TOP: UNRESOLVED" in txt
    assert "TOP CIRCUIT MISSING" in txt
    assert "LVS_VERDICT: INCONCLUSIVE" in txt
    assert "DEVICES:" not in txt


def test_fail_verdict_returns_nonzero_exit_code(tmp_path):
    """SR-07 U2: an LVS_VERDICT: FAIL must produce a non-zero exit status.

    The comparator used to print FAIL yet return 0, so 15_lvs.sh's `rc=$?`
    gate could not detect a failed independent comparison.  A genuine match
    must still exit 0 (covered by the PASS tests above).
    """
    good = _netlist("soc_ctrl")
    # Structurally mismatched reference: one device geometry differs, so the
    # device multiset (and WL connectivity) cannot match.
    bad = good.replace("w=0.42u", "w=0.50u", 1)
    assert bad != good
    extracted, reference = _write_run(tmp_path, good, bad)
    out = tmp_path / "lvs_compare.txt"
    proc = _run_comparator(extracted, reference, out, {"REG_DESIGN": "soc_ctrl"})
    txt = _report(out, proc)

    assert proc.returncode == 1, txt
    assert "LVS_VERDICT: FAIL" in txt
    m = re.search(r"UNMATCHED_DEVICES: (\d+)", txt)
    assert m and int(m.group(1)) > 0
    assert "LVS_VERDICT: PASS" not in txt


def test_synthetic_negative_controls_preserved(tmp_path):
    """The existing lvs_synthetic negative-control suite stays intact."""
    work = tmp_path / "regression"
    shutil.copytree(SYNTHETIC, work / "tests" / "lvs_synthetic")
    (work / "checks").mkdir(parents=True)
    shutil.copy2(COMPARATOR, work / "checks" / "lvs_compare.py")
    proc = subprocess.run(
        [sys.executable, str(work / "tests" / "lvs_synthetic" / "run.py")],
        capture_output=True, text=True, env=_env(),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert '"fail": 0' in proc.stdout


# ---------------------------------------------------------------------------
# EXT-06: top-level pin correspondence is gating, never "informational".
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_layout", [
    "lay_pinname.cir", "lay_pinorder.cir", "lay_pincount.cir",
])
def test_pin_mismatch_fails_closed(tmp_path, bad_layout):
    """An unresolved pin mapping must produce FAIL + non-zero rc (EXT-06)."""
    ref = (SYNTHETIC / "ref.cdl").read_text(encoding="utf-8")
    bad = (SYNTHETIC / bad_layout).read_text(encoding="utf-8")
    extracted, reference = _write_run(tmp_path, bad, ref)
    out = tmp_path / "lvs_compare.txt"
    proc = _run_comparator(extracted, reference, out, {"REG_DESIGN": "test_counter"})
    txt = _report(out, proc)

    assert proc.returncode == 1, txt
    assert "PIN_CORRESPONDENCE: UNRESOLVED" in txt
    assert "PIN_CORRESPONDENCE_OK: False" in txt
    assert "LVS_VERDICT: FAIL" in txt
    assert "LVS_VERDICT: PASS" not in txt


def test_equivalent_pins_pass_and_report_resolved(tmp_path):
    """The positive control still passes and states the mapping is resolved."""
    netlist = (SYNTHETIC / "lay_ok.cir").read_text(encoding="utf-8")
    extracted, reference = _write_run(tmp_path, netlist, netlist)
    out = tmp_path / "lvs_compare.txt"
    proc = _run_comparator(extracted, reference, out, {"REG_DESIGN": "test_counter"})
    txt = _report(out, proc)

    assert proc.returncode == 0, txt
    assert "PIN_CORRESPONDENCE: RESOLVED" in txt
    assert "PIN_CORRESPONDENCE_OK: True" in txt
    assert "LVS_VERDICT: PASS" in txt
