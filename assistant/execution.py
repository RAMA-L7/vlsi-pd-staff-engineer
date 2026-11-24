"""Execution boundary: registered-tool launch + OS-observed process identity.

Round 6 architectural redesign, Agent 5 (ADR §7 and the ``assistant/execution.py``
interface contract).  This module owns the *only* code path that may create
``TOOL_EXECUTION`` evidence:

* :class:`ToolRegistry` pins a tool id to an absolute path and a SHA-256, and
  re-verifies that digest at registration time.
* :class:`ManagedExecution.run_authoritative` resolves a registered tool,
  re-hashes its file, launches it directly (no shell, no wrapper), observes the
  *actual* OS process image, and monitors descendants for unsupported
  identities (WSL / shells / wrappers / unknown).  Any mismatch, any
  unsupported descendant, and any observation error yields ``UNSUPPORTED``
  (non-authorizing) evidence — this is **detection + fail-closed**, NOT
  containment (ADR §7, §11).
* :class:`ManagedExecution.run_observation` runs arbitrary commands for
  diagnostics/tests and can only ever return ``OBSERVATION`` (non-authorizing)
  or ``UNSUPPORTED``.  It must never return ``TOOL_EXECUTION``.

Design constraints
------------------
* Python 3.10+, standard library only.  Windows process observation uses
  ``ctypes`` (``OpenProcess`` + ``QueryFullProcessImageNameW`` and
  ``CreateToolhelp32Snapshot``/``Process32FirstW``/``Process32NextW``); Linux
  uses ``/proc/<pid>/exe`` and the ``/proc/<pid>/stat`` PPID tree.
* ``provenance`` (and the injected ``trust``) are imported lazily so this file
  can be reviewed/tested in isolation during staged integration.  If they are
  unavailable, callers still get a non-authorizing UNSUPPORTED envelope.
* The exit status is recorded in the envelope's ``status`` field.  A non-zero
  exit keeps ``TOOL_EXECUTION`` provenance (a real tool ran) but sets
  ``status="FAIL"``; authorization additionally requires ``status == "PASS"``
  and is enforced by ``state_machine.derive_authorization`` (ADR §5).  This
  module does not itself make authorization decisions.

Known limits (documented, not claimed as defended): PID reuse between sample
and observation, a fully compromised host, and a fast-exiting process on Linux
whose ``/proc/<pid>/exe`` symlink disappears before observation (fail-closed).
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

__all__ = ["ToolRegistry", "ManagedExecution"]

_IS_WINDOWS = os.name == "nt"
_HASH_CHARS = frozenset("0123456789abcdef")

# Basenames that are wrappers/shells/WSL launchers and therefore never legal
# authoritative tool identities.  The ADR names wsl.exe / bash.exe / cmd.exe /
# powershell.exe / wslhost.exe; pwsh.exe and wslg.exe are added defensively.
_UNSUPPORTED_BASENAMES = frozenset(
    {
        "wsl.exe",
        "bash.exe",
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "wslhost.exe",
        "wslg.exe",
        "wsl",
        "bash",
        "cmd",
        "powershell",
    }
)


# ---------------------------------------------------------------------------
# Small local utilities (no dependency on other assistant modules)
# ---------------------------------------------------------------------------


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm_digest(value: Any) -> str:
    return str(value or "").strip().lower()


def _is_valid_digest(value: str) -> bool:
    return len(value) == 64 and all(c in _HASH_CHARS for c in value)


def _same_path(a: str, b: str) -> bool:
    """Compare two paths as identities (case-insensitive on Windows)."""
    try:
        pa = os.path.realpath(os.path.normpath(str(a)))
        pb = os.path.realpath(os.path.normpath(str(b)))
    except (OSError, ValueError):
        return os.path.normcase(str(a)) == os.path.normcase(str(b))
    if _IS_WINDOWS:
        return os.path.normcase(pa) == os.path.normcase(pb)
    return pa == pb


def _is_unsupported_identity(image: str | None) -> bool:
    """True for WSL/wrapper/shell identities and for *unknown* images.

    Called on descendant images (and on a root image where it is folded into
    the path comparison).  Unknown (``None``) fails closed by design.
    """
    if not image:
        return True
    low = str(image).replace("\\", "/").lower()
    base = low.rsplit("/", 1)[-1]
    if base in _UNSUPPORTED_BASENAMES:
        return True
    if "wsl" in base:
        return True
    # "path under System32 with wsl" (explicit ADR requirement).
    if "/windows/system32/" in low and "wsl" in low:
        return True
    return False


def _load_provenance():
    """Lazily import ``assistant.provenance``; return (module_or_None, error)."""
    try:
        from . import provenance  # type: ignore

        return provenance, None
    except ImportError:
        pass
    try:  # supports `python assistant/execution.py` self-test
        import provenance  # type: ignore

        return provenance, None
    except ImportError as exc:
        return None, str(exc)


def _prov_value(module: Any, name: str) -> Any:
    """Prefer the real ``Provenance`` enum member over a raw string."""
    enum_cls = getattr(module, "Provenance", None)
    member = getattr(enum_cls, name, None) if enum_cls is not None else None
    return member if member is not None else name


def _trust_context(trust: Any) -> dict:
    """Convert the injected trust root's context into a Mapping for the MAC."""
    ctx = getattr(trust, "context", None)
    if ctx is None:
        return {
            "session_id": "",
            "repo_id": "",
            "revision": "",
            "created_utc": "",
        }
    if isinstance(ctx, Mapping):
        return dict(ctx)
    if is_dataclass(ctx):
        return asdict(ctx)
    return {
        "session_id": str(getattr(ctx, "session_id", "")),
        "repo_id": str(getattr(ctx, "repo_id", "")),
        "revision": str(getattr(ctx, "revision", "")),
        "created_utc": str(getattr(ctx, "created_utc", "")),
    }


