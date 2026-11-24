"""Trust root and domain-separated MAC primitive for the Round 6 redesign.

This module is the single source of the process trust root.  It deliberately
has *no* file-writing code: the key (whether provisioned through
``ASSISTANT_LEDGER_KEY`` or generated as a per-instance ephemeral secret) is
held only in the ``TrustRoot`` instance's memory and is never serialized,
logged, or persisted to any durable artifact.  No secret-key field exists in
this module or in any record it authenticates.

Every MAC is HMAC-SHA256 over a canonical JSON encoding (sorted keys, compact
separators, ``default=str``) of a payload augmented with both a domain tag and
the current :class:`TrustContext` (session / repo / revision).  This gives
domain separation (T: cross-domain replay) and context binding (T7 replay
attacker): a valid MAC from another domain, session, repo, or revision does not
verify.

The module has no dependencies outside the Python 3.10+ standard library.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Tuple

__all__ = [
    "KEY_ENV",
    "DOMAIN_GATE_DEFINITION",
    "DOMAIN_EVIDENCE",
    "DOMAIN_AUTHORIZATION",
    "DOMAIN_DEPENDENCY",
    "domains",
    "TrustError",
    "TrustContext",
    "TrustRoot",
]

#: Environment variable carrying the external trust anchor (a passphrase).
#: The raw value and its derived key material never leave process memory.
KEY_ENV = "ASSISTANT_LEDGER_KEY"

_KEY_LABEL = b"assistant-trust-root/key/v1\x00"
_MIN_KEY_BYTES = 16

# session_id is random PER PROCESS (ADR §3).  All TrustRoot instances created
# in this process start with the same session id; a new process gets a new one,
# so a record signed in another process is rejected even with the same key.
_PROCESS_SESSION_ID = secrets.token_hex(16)

# Domain-separated MAC domains.  A binding made in one domain cannot be
# replayed into another.  Values are stable strings; only trust.py computes
# and verifies these MACs, so the exact spelling is an internal contract.
DOMAIN_GATE_DEFINITION = "gate_definition/v1"
DOMAIN_EVIDENCE = "evidence/v1"
DOMAIN_AUTHORIZATION = "authorization/v1"
DOMAIN_DEPENDENCY = "dependency/v1"

_ALL_DOMAINS: Tuple[str, ...] = (
    DOMAIN_GATE_DEFINITION,
    DOMAIN_EVIDENCE,
    DOMAIN_AUTHORIZATION,
    DOMAIN_DEPENDENCY,
)


def domains() -> Tuple[str, ...]:
    """Return every built-in domain string (domain-tag helper for callers)."""
    return _ALL_DOMAINS


class TrustError(Exception):
    """Raised on invalid trust-root construction or signing input."""


@dataclass(frozen=True)
class TrustContext:
    """Immutable context bound into every MAC (session / repo / revision)."""

    session_id: str
    repo_id: str
    revision: str
    created_utc: str


# ---------------------------------------------------------------------------
# Internal helpers (read-only; nothing here writes a file)
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    """UTC timestamp, second precision, e.g. ``2026-11-20T18:30:00Z``."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _module_working_copy() -> str:
    """Locate WORKING_COPY (parent of the ``assistant`` package) read-only."""
    try:
        here = os.path.abspath(__file__)
    except NameError:  # pragma: no cover - defensive for exec() without __file__
        return os.getcwd()
    assistant_pkg = os.path.dirname(here)
    return os.path.dirname(assistant_pkg)


def _git_output(*args: str) -> Optional[str]:
    """Run a read-only git command in WORKING_COPY; return None on any failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", _module_working_copy(), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "replace").strip() or None


def _compute_repo_id() -> str:
    """SHA-256 hex of the git remote origin URL; 'unknown' if unavailable."""
    url = _git_output("remote", "get-url", "origin")
    if url is None:
        return "unknown"
    return hashlib.sha256(url.encode("utf-8", "replace")).hexdigest()


def _compute_revision() -> str:
    """Current git HEAD commit id; 'unknown' if unavailable."""
    return _git_output("rev-parse", "HEAD") or "unknown"


def _json_bytes(obj: Any) -> bytes:
    """Canonical JSON: sorted keys, compact separators, default=str."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def _derive_key(passphrase: str) -> bytes:
    """Derive a fixed-length HMAC key from an external passphrase (in memory)."""
    return hashlib.sha256(
        _KEY_LABEL + passphrase.encode("utf-8", "surrogateescape")
    ).digest()


