"""Deterministic gate controller: stage states, evidence binding, blocking.

Design rules enforced here (see docs/reviews/remediation-tracker.md, finding A,
and docs/reviews/second-review.md SR-03):

* Gate decisions are computed ONLY from recorded evidence records.
* LLM/human summaries are stored as advisory text and are never consulted by
  ``evaluate_gate``; ``force_pass`` raises ``GateBypassError``.
* Evidence whose registered inputs changed since recording is STALE and no
  longer counts (the check falls back to NOT_RUN until re-executed).
* A required check that is not PASS (or explicitly waived / validated
  NOT_APPLICABLE) blocks the gate, and a blocked gate blocks downstream
  stages.
* NOT_APPLICABLE requires an ``NA-RULE:`` reason PLUS registered design facts
  that corroborate the rule (never a free-text reason alone). Non-waivable
  functional-proof checks can never be NOT_APPLICABLE or waived (U-A).
* Waivers are explicit, scoped, identity-bound, revision-bound, expiring and
  surfaced in every gate report. Functional-proof failures are non-waivable.
* Clean PASS, WAIVED_PASS and NOT_APPLICABLE are distinct dispositions; a
  validated N/A gate is never reported as CLEAN_PASS (U-A).
* F-01: caller-labelled simulated/internal evidence is persisted with an
  explicit classification and exact label; it can only produce a
  SIMULATED_PASS disposition, which is visible and NOT qualified to authorize
  production work or downstream stages.
* F-07: applicability rules are bound to allowed check ids. NO-MACROS can only
  justify genuinely macro-specific checks and no non-physical rule can exempt
  physical verification.
* Strict provenance validation is the DEFAULT for substantive statuses; only
  explicitly labelled simulated checks may opt out with ``strict=False`` (U-B).
* Registered gate specifications define the authoritative required-check set,
  inputs, view and upstream dependencies: callers cannot shorten that set to
  obtain a PASS, and empty definitions/cycles are rejected.
* ALL recording boundaries (direct API, CLI, runner, ECO) pass through the
  single validation boundary ``validate_gate_submission``.
* State is persisted atomically so a cancelled run can be restarted without
  losing evidence; cancellation blocks all new work until a valid restart.
"""
from __future__ import annotations

import contextlib
import copy
import datetime as _dt
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Callable, Iterable, Mapping, Sequence

from .evidence import (
    BindingContext,
    DependencySet,
    Evidence,
    VIEW_CONTRACTS,
    ViewContract,
    artifact_path,
    contract_compatible,
    post_fill_proof_ok,
    prepare_run_output_dir,
    revalidate_evidence,
    utc_now,
    validate_provenance,
    view_compatible,
)
from .hashing import sha256_file
from .statuses import Status, blocks, parse_status


class GateBypassError(RuntimeError):
    """Raised when something tries to authorize a gate without evidence."""


class ApplicabilityError(ValueError):
    """Raised when a NOT_APPLICABLE reason fails validation."""


class StateError(RuntimeError):
    """Raised for illegal state transitions (e.g. work while cancelled)."""


class GateDefinitionError(ValueError):
    """Raised for empty/invalid gate specs, unknown checks or dependency cycles."""


class GateValidationError(ValueError):
    """Raised when a submitted check fails the authoritative validation boundary."""


# ---------------------------------------------------------------------------
# Ledger integrity primitive (R5 gate-definition trust; reusable by Agent 9)
# ---------------------------------------------------------------------------
# A gate definition is authoritative only when (a) it is derived from a
# registered/default GateSpec, or (b) it was recorded through the controller's
# authoritative API AND its persisted binding verifies under a key that lives
# OUTSIDE the ledger it protects.  A MAC stored next to its own key is not a
# security boundary (A7-04), so the key is resolved from the process environment
# or, when no key is provisioned, generated as a per-process ephemeral secret
# that is NEVER written to state.json.  A fresh process without the provisioned
# key therefore cannot verify persisted bindings and fails closed.
LEDGER_KEY_ENV = "ASSISTANT_LEDGER_KEY"
_LEDGER_KEY_LABEL = b"assistant-ledger-key/v1\x00"
_MIN_LEDGER_KEY_BYTES = 16
_LEDGER_BINDINGS_FIELD = "ledger_bindings"

#: Domain-separated MAC domains.  A binding made in one domain cannot be
#: replayed in another.  Agent 9 owns the evidence domain (see report).
DOMAIN_GATE_DEFINITION = "gate_definition/v1"
# R6: evidence provenance is bound under the SAME external/ephemeral trust root
# as gate definitions. There is no in-ledger key (P0-1 fix).
DOMAIN_EVIDENCE = "evidence/v1"
# R6: waivers are authorization decisions and must be MAC-bound too; a
# ledger-only attacker must not forge a waiver to qualify failing evidence.
DOMAIN_WAIVER = "waiver/v1"
# R6.3: one-time proof-nonce ownership is an authorization decision and must be
# MAC-bound under the SAME external key as every other ledger decision. The
# registry is durable (state.json) so replay from an earlier run/process fails
# closed. The proof's own nonce is the key -- NEVER a caller label.
DOMAIN_PROOF_NONCE = "proof_nonce/v1"
# R7.1: check_index membership is an AUTHORIZATION decision. It is bound under
# the SAME external key as evidence/waivers/nonces so a ledger-only writer cannot
# point one check at another check's evidence (A7-P0-01).
DOMAIN_CHECK_INDEX = "check_index/v1"
# EXT-01 (Agent A): append-only evidence manifest. Every evidence id EVER
# recorded must remain enumerated here, and the whole list is MAC-bound under
# the external key. This is the first ledger record whose ABSENCE (or whose
# entry removal) is detectable: all pre-existing checks compare a binding to a
# payload still present in the same state, so deleting both record and binding
# left no trace. A key-free writer cannot re-sign a shortened manifest.
DOMAIN_EVIDENCE_MANIFEST = "evidence_manifest/v1"
_EVIDENCE_MANIFEST_FIELD = "evidence_manifest"
_EVIDENCE_MANIFEST_RECORD = "all"

_EPHEMERAL_LEDGER_KEY: bytes | None = None
#: Process-wide critical section: record -> envelope mint -> index write -> nonce consume.
_PROOF_RECORD_LOCK = threading.RLock()
#: Re-entrancy counters for the OS-level per-ledger advisory lock.
_CROSS_PROCESS_LOCKS: dict[str, int] = {}
_CROSS_PROCESS_LOCK_GUARD = threading.RLock()


class LedgerIntegrityError(RuntimeError):
    """Raised when ledger integrity cannot be established (fail closed)."""


def _state_fingerprint(state: Mapping) -> str:
    """Content hash of a durable state; detects a newer on-disk snapshot."""
    try:
        body = json.dumps(
            state, sort_keys=True, separators=(",", ":"), default=str
        )
    except Exception:  # pragma: no cover - defensive
        return ""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _os_file_lock(handle, *, exclusive: bool) -> None:
    """Acquire/release an advisory lock on one byte of *handle* (best effort)."""
    try:  # Windows
        import msvcrt  # type: ignore

        mode = msvcrt.LK_NBLCK if exclusive else msvcrt.LK_UNLCK
        msvcrt.locking(handle.fileno(), mode, 1)
        return
    except ImportError:  # POSIX
        import fcntl  # type: ignore

        op = fcntl.LOCK_EX | fcntl.LOCK_NB if exclusive else fcntl.LOCK_UN
        fcntl.flock(handle.fileno(), op)


@contextlib.contextmanager
def ledger_critical_section(state_path):
    """The ONE cross-process boundary for every state transaction (R7.2 T1/T2).

    ``_PROOF_RECORD_LOCK`` serializes threads; an OS advisory lock on the
    sidecar ``<state>.lock`` file serializes processes.  The lock is acquired
    BEFORE the durable state is read and released only AFTER the fsync+replace
    that commits that same state.  Because the nonce ledger lives inside the
    same state file, the only lock ordering is ``state lock -> nonce ledger``;
    no nested lock on a DIFFERENT state is ever taken here, so no ordering
    deadlock is possible.  Re-entrant on the same state (evaluation ->
    qualification -> nonce consumption).

    Any acquisition/open failure or timeout is fail-closed: the caller gets
    ``LedgerIntegrityError`` and NO write occurs.  A process that dies while
    holding the OS lock loses it on process exit; the (harmless) lock file
    persists.
    """
    with _PROOF_RECORD_LOCK:
        # A6: canonicalise so two spellings of the same state (symlinks, ..)
        # cannot acquire two different locks and race on the same file.
        lock_path = os.path.realpath(os.path.abspath(str(state_path))) + ".lock"
        handle = None
        acquired = False
        with _CROSS_PROCESS_LOCK_GUARD:
            depth = _CROSS_PROCESS_LOCKS.get(lock_path, 0)
        if depth == 0:
            try:
                directory = os.path.dirname(lock_path) or "."
                os.makedirs(directory, exist_ok=True)
                handle = open(lock_path, "a+b")
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\x00")
                    handle.flush()
                    os.fsync(handle.fileno())
                handle.seek(0)
            except OSError as exc:
                if handle is not None:
                    try:
                        handle.close()
                    except OSError:  # pragma: no cover - defensive cleanup
                        pass
                raise LedgerIntegrityError(
                    f"could not open cross-process ledger lock {lock_path}: {exc}"
                ) from exc
            deadline = time.monotonic() + 30.0
            while True:
                try:
                    _os_file_lock(handle, exclusive=True)
                    acquired = True
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        try:
                            handle.close()
                        finally:
                            raise LedgerIntegrityError(
                                "could not acquire cross-process ledger lock "
                                f"{lock_path}: {exc}"
                            ) from exc
                    time.sleep(0.005)
        with _CROSS_PROCESS_LOCK_GUARD:
            _CROSS_PROCESS_LOCKS[lock_path] = (
                _CROSS_PROCESS_LOCKS.get(lock_path, 0) + 1
            )
        try:
            yield
        finally:
            with _CROSS_PROCESS_LOCK_GUARD:
                remaining = _CROSS_PROCESS_LOCKS.get(lock_path, 1) - 1
                if remaining <= 0:
                    _CROSS_PROCESS_LOCKS.pop(lock_path, None)
                else:
                    _CROSS_PROCESS_LOCKS[lock_path] = remaining
            if acquired and handle is not None:
                try:
                    _os_file_lock(handle, exclusive=False)
                finally:
                    handle.close()



def _derive_ledger_key(passphrase: str) -> bytes:
    """Derive a fixed-length HMAC key from an external secret passphrase."""
    return hashlib.sha256(_LEDGER_KEY_LABEL + passphrase.encode("utf-8")).digest()


def resolve_ledger_key(*, allow_ephemeral: bool = True) -> tuple[bytes, str]:
    """Resolve the ledger integrity key from OUTSIDE the ledger.

    Resolution order:
      1. ``ASSISTANT_LEDGER_KEY`` in the process environment.  This is the
         production/restart flow: the same provisioned passphrase yields the
         same key in every process, so persisted bindings keep verifying.
      2. If unset and *allow_ephemeral*, a per-process random key, generated
         once and cached at module scope.  It is never persisted, so a *new*
         process cannot verify bindings written by an earlier one (fail closed)
         while ordinary within-process flows and fresh ledger creation work.
      3. If unset and *allow_ephemeral=False*, raise ``LedgerIntegrityError``.
         Production callers that require a real external trust anchor should
         use this strict form.

    Returns ``(key_bytes, source)`` where source is ``"environment"`` or
    ``"ephemeral"``.
    """
    passphrase = os.environ.get(LEDGER_KEY_ENV, "")
    if passphrase:
        key = _derive_ledger_key(passphrase)
        if len(key) < _MIN_LEDGER_KEY_BYTES:  # pragma: no cover - defensive
            raise LedgerIntegrityError("derived ledger integrity key is too short")
        return key, "environment"
    if not allow_ephemeral:
        raise LedgerIntegrityError(
            f"{LEDGER_KEY_ENV} is not set; refusing to run without an external "
            "ledger integrity key"
        )
    global _EPHEMERAL_LEDGER_KEY
    if _EPHEMERAL_LEDGER_KEY is None:
        _EPHEMERAL_LEDGER_KEY = secrets.token_bytes(32)
    return _EPHEMERAL_LEDGER_KEY, "ephemeral"


class LedgerIntegrity:
    """Reusable HMAC binding primitive keyed from outside the ledger.

    ``domain`` separates record kinds (gate definitions vs. evidence, etc.).
    ``record_id`` is bound into the payload so a valid MAC for one record cannot
    be replayed onto another record with an identical body.  Agent 9 can reuse
    this class directly for evidence-provenance provenance bindings.
    """

    def __init__(self, key: bytes):
        if len(key) < _MIN_LEDGER_KEY_BYTES:
            raise LedgerIntegrityError("ledger integrity key is too short")
        self._key = bytes(key)

    def mac(self, domain: str, payload: Mapping, *, record_id: str = "") -> str:
        """Return the hex MAC for *payload* in *domain*."""
        body = json.dumps(
            {"record_id": record_id, "payload": payload},
            sort_keys=True, separators=(",", ":"), default=str,
        )
        message = (domain + "\x00" + body).encode("utf-8")
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def verify(
        self, domain: str, payload: Mapping | None, expected: str | None, *,
        record_id: str = "",
    ) -> bool:
        """Constant-time verification; missing data/MAC fails closed."""
        if payload is None or not expected:
            return False
        return hmac.compare_digest(
            str(expected), self.mac(domain, payload, record_id=record_id)
        )


def read_binding(state: Mapping, domain: str, record_id: str) -> str | None:
    """Read a persisted MAC (never secret) for one record in one domain."""
    return (
        (state.get(_LEDGER_BINDINGS_FIELD) or {})
        .get(domain, {})
        .get(record_id)
    )


def write_binding(state: dict, domain: str, record_id: str, mac_value: str) -> None:
    """Persist a MAC for one record in one domain."""
    state.setdefault(_LEDGER_BINDINGS_FIELD, {}).setdefault(domain, {})[record_id] = (
        str(mac_value)
    )


#: Maps a MAC domain to a resolver returning the payload that domain binds.
#: Agent 9 registers its evidence domain here so ``verify_ledger_integrity``
#: can cover it; the gate-definition domain is registered below.
BOUND_PAYLOAD_RESOLVERS: dict[
    str, Callable[[Mapping, str], Mapping | None]
] = {}


def _gate_definition_payload(state: Mapping, record_id: str) -> Mapping | None:
    return (state.get("gate_definitions") or {}).get(record_id)


BOUND_PAYLOAD_RESOLVERS[DOMAIN_GATE_DEFINITION] = _gate_definition_payload


def _evidence_payload(state: Mapping, record_id: str) -> Mapping | None:
    """R6: resolver for evidence + provenance (DOMAIN_EVIDENCE)."""
    ev = (state.get("evidence") or {}).get(record_id)
    if ev is None:
        return None
    prov = (state.get("evidence_provenance") or {}).get(record_id) or {}
    return {"evidence": ev, "provenance": prov}


BOUND_PAYLOAD_RESOLVERS[DOMAIN_EVIDENCE] = _evidence_payload


def _waiver_payload(state: Mapping, record_id: str) -> Mapping | None:
    for w in state.get("waivers") or []:
        if w.get("waiver_id") == record_id:
            return w
    return None


BOUND_PAYLOAD_RESOLVERS[DOMAIN_WAIVER] = _waiver_payload


def _proof_nonce_payload(state: Mapping, record_id: str) -> Mapping | None:
    """R6.3: payload bound for a consumed proof nonce (cross-run replay ledger)."""
    registry = state.get("used_proof_nonces") or {}
    if record_id not in registry:
        return None
    return {"nonce": record_id, "evidence_id": registry[record_id]}


BOUND_PAYLOAD_RESOLVERS[DOMAIN_PROOF_NONCE] = _proof_nonce_payload


def _check_index_payload(state: Mapping, record_id: str) -> Mapping | None:
    """R7.1: payload bound for one check's evidence-id list.

    ``record_id`` is the check_id. The payload carries the check_id explicitly
    as well, so a MAC valid for one check cannot be replayed under another
    check's index key even if the two id lists happen to be identical.
    """
    ids = (state.get("check_index") or {}).get(record_id)
    if ids is None:
        return None
    if isinstance(ids, (str, bytes)) or not isinstance(ids, (list, tuple)):
        return None
    return {"check_id": record_id, "evidence_ids": list(ids)}


BOUND_PAYLOAD_RESOLVERS[DOMAIN_CHECK_INDEX] = _check_index_payload


def _evidence_manifest_payload(state: Mapping, record_id: str) -> Mapping | None:
    """EXT-01: canonical payload for the append-only evidence manifest."""
    if str(record_id) != _EVIDENCE_MANIFEST_RECORD:
        return None
    manifest = state.get(_EVIDENCE_MANIFEST_FIELD)
    if isinstance(manifest, (str, bytes)) or not isinstance(manifest, (list, tuple)):
        return None
    return {"evidence_ids": [str(i) for i in manifest]}


