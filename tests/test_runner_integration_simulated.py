"""REAL RUNNER INTEGRATION WITH SIMULATED TOOLS (TR-04).

These tests invoke the actual ``regression/run_regression.ps1`` PowerShell
control flow. Only the tool, acceptance, manifest executors and evidence output
location are injected (additive DI seams with unchanged production defaults).
Fake executables record every launch in a sentinel file so downstream blocking
is proven against real launch records, not a Python mirror of the scheduler.

Remediation round (F-14/F-15/F-25/F-26): the ValidateOnly and resume tests now
assert WHICH stages were actually validated / re-executed using two independent
OS-level sentinels (stage-executor launches and acceptance-provider launches).
The acceptance fake materialises artifacts at the runner's DECLARED $StageArt
paths; otherwise every resume stage would fail on "artifact missing" and the
invalidation assertion would pass for the wrong reason.

``SIM_RUNNER_OVERRIDE`` is a TEST-ONLY seam (default: the production runner)
used to demonstrate before/after behaviour against a byte-identical vulnerable
copy. It does not alter the runner or any production default.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNNER = Path(os.environ.get("SIM_RUNNER_OVERRIDE", str(REPO / "regression" / "run_regression.ps1")))
PY = sys.executable

pytestmark = pytest.mark.skipif(os.name != "nt", reason="PowerShell runner is Windows-only")


def _required_checks() -> dict[str, list[str]]:
    ps1 = RUNNER.read_text(encoding="utf-8")
    block = ps1.split("$RequiredChecks = @{", 1)[1].split("\n}", 1)[0]
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"^\s*'([0-9]{2}_[a-z0-9_]+)'\s*=\s*@\(([^)]*)\)", block, re.M):
        out[m.group(1)] = re.findall(r"'([^']+)'", m.group(2))
    return out


def _stage_art(art_prefix: str = "test_counter") -> dict[str, list[str]]:
    """Read the runner's declared artifact paths (F-15 false-positive fix)."""
    ps1 = RUNNER.read_text(encoding="utf-8")
    block = ps1.split("$StageTable = @(", 1)[1].split("\n)", 1)[0]
    out: dict[str, list[str]] = {}
    for m in re.finditer(r"N='([0-9]{2}_[a-z0-9_]+)'.*?Art=@\((.*?)\)\s*\}", block, re.S):
        vals = []
        for single, double in re.findall(r"'([^']+)'|\"([^\"]+)\"", m.group(2)):
            vals.append((single or double).replace("${ArtPrefix}", art_prefix))
        out[m.group(1)] = vals
    return out


