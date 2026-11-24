"""Authoritative qualification state machine and authorization derivation.

This module is the *single* authorization authority for the Round 6 design.
Every consumer (``evaluate_gate``, ``can_enter_stage``, ECO finalization,
downstream stage callbacks and the CLI) must obtain its decision from
:func:`derive_authorization`.  No parallel ``qualified`` / ``passed`` /
``authorization_safe`` boolean may override it.

Design contract (ADR v1.0, §5 and §12)
--------------------------------------
* Only :attr:`QualState.QUALIFIED_PASS` authorizes, and only after
  :func:`derive_authorization` re-validates a *durable, untrusted* record
  against the external trust root and the current execution context.
* ``derive_authorization`` runs checks 1-10 in order, *accumulating* rejection
  reasons, and returns ``authorized=True`` only when **every** check passes.
* It fails closed on ANY exception: unexpected errors are converted into a
  denial with a reason, never into an authorization.

Record schema (assumed canonical keys; bounded aliases are accepted)
-------------------------------------------------------------------
``transaction_id``, ``state``, ``context`` (``session_id``, ``repo_id``,
``revision``), ``mac``, ``transaction_complete``, ``deadline_utc``,
``evidence`` (sequence of provenance envelope dicts), ``consumed_sha256``
(mapping ``path -> sha256``), ``dependencies`` (dependency snapshot),
``contradictions``.  Keys outside ``KNOWN_KEYS`` are rejected (ADR §5 check
10: unknown keys/states -> fail closed).

Lazy imports
------------
``provenance.EvidenceEnvelope`` and the trust domain constant are resolved
lazily (at verification time) to avoid import cycles with Agent 1 / Agent 2
modules.  If either is unavailable, verification fails closed.

No external dependencies.  Python 3.10.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional

__all__ = [
    "QualState",
    "AuthorizationDecision",
    "AUTHORIZING_STATES",
    "TERMINAL_STATES",
    "DOMAIN_AUTHORIZATION",
    "legal_transition",
    "derive_authorization",
]


# ---------------------------------------------------------------------------
# R6.2 structural execution-proof helpers
# ---------------------------------------------------------------------------
def _proof_from_envelope(item: Mapping):
    """Extract an ExecutionProof dict from a MAC-bound evidence envelope.

    The proof lives inside ``execution_identity`` so it is covered by the
    envelope MAC. Caller-supplied top-level fields are never trusted.
    """
    try:
        ident = item.get("execution_identity")
        if not isinstance(ident, Mapping):
            return None
        proof = ident.get("execution_proof")
        if proof is None:
            proof = ident.get("proof")
        return proof if isinstance(proof, Mapping) else None
    except Exception:
        return None


def _proof_authorizes(proof: Mapping):
    """Return (ok, reason). Verifies the proof MAC + TRUSTED state via the
    trusted execution-trust module. Import is lazy; any failure fails closed.
    """
    try:
        mod = None
        try:
            from . import execution_trust as mod  # type: ignore
        except ImportError:
            try:
                import execution_trust as mod  # type: ignore
            except ImportError:
                return False, "execution_trust module unavailable"
        if mod is None:
            return False, "execution_trust module unavailable"
        # rebuild proof object and verify MAC with the process trust root
        pcls = getattr(mod, "ExecutionProof", None)
        if pcls is None:
            return False, "ExecutionProof type unavailable"
        trust = None
        try:
            trust = mod.default_trust()
        except Exception:
            try:
                from .r6_authority import get_trust as trust  # type: ignore
            except Exception:
                return False, "trust root unavailable"
        obj = pcls.from_dict(dict(proof), trust)
        if not getattr(obj, "verify_mac", lambda _t: False)(trust):
            return False, "proof MAC invalid"
        fn = getattr(mod, "proof_authorizes", None)
        if fn is not None:
            ok = fn(obj, trust)
            if isinstance(ok, tuple):
                ok = bool(ok[0])
            if not ok:
                return False, "proof not TRUSTED"
        # P0 fix (Agent 1 / coordinator R6.3 concern #1): the previous test
        # `"TRUSTED" not in state.upper()` is TRUE for "UNTRUSTED", because the
        # literal substring "TRUSTED" is contained in "UNTRUSTED". That was a
        # live fail-open masked only by the identity check above. Use EXACT
        # equality on the MAC-bound state value, never a substring test.
        state_field = getattr(obj, "state", None)
        state = str(getattr(state_field, "value", state_field) or "").strip().upper()
        if state != "TRUSTED":
            return False, f"execution state {state!r} is not exactly TRUSTED"
        # Belt and braces: when the enum is available, demand object identity
        # with the canonical TRUSTED member (subclass/alias tricks fail closed).
        state_enum = getattr(mod, "ExecutionState", None)
        wanted = getattr(state_enum, "TRUSTED", None) if state_enum is not None else None
        if wanted is not None and state_field is not wanted:
            return False, "execution state is not the canonical TRUSTED member"
        return True, "trusted proof"
    except Exception as exc:
        return False, f"proof verification failed ({type(exc).__name__}: {exc})"


def _load_execution_trust():
    """Lazily import assistant.execution_trust; None when unavailable.

    R6.3: the authoritative binding check MUST use the very same helpers the
    proof producer uses (``_sha256_argv`` and
    ``ExecutionBoundary._execution_digest``).  If they cannot be imported the
    caller fails closed.  Import is lazy to avoid cycles and so that a missing
    module never becomes an authorization path.
    """
    for module_name in (".execution_trust", "assistant.execution_trust"):
        try:
            return importlib.import_module(module_name, __package__)
        except Exception:  # noqa: BLE001 - fail closed, try next source
            continue
    return None


def _canonical_hex(value: Any) -> str:
    """Normalize a digest field to a lower-case hex string ('' if unusable)."""
    if value is None:
        return ""
    return str(value).strip().lower()


def _bound_digest(*values: Any) -> str:
    """Return the first non-empty digest among *values* (authoritative source)."""
    for value in values:
        digest = _canonical_hex(value)
        if digest and digest not in ("0" * 64,):
            return digest
    return ""


def _verify_proof_binding(item: Mapping, proof: Mapping, record: Mapping):
    """Bind a MAC-verified ExecutionProof to its evidence envelope and record.

    This is the authoritative R6.3 choke point.  It returns a LIST of specific
    fail-closed reasons (empty means the binding holds).  It is called from
    ``check_7`` for every TOOL_EXECUTION / QUALIFIED envelope, after
    ``_proof_authorizes`` has confirmed the proof MAC and TRUSTED state.

    Checks, in order:
      1. ``sha256(canonical(envelope.execution_identity.command))`` recomputed
         with ``execution_trust._sha256_argv`` == ``proof.argv_sha256``.  This is
         the non-negotiable command-binding fix: ``execution_digest`` alone is
         self-consistent for a substituted argv and cannot detect it.
      2. ``execution_digest`` recomputed with
         ``ExecutionBoundary._execution_digest`` == the MAC-bound value.
      3. proof context == envelope context == record context (3 binding keys).
      4. non-empty proof candidate/consumed/dependency digests match the
         authoritative record/envelope values; a non-empty proof digest with
         NO authoritative source fails closed (never compare '' == '').
    """
    errors: list[str] = []
    et = _load_execution_trust()
    if et is None:
        return ["proof binding: execution_trust helpers unavailable -> fail closed"]
    sha_argv = getattr(et, "_sha256_argv", None)
    boundary_cls = getattr(et, "ExecutionBoundary", None)
    recompute_digest = getattr(boundary_cls, "_execution_digest", None)
    if not callable(sha_argv) or not callable(recompute_digest):
        return ["proof binding: execution_trust digest helpers unavailable -> fail closed"]

    # -- 1. command / argv binding (the P0 fix) ------------------------------
    identity = item.get("execution_identity")
    command = identity.get("command") if isinstance(identity, Mapping) else None
    if not isinstance(command, (list, tuple)) or not command:
        errors.append(
            "proof binding: envelope execution_identity.command missing or invalid"
        )
    else:
        try:
            expected_argv = str(sha_argv(command))
        except Exception as exc:  # noqa: BLE001
            errors.append(
                f"proof binding: argv recomputation failed "
                f"({type(exc).__name__}: {exc})"
            )
        else:
            proof_argv = _canonical_hex(proof.get("argv_sha256"))
            if not proof_argv:
                errors.append("proof binding: proof.argv_sha256 missing or empty")
            elif proof_argv != expected_argv:
                errors.append(
                    "proof binding: argv_sha256 does not bind envelope command "
                    f"(expected {expected_argv}, proof {proof_argv})"
                )

    # -- 2. execution_digest recomputation (defence in depth) ----------------
    try:
        expected_exec = str(recompute_digest(proof))
    except Exception as exc:  # noqa: BLE001
        errors.append(
            f"proof binding: execution_digest recomputation failed "
            f"({type(exc).__name__}: {exc})"
        )
    else:
        proof_exec = _canonical_hex(proof.get("execution_digest"))
        if not proof_exec:
            errors.append("proof binding: proof.execution_digest missing or empty")
        elif proof_exec != expected_exec:
            errors.append(
                "proof binding: execution_digest mismatch "
                f"(expected {expected_exec}, proof {proof_exec})"
            )

    # -- 3. context equality: proof == envelope == record -------------------
    proof_ctx = _context_dict(proof.get("context"))
    env_ctx = _context_dict(item.get("context"))
    rec_ctx = _context_dict(record.get("context"))
    for label, ctx in (
        ("proof", proof_ctx), ("envelope", env_ctx), ("record", rec_ctx)
    ):
        if ctx is None:
            errors.append(f"proof binding: {label} context missing or invalid")
            continue
        for key in _CTX_KEYS:
            if ctx.get(key) in (None, ""):
                errors.append(f"proof binding: {label} context missing {key}")
    if proof_ctx and env_ctx and rec_ctx:
        for key in _CTX_KEYS:
            values = {str(proof_ctx.get(key)), str(env_ctx.get(key)),
                      str(rec_ctx.get(key))}
            if len(values) != 1:
                errors.append(
                    f"proof binding: {key} context mismatch "
                    f"(proof={proof_ctx.get(key)!r}, "
                    f"envelope={env_ctx.get(key)!r}, "
                    f"record={rec_ctx.get(key)!r})"
                )
        # Also bind any extra context key carried by BOTH proof and envelope
        # (e.g. a stage token), never relying on the trust root alone.
        proof_raw = proof.get("context")
        env_raw = item.get("context")
        if isinstance(proof_raw, Mapping) and isinstance(env_raw, Mapping):
            for key in set(proof_raw) & set(env_raw):
                if key in _CTX_KEYS or key == "created_utc":
                    continue
                if str(proof_raw[key]) != str(env_raw[key]):
                    errors.append(
                        f"proof binding: context key {key!r} mismatch "
                        f"(proof={proof_raw[key]!r}, envelope={env_raw[key]!r})"
                    )

    # -- 4. downstream digest binding ---------------------------------------
    identity = identity if isinstance(identity, Mapping) else {}
    # Authoritative record-side values (derived from recorded evidence).
    record_consumed = _first(
        record, ("consumed_sha256", "consumed", "consumed_digests"), ""
    )
    if isinstance(record_consumed, Mapping):
        record_consumed = next(iter(record_consumed.values()), "")
    record_deps = _first(
        record, ("dependency_digest", "dependencies", "dependency_snapshot",
                 "dependency_closure", "deps"), ""
    )
    if not isinstance(record_deps, str):
        # Canonical digest of a structured dependency snapshot.
        try:
            import hashlib as _hl
            import json as _js
            record_deps = _hl.sha256(
                _js.dumps(_normalize_deps(record_deps), sort_keys=True,
                          separators=(",", ":"), default=str).encode("utf-8")
            ).hexdigest()
        except Exception:  # noqa: BLE001
            record_deps = ""

    bindings = (
        ("candidate_digest", "candidate_digest",
         _bound_digest(identity.get("candidate_digest"),
                       record.get("candidate_digest"))),
        ("consumed_digest", "consumed_digest",
         _bound_digest(item.get("consumed_sha256"), record_consumed)),
        ("dependency_digest", "dependency_digest",
         _bound_digest(identity.get("dependency_digest"), record_deps)),
    )
    for proof_key, label, bound in bindings:
        supplied = _canonical_hex(proof.get(proof_key))
        if not supplied:
            continue  # producer left it unset; nothing to bind
        if not bound:
            # Agent 1 concern B5/B1: non-empty proof digest with NO
            # authoritative record-side source must fail closed. Never accept
            # '' == '' as a match.
            errors.append(
                f"proof binding: proof.{proof_key} is present but the record "
                "has no authoritative bound value -> fail closed"
            )
        elif supplied != bound:
            errors.append(
                f"proof binding: {label} mismatch "
                f"(record {bound}, proof {supplied})"
            )
    return errors


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------
class QualState(Enum):
    """Lifecycle states of a durable qualification transaction."""

    INITIAL = "INITIAL"
    VERIFYING = "VERIFYING"
    EVIDENCE_COLLECTED = "EVIDENCE_COLLECTED"
    VALIDATING = "VALIDATING"
    FINALIZING = "FINALIZING"
    QUALIFIED_PASS = "QUALIFIED_PASS"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    REVOKED = "REVOKED"
    RECOVERED = "RECOVERED"
    PENDING = "PENDING"


@dataclass(frozen=True)
class AuthorizationDecision:
    """The single decision object returned by :func:`derive_authorization`."""

    authorized: bool
    reasons: tuple
    state: QualState
    transaction_id: str


#: The ONLY state that authorizes.  Exposed for consumers that need to check
#: the authorizing set without re-deriving the whole transition table.
AUTHORIZING_STATES = frozenset({QualState.QUALIFIED_PASS})

#: Terminal states: no transition out of these is legal (ADR §5).
TERMINAL_STATES = frozenset(
    {
        QualState.QUALIFIED_PASS,
        QualState.FAILED,
        QualState.EXPIRED,
        QualState.INVALIDATED,
        QualState.REVOKED,
    }
)

#: Fallback domain; the authoritative value is resolved from ``trust`` lazily.
DOMAIN_AUTHORIZATION = "authorization/v1"


# ---------------------------------------------------------------------------
# Legal transition table
# ---------------------------------------------------------------------------
# Forward, non-terminal lifecycle order.  PENDING is created by
# ``TransactionJournal.begin``; INITIAL is the in-memory starting point.
_FORWARD_RANK = {
    QualState.INITIAL: 0,
    QualState.PENDING: 1,
    QualState.VERIFYING: 2,
    QualState.EVIDENCE_COLLECTED: 3,
    QualState.VALIDATING: 4,
    QualState.FINALIZING: 5,
}


def legal_transition(old: QualState, new: QualState) -> bool:
    """Return True only for a strictly forward, legal state transition.

    Rules (ADR §5):

    * both arguments must be :class:`QualState` members;
    * no self-transitions;
    * **no transition out of a terminal state**
      (QUALIFIED_PASS / FAILED / EXPIRED / INVALIDATED / REVOKED) back to any
      non-terminal state — terminal really means terminal;
    * RECOVERED is non-authorizing and a dead end: it may be *entered* from an
      in-progress state (recovery marker) but nothing may transition out of it;
    * any in-progress state may move to any terminal outcome (success or a
      failure mode such as FAILED / EXPIRED / INVALIDATED / REVOKED);
    * otherwise ``new`` must be strictly ahead of ``old`` in the forward order
      (INITIAL < PENDING < VERIFYING < EVIDENCE_COLLECTED < VALIDATING <
      FINALIZING).  Backward jumps are illegal.
    """
    if not isinstance(old, QualState) or not isinstance(new, QualState):
        return False
    if old == new:
        return False
    if old in TERMINAL_STATES:
        return False
    if old is QualState.RECOVERED:
        # Non-authorizing dead end: recovery never re-authorizes by itself.
        return False
    if new is QualState.RECOVERED:
        # Recovery marker may be attached to any in-progress transaction.
        return True
    if new in TERMINAL_STATES:
        # Any in-progress state may finalize into any terminal outcome.
        return True
    if old in _FORWARD_RANK and new in _FORWARD_RANK:
        return _FORWARD_RANK[new] > _FORWARD_RANK[old]
    return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
_MISSING = object()

_CTX_KEYS = ("session_id", "repo_id", "revision")

#: Canonical keys plus bounded aliases.  Anything outside this set is an
#: "unknown key" and fails check 10 (closed schema, no authorization backdoor
#: such as ``qualified = True`` smuggled into a durable record).
KNOWN_KEYS = frozenset(
    {
        "transaction_id",
        "txn_id",
        "id",
        "state",
        "terminal_state",
        "status",
        "context",
        "mac",
        "transaction_complete",
        "complete",
        "committed",
        "is_complete",
        "deadline_utc",
        "deadline",
        "expires_utc",
        "evidence",
        "evidence_envelopes",
        "envelopes",
        "consumed_sha256",
        "consumed",
        "consumed_files",
        "consumed_bytes",
        "consumed_digests",
        "candidate_digest",
        "consumed_digest",
        "dependency_digest",
        "dependencies",
        "dependency_snapshot",
        "dependency_closure",
        "deps",
        "contradictions",
        "version",
        "created_utc",
        "gate_id",
        "check_id",
    }
)


def _first(mapping: Any, keys, default=_MISSING):
    """Return the first present key from *keys* in *mapping*."""
    if isinstance(mapping, Mapping):
        for key in keys:
            if key in mapping:
                return mapping[key]
    return default


def _parse_state(value: Any) -> Optional[QualState]:
    """Parse a QualState from a member, name or value; None if unknown."""
    if isinstance(value, QualState):
        return value
    if isinstance(value, str):
        token = value.strip().upper()
        for state in QualState:
            if token == state.name or token == state.value:
                return state
    return None


def _context_dict(obj: Any) -> Optional[dict]:
    """Extract the three binding keys from a Mapping or a TrustContext-like obj."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return {key: obj.get(key) for key in _CTX_KEYS}
    return {key: getattr(obj, key, None) for key in _CTX_KEYS}


