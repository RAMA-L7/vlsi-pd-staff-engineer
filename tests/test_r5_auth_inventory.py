"""R5 authorization-path inventory -- mandatory-boundary regression tests.

Target invariant (ARCHITECTURE_DECISIONS.md, principle 2):
    A PRODUCTION-qualified PASS is possible ONLY through the managed execution
    boundary (``EcoManager.run_experiment(verification_tools=...)`` ->
    ``managed_tool_verification`` -> ``VerificationContext.spawn`` ->
    ``runner.run_managed``).  A caller-supplied raw ``verify=`` callback that
    launches no managed process may be used only for explicitly labelled
    simulation and must never yield a production PASS, durable PASS, or
    downstream execution authorization.

These tests drive the PUBLIC API and observe behaviour (returned result,
durable history, downstream callback, managed-runner call count).  They do NOT
grep source.  They are expected to FAIL on the round-5 vulnerable baseline and
to PASS once the mandatory-boundary fix is in place.  No source file is edited
by this module.

Run:
    <PY> -m pytest tests/test_r5_auth_inventory.py -v
"""
from __future__ import annotations

import sys

import pytest

from assistant.controller import GateController
from assistant.eco import (
    EcoChangeScope,
    EcoManager,
    EcoStage,
    ManagedToolSpec,
    ProtectedMetric,
)
from assistant.evidence import Evidence
from assistant.statuses import Status

