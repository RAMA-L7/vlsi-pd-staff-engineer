"""STA parsing: ran-vs-meets-targets, malformed reports, structural rejects."""
import pytest

from assistant.diagnosis import (
    diagnose_stage_delta,
    diagnose_timing,
    parse_sta_report,
    sta_verdict,
    summarize_for_llm,
)
from assistant.statuses import Status

GOOD_REPORT = """
# OpenSTA report
create_clock -name clk -period 7.0
Startpoint: count_reg[0]
Endpoint: count_reg[1]
slack (MET)                          0.42
=== TT CORNER ===
worst_slack: 0.42
tns: 0.00
"""

VIOLATED_REPORT = """
create_clock -name clk -period 7.0
slack (VIOLATED)                    -0.13
worst_slack: -0.13
tns: -1.20
"""

EMPTY_REPORT = ""

NO_CLOCK_REPORT = """
worst_slack: 0.10
"""

UNCONSTRAINED_REPORT = """
create_clock -name clk -period 7.0
Warning: unconstrained path from port A
no paths found for this group
worst_slack: 0.00
"""

UNRESOLVED_REPORT = """
create_clock -name clk -period 7.0
Error: unresolved reference sky130_fd_sc_hd__missing_cell
worst_slack: 0.05
"""


def test_good_report_meets_target():
    v = sta_verdict(GOOD_REPORT, target_wns_ns=0.0)
    assert v["status"] == Status.PASS.value
    assert v["ran"] is True
    assert v["meets_targets"] is True


def test_violated_report_ran_but_misses_target():
    """The key distinction: analysis ran (ran=True) but does NOT meet target."""
    v = sta_verdict(VIOLATED_REPORT, target_wns_ns=0.0)
    assert v["status"] == Status.FAIL.value
    assert v["ran"] is True
    assert v["meets_targets"] is False
    assert any("misses target" in r for r in v["reasons"])


def test_empty_report_fails():
    v = sta_verdict(EMPTY_REPORT)
    assert v["status"] == Status.FAIL.value
    assert v["ran"] is False


def test_missing_clock_rejected():
    v = sta_verdict(NO_CLOCK_REPORT)
    assert v["status"] == Status.FAIL.value
    assert any("no clock" in r for r in v["reasons"])


def test_unconstrained_paths_rejected():
    v = sta_verdict(UNCONSTRAINED_REPORT)
    assert v["status"] == Status.FAIL.value
    assert any("unconstrained" in r for r in v["reasons"])


def test_unresolved_cells_rejected():
    v = sta_verdict(UNRESOLVED_REPORT)
    assert v["status"] == Status.FAIL.value
    assert any("unresolved" in r for r in v["reasons"])


def test_zero_exit_without_slack_is_not_a_pass():
    """Regression guard for finding E: text with no slack values."""
    v = sta_verdict("STA completed successfully\nno errors\n", expect_paths=True)
    assert v["status"] != Status.PASS.value
    assert v["meets_targets"] is False


def test_parse_flags_malformed_worst_slack():
    p = parse_sta_report("worst_slack: abc\ntns: ??")
    assert p.malformed
    assert p.errors


def test_parse_extracts_path_slacks():
    p = parse_sta_report(GOOD_REPORT)
    assert p.wns == pytest.approx(0.42)
    assert p.tns == pytest.approx(0.0)
    assert p.path_slacks == [pytest.approx(0.42)]
    assert p.has_clock


def test_diagnosis_separates_epistemic_categories():
    rep = diagnose_timing(VIOLATED_REPORT, check_id="sta_tt", target_wns_ns=0.0,
                          previous_wns_ns=0.30)
    d = rep.to_dict()
    assert d["observations"], "must have observations"
    assert d["hypotheses"], "must have hypotheses for a violation"
    assert d["heuristics"], "must label heuristics"
    for h in d["hypotheses"]:
        assert h["confidence"] != "n/a"
        assert h["discriminating_test"], "each hypothesis needs a test"
    assert "authorize any gate transition" in d["disclaimer"]


def test_diagnosis_delta_labels_worsening():
    rep = diagnose_stage_delta({"wns": 0.5}, {"wns": -0.1}, worse_is="higher")
    d = rep.to_dict()
    assert any("worsened" in o["text"] for o in d["observations"])
    assert d["hypotheses"]


def test_advisory_summary_is_clearly_labelled():
    rep = diagnose_timing(GOOD_REPORT)
    text = summarize_for_llm(rep)
    assert "advisory only" in text.lower()
    assert "cannot authorize" in text.lower()
