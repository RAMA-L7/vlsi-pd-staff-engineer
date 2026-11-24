"""R6.3 — strict proof-to-evidence binding regressions (Agent 2).

Every case below fails closed when a MAC-valid TRUSTED proof is used to
authorize anything other than the exact execution it was issued for. The
authority is ``state_machine.check_7``; the controller check is defence in
depth. Run:  <PY> -m pytest tests/r63_binding_authority.py -q
"""
from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from pathlib import Path

import pytest

from assistant.controller import GateController, GateValidationError
from assistant.evidence import Evidence
from assistant.r6_authority import (
    _envelope_digest_material, authoritative_qualification, get_trust,
    r6_execution_guard, decode_aware_execution_risk,
)
from assistant.state_machine import derive_authorization
from assistant import execution_trust as et
from r63_binding_helper import trusted_proof_for, reset_trust

BENIGN = [sys.executable, "-c", "print('ok')"]
ATTACK = [sys.executable, "-c", "import subprocess;subprocess.run(['cmd.exe'])"]

_AUTHORIZATION_DOMAIN = "authorization/v1"


@pytest.fixture(autouse=True)
def _fixed_trust(monkeypatch):
    """Stable external key so a fresh controller verifies persisted bindings."""
    monkeypatch.setenv("ASSISTANT_LEDGER_KEY", "r63-binding-test-external-key")
    reset_trust()
    yield
    reset_trust()


def _report(tmp_path: Path) -> str:
    p = tmp_path / (uuid.uuid4().hex + ".rpt")
    p.write_text("slack 0.05 ns\n", encoding="utf-8")
    return str(p)


def _evidence(command, proof, report, *, check_id="eco_sta", consumed=None,
              deps=None, candidate=""):
    ev = Evidence.from_run(
        check_id=check_id, status="PASS", reason="genuine bound execution",
        command=command, exit_code=0, analysis_view="ROUTED_EXTRACTED",
        tool_versions={"sta": "1.0"}, reports=[report],
        input_hashes=dict(consumed or {}), dependencies=dict(deps or {}),
    )
    ev.candidate_digest = candidate
    ev.execution_proof = proof
    return ev


def _record(tmp_path, command, proof, **kwargs):
    state = tmp_path / "state.json"
    ctl = GateController(str(state))
    ev = _evidence(command, proof, _report(tmp_path), **kwargs)
    ctl.record_evidence(ev)
    return ctl, state, ev


def _resign_proof(proof, trust, **overrides):
    d = dict(proof)
    d.update(overrides)
    # execution_digest binds nonce/tool/argv/image/state (not downstream digests)
    if "nonce" in overrides or "argv_sha256" in overrides or "state" in overrides:
        d["execution_digest"] = et.ExecutionBoundary._execution_digest(d)
    return _sign_proof(d, trust)


def _sign_proof(values, trust):
    return et.ExecutionProof(**{
        k: v for k, v in values.items()
        if k in et.ExecutionProof.__dataclass_fields__
    }).sign(trust).to_dict()


def _resign_envelope(env, trust, **overrides):
    from assistant.provenance import Provenance, make_envelope

    d = dict(env)
    d.update(overrides)
    return make_envelope(
        evidence_id=d["evidence_id"], check_id=d["check_id"], gate_id=d["gate_id"],
        provenance=Provenance(d["provenance"]),
        execution_identity=d["execution_identity"],
        consumed_sha256=d["consumed_sha256"], dependencies=d["dependencies"],
        status=d["status"], observed_utc=d["observed_utc"], context=d["context"],
        trust=trust,
    ).to_dict()


def _derive(ctl, eids):
    """Build the same record the authoritative consumer builds, then derive."""
    from assistant.provenance import EvidenceEnvelope

    trust = get_trust()
    stored = ctl._state["r6_evidence_envelopes"]
    envs = [EvidenceEnvelope.from_dict(stored[e], trust).to_dict() for e in eids]
    material = _envelope_digest_material(envs)
    ctx = trust.context
    rec = {
        "transaction_id": "r63", "state": "QUALIFIED_PASS", "complete": True,
        "deadline_utc": "2099-01-01T00:00:00+00:00",
        "context": {"session_id": ctx.session_id, "repo_id": ctx.repo_id,
                    "revision": ctx.revision},
        "evidence": envs,
        "candidate_digest": material["candidate"],
        "consumed_sha256": material["consumed"],
        "dependency_digest": material["dependency_digest"],
        "dependencies": material["dependencies"],
    }
    rec["mac"] = trust.sign(_AUTHORIZATION_DOMAIN, rec)
    current_files = ({"__evidence_consumed__": material["consumed"]}
                     if material["consumed"] else None)
    current_deps = material["dependencies"] or None
    return derive_authorization(rec, trust, current_files=current_files,
                                current_deps=current_deps)


