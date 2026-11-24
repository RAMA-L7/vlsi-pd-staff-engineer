"""R6.1 P0-B -- dynamic OS process-identity observation (DETECTION, not containment).

This module is the authoritative execution-identity boundary for managed runs.
It does NOT rely on the literal/encoded command strings that the string guard
(``r6_authority.decode_aware_execution_risk``) inspects: a ``chr()``-built
``wsl.exe`` has no literal token, yet the OS still launches a *real* ``wsl.exe``
image.  We therefore observe the OS process table while a managed run is live
and recursively resolve descendants of the launched PID by parent PID:

* Win32 -- ``CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS)`` +
  ``Process32FirstW``/``Process32NextW`` for the PPID map, and
  ``OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)`` +
  ``QueryFullProcessImageNameW`` for full image paths (Agent 5's
  ``assistant.execution`` helpers are reused where available).
* Linux -- ``/proc/<pid>/stat`` (PPID) + ``/proc/<pid>/exe`` (image).

Any descendant (or the direct child) whose image is an unsupported shell /
wrapper / WSL identity -- or whose image cannot be read at all -- makes the
monitor FAIL CLOSED.  This is **detection + fail-closed classification**, NOT
containment: no code here kills or prevents a process, and WSL2 Linux
descendants remain unconfirmable from the host.  Downstream consumers must gate
on ``authorization_safe`` / ``wsl_uncontained``.

Public integration surface:
    ``ProcessIdentityMonitor``  -- start/stop/poll_once/evidence
    ``monitored_run_managed(run_fn, *args, **kwargs)`` -- wrapper contract
    ``apply_identity_finding(result, monitor)`` -- fail-closed result mutation
    ``register_root`` / ``unregister_root`` / ``active_roots`` -- shared registry

All observation helpers are monkeypatchable module globals (``IMAGE_FN``,
``DESCENDANTS_FN``) so tests can inject deterministic fake process tables.
"""
from __future__ import annotations

import os
import threading
import time
import unicodedata
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

__all__ = [
    "CONTAINMENT_UNSUPPORTED",
    "REASON_DYNAMIC_IDENTITY",
    "REASON_WSL_UNCONFIRMABLE",
    "ProcessIdentityMonitor",
    "is_unsupported_image",
    "apply_identity_finding",
    "monitored_run_managed",
    "register_root",
    "unregister_root",
    "active_roots",
    "clear_roots",
    "IMAGE_FN",
    "DESCENDANTS_FN",
]

# Keep these literal values identical to assistant.runner's constants.  We do
# not import runner here (runner imports this module) to avoid a cycle.
CONTAINMENT_UNSUPPORTED = "unsupported"
REASON_WSL_UNCONFIRMABLE = "wsl_linux_descendants_unconfirmable"
REASON_DYNAMIC_IDENTITY = "dynamic_execution_identity_unsupported"

# Reasons that already describe a non-authorizing path.  They are preserved
# verbatim so the string gate's canonical machine token survives; a stale
# "clean_exit" is replaced by the dynamic finding's reason.
_PRESERVE_REASONS = frozenset(
    {
        REASON_WSL_UNCONFIRMABLE,
        "uncontainable_subprocess_tree",
        "wrapper_exit_orphan",
        "termination_unconfirmed",
        "spawn_failed",
        "cooperative_only",
        "deadline_expired_at_entry",
        "no_finite_deadline",
    }
)

_IS_WINDOWS = os.name == "nt"
_MAX_OBSERVATIONS = 400

# Reuse Agent 5's OS helpers where they are available; fall back to a local
# implementation below when the module is not importable (staged integration).
try:  # pragma: no cover - import layout varies by harness
    from . import execution as _exec  # type: ignore
except ImportError:  # pragma: no cover
    try:
        import execution as _exec  # type: ignore
    except ImportError:  # pragma: no cover
        _exec = None  # type: ignore