class Harness:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.required = _required_checks()
        self.art = _stage_art()
        assert len(self.required) == len(self.art) == 16
        self.sentinel = tmp_path / "sentinel.txt"
        self.acc_log = tmp_path / "accept.log"
        self.evidence = tmp_path / "evidence"
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.repo = tmp_path / "repo"
        for rel in (
            "examples/test-counter/rtl/test_counter.v",
            "examples/test-counter/rtl/test_counter.sdc",
            "examples/soc_ctrl/rtl/soc_ctrl.v",
            "examples/soc_ctrl/rtl/soc_ctrl.sdc",
        ):
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("// SIMULATED design input for real-runner integration\n", encoding="utf-8")
        # The runner records git provenance from REG_REPO; use an isolated git
        # repo so no real repository (especially D:\audit\repo) is touched.
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run([
            "git", "-C", str(self.repo),
            "-c", "user.email=simulated@example.invalid",
            "-c", "user.name=SIMULATED", "commit", "-q", "-m", "simulated inputs",
        ], check=True)

        stage_py = tmp_path / "fake_stage.py"
        stage_py.write_text(textwrap.dedent("""\
            import json, os, sys
            name, kind, log = sys.argv[1], sys.argv[2], sys.argv[3]
            if log.startswith('/mnt/'):
                parts = log.split('/', 3)
                log = parts[2].upper() + ':\\\\' + parts[3].replace('/', '\\\\')
            # Sentinel is the FIRST action: OS-level proof the process launched.
            with open(os.environ['SIM_SENTINEL'], 'a', encoding='utf-8') as f:
                f.write(name + '\\n')
            with open(log, 'a', encoding='utf-8') as f:
                f.write('SIMULATED stage ' + name + '\\n')
            arts = json.loads(os.environ.get('SIM_ARTIFACTS', '{}'))
            run = os.environ['SIM_RUN_DIR']
            # Materialise the runner's DECLARED artifact paths so the resume
            # skip/re-execute decision is exercised instead of short-circuited.
            for rel in arts.get(name, []):
                q = os.path.join(run, rel.replace('/', os.sep))
                os.makedirs(os.path.dirname(q), exist_ok=True)
                with open(q, 'w', encoding='utf-8') as f:
                    f.write('SIMULATED artifact for ' + name + '\\n')
            if name == os.environ.get('SIM_FAIL_STAGE', '__none__'):
                sys.exit(int(os.environ.get('SIM_FAIL_RC', '1')))
            sys.exit(0)
        """), encoding="utf-8")

        acc_py = tmp_path / "fake_acceptance.py"
        acc_py.write_text(textwrap.dedent("""\
            import json, os, sys
            name, out = sys.argv[1], sys.argv[2]
            req = json.loads(os.environ['SIM_REQUIRED_JSON'])
            cfg = json.loads(os.environ.get('SIM_ACC_CFG', '{}'))
            acc_log = os.environ.get('SIM_ACC_LOG')
            if acc_log:
                with open(acc_log, 'a', encoding='utf-8') as f:
                    f.write(name + '\\n')
            if name in cfg.get('skip_write_stages', []):
                sys.exit(0)
            stages = list(req) if name == '__final__' else [name]
            run_id = cfg.get('emit_run_id', os.environ.get('SIM_RUN_ID', ''))
            design = cfg.get('emit_design', os.environ.get('SIM_DESIGN', 'test_counter'))
            per_id = cfg.get('emit_per_check_identity', False)
            per_override = cfg.get('per_check_identity_override', {})
            checks = []
            for st in stages:
                for n in req[st]:
                    if n in cfg.get('omit_checks', []):
                        continue
                    status = 'PASS'
                    for scope in (n, st + '/' + n, '__all__'):
                        if scope in cfg.get('fail_checks', {}):
                            status = cfg['fail_checks'][scope]
                    tag = cfg.get('wrong_stage_tag_value', '00_stage00') if cfg.get('wrong_stage_tag') else st
                    c = {'stage': tag, 'name': n, 'status': status, 'detail': 'SIMULATED'}
                    if per_id:
                        c['run_id'] = per_override.get('run_id', run_id)
                        c['design'] = per_override.get('design', design)
                    checks.append(c)
            if name == '__final__':
                for extra in cfg.get('extra_final_checks', []):
                    checks.append(dict(extra))
            if name in cfg.get('malformed_stages', []):
                with open(out, 'w', encoding='utf-8') as f:
                    f.write('{ this is deliberately invalid json ')
                sys.exit(0)
            with open(out, 'w', encoding='utf-8') as f:
                json.dump({'checks': checks, 'gate_schema': 'simulated-third-review',
                           'run_id': run_id, 'design': design}, f, indent=2)
            if name == '__final__':
                if 'final_rc' in cfg:
                    sys.exit(int(cfg['final_rc']))
                if cfg.get('final_fail_rc0'):
                    sys.exit(0)
            sys.exit(1 if any(c['status'] != 'PASS' for c in checks) else 0)
        """), encoding="utf-8")

        man_py = tmp_path / "fake_manifest.py"
        man_py.write_text(textwrap.dedent("""\
            import json, os, sys
            a = sys.argv[1:]
            cfg = json.loads(os.environ.get('SIM_MANIFEST_CFG', '{}'))
            cmd = a[0] if a else ''
            if cmd in cfg.get('fail_cmds', []):
                sys.exit(int(cfg.get('fail_rc', 1)))
            sys.exit(0)
        """), encoding="utf-8")
        self.stage_cmd = self._cmd("fake_stage.cmd", stage_py)
        self.acc_cmd = self._cmd("fake_acceptance.cmd", acc_py)
        self.man_cmd = self._cmd("fake_manifest.cmd", man_py)

    def _cmd(self, name: str, script: Path) -> Path:
        p = self.tmp / name
        p.write_text(f'@echo off\n"{PY}" "{script}" %*\n', encoding="utf-8")
        return p

    def run(self, run_id: str, *, run_dir: Path | None = None, extra_args=None,
            overrides=None, accept_cfg=None, manifest_cfg=None, additional_env=None) -> subprocess.CompletedProcess:
        run_dir = run_dir or (self.tmp / "ws" / f"run_{run_id}")
        cfg = dict(accept_cfg or {})
        if overrides:
            cfg["fail_checks"] = {**cfg.get("fail_checks", {}), **overrides}
        env = os.environ.copy()
        env.update({
            "SIM_SENTINEL": str(self.sentinel),
            "SIM_ACC_LOG": str(self.acc_log),
            "SIM_RUN_DIR": str(run_dir),
            "SIM_RUN_ID": run_id,
            "SIM_DESIGN": "test_counter",
            "SIM_REQUIRED_JSON": json.dumps(self.required),
            "SIM_ARTIFACTS": json.dumps(self.art),
            "SIM_ACC_CFG": json.dumps(cfg),
            "SIM_MANIFEST_CFG": json.dumps(manifest_cfg or {}),
            "REG_REPO": str(self.repo),
            "REG_PYTHON": PY,
        })
        env.update(additional_env or {})
        args = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(RUNNER),
            "-RunId", run_id, "-Workspace", str(self.tmp / "ws"), "-RunDir", str(run_dir),
            "-SkipToolProbe", "-EvidenceDirOverride", str(self.evidence),
            "-StageExecutor", str(self.stage_cmd),
            "-AcceptanceExecutor", str(self.acc_cmd),
            "-ManifestExecutor", str(self.man_cmd),
        ]
        args.extend(extra_args or [])
        return subprocess.run(args, cwd=REPO, env=env, capture_output=True,
                              text=True, timeout=300)

    def report(self, run_dir: Path) -> dict:
        return json.loads((run_dir / "report.json").read_text(encoding="utf-8"))

    def launched(self) -> list[str]:
        if not self.sentinel.exists():
            return []
        return self.sentinel.read_text(encoding="utf-8").splitlines()

    def accept_invoked(self) -> list[str]:
        if not self.acc_log.exists():
            return []
        return self.acc_log.read_text(encoding="utf-8").splitlines()


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path)


