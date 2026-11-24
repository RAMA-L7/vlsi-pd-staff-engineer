"""R6.1 P0-A — deterministic cancellation-through-commit regression suite.

Every injection below is a deterministic hook (barrier / wrapper / monkeypatch),
never a wall-clock sleep or a scheduling-luck race. Each test asserts:

* NO durable record is a qualified ``PASS``/``PRODUCTION`` (or ``SIMULATED``);
* a FRESH ``EcoManager`` re-reading the same log observes no qualified PASS;
* the returned result MIRRORS the durable record on status,
  qualification_class, deadline_exceeded and cancelled (invariant I9);
* cancellation and deadline remain semantically distinct (``cancelled=True``,
  ``deadline_exceeded=False`` for a pure cancellation).
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest

import assistant.eco as eco_mod
from assistant.controller import GateController
from assistant.eco import (
    EcoChangeScope,
    EcoManager,
    EcoStage,
    ProtectedMetric,
)

BASELINE = "module d; endmodule\n"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _make_env(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "design.v").write_text(BASELINE, encoding="utf-8")
    ctl = GateController(str(tmp_path / "state.json"))
    eco = EcoManager(
        str(ws), ctl,
        checkpoint_dir=str(tmp_path / "ckpt"),
        experiment_log=str(tmp_path / "exp.json"),
    )
    return ws, ctl, eco


def _run(eco, eid="p0a-run", downstream=None):
    return eco.run_experiment(
        experiment_id=eid, description="r61 p0a regression",
        change_class="no_op", changed_files=["design.v"],
        apply_change=lambda: None,
        evaluate=lambda: {"wns": 1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0)],
        verify=None, verification_tools=None, verification_timeout_s=0.5,
        change_scope=EcoChangeScope(
            "no_op", EcoStage.MAPPED, False, "r61 p0a probe"
        ),
        downstream_stage=downstream,
        simulated="", analysis_mode=False,
    )


def _durable(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["experiments"]


def _assert_no_qualified_pass(tmp_path, ctl, result, *, token=None):
    """Assert durable + fresh-manager state is non-authorizing and I9 holds."""
    dur = _durable(tmp_path / "exp.json")
    bad = [
        r for r in dur
        if r.get("status") == "PASS"
        and r.get("qualification_class") in ("PRODUCTION", "SIMULATED")
    ]
    assert not bad, f"durable qualified PASS survived: {bad}"

    fresh = EcoManager(
        str(tmp_path / "ws"), ctl,
        checkpoint_dir=str(tmp_path / "ckpt2"),
        experiment_log=str(tmp_path / "exp.json"),
    ).history()
    bad_fresh = [
        r for r in fresh
        if r.get("status") == "PASS"
        and r.get("qualification_class") in ("PRODUCTION", "SIMULATED")
    ]
    assert not bad_fresh, f"fresh manager observed qualified PASS: {bad_fresh}"

    if result is not None:
        assert result.status != "PASS", f"caller received PASS: {result}"
        rec = None
        if token:
            rec = next((r for r in dur if r.get("transaction_token") == token), None)
        if rec is None:
            non_pass = [r for r in dur if r.get("status") != "PASS"]
            rec = non_pass[-1] if non_pass else None
        if rec is not None:
            assert result.status == rec["status"], (
                "I9 status mismatch", result.status, rec["status"]
            )
            assert result.qualification_class == rec["qualification_class"], (
                "I9 qualification mismatch",
                result.qualification_class, rec["qualification_class"],
            )
            assert bool(result.deadline_exceeded) == bool(
                rec.get("deadline_exceeded", False)
            ), ("I9 deadline mismatch",
                result.deadline_exceeded, rec.get("deadline_exceeded"))
            assert bool(getattr(result, "cancelled", False)) == bool(
                rec.get("cancelled", False)
            ), ("I9 cancelled mismatch",
                getattr(result, "cancelled", False), rec.get("cancelled"))
    return dur


# ---------------------------------------------------------------------------
# 1. cancel BEFORE commit (at the _commit boundary)
# ---------------------------------------------------------------------------
def test_p0a_cancel_before_commit(tmp_path):
    ws, ctl, eco = _make_env(tmp_path)
    fired = {"v": False}
    orig_commit = eco._commit

    def hooked_commit(result):
        if result.status == "PASS" and not fired["v"]:
            fired["v"] = True
            ctl.cancel("r61 cancel before commit")
        return orig_commit(result)

    eco._commit = hooked_commit
    result = _run(eco)
    assert fired["v"]
    assert result.status != "PASS"
    _assert_no_qualified_pass(tmp_path, ctl, result)


# ---------------------------------------------------------------------------
# 2. cancel DURING commit (inside the _save_log hook, before original)
# ---------------------------------------------------------------------------
def test_p0a_cancel_during_commit(tmp_path):
    ws, ctl, eco = _make_env(tmp_path)
    fired = {"v": False}
    orig_save = eco._save_log

    def hooked_save():
        if eco._history and eco._history[-1].get("status") == "PASS" and not fired["v"]:
            fired["v"] = True
            ctl.cancel("r61 cancel inside persistence")
        return orig_save()

    eco._save_log = hooked_save
    result = _run(eco)
    assert fired["v"]
    dur = _assert_no_qualified_pass(tmp_path, ctl, result)
    stamp = next(r for r in dur if r.get("status") != "PASS")
    assert stamp["qualification_class"] == "UNQUALIFIED"
    assert stamp["cancelled"] is True, stamp
    assert stamp["deadline_exceeded"] is False, stamp
    assert "cancel" in stamp["reason"].lower()


# ---------------------------------------------------------------------------
# 3. cancel AFTER write begins (inside os.replace) — exercises GAP 1 raise
# ---------------------------------------------------------------------------
def test_p0a_cancel_at_os_replace_raises_and_retry_rewrites_fail(tmp_path, monkeypatch):
    ws, ctl, eco = _make_env(tmp_path)
    fired = {"v": False, "calls": 0}
    real_replace = eco_mod.os.replace

    def cancelling_replace(src, dst, *a, **kw):
        # Only ECO's experiment-log write is the commit boundary; the
        # controller's own state save must not consume the injection.
        if not str(dst).endswith("exp.json"):
            return real_replace(src, dst, *a, **kw)
        fired["calls"] += 1
        if not fired["v"]:
            fired["v"] = True
            # Cancel after guard evaluation but at the atomic-write boundary.
            ctl.cancel("r61 cancel at os.replace")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(eco_mod.os, "replace", cancelling_replace)
    result = _run(eco)
    assert fired["v"]
    assert fired["calls"] >= 2, "retry loop must re-run _save_log after raise"
    dur = _assert_no_qualified_pass(tmp_path, ctl, result)
    stamp = next(r for r in dur if r.get("status") != "PASS")
    assert stamp["cancelled"] is True, stamp
    assert stamp["deadline_exceeded"] is False, stamp


# ---------------------------------------------------------------------------
# 4. cancel inside json.dump (mid-serialization) — GAP 1, second injection
# ---------------------------------------------------------------------------
def test_p0a_cancel_inside_json_dump(tmp_path, monkeypatch):
    ws, ctl, eco = _make_env(tmp_path)
    fired = {"v": False}
    real_dump = eco_mod.json.dump

    def cancelling_dump(obj, fp, *a, **kw):
        # ECO's log payload has an "experiments" key; controller state does not.
        if not isinstance(obj, dict) or "experiments" not in obj:
            return real_dump(obj, fp, *a, **kw)
        if not fired["v"]:
            fired["v"] = True
            ctl.cancel("r61 cancel inside json.dump")
        return real_dump(obj, fp, *a, **kw)

    monkeypatch.setattr(eco_mod.json, "dump", cancelling_dump)
    result = _run(eco)
    assert fired["v"]
    dur = _assert_no_qualified_pass(tmp_path, ctl, result)
    stamp = next(r for r in dur if r.get("status") != "PASS")
    assert stamp["cancelled"] is True, stamp


# ---------------------------------------------------------------------------
# 5. cancel NEAR COMPLETION (right after the atomic write, before return)
# ---------------------------------------------------------------------------
def test_p0a_cancel_near_completion(tmp_path):
    ws, ctl, eco = _make_env(tmp_path)
    fired = {"v": False}
    orig_commit = eco._commit

    def hooked_commit(result):
        r = orig_commit(result)
        if result.status == "PASS" and not fired["v"]:
            fired["v"] = True
            ctl.cancel("r61 cancel right after commit")
        return r

    eco._commit = hooked_commit
    result = _run(eco)
    assert fired["v"]
    dur = _assert_no_qualified_pass(tmp_path, ctl, result)
    stamp = next(r for r in dur if r.get("status") != "PASS")
    assert stamp["cancelled"] is True, stamp


# ---------------------------------------------------------------------------
# 6. CONCURRENT cancel at persistence barrier (deterministic Barrier(2))
# ---------------------------------------------------------------------------
def test_p0a_concurrent_cancel(tmp_path):
    ws, ctl, eco = _make_env(tmp_path)
    barrier = threading.Barrier(2)
    fired = {"v": False}

    def canceller():
        barrier.wait(timeout=5)
        ctl.cancel("r61 concurrent cancel at persistence")

    thread = threading.Thread(target=canceller, name="r61-canceller")
    thread.start()
    orig_save = eco._save_log

    def hooked_save():
        if eco._history and eco._history[-1].get("status") == "PASS" and not fired["v"]:
            fired["v"] = True
            barrier.wait(timeout=5)
        return orig_save()

    eco._save_log = hooked_save
    try:
        result = _run(eco)
    finally:
        thread.join(timeout=5)
    assert not thread.is_alive(), "canceller did not finish"
    assert fired["v"]
    dur = _assert_no_qualified_pass(tmp_path, ctl, result)
    stamp = next(r for r in dur if r.get("status") != "PASS")
    assert stamp["cancelled"] is True, stamp


# ---------------------------------------------------------------------------
# 7. cancel + PERSISTENCE FAILURE (first os.replace fails after cancel)
# ---------------------------------------------------------------------------
def test_p0a_cancel_plus_persistence_failure(tmp_path, monkeypatch):
    ws, ctl, eco = _make_env(tmp_path)
    state = {"calls": 0}
    real_replace = eco_mod.os.replace

    def flaky_replace(src, dst, *a, **kw):
        if not str(dst).endswith("exp.json"):
            return real_replace(src, dst, *a, **kw)
        state["calls"] += 1
        if state["calls"] == 1:
            ctl.cancel("r61 cancel + persistence failure")
            raise OSError("SIMULATED transient persistence failure")
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(eco_mod.os, "replace", flaky_replace)
    result = _run(eco)
    assert state["calls"] >= 2, "retry loop must be exercised"
    _assert_no_qualified_pass(tmp_path, ctl, result)


# ---------------------------------------------------------------------------
# 8. cancel + FRESH-MANAGER RECOVERY
# ---------------------------------------------------------------------------
def test_p0a_cancel_fresh_manager_recovery(tmp_path):
    ws, ctl, eco = _make_env(tmp_path)

    # A clean PASS first, so the ledger has a legitimate record for another id.
    clean = _run(eco, eid="clean-run")
    assert clean.status == "PASS"

    fired = {"v": False}
    orig_save = eco._save_log

    def hooked_save():
        if eco._history and eco._history[-1].get("status") == "PASS" and not fired["v"]:
            fired["v"] = True
            ctl.cancel("r61 cancel after clean run")
        return orig_save()

    eco._save_log = hooked_save
    result = _run(eco, eid="cancelled-run")
    assert fired["v"]
    assert result.status != "PASS"

    # Fresh manager must not expose a qualified PASS for the CANCELLED token.
    fresh = EcoManager(
        str(ws), ctl, checkpoint_dir=str(tmp_path / "ckpt2"),
        experiment_log=str(tmp_path / "exp.json"),
    ).history()
    cancelled_recs = [
        r for r in fresh if r.get("experiment_id") == "cancelled-run"
    ]
    assert cancelled_recs and all(
        r.get("status") != "PASS"
        and r.get("qualification_class") != "PRODUCTION"
        for r in cancelled_recs
    ), cancelled_recs
    # The clean PASS belongs to a different transaction and is unaffected.
    assert any(
        r.get("experiment_id") == "clean-run" and r.get("status") == "PASS"
        for r in fresh
    ), fresh


# ---------------------------------------------------------------------------
# 9. cancel + CONCURRENT ACCEPTANCE (re-entrant run_experiment refused)
# ---------------------------------------------------------------------------
def test_p0a_cancel_plus_concurrent_acceptance(tmp_path):
    ws, ctl, eco = _make_env(tmp_path)
    seen = {}

    def downstream(_r):
        ctl.cancel("r61 cancel before nested acceptance")
        try:
            nested = _run(eco, eid="nested")
            seen["nested_status"] = nested.status
        except Exception as exc:  # noqa: BLE001 - nested acceptance must be refused
            seen["error"] = f"{type(exc).__name__}: {exc}"

    result = _run(eco, eid="outer", downstream=downstream)
    assert result.status != "PASS"
    assert seen.get("nested_status") != "PASS"
    assert "error" in seen, "nested acceptance was not refused"
    dur = _durable(tmp_path / "exp.json")
    assert not [r for r in dur if r.get("experiment_id") == "nested"], dur
    _assert_no_qualified_pass(tmp_path, ctl, result)


# ---------------------------------------------------------------------------
# 10. STRESS: >=20 reps under background load
# ---------------------------------------------------------------------------
def test_p0a_stress_cancel_under_load(tmp_path):
    reps = 24
    stop = threading.Event()

    def load():
        buf = b"r61-p0a-load" * 512
        while not stop.is_set():
            hashlib.sha256(buf).digest()

    loader = threading.Thread(target=load, name="r61-load", daemon=True)
    loader.start()
    try:
        for i in range(reps):
            case = tmp_path / f"rep{i}"
            case.mkdir()
            ws, ctl, eco = _make_env(case)
            fired = {"v": False}
            orig_save = eco._save_log

            def hooked_save(_eco=eco, _ctl=ctl, _fired=fired, _orig=orig_save):
                if _eco._history and _eco._history[-1].get("status") == "PASS" \
                        and not _fired["v"]:
                    _fired["v"] = True
                    _ctl.cancel(f"r61 stress cancel rep {i}")
                return _orig()

            eco._save_log = hooked_save
            result = _run(eco, eid=f"stress-{i}")
            assert fired["v"], f"rep {i} cancel did not fire"
            assert result.status != "PASS", f"rep {i} returned PASS"
            _assert_no_qualified_pass(case, ctl, result)
    finally:
        stop.set()
        loader.join(timeout=5)
    assert not loader.is_alive(), "load thread did not stop"