CHECKS = ("eco_lec", "eco_sta", "eco_physical_drc")


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def env(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    source = ws / "design.v"
    source.write_text("module d; endmodule\n", encoding="utf-8")
    ctl = GateController(str(tmp_path / "state.json"))
    eco = EcoManager(
        str(ws), ctl,
        checkpoint_dir=str(tmp_path / "ckpt"),
        experiment_log=str(tmp_path / "experiments.json"),
    )
    return ws, ctl, eco, source


def _scope(source, *, simulated: bool = False) -> EcoChangeScope:
    # The production source must be a corroborated on-disk path.  Labelled
    # simulation may use a descriptive token (Windows paths contain '\' which
    # the simulated source regex does not accept).
    src = "r5-labelled-simulation-source" if simulated else str(source)
    return EcoChangeScope("logic_optimization", EcoStage.ROUTE, True, src)


def _fabricated_evidence(report, cid):
    """TOOL-class PASS built entirely in memory; no process is launched."""
    return Evidence.from_run(
        check_id=cid,
        status=Status.PASS,
        reason=f"UNMANAGED fabricated PASS for {cid} (no process launched)",
        command=["/nonexistent/fabricated-eda-tool", "--check", cid],
        exit_code=0,
        analysis_view="MAPPED_NETLIST",
        tool_versions={"fabricated-tool": "0.0"},
        reports=[str(report)],
    )


def _raw_unmanaged_verify(report):
    """Zero-argument provider: the classic raw verify= escape hatch."""
    def verify():
        for cid in CHECKS:
            yield _fabricated_evidence(report, cid)
    return verify


def _ctx_unmanaged_verify(report):
    """Context-aware by signature, but NEVER calls ctx.spawn -- no managed run."""
    def verify(ctx):  # noqa: ARG001 - intentionally ignores the context
        for cid in CHECKS:
            yield _fabricated_evidence(report, cid)
    return verify


def _attempt(env, source, *, verify=None, tools=None, downstream=None, simulated=""):
    """Run the public entry point; return (result_or_None, exception_or_None).

    A refusal by exception is an acceptable way to uphold the invariant, so the
    tests treat it as non-qualification rather than as an error.
    """
    _ws, _ctl, eco, _source = env
    try:
        result = eco.run_experiment(
            experiment_id="r5-auth-inventory",
            description="authorization-path inventory probe",
            change_class="logic_optimization",
            changed_files=["design.v"],
            apply_change=lambda: source.write_text(
                "module d; wire x; endmodule\n", encoding="utf-8"
            ),
            evaluate=lambda: {"wns": 0.2},
            protected_metrics=[
                ProtectedMetric("wns", 0.0, direction="higher_is_better")
            ],
            verify=verify,
            verification_tools=tools,
            downstream_stage=downstream,
            simulated=simulated,
            change_scope=_scope(source, simulated=bool(simulated.strip())),
        )
        return result, None
    except Exception as exc:  # noqa: BLE001 - refusal is a valid outcome here
        return None, exc


def _is_production_pass(result) -> bool:
    """A durable, qualified PRODUCTION acceptance -- the forbidden outcome."""
    return (
        result is not None
        and getattr(result, "status", None) == "PASS"
        and getattr(result, "qualification_class", None) == "PRODUCTION"
    )


def _durable_pass_exists(eco) -> bool:
    try:
        history = eco.history()
    except Exception:  # noqa: BLE001
        return False
    return bool(history) and history[-1].get("status") == "PASS"


def _specs() -> list[ManagedToolSpec]:
    return [
        ManagedToolSpec(
            check_id=cid,
            command=[sys.executable, "-c", "print('managed-tool ok')"],
            reason=f"managed {cid}",
            timeout_s=10.0,
        )
        for cid in CHECKS
    ]


# ---------------------------------------------------------------------------
# INVARIANT -- unmanaged verify() must not qualify PRODUCTION
# ---------------------------------------------------------------------------
def test_raw_unmanaged_verify_cannot_produce_production_pass(env, tmp_path):
    _ws, _ctl, eco, source = env
    report = tmp_path / "fabricated_report.txt"
    report.write_text("fabricated\n", encoding="utf-8")

    result, _exc = _attempt(env, source, verify=_raw_unmanaged_verify(report))

    assert not _is_production_pass(result), (
        "raw verify= with no managed process obtained a PRODUCTION PASS: "
        f"status={getattr(result, 'status', None)!r}, "
        f"qualification={getattr(result, 'qualification_class', None)!r}"
    )


def test_ctx_aware_unmanaged_verify_cannot_produce_production_pass(env, tmp_path):
    _ws, _ctl, eco, source = env
    report = tmp_path / "fabricated_report.txt"
    report.write_text("fabricated\n", encoding="utf-8")

    result, exc = _attempt(env, source, verify=_ctx_unmanaged_verify(report))

    assert not _is_production_pass(result), (
        "a ctx-aware provider that never calls ctx.spawn must not qualify "
        "PRODUCTION; got status="
        f"{getattr(result, 'status', None)!r}, "
        f"qualification={getattr(result, 'qualification_class', None)!r}"
        + (f" (refusal={exc!r})" if exc else "")
    )


def test_unmanaged_verify_cannot_authorize_downstream(env, tmp_path):
    _ws, _ctl, eco, source = env
    report = tmp_path / "fabricated_report.txt"
    report.write_text("fabricated\n", encoding="utf-8")
    downstream_calls: list[str] = []

    result, _exc = _attempt(
        env, source,
        verify=_raw_unmanaged_verify(report),
        downstream=lambda r: downstream_calls.append(getattr(r, "status", "?")),
    )

    assert downstream_calls == [], (
        "an unmanaged verify() result authorized physical/downstream execution: "
        f"{downstream_calls}"
    )


def test_unmanaged_verify_cannot_leave_durable_pass(env, tmp_path):
    _ws, _ctl, eco, source = env
    report = tmp_path / "fabricated_report.txt"
    report.write_text("fabricated\n", encoding="utf-8")

    _result, _exc = _attempt(env, source, verify=_raw_unmanaged_verify(report))

    assert not _durable_pass_exists(eco), (
        "an unmanaged verify() produced a durable on-disk PASS"
    )


def test_production_pass_requires_managed_execution_marker(env, tmp_path, monkeypatch):
    """If no managed process ran, a PRODUCTION PASS is forbidden."""
    _ws, _ctl, eco, source = env
    report = tmp_path / "fabricated_report.txt"
    report.write_text("fabricated\n", encoding="utf-8")

    import assistant.runner as runner

    managed_calls: list[object] = []
    real_run_managed = runner.run_managed

    def spy(command, **kw):
        managed_calls.append(kw.get("name", command))
        return real_run_managed(command, **kw)

    monkeypatch.setattr(runner, "run_managed", spy)
    result, _exc = _attempt(env, source, verify=_raw_unmanaged_verify(report))

    assert managed_calls == [], "test premise: raw verify= must launch no process"
    marker = (getattr(result, "verification", None) or {}).get("managed_execution")
    assert not (_is_production_pass(result) and marker is None), (
        "a PRODUCTION PASS was produced with no managed_execution evidence"
    )


def test_production_pass_requires_at_least_one_managed_run(env, tmp_path, monkeypatch):
    """Composite behavioural check: production PASS => run_managed was called."""
    _ws, _ctl, eco, source = env
    report = tmp_path / "fabricated_report.txt"
    report.write_text("fabricated\n", encoding="utf-8")

    import assistant.runner as runner

    managed_calls: list[object] = []
    real_run_managed = runner.run_managed

    def spy(command, **kw):
        managed_calls.append(kw.get("name", command))
        return real_run_managed(command, **kw)

    monkeypatch.setattr(runner, "run_managed", spy)
    result, _exc = _attempt(env, source, verify=_raw_unmanaged_verify(report))

    if _is_production_pass(result):
        assert managed_calls, (
            "PRODUCTION PASS obtained with runner.run_managed call count 0"
        )


# ---------------------------------------------------------------------------
# CONTROLS -- ensure the fix does not break legitimate paths
# ---------------------------------------------------------------------------
def test_labelled_simulation_may_use_unmanaged_verify(env, tmp_path):
    """Explicit simulation is the ONLY permitted home for raw verify=."""
    _ws, _ctl, eco, source = env
    report = tmp_path / "fabricated_report.txt"
    report.write_text("fabricated\n", encoding="utf-8")

    result, exc = _attempt(
        env, source,
        verify=_raw_unmanaged_verify(report),
        simulated="r5-auth-inventory-labelled-simulation",
    )

    assert exc is None, f"labelled simulation should remain usable: {exc!r}"
    assert result is not None
    assert result.qualification_class == "SIMULATED", result.qualification_class


def test_managed_verification_tools_still_qualify_production(env, tmp_path, monkeypatch):
    """Positive control: the managed boundary must still produce a PASS."""
    _ws, _ctl, eco, source = env

    import assistant.runner as runner

    managed_calls: list[object] = []
    real_run_managed = runner.run_managed

    def spy(command, **kw):
        managed_calls.append(kw.get("name", command))
        return real_run_managed(command, **kw)

    monkeypatch.setattr(runner, "run_managed", spy)
    result, exc = _attempt(env, source, tools=_specs())

    assert exc is None, f"managed production path regressed: {exc!r}"
    assert _is_production_pass(result), (
        "the managed path must still be able to qualify PRODUCTION: "
        f"status={getattr(result, 'status', None)!r}, reason={getattr(result, 'reason', None)!r}"
    )
    assert len(managed_calls) == len(CHECKS), managed_calls
    marker = result.verification.get("managed_execution")
    assert marker and marker.get("authorization_safe") is True, marker