# --------------------------------------------------------------------------- baseline gates

def test_real_runner_full_pass_and_final_report(harness):
    run_dir = harness.tmp / "ws" / "run_pass"
    cp = harness.run("pass", run_dir=run_dir)
    assert cp.returncode == 0, cp.stdout[-3000:]
    r = harness.report(run_dir)
    assert r["overall"] == "PASS"
    assert r["qualification"] == "QUALIFIED_PASS"
    assert len(r["stages"]) == len(harness.required) == 16
    assert set(r["gate_policy"]["stage_deps"]) == set(harness.required)
    assert all(s["qualification_status"] == "QUALIFIED_PASS" for s in r["stages"])
    assert r.get("final_acceptance_failures", []) == []
    # OS-level launch record, in exact dependency order.
    assert harness.launched() == [s["name"] for s in r["stages"]]
    assert harness.accept_invoked()[-1] == "__final__"
    assert (run_dir / "summary.md").exists()
    assert (run_dir / "gate_ledger.json").exists()


def test_zero_exit_with_engineering_failure_blocks_consumer(harness):
    check = harness.required["01_synthesis"][0]
    run_dir = harness.tmp / "ws" / "run_eng_fail"
    cp = harness.run("eng_fail", run_dir=run_dir, overrides={check: "FAIL"})
    assert cp.returncode != 0
    r = harness.report(run_dir)
    stages = {s["name"]: s for s in r["stages"]}
    assert stages["01_synthesis"]["exit_code"] == 0  # tool succeeded
    assert stages["01_synthesis"]["engineering_status"] == "FAIL"
    assert stages["02_lec_generic"]["process_status"] == "NOT_RUN"
    launched = harness.launched()
    assert "01_synthesis" in launched and "02_lec_generic" not in launched