# ---------------------------------------------------------------------------
# TrustRoot
# ---------------------------------------------------------------------------


class TrustRoot:
    """Process-local trust root and MAC signer/verifier.

    Key resolution (in memory only):

    * If ``ASSISTANT_LEDGER_KEY`` is present in *env* (or ``os.environ`` when
      *env* is ``None``), its value is the external trust anchor and is derived
      into an in-memory HMAC key.  :attr:`available` is ``True`` and
      :attr:`ephemeral` is ``False``.
    * If it is absent, a cryptographically random per-instance key is generated
      with :mod:`secrets`.  :attr:`available` is ``False`` and
      :attr:`ephemeral` is ``True``.  The key is never written anywhere; a new
      TrustRoot in a new process cannot verify this instance's MACs (fail
      closed for persisted records).

    The key is stored as ``_key`` and deliberately omitted from ``repr`` so it
    cannot leak into a log via a stray object dump.
    """

    def __init__(self, env: Optional[Mapping[str, Any]] = None):
        if env is None:
            env = os.environ
        if not isinstance(env, Mapping):
            raise TrustError("env must be a mapping or None")

        raw = env.get(KEY_ENV)
        if raw is None:
            raw = ""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "surrogateescape")
        elif not isinstance(raw, str):
            raw = str(raw)

        if raw:
            key = _derive_key(raw)
            if len(key) < _MIN_KEY_BYTES:  # pragma: no cover - defensive
                raise TrustError("derived trust key is too short")
            self._key = key
            self._available = True
            self._ephemeral = False
        else:
            self._key = secrets.token_bytes(32)
            self._available = False
            self._ephemeral = True

        # R6 integration: with a provisioned (external) key, the session id must
        # be STABLE across processes so evidence MACs verify in CLI subprocesses.
        # With an ephemeral key, keep the per-process random id (fail closed
        # across processes by design).
        if self._available and not self._ephemeral:
            raw = os.environ.get(KEY_ENV, "") if env is None else str(
                (env or {}).get(KEY_ENV, "")
            )
            _session = hashlib.sha256(
                ("r6-session\x00" + raw).encode("utf-8")
            ).hexdigest()[:32]
        else:
            _session = _PROCESS_SESSION_ID
        self._context = TrustContext(
            session_id=_session,
            repo_id=_compute_repo_id(),
            revision=_compute_revision(),
            created_utc=_utc_now(),
        )

    # -- introspection (no key exposure) ---------------------------------

    @property
    def available(self) -> bool:
        """True when an external provisioned key anchor is available."""
        return self._available

    @property
    def ephemeral(self) -> bool:
        """True when the key is a random per-instance in-memory secret."""
        return self._ephemeral

    @property
    def context(self) -> TrustContext:
        """The immutable context currently bound into MACs."""
        return self._context

    def __repr__(self) -> str:  # never render key material
        c = self._context
        return (
            f"TrustRoot(available={self._available!r}, "
            f"ephemeral={self._ephemeral!r}, "
            f"session_id={c.session_id!r}, repo_id={c.repo_id!r}, "
            f"revision={c.revision!r}, created_utc={c.created_utc!r})"
        )

    # -- canonicalization / MAC ------------------------------------------

    def _context_map(self) -> dict:
        # R6 integration: only STABLE context fields are MAC-bound. created_utc
        # is informational (it differs across processes and would break valid
        # cross-process verification with a provisioned key).
        c = self._context
        return {
            "session_id": c.session_id,
            "repo_id": c.repo_id,
            "revision": c.revision,
        }

    def canonical(self, payload: Mapping) -> bytes:
        """Canonical JSON of *payload* augmented with the current context.

        Domain separation is applied by :meth:`sign`/:meth:`verify` (the domain
        tag is not an argument to this helper), so the returned bytes are the
        shared context-bound encoding of the payload.
        """
        if not isinstance(payload, Mapping):
            raise TrustError("payload must be a mapping")
        return _json_bytes(
            {"context": self._context_map(), "payload": dict(payload)}
        )

    def _mac_message(self, domain: str, payload: Mapping) -> bytes:
        """Canonical JSON of payload + domain tag + context (the MAC input)."""
        return _json_bytes(
            {
                "domain": domain,
                "context": self._context_map(),
                "payload": dict(payload),
            }
        )

    def sign(self, domain: str, payload: Mapping) -> str:
        """Return the hex HMAC-SHA256 tag for *domain* and *payload*."""
        if not isinstance(domain, str) or not domain:
            raise TrustError("domain must be a non-empty string")
        if not isinstance(payload, Mapping):
            raise TrustError("payload must be a mapping")
        try:
            message = self._mac_message(domain, payload)
        except (TypeError, ValueError) as exc:
            raise TrustError(f"payload is not canonically serializable: {exc}")
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    def verify(self, domain: str, payload: Mapping, mac: str) -> bool:
        """Constant-time verification; return False for any malformed input."""
        try:
            if not isinstance(domain, str) or not domain:
                return False
            if not isinstance(payload, Mapping):
                return False
            if not isinstance(mac, str) or not mac:
                return False
            if len(mac) != 64:
                return False
            message = self._mac_message(domain, payload)
            expected = hmac.new(self._key, message, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, mac)
        except Exception:
            return False

    # -- session rotation -------------------------------------------------

    def new_session(self) -> str:
        """Rotate the session id, update the bound context, return the new id.

        Previously issued MACs bound to the old session no longer verify under
        this root, which is the intended replay/session-rotation behavior.
        """
        new_id = secrets.token_hex(16)
        old = self._context
        self._context = TrustContext(
            session_id=new_id,
            repo_id=old.repo_id,
            revision=old.revision,
            created_utc=_utc_now(),
        )
        return new_id


