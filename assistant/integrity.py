"""Immutable verified content and dependency-closure integrity (Round 6, Agent 6).

This module closes the Round 5 ``VerifiedCandidate`` bypass.  In Round 5 a
"verified" object held a *mutable* ``snapshot`` mapping and a path, and
``read_bytes(rel)`` re-read from disk on every call -- so replacing the bytes on
disk after acceptance silently changed what downstream consumers received.

The Round 6 model is intentionally different:

* :class:`VerifiedContent` is a frozen dataclass that captures the
  **exact bytes** at verification time.  :meth:`VerifiedContent.read_bytes`
  returns that one immutable ``bytes`` object (same object, same value, on every
  call) -- there is no disk re-read, no mutable mapping and no path indirection.
  A downstream consumer must :meth:`VerifiedContent.materialize` a fresh file
  from those bytes, and :func:`verify_content` re-hashes at consumption.

* :class:`DependencyClosure` snapshots the **actual resolved bytes** of every
  dependency (tool binaries + collateral), following symlinks/junctions with
  ``os.path.realpath``.  Snapshots are deterministic, sorted tuples of
  ``(canonical_abs_path, sha256, size)``.  Re-verification re-hashes each entry;
  any content change, size change, missing file or resolution change fails
  closed.  :meth:`~DependencyClosure.bind` / :meth:`~DependencyClosure.verify_bound`
  seal a snapshot with a domain-separated MAC from the trust root.

No external dependencies.  Python 3.10+.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from dataclasses import dataclass
from typing import Iterable, Mapping

try:  # ``str | Path`` / ``os.PathLike`` are only used in annotations.
    from pathlib import Path
except ImportError:  # pragma: no cover - pathlib is in the stdlib on 3.10+
    Path = None  # type: ignore[assignment]

import re as _re

# Domain-separated MAC tag for dependency bindings.  Prefer the trust root's
# canonical definition when it is available (Agent 1 owns ``assistant/trust.py``
# and defines the four ADR §3 domains).  Fall back to the conventional literal
# so this module remains importable in isolation / during staged integration.
try:  # package import (normal integration)
    from .trust import DOMAIN_DEPENDENCY  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - defensive during staged integration
    try:  # top-level import (standalone self-test / scripts)
        from trust import DOMAIN_DEPENDENCY  # type: ignore[no-redef]
    except Exception:  # pragma: no cover
        DOMAIN_DEPENDENCY = "dependency/v1"

__all__ = [
    "IntegrityError",
    "VerifiedContent",
    "verify_content",
    "DependencyClosure",
    "DOMAIN_DEPENDENCY",
]


class IntegrityError(RuntimeError):
    """Raised when content identity or a dependency closure cannot be trusted."""


_HEX64 = _re.compile(r"^[0-9a-fA-F]{64}$")


# ---------------------------------------------------------------------------
# Immutable verified content
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VerifiedContent:
    """A hash-verified, immutable byte buffer captured at verification time.

    The ``data`` field is coerced to a genuine :class:`bytes` object and the
    class is frozen, so no caller can reassign ``data``/``digest``/``source_path``
    *or* attach a new attribute (e.g. a mutable ``snapshot``) -- the standard
    frozen-dataclass ``__setattr__`` raises ``FrozenInstanceError`` for every
    assignment on an exact instance of this class.  ``read_bytes()`` therefore
    always yields the identical object, defeating the Round 5 "mutate the source
    file / mutable mapping" bypass.  (``slots=True`` is deliberately NOT used:
    on Python 3.10 it generates a broken ``__setattr__`` -- bpo-46725.)
    """

    digest: str
    data: bytes
    source_path: str

    def __post_init__(self) -> None:
        # Guarantee true immutability even if a caller passed a bytearray or a
        # memoryview.  ``bytes(x)`` is identity-preserving when x is already
        # bytes, so from_path() objects are unaffected.
        if not isinstance(self.data, bytes):
            object.__setattr__(self, "data", bytes(self.data))
        # Normalise/validate source_path to a stable string.
        object.__setattr__(self, "source_path", os.fspath(self.source_path))
        # A well-formed digest is required for the binding to be meaningful;
        # do NOT check data-vs-digest here so that materialize() remains the
        # single place that detects a mismatch (disk corruption / bad input).
        if not isinstance(self.digest, str) or not _HEX64.match(self.digest):
            raise IntegrityError(
                "VerifiedContent.digest must be a 64-character hexadecimal SHA-256"
            )

    def read_bytes(self) -> bytes:
        """Return the *exact* immutable bytes captured at verification time.

        This never touches the filesystem and never consults ``source_path``.
        Every call returns the same object (``vc.read_bytes() is
        vc.read_bytes()``), so no downstream consumer can observe a file that
        was modified after verification.
        """
        return self.data

    def materialize(self, path: str | os.PathLike[str]) -> None:
        """Atomically write the verified bytes to ``path`` and re-verify.

        Uses temp-file + flush + fsync + ``os.replace`` (and best-effort
        directory fsync) so a partial/torn write is never visible at ``path``.
        After the rename the file is read back and its SHA-256 compared to
        ``self.digest``; any mismatch raises :class:`IntegrityError`.
        """
        target = os.fspath(path)
        directory = os.path.dirname(os.path.abspath(target)) or os.curdir
        fd: int | None = None
        tmp: str | None = None
        try:
            fd, tmp = tempfile.mkstemp(
                prefix=".verified-content-", suffix=".tmp", dir=directory
            )
            with os.fdopen(fd, "wb") as fh:
                fd = None  # ownership transferred to the file object
                fh.write(self.data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
            tmp = None  # rename succeeded; nothing left to clean up
            _fsync_dir(directory)
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

        # Re-verify the bytes that actually landed on disk.
        try:
            with open(target, "rb") as fh:
                written = fh.read()
        except OSError as exc:
            raise IntegrityError(
                f"materialize: cannot read back {target!r} for verification"
            ) from exc
        if not verify_content(written, self.digest):
            raise IntegrityError(
                f"materialize: SHA-256 mismatch after writing {target!r}; "
                "refusing to return unverified content"
            )

    @classmethod
    def from_path(cls, path: str | os.PathLike[str]) -> "VerifiedContent":
        """Read ``path``, hash its bytes and return a :class:`VerifiedContent`.

        The bytes are captured into the returned object; subsequent changes to
        the file on disk are irrelevant to :meth:`read_bytes`.
        """
        raw = os.fspath(path)
        with open(raw, "rb") as fh:
            data = fh.read()
        digest = hashlib.sha256(data).hexdigest()
        return cls(
            digest=digest,
            data=data,
            source_path=os.path.abspath(os.path.realpath(raw)),
        )


def verify_content(data: bytes, digest: str) -> bool:
    """Return True iff ``sha256(data)`` equals ``digest`` in constant time.

    Uses :func:`hmac.compare_digest` so the comparison leaks no timing signal
    about the expected digest.  Malformed digests / types fail closed.
    """
    if not isinstance(digest, str):
        return False
    actual = hashlib.sha256(data).hexdigest()
    try:
        return hmac.compare_digest(actual, digest)
    except TypeError:  # e.g. non-ASCII digest string
        return False


def _fsync_dir(directory: str) -> None:
    """Best-effort directory fsync; ignored where the OS forbids it (Windows)."""
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Dependency closure
# ---------------------------------------------------------------------------

# A snapshot entry is (canonical_abs_path, sha256_hex, size_bytes).
_SnapshotEntry = tuple[str, str, int]


class DependencyClosure:
    """Hash the actual resolved bytes of every dependency and detect drift.

    A snapshot is a deterministic tuple of ``(canonical_abs_path, sha256, size)``
    entries.  Symlinks and Windows junctions are resolved with
    ``os.path.realpath`` so the *real* file is hashed and recorded; parent
    traversal in supplied paths and duplicate resolutions are rejected.
    """

    @staticmethod
    def _canonical(raw: str | os.PathLike[str]) -> str:
        """Resolve ``raw`` to a canonical absolute path, rejecting traversal."""
        s = os.fspath(raw)
        if not isinstance(s, str):
            s = str(s)
        if not s or "\x00" in s:
            raise IntegrityError(f"invalid dependency path: {raw!r}")
        # Reject explicit parent-traversal components in the supplied path.
        # Real resolution would collapse them, but accepting them would allow
        # ambiguous inputs whose meaning depends on the current working dir.
        if any(part == ".." for part in _re.split(r"[\\/]", s)):
            raise IntegrityError(f"dependency path contains parent traversal: {raw!r}")
        absolute = os.path.abspath(s)
        real = os.path.realpath(absolute)
        if not os.path.isabs(real):
            raise IntegrityError(f"dependency path did not resolve to absolute: {raw!r}")
        return real

    def snapshot(self, paths: Iterable[str | os.PathLike[str]]) -> tuple:
        """Hash the actual bytes at every resolved dependency path.

        Returns a sorted tuple of ``(canonical_abs_path, sha256, size)``.  The
        caller is responsible for passing tool binary paths *and* collateral;
        this method never trusts a declared digest, it always hashes bytes.
        """
        entries: list[_SnapshotEntry] = []
        seen: set[str] = set()
        for raw in paths:
            real = self._canonical(raw)
            if real in seen:
                raise IntegrityError(f"duplicate dependency resolution: {real!r}")
            seen.add(real)
            if not os.path.isfile(real):
                raise IntegrityError(f"dependency is not a regular file: {real!r}")
            try:
                with open(real, "rb") as fh:
                    data = fh.read()
            except OSError as exc:
                raise IntegrityError(f"cannot read dependency {real!r}") from exc
            digest = hashlib.sha256(data).hexdigest()
            # Size comes from the same read as the hash (no stat/read TOCTOU).
            entries.append((real, digest, len(data)))
        entries.sort()
        return tuple(entries)

    def verify(self, snapshot: tuple) -> bool:
        """Re-hash every entry and fail closed on ANY change.

        Re-resolves and re-hashes exactly the canonical paths recorded in
        ``snapshot``.  A content change, a size change, a missing/unreadable
        file, a canonical path that has itself become a symlink (resolution
        change), or a malformed snapshot all return False.  Only the actual
        files on disk are compared -- never a declared root or trusted digest.
        """
        if not isinstance(snapshot, tuple):
            return False
        try:
            normalised: list[_SnapshotEntry] = []
            for entry in snapshot:
                if not isinstance(entry, (tuple, list)) or len(entry) != 3:
                    return False
                path, digest, size = entry
                normalised.append((str(path), str(digest), int(size)))
            recorded = tuple(normalised)
            # Re-resolve + re-hash the very paths that were recorded.  Because
            # snapshot() emits resolved realpaths, any path that has since
            # become a symlink resolves elsewhere and the tuples differ.
            recomputed = self.snapshot([entry[0] for entry in recorded])
        except Exception:
            return False
        return recomputed == recorded

    # -- MAC-bound snapshots -------------------------------------------------

    def bind(self, snapshot: tuple, trust) -> dict:
        """Return an authenticable dict sealing ``snapshot`` with a trust MAC.

        The MAC is produced by ``trust.sign(DOMAIN_DEPENDENCY, payload)``; the
        trust root supplies the context/session binding.  The returned dict is
        JSON-serialisable and safe to persist in an untrusted ledger.
        """
        entries = [[str(p), str(d), int(s)] for (p, d, s) in snapshot]
        payload = {"entries": entries}
        mac = trust.sign(DOMAIN_DEPENDENCY, payload)
        return {"domain": DOMAIN_DEPENDENCY, "entries": entries, "mac": mac}

    def verify_bound(self, bound: Mapping, trust) -> bool:
        """Verify a binding's MAC and that the sealed closure is still intact.

        Returns True only if (a) the domain matches, (b) the MAC validates over
        the exact entries via ``trust.verify(DOMAIN_DEPENDENCY, ...)``, and
        (c) the authenticated snapshot still matches the files on disk.  Any
        tampering with entries, domain or MAC, or any dependency drift, fails
        closed.
        """
        try:
            if not isinstance(bound, Mapping):
                return False
            if bound.get("domain") != DOMAIN_DEPENDENCY:
                return False
            entries = bound["entries"]
            mac = bound["mac"]
            if not isinstance(mac, str):
                return False
            payload = {
                "entries": [
                    [str(p), str(d), int(s)] for (p, d, s) in entries
                ]
            }
            if not trust.verify(DOMAIN_DEPENDENCY, payload, mac):
                return False
            sealed = tuple((str(p), str(d), int(s)) for (p, d, s) in entries)
            return self.verify(sealed)
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Self-test (python assistant/integrity.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import shutil

    class _TestTrust:
        """Minimal stand-in for Agent 1's TrustRoot (same canonicalisation)."""

        def __init__(self, key: bytes = b"integrity-selftest-key"):
            self._key = key

        def canonical(self, payload: Mapping) -> bytes:
            return json.dumps(
                payload, sort_keys=True, separators=(",", ":"), default=str
            ).encode("utf-8")

        def sign(self, domain: str, payload: Mapping) -> str:
            msg = domain.encode("ascii") + b"\x00" + self.canonical(payload)
            return hmac.new(self._key, msg, hashlib.sha256).hexdigest()

        def verify(self, domain: str, payload: Mapping, mac: str) -> bool:
            try:
                return hmac.compare_digest(self.sign(domain, payload), mac)
            except TypeError:
                return False

    def _check(cond: bool, label: str) -> None:
        if not cond:
            raise AssertionError(f"SELFTEST FAILED: {label}")
        print(f"ok - {label}")

    workdir = tempfile.mkdtemp(prefix="integrity-selftest-")
    try:
        src = os.path.join(workdir, "source.bin")
        original = b"verified-bytes-\x00\x01\xfe\xff" * 8
        with open(src, "wb") as fh:
            fh.write(original)

        # 1. from_path -> read_bytes returns the exact immutable bytes.
        vc = VerifiedContent.from_path(src)
        _check(verify_content(original, vc.digest), "from_path digest matches bytes")
        first = vc.read_bytes()
        _check(first is original or first == original, "read_bytes value == source")
        _check(vc.read_bytes() is first, "read_bytes returns the SAME object each call")

        # 2. Mutating the source file does NOT change read_bytes.
        with open(src, "wb") as fh:
            fh.write(b"MUTATED" * 16)
        _check(vc.read_bytes() is first and vc.read_bytes() == original,
               "source-file mutation does not change read_bytes")
        # A frozen dataclass must reject ALL attribute assignment on an exact
        # instance -- including attempts to attach a mutable 'snapshot' dict
        # (the Round 5 bypass vector) -- with FrozenInstanceError or equivalent.
        mutation_error = None
        try:
            vc.snapshot = {"x": "y"}  # type: ignore[attr-defined]
        except Exception as exc:  # FrozenInstanceError subclasses AttributeError
            mutation_error = exc
        _check(
            mutation_error is not None,
            "frozen object rejects new 'snapshot' attribute "
            f"({type(mutation_error).__name__ if mutation_error else 'NO ERROR'})",
        )

        # 3. materialize roundtrip (atomic write + re-verify).
        dst = os.path.join(workdir, "materialized.bin")
        vc.materialize(dst)
        with open(dst, "rb") as fh:
            _check(fh.read() == original, "materialize roundtrip bytes match")
        _check(verify_content(open(dst, "rb").read(), vc.digest),
               "materialized file re-verifies against digest")

        # 4. snapshot -> modify file -> verify() == False.
        dep_a = os.path.join(workdir, "dep_a.bin")
        dep_b = os.path.join(workdir, "dep_b.bin")
        with open(dep_a, "wb") as fh:
            fh.write(b"tool-binary")
        with open(dep_b, "wb") as fh:
            fh.write(b"collateral-data")
        closure = DependencyClosure()
        snap = closure.snapshot([dep_b, dep_a])
        _check(len(snap) == 2 and snap == tuple(sorted(snap)),
               "snapshot is sorted and complete")
        _check(closure.verify(snap), "verify() True for unchanged closure")
        with open(dep_a, "ab") as fh:
            fh.write(b"-tampered")
        _check(closure.verify(snap) is False, "verify() False after file modification")
        os.unlink(dep_b)
        _check(closure.verify(snap) is False, "verify() False after missing file")

        # 5. bind / verify_bound tamper rejection.
        snap2 = closure.snapshot([dep_a])  # current (tampered) state is a valid snapshot
        trust = _TestTrust()
        bound = closure.bind(snap2, trust)
        _check(closure.verify_bound(bound, trust), "verify_bound True for authentic binding")
        tampered = dict(bound)
        tampered["entries"] = [[p, "0" * 64, s] for (p, _d, s) in bound["entries"]]
        _check(closure.verify_bound(tampered, trust) is False,
               "verify_bound False for tampered entries")
        tampered_mac = dict(bound)
        tampered_mac["mac"] = "0" * 64
        _check(closure.verify_bound(tampered_mac, trust) is False,
               "verify_bound False for tampered MAC")
        wrong_trust = _TestTrust(key=b"a-different-key")
        _check(closure.verify_bound(bound, wrong_trust) is False,
               "verify_bound False under a different key")

        print("ALL SELFTESTS PASSED")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
