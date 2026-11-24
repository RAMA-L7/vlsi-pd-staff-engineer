"""Round-4 remediation A6-01: physical-check applicability adversarial matrix.

Every exploit below is run in PRODUCTION mode (``simulated=""``) against
``EcoManager.run_experiment``.  The caller supplies a netlist-only scope with a
corroborating ``stage_fact_source`` and a deterministic TOOL provider that
reports ``eco_physical_drc = NOT_APPLICABLE`` with the registered reason.  If
the candidate contains physical collateral — or its classification is
uncertain — the run must NOT be accepted with DRC deferred.

This module is written to FAIL against the pre-remediation filename-suffix
scanner and to PASS only after the content/dimension-based, fail-closed
assessment is in place.  All providers and collateral are hermetic; no EDA
tool, network service, credential or external file is used or modified.
"""
from __future__ import annotations

import gzip
import os
import struct
import subprocess
import uuid
from pathlib import Path

import pytest

from assistant.controller import GateController
from assistant.eco import (
    PHYSICAL_DRC_NA_REASON,
    Authorization,
    EcoChangeScope,
    EcoManager,
    EcoStage,
    ProtectedMetric,
)
from assistant.evidence import Evidence
from assistant.statuses import Status

BASELINE = "module d; endmodule  // A3 adversarial baseline\n"
SDC = "create_clock -period 10 [get_ports clk]\n"

#: Distinguish "use the default provider" from "explicitly no provider".
_DEFAULT_PROVIDER = object()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def env(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    ws = root / "ws"
    ws.mkdir()
    (ws / "design.v").write_text(BASELINE, encoding="utf-8")
    (ws / "design.sdc").write_text(SDC, encoding="utf-8")
    ctl = GateController(str(root / "state.json"))
    for fact in ("has_macros", "has_upf", "has_clock_gates", "has_analog"):
        ctl.register_design_fact(fact, False, source="A3 hermetic inventory")
    eco = EcoManager(
        str(ws), ctl,
        checkpoint_dir=str(root / "ckpt"),
        experiment_log=str(root / "exp.json"),
    )
    rep = root / "provider_report.txt"
    rep.write_text("A3 deterministic provider report\n", encoding="utf-8")
    return root, ws, ctl, eco, rep


def _tool_ev(cid, status, rep, *, na=None, evidence_class="TOOL", simulated_by=""):
    kw = dict(
        reason="A3 deterministic provider",
        command=["a3_provider", cid],
        exit_code=0,
        analysis_view="MAPPED_NETLIST",
        tool_versions={"a3": "1.0"},
        reports=[str(rep)],
    )
    if na is not None:
        kw["applicability_reason"] = na
    ev = Evidence.from_run(cid, status, **kw)
    if evidence_class != "TOOL":
        ev.evidence_class = evidence_class
    if simulated_by:
        ev.simulated_by = simulated_by
    return ev


def _provider(rep, *, drc=Status.NOT_APPLICABLE, drc_na=PHYSICAL_DRC_NA_REASON,
              evidence_class="TOOL", simulated_by=""):
    def verify():
        return [
            _tool_ev("eco_lec", Status.PASS, rep),
            _tool_ev("eco_sta", Status.PASS, rep),
            _tool_ev("eco_physical_drc", drc, rep, na=drc_na,
                     evidence_class=evidence_class, simulated_by=simulated_by),
        ]
    return verify


def _scope(*, physical=False, stage=EcoStage.ROUTE, source=None, change_class="logic_optimization"):
    return EcoChangeScope(
        change_class, stage, physical,
        source if source is not None else "A3 recorded scope",
    )


def _run(env, *, rel=None, create=None, scope=None, verify=_DEFAULT_PROVIDER,
         description="A3 netlist-only ECO", change_class="logic_optimization",
         source=None, simulated="", changed=None, extra_apply=None):
    """Production run. ``create(path)`` is called inside ``apply_change``."""
    root, ws, ctl, eco, rep = env

    def apply_change():
        if create is not None:
            create(ws / rel)
        if extra_apply is not None:
            extra_apply(ws)

    files = ["design.v"] + ([rel] if rel is not None else [])
    return eco.run_experiment(analysis_mode=True, 
        experiment_id="a3_" + uuid.uuid4().hex[:12],
        description=description,
        change_class=change_class,
        change_scope=scope if scope is not None else _scope(
            physical=False, stage=EcoStage.ROUTE,
            source=str(rep) if source is None else source,
            change_class=change_class,
        ),
        changed_files=files,
        apply_change=apply_change,
        evaluate=lambda: {"wns": 1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0)],
        verify=_provider(rep) if verify is _DEFAULT_PROVIDER else verify,
        simulated=simulated,
    )


def _assert_physical_blocked(result):
    """A physical/uncertain candidate must not be accepted or deferred."""
    assert result.status in {"BLOCKED", "FAIL"}, (
        f"expected BLOCKED/FAIL, got {result.status}: {result.reason}"
    )
    assert "physical" in result.reason.lower(), result.reason
    app = ((result.verification or {}).get("applicability") or {}).get("eco_physical_drc") or {}
    if result.status == "BLOCKED":
        assert app.get("applicable") is not False