BOUND_PAYLOAD_RESOLVERS[DOMAIN_EVIDENCE_MANIFEST] = _evidence_manifest_payload


def check_index_mac_payload(check_id: str, evidence_ids: Sequence[str]) -> dict:
    """Canonical, MAC-covered payload for a check_index association."""
    return {"check_id": str(check_id), "evidence_ids": [str(i) for i in evidence_ids]}


#: Applicability reasons must start with an explicit rule token and explain
#: why the check does not apply.  Rule ids live in APPLICABILITY_RULES.
APPLICABILITY_RULES: dict[str, str] = {
    "NA-RULE:NO-UPF": "design has no UPF/CPF power-intent file in scope",
    "NA-RULE:NO-MACROS": "design contains no hard macros (macro-specific checks only)",
    # TR-01: a standard-cell design can have placed/routed geometry. The absence
    # of macros is NOT a reason to exempt physical verification. This rule is
    # corroborated by a recorded implementation fact, not by caller prose.
    "NA-RULE:NO-PHYSICAL-IMPLEMENTATION": (
        "ECO candidate has no placed or routed physical implementation "
        "(netlist-only exploration)"
    ),
    "NA-RULE:NO-CLOCK-GATES": "design contains no integrated clock gates",
    "NA-RULE:NO-ANALOG": "design contains no analog/hard IP",
    "NA-RULE:COMBO-ONLY": "design is purely combinational (no sequential elements)",
}

#: Each applicability rule is corroborated by a validated design fact of the
#: shape ``{name, expected_value}``.  A reason alone is never sufficient once
#: design facts are available to the controller (SR-03 sub-finding 13).
APPLICABILITY_RULE_FACTS: dict[str, tuple[str, object]] = {
    "NA-RULE:NO-UPF": ("has_upf", False),
    "NA-RULE:NO-MACROS": ("has_macros", False),
    "NA-RULE:NO-PHYSICAL-IMPLEMENTATION": ("eco_physical_implementation_present", False),
    "NA-RULE:NO-CLOCK-GATES": ("has_clock_gates", False),
    "NA-RULE:NO-ANALOG": ("has_analog", False),
    "NA-RULE:COMBO-ONLY": ("is_combinational", True),
}

# F-07: bind applicability rules to the checks they may justify. A standard-cell
# design can have placed/routed geometry even with no hard macros, so an
# inventory rule that says nothing about physical scope can never exempt
# physical verification. Only the dedicated physical-implementation rule (which
# is corroborated by a registered design fact above) may do so.
PHYSICAL_VERIFICATION_CHECKS: frozenset[str] = frozenset(
    {
        "eco_physical_drc", "eco_physical_lvs", "eco_physical_antenna",
        "eco_physical_ir_drop", "physical_drc", "physical_lvs",
        "drc", "lvs", "antenna", "ir_drop", "sta", "timing",
        "parasitics", "gates", "clock_gates", "physical_verification",
    }
)

#: Macro-specific checks are the ONLY checks NA-RULE:NO-MACROS may justify.
MACRO_SPECIFIC_CHECKS: frozenset[str] = frozenset(
    {
        "macro_drc", "macro_lvs", "macro_antenna", "macro_current_density",
        "macro_ir_drop", "macro_timing", "macro_physical_verify",
    }
)

#: Rules permitted to justify N/A for a physical-verification check. Corroboration
#: by the physical-scope design fact is still mandatory (see FACTS above).
PHYSICAL_NA_RULES: frozenset[str] = frozenset(
    {"NA-RULE:NO-PHYSICAL-IMPLEMENTATION"}
)

#: Explicit rule -> allowed check-id bindings. Rules not listed are generic but
#: remain subject to the physical-check restriction above.
APPLICABILITY_RULE_CHECKS: dict[str, frozenset[str]] = {
    "NA-RULE:NO-MACROS": MACRO_SPECIFIC_CHECKS,
}


def _applicability_rule_allows_check(rule_token: str, check_id: str) -> bool:
    """Whether *rule_token* may justify NOT_APPLICABLE for *check_id*.

    Physical verification is never exempted by a non-physical inventory rule
    (macro/UPF/analog/clock-gate absence). Only NA-RULE:NO-PHYSICAL-IMPLEMENTATION
    may do so, and it must be corroborated by a registered design fact.
    """
    cid = (check_id or "").strip()
    if cid in PHYSICAL_VERIFICATION_CHECKS and rule_token not in PHYSICAL_NA_RULES:
        return False
    allowed = APPLICABILITY_RULE_CHECKS.get(rule_token)
    if allowed is None:
        return True
    if cid in allowed:
        return True
    # Prefix convention keeps macro-specific check families working.
    return cid.startswith("macro_") and cid not in PHYSICAL_VERIFICATION_CHECKS


MIN_REASON_LEN = 20
MIN_WAIVER_REASON_LEN = 20

#: Substantive statuses must carry full provenance when recorded. NOT_APPLICABLE
#: is not substantive: it is governed by validated design facts instead (U-A).
SUBSTANTIVE_STATUSES: frozenset[Status] = frozenset(
    {
        Status.PASS,
        Status.FAIL,
        Status.INCONCLUSIVE,
        Status.UNSUPPORTED,
        Status.NOT_RUN,
    }
)

#: Change classes that require explicit architectural authorization.
RESTRICTED_CHANGE_CLASSES = frozenset(
    {
        "rtl_behavior",
        "interface_latency",
        "clock_definition",
        "timing_exception",
        "power_intent",
        "verification_exclusion",
    }
)

#: Functional-proof checks can never be made PASS by a waiver. An unresolved
#: formal/equivalence failure is a hard blocker (SR-03 sub-finding 11).
NON_WAIVABLE_CHECKS: frozenset[str] = frozenset(
    {"formal_lec", "lec_formal", "functional_equiv", "formal_proof"}
)

#: Recognised waiver policies. "unspecified" is the legacy default and only
#: honoured for gates that do not carry a registered specification.
WAIVER_POLICIES: frozenset[str] = frozenset(
    {"temporary_risk_acceptance", "tool_gap", "unspecified"}
)

# EXT-05 / B-03 -- explicit waiver policy (intended semantics)
# ------------------------------------------------------------
# A waiver is a SUPERVISED AUTHORIZATION DECISION about reporting, never an
# authorization *grant*. It must never convert failed engineering evidence
# into an authorized (production-qualified) PASS:
#
# * A waiver may make a blocking status non-blocking for the gate *decision*
#   (decision=PASS, disposition=WAIVED_PASS, CLI exit 0) so the supervised
#   workflow can continue and the exception stays visible in the report.
# * A waived check keeps its underlying FAIL/INCONCLUSIVE/UNSUPPORTED/NOT_RUN
#   status in the check report. It is NON-AUTHORIZING: a gate that needed a
#   waiver is never ``qualified`` and never authorizes a downstream stage.
#
# HARD BLOCKERS -- can never be waived, even for the reporting disposition:
#   - functional-proof / equivalence checks (NON_WAIVABLE_CHECKS or a
#     registered CheckSpec with waivable=False): genuine PASS only;
#   - integrity / revalidation failures (revalidate_evidence problems), e.g.
#     modified/missing artifacts or changed pinned dependencies/context;
#   - structural evidence/check_index problems, invalid MACs and simulated
#     provenance: these already fail closed before the waiver decision;
#   - waivers with an invalid/missing MAC, wrong scope, wrong input revision
#     or expiry are ignored by waivers_for() (fail closed).
#
# WAIVABLE (reporting-only, non-authorizing): ancillary engineering checks
# whose failure is a documented, scoped, approved, revision-bound exception.
# Validated NOT_APPLICABLE is distinct from a waiver: it removes a check from
# scope entirely and may remain qualified.


def _parse_utc(value: str) -> _dt.datetime:
    """Parse an ISO UTC timestamp (``...Z`` or ``+00:00``) into an aware dt."""
    text = (value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = _dt.datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def validate_applicability_reason(
    reason: str,
    design_facts: Mapping | None = None,
    check_id: str = "",
) -> str:
    """Validate + normalize a NOT_APPLICABLE reason (U-A, F-07).

    Applicability is NEVER decided by a free-text reason string alone: validated
    design facts matching the rule (``APPLICABILITY_RULE_FACTS``) are mandatory.
    When ``check_id`` is supplied the rule is also bound to the target check
    (``APPLICABILITY_RULE_CHECKS``): NO-MACROS may only justify macro-specific
    checks and no non-physical rule may exempt physical verification. This is
    the single validator invoked from ``record_evidence`` so every recording
    boundary (direct API, CLI, ``mark_not_applicable``) is covered.
    A missing/empty ``design_facts`` mapping is rejected outright.
    """
    if not design_facts:
        raise ApplicabilityError(
            "NOT_APPLICABLE requires registered, validated design facts for rule "
            "corroboration; reason-only applicability is not accepted"
        )
    text = (reason or "").strip()
    if len(text) < MIN_REASON_LEN:
        raise ApplicabilityError(
            f"applicability reason must be at least {MIN_REASON_LEN} characters "
            f"(got {len(text)})"
        )
    if not text.startswith("NA-RULE:"):
        raise ApplicabilityError(
            "applicability reason must start with an explicit NA-RULE:<id> token"
        )
    rule_token = text.split()[0]
    if rule_token not in APPLICABILITY_RULES:
        raise ApplicabilityError(
            f"unknown applicability rule {rule_token!r}; known rules: "
            + ", ".join(sorted(APPLICABILITY_RULES))
        )
    # F-07: a rule can only corroborate checks it is bound to. Absence of macros
    # is not a physical-scope fact and can never exempt physical verification.
    if check_id and not _applicability_rule_allows_check(rule_token, check_id):
        raise ApplicabilityError(
            f"applicability rule {rule_token} cannot justify NOT_APPLICABLE for "
            f"check {check_id!r}: a non-physical inventory rule never exempts "
            "physical verification; use NA-RULE:NO-PHYSICAL-IMPLEMENTATION with "
            "corroborating implementation facts (a registered physical-scope "
            "design fact)"
        )
    # The reason must also say something beyond the rule id.
    if len(text) <= len(rule_token) + MIN_REASON_LEN // 2:
        raise ApplicabilityError(
            "applicability reason must justify the rule, not only name it"
        )
    contract = APPLICABILITY_RULE_FACTS.get(rule_token)
    if contract is None:
        raise ApplicabilityError(
            f"applicability rule {rule_token} has no validated design-fact contract"
        )
    fact_name, expected = contract
    if fact_name not in design_facts:
        raise ApplicabilityError(
            f"missing validated design fact {fact_name!r} required by {rule_token}"
        )
    raw = design_facts[fact_name]
    actual = raw.get("value") if isinstance(raw, Mapping) else raw
    if actual != expected:
        raise ApplicabilityError(
            f"validated design fact {fact_name}={actual!r} contradicts {rule_token}"
        )
    return text


def validate_applicability_for_check(
    reason: str,
    check_id: str,
    design_facts: Mapping,
) -> str:
    """Authoritative applicability helper for CLI/runner callers (U-A).

    Validates a NOT_APPLICABLE decision for a *target check id* against the
    registered design facts. Non-waivable functional-proof checks can never be
    declared NOT_APPLICABLE. Returns the normalized reason; raises
    ``ApplicabilityError`` on any violation.
    """
    if not check_id:
        raise ApplicabilityError("a target check_id is required for applicability")
    if check_id in NON_WAIVABLE_CHECKS:
        raise ApplicabilityError(
            f"non-waivable check {check_id!r} cannot be NOT_APPLICABLE; "
            "genuine PASS evidence is required"
        )
    return validate_applicability_reason(reason, design_facts, check_id=check_id)


# Backwards-compatible private alias (kept so existing callers do not break).
_validate_applicability_reason = validate_applicability_reason


def evidence_view_contract(ev: Evidence) -> ViewContract:
    """Build the explicit contract an evidence record actually provides.

    The base dimensions come from the recorded ``analysis_view`` (sound contract
    model, NOT the legacy total order); the binding dimensions (corner, library
    and extraction provenance) come from the evidence's recorded binding context.
    """
    base = VIEW_CONTRACTS.get(ev.analysis_view, VIEW_CONTRACTS["UNKNOWN"])
    return ViewContract(
        artifact_type=base.artifact_type,
        implementation_stage=base.implementation_stage,
        analysis_type=base.analysis_type,
        parasitic_source=base.parasitic_source,
        fill_status=base.fill_status,
        timing_mode=base.timing_mode,
        corner=ev.corner or base.corner,
        library_provenance=ev.library_provenance or base.library_provenance,
        extraction_provenance=ev.extraction_provenance or base.extraction_provenance,
    )


# ---------------------------------------------------------------------------
# Registered gate / check specifications (SR-03 sub-findings 3, 6, 7, 11)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CheckSpec:
    """Contract for a single required check.

    ``execution_kind`` is ``"tool"`` (an external process ran) or ``"internal"``
    (a deterministic in-process validator ran).  Tool checks must present an
    explicit successful process result (``exit_code == 0``) *and* a parsed
    engineering outcome validated by ``engineering_validator``.  Internal
    checks must declare an explicit ``validator_type`` and ``evidence_contract``.
    """

    check_id: str
    execution_kind: str = "tool"  # "tool" | "internal"
    required_view: str | None = None
    engineering_validator: str = ""  # tool: name in OUTCOME_VALIDATORS
    validator_type: str = ""  # internal: explicit validator kind
    evidence_contract: str = ""  # internal: what evidence must be present
    waivable: bool = True
    required_inputs: tuple[str, ...] = ()
    description: str = ""

    # -- SR-04/SR-05 evaluation-time expectations (serialized forms) ---------
    #: Serialized :class:`DependencySet` (persistent paths + hashes) that must
    #: still match at gate evaluation time.
    expected_dependencies: Mapping[str, object] = field(default_factory=dict)
    #: Serialized :class:`BindingContext` (run/design/stage/tool/view/corner).
    expected_context: Mapping[str, object] = field(default_factory=dict)
    #: Artifacts must live under this isolated run output directory.
    expected_output_dir: str = ""
    #: Serialized :class:`ViewContract` for explicit, multi-dimensional checks.
    view_contract: Mapping[str, object] = field(default_factory=dict)
    require_post_fill_proof: bool = False
    require_extraction_provenance: bool = False

    def dependency_expectation(self) -> DependencySet | None:
        return DependencySet.from_dict(self.expected_dependencies) if self.expected_dependencies else None

    def context_expectation(self) -> BindingContext | None:
        return BindingContext.from_dict(self.expected_context) if self.expected_context else None

    def contract_expectation(self) -> ViewContract | None:
        return ViewContract.from_dict(self.view_contract) if self.view_contract else None

    def validate(self) -> None:
        if not self.check_id:
            raise GateDefinitionError("CheckSpec requires a non-empty check_id")
        if self.execution_kind not in ("tool", "internal"):
            raise GateDefinitionError(
                f"unknown execution_kind {self.execution_kind!r} for {self.check_id}"
            )
        if self.execution_kind == "tool" and not self.engineering_validator:
            raise GateDefinitionError(
                f"tool check {self.check_id} requires an engineering_validator"
            )
        if self.execution_kind == "internal":
            if not self.validator_type:
                raise GateDefinitionError(
                    f"internal check {self.check_id} requires a validator_type"
                )
            if not self.evidence_contract:
                raise GateDefinitionError(
                    f"internal check {self.check_id} requires an evidence_contract"
                )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["required_inputs"] = list(self.required_inputs)
        return d

    @classmethod
    def from_dict(cls, d: Mapping) -> "CheckSpec":
        data = dict(d)
        data["required_inputs"] = tuple(data.get("required_inputs") or ())
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class GateSpec:
    """Authoritative definition of a gate.

    ``required_checks`` is the full set a caller must satisfy; a caller may not
    pass a shorter list to ``evaluate_gate`` and obtain a PASS. ``required_inputs``
    must be registered and bound into evidence; ``required_view`` constrains the
    analysis view; ``upstream`` names gates that must be *re-evaluated* first.
    """

    gate_id: str
    required_checks: tuple[str, ...]
    required_inputs: tuple[str, ...] = ()
    required_view: str | None = None
    upstream: tuple[str, ...] = ()
    checks: Mapping[str, CheckSpec] = field(default_factory=dict)
    description: str = ""

    # -- SR-04/SR-05 evaluation-time expectations (serialized forms) ---------
    #: Serialized :class:`DependencySet` required for every check in this gate.
    expected_dependencies: Mapping[str, object] = field(default_factory=dict)
    #: Serialized :class:`BindingContext` (design/corner binding) for the gate.
    expected_context: Mapping[str, object] = field(default_factory=dict)
    #: Isolated run output directory all evidence artifacts must live under.
    expected_output_dir: str = ""
    #: Serialized :class:`ViewContract` pinning every compatibility dimension.
    view_contract: Mapping[str, object] = field(default_factory=dict)
    require_post_fill_proof: bool = False
    require_extraction_provenance: bool = False

    def dependency_expectation(self) -> DependencySet | None:
        return DependencySet.from_dict(self.expected_dependencies) if self.expected_dependencies else None

    def context_expectation(self) -> BindingContext | None:
        return BindingContext.from_dict(self.expected_context) if self.expected_context else None

    def contract_expectation(self) -> ViewContract | None:
        return ViewContract.from_dict(self.view_contract) if self.view_contract else None

    def validate(self) -> None:
        if not self.gate_id:
            raise GateDefinitionError("GateSpec requires a non-empty gate_id")
        if not self.required_checks:
            raise GateDefinitionError(
                f"gate {self.gate_id!r} has an empty required-check list"
            )
        if len(set(self.required_checks)) != len(self.required_checks):
            raise GateDefinitionError(
                f"gate {self.gate_id!r} has duplicate required checks"
            )
        for cid in self.required_checks:
            if cid not in self.checks:
                raise GateDefinitionError(
                    f"gate {self.gate_id!r}: no CheckSpec for required check {cid!r}"
                )
            self.checks[cid].validate()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["required_checks"] = list(self.required_checks)
        d["required_inputs"] = list(self.required_inputs)
        d["upstream"] = list(self.upstream)
        d["checks"] = {k: v.to_dict() for k, v in self.checks.items()}
        return d

    @classmethod
    def from_dict(cls, d: Mapping) -> "GateSpec":
        data = dict(d)
        data["required_checks"] = tuple(data.get("required_checks") or ())
        data["required_inputs"] = tuple(data.get("required_inputs") or ())
        data["upstream"] = tuple(data.get("upstream") or ())
        data["checks"] = {
            k: CheckSpec.from_dict(v) for k, v in (data.get("checks") or {}).items()
        }
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


#: Registered outcome validators (engineering verdict parsers / internal
#: validators).  A validator returns True only when the parsed outcome is a
#: valid engineering result consistent with the evidence.
OUTCOME_VALIDATORS: dict[str, Callable[[Evidence, Mapping], bool]] = {}


def register_outcome_validator(
    name: str, fn: Callable[[Evidence, Mapping], bool]
) -> None:
    """Register a named parsed-outcome validator used by check specs."""
    if not name or not callable(fn):
        raise GateDefinitionError("outcome validator needs a name and a callable")
    OUTCOME_VALIDATORS[name] = fn


def default_gate_specs() -> dict[str, GateSpec]:
    """Canonical example specifications (not auto-registered).

    Callers/CLI/runner/ECO can register these via ``GateController.register_gate``
    to obtain authoritative, non-shortenable gate evaluation.
    """
    specs = {
        "STA_SIGNOFF": GateSpec(
            gate_id="STA_SIGNOFF",
            required_checks=("sta", "parasitics"),
            required_inputs=("rtl", "sdc"),
            required_view="ROUTED_EXTRACTED",
            checks={
                "sta": CheckSpec(
                    check_id="sta", execution_kind="tool",
                    required_view="ROUTED_EXTRACTED",
                    engineering_validator="sta_verdict",
                ),
                "parasitics": CheckSpec(
                    check_id="parasitics", execution_kind="tool",
                    required_view="ROUTED_EXTRACTED",
                    engineering_validator="parasitic_extraction",
                ),
            },
        ),
        "LEC_SIGNOFF": GateSpec(
            gate_id="LEC_SIGNOFF",
            required_checks=("formal_lec",),
            required_inputs=("rtl", "netlist"),
            checks={
                "formal_lec": CheckSpec(
                    check_id="formal_lec", execution_kind="tool",
                    engineering_validator="equivalence_verdict",
                    waivable=False,  # unresolved functional proof is non-waivable
                ),
            },
        ),
    }
    for spec in specs.values():
        spec.validate()
    return specs


# ---------------------------------------------------------------------------
# Authoritative validation boundary (SR-03 sub-finding 1)
# ---------------------------------------------------------------------------
def validate_gate_submission(
    ev: Evidence,
    *,
    spec: CheckSpec | None = None,
    design_facts: Mapping | None = None,
    engineering_outcome: Mapping | None = None,
    strict_provenance: bool = False,
    required_inputs: Sequence[str] = (),
) -> list[str]:
    """Validate any evidence submission before it is recorded.

    This is the ONE authoritative boundary shared by the direct API, CLI,
    runner and ECO.  CLI callers should invoke it with ``strict_provenance=True``
    (which additionally enforces ``assistant.evidence.validate_provenance``);
    the controller invokes it on every ``record_evidence`` call.  It returns a
    list of validation errors (empty means valid) rather than mutating state.

    NOT_APPLICABLE evidence is validated through ``validate_applicability_reason``
    (which raises ``ApplicabilityError`` on a bad/mis-corroborated reason) and is
    exempt from tool/internal substantive requirements.
    """
    errors: list[str] = []
    if ev.status is Status.NOT_APPLICABLE:
        # U-A: non-waivable functional-proof checks can never be N/A.
        if ev.check_id in NON_WAIVABLE_CHECKS or (spec is not None and not spec.waivable):
            errors.append(
                f"non-waivable check {ev.check_id!r} cannot be NOT_APPLICABLE; "
                "genuine PASS evidence is required"
            )
        # Raises ApplicabilityError when facts are missing/mismatched or when the
        # rule is not bound to this check (F-07). Reason-only N/A is rejected in
        # this authoritative boundary.
        validate_applicability_reason(
            ev.applicability_reason or ev.reason, design_facts, check_id=ev.check_id
        )
        return errors

    if not (ev.reason or "").strip():
        errors.append("a substantive reason is required")
    if strict_provenance:
        errors.extend(validate_provenance(ev))

    kind = spec.execution_kind if spec is not None else "tool"
    if kind == "tool":
        if spec is not None:
            if ev.exit_code != 0:
                errors.append(
                    f"tool-executed check {spec.check_id} requires an explicit "
                    f"successful process result (exit_code 0), got {ev.exit_code!r}"
                )
            if engineering_outcome is None:
                errors.append(
                    f"tool-executed check {spec.check_id} requires a parsed "
                    "engineering outcome"
                )
            else:
                validator = OUTCOME_VALIDATORS.get(spec.engineering_validator)
                if validator is None:
                    errors.append(
                        f"unknown engineering validator {spec.engineering_validator!r}"
                    )
                elif not validator(ev, engineering_outcome):
                    errors.append(
                        f"engineering outcome rejected for {spec.check_id}"
                    )
        # Legacy/no-spec checks are validated for PASS consistency in
        # evaluate_gate (exit_code=None never yields a clean PASS).
    else:  # internal
        assert spec is not None
        if not spec.validator_type:
            errors.append(f"internal check {spec.check_id} requires a validator_type")
        if not spec.evidence_contract:
            errors.append(f"internal check {spec.check_id} requires an evidence_contract")
        if engineering_outcome is None:
            errors.append(f"internal check {spec.check_id} requires a parsed engineering outcome")
        validator = OUTCOME_VALIDATORS.get(spec.validator_type)
        if spec.validator_type and validator is None:
            errors.append(f"unknown internal validator {spec.validator_type!r}")
        elif validator is not None and engineering_outcome is not None:
            if not validator(ev, engineering_outcome):
                errors.append(f"internal validation rejected for {spec.check_id}")

    for label in required_inputs:
        if label not in ev.input_hashes:
            errors.append(f"evidence is not bound to required input {label!r}")
    return errors


def assert_valid_gate_submission(ev: Evidence, **kwargs) -> None:
    """Raise ``GateValidationError`` if the submission fails validation."""
    errors = validate_gate_submission(ev, **kwargs)
    if errors:
        raise GateValidationError("; ".join(errors))


def evaluate_gate_from_state(
    state_path: str | os.PathLike[str], gate_id: str, required_checks: Sequence[str],
    **kwargs,
) -> "GateDecision":
    """Convenience entry point for CLI/runner/ECO callers."""
    return GateController(state_path).evaluate_gate(gate_id, required_checks, **kwargs)


# ---------------------------------------------------------------------------
# Waiver / decision records
# ---------------------------------------------------------------------------
@dataclass
class Waiver:
    check_id: str
    scope: str
    reason: str
    approved_by: str
    created_utc: str = field(default_factory=utc_now)
    expires_utc: str | None = None
    waiver_id: str = field(
        default_factory=lambda: f"W{utc_now()}".replace(":", "") + uuid.uuid4().hex[:8]
    )
    #: Input hashes captured when the waiver was granted. A later input change
    #: invalidates the waiver (it applies only to the revision it was granted for).
    input_revision: dict[str, str] = field(default_factory=dict)
    policy: str = "unspecified"
    design_scope: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping) -> "Waiver":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})  # type: ignore[attr-defined]

    def covers(self, gate_id: str, check_id: str) -> bool:
        """A waiver is scoped: it must name the check and the gate scope."""
        if self.check_id != check_id:
            return False
        if self.scope in ("*", "all", gate_id):
            return True
        # scope may be "gate:xxx" or a comma list
        tokens = [t.strip() for t in self.scope.replace(";", ",").split(",")]
        return gate_id in tokens or f"gate:{gate_id}" in tokens


