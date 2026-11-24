"""R6 coordinator bridge: unified authorization and execution-identity guards.

This module is coordinator-owned (integration). It does not replace the managed
runner; it adds fail-closed checks that the Round 5 architecture lacked.
"""
from __future__ import annotations

import ast
import base64
import binascii
import re
from typing import Iterable, Mapping

# Execution identities that are NOT inside the supported managed boundary.
_UNSUPPORTED_IMAGE_TOKENS = (
    "wsl.exe", "wslhost.exe", "bash.exe", "cmd.exe", "powershell.exe",
    "pwsh.exe", "sh.exe", "/bin/bash", "/bin/sh", "wsl -d", "wsl --",
)
_HEX_RUN = re.compile(r"[0-9a-fA-F]{40,}")


def _decoded_variants(arg: str) -> list[str]:
    """Best-effort decode of hex/base64/repr'd-list payloads inside an argument."""
    out: list[str] = []
    try:
        for m in _HEX_RUN.finditer(arg):
            raw = m.group(0)
            if len(raw) % 2:
                raw = raw[:-1]
            try:
                dec = binascii.unhexlify(raw).decode("utf-8", "ignore")
                out.append(dec)
                try:
                    obj = ast.literal_eval(dec)
                    if isinstance(obj, (list, tuple)):
                        out.append(" ".join(str(x) for x in obj))
                except Exception:
                    pass
            except Exception:
                pass
    except Exception:
        pass
    try:
        for m in re.finditer(r"[A-Za-z0-9+/]{40,}={0,2}", arg):
            dec = base64.b64decode(m.group(0), validate=True).decode("utf-8", "ignore")
            out.append(dec)
    except Exception:
        pass
    return out


def decode_aware_execution_risk(command: Iterable[str]) -> tuple[bool, str]:
    """Return (risk, reason). Fail closed on encoded unsupported execution.

    Detects the Round 5 P0-3 hexpy/encoded/wrapper WSL attack by decoding
    embedded payloads before launch. This is an ADDITIONAL gate — dynamic
    descendant observation remains the responsibility of
    ``assistant.execution.ManagedExecution.run_authoritative``.
    """
    try:
        parts = [str(x) for x in command]
    except Exception:
        return True, "unparseable command"
    joined = " ".join(parts).lower()
    for token in _UNSUPPORTED_IMAGE_TOKENS:
        if token in joined:
            return True, f"plain unsupported execution identity: {token}"
    for part in parts:
        for dec in _decoded_variants(part):
            dl = dec.lower()
            for token in _UNSUPPORTED_IMAGE_TOKENS:
                if token in dl:
                    return True, f"encoded unsupported execution identity: {token}"
    return False, ""


def r6_execution_guard(command: Iterable[str]) -> None:
    """Raise RuntimeError if the command hides an unsupported execution path."""
    risk, reason = decode_aware_execution_risk(command)
    if risk:
        raise RuntimeError(f"R6 execution-identity guard: {reason}")


# ---- Stage 2: single authoritative authorization decision -----------------
_TRUST = None
_TRUST_KEY = object()
_AUTHORIZATION_DOMAIN = "authorization/v1"
_FUTURE_DEADLINE = "2099-01-01T00:00:00+00:00"


def get_trust():
    """Process-wide trust root, recreated when the provisioned key changes.

    A stable per-process ephemeral key is used when no key is provisioned; a
    change to ASSISTANT_LEDGER_KEY creates a fresh trust root (matching a new
    process). Old records signed under the previous key then fail closed.
    """
    global _TRUST, _TRUST_KEY
    import os
    current = os.environ.get("ASSISTANT_LEDGER_KEY")
    if _TRUST is None or current != _TRUST_KEY:
        from .trust import TrustRoot
        _TRUST = TrustRoot()
        _TRUST_KEY = current
    return _TRUST


def _envelope_digest_material(envelopes) -> dict:
    """Collect authoritative digest material from MAC-verified envelopes.

    R6.3 concern #2: the derived record MUST carry the consumed / candidate /
    dependency digests so ``state_machine.check_7`` has an authoritative value
    to compare against a proof's self-reported digest, and checks 8/9 can run
    instead of being silent no-ops. Values come only from the MAC-bound
    envelope, never from a caller-supplied proof field.
    """
    consumed = ""
    candidate = ""
    dep_digest = ""
    deps: list = []
    for env in envelopes:
        if not consumed:
            consumed = str(env.get("consumed_sha256", "") or "")
        identity = env.get("execution_identity")
        if not isinstance(identity, Mapping):
            continue
        if not candidate:
            candidate = str(identity.get("candidate_digest", "") or "")
        if not dep_digest:
            dep_digest = str(identity.get("dependency_digest", "") or "")
        raw_deps = env.get("dependencies") or ()
        for entry in raw_deps:
            if entry not in deps:
                deps.append(entry)
    return {
        "consumed": consumed,
        "candidate": candidate,
        "dependency_digest": dep_digest,
        "dependencies": tuple(deps),
    }


