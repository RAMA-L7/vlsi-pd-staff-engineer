"""Agent 8 (Round 6) independent adversarial regression tests.

These tests encode the security properties from ADR v1.0. Two tests are
EXPECTED TO FAIL on the integrated WORKING_COPY because they expose NEW P0
authorization bypasses found by Agent 8 (see ADVERSARIAL/AGENT8_REPORT.md):

  * test_cancel_during_commit_never_leaves_durable_production_pass
  * test_chr_hidden_wsl_cannot_qualify_production

Do not weaken these assertions. The coordinator decides disposition.
"""
import os
import json
import hashlib
import binascii

import pytest

from assistant.controller import GateController
from assistant.evidence import Evidence
from assistant.statuses import Status
from assistant.r6_authority import (r6_execution_guard, decode_aware_execution_risk)
import assistant.r6_authority as ra
from assistant.eco import (EcoChangeScope, EcoManager, EcoStage, ProtectedMetric,
                           ManagedToolSpec)


PY = r"D:\tools\venv\Scripts\python.exe"


def _gen(cid, path):
    return Evidence.from_run(check_id=cid, status=Status.PASS, reason="genuine tool run",
        command=[cid, "--real-tool"], exit_code=0, analysis_view="ROUTED_EXTRACTED",
        tool_versions={cid: "2026.1"}, reports=[str(path)])


def _reset_trust():
    os.environ.pop("ASSISTANT_LEDGER_KEY", None)
    ra._TRUST = None
    ra._TRUST_KEY = object()


def test_control_genuine_evidence_qualifies(tmp_path):
    _reset_trust()
    rpt = tmp_path / "r.rpt"
    rpt.write_text("slack 0.05 ns\n", encoding="utf-8")
    st = tmp_path / "state.json"
    c = GateController(str(st))
    c.record_evidence(_gen("sta_up", rpt))
    c.record_evidence(_gen("sta_down", rpt))
    gd = c.evaluate_gate("UP", ["sta_up"])
    assert gd.qualified is True
    assert c.verify_ledger_integrity() == []


def test_p01_ledger_forge_cannot_elevate_simulated(tmp_path):
    """Relabel simulated provenance in ledger + guess MAC -> no qualified auth."""
    _reset_trust()
    rpt = tmp_path / "r.rpt"
    rpt.write_text("slack 0.05 ns\n", encoding="utf-8")
    st = tmp_path / "state.json"
    c = GateController(str(st))
    c.record_evidence(Evidence.from_run("sta_up", Status.PASS, reason="sim",
        command=["sim"], exit_code=0, analysis_view="MAPPED_NETLIST",
        tool_versions={"sim": "1"}, reports=[str(rpt)]),
        strict=False, simulated="a8 simulated seed")
    raw = json.loads(st.read_text(encoding="utf-8"))
    assert "_integrity_key" not in raw  # no secret in ledger
    eid = raw["check_index"]["sta_up"][-1]
    raw["evidence"][eid]["evidence_class"] = "TOOL"
    raw["evidence_provenance"][eid]["evidence_class"] = "TOOL"
    raw["evidence_integrity"][eid] = "0" * 64
    st.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
    c2 = GateController(str(st))
    gd = c2.evaluate_gate("UP", ["sta_up"])
    assert gd.qualified is False
    assert c2.verify_ledger_integrity()


def test_p02_deadline_during_commit_is_non_authorizing(tmp_path):
    """R5 P0-2 is fixed: deadline crossing during persistence -> no durable PASS."""
    import assistant.eco as eco_mod
    orig_time = eco_mod.time
    class Clock:
        def __init__(self):
            self.now = 1000.0
            self.n = 0
        def monotonic(self):
            self.n += 1
            return self.now + self.n * 0.001
        def time(self):
            return self.now
        def sleep(self, s):
            self.now += s
        def advance(self, s=1.0):
            self.now += s
    _reset_trust()
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "design.v").write_text("module d; endmodule\n", encoding="utf-8")
    ctl = GateController(str(tmp_path / "state.json"))
    eco = EcoManager(str(ws), ctl, checkpoint_dir=str(tmp_path / "ckpt"),
                     experiment_log=str(tmp_path / "exp.json"))
    clock = Clock(); eco_mod.time = clock
    orig = eco._save_log
    fired = {"v": False}
    def hooked():
        if eco._history and eco._history[-1].get("status") == "PASS" and not fired["v"]:
            fired["v"] = True
            clock.advance(1.0)
        return orig()
    eco._save_log = hooked
    try:
        res = eco.run_experiment(experiment_id="r6p02", description="a8 p02",
            change_class="no_op", changed_files=["design.v"], apply_change=lambda: None,
            evaluate=lambda: {"wns": 1.0},
            protected_metrics=[ProtectedMetric("wns", 0.0)], verify=None,
            verification_tools=None, verification_timeout_s=0.5,
            change_scope=EcoChangeScope("no_op", EcoStage.MAPPED, False, "a8 probe"),
            simulated="", analysis_mode=False)
    finally:
        eco_mod.time = orig_time
    dur = json.loads((tmp_path / "exp.json").read_text(encoding="utf-8"))["experiments"]
    assert fired["v"]
    assert not any(r.get("status") == "PASS" and r.get("qualification_class") == "PRODUCTION"
                   for r in dur)