def test_nonzero_tool_exit_blocks_downstream(harness):
    run_dir = harness.tmp / "ws" / "run_rc"
    cp = harness.run("rc", run_dir=run_dir,
                     additional_env={"SIM_FAIL_STAGE": "01_synthesis", "SIM_FAIL_RC": "7"})
    assert cp.returncode != 0
    r = harness.report(run_dir)
    stages = {s["name"]: s for s in r["stages"]}
    assert stages["01_synthesis"]["process_status"] == "FAILED"
    assert stages["01_synthesis"]["exit_code"] == 7
    assert stages["02_lec_generic"]["process_status"] == "NOT_RUN"
    assert "02_lec_generic" not in harness.launched()


def test_missing_acceptance_output_is_inconclusive_and_blocks(harness):
    run_dir = harness.tmp / "ws" / "run_noacc"
    cp = harness.run("noacc", run_dir=run_dir,
                     accept_cfg={"skip_write_stages": ["00_stage00"]})
    assert cp.returncode != 0
    r = harness.report(run_dir)
    stages = {s["name"]: s for s in r["stages"]}
    assert stages["00_stage00"]["engineering_status"] == "INCONCLUSIVE"
    assert stages["01_synthesis"]["process_status"] == "NOT_RUN"
    assert "01_synthesis" not in harness.launched()


def test_absent_mandatory_check_is_inconclusive(harness):
    missing = harness.required["00_stage00"][0]
    run_dir = harness.tmp / "ws" / "run_missing"
    cp = harness.run("missing", run_dir=run_dir, accept_cfg={"omit_checks": [missing]})
    assert cp.returncode != 0
    r = harness.report(run_dir)
    stages = {s["name"]: s for s in r["stages"]}
    assert stages["00_stage00"]["engineering_status"] == "INCONCLUSIVE"
    assert any(c["status"] == "NOT_TESTED" for c in stages["00_stage00"]["checks"])
    assert "01_synthesis" not in harness.launched()


def test_evidence_persistence_failure_downgrades_qualification(harness):
    run_dir = harness.tmp / "ws" / "run_persist"
    cp = harness.run("persist", run_dir=run_dir,
                     manifest_cfg={"fail_cmds": ["upsert-stage"]})
    assert cp.returncode != 0
    r = harness.report(run_dir)
    stages = {s["name"]: s for s in r["stages"]}
    assert stages["00_stage00"]["qualification_status"] != "QUALIFIED_PASS"
    assert stages["01_synthesis"]["process_status"] == "NOT_RUN"
    assert "01_synthesis" not in harness.launched()


def test_partial_stage_run_requires_prerequisites(harness):
    run_dir = harness.tmp / "ws" / "run_partial"
    cp = harness.run("partial", run_dir=run_dir,
                     extra_args=["-Stages", "02_lec_generic"])
    assert cp.returncode != 0
    r = harness.report(run_dir)
    stages = {s["name"]: s for s in r["stages"]}
    assert stages["02_lec_generic"]["process_status"] == "NOT_RUN"
    assert stages["02_lec_generic"].get("block_reasons")
    assert "02_lec_generic" not in harness.launched()


def test_wrong_stage_tag_acceptance_is_rejected(harness):
    cp = harness.run("wrong_tag", accept_cfg={"wrong_stage_tag": True})
    assert cp.returncode != 0
    r = harness.report(Path(cp.args[cp.args.index("-RunDir") + 1]))
    stages = {s["name"]: s for s in r["stages"]}
    assert stages["01_synthesis"]["engineering_status"] == "INCONCLUSIVE"
    # The tool for a stage launches before that stage's own acceptance is
    # evaluated; the first consumer (02) must nevertheless never launch.
    assert harness.launched() == ["00_stage00", "01_synthesis"]


# --------------------------------------------------------------------------- F-14 ValidateOnly

def test_validate_only_clean_validates_entire_graph_without_execution(harness):
    run_dir = harness.tmp / "ws" / "run_vo_clean"
    assert harness.run("vo_clean_seed", run_dir=run_dir).returncode == 0
    launched_before = len(harness.launched())
    acc_before = len(harness.accept_invoked())
    cp = harness.run("vo_clean", run_dir=run_dir, extra_args=["-ValidateOnly"])
    assert cp.returncode == 0, cp.stdout[-3000:]
    r = harness.report(run_dir)
    assert r["overall"] == "VALIDATION_PASS"
    assert r["qualification"] == "VALIDATION_PASS"
    assert r["process_completion"] == "NOT_RUN_VALIDATION_ONLY"
    assert r["not_executed"] == []
    assert all(s["qualification_status"] == "VALIDATION_PASS" for s in r["stages"])
    assert all(s["process_status"] == "VALIDATION_ONLY" for s in r["stages"])
    # No stage tool process may launch in validation mode.
    assert harness.launched()[launched_before:] == []
    # But EVERY stage's acceptance checks must be freshly evaluated.
    assert set(harness.accept_invoked()[acc_before:]) == set(harness.required) | {"__final__"}
    assert (run_dir / "validation_status.json").exists()