def _envelope(ctl, eid):
    return dict(ctl._state["r6_evidence_envelopes"][eid])


# ---------------------------------------------------------------------------
# Positive control: a correctly bound proof authorizes.
# ---------------------------------------------------------------------------
def test_correctly_bound_proof_authorizes(tmp_path):
    proof = trusted_proof_for(BENIGN)
    ctl, state, ev = _record(tmp_path, BENIGN, proof)
    assert _derive(ctl, [ev.evidence_id]).authorized is True
    assert authoritative_qualification(ctl, [ev.evidence_id]) is True
    # durable + fresh consistency
    fresh = GateController(str(state))
    assert authoritative_qualification(fresh, [ev.evidence_id]) is True


# ---------------------------------------------------------------------------
# 1/2. TRUSTED proof + untrusted command; proof from A + evidence command B.
# ---------------------------------------------------------------------------
def test_trusted_proof_with_untrusted_command_denied(tmp_path):
    proof = trusted_proof_for(BENIGN)  # genuine TRUSTED proof for benign command
    # Normal API must refuse at creation time.
    with pytest.raises(GateValidationError, match="binding"):
        GateController(str(tmp_path / "s.json")).record_check_result(
            "eco_sta", "PASS", "substituted", command=ATTACK,
            execution_proof=proof, analysis_view="ROUTED_EXTRACTED",
            exit_code=0, tool_versions={"sta": "1.0"},
            reports=[_report(tmp_path)],
        )
    # Ledger-only attacker bypasses the API and re-signs an envelope carrying
    # the attack command beside the benign proof: authority must still deny.
    ctl, _, ev = _record(tmp_path, BENIGN, proof)
    trust = get_trust()
    env = _envelope(ctl, ev.evidence_id)
    identity = dict(env["execution_identity"])
    identity["command"] = list(ATTACK)
    ctl._state["r6_evidence_envelopes"][ev.evidence_id] = _resign_envelope(
        env, trust, execution_identity=identity)
    dec = _derive(ctl, [ev.evidence_id])
    assert dec.authorized is False
    assert any("argv_sha256 does not bind" in r for r in dec.reasons), dec.reasons
    assert authoritative_qualification(ctl, [ev.evidence_id]) is False


def test_proof_from_command_a_with_evidence_command_b_denied(tmp_path):
    proof_a = trusted_proof_for(BENIGN)
    ctl, _, ev = _record(tmp_path, BENIGN, proof_a)
    trust = get_trust()
    env = _envelope(ctl, ev.evidence_id)
    identity = dict(env["execution_identity"])
    # Proof issued for command A (BENIGN) but evidence now carries command B
    # (ATTACK): the envelope is re-signed by a ledger-only attacker.
    identity["command"] = list(ATTACK)
    identity["execution_proof"] = proof_a
    ctl._state["r6_evidence_envelopes"][ev.evidence_id] = _resign_envelope(
        env, trust, execution_identity=identity)
    dec = _derive(ctl, [ev.evidence_id])
    assert dec.authorized is False
    assert authoritative_qualification(ctl, [ev.evidence_id]) is False


# ---------------------------------------------------------------------------
# 3. argv substitution (proof argv field swapped, envelope command unchanged).
# ---------------------------------------------------------------------------
def test_argv_substitution_denied(tmp_path):
    proof = trusted_proof_for(BENIGN)
    ctl, _, ev = _record(tmp_path, BENIGN, proof)
    trust = get_trust()
    env = _envelope(ctl, ev.evidence_id)
    identity = dict(env["execution_identity"])
    swapped = dict(identity["execution_proof"])
    swapped["argv_sha256"] = et._sha256_argv(ATTACK)
    identity["execution_proof"] = _sign_proof(swapped, trust)
    ctl._state["r6_evidence_envelopes"][ev.evidence_id] = _resign_envelope(
        env, trust, execution_identity=identity)
    dec = _derive(ctl, [ev.evidence_id])
    assert dec.authorized is False
    assert any("argv_sha256 does not bind" in r for r in dec.reasons), dec.reasons


