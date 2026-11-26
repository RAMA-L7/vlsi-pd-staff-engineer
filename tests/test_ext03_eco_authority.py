"""EXT-03 RUNTIME-PATH integration tests for ECO acceptance authority.

These tests drive the REAL production entry point
``EcoManager.run_experiment(verification_tools=...)`` -- including the real
managed runner, real evidence recording, the real gate authority and the real
durable ECO log.  They are NOT selftests of ``state_machine`` or
``r6_authority`` in isolation: authority invocation is observed through the
acceptance call path and asserted on both the returned result and the durable
record.

Run:  <PY> -m pytest tests/test_ext03_eco_authority.py -s -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import assistant.r6_authority as ra
import assistant.state_machine as sm
from assistant.controller import GateController
from assistant.eco import (
    EcoChangeScope,
    EcoManager,
    EcoStage,
    ManagedToolSpec,
    ProtectedMetric,
)
from assistant.evidence import Evidence
from assistant.runner import CONTAINMENT_CLEAN, RunResult
from r63_binding_helper import trusted_proof_for

CHECKS = ("eco_lec", "eco_sta", "eco_physical_drc")
PY = r"D:\tools\venv\Scripts\python.exe"
BASELINE = "module d; endmodule\n"


@pytest.fixture
def env(tmp_path):
    # Use the process trust root as production does; do not inject a test key
    # so the managed runner signs proofs with the same root the gate verifies.
    ws = tmp_path / "ws"
    ws.mkdir()
    source = ws / "design.v"
    source.write_text(BASELINE, encoding="utf-8")
    ctl = GateController(str(tmp_path / "state.json"))
    eco = EcoManager(
        str(ws), ctl,
        checkpoint_dir=str(tmp_path / "ckpt"),
        experiment_log=str(tmp_path / "experiments.json"),
    )
    yield ws, source, ctl, eco, tmp_path


def _specs() -> list[ManagedToolSpec]:
    return [
        ManagedToolSpec(
            check_id=cid,
            command=[PY, "-c", "print('managed-tool ok')"],
            reason=f"ext03 managed {cid}",
            timeout_s=10.0,
        )
        for cid in CHECKS
    ]


def _run(env, *, tools=None, verify=None, simulated="", analysis_mode=False,
         downstream=None, experiment_id="ext03-acceptance", source_label=None):
    ws, source, _ctl, eco, _tmp = env
    return eco.run_experiment(
        experiment_id=experiment_id,
        description="EXT-03 runtime acceptance probe",
        change_class="logic_optimization",
        changed_files=["design.v"],
        apply_change=lambda: source.write_text(
            "module d; wire x; endmodule\n", encoding="utf-8"
        ),
        evaluate=lambda: {"wns": 0.2},
        protected_metrics=[ProtectedMetric(
            "wns", 0.0, direction="higher_is_better"
        )],
        verification_tools=tools,
        verify=verify,
        simulated=simulated,
        analysis_mode=analysis_mode,
        downstream_stage=downstream,
        change_scope=EcoChangeScope(
            "logic_optimization", EcoStage.ROUTE, True,
            source_label or str(source),
        ),
    )


def _run_no_op(env, *, experiment_id="ext03-noop", downstream=None):
    _ws, source, _ctl, eco, _tmp = env
    return eco.run_experiment(
        experiment_id=experiment_id,
        description="EXT-03 verification-free route",
        change_class="no_op",
        changed_files=["design.v"],
        apply_change=lambda: None,
        evaluate=lambda: {"wns": 1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0)],
        verification_tools=None,
        verify=None,
        change_scope=EcoChangeScope(
            "no_op", EcoStage.MAPPED, False, "EXT-03 recorded no-op scope"
        ),
        downstream_stage=downstream,
    )


def _spy_authority(monkeypatch):
    """Observe the gate authority AND the state-machine decision at runtime."""
    calls = {"authority": [], "derive": []}
    real_authority = ra.authoritative_qualification
    real_derive = sm.derive_authorization

    def authority(controller, eids, **kw):
        calls["authority"].append((list(eids), dict(kw)))
        return real_authority(controller, eids, **kw)

    def derive(*args, **kw):
        decision = real_derive(*args, **kw)
        calls["derive"].append(bool(decision.authorized))
        return decision

    monkeypatch.setattr(ra, "authoritative_qualification", authority)
    monkeypatch.setattr(sm, "derive_authorization", derive)
    return calls


def _durable(env):
    _ws, _source, _ctl, eco, _tmp = env
    return eco.history()[-1]


# ---------------------------------------------------------------------------
# 1. Positive control -- real managed path really invokes the authority.
# ---------------------------------------------------------------------------
def test_real_managed_acceptance_invokes_authority(env, monkeypatch):
    calls = _spy_authority(monkeypatch)
    result = _run(env, tools=_specs())

    assert result.status == "PASS", result.reason
    assert result.qualification_class == "PRODUCTION"
    assert len(calls["authority"]) == 1, calls["authority"]
    assert calls["derive"] and calls["derive"][-1] is True, calls["derive"]
    eids, kw = calls["authority"][0]
    assert len(eids) == len(CHECKS)
    assert set(kw["check_bindings"]) == set(CHECKS)
    assert set(kw["check_bindings"].values()) == set(eids)
    assert result.verification["authorization"]["authoritative"] is True
    durable = _durable(env)
    assert durable["status"] == "PASS"
    assert durable["qualification_class"] == "PRODUCTION"


# ---------------------------------------------------------------------------
# 2. Authority denial controls the claim on the DOWNSTREAM route.
# ---------------------------------------------------------------------------
def test_authority_denial_blocks_production_claim_with_downstream(env, monkeypatch):
    calls = _spy_authority(monkeypatch)
    denied = {"calls": 0}

    def deny(controller, eids, **kw):
        denied["calls"] += 1
        return False

    monkeypatch.setattr(ra, "authoritative_qualification", deny)
    downstream_payloads = []
    result = _run(
        env, tools=_specs(),
        downstream=lambda r: downstream_payloads.append(r.qualification_class),
    )

    assert result.status == "PASS", result.reason
    assert result.qualification_class == "UNQUALIFIED"
    assert downstream_payloads == ["UNQUALIFIED"], downstream_payloads
    assert result.verification["authorization"]["authoritative"] is False
    durable = _durable(env)
    assert durable["status"] == "PASS"
    assert durable["qualification_class"] == "UNQUALIFIED"
    assert denied["calls"] == 1  # authority consulted on the downstream route


# ---------------------------------------------------------------------------
# 3. Verification-free production route cannot mint PRODUCTION.
# ---------------------------------------------------------------------------
def test_no_op_production_route_is_not_authorizing(env, monkeypatch):
    calls = _spy_authority(monkeypatch)
    result = _run_no_op(env)

    assert result.status == "PASS", result.reason
    assert result.qualification_class == "UNQUALIFIED"
    assert result.verification["authorization"]["authoritative"] is False
    assert "no authorizing tool evidence" in (
        result.verification["authorization"]["decision"]
    )
    assert calls["authority"] == [], calls["authority"]
    durable = _durable(env)
    assert durable["qualification_class"] == "UNQUALIFIED"


# ---------------------------------------------------------------------------
# 4. A TRUSTED proof for the WRONG CHECK cannot authorize ECO acceptance.
# ---------------------------------------------------------------------------
def test_cross_check_proof_cannot_authorize_acceptance(env, monkeypatch, tmp_path):
    calls = _spy_authority(monkeypatch)
    import assistant.runner as runner

    logs = tmp_path / "managed_logs"
    logs.mkdir(exist_ok=True)

    def fake_run(command, **kw):
        name = kw.get("name", "tool")
        for suffix in ("", ".stdout", ".stderr"):
            (logs / (name + suffix + ".log")).write_text(
                "ext03 fake managed run\n", encoding="utf-8"
            )
        # Proof is MAC-valid and TRUSTED, but issued for eco_sta only; using it
        # for eco_lec / eco_physical_drc must be refused by derive_authorization.
        proof = trusted_proof_for(command, check_id="eco_sta")
        return RunResult(
            command=list(command), exit_code=0, timed_out=False,
            duration_s=0.01,
            stdout_log=str(logs / (name + ".stdout.log")),
            stderr_log=str(logs / (name + ".stderr.log")),
            combined_log=str(logs / (name + ".log")),
            cwd=str(env[0]), containment=CONTAINMENT_CLEAN,
            containment_reason="clean_exit", execution_proof=proof,
        )

    monkeypatch.setattr(runner, "run_managed", fake_run)
    result = _run(env, tools=_specs())

    assert result.status == "PASS", result.reason
    assert result.qualification_class == "UNQUALIFIED"
    assert len(calls["authority"]) == 1, calls["authority"]
    assert calls["derive"] and calls["derive"][-1] is False, calls["derive"]
    assert result.verification["authorization"]["authoritative"] is False
    durable = _durable(env)
    assert durable["qualification_class"] == "UNQUALIFIED"
    assert env[2]._state.get("used_proof_nonces") == {}  # no nonce consumed


# ---------------------------------------------------------------------------
# 5. Labelled simulation route remains explicitly non-PRODUCTION.
# ---------------------------------------------------------------------------
def test_labelled_simulation_never_claims_production(env, monkeypatch, tmp_path):
    calls = _spy_authority(monkeypatch)
    report = tmp_path / "sim_report.txt"
    report.write_text("ext03 simulated report\n", encoding="utf-8")

    def verify():
        for cid in CHECKS:
            yield Evidence.from_run(
                cid, "PASS", reason="EXT-03 simulated evidence",
                command=["sim"], exit_code=0, analysis_view="MAPPED_NETLIST",
                tool_versions={"sim": "1"}, reports=[str(report)],
            )

    result = _run(
        env, verify=verify, simulated="EXT-03 labelled simulation",
        source_label="EXT-03 labelled simulated source",
    )

    assert result.status == "PASS", result.reason
    assert result.qualification_class == "SIMULATED"
    assert result.verification["authorization"]["authoritative"] is False
    assert calls["authority"] == [], calls["authority"]
    assert _durable(env)["qualification_class"] == "SIMULATED"
