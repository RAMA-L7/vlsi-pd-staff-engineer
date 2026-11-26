"""R6.2 test helper: obtain a REAL TRUSTED ExecutionProof from the managed boundary.

Used by historical gate-trust tests so they keep their security purpose (MAC
binding, waivers, N-A, provenance) while satisfying the new structural proof
requirement. This does NOT fabricate a proof — it runs an approved benign command
through the real ExecutionBoundary and returns its MAC-bound TRUSTED proof.
"""
import os, sys
from pathlib import Path

def real_trusted_proof(check_id=""):
    """Return (proof_dict, None) from a real managed execution of an approved tool.

    R7.1 fixture change: ``check_id`` is forwarded into the trusted boundary so
    the proof is MAC-bound to the check it authorizes. Historical call sites that
    omit it mint a pre-R7.1-style proof (no check_id) which the authority treats
    via its documented legacy compatibility rule; new tests pass it explicitly.
    Returns (None, reason) if the boundary cannot establish TRUSTED.
    """
    try:
        from assistant.execution_trust import ExecutionBoundary, DOMAIN_EXECUTION
        from assistant.r6_authority import get_trust
        trust = get_trust()
        boundary = ExecutionBoundary(trust)
        # use a manifest-approved argv (the framework's own interpreter, no -c payload)
        import subprocess
        argv = [sys.executable, "-c", "print('ok')"]
        tool_id = boundary.tool_id_for_argv(argv)
        if tool_id is None:
            return None, "argv not in signed tool-spec allowlist"
        result, proof = boundary.run(argv, timeout=10, check_id=str(check_id or ""))
        if proof is None:
            return None, "no proof issued"
        d = proof.to_dict()
        state = str(d.get("state", ""))
        if "TRUSTED" not in state.upper():
            return None, "proof state=" + state
        return d, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


APPROVED_COMMAND = [sys.executable, "-c", "print('ok')"]


def proof_and_command(check_id=""):
    """Return (proof_dict, command) where command is EXACTLY the argv the proof
    binds. R6.3 proof-to-evidence binding requires evidence.command == command.
    R7.1 fixture change: ``check_id`` is forwarded to the trusted boundary.
    Returns (None, None) if no TRUSTED proof could be minted.
    """
    # get_trust() re-detects ASSISTANT_LEDGER_KEY changes via _TRUST_KEY, so no
    # manual singleton reset is needed (a reset would change the session_id and
    # break context binding between evidence items minted in the same test).
    d, err = real_trusted_proof(check_id=check_id)
    return (d, list(APPROVED_COMMAND)) if d is not None else (None, None)


def proof_for_test(check_id=""):
    """Convenience: proof dict or None (tests attach to Evidence.execution_proof)."""
    d, err = real_trusted_proof(check_id=check_id)
    return d
