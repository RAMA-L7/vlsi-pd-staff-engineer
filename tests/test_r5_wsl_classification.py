"""R5 / R4A-03 regression: conservative WSL classification + fail-closed gate.

BASELINE (reproduced before the fix in assistant/runner.py)::

    run_managed(["cmd", "/c", "wsl -d Ubuntu-24.04 -- echo hi"])
      -> containment='clean', containment_reason='clean_exit',
         wsl_uncontained=False, authorization_safe=True, ran=True

That is a FAIL-OPEN: the bare ``wsl`` shell-string token was invisible to the
R4 detector (which only matched an exact ``wsl`` token or the substring
``wsl.exe``), so an unhost-containable WSL2 execution produced authoritative
PASS evidence with a false ``hard-contained-direct-tree`` audit record.

Every test in the "bypass" / "indirection" / "gate" groups below FAILS on that
vulnerable baseline and PASSES after the R5 fix.  The final "controls" group
preserves the hard-containment positives (clean exit, timeout confirmation) and
must pass on BOTH baseline and fixed code.

Run:
    <PY> -m pytest tests/test_r5_wsl_classification.py -q
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from assistant.runner import (
    CONTAINMENT_CLEAN,
    CONTAINMENT_UNSUPPORTED,
    REASON_CLEAN,
    REASON_UNCONTAINED_TREE,
    REASON_WSL_UNCONFIRMABLE,
    RunResult,
    _looks_like_uncontainable_tree,
    _looks_like_wsl,
    _wsl_risk_detail,
    run_command,
    run_managed,
)

IS_WINDOWS = os.name == "nt"
PY = sys.executable


def _result(command, **kw) -> RunResult:
    """Build a minimal valid RunResult for gate-unit tests."""
    base = dict(
        command=command,
        exit_code=0,
        timed_out=False,
        duration_s=0.01,
        stdout_log="",
        stderr_log="",
        combined_log="",
        cwd=str(Path(__file__).parent),
        containment=CONTAINMENT_CLEAN,
        containment_reason=REASON_CLEAN,
        error="",
    )
    base.update(kw)
    return RunResult(**base)


# ---------------------------------------------------------------------------
# 1. DETECTOR UNIT TESTS -- bare wsl, case, whitespace, wrappers, indirection.
#    All of these are MISSED by the vulnerable R4 detector (no "wsl.exe"
#    substring / no exact bare "wsl" token), so they fail on baseline.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    # canonical R4A-03 bypass: bare wsl inside a cmd /c shell string
    ["cmd", "/c", "wsl -d Ubuntu-24.04 -- echo hi"],
    # leading whitespace + case variation inside the shell string
    ["cmd", "/c", "   WSL  -d Ubuntu-24.04 -- echo hi"],
    # command chaining (&&) hides wsl behind a benign first command
    ["cmd", "/c", "echo ready && wsl -d Ubuntu-24.04 -- echo hi"],
    # pipeline
    ["cmd", "/c", "wsl -d Ubuntu-24.04 -- echo hi | tee out.txt"],
    # cmd caret escaping and quote-splitting of the executable name
    ["cmd", "/c", "w^s^l -d Ubuntu-24.04"],
    ["cmd", "/c", "w\"s\"l -d Ubuntu-24.04"],
    # PowerShell wrappers (including the `&` call operator and nested quotes)
    ["powershell", "-NoProfile", "-Command", "wsl --list"],
    ["powershell", "-NoProfile", "-Command", "& 'wsl' --status"],
    # nested POSIX shell calls
    ["bash", "-lc", "wsl -d Ubuntu-24.04 -- bash /tmp/run.sh"],
    # child process invoking WSL from a Python -c literal
    ["bash", "-lc", "python -c \"import subprocess; subprocess.run(['wsl','-l'])\""],
    # environment indirection / assignment (no literal wsl as a command word)
    ["cmd", "/c", "set WSLEXE=wsl && call %WSLEXE% -l"],
    ["env", "WSLEXE=wsl", "sh", "-c", "$WSLEXE -l"],
    # externally-set variable in COMMAND position (value not in argv at all)
    ["cmd", "/c", "%WSLCMD% -l"],
    ["bash", "-lc", "$GUEST_TOOL --version"],
    # WSL UNC share / executable path embedded in a shell string
    ["cmd", "/c", "call \\\\wsl$\\Ubuntu-24.04\\bin\\echo hi"],
])
def test_bare_wsl_and_indirection_detected(command):
    risk, reason, detail = _wsl_risk_detail(command)
    assert risk is True, f"vulnerable baseline missed WSL in {command!r}"
    assert reason.startswith("wsl_"), reason
    assert detail, "a machine-readable detail token must be returned for the log"
    assert _looks_like_wsl(command) is True


def test_script_invoking_wsl_detected(tmp_path):
    """A script file that invokes WSL is detected by bounded read-only scan."""
    script = tmp_path / "launch_guest.py"
    script.write_text(
        "import subprocess\nsubprocess.run(['wsl', '-d', 'Ubuntu-24.04'])\n",
        encoding="utf-8",
    )
    risk, reason, detail = _wsl_risk_detail([PY, str(script)])
    assert risk is True
    assert reason == "wsl_script", (reason, detail)
    assert _looks_like_wsl([PY, str(script)]) is True


# ---------------------------------------------------------------------------
# 2. INTEGRATION -- run_managed must classify, never authorize, the bypass.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not IS_WINDOWS, reason="cmd /c is a Windows shell builtin")
@pytest.mark.parametrize("shell_string", [
    "wsl -d Ubuntu-24.04 -- echo hi",
    "   WSL  -d Ubuntu-24.04 -- echo hi",   # case + leading whitespace
])
def test_cmd_c_bare_wsl_clean_exit_is_unsupported(tmp_path, shell_string):
    """The R4A-03 bypass must be unsupported even on a zero-exit clean run."""
    result = run_managed(
        ["cmd", "/c", shell_string],
        log_dir=str(tmp_path / "logs"),
        name="cmd-bare-wsl",
        deadline=time.monotonic() + 20.0,
    )
    assert result.containment == CONTAINMENT_UNSUPPORTED, (
        f"baseline fail-open reproduced: {result.containment}"
    )
    assert result.containment_reason == REASON_WSL_UNCONFIRMABLE
    assert result.wsl_uncontained is True
    assert result.is_host_contained is False
    assert result.authorization_safe is False
    assert result.ran is False
    assert REASON_WSL_UNCONFIRMABLE in result.error

    combined = Path(result.combined_log).read_text(encoding="utf-8", errors="replace")
    assert f"containment_reason={REASON_WSL_UNCONFIRMABLE}" in combined
    assert "wsl_uncontained=True" in combined


def test_bare_wsl_in_python_c_string_is_unsupported(tmp_path):
    """Cross-platform bypass form; does not require WSL to be installed.

    The command merely PRINTS a string containing bare ``wsl``.  The vulnerable
    detector saw no ``wsl.exe`` substring and classified it clean/auth-safe.
    """
    result = run_managed(
        [PY, "-c", "print('wsl -d Ubuntu-24.04 -- echo hi')"],
        log_dir=str(tmp_path / "logs"),
        name="py-bare-wsl",
        deadline=time.monotonic() + 15.0,
    )
    assert result.containment == CONTAINMENT_UNSUPPORTED
    assert result.containment_reason == REASON_WSL_UNCONFIRMABLE
    assert result.wsl_uncontained is True
    assert result.authorization_safe is False
    assert result.ran is False


@pytest.mark.skipif(not IS_WINDOWS, reason="cmd /c is a Windows shell builtin")
def test_detached_tree_in_cmd_is_unsupported(tmp_path):
    """An explicitly detached subprocess tree cannot be host-confirmed."""
    result = run_managed(
        ["cmd", "/c", "start /b cmd /c echo detached"],
        log_dir=str(tmp_path / "logs"),
        name="detached-tree",
        deadline=time.monotonic() + 20.0,
    )
    assert result.containment == CONTAINMENT_UNSUPPORTED
    assert result.containment_reason == REASON_UNCONTAINED_TREE
    assert result.wsl_uncontained is False  # not a WSL path
    assert result.authorization_safe is False
    assert _looks_like_uncontainable_tree(
        ["cmd", "/c", "start /b cmd /c echo detached"]
    ) is True


def test_wsl_timeout_path_classified_unsupported(tmp_path):
    """The timeout branch must also carry the canonical WSL token."""
    result = run_managed(
        [PY, "-c", "import time; time.sleep(5); print('wsl -l')"],
        log_dir=str(tmp_path / "logs"),
        name="wsl-timeout",
        timeout=0.3,
        confirm_grace_s=0.3,
        deadline=time.monotonic() + 15.0,
    )
    assert result.timed_out is True
    assert result.containment == CONTAINMENT_UNSUPPORTED
    assert result.containment_reason == REASON_WSL_UNCONFIRMABLE
    assert result.wsl_uncontained is True
    assert result.authorization_safe is False
    assert REASON_WSL_UNCONFIRMABLE in result.error


# ---------------------------------------------------------------------------
# 3. AUTHORIZATION GATE -- unsupported/uncertain execution is NEVER safe.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    ["cmd", "/c", "wsl -d Ubuntu-24.04 -- echo hi"],
    [PY, "-c", "print('wsl -l')"],
    ["powershell", "-Command", "wsl --status"],
    ["cmd", "/c", "start /b cmd /c echo detached"],
])
def test_authorization_gate_rechecks_command(command):
    """A hand-built 'clean' RunResult with a WSL/detached command cannot authorize.

    On the vulnerable baseline ``is_host_contained`` and ``authorization_safe``
    trusted only the recorded flag/containment, so this test fails there.
    """
    result = _result(command)
    assert result.is_host_contained is False
    assert result.authorization_safe is False


def test_unsupported_never_authorization_safe_even_with_flag_cleared():
    """Unsupported containment alone must veto authorization."""
    result = _result(
        ["cmd", "/c", "wsl -l"],
        containment=CONTAINMENT_UNSUPPORTED,
        containment_reason=REASON_WSL_UNCONFIRMABLE,
        error=REASON_WSL_UNCONFIRMABLE,
        wsl_uncontained=False,  # adversarially cleared flag
    )
    assert result.ran is False
    assert result.is_host_contained is False
    assert result.authorization_safe is False


# ---------------------------------------------------------------------------
# 4. CONTROLS -- must pass on baseline AND after the fix (no regressions).
# ---------------------------------------------------------------------------

def test_detector_controls_benign_and_explicit_exe(tmp_path):
    # Explicit wsl.exe forms were already caught by the R4 detector.
    for command in (
        ["wsl.exe", "-l"],
        ["C:\\Windows\\System32\\WSL.EXE", "-l"],
        ["cmd", "/c", "wsl.exe -l"],
        [PY, "wrapper.py", "wsl.exe"],
    ):
        assert _looks_like_wsl(command) is True, command
    # Benign commands must NOT be flagged (preserve hard-containment positives).
    for command in (
        [PY, "-c", "print('ok')"],
        [PY, str(tmp_path / "wrapper.py")],
        ["cmd", "/c", "echo hello"],
        ["cmd", "/c", "set X=hello && echo %X%"],
    ):
        assert _looks_like_wsl(command) is False, command
        assert _looks_like_uncontainable_tree(command) is False, command


def test_benign_command_still_clean_and_authorized(tmp_path):
    result = run_managed(
        [PY, "-c", "print('ok')"],
        log_dir=str(tmp_path / "logs"),
        name="benign",
        deadline=time.monotonic() + 15.0,
    )
    assert result.containment == CONTAINMENT_CLEAN
    assert result.containment_reason == REASON_CLEAN
    assert result.wsl_uncontained is False
    assert result.authorization_safe is True
    assert result.ran is True


def test_run_command_legacy_behavior_preserved(tmp_path):
    """run_command (non-managed) keeps its legacy defaults and no WSL gate."""
    result = run_command(
        [PY, "-c", "print('legacy ok')"],
        log_dir=str(tmp_path / "logs"),
        name="legacy",
        timeout=10.0,
    )
    assert result.exit_code == 0
    assert result.containment == "none"
    assert result.authorization_safe is False  # no managed claim => never authorizes
    assert "legacy ok" in Path(result.stdout_log).read_text(encoding="utf-8")
