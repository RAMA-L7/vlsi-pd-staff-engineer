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


# ------------------------------------------------------------------- SR-08 / SR-10
def test_pins_include_extraction_decks_and_approved_soc_ctrl_sdc():
    """Extraction/deck collateral is pinned, and the 10 ns SDC pin is current."""
    import hashlib
    import json

    with open(os.path.join(REPO, "assistant", "pins.json"), "r", encoding="utf-8") as f:
        pins = json.load(f)
    assert pins["extraction"]["setRC.tcl"]
    assert pins["extraction"]["rcx_patterns.rules"]
    assert pins["extraction"]["sky130_fd_sc_hd.tlef"]
    assert len(pins["extraction"]["setRC.tcl"]) == 64  # sha256 hex
    for key in ("cdl/sky130hd.cdl", "drc/sky130hd.lydrc", "lvs/sky130hd.lylvs",
                "regression/stages/15a_lvs_refcdl.tcl"):
        assert len(pins["decks"][key]) == 64, key
    assert pins["design_inputs"]["soc_ctrl_sdc"] == "ab66de1eb52fda4d52e1585c8ddc7afc39267f781c9e24a65cf03865de0cad5a"

    with open(os.path.join(REPO, "regression", "configs", "soc_ctrl_10ns.json"),
              "r", encoding="utf-8") as f:
        cfg = json.load(f)
    sdc = os.path.join(REPO, "examples", "soc_ctrl", "rtl", "soc_ctrl.sdc")
    raw_sha = hashlib.sha256(_read(sdc)).hexdigest()
    assert cfg["constraints"]["sdc_sha256"] == raw_sha
    assert pins["design_inputs"]["soc_ctrl_sdc"] == raw_sha


def test_tools_environment_overrides_are_validated_and_defaults_preserved():
    from assistant import tools

    cfg = tools.resolve_environment_config({})
    assert cfg["values"]["tools_root"] == r"D:\tools"
    assert cfg["values"]["wsl_distro"] == "Ubuntu-24.04"
    assert set(cfg["sources"].values()) == {"default"}

    injected = {
        "ASSISTANT_TOOLS_ROOT": r"D:\alt-tools",
        "ASSISTANT_WSL_DISTRO": "Ubuntu-22.04",
        "REG_LIB_DIR": r"D:\alt-libs",
    }
    c2 = tools.resolve_environment_config(injected)
    assert c2["values"]["tools_root"] == r"D:\alt-tools"
    assert c2["sources"]["tools_root"] == "ASSISTANT_TOOLS_ROOT"
    assert c2["values"]["wsl_distro"] == "Ubuntu-22.04"
    assert c2["values"]["lib_dir"] == r"D:\alt-libs"
    # A whitespace-only override is treated as unset (documented fallback);
    # a control character is malformed and must be rejected.
    assert tools.resolve_environment_config(
        {"ASSISTANT_TOOLS_ROOT": "   "}
    )["values"]["tools_root"] == r"D:\tools"
    with pytest.raises(ValueError):
        tools.resolve_environment_config({"ASSISTANT_TOOLS_ROOT": "D:\\tools\x00bad"})


def test_readme_has_one_current_matrix_and_corrected_timing():
    readme = _read(os.path.join(REPO, "README.md")).decode("utf-8", "replace")
    assert "Authoritative qualification matrix" in readme
    # The matrix is tied to the published content identity (TR-00); the base
    # commit is retained only as historical provenance.
    assert "45e4251370c39ed27e2ccd6a7a804ed90eaf4472" in readme
    assert "0a9eb57" in readme
    assert "baseline_20261116" in readme
    assert "run_pub_m1d_soc_ctrl_full" in readme
    # macro example explicitly unsupported; automation vs agent orchestration distinguished
    assert "NO-MACROS" in readme
    assert "Automated executor vs AI-agent orchestration" in readme
    assert "PPA conditions" in readme
    # negative setup WNS must not be presented alongside a PASS/zero-violator claim
    assert "WNS -0.6159" not in readme
    assert "5.7003" in readme and "0.3917" in readme


