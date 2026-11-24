"""R6.1 P0-B regression (unit): dynamic OS image identity classification + monitor.

The P0-B bypass was a ``chr()``-built ``wsl.exe``: the string guard sees no
literal token, so the old ``run_managed`` reported ``authorization_safe=True``.
These tests exercise the *image-based* classifier and the monitor with injected
process tables, so every construction trick (literal, hex, chr, env, unicode,
shell wrapper, alternate resolution) is covered deterministically.

Run:  <PY> -m pytest tests/r61_p0b_identity.py -q
"""
from __future__ import annotations

import os
import sys
import time
from types import SimpleNamespace

import pytest

from assistant import r61_identity as ident

PY = sys.executable


# --- image classification: every P0-B construction trick -------------------

def test_literal_wsl_image_is_unsupported():
    ok, reason = ident.is_unsupported_image(r"C:\Windows\System32\wsl.exe")
    assert ok is True
    assert "wsl" in reason.lower()


def test_hex_constructed_wsl_image_is_unsupported():
    name = bytes.fromhex("77736c2e657865").decode("ascii")  # wsl.exe
    assert name == "wsl.exe"
    ok, reason = ident.is_unsupported_image(name)
    assert ok is True


def test_chr_constructed_wsl_image_is_unsupported():
    name = "".join(chr(x) for x in [119, 115, 108, 46, 101, 120, 101])
    assert name == "wsl.exe"  # built at runtime, no literal token in the command
    ok, reason = ident.is_unsupported_image(name)
    assert ok is True


def test_env_constructed_executable_is_unsupported(monkeypatch):
    monkeypatch.setenv("R61_WSLCMD", "wsl.exe")
    name = os.environ["R61_WSLCMD"]
    ok, reason = ident.is_unsupported_image(r"C:\Windows\System32\\" + name)
    assert ok is True


@pytest.mark.parametrize("image", [
    "cmd.exe", "powershell.exe", "pwsh.exe", "bash.exe", "sh.exe",
    r"C:\Windows\System32\cmd.exe", r"C:\work\run.bat", "launch.cmd",
    "script.ps1", "wslhost.exe", "wslg.exe",
])
def test_shell_and_wrapper_images_are_unsupported(image):
    ok, reason = ident.is_unsupported_image(image)
    assert ok is True, image


def test_unicode_fullwidth_wsl_variant_is_unsupported():
    # Fullwidth ｗｓｌ.exe normalises (NFKC) to wsl.exe.
    ok, reason = ident.is_unsupported_image("\uff57\uff53\uff4c.exe")
    assert ok is True


@pytest.mark.parametrize("image", [
    "wsl", "WSL.EXE", r"C:\Windows\System32\WSL.EXE", "wsl~1.exe",
    r"C:\Windows\Apps\wsl.exe",
])
def test_alternate_resolution_variants_are_unsupported(image):
    ok, reason = ident.is_unsupported_image(image)
    assert ok is True, image


@pytest.mark.parametrize("image", [
    None, "", "  ", "<unreadable>",
])
def test_unreadable_or_missing_image_fails_closed(image):
    ok, reason = ident.is_unsupported_image(image)
    assert ok is True, image


@pytest.mark.parametrize("image", [
    sys.executable, "yosys.exe", "sta.exe", "/usr/bin/python3", "design.v",
])
def test_benign_tool_images_are_supported(image):
    ok, reason = ident.is_unsupported_image(image)
    assert ok is False, (image, reason)


# --- monitor: injection-based descendant observation -----------------------

def _monitor(image, *, root=PY, error=None, descendants_error=None):
    def descendants(_root):
        if descendants_error:
            raise RuntimeError(descendants_error)
        return [{"pid": 424242, "image": image, "error": error}]

    return ident.ProcessIdentityMonitor(
        image_fn=lambda pid: (root, None),
        descendants_fn=descendants,
        roots=[7777],
        watch_registry=False,
    )


@pytest.mark.parametrize("image", [
    "wsl.exe", r"C:\Windows\System32\wsl.exe", "wslhost.exe",
    "cmd.exe", "powershell.exe", "run.bat", "wsl.exe (hex-built)",
    "\uff57\uff53\uff4c.exe", "wsl~1.exe", None,
])
def test_monitor_flags_injected_unsupported_descendant(image):
    m = _monitor(image)
    m.start()
    time.sleep(0.03)
    m.stop()
    ev = m.evidence()
    assert ev["unsupported"] is True, image
    assert ev["detected"] is True
    assert ev["note"].endswith("NOT containment")


def test_monitor_keeps_benign_descendant_supported():
    m = _monitor("yosys.exe")
    m.start()
    time.sleep(0.03)
    m.stop()
    ev = m.evidence()
    assert ev["unsupported"] is False
    assert any(o.get("pid") == 424242 for o in ev["observations"])


def test_monitor_fails_closed_on_observation_error():
    m = _monitor(PY, descendants_error="Toolhelp32 snapshot denied")
    m.poll_once()
    assert m.unsupported is True
    assert any("snapshot denied" in r for r in m.evidence()["reasons"])


def test_monitor_fails_closed_on_unreadable_image():
    m = _monitor(PY, error="QueryFullProcessImageNameW failed")
    m.poll_once()
    assert m.unsupported is True


# --- integration wrapper contract ------------------------------------------

def test_monitored_run_managed_calls_run_fn_and_fails_closed(monkeypatch):
    calls = []

    class FakeResult:
        pid = 9999
        exit_code = 0
        containment = "clean"
        containment_reason = "clean_exit"
        wsl_uncontained = False
        error = ""
        authorization_safe = True  # mutable: mirrors RunResult's settable gate

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResult()

    monkeypatch.setattr(ident, "IMAGE_FN", lambda pid: (PY, None))
    monkeypatch.setattr(
        ident, "DESCENDANTS_FN",
        lambda root: [{"pid": 123, "image": "wsl.exe", "error": None}],
    )
    result = ident.monitored_run_managed(fake_run, "arg", kw="value")

    assert calls == [(("arg",), {"kw": "value"})]  # existing monkeypatch tests see the call
    assert isinstance(result.identity_evidence, dict)
    assert result.wsl_uncontained is True
    assert result.containment == "unsupported"
    assert result.authorization_safe is False


def test_monitored_run_managed_never_raises_on_monitor_failure(monkeypatch):
    class FakeResult:
        pid = 9999
        containment = "clean"
        containment_reason = "clean_exit"
        wsl_uncontained = False
        error = ""
        authorization_safe = True  # mutable: mirrors RunResult's settable gate

    def boom(_root):
        raise RuntimeError("observation blocked")

    monkeypatch.setattr(ident, "IMAGE_FN", lambda pid: (PY, None))
    monkeypatch.setattr(ident, "DESCENDANTS_FN", boom)

    result = ident.monitored_run_managed(lambda: FakeResult())
    assert result.authorization_safe is False
    assert result.wsl_uncontained is True
    assert result.identity_evidence["unsupported"] is True


def test_monitored_run_managed_propagates_run_fn_failure():
    def failing_run():
        raise ValueError("tool exploded")

    with pytest.raises(ValueError):
        ident.monitored_run_managed(failing_run)