def _parse_utc(value: Any) -> datetime:
    """Parse an ISO-8601 UTC timestamp into an aware UTC datetime."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("empty timestamp")
        if text.endswith("Z") or text.endswith("z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    else:
        raise ValueError(f"unsupported timestamp type: {type(value).__name__}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _truthy_flag(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().upper() in ("TRUE", "1", "YES", "Y")
    if isinstance(value, int) and not isinstance(value, bool):
        return value == 1
    return False


def _authorization_domain() -> str:
    """Resolve DOMAIN_AUTHORIZATION from Agent 1's trust module lazily."""
    for module_name in (".trust", "assistant.trust"):
        try:
            module = importlib.import_module(module_name, __package__)
            domain = getattr(module, "DOMAIN_AUTHORIZATION", None)
            if isinstance(domain, str) and domain:
                return domain
        except Exception:  # noqa: BLE001 - fail closed, try next source
            continue
    return DOMAIN_AUTHORIZATION


def _load_envelope_class():
    """Lazily import provenance.EvidenceEnvelope; None when unavailable."""
    for module_name in (".provenance", "assistant.provenance"):
        try:
            module = importlib.import_module(module_name, __package__)
            envelope = getattr(module, "EvidenceEnvelope", None)
            if envelope is not None:
                return envelope
        except Exception:  # noqa: BLE001 - unavailable => caller fails closed
            continue
    return None