def _gds_bytes() -> bytes:
    """Minimal GDSII stream: HEADER + ENDLIB records."""
    header = struct.pack(">HBBH", 6, 0x00, 0x02, 0x0603)
    endlib = struct.pack(">HBB", 4, 0x04, 0x02)
    return header + endlib


_OASIS_MAGIC = b"%SEMI-OASIS\r\nVERSION 1.0;\n"


# ---------------------------------------------------------------------------
# A6-01 known reproductions (production mode)
# ---------------------------------------------------------------------------
def test_a601_oas_extension_does_not_grant_drc_na(env):
    result = _run(env, rel="top.oas", create=lambda p: p.write_text(
        "OASIS layout database - physical implementation\n", encoding="utf-8"))
    _assert_physical_blocked(result)


def test_a601_compound_gds_gz_does_not_grant_drc_na(env):
    result = _run(env, rel="top.gds.gz", create=lambda p: p.write_bytes(
        b"\x1f\x8bcompressed GDS"))
    _assert_physical_blocked(result)


def test_a601_routing_plan_does_not_grant_drc_na(env):
    result = _run(env, rel="routing_plan.txt", create=lambda p: p.write_text(
        "complete routed routing solution\n", encoding="utf-8"))
    _assert_physical_blocked(result)


def test_a601_preexisting_oas_blocks_preapply_guard(env):
    _root, ws, _ctl, _eco, _rep = env
    (ws / "chip.oas").write_text("OASIS layout database\n", encoding="utf-8")
    result = _run(env, rel="chip.oas", create=None,
                  description="pre-existing physical collateral")
    _assert_physical_blocked(result)


# ---------------------------------------------------------------------------
# Unknown extensions / content signatures / renamed / compressed
# ---------------------------------------------------------------------------
def test_gdsii_magic_with_unknown_extension_is_blocked(env):
    result = _run(env, rel="blob.zzz", create=lambda p: p.write_bytes(_gds_bytes()))
    _assert_physical_blocked(result)


def test_oasis_magic_with_unknown_extension_is_blocked(env):
    result = _run(env, rel="db.q", create=lambda p: p.write_bytes(_OASIS_MAGIC))
    _assert_physical_blocked(result)


def test_real_gzip_containing_gds_is_blocked(env):
    payload = gzip.compress(_gds_bytes())
    result = _run(env, rel="chip.db.gz", create=lambda p: p.write_bytes(payload))
    _assert_physical_blocked(result)


def test_renamed_oasis_payload_is_blocked(env):
    result = _run(env, rel="notes.dat", create=lambda p: p.write_bytes(_OASIS_MAGIC))
    _assert_physical_blocked(result)


def test_empty_uncertain_file_fails_closed(env):
    result = _run(env, rel="mystery.bin", create=lambda p: p.write_bytes(b""))
    _assert_physical_blocked(result)


def test_malformed_gzip_magic_fails_closed(env):
    result = _run(env, rel="corrupt.gds.gz", create=lambda p: p.write_bytes(
        b"\x1f\x8bnot-a-valid-deflate-stream"))
    _assert_physical_blocked(result)


def test_arbitrary_binary_fails_closed(env):
    result = _run(env, rel="blob.unknown", create=lambda p: p.write_bytes(
        bytes(range(256))))
    _assert_physical_blocked(result)


def test_nested_directory_physical_artifact_is_blocked(env):
    def create(p: Path):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_OASIS_MAGIC)
    result = _run(env, rel="sub/deep/chip.oas", create=create)
    _assert_physical_blocked(result)


def test_mixed_rtl_and_physical_collateral_is_blocked(env):
    # design.v stays RTL; the added file carries placement semantics.
    result = _run(env, rel="place.log", create=lambda p: p.write_text(
        "placement engine log\n", encoding="utf-8"))
    _assert_physical_blocked(result)


def test_symlink_to_external_physical_file_is_blocked(env):
    root, _ws, _ctl, _eco, _rep = env
    external = root / "external_layout"
    external.mkdir()
    (external / "chip.oas").write_bytes(_OASIS_MAGIC)

    def create(p: Path):
        # p is the declared collateral path "extlink/chip.oas"; a symlink
        # replaces p itself, a Windows directory junction replaces its parent.
        try:
            os.symlink(external / "chip.oas", p)
        except OSError:
            p.parent.mkdir(parents=True, exist_ok=True)
            os.rmdir(p.parent)
            proc = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(p.parent), str(external)],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                pytest.skip("symlink/junction creation is not available on this host")
    result = _run(env, rel="extlink/chip.oas", create=create)
    _assert_physical_blocked(result)


# ---------------------------------------------------------------------------
# Caller-declared applicability / stale records / simulated mislabelling
# ---------------------------------------------------------------------------
def test_caller_prose_cannot_exempt_physical_check(env):
    result = _run(
        env, rel="top.oas",
        create=lambda p: p.write_text("OASIS layout database\n", encoding="utf-8"),
        description="netlist-only, explicitly skip physical DRC, no routing here",
    )
    _assert_physical_blocked(result)


