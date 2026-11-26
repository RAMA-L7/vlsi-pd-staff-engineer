"""Round 5 / gate-definition trust boundary (Agent 8, A7-03).

The authoritative ``gate_definitions`` store added in R4 had no integrity
binding: editing ``state.json`` to set
``gate_definitions["UP"].required_checks = ["sta_down"]`` re-qualified a
simulated upstream, producing CLI ``gate-evaluate`` rc=0 and
``can_enter_stage == (True, [])``.

R5 binds each gate definition with an HMAC whose key is resolved from OUTSIDE
the ledger (``ASSISTANT_LEDGER_KEY``) or, when no key is provisioned, from a
per-process ephemeral secret that is never persisted. A state-file writer
cannot recompute the binding, so a forged/unbound definition is not admitted
as authoritative and upstream re-evaluation fails closed.

The ``test_exploit_*`` tests below fail on the vulnerable baseline and pass
after the fix. The ``test_guard_*`` tests pass on both and protect legitimate
default-spec, tool-PASS, validated N/A and provisioned-restart flows. EXT-05
tightens the waiver guard: WAIVED_PASS stays visible (reporting) but never
confers production authorization.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import assistant.controller as ctrl
from assistant.cli import EXIT_OK, EXIT_VALIDATION_FAILED
from assistant.controller import GateController, Waiver
from assistant.evidence import Evidence
from assistant.statuses import Status

REPO_ROOT = Path(__file__).resolve().parents[1]
SIM_LABEL = "R5-gate-trust: caller-labelled simulated (no real tool provenance)"

# Guard the imports so this module also RUNS (and visibly fails) on the
# un-remediated baseline, where the new API does not exist yet.
LEDGER_KEY_ENV = getattr(ctrl, "LEDGER_KEY_ENV", "ASSISTANT_LEDGER_KEY")
DOMAIN_GATE_DEFINITION = getattr(ctrl, "DOMAIN_GATE_DEFINITION", "gate_definition/v1")
LedgerIntegrity = getattr(ctrl, "LedgerIntegrity", None)
read_binding = getattr(ctrl, "read_binding", None)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _report(tmp_path: Path, name: str) -> str:
    p = tmp_path / f"{name}.rpt"
    p.write_text("slack 0.10 ns\n", encoding="utf-8")
    return str(p)


def _genuine_pass(tmp_path: Path, check_id: str) -> Evidence:
    ev = Evidence.from_run(
        check_id=check_id, status=Status.PASS, reason="meets target",
        command=[check_id, "--run-real-tool"], exit_code=0,
        analysis_view="ROUTED_EXTRACTED", tool_versions={check_id: "1.0"},
        reports=[_report(tmp_path, check_id)],
    )
    # R6.2: attach a REAL TRUSTED proof from the managed boundary so this test
    # keeps its original security purpose (MAC binding, provenance, downstream).
    try:
        import r62_proof_helper as _ph
        _d, _cmd = _ph.proof_and_command(check_id=check_id)
        if _d is not None:
            ev.execution_proof = _d
            object.__setattr__(ev, "command", list(_cmd))
    except Exception:
        pass
    return ev


def _sim_pass(check_id: str) -> Evidence:
    return Evidence.from_run(
        check_id=check_id, status=Status.PASS, reason="meets target",
        exit_code=0, analysis_view="ROUTED_EXTRACTED",
    )


def _seed_up_down(tmp_path: Path):
    """UP is authorized only by a simulated PASS; DOWN has a genuine PASS and a
    genuine ``sta_down`` record exists as the substitution candidate (A3-F1)."""
    state = tmp_path / "state.json"
    c = GateController(str(state))
    c.record_evidence(_sim_pass("sta_up"), strict=False, simulated=SIM_LABEL)
    c.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    up = c.evaluate_gate("UP", ["sta_up"])
    assert up.disposition == "SIMULATED_PASS" and up.qualified is False
    return c, state


def _forge_up(state: Path) -> None:
    """A7-03 vector: rewrite ONLY the authoritative gate_definitions store."""
    raw = json.loads(state.read_text(encoding="utf-8"))
    raw["gate_definitions"]["UP"] = {
        "required_checks": ["sta_down"],
        "required_view": None,
        "upstream": [],
    }
    state.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")


def _run_cli(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "assistant", *[str(a) for a in args]],
        cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=120, env=env,
    )


def _no_key_env() -> dict:
    return {k: v for k, v in os.environ.items() if k != LEDGER_KEY_ENV}


def _collect_strings(obj):
    out: list[str] = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_collect_strings(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_collect_strings(v))
    return out


def _candidate_keys(text: str):
    """Every plausible key an attacker could extract from the ledger."""
    keys = []
    try:
        b = bytes.fromhex(text)
        if len(b) >= 16:
            keys.append(b)
    except ValueError:
        pass
    raw = text.encode("utf-8")
    if len(raw) >= 16:
        keys.append(raw)
    keys.append(hashlib.sha256(b"assistant-ledger-key/v1\x00" + raw).digest())
    return keys


def _fresh_process(monkeypatch, *, key: str | None = None):
    """Simulate a brand-new process: drop env key and reset the cached
    per-process ephemeral key."""
    if key is None:
        monkeypatch.delenv(LEDGER_KEY_ENV, raising=False)
    else:
        monkeypatch.setenv(LEDGER_KEY_ENV, key)
    monkeypatch.setattr(ctrl, "_EPHEMERAL_LEDGER_KEY", None, raising=False)


# ===========================================================================
# exploit tests -- fail on baseline, pass after fix
# ===========================================================================
def test_exploit_forged_gate_definition_cannot_requalify_upstream_api(tmp_path):
    _, state = _seed_up_down(tmp_path)
    _forge_up(state)

    gd = GateController(str(state)).evaluate_gate(
        "DOWN", ["sta_down"], upstream_gates=["UP"]
    )
    assert gd.qualified is False, (
        "EXPLOIT: forged gate_definitions re-qualified simulated upstream: "
        f"{gd.disposition}/{gd.qualified}"
    )
    assert gd.disposition == "BLOCKED"
    up_check = gd.checks.get("upstream:UP", {})
    assert up_check.get("status") == "BLOCKED"
    assert up_check.get("qualified") is False


def test_exploit_forged_gate_definition_cannot_requalify_upstream_cli(tmp_path):
    _, state = _seed_up_down(tmp_path)
    _forge_up(state)

    proc = _run_cli(
        "gate-evaluate", "--state", str(state), "--gate", "DOWN",
        "--required", "sta_down", "--upstream", "UP",
    )
    assert proc.returncode == EXIT_VALIDATION_FAILED, (
        f"EXPLOIT: CLI authorized forged definition (rc={proc.returncode}): "
        f"{proc.stdout}"
    )
    data = json.loads(proc.stdout)
    assert data["qualified"] is False
    assert data["disposition"] == "BLOCKED"


def test_exploit_forged_gate_definition_cannot_enter_stage(tmp_path):
    _, state = _seed_up_down(tmp_path)
    _forge_up(state)

    ok, reasons = GateController(str(state)).can_enter_stage(
        "production", [("DOWN", ["sta_down"])], upstream=["UP"]
    )
    assert ok is False, "EXPLOIT: forged definition authorized production stage"
    assert reasons


def test_exploit_forged_definition_cannot_replay_another_records_mac(tmp_path):
    """Binding includes record_id: copying DOWN's valid MAC onto a forged UP
    definition must not validate (cross-record replay)."""
    ctl, state = _seed_up_down(tmp_path)
    ctl.evaluate_gate("DOWN", ["sta_down"])  # create a second bound definition
    raw = json.loads(state.read_text(encoding="utf-8"))
    forged = dict(raw["gate_definitions"]["DOWN"])  # identical body
    down_mac = None
    if LedgerIntegrity is not None:
        bindings = raw.get("ledger_bindings", {}).get(DOMAIN_GATE_DEFINITION, {})
        down_mac = bindings.get("DOWN")
    raw["gate_definitions"]["UP"] = forged
    if down_mac:
        raw.setdefault("ledger_bindings", {}).setdefault(
            DOMAIN_GATE_DEFINITION, {})["UP"] = down_mac
    state.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")

    gd = GateController(str(state)).evaluate_gate(
        "DOWN", ["sta_down"], upstream_gates=["UP"]
    )
    assert gd.qualified is False, "EXPLOIT: replayed MAC re-qualified upstream"
    assert gd.disposition == "BLOCKED"


def test_exploit_ledger_key_cannot_be_extracted_and_replayed(tmp_path):
    """Every key-like string in the ledger is tried to recompute a valid MAC
    for a forged definition. None can work: the real key is external."""
    _, state = _seed_up_down(tmp_path)
    baseline = json.loads(state.read_text(encoding="utf-8"))
    forged = {"required_checks": ["sta_down"], "required_view": None, "upstream": []}
    strings = _collect_strings(baseline)
    assert strings

    attempted = 0
    for text in strings:
        for key in _candidate_keys(text):
            if LedgerIntegrity is None:
                pytest.fail("baseline: no LedgerIntegrity primitive to exercise")
            mac = LedgerIntegrity(key).mac(
                DOMAIN_GATE_DEFINITION, forged, record_id="UP"
            )
            attack = json.loads(state.read_text(encoding="utf-8"))
            attack["gate_definitions"]["UP"] = forged
            attack.setdefault("ledger_bindings", {}).setdefault(
                DOMAIN_GATE_DEFINITION, {})["UP"] = mac
            state.write_text(json.dumps(attack, sort_keys=True), encoding="utf-8")

            gd = GateController(str(state)).evaluate_gate(
                "DOWN", ["sta_down"], upstream_gates=["UP"]
            )
            assert gd.qualified is False, (
                "EXPLOIT: a key extracted from the ledger re-qualified upstream"
            )
            attempted += 1
    assert attempted > 0


def test_exploit_missing_key_fails_closed_on_restart(tmp_path, monkeypatch):
    """A ledger written under a provisioned key cannot be trusted by a fresh
    process that has no key: bindings are unverifiable and authorization
    fails closed."""
    monkeypatch.setenv(LEDGER_KEY_ENV, "r5-provisioned-key-A")
    _fresh_process(monkeypatch, key="r5-provisioned-key-A")
    state = tmp_path / "state.json"
    c = GateController(str(state))
    c.record_evidence(_sim_pass("sta_up"), strict=False, simulated=SIM_LABEL)
    c.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    assert c.evaluate_gate("UP", ["sta_up"]).qualified is False

    # Fresh process, NO provisioned key and a fresh ephemeral key.
    _fresh_process(monkeypatch)
    c2 = GateController(str(state))
    assert c2.ledger_key_source == "ephemeral"
    assert "UP" in c2.unverifiable_gate_definitions
    assert f"{DOMAIN_GATE_DEFINITION}:UP" in c2.verify_ledger_integrity()

    gd = c2.evaluate_gate("DOWN", ["sta_down"], upstream_gates=["UP"])
    assert gd.qualified is False and gd.disposition == "BLOCKED"


def test_exploit_restart_without_key_denies_even_genuine_upstream(tmp_path, monkeypatch):
    """Fail-closed is intentional even for a genuinely qualified upstream: a
    new process without the external key cannot verify its definition."""
    _fresh_process(monkeypatch, key="r5-provisioned-key-B")
    state = tmp_path / "state.json"
    c = GateController(str(state))
    c.record_evidence(_genuine_pass(tmp_path, "sta_up"))
    c.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    assert c.evaluate_gate("UP", ["sta_up"]).qualified is True

    _fresh_process(monkeypatch)
    c2 = GateController(str(state))
    gd = c2.evaluate_gate("DOWN", ["sta_down"], upstream_gates=["UP"])
    assert gd.qualified is False and gd.disposition == "BLOCKED"
    assert "UP" in c2.unverifiable_gate_definitions


def test_exploit_cli_without_key_fails_closed_on_bound_upstream(tmp_path, monkeypatch):
    """The reachable CLI path also fails closed when restarted without the
    provisioned key, even without any tampering."""
    _fresh_process(monkeypatch, key="r5-provisioned-key-C")
    state = tmp_path / "state.json"
    c = GateController(str(state))
    c.record_evidence(_genuine_pass(tmp_path, "sta_up"))
    c.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    assert c.evaluate_gate("UP", ["sta_up"]).qualified is True

    proc = _run_cli(
        "gate-evaluate", "--state", str(state), "--gate", "DOWN",
        "--required", "sta_down", "--upstream", "UP", env=_no_key_env(),
    )
    assert proc.returncode == EXIT_VALIDATION_FAILED, (
        f"EXPLOIT: CLI without key authorized bound upstream: {proc.stdout}"
    )
    assert json.loads(proc.stdout)["qualified"] is False


def test_exploit_strict_key_resolution_raises_when_unprovisioned(monkeypatch):
    """Production callers can demand a real external trust anchor."""
    resolve = getattr(ctrl, "resolve_ledger_key", None)
    if resolve is None:
        pytest.fail("baseline: no external key resolver")
    _fresh_process(monkeypatch)
    with pytest.raises(ctrl.LedgerIntegrityError):
        resolve(allow_ephemeral=False)


def test_exploit_primitive_is_domain_and_record_bound():
    """MAC primitive contract: key/domain/record separation (Agent 9 reuse)."""
    if LedgerIntegrity is None:
        pytest.fail("baseline: no LedgerIntegrity primitive")
    a = LedgerIntegrity(b"0" * 32)
    b = LedgerIntegrity(b"1" * 32)
    payload = {"required_checks": ["sta"], "upstream": []}
    m1 = a.mac("domain/one", payload, record_id="G1")
    assert a.verify("domain/one", payload, m1, record_id="G1")
    assert not b.verify("domain/one", payload, m1, record_id="G1")
    assert not a.verify("domain/two", payload, m1, record_id="G1")
    assert not a.verify("domain/one", payload, m1, record_id="G2")
    assert not a.verify("domain/one", None, m1, record_id="G1")
    assert not a.verify("domain/one", payload, None, record_id="G1")


# ===========================================================================
# guard tests -- pass on both baseline and fixed code
# ===========================================================================
def test_guard_fresh_ledger_without_key_still_works(tmp_path, monkeypatch):
    _fresh_process(monkeypatch)
    state = tmp_path / "state.json"
    c = GateController(str(state))
    c.record_evidence(_genuine_pass(tmp_path, "sta"))
    gd = c.evaluate_gate("G", ["sta"])
    assert gd.qualified is True and gd.disposition == "CLEAN_PASS"


def test_guard_restart_with_provisioned_key_preserves_authorization(
    tmp_path, monkeypatch
):
    _fresh_process(monkeypatch, key="r5-provisioned-key-D")
    state = tmp_path / "state.json"
    c = GateController(str(state))
    c.record_evidence(_genuine_pass(tmp_path, "sta_up"))
    c.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    assert c.evaluate_gate("UP", ["sta_up"]).qualified is True

    c2 = GateController(str(state))  # same provisioned key => restart works
    assert c2.ledger_key_source == "environment"
    assert c2.verify_ledger_integrity() == []
    gd = c2.evaluate_gate("DOWN", ["sta_down"], upstream_gates=["UP"])
    assert gd.qualified is True and gd.disposition == "CLEAN_PASS"


def test_guard_cli_with_provisioned_key_authorizes(tmp_path, monkeypatch):
    """The reachable CLI path also works across processes when the key is
    provisioned: genuine upstream definition verifies, so DOWN is qualified."""
    _fresh_process(monkeypatch, key="r5-provisioned-key-E")
    state = tmp_path / "state.json"
    c = GateController(str(state))
    c.record_evidence(_genuine_pass(tmp_path, "sta_up"))
    c.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    assert c.evaluate_gate("UP", ["sta_up"]).qualified is True

    proc = _run_cli(
        "gate-evaluate", "--state", str(state), "--gate", "DOWN",
        "--required", "sta_down", "--upstream", "UP", env=dict(os.environ),
    )
    assert proc.returncode == EXIT_OK, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["qualified"] is True and data["disposition"] == "CLEAN_PASS"


def test_guard_forging_registered_default_spec_has_no_effect(tmp_path):
    """Registered/default specs outrank any gate definition (defense in depth)."""
    state = tmp_path / "state.json"
    c = GateController(str(state))
    from assistant.controller import default_gate_specs
    for spec in default_gate_specs().values():
        c.register_gate(spec)
    raw = json.loads(state.read_text(encoding="utf-8"))
    raw["gate_definitions"]["STA_SIGNOFF"] = {
        "required_checks": ["sta_down"], "required_view": None, "upstream": [],
    }
    state.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
    c2 = GateController(str(state))
    # The forged definition is not authoritative; the spec's check set remains.
    spec, definition = c2._source_of_truth("STA_SIGNOFF")
    assert spec is not None
    assert set(spec.required_checks) == {"sta", "parasitics"}


def test_guard_genuine_tool_pass_authorizes(tmp_path):
    state = tmp_path / "state.json"
    c = GateController(str(state))
    c.record_evidence(_genuine_pass(tmp_path, "sta_up"))
    c.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    assert c.evaluate_gate("UP", ["sta_up"]).disposition == "CLEAN_PASS"
    gd = c.evaluate_gate("DOWN", ["sta_down"], upstream_gates=["UP"])
    assert gd.qualified is True and gd.disposition == "CLEAN_PASS"
    ok, _ = c.can_enter_stage(
        "production", [("DOWN", ["sta_down"])], upstream=["UP"]
    )
    assert ok


def test_guard_waived_gate_never_authorizes(tmp_path):
    """EXT-05: WAIVED_PASS is a reporting state, never production authorization."""
    state = tmp_path / "state.json"
    c = GateController(str(state))
    c.record_evidence(Evidence.from_run(
        "antenna", Status.FAIL, "waived finding", exit_code=0,
        analysis_view="ROUTED_EXTRACTED",
    ), strict=False, simulated="unit-test waived finding")
    c.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    c.add_waiver(Waiver(
        check_id="antenna", scope="UP", reason="documented tool gap in corner",
        approved_by="reviewer", policy="tool_gap",
    ))
    c.record_evidence(_genuine_pass(tmp_path, "up_proof"))
    up = c.evaluate_gate("UP", ["antenna", "up_proof"])
    assert up.disposition == "WAIVED_PASS" and up.passed
    # Even with a second proof-bearing check carrying genuine proof, the waived
    # FAIL must not be laundered into a qualified gate.
    assert up.qualified is False
    assert c.evaluate_gate(
        "DOWN", ["sta_down"], upstream_gates=["UP"]
    ).qualified is False


def test_guard_validated_not_applicable_still_authorizes(tmp_path):
    state = tmp_path / "state.json"
    c = GateController(str(state))
    c.register_design_fact("has_upf", False, source="inventory")
    c.record_evidence(_genuine_pass(tmp_path, "sta_down"))
    c.mark_not_applicable_for_check(
        "upf_check",
        "NA-RULE:NO-UPF design has no UPF/CPF power-intent file in scope",
        design_facts={"has_upf": False},
    )
    # R6.2: proof-bearing check alongside the N/A check.
    c.record_evidence(_genuine_pass(tmp_path, "up_proof"))
    assert c.evaluate_gate("UP", ["upf_check", "up_proof"]).disposition == "NOT_APPLICABLE"
    assert c.evaluate_gate(
        "DOWN", ["sta_down"], upstream_gates=["UP"]
    ).qualified is True
