"""Command execution with mandatory log preservation (including on failure).

Nothing in this module interprets exit codes as proof of correctness: a zero
exit code only means the process returned 0.  Verdicts come from parsers in
the calling checks.

R3 (R2-05 / R2-08) adds :func:`run_managed`, a containment-aware executor for
verification tools.  It enforces a monotonic deadline and an operator
cancellation callback, launches the tool in its own process group / session,
terminates the WHOLE descendant tree, and *confirms* termination by enumerating
PIDs before returning.  ``run_command`` is preserved unchanged for callers
that do not opt into managed execution.

R4 (A5-B / A5-C) hardens the containment contract:

* A5-B -- A ``wsl.exe`` invocation is classified ``unsupported`` on EVERY path
  (clean exit / timeout / cancel / orphan) with a single machine-readable
  ``containment_reason``, an explicit ``wsl_uncontained`` flag, and an
  ``authorization_safe`` gate.  No host-side technique can *confirm* termination
  of Linux descendants in WSL2's separate PID namespace (see module-level note
  below), so this is CLASSIFICATION, never prevention.  ``eco.py`` must refuse
  to authorize a verification whose result has ``wsl_uncontained=True``.
* A5-C -- :class:`ThreadContainment` is a registry + bounded-join + re-hash
  contract for in-process workers.  It can never kill a CPython thread; it
  WAITS (bounded), RE-HASHES the workspace, and returns an explicit
  ``uncontained_workers`` list so the caller can fail closed.
* Liveness confirmation now requires an INDEPENDENT alive oracle in addition to
  the PID snapshot, and two consecutive clean snapshots, so a kill command's
  exit code is never trusted alone.

R5 (R4A-03) closes the WSL detection bypass: classification is by EXECUTION
IDENTITY (normalised executable names, parsed shell strings, env indirection,
paths, quoted/escaped tokens, script files) rather than a literal ``wsl.exe``
substring.  Any WSL or uncontainable-tree path is ``unsupported`` and can never
be ``authorization_safe``; hard-containment positives and ``run_command`` are
unchanged.  WSL2 Linux descendants still cannot be prevented from the host --
this remains classification, never prevention.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

# R6.1 P0-B: authoritative dynamic OS process-identity observation.  Imported
# lazily-safe (r61_identity never imports runner) so staged integration works.
try:  # pragma: no cover - import layout varies by harness
    from . import r61_identity as _r61
except ImportError:  # pragma: no cover
    try:
        import r61_identity as _r61  # type: ignore
    except ImportError:  # pragma: no cover
        _r61 = None  # type: ignore

# R6.2: structural execution trust boundary (ADR §6 + AMENDMENT 1).  Imported
# lazily-safe; execution_trust never imports runner at module level.
try:  # pragma: no cover - import layout varies by harness
    from . import execution_trust as _et
except ImportError:  # pragma: no cover
    try:
        import execution_trust as _et  # type: ignore
    except ImportError:  # pragma: no cover
        _et = None  # type: ignore

# Re-export the wrapper contract for coordinator integration.  The canonical
# definition lives in r61_identity (which never imports this module).
monitored_run_managed = getattr(_r61, "monitored_run_managed", None) if _r61 else None

_IS_WINDOWS = os.name == "nt"
_IS_POSIX = os.name == "posix"

#: Explicit containment classifications reported on :class:`RunResult`.
#: ``none`` is the legacy default used by ``run_command`` (no managed claim).
CONTAINMENT_CLEAN = "clean"
CONTAINMENT_TREE_CONFIRMED = "tree_terminated_and_confirmed"
CONTAINMENT_TREE_UNCONFIRMED = "tree_attempt_unconfirmed"
CONTAINMENT_WRAPPER_ORPHAN = "wrapper_orphan_detected"
CONTAINMENT_UNSUPPORTED = "unsupported"
CONTAINMENT_COOPERATIVE_ONLY = "cooperative_only"
CONTAINMENT_NONE = "none"

#: Machine-readable reason codes carried on :attr:`RunResult.containment_reason`.
#: ``wsl_linux_descendants_unconfirmable`` is the canonical A5-B token: it is
#: always used as (or as a prefix of) ``RunResult.error`` on a WSL path and is
#: written verbatim into the combined log, so no consumer can mistake a WSL run
#: for a contained one.  ``eco.py`` should gate authorization on
#: ``RunResult.authorization_safe`` / ``RunResult.wsl_uncontained``.
REASON_CLEAN = "clean_exit"
REASON_TREE_CONFIRMED = "tree_terminated_and_confirmed"
REASON_TREE_UNCONFIRMED = "termination_unconfirmed"
REASON_WRAPPER_ORPHAN = "wrapper_exit_orphan"
REASON_WSL_UNCONFIRMABLE = "wsl_linux_descendants_unconfirmable"
# R5: an explicitly detached subprocess tree that the host cannot independently
# confirm (e.g. ``start /b``, ``nohup``, ``Start-Process``).  It is unsupported
# for the same reason as WSL: host-side PID confirmation cannot reach it.
REASON_UNCONTAINED_TREE = "uncontainable_subprocess_tree"
REASON_COOPERATIVE_ONLY = "cooperative_only"
REASON_DEADLINE_AT_ENTRY = "deadline_expired_at_entry"
REASON_NO_FINITE_DEADLINE = "no_finite_deadline"
REASON_NOT_REQUESTED = "containment_not_requested"

#: Containment states for which ``ran`` MUST be false -- the framework cannot
#: claim that the executed work was observed to completion and fully contained.
_UNCONTAINED_STATES = frozenset({
    CONTAINMENT_WRAPPER_ORPHAN,
    CONTAINMENT_TREE_UNCONFIRMED,
    CONTAINMENT_UNSUPPORTED,
    CONTAINMENT_COOPERATIVE_ONLY,
})

#: The ONLY states from which downstream authorization may be derived.  Making
#: this set explicit means a newly introduced classification can never silently
#: become "safe" (R5 / R4A-03 hardening of the ``authorization_safe`` gate).
_HOST_CONTAINED_STATES = frozenset({
    CONTAINMENT_CLEAN,
    CONTAINMENT_TREE_CONFIRMED,
})


@dataclass
class RunResult:
    command: list[str]
    exit_code: int | None
    timed_out: bool
    duration_s: float
    stdout_log: str
    stderr_log: str
    combined_log: str
    cwd: str
    error: str = ""
    # TR-03: how termination was attempted/confirmed, and the measured
    # (monotonic) wall-clock duration. ``termination`` is one of
    # none | tree | direct | failed.
    termination: str = "none"
    termination_detail: str = ""
    pid: int | None = None
    # --- R3 additions (defaults preserve all legacy callers) ---
    # One of the CONTAINMENT_* constants above. ``none`` means the caller did
    # not request managed execution (legacy ``run_command`` path).
    containment: str = CONTAINMENT_NONE
    # True when an operator cancellation callback fired mid-flight.
    cancelled: bool = False
    # PIDs observed as descendants of the root (evidence for confirmation).
    descendant_pids: tuple[int, ...] = ()
    # --- R4 additions (defaults preserve all legacy callers) ---
    # Machine-readable reason for the ``containment`` value.  One of the
    # REASON_* constants above.  On any WSL path this is always
    # REASON_WSL_UNCONFIRMABLE.
    containment_reason: str = ""
    # True iff this run went through a wsl.exe path whose Linux-side descendants
    # could not be confirmed terminated from the host.  This is the explicit
    # caller-visible hook for A5-B: eco.py MUST refuse to authorize verification
    # when this is True (use ``authorization_safe`` for the fail-closed gate).
    wsl_uncontained: bool = False
    # --- R6.1 P0-B additions (defaults preserve all legacy callers) ---
    # Evidence from dynamic OS process-identity observation.  This is
    # DETECTION + FAIL-CLOSED, never containment.
    identity_evidence: dict = field(default_factory=dict)
    # Latch used by ``authorization_safe`` once a finding is applied.  The
    # default True keeps the computed gate intact for every existing caller;
    # ``apply_identity_finding`` sets it to False via the property setter.
    _auth_safe_override: bool = field(default=True, repr=False, compare=False)
    # --- R6.2 additions (defaults preserve all legacy callers) ---
    # MAC-bound structural execution proof.  ``authorization_safe`` is DERIVED
    # from this proof when present (ADR §6 / AMENDMENT 1).  ``execution_proof``
    # is the serializable form carried by the evidence envelope.
    proof: Any = None
    execution_proof: Any = None

    @property
    def is_host_contained(self) -> bool:
        """Whether the run's own process tree was fully contained from the host.

        False for every uncontained classification and, critically, for any WSL
        path -- even when the host-side ``wsl.exe`` wrapper itself was killed and
        confirmed dead.  A surviving Linux descendant can still mutate files.

        R5 (R4A-03): the gate re-checks the executed command as well as the
        recorded flag.  A detector miss or a hand-built ``RunResult`` therefore
        cannot make a WSL/uncontainable-tree execution look host-contained.

        R6.1 P0-B: a latched dynamic-identity finding (``_auth_safe_override``)
        also vetoes containment.  Detection + fail-closed, not containment.
        """
        if getattr(self, "_auth_safe_override", True) is False:
            return False
        return (
            self.containment in _HOST_CONTAINED_STATES
            and not self.wsl_uncontained
            and not _looks_like_wsl(_as_command_list(self.command))
            and not _looks_like_uncontainable_tree(_as_command_list(self.command))
        )

    @property
    def authorization_safe(self) -> bool:
        """Fail-closed gate for consumers that authorize downstream work.

        R6.2 (ADR §6 / AMENDMENT 1): when a structural :class:`ExecutionProof`
        is attached, authorization is DERIVED from it -- the proof must be
        MAC-verifiable under the external trust root and its state must be
        exactly ``TRUSTED``.  No caller-settable boolean can force the positive
        direction.  The legacy containment/identity signals are still required
        to agree (defence in depth).

        Compatibility: results built without a proof (legacy ``run_command``,
        hand-built test doubles) keep the pre-R6.2 computation so existing
        callers are unaffected; such results are never given a positive
        override by this class.
        """
        if getattr(self, "_auth_safe_override", True) is False:
            return False
        cmd = _as_command_list(self.command)
        legacy_gate = (
            self.ran
            and self.containment in _HOST_CONTAINED_STATES
            and not self.wsl_uncontained
            and not _looks_like_wsl(cmd)
            and not _looks_like_uncontainable_tree(cmd)
            and self.is_host_contained
        )
        if not legacy_gate:
            return False
        proof = getattr(self, "proof", None)
        if proof is None or _et is None:
            return legacy_gate  # compatibility for proof-less result objects
        return _et.proof_authorizes(proof)

    @authorization_safe.setter
    def authorization_safe(self, value: bool) -> None:
        """Only a False assignment has an effect (a permanent veto).

        A True assignment is deliberately inert: positive authorization can
        only be derived from a MAC-verified TRUSTED proof, never forced by a
        caller.
        """
        if not bool(value):
            try:
                self._auth_safe_override = False
            except Exception:  # noqa: BLE001
                pass

    @property
    def ran(self) -> bool:
        """Whether the tool completed cleanly *and* was contained.

        R3: an orphan, an unconfirmed termination, an unsupported platform and
        a cooperative-only run are never reported as ``ran`` even if the direct
        child returned 0 -- a surviving descendant can still mutate files after
        this result is consumed.
        """
        return (
            self.error == ""
            and not self.timed_out
            and not self.cancelled
            and self.containment not in _UNCONTAINED_STATES
        )


# ---------------------------------------------------------------------------
# Process-tree enumeration / confirmation (R3)
# ---------------------------------------------------------------------------

if _IS_WINDOWS:  # pragma: no cover - exercised only on Windows hosts
    import ctypes
    from ctypes import wintypes

    _TH32CS_SNAPPROCESS = 0x00000002
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
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
            # Variable-length tail; allocate extra capacity and read via cast.
            ("ProcessIdList", ctypes.c_ulong * 1),
        ]

    class _WinJob:
        """Best-effort Windows Job Object with KILL_ON_JOB_CLOSE.

        Job membership is inherited by every descendant (including
        DETACHED_PROCESS children) and is independent of console process
        groups, so closing the job handle kills orphans whose wrapper already
        exited.  If any Win32 call fails, ``ok`` is False and the caller
        degrades to tree enumeration + explicit PID kills.
        """

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
                k32.QueryInformationJobObject.argtypes = [
                    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                ]
                k32.QueryInformationJobObject.restype = wintypes.BOOL
                k32.CloseHandle.argtypes = [wintypes.HANDLE]
                k32.CloseHandle.restype = wintypes.BOOL
                h = k32.CreateJobObjectW(None, None)
                if not h or h == _INVALID_HANDLE_VALUE:
                    self.note = "CreateJobObjectW failed"
                    return
                info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
                info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if not k32.SetInformationJobObject(
                    h, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(info), ctypes.sizeof(info),
                ):
                    self.note = f"SetInformationJobObject failed ({ctypes.get_last_error()})"
                    k32.CloseHandle(h)
                    return
                self.handle = h
                self.k32 = k32
                self.ok = True
            except Exception as exc:  # noqa: BLE001
                self.note = f"job object unavailable: {type(exc).__name__}: {exc}"
                self.ok = False

        def assign(self, proc: subprocess.Popen) -> bool:
            if not self.ok:
                return False
            hproc = getattr(proc, "_handle", None)
            if hproc is None:
                self.note = "no proc._handle for job assignment"
                return False
            try:
                ok = self.k32.AssignProcessToJobObject(self.handle, int(hproc))
            except Exception as exc:  # noqa: BLE001
                self.note = f"AssignProcessToJobObject error: {type(exc).__name__}: {exc}"
                return False
            if not ok:
                self.note = f"AssignProcessToJobObject failed ({ctypes.get_last_error()})"
            return bool(ok)

        def is_in_job(self, pid: int) -> bool:
            """True iff *pid* is currently a member of this Job Object."""
            if not self.ok:
                return False
            try:
                self.k32.OpenProcess.argtypes = [
                    wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
                ]
                self.k32.OpenProcess.restype = wintypes.HANDLE
                self.k32.IsProcessInJob.argtypes = [
                    wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
                ]
                self.k32.IsProcessInJob.restype = wintypes.BOOL
                self.k32.CloseHandle.argtypes = [wintypes.HANDLE]
                hproc = self.k32.OpenProcess(0x1000, False, int(pid))
                if not hproc:
                    return False
                try:
                    answer = wintypes.BOOL(False)
                    ok = self.k32.IsProcessInJob(
                        hproc, self.handle, ctypes.byref(answer)
                    )
                    return bool(ok and answer.value)
                finally:
                    self.k32.CloseHandle(hproc)
            except Exception:  # noqa: BLE001 - fail closed
                return False

        def members(self) -> set[int]:
            if not self.ok:
                return set()
            need = wintypes.DWORD(0)
            self.k32.QueryInformationJobObject(
                self.handle, _JOB_OBJECT_BASIC_PROCESS_ID_LIST,
                None, 0, ctypes.byref(need),
            )
            size = max(ctypes.sizeof(_JOBOBJECT_BASIC_PROCESS_ID_LIST), int(need.value))
            buf = ctypes.create_string_buffer(size)
            ok = self.k32.QueryInformationJobObject(
                self.handle, _JOB_OBJECT_BASIC_PROCESS_ID_LIST,
                buf, size, ctypes.byref(need),
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
            if self.handle is not None and self.k32 is not None:
                try:
                    self.k32.CloseHandle(self.handle)
                except Exception:  # noqa: BLE001
                    pass
            self.handle = None
            self.ok = False

    def _win32_snapshot_pids() -> dict[int, int]:
        """Return ``{pid: parent_pid}`` using Toolhelp32 (no external process)."""
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        k32.Process32FirstW.restype = wintypes.BOOL
        k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        k32.Process32NextW.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snap or snap == _INVALID_HANDLE_VALUE:
            return {}
        out: dict[int, int] = {}
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            ok = k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                out[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                ok = k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)
        return out


class _PosixJobGuard:  # pragma: no cover - non-Windows
    """POSIX containment scope: dedicated process group (start_new_session)."""

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


def _posix_snapshot_pids() -> dict[int, int]:  # pragma: no cover - non-Windows
    out: dict[int, int] = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "r", encoding="utf-8") as fh:
                data = fh.read()
            rparen = data.rfind(")")
            fields = data[rparen + 2:].split()
            out[int(name)] = int(fields[1])  # state=fields[0], ppid=fields[1]
        except (OSError, ValueError, IndexError):
            continue
    return out


def _snapshot_pids() -> dict[int, int]:
    if _IS_WINDOWS:  # pragma: no cover - Windows branch
        try:
            return _win32_snapshot_pids()
        except Exception:  # noqa: BLE001 - enumeration must never break the run
            return {}
    if _IS_POSIX:  # pragma: no cover - POSIX branch
        return _posix_snapshot_pids()
    return {}


def _descendants(root_pid: int, snapshot: dict[int, int]) -> set[int]:
    """All transitive descendants of *root_pid* in a ``{pid: ppid}`` snapshot."""
    children: dict[int, list[int]] = {}
    for pid, ppid in snapshot.items():
        children.setdefault(ppid, []).append(pid)
    found: set[int] = set()
    stack = [root_pid]
    while stack:
        parent = stack.pop()
        for child in children.get(parent, ()):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def _kill_pid(pid: int, notes: list[str] | None = None) -> None:
    """Best-effort direct kill of a single PID (used for confirmed stragglers)."""
    msg = ""
    try:
        if _IS_WINDOWS:  # pragma: no cover - Windows branch
            cp = subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True, text=True, timeout=15,
            )
            msg = f"taskkill /F exit={cp.returncode}"
        elif _IS_POSIX:  # pragma: no cover - POSIX branch
            os.kill(pid, signal.SIGKILL)
            msg = "SIGKILL"
        else:
            msg = "unsupported platform"
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
    if notes is not None:
        notes.append(f"direct kill pid={pid}: {msg}")


def _pid_alive_independent(pid: int) -> bool:
    """Independent liveness oracle for a single PID.

    Deliberately does NOT use the process snapshot used by ``_confirm_gone``:
    a snapshot that fails/returns ``{}`` must not be able to "prove" that every
    target vanished.  On Windows this uses raw ``OpenProcess`` +
    ``GetExitCodeProcess`` (a process that has exited but is still a zombie
    reports ``STILL_ACTIVE`` and is treated conservatively as alive).  On POSIX
    it combines ``os.kill(pid, 0)`` with a ``/proc`` zombie-state check.
    """
    if pid <= 0:
        return False
    if _IS_WINDOWS:  # pragma: no cover - Windows branch
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            k32.GetExitCodeProcess.restype = wintypes.BOOL
            k32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                code = wintypes.DWORD(0)
                if k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return int(code.value) == 0x103  # STILL_ACTIVE
                return True  # cannot query -> assume alive (fail closed)
            finally:
                k32.CloseHandle(handle)
        except Exception:  # noqa: BLE001 - oracle failure is conservative
            return True
    if _IS_POSIX:  # pragma: no cover - POSIX branch
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True
        try:
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as fh:
                data = fh.read()
            state = data[data.rfind(")") + 2:].split()[0]
            return state not in ("Z", "X")
        except OSError:
            return True
    return False


# ---------------------------------------------------------------------------
# Execution-identity classification (R5 / R4A-03): conservative, fail closed
# ---------------------------------------------------------------------------
#
# The R4 detector matched the exact basename ``wsl``/``wsl.exe``/``wslg.exe``
# and the *substrings* ``wsl.exe``/``wslg.exe``.  A bare ``wsl`` inside a shell
# string -- ``cmd /c "wsl -d Ubuntu ..."`` -- was therefore never seen, and the
# run was mislabelled ``clean``/``authorization_safe=True`` even though a WSL2
# Linux descendant is not contained by any host-side tree kill.  We now classify
# *execution identity* rather than decorate strings: normalise each token and
# each parsed shell word, collapse ``wsl`` and ``wsl.exe`` to one identity,
# parse nested shell strings, follow env indirection, inspect referenced scripts
# (read-only), and fail closed on uncertainty.  A false positive only downgrades
# a result to ``unsupported`` (which can never authorize), so the classifier is
# deliberately over-inclusive.

#: Executable identities that cross into WSL's Linux PID namespace.  ``wsl`` is
#: included WITHOUT an extension on purpose: Windows resolves it via PATHEXT,
#: and ``cmd /c wsl ...`` / ``bash -lc "wsl ..."`` pass it as a shell-string
#: word.  ``wslg`` (GUI), ``wslconfig`` and the legacy installer are included as
#: they share the same unconfirmable namespace / lifecycle.
_WSL_EXECUTABLES = frozenset({"wsl", "wslg", "wslconfig", "wslinstaller"})

#: Acceptable path segments / UNC share markers for WSL executable paths.
_WSL_PATH_SEGMENTS = frozenset({
    "wsl", "wsl.exe", "wslg", "wslg.exe", "wslconfig", "wslconfig.exe",
})

#: Word-boundary matcher for a WSL name inside a larger string (shell command,
#: ``X=wsl`` env assignment, ``subprocess.Popen(['wsl', ...])`` literal, etc.).
#: The lookarounds deliberately exclude ``_`` so audit field names such as
#: ``wsl_uncontained`` do not self-trigger the classifier.
_WSL_WORD_RE = re.compile(
    r"(?<![0-9a-z_])wsl(?:g|config|installer)?(?:\.exe)?(?![0-9a-z_])",
    re.IGNORECASE,
)

#: Executables whose command-line *string* arguments must be parsed instead of
#: treated as opaque (nested shell / wrapper calls, R4A-03).
_SHELL_WRAPPERS = frozenset({
    "cmd", "powershell", "pwsh", "bash", "sh", "dash", "zsh", "ksh",
    "fish", "env", "start", "call", "nohup", "setsid",
})

#: Explicit detach / background constructs: their children can outlive the
#: host-visible tree, so containment cannot be independently verified.
_DETACH_TREE_RE = re.compile(
    r"(?<![0-9a-z_])(?:nohup|setsid|disown|start-process|start\s+/b)(?![0-9a-z_])",
    re.IGNORECASE,
)

#: An UNRESOLVED environment variable used as a COMMAND word (start of a shell
#: command segment), e.g. ``cmd /c "%WSLCMD% -l"`` or ``bash -lc "$X -l"``.
#: Its value cannot be determined statically, so it could be ``wsl``; fail
#: closed.  Variables in ARGUMENT position (``echo %X%``) are not matched.
_VARIABLE_COMMAND_RE = re.compile(
    r"^\s*(?:%[^%\s]+%|\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*)(?:\s|$)"
)

#: Script suffixes whose referenced file text is inspected (read-only, bounded)
#: for indirect WSL launches (``bash run.sh``, ``python launch.py``, ...).
_SCRIPT_SUFFIXES = (".sh", ".bat", ".cmd", ".ps1", ".py", ".pl", ".rb")
_MAX_SCRIPT_BYTES = 262_144


def _as_command_list(command) -> list[str]:
    """Best-effort normalization of a command (list/tuple/str) to a str list."""
    if isinstance(command, str):
        return [command]
    if command is None:
        return []
    try:
        return [str(part) for part in command]
    except TypeError:
        return []


def _clean_command_text(text: str) -> str:
    """Neutralise quote/escape tricks that split a WSL name across tokens.

    Handles POSIX backslash escapes of metacharacters, ``cmd.exe`` caret
    escapes, PowerShell backtick escapes, and quote characters used to break a
    name (``w"s"l``).  Over-cleaning can only ADD detections (fail closed).
    """
    s = str(text).strip()
    s = re.sub(r"\\([\s\"'`|&;<>()$\[\]{}])", r"\1", s)
    s = re.sub(r"[\^`](.)", r"\1", s)
    s = s.replace('"', "").replace("'", "")
    return s


def _executable_identity(token: str) -> str:
    """Return a comparable execution identity: no path, no extension, lowercase.

    Windows PATHEXT resolution means ``wsl`` and ``wsl.exe`` are the SAME
    identity.  Surrounding quotes, path separators, ``./`` decorations, 8.3
    short names (``wsl~1``) and common executable/script extensions are removed.
    """
    s = _clean_command_text(token).replace("\\", "/")
    base = s.rsplit("/", 1)[-1].strip().lstrip(".")
    low = base.lower()
    if "~" in low:  # 8.3 short name, e.g. wsl~1.exe
        low = low.split("~", 1)[0]
    for ext in (".exe", ".com", ".bat", ".cmd", ".ps1", ".sh", ".py"):
        if low.endswith(ext):
            low = low[: -len(ext)]
            break
    return low


def _shell_words(text: str) -> list[str]:
    """Split a shell command string into candidate words (over-splitting is OK).

    Every shell metacharacter is a separator, so chained / pipelined /
    backgrounded commands still expose their command words, e.g.
    ``echo hi && wsl -d Ubuntu`` -> ``['echo', 'hi', 'wsl', '-d', 'Ubuntu']``.
    """
    return [
        word for word in re.split(r"[\s|&;<>()\[\]{}]+", _clean_command_text(text))
        if word
    ]


def _read_script_text(path: str) -> str | None:
    """Best-effort, BOUNDED, read-only read of a referenced script file.

    Regular files under :data:`_MAX_SCRIPT_BYTES` only; anything else is
    ignored.  This catches ``bash run.sh`` / ``python launch.py`` where the
    script itself invokes WSL, WITHOUT executing or trusting the script.
    """
    try:
        if not os.path.isfile(path) or os.path.getsize(path) > _MAX_SCRIPT_BYTES:
            return None
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except (OSError, ValueError):
        return None


def _wsl_risk_detail(command) -> tuple[bool, str, str]:
    """Classify *command* for WSL / indirection risk.

    Returns ``(risk, reason, detail)`` where ``reason`` is a short
    machine-readable label (``wsl_executable``, ``wsl_path``, ``wsl_shell_word``,
    ``wsl_env_indirection``, ``wsl_script``) and ``detail`` names the offending
    token for the combined log.
    """
    for token in _as_command_list(command):
        raw = str(token)
        cleaned = _clean_command_text(raw)

        # 1. Direct executable identity (argv[0] OR any argv token).
        if _executable_identity(raw) in _WSL_EXECUTABLES:
            return True, "wsl_executable", raw

        # 2. Executable paths and WSL UNC shares (\\wsl$\, \\wsl.localhost\).
        norm = cleaned.replace("\\", "/").lower()
        if "wsl$" in norm or "wsl.localhost" in norm:
            return True, "wsl_path", raw
        if any(seg in _WSL_PATH_SEGMENTS for seg in norm.split("/")):
            return True, "wsl_path", raw

        # 3. Parse shell-string words (cmd /c, powershell -Command, bash -lc,
        #    nested wrappers, chains, pipelines, child-process literals).
        words = _shell_words(raw)
        for word in words:
            if _executable_identity(word) in _WSL_EXECUTABLES:
                return True, "wsl_shell_word", word
        joined = " ".join(words)
        if _WSL_WORD_RE.search(joined):
            return True, "wsl_shell_word", raw

        # 4. Env indirection / assignments that the word split may hide.
        if _WSL_WORD_RE.search(cleaned):
            return True, "wsl_env_indirection", raw
        # 4b. An opaque variable in COMMAND position cannot be statically
        # resolved; it could expand to wsl => unsupported (fail closed).
        for segment in re.split(r"&&|\|\||[|;&\n]", cleaned):
            if _VARIABLE_COMMAND_RE.search(segment):
                return True, "wsl_env_indirection", raw

        # 5. Script files whose (bounded, read-only) text invokes WSL.
        for word in words:
            if word.lower().endswith(_SCRIPT_SUFFIXES):
                script = _read_script_text(word)
                if script and _WSL_WORD_RE.search(_clean_command_text(script)):
                    return True, "wsl_script", word
    return False, "", ""


def _looks_like_wsl(cmd_list: list[str]) -> bool:
    """Conservative WSL/indirection detection (R5 / R4A-03).

    True when *any* token is, names, embeds, chains to, pipes to, follows via
    environment indirection, or references a script that launches a WSL
    executable.  This replaces the R4 exact-basename / ``wsl.exe``-substring
    matcher, which missed bare ``wsl`` inside shell strings and let
    ``cmd /c "wsl -d Ubuntu ..."`` authorize as ``clean``.
    """
    return _wsl_risk_detail(cmd_list)[0]


def _looks_like_uncontainable_tree(cmd_list) -> bool:
    """True when a command explicitly detaches a subprocess tree (R5).

    ``start /b``, ``nohup``, ``setsid``, ``disown`` and ``Start-Process`` create
    work that can outlive the host-visible tree; no host-side PID oracle can
    independently confirm termination of such descendants.  These are
    classified ``unsupported`` (fail closed), never authorized.
    """
    for token in _as_command_list(cmd_list):
        if _DETACH_TREE_RE.search(_clean_command_text(token)):
            return True
    return False


def terminate_process_tree(proc: subprocess.Popen, *, detail_log: list[str] | None = None) -> tuple[str, str]:
    """Terminate *proc* and its descendants, returning (method, detail).

    A wrapper (cmd.exe, powershell.exe, wsl.exe, bash -lc) dying does NOT
    guarantee its children die.  On Windows ``taskkill /T /F`` walks the tree;
    on POSIX the child is started in its own session so ``killpg`` reaches all
    descendants.  A failure is reported explicitly, never silently assumed.
    """
    notes: list[str] = []
    method = "failed"

    def note(msg: str) -> None:
        notes.append(msg)
        if detail_log is not None:
            detail_log.append(msg)

    if _IS_WINDOWS:  # pragma: no cover - Windows branch
        try:
            cp = subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, text=True, timeout=15,
            )
            note(f"taskkill /T /F exit={cp.returncode}: {(cp.stdout or cp.stderr).strip()[:300]}")
            method = "tree" if cp.returncode == 0 else "failed"
        except Exception as exc:  # noqa: BLE001
            note(f"taskkill failed: {type(exc).__name__}: {exc}")
        if method == "failed":
            try:
                proc.kill()
                note("fallback direct TerminateProcess")
                method = "direct"
            except Exception as exc:  # noqa: BLE001
                note(f"direct kill failed: {type(exc).__name__}: {exc}")
    else:  # POSIX / unsupported fallback
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            note("killpg(SIGKILL) on child process group")
            method = "tree"
        except (ProcessLookupError, PermissionError, OSError) as exc:
            note(f"killpg failed: {type(exc).__name__}: {exc}")
            try:
                proc.kill()
                note("fallback direct SIGKILL")
                method = "direct"
            except Exception as exc2:  # noqa: BLE001
                note(f"direct kill failed: {type(exc2).__name__}: {exc2}")
    return method, "; ".join(notes)


# ---------------------------------------------------------------------------
# Managed, containment-aware execution (R2-05 / R2-08)
# ---------------------------------------------------------------------------

@dataclass
class _ManagedOutcome:
    reason: str | None  # None | "timeout" | "cancel" | "orphan"
    exit_code: int | None
    confirmed: bool
    survivors: tuple[int, ...]
    targets: tuple[int, ...]
    method: str
    detail: str


def _confirm_gone(
    targets: set[int],
    snap_fn: Callable[[], dict[int, int]],
    grace_s: float,
    notes: list[str],
    *,
    alive_fn: Callable[[int], bool] | None = None,
    require_stable: int = 2,
) -> list[int]:
    """Poll until every target PID is independently confirmed gone or grace elapses.

    R4 hardening of liveness confirmation:

    * A target is a survivor if EITHER the PID snapshot still lists it OR an
      INDEPENDENT alive oracle (``alive_fn``, default :func:`_pid_alive_independent`)
      reports it alive.  Both must agree "gone" before a target is cleared, so a
      failed/empty snapshot can no longer "prove" mass termination, and the
      ``taskkill``/``killpg`` exit code is never trusted by itself.
    * Confirmation requires ``require_stable`` consecutive clean polls (default
      two), closing the single-snapshot race where a process briefly drops out
      of an enumeration.
    * Stragglers receive a direct kill on each pass.  A PID that is still
      present in the final check is reported as a survivor (fail closed).

    ``alive_fn`` is an injection seam for tests; normal callers leave it None.
    """
    check_alive = alive_fn if alive_fn is not None else _pid_alive_independent
    deadline = time.monotonic() + grace_s
    survivors: list[int] = sorted(targets)
    stable_clean = 0
    last_snapshot: dict[int, int] = {}
    while True:
        last_snapshot = snap_fn()
        survivors = sorted(
            p for p in targets if (p in last_snapshot or check_alive(p))
        )
        if not survivors:
            stable_clean += 1
            if stable_clean >= max(1, require_stable):
                break
        else:
            stable_clean = 0
            for pid in survivors:
                _kill_pid(pid, notes)
        if time.monotonic() >= deadline:
            break
        time.sleep(0.02)
    if survivors:
        # Final independent re-check (both oracles must agree "gone").
        final_snap = snap_fn()
        survivors = sorted(
            p for p in targets if (p in final_snap or check_alive(p))
        )
    else:
        notes.append(
            f"independent confirmation ok: {len(targets)} PID(s) absent from "
            f"snapshot and alive-probe after {require_stable} stable polls"
        )
    return survivors


def run_managed(
    command,
    *,
    cwd: str | os.PathLike[str] | None = None,
    log_dir: str | os.PathLike[str],
    name: str,
    deadline: float | None = None,
    cancelled: Callable[[], bool] | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
    poll_interval: float = 0.02,
    confirm_grace_s: float = 5.0,
    terminator=None,
    pid_probe=None,
    pid_alive_probe=None,
) -> RunResult:
    """Run a verification tool with enforced deadline, cancellation and containment.

    Contract (for ``eco.py`` / ``VerificationContext.spawn`` integration):

    * ``deadline`` is a ``time.monotonic()`` instant (hard cap).  ``timeout`` is
      an optional per-tool cap in seconds.  The effective cap is
      ``min(deadline, now + timeout)``; all timing uses ``time.monotonic()``.
    * ``cancelled`` is a zero-argument callable; when it returns True mid-flight
      the whole process tree is terminated (not merely flagged).
    * The tool is launched in its own process group (POSIX session / Windows
      process group + best-effort Job Object with KILL_ON_JOB_CLOSE).
    * On timeout/cancel, descendants are enumerated (including ones observed
      while the wrapper was alive), the whole tree is killed, and termination
      is CONFIRMED by BOTH PID enumeration and an independent alive probe over
      two consecutive clean polls before returning.  If any captured PID
      survives either oracle, ``containment`` reports it and ``ran`` is False.
    * A wrapper that exits while descendants survive is classified
      ``wrapper_orphan_detected`` with ``ran=False``.
    * A WSL invocation (or an explicitly detached subprocess tree) is
      classified ``unsupported`` on EVERY path.  WSL paths carry
      ``containment_reason == REASON_WSL_UNCONFIRMABLE``,
      ``wsl_uncontained=True``; detached trees carry
      ``REASON_UNCONTAINED_TREE``.  Both are always ``authorization_safe=False``.
      Host-side tools cannot confirm Linux descendants in WSL2's PID namespace
      or the children of an explicit detach (A5-B / R4A-03); this is
      classification, not prevention.  Callers MUST gate on ``authorization_safe``.
    * Partial stdout/stderr is always preserved in the log files.
    * ``terminator`` / ``pid_probe`` / ``pid_alive_probe`` are dependency-
      injection seams for tests; normal callers leave them as None.

    The caller decides how to surface ``timed_out`` / ``cancelled`` (e.g. via
    ``VerificationContext.check()`` raising EcoTimeoutError / EcoCancellationError).
    """
    if isinstance(command, str):
        cmd_list = [command] if _IS_WINDOWS else ["/bin/bash", "-lc", command]
    else:
        cmd_list = list(command)

    # R5/R4A-03: classify execution identity BEFORE any launch/exit path (the
    # deadline-at-entry return below included).  WSL is detected by identity,
    # shell-string parsing, env indirection, paths and referenced scripts --
    # not by the presence of a literal ``wsl.exe`` substring.
    wsl_risk, wsl_risk_reason, wsl_risk_detail = _wsl_risk_detail(cmd_list)
    wsl = wsl_risk
    uncontainable_tree = _looks_like_uncontainable_tree(cmd_list)
    unsupported = wsl or uncontainable_tree
    unsupported_reason = REASON_WSL_UNCONFIRMABLE if wsl else (
        REASON_UNCONTAINED_TREE if uncontainable_tree else ""
    )
    unsupported_detail = (
        "WSL/Linux descendants live in a separate PID namespace; host-side "
        "enumeration cannot confirm them (classification only, not prevention)"
        if wsl else
        f"command explicitly detaches a subprocess tree ({wsl_risk_detail or 'detach'}); "
        "host-side PID confirmation cannot reach it (classification only)"
    )

    log_dir = str(log_dir)
    os.makedirs(log_dir, exist_ok=True)
    out_path = os.path.join(log_dir, f"{name}.stdout.log")
    err_path = os.path.join(log_dir, f"{name}.stderr.log")
    comb_path = os.path.join(log_dir, f"{name}.log")

    started = time.monotonic()
    now = started
    hard_deadline = now + timeout if timeout is not None else float("inf")
    if deadline is not None:
        hard_deadline = min(hard_deadline, deadline)

    def snap_fn() -> dict[int, int]:
        try:
            return dict(pid_probe()) if pid_probe is not None else _snapshot_pids()
        except Exception as exc:  # noqa: BLE001
            term_notes.append(f"pid_probe failed: {type(exc).__name__}: {exc}")
            return {}

    term_notes: list[str] = []

    # R6.2: structural execution boundary (ADR §6 / AMENDMENT 1).  A failed
    # boundary setup never raises -- it degrades to an UNKNOWN proof.
    boundary = None
    started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if _et is not None:
        try:
            boundary = _et.ExecutionBoundary(_et.default_trust())
        except Exception as exc:  # noqa: BLE001
            term_notes.append(f"execution boundary unavailable: {exc}")
            boundary = None

    # Expired before launch: no process is started, nothing to contain.  A WSL /
    # uncontainable-tree command is STILL classified unsupported and never
    # authorization-safe, even when nothing was launched (R5 / R4A-03).
    if not math.isfinite(hard_deadline) or hard_deadline <= now:
        duration = time.monotonic() - started
        at_entry = hard_deadline <= now
        base_error = REASON_DEADLINE_AT_ENTRY if at_entry else REASON_NO_FINITE_DEADLINE
        if unsupported:
            error_msg = f"{base_error}; {unsupported_reason}"
            early_containment = CONTAINMENT_UNSUPPORTED
            early_reason = unsupported_reason
            early_wsl_flag = wsl
        else:
            error_msg = base_error
            early_containment = CONTAINMENT_COOPERATIVE_ONLY
            early_reason = base_error
            early_wsl_flag = False
        fe = open(err_path, "wb")
        try:
            fe.write(error_msg.encode("utf-8", "replace"))
        finally:
            fe.close()
        header = (
            f"$ {' '.join(cmd_list)}\n"
            f"# cwd={cwd or os.getcwd()} exit_code=None "
            f"timed_out=True duration_s={duration:.3f}\n"
            f"# containment={early_containment} cancelled=False "
            f"wsl_uncontained={early_wsl_flag} error={error_msg} "
            f"containment_reason={early_reason}\n"
        ).encode("utf-8", "replace")
        with open(comb_path, "wb") as fc, open(out_path, "wb") as _fo:
            fc.write(header)
        early_proof = None
        if boundary is not None:
            try:
                early_proof = boundary.denied_proof(
                    cmd_list, tool_id=name, check_id=name, reason=base_error,
                    state=_et.ExecutionState.UNKNOWN,
                )
            except Exception:  # noqa: BLE001
                early_proof = None
        return RunResult(
            command=cmd_list, exit_code=None, timed_out=True, duration_s=duration,
            stdout_log=out_path, stderr_log=err_path, combined_log=comb_path,
            cwd=str(cwd or os.getcwd()), error=error_msg,
            containment=early_containment,
            containment_reason=early_reason, wsl_uncontained=early_wsl_flag,
            proof=early_proof,
            execution_proof=early_proof.to_dict() if early_proof is not None else None,
        )

    is_cancel = cancelled if callable(cancelled) else (lambda: False)
    supported_platform = _IS_WINDOWS or _IS_POSIX

    error = ""
    exit_code: int | None = None
    timed_out = False
    cancelled_flag = False
    termination = "none"
    termination_detail = ""
    pid: int | None = None
    containment = CONTAINMENT_COOPERATIVE_ONLY if not supported_platform else CONTAINMENT_CLEAN
    containment_reason = REASON_COOPERATIVE_ONLY if not supported_platform else REASON_CLEAN
    wsl_uncontained = False
    observed: set[int] = set()
    spawn_failed = False
    outcome: _ManagedOutcome | None = None
    job = None
    # R6.1 P0-B: dynamic OS process-identity monitor (authoritative boundary).
    # Created only once a child PID exists; stopped and folded into the result
    # before any return path builds its log/RunResult.
    monitor = None
    # R6.2: observations captured while the child is alive.  A post-exit
    # QueryFullProcessImageNameW always fails on Windows.
    root_image = None
    root_image_error = None
    descendants_seen: dict[int, dict] = {}
    descendant_error = None

    popen_kwargs: dict = {}
    if _IS_WINDOWS:  # pragma: no cover - Windows branch
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        job = _WinJob()
        if not job.ok and job.note:
            term_notes.append(job.note)
    elif _IS_POSIX:  # pragma: no cover - POSIX branch
        popen_kwargs["start_new_session"] = True

    with open(out_path, "wb") as fo, open(err_path, "wb") as fe:
        try:
            proc = subprocess.Popen(
                cmd_list,
                cwd=str(cwd) if cwd else None,
                stdout=fo,
                stderr=fe,
                env=env,
                shell=False,
                **popen_kwargs,
            )
        except OSError as exc:
            error = f"spawn_failed: {exc}"
            fe.write(error.encode("utf-8", "replace"))
            fe.flush()
            spawn_failed = True
            containment = CONTAINMENT_NONE  # nothing ran; make no containment claim
            # R5: a WSL/uncontainable-tree command stays honestly flagged even
            # when it could not be spawned; the result is never auth-safe.
            containment_reason = "spawn_failed" + (
                f"; {unsupported_reason}" if unsupported else ""
            )
            wsl_uncontained = wsl
            if unsupported:
                term_notes.append(f"{unsupported_reason}: {unsupported_detail}")
        else:
            pid = proc.pid
            if job is not None:
                assigned = job.assign(proc)
                term_notes.append(
                    "job assignment ok" if assigned else f"job assignment unavailable: {job.note}"
                )
            elif _IS_POSIX:  # pragma: no cover - POSIX branch
                job = _PosixJobGuard(pid)
            # R6.1 P0-B: start dynamic identity observation while the child is
            # still alive.  Read the root image NOW (before a fast exit) and
            # hand it to the monitor so the root identity is known even if a
            # later poll cannot re-open the process.  Any setup/observation
            # error is recorded and later fails closed -- it never raises.
            if _r61 is not None:
                try:
                    root_image, root_image_error = _r61.IMAGE_FN(pid)
                except Exception as exc:  # noqa: BLE001
                    root_image = None
                    root_image_error = "root image observation error: %s" % exc
                try:
                    monitor = _r61.ProcessIdentityMonitor()
                    monitor.add_root(pid, image=root_image)
                    _r61.register_root(pid)
                    monitor.start()
                except Exception as exc:  # noqa: BLE001 - fail closed via flags
                    if monitor is None:
                        monitor = _r61.ProcessIdentityMonitor()
                    monitor.fail_closed("identity monitor setup error: %s" % exc)

        if not spawn_failed:
            reason: str | None = None
            while True:
                rc = proc.poll()
                if rc is None:
                    observed |= _descendants(pid, snap_fn())
                    # R6.2: collect full descendant observations (image + error)
                    # while the tree is live for the structural proof.
                    if _r61 is not None:
                        try:
                            for entry in _r61.DESCENDANTS_FN(pid) or []:
                                if isinstance(entry, Mapping) and entry.get("pid") is not None:
                                    descendants_seen[int(entry["pid"])] = dict(entry)
                        except Exception as exc:  # noqa: BLE001
                            descendant_error = descendant_error or (
                                "descendant observation error: %s" % exc
                            )
                else:
                    # Direct child (wrapper) has exited.  An orphan is a
                    # previously-observed descendant that is still alive.
                    s = snap_fn()
                    orphans = [p for p in observed if p != pid and p in s]
                    exit_code = proc.returncode
                    if orphans:
                        reason = "orphan"
                        term_notes.append(
                            "wrapper exited with surviving descendants: " + repr(sorted(orphans))
                        )
                    elif unsupported:
                        # A5-B / R5: classify (do NOT claim prevention) on every
                        # WSL / uncontainable-tree path with one canonical,
                        # machine-readable token and never authorize it.
                        containment = CONTAINMENT_UNSUPPORTED
                        containment_reason = unsupported_reason
                        wsl_uncontained = wsl
                        error = unsupported_reason
                        term_notes.append(
                            f"{unsupported_reason}: {unsupported_detail} "
                            f"[signal={wsl_risk_reason or 'detached-tree'}]"
                        )
                    break
                if is_cancel():
                    reason = "cancel"
                    break
                if time.monotonic() >= hard_deadline:
                    reason = "timeout"
                    break
                sleep_for = min(poll_interval, max(0.001, hard_deadline - time.monotonic()))
                time.sleep(sleep_for)

            if reason is not None:
                # Terminate the whole tree using captured + currently connected
                # descendants, then confirm every captured PID is gone.
                s = snap_fn()
                observed |= _descendants(pid, s)
                targets = set(observed)
                targets.add(pid)
                if job is not None:
                    targets |= job.members()

                if terminator is not None:
                    method, detail = terminator(
                        proc, sorted(targets), term_notes,
                    )
                else:
                    method, detail = terminate_process_tree(proc, detail_log=term_notes)

                # Explicitly kill descendants that may have been reparented out
                # of the tree kill (DETACHED_PROCESS / wrapper-exit orphans).
                for target in sorted(targets):
                    if target != pid and target in snap_fn():
                        _kill_pid(target, term_notes)

                # Closing the job handle kills every remaining job member,
                # including detached descendants of an exited wrapper.
                if job is not None:
                    job.close()
                    term_notes.append("job handle closed (KILL_ON_JOB_CLOSE)")

                survivors = _confirm_gone(
                    targets, snap_fn, confirm_grace_s, term_notes,
                    alive_fn=pid_alive_probe,
                )
                confirmed = not survivors
                if survivors:
                    term_notes.append(
                        "UNCONFIRMED survivors still alive after termination: "
                        + repr(survivors)
                    )
                else:
                    term_notes.append("all captured PIDs confirmed dead")
                termination = method
                termination_detail = "; ".join([detail] + term_notes)

                if reason == "timeout":
                    timed_out = True
                    exit_code = None
                    if unsupported:
                        containment = CONTAINMENT_UNSUPPORTED
                        containment_reason = unsupported_reason
                        wsl_uncontained = wsl
                        error = f"timed_out; {unsupported_reason}"
                    elif confirmed:
                        containment = CONTAINMENT_TREE_CONFIRMED
                        containment_reason = REASON_TREE_CONFIRMED
                    else:
                        containment = CONTAINMENT_TREE_UNCONFIRMED
                        containment_reason = REASON_TREE_UNCONFIRMED
                        error = "termination_unconfirmed: survivors=" + repr(survivors)
                elif reason == "cancel":
                    cancelled_flag = True
                    exit_code = None
                    if unsupported:
                        containment = CONTAINMENT_UNSUPPORTED
                        containment_reason = unsupported_reason
                        wsl_uncontained = wsl
                        error = f"cancelled; {unsupported_reason}"
                    elif confirmed:
                        containment = CONTAINMENT_TREE_CONFIRMED
                        containment_reason = REASON_TREE_CONFIRMED
                        error = "cancelled"
                    else:
                        containment = CONTAINMENT_TREE_UNCONFIRMED
                        containment_reason = REASON_TREE_UNCONFIRMED
                        error = "cancelled; termination_unconfirmed: survivors=" + repr(survivors)
                else:  # orphan
                    exit_code = proc.returncode
                    if unsupported:
                        # R3 left this path's error as plain "wrapper_exit_orphan"
                        # with no WSL marker -- ambiguous for callers.  R4/R5 make
                        # EVERY unsupported path carry the canonical token.
                        containment = CONTAINMENT_UNSUPPORTED
                        containment_reason = unsupported_reason
                        wsl_uncontained = wsl
                        error = f"wrapper_exit_orphan; {unsupported_reason}"
                    elif confirmed:
                        containment = CONTAINMENT_WRAPPER_ORPHAN
                        containment_reason = REASON_WRAPPER_ORPHAN
                        error = REASON_WRAPPER_ORPHAN
                    else:
                        containment = CONTAINMENT_TREE_UNCONFIRMED
                        containment_reason = REASON_TREE_UNCONFIRMED
                        error = (
                            "wrapper_exit_orphan; termination_unconfirmed: survivors="
                            + repr(survivors)
                        )

                try:
                    proc.wait(timeout=2)
                except Exception:  # noqa: BLE001
                    pass

                outcome = _ManagedOutcome(
                    reason=reason, exit_code=exit_code, confirmed=confirmed,
                    survivors=tuple(survivors), targets=tuple(sorted(targets)),
                    method=method, detail=termination_detail,
                )

    duration = time.monotonic() - started

    # R6.1 P0-B (authoritative boundary): stop the dynamic identity monitor and
    # fold its findings into the result BEFORE the log is built, so the durable
    # audit record and the downstream authorization gate agree.  Detection +
    # fail-closed only -- nothing here kills or prevents a process.
    identity_line = ""
    if monitor is not None:
        try:
            monitor.stop()
        except Exception as exc:  # noqa: BLE001 - detection must never raise
            monitor.fail_closed("identity monitor stop error: %s" % exc)
        try:
            _r61.unregister_root(pid)
        except Exception:  # noqa: BLE001
            pass

    # R6.2 (ADR §6 / AMENDMENT 1): derive the structural execution proof from
    # the observations captured while the child was alive.  A monitor failure
    # becomes an UNKNOWN proof; it never raises into the caller.
    proof = None
    if boundary is not None:
        try:
            if pid is None or spawn_failed or descendant_error:
                proof = boundary.denied_proof(
                    cmd_list, tool_id=name, check_id=name,
                    reason=descendant_error or error or "spawn_failed",
                    state=_et.ExecutionState.UNKNOWN, exit_code=exit_code,
                )
            else:
                proof = boundary.prove_for_run(
                    cmd_list,
                    tool_id=name,
                    check_id=name,
                    root_pid=pid,
                    guard=job,
                    exit_code=exit_code,
                    started_utc=started_utc,
                    ended_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    sampling_window_s=duration,
                    sampling_samples=len(observed),
                    root_image=root_image,
                    root_image_error=root_image_error,
                    descendants=list(descendants_seen.values()),
                )
        except Exception as exc:  # noqa: BLE001
            try:
                proof = boundary.denied_proof(
                    cmd_list, tool_id=name, check_id=name,
                    reason="proof construction error: %s" % exc,
                    state=_et.ExecutionState.UNKNOWN, exit_code=exit_code,
                )
            except Exception:  # noqa: BLE001
                proof = None

    result = RunResult(
        command=cmd_list,
        exit_code=exit_code,
        timed_out=timed_out,
        duration_s=duration,
        stdout_log=out_path,
        stderr_log=err_path,
        combined_log=comb_path,
        cwd=str(cwd or os.getcwd()),
        error=error,
        termination=termination,
        termination_detail=termination_detail,
        pid=pid,
        containment=containment,
        cancelled=cancelled_flag,
        descendant_pids=tuple(sorted(observed)),
        containment_reason=containment_reason,
        wsl_uncontained=wsl_uncontained,
        proof=proof,
        execution_proof=proof.to_dict() if proof is not None else None,
    )

    if monitor is not None:
        try:
            # Always attach evidence.  Only MUTATE flags when the cheap string
            # gate did not already classify this command unsupported; in that
            # case its more specific canonical reason is authoritative and is
            # preserved verbatim (existing R5/A5-B contract).
            result.identity_evidence = monitor.evidence()
            if monitor.unsupported and not unsupported:
                _r61.apply_identity_finding(result, monitor)
                images = result.identity_evidence.get("detected_images", [])
                identity_line = (
                    f"# identity_observation=unsupported "
                    f"reason={result.containment_reason} images={images}\n"
                )
        except Exception as exc:  # noqa: BLE001 - never raise for monitor code
            try:
                result.identity_evidence = {
                    "unsupported": True,
                    "reason": "identity finalize error: %s" % exc,
                }
                result.wsl_uncontained = True
                result.containment = CONTAINMENT_UNSUPPORTED
                result.authorization_safe = False
                if not result.containment_reason:
                    result.containment_reason = _r61.REASON_DYNAMIC_IDENTITY
            except Exception:
                pass

    parts: list[bytes] = []
    for p in (out_path, err_path):
        try:
            with open(p, "rb") as fh:
                parts.append(fh.read())
        except OSError:
            pass
    header = (
        f"$ {' '.join(cmd_list)}\n"
        f"# cwd={cwd or os.getcwd()} exit_code={result.exit_code} "
        f"timed_out={result.timed_out} duration_s={duration:.3f}\n"
        f"# containment={result.containment} cancelled={result.cancelled} "
        f"pid={pid} descendants={sorted(observed)}\n"
        f"# containment_reason={result.containment_reason} "
        f"wsl_uncontained={result.wsl_uncontained} error={result.error}\n"
        f"# termination={termination} detail={termination_detail[:500]}\n"
        + (
            f"# execution_proof state={proof.state.value} "
            f"nonce={proof.nonce} execution_digest={proof.execution_digest} "
            f"manifest_match={proof.approved_images_match} "
            f"job_members={proof.job_member_count}/{proof.descendant_count} "
            f"mac={proof.mac}\n"
            if proof is not None else ""
        )
        + identity_line
    ).encode("utf-8", "replace")
    with open(comb_path, "wb") as fc:
        fc.write(header)
        for i, part in enumerate(parts):
            if i:
                fc.write(b"\n----- stderr -----\n")
            fc.write(part)

    return result


# ---------------------------------------------------------------------------
# Thread containment contract (R4 / A5-C)
# ---------------------------------------------------------------------------

#: Sentinel used when a path is absent from a workspace hash mapping.
_WORKSPACE_ABSENT = "<absent>"


@dataclass
class WorkerHandle:
    """Identity + lifetime of a registered in-process worker thread."""

    name: str
    thread: threading.Thread
    started_at: float = field(default_factory=time.monotonic)

    @property
    def alive(self) -> bool:
        return self.thread.is_alive()


@dataclass
class ThreadContainmentResult:
    """Fail-closed outcome of :meth:`ThreadContainment.quiesce`.

    ``contained`` is True only when every registered worker finished within the
    bound AND the post-wait re-hash equals ``before``.  A False value means the
    caller MUST fail closed (do not authorize, do not report rollback verified).
    """

    contained: bool
    uncontained_workers: tuple[str, ...] = ()
    late_mutations: tuple[str, ...] = ()
    waited_s: float = 0.0
    detail: str = ""


class ThreadContainment:
    """Registry + bounded-join + re-hash for in-process verification workers.

    CPython cannot forcibly terminate a thread.  This class makes NO prevention
    claim (A5-C).  Instead it makes an uncooperative worker's late mutation
    OBSERVABLE and hands the caller an explicit fail-closed signal:

    1. :meth:`register` tracks every worker's lifetime (name, thread, start time).
    2. :meth:`quiesce` -- called AFTER the caller rolls back the workspace --
       WAITS up to an explicit ``wait_s`` for any still-alive worker, polling at
       ``poll_interval`` (a bounded join; never an unbounded wait).
    3. It then RE-HASHES the workspace with ``snapshot_fn`` and diffs against the
       post-rollback ``before`` mapping to detect writes that landed during the
       wait (the exact gap that escaped the R3 0.05 s hash window).
    4. A worker still alive after the bound is reported by name in
       ``uncontained_workers`` and ``contained`` is False.

    ``snapshot_fn`` must be a zero-argument callable returning ``{rel: sha256}``;
    pass ``EcoManager.snapshot_workspace`` to integrate with eco.py.  A default
    walker over the current working directory is provided for standalone use.
    """

    def __init__(
        self,
        *,
        snapshot_fn: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self._snapshot_fn = snapshot_fn if snapshot_fn is not None else self._default_snapshot
        self._workers: dict[int, WorkerHandle] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _default_snapshot() -> dict[str, str]:
        """Hash every readable file under cwd as ``{rel: sha256}``."""
        root = os.getcwd()
        out: dict[str, str] = {}
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                try:
                    h = hashlib.sha256()
                    with open(full, "rb") as fh:
                        for chunk in iter(lambda: fh.read(1 << 20), b""):
                            h.update(chunk)
                    out[rel] = h.hexdigest()
                except OSError:
                    out[rel] = "<unreadable>"
        return out

    # -- registry ------------------------------------------------------------

    def register(self, thread: threading.Thread, *, name: str | None = None) -> WorkerHandle:
        """Track *thread* for later bounded quiescence. Idempotent per object."""
        handle = WorkerHandle(name=name or getattr(thread, "name", "worker"), thread=thread)
        with self._lock:
            self._workers[id(thread)] = handle
        return handle

    def unregister(self, thread: threading.Thread) -> None:
        """Stop tracking *thread* (e.g. after a confirmed clean finish)."""
        with self._lock:
            self._workers.pop(id(thread), None)

    def workers(self) -> tuple[WorkerHandle, ...]:
        """Snapshot of all registered handles (registry is thread-safe)."""
        with self._lock:
            return tuple(self._workers.values())

    def active_workers(self) -> tuple[WorkerHandle, ...]:
        """Registered handles whose thread is currently alive."""
        return tuple(h for h in self.workers() if h.alive)

    # -- bounded wait + re-hash ---------------------------------------------

    def quiesce(
        self,
        *,
        before: Mapping[str, str] | None = None,
        wait_s: float = 1.0,
    ) -> ThreadContainmentResult:
        """Wait (bounded) for registered workers, then re-hash the workspace.

        Call this AFTER rollback has restored the workspace.  ``before`` must be
        the post-rollback workspace hash mapping (the state that must still hold
        when this method returns).  If omitted, no late-mutation diff is made
        and only worker liveness is reported.

        Returns a :class:`ThreadContainmentResult`.  ``contained`` is False if
        any worker outlives ``wait_s`` or any path's hash changed -- both are
        explicit fail-closed signals for the caller (eco.py must not authorize).
        """
        if wait_s < 0:
            raise ValueError("wait_s must be >= 0")
        t0 = time.monotonic()
        deadline = t0 + wait_s
        handles = self.workers()

        for handle in handles:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if handle.alive:
                handle.thread.join(timeout=remaining)
        waited = time.monotonic() - t0

        still_alive = tuple(h.name for h in handles if h.alive)
        after = self._snapshot_fn()
        late: list[str] = []
        if before is not None:
            for rel in sorted(set(before) | set(after)):
                if before.get(rel, _WORKSPACE_ABSENT) != after.get(rel, _WORKSPACE_ABSENT):
                    late.append(rel)

        contained = not still_alive and not late
        parts: list[str] = []
        if still_alive:
            parts.append("uncontained_workers=" + repr(still_alive))
        if late:
            parts.append("late_mutations=" + repr(late[:20]))
        if not parts:
            parts.append("all workers finished within bound; workspace hash unchanged")
        return ThreadContainmentResult(
            contained=contained,
            uncontained_workers=still_alive,
            late_mutations=tuple(late),
            waited_s=waited,
            detail="; ".join(parts),
        )


def run_command(
    command: list[str] | str,
    *,
    cwd: str | os.PathLike[str] | None = None,
    log_dir: str | os.PathLike[str],
    name: str,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> RunResult:
    """Run *command*, teeing all output to files under *log_dir*.

    Logs are written even when the command fails, times out or cannot start,
    so failed runs keep their evidence.  On timeout the process is killed and
    ``timed_out=True`` with ``exit_code=None``.

    This is the legacy (non-managed) path: behavior and defaults are preserved
    exactly.  Callers that need deadline/cancellation enforcement and
    containment confirmation should use :func:`run_managed`.
    """
    if isinstance(command, str):
        cmd_list = [command] if os.name == "nt" else ["/bin/bash", "-lc", command]
    else:
        cmd_list = list(command)
    log_dir = str(log_dir)
    os.makedirs(log_dir, exist_ok=True)
    out_path = os.path.join(log_dir, f"{name}.stdout.log")
    err_path = os.path.join(log_dir, f"{name}.stderr.log")
    comb_path = os.path.join(log_dir, f"{name}.log")

    # TR-03: monotonic clock for both the deadline and the measured duration.
    started = time.monotonic()
    error = ""
    exit_code: int | None = None
    timed_out = False
    termination = "none"
    termination_detail = ""
    pid: int | None = None
    term_notes: list[str] = []

    popen_kwargs: dict = {}
    if os.name == "nt":
        # A new process group lets taskkill /T identify the whole tree.
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True

    with open(out_path, "wb") as fo, open(err_path, "wb") as fe:
        try:
            proc = subprocess.Popen(
                cmd_list,
                cwd=str(cwd) if cwd else None,
                stdout=fo,
                stderr=fe,
                env=env,
                shell=False,
                **popen_kwargs,
            )
        except OSError as exc:  # command could not start: preserve the error
            error = f"spawn_failed: {exc}"
            fe.write(error.encode("utf-8", "replace"))
            fe.flush()
        else:
            pid = proc.pid
            try:
                exit_code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                termination, termination_detail = terminate_process_tree(proc, detail_log=term_notes)
                if termination == "failed":
                    error = "kill_failed"
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    error = (error + "; rewait_failed").strip("; ")
                exit_code = None
    duration = time.monotonic() - started

    # Build a combined log (best effort; preserves failure output).
    parts: list[str] = []
    for p in (out_path, err_path):
        try:
            with open(p, "rb") as f:
                parts.append(f.read())
        except OSError:
            pass
    header = (
        f"$ {' '.join(cmd_list)}\n"
        f"# cwd={cwd or os.getcwd()} exit_code={exit_code} "
        f"timed_out={timed_out} duration_s={duration:.3f}\n"
    ).encode("utf-8", "replace")
    with open(comb_path, "wb") as f:
        f.write(header)
        for i, part in enumerate(parts):
            if i:
                f.write(b"\n----- stderr -----\n")
            f.write(part)

    return RunResult(
        command=cmd_list,
        exit_code=exit_code,
        timed_out=timed_out,
        duration_s=duration,
        stdout_log=out_path,
        stderr_log=err_path,
        combined_log=comb_path,
        cwd=str(cwd or os.getcwd()),
        error=error,
        termination=termination,
        termination_detail=termination_detail,
        pid=pid,
    )
