"""Regression tests for the three confirmed baseline-reporting gaps.

GAP 1 - a timeout / spawn failure must signal non-zero to automation while the
        manifest keeps ``exit_code = None`` (the tool never exited).
GAP 2 - the regression must receive ``REG_REPO`` pointing at the validated
        checkout, not run_regression.ps1's hard-coded ``D:\\audit\\repo``.
GAP 3 - WSL ``/mnt/<letter>/...`` view paths must hash on Windows without
        rewriting the path recorded in the manifest.

The real 15-stage regression is never executed here: ``subprocess.run`` and
tool provenance are mocked so pytest stays fast and offline.
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from assistant import baseline
from assistant.baseline import (
    BASELINE_INCOMPLETE_EXIT,
    BASELINE_SPAWN_EXIT,
    BASELINE_TIMEOUT_EXIT,
    BaselineManifest,
    _hash_view_artifact,
    baseline_exit_signal,
    normalize_wsl_path,
    run_baseline,
)


def _empty_provenance():
    """Minimal stand-in so run_baseline never probes the real toolchain."""
    prov = MagicMock()
    prov.to_dict.return_value = {"tools": []}
    return prov


@pytest.fixture
def fake_repo(tmp_path):
    """A checkout-shaped tree with just the regression entry point present."""
    root = tmp_path / "repo"
    (root / "regression").mkdir(parents=True)
    (root / "regression" / "run_regression.ps1").write_text(
        "# stub - the real regression is never invoked in these tests\n",
        encoding="utf-8",
    )
    return str(root)


def _main_manifest(**kw):
    m = BaselineManifest(
        command=["powershell"],
        exit_code=kw.get("exit_code"),
        run_dir=kw.get("run_dir", ""),
    )
    m.timed_out = kw.get("timed_out", False)
    m.start_failed = kw.get("start_failed", False)
    return m


# ------------------------------------------------------------------- GAP 1
def test_timeout_sets_flag_keeps_null_exit_and_signals_124(fake_repo, tmp_path):
    with patch.object(baseline, "default_provenance", return_value=_empty_provenance()), \
         patch("assistant.baseline.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="powershell", timeout=1.0)) as run_mock:
        m = run_baseline(fake_repo, run_id="timeout", timeout_s=1.0,
                         out_dir=str(tmp_path / "out"))
    assert m.timed_out is True
    assert m.start_failed is False
    assert m.exit_code is None                      # honest: tool never exited
    assert m.completed_ok is False
    assert any("timed out" in n for n in m.notes)
    assert baseline_exit_signal(m) == BASELINE_TIMEOUT_EXIT == 124
    # the invocation still carried the validated checkout (GAP 2 regression)
    assert run_mock.call_args.kwargs["env"]["REG_REPO"] == fake_repo


def test_spawn_failure_sets_flag_keeps_null_exit_and_signals_125(fake_repo, tmp_path):
    with patch.object(baseline, "default_provenance", return_value=_empty_provenance()), \
         patch("assistant.baseline.subprocess.run",
               side_effect=OSError("no such binary")) as run_mock:
        m = run_baseline(fake_repo, run_id="spawn", out_dir=str(tmp_path / "out"))
    assert m.start_failed is True
    assert m.timed_out is False
    assert m.exit_code is None
    assert m.completed_ok is False
    assert any("failed to start" in n for n in m.notes)
    assert baseline_exit_signal(m) == BASELINE_SPAWN_EXIT == 125
    assert run_mock.call_args.kwargs["env"]["REG_REPO"] == fake_repo


def test_genuine_zero_exit_signals_zero(fake_repo, tmp_path):
    with patch.object(baseline, "default_provenance", return_value=_empty_provenance()), \
         patch("assistant.baseline.subprocess.run",
               return_value=subprocess.CompletedProcess(args=[], returncode=0)):
        m = run_baseline(fake_repo, run_id="ok", out_dir=str(tmp_path / "out"))
    assert m.exit_code == 0
    assert m.timed_out is False
    assert m.start_failed is False
    assert m.completed_ok is True
    assert baseline_exit_signal(m) == 0


def test_nonzero_regression_exit_is_propagated(fake_repo, tmp_path):
    with patch.object(baseline, "default_provenance", return_value=_empty_provenance()), \
         patch("assistant.baseline.subprocess.run",
               return_value=subprocess.CompletedProcess(args=[], returncode=7)):
        m = run_baseline(fake_repo, run_id="fail", out_dir=str(tmp_path / "out"))
    assert m.exit_code == 7
    assert m.completed_ok is False
    assert baseline_exit_signal(m) == 7


def test_dry_run_is_incomplete_not_success(fake_repo, tmp_path):
    with patch.object(baseline, "default_provenance", return_value=_empty_provenance()), \
         patch("assistant.baseline.subprocess.run") as run_mock:
        m = run_baseline(fake_repo, run_id="dry", dry_run=True,
                         out_dir=str(tmp_path / "out"))
    run_mock.assert_not_called()
    assert m.exit_code is None
    assert m.timed_out is False and m.start_failed is False
    assert m.completed_ok is False
    assert baseline_exit_signal(m) == BASELINE_INCOMPLETE_EXIT == 1


def test_main_returns_124_on_timeout_and_prints_signal(capsys, tmp_path):
    m = _main_manifest(timed_out=True)
    m.notes.append("baseline timed out after 1.0s")
    with patch.object(baseline, "run_baseline", return_value=m), \
         patch.object(baseline, "save_manifest", return_value=str(tmp_path / "m.json")):
        rc = baseline.main(["--out", str(tmp_path)])
    assert rc == 124
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "TIMEOUT"
    assert payload["return_code"] == 124
    assert payload["timed_out"] is True
    assert payload["start_failed"] is False
    assert payload["exit_code"] is None
    assert payload["notes"] == m.notes


def test_main_returns_125_on_spawn_failure_and_prints_signal(capsys, tmp_path):
    m = _main_manifest(start_failed=True)
    m.notes.append("failed to start regression: boom")
    with patch.object(baseline, "run_baseline", return_value=m), \
         patch.object(baseline, "save_manifest", return_value=str(tmp_path / "m.json")):
        rc = baseline.main(["--out", str(tmp_path)])
    assert rc == 125
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "SPAWN_FAILED"
    assert payload["return_code"] == 125
    assert payload["start_failed"] is True
    assert payload["timed_out"] is False
    assert payload["exit_code"] is None


def test_main_returns_one_on_other_incomplete(capsys, tmp_path):
    m = _main_manifest()
    m.notes.append("dry run: command not executed")
    with patch.object(baseline, "run_baseline", return_value=m), \
         patch.object(baseline, "save_manifest", return_value=str(tmp_path / "m.json")):
        rc = baseline.main(["--out", str(tmp_path)])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "INCOMPLETE"
    assert payload["return_code"] == 1
    assert payload["exit_code"] is None


def test_main_returns_zero_only_on_genuine_completion(capsys, tmp_path):
    m = _main_manifest(exit_code=0, run_dir="run_ok")
    with patch.object(baseline, "run_baseline", return_value=m), \
         patch.object(baseline, "save_manifest", return_value=str(tmp_path / "m.json")):
        rc = baseline.main(["--out", str(tmp_path)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["return_code"] == 0
    assert payload["exit_code"] == 0
    assert payload["timed_out"] is False and payload["start_failed"] is False


# ------------------------------------------------------------------- GAP 2
def test_reg_repo_env_points_to_validated_checkout(fake_repo, tmp_path):
    with patch.object(baseline, "default_provenance", return_value=_empty_provenance()), \
         patch.dict(os.environ, {"BASELINE_ENV_SENTINEL": "kept"}), \
         patch("assistant.baseline.subprocess.run",
               return_value=subprocess.CompletedProcess(args=[], returncode=0)) as run_mock:
        run_baseline(fake_repo, run_id="env", out_dir=str(tmp_path / "out"))
    run_mock.assert_called_once()
    kwargs = run_mock.call_args.kwargs
    assert kwargs["cwd"] == fake_repo
    env = kwargs["env"]
    assert env is not os.environ                       # a copy, not the live mapping
    assert env["REG_REPO"] == fake_repo                 # positive: validated checkout
    assert env["REG_REPO"] != r"D:\audit\repo"          # negative: not the default
    assert env["BASELINE_ENV_SENTINEL"] == "kept"       # other env vars preserved
    assert env["PATH"] == os.environ["PATH"]


# ------------------------------------------------------------------- GAP 3
def _wsl_form(native_path):
    """Express a native absolute path as a WSL ``/mnt/<letter>/...`` path."""
    p = str(native_path).replace("\\", "/")
    drive, rest = p.split(":", 1)
    return f"/mnt/{drive.lower()}{rest}"


@pytest.mark.skipif(os.name != "nt", reason="WSL mount translation is Windows-only")
def test_normalize_wsl_path_is_case_insensitive_and_scoped():
    assert normalize_wsl_path("/mnt/d/audit/x.v") == "D:/audit/x.v"
    assert normalize_wsl_path("/mnt/D/Audit/x.v") == "D:/Audit/x.v"
    # no-op for native Windows paths and for non-mount POSIX paths
    assert normalize_wsl_path(r"C:\work\x.v") == r"C:\work\x.v"
    assert normalize_wsl_path("C:/work/x.v") == "C:/work/x.v"
    assert normalize_wsl_path("/usr/local/bin/openroad") == "/usr/local/bin/openroad"
    assert normalize_wsl_path("/mnt/d") == "/mnt/d"


def test_normalize_wsl_path_noop_on_non_windows():
    with patch.object(baseline.os, "name", "posix"):
        assert normalize_wsl_path("/mnt/d/audit/x.v") == "/mnt/d/audit/x.v"


@pytest.mark.skipif(os.name != "nt", reason="requires Windows path semantics")
def test_hash_view_artifact_resolves_wsl_path(tmp_path):
    f = tmp_path / "synth" / "test_counter_synth.v"
    f.parent.mkdir(parents=True)
    data = b"module test_counter; endmodule\n"
    f.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    wsl = _wsl_form(f)
    assert _hash_view_artifact(wsl) == expected
    assert _hash_view_artifact(wsl) != "<missing>"
    # hashing must not rewrite the recorded path
    assert wsl.startswith("/mnt/")


@pytest.mark.skipif(os.name != "nt", reason="requires Windows path semantics")
def test_hash_view_artifact_still_hashes_native_windows_path(tmp_path):
    f = tmp_path / "test_counter_synth.v"
    data = b"native windows path\n"
    f.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    assert _hash_view_artifact(str(f)) == expected
    assert _hash_view_artifact(str(f)) != "<missing>"
    # a genuinely absent file is still reported honestly
    assert _hash_view_artifact(str(tmp_path / "does_not_exist.v")) == "<missing>"


@pytest.mark.skipif(os.name != "nt", reason="requires Windows path semantics")
def test_manifest_keeps_wsl_path_but_records_real_hash(fake_repo, tmp_path):
    netlist = tmp_path / "synth" / "test_counter_synth.v"
    netlist.parent.mkdir(parents=True)
    data = b"module test_counter; /* mapped */ endmodule\n"
    netlist.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    wsl = _wsl_form(netlist)

    run_dir = Path(fake_repo) / "regression" / "workspace" / "run_wsl"
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text(json.dumps({
        "stages": [{
            "stage": "01_synthesis",
            "status": "PASS",
            "checks": [{"name": "mapped_netlist_exists", "detail": wsl}],
            "artifacts": [],
        }]
    }), encoding="utf-8")

    with patch.object(baseline, "default_provenance", return_value=_empty_provenance()), \
         patch("assistant.baseline.subprocess.run",
               return_value=subprocess.CompletedProcess(args=[], returncode=0)):
        m = run_baseline(fake_repo, run_id="wsl", out_dir=str(tmp_path / "out"))

    view = m.views["mapped_netlist"]
    assert view["path"] == wsl                 # recorded path kept verbatim
    assert view["sha256"] == expected          # real hash, not "<missing>"


@pytest.mark.skipif(os.name != "nt", reason="requires Windows path semantics")
def test_manifest_native_windows_path_hash(fake_repo, tmp_path):
    netlist = tmp_path / "test_counter_synth.v"
    data = b"module test_counter; endmodule // native\n"
    netlist.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    native = str(netlist)

    run_dir = Path(fake_repo) / "regression" / "workspace" / "run_native"
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text(json.dumps({
        "stages": [{
            "stage": "01_synthesis",
            "status": "PASS",
            "checks": [{"name": "mapped_netlist_exists", "detail": native}],
            "artifacts": [],
        }]
    }), encoding="utf-8")

    with patch.object(baseline, "default_provenance", return_value=_empty_provenance()), \
         patch("assistant.baseline.subprocess.run",
               return_value=subprocess.CompletedProcess(args=[], returncode=0)):
        m = run_baseline(fake_repo, run_id="native", out_dir=str(tmp_path / "out"))

    view = m.views["mapped_netlist"]
    assert view["path"] == native
    assert view["sha256"] == expected
