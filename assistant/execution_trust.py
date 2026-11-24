"""Round 6.2 -- structural execution trust boundary (ADR §6 + AMENDMENT 1).

This module is the ONLY producer of :class:`ExecutionProof`.  Trust is never
derived from what a command *looks like* (name / path / argv token / caller
label).  It is derived from a frozen, bootstrap-established content manifest
and a signed tool-spec allowlist.

Trust model (AMENDMENT 1)
-------------------------
A1 -- A **frozen approved-image manifest** of exact ``(realpath, sha256)`` pairs
is built once per session: the seed is the content hash of the ACTUAL current
process image (``QueryFullProcessImageNameW(GetCurrentProcess())``, no hardcoded
path); the approved bootstrap launcher and its in-job descendants are admitted
only if they are under ``sys.prefix``/``sys.base_prefix`` and readable; then the
manifest is frozen.  It is COMPARED against at run time and NEVER extended from
observed processes.  A ``cmd.exe`` copied into the writable venv has a hash that
is not in the manifest, so it is UNTRUSTED (Agent 1 P0-A).

A2 -- A **signed, MAC-bound tool-spec allowlist** lists exact
``(tool_id, argv_tuple)`` pairs the project authorises.  ``run(argv)`` accepts
an argv only on an exact match; arbitrary ``-c`` payloads (P0-C class) are
rejected without launch.  TRUSTED is necessary but NOT sufficient -- gate
policy stays authoritative (Agent 1 P0-B).

A3 -- Every proof carries a fresh nonce and mutual digest binding
(``execution_digest`` for the evidence envelope, ``candidate_digest`` for the
candidate/evidence).  MAC + context + nonce reject stale/replayed proofs.  An
attacker controlling the environment or host is OUT OF SCOPE and NOT claimed
resistant; the key is external/env or per-process ephemeral.  Ephemeral keys
are usable for live in-process proofs only -- persisted derivation must require
the external key (coordinator responsibility).

A4 -- ``consumed_digest`` / ``dependency_digest`` are exposed on the proof for
mandatory E4/E5 derivation checks.

A5 -- Sampling cannot prevent spawn-execute-exit between samples, POSIX
``setsid``, or process hollowing.  These are declared UNSUPPORTED; the proof
records the sampling window and makes NO containment claim beyond observed job
membership at sample instants.

All observation failures fail closed to UNKNOWN.  No monitor failure ever
raises into the caller.  OS observation primitives are reused from
:mod:`assistant.r61_identity` (``IMAGE_FN`` / ``DESCENDANTS_FN`` hooks).
"""
from __future__ import annotations

import ctypes
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

__all__ = [
    "DOMAIN_EXECUTION",
    "DOMAIN_TOOL_SPEC",
    "ExecutionState",
    "ExecutionProof",
    "ExecutionProofError",
    "ExecutionBoundary",
]

#: MAC domains.  Separate from evidence/authorization to stop cross-domain replay.
DOMAIN_EXECUTION = "execution/v1"
DOMAIN_TOOL_SPEC = "tool_spec/v1"

_IS_WINDOWS = os.name == "nt"
_IS_POSIX = os.name == "posix"
_CREATE_SUSPENDED = 0x00000004
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class ExecutionState(Enum):
    """Outcome of an execution-boundary observation (ADR §2)."""

    TRUSTED = "TRUSTED"
    UNTRUSTED = "UNTRUSTED"
    UNKNOWN = "UNKNOWN"


class ExecutionProofError(ValueError):
    """Raised by :meth:`ExecutionProof.from_dict` on MAC/format failure."""


# ---------------------------------------------------------------------------
# Canonical proof serialization (MAC-bound representation)
# ---------------------------------------------------------------------------


def _canonical_state(value: Any) -> str:
    if isinstance(value, ExecutionState):
        return value.value
    text = str(value or "")
    try:
        return ExecutionState(text).value
    except ValueError as exc:
        raise ExecutionProofError("invalid execution state: %r" % (text,)) from exc


def _canonical_images(value: Any) -> list:
    out: list = []
    for item in (value or ()):
        if isinstance(item, (list, tuple)) and len(item) == 2:
            out.append([str(item[0]), str(item[1])])
        else:
            raise ExecutionProofError("malformed approved_images entry: %r" % (item,))
    return out


def _canonical_context(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(k): v for k, v in dict(value).items()}
    out: dict = {}
    for name in ("session_id", "repo_id", "revision", "created_utc"):
        if hasattr(value, name):
            out[name] = getattr(value, name)
    return out


def _canonical_proof_payload(values: Mapping) -> dict:
    """Exact MAC input for a proof.  ``mac`` is excluded."""
    exit_code = values.get("exit_code")
    if exit_code is not None:
        exit_code = int(exit_code)
    return {
        "nonce": str(values.get("nonce", "")),
        "tool_id": str(values.get("tool_id", "")),
        "image_realpath": str(values.get("image_realpath", "")),
        "image_sha256": str(values.get("image_sha256", "")),
        "approved_images": _canonical_images(values.get("approved_images", ())),
        "approved_images_match": bool(values.get("approved_images_match", False)),
        "descendant_count": int(values.get("descendant_count") or 0),
        "job_member_count": int(values.get("job_member_count") or 0),
        "root_job_member": bool(values.get("root_job_member", False)),
        "argv_sha256": str(values.get("argv_sha256", "")),
        "boundary_id": str(values.get("boundary_id", "")),
        "context": _canonical_context(values.get("context")),
        "started_utc": str(values.get("started_utc", "")),
        "ended_utc": str(values.get("ended_utc", "")),
        "exit_code": exit_code,
        "state": _canonical_state(values.get("state")),
        "reason": str(values.get("reason", "")),
        "execution_digest": str(values.get("execution_digest", "")),
        "candidate_digest": str(values.get("candidate_digest", "")),
        "consumed_digest": str(values.get("consumed_digest", "")),
        "dependency_digest": str(values.get("dependency_digest", "")),
        "sampling_window_s": float(values.get("sampling_window_s") or 0.0),
        "sampling_samples": int(values.get("sampling_samples") or 0),
        "containment_claim": str(values.get("containment_claim") or "none"),
        "key_ephemeral": bool(values.get("key_ephemeral", False)),
        "sampling_limits_unsupported": bool(
            values.get("sampling_limits_unsupported", True)
        ),
    }