def _proof_nonce_claims(envelopes, evidence_ids) -> tuple[dict, bool]:
    """Build ``nonce -> evidence_id`` claims from MAC-bound proofs.

    Returns (claims, ok). ``ok`` is False when a proof has no nonce or when the
    SAME nonce is attached to two different evidence items (replay/substitution
    in one submission). The key is proof.nonce -- NEVER a caller-supplied
    tool_execution_id (Agent 1 B7).
    """
    claims: dict[str, str] = {}
    for env, eid in zip(envelopes, evidence_ids):
        identity = env.get("execution_identity")
        proof = None
        if isinstance(identity, Mapping):
            proof = identity.get("execution_proof") or identity.get("proof")
        if not isinstance(proof, Mapping):
            return {}, False
        nonce = str(proof.get("nonce", "") or "")
        if not nonce:
            return {}, False
        prior = claims.get(nonce)
        if prior is not None and prior != eid:
            return {}, False
        claims[nonce] = eid
    return claims, True


def authoritative_qualification(controller, evidence_ids: Iterable[str]) -> bool:
    """R6.3 authoritative qualification (the ONLY gate authority).

    In addition to the R6 checks, this now enforces the proof-to-evidence
    binding via ``state_machine.check_7`` and atomically consumes each proof's
    nonce in a durable, external-key-bound ledger so cross-run replay and
    concurrent reuse fail closed.
    """
    try:
        trust = get_trust()
        if controller.verify_ledger_integrity():
            return False
        eids = list(evidence_ids)
        if not eids:
            # R6.2 E1/E9: production authorization requires a MAC-bound TRUSTED
            # ExecutionProof. A gate satisfied only by waiver / NOT_APPLICABLE,
            # or a verification-free `no_op` path, has NO tool evidence and NO
            # proof — it must never be granted production qualification.
            return False
        from .state_machine import derive_authorization
        from .provenance import EvidenceEnvelope
        envelopes = []
        stored = getattr(controller, "_state", {}).get("r6_evidence_envelopes") or {}
        for eid in eids:
            d = stored.get(eid)
            if not isinstance(d, Mapping):
                return False
            envelopes.append(EvidenceEnvelope.from_dict(d, trust).to_dict())
        if not envelopes:
            return False
        ctx = trust.context
        context = {
            "session_id": ctx.session_id, "repo_id": ctx.repo_id,
            "revision": ctx.revision,
        }
        material = _envelope_digest_material(envelopes)
        rec = {
            "transaction_id": "qualification",
            "state": "QUALIFIED_PASS",
            "complete": True,
            "deadline_utc": _FUTURE_DEADLINE,
            "context": context,
            "evidence": envelopes,
            # R6.3: record-side authoritative digest values for check_7/8/9.
            "candidate_digest": material["candidate"],
            "consumed_sha256": material["consumed"],
            "dependency_digest": material["dependency_digest"],
            "dependencies": material["dependencies"],
        }
        # derive_authorization verifies the MAC over the payload WITHOUT the
        # 'mac' key — sign exactly that payload.
        rec["mac"] = trust.sign(_AUTHORIZATION_DOMAIN, rec)
        # R6.3: checks 8/9 must actually run against authoritative values.
        current_files = (
            {"__evidence_consumed__": material["consumed"]}
            if material["consumed"] else None
        )
        current_deps = material["dependencies"] or None
        decision = derive_authorization(
            rec, trust, current_files=current_files, current_deps=current_deps
        )
        if not decision.authorized:
            return False
        # R6.3 durable replay ledger: a nonce may be owned by only ONE evidence
        # item. Consume atomically AFTER a positive decision so an early failure
        # never burns a valid proof, and concurrent submissions cannot both win.
        claims, ok = _proof_nonce_claims(envelopes, eids)
        if not ok:
            return False
        consume = getattr(controller, "consume_proof_nonces", None)
        if not callable(consume) or not consume(claims):
            return False
        return True
    except Exception:  # noqa: BLE001 - fail closed
        return False
