"""R6.3 test helper: build a MAC-valid TRUSTED ExecutionProof for a given argv.

The production boundary (``ExecutionBoundary.run``) only issues TRUSTED proofs
for allowlisted argv. Tests that need a proof bound to an arbitrary evidence
command construct a genuine, correctly MAC-signed proof with the process trust
root. Holding the trust root is explicitly OUT OF SCOPE for the R6.3 threat
model (Agent 1 finding #5): it is equivalent to ``ExecutionProof.bind_digests``
and is only usable by trusted in-process test code.

Crucially, the proof is internally consistent: ``argv_sha256`` is the exact
helper output for *command*, and ``execution_digest`` is recomputed with the
same helper the producer uses. It therefore passes MAC + state + digest checks
and isolates the R6.3 proof-to-evidence BINDING property under test.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from assistant import execution_trust as et
from assistant.r6_authority import get_trust


def _utc() -> str:
    return (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        .replace("+00:00", "Z")
    )


def trusted_proof_for(command, *, trust=None, nonce=None, state="TRUSTED",
                      candidate_digest="", consumed_digest="",
                      dependency_digest="", tool_id="python_print_ok",
                      check_id="eco_sta"):
    """Return a MAC-valid proof dict bound to *command* and the trust context.

    R7.1 fixture change: the proof now carries a MAC-bound ``check_id`` (default
    ``eco_sta``, matching the historical helper evidence). Tests that need a
    proof for a different check pass ``check_id=...`` explicitly. Assertions are
    unchanged.
    """
    trust = trust or get_trust()
    values = dict(
        nonce=str(nonce or secrets.token_hex(16)),
        tool_id=str(tool_id),
        check_id=str(check_id or ""),
        image_realpath="",
        image_sha256="",
        approved_images=(),
        approved_images_match=True,
        descendant_count=0,
        job_member_count=1,
        root_job_member=True,
        argv_sha256=str(et._sha256_argv(list(command))),
        boundary_id="r63-test-boundary",
        context=et._canonical_context(trust.context),
        started_utc=_utc(),
        ended_utc=_utc(),
        exit_code=0,
        state=state,
        reason="r63 test proof",
        execution_digest="",
        candidate_digest=str(candidate_digest or ""),
        consumed_digest=str(consumed_digest or ""),
        dependency_digest=str(dependency_digest or ""),
        sampling_window_s=0.0,
        sampling_samples=0,
        containment_claim="none",
        key_ephemeral=bool(getattr(trust, "ephemeral", False)),
        sampling_limits_unsupported=True,
        mac="",
    )
    values["execution_digest"] = et.ExecutionBoundary._execution_digest(values)
    return et.ExecutionProof(**values).sign(trust).to_dict()


def reset_trust():
    """Reset the process trust singleton so tests pick up current env/key."""
    import assistant.r6_authority as ra

    ra._TRUST = None
    ra._TRUST_KEY = object()