@dataclass(frozen=True)
class ExecutionProof:
    """Immutable, MAC-bound record of one managed execution (ADR §6 + A1-A5)."""

    nonce: str
    execution_digest: str
    candidate_digest: str
    approved_images_match: bool
    tool_id: str
    image_realpath: str
    image_sha256: str
    approved_images: tuple  # ((realpath, sha256), ...) frozen-manifest matches
    descendant_count: int
    job_member_count: int
    argv_sha256: str
    boundary_id: str
    context: Mapping
    started_utc: str
    ended_utc: str
    exit_code: int | None
    state: ExecutionState
    reason: str
    mac: str
    # A3/A4/A5 additions (all MAC-bound)
    root_job_member: bool = False
    consumed_digest: str = ""
    dependency_digest: str = ""
    sampling_window_s: float = 0.0
    sampling_samples: int = 0
    containment_claim: str = "none"
    key_ephemeral: bool = False
    sampling_limits_unsupported: bool = True

    # -- serialization -------------------------------------------------------

    def to_dict(self) -> dict:
        d = _canonical_proof_payload(vars(self))
        d["mac"] = str(self.mac)
        return d

    @classmethod
    def from_dict(cls, d: Mapping, trust) -> "ExecutionProof":
        """Verify the MAC with the external trust root and rebuild the proof.

        Caller-supplied ``state``/``mac`` never survive: both are part of the
        MAC input, so tampering raises :class:`ExecutionProofError`.  The
        caller MUST use the returned object; the untrusted mapping is never
        authoritative.
        """
        if not isinstance(d, Mapping):
            raise ExecutionProofError("proof must be a mapping")
        payload = _canonical_proof_payload(d)
        mac = str(d.get("mac", "") or "")
        if trust is None or not trust.verify(DOMAIN_EXECUTION, payload, mac):
            raise ExecutionProofError("execution proof MAC verification failed")
        images = tuple((str(a), str(b)) for a, b in payload["approved_images"])
        return cls(
            nonce=payload["nonce"],
            execution_digest=payload["execution_digest"],
            candidate_digest=payload["candidate_digest"],
            approved_images_match=payload["approved_images_match"],
            tool_id=payload["tool_id"],
            image_realpath=payload["image_realpath"],
            image_sha256=payload["image_sha256"],
            approved_images=images,
            descendant_count=payload["descendant_count"],
            job_member_count=payload["job_member_count"],
            argv_sha256=payload["argv_sha256"],
            boundary_id=payload["boundary_id"],
            context=payload["context"],
            started_utc=payload["started_utc"],
            ended_utc=payload["ended_utc"],
            exit_code=payload["exit_code"],
            state=ExecutionState(payload["state"]),
            reason=payload["reason"],
            mac=mac,
            root_job_member=payload["root_job_member"],
            consumed_digest=payload["consumed_digest"],
            dependency_digest=payload["dependency_digest"],
            sampling_window_s=payload["sampling_window_s"],
            sampling_samples=payload["sampling_samples"],
            containment_claim=payload["containment_claim"],
            key_ephemeral=payload["key_ephemeral"],
            sampling_limits_unsupported=payload["sampling_limits_unsupported"],
        )

    # -- signing / verification ---------------------------------------------

    def sign(self, trust) -> "ExecutionProof":
        """Return a copy carrying a fresh MAC from *trust*."""
        mac = trust.sign(DOMAIN_EXECUTION, _canonical_proof_payload(vars(self)))
        return replace(self, mac=mac)

    def verify_mac(self, trust) -> bool:
        try:
            ExecutionProof.from_dict(self.to_dict(), trust)
            return True
        except Exception:  # noqa: BLE001
            return False

    def bind_digests(self, *, candidate_digest="", consumed_digest="",
                     dependency_digest="", trust) -> "ExecutionProof":
        """Return a re-signed proof carrying downstream digest bindings."""
        return replace(
            self,
            candidate_digest=str(candidate_digest or ""),
            consumed_digest=str(consumed_digest or ""),
            dependency_digest=str(dependency_digest or ""),
        ).sign(trust)


@dataclass
class _BoundaryRunResult:
    """Duck-typed ``RunResultLike`` returned by :meth:`ExecutionBoundary.run`."""

    command: list
    exit_code: int | None = None
    timed_out: bool = False
    duration_s: float = 0.0
    stdout_log: str = ""
    stderr_log: str = ""
    combined_log: str = ""
    cwd: str = ""
    error: str = ""
    termination: str = "none"
    containment: str = "none"
    containment_reason: str = ""
    wsl_uncontained: bool = False
    pid: int | None = None
    cancelled: bool = False
    proof: Any = None
    execution_proof: Any = None
    trust: Any = None
    _auth_safe_override: bool = field(default=True, repr=False, compare=False)

    @property
    def ran(self) -> bool:
        return self.error == "" and not self.timed_out and not self.cancelled

    @property
    def authorization_safe(self) -> bool:
        if self._auth_safe_override is False or self.proof is None:
            return False
        return _proof_authorizes(self.proof, self.trust or _default_trust())


def _proof_authorizes(proof: ExecutionProof, trust) -> bool:
    """True only for a MAC-valid proof whose state is exactly TRUSTED."""
    try:
        ExecutionProof.from_dict(proof.to_dict(), trust)
    except Exception:  # noqa: BLE001
        return False
    return proof.state is ExecutionState.TRUSTED


def proof_authorizes(proof: ExecutionProof, trust=None) -> bool:
    """Public derivation helper used by runner / coordinator."""
    if proof is None:
        return False
    if trust is None:
        trust = default_trust()
    return _proof_authorizes(proof, trust)


def default_trust():
    """Public alias for :func:`_default_trust` (used by runner integration)."""
    return _default_trust()


def _default_trust():
    """Process-wide trust root; prefers the coordinator's R6 root."""
    try:
        from . import r6_authority as _ra  # type: ignore

        return _ra.get_trust()
    except Exception:  # pragma: no cover - standalone / staged layout
        try:
            import r6_authority as _ra  # type: ignore

            return _ra.get_trust()
        except Exception:
            pass
    try:
        from .trust import TrustRoot  # type: ignore
    except ImportError:  # pragma: no cover - standalone self-test layout
        from trust import TrustRoot  # type: ignore

    return TrustRoot()


