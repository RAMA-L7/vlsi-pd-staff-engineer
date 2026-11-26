"""EXT-02: deterministic regressions for verification-deadline semantics.

These tests replace wall-clock races with a controlled monotonic clock and an
explicit queue observation seam. Every security assertion (non-PASS on
deadline, incomplete report, durable partial evidence, strict bounds) is kept
or strengthened. No EDA tool, network or credential is used.
"""
from __future__ import annotations

import queue
import threading
import time as real_time
from pathlib import Path

import pytest

import assistant.eco as eco_module
from assistant.controller import GateController
from assistant.eco import (
    PHYSICAL_DRC_NA_REASON,
    EcoChangeScope,
    EcoManager,
    EcoStage,
    ProtectedMetric,
)
from assistant.evidence import Evidence
from assistant.hashing import sha256_file
from assistant.statuses import Status

BASELINE = "module d; endmodule  // SIMULATED golden\n"


class ControlledClock:
    """A monotonic clock the test can advance/jump deterministically."""

    def __init__(self, base: float | None = None) -> None:
        self.base = real_time.monotonic() if base is None else base
        self.jumped = threading.Event()

    def monotonic(self) -> float:
        return self.base + (10.0 if self.jumped.is_set() else 0.0)

    def advance(self, seconds: float) -> None:
        self.base += seconds

    def time(self) -> float:  # checkpoint directory naming only
        return real_time.time()

    def sleep(self, seconds: float) -> None:
        real_time.sleep(seconds)


@pytest.fixture
def env(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "design.v").write_text(BASELINE, encoding="utf-8")
    (ws / "design.sdc").write_text("create_clock -period 10 [get_ports clk]\n", encoding="utf-8")
    ctl = GateController(str(tmp_path / "state.json"))
    for fact in ("has_macros", "has_upf", "has_clock_gates", "has_analog"):
        ctl.register_design_fact(fact, False, source="SIMULATED design inventory")
    eco = EcoManager(
        str(ws), ctl, checkpoint_dir=str(tmp_path / "ckpt"),
        experiment_log=str(tmp_path / "experiments.json"),
    )
    report = tmp_path / "provider_report.txt"
    report.write_text("SIMULATED provider report\n", encoding="utf-8")
    return ws, ctl, eco, report


def _ev(check_id: str, status: Status, report: Path) -> Evidence:
    return Evidence.from_run(
        check_id, status, reason="SIMULATED EXT-02 regression",
        command=["simulated_provider", check_id], exit_code=0,
        analysis_view="MAPPED_NETLIST", tool_versions={"simulated": "1.0"},
        reports=[str(report)],
    )


def _run(eco, report, *, verify, apply_change=lambda: None, timeout=1.0):
    return eco.run_experiment(
        change_scope=EcoChangeScope("logic_optimization", EcoStage.MAPPED, False,
                                    "SIMULATED recorded implementation scope"),
        experiment_id="ext02_regression", description="SIMULATED EXT-02 regression",
        change_class="logic_optimization", changed_files=["design.v"],
        apply_change=apply_change, evaluate=lambda: {"wns": 1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0, direction="higher_is_better")],
        verify=verify, verification_timeout_s=timeout,
        simulated="EXT-02 deterministic deadline regression",
    )


def test_setup_time_does_not_consume_verification_deadline(env, monkeypatch):
    """Contract: the budget bounds verification, not pre-verification setup.

    A 10 s jump inside apply_change (before verification) would have expired a
    deadline armed at transaction start. With the fix the clock is armed at the
    verification boundary, so a normal provider still completes and all
    evidence is durable.
    """
    ws, ctl, eco, report = env
    before = sha256_file(ws / "design.v")
    clock = ControlledClock()
    monkeypatch.setattr(eco_module, "time", clock)

    def provider():
        return [
            _ev("eco_lec", Status.PASS, report),
            _ev("eco_sta", Status.PASS, report),
            Evidence.from_run(
                "eco_physical_drc", Status.NOT_APPLICABLE,
                reason="SIMULATED EXT-02 regression",
                applicability_reason=PHYSICAL_DRC_NA_REASON,
                command=["simulated_provider", "eco_physical_drc"], exit_code=0,
                analysis_view="MAPPED_NETLIST", tool_versions={"simulated": "1.0"},
                reports=[str(report)],
            ),
        ]

    result = _run(eco, report, verify=provider,
                  apply_change=lambda: clock.advance(10.0), timeout=1.0)
    assert result.status == "PASS"
    assert result.deadline_exceeded is False
    assert len(result.evidence_ids) == 3
    assert ctl.latest_evidence("eco_lec") is not None
    assert sha256_file(ws / "design.v") == before


def test_queued_partial_evidence_is_drained_on_deadline(env, monkeypatch):
    """Contract: evidence already yielded when the clock expires survives.

    The clock jump is triggered by the act of queueing the first evidence item;
    the first queue read is forced to miss that item. The ONLY way to satisfy
    the assertions is the drain-on-deadline path in _collect_verification.
    """
    ws, ctl, eco, report = env
    clock = ControlledClock()
    monkeypatch.setattr(eco_module, "time", clock)

    block = threading.Event()

    def streaming():
        yield _ev("eco_lec", Status.PASS, report)
        block.wait(2.0)  # provider hangs after the first streamed item

    orig_put = queue.Queue.put
    orig_get = queue.Queue.get
    state = {"first_get": True}

    def evidence_put(self, item, *args, **kwargs):
        if isinstance(item, tuple) and item and item[0] == "evidence":
            clock.jumped.set()  # expiry happens at the evidence boundary
        return orig_put(self, item, *args, **kwargs)

    def miss_first_get(self, *args, **kwargs):
        if state["first_get"]:
            state["first_get"] = False
            clock.jumped.wait(2.0)  # let the worker queue its evidence first
            raise queue.Empty()
        return orig_get(self, *args, **kwargs)

    monkeypatch.setattr(queue.Queue, "put", evidence_put)
    monkeypatch.setattr(queue.Queue, "get", miss_first_get)
    try:
        result = _run(eco, report, verify=streaming, timeout=1.0)
    finally:
        block.set()
        assert state["first_get"] is False, "deadline loop never reached a queue read"

    assert result.status != "PASS"
    assert result.deadline_exceeded is True
    assert result.verification.get("incomplete") is True
    assert result.verification.get("partial_evidence_drained") == 1
    assert ctl.latest_evidence("eco_lec") is not None
    assert len(result.evidence_ids) == 1