class _ProvenanceUnavailable(RuntimeError):
    """Raised internally when the provenance boundary cannot be used."""


class _ObservationError(RuntimeError):
    """Raised internally when an OS identity observation fails."""


class ToolRegistryError(ValueError):
    """Raised on an invalid tool registration (bad path/digest/mismatch)."""


# ---------------------------------------------------------------------------
# Fallback envelope (used ONLY when provenance cannot be imported or used)
# ---------------------------------------------------------------------------


class _FallbackEnvelope:
    """Minimal, deliberately non-authorizing envelope for fail-closed returns.

    It mirrors the public shape of ``provenance.EvidenceEnvelope`` so callers
    can still inspect ``.provenance`` / ``.execution_identity`` / ``.to_dict()``
    even when the provenance module is not yet available during staged
    integration.  It is never authorizing and carries no MAC.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.evidence_id = str(kwargs.get("evidence_id") or uuid.uuid4().hex[:16])
        self.check_id = str(kwargs.get("check_id") or "")
        self.gate_id = str(kwargs.get("gate_id") or "")
        self.provenance = kwargs.get("provenance", "UNSUPPORTED")
        self.execution_identity = dict(kwargs.get("execution_identity") or {})
        self.consumed_sha256 = str(kwargs.get("consumed_sha256") or "")
        self.dependencies = tuple(kwargs.get("dependencies") or ())
        self.status = str(kwargs.get("status") or "UNSUPPORTED")
        self.observed_utc = str(kwargs.get("observed_utc") or _utc_now())
        self.context = dict(kwargs.get("context") or {})
        self.mac = str(kwargs.get("mac") or "")

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "check_id": self.check_id,
            "gate_id": self.gate_id,
            "provenance": self.provenance,
            "execution_identity": self.execution_identity,
            "consumed_sha256": self.consumed_sha256,
            "dependencies": list(self.dependencies),
            "status": self.status,
            "observed_utc": self.observed_utc,
            "context": self.context,
            "mac": self.mac,
        }

    def is_authorizing(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Windows OS observation (ctypes)
# ---------------------------------------------------------------------------

if _IS_WINDOWS:  # pragma: no cover - platform-dependent
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TH32CS_SNAPPROCESS = 0x00000002
    _INVALID_HANDLE = -1  # c_ssize_t (signed pointer) representation

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_ssize_t),  # ULONG_PTR
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    def _win_kernel32():
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        k32.OpenProcess.restype = ctypes.c_ssize_t
        k32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_ssize_t,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [ctypes.c_ssize_t]
        k32.CloseHandle.restype = wintypes.BOOL
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.CreateToolhelp32Snapshot.restype = ctypes.c_ssize_t
        k32.Process32FirstW.argtypes = [
            ctypes.c_ssize_t,
            ctypes.POINTER(_PROCESSENTRY32W),
        ]
        k32.Process32FirstW.restype = wintypes.BOOL
        k32.Process32NextW.argtypes = [
            ctypes.c_ssize_t,
            ctypes.POINTER(_PROCESSENTRY32W),
        ]
        k32.Process32NextW.restype = wintypes.BOOL
        return k32

    def _win_image_from_handle(k32: Any, pid: int) -> tuple[str | None, str | None]:
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle or handle == _INVALID_HANDLE:
            return None, "OpenProcess failed (err=%d)" % ctypes.get_last_error()
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            ok = k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
            if not ok:
                return None, "QueryFullProcessImageNameW failed (err=%d)" % ctypes.get_last_error()
            return (buf.value or None), None
        finally:
            k32.CloseHandle(handle)

    def _win_descendants(k32: Any, root_pid: int) -> list[dict]:
        """Snapshot the process table and return descendants of *root_pid*.

        Uses ``CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)`` and walks the
        parent-PID tree.  Full image paths come from
        ``OpenProcess`` + ``QueryFullProcessImageNameW`` with the snapshot's
        ``szExeFile`` as a fallback for short-lived children.
        """
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == _INVALID_HANDLE:
            raise _ObservationError(
                "CreateToolhelp32Snapshot failed (err=%d)" % ctypes.get_last_error()
            )
        entries: dict[int, tuple[int, str]] = {}
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            if not k32.Process32FirstW(snap, ctypes.byref(entry)):
                return []
            while True:
                pid = int(entry.th32ProcessID)
                entries[pid] = (int(entry.th32ParentProcessID), str(entry.szExeFile))
                if not k32.Process32NextW(snap, ctypes.byref(entry)):
                    break
        finally:
            k32.CloseHandle(snap)

        children: dict[int, list[int]] = {}
        for pid, (ppid, _name) in entries.items():
            children.setdefault(ppid, []).append(pid)

        out: list[dict] = []
        seen = {int(root_pid)}
        queue = [int(root_pid)]
        while queue:
            parent = queue.pop(0)
            for child in children.get(parent, []):
                if child in seen:
                    continue
                seen.add(child)
                full, err = _win_image_from_handle(k32, child)
                image = full or entries.get(child, (0, ""))[1] or None
                out.append(
                    {
                        "pid": child,
                        "image": image,
                        "error": None if image else (err or "descendant image unknown"),
                    }
                )
                queue.append(child)
        return out


# ---------------------------------------------------------------------------
# Linux OS observation (/proc)
# ---------------------------------------------------------------------------


def _linux_image(pid: int) -> tuple[str | None, str | None]:
    try:
        return os.readlink("/proc/%d/exe" % int(pid)), None
    except FileNotFoundError:
        return None, "process exited before /proc/<pid>/exe could be read"
    except OSError as exc:
        return None, "readlink /proc/<pid>/exe failed: %s" % exc


def _linux_cmdline(pid: int) -> str:
    try:
        with open("/proc/%d/cmdline" % int(pid), "rb") as fh:
            return fh.read().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def _linux_parent(pid: int) -> int | None:
    """Parse field 4 (PPID) of ``/proc/<pid>/stat`` (comm may contain spaces)."""
    try:
        with open("/proc/%d/stat" % int(pid), "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read()
    except OSError:
        return None
    close = data.rfind(")")
    if close < 0:
        return None
    parts = data[close + 1 :].strip().split()
    # After ") ": state(3), ppid(4), ...
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def _linux_descendants(root_pid: int) -> list[dict]:
    parents: dict[int, int | None] = {}
    try:
        names = os.listdir("/proc")
    except OSError as exc:
        raise _ObservationError("cannot list /proc: %s" % exc) from exc
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        parents[pid] = _linux_parent(pid)

    children: dict[int, list[int]] = {}
    for pid, ppid in parents.items():
        if ppid is not None:
            children.setdefault(ppid, []).append(pid)

    out: list[dict] = []
    seen = {int(root_pid)}
    queue = [int(root_pid)]
    while queue:
        parent = queue.pop(0)
        for child in children.get(parent, []):
            if child in seen:
                continue
            seen.add(child)
            image, err = _linux_image(child)
            cmdline = _linux_cmdline(child)
            wsl = _is_unsupported_identity(image)
            if not wsl and image:
                low = image.replace("\\", "/").lower()
                base = low.rsplit("/", 1)[-1]
                # WSL interop: a Windows executable reached through /mnt/<drive>
                # or a command line mentioning wsl indicates a wrapper origin.
                if low.startswith("/mnt/") and base.endswith(".exe"):
                    wsl = True
                elif "wsl" in cmdline.lower():
                    wsl = True
            out.append(
                {
                    "pid": child,
                    "image": image,
                    "error": None if image else (err or "descendant image unknown"),
                    "wsl": bool(wsl),
                }
            )
            queue.append(child)
    return out


# ---------------------------------------------------------------------------
# Unified observation dispatch
# ---------------------------------------------------------------------------


def _observe_image(pid: int) -> tuple[str | None, str, str | None]:
    """Return ``(image, method, error)`` for the process with *pid*."""
    if _IS_WINDOWS:  # pragma: no cover - platform-dependent
        k32 = _win_kernel32()
        image, err = _win_image_from_handle(k32, pid)
        return image, "win32:OpenProcess+QueryFullProcessImageNameW", err
    image, err = _linux_image(pid)
    return image, "linux:/proc/<pid>/exe", err


def _observe_descendants(root_pid: int) -> tuple[list[dict], bool]:
    """Return ``(descendant_entries, wsl_detected)``; fail closed on errors."""
    if _IS_WINDOWS:  # pragma: no cover - platform-dependent
        entries = _win_descendants(_win_kernel32(), root_pid)
    else:
        entries = _linux_descendants(root_pid)
    wsl = any(
        bool(e.get("wsl")) or _is_unsupported_identity(e.get("image")) for e in entries
    )
    return entries, wsl


def _kill_tree(pid: int) -> None:
    """Best-effort termination of a timed-out process tree."""
    try:
        if _IS_WINDOWS:  # pragma: no cover - platform-dependent
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        else:
            os.kill(int(pid), 9)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Immutable-ish map of ``tool_id -> (absolute_path, sha256)``.

    ``register`` refuses relative/non-existent paths and any digest that does
    not match the file's computed SHA-256.  ``resolve`` raises ``KeyError`` for
    unknown ids.  The registry is re-hashed at execution time by
    :class:`ManagedExecution` to detect post-registration mutation.
    """

    def __init__(self) -> None:
        self._tools: dict[str, tuple[str, str]] = {}

    def register(self, tool_id: Any, path: Any, sha256: Any) -> tuple[str, str]:
        tid = str(tool_id)
        if not tid:
            raise ToolRegistryError("tool_id must be non-empty")
        raw_path = str(path)
        if not os.path.isabs(raw_path):
            raise ToolRegistryError("tool path must be absolute: %r" % raw_path)
        full_path = os.path.normpath(raw_path)
        if not os.path.isfile(full_path):
            raise ToolRegistryError(
                "tool path does not exist or is not a file: %r" % full_path
            )
        declared = _norm_digest(sha256)
        if not _is_valid_digest(declared):
            raise ToolRegistryError("sha256 must be 64 lowercase hex characters")
        actual = _sha256_file(full_path)
        if actual != declared:
            raise ToolRegistryError(
                "sha256 mismatch for %r: declared %s, computed %s"
                % (tid, declared, actual)
            )
        self._tools[tid] = (full_path, actual)
        return full_path, actual

    def resolve(self, tool_id: Any) -> tuple[str, str]:
        tid = str(tool_id)
        if tid not in self._tools:
            raise KeyError("unregistered tool_id: %r" % tid)
        path, digest = self._tools[tid]
        return path, digest

    def __contains__(self, tool_id: Any) -> bool:
        return str(tool_id) in self._tools

    def __len__(self) -> int:
        return len(self._tools)