# ---------------------------------------------------------------------------
# 4. execution-id / nonce substitution and reuse.
# ---------------------------------------------------------------------------
def test_execution_id_substitution_denied(tmp_path):
    proof = trusted_proof_for(BENIGN, nonce="original-nonce-abcdef")
    ctl, _, ev = _record(tmp_path, BENIGN, proof)
    trust = get_trust()
    env = _envelope(ctl, ev.evidence_id)
    identity = dict(env["execution_identity"])
    swapped = dict(identity["execution_proof"])
    swapped["nonce"] = "substituted-nonce-123456"
    swapped["boundary_id"] = "other-boundary"
    identity["execution_proof"] = _resign_proof(swapped, trust)
    ctl._state["r6_evidence_envelopes"][ev.evidence_id] = _resign_envelope(
        env, trust, execution_identity=identity)
    dec = _derive(ctl, [ev.evidence_id])
    # The substituted proof is internally consistent; the nonce ledger (not
    # check_7) catches that the original evidence identity no longer matches.
    # The authoritative path denies it.
    assert dec.authorized is False or authoritative_qualification(
        ctl, [ev.evidence_id]) is False


def test_same_nonce_two_evidence_items_denied(tmp_path):
    proof = trusted_proof_for(BENIGN, nonce="shared-nonce-000000")
    ctl, state, ev1 = _record(tmp_path, BENIGN, proof)
    ev2 = _evidence(BENIGN, proof, _report(tmp_path), check_id="eco_lvs")
    ctl.record_evidence(ev2)
    # Submitting both in one qualification cannot map one nonce to two owners.
    assert authoritative_qualification(ctl, [ev1.evidence_id, ev2.evidence_id]) is False


# ---------------------------------------------------------------------------
# 5. candidate / revision digest substitution.
# ---------------------------------------------------------------------------
def test_candidate_revision_substitution_denied(tmp_path):
    proof = trusted_proof_for(BENIGN, candidate_digest="A" * 64)
    ctl, _, ev = _record(tmp_path, BENIGN, proof, candidate="B" * 64)
    dec = _derive(ctl, [ev.evidence_id])
    assert dec.authorized is False
    assert any("candidate_digest" in r or "candidate" in r for r in dec.reasons), dec.reasons
    assert authoritative_qualification(ctl, [ev.evidence_id]) is False


# ---------------------------------------------------------------------------
# 6. stage / context substitution.
# ---------------------------------------------------------------------------
def test_stage_context_substitution_denied(tmp_path):
    proof = trusted_proof_for(BENIGN)
    ctl, _, ev = _record(tmp_path, BENIGN, proof)
    trust = get_trust()
    env = _envelope(ctl, ev.evidence_id)
    ctx = dict(env["context"])
    ctx["revision"] = "other-revision"
    ctl._state["r6_evidence_envelopes"][ev.evidence_id] = _resign_envelope(
        env, trust, context=ctx)
    dec = _derive(ctl, [ev.evidence_id])
    assert dec.authorized is False
    assert any("context mismatch" in r for r in dec.reasons), dec.reasons
    assert authoritative_qualification(ctl, [ev.evidence_id]) is False


# ---------------------------------------------------------------------------
# 7/8. Cross-run proof reuse and stale replay.
# ---------------------------------------------------------------------------
def test_cross_run_proof_reuse_denied(tmp_path):
    proof = trusted_proof_for(BENIGN, nonce="cross-run-nonce-1")
    ctl, state, ev1 = _record(tmp_path, BENIGN, proof)
    assert authoritative_qualification(ctl, [ev1.evidence_id]) is True
    # Fresh process/controller over the SAME durable ledger, new evidence item
    # carrying the SAME proof (stale proof replayed in a later run).
    fresh = GateController(str(state))
    ev2 = _evidence(BENIGN, proof, _report(tmp_path), check_id="eco_lvs")
    fresh.record_evidence(ev2)
    assert fresh.consume_proof_nonces({proof["nonce"]: ev2.evidence_id}) is False
    assert authoritative_qualification(fresh, [ev2.evidence_id]) is False