def _prov_name(value: Any) -> str:
    """Normalize a provenance value to an upper-case bare name."""
    name = getattr(value, "name", None)
    text = str(name if name is not None else value)
    return text.split(".")[-1].split(":")[-1].strip().upper()


def _normalize_deps(value: Any):
    """Order-insensitive normalization for dependency snapshot comparison."""
    if isinstance(value, Mapping):
        return ("map",) + tuple(
            sorted((str(k), str(v)) for k, v in value.items())
        )
    if isinstance(value, (str, bytes)):
        return ("raw", str(value))
    try:
        items = list(value)
    except TypeError:
        return ("raw", str(value))
    return ("seq",) + tuple(sorted(str(item) for item in items))


def _consumed_path(envelope: Mapping) -> Optional[str]:
    """Best-effort path for an envelope's consumed digest."""
    for key in ("source_path", "consumed_path", "path"):
        value = envelope.get(key)
        if isinstance(value, str) and value:
            return value
    identity = envelope.get("execution_identity")
    if isinstance(identity, Mapping):
        for key in ("source_path", "consumed_path", "path"):
            value = identity.get(key)
            if isinstance(value, str) and value:
                return value
    return None


# ---------------------------------------------------------------------------
# The single authorization function
# ---------------------------------------------------------------------------
def derive_authorization(
    record: Mapping,
    trust,
    *,
    now_utc=None,
    current_files=None,
    current_deps=None,
) -> AuthorizationDecision:
    """Derive authorization from an untrusted durable *record*.

    Runs ADR §5 checks 1-10 in order, accumulating every rejection reason.
    Returns ``authorized=True`` only if no check produced a reason.  Any
    exception (in a check or in the overall derivation) is converted into a
    denial with a reason -- this function never throws to its caller and never
    fails open.
    """
    reasons: list[str] = []
    state: QualState = QualState.INITIAL  # safe, non-authorizing default
    transaction_id: str = ""

    # Extract decision metadata defensively (never let this authorize).
    try:
        if isinstance(record, Mapping):
            raw_id = _first(record, ("transaction_id", "txn_id", "id"), "")
            transaction_id = str(raw_id) if raw_id is not None else ""
            parsed = _parse_state(
                _first(record, ("state", "terminal_state", "status"), None)
            )
            if parsed is not None:
                state = parsed
    except Exception:  # noqa: BLE001
        pass

    def run(label: str, check) -> None:
        """Execute one check, accumulating its reasons; isolate exceptions."""
        try:
            result = check()
            if result:
                if isinstance(result, str):
                    reasons.append(result)
                else:
                    reasons.extend(str(item) for item in result)
        except Exception as exc:  # noqa: BLE001 - fail closed, keep going
            reasons.append(
                f"{label}: unexpected exception "
                f"({type(exc).__name__}: {exc})"
            )

    # -- check 1: record shape ------------------------------------------------
    def check_1():
        if not isinstance(record, Mapping):
            return ["check 1 (record): record is not a Mapping"]
        if not record:
            return ["check 1 (record): record is empty"]
        return []

    # -- check 2: MAC verification -------------------------------------------
    def check_2():
        if not isinstance(record, Mapping):
            return ["check 2 (MAC): skipped - invalid record"]
        mac = record.get("mac")
        if not isinstance(mac, str) or not mac:
            return ["check 2 (MAC): missing or empty MAC"]
        verify = getattr(trust, "verify", None)
        if not callable(verify):
            return ["check 2 (MAC): trust has no callable verify()"]
        payload = {key: value for key, value in record.items() if key != "mac"}
        if not verify(_authorization_domain(), payload, mac):
            return ["check 2 (MAC): MAC verification failed"]
        return []

    # -- check 3: context match ----------------------------------------------
    def check_3():
        if not isinstance(record, Mapping):
            return ["check 3 (context): skipped - invalid record"]
        rec_ctx = _context_dict(record.get("context"))
        trust_ctx = _context_dict(getattr(trust, "context", None))
        if trust_ctx is None or not any(trust_ctx.values()):
            return ["check 3 (context): trust context unavailable"]
        if rec_ctx is None:
            return ["check 3 (context): record context missing or invalid"]
        for key in _CTX_KEYS:
            have, want = rec_ctx.get(key), trust_ctx.get(key)
            if have in (None, "") or want in (None, ""):
                return [f"check 3 (context): missing {key}"]
            if have != want:
                return [f"check 3 (context): {key} mismatch"]
        return []

    # -- check 4: state == QUALIFIED_PASS ------------------------------------
    def check_4():
        if not isinstance(record, Mapping):
            return ["check 4 (state): skipped - invalid record"]
        parsed = _parse_state(
            _first(record, ("state", "terminal_state", "status"), None)
        )
        if parsed is None:
            return ["check 4 (state): unknown/invalid state -> fail closed"]
        if parsed not in AUTHORIZING_STATES:
            return [f"check 4 (state): state {parsed.name} is not authorizing"]
        return []

    # -- check 5: transaction complete ---------------------------------------
    def check_5():
        if not isinstance(record, Mapping):
            return ["check 5 (completion): skipped - invalid record"]
        flag = _first(
            record,
            ("transaction_complete", "complete", "committed", "is_complete"),
            _MISSING,
        )
        if flag is _MISSING:
            return ["check 5 (completion): transaction complete flag missing"]
        if not _truthy_flag(flag):
            return ["check 5 (completion): transaction not complete"]
        return []

    # -- check 6: deadline not expired ---------------------------------------
    def check_6():
        if not isinstance(record, Mapping):
            return ["check 6 (deadline): skipped - invalid record"]
        raw = _first(
            record, ("deadline_utc", "deadline", "expires_utc"), _MISSING
        )
        if raw is _MISSING or raw in (None, ""):
            return ["check 6 (deadline): missing deadline"]
        try:
            deadline = _parse_utc(raw)
        except Exception as exc:
            return [f"check 6 (deadline): invalid deadline ({exc})"]
        try:
            now = (
                _parse_utc(now_utc)
                if now_utc is not None
                else datetime.now(timezone.utc)
            )
        except Exception as exc:
            return [f"check 6 (deadline): invalid now_utc ({exc})"]
        if now >= deadline:
            return ["check 6 (deadline): deadline expired"]
        return []

    # -- check 7: evidence envelopes -----------------------------------------
    def check_7():
        if not isinstance(record, Mapping):
            return ["check 7 (evidence): skipped - invalid record"]
        envelopes = _first(
            record, ("evidence", "evidence_envelopes", "envelopes"), _MISSING
        )
        if envelopes is _MISSING or envelopes is None:
            return ["check 7 (evidence): evidence list missing"]
        if isinstance(envelopes, (Mapping, str, bytes)):
            return [
                "check 7 (evidence): evidence must be a sequence of envelope dicts"
            ]
        try:
            env_list = list(envelopes)
        except Exception as exc:
            return [f"check 7 (evidence): invalid evidence list ({exc})"]
        if not env_list:
            return ["check 7 (evidence): no evidence envelopes"]

        envelope_cls = _load_envelope_class()
        if envelope_cls is None:
            return [
                "check 7 (evidence): provenance.EvidenceEnvelope unavailable "
                "-> fail closed"
            ]

        errors: list[str] = []
        non_authorizing = {
            "INVALIDATED",
            "UNSUPPORTED",
            "SIMULATED",
            "ANALYSIS",
            "OBSERVATION",
        }
        for index, item in enumerate(env_list):
            if not isinstance(item, Mapping):
                errors.append(
                    f"check 7 (evidence[{index}]): envelope is not a Mapping"
                )
                continue
            raw_prov = _prov_name(item.get("provenance"))
            if raw_prov in non_authorizing:
                errors.append(
                    f"check 7 (evidence[{index}]): non-authorizing provenance "
                    f"{raw_prov}"
                )
            try:
                envelope = envelope_cls.from_dict(item, trust)
            except Exception as exc:
                errors.append(
                    f"check 7 (evidence[{index}]): envelope verification failed "
                    f"({type(exc).__name__}: {exc})"
                )
                continue
            env_prov = _prov_name(getattr(envelope, "provenance", None))
            if env_prov in ("INVALIDATED", "UNSUPPORTED"):
                errors.append(
                    f"check 7 (evidence[{index}]): envelope provenance is "
                    f"{env_prov}"
                )
            is_authorizing = getattr(envelope, "is_authorizing", None)
            if not callable(is_authorizing) or not is_authorizing():
                errors.append(
                    f"check 7 (evidence[{index}]): envelope is not authorizing"
                )
            # R6 integration fix for Agent5 blocking concern B1: an authorizing
            # provenance class is NOT sufficient. A TOOL_EXECUTION run that FAILED
            # must never authorize. The recorded status must be exactly PASS.
            raw_status = _first(item, ("status", "result_status"), None)
            if not isinstance(raw_status, str) or raw_status.upper() != "PASS":
                errors.append(
                    f"check 7 (evidence[{index}]): non-PASS status "
                    f"{raw_status!r} cannot authorize"
                )
            # R6.2 E3/E9/E10: an authorizing provenance class and PASS status are
            # NOT sufficient. TOOL_EXECUTION evidence MUST carry a MAC-bound
            # ExecutionProof that (a) verifies under the external trust root and
            # (b) is in TRUSTED state. Caller-supplied command/status/labels can
            # never manufacture this proof.
            if env_prov in ("TOOL_EXECUTION", "QUALIFIED"):
                proof = _proof_from_envelope(item)
                if proof is None:
                    errors.append(
                        f"check 7 (evidence[{index}]): TOOL_EXECUTION without "
                        "structural ExecutionProof fails closed"
                    )
                else:
                    verdict = _proof_authorizes(proof)
                    if not verdict[0]:
                        errors.append(
                            f"check 7 (evidence[{index}]): execution proof not "
                            f"authorizing ({verdict[1]})"
                        )
                    else:
                        # R6.3 primary authority: a TRUSTED proof authorizes
                        # ONLY the exact command/context/digests it was issued
                        # for. Each mismatch yields a specific fail-closed
                        # reason; never fail open on a missing helper/field.
                        for reason in _verify_proof_binding(item, proof, record):
                            errors.append(
                                f"check 7 (evidence[{index}]): {reason}"
                            )
        return errors

    # -- check 8: consumed bytes rehash --------------------------------------
    def check_8():
        if current_files is None:
            return []  # caller did not request consumed-state verification
        if not isinstance(current_files, Mapping):
            return [
                "check 8 (consumed): current_files is not a mapping -> fail closed"
            ]
        if not isinstance(record, Mapping):
            return ["check 8 (consumed): skipped - invalid record"]

        errors: list[str] = []
        checked = False
        consumed = _first(
            record,
            (
                "consumed_sha256",
                "consumed",
                "consumed_files",
                "consumed_bytes",
                "consumed_digests",
            ),
            _MISSING,
        )
        if consumed is not _MISSING and consumed is not None:
            if isinstance(consumed, Mapping):
                for path, digest in consumed.items():
                    checked = True
                    current = current_files.get(path, _MISSING)
                    if current is _MISSING:
                        errors.append(
                            f"check 8 (consumed): no current hash for path {path!r}"
                        )
                    elif str(current).lower() != str(digest).lower():
                        errors.append(
                            f"check 8 (consumed): digest mismatch for {path!r}"
                        )
            elif isinstance(consumed, str) and consumed:
                # Single authorized digest: it must still be present among the
                # current hashes (the exact bytes survived).
                checked = True
                current_digests = {
                    str(value).lower() for value in current_files.values()
                }
                if consumed.lower() not in current_digests:
                    errors.append(
                        "check 8 (consumed): authorized consumed digest not "
                        "present in current files"
                    )
            else:
                errors.append(
                    "check 8 (consumed): record consumed digest set has an "
                    "unsupported type"
                )

        # Also verify per-envelope consumed digests when a path is resolvable.
        envelopes = _first(
            record, ("evidence", "evidence_envelopes", "envelopes"), ()
        )
        if isinstance(envelopes, (list, tuple)):
            for index, item in enumerate(envelopes):
                if not isinstance(item, Mapping):
                    continue
                digest = item.get("consumed_sha256")
                path = _consumed_path(item)
                if not digest or not path:
                    continue
                checked = True
                current = current_files.get(path, _MISSING)
                if current is _MISSING:
                    errors.append(
                        f"check 8 (consumed): envelope[{index}] path {path!r} "
                        "has no current hash"
                    )
                elif str(current).lower() != str(digest).lower():
                    errors.append(
                        f"check 8 (consumed): envelope[{index}] digest mismatch "
                        f"for {path!r}"
                    )
        if not checked:
            errors.append(
                "check 8 (consumed): current_files supplied but no consumed "
                "digest could be checked -> fail closed"
            )
        return errors

    # -- check 9: dependency closure -----------------------------------------
    def check_9():
        if current_deps is None:
            return []  # caller did not request dependency verification
        if not isinstance(record, Mapping):
            return ["check 9 (dependencies): skipped - invalid record"]
        snapshot = _first(
            record,
            ("dependency_snapshot", "dependencies", "dependency_closure", "deps"),
            _MISSING,
        )
        if snapshot is _MISSING or snapshot is None:
            return [
                "check 9 (dependencies): current_deps supplied but record has "
                "no dependency snapshot -> fail closed"
            ]
        if _normalize_deps(snapshot) != _normalize_deps(current_deps):
            return ["check 9 (dependencies): dependency snapshot mismatch"]
        return []

    # -- check 10: contradictions / closed schema ----------------------------
    def check_10():
        if not isinstance(record, Mapping):
            return ["check 10 (schema): skipped - invalid record"]
        errors: list[str] = []
        contradictions = record.get("contradictions", _MISSING)
        if contradictions is not _MISSING and contradictions:
            errors.append(
                "check 10 (contradictions): record has non-empty contradictions"
            )
        unknown = set(record.keys()) - KNOWN_KEYS
        if unknown:
            errors.append(
                "check 10 (schema): unknown record key(s): "
                + ", ".join(sorted(str(key) for key in unknown))
            )
        raw_state = _first(
            record, ("state", "terminal_state", "status"), _MISSING
        )
        if isinstance(raw_state, str) and _parse_state(raw_state) is None:
            errors.append(
                f"check 10 (schema): unknown state value {raw_state!r}"
            )
        return errors

    # -- run all checks in order ---------------------------------------------
    try:
        run("check 1", check_1)
        run("check 2", check_2)
        run("check 3", check_3)
        run("check 4", check_4)
        run("check 5", check_5)
        run("check 6", check_6)
        run("check 7", check_7)
        run("check 8", check_8)
        run("check 9", check_9)
        run("check 10", check_10)
    except Exception as exc:  # noqa: BLE001 - final fail-closed guard
        reasons.append(
            f"derive_authorization: unexpected exception "
            f"({type(exc).__name__}: {exc})"
        )

    authorized = not reasons
    if authorized and state not in AUTHORIZING_STATES:
        # Defense in depth: checks 4/10 should already have caught this.
        authorized = False
        reasons.append(
            "defensive: non-authorizing state survived all checks -> fail closed"
        )

    return AuthorizationDecision(
        authorized=authorized,
        reasons=tuple(reasons),
        state=state,
        transaction_id=transaction_id,
    )


