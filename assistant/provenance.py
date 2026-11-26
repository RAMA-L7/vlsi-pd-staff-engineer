"""Evidence provenance model for the Round 6 qualification architecture.

This module is the single source of truth for evidence *classification*.  A
``Provenance`` value is embedded inside a MAC'd evidence payload; it is set by
the trusted execution boundary and can never be relabelled by editing a ledger
record, because ``EvidenceEnvelope.from_dict`` verifies the MAC **before**
constructing the object and derives the provenance from the verified payload.

Provenance classes (ADR §4)
---------------------------
* ``SIMULATED``      - simulated/dry-run evidence; non-authorizing.
* ``ANALYSIS``       - static analysis output; non-authorizing.
* ``OBSERVATION``    - diagnostic observation (e.g. ``run_observation``);
                       non-authorizing.
* ``TOOL_EXECUTION`` - produced only by the execution boundary (registered
                       tool, observed OS process image); authorizing.
* ``QUALIFIED``      - a TOOL_EXECUTION/ANALYSIS/OBSERVATION record that has
                       passed validation via trusted code; authorizing.
* ``INVALIDATED``    - revoked/expired/detected; terminal, non-authorizing.
* ``UNSUPPORTED``    - unsupported execution identity or content;
                       non-authorizing.

Authorizing classes are exactly ``TOOL_EXECUTION`` and ``QUALIFIED``
(``AUTHORIZING``).  Note that provenance alone is *not* authorization: a full
authorization decision additionally requires a valid MAC, matching context,
and terminal state, all enforced by ``from_dict`` and by
``state_machine.derive_authorization``.  ``is_authorizing()`` is only the
provenance-class predicate.

Legal transitions (``legal_transition``; trusted code only)
------------------------------------------------------------
* SIMULATED      -> INVALIDATED            (expiry)
* ANALYSIS       -> QUALIFIED              (policy validation)
* OBSERVATION    -> QUALIFIED              (full validation)
* TOOL_EXECUTION -> QUALIFIED              (gate validation)
* <any>          -> INVALIDATED            (detection / revocation / expiry;
                                            includes QUALIFIED -> INVALIDATED
                                            as the "revoked" outcome)
* <any>          -> <same>                 (identity)

Explicitly prohibited
---------------------
* SIMULATED      -> TOOL_EXECUTION
* INVALIDATED    -> QUALIFIED              (INVALIDATED is terminal)
* <non-execution> -> TOOL_EXECUTION        (TOOL_EXECUTION is assigned only at
                                            creation by the execution boundary)
* UNSUPPORTED    -> QUALIFIED, SIMULATED -> QUALIFIED, etc.

No external dependencies; Python 3.10+.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

# Prefer the trust module's domain tag when it is importable, so that the
# literal value cannot drift between signer and verifier during integration.
# The fallback keeps this module self-contained (including the self-test when
# trust.py is not yet present).
try:  # pragma: no cover - depends on integration state
    from .trust import DOMAIN_EVIDENCE as DOMAIN_EVIDENCE  # type: ignore
except Exception:  # noqa: BLE001
    try:  # pragma: no cover
        from trust import DOMAIN_EVIDENCE as DOMAIN_EVIDENCE  # type: ignore
    except Exception:  # noqa: BLE001
        DOMAIN_EVIDENCE = "EVIDENCE"


class Provenance(Enum):
    """Evidence provenance classes (exact set per ADR §4)."""

    SIMULATED = "SIMULATED"
    ANALYSIS = "ANALYSIS"
    OBSERVATION = "OBSERVATION"
    TOOL_EXECUTION = "TOOL_EXECUTION"
    QUALIFIED = "QUALIFIED"
    INVALIDATED = "INVALIDATED"
    UNSUPPORTED = "UNSUPPORTED"


AUTHORIZING = frozenset({Provenance.TOOL_EXECUTION, Provenance.QUALIFIED})

# Every field that is authenticated by the MAC, in a fixed order.  The MAC is
# computed over these fields only; ``mac`` itself is deliberately excluded.
_PAYLOAD_FIELDS = (
    "evidence_id",
    "check_id",
    "gate_id",
    "provenance",
    "execution_identity",
    "consumed_sha256",
    "dependencies",
    "status",
    "observed_utc",
    "context",
)
_STRING_FIELDS = frozenset(
    {
        "evidence_id",
        "check_id",
        "gate_id",
        "consumed_sha256",
        "status",
        "observed_utc",
    }
)

# Legal non-identity transitions.  "any -> INVALIDATED" and identity are
# handled explicitly in legal_transition().
_ALLOWED_TRANSITIONS = frozenset(
    {
        (Provenance.SIMULATED, Provenance.INVALIDATED),
        (Provenance.ANALYSIS, Provenance.QUALIFIED),
        (Provenance.OBSERVATION, Provenance.QUALIFIED),
        (Provenance.TOOL_EXECUTION, Provenance.QUALIFIED),
    }
)


def _to_jsonable(value: Any) -> Any:
    """Recursively normalize a value into JSON-serializable primitives.

    Mappings become ``dict`` (string keys), tuples/lists become ``list``, and
    enums become their string values.  This makes canonical JSON identical
    regardless of whether a caller supplied a tuple or a list, an Enum or its
    string value, etc.
    """

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    return value


def _normalize_payload(d: Mapping) -> dict:
    """Build the canonical (MAC-covered) payload from a mapping of fields.

    Raises ``ValueError`` on malformed input so that callers fail closed.
    """

    if not isinstance(d, Mapping):
        raise ValueError("malformed evidence envelope: expected a mapping")
    payload: dict = {}
    for field in _PAYLOAD_FIELDS:
        if field not in d:
            raise ValueError(f"malformed evidence envelope: missing field {field!r}")
        value = d[field]
        if field in _STRING_FIELDS and not isinstance(value, str):
            raise ValueError(f"malformed evidence envelope: {field!r} must be a string")
        # R7.1: check_id is an authorization identity. An envelope that names no
        # check can never authorize anything, so refuse it at the boundary in
        # BOTH signing and verification paths.
        if field == "check_id" and not value.strip():
            raise ValueError("malformed evidence envelope: empty check_id")
        payload[field] = _to_jsonable(value)
    if not isinstance(payload["execution_identity"], dict):
        raise ValueError("malformed evidence envelope: execution_identity must be a mapping")
    if not isinstance(payload["context"], dict):
        raise ValueError("malformed evidence envelope: context must be a mapping")
    if not isinstance(payload["dependencies"], list):
        raise ValueError("malformed evidence envelope: dependencies must be a sequence")
    return payload


def _to_tuple_tree(value: Any) -> Any:
    """Restore a JSON list-tree into the immutable tuple-tree used by the model.

    ``_to_jsonable`` turns nested tuples into lists so the MAC payload is
    JSON-serializable; this helper reverses that for the ``dependencies``
    field so ``from_dict`` round-trips preserve the exact tuple structure.
    """

    if isinstance(value, (list, tuple)):
        return tuple(_to_tuple_tree(v) for v in value)
    return value


def _coerce_provenance(value: Any) -> Provenance:
    if isinstance(value, Provenance):
        return value
    try:
        return Provenance(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid provenance: {value!r}") from exc


def legal_transition(old: Provenance, new: Provenance) -> bool:
    """Return True only for the trusted-code transitions documented above.

    ``TOOL_EXECUTION`` is assignable only at creation by the execution
    boundary; therefore the only legal way to *reach* it here is the identity
    transition (TOOL_EXECUTION -> TOOL_EXECUTION).  ``INVALIDATED`` is terminal.
    """

    o = _coerce_provenance(old)
    n = _coerce_provenance(new)
    if o is n:  # identity transitions
        return True
    if n is Provenance.TOOL_EXECUTION:  # never relabel into an execution class
        return False
    if n is Provenance.INVALIDATED:  # any -> INVALIDATED (revoked/expired/detected)
        return True
    return (o, n) in _ALLOWED_TRANSITIONS


@dataclass(frozen=True)
class EvidenceEnvelope:
    """An immutable, MAC-authenticated evidence record.

    Construct via ``make_envelope`` (signs) or ``from_dict`` (verifies).
    Direct construction is possible but produces an unauthenticated object;
    authorization code must always go through ``from_dict``/``make_envelope``.
    """

    evidence_id: str
    check_id: str
    gate_id: str
    provenance: Provenance
    execution_identity: Mapping
    consumed_sha256: str
    dependencies: tuple
    status: str
    observed_utc: str
    context: Mapping
    mac: str

    def to_dict(self) -> dict:
        """Return a JSON-serializable dict; ``mac`` covers all other fields."""

        payload = _normalize_payload(
            {
                "evidence_id": self.evidence_id,
                "check_id": self.check_id,
                "gate_id": self.gate_id,
                "provenance": self.provenance,
                "execution_identity": self.execution_identity,
                "consumed_sha256": self.consumed_sha256,
                "dependencies": self.dependencies,
                "status": self.status,
                "observed_utc": self.observed_utc,
                "context": self.context,
            }
        )
        payload["mac"] = self.mac
        return payload

    @classmethod
    def from_dict(cls, d: Mapping, trust) -> "EvidenceEnvelope":
        """Verify the MAC and reconstruct an envelope, or raise ``ValueError``.

        The MAC is verified over the payload with the ``mac`` field removed.
        No caller-supplied provenance string is ever accepted without this
        verification; the provenance is taken from the verified payload.
        """

        if not isinstance(d, Mapping):
            raise ValueError("malformed evidence envelope: expected a mapping")
        mac = d.get("mac")
        if not isinstance(mac, str) or not mac:
            raise ValueError("malformed evidence envelope: missing or invalid mac")

        payload = _normalize_payload(d)  # all fields except 'mac'

        try:
            verified = trust.verify(DOMAIN_EVIDENCE, payload, mac)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("evidence MAC verification failed") from exc
        if verified is not True:
            raise ValueError(
                "evidence MAC verification failed: record is tampered, forged, "
                "or signed with a different key"
            )

        prov = _coerce_provenance(payload["provenance"])
        return cls(
            evidence_id=payload["evidence_id"],
            check_id=payload["check_id"],
            gate_id=payload["gate_id"],
            provenance=prov,
            execution_identity=payload["execution_identity"],
            consumed_sha256=payload["consumed_sha256"],
            dependencies=_to_tuple_tree(payload["dependencies"]),
            status=payload["status"],
            observed_utc=payload["observed_utc"],
            context=payload["context"],
            mac=mac,
        )

    def is_authorizing(self) -> bool:
        """Provenance-class predicate only.

        Full authorization additionally requires verified MAC, matching
        context, and terminal state (enforced by ``from_dict`` and by
        ``state_machine.derive_authorization``).
        """

        return self.provenance in AUTHORIZING

    def authorizes_check(self, check_id: str) -> bool:
        """R7.1: whether this envelope's MAC-bound check_id names *check_id*.

        This is the authoritative evidence-to-check identity predicate. It is
        safe to call on an envelope only after construction through
        ``from_dict``/``make_envelope`` (both verify the MAC).
        """
        return (
            isinstance(check_id, str)
            and bool(check_id.strip())
            and self.check_id == check_id
        )


def make_envelope(
    evidence_id: str,
    check_id: str,
    gate_id: str,
    provenance: Provenance,
    execution_identity: Mapping,
    consumed_sha256: str,
    dependencies: Any,
    status: str,
    observed_utc: str,
    context: Mapping,
    *,
    trust,
) -> EvidenceEnvelope:
    """Create and sign an ``EvidenceEnvelope`` via ``trust.sign``.

    ``trust`` is keyword-only so it cannot be confused with a data field.
    The signed payload is exactly the dict returned by ``to_dict()`` minus the
    ``mac`` field, which guarantees sign/verify symmetry.
    """

    payload = _normalize_payload(
        {
            "evidence_id": evidence_id,
            "check_id": check_id,
            "gate_id": gate_id,
            "provenance": provenance,
            "execution_identity": execution_identity,
            "consumed_sha256": consumed_sha256,
            "dependencies": dependencies,
            "status": status,
            "observed_utc": observed_utc,
            "context": context,
        }
    )
    mac = trust.sign(DOMAIN_EVIDENCE, payload)
    return EvidenceEnvelope(
        evidence_id=payload["evidence_id"],
        check_id=payload["check_id"],
        gate_id=payload["gate_id"],
        provenance=_coerce_provenance(payload["provenance"]),
        execution_identity=payload["execution_identity"],
        consumed_sha256=payload["consumed_sha256"],
        dependencies=_to_tuple_tree(payload["dependencies"]),
        status=payload["status"],
        observed_utc=payload["observed_utc"],
        context=payload["context"],
        mac=mac,
    )


def authorizes_check(envelope, check_id: str) -> bool:
    """R7.1 module-level predicate, tolerant of legacy/fake envelope objects.

    Prefers the envelope's own MAC-bound predicate; falls back to comparing the
    already-verified ``check_id`` attribute for older implementations. Never
    consults a caller-supplied mapping or ledger field.
    """
    if envelope is None or not isinstance(check_id, str) or not check_id.strip():
        return False
    method = getattr(envelope, "authorizes_check", None)
    if callable(method):
        return bool(method(check_id))
    bound = getattr(envelope, "check_id", None)
    return isinstance(bound, str) and bound == check_id


__all__ = [
    "DOMAIN_EVIDENCE",
    "Provenance",
    "AUTHORIZING",
    "EvidenceEnvelope",
    "legal_transition",
    "make_envelope",
    "authorizes_check",
]


if __name__ == "__main__":
    # Self-contained self-test: a minimal HMAC trust double mirrors the ADR §3
    # canonicalization so no dependency on assistant/trust.py is required.
    import hashlib
    import hmac
    import json

    class _SelfTestTrust:
        def __init__(self, key: bytes = b"r6-agent2-self-test-key"):
            self.key = key

        def canonical(self, payload: Mapping) -> bytes:
            return json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")

        def sign(self, domain: str, payload: Mapping) -> str:
            body = self.canonical({"domain": domain, "payload": payload})
            return hmac.new(self.key, body, hashlib.sha256).hexdigest()

        def verify(self, domain: str, payload: Mapping, mac: str) -> bool:
            return hmac.compare_digest(self.sign(domain, payload), mac)

    t = _SelfTestTrust()

    # 1) Legal / illegal transitions.
    legal = [
        (Provenance.SIMULATED, Provenance.INVALIDATED),
        (Provenance.ANALYSIS, Provenance.QUALIFIED),
        (Provenance.OBSERVATION, Provenance.QUALIFIED),
        (Provenance.TOOL_EXECUTION, Provenance.QUALIFIED),
        (Provenance.QUALIFIED, Provenance.INVALIDATED),  # revoked-like
        (Provenance.UNSUPPORTED, Provenance.INVALIDATED),  # any -> INVALIDATED
        (Provenance.SIMULATED, Provenance.SIMULATED),  # identity
        (Provenance.INVALIDATED, Provenance.INVALIDATED),  # identity
    ]
    illegal = [
        (Provenance.SIMULATED, Provenance.TOOL_EXECUTION),
        (Provenance.INVALIDATED, Provenance.QUALIFIED),
        (Provenance.ANALYSIS, Provenance.TOOL_EXECUTION),
        (Provenance.OBSERVATION, Provenance.TOOL_EXECUTION),
        (Provenance.QUALIFIED, Provenance.TOOL_EXECUTION),
        (Provenance.UNSUPPORTED, Provenance.QUALIFIED),
        (Provenance.SIMULATED, Provenance.QUALIFIED),
        (Provenance.INVALIDATED, Provenance.TOOL_EXECUTION),
    ]
    for old, new in legal:
        assert legal_transition(old, new) is True, (old, new)
    for old, new in illegal:
        assert legal_transition(old, new) is False, (old, new)

    # 2) sign -> from_dict roundtrip, including JSON serialize/deserialize.
    env = make_envelope(
        evidence_id="ev-1",
        check_id="chk-1",
        gate_id="gate-1",
        provenance=Provenance.TOOL_EXECUTION,
        execution_identity={"image": "/usr/bin/tool", "sha256": "aa" * 32},
        consumed_sha256="bb" * 32,
        dependencies=(("dep.py", "cc" * 32),),
        status="PASS",
        observed_utc="2026-11-20T18:30:00Z",
        context={"session_id": "s-1", "repo_id": "r-1", "revision": "h-1"},
        trust=t,
    )
    assert env.is_authorizing() is True
    wire = json.loads(json.dumps(env.to_dict()))
    restored = EvidenceEnvelope.from_dict(wire, t)
    assert restored == env
    assert restored.provenance is Provenance.TOOL_EXECUTION
    assert restored.dependencies == (("dep.py", "cc" * 32),)

    sim = make_envelope(
        evidence_id="ev-2",
        check_id="chk-2",
        gate_id="gate-2",
        provenance=Provenance.SIMULATED,
        execution_identity={"image": "sim"},
        consumed_sha256="dd" * 32,
        dependencies=(),
        status="SIMULATED",
        observed_utc="2026-11-20T18:30:01Z",
        context={"session_id": "s-1"},
        trust=t,
    )
    assert sim.is_authorizing() is False

    # 3) Tamper rejection: modifying any MAC-covered field must fail.
    tampered = dict(wire)
    tampered["consumed_sha256"] = "ff" * 32
    try:
        EvidenceEnvelope.from_dict(tampered, t)
    except ValueError:
        pass
    else:
        raise AssertionError("tampered field was accepted")

    # 4) Simulated cannot be relabelled to TOOL_EXECUTION by a dict edit.
    relabelled = json.loads(json.dumps(sim.to_dict()))
    relabelled["provenance"] = "TOOL_EXECUTION"
    try:
        forged = EvidenceEnvelope.from_dict(relabelled, t)
    except ValueError:
        pass
    else:
        raise AssertionError("SIMULATED -> TOOL_EXECUTION relabel was accepted")
    assert "forged" not in locals()

    # Malformed input must fail closed too.
    malformed = dict(wire)
    malformed.pop("gate_id")
    try:
        EvidenceEnvelope.from_dict(malformed, t)
    except ValueError:
        pass
    else:
        raise AssertionError("malformed envelope was accepted")

    print("agent2 provenance self-test: PASS")