def _load_r61():
    try:
        from . import r61_identity as _r61

        return _r61
    except ImportError:  # pragma: no cover
        try:
            import r61_identity as _r61  # type: ignore

            return _r61
        except ImportError:
            return None


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_blob(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _sha256_argv(argv: Sequence[str]) -> str:
    return _sha256_blob(
        json.dumps(
            [str(x) for x in (argv or ())],
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )


# ---------------------------------------------------------------------------
# Known benign verification tool specs (AMENDMENT 1 / A2)
# ---------------------------------------------------------------------------
# Exact argv tuples the project authorises as verification tools.  These are
# the calibration commands used by eco.py / the R6.x regression suite.  The
# P0-C renamed-shell payload, chr()-built WSL payloads, and arbitrary `-c`
# strings are deliberately NOT in this list.  A tool_id maps to one exact argv.
_BENIGN_TOOL_SPECS: tuple = (
    ("python_pass", ("{python}", "-c", "pass")),
    ("python_print_ok", ("{python}", "-c", "print('ok')")),
    ("python_managed_tool_ok", ("{python}", "-c", "print('managed-tool ok')")),
    ("python_r61_benign", ("{python}", "-c", "print('r61 benign')")),
    ("python_r61_benign_command", ("{python}", "-c", "print('benign command')")),
    ("python_r61_benign_short", ("{python}", "-c", "print('benign')")),
    ("python_r61_wrapped", ("{python}", "-c", "print('wrapped benign')")),
    ("python_legacy_ok", ("{python}", "-c", "print('legacy ok')")),
    ("python_legacy_ok_dash", ("{python}", "-c", "print('legacy-ok')")),
)


def _build_tool_spec_manifest(trust):
    """MAC-sign the exact (argv_tuple -> tool_id) allowlist."""
    entries = []
    for tool_id, template in _BENIGN_TOOL_SPECS:
        argv = tuple(sys.executable if a == "{python}" else a for a in template)
        entries.append([tool_id, list(argv)])
    payload = {"tool_specs": entries}
    return payload, trust.sign(DOMAIN_TOOL_SPEC, payload)


# ---------------------------------------------------------------------------
# Windows containment primitives (Job Object + CREATE_SUSPENDED)
# ---------------------------------------------------------------------------

if _IS_WINDOWS:  # pragma: no cover - platform-dependent
    from ctypes import wintypes

    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_ssize_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    class _THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", wintypes.ULARGE_INTEGER),
            ("WriteOperationCount", wintypes.ULARGE_INTEGER),
            ("OtherOperationCount", wintypes.ULARGE_INTEGER),
            ("ReadTransferCount", wintypes.ULARGE_INTEGER),
            ("WriteTransferCount", wintypes.ULARGE_INTEGER),
            ("OtherTransferCount", wintypes.ULARGE_INTEGER),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _JOBOBJECT_BASIC_PROCESS_ID_LIST(ctypes.Structure):
        _fields_ = [
            ("NumberOfProcessIdsInList", wintypes.DWORD),
            ("ProcessIdList", ctypes.c_ulong * 1),
        ]

    class _WinJobGuard:
        """KILL_ON_JOB_CLOSE Job Object with per-PID membership checks."""

        def __init__(self) -> None:
            self.ok = False
            self.handle = None
            self.k32 = None
            self.note = ""
            try:
                k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
                k32.CreateJobObjectW.restype = wintypes.HANDLE
                k32.SetInformationJobObject.argtypes = [
                    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                ]
                k32.SetInformationJobObject.restype = wintypes.BOOL
                k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
                k32.AssignProcessToJobObject.restype = wintypes.BOOL
                k32.IsProcessInJob.argtypes = [
                    wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
                ]
                k32.IsProcessInJob.restype = wintypes.BOOL
                k32.QueryInformationJobObject.argtypes = [
                    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                ]
                k32.QueryInformationJobObject.restype = wintypes.BOOL
                k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                k32.OpenProcess.restype = wintypes.HANDLE
                k32.CloseHandle.argtypes = [wintypes.HANDLE]
                h = k32.CreateJobObjectW(None, None)
                if not h or h == _INVALID_HANDLE:
                    self.note = "CreateJobObjectW failed"
                    return
                info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
                info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if not k32.SetInformationJobObject(
                    h, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(info), ctypes.sizeof(info),
                ):
                    self.note = "SetInformationJobObject failed (%d)" % ctypes.get_last_error()
                    k32.CloseHandle(h)
                    return
                self.handle, self.k32, self.ok = h, k32, True
            except Exception as exc:  # noqa: BLE001
                self.note = "job object unavailable: %s" % exc
                self.ok = False

        def assign(self, proc: subprocess.Popen) -> bool:
            if not self.ok:
                return False
            hproc = getattr(proc, "_handle", None)
            if hproc is None:
                self.note = "no proc._handle for job assignment"
                return False
            ok = self.k32.AssignProcessToJobObject(self.handle, int(hproc))
            if not ok:
                self.note = "AssignProcessToJobObject failed (%d)" % ctypes.get_last_error()
            return bool(ok)

        def is_in_job(self, pid: int) -> bool:
            if not self.ok:
                return False
            hproc = self.k32.OpenProcess(0x1000, False, int(pid))
            if not hproc or hproc == _INVALID_HANDLE:
                return False
            try:
                answer = wintypes.BOOL(False)
                ok = self.k32.IsProcessInJob(hproc, self.handle, ctypes.byref(answer))
                return bool(ok and answer.value)
            finally:
                self.k32.CloseHandle(hproc)

        def members(self) -> set:
            if not self.ok:
                return set()
            need = wintypes.DWORD(0)
            self.k32.QueryInformationJobObject(
                self.handle, _JOB_OBJECT_BASIC_PROCESS_ID_LIST, None, 0,
                ctypes.byref(need),
            )
            size = max(ctypes.sizeof(_JOBOBJECT_BASIC_PROCESS_ID_LIST), int(need.value))
            buf = ctypes.create_string_buffer(size)
            ok = self.k32.QueryInformationJobObject(
                self.handle, _JOB_OBJECT_BASIC_PROCESS_ID_LIST, buf, size,
                ctypes.byref(need),
            )
            if not ok:
                return set()
            head = ctypes.cast(buf, ctypes.POINTER(_JOBOBJECT_BASIC_PROCESS_ID_LIST)).contents
            count = int(head.NumberOfProcessIdsInList)
            ids = ctypes.cast(
                ctypes.addressof(head.ProcessIdList), ctypes.POINTER(ctypes.c_ulong)
            )
            return {int(ids[i]) for i in range(count)}

        def close(self) -> None:
            if self.ok and self.handle is not None:
                try:
                    self.k32.CloseHandle(self.handle)
                except Exception:  # noqa: BLE001
                    pass
            self.handle, self.k32, self.ok = None, None, False

    def _current_process_image() -> str:
        """Seed of the trust chain: actual image of THIS process, no hardcode."""
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if not k32.QueryFullProcessImageNameW(
            k32.GetCurrentProcess(), 0, buf, ctypes.byref(size)
        ):
            raise RuntimeError(
                "QueryFullProcessImageNameW failed (err=%d)" % ctypes.get_last_error()
            )
        return os.path.realpath(buf.value)

    def _resume_suspended_process(proc: subprocess.Popen) -> bool:
        """Resume the primary thread of a CREATE_SUSPENDED child."""
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
            k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            k32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_THREADENTRY32)]
            k32.Thread32First.restype = wintypes.BOOL
            k32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_THREADENTRY32)]
            k32.Thread32Next.restype = wintypes.BOOL
            k32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.OpenThread.restype = wintypes.HANDLE
            k32.ResumeThread.argtypes = [wintypes.HANDLE]
            k32.ResumeThread.restype = wintypes.DWORD
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            snap = k32.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
            if not snap or snap == _INVALID_HANDLE:
                return False
            tid = None
            try:
                entry = _THREADENTRY32()
                entry.dwSize = ctypes.sizeof(entry)
                ok = k32.Thread32First(snap, ctypes.byref(entry))
                while ok:
                    if entry.th32OwnerProcessID == proc.pid:
                        tid = int(entry.th32ThreadID)
                        break
                    ok = k32.Thread32Next(snap, ctypes.byref(entry))
            finally:
                k32.CloseHandle(snap)
            if tid is None:
                return False
            thread = k32.OpenThread(0x0002, False, tid)  # THREAD_SUSPEND_RESUME
            if not thread or thread == _INVALID_HANDLE:
                return False
            try:
                return k32.ResumeThread(thread) != 0xFFFFFFFF
            finally:
                k32.CloseHandle(thread)
        except Exception:  # noqa: BLE001
            return False