@dataclass
class GateDecision:
    gate_id: str
    decision: str  # PASS | BLOCKED
    reason: str
    checks: dict[str, dict] = field(default_factory=dict)
    waivers: list[dict] = field(default_factory=list)
    advisory_summaries: int = 0
    decided_utc: str = field(default_factory=utc_now)
    #: CLEAN_PASS | WAIVED_PASS | NOT_APPLICABLE | BLOCKED -- clean PASS is
    #: distinct from waived and from validated-N/A dispositions (U-A).
    disposition: str = "CLEAN_PASS"
    waived_checks: list[str] = field(default_factory=list)
    na_checks: list[str] = field(default_factory=list)
    #: F-01: check ids whose PASS was derived from caller-labelled simulated
    #: evidence. A SIMULATED_PASS decision is not authoritative and is never
    #: qualified for production / downstream stage authorization.
    simulated_checks: list[str] = field(default_factory=list)
    #: True only for a genuinely authoritative PASS. Never true for
    #: SIMULATED_PASS (F-01) or WAIVED_PASS (EXT-05): a waived failure remains a
    #: failure and carries no downstream authorization weight.
    qualified: bool = True

    @property
    def passed(self) -> bool:
        return self.decision == "PASS"

    @property
    def human_summary(self) -> str:
        summary = f"[{self.disposition}] {self.gate_id}: {self.reason}"
        if self.simulated_checks:
            summary += (
                " (SIMULATED / NON-AUTHORITATIVE: "
                + ", ".join(self.simulated_checks)
                + ")"
            )
        return summary

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------
class GateController:
    """Owns evidence records, waivers and gate evaluation for one workspace."""

    @staticmethod
    def _blank_state() -> dict:
        """A fresh, empty durable-state skeleton (shared by init and reload)."""
        return {
            "version": 2,
            "run_status": "ACTIVE",
            "cancel_reason": "",
            "restart_history": [],
            "evidence": {},       # evidence_id -> record
            # EXT-01: append-only list of every evidence id ever recorded.
            # Never pruned on deletion: a missing record stays detectable.
            "evidence_manifest": [],
            "check_index": {},    # check_id -> [evidence_id, ...] (ordered)
            "waivers": [],
            "summaries": [],      # advisory only, never read by evaluate_gate
            "gate_history": [],
            "registered_inputs": {},  # label -> path (persisted across restart)
            "design_facts": {},       # validated design facts
            "submissions": {},        # evidence_id -> parsed outcome / spec
            "gate_specs": {},         # persisted registered gate specs (AUTHORITATIVE)
            "check_specs": {},        # persisted registered check specs (AUTHORITATIVE)
            # R4 state-trust boundary. gate_definitions and evidence_integrity
            # are AUTHORITATIVE gate/evidence metadata; gate_history (below) is
            # DESCRIPTIVE cache and is never consulted for required_checks.
            "gate_definitions": {},   # gate_id -> pinned authoritative definition
            "evidence_integrity": {},  # evidence_id -> HMAC binding (provenance included)
            # R6.3: durable, external-key-bound proof-nonce ownership ledger.
            # nonce -> evidence_id. A nonce may only ever be owned by ONE
            # evidence item; reuse by another item is denied fail-closed.
            "used_proof_nonces": {},
            "_integrity_key": "",     # legacy field: purged, never used
            "created_utc": utc_now(),
            "updated_utc": utc_now(),
        }

    def __init__(self, state_path: str | os.PathLike[str]):
        self.state_path = str(state_path)
        # RC P0-A fix: an IMMEDIATE in-process cancellation latch. cancel() sets
        # this synchronously BEFORE the (slower) durable transaction, so ECO's
        # commit-time guard observes cancellation even if the cross-process
        # lock/fsync transaction has not yet committed run_status to state.json.
        self._cancel_latch: str = ""
        self._state: dict = self._blank_state()
        self._disk_fingerprint = _state_fingerprint(self._state)
        if os.path.exists(self.state_path):
            with open(self.state_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self._disk_fingerprint = _state_fingerprint(loaded)
            self._state.update(loaded)

        # R5: ledger integrity key resolved from OUTSIDE the ledger. Never
        # written to state.json. A fresh process with no provisioned key gets a
        # new ephemeral key and therefore cannot verify persisted bindings.
        self._ledger_key, self._ledger_key_source = resolve_ledger_key()
        self.ledger_integrity = LedgerIntegrity(self._ledger_key)
        self._unverifiable_definitions: list[str] = []
        self._state.setdefault(_LEDGER_BINDINGS_FIELD, {})

        # R6 P0-1 FIX: no provenance key is generated, read, or persisted from
        # the ledger. Evidence provenance is bound with the SAME external
        # (env/provisioned or per-process ephemeral) key used for gate
        # definitions, via self.ledger_integrity. A ledger-only attacker cannot
        # obtain the key, therefore cannot recompute a forged provenance MAC.
        # Any legacy `_integrity_key` left in an old state file is purged and
        # NEVER used.
        self._state.pop("_integrity_key", None)

        # Input registry and design facts live *in state* so they survive a
        # restart (SR-03 sub-finding 9). ``self._inputs`` is a live view.
        self._inputs: dict[str, str] = self._state.setdefault("registered_inputs", {})
        self._state.setdefault("design_facts", {})
        self._state.setdefault("submissions", {})
        self._state.setdefault("gate_specs", {})
        self._state.setdefault("check_specs", {})
        self._state.setdefault("restart_history", [])
        # F-01: independent persisted record of evidence provenance. Gate
        # evaluation consults BOTH this map and the evidence record so tampering
        # with a single serialized record cannot strip simulated classification.
        self._state.setdefault("evidence_provenance", {})
        self._state.setdefault("gate_definitions", {})
        self._state.setdefault("evidence_integrity", {})
        self._state.setdefault("used_proof_nonces", {})

        self.gate_specs: dict[str, GateSpec] = {}
        self.check_specs: dict[str, CheckSpec] = {}
        for gid, data in self._state["gate_specs"].items():
            try:
                self.gate_specs[gid] = GateSpec.from_dict(data)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                continue
        for cid, data in self._state["check_specs"].items():
            try:
                self.check_specs[cid] = CheckSpec.from_dict(data)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                continue

        # R5: authoritative gate definitions pinned on first evaluation. A
        # definition is authoritative ONLY when its binding verifies under the
        # external key. A forged/unbound definition is loaded into raw state for
        # tamper evidence but is NOT admitted as authoritative, so an upstream
        # re-evaluation fails closed instead of trusting it (A7-03).
        self.gate_definitions: dict[str, dict] = {}
        for gid, definition in (self._state.get("gate_definitions") or {}).items():
            expected = read_binding(self._state, DOMAIN_GATE_DEFINITION, gid)
            if self.ledger_integrity.verify(
                DOMAIN_GATE_DEFINITION, definition, expected, record_id=gid
            ):
                self.gate_definitions[gid] = definition
            else:
                self._unverifiable_definitions.append(gid)
        # R4: default_gate_specs() now has a REAL call site in the gate-evaluation
        # path. Production gate definitions are authoritative even if a caller
        # never registered one; explicitly registered specs still take priority.
        self._default_specs: dict[str, GateSpec] = default_gate_specs()

        # R7.1: migrate only *consistent* legacy index entries to the new
        # external-key binding. Inconsistent entries stay unbound and fail
        # closed (see _migrate_check_index); migration never authenticates a
        # ledger-only substitution.
        with _PROOF_RECORD_LOCK:
            self._migrate_check_index()

    # -- persistence & the single transaction boundary -----------------------
    def _save(self) -> None:
        """Low-level atomic writer.

        R7.2: production callers MUST go through :meth:`_mutate`; this method is
        retained only as the durable writer used by that transaction (and by a
        few explicit test fixtures).  It fsyncs the temp file before the rename
        so an acknowledged write is durable (T5).
        """
        self._state["updated_utc"] = utc_now()
        directory = os.path.dirname(os.path.abspath(self.state_path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_path)  # atomic on POSIX and Windows
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _raw_disk_state(self) -> dict:
        """The durable JSON mapping exactly as stored (no defaults added)."""
        if not os.path.exists(self.state_path):
            return {}
        with open(self.state_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, Mapping):
            raise LedgerIntegrityError(
                f"durable state {self.state_path!r} is not a mapping"
            )
        return dict(loaded)

    def durable_state_is_current(self) -> bool:
        """True when this controller's last known disk snapshot is unchanged.

        A1: a stand-alone authorization read may honour in-memory edits only
        while the durable snapshot they were derived from is still current. If
        another process has committed a newer state, the caller MUST use the
        freshly reloaded state instead.
        """
        try:
            return _state_fingerprint(self._raw_disk_state()) == self._disk_fingerprint
        except Exception:  # noqa: BLE001 - corrupt/unreadable state fails closed
            return False

    def _load_state_fresh(self) -> dict:
        """Reload durable state from disk into a NEW dict (never a stale cache).

        Missing keys are defaulted exactly as on a fresh controller and the
        legacy in-ledger key is purged.  A corrupt/unreadable state file raises:
        the transaction then fails closed without writing anything.
        """
        fresh = self._blank_state()
        fresh.update(self._raw_disk_state())
        fresh.pop("_integrity_key", None)
        for key in (
            "ledger_bindings", "evidence", "evidence_manifest", "check_index",
            "waivers", "summaries",
            "gate_history", "registered_inputs", "design_facts", "submissions",
            "gate_specs", "check_specs", "gate_definitions", "evidence_integrity",
            "used_proof_nonces", "evidence_provenance",
        ):
            fresh.setdefault(key, {})
        fresh.setdefault("restart_history", [])
        fresh.setdefault(_LEDGER_BINDINGS_FIELD, {})
        return fresh

    def _refresh_derived_views(self) -> None:
        """Re-derive in-memory views IN PLACE from ``self._state`` (A2).

        ``self._inputs``, ``gate_specs``, ``check_specs`` and ``gate_definitions``
        are live views held by callers (eco.py / runner.py / tests). A reload
        must clear+update the SAME objects, never rebind them, otherwise writes
        through those views go to a detached dictionary.
        """
        registered = dict(self._state.get("registered_inputs") or {})
        if not hasattr(self, "_inputs") or self._inputs is None:
            self._inputs = registered
        else:
            self._inputs.clear()
            self._inputs.update(registered)
        # The live view MUST be the very object persisted under this key:
        # register_input() writes through self._inputs, so a detached copy would
        # silently drop registrations (A2 detached-dict bug).
        self._state["registered_inputs"] = self._inputs
        for attr in ("gate_specs", "check_specs", "gate_definitions"):
            if not hasattr(self, attr) or getattr(self, attr) is None:
                setattr(self, attr, {})
        self.gate_specs.clear()
        self.check_specs.clear()
        self.gate_definitions.clear()
        if not hasattr(self, "_unverifiable_definitions") or self._unverifiable_definitions is None:
            self._unverifiable_definitions = []
        self._unverifiable_definitions.clear()
        for gid, data in (self._state.get("gate_specs") or {}).items():
            try:
                self.gate_specs[gid] = GateSpec.from_dict(data)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                continue
        for cid, data in (self._state.get("check_specs") or {}).items():
            try:
                self.check_specs[cid] = CheckSpec.from_dict(data)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                continue
        for gid, definition in (self._state.get("gate_definitions") or {}).items():
            expected = read_binding(self._state, DOMAIN_GATE_DEFINITION, gid)
            if self.ledger_integrity.verify(
                DOMAIN_GATE_DEFINITION, definition, expected, record_id=gid
            ):
                self.gate_definitions[gid] = definition
            else:
                self._unverifiable_definitions.append(gid)
        self._default_specs = default_gate_specs()

    @staticmethod
    def _fail_evidences(state: Mapping) -> set[str]:
        """Evidence ids whose durable status is FAIL (append-only, never lost)."""
        return {
            str(eid) for eid, rec in (state.get("evidence") or {}).items()
            if isinstance(rec, Mapping)
            and parse_status(rec.get("status", "")) is Status.FAIL
        }

    def _validate_transaction(self, baseline: dict, final: dict) -> None:
        """Fail closed on any invariant the transaction itself would break.

        Problems that ALREADY existed in the freshly-read durable state are not
        introduced by this write, so they are compared as sets and only NEW
        problems (or a lost FAIL) abort the commit.  This preserves the existing
        "tampered state evaluates as BLOCKED" semantics while guaranteeing a
        writer can never persist a stale snapshot over newer authorization.
        """
        baseline_fails = self._fail_evidences(baseline)
        final_fails = self._fail_evidences(final)
        lost = sorted(baseline_fails - final_fails)
        if lost:
            raise LedgerIntegrityError(
                "transaction would lose a durable FAIL: " + ", ".join(lost)
            )
        base_problems = set(self._check_index_problems_for(baseline))
        final_problems = set(self._check_index_problems_for(final))
        introduced = sorted(final_problems - base_problems)
        if introduced:
            raise LedgerIntegrityError(
                "transaction would introduce check_index inconsistency: "
                + "; ".join(introduced)
            )
        # EXT-01: the manifest is append-only. No transaction may remove an id
        # that was already recorded, and no transaction may introduce a new
        # manifest integrity problem. This closes the in-process laundering path
        # even for callers that hold the external key.
        base_manifest = [
            str(i) for i in (baseline.get(_EVIDENCE_MANIFEST_FIELD) or [])
        ]
        final_manifest = [
            str(i) for i in (final.get(_EVIDENCE_MANIFEST_FIELD) or [])
        ]
        lost_manifest = sorted(set(base_manifest) - set(final_manifest))
        if lost_manifest:
            raise LedgerIntegrityError(
                "transaction would remove evidence from the append-only manifest: "
                + ", ".join(lost_manifest)
            )
        base_mprob = set(self._evidence_manifest_problems_for(baseline))
        final_mprob = set(self._evidence_manifest_problems_for(final))
        introduced_mprob = sorted(final_mprob - base_mprob)
        if introduced_mprob:
            raise LedgerIntegrityError(
                "transaction would introduce evidence-manifest inconsistency: "
                + "; ".join(introduced_mprob)
            )
        base_nonces = baseline.get("used_proof_nonces") or {}
        for nonce, eid in (final.get("used_proof_nonces") or {}).items():
            prior = base_nonces.get(nonce)
            if prior is not None and str(prior) == str(eid):
                # Pre-existing owner: never invalidate an unrelated write just
                # because this process has a different ledger key. Unverifiable
                # bindings are reported by verify_ledger_integrity() separately.
                continue
            expected = read_binding(final, DOMAIN_PROOF_NONCE, str(nonce))
            if not self.ledger_integrity.verify(
                DOMAIN_PROOF_NONCE,
                {"nonce": str(nonce), "evidence_id": str(eid)},
                expected,
                record_id=str(nonce),
            ):
                raise LedgerIntegrityError(
                    f"transaction would persist an unbound proof-nonce owner: {nonce}"
                )

    def _mutate(self, fn: Callable[[dict], object]) -> object:
        """Apply ``fn`` to FRESH durable state inside ONE cross-process transaction.

        Steps (T1): acquire the cross-process lock; reload durable state; rebind
        derived views; run ``fn``; validate invariants; fsync+replace; adopt the
        committed state.  A nested call shares the outer transaction's fresh
        state (re-entrant), so qualification + nonce consumption + history are
        one atomic commit.  Any failure restores the caller's previous snapshot
        and writes nothing.
        """
        with ledger_critical_section(self.state_path):
            depth = getattr(self, "_txn_depth", 0)
            if depth:
                # Re-entrant: the outer transaction already owns fresh state.
                self._txn_depth = depth + 1
                try:
                    return fn(self._state)
                finally:
                    self._txn_depth = depth
            # A2: keep object identity. Callers (eco/runner/tests) hold live
            # references to self._state / self._inputs / the spec caches.
            prev_state = json.loads(
                json.dumps(self._state, sort_keys=True, default=str)
            )
            self._txn_depth = 1
            committed = False
            try:
                # A1: reload when (and only when) another process has committed a
                # newer durable snapshot. When the disk fingerprint is unchanged,
                # this instance's in-memory state is already fresh and may hold
                # deliberate caller edits that must not be discarded.
                if not self.durable_state_is_current():
                    fresh = self._load_state_fresh()
                    self._state.clear()
                    self._state.update(fresh)
                self._refresh_derived_views()
                # Legacy index migration is part of the same committed write.
                self._migrate_check_index()
                baseline = json.loads(
                    json.dumps(self._state, sort_keys=True, default=str)
                )
                result = fn(self._state)
                self._validate_transaction(baseline, self._state)
                self._save()  # fsync + os.replace, still under the lock
                self._disk_fingerprint = _state_fingerprint(self._state)
                committed = True
                return result
            except BaseException:
                if not committed:
                    self._state.clear()
                    self._state.update(prev_state)
                    self._refresh_derived_views()
                raise
            finally:
                self._txn_depth = 0

    def _check_index_problems_for(self, state: Mapping, check_id: str | None = None) -> list[str]:
        """Index integrity over an explicit state mapping (used in validation)."""
        saved = self._state
        try:
            self._state = state
            return self._check_index_problems(check_id)
        finally:
            self._state = saved

    @contextlib.contextmanager
    def _fresh_read(self):
        """A1: read GA/ECO evidence under the state lock from durable state.

        A2: the reload is in-place (clear/update) so caller-held views of the
        controller state keep their object identity.  After the read the
        previous snapshot is restored in place: reads never mutate durability,
        and every later write reloads fresh under :meth:`_mutate` anyway.
        """
        with ledger_critical_section(self.state_path):
            if getattr(self, "_txn_depth", 0):
                # Already inside a transaction: this state IS the fresh read.
                yield self._state
                return
            prev = json.loads(json.dumps(self._state, sort_keys=True, default=str))
            if not self.durable_state_is_current():
                fresh = self._load_state_fresh()
                self._state.clear()
                self._state.update(fresh)
            self._refresh_derived_views()
            try:
                yield self._state
            finally:
                self._state.clear()
                self._state.update(prev)
                self._refresh_derived_views()

    def _require_active(self) -> None:
        if self._state["run_status"] == "CANCELLED":
            raise StateError(
                f"run is CANCELLED ({self._state['cancel_reason']}); restart() "
                "before starting new work"
            )

    # -- ledger integrity (R5; reusable by Agent 9) ---------------------------
    @property
    def ledger_key_source(self) -> str:
        """``"environment"`` when provisioned externally, else ``"ephemeral"``."""
        return self._ledger_key_source

    @property
    def unverifiable_gate_definitions(self) -> list[str]:
        """Gate ids whose persisted definition binding did not verify on load."""
        return list(self._unverifiable_definitions)

    def verify_ledger_integrity(self) -> list[str]:
        """Return ``domain:record_id`` for every bound record that fails MAC
        verification (empty list = all verified). Unknown domains without a
        registered payload resolver are skipped; Agent 9 registers its evidence
        domain in ``BOUND_PAYLOAD_RESOLVERS`` to include it here.

        R7.1: the check_index is additionally cross-checked against the MAC-
        bound evidence records, so a ledger-only insertion of a *new* index key
        (which has no binding to iterate over) is still reported. Problems are
        sorted and de-duplicated.
        """
        return self._verify_ledger_integrity_for(self._state)

    def _verify_ledger_integrity_for(self, state: Mapping) -> list[str]:
        """Same verification, over an explicitly supplied (fresh) state."""
        problems: list[str] = []
        bindings = state.get(_LEDGER_BINDINGS_FIELD) or {}
        for domain, records in bindings.items():
            resolver = BOUND_PAYLOAD_RESOLVERS.get(domain)
            if resolver is None:
                continue
            for record_id, expected in records.items():
                payload = resolver(state, record_id)
                if not self.ledger_integrity.verify(
                    domain, payload, expected, record_id=record_id
                ):
                    problems.append(f"{domain}:{record_id}")
        problems.extend(self._check_index_problems_for(state))
        # A MAC-valid index binding must still cover every check key present in
        # the ledger: an attacker who adds a brand-new key has no binding to
        # iterate, so report that absence explicitly (fail closed).
        for cid in state.get("check_index") or {}:
            expected = read_binding(state, DOMAIN_CHECK_INDEX, str(cid))
            if not expected:
                problems.append(f"{DOMAIN_CHECK_INDEX}:{cid}:unbound-index")
        # EXT-01: integrity is not only "does this binding match this record" —
        # it must also prove that NO record was erased. The append-only manifest
        # is checked here so the authoritative qualification path fails closed
        # on a whole-record deletion, even when every remaining binding verifies.
        problems.extend(self._evidence_manifest_problems_for(state))
        return sorted(set(problems))

    # -- EXT-01: append-only evidence manifest --------------------------------
    def _evidence_manifest_problems_for(self, state: Mapping) -> list[str]:
        """Detect deleted / unmanifested / unbound evidence in *any* state.

        Binds the COMPLETE set of evidence ids ever recorded. A key-free ledger
        writer can delete a record and its own binding, but cannot delete the
        id from this MAC-bound manifest (or delete the manifest itself) without
        leaving a detectable problem. No migration from remaining evidence is
        performed: that is precisely the laundering step that caused EXT-01.
        """
        manifest = state.get(_EVIDENCE_MANIFEST_FIELD)
        evidence = state.get("evidence") or {}
        # Only records that were actually recorded through the authoritative
        # boundary carry an evidence binding. Legacy/unbound records injected
        # directly into state are already non-authoritative at evaluation and
        # must not be forced into the manifest (keeps legacy fixtures working).
        bound_ids = {str(eid) for eid in (state.get("evidence_integrity") or {})}
        problems: list[str] = []
        if isinstance(manifest, (str, bytes)) or (
            manifest is not None and not isinstance(manifest, (list, tuple))
        ):
            return [f"{DOMAIN_EVIDENCE_MANIFEST}:invalid-manifest-type"]
        ids: list[str] = []
        if manifest is None:
            if bound_ids:
                problems.append(f"{DOMAIN_EVIDENCE_MANIFEST}:missing-manifest")
        else:
            ids = [str(i) for i in manifest]
            if len(ids) != len(set(ids)):
                problems.append(f"{DOMAIN_EVIDENCE_MANIFEST}:duplicate-ids")
            expected = read_binding(
                state, DOMAIN_EVIDENCE_MANIFEST, _EVIDENCE_MANIFEST_RECORD
            )
            payload = _evidence_manifest_payload(state, _EVIDENCE_MANIFEST_RECORD)
            if not expected:
                if ids or bound_ids:
                    problems.append(f"{DOMAIN_EVIDENCE_MANIFEST}:unbound-manifest")
            elif not self.ledger_integrity.verify(
                DOMAIN_EVIDENCE_MANIFEST,
                payload,
                expected,
                record_id=_EVIDENCE_MANIFEST_RECORD,
            ):
                problems.append(f"{DOMAIN_EVIDENCE_MANIFEST}:mac-invalid")
            manifested = set(ids)
            for eid in ids:
                if str(eid) not in evidence:
                    problems.append(
                        f"{DOMAIN_EVIDENCE_MANIFEST}:deleted-evidence:{eid}"
                    )
            for eid in bound_ids:
                if eid not in manifested:
                    problems.append(
                        f"{DOMAIN_EVIDENCE_MANIFEST}:unmanifested-evidence:{eid}"
                    )
        return sorted(set(problems))

    def _bind_evidence_manifest(self) -> None:
        """MAC-bind the current append-only manifest under the external key.

        Caller must hold the ledger critical section; this is invoked from the
        same atomic step that records new evidence.
        """
        write_binding(
            self._state,
            DOMAIN_EVIDENCE_MANIFEST,
            _EVIDENCE_MANIFEST_RECORD,
            self.ledger_integrity.mac(
                DOMAIN_EVIDENCE_MANIFEST,
                _evidence_manifest_payload(self._state, _EVIDENCE_MANIFEST_RECORD),
                record_id=_EVIDENCE_MANIFEST_RECORD,
            ),
        )

    # -- R7.1: authoritative check_index integrity ----------------------------
    def _check_index_evidence_problems(self, check_id: str) -> list[str]:
        """Structural (key-independent) integrity problems for one index entry.

        These are checked even for legacy unbound entries so that a ledger-only
        substitution of an evidence_id from another check is always detected.
        Missing *bindings* are deliberately NOT part of this set: legacy entries
        are migrated on load/first write and remain evaluable, while new writes
        always carry a MAC.
        """
        return self._check_index_evidence_problems_for(self._state, check_id)

    def _check_index_evidence_problems_for(
        self, state: Mapping, check_id: str
    ) -> list[str]:
        """Structural index problems over an explicitly supplied (fresh) state."""
        idx = state.get("check_index") or {}
        ids = idx.get(check_id)
        if ids is None:
            return []
        if isinstance(ids, (str, bytes)) or not isinstance(ids, (list, tuple)):
            return [f"{DOMAIN_CHECK_INDEX}:{check_id}:invalid-index-type"]
        problems: list[str] = []
        seen: set[str] = set()
        evidence = state.get("evidence") or {}
        for raw_eid in ids:
            eid = str(raw_eid)
            if eid in seen:
                problems.append(f"{DOMAIN_CHECK_INDEX}:{check_id}:duplicate:{eid}")
            seen.add(eid)
            record = evidence.get(eid)
            if not isinstance(record, Mapping):
                problems.append(
                    f"{DOMAIN_CHECK_INDEX}:{check_id}:missing-evidence:{eid}"
                )
                continue
            if str(record.get("check_id", "")) != str(check_id):
                problems.append(
                    f"{DOMAIN_CHECK_INDEX}:{check_id}:check-mismatch:{eid}"
                )
        return problems

    def _check_index_problems(self, check_id: str | None = None) -> list[str]:
        """Full index integrity problems (structural + MAC), fail closed.

        When ``check_id`` is supplied only that index key is inspected; with no
        argument every key is inspected, including the same evidence_id being
        referenced by two different checks (cross-check reuse).
        """
        idx = self._state.get("check_index") or {}
        cids = [check_id] if check_id is not None else list(idx)
        problems: list[str] = []
        owners: dict[str, str] = {}
        for cid in cids:
            cid = str(cid)
            problems.extend(self._check_index_evidence_problems(cid))
            expected = read_binding(self._state, DOMAIN_CHECK_INDEX, cid)
            payload = _check_index_payload(self._state, cid)
            if expected and not self.ledger_integrity.verify(
                DOMAIN_CHECK_INDEX, payload, expected, record_id=cid
            ):
                problems.append(f"{DOMAIN_CHECK_INDEX}:{cid}:mac-invalid")
            for raw_eid in idx.get(cid) or ():
                eid = str(raw_eid)
                prior = owners.get(eid)
                if prior is not None and prior != cid:
                    problems.append(
                        f"{DOMAIN_CHECK_INDEX}:{cid}:cross-check-reuse:{eid}"
                    )
                owners[eid] = cid
        # B3: enumerate authoritative membership too, so an attacker who DELETES
        # an index key (or one evidence_id from it) is detected. Without this,
        # iterating ledger_bindings alone would miss deleted bindings.
        authoritative = self._authoritative_index_members()
        for cid in set(str(c) for c in cids) | set(authoritative):
            expected_ids = set(authoritative.get(cid, ()))
            actual_ids = set(str(i) for i in (idx.get(cid) or ()))
            for eid in sorted(expected_ids - actual_ids):
                problems.append(
                    f"{DOMAIN_CHECK_INDEX}:{cid}:deleted-evidence:{eid}"
                )
        return sorted(set(problems))

    def _bind_check_index(self, check_id: str) -> None:
        """MAC-bind the current ``check_index[check_id]`` under the external key.

        Caller must hold ``_PROOF_RECORD_LOCK`` when mutating persisted state.
        """
        ids = list((self._state.get("check_index") or {}).get(check_id) or ())
        payload = check_index_mac_payload(check_id, ids)
        write_binding(
            self._state, DOMAIN_CHECK_INDEX, str(check_id),
            self.ledger_integrity.mac(
                DOMAIN_CHECK_INDEX, payload, record_id=str(check_id)
            ),
        )

    def _authoritative_index_members(self) -> dict[str, list[str]]:
        """Rebuild index membership from MAC-bound evidence, never from the index.

        Only evidence records whose external-key binding still verifies, and
        whose MAC-bound envelope (when present) carries the same check_id,
        contribute. This is the authoritative membership set used both to detect
        DELETED index entries and to migrate legacy state.
        """
        evidence = self._state.get("evidence") or {}
        provenance = self._state.get("evidence_provenance") or {}
        integrity = self._state.get("evidence_integrity") or {}
        envelopes = self._state.get("r6_evidence_envelopes") or {}
        members: dict[str, list[str]] = {}
        for eid in sorted(evidence):
            rec = evidence[eid]
            if not isinstance(rec, Mapping):
                continue
            expected = integrity.get(eid)
            if not self.ledger_integrity.verify(
                DOMAIN_EVIDENCE,
                {"evidence": rec, "provenance": provenance.get(eid) or {}},
                expected,
                record_id=str(eid),
            ):
                continue
            cid = str(rec.get("check_id", "") or "")
            if not cid:
                continue
            env = envelopes.get(eid)
            if isinstance(env, Mapping):
                try:
                    from .provenance import EvidenceEnvelope
                    from .r6_authority import get_trust

                    verified_env = EvidenceEnvelope.from_dict(env, get_trust())
                    if verified_env.check_id != cid:
                        continue
                except Exception:  # noqa: BLE001 - unverifiable envelope fails closed
                    continue
            members.setdefault(cid, []).append(str(eid))
        return members

    def _migrate_check_index(self) -> None:
        """Migrate legacy index entries by REBUILDING from MAC-bound evidence.

        The raw legacy index is never signed. A key is migrated only when its
        membership already equals the authoritative set derived from MAC-bound
        evidence; an inconsistent (tampered / deleted / swapped) legacy entry is
        left unbound and reported by ``verify_ledger_integrity``. Caller must
        hold ``_PROOF_RECORD_LOCK``.
        """
        idx = self._state.get("check_index") or {}
        authoritative = self._authoritative_index_members()
        for cid in list(idx):
            cid = str(cid)
            if read_binding(self._state, DOMAIN_CHECK_INDEX, cid):
                continue  # already bound: never overwrite a possibly forged MAC
            current = [str(i) for i in (idx.get(cid) or ())]
            expected = authoritative.get(cid, [])
            if not expected or len(current) != len(expected):
                continue
            if set(current) != set(expected):
                continue  # tampered legacy index: never launder it
            # Rebuild from the authoritative source (MAC-bound evidence).
            idx[cid] = list(expected)
            self._bind_check_index(cid)

    # -- spec registration ----------------------------------------------------
    def register_check_spec(self, spec: CheckSpec) -> CheckSpec:
        """Transaction-wrapped registration (fresh read under the state lock)."""
        return self._mutate(lambda fresh: self._register_check_spec_locked(spec))

    def _register_check_spec_locked(self, spec: CheckSpec) -> CheckSpec:
        self._require_active()
        spec.validate()
        self.check_specs[spec.check_id] = spec
        self._state["check_specs"][spec.check_id] = spec.to_dict()
        return spec

    def register_gate(self, spec: GateSpec) -> GateSpec:
        """Register an authoritative gate specification (persisted across restart)."""
        return self._mutate(lambda fresh: self._register_gate_locked(spec))

    def _register_gate_locked(self, spec: GateSpec) -> GateSpec:
        self._require_active()
        spec.validate()
        if spec.upstream and set(spec.upstream) == {spec.gate_id}:
            raise GateDefinitionError(
                f"self-dependency detected in gate {spec.gate_id!r}"
            )
        for cid in spec.required_checks:
            self.check_specs[cid] = spec.checks[cid]
            self._state["check_specs"][cid] = spec.checks[cid].to_dict()
        self.gate_specs[spec.gate_id] = spec
        self._state["gate_specs"][spec.gate_id] = spec.to_dict()
        return spec

    # -- authoritative state / trust boundary (R4) ----------------------------
    def _evidence_integrity(self, ev_dict: Mapping, prov_dict: Mapping,
                            record_id: str = "") -> str:
        """R6: HMAC over evidence + provenance with the EXTERNAL trust root.

        P0-1 fix: the key is `resolve_ledger_key()` (env/provisioned or
        per-process ephemeral), never a ledger field. The record_id is bound
        so a MAC cannot be replayed onto another evidence id. The binding is
        also written to `ledger_bindings[DOMAIN_EVIDENCE]` so
        `verify_ledger_integrity()` covers evidence provenance.
        """
        return self.ledger_integrity.mac(
            DOMAIN_EVIDENCE,
            {"evidence": ev_dict, "provenance": prov_dict},
            record_id=record_id or str(ev_dict.get("evidence_id", "")),
        )

    def _integrity_ok(self, ev: Evidence | None) -> bool:
        """Whether the persisted evidence + provenance still match the binding."""
        if ev is None:
            return False
        expected = (self._state.get("evidence_integrity") or {}).get(ev.evidence_id)
        if not expected:
            return False
        rec = (self._state.get("evidence") or {}).get(ev.evidence_id)
        if rec is None:
            return False
        prov = (self._state.get("evidence_provenance") or {}).get(ev.evidence_id) or {}
        return hmac.compare_digest(
            str(expected), self._evidence_integrity(rec, prov, ev.evidence_id)
        )

    def _verified_definition(self, gate_id: str) -> dict | None:
        """Return the pinned definition only if its external-key binding holds.

        Re-checked at use time (defense in depth on top of the load-time filter)
        so an in-process mutation cannot substitute an unbound definition.
        """
        definition = self.gate_definitions.get(gate_id)
        if definition is None:
            return None
        expected = read_binding(self._state, DOMAIN_GATE_DEFINITION, gate_id)
        if not self.ledger_integrity.verify(
            DOMAIN_GATE_DEFINITION, definition, expected, record_id=gate_id
        ):
            return None
        return definition

    def _source_of_truth(self, gate_id: str) -> tuple["GateSpec | None", "dict | None"]:
        """Authoritative sources for *gate_id*.

        A registered ``GateSpec`` is strongest, then a pinned gate definition
        whose binding verifies under the external key, then the production
        defaults. ``gate_history`` is descriptive cache and is deliberately
        NEVER consulted here (A3-F1).
        """
        spec = self.gate_specs.get(gate_id) or self._default_specs.get(gate_id)
        return spec, self._verified_definition(gate_id)

    def _record_gate_definition(
        self, gate_id: str, required_checks: Sequence[str],
        required_view: str | None, upstream_gates: Iterable[str],
    ) -> None:
        """Transaction wrapper: used by evaluation and direct callers alike."""
        self._mutate(
            lambda fresh: self._record_gate_definition_locked(
                gate_id, required_checks, required_view, upstream_gates
            )
        )

    def _record_gate_definition_locked(
        self, gate_id: str, required_checks: Sequence[str],
        required_view: str | None, upstream_gates: Iterable[str],
    ) -> None:
        """Record the authoritative required checks from an explicit evaluation.

        A caller's *explicit* declaration is authoritative (unlike the cached
        ``gate_history`` summary, which is never trusted). The most recent
        explicit evaluation wins, so a caller may legitimately expand a gate's
        check set over time. Upstream re-evaluation uses this definition, never
        the mutable ``gate_history``; a gate with cached history but no recorded
        definition therefore fails closed. A registered ``GateSpec`` overrides
        any caller declaration.
        """
        spec, _ = self._source_of_truth(gate_id)
        if spec is not None:
            return  # registered/default spec is authoritative
        checks = list(required_checks or ())
        if not checks:
            return  # let _evaluate_gate raise the empty-list GateDefinitionError
        definition = {
            "required_checks": checks,
            "required_view": required_view,
            "upstream": list(upstream_gates or ()),
        }
        self.gate_definitions[gate_id] = definition
        self._state["gate_definitions"][gate_id] = dict(definition)
        # R5: bind the definition under the external key. The MAC is public and
        # may live in the ledger; the key never does, so a state writer cannot
        # recompute it. record_id binding prevents cross-gate MAC replay.
        write_binding(
            self._state, DOMAIN_GATE_DEFINITION, gate_id,
            self.ledger_integrity.mac(
                DOMAIN_GATE_DEFINITION, definition, record_id=gate_id
            ),
        )
        if gate_id in self._unverifiable_definitions:
            self._unverifiable_definitions.remove(gate_id)

    # -- input registry -------------------------------------------------------
    def register_input(self, label: str, path: str) -> str:
        """Register a file whose change invalidates dependent evidence."""
        return self._mutate(lambda fresh: self._register_input_locked(label, path))

    def _register_input_locked(self, label: str, path: str) -> str:
        self._require_active()
        self._inputs[label] = str(path)  # live view into self._state
        return self.current_input_hashes().get(label, "<missing>")

    def register_inputs(self, mapping: Mapping[str, str]) -> None:
        for label, path in mapping.items():
            self.register_input(label, path)

    def current_input_hashes(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for label, path in self._inputs.items():
            try:
                out[label] = sha256_file(path)
            except OSError:
                out[label] = "<missing>"
        return out

    # -- run output isolation (SR-04) -----------------------------------------
    def prepare_output_dir(
        self, run_id: str, base: str | os.PathLike[str] | None = None, *,
        exist_ok: bool = False,
    ) -> str:
        """Create/validate an isolated output directory for one run.

        Wraps ``evidence.prepare_run_output_dir`` so artifacts from a previous
        run cannot be silently reused. ``artifact_path`` should be used for every
        file written into the returned directory.
        """
        self._require_active()
        root = str(base) if base is not None else os.path.dirname(
            os.path.abspath(self.state_path)
        )
        return prepare_run_output_dir(root, run_id, exist_ok=exist_ok)

    def safe_artifact_path(self, output_dir: str, name: str) -> str:
        """Return a path guaranteed to stay under *output_dir*."""
        return artifact_path(output_dir, name)

    # -- validated design facts ----------------------------------------------
    @property
    def design_facts(self) -> dict:
        return self._state["design_facts"]

    def register_design_fact(
        self, name: str, value: object, *, source: str = "", evidence_ref: str = ""
    ) -> dict:
        """Register a validated design fact used to corroborate N/A decisions."""
        return self._mutate(
            lambda fresh: self._register_design_fact_locked(
                name, value, source=source, evidence_ref=evidence_ref
            )
        )

    def _register_design_fact_locked(
        self, name: str, value: object, *, source: str = "", evidence_ref: str = ""
    ) -> dict:
        self._require_active()
        if not name:
            raise ValueError("design fact needs a name")
        entry = {"value": value, "source": source, "evidence_ref": evidence_ref}
        self._state["design_facts"][name] = entry
        return entry

    # -- evidence -------------------------------------------------------------
    @staticmethod
    def _proof_command_mismatch(proof, command) -> str:
        """R6.3 defence in depth: a supplied proof must bind to *this* command.

        Returns '' when the binding holds (or no proof was supplied), otherwise
        a specific reason.  Uses the SAME helper as the proof producer.  A
        missing helper / malformed proof fails closed.  The authoritative gate
        remains ``state_machine.check_7``; this only stops a bad submission at
        the API boundary before any TOOL_EXECUTION envelope is minted.
        """
        if proof is None:
            return ""
        if hasattr(proof, "to_dict"):
            try:
                proof = proof.to_dict()
            except Exception:  # noqa: BLE001
                return "execution proof could not be serialized"
        if not isinstance(proof, Mapping):
            return "execution proof must be a mapping"
        try:
            from .execution_trust import _sha256_argv
        except Exception:  # noqa: BLE001
            return "execution_trust._sha256_argv unavailable -> fail closed"
        argv = list(command or []) if not isinstance(command, (str, bytes)) else [str(command)]
        try:
            expected = str(_sha256_argv(argv))
        except Exception as exc:  # noqa: BLE001
            return f"command digest recomputation failed ({type(exc).__name__}: {exc})"
        supplied = str(proof.get("argv_sha256", "") or "").strip().lower()
        if not supplied:
            return "execution proof has no argv_sha256"
        if supplied != expected:
            return (
                f"execution proof argv_sha256 {supplied} does not bind the recorded "
                f"command (expected {expected})"
            )
        return ""

    @staticmethod
    def _evidence_digest_material(ev: Evidence) -> dict:
        """Derive authoritative digest material from a recorded Evidence.

        R6.3 concern #2: envelopes/records previously hardcoded
        ``consumed_sha256=''`` and ``dependencies=()``, leaving checks 8/9 and
        the proof<->record digest binding without an authoritative source.
        Values here are computed ONLY from the trusted-in-ledger Evidence
        record, never from caller-supplied proof digest fields.
        """
        consumed: dict[str, str] = {}
        for label, mapping in (
            ("input", ev.input_hashes),
            ("config", ev.config_hashes),
            ("constraint", ev.constraint_hashes),
            ("library", ev.library_hashes),
            ("pdk", ev.pdk_hashes),
        ):
            for name, digest in (mapping or {}).items():
                consumed[f"{label}:{name}"] = str(digest)
        deps = dict(ev.dependencies or {})
        candidate = str(getattr(ev, "candidate_digest", "") or "")
        return {"consumed": consumed, "dependencies": deps, "candidate": candidate}

    def consume_proof_nonces(self, claims: Mapping[str, str]) -> bool:
        """Atomically claim proof nonces for their evidence ids (durable).

        ``claims`` maps ``proof.nonce -> evidence_id``.  Returns False (and
        mutates nothing) when ANY nonce is already owned by a DIFFERENT
        evidence item, when a persisted binding is missing/invalid, or when the
        transaction cannot be committed.  Re-claiming a nonce for the SAME
        evidence id is idempotent so repeated gate evaluations never self-deny.

        A3: there is NO second writer here.  The check, the durable claim and
        its external-key binding all run through :meth:`_mutate` on FRESH state
        under the state lock; the nonce ledger lives inside that same state
        file (lock order: state lock -> same-state nonce ledger).  A concurrent
        process therefore sees the winner's durable owner and fails closed.
        """
        if not claims:
            return True
        try:
            return bool(self._mutate(lambda fresh: self._consume_nonces_locked(claims)))
        except Exception:  # noqa: BLE001 - fail closed, no write occurred
            return False

    def _consume_nonces_locked(self, claims: Mapping[str, str]) -> bool:
        """Check-then-consume against the transaction's fresh state (no I/O)."""
        registry = dict(self._state.get("used_proof_nonces") or {})
        bindings = self._state.get(_LEDGER_BINDINGS_FIELD, {}) or {}
        nonces_bindings = bindings.get(DOMAIN_PROOF_NONCE, {}) or {}
        # 1. validate ALL claims before mutating anything (fail closed as a unit)
        for nonce, eid in claims.items():
            if not nonce:
                return False
            prior = registry.get(nonce)
            if prior is not None and str(prior) != str(eid):
                return False  # one nonce -> at most one qualified gate/evidence
            if prior is not None and not self.ledger_integrity.verify(
                DOMAIN_PROOF_NONCE,
                {"nonce": nonce, "evidence_id": prior},
                nonces_bindings.get(nonce),
                record_id=nonce,
            ):
                return False
        # 2. consume (idempotent) + bind under the same transaction
        changed = False
        for nonce, eid in claims.items():
            if nonce in registry:
                continue
            registry[nonce] = str(eid)
            write_binding(
                self._state, DOMAIN_PROOF_NONCE, nonce,
                self.ledger_integrity.mac(
                    DOMAIN_PROOF_NONCE,
                    {"nonce": nonce, "evidence_id": str(eid)},
                    record_id=nonce,
                ),
            )
            changed = True
        if changed:
            self._state["used_proof_nonces"] = registry
        return True

    def record_evidence(
        self,
        ev: Evidence,
        *,
        allow_while_cancelled: bool = False,
        engineering_outcome: Mapping | None = None,
        check_spec: CheckSpec | None = None,
        strict: bool = True,
        simulated: str = "",
    ) -> Evidence:
        """Record evidence in ONE transaction (fresh read, validate, fsync).

        A5: every state mutation below now happens inside :meth:`_mutate`; the
        former out-of-lock `_save()` has been removed from the write path.
        """
        return self._mutate(
            lambda fresh: self._record_evidence_locked(
                ev,
                allow_while_cancelled=allow_while_cancelled,
                engineering_outcome=engineering_outcome,
                check_spec=check_spec,
                strict=strict,
                simulated=simulated,
            )
        )

    def _record_evidence_locked(
        self,
        ev: Evidence,
        *,
        allow_while_cancelled: bool = False,
        engineering_outcome: Mapping | None = None,
        check_spec: CheckSpec | None = None,
        strict: bool = True,
        simulated: str = "",
    ) -> Evidence:
        """Record evidence through the single authoritative validation boundary.

        Strict provenance validation is the DEFAULT for every substantive status
        (PASS/FAIL/INCONCLUSIVE/UNSUPPORTED/NOT_RUN). The only way to opt out is
        an explicit, labelled ``strict=False, simulated="<why>"`` pair for
        internal/simulated checks; an unlabelled ``strict=False`` is refused so
        no caller can silently drop provenance by accident (U-B).

        NOT_APPLICABLE is never exempt from design-fact corroboration and is
        forbidden outright for non-waivable checks (U-A).

        ``allow_while_cancelled`` is retained for signature compatibility but
        is deliberately ignored: cancellation always blocks new work (SR-03
        sub-finding 14).
        """
        self._require_active()
        # R6.3 defence in depth: reject a proof that was issued for a DIFFERENT
        # command before ANY state is mutated or a TOOL_EXECUTION envelope is
        # minted. The authoritative check remains state_machine.check_7.
        _submitted_proof = getattr(ev, "execution_proof", None)
        if _submitted_proof is not None:
            _mismatch = self._proof_command_mismatch(_submitted_proof, ev.command)
            if _mismatch:
                raise GateValidationError(
                    "proof-to-evidence binding rejected: " + _mismatch
                )
        if not strict and not simulated.strip():
            raise GateValidationError(
                "strict=False requires an explicit 'simulated' label describing "
                "why the evidence is internal/simulated (no real tool provenance)"
            )
        # F-01: any labelled simulated/internal submission is permanently
        # classified as non-authoritative before it reaches serialized state.
        if simulated.strip():
            ev.mark_simulated(simulated)
        spec = check_spec or self.check_specs.get(ev.check_id)

        # Central NOT_APPLICABLE validation: a target check id + registered,
        # matching design facts are mandatory; non-waivable checks are refused.
        # Validate BEFORE mutating state so a bad decision is never persisted.
        if ev.status is Status.NOT_APPLICABLE:
            ev.applicability_reason = validate_applicability_for_check(
                ev.applicability_reason or ev.reason, ev.check_id, self.design_facts
            )
            ev.reason = ev.applicability_reason

        errors = validate_gate_submission(
            ev,
            spec=spec,
            design_facts=self.design_facts or None,
            engineering_outcome=engineering_outcome,
            strict_provenance=strict,
        )
        if errors:
            raise GateValidationError("; ".join(errors))

        # Bind evidence to the currently registered inputs (only labels that
        # were registered at record time become staleness anchors).
        if not ev.input_hashes:
            ev.input_hashes = self.current_input_hashes()
        prov_record = {
            "evidence_class": ev.evidence_class,
            "simulated_by": ev.simulated_by,
        }
        self._state["evidence"][ev.evidence_id] = ev.to_dict()
        self._state["evidence_provenance"][ev.evidence_id] = prov_record
        # R4: bind provenance INTO the evidence integrity record. Any later
        # edit of the evidence record or of either provenance marker in
        # state.json breaks the binding and the evidence is treated as
        # non-authoritative at evaluation time.
        _ebind = self._evidence_integrity(
            self._state["evidence"][ev.evidence_id], prov_record, ev.evidence_id
        )
        self._state["evidence_integrity"][ev.evidence_id] = _ebind
        write_binding(self._state, DOMAIN_EVIDENCE, ev.evidence_id, _ebind)
        # EXT-01: append to the authoritative evidence manifest and re-bind it
        # inside the SAME transaction as the record/envelope/index/nonce write.
        # The manifest is never pruned: a later deletion of this record (even
        # with all of its own bindings removed) stays detectable.
        _manifest = self._state.get(_EVIDENCE_MANIFEST_FIELD)
        if not isinstance(_manifest, list):
            _manifest = []
            self._state[_EVIDENCE_MANIFEST_FIELD] = _manifest
        if ev.evidence_id not in _manifest:
            _manifest.append(ev.evidence_id)
        self._bind_evidence_manifest()
        # R6 Stage 2: also create a signed evidence envelope for the authoritative
        # state machine. Provenance is fixed at creation from the trusted input;
        # later ledger edits cannot relabel it (MAC verification fails).
        with ledger_critical_section(self.state_path):
            try:
                from .r6_authority import get_trust
                from .provenance import make_envelope, Provenance
                import hashlib as _hashlib
                import json as _json
                _trust = get_trust()
                # R6.2: TOOL_EXECUTION is ONLY minted when the evidence carries a
                # structural ExecutionProof from the managed boundary. Caller-supplied
                # command/status/labels alone can never create an authorizing envelope.
                _proof = getattr(ev, "execution_proof", None)
                _authorizing = bool(_proof) and not simulated.strip()
                if simulated.strip():
                    _prov = Provenance.SIMULATED
                elif _authorizing:
                    _prov = Provenance.TOOL_EXECUTION
                else:
                    _prov = Provenance.OBSERVATION
                # R6.3: authoritative digest material is derived from the RECORDED
                # evidence (never from caller-supplied proof digest fields) and
                # MAC-bound into the envelope, giving check_7/8/9 a real source.
                _material = self._evidence_digest_material(ev)
                _consumed_digest = ""
                if _material["consumed"]:
                    _consumed_digest = _hashlib.sha256(
                        _json.dumps(_material["consumed"], sort_keys=True,
                                    separators=(",", ":"), default=str).encode("utf-8")
                    ).hexdigest()
                _deps = _material["dependencies"]
                _dep_digest = ""
                if _deps:
                    _dep_digest = _hashlib.sha256(
                        _json.dumps(_deps, sort_keys=True, separators=(",", ":"),
                                    default=str).encode("utf-8")
                    ).hexdigest()
                _env = make_envelope(
                    evidence_id=ev.evidence_id, check_id=ev.check_id, gate_id="",
                    provenance=_prov,
                    execution_identity={
                        "command": list(ev.command) if ev.command else [],
                        "tool_versions": dict(ev.tool_versions or {}),
                        # proof is inside the MAC-bound identity, so it cannot be
                        # stripped or forged by later ledger edits
                        "execution_proof": _proof,
                        "candidate_digest": _material["candidate"],
                        "consumed_digest": _consumed_digest,
                        "dependency_digest": _dep_digest,
                    },
                    consumed_sha256=_consumed_digest,
                    dependencies=tuple(
                        sorted((str(k), str(v)) for k, v in _deps.items())
                    ),
                    status=str(ev.status.value),
                    observed_utc=utc_now(),
                    context={"session_id": _trust.context.session_id,
                             "repo_id": _trust.context.repo_id,
                             "revision": _trust.context.revision},
                    trust=_trust,
                )
                self._state.setdefault("r6_evidence_envelopes", {})[ev.evidence_id] = (
                    _env.to_dict()
                )
            except Exception:  # noqa: BLE001 - authoritative check fails closed later
                pass
            # R7.1: index membership is an authorization decision. Write it
            # INSIDE the record/envelope/nonce critical section and bind the
            # full check_id -> [evidence_id, ...] list under the external key in
            # the same atomic step. A stale snapshot can no longer overwrite a
            # concurrent index update.
            idx = self._state["check_index"].setdefault(ev.check_id, [])
            if ev.evidence_id not in idx:
                idx.append(ev.evidence_id)
            self._bind_check_index(ev.check_id)
        if spec is not None or engineering_outcome is not None:
            self._state["submissions"][ev.evidence_id] = {
                "check_id": ev.check_id,
                "spec": spec.to_dict() if spec is not None else None,
                "engineering_outcome": dict(engineering_outcome or {}),
            }
        # No _save(): _mutate owns validation + fsync + replace for this write.
        return ev

    def record_check_result(
        self,
        check_id: str,
        status: Status | str,
        reason: str,
        *,
        engineering_outcome: Mapping | None = None,
        exit_code: int | None = None,
        analysis_view: str = "UNKNOWN",
        command: Iterable[str] | str | None = None,
        tool_versions: Mapping[str, str] | None = None,
        reports: Iterable[str] = (),
        logs: Iterable[str] = (),
        artifacts: Iterable[str] = (),
        applicability_reason: str = "",
        spec: CheckSpec | None = None,
        dependencies: DependencySet | None = None,
        binding_context: BindingContext | None = None,
        run_id: str = "",
        design: str = "",
        stage: str = "",
        tool_execution_id: str = "",
        corner: str = "",
        library_provenance: str = "",
        extraction_provenance: str = "",
        strict: bool = True,
        simulated: str = "",
        execution_proof: dict | None = None,
    ) -> Evidence:
        """Convenience API: build + validate + record a check result in one call.

        ``dependencies`` (persistent paths + hashes) and ``binding_context``
        (run/design/stage/tool/view/corner) are attached via the evidence module's
        ``set_dependencies`` / ``bind_context`` so they can be re-validated at
        every gate evaluation. ``strict`` / ``simulated`` are forwarded to
        :meth:`record_evidence` (strict provenance is the default).
        """
        ev = Evidence.from_run(
            check_id=check_id, status=status, reason=reason, command=command,
            exit_code=exit_code, analysis_view=analysis_view,
            tool_versions=tool_versions, reports=reports, logs=logs,
            artifacts=artifacts, applicability_reason=applicability_reason,
            dependencies=dependencies, binding_context=binding_context,
            run_id=run_id, design=design, stage=stage,
            tool_execution_id=tool_execution_id, corner=corner,
            library_provenance=library_provenance,
            extraction_provenance=extraction_provenance,
        )
        if execution_proof is not None:
            # R6.3 defence in depth: reject at the caller boundary BEFORE a
            # TOOL_EXECUTION envelope is minted for a mismatched command.
            _mismatch = self._proof_command_mismatch(execution_proof, command)
            if _mismatch:
                raise GateValidationError(
                    "proof-to-evidence binding rejected: " + _mismatch
                )
            ev.execution_proof = execution_proof
        return self.record_evidence(
            ev, engineering_outcome=engineering_outcome, check_spec=spec,
            strict=strict, simulated=simulated,
        )

    def latest_evidence(self, check_id: str) -> Evidence | None:
        """Resolve the newest evidence that ACTUALLY belongs to *check_id*.

        A1/D4: ECO pre-acceptance revalidation and every other GA reader call
        this method. It therefore takes the cross-process state lock and reads
        a FRESH durable snapshot; a controller loaded before a concurrent FAIL
        can no longer return its stale PASS to ECO.

        A7-P0-01: the unauthenticated index is only a lookup hint. Every
        candidate's own (MAC-bound) record check_id is authoritative. A stale,
        duplicated, cross-check or invalid-MAC index entry makes the whole
        resolution non-authorizing (fail closed) instead of silently selecting
        another entry.
        """
        with self._fresh_read() as state:
            return self._latest_evidence_from(state, check_id)

    def _latest_evidence_from(self, state: Mapping, check_id: str) -> Evidence | None:
        ids = (state.get("check_index") or {}).get(check_id) or []
        if not ids:
            return None
        if self._check_index_evidence_problems_for(state, check_id):
            return None
        evidence = state.get("evidence") or {}
        for raw_eid in reversed(list(ids)):
            rec = evidence.get(str(raw_eid))
            if not isinstance(rec, Mapping) or str(rec.get("check_id", "")) != str(check_id):
                return None
            try:
                # Deep-copy so a caller mutating the returned Evidence cannot
                # mutate the controller's (or any durable) snapshot in place.
                ev = Evidence.from_dict(copy.deepcopy(rec))
            except Exception:  # noqa: BLE001 - malformed record fails closed
                return None
            if ev.check_id != check_id:
                return None
            # A different external key (unprovisioned cross-process flow) makes
            # EVERY binding unverifiable; legacy semantics keep the PASS-shaped
            # decision but ``authoritative_qualification`` denies it. Only an
            # index MAC failure on otherwise-verifiable evidence is a hard block.
            expected = read_binding(state, DOMAIN_CHECK_INDEX, str(check_id))
            payload = _check_index_payload(state, str(check_id))
            if expected and self._integrity_ok(ev) and not self.ledger_integrity.verify(
                DOMAIN_CHECK_INDEX, payload, expected, record_id=str(check_id)
            ):
                return None
            return ev
        return None

    def all_evidence(self, check_id: str | None = None) -> list[Evidence]:
        """All durable evidence, read under the state lock (A1/D4)."""
        with self._fresh_read() as state:
            return self._all_evidence_from(state, check_id)

    def _all_evidence_from(self, state: Mapping, check_id: str | None) -> list[Evidence]:
        if check_id is None:
            recs = list((state.get("evidence") or {}).values())
        else:
            if self._check_index_evidence_problems_for(state, check_id):
                return []
            ids = (state.get("check_index") or {}).get(check_id) or []
            evidence = state.get("evidence") or {}
            recs = [evidence[str(i)] for i in ids if str(i) in evidence]
        # Deep-copy: returned Evidence objects are detached from durable state.
        return [Evidence.from_dict(copy.deepcopy(r)) for r in recs]

    # -- advisory summaries (never authorize anything) ------------------------
    def record_llm_summary(self, text: str, *, author: str = "llm") -> dict:
        return self._mutate(
            lambda fresh: self._record_llm_summary_locked(text, author=author)
        )

    def _record_llm_summary_locked(self, text: str, *, author: str = "llm") -> dict:
        self._require_active()
        entry = {"author": author, "text": text, "created_utc": utc_now()}
        self._state["summaries"].append(entry)
        return entry

    @property
    def summaries(self) -> list[dict]:
        return list(self._state["summaries"])

    def force_pass(self, gate_id: str, justification: str = "") -> None:
        """Deliberately unsupported: gate transitions need evidence."""
        raise GateBypassError(
            f"refusing to force PASS for gate {gate_id!r}: gate decisions are "
            f"computed from recorded evidence only (justification={justification!r})"
        )

    def authorize_from_summary(self, gate_id: str) -> None:
        raise GateBypassError(
            "LLM/human summaries are advisory and cannot authorize a gate transition"
        )

    # -- applicability & waivers ---------------------------------------------
    def mark_not_applicable(self, ev: Evidence) -> Evidence:
        # Set the status, then delegate to record_evidence(), which applies
        # the single shared NA validator. No separate/divergent validation.
        ev.status = Status.NOT_APPLICABLE
        return self.record_evidence(ev)

    def mark_not_applicable_for_check(
        self,
        check_id: str,
        reason: str,
        *,
        design_facts: Mapping | None = None,
    ) -> Evidence:
        """CLI-facing helper: record a validated N/A for a target check (U-A).

        ``design_facts`` may be supplied explicitly (e.g. from a CLI inventory)
        or defaults to the controller's registered facts. Non-waivable checks
        and reason-only decisions are refused.
        """
        facts = design_facts if design_facts is not None else self.design_facts
        normalized = validate_applicability_for_check(reason, check_id, facts)
        ev = Evidence(
            check_id=check_id,
            status=Status.NOT_APPLICABLE,
            reason=normalized,
            applicability_reason=normalized,
        )
        return self.record_evidence(ev)

    def add_waiver(self, waiver: Waiver) -> Waiver:
        """Record a waiver with enforced identity, scope, policy and revision."""
        return self._mutate(lambda fresh: self._add_waiver_locked(waiver))

    def _add_waiver_locked(self, waiver: Waiver) -> Waiver:
        self._require_active()
        if not waiver.check_id.strip():
            raise ValueError("waiver must name a check_id (identity)")
        if len(waiver.reason.strip()) < MIN_WAIVER_REASON_LEN:
            raise ValueError(
                f"waiver reason must be at least {MIN_WAIVER_REASON_LEN} characters"
            )
        if not waiver.scope.strip():
            raise ValueError("waiver must declare an explicit scope")
        if not waiver.approved_by.strip():
            raise ValueError("waiver must record who approved it")
        if waiver.policy not in WAIVER_POLICIES:
            raise ValueError(
                f"unknown waiver policy {waiver.policy!r}; "
                f"expected one of {sorted(WAIVER_POLICIES)}"
            )
        if any(w.get("waiver_id") == waiver.waiver_id for w in self._state["waivers"]):
            raise ValueError(f"duplicate waiver identity {waiver.waiver_id!r}")
        if waiver.expires_utc:
            try:
                _parse_utc(waiver.expires_utc)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid waiver expiry {waiver.expires_utc!r}") from exc
        if not waiver.input_revision and self.current_input_hashes():
            waiver.input_revision = self.current_input_hashes()
        _w = waiver.to_dict()
        self._state["waivers"].append(_w)
        write_binding(
            self._state, DOMAIN_WAIVER, waiver.waiver_id,
            self.ledger_integrity.mac(DOMAIN_WAIVER, _w, record_id=waiver.waiver_id),
        )
        return waiver

    def _waiver_expired(self, waiver: Waiver) -> bool:
        if not waiver.expires_utc:
            return False
        try:
            return _parse_utc(waiver.expires_utc) <= _dt.datetime.now(_dt.timezone.utc)
        except (TypeError, ValueError):
            return True  # malformed expiry is treated as expired, never active

    def waivers_for(self, gate_id: str, check_id: str) -> list[Waiver]:
        """Only identity/scope/expiry/revision-valid waivers are returned."""
        current = self.current_input_hashes()
        out: list[Waiver] = []
        for w in self._state["waivers"]:
            # R6: a waiver is an authorization decision; require its external-key
            # MAC or ignore it (fail closed against ledger-only forgery).
            _wmac = read_binding(self._state, DOMAIN_WAIVER, str(w.get("waiver_id", "")))
            if not self.ledger_integrity.verify(
                DOMAIN_WAIVER, w, _wmac, record_id=str(w.get("waiver_id", ""))
            ):
                continue
            obj = Waiver.from_dict(w)
            if not obj.covers(gate_id, check_id):
                continue
            if self._waiver_expired(obj):
                continue
            if obj.input_revision and obj.input_revision != current:
                continue  # waiver applies only to the input revision it was granted for
            out.append(obj)
        return out

    def waivers(self) -> list[Waiver]:
        return [Waiver.from_dict(w) for w in self._state["waivers"]]

    # -- gate evaluation ------------------------------------------------------
    def evaluate_gate(
        self,
        gate_id: str,
        required_checks: Sequence[str],
        *,
        stage: str = "",
        required_view: str | None = None,
        upstream_gates: Iterable[str] = (),
    ) -> GateDecision:
        """Authoritative gate decision in ONE transaction (A1 / C4 fix).

        The cross-process lock is taken and durable state reloaded at ENTRY,
        so ``qualified`` is computed from a fresh snapshot. No authorization
        decision is ever derived from a pre-lock ``self._state``.
        """
        return self._mutate(
            lambda fresh: self._evaluate_gate_entry_locked(
                gate_id, required_checks, stage=stage, required_view=required_view,
                upstream_gates=upstream_gates,
            )
        )

    def _evaluate_gate_entry_locked(
        self,
        gate_id: str,
        required_checks: Sequence[str],
        *,
        stage: str = "",
        required_view: str | None = None,
        upstream_gates: Iterable[str] = (),
    ) -> GateDecision:
        self._require_active()
        self._assert_no_cycles()  # authoritative dependency graph is cycle-free
        # R4: record the authoritative gate definition from this explicit
        # evaluation. All (re-)evaluations use it, never gate_history.checks.
        self._record_gate_definition(gate_id, required_checks, required_view, upstream_gates)
        return self._evaluate_gate(
            gate_id, required_checks, stage=stage, required_view=required_view,
            upstream_gates=upstream_gates, visiting=frozenset(),
        )

    def _assert_no_cycles(self) -> None:
        """Detect cycles in the registered gate dependency graph (SR-03 #8)."""
        state: dict[str, int] = {}

        def visit(gid: str, stack: list[str]) -> None:
            mark = state.get(gid, 0)
            if mark == 1:
                raise GateDefinitionError(
                    "dependency cycle detected: " + " -> ".join(stack + [gid])
                )
            if mark == 2:
                return
            state[gid] = 1
            spec = self.gate_specs.get(gid)
            if spec:
                for up in spec.upstream:
                    visit(up, stack + [gid])
            state[gid] = 2

        for gid in sorted(self.gate_specs):
            visit(gid, [])

    def _evaluate_gate(
        self,
        gate_id: str,
        required_checks: Sequence[str],
        *,
        stage: str,
        required_view: str | None,
        upstream_gates: Iterable[str],
        visiting: frozenset[str],
    ) -> GateDecision:
        """Re-entrant wrapper; the outer evaluate_gate transaction owns the write."""
        return self._mutate(
            lambda fresh: self._evaluate_gate_locked(
                gate_id, required_checks, stage=stage, required_view=required_view,
                upstream_gates=upstream_gates, visiting=visiting,
            )
        )

    def _evaluate_gate_locked(
        self,
        gate_id: str,
        required_checks: Sequence[str],
        *,
        stage: str,
        required_view: str | None,
        upstream_gates: Iterable[str],
        visiting: frozenset[str],
    ) -> GateDecision:
        self._require_active()
        if gate_id in visiting:
            chain = " -> ".join(list(visiting) + [gate_id])
            raise GateDefinitionError(f"dependency cycle detected: {chain}")

        # R4: resolve the AUTHORITATIVE required-check set. A registered GateSpec
        # wins; otherwise the definition recorded from the most recent explicit
        # evaluation is used. The cached gate_history summary is NEVER a source
        # of required_checks.
        spec, definition = self._source_of_truth(gate_id)
        if spec is not None:
            effective = list(spec.required_checks)
        elif definition is not None:
            effective = list(definition["required_checks"])
        else:
            effective = list(required_checks)
        if not effective:
            raise GateDefinitionError(
                f"gate {gate_id!r} has an empty required-check list"
            )
        if spec and set(effective) != set(required_checks):
            # Record that the caller's list was overridden by the spec, but do
            # NOT let a shorter caller list reduce the enforced set.
            pass

        here = visiting | {gate_id}
        view = (
            required_view
            or (spec.required_view if spec else None)
            or (definition or {}).get("required_view")
        )
        upstream: list[str] = list(upstream_gates)
        if spec:
            for up in spec.upstream:
                if up not in upstream:
                    upstream.append(up)

        current = self.current_input_hashes()
        check_report: dict[str, dict] = {}
        blocking: list[str] = []
        waived_visible: list[dict] = []
        na_visible: list[dict] = []
        waived_checks: list[str] = []
        na_checks: list[str] = []  # valid N/A checks -> disposition NOT_APPLICABLE
        simulated_checks: list[str] = []  # F-01: non-authoritative PASS sources
        #: R7.1 occupancy: one evidence_id may satisfy at most ONE check per gate.
        used_evidence: dict[str, str] = {}

        # Registered input requirements must be registered and bound.
        if spec:
            missing_registered = [lbl for lbl in spec.required_inputs if lbl not in self._inputs]
            if missing_registered:
                blocking.append(
                    f"gate requires unregistered inputs: {', '.join(missing_registered)}"
                )
                check_report["inputs:registered"] = {
                    "status": Status.NOT_RUN.value,
                    "reason": "required inputs not registered: "
                    + ", ".join(missing_registered),
                }

        # Upstream gates must be RE-EVALUATED from current evidence, not trusted
        # from a historical PASS (SR-03 sub-findings 4 and 8).
        for up in upstream:
            hist = self._last_decision(up)
            if hist is None:
                blocking.append(f"upstream gate {up} not PASS")
                check_report[f"upstream:{up}"] = {
                    "status": Status.NOT_RUN.value,
                    "reason": "upstream gate has no PASS decision",
                }
                continue
            # R4 / A3-F1: the cached gate_history entry is consulted ONLY to
            # detect whether the gate was ever evaluated. Its required_checks,
            # required_view and upstream_gates are DESCRIPTIVE and are NEVER
            # used for authorization. Re-derive them from authoritative gate
            # definitions; a gate with cached history but no definition fails
            # closed rather than trusting the mutable summary.
            up_spec, up_definition = self._source_of_truth(up)
            if up_spec is not None:
                up_checks = list(up_spec.required_checks)
                up_view = up_spec.required_view
                up_upstream = list(up_spec.upstream)
            elif up_definition is not None:
                up_checks = list(up_definition["required_checks"])
                up_view = up_definition.get("required_view")
                up_upstream = list(up_definition.get("upstream") or ())
            else:
                if up in self._unverifiable_definitions:
                    reason = (
                        f"upstream gate {up} has an unverifiable/invalid gate "
                        "definition binding; refusing to authorize"
                    )
                else:
                    reason = (
                        f"upstream gate {up} has no authoritative gate definition; "
                        "refusing to authorize from cached history"
                    )
                blocking.append(reason)
                check_report[f"upstream:{up}"] = {
                    "status": "BLOCKED",
                    "reason": reason,
                    "qualified": False,
                }
                continue
            re_dec = self._evaluate_gate(
                up, up_checks, stage=hist.get("stage", ""),
                required_view=up_view,
                upstream_gates=up_upstream,
                visiting=here,
            )
            # R2-03: authorization is keyed on ``qualified``, NOT on the
            # PASS-shaped ``passed`` property. A SIMULATED_PASS (or any other
            # non-qualified PASS, e.g. an unauthoritative legacy record) keeps
            # ``decision=PASS`` for reporting continuity but must hard-block the
            # dependent gate. Genuine CLEAN_PASS / WAIVED_PASS / validated
            # NOT_APPLICABLE upstreams remain qualified and still authorize.
            if not re_dec.qualified:
                detail = re_dec.reason
                if re_dec.decision == "PASS" and re_dec.disposition != "CLEAN_PASS":
                    detail = (
                        f"upstream gate {up} is not production-qualified "
                        f"(disposition={re_dec.disposition}, "
                        f"decision={re_dec.decision})"
                    )
                blocking.append(f"upstream gate {up} fails re-evaluation: {detail}")
                check_report[f"upstream:{up}"] = {
                    "status": "BLOCKED",
                    "reason": detail,
                    "disposition": re_dec.disposition,
                    "qualified": re_dec.qualified,
                }

        for check_id in effective:
            cspec = (spec.checks.get(check_id) if spec else None) or self.check_specs.get(check_id)
            ev = self.latest_evidence(check_id)
            waivers = self.waivers_for(gate_id, check_id)
            entry: dict = {"waived": [w.to_dict() for w in waivers]}

            # A7-P0-01: the evidence-to-check identity is the AUTHORITATIVE
            # binding. Never let a genuine proof for check A satisfy check B.
            # Structural index problems (stale ref, duplicate, cross-check
            # reuse, check mismatch) and an invalid MAC on an existing binding
            # both make the check non-authorizing -> NOT_RUN / BLOCKED.
            index_problems = self._check_index_problems(check_id)
            _evidence_verifiable = self._integrity_ok(ev)
            hard_index_problems = [
                p for p in index_problems
                if ":unbound-index" not in p  # legacy entries remain evaluable
                # Cross-process key mismatch makes all bindings unverifiable;
                # authority still denies qualification, legacy decisions survive.
                and not (
                    ":mac-invalid" in p and not _evidence_verifiable
                )
            ]
            # EXT-01: manifest problems are hard, independent of the legacy
            # index-migration exemption above. A deleted, unmanifested, unbound
            # or malformed evidence record can never be laundered into
            # CLEAN_PASS or downstream authorization. The ONLY exemption mirrors
            # the existing cross-process key mismatch rule: when the evidence
            # itself is unverifiable (different process key), the manifest MAC
            # is likewise unverifiable; authoritative_qualification still denies
            # qualification, while legacy revalidation diagnostics stay visible.
            manifest_problems = [
                p for p in self._evidence_manifest_problems_for(self._state)
                if not (":mac-invalid" in p and not _evidence_verifiable)
            ]
            if manifest_problems:
                hard_index_problems.extend(manifest_problems)
                entry["evidence_manifest_problems"] = manifest_problems
            binding_mismatch = ev is not None and ev.check_id != check_id
            if hard_index_problems or binding_mismatch:
                reasons = list(hard_index_problems)
                if binding_mismatch:
                    reasons.append(
                        f"evidence {ev.evidence_id} has check_id "
                        f"{ev.check_id!r}, required {check_id!r}"
                    )
                detail = "; ".join(reasons)
                entry.update(
                    status=Status.NOT_RUN.value,
                    reason="non-authorizing check_index: " + detail,
                    check_index_problems=hard_index_problems,
                )
                if ev is not None:
                    entry["evidence_id"] = ev.evidence_id
                blocking.append(f"{check_id}: NOT_RUN ({detail})")
                check_report[check_id] = entry
                continue

            if ev is not None:
                owner = used_evidence.get(ev.evidence_id)
                if owner is not None and owner != check_id:
                    detail = (
                        f"evidence {ev.evidence_id} already satisfies check "
                        f"{owner!r} and cannot also satisfy {check_id!r}"
                    )
                    entry.update(
                        status=Status.NOT_RUN.value,
                        reason="duplicate evidence occupancy: " + detail,
                        evidence_id=ev.evidence_id,
                    )
                    blocking.append(f"{check_id}: NOT_RUN ({detail})")
                    check_report[check_id] = entry
                    continue
                used_evidence[ev.evidence_id] = check_id

            # F-01: provenance is authoritative only when BOTH the evidence
            # record and the independent persisted provenance map agree it is
            # tool-derived. Either marker is enough to make it non-authoritative.
            # R4 / A3-F2: the evidence's own integrity binding is now the primary
            # authority. Because provenance is part of the bound payload, flipping
            # BOTH markers in state.json no longer relabels simulated evidence:
            # the binding mismatches and the record is non-authoritative.
            prov = (
                (self._state.get("evidence_provenance") or {}).get(ev.evidence_id) or {}
            ) if ev is not None else {}
            integrity_ok = self._integrity_ok(ev)
            ev_simulated = bool(
                ev is not None
                and (
                    not integrity_ok
                    or ev.is_simulated
                    or str(prov.get("evidence_class", "TOOL") or "TOOL").upper() == "SIMULATED"
                    or prov.get("simulated_by")
                )
            )
            if ev_simulated:
                entry["evidence_class"] = "SIMULATED"
                entry["simulated_by"] = ev.simulated_by or str(prov.get("simulated_by", ""))
                if not integrity_ok:
                    entry["integrity"] = "INVALID"

            if ev is None:
                entry.update(
                    status=Status.NOT_RUN.value,
                    reason="no evidence recorded for required check",
                )
                blocking.append(f"{check_id}: NOT_RUN (no evidence)")
                check_report[check_id] = entry
                continue

            stale = ev.stale_inputs(current)
            if stale:
                entry.update(
                    status=Status.NOT_RUN.value,
                    reason="STALE evidence: inputs changed since recording: "
                    + ", ".join(sorted(stale)),
                    stale=True,
                    evidence_id=ev.evidence_id,
                )
                blocking.append(f"{check_id}: NOT_RUN (stale evidence)")
                check_report[check_id] = entry
                continue

            status = ev.status
            submission = self._state.get("submissions", {}).get(ev.evidence_id)
            outcome = submission.get("engineering_outcome") if submission else None

            # Consistency guards: an exit code alone never proves correctness.
            if status is Status.PASS and ev.exit_code not in (None, 0):
                status = Status.FAIL
                entry["reason"] = (
                    f"inconsistent evidence: PASS recorded but exit_code="
                    f"{ev.exit_code}"
                )
            elif status is Status.PASS and ev.missing_report_artifacts():
                status = Status.FAIL
                entry["reason"] = (
                    "missing report artifacts: "
                    + ", ".join(ev.missing_report_artifacts())
                )
            else:
                entry["reason"] = ev.reason

            # PASS without an explicit successful process result is never a
            # clean PASS, except for a fully contracted internal check.
            internal_ok = bool(
                cspec and cspec.execution_kind == "internal"
                and cspec.validator_type and cspec.evidence_contract
                and outcome is not None
                and cspec.validator_type in OUTCOME_VALIDATORS
                and OUTCOME_VALIDATORS[cspec.validator_type](ev, outcome)
            )
            if status is Status.PASS and ev.exit_code is None and not internal_ok:
                status = Status.FAIL
                entry["reason"] = (
                    "PASS recorded without an explicit successful process result "
                    "(exit_code=None)"
                )

            # Tool / internal execution contracts are re-checked at evaluation.
            if status is Status.PASS and cspec is not None:
                if cspec.execution_kind == "tool":
                    validator = OUTCOME_VALIDATORS.get(cspec.engineering_validator)
                    if outcome is None or validator is None or not validator(ev, outcome):
                        status = Status.FAIL
                        entry["reason"] = (
                            f"tool check {check_id} lacks a valid parsed engineering outcome"
                        )
                elif not internal_ok:
                    status = Status.FAIL
                    entry["reason"] = (
                        f"internal check {check_id} lacks a valid validator/contract"
                    )

            # Registered view / input binding constraints.
            check_view = (cspec.required_view if cspec and cspec.required_view else view)
            if status is Status.PASS and check_view and not view_compatible(
                check_view, ev.analysis_view
            ):
                status = Status.FAIL
                entry["reason"] = (
                    f"analysis view mismatch: gate requires {check_view}, "
                    f"evidence is {ev.analysis_view}"
                )
            if status is Status.PASS and spec:
                unbound = [lbl for lbl in spec.required_inputs if lbl not in ev.input_hashes]
                if unbound:
                    status = Status.FAIL
                    entry["reason"] = (
                        "evidence not bound to required inputs: " + ", ".join(unbound)
                    )

            # ---- SR-04/SR-05 evaluation-time revalidation -------------------
            # Re-hash dependencies AND artifacts from their persistent paths and
            # check run/design/stage/tool/view/corner binding. Recorded metadata
            # is never trusted: any problem makes the evidence STALE/invalid and
            # hard-blocks the check (it can never become a clean PASS, nor be
            # waived away). With no expectations configured this is still called
            # so that modified/missing artifacts are caught for every gate.
            rv_problems: list[str] = []
            if status is not Status.NOT_APPLICABLE:
                exp_deps = (
                    (cspec.dependency_expectation() if cspec and cspec.expected_dependencies else None)
                    or (spec.dependency_expectation() if spec else None)
                )
                exp_ctx = (
                    (cspec.context_expectation() if cspec and cspec.expected_context else None)
                    or (spec.context_expectation() if spec else None)
                )
                exp_dir = (
                    (cspec.expected_output_dir if cspec and cspec.expected_output_dir else "")
                    or (spec.expected_output_dir if spec else "")
                    or None
                )
                rv_problems = revalidate_evidence(
                    ev,
                    expected_dependencies=exp_deps,
                    expected_context=exp_ctx,
                    expected_output_dir=exp_dir,
                )
            rv_non_waivable = bool(rv_problems)
            if rv_problems:
                entry["revalidation_problems"] = rv_problems
                blocking.append(
                    f"{check_id}: STALE/invalid evidence: " + "; ".join(rv_problems)
                )
                if status is Status.PASS:
                    status = Status.NOT_RUN
                    entry["reason"] = "STALE/invalid evidence: " + "; ".join(rv_problems)

            # Explicit, multi-dimensional view contracts. The legacy total
            # order is never used here: a physical database cannot satisfy an
            # extracted-timing gate, a GR estimate cannot satisfy routed
            # extraction, and post-fill needs filled+extracted+analysed proof.
            if status is Status.PASS:
                req_contract = (
                    (cspec.contract_expectation() if cspec and cspec.view_contract else None)
                    or (spec.contract_expectation() if spec else None)
                )
                need_fill = bool(
                    (cspec and cspec.require_post_fill_proof)
                    or (spec and spec.require_post_fill_proof)
                )
                if req_contract is not None:
                    actual_contract = evidence_view_contract(ev)
                    if not contract_compatible(req_contract, actual_contract):
                        status = Status.FAIL
                        entry["reason"] = (
                            "view contract mismatch: required "
                            + str(req_contract.to_dict())
                            + ", evidence "
                            + str(actual_contract.to_dict())
                        )
                if need_fill and status is Status.PASS:
                    need_prov = bool(
                        (cspec and cspec.require_extraction_provenance)
                        or (spec and spec.require_extraction_provenance)
                    )
                    if not post_fill_proof_ok(
                        evidence_view_contract(ev),
                        require_extraction_provenance=need_prov,
                    ):
                        status = Status.FAIL
                        entry["reason"] = (
                            "post-fill proof required: need a filled, extracted, "
                            "analysed report"
                            + (" with extraction provenance" if need_prov else "")
                        )

            non_waivable = bool(
                check_id in NON_WAIVABLE_CHECKS or (cspec and not cspec.waivable)
            )

            # F-07 defense in depth: re-validate N/A applicability at evaluation
            # time. A tampered or legacy serialized record cannot smuggle a rule
            # that is not bound to this check (e.g. NO-MACROS for physical DRC)
            # past the authoritative recording boundary.
            if status is Status.NOT_APPLICABLE and not non_waivable:
                try:
                    validate_applicability_for_check(
                        ev.applicability_reason or ev.reason, check_id, self.design_facts
                    )
                except ApplicabilityError as exc:
                    status = Status.FAIL
                    entry["reason"] = f"invalid NOT_APPLICABLE record: {exc}"
                    entry["applicability_error"] = str(exc)
                    blocking.append(f"{check_id}: invalid NOT_APPLICABLE record: {exc}")
                    check_report[check_id] = {
                        **entry,
                        "status": status.value,
                        "evidence_id": ev.evidence_id,
                        "analysis_view": ev.analysis_view,
                        "exit_code": ev.exit_code,
                    }
                    continue

            # U-A: non-waivable checks can NEVER be NOT_APPLICABLE. A persisted
            # legacy N/A record must be converted to a hard-blocking FAIL so no
            # gate can pass on an applicability claim for a functional proof.
            if non_waivable and status is Status.NOT_APPLICABLE:
                status = Status.FAIL
                entry["non_waivable"] = True
                entry["reason"] = (
                    "non-waivable check cannot be NOT_APPLICABLE; "
                    "genuine PASS evidence is required"
                )
                blocking.append(
                    f"{check_id}: non-waivable check declared NOT_APPLICABLE"
                )
                check_report[check_id] = {
                    **entry,
                    "status": status.value,
                    "evidence_id": ev.evidence_id,
                    "analysis_view": ev.analysis_view,
                    "exit_code": ev.exit_code,
                }
                continue

            if status is Status.NOT_APPLICABLE:
                # validated at record time against registered design facts
                na_visible.append(
                    {"check": check_id, "reason": ev.applicability_reason}
                )
                na_checks.append(check_id)

            applicable_waivers = [
                w for w in waivers
                if not non_waivable
                and not rv_non_waivable
                and (spec is None or w.policy != "unspecified")
            ]
            waived = bool(applicable_waivers) and blocks(status)
            if applicable_waivers:
                waived_visible.extend(w.to_dict() for w in applicable_waivers)
            if non_waivable and blocks(status) and waivers:
                entry["non_waivable"] = True
                blocking.append(
                    f"{check_id}: non-waivable functional proof failure "
                    f"({status.value})"
                )
                check_report[check_id] = {
                    **entry,
                    "status": status.value,
                    "evidence_id": ev.evidence_id,
                    "analysis_view": ev.analysis_view,
                    "exit_code": ev.exit_code,
                }
                continue

            entry.update(
                status=status.value,
                evidence_id=ev.evidence_id,
                analysis_view=ev.analysis_view,
                exit_code=ev.exit_code,
            )
            check_report[check_id] = entry

            if blocks(status, waived=waived):
                blocking.append(f"{check_id}: {status.value}")
            if waived:
                waived_checks.append(check_id)
            # F-01: only a PASS derived from simulated/internal evidence is a
            # non-authoritative SIMULATED_PASS. A validated N/A keeps its own
            # disposition (the simulated marker stays visible on the check).
            if ev_simulated and status is Status.PASS:
                simulated_checks.append(check_id)
                entry["simulated_pass"] = True

        decision = "PASS" if not blocking else "BLOCKED"
        if blocking:
            disposition = "BLOCKED"
            reason = "; ".join(blocking)
        elif simulated_checks:
            # F-01: a PASS that relies on caller-labelled simulated/internal
            # evidence is visibly non-authoritative and never qualifies.
            disposition = "SIMULATED_PASS"
            reason = "all required checks PASS"
        elif waived_checks:
            disposition = "WAIVED_PASS"
            reason = "PASS with waived checks: " + ", ".join(waived_checks)
        elif na_checks:
            # U-A: a gate satisfied partly/wholly by validated N/A is NOT a clean PASS.
            disposition = "NOT_APPLICABLE"
            reason = "PASS with validated NOT_APPLICABLE checks: " + ", ".join(na_checks)
        else:
            disposition = "CLEAN_PASS"
            reason = "all required checks PASS"

        # EXT-05 / B-03: a waived FAIL stays a FAIL. It may be reported as
        # WAIVED_PASS (decision=PASS, CLI exit 0) for workflow continuity, but it
        # is NEVER downstream authorization: waived checks are non-authorizing
        # and must not be laundered into a qualified gate by another check's
        # genuine proof. This is the authorization counterpart of the F-01
        # SIMULATED_PASS rule.
        qualified = (
            decision == "PASS"
            and not simulated_checks
            and not waived_checks
        )
        # R6 Stage 2: the authoritative state machine is the ONLY authorization
        # source. A PASS-shaped legacy boolean cannot override it.
        if qualified:
            try:
                from .r6_authority import authoritative_qualification
                # Only checks that actually carry authorization weight count.
                # Waived / simulated checks are non-authorizing and gates with
                # them are already unqualified above. Validated N-A removes a
                # check from scope and does not carry authorization weight.
                _eids: list[str] = []
                _check_bindings: dict[str, str] = {}
                for cid, v in check_report.items():
                    if not isinstance(v, dict) or not v.get("evidence_id"):
                        continue
                    if cid in waived_checks or cid in na_checks or cid in simulated_checks:
                        continue
                    eid = str(v["evidence_id"])
                    if eid in _eids or cid in _check_bindings:
                        qualified = False  # duplicate occupancy -> deny
                        break
                    _eids.append(eid)
                    _check_bindings[cid] = eid
                if qualified and not authoritative_qualification(
                    self, _eids, check_bindings=_check_bindings
                ):
                    qualified = False
            except Exception:  # noqa: BLE001
                qualified = False
        gd = GateDecision(
            gate_id=gate_id,
            decision=decision,
            reason=reason,
            checks=check_report,
            waivers=waived_visible,
            advisory_summaries=len(self._state["summaries"]),
            disposition=disposition,
            waived_checks=waived_checks,
            na_checks=na_checks,
            simulated_checks=simulated_checks,
            qualified=qualified,
        )
        self._state["gate_history"].append(
            {
                "stage": stage,
                "required_checks": effective,
                "required_view": view,
                "upstream_gates": list(upstream),
                **gd.to_dict(),
            }
        )
        # No _save(): the evaluate_gate transaction commits this history record
        # together with its definition/nonce/authorization changes.
        return gd

    def _last_decision(self, gate_id: str) -> dict | None:
        for entry in reversed(self._state["gate_history"]):
            if entry.get("gate_id") == gate_id:
                return entry
        return None

    def can_enter_stage(
        self, stage: str, gates: Sequence[tuple[str, Sequence[str]]], *,
        required_view: str | None = None, upstream: Sequence[str] = ()
    ) -> tuple[bool, list[str]]:
        """Evaluate every gate in ONE fresh-state transaction (A1)."""
        return self._mutate(
            lambda fresh: self._can_enter_stage_locked(
                stage, gates, required_view=required_view, upstream=upstream
            )
        )

    def _can_enter_stage_locked(
        self, stage: str, gates: Sequence[tuple[str, Sequence[str]]], *,
        required_view: str | None = None, upstream: Sequence[str] = ()
    ) -> tuple[bool, list[str]]:
        """Evaluate every gate of *stage*; return (allowed, blocking reasons)."""
        reasons: list[str] = []
        for gate_id, checks in gates:
            gd = self.evaluate_gate(
                gate_id, checks, stage=stage, required_view=required_view,
                upstream_gates=upstream,
            )
            # F-01: SIMULATED_PASS keeps decision=PASS for reporting continuity,
            # but it is not authoritative and cannot authorize a downstream stage.
            if not gd.qualified:
                reasons.append(f"{gate_id}: {gd.reason} ({gd.disposition})")
        return (not reasons), reasons

    # -- cancellation / restart ----------------------------------------------
    def durable_cancellation_requested(self) -> bool:
        """True iff the DURABLE ledger records a cancellation.

        EXT-04: ``_cancel_latch`` and the ``run_status`` property are per-instance
        views. A cancellation committed by ANOTHER process is invisible to both.
        This method establishes the missing synchronization edge: it reads the
        authoritative ``run_status`` from disk INSIDE the same cross-process
        critical section that :meth:`cancel` uses to commit that status, so a
        cancellation accepted before the reader's lock acquisition is observed.

        Fail closed: any unreadable/corrupt/non-mapping durable state is treated
        as cancelled rather than as permission to accept a PASS.
        """
        try:
            with ledger_critical_section(self.state_path):
                durable = self._raw_disk_state()
            return str(durable.get("run_status", "")) == "CANCELLED"
        except Exception:  # noqa: BLE001 - fail closed on any read/lock failure
            return True

    def cancel(self, reason: str) -> None:
        # Set the immediate latch FIRST (synchronous, before any lock/fsync), so
        # an in-flight ECO commit guard sees the cancellation. The durable state
        # is still committed atomically below; a failed transaction still leaves
        # the latch set (fail closed: cancellation is never silently dropped).
        self._cancel_latch = str(reason)
        self._mutate(lambda fresh: self._cancel_locked(reason))

    def cancellation_requested(self) -> bool:
        """True as soon as cancel() is called, before durable commit."""
        return bool(self._cancel_latch)

    def _cancel_locked(self, reason: str) -> None:
        self._state["run_status"] = "CANCELLED"
        self._state["cancel_reason"] = reason

    def restart(self, reason: str = "", *, operator: str = "") -> str:
        """Reopen a cancelled run, preserving all recorded evidence.

        A *valid* restart records who/why; this is surfaced in the report and
        gate history. The no-argument form is retained for compatibility.
        """
        return self._mutate(
            lambda fresh: self._restart_locked(reason, operator=operator)
        )

    def _restart_locked(self, reason: str = "", *, operator: str = "") -> str:
        self._cancel_latch = ""  # restart explicitly clears the immediate latch
        prev = self._state["run_status"]
        self._state["run_status"] = "ACTIVE"
        self._state["cancel_reason"] = ""
        self._state["restart_history"].append(
            {"from": prev, "reason": reason, "operator": operator,
             "created_utc": utc_now()}
        )
        return f"run restarted from {prev}; evidence preserved"

    @property
    def run_status(self) -> str:
        return self._state["run_status"]

    def report(self) -> dict:
        """Full visible state: evidence, waivers (visible), summaries (advisory)."""
        history = list(self._state["gate_history"])
        last = history[-1] if history else {}
        return {
            "run_status": self._state["run_status"],
            "cancel_reason": self._state["cancel_reason"],
            "inputs": dict(self._inputs),
            "current_input_hashes": self.current_input_hashes(),
            "design_facts": dict(self._state["design_facts"]),
            "evidence_count": len(self._state["evidence"]),
            "waivers": list(self._state["waivers"]),
            "advisory_summaries": list(self._state["summaries"]),
            "gate_history": history,
            "restart_history": list(self._state["restart_history"]),
            "last_gate_disposition": last.get("disposition", "NONE"),
            # R5: trust-boundary observability. key source is "environment" only
            # when provisioned outside the ledger; unverifiable definitions are
            # never used for authorization.
            "ledger_key_source": self._ledger_key_source,
            "unverifiable_gate_definitions": sorted(self._unverifiable_definitions),
        }