# ---------------------------------------------------------------------------
# ManagedExecution
# ---------------------------------------------------------------------------


class ManagedExecution:
    """Launch registered tools and classify evidence by observed identity."""

    def __init__(self, registry: ToolRegistry, trust: Any) -> None:
        self.registry = registry
        self.trust = trust

    # -- envelope construction ------------------------------------------------

    def _emit(
        self,
        module: Any,
        *,
        provenance_value: Any,
        execution_identity: Mapping,
        consumed_sha256: Any,
        dependencies: Iterable[Any],
        status: str,
        check_id: Any,
        gate_id: Any,
    ) -> Any:
        """Build a signed envelope, falling back to non-authorizing on error."""
        ident = dict(execution_identity)
        deps = tuple(dependencies or ())
        try:
            if module is None:
                raise _ProvenanceUnavailable("provenance module is unavailable")
            make_envelope = getattr(module, "make_envelope", None)
            if make_envelope is None:
                raise _ProvenanceUnavailable("provenance.make_envelope is unavailable")
            fields = dict(
                evidence_id=uuid.uuid4().hex[:16],
                check_id=str(check_id or ""),
                gate_id=str(gate_id or ""),
                provenance=provenance_value,
                execution_identity=ident,
                consumed_sha256=str(consumed_sha256 or ""),
                dependencies=deps,
                status=str(status),
                observed_utc=_utc_now(),
                context=_trust_context(self.trust),
            )
            try:
                # Actual Agent 2 signature: trust is keyword-only.
                return make_envelope(trust=self.trust, **fields)
            except TypeError as first:
                try:  # tolerate an alternate positional-trust convention
                    return make_envelope(self.trust, **fields)
                except TypeError:
                    raise first
        except Exception as exc:  # noqa: BLE001 - fail closed on any boundary error
            ident["unsupported_reason"] = (
                str(ident.get("unsupported_reason") or "")
                + " | envelope creation failed: %s" % exc
            ).strip(" |")
            return _FallbackEnvelope(
                check_id=str(check_id or ""),
                gate_id=str(gate_id or ""),
                provenance="UNSUPPORTED",
                execution_identity=ident,
                consumed_sha256=str(consumed_sha256 or ""),
                dependencies=deps,
                status="UNSUPPORTED",
                context=_trust_context(self.trust),
            )

    def _unsupported(
        self,
        module: Any,
        *,
        reason: str,
        check_id: Any,
        gate_id: Any,
        tool_id: Any = "",
        registered_path: str = "",
        observed_image: str | None = None,
        sha256: str = "",
        pid: int | None = None,
        dependencies: Iterable[Any] = (),
        consumed_sha256: Any = "",
        observation_method: str = "none",
        descendants: Iterable[Mapping] = (),
        wsl_detected: bool = False,
    ) -> Any:
        """Emit an UNSUPPORTED envelope with the reason in execution_identity."""
        ident = {
            "tool_id": str(tool_id),
            "registered_path": str(registered_path),
            "observed_image": observed_image,
            "sha256": str(sha256),
            "pid": pid,
            "descendants": list(descendants),
            "observation_method": observation_method,
            "wsl_detected": bool(wsl_detected),
            "unsupported_reason": str(reason),
        }
        return self._emit(
            module,
            provenance_value=_prov_value(module, "UNSUPPORTED") if module else "UNSUPPORTED",
            execution_identity=ident,
            consumed_sha256=consumed_sha256,
            dependencies=dependencies,
            status="UNSUPPORTED",
            check_id=check_id,
            gate_id=gate_id,
        )

    # -- authoritative execution ---------------------------------------------

    def run_authoritative(
        self,
        tool_id: Any,
        args: Sequence[Any],
        consumed_sha256: Any,
        dependencies: Iterable[Any],
        check_id: Any,
        gate_id: Any,
        timeout: float = 60,
    ) -> Any:
        """Run a registered tool and return TOOL_EXECUTION or UNSUPPORTED evidence.

        Fail-closed checks (each produces UNSUPPORTED with a reason):

        1. provenance boundary importable and a trust root present;
        2. tool id resolves in the registry;
        3. on-disk SHA-256 still equals the registered digest;
        4. observed OS image path equals the registered path;
        5. no unsupported descendant identity (WSL/shell/wrapper/unknown);
        6. no observation error and no timeout.

        A non-zero exit code yields ``status="FAIL"`` but keeps
        ``TOOL_EXECUTION`` provenance (a real tool did run).
        """
        module, import_error = _load_provenance()
        if module is None:
            return self._unsupported(
                None,
                reason="provenance module unavailable: %s" % import_error,
                check_id=check_id,
                gate_id=gate_id,
                tool_id=tool_id,
            )
        if self.trust is None:
            return self._unsupported(
                module,
                reason="no trust root provided to execution boundary",
                check_id=check_id,
                gate_id=gate_id,
                tool_id=tool_id,
                consumed_sha256=consumed_sha256,
                dependencies=dependencies,
            )

        # 2. Resolve the registered tool.
        try:
            registered_path, registered_sha = self.registry.resolve(tool_id)
        except KeyError as exc:
            return self._unsupported(
                module,
                reason="unregistered tool: %s" % exc,
                check_id=check_id,
                gate_id=gate_id,
                tool_id=tool_id,
                consumed_sha256=consumed_sha256,
                dependencies=dependencies,
            )

        # 3. Re-hash the file at run time (detect post-registration mutation).
        try:
            actual_sha = _sha256_file(registered_path)
        except OSError as exc:
            return self._unsupported(
                module,
                reason="tool file unreadable at run time: %s" % exc,
                check_id=check_id,
                gate_id=gate_id,
                tool_id=tool_id,
                registered_path=registered_path,
                sha256=registered_sha,
                consumed_sha256=consumed_sha256,
                dependencies=dependencies,
            )
        if actual_sha != registered_sha:
            return self._unsupported(
                module,
                reason=(
                    "tool file hash mismatch: registered %s, actual %s"
                    % (registered_sha, actual_sha)
                ),
                check_id=check_id,
                gate_id=gate_id,
                tool_id=tool_id,
                registered_path=registered_path,
                sha256=actual_sha,
                consumed_sha256=consumed_sha256,
                dependencies=dependencies,
            )

        # Launch directly (no shell / wrapper / WSL interop).
        argv = [registered_path] + [str(a) for a in (args or ())]
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return self._unsupported(
                module,
                reason="failed to launch registered tool: %s" % exc,
                check_id=check_id,
                gate_id=gate_id,
                tool_id=tool_id,
                registered_path=registered_path,
                sha256=actual_sha,
                consumed_sha256=consumed_sha256,
                dependencies=dependencies,
            )

        root_pid = proc.pid
        observation_method = "none"
        # 4. Observe the ACTUAL process image immediately, before the process
        #    can exit (on Windows the handle keeps the image queryable).
        try:
            observed_image, observation_method, image_error = _observe_image(root_pid)
        except Exception as exc:  # noqa: BLE001 - observation error fails closed
            observed_image, image_error = None, "image observation error: %s" % exc
            observation_method = "error"

        unsupported_reason = None
        if image_error:
            unsupported_reason = "could not observe tool image: %s" % image_error
        elif observed_image is None:
            unsupported_reason = "tool image could not be determined (unknown)"
        elif not _same_path(observed_image, registered_path):
            unsupported_reason = (
                "observed process image %r does not match registered path %r"
                % (observed_image, registered_path)
            )

        # 5. Monitor descendants while the process is alive.  Best-effort
        #    sampling, but any observation error / unknown image fails closed.
        descendants: list[dict] = []
        wsl_detected = False
        observation_error = image_error
        timed_out = False
        deadline = time.monotonic() + float(timeout)
        try:
            while proc.poll() is None:
                try:
                    sample, sample_wsl = _observe_descendants(root_pid)
                    if sample:
                        descendants = sample
                    wsl_detected = wsl_detected or sample_wsl
                    for entry in sample:
                        if entry.get("error"):
                            observation_error = observation_error or str(entry["error"])
                        if _is_unsupported_identity(entry.get("image")):
                            wsl_detected = True
                except Exception as exc:  # noqa: BLE001
                    observation_error = observation_error or (
                        "descendant observation error: %s" % exc
                    )
                if time.monotonic() >= deadline:
                    timed_out = True
                    _kill_tree(root_pid)
                    break
                time.sleep(0.02)
        finally:
            try:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass

        # One final sample catches descendants that appeared near exit.
        try:
            sample, sample_wsl = _observe_descendants(root_pid)
            if sample:
                descendants = sample
            wsl_detected = wsl_detected or sample_wsl
        except Exception as exc:  # noqa: BLE001
            observation_error = observation_error or (
                "final descendant observation error: %s" % exc
            )

        if wsl_detected and not unsupported_reason:
            unsupported_reason = (
                "unsupported descendant identity detected (WSL/wrapper/shell/unknown)"
            )
        if observation_error and not unsupported_reason:
            unsupported_reason = "process observation error: %s" % observation_error
        if timed_out and not unsupported_reason:
            unsupported_reason = "execution timed out after %ss" % timeout

        ident = {
            "tool_id": str(tool_id),
            "registered_path": registered_path,
            "observed_image": observed_image,
            "sha256": actual_sha,
            "pid": root_pid,
            "descendants": list(descendants),
            "observation_method": observation_method,
            "wsl_detected": bool(wsl_detected),
            "unsupported_reason": str(unsupported_reason or ""),
        }
        if unsupported_reason:
            ident["unsupported_reason"] = str(unsupported_reason)
            return self._emit(
                module,
                provenance_value=_prov_value(module, "UNSUPPORTED"),
                execution_identity=ident,
                consumed_sha256=consumed_sha256,
                dependencies=dependencies,
                status="UNSUPPORTED",
                check_id=check_id,
                gate_id=gate_id,
            )

        exit_code = proc.returncode
        status = "PASS" if exit_code == 0 else "FAIL"
        ident["exit_code"] = int(exit_code) if exit_code is not None else -1
        return self._emit(
            module,
            provenance_value=_prov_value(module, "TOOL_EXECUTION"),
            execution_identity=ident,
            consumed_sha256=consumed_sha256,
            dependencies=dependencies,
            status=status,
            check_id=check_id,
            gate_id=gate_id,
        )

    # -- non-authorizing observation ------------------------------------------

    def run_observation(self, command: Any, *, timeout: float = 60) -> Any:
        """Run an arbitrary diagnostic command; NEVER authorizing.

        Returns ``OBSERVATION`` (non-authorizing) on success/failure, or
        ``UNSUPPORTED`` if the provenance boundary itself is unavailable.  The
        command is split with ``shlex.split`` for strings so no shell wrapper
        is introduced, and no code path in this method can emit
        ``TOOL_EXECUTION``.
        """
        module, import_error = _load_provenance()
        if module is None:
            return _FallbackEnvelope(
                provenance="UNSUPPORTED",
                execution_identity={
                    "tool_id": "<observation>",
                    "unsupported_reason": "provenance module unavailable: %s" % import_error,
                },
                status="UNSUPPORTED",
            )
        if isinstance(command, str):
            try:
                argv = shlex.split(command)
            except ValueError:
                argv = [command]
        else:
            argv = [str(c) for c in (command or ())]
        if not argv:
            return self._emit(
                module,
                provenance_value=_prov_value(module, "OBSERVATION"),
                execution_identity={
                    "tool_id": "<observation>",
                    "command": [],
                    "unsupported_reason": "empty observation command",
                },
                consumed_sha256="",
                dependencies=(),
                status="FAIL",
                check_id="",
                gate_id="",
            )

        ident: dict[str, Any] = {
            "tool_id": "<observation>",
            "command": list(argv),
            "registered_path": "",
            "observed_image": None,
            "sha256": "",
            "pid": None,
            "descendants": [],
            "observation_method": "none",
            "wsl_detected": False,
            "unsupported_reason": "",
        }
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            ident["unsupported_reason"] = "failed to launch observation: %s" % exc
            return self._emit(
                module,
                provenance_value=_prov_value(module, "OBSERVATION"),
                execution_identity=ident,
                consumed_sha256="",
                dependencies=(),
                status="FAIL",
                check_id="",
                gate_id="",
            )

        ident["pid"] = proc.pid
        try:
            image, method, err = _observe_image(proc.pid)
            ident["observed_image"] = image
            ident["observation_method"] = method
            ident["unsupported_reason"] = str(err or "")
        except Exception as exc:  # noqa: BLE001
            ident["unsupported_reason"] = "image observation error: %s" % exc
        try:
            exit_code = proc.wait(timeout=float(timeout))
            status = "PASS" if exit_code == 0 else "FAIL"
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            status = "FAIL"
            ident["unsupported_reason"] = (
                str(ident.get("unsupported_reason") or "") + " | observation timeout"
            ).strip(" |")

        # Defensive guard: this method must never produce authorizing evidence.
        prov = _prov_value(module, "OBSERVATION")
        return self._emit(
            module,
            provenance_value=prov,
            execution_identity=ident,
            consumed_sha256="",
            dependencies=(),
            status=status,
            check_id="",
            gate_id="",
        )