else:  # pragma: no cover - POSIX branch
    class _PosixJobGuard:
        """POSIX equivalent: dedicated process group (start_new_session)."""

        def __init__(self, root_pid: int) -> None:
            self.ok = True
            self.root_pid = int(root_pid)
            self.note = ""

        def is_in_job(self, pid: int) -> bool:
            try:
                return os.getpgid(int(pid)) == self.root_pid
            except OSError:
                return False

        def members(self) -> set:
            return set()

        def close(self) -> None:
            self.ok = False

    def _current_process_image() -> str:
        return os.path.realpath(sys.executable)


# ---------------------------------------------------------------------------
# ExecutionBoundary
# ---------------------------------------------------------------------------


@dataclass
class _Launch:
    proc: subprocess.Popen
    guard: Any
    root_pid: int
    note: str = ""


#: Frozen manifest cache keyed by approved roots.  Built once per session.
_MANIFEST_LOCK = threading.Lock()
_MANIFEST_CACHE: dict = {}


class ExecutionBoundary:
    """Launch and prove managed execution against a frozen trust manifest."""

    def __init__(self, trust) -> None:
        if trust is None:
            raise ValueError("ExecutionBoundary requires an external trust root")
        self.trust = trust
        self._roots = (os.path.realpath(sys.prefix), os.path.realpath(sys.base_prefix))
        self._norm_roots = tuple(os.path.normcase(r) for r in self._roots)
        self._boundary_id = uuid.uuid4().hex[:16]
        manifest, tool_payload, tool_mac = self._get_bootstrap()
        self._manifest = manifest  # frozenset of (normcase_realpath, sha256)
        self._tool_payload = tool_payload
        self._tool_mac = tool_mac
        self._argv_to_tool = {
            tuple(entry[1]): str(entry[0]) for entry in tool_payload["tool_specs"]
        }
        # A3: absent/unusable key fails closed.  A trust root with no sign/verify
        # is unusable; ephemeral in-process keys are usable for live proofs but
        # flagged for downstream derivation (A3).
        self._key_usable = all(
            callable(getattr(trust, name, None)) for name in ("sign", "verify")
        )

    # -- approved roots ------------------------------------------------------

    @property
    def approved_roots(self) -> tuple:
        return self._roots

    @property
    def approved_manifest(self) -> frozenset:
        return self._manifest

    def _inside_roots(self, path: str) -> bool:
        try:
            rp = os.path.normcase(os.path.realpath(str(path)))
        except (OSError, ValueError):
            return False
        for root in self._norm_roots:
            try:
                if os.path.commonpath([rp, root]) == root:
                    return True
            except ValueError:
                continue
        return False

    def _manifest_key(self, realpath: str, sha256: str) -> tuple:
        return (os.path.normcase(os.path.realpath(realpath)), str(sha256))

    def image_in_manifest(self, realpath: str, sha256: str) -> bool:
        """A1: exact (realpath, sha256) match against the FROZEN manifest."""
        return self._manifest_key(realpath, sha256) in self._manifest

    def _resolve_observed_image(self, image: str) -> str:
        """Resolve an observed image string to a realpath.

        Toolhelp32's ``szExeFile`` fallback yields a BARE basename (e.g.
        ``python3.10.exe``) when ``QueryFullProcessImageNameW`` fails on a
        fast-exiting process.  Resolve such names against the frozen manifest
        (by basename) instead of the caller's CWD, which would point at a
        non-existent file and fail closed on a benign run.
        """
        image = str(image or "")
        if os.path.isabs(image):
            return os.path.realpath(image)
        base = os.path.normcase(os.path.basename(image))
        for realpath, _sha in getattr(self, "_manifest", ()):
            if os.path.normcase(os.path.basename(realpath)) == base:
                return realpath
        found = shutil.which(image)
        if found:
            return os.path.realpath(found)
        return os.path.realpath(image)

    def tool_id_for_argv(self, argv: Sequence[str]) -> str | None:
        """A2: exact argv_tuple match against the signed allowlist."""
        return self._argv_to_tool.get(tuple(str(a) for a in (argv or ())))

    # -- bootstrap (frozen manifest) ----------------------------------------

    def _get_bootstrap(self):
        key = self._roots
        with _MANIFEST_LOCK:
            cached = _MANIFEST_CACHE.get(key)
            if cached is not None:
                return cached
            manifest = self._bootstrap_manifest()
            payload, mac = _build_tool_spec_manifest(self.trust)
            value = (manifest, payload, mac)
            _MANIFEST_CACHE[key] = value
            return value

    def _bootstrap_manifest(self) -> frozenset:
        """A1: establish the frozen approved-image manifest once per session."""
        admitted: set = set()
        seed = ""
        try:
            seed = _current_process_image()
            admitted.add(self._manifest_key(seed, _sha256_file(seed)))
        except Exception as exc:  # noqa: BLE001
            seed = ""
        # The approved bootstrap launcher is the framework's own interpreter.
        launcher = os.path.realpath(sys.executable)
        try:
            admitted.add(self._manifest_key(launcher, _sha256_file(launcher)))
        except Exception:  # noqa: BLE001
            pass
        # Launch one approved bootstrap spec and admit in-job descendants only.
        guard = None
        proc = None
        try:
            launch = self.launch([launcher, "-c", "pass"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            guard, proc = launch.guard, launch.proc
            deadline = time.monotonic() + 5.0
            while proc.poll() is None and time.monotonic() < deadline:
                try:
                    for entry in self._descendants(proc.pid) or []:
                        if not isinstance(entry, Mapping):
                            continue
                        pid = int(entry.get("pid") or 0)
                        image = entry.get("image")
                        if not image or not self._inside_roots(str(image)):
                            continue
                        try:
                            if guard is not None and getattr(guard, "ok", False):
                                if not guard.is_in_job(pid):
                                    continue
                        except Exception:  # noqa: BLE001
                            continue
                        rp = self._resolve_observed_image(str(image))
                        try:
                            admitted.add(self._manifest_key(rp, _sha256_file(rp)))
                        except OSError:  # noqa: BLE001
                            continue
                except Exception:  # noqa: BLE001
                    break
                time.sleep(0.01)
        except Exception:  # noqa: BLE001
            pass
        finally:
            if guard is not None:
                try:
                    guard.close()
                except Exception:  # noqa: BLE001
                    pass
            if proc is not None:
                try:
                    proc.wait(timeout=2)
                except Exception:  # noqa: BLE001
                    pass
        if not admitted:
            # Manifest cannot be established -> fail closed for every proof.
            return frozenset()
        return frozenset(admitted)

    # -- OS observation helpers (reuse r61_identity) ------------------------

    @staticmethod
    def _r61():
        return _load_r61()

    def _image(self, pid: int):
        r61 = self._r61()
        if r61 is not None and hasattr(r61, "IMAGE_FN"):
            return r61.IMAGE_FN(int(pid))
        if _IS_POSIX:  # pragma: no cover
            try:
                return os.readlink("/proc/%d/exe" % int(pid)), None
            except OSError as exc:
                return None, str(exc)
        return None, "r61 image helper unavailable"

    def _descendants(self, pid: int):
        r61 = self._r61()
        if r61 is not None and hasattr(r61, "DESCENDANTS_FN"):
            return r61.DESCENDANTS_FN(int(pid))
        raise RuntimeError("r61 descendant helper unavailable")

    @staticmethod
    def _alive(pid: int) -> bool:
        pid = int(pid)
        if pid <= 0:
            return False
        if _IS_WINDOWS:  # pragma: no cover
            try:
                k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                k32.OpenProcess.restype = wintypes.HANDLE
                k32.GetExitCodeProcess.argtypes = [
                    wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)
                ]
                k32.CloseHandle.argtypes = [wintypes.HANDLE]
                h = k32.OpenProcess(0x1000, False, pid)
                if not h:
                    return False
                try:
                    code = wintypes.DWORD(0)
                    if k32.GetExitCodeProcess(h, ctypes.byref(code)):
                        return int(code.value) == 0x103
                    return True
                finally:
                    k32.CloseHandle(h)
            except Exception:  # noqa: BLE001
                return True
        if _IS_POSIX:  # pragma: no cover
            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                return False
            except OSError:
                return True
        return False

    # -- launch --------------------------------------------------------------

    def _resolve_image(self, argv: Sequence[str]) -> str:
        target = str(argv[0])
        if os.sep in target or (os.altsep and os.altsep in target):
            return os.path.realpath(target)
        found = shutil.which(target)
        return os.path.realpath(found) if found else os.path.realpath(target)

    def launch(self, argv, *, cwd=None, env=None, stdout=None, stderr=None) -> _Launch:
        """Create the child inside a containment scope before it can run."""
        notes: list[str] = []
        popen_kwargs: dict = {}
        guard = None
        if _IS_WINDOWS:  # pragma: no cover
            guard = _WinJobGuard()
            if not guard.ok:
                notes.append(guard.note)
            popen_kwargs["creationflags"] = _CREATE_SUSPENDED | _CREATE_NEW_PROCESS_GROUP
        elif _IS_POSIX:  # pragma: no cover
            popen_kwargs["start_new_session"] = True

        proc = subprocess.Popen(
            [str(a) for a in argv],
            cwd=str(cwd) if cwd else None,
            stdout=stdout,
            stderr=stderr,
            env=env,
            shell=False,
            **popen_kwargs,
        )
        if _IS_WINDOWS:  # pragma: no cover
            if guard is not None and guard.ok:
                if not guard.assign(proc):
                    notes.append(guard.note)
                if not _resume_suspended_process(proc):
                    notes.append("ResumeThread failed")
            else:
                _resume_suspended_process(proc)
                notes.append("job object unavailable; containment not established")
        else:  # pragma: no cover
            guard = _PosixJobGuard(proc.pid)
        return _Launch(proc=proc, guard=guard, root_pid=proc.pid, note="; ".join(notes))

    # -- proof construction --------------------------------------------------

    @staticmethod
    def _execution_digest(proof_values: Mapping) -> str:
        core = {
            "nonce": str(proof_values.get("nonce", "")),
            "tool_id": str(proof_values.get("tool_id", "")),
            "argv_sha256": str(proof_values.get("argv_sha256", "")),
            "image_realpath": str(proof_values.get("image_realpath", "")),
            "image_sha256": str(proof_values.get("image_sha256", "")),
            "approved_images": _canonical_images(proof_values.get("approved_images", ())),
            "approved_images_match": bool(proof_values.get("approved_images_match", False)),
            "exit_code": proof_values.get("exit_code"),
            "state": _canonical_state(proof_values.get("state")),
        }
        return _sha256_blob(
            json.dumps(core, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        )

    def _empty_state(self, state: ExecutionState, reason: str, *, tool_id: str,
                     argv=(), exit_code=None, candidate_digest="", consumed_digest="",
                     dependency_digest="") -> ExecutionProof:
        """Build and sign a fail-closed proof without observations."""
        values = dict(
            nonce=secrets.token_hex(16),
            tool_id=str(tool_id),
            image_realpath="",
            image_sha256="",
            approved_images=(),
            approved_images_match=False,
            descendant_count=0,
            job_member_count=0,
            root_job_member=False,
            argv_sha256=_sha256_argv(argv),
            boundary_id=self._boundary_id,
            context=_canonical_context(getattr(self.trust, "context", None)),
            started_utc=_utc_now(),
            ended_utc=_utc_now(),
            exit_code=exit_code,
            state=state,
            reason=str(reason),
            execution_digest="",
            candidate_digest=str(candidate_digest or ""),
            consumed_digest=str(consumed_digest or ""),
            dependency_digest=str(dependency_digest or ""),
            sampling_window_s=0.0,
            sampling_samples=0,
            containment_claim="none",
            key_ephemeral=bool(getattr(self.trust, "ephemeral", False)),
            sampling_limits_unsupported=True,
            mac="",
        )
        values["execution_digest"] = self._execution_digest(values)
        return ExecutionProof(**values).sign(self.trust)

    def denied_proof(self, argv, *, tool_id: str, reason: str,
                     state: ExecutionState = ExecutionState.UNKNOWN,
                     exit_code=None, **digests) -> ExecutionProof:
        return self._empty_state(
            state, reason, tool_id=tool_id, exit_code=exit_code, argv=argv, **digests
        )

    def _build_proof(self, *, argv, tool_id, root_pid, guard, exit_code,
                     started_utc, ended_utc, sampling_window_s=0.0,
                     sampling_samples=0, candidate_digest="", consumed_digest="",
                     dependency_digest="", root_image_override=None,
                     root_image_error=None, descendants_override=None) -> ExecutionProof:
        """Observe the running tree and build a signed, fail-closed proof.

        A1/A2: every image must match the FROZEN manifest and argv must match
        the signed allowlist.  Never raises; errors downgrade to UNKNOWN.
        """
        state = ExecutionState.TRUSTED
        reasons: list[str] = []
        approved: list = []
        root_real = ""
        root_sha = ""
        matches = True
        descendant_count = 0
        job_member_count = 0
        root_member = False

        def downgrade(new_state: ExecutionState, reason: str) -> None:
            nonlocal state
            if new_state is ExecutionState.UNKNOWN or state is not ExecutionState.UNTRUSTED:
                state = new_state
            if reason and reason not in reasons:
                reasons.append(reason)

        # A2: exact tool-spec allowlist match (before any image observation).
        if self.tool_id_for_argv(argv) is None:
            downgrade(
                ExecutionState.UNTRUSTED,
                "argv not in signed tool-spec allowlist",
            )
        # A3: unusable trust key -> UNKNOWN.
        if not self._key_usable:
            downgrade(ExecutionState.UNKNOWN, "no usable trust key")
        # A1: manifest must exist.
        if not self._manifest:
            downgrade(ExecutionState.UNKNOWN, "approved-image manifest unavailable")

        # 1. Direct child image: realpath + content hash + frozen manifest.
        #    The root image is captured at launch (while the process is alive)
        #    by callers; a post-exit image query always fails on Windows.
        try:
            if root_image_override is not None:
                image, err = root_image_override, root_image_error
            else:
                image, err = self._image(root_pid)
            if err or not image:
                raise RuntimeError("root image unavailable: %s" % (err or "none"))
            rp = self._resolve_observed_image(image)
            root_sha = _sha256_file(rp)
            root_real = rp
            if not self._inside_roots(rp):
                downgrade(ExecutionState.UNTRUSTED,
                          "direct image outside approved roots: %s" % rp)
            if not self.image_in_manifest(rp, root_sha):
                matches = False
                downgrade(
                    ExecutionState.UNTRUSTED,
                    "direct image not in frozen approved manifest: %s" % rp,
                )
            else:
                approved.append((rp, root_sha))
        except Exception as exc:  # noqa: BLE001
            downgrade(ExecutionState.UNKNOWN, "root image observation error: %s" % exc)

        # 2. Containment membership for the root.
        if guard is None or not getattr(guard, "ok", False):
            downgrade(ExecutionState.UNKNOWN, "containment scope not established")
        else:
            try:
                if self._alive(root_pid) and not guard.is_in_job(root_pid):
                    downgrade(ExecutionState.UNKNOWN, "root process left containment")
                else:
                    root_member = True
                    job_member_count += 1
            except Exception as exc:  # noqa: BLE001
                downgrade(ExecutionState.UNKNOWN, "root membership check error: %s" % exc)

        # 3. Descendants: frozen manifest + observed job membership.
        try:
            if descendants_override is not None:
                entries = list(descendants_override)
            else:
                entries = self._descendants(root_pid) or []
        except Exception as exc:  # noqa: BLE001
            entries = []
            downgrade(ExecutionState.UNKNOWN, "descendant observation error: %s" % exc)
        for entry in entries:
            if not isinstance(entry, Mapping):
                downgrade(ExecutionState.UNKNOWN, "malformed descendant observation")
                continue
            try:
                pid_i = int(entry.get("pid"))
            except (TypeError, ValueError):
                downgrade(ExecutionState.UNKNOWN, "descendant with invalid pid")
                continue
            descendant_count += 1
            image = entry.get("image")
            alive = self._alive(pid_i)
            if not image:
                if alive:
                    downgrade(ExecutionState.UNKNOWN,
                              "descendant image unreadable: pid=%s" % pid_i)
                continue  # exited between sample and image open: not a live descendant
            try:
                rp = self._resolve_observed_image(image)
                if not os.path.isfile(rp) and not alive:
                    # Exited between sample and image open -- no live descendant
                    # to prove; A5 sampling limitation, not a violation.
                    continue
                sha = _sha256_file(rp)
            except Exception as exc:  # noqa: BLE001
                if not alive:
                    continue
                downgrade(ExecutionState.UNKNOWN, "descendant hash error: %s" % exc)
                continue
            if not self._inside_roots(rp):
                downgrade(ExecutionState.UNKNOWN,
                          "descendant outside approved roots: %s" % rp)
                continue
            if not self.image_in_manifest(rp, sha):
                matches = False
                downgrade(ExecutionState.UNKNOWN,
                          "descendant not in frozen approved manifest: %s" % rp)
                continue
            if alive and guard is not None and getattr(guard, "ok", False):
                try:
                    if not guard.is_in_job(pid_i):
                        downgrade(ExecutionState.UNKNOWN,
                                  "descendant left containment: pid=%s" % pid_i)
                        continue
                    job_member_count += 1
                except Exception as exc:  # noqa: BLE001
                    downgrade(ExecutionState.UNKNOWN,
                              "descendant membership error: %s" % exc)
                    continue
            approved.append((rp, sha))

        values = dict(
            nonce=secrets.token_hex(16),
            tool_id=str(tool_id),
            image_realpath=root_real,
            image_sha256=root_sha,
            approved_images=tuple(approved),
            approved_images_match=matches and state is ExecutionState.TRUSTED,
            descendant_count=descendant_count,
            job_member_count=job_member_count,
            root_job_member=root_member,
            argv_sha256=_sha256_argv(argv),
            boundary_id=self._boundary_id,
            context=_canonical_context(getattr(self.trust, "context", None)),
            started_utc=started_utc,
            ended_utc=ended_utc,
            exit_code=exit_code,
            state=state,
            reason="; ".join(reasons),
            execution_digest="",
            candidate_digest=str(candidate_digest or ""),
            consumed_digest=str(consumed_digest or ""),
            dependency_digest=str(dependency_digest or ""),
            sampling_window_s=float(sampling_window_s),
            sampling_samples=int(sampling_samples),
            containment_claim="none",  # A5: no containment claim
            key_ephemeral=bool(getattr(self.trust, "ephemeral", False)),
            sampling_limits_unsupported=True,
            mac="",
        )
        values["execution_digest"] = self._execution_digest(values)
        return ExecutionProof(**values).sign(self.trust)

    def prove_for_run(self, argv, *, tool_id: str, root_pid: int, guard,
                      exit_code, started_utc: str, ended_utc: str,
                      sampling_window_s: float = 0.0, sampling_samples: int = 0,
                      candidate_digest="", consumed_digest="",
                      dependency_digest="", root_image=None, root_image_error=None,
                      descendants=None) -> ExecutionProof:
        """Build the proof for an already-contained launch (runner integration)."""
        try:
            return self._build_proof(
                argv=argv, tool_id=tool_id, root_pid=root_pid, guard=guard,
                exit_code=exit_code, started_utc=started_utc, ended_utc=ended_utc,
                sampling_window_s=sampling_window_s,
                sampling_samples=sampling_samples,
                candidate_digest=candidate_digest,
                consumed_digest=consumed_digest,
                dependency_digest=dependency_digest,
                root_image_override=root_image,
                root_image_error=root_image_error,
                descendants_override=descendants,
            )
        except Exception as exc:  # noqa: BLE001
            return self._empty_state(
                ExecutionState.UNKNOWN,
                "proof construction error: %s" % exc,
                tool_id=tool_id,
                exit_code=exit_code,
                argv=argv,
            )

    # -- public entry points -------------------------------------------------

    def run(self, argv: Sequence[str], *, timeout=None, cancelled=None,
            cwd=None, log_dir=None, tool_id=None, candidate_digest="",
            consumed_digest="", dependency_digest="") -> tuple:
        """Launch *argv* through the boundary and return ``(result, proof)``.

        ``argv`` must EXACTLY match the signed tool-spec allowlist (A2); an
        unknown argv is rejected with an UNTRUSTED proof and is never launched.
        """
        argv = [str(a) for a in (argv or ())]
        tool_id = tool_id or self.tool_id_for_argv(argv) or (
            os.path.basename(argv[0]) if argv else ""
        )
        digests = dict(
            candidate_digest=candidate_digest,
            consumed_digest=consumed_digest,
            dependency_digest=dependency_digest,
        )
        started_utc = _utc_now()
        started = time.monotonic()
        reason = ""
        note = ""

        out_path = err_path = comb_path = ""
        fo = fe = None
        if log_dir:
            try:
                os.makedirs(str(log_dir), exist_ok=True)
                out_path = os.path.join(str(log_dir), "%s.stdout.log" % tool_id)
                err_path = os.path.join(str(log_dir), "%s.stderr.log" % tool_id)
                comb_path = os.path.join(str(log_dir), "%s.log" % tool_id)
                fo = open(out_path, "wb")
                fe = open(err_path, "wb")
            except OSError as exc:  # noqa: BLE001
                reason = "log setup error: %s" % exc

        # A2: allowlist check BEFORE launch.
        if not argv or self.tool_id_for_argv(argv) is None:
            proof = self._empty_state(
                ExecutionState.UNTRUSTED,
                "argv not in signed tool-spec allowlist",
                tool_id=tool_id, argv=argv, **digests,
            )
            for fh in (fo, fe):
                if fh is not None:
                    fh.close()
            result = _BoundaryRunResult(
                command=argv, error=proof.reason, containment="unsupported",
                stdout_log=out_path, stderr_log=err_path, combined_log=comb_path,
                cwd=str(cwd or os.getcwd()), proof=proof, trust=self.trust,
                execution_proof=proof.to_dict(),
            )
            return result, proof

        launch = None
        samples = 0
        root_image = None
        root_image_error = None
        descendants_seen: dict = {}
        try:
            launch = self.launch(argv, cwd=cwd, stdout=fo, stderr=fe)
            # Capture the root image NOW, while the child is still suspended.
            try:
                root_image, root_image_error = self._image(launch.root_pid)
            except Exception as exc:  # noqa: BLE001
                root_image, root_image_error = None, str(exc)
            deadline = started + float(timeout) if timeout is not None else float("inf")
            while True:
                samples += 1
                # Sample descendants on every pass while the tree is live.
                try:
                    for entry in self._descendants(launch.root_pid) or []:
                        if isinstance(entry, Mapping) and entry.get("pid") is not None:
                            descendants_seen[int(entry["pid"])] = dict(entry)
                except Exception as exc:  # noqa: BLE001
                    reason = reason or "descendant observation error: %s" % exc
                if launch.proc.poll() is not None:
                    break
                if cancelled is not None and cancelled():
                    note = "cancelled"
                    break
                if time.monotonic() >= deadline:
                    note = "timeout"
                    break
                time.sleep(0.01)
        except Exception as exc:  # noqa: BLE001
            reason = "launch/monitor error: %s" % exc
        finally:
            for fh in (fo, fe):
                if fh is not None:
                    fh.close()

        sampling_window = time.monotonic() - started
        timed_out = note == "timeout"
        cancelled_flag = note == "cancelled"
        exit_code = None
        if launch is None:
            proof = self._empty_state(
                ExecutionState.UNKNOWN, reason or "launch failed",
                tool_id=tool_id, argv=argv, **digests,
            )
            return (
                _BoundaryRunResult(
                    command=argv, error=proof.reason, containment="unsupported",
                    timed_out=timed_out, cancelled=cancelled_flag,
                    stdout_log=out_path, stderr_log=err_path, combined_log=comb_path,
                    cwd=str(cwd or os.getcwd()), proof=proof, trust=self.trust,
                    execution_proof=proof.to_dict(),
                ),
                proof,
            )
        if launch.proc.poll() is not None:
            exit_code = launch.proc.returncode
        if timed_out or cancelled_flag:
            try:
                if launch.guard is not None:
                    launch.guard.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                launch.proc.kill()
                launch.proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass

        ended = _utc_now()
        if reason:
            proof = self._empty_state(
                ExecutionState.UNKNOWN, reason, tool_id=tool_id,
                exit_code=exit_code, argv=argv, **digests,
            )
        else:
            proof = self._build_proof(
                argv=argv, tool_id=tool_id, root_pid=launch.root_pid,
                guard=launch.guard, exit_code=exit_code,
                started_utc=started_utc, ended_utc=ended,
                sampling_window_s=sampling_window, sampling_samples=samples,
                root_image_override=root_image,
                root_image_error=root_image_error,
                descendants_override=list(descendants_seen.values()),
                **digests,
            )
            if (timed_out or cancelled_flag) and proof.state is ExecutionState.TRUSTED:
                proof = replace(
                    proof, state=ExecutionState.UNKNOWN,
                    reason=(proof.reason + "; terminated_before_completion").strip("; "),
                ).sign(self.trust)

        containment = "clean" if proof.state is ExecutionState.TRUSTED else "unsupported"
        result = _BoundaryRunResult(
            command=argv, exit_code=exit_code, timed_out=timed_out,
            duration_s=time.monotonic() - started,
            stdout_log=out_path, stderr_log=err_path, combined_log=comb_path,
            cwd=str(cwd or os.getcwd()), error=proof.reason,
            termination="job_close" if (timed_out or cancelled_flag) else "none",
            containment=containment, containment_reason=proof.reason,
            pid=launch.root_pid, cancelled=cancelled_flag,
            proof=proof, trust=self.trust, execution_proof=proof.to_dict(),
        )
        return result, proof

    def run_tool(self, tool_id: str, params=None, **kwargs) -> tuple:
        """A2 convenience API: resolve an exact manifest entry by tool_id."""
        for entry in self._tool_payload["tool_specs"]:
            if str(entry[0]) == str(tool_id):
                argv = tuple(entry[1]) + tuple(str(p) for p in (params or ()))
                return self.run(argv, tool_id=str(tool_id), **kwargs)
        raise KeyError("unregistered tool_id: %r" % (tool_id,))


# ---------------------------------------------------------------------------
# Self-test (python assistant/execution_trust.py) -- must pass on Python 3.10
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    class _SelfTestTrust:
        def __init__(self):
            self._key = b"r62-execution-self-test"
            self.context = {
                "session_id": "self-test", "repo_id": "self-test",
                "revision": "self-test", "created_utc": _utc_now(),
            }

        def sign(self, domain, payload):
            blob = json.dumps(
                {"domain": domain, "payload": dict(payload)},
                sort_keys=True, separators=(",", ":"), default=str,
            ).encode("utf-8")
            return hmac.new(self._key, blob, hashlib.sha256).hexdigest()

        def verify(self, domain, payload, mac):
            return hmac.compare_digest(self.sign(domain, payload), str(mac))

    trust = _SelfTestTrust()
    boundary = ExecutionBoundary(trust)
    print("approved roots:", boundary.approved_roots)
    print("frozen manifest size:", len(boundary.approved_manifest))
    result, proof = boundary.run([sys.executable, "-c", "pass"])
    print("state:", proof.state.value, "reason:", proof.reason)
    print("approved:", proof.approved_images)
    print("nonce:", proof.nonce[:8], "exec_digest:", proof.execution_digest[:12])
    print("exit:", result.exit_code, "mac_ok:", proof.verify_mac(trust))
    restored = ExecutionProof.from_dict(proof.to_dict(), trust)
    print("roundtrip:", restored.state is proof.state)
    try:
        tampered = proof.to_dict()
        tampered["state"] = "UNTRUSTED"
        ExecutionProof.from_dict(tampered, trust)
        print("TAMPER ACCEPTED (BUG)")
    except ExecutionProofError:
        print("tamper rejected: OK")
    unknown, uproof = boundary.run([sys.executable, "-c", "import os; os.system('calc')"])
    print("unknown argv state:", uproof.state.value, "-", uproof.reason)