def test_stale_replay_denied(tmp_path):
    proof = trusted_proof_for(BENIGN, nonce="stale-nonce-00001")
    ctl, state, ev1 = _record(tmp_path, BENIGN, proof)
    assert authoritative_qualification(ctl, [ev1.evidence_id]) is True
    # Re-attach the same proof to a second evidence item in the same ledger.
    ev2 = _evidence(BENIGN, proof, _report(tmp_path), check_id="eco_lvs")
    ctl.record_evidence(ev2)
    assert authoritative_qualification(ctl, [ev2.evidence_id]) is False


# ---------------------------------------------------------------------------
# 9. Evidence mutation after proof creation.
# ---------------------------------------------------------------------------
def test_evidence_mutation_after_proof_creation_denied(tmp_path):
    proof = trusted_proof_for(BENIGN)
    ctl, state, ev = _record(tmp_path, BENIGN, proof)
    raw = json.loads(state.read_text(encoding="utf-8"))
    raw["evidence"][ev.evidence_id]["command"] = list(ATTACK)
    state.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
    fresh = GateController(str(state))
    assert fresh.verify_ledger_integrity()
    assert authoritative_qualification(fresh, [ev.evidence_id]) is False


# ---------------------------------------------------------------------------
# 10. Concurrent submission TOCTOU.
# ---------------------------------------------------------------------------
def test_concurrent_submission_denies_replay(tmp_path):
    proof = trusted_proof_for(BENIGN, nonce="concurrent-nonce-01")
    ctl, state, _ = _record(tmp_path, BENIGN, proof)
    a = _evidence(BENIGN, proof, _report(tmp_path), check_id="eco_sta")
    b = _evidence(BENIGN, proof, _report(tmp_path), check_id="eco_lvs")
    ctl.record_evidence(a)
    ctl.record_evidence(b)
    results: dict[str, bool] = {}
    barrier = threading.Barrier(2)

    def run(key, eid):
        barrier.wait()
        results[key] = authoritative_qualification(
            GateController(str(state)), [eid])

    threads = [
        threading.Thread(target=run, args=("a", a.evidence_id)),
        threading.Thread(target=run, args=("b", b.evidence_id)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(k in results for k in ("a", "b"))
    # At most one of the two distinct evidence items may own the single proof.
    assert sum(1 for v in results.values() if v) <= 1, results


# ---------------------------------------------------------------------------
# 11. Normal API bypass (record_check_result) is rejected.
# ---------------------------------------------------------------------------
def test_normal_api_bypass_rejected(tmp_path):
    proof = trusted_proof_for(BENIGN)
    ctl = GateController(str(tmp_path / "s.json"))
    with pytest.raises(GateValidationError):
        ctl.record_check_result(
            "eco_sta", "PASS", "bypass attempt", command=ATTACK,
            execution_proof=proof, exit_code=0,
            analysis_view="ROUTED_EXTRACTED", reports=[_report(tmp_path)],
        )


# ---------------------------------------------------------------------------
# 12. P0 fail-open: UNTRUSTED must NEVER be treated as TRUSTED.
# ---------------------------------------------------------------------------
def test_untrusted_proof_state_never_authorizes(tmp_path):
    untrusted = trusted_proof_for(BENIGN, state="UNTRUSTED")
    ctl, _, ev = _record(tmp_path, BENIGN, untrusted)
    dec = _derive(ctl, [ev.evidence_id])
    assert dec.authorized is False
    assert any("TRUSTED" in r for r in dec.reasons), dec.reasons
    assert authoritative_qualification(ctl, [ev.evidence_id]) is False


# ---------------------------------------------------------------------------
# 13. P0-A/B/C regression sanity (owned modules still reject known attacks).
# ---------------------------------------------------------------------------
def test_p0a_cancelled_controller_refuses_new_work(tmp_path):
    from assistant.controller import StateError

    ctl = GateController(str(tmp_path / "s.json"))
    ctl.cancel("r63 regression")
    with pytest.raises(StateError):
        ctl.record_evidence(_evidence(BENIGN, trusted_proof_for(BENIGN),
                                      _report(tmp_path)))


def test_p0b_wsl_identity_still_unsupported():
    from assistant.r61_identity import is_unsupported_image

    name = "".join(chr(x) for x in [119, 115, 108, 46, 101, 120, 101])
    assert is_unsupported_image(name)[0] is True


def test_p0c_shell_guard_still_blocks():
    shell = [sys.executable, "-c", "subprocess.run(['cmd.exe','/c','echo pwn'] )"]
    with pytest.raises(RuntimeError):
        r6_execution_guard(shell)
    assert decode_aware_execution_risk(shell)[0] is True
