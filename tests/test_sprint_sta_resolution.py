"""Targeted path-resolution tests for the Phase 1 OpenSTA override fix.

The resolver lives inside ``regression/run_regression.ps1``.  These tests
extract that self-contained block verbatim and execute it with PowerShell,
injecting an existence predicate so each outcome is deterministic and does not
depend on WSL or on which EDA tools happen to be installed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "regression" / "run_regression.ps1"
STA_STAGE = REPO / "regression" / "stages" / "12_sta.sh"

BEGIN = "# --- STA path resolution (BEGIN)"
END = "# --- STA path resolution (END)"


def _powershell() -> str:
    exe = shutil.which("powershell.exe") or shutil.which("powershell")
    if exe:
        return exe
    fallback = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    if fallback.is_file():
        return str(fallback)
    pytest.fail("PowerShell executable not available; cannot exercise the shipped resolver")


def _resolver_block() -> str:
    source = SCRIPT.read_text(encoding="utf-8")
    assert BEGIN in source, "STA resolver BEGIN marker missing from shipped script"
    assert END in source, "STA resolver END marker missing from shipped script"
    after_begin = source.split(BEGIN, 1)[1]
    # Discard the trailing dashes on the BEGIN marker line itself.
    return after_begin.split("\n", 1)[1].split(END, 1)[0]


def _resolve(args: str, *, expect_error: bool = False,
             function: str = "Resolve-RegStaPath"):
    command = (
        _resolver_block()
        + "\n"
        + f"{function} {args} | ConvertTo-Json -Compress -Depth 3"
    )
    proc = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if expect_error:
        return proc
    assert proc.returncode == 0, f"resolver failed: {proc.stderr}\n{proc.stdout}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_override_is_honored():
    result = _resolve("-Override '/opt/custom/sta' -Exists { $true }")
    assert result["Path"] == "/opt/custom/sta"
    assert result["Source"] == "env-override"


def test_wsl_native_override_is_not_blocked_by_windows_stat():
    # A WSL-only path cannot be stat'ed from Windows; an explicit override must
    # still win (the absence warning is advisory, not a gate).
    result = _resolve("-Override '/usr/local/bin/sta' -Exists { $false }")
    assert result["Path"] == "/usr/local/bin/sta"
    assert result["Source"] == "env-override"


def test_default_resolves_to_validated_path():
    result = _resolve("-Exists { param($p) $p -eq '/mnt/d/tools/opensta/bin/sta' }")
    assert result["Path"] == "/mnt/d/tools/opensta/bin/sta"
    assert result["Source"] == "validated-recorded"


def test_legacy_path_fallback_preserves_backward_compatibility():
    result = _resolve("-Exists { param($p) $p -eq '/mnt/d/tools/src/OpenSTA/build/sta' }")
    assert result["Path"] == "/mnt/d/tools/src/OpenSTA/build/sta"
    assert result["Source"] == "legacy"


def test_missing_path_yields_actionable_error():
    proc = _resolve("-Exists { $false }", expect_error=True)
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "OpenSTA executable not found" in combined
    assert "ASSISTANT_STA" in combined and "REG_STA" in combined
    assert "/mnt/d/tools/opensta/bin/sta" in combined
    assert "/mnt/d/tools/src/OpenSTA/build/sta" in combined


def test_lib_dir_default_resolves_to_validated_directory():
    result = _resolve("-Exists { param($p) $p -eq '/mnt/d/tools/opensta/lib' }",
                      function="Resolve-RegStaLibPath")
    assert result["Dir"] == "/mnt/d/tools/opensta/lib"
    assert result["Value"] == "/mnt/d/tools/opensta/lib"


def test_lib_dir_override_is_honored_and_appendable():
    result = _resolve(
        "-Override '/opt/custom/cudd/lib' -Base '/opt/base' -Exists { $true }",
        function="Resolve-RegStaLibPath",
    )
    assert result["Dir"] == "/opt/custom/cudd/lib"
    assert result["Value"] == "/opt/base:/opt/custom/cudd/lib"


def test_missing_lib_dir_is_advisory_not_fatal():
    # A WSL-only lib dir cannot be stat'ed from Windows; the emitted value must
    # still be usable rather than blocking the flow.
    result = _resolve("-Exists { $false }", function="Resolve-RegStaLibPath")
    assert result["Value"] == "/mnt/d/tools/opensta/lib"


def test_shipped_script_and_sta_stage_are_wired_to_resolution():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "ASSISTANT_STA" in source and "REG_STA" in source
    assert "ASSISTANT_STA_LIB_DIR" in source and "REG_STA_LIB_DIR" in source
    assert "REG_STA         = $StaPath" in source
    assert "LD_LIBRARY_PATH = $StaLdLibraryPath" in source
    assert 'opensta     = "wsl -d $WslDistro -- env LD_LIBRARY_PATH=$StaLdLibraryPath $StaPath -version"' in source
    # 12_sta.sh consumes the exported REG_STA; it needs no change to respect
    # the override, and this test guards against accidentally breaking that.
    assert '"$REG_STA"' in STA_STAGE.read_text(encoding="utf-8")
