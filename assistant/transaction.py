"""Durable transaction journal with fail-closed authorization derivation.

Storage format
--------------
The ``path`` given to :class:`TransactionJournal` is a JSON file holding a
**list of record dicts** (an append-only version log).  ``begin``, ``append``
and ``finalize`` each add one new immutable version; earlier versions are
retained so that a contradiction (two distinct terminal states for one
``transaction_id``) remains observable during recovery.  Records are never
edited in place and the file is never truncated in place: every mutation goes
through :func:`atomic_write` (temp file in the same directory + flush +
fsync + ``os.replace``).

MAC payload contract
--------------------
``mac = trust.sign(domain, payload)`` where ``payload`` is the whole record
dict *except* ``mac`` and ``terminal``.  ``terminal`` is a redundant derived
marker (always mirrored by ``complete`` plus ``state``) and is intentionally
excluded from both signing and verification: Agent 3's closed record schema
rejects the ``terminal`` key, so :meth:`derive` projects it away before
calling the single authorization function, leaving the MAC over the remaining
fields intact.  No authority is added by the projection.  The domain value is
resolved from Agent 1's ``trust.DOMAIN_AUTHORIZATION`` (``authorization/v1``),
with a literal fallback so this module stays independently testable.

Design decisions (documented for the coordinator)
-------------------------------------------------
* ``finalize`` has no ``transaction_id`` parameter (per the ADR interface), so
  the target is resolved in this order: (1) ``context["transaction_id"]`` if
  supplied, (2) the id set by the most recent ``begin``/``append`` on this
  journal instance, (3) the sole non-terminal record in the durable log.  An
  ambiguous/absent target raises ``ValueError``; no write occurs.
* ``append`` is strictly non-authorizing: it refuses terminal records and
  refuses to set a terminal state (use ``finalize`` instead).
* ``finalize`` computes and validates the terminal state *before* a single
  atomic write; there is no post-commit amendment of any terminal record,
  and especially not of a ``QUALIFIED_PASS``.
* Context-binding keys (``session_id``, ``repo_id``, ``revision``) may not
  change between versions of one transaction; a mismatch raises
  ``ValueError`` before any write.
* A module-level re-entrant lock serializes read-modify-write cycles within a
  process.  Cross-process concurrent writers are out of scope here (the OS
  process boundary is a trust boundary of its own); flag to the coordinator
  if cross-process coordination is required.

Python 3.10, standard library only.
"""
from __future__ import annotations

import importlib
import json
import os
import tempfile
import threading
from dataclasses import dataclass
from typing import Any, Mapping

__all__ = [
    "DOMAIN_AUTHORIZATION",
    "TERMINAL_STATES",
    "PENDING_STATE",
    "CONTEXT_BINDING_KEYS",
    "atomic_write",
    "TransactionJournal",
]

#: Domain tag for transaction/authorization records (ADR §3).  Mirrors Agent 1's
#: ``trust.DOMAIN_AUTHORIZATION``; ``_auth_domain`` prefers that live constant.
DOMAIN_AUTHORIZATION = "authorization/v1"

#: The only states permitted as a terminal outcome (ADR §"finalize").
TERMINAL_STATES = frozenset({"QUALIFIED_PASS", "FAILED", "EXPIRED", "INVALIDATED"})

#: Non-authorizing initial state (ADR §"state_machine.py").
PENDING_STATE = "PENDING"

#: Fields that bind a record to a session/repo/revision (ADR §3, T7).
CONTEXT_BINDING_KEYS = ("session_id", "repo_id", "revision")

# Serializes begin/append/finalize read-modify-write cycles in this process.
_WRITE_LOCK = threading.RLock()


def _fsync_dir(directory: str) -> None:
    """Best-effort directory fsync (durability of the rename).  Never raises."""
    try:
        if os.name == "nt":  # Windows directory handles are not openable.
            return
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:  # pragma: no cover - platform dependent
        pass


