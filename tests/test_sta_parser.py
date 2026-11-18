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

# Real OpenSTA 12_sta.sh spelling (SR-07 U3): "setup_wns <num>" and
# "<num> slack (MET|VIOLATED)" rather than "worst_slack:" / "slack (MET) <num>".
REAL_FORMAT_REPORT = """\
OpenSTA 3.1.0
STA_TRACE read_sdc 11_final.sdc
STA_TRACE read_spef 11_final.spef
=== TT CORNER (typical, 25C, 1.80V) ===
Startpoint: cin (input port clocked by clk)
Endpoint: sum[3] (output port clocked by clk)
   4.9571   slack (MET)
   4.9605   slack (MET)
--- WNS ---
setup_wns 4.957081108122514
hold_wns 1.7040449533756277
--- TNS ---
setup_tns 0.0
hold_tns 0.0
"""

REAL_FORMAT_VIOLATED_REPORT = """\
OpenSTA 3.1.0
=== SS CORNER (slow, -40C, 1.40V) ===
  -1.8325   slack (VIOLATED)
  -1.4406   slack (VIOLATED)
setup_wns -1.8324693392474176
setup_tns -4.6857221651259175
"""

CHECK_TIMING_CLEAN_REPORT = REAL_FORMAT_REPORT + """\
=== CHECK_TIMING ===
STA_CHECK_TIMING_VERDICT: CLEAN
STA_MISSING_CLOCK_ENDPOINTS: 0
=== CHECK_TIMING_END ===
"""

CHECK_TIMING_UNCONSTRAINED_REPORT = REAL_FORMAT_REPORT + """\
=== CHECK_TIMING ===
Warning: endpoint count_reg[0]/D is unconstrained (has no clock)
STA_CHECK_TIMING_VERDICT: UNCONSTRAINED
STA_MISSING_CLOCK_ENDPOINTS: 3
=== CHECK_TIMING_END ===
"""

# Real check_setup wording observed in a direct OpenSTA probe on the pinned
# test_counter run (ports missing set_input_delay), plus the probe-failed marker
# emitted when the Tcl command is unavailable.
CHECK_SETUP_INPUT_DELAY_REPORT = REAL_FORMAT_REPORT + """\
=== CHECK_TIMING ===
Warning: There are 6 input ports missing set_input_delay.
  enable
  rst_n
STA_TRACE check_setup_catch=0
STA_TRACE check_setup_result=0
STA_CHECK_TIMING_VERDICT: UNCONSTRAINED
CHECK_SETUP_CATCH: 0
=== CHECK_TIMING_END ===
"""

CHECK_SETUP_PROBE_FAILED_REPORT = REAL_FORMAT_REPORT + """\
=== CHECK_TIMING ===
STA_TRACE check_setup_catch=1
STA_TRACE check_setup_result=invalid command name "check_setup"
STA_CHECK_TIMING_VERDICT: PROBE_FAILED
CHECK_SETUP_CATCH: 1
=== CHECK_TIMING_END ===
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


def test_real_opensta_report_format_is_parsed():
    """SR-07 U3: parse the actual 12_sta.sh report spelling."""
    p = parse_sta_report(REAL_FORMAT_REPORT)
    assert p.wns == pytest.approx(4.957081108122514)
    assert p.tns == pytest.approx(0.0)
    assert p.path_slacks == pytest.approx([4.9571, 4.9605])
    assert p.has_clock
    v = sta_verdict(REAL_FORMAT_REPORT, target_wns_ns=0.0)
    assert v["status"] == Status.PASS.value
    assert v["meets_targets"] is True


def test_real_opensta_violated_report_ran_but_fails():
    p = parse_sta_report(REAL_FORMAT_VIOLATED_REPORT)
    assert p.wns == pytest.approx(-1.8324693392474176)
    assert p.tns == pytest.approx(-4.6857221651259175)
    assert p.path_slacks == pytest.approx([-1.8325, -1.4406])
    v = sta_verdict(REAL_FORMAT_VIOLATED_REPORT, target_wns_ns=0.0)
    assert v["status"] == Status.FAIL.value
    assert v["ran"] is True
    assert v["meets_targets"] is False


def test_check_timing_unconstrained_is_never_a_pass():
    """An unconstrained endpoint must reject even when WNS looks positive."""
    p = parse_sta_report(CHECK_TIMING_UNCONSTRAINED_REPORT)
    assert p.unconstrained_hits, "check_timing warning was not detected"
    v = sta_verdict(CHECK_TIMING_UNCONSTRAINED_REPORT, target_wns_ns=0.0)
    assert v["status"] in (Status.FAIL.value, Status.INCONCLUSIVE.value)
    assert v["status"] != Status.PASS.value
    assert v["meets_targets"] is False
    assert any("unconstrained" in r for r in v["reasons"])


def test_check_setup_input_delay_warning_is_rejected():
    """Real check_setup 'missing set_input_delay' wording must reject."""
    p = parse_sta_report(CHECK_SETUP_INPUT_DELAY_REPORT)
    assert p.unconstrained_hits
    v = sta_verdict(CHECK_SETUP_INPUT_DELAY_REPORT, target_wns_ns=0.0)
    assert v["status"] in (Status.FAIL.value, Status.INCONCLUSIVE.value)
    assert any("unconstrained" in r for r in v["reasons"])


def test_check_setup_probe_failed_is_rejected():
    """An unavailable/failed probe marker must never be treated as PASS."""
    p = parse_sta_report(CHECK_SETUP_PROBE_FAILED_REPORT)
    assert p.unconstrained_hits
    v = sta_verdict(CHECK_SETUP_PROBE_FAILED_REPORT, target_wns_ns=0.0)
    assert v["status"] in (Status.FAIL.value, Status.INCONCLUSIVE.value)
    assert v["status"] != Status.PASS.value


def test_clean_check_timing_does_not_spuriously_reject():
    p = parse_sta_report(CHECK_TIMING_CLEAN_REPORT)
    assert not p.unconstrained_hits
    v = sta_verdict(CHECK_TIMING_CLEAN_REPORT, target_wns_ns=0.0)
    assert v["status"] == Status.PASS.value


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