def test_setup_and_regression_documented_commands_and_platform():
    setup = _read(os.path.join(REPO, "docs", "SETUP.md")).decode("utf-8", "replace")
    reg = _read(os.path.join(REPO, "docs", "REGRESSION.md")).decode("utf-8", "replace")
    for token in ("ASSISTANT_TOOLS_ROOT", "Ubuntu-24.04", "3.10.11",
                  "Modified upstream collateral", "baseline --design"):
        assert token in setup, token
    for token in ("stage00_preflight.py", "--require-tool", "missing_mandatory",
                  "baseline --design"):
        assert token in reg, token
    rreadme = _read(os.path.join(REPO, "regression", "README.md")).decode("utf-8", "replace")
    assert "stage00_preflight.py" in rreadme
    assert "Authoritative" in rreadme


def test_preflight_helpers_reject_store_stub_python():
    """The WindowsApps python.exe alias is not a working interpreter."""
    import importlib.util

    script = os.path.join(REPO, "regression", "checks", "stage00_preflight.py")
    spec = importlib.util.spec_from_file_location("stage00_preflight_under_test", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class _StoreStub:
        returncode = 1
        stdout = "Microsoft Store alias stub - install Python"
        stderr = ""

    original = mod.subprocess.run
    try:
        mod.subprocess.run = lambda *a, **k: _StoreStub()
        ok, info = mod._probe_python_candidate(
            r"C:\Users\test\AppData\Local\Microsoft\WindowsApps\python.exe"
        )
    finally:
        mod.subprocess.run = original
    assert ok is False
    assert "Microsoft Store" in info


def test_preflight_reports_real_failures_and_exact_dependencies(tmp_path):
    """No `or True` no-ops: shared out dir and undriven outputs really fail."""
    import json
    import subprocess
    import sys

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "bad.v").write_text("module bad(output y); endmodule\n", encoding="utf-8")
    (proj / "project.json").write_text(
        json.dumps({"top_module": "bad", "rtl_sources": ["bad.v"]}), encoding="utf-8"
    )
    script = os.path.join(REPO, "regression", "checks", "stage00_preflight.py")
    env = os.environ.copy()
    env["REG_PYTHON"] = sys.executable  # real interpreter, not a Store stub
    env["ASSISTANT_WSL_DISTRO"] = "NO_SUCH_DISTRO"  # skip WSL probes quickly
    proc = subprocess.run(
        [sys.executable, script, str(proj / "project.json"), "--out-dir", str(proj)],
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    with open(os.path.join(proj, "stage00_result.json"), "r", encoding="utf-8") as f:
        result = json.load(f)
    checks = {c["name"]: c for c in result["checks"]}
    assert result["overall"] == "FAIL"
    assert checks["workspace_isolation"]["status"] == "FAIL"
    assert checks["rtl_output_drive_scan:bad.v"]["status"] == "FAIL"
    assert checks["tool_python_available"]["status"] == "PASS"
    deps = result["dependencies"]
    assert "python" in deps["mandatory"][0]["name"] or any(
        d["name"] == "python" for d in deps["mandatory"]
    )
    assert isinstance(deps["missing_mandatory"], list)
    assert isinstance(deps["missing_optional"], list)


# ------------------------------------------------------------------- WORKSTREAM 2
def test_portable_config_source_of_truth_is_structurally_valid():
    """pins.json portable_config is a usable, complete config source of truth."""
    from assistant import tools

    pins = tools.load_pins()
    assert tools.validate_portable_config(pins) == []
    items = pins["portable_config"]["collateral"]
    required = [i for i in items if i["required"]]
    optional = [i for i in items if not i["required"]]
    assert len(required) >= 10, "expected the core LEF/GDS/deck/RC collateral to be required"
    assert len(optional) >= 1, "ORFS-only assets must be pinned but optional"
    for it in items:
        assert len(it["sha256"]) == 64
        assert isinstance(it["required"], bool)


def test_portable_config_validation_rejects_corruption():
    """A truncated/edited config fails closed instead of silently disabling checks."""
    import copy

    from assistant import tools

    pins = tools.load_pins()
    assert tools.validate_portable_config(pins) == []

    bad_hash = copy.deepcopy(pins)
    bad_hash["portable_config"]["collateral"][0]["sha256"] = "not-a-hash"
    assert any("sha256" in e for e in tools.validate_portable_config(bad_hash))

    no_fallbacks = copy.deepcopy(pins)
    del no_fallbacks["portable_config"]["fallbacks"]
    assert any("fallbacks" in e for e in tools.validate_portable_config(no_fallbacks))

    no_collateral = copy.deepcopy(pins)
    no_collateral["portable_config"]["collateral"] = []
    assert any("collateral" in e for e in tools.validate_portable_config(no_collateral))

    # A structurally invalid source never applies a fallback.
    cfg = tools.resolve_platform_config({}, pins=no_fallbacks, exists=lambda p: False)
    assert cfg.get("portable_config_errors")
    assert not cfg.get("portable_fallback_applied")


def test_missing_default_fallback_applies_only_when_default_absent_and_no_override():
    """Fallback is used solely for absent defaults with no caller override."""
    from assistant import tools

    pins = tools.load_pins()
    fb = pins["portable_config"]["fallbacks"]
    default_orfs = os.path.join(
        r"D:\rta-real-world-benchmark\02_projects", "OpenROAD-flow-scripts"
    )

    def default_absent(p):
        return p != default_orfs and p in fb.values()

    cfg = tools.resolve_platform_config({}, pins=pins, exists=default_absent)
    assert cfg["sources"]["orfs_dir"] == "portable_fallback"
    assert cfg["values"]["orfs_dir"] == fb["orfs_dir"]
    assert "orfs_dir" in cfg["portable_fallback_applied"]
    assert cfg["validation"]["orfs_dir"]["exists"] is True

    # An explicit caller override wins and is never recorded as a fallback.
    c2 = tools.resolve_platform_config(
        {"ASSISTANT_ORFS_DIR": r"D:\explicit-orfs"}, pins=pins, exists=default_absent
    )
    assert c2["sources"]["orfs_dir"] == "ASSISTANT_ORFS_DIR"
    assert c2["values"]["orfs_dir"] == r"D:\explicit-orfs"
    assert "orfs_dir" not in c2.get("portable_fallback_applied", [])

    # If the recorded default is present, no fallback is applied.
    c3 = tools.resolve_platform_config({}, pins=pins, exists=lambda p: True)
    assert c3["sources"]["orfs_dir"] == "default"
    assert not c3.get("portable_fallback_applied")


def test_platform_collateral_hash_mismatch_and_missing_are_rejected():
    """Exact-hash identity is enforced; optional absence is non-fatal."""
    from assistant import tools

    pins = tools.load_pins()
    items = pins["portable_config"]["collateral"]
    correct = lambda p: next(i["sha256"] for i in items if p.endswith(i["path"]))

    all_ok = tools.validate_platform_collateral(
        "hd", "hs", pins, exists=lambda p: True, sha256=correct
    )
    assert all_ok["overall"] == "PASS"
    assert not all_ok["hash_mismatch"] and not all_ok["missing_required"]

    wrong_hash = tools.validate_platform_collateral(
        "hd", "hs", pins, exists=lambda p: True, sha256=lambda p: "0" * 64
    )
    assert wrong_hash["overall"] == "FAIL"
    assert len(wrong_hash["hash_mismatch"]) == len(items)

    required = next(i for i in items if i["required"])
    optional = next(i for i in items if not i["required"])
    missing_req = tools.validate_platform_collateral(
        "hd", "hs", pins,
        exists=lambda p: not p.endswith(required["path"]), sha256=correct,
    )
    assert missing_req["overall"] == "FAIL"
    assert required["key"] in missing_req["missing_required"]

    missing_opt = tools.validate_platform_collateral(
        "hd", "hs", pins,
        exists=lambda p: not p.endswith(optional["path"]), sha256=correct,
    )
    assert missing_opt["overall"] == "PASS"
    assert optional["key"] in missing_opt["optional_missing"]