def atomic_write(path: str | os.PathLike[str], obj: Any) -> None:
    """Atomically replace *path* with the JSON serialization of *obj*.

    The JSON is written to a temporary file in the *same directory*, flushed
    and ``os.fsync``-ed, then moved into place with ``os.replace``.  On any
    failure the temporary file is removed and the destination is left
    untouched (no partial writes, no in-place truncation).
    """
    target = os.fspath(path)
    parent = os.path.dirname(os.path.abspath(target)) or "."
    fd: int | None = None
    tmp: str | None = None
    try:
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".txn-", suffix=".tmp", dir=parent)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = None  # ownership transferred to the file object
            json.dump(obj, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        tmp = None
        _fsync_dir(parent)
    except BaseException:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


@dataclass(frozen=True)
class _FallbackDecision:
    """Minimal fail-closed decision used only when state_machine is absent."""

    authorized: bool
    reasons: tuple
    state: Any
    transaction_id: str


def _import_state_machine():
    """Lazily import the state machine; return ``None`` if unavailable."""
    candidates: list[str] = []
    if __package__:
        candidates.append(__package__ + ".state_machine")
    candidates.extend(("assistant.state_machine", "state_machine"))
    for name in dict.fromkeys(candidates):
        try:
            return importlib.import_module(name)
        except Exception:
            continue
    return None


def _auth_domain() -> str:
    """Resolve the authorization domain from Agent 1's trust module lazily."""
    for name in (".trust", "assistant.trust"):
        try:
            module = importlib.import_module(name, __package__)
            domain = getattr(module, "DOMAIN_AUTHORIZATION", None)
            if isinstance(domain, str) and domain:
                return domain
        except Exception:
            continue
    return DOMAIN_AUTHORIZATION


def _fail_closed(reason: str, transaction_id: Any = "", sm: Any = None):
    """Build a non-authorizing decision, preferring the real decision type."""
    cls = getattr(sm, "AuthorizationDecision", None) if sm is not None else None
    qual = getattr(sm, "QualState", None) if sm is not None else None
    state = getattr(qual, "INVALIDATED", "INVALIDATED") if qual is not None else "INVALIDATED"
    if cls is None:
        cls = _FallbackDecision
    try:
        return cls(
            authorized=False,
            reasons=(reason,),
            state=state,
            transaction_id=str(transaction_id or ""),
        )
    except Exception:  # pragma: no cover - defensive against a foreign ctor
        return _FallbackDecision(False, (reason,), state, str(transaction_id or ""))


def _context_compatible(new: Mapping, old: Mapping) -> bool:
    """True when no context-binding key changed between two versions."""
    for key in CONTEXT_BINDING_KEYS:
        if key in old and key in new and new[key] != old[key]:
            return False
    return True


class TransactionJournal:
    """Append-only JSON transaction log guarded by an external trust root."""

    def __init__(self, path: str | os.PathLike[str], trust: Any):
        self.path = os.fspath(path)
        self.trust = trust
        self._active_tid: str | None = None

    # -- helpers ---------------------------------------------------------
    def _sign(self, record: Mapping) -> str:
        # ``terminal`` is a derived marker mirrored by ``complete``/``state``
        # and is outside the closed schema enforced by state_machine check 10.
        payload = {k: v for k, v in record.items() if k not in ("mac", "terminal")}
        return self.trust.sign(_auth_domain(), payload)

    @staticmethod
    def _is_terminal(record: Mapping) -> bool:
        return bool(record.get("terminal")) or bool(record.get("complete")) or (
            str(record.get("state")) in TERMINAL_STATES
        )

    @staticmethod
    def _latest(records: list[dict], transaction_id: Any):
        """Return ``(index, record)`` of the newest version, or ``None``."""
        entries = [
            (i, r)
            for i, r in enumerate(records)
            if r.get("transaction_id") == transaction_id
        ]
        if not entries:
            return None
        return max(
            entries,
            key=lambda pair: (int(pair[1].get("version", 0) or 0), pair[0]),
        )

    # -- public API ------------------------------------------------------
    def begin(self, transaction_id: str, deadline_utc: str, context: Mapping) -> dict:
        """Create a PENDING (non-authorizing) record and commit it atomically."""
        tid = str(transaction_id)
        if not tid:
            raise ValueError("transaction_id must be non-empty")
        with _WRITE_LOCK:
            records = self.load()
            if any(r.get("transaction_id") == tid for r in records):
                raise ValueError("transaction_id already exists")
            ctx = dict(context or {})
            record: dict[str, Any] = {
                "transaction_id": tid,
                "version": 1,
                "state": PENDING_STATE,
                "terminal": False,
                "complete": False,
                "deadline_utc": deadline_utc,
                "context": ctx,
                "evidence": [],
                "mac": "",
            }
            record["mac"] = self._sign(record)
            records.append(record)
            atomic_write(self.path, records)
            self._active_tid = tid
            return dict(record)

    def append(self, record: Mapping) -> dict:
        """Add a new non-authorizing version of an existing transaction."""
        if not isinstance(record, Mapping):
            raise ValueError("record must be a mapping")
        tid = record.get("transaction_id")
        if tid is None:
            raise ValueError("record has no transaction_id")
        with _WRITE_LOCK:
            records = self.load()
            found = self._latest(records, tid)
            if found is None:
                raise ValueError("unknown transaction_id")
            _idx, previous = found
            if self._is_terminal(previous):
                raise ValueError("cannot append to a terminal record")
            state = record.get("state", previous.get("state", PENDING_STATE))
            if str(state) in TERMINAL_STATES or record.get("terminal") or record.get(
                "complete"
            ):
                raise ValueError("append cannot set a terminal state; use finalize")
            ctx = dict(record["context"]) if "context" in record else dict(
                previous.get("context", {})
            )
            if not _context_compatible(ctx, previous.get("context", {})):
                raise ValueError("context binding mismatch")
            new: dict[str, Any] = dict(record)
            new.update(
                {
                    "transaction_id": tid,
                    "version": int(previous.get("version", 0) or 0) + 1,
                    "state": str(state),
                    "terminal": False,
                    "complete": False,
                    "deadline_utc": record.get(
                        "deadline_utc", previous.get("deadline_utc")
                    ),
                    "context": ctx,
                    "evidence": list(
                        record.get("evidence", previous.get("evidence", [])) or []
                    ),
                }
            )
            new.pop("mac", None)
            new["mac"] = self._sign(new)
            records.append(new)
            atomic_write(self.path, records)
            self._active_tid = str(tid)
            return dict(new)

    def finalize(
        self, terminal_state: str, evidence: tuple, context: Mapping
    ) -> dict:
        """Commit ONE atomic terminal record; no post-commit amendment."""
        state = str(terminal_state)
        if state not in TERMINAL_STATES:
            raise ValueError("invalid terminal state: %r" % (terminal_state,))
        ctx_arg = dict(context or {})
        with _WRITE_LOCK:
            records = self.load()
            tid = ctx_arg.get("transaction_id", self._active_tid)
            if tid is None:
                non_terminal = [r for r in records if not self._is_terminal(r)]
                if len(non_terminal) != 1:
                    raise ValueError("ambiguous or absent active transaction")
                tid = non_terminal[-1].get("transaction_id")
            found = self._latest(records, tid)
            if found is None:
                raise ValueError("unknown transaction_id")
            _idx, previous = found
            if self._is_terminal(previous):
                raise ValueError("transaction already terminal; no amendment")
            ctx = dict(previous.get("context", {}))
            if not _context_compatible(ctx_arg, ctx):
                raise ValueError("context binding mismatch")
            # State is fixed BEFORE the write: a single atomic commit follows.
            terminal: dict[str, Any] = dict(previous)
            terminal.update(
                {
                    "transaction_id": tid,
                    "version": int(previous.get("version", 0) or 0) + 1,
                    "state": state,
                    "terminal": True,
                    "complete": True,
                    "deadline_utc": previous.get("deadline_utc"),
                    "context": ctx,
                    "evidence": list(evidence or []),
                }
            )
            terminal.pop("mac", None)
            terminal["mac"] = self._sign(terminal)
            records.append(terminal)
            atomic_write(self.path, records)  # the only write; failure leaves PENDING
            self._active_tid = None
            return dict(terminal)

    def load(self) -> list[dict]:
        """Safe load: corrupt/truncated/missing JSON yields ``[]``; never raises."""
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        return [dict(item) for item in data if isinstance(item, dict)]

    def derive(self, **kwargs) -> Any:
        """Re-derive authorization from durable records; fail closed on error."""
        sm = None
        tid = self._active_tid or ""
        try:
            records = self.load()
            sm = _import_state_machine()

            by_tid: dict[str, list[tuple[int, dict]]] = {}
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    continue
                value = record.get("transaction_id")
                if value is None:
                    continue
                by_tid.setdefault(str(value), []).append((index, record))

            # Contradiction check spans ALL versions, not just the latest.
            for txid, entries in by_tid.items():
                terminal_states = {
                    str(rec.get("state"))
                    for _i, rec in entries
                    if rec.get("terminal") is True
                    or (rec.get("complete") is True and self._is_terminal(rec))
                }
                if len(terminal_states) > 1:
                    return _fail_closed("contradictory durable state", txid, sm)

            latest: list[tuple[int, dict]] = []
            for entries in by_tid.values():
                latest.append(
                    max(
                        entries,
                        key=lambda pair: (int(pair[1].get("version", 0) or 0), pair[0]),
                    )
                )
            latest.sort(key=lambda pair: pair[0])

            if sm is None or not callable(getattr(sm, "derive_authorization", None)):
                return _fail_closed("state_machine unavailable", tid, sm)

            last_decision = None
            for index, record in latest:
                tid = str(record.get("transaction_id", tid))
                # Project away only the redundant ``terminal`` marker so the
                # closed schema of derive_authorization accepts the record;
                # the MAC (computed over the same projected payload) stays
                # valid and no field is added or weakened.
                view = {k: v for k, v in record.items() if k != "terminal"}
                decision = sm.derive_authorization(view, self.trust, **kwargs)
                if decision is None:
                    continue
                if getattr(decision, "authorized", False) is True:
                    return decision
                last_decision = decision
            if last_decision is not None:
                return last_decision
            return _fail_closed("no durable records", tid, sm)
        except BaseException:
            return _fail_closed("derive failure", tid, sm)


# ---------------------------------------------------------------------------
# Self-test: run ``python transaction.py`` (stdlib only, no other agent module
# needs to exist).  Fake trust/state_machine modules are injected locally.
# ---------------------------------------------------------------------------
def _self_test() -> None:  # pragma: no cover - exercised via __main__
    import sys
    import types
    from enum import Enum

    class FakeTrust:
        def sign(self, domain: str, payload: Mapping) -> str:
            body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            return "mac:" + domain + ":" + body

        def verify(self, domain: str, payload: Mapping, mac: str) -> bool:
            return mac == self.sign(domain, payload)

    fake_sm = types.ModuleType("assistant.state_machine")

    class QualState(str, Enum):
        INITIAL = "INITIAL"
        PENDING = "PENDING"
        QUALIFIED_PASS = "QUALIFIED_PASS"
        FAILED = "FAILED"
        EXPIRED = "EXPIRED"
        INVALIDATED = "INVALIDATED"

    @dataclass(frozen=True)
    class AuthorizationDecision:
        authorized: bool
        reasons: tuple
        state: QualState
        transaction_id: str

    def derive_authorization(record: Mapping, trust, **kwargs):
        txid = str(record.get("transaction_id", ""))
        payload = {k: v for k, v in record.items() if k != "mac"}
        if not trust.verify(_auth_domain(), payload, record.get("mac", "")):
            return AuthorizationDecision(False, ("mac invalid",), QualState.INVALIDATED, txid)
        # The real state machine uses ``complete`` (not ``terminal``), which is
        # why derive() projects the redundant ``terminal`` marker away.
        if (
            str(record.get("state")) == "QUALIFIED_PASS"
            and record.get("complete") is True
        ):
            return AuthorizationDecision(True, ("qualified",), QualState.QUALIFIED_PASS, txid)
        return AuthorizationDecision(False, ("not qualified",), QualState.PENDING, txid)

    fake_sm.QualState = QualState
    fake_sm.AuthorizationDecision = AuthorizationDecision
    fake_sm.derive_authorization = derive_authorization
    sys.modules["assistant.state_machine"] = fake_sm
    sys.modules["state_machine"] = fake_sm

    trust = FakeTrust()
    ctx = {"session_id": "s1", "repo_id": "r1", "revision": "h1"}

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "ledger.json")

        # 1) begin -> PENDING non-authorizing
        j = TransactionJournal(path, trust)
        rec = j.begin("T1", "2030-01-01T00:00:00Z", ctx)
        assert rec["state"] == "PENDING" and rec["terminal"] is False
        assert rec["evidence"] == [] and rec["transaction_id"] == "T1"
        assert j.derive().authorized is False, "PENDING must not authorize"
        print("[ok] begin/PENDING is non-authorizing")

        # 2) append is non-authorizing and refuses terminal escalation
        j.append({"transaction_id": "T1", "state": "VERIFYING"})
        assert j.derive().authorized is False
        try:
            j.append({"transaction_id": "T1", "state": "QUALIFIED_PASS"})
        except ValueError:
            pass
        else:
            raise AssertionError("append must refuse a terminal state")
        assert j.derive().authorized is False, "refused append left state intact"
        print("[ok] append refuses terminal escalation")

        # 3) finalize QUALIFIED_PASS -> authorizing
        passed = j.finalize("QUALIFIED_PASS", (), ctx)
        assert passed["terminal"] is True and passed["complete"] is True
        assert passed["state"] == "QUALIFIED_PASS"
        assert j.derive().authorized is True, "QUALIFIED_PASS must authorize"
        print("[ok] finalize QUALIFIED_PASS authorizes")

        # 4) no post-commit amendment of a terminal record
        try:
            j.finalize("FAILED", (), ctx)
        except ValueError:
            pass
        else:
            raise AssertionError("terminal record was amended")
        assert j.derive().authorized is True
        print("[ok] terminal record cannot be amended")

        # 5) atomic_write failure during finalize -> prior PENDING survives
        path2 = os.path.join(folder, "ledger2.json")
        j2 = TransactionJournal(path2, trust)
        j2.begin("T2", "2030-01-01T00:00:00Z", ctx)
        original = globals()["atomic_write"]

        def boom(_path, _obj):
            raise OSError("simulated disk full")

        globals()["atomic_write"] = boom
        try:
            try:
                j2.finalize("QUALIFIED_PASS", (), ctx)
            except OSError:
                pass
            else:
                raise AssertionError("finalize should have propagated the write error")
        finally:
            globals()["atomic_write"] = original
        assert j2.load()[-1]["state"] == "PENDING"
        assert j2.derive().authorized is False, "failed commit must not yield a PASS"
        print("[ok] atomic write failure leaves PENDING (never a PASS)")

        # 6) finalize FAILED -> non-authorizing
        path3 = os.path.join(folder, "ledger3.json")
        j3 = TransactionJournal(path3, trust)
        j3.begin("T3", "2030-01-01T00:00:00Z", ctx)
        failed = j3.finalize("FAILED", (), ctx)
        assert failed["terminal"] is True and failed["state"] == "FAILED"
        assert j3.derive().authorized is False
        print("[ok] finalize FAILED is non-authorizing")

        # 7) two distinct terminal states for one transaction -> contradiction
        path4 = os.path.join(folder, "ledger4.json")
        tA = {
            "transaction_id": "T4", "version": 1, "state": "QUALIFIED_PASS",
            "terminal": True, "complete": True, "deadline_utc": "2030-01-01T00:00:00Z",
            "context": ctx, "evidence": [], "mac": "forged",
        }
        tB = dict(tA, version=2, state="FAILED")
        atomic_write(path4, [tA, tB])
        decision = TransactionJournal(path4, trust).derive()
        assert decision.authorized is False
        assert "contradictory durable state" in decision.reasons
        print("[ok] contradictory durable state fails closed")

        # 8) corrupt JSON -> load() == [], derive() fail closed
        with open(path4, "w", encoding="utf-8") as handle:
            handle.write('[{"transaction_id": "T4", "state": "QUAL')
        j4 = TransactionJournal(path4, trust)
        assert j4.load() == []
        assert j4.derive().authorized is False
        print("[ok] corrupt JSON loads empty and fails closed")

    print("ALL TRANSACTION SELF-TESTS PASSED")


if __name__ == "__main__":
    _self_test()