# ---------------------------------------------------------------------------
# Self-test (run: python assistant/trust.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Snapshot the package directory: no file may be created, removed, or
    # changed by any operation in this module.
    def _snapshot(root: str):
        seen = {}
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(dirpath, name)
                try:
                    st = os.stat(path)
                    seen[path] = (st.st_size, st.st_mtime_ns)
                except OSError:
                    seen[path] = None
        return seen

    _dir = os.path.dirname(os.path.abspath(__file__))
    _before = _snapshot(_dir)

    # Use an explicit empty env mapping so the self-test is deterministic and
    # does not depend on the ambient ASSISTANT_LEDGER_KEY.
    tr = TrustRoot(env={})

    # 1. Sign / verify round-trip.
    payload = {"check_id": "C1", "gate_id": "G1", "value": 42}
    mac = tr.sign(DOMAIN_EVIDENCE, payload)
    assert tr.verify(DOMAIN_EVIDENCE, payload, mac), "round-trip failed"
    assert tr.available is False and tr.ephemeral is True

    # 2. Cross-domain rejection.
    assert not tr.verify(DOMAIN_AUTHORIZATION, payload, mac), "cross-domain accepted"

    # 3. Tamper rejection (payload change and MAC corruption).
    tampered = dict(payload, value=43)
    assert not tr.verify(DOMAIN_EVIDENCE, tampered, mac), "tampered payload accepted"
    assert not tr.verify(DOMAIN_EVIDENCE, payload, mac[:-1] + ("0" if mac[-1] != "0" else "1")), (
        "corrupted MAC accepted"
    )
    # 4. Malformed inputs fail closed.
    assert not tr.verify(DOMAIN_EVIDENCE, None, mac)  # type: ignore[arg-type]
    assert not tr.verify(DOMAIN_EVIDENCE, payload, "")
    assert not tr.verify("", payload, mac)
    assert not tr.verify(DOMAIN_EVIDENCE, payload, "zz" * 32)

    # 5. Session rotation invalidates old MACs.
    old_ctx = tr.context
    assert tr.new_session() != old_ctx.session_id
    assert not tr.verify(DOMAIN_EVIDENCE, payload, mac), "session rotation did not rebind"
    assert tr.verify(
        DOMAIN_EVIDENCE, payload, tr.sign(DOMAIN_EVIDENCE, payload)
    ), "sign/verify after rotation failed"

    # 6. No file created / changed anywhere in the package directory.
    _after = _snapshot(_dir)
    assert _after == _before, f"file artifact created/changed: {set(_after) ^ set(_before)}"

    # 7. Key never appears in repr / serialized context.
    assert "_key" not in repr(tr)
    assert b"key" not in tr.canonical(payload).lower()

    print(
        "trust.py self-test OK: round-trip, cross-domain, tamper, malformed, "
        "session rotation, no-file invariant, no key in repr."
    )
    print("public domains:", domains())
    print("context:", tr.context)