def test_validate_only_observes_new_failure_without_execution(harness):
    run_dir = harness.tmp / "ws" / "run_vo"
    assert harness.run("vo_seed", run_dir=run_dir).returncode == 0
    launched_before = len(harness.launched())
    acc_before = len(harness.accept_invoked())
    check = harness.required["12_sta"][0]
    cp = harness.run("vo", run_dir=run_dir, extra_args=["-ValidateOnly"],
                     accept_cfg={"fail_checks": {f"12_sta/{check}": "FAIL"},
                                 "final_fail_rc0": True})
    assert cp.returncode != 0
    # Sentinel proof: no tool process launched, but stage 12 WAS validated.
    assert harness.launched()[launched_before:] == []
    assert "12_sta" in harness.accept_invoked()[acc_before:]
    r = harness.report(run_dir)
    stages = {s["name"]: s for s in r["stages"]}
    assert stages["12_sta"]["engineering_status"] == "FAIL"
    assert stages["12_sta"]["qualification_status"] != "VALIDATION_PASS"
    assert r["overall"] != "PASS"
    assert any("12_sta" in m for m in r.get("final_acceptance_failures", []))
    assert (run_dir / "validation_status.json").exists()


# --------------------------------------------------------------------------- F-15 resume invalidation

def test_unchanged_resume_skips_every_stage(harness):
    """Control: declared-path artifacts must make the real skip decision work."""
    run_dir = harness.tmp / "ws" / "run_resume_ctrl"
    assert harness.run("resume_ctrl_seed", run_dir=run_dir).returncode == 0
    before = len(harness.launched())
    cp = harness.run("resume_ctrl", run_dir=run_dir, extra_args=["-Resume"])
    assert cp.returncode == 0, cp.stdout[-3000:]
    assert harness.launched()[before:] == []
    r = harness.report(run_dir)
    assert all(s["skipped"] for s in r["stages"])


def test_changed_input_invalidates_descendant_on_resume(harness):
    run_dir = harness.tmp / "ws" / "run_resume"
    assert harness.run("resume_seed", run_dir=run_dir).returncode == 0
    before = len(harness.launched())
    rtl = harness.repo / "examples/test-counter/rtl/test_counter.v"
    rtl.write_text(rtl.read_text(encoding="utf-8") + "// SIMULATED RTL change after qualification\n",
                   encoding="utf-8")
    cp = harness.run("resume", run_dir=run_dir, extra_args=["-Resume"])
    assert cp.returncode == 0, cp.stdout[-3000:]
    # 12_sta..15_lvs do not carry the RTL tag; they must still re-execute.
    assert harness.launched()[before:] == list(harness.required)
    r = harness.report(run_dir)
    stages = {s["name"]: s for s in r["stages"]}
    for name in ("12_sta", "13_gds", "14_drc", "15_lvs"):
        assert not stages[name]["skipped"]
        assert any("invalidated by ancestor" in x for x in stages[name]["resume_reasons"])
    assert "invalidated stages (re-execution required)" in cp.stdout
    invalidation_line = cp.stdout.split("invalidated stages (re-execution required):", 1)[1]
    assert "12_sta" in invalidation_line and "15_lvs" in invalidation_line


def test_changed_sdc_invalidates_downstream_stages_on_resume(harness):
    run_dir = harness.tmp / "ws" / "run_resume_sdc"
    assert harness.run("resume_sdc_seed", run_dir=run_dir).returncode == 0
    before = len(harness.launched())
    sdc = harness.repo / "examples/test-counter/rtl/test_counter.sdc"
    sdc.write_text(sdc.read_text(encoding="utf-8") + "# SIMULATED SDC change\n", encoding="utf-8")
    cp = harness.run("resume_sdc", run_dir=run_dir, extra_args=["-Resume"])
    assert cp.returncode == 0, cp.stdout[-3000:]
    assert harness.launched()[before:] == list(harness.required)
    r = harness.report(run_dir)
    stages = {s["name"]: s for s in r["stages"]}
    for name in ("13_gds", "14_drc", "15_lvs"):
        assert not stages[name]["skipped"]


