"""Foundation-repair regression tests (findings B/C/D/E and EOL).

These tests pin the repaired repository structure so the verified problems
cannot silently return:
  B - repo-root paths resolved from repo root, not the example dir
  C - technology-mapped synthesis (abc -liberty) exists and is labelled
  D - LEC uses equiv_make with the required 3 args + verdict assertion
  E - STA verdict distinguishes ran vs meets-targets (covered in
      test_sta_parser.py; here we check the script wiring)
  EOL - shell scripts are LF in the index and checked out as LF
"""
import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "examples", "test-counter", "test_framework.sh")


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def test_gitattributes_pins_shell_eol():
    ga = _read(os.path.join(REPO, ".gitattributes")).decode("utf-8", "replace")
    # all *.sh must be LF, plus the hook script
    assert re.search(r"^\*\.sh\s+text\s+eol=lf", ga, re.M), (
        ".gitattributes must pin *.sh to LF so fresh Windows clones work"
    )
    assert "hooks/session-start" in ga


def test_shell_scripts_have_no_crlf_in_worktree():
    for rel in (
        "examples/test-counter/test_framework.sh",
        "hooks/session-start",
        "regression/negative/run_negative_tests.sh",
    ):
        p = os.path.join(REPO, rel)
        if not os.path.isfile(p):
            continue
        data = _read(p)
        assert b"\r\n" not in data, f"{rel} contains CRLF; must be LF"


def test_script_resolves_repo_root_not_example_dir():
    """Finding B: repo-root files must be checked against the repo root."""
    text = _read(SCRIPT).decode("utf-8", "replace")
    assert "REPO_ROOT" in text, "script must compute REPO_ROOT"
    # PROJECT_DIR must no longer be the base for repo-root checks
    m = re.search(r'REPO_ROOT="\$\(([^)]+)\)"', text)
    assert m, "REPO_ROOT must be derived from the script location"
    # repo-root files are checked with $REPO_ROOT prefix (loop or explicit)
    assert re.search(r'\$REPO_ROOT/\$f|\$REPO_ROOT/CLAUDE\.md', text), (
        "CLAUDE.md must be checked relative to REPO_ROOT"
    )


def test_script_technology_mapped_synthesis():
    """Finding C: mapped synthesis via abc -liberty must exist and be labelled."""
    text = _read(SCRIPT).decode("utf-8", "replace")
    assert "abc -liberty" in text, "must run technology mapping with abc -liberty"
    assert "read_liberty" in text
    # explicit labelling of generic vs mapped
    assert re.search(r"mapped|technology-mapped", text, re.I)
    assert re.search(r"generic", text, re.I)


def test_script_lec_uses_correct_equiv_make_arity():
    """Finding D: equiv_make gold gate equiv (3 args) + asserted verdict."""
    text = _read(SCRIPT).decode("utf-8", "replace")
    # no two-argument equiv_make calls
    for m in re.finditer(r"equiv_make\s+([^\s;|&]+)\s+([^\s;|&]+)", text):
        args = m.group(0).split()
        # a 2-arg call is followed by nothing (newline/EOF/redirect)
        tail = text[m.end(): m.end() + 2]
        if not tail or tail[0] in "\n\r;":
            pytest.fail(f"equiv_make called with only 2 args: {m.group(0)!r}")
    assert re.search(r"equiv_make\s+\S+\s+\S+\s+\S+", text), (
        "equiv_make needs gold, gate and output module"
    )
    assert "async2sync" in text, "sequential RTL needs async2sync before LEC"
    assert "equiv_status -assert" in text
    assert "Equivalence successfully proven" in text, (
        "the asserted verdict string must be required, not just exit code"
    )


def test_script_sta_distinguishes_ran_from_meets():
    """Finding E: exit 0 alone must not be a pass; slack must be parsed."""
    text = _read(SCRIPT).decode("utf-8", "replace")
    assert "meets" in text.lower() or "wns" in text.lower()
    # a pass must depend on a parsed slack value, not only STA_RC
    assert re.search(r"WNS", text), "script must capture a WNS value"
    # guard: 'pass' must not be granted when WNS is empty
    assert not re.search(
        r'sta_rc\s*-eq\s*0.*\n.*pass\s+"STA completed', text, re.I
    ), "exit-0-only STA pass is forbidden"


def test_synthesis_targets_pvt_labels_match_liberty():
    """Finding F: corrected PVT strings + no TT-only claim."""
    import json

    with open(os.path.join(REPO, "synthesis_targets.json"), "r", encoding="utf-8") as f:
        cfg = json.load(f)
    corners = cfg["corners"]
    # tt_025C_1v80 => 1.80V
    assert "1.80" in corners["func_tt"]["pvt"] or "1.8V" in corners["func_tt"]["pvt"]
    # ff_n40C_1v95 => 1.95V / -40C
    assert "1.95" in corners["func_ff"]["pvt"] or "1.95V" in corners["func_ff"]["pvt"]
    # ss_100C_1v60 => 1.60V / 100C
    assert "1.60" in corners["func_ss"]["pvt"] or "1.6V" in corners["func_ss"]["pvt"]
    # no false "only TT" claim anywhere
    blob = json.dumps(cfg).lower()
    assert "only ships tt" not in blob
    assert "only tt" not in blob
    # availability must be stated as LOCAL file availability, not PDK corners
    for name, spec in corners.items():
        note = str(spec.get("note", "")).lower()
        if note:
            assert "local" in note or "pdk" in note or "present" in note


def test_no_pipelining_by_default():
    """Finding I: interface latency preservation by default."""
    import json

    with open(os.path.join(REPO, "synthesis_targets.json"), "r", encoding="utf-8") as f:
        cfg = json.load(f)
    prefs = cfg.get("optimization_preferences", {})
    assert prefs.get("allow_pipelining") is False, (
        "allow_pipelining must default to false (interface latency preserved)"
    )
    assert prefs.get("max_latency_increase_cycles", 0) == 0


def test_initial_review_and_tracker_exist():
    for rel in (
        "docs/reviews/initial-review.md",
        "docs/reviews/remediation-tracker.md",
    ):
        assert os.path.isfile(os.path.join(REPO, rel)), rel
    tr = _read(os.path.join(REPO, "docs", "reviews", "remediation-tracker.md")).decode(
        "utf-8", "replace"
    )
    for fid in "ABCDEFGHIJKLMN":
        assert re.search(rf"\|\s*{fid}\s*\|", tr), f"tracker missing finding {fid}"


def test_readme_claims_match_demonstrated_capabilities():
    """Finding K: IR/EM/antenna must not be claimed as implemented checks."""
    readme = _read(os.path.join(REPO, "README.md")).decode("utf-8", "replace").lower()
    # the capability matrix must mark them unsupported/not implemented
    assert "not implemented" in readme or "unsupported" in readme