def test_cancel_during_commit_never_leaves_durable_production_pass(tmp_path):
    """NEW P0 (A8): cancellation inside _save_log leaves a durable PRODUCTION PASS.

    EXPECTED TO FAIL on integrated WORKING_COPY. Root cause: EcoManager._save_log
    commit-time stamping calls guard.expired() (deadline-only) and does not
    check the cancellation latch; the post-commit status check therefore sees
    a PASS and returns it.
    """
    _reset_trust()
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "design.v").write_text("module d; endmodule\n", encoding="utf-8")
    ctl = GateController(str(tmp_path / "state.json"))
    eco = EcoManager(str(ws), ctl, checkpoint_dir=str(tmp_path / "ckpt"),
                     experiment_log=str(tmp_path / "exp.json"))
    orig = eco._save_log
    fired = {"v": False}
    def hooked():
        if eco._history and eco._history[-1].get("status") == "PASS" and not fired["v"]:
            fired["v"] = True
            ctl.cancel("a8 cancel inside persistence")
        return orig()
    eco._save_log = hooked
    res = eco.run_experiment(experiment_id="r6cancel", description="a8 cancel",
        change_class="no_op", changed_files=["design.v"], apply_change=lambda: None,
        evaluate=lambda: {"wns": 1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0)], verify=None,
        verification_tools=None, verification_timeout_s=0.5,
        change_scope=EcoChangeScope("no_op", EcoStage.MAPPED, False, "a8 probe"),
        simulated="", analysis_mode=False)
    dur = json.loads((tmp_path / "exp.json").read_text(encoding="utf-8"))["experiments"]
    assert fired["v"]
    assert not any(r.get("status") == "PASS" and r.get("qualification_class") == "PRODUCTION"
                   for r in dur), ("NEW P0: durable PRODUCTION PASS survived cancel "
                                   "inside persistence: %r" % dur)


def test_chr_hidden_wsl_cannot_qualify_production(tmp_path):
    """NEW P0 (A8): chr()-built 'wsl.exe' evades decode-aware guard and run_managed.

    EXPECTED TO FAIL on integrated WORKING_COPY. The command builds the WSL
    executable name from chr codes (no literal token, no >=40-char hex/base64
    run), so r6_authority.decode_aware_execution_risk returns False and
    runner.run_managed classifies the run clean/authorization_safe. The managed
    verification path then emits a PRODUCTION PASS.
    """
    _reset_trust()
    chr_expr = "''.join(chr(x) for x in [119,115,108,46,101,120,101])"
    cmd = [PY, "-c",
           "import subprocess;subprocess.run([%s,'-d','Ubuntu-24.04','--','/bin/true'])"
           % chr_expr]
    risk, _ = decode_aware_execution_risk(cmd)
    assert risk is True, "decode-aware guard missed chr()-built wsl.exe"

    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "design.v").write_text("module d; endmodule\n", encoding="utf-8")
    ctl = GateController(str(tmp_path / "state.json"))
    eco = EcoManager(str(ws), ctl, checkpoint_dir=str(tmp_path / "ckpt"),
                     experiment_log=str(tmp_path / "exp.json"))
    spec = ManagedToolSpec(check_id="eco_sta", command=tuple(cmd),
        status_on_success="PASS", reason="a8 chr wsl probe", tool_name="sta",
        tool_version="1.0", timeout_s=20)
    res = eco.run_experiment(experiment_id="r6chr", description="a8 chr wsl",
        change_class="no_op", changed_files=["design.v"], apply_change=lambda: None,
        evaluate=lambda: {"wns": 1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0)], verify=None,
        verification_tools=[spec], verification_timeout_s=25,
        change_scope=EcoChangeScope("no_op", EcoStage.MAPPED, False, "a8 probe"),
        simulated="", analysis_mode=False)
    assert not (res.status == "PASS" and res.qualification_class == "PRODUCTION"), (
        "NEW P0: chr-hidden WSL execution qualified PRODUCTION PASS")


def test_hexpy_guard_blocks_phase0_attack():
    """P0-3 baseline hexpy attack is blocked by decode-aware guard."""
    cmd = [PY, "-c",
        "import subprocess,binascii,ast;subprocess.run(ast.literal_eval(binascii.unhexlify('%s').decode()))"
        % binascii.hexlify(repr([r"C:\Windows\System32\wsl.exe", "-d", "Ubuntu-24.04"]).encode()).decode()]
    with pytest.raises(RuntimeError):
        r6_execution_guard(cmd)