# ---------------------------------------------------------------------------
# Self-test (python -m assistant.state_machine)
# ---------------------------------------------------------------------------
def _selftest() -> None:  # pragma: no cover - exercised via __main__ only
    """Build a fake trust + provenance, verify a valid record, mutate each field."""
    import copy
    import hashlib
    import hmac
    import json
    import sys
    import types

    evidence_domain = "evidence/v1"
    auth_domain = _authorization_domain()

    class _Ctx:
        session_id = "sess-r6"
        repo_id = "repo-r6"
        revision = "rev-r6"
        created_utc = "2026-11-20T00:00:00Z"

    class _FakeTrust:
        def __init__(self):
            self._key = b"selftest-trust-key"
            self.context = _Ctx()

        def canonical(self, payload):
            return json.dumps(
                payload, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")

        def sign(self, domain, payload):
            message = domain.encode("utf-8") + b"\x00" + self.canonical(payload)
            return hmac.new(self._key, message, hashlib.sha256).hexdigest()

        def verify(self, domain, payload, mac):
            if not mac:
                return False
            return hmac.compare_digest(str(mac), self.sign(domain, payload))

    class _FakeEnvelope:
        def __init__(self, data):
            self._data = data
            self.provenance = data.get("provenance")

        @classmethod
        def from_dict(cls, data, trust):
            payload = {k: v for k, v in data.items() if k != "mac"}
            if not trust.verify(evidence_domain, payload, data.get("mac")):
                raise ValueError("bad evidence MAC")
            return cls(data)

        def is_authorizing(self):
            return _prov_name(self.provenance) in ("TOOL_EXECUTION", "QUALIFIED")

    # Install a self-contained provenance module for the duration of the test.
    fake_provenance = types.ModuleType("assistant.provenance")
    fake_provenance.EvidenceEnvelope = _FakeEnvelope  # type: ignore[attr-defined]
    previous = sys.modules.get("assistant.provenance")
    sys.modules["assistant.provenance"] = fake_provenance

    try:
        trust = _FakeTrust()
        now = "2026-11-20T12:00:00Z"

        def signed_envelope(provenance="TOOL_EXECUTION"):
            env = {
                "evidence_id": "e1",
                "check_id": "sta_signoff",
                "gate_id": "STA",
                "provenance": provenance,
                "execution_identity": {"path": "C:/tools/sta.exe"},
                "consumed_sha256": "a" * 64,
                "source_path": "rtl/top.v",
                "dependencies": (),
                "status": "PASS",
                "observed_utc": now,
                "context": {
                    "session_id": "sess-r6",
                    "repo_id": "repo-r6",
                    "revision": "rev-r6",
                },
            }
            env["mac"] = trust.sign(evidence_domain, env)
            return env

        def signed_record(**overrides):
            record = {
                "transaction_id": "txn-r6-1",
                "state": "QUALIFIED_PASS",
                "context": {
                    "session_id": "sess-r6",
                    "repo_id": "repo-r6",
                    "revision": "rev-r6",
                },
                "transaction_complete": True,
                "deadline_utc": "2026-11-20T13:00:00Z",
                "evidence": [signed_envelope()],
                "consumed_sha256": {"rtl/top.v": "a" * 64},
                "dependencies": {"rtl/top.v": "b" * 64},
                "contradictions": [],
                "version": 1,
            }
            record.update(overrides)
            payload = {k: v for k, v in record.items() if k != "mac"}
            record["mac"] = trust.sign(auth_domain, payload)
            return record

        current_files = {"rtl/top.v": "a" * 64}
        current_deps = {"rtl/top.v": "b" * 64}

        valid = signed_record()
        decision = derive_authorization(
            valid,
            trust,
            now_utc=now,
            current_files=current_files,
            current_deps=current_deps,
        )
        assert decision.authorized is True, decision.reasons
        assert decision.reasons == ()
        assert decision.state is QualState.QUALIFIED_PASS
        assert decision.transaction_id == "txn-r6-1"
        print("SELFTEST valid record           -> authorized=True")

        def expect_denied(label, mutated):
            result = derive_authorization(
                mutated,
                trust,
                now_utc=now,
                current_files=current_files,
                current_deps=current_deps,
            )
            assert result.authorized is False, f"{label} unexpectedly authorized"
            assert result.reasons, f"{label} denied without reasons"
            print(f"SELFTEST {label:<30} -> authorized=False ({result.reasons[0]})")

        # MAC mutation (no re-sign).
        bad_mac = copy.deepcopy(valid)
        bad_mac["mac"] = "deadbeef"
        expect_denied("mac tampered", bad_mac)

        # Context mutation, re-signed so only check 3 rejects.
        bad_ctx = copy.deepcopy(valid)
        bad_ctx["context"]["repo_id"] = "other-repo"
        bad_ctx["mac"] = trust.sign(
            auth_domain, {k: v for k, v in bad_ctx.items() if k != "mac"}
        )
        expect_denied("context mismatch", bad_ctx)

        # State mutation, re-signed so only check 4 rejects.
        bad_state = signed_record(state="PENDING")
        expect_denied("state PENDING", bad_state)

        # Deadline expiry, re-signed so only check 6 rejects.
        bad_deadline = signed_record(deadline_utc="2026-11-20T11:00:00Z")
        expect_denied("deadline expired", bad_deadline)

        # Evidence provenance mutation, re-signed end to end.
        bad_ev = signed_envelope("INVALIDATED")
        bad_ev_record = signed_record(evidence=[bad_ev])
        expect_denied("evidence INVALIDATED", bad_ev_record)

        # Contradiction, re-signed so only check 10 rejects.
        bad_contra = signed_record(contradictions=["FAILED"])
        expect_denied("contradictions present", bad_contra)

        # Extra integrations checks (consumed / dependencies / unknown key).
        bad_consumed = signed_record(consumed_sha256={"rtl/top.v": "c" * 64})
        expect_denied("consumed digest drift", bad_consumed)
        bad_deps = signed_record(dependencies={"rtl/top.v": "d" * 64})
        expect_denied("dependency drift", bad_deps)
        bad_key = signed_record(qualified=True)  # type: ignore[arg-type]
        expect_denied("unknown key backdoor", bad_key)

        # legal_transition sanity.
        assert legal_transition(QualState.INITIAL, QualState.VERIFYING)
        assert legal_transition(QualState.FINALIZING, QualState.QUALIFIED_PASS)
        assert not legal_transition(QualState.QUALIFIED_PASS, QualState.FINALIZING)
        assert not legal_transition(QualState.INVALIDATED, QualState.QUALIFIED_PASS)
        assert not legal_transition(QualState.RECOVERED, QualState.QUALIFIED_PASS)
        assert not legal_transition(QualState.EXPIRED, QualState.RECOVERED)
        print("SELFTEST legal_transition         -> OK")

        print("ALL SELFTESTS PASSED (fail-closed authorization)")
    finally:
        if previous is None:
            sys.modules.pop("assistant.provenance", None)
        else:
            sys.modules["assistant.provenance"] = previous


if __name__ == "__main__":  # pragma: no cover
    _selftest()