def test_tampered_ledger_entry_forces_its_stage_to_rerun(harness):
    run_dir = harness.tmp / "ws" / "run_tamper"
    assert harness.run("tamper_seed", run_dir=run_dir).returncode == 0
    before = len(harness.launched())
    ledger_path = run_dir / "gate_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["stages"]["15_lvs"]["qualification"] = "NOT_QUALIFIED"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    cp = harness.run("tamper", run_dir=run_dir, extra_args=["-Resume"])
    assert cp.returncode == 0, cp.stdout[-3000:]
    assert harness.launched()[before:] == ["15_lvs"]


# --------------------------------------------------------------------------- F-25 / F-26 final acceptance

def test_final_acceptance_fail_check_blocks_pass_even_with_zero_exit(harness):
    cp = harness.run("final_fail", accept_cfg={
        "extra_final_checks": [{"stage": "01_synthesis", "name": "final_sentinel_fail",
                                "status": "FAIL", "detail": "SIMULATED-FINAL-FAIL"}],
        "final_fail_rc0": True,
    })
    assert cp.returncode != 0
    r = harness.report(Path(cp.args[cp.args.index("-RunDir") + 1]))
    assert r["overall"] == "FAIL"
    final = r.get("final_acceptance_failures", [])
    assert any("final_sentinel_fail" in m for m in final)
    assert any("final_sentinel_fail" in m for m in r["failure_messages"])


def test_stale_final_acceptance_is_deleted_and_blocks_when_not_regenerated(harness):
    run_dir = harness.tmp / "ws" / "run_stale_final"
    assert harness.run("stale_seed", run_dir=run_dir).returncode == 0
    fp = run_dir / "acceptance.json"
    fp.write_text(fp.read_text(encoding="utf-8") + "\n/* SIMULATED STALE MARKER */\n",
                  encoding="utf-8")
    cp = harness.run("stale", run_dir=run_dir,
                     accept_cfg={"skip_write_stages": ["__final__"]})
    assert cp.returncode != 0
    assert not fp.exists() or "SIMULATED STALE MARKER" not in fp.read_text(encoding="utf-8")
    r = harness.report(run_dir)
    assert r["overall"] != "PASS"
    assert any("final acceptance" in p for p in r["evidence_persistence_failures"])


def test_acceptance_run_and_design_binding_is_enforced(harness):
    cp = harness.run("wrong_identity", accept_cfg={
        "emit_run_id": "SOME-OTHER-RUN", "emit_design": "soc_ctrl",
    })
    assert cp.returncode != 0
    r = harness.report(Path(cp.args[cp.args.index("-RunDir") + 1]))
    assert r["overall"] != "PASS"
    assert any("run/design binding" in p for p in r["evidence_persistence_failures"])


def test_per_check_run_identity_mismatch_is_rejected(harness):
    # Top-level identity matches, but every check carries a different design.
    cp = harness.run("wrong_check_identity", accept_cfg={
        "emit_per_check_identity": True,
        "per_check_identity_override": {"design": "soc_ctrl"},
    })
    assert cp.returncode != 0
    r = harness.report(Path(cp.args[cp.args.index("-RunDir") + 1]))
    assert r["overall"] != "PASS"
    assert any("run/design binding" in p for p in r["evidence_persistence_failures"])


def test_missing_final_acceptance_blocks_pass(harness):
    cp = harness.run("final_missing", accept_cfg={"skip_write_stages": ["__final__"]})
    assert cp.returncode != 0
    r = harness.report(Path(cp.args[cp.args.index("-RunDir") + 1]))
    assert r["overall"] == "FAIL"
    assert any("final acceptance" in p for p in r["evidence_persistence_failures"])


def test_final_acceptance_nonzero_exit_blocks_pass(harness):
    cp = harness.run("final_rc1", accept_cfg={"final_rc": 1})
    assert cp.returncode != 0
    r = harness.report(Path(cp.args[cp.args.index("-RunDir") + 1]))
    assert r["overall"] == "FAIL"