class IdentityObservationError(RuntimeError):
    """Raised when the OS process table/image cannot be observed reliably."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


# ---------------------------------------------------------------------------
# Image identity classification (normalisation, unicode, 8.3, script wrappers)
# ---------------------------------------------------------------------------

#: Basenames that are shells / wrappers / WSL launchers and therefore never a
#: legal authoritative tool identity.  ``.bat``/``.cmd``/``.ps1``/``.sh`` and
#: any basename containing ``wsl`` are handled by suffix/substring rules below.
_UNSUPPORTED_BASENAMES = frozenset(
    {
        "wsl", "wsl.exe", "wslg", "wslg.exe", "wslconfig", "wslconfig.exe",
        "wslinstaller", "wslinstaller.exe", "wslhost", "wslhost.exe",
        "bash", "bash.exe", "sh", "sh.exe", "dash", "dash.exe",
        "zsh", "zsh.exe", "ksh", "ksh.exe", "fish", "fish.exe",
        "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
        "powershell_ise", "powershell_ise.exe", "conemu", "conemu.exe",
        "mintty", "mintty.exe", "cygwin", "cygwin.exe",
    }
)

_SCRIPT_SUFFIXES = (
    ".bat", ".cmd", ".ps1", ".sh", ".bash", ".vbs", ".js", ".wsf", ".hta",
    ".pyw",
)

# Sentinel strings used by callers to denote an image that could not be read.
_UNREADABLE_SENTINELS = frozenset(
    {"<unreadable>", "<absent>", "<unknown>", "unknown", "n/a", "<none>"}
)

# Common single-character homoglyphs used to spell a forbidden name with
# non-ASCII characters (e.g. fullwidth ｗｓｌ.exe).  NFKC below handles most.
_HOMOGLYPHS = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "і": "i",
    "ѕ": "s", "у": "y", "ԁ": "d", "һ": "h", "κ": "k", "ο": "o", "ρ": "p",
    "ɑ": "a", "е": "e", "ѕ": "s", "ⅽ": "c", "ⅿ": "m", "ⅾ": "d",
}


def _normalise_image(image: Any) -> tuple[str, str]:
    """Return ``(normalised_basename, normalised_full_path)``."""
    raw = str(image or "")
    folded = unicodedata.normalize("NFKC", raw)
    folded = "".join(_HOMOGLYPHS.get(ch, ch) for ch in folded)
    full = folded.replace("\\", "/").lower()
    base = full.rsplit("/", 1)[-1].strip().lstrip(".")
    # 8.3 short names: wsl~1.exe -> wsl~1 (strip a trailing extension first).
    if "." in base and not base.endswith(_SCRIPT_SUFFIXES):
        base = base.rsplit(".", 1)[0]
    return base, full


def is_unsupported_image(image: Any) -> tuple[bool, str]:
    """Classify an OS image path.

    Returns ``(unsupported, reason)``.  ``image`` that is missing/unreadable
    fails closed (``unsupported=True``, reason describing the uncertainty).
    """
    if not image or str(image).strip().lower() in _UNREADABLE_SENTINELS:
        return True, "image_missing_or_unreadable"
    base, full = _normalise_image(image)
    if not base:
        return True, "image_missing_or_unreadable"
    if base in _UNSUPPORTED_BASENAMES:
        return True, "unsupported_identity:" + base
    if "wsl" in base:
        return True, "wsl_identity:" + base
    if base.endswith(_SCRIPT_SUFFIXES):
        return True, "script_wrapper:" + base
    # A path under System32/WSL whose *directory* names a WSL component.
    if "wsl" in full and ("/windows/system32/" in full or "/windows/apps/" in full):
        return True, "wsl_path:" + full
    return False, ""


# ---------------------------------------------------------------------------
# OS observation primitives (reuse assistant.execution where possible)
# ---------------------------------------------------------------------------


def default_image_for_pid(pid: int) -> tuple[str | None, str | None]:
    """Return ``(image, error)`` for *pid* (full path where available)."""
    pid = int(pid)
    if _exec is not None and hasattr(_exec, "_observe_image"):
        try:
            image, _method, err = _exec._observe_image(pid)
            return (image or None), err
        except Exception as exc:  # noqa: BLE001 - fail closed upstream
            return None, "image observation error: %s" % exc
    if _IS_WINDOWS:  # pragma: no cover - platform-dependent
        try:  # local ctypes fallback
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            k32.OpenProcess.restype = ctypes.c_ssize_t
            k32.QueryFullProcessImageNameW.argtypes = [
                ctypes.c_ssize_t, wintypes.DWORD, wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
            ]
            k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            k32.CloseHandle.argtypes = [ctypes.c_ssize_t]
            handle = k32.OpenProcess(0x1000, False, pid)
            if not handle or handle == -1:
                return None, "OpenProcess failed (err=%d)" % ctypes.get_last_error()
            try:
                buf = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(buf))
                if not k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    return None, "QueryFullProcessImageNameW failed (err=%d)" % ctypes.get_last_error()
                return (buf.value or None), None
            finally:
                k32.CloseHandle(handle)
        except Exception as exc:  # noqa: BLE001
            return None, "image observation error: %s" % exc
    try:  # POSIX fallback
        return os.readlink("/proc/%d/exe" % pid), None
    except OSError as exc:
        return None, "readlink /proc/<pid>/exe failed: %s" % exc


def _local_snapshot() -> dict[int, int]:
    """Return ``{pid: ppid}`` from the OS process table (no external process)."""
    if _IS_WINDOWS:  # pragma: no cover - platform-dependent
        import ctypes
        from ctypes import wintypes

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

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k32.CreateToolhelp32Snapshot.restype = ctypes.c_ssize_t
        k32.Process32FirstW.argtypes = [ctypes.c_ssize_t, ctypes.POINTER(_PROCESSENTRY32W)]
        k32.Process32FirstW.restype = wintypes.BOOL
        k32.Process32NextW.argtypes = [ctypes.c_ssize_t, ctypes.POINTER(_PROCESSENTRY32W)]
        k32.Process32NextW.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [ctypes.c_ssize_t]
        snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
        if not snap or snap == -1:
            raise IdentityObservationError(
                "CreateToolhelp32Snapshot failed (err=%d)" % ctypes.get_last_error()
            )
        out: dict[int, int] = {}
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                out[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                ok = k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)
        return out

    out: dict[int, int] = {}
    try:
        names = os.listdir("/proc")
    except OSError as exc:
        raise IdentityObservationError("cannot list /proc: %s" % exc) from exc
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % name, "r", encoding="utf-8", errors="replace") as fh:
                data = fh.read()
            close = data.rfind(")")
            parts = data[close + 1:].strip().split()
            out[int(name)] = int(parts[1])  # state=parts[0], ppid=parts[1]
        except (OSError, ValueError, IndexError):
            continue
    return out


def _local_descendants(root_pid: int) -> list[dict]:
    """Local descendant walk (used when execution.py helpers are unavailable)."""
    snapshot = _local_snapshot()
    children: dict[int, list[int]] = {}
    for pid, ppid in snapshot.items():
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
            image, err = default_image_for_pid(child)
            out.append({"pid": child, "image": image, "error": err})
            queue.append(child)
    return out


def default_descendants(root_pid: int) -> list[dict]:
    """Return ``[{'pid', 'image', 'error'}, ...]`` for descendants of *root_pid*.

    Reuses ``assistant.execution._observe_descendants`` (itself built from
    Toolhelp32 + QueryFullProcessImageNameW / ``/proc``) where available.
    """
    root_pid = int(root_pid)
    if _exec is not None and hasattr(_exec, "_observe_descendants"):
        try:
            entries, _wsl = _exec._observe_descendants(root_pid)
            return [
                {
                    "pid": e.get("pid"),
                    "image": e.get("image"),
                    "error": e.get("error"),
                }
                for e in entries
            ]
        except Exception as exc:  # noqa: BLE001 - fall through to local impl
            # A snapshot failure is an observation failure: propagate it so the
            # monitor fails closed rather than silently reporting "no children".
            raise IdentityObservationError(
                "descendant observation failed: %s" % exc
            ) from exc
    return _local_descendants(root_pid)


# Monkeypatchable observation hooks (module level).  The monitor resolves these
# at call time so a test can replace them after the monitor was constructed.
IMAGE_FN: Callable[[int], tuple[str | None, str | None]] = default_image_for_pid
DESCENDANTS_FN: Callable[[int], list[dict]] = default_descendants


# ---------------------------------------------------------------------------
# Shared root registry (lets an external wrapper observe run_managed's child)
# ---------------------------------------------------------------------------

_active_roots: set[int] = set()
_roots_lock = threading.Lock()


def register_root(pid: int) -> None:
    with _roots_lock:
        _active_roots.add(int(pid))


def unregister_root(pid: int) -> None:
    with _roots_lock:
        _active_roots.discard(int(pid))


def active_roots() -> set[int]:
    with _roots_lock:
        return set(_active_roots)


def clear_roots() -> None:
    """Test helper: drop every registered root."""
    with _roots_lock:
        _active_roots.clear()


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


class ProcessIdentityMonitor:
    """Observe process identity during a run and fail closed on uncertainty.

    This is DETECTION + FAIL-CLOSED, not containment: it never kills or blocks
    a process.  ``poll_once`` is safe to call manually (e.g. from a runner's
    existing wait loop); ``start``/``stop`` run the same poll on a daemon
    thread at ``interval`` seconds and perform a final poll on stop.
    """

    def __init__(
        self,
        *,
        interval: float = 0.01,
        image_fn: Callable[[int], tuple[str | None, str | None]] | None = None,
        descendants_fn: Callable[[int], list[dict]] | None = None,
        roots: Iterable[int] = (),
        watch_registry: bool = True,
    ) -> None:
        if interval <= 0:
            interval = 0.01
        self.interval = float(interval)
        self._image_fn = image_fn
        self._descendants_fn = descendants_fn
        self._roots: set[int] = {int(p) for p in roots}
        self.watch_registry = bool(watch_registry)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._observations: list[dict] = []
        self._reasons: list[str] = []
        self._unsupported = False
        self._samples = 0
        self._started_utc = _utc_now()
        self._root_images: dict[int, str | None] = {}

    # -- configuration -------------------------------------------------------

    def add_root(self, pid: int, image: str | None = None) -> None:
        pid = int(pid)
        with self._lock:
            self._roots.add(pid)
            if image is not None:
                self._root_images[pid] = image

    def discard_root(self, pid: int) -> None:
        with self._lock:
            self._roots.discard(int(pid))

    def fail_closed(self, reason: str) -> None:
        """Explicitly mark the run unsupported (e.g. monitor start/stop error)."""
        with self._lock:
            self._unsupported = True
            text = str(reason or "identity observation error")
            if text not in self._reasons:
                self._reasons.append(text)
            self._observations.append(
                {
                    "pid": None,
                    "image": None,
                    "error": text,
                    "unsupported": True,
                    "reason": "observation_error",
                }
            )
            self._trim()

    def _trim(self) -> None:
        if len(self._observations) > _MAX_OBSERVATIONS:
            self._observations = self._observations[-_MAX_OBSERVATIONS:]

    # -- observation ---------------------------------------------------------

    def _image(self, pid: int) -> tuple[str | None, str | None]:
        fn = self._image_fn or globals().get("IMAGE_FN", default_image_for_pid)
        return fn(int(pid))

    def _descendants(self, root_pid: int) -> list[dict]:
        fn = self._descendants_fn or globals().get("DESCENDANTS_FN", default_descendants)
        result = fn(int(root_pid))
        if result is None:
            return []
        return list(result)

    def _record(self, entry: dict) -> bool:
        """Record one observed image; return True when it fails closed."""
        image = entry.get("image")
        err = entry.get("error")
        unsupported, reason = is_unsupported_image(image)
        if err and not unsupported:
            unsupported, reason = True, "image_error:" + str(err)[:200]
        entry = dict(entry)
        entry["unsupported"] = bool(unsupported)
        entry["reason"] = reason
        with self._lock:
            self._observations.append(entry)
            self._trim()
            if unsupported:
                self._unsupported = True
                label = reason or str(err or "unknown image")
                if label not in self._reasons:
                    self._reasons.append(label)
        return bool(unsupported)

    def poll_once(self) -> bool:
        """Perform one observation cycle; returns True if unsupported seen."""
        with self._lock:
            roots = set(self._roots)
        if self.watch_registry:
            roots |= active_roots()
        if not roots:
            with self._lock:
                self._samples += 1
            return self._unsupported
        for root in sorted(roots):
            image, err = None, None
            with self._lock:
                known = self._root_images.get(root)
            if known is not None:
                image = known
            else:
                try:
                    image, err = self._image(root)
                    with self._lock:
                        self._root_images[root] = image
                except Exception as exc:  # noqa: BLE001 - observation error
                    err = "image observation error: %s" % exc
                    image = None
            self._record(
                {"pid": root, "image": image, "error": err, "role": "root"}
            )
            try:
                entries = self._descendants(root)
            except Exception as exc:  # noqa: BLE001 - fail closed on error
                self.fail_closed("descendant observation error: %s" % exc)
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    self.fail_closed("malformed descendant observation: %r" % (entry,))
                    continue
                child = entry.get("pid")
                child_image = entry.get("image")
                child_err = entry.get("error")
                self._record(
                    {
                        "pid": child,
                        "image": child_image,
                        "error": child_err,
                        "role": "descendant",
                    }
                )
        with self._lock:
            self._samples += 1
        return self._unsupported

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()

        def _loop() -> None:
            while not self._stop.is_set():
                try:
                    self.poll_once()
                except Exception as exc:  # noqa: BLE001 - never kill the thread
                    self.fail_closed("monitor poll error: %s" % exc)
                self._stop.wait(self.interval)

        self._thread = threading.Thread(
            target=_loop, name="r61-identity-monitor", daemon=True
        )
        self._thread.start()
        # One synchronous sample closes the launch->first-poll race.
        try:
            self.poll_once()
        except Exception as exc:  # noqa: BLE001
            self.fail_closed("monitor initial poll error: %s" % exc)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            try:
                thread.join(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
        self._thread = None
        # Final sample catches a descendant that appeared near wrapper exit.
        try:
            self.poll_once()
        except Exception as exc:  # noqa: BLE001
            self.fail_closed("monitor final poll error: %s" % exc)

    def __enter__(self) -> "ProcessIdentityMonitor":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    # -- results -------------------------------------------------------------

    @property
    def unsupported(self) -> bool:
        with self._lock:
            return self._unsupported

    def evidence(self) -> dict:
        with self._lock:
            observations = [dict(o) for o in self._observations]
            reasons = list(self._reasons)
            unsupported = self._unsupported
            samples = self._samples
            roots = sorted(self._roots)
        detected_images = sorted(
            {
                str(o.get("image"))
                for o in observations
                if o.get("unsupported") and o.get("image")
            }
        )
        return {
            "detected": bool(unsupported),
            "unsupported": bool(unsupported),
            "root_pids": roots,
            "observations": observations,
            "detected_images": detected_images,
            "reasons": reasons,
            "reason": reasons[0] if reasons else "",
            "samples": samples,
            "observation_method": (
                "win32:Toolhelp32+QueryFullProcessImageNameW"
                if _IS_WINDOWS else "linux:/proc/<pid>/stat+exe"
            ),
            "observed_utc": self._started_utc,
            "note": "dynamic OS identity detection + fail-closed; NOT containment",
        }


# ---------------------------------------------------------------------------
# Fail-closed result mutation + integration wrapper
# ---------------------------------------------------------------------------


def apply_identity_finding(result: Any, monitor: ProcessIdentityMonitor | None) -> Any:
    """Attach ``identity_evidence`` and fail closed if the monitor found trouble.

    Never raises on behalf of the monitor.  The three flags mandated by the
    R6.1 contract are set: ``wsl_uncontained=True``, ``authorization_safe=False``
    and ``containment='unsupported'``.  ``RunResult.authorization_safe`` has a
    setter that latches a False override so the property cannot be recomputed
    back to True by a later caller.
    """
    evidence = monitor.evidence() if monitor is not None else {
        "detected": True,
        "unsupported": True,
        "root_pids": [],
        "observations": [],
        "detected_images": [],
        "reasons": ["no identity monitor available"],
        "reason": "no identity monitor available",
        "samples": 0,
        "observation_method": "none",
        "observed_utc": _utc_now(),
        "note": "dynamic OS identity unavailable -> fail closed; NOT containment",
    }
    try:
        setattr(result, "identity_evidence", evidence)
    except Exception:  # noqa: BLE001 - best effort on exotic result objects
        pass
    if not evidence.get("unsupported"):
        return result
    for attr, value in (
        ("wsl_uncontained", True),
        ("containment", CONTAINMENT_UNSUPPORTED),
        ("authorization_safe", False),
    ):
        try:
            setattr(result, attr, value)
        except Exception:  # noqa: BLE001
            pass
    # Defensive fallback for RunResult-like objects whose authorization_safe is
    # a computed property: latch the same veto the real setter would latch.
    try:
        setattr(result, "_auth_safe_override", False)
    except Exception:  # noqa: BLE001
        pass
    # Preserve a more specific NON-authorizing reason already stamped by the
    # string gate (e.g. wsl_linux_descendants_unconfirmable /
    # uncontainable_subprocess_tree) so existing consumers keep their canonical
    # machine token.  A stale "clean_exit" is replaced by this finding.
    old_reason = str(getattr(result, "containment_reason", "") or "")
    if old_reason not in _PRESERVE_REASONS:
        try:
            setattr(result, "containment_reason", REASON_DYNAMIC_IDENTITY)
        except Exception:  # noqa: BLE001
            pass
    # Append a machine-readable marker to error/log detail when possible.
    reason = str(evidence.get("reason") or REASON_DYNAMIC_IDENTITY)
    old_error = str(getattr(result, "error", "") or "")
    marker = f"{REASON_DYNAMIC_IDENTITY}: {reason}"
    if marker not in old_error:
        try:
            setattr(result, "error", (old_error + "; " if old_error else "") + marker)
        except Exception:  # noqa: BLE001
            pass
    return result


def monitored_run_managed(run_fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Wrapper contract: monitor identity for the duration of ``run_fn``.

    ``run_fn`` is called exactly as given (normally ``assistant.runner
    .run_managed``) so monkeypatch-based tests still observe the call.  The
    monitor is started before and stopped after the call; a real
    ``run_managed`` publishes its launched PID through :func:`register_root`,
    and the wrapper additionally takes a final sample using ``result.pid`` so
    it also works with a stubbed ``run_fn``.

    Monitor failures NEVER propagate: they are recorded as unsupported
    evidence and the returned result is failed closed via
    :func:`apply_identity_finding`.
    """
    monitor = ProcessIdentityMonitor()
    result: Any = None
    try:
        try:
            monitor.start()
        except Exception as exc:  # noqa: BLE001
            monitor.fail_closed("monitor start error: %s" % exc)
        try:
            result = run_fn(*args, **kwargs)
        finally:
            try:
                monitor.stop()
            except Exception as exc:  # noqa: BLE001
                monitor.fail_closed("monitor stop error: %s" % exc)
    except Exception:
        # A run_fn failure is the caller's business; propagate it unchanged.
        raise
    pid = getattr(result, "pid", None)
    if isinstance(pid, int) and pid > 0:
        monitor.add_root(pid)
        try:
            monitor.poll_once()
        except Exception as exc:  # noqa: BLE001
            monitor.fail_closed("final identity sample error: %s" % exc)
    if result is not None:
        apply_identity_finding(result, monitor)
    return result
