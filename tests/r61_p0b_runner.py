"""R6.1 P0-B regression (integration): run_managed dynamic identity fail-closed.

Run:  <PY> -m pytest tests/r61_p0b_runner.py -q
"""
from __future__ import annotations

import os
import shutil
import sys
import time

import pytest

from assistant import r61_identity as ident
from assistant import runner
from assistant.runner import (
    CONTAINMENT_CLEAN,
    CONTAINMENT_UNSUPPORTED,
    run_managed,
)

PY = sys.executable


def _run(command, name, **kw):
    kw.setdefault("deadline", time.monotonic() + 15.0)
    return run_managed(command, log_dir=kw.pop("_log_dir", None) or _tmp_log(name),
                       name=name, **kw)


def _tmp_log(name):
    import tempfile
    return os.path.join(tempfile.mkdtemp(prefix="r61_p0b_"), "logs")


def _inject(monkeypatch, image, *, root=PY, error=None):
    """Inject a fake OS process table: root=benign python, one child=image."""
    monkeypatch.setattr(ident, "IMAGE_FN", lambda pid: (root, None))
    monkeypatch.setattr(
        ident, "DESCENDANTS_FN",
        lambda _root: [{"pid": 31337, "image": image, "error": error}],
    )


# --- benign control: no false positives on normal Python tools --------------

def test_benign_managed_run_stays_authorized():
    result = run_managed(
        [PY, "-c", "print('r61 benign')"],
        log_dir=_tmp_log("benign"), name="r61-benign",
        deadline=time.monotonic() + 15.0,
    )
    assert result.exit_code == 0
    assert result.containment == CONTAINMENT_CLEAN
    assert result.wsl_uncontained is False
    assert result.authorization_safe is True
    assert result.ran is True
    assert isinstance(result.identity_evidence, dict)
    assert result.identity_evidence["unsupported"] is False


# --- every unsupported construction trick fails closed ----------------------

@pytest.mark.parametrize("image", [
    r"C:\Windows\System32\wsl.exe",                   # literal WSL
    bytes.fromhex("77736c2e657865").decode("ascii"),  # hex-built WSL
    "".join(chr(x) for x in [119, 115, 108, 46, 101, 120, 101]),  # chr-built WSL
    "wsl.exe",                                        # env-constructed executable
    "bash.exe",                                       # nested shell / Linux shim
    "cmd.exe",                                        # CMD wrapper
    "powershell.exe",                                 # PowerShell wrapper
    r"C:\work\run.bat",                               # batch script wrapper
    "\uff57\uff53\uff4c.exe",                         # unicode fullwidth variant
    "wsl~1.exe",                                      # alternate 8.3 resolution
])
def test_injected_unsupported_identity_never_authorizes(monkeypatch, image):
    _inject(monkeypatch, image)
    result = run_managed(
        [PY, "-c", "print('benign command')"],
        log_dir=_tmp_log("inject"), name="r61-inject",
        deadline=time.monotonic() + 15.0,
    )
    assert result.containment == CONTAINMENT_UNSUPPORTED
    assert result.wsl_uncontained is True
    assert result.authorization_safe is False
    assert result.ran is False
    assert result.identity_evidence["unsupported"] is True
    assert result.identity_evidence["detected_images"], "observed image must be recorded"


@pytest.mark.parametrize("read_error", [
    "image unreadable", "QueryFullProcessImageNameW failed (err=5)",
])
def test_unreadable_image_fails_closed(monkeypatch, read_error):
    # An image that cannot be read is reported as image=None + an error from
    # the OS observation primitive; both must fail closed.
    _inject(monkeypatch, None, error=read_error)
    result = run_managed(
        [PY, "-c", "print('benign')"],
        log_dir=_tmp_log("unreadable"), name="r61-unreadable",
        deadline=time.monotonic() + 15.0,
    )
    assert result.authorization_safe is False
    assert result.containment == CONTAINMENT_UNSUPPORTED


def test_observation_error_fails_closed_in_run_managed(monkeypatch):
    def boom(_root):
        raise RuntimeError("process table unavailable")

    monkeypatch.setattr(ident, "IMAGE_FN", lambda pid: (PY, None))
    monkeypatch.setattr(ident, "DESCENDANTS_FN", boom)
    result = run_managed(
        [PY, "-c", "print('benign')"],
        log_dir=_tmp_log("obserr"), name="r61-obserr",
        deadline=time.monotonic() + 15.0,
    )
    assert result.authorization_safe is False
    assert result.wsl_uncontained is True
    assert result.containment == CONTAINMENT_UNSUPPORTED
    assert result.identity_evidence["unsupported"] is True


def test_authorization_safe_false_latch_cannot_be_recomputed():
    result = run_managed(
        [PY, "-c", "print('benign')"],
        log_dir=_tmp_log("latch"), name="r61-latch",
        deadline=time.monotonic() + 15.0,
    )
    assert result.authorization_safe is True
    result.authorization_safe = False
    # Even after a caller resets the visible fields, the latch holds.
    result.containment = CONTAINMENT_CLEAN
    result.wsl_uncontained = False
    result.error = ""
    assert result.authorization_safe is False


# --- the original P0-B reproduction against real OS observation -------------

@pytest.mark.skipif(shutil.which("wsl.exe") is None, reason="WSL not installed")
def test_real_chr_built_wsl_executable_fails_closed():
    """Exact P0-B attack: no literal 'wsl.exe' token anywhere in argv."""
    chr_wsl = "''.join(chr(x) for x in [119,115,108,46,101,120,101])"
    inner = (
        "import subprocess;"
        "subprocess.run([%s,'-d','Ubuntu-24.04','--','/bin/true'])" % chr_wsl
    )
    cmd = [PY, "-c", inner]
    assert "wsl.exe" not in " ".join(cmd)  # proves the string guard is blind

    result = run_managed(cmd, log_dir=_tmp_log("real-chr"), name="r61-real-chr",
                         deadline=time.monotonic() + 25.0)
    assert result.containment == CONTAINMENT_UNSUPPORTED
    assert result.wsl_uncontained is True
    assert result.authorization_safe is False
    assert result.identity_evidence["unsupported"] is True
    assert any("wsl" in img.lower() for img in result.identity_evidence["detected_images"])


# --- wrapper contract around the REAL run_managed ---------------------------

def test_wrapper_around_real_run_managed_benign_is_authorized():
    result = ident.monitored_run_managed(
        runner.run_managed, [PY, "-c", "print('wrapped benign')"],
        log_dir=_tmp_log("wrapped"), name="r61-wrapped",
        deadline=time.monotonic() + 15.0,
    )
    assert result.authorization_safe is True
    assert result.identity_evidence["unsupported"] is False
