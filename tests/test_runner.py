"""Runner tests: log preservation on failure, timeout kill, spawn failure."""
import os
import sys

from assistant.runner import run_command


def test_success_writes_logs(tmp_path):
    res = run_command(
        [sys.executable, "-c", "print('hello')"],
        log_dir=str(tmp_path / "logs"),
        name="ok",
    )
    assert res.exit_code == 0
    assert not res.timed_out
    assert os.path.isfile(res.combined_log)
    with open(res.combined_log, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    assert "hello" in text
    assert "exit_code=0" in text


def test_failure_preserves_logs(tmp_path):
    res = run_command(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(7)"],
        log_dir=str(tmp_path / "logs"),
        name="fail",
    )
    assert res.exit_code == 7
    assert os.path.isfile(res.stderr_log)
    with open(res.stderr_log, "r", encoding="utf-8", errors="replace") as f:
        assert "boom" in f.read()
    # combined log exists too - failed runs keep evidence
    assert os.path.isfile(res.combined_log)


def test_timeout_kills_and_marks_timed_out(tmp_path):
    res = run_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        log_dir=str(tmp_path / "logs"),
        name="slow",
        timeout=1.0,
    )
    assert res.timed_out
    assert res.exit_code is None
    assert not res.ran
    # logs still exist
    assert os.path.isfile(res.stdout_log)


def test_spawn_failure_recorded(tmp_path):
    res = run_command(
        [r"C:\no\such\binary.exe"],
        log_dir=str(tmp_path / "logs"),
        name="spawn",
    )
    assert not res.ran
    assert "spawn_failed" in res.error
    assert os.path.isfile(res.stderr_log)