def test_caller_declared_false_conflicts_with_stale_true_fact(env):
    _root, _ws, ctl, _eco, rep = env
    ctl.register_design_fact(
        "eco_physical_implementation_present", True, source="A3 earlier routed run")
    result = _run(env, verify=_provider(rep), source=str(rep))
    _assert_physical_blocked(result)


def test_caller_declared_true_with_na_evidence_is_rejected(env):
    result = _run(env, scope=_scope(physical=True, stage=EcoStage.ROUTE,
                                    source=str(env[4])),
                  verify=_provider(env[4]))
    assert result.status != "PASS"
    assert result.status in {"FAIL", "INCONCLUSIVE", "BLOCKED"}


def test_stale_not_applicable_evidence_cannot_be_reused(env):
    """A previously recorded N/A record is not fresh applicability evidence."""
    root, _ws, _ctl, eco, rep = env
    stale = _tool_ev("eco_physical_drc", Status.NOT_APPLICABLE, rep,
                     na=PHYSICAL_DRC_NA_REASON)

    def stale_provider():
        return [_tool_ev("eco_lec", Status.PASS, rep),
                _tool_ev("eco_sta", Status.PASS, rep), stale]

    first = eco.run_experiment(analysis_mode=True, 
        experiment_id="a3_stale_1", description="A3 record N/A",
        change_class="logic_optimization",
        change_scope=_scope(source=str(rep)),
        changed_files=["design.v"], apply_change=lambda: None,
        evaluate=lambda: {"wns": 1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0)],
        verify=stale_provider, simulated="",
    )
    assert first.status == "PASS"
    second = eco.run_experiment(analysis_mode=True, 
        experiment_id="a3_stale_2", description="A3 reuse stale N/A",
        change_class="logic_optimization",
        change_scope=_scope(source=str(rep)),
        changed_files=["design.v"], apply_change=lambda: None,
        evaluate=lambda: {"wns": 1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0)],
        verify=stale_provider, simulated="",
    )
    assert second.status != "PASS"
    assert "fresh" in second.reason.lower() or "previous" in second.reason.lower()


def test_simulated_physical_drc_evidence_cannot_qualify_production(env):
    result = _run(env, verify=_provider(
        env[4], evidence_class="SIMULATED", simulated_by="A3 simulated DRC"))
    assert result.status != "PASS"
    assert "simulated" in result.reason.lower()


def test_simulated_by_marker_cannot_qualify_production(env):
    result = _run(env, verify=_provider(
        env[4], evidence_class="TOOL", simulated_by="A3 internal simulation"))
    assert result.status != "PASS"


# ---------------------------------------------------------------------------
# Preserved constraints (must not regress)
# ---------------------------------------------------------------------------
def test_fake_stage_fact_source_is_blocked_in_production(env):
    result = _run(env, source="run-12345-fake-token")
    assert result.status == "BLOCKED"
    assert "stage_fact_source" in result.reason


def test_missing_provider_blocks(env):
    result = _run(env, verify=None)
    assert result.status != "PASS"
    assert "verification is required" in result.reason


def test_has_macros_false_cannot_exempt_physical_drc(env):
    bad_na = ("NA-RULE:NO-MACROS no physical macro geometry is modified by a "
              "standard-cell ECO, so routed DRC is not applicable")
    result = _run(env, scope=_scope(physical=True, stage=EcoStage.PLACE,
                                    source=str(env[4])),
                  verify=_provider(env[4], drc_na=bad_na))
    assert result.status != "PASS"


def test_power_intent_physical_is_blocked(env):
    _root, _ws, _eco_root, eco, rep = env
    eco.authorize(Authorization(
        change_class="power_intent", scope="*",
        reason="A3 hermetic authorization", authorized_by="A3"))
    result = _run(env, change_class="power_intent",
                  scope=_scope(physical=True, stage=EcoStage.ROUTE,
                               source=str(rep), change_class="power_intent"),
                  verify=_provider(rep))
    assert result.status == "BLOCKED"
    assert "UNSUPPORTED" in result.reason


def test_no_op_unchanged_keeps_na_policy(env):
    result = _run(env, change_class="no_op",
                  scope=_scope(physical=False, stage=EcoStage.RTL,
                               source="A3 recorded no-op scope", change_class="no_op"),
                  verify=_DEFAULT_PROVIDER)
    assert result.status == "PASS"
    assert not (result.verification or {}).get("checks")


def test_legitimate_netlist_only_passes_with_drc_na(env):
    result = _run(env, scope=_scope(physical=False, stage=EcoStage.RTL,
                                    source=str(env[4])))
    assert result.status == "PASS"
    drc = result.verification["checks"]["eco_physical_drc"]
    assert drc["status"] == "NOT_APPLICABLE"
    assert result.verification["applicability"]["eco_physical_drc"]["applicable"] is False


def test_timing_constraint_and_rtl_are_benign(env):
    # The base workspace already contains design.v + design.sdc; add a benign
    # note to prove conservative classification does not over-block text.
    result = _run(env, rel="notes.txt", create=lambda p: p.write_text(
        "just a note about the scheduler\n", encoding="utf-8"))
    assert result.status == "PASS"