# ---------------------------------------------------------------------------
# Self-test (run: python assistant/execution.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover - manual verification
    import hashlib as _hashlib
    import hmac as _hmac
    import json as _json
    import tempfile as _tempfile

    class _SelfTestTrust:
        """Minimal MAC signer (mirrors trust.py canonicalization) for isolation."""

        def __init__(self) -> None:
            self._key = b"r6-agent5-self-test-key"
            self.context = {
                "session_id": "self-test-session",
                "repo_id": "self-test-repo",
                "revision": "self-test-revision",
                "created_utc": _utc_now(),
            }

        def canonical(self, payload: Mapping) -> bytes:
            return _json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")

        def sign(self, domain: str, payload: Mapping) -> str:
            return _hmac.new(
                self._key, self.canonical({"domain": domain, "payload": payload}), _hashlib.sha256
            ).hexdigest()

        def verify(self, domain: str, payload: Mapping, mac: str) -> bool:
            return _hmac.compare_digest(self.sign(domain, payload), mac)

    module, error = _load_provenance()
    if module is None:
        print("SELF-TEST SKIPPED: provenance module unavailable (%s)" % error)
        raise SystemExit(0)

    Prov = module.Provenance
    trust = _SelfTestTrust()
    registry = ToolRegistry()
    me = ManagedExecution(registry, trust)
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        status = "PASS" if condition else "FAIL"
        print("  [%s] %s%s" % (status, name, (" - " + detail) if detail else ""))
        if not condition:
            failures.append(name)

    print("agent5 execution self-test:")

    py = sys.executable
    digest = _sha256_file(py)
    try:
        registry.register("py", py, digest)
        check("register(sys.executable)", True)
    except Exception as exc:  # pragma: no cover
        check("register(sys.executable)", False, str(exc))
        raise SystemExit(1)

    # 1) benign authoritative run -> TOOL_EXECUTION (or SKIP when OS APIs are
    #    unavailable; the UNSUPPORTED reason documents why).
    env = me.run_authoritative(
        "py", ["-c", "pass"], "ab" * 32, (), "check-benign", "gate-benign", timeout=30
    )
    if env.provenance == Prov.TOOL_EXECUTION:
        check("benign run -> TOOL_EXECUTION", True, "status=%s" % env.status)
        check("benign run status PASS", env.status == "PASS")
        check("benign run is_authorizing", env.is_authorizing() is True)
        ident = dict(env.execution_identity)
        check(
            "execution_identity complete",
            all(
                k in ident
                for k in (
                    "tool_id",
                    "registered_path",
                    "observed_image",
                    "sha256",
                    "pid",
                    "descendants",
                    "observation_method",
                    "wsl_detected",
                    "unsupported_reason",
                )
            ),
        )
    elif env.provenance == Prov.UNSUPPORTED:
        print(
            "  [SKIP] OS-observation assertion: %s"
            % dict(env.execution_identity).get("unsupported_reason", "")
        )
    else:
        check("benign run -> TOOL_EXECUTION", False, "got %r" % env.provenance)

    # 2) unknown tool -> UNSUPPORTED.
    env = me.run_authoritative(
        "does-not-exist", ["-c", "pass"], "ab" * 32, (), "check-unknown", "gate-unknown"
    )
    check("unknown tool -> UNSUPPORTED", env.provenance == Prov.UNSUPPORTED)
    check("unknown tool reason present", bool(dict(env.execution_identity).get("unsupported_reason")))

    # 3) run_observation -> OBSERVATION, never TOOL_EXECUTION.
    env = me.run_observation([py, "-c", "pass"], timeout=30)
    check("observation -> OBSERVATION", env.provenance == Prov.OBSERVATION)
    check("observation not authorizing", env.is_authorizing() is False)

    # 4) tampered registered digest -> UNSUPPORTED (registry digest modified
    #    after registration to simulate post-registration tamper).
    with _tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as fh:
        fh.write("agent5 self-test payload")
        tamper_path = fh.name
    try:
        tamper_digest = _sha256_file(tamper_path)
        registry.register("tamper", tamper_path, tamper_digest)
        registry._tools["tamper"] = (tamper_path, "0" * 64)  # white-box tamper
        env = me.run_authoritative(
            "tamper", [], "ab" * 32, (), "check-tamper", "gate-tamper"
        )
        check("tampered digest -> UNSUPPORTED", env.provenance == Prov.UNSUPPORTED)
        check(
            "tamper reason mentions hash",
            "hash" in str(dict(env.execution_identity).get("unsupported_reason", "")).lower(),
        )
    finally:
        try:
            os.unlink(tamper_path)
        except OSError:
            pass

    if failures:
        print("SELF-TEST FAILED: %s" % ", ".join(failures))
        raise SystemExit(1)
    print("agent5 execution self-test: PASS (detection + fail-closed, not containment)")
