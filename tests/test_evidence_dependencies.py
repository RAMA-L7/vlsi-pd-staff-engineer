"""SR-04 / SR-05 regression tests: unified dependencies, binding, view contracts.

All state lives in ``tmp_path`` and hashes are computed from real (or
simulated, where noted) files; no EDA tools, network or credentials are used.
"""
from __future__ import annotations

import os

import pytest

from assistant import evidence as ev_mod
from assistant.evidence import (
    BindingContext,
    Dependency,
    DependencySet,
    Evidence,
    StaleOutputError,
    ViewContract,
    artifact_path,
    assert_clean_output_dir,
    contract_compatible,
    DEPENDENCY_CATEGORIES,
    post_fill_proof_ok,
    prepare_run_output_dir,
    revalidate_evidence,
    view_compatible,
)
from assistant.hashing import sha256_file
from assistant.statuses import Status


def _mk(path, text="content\n"):
    path = str(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# ---------------------------------------------------------------------------
# SR-04 h3/h4/h5: unified dependency model with persistent paths + hashes
# ---------------------------------------------------------------------------
def test_dependency_set_covers_all_categories_and_persists_paths(tmp_path):
    files = {}
    for category in DEPENDENCY_CATEGORIES:
        if category == "tool":
            continue
        files[category] = {category: _mk(tmp_path / f"{category}.dat", category)}
    deps = DependencySet.from_paths(files)
    deps.add_tool("sta", "3.1")

    assert set(deps.keys()) == (
        {f"{c}:{c}" for c in DEPENDENCY_CATEGORIES if c != "tool"} | {"tool:sta"}
    )
    for dep in deps:
        assert os.path.isabs(dep.path) or dep.category == "tool"
        expected = sha256_file(dep.path) if dep.category != "tool" else dep.sha256
        assert dep.sha256 == expected
    tool = [d for d in deps if d.category == "tool"][0]
    assert tool.value == "3.1" and tool.sha256

    # Serialization round-trip preserves paths and hashes.
    restored = DependencySet.from_dict(deps.to_dict())
    assert restored.manifest() == deps.manifest()
    assert restored.hashes() == deps.hashes()


def test_dependency_rehash_detects_modified_file(tmp_path):
    p = _mk(tmp_path / "rtl.v", "module a; endmodule\n")
    deps = DependencySet.from_paths({"rtl": {"top": p}})
    assert deps.validate_current() == []

    with open(p, "w", encoding="utf-8") as f:
        f.write("module a; // changed\n")
    problems = deps.validate_current()
    assert any("modified" in p2 for p2 in problems)


def test_dependency_rehash_detects_missing_and_unreadable(tmp_path, monkeypatch):
    p = _mk(tmp_path / "sdc.sdc", "create_clock\n")
    deps = DependencySet.from_paths({"sdc": {"corner_tt": p}})

    os.remove(p)
    problems = deps.validate_current()
    assert any("missing" in x for x in problems)

    # Simulate an unreadable-but-present file by forcing the hasher to fail.
    _mk(tmp_path / "locked.lib", "liberty\n")
    dep = DependencySet.from_paths({"library": {"tt": str(tmp_path / "locked.lib")}})

    def boom(path):
        raise OSError("permission denied")

    monkeypatch.setattr(ev_mod, "sha256_file", boom)
    problems = dep.validate_current()
    assert any("unreadable" in x for x in problems)


def test_dependency_validate_current_rejects_rebind_and_manifest_drift(tmp_path):
    p = _mk(tmp_path / "design.v", "module d; endmodule\n")
    recorded = DependencySet.from_paths({"rtl": {"dut": p}})

    # New input hashes for a different run: old evidence must not be re-bound.
    new_hashes = DependencySet([Dependency("rtl", "dut", recorded.manifest()["rtl:dut"][2], "deadbeef")])
    problems = recorded.validate_current(new_hashes)
    assert any("rebind refused" in x for x in problems)

    # Re-pointing the same label to a different file is a rebind too.
    other = _mk(tmp_path / "other.v", "module other; endmodule\n")
    repointed = DependencySet.from_paths({"rtl": {"dut": other}})
    assert any("rebind" in x for x in recorded.validate_current(repointed))

    # Required dependency absent from the recorded manifest.
    extra = DependencySet.from_paths({"rtl": {"dut": p}, "sdc": {"tt": _mk(tmp_path / "c.sdc")}})
    assert any("not bound" in x for x in recorded.validate_current(extra))


# ---------------------------------------------------------------------------
# SR-04 h1/h2: rehash dependencies and artifacts at evaluation time
# ---------------------------------------------------------------------------
def test_artifact_rehashing_detects_deleted_and_modified_reports(tmp_path):
    rpt = _mk(tmp_path / "sta.rpt", "WNS: 0.5\n")
    ev = Evidence.from_run(
        "sta", Status.PASS, "ok", analysis_view="ROUTED_EXTRACTED", reports=[rpt]
    )
    assert ev.revalidate_artifacts() == []

    # Legacy metadata-only check does NOT notice a changed file (SR-04 h2).
    with open(rpt, "w", encoding="utf-8") as f:
        f.write("WNS: -9.0\n")
    assert ev.missing_report_artifacts() == []  # stale metadata
    assert any("modified" in x for x in ev.revalidate_artifacts())

    os.remove(rpt)
    assert any("missing" in x for x in ev.revalidate_artifacts())


def test_revalidate_evidence_combines_all_checks(tmp_path):
    rtl = _mk(tmp_path / "rtl.v", "module d; endmodule\n")
    rpt = _mk(tmp_path / "sta.rpt", "WNS: 0.5\n")
    deps = DependencySet.from_paths({"rtl": {"dut": rtl}})
    ctx = BindingContext("run-1", "designA", "route", "exec-1", "ROUTED_EXTRACTED", "tt")
    out_dir = str(tmp_path / "out")
    os.makedirs(out_dir, exist_ok=True)
    inside = _mk(os.path.join(out_dir, "sta.rpt"), "WNS: 0.5\n")

    ev = Evidence.from_run(
        "sta", Status.PASS, "ok", analysis_view="ROUTED_EXTRACTED",
        reports=[inside], dependencies=deps, binding_context=ctx,
    )
    assert revalidate_evidence(
        ev, expected_dependencies=DependencySet.from_dict(deps.to_dict()),
        expected_context=ctx, expected_output_dir=out_dir,
    ) == []

    # A report outside the run output dir, a modified input and a wrong design
    # are all flagged together.
    ev.reports = [{"path": rpt, "sha256": sha256_file(rpt)}]
    with open(rpt, "w", encoding="utf-8") as f:
        f.write("WNS: -9.0\n")
    with open(rtl, "w", encoding="utf-8") as f:
        f.write("module d; // edited\n")
    bad_ctx = BindingContext("run-1", "designB", "route", "exec-1", "ROUTED_EXTRACTED", "ff")
    problems = revalidate_evidence(
        ev, expected_dependencies=DependencySet.from_dict(deps.to_dict()),
        expected_context=bad_ctx, expected_output_dir=out_dir,
    )
    assert any("artifact modified" in x for x in problems)
    assert any("artifact outside run output" in x for x in problems)
    assert any("design" in x for x in problems)
    assert any("corner" in x for x in problems)


# ---------------------------------------------------------------------------
# SR-04 h7/h9: binding to run/design/stage/tool/view/corner/provenance
# ---------------------------------------------------------------------------
def test_binding_rejects_cross_design_and_cross_corner(tmp_path):
    good = BindingContext("run-1", "designA", "route", "exec-1", "ROUTED_EXTRACTED", "tt")
    ev = Evidence.from_run("sta", Status.PASS, "ok", binding_context=good)
    assert ev.binding_errors(good) == []

    cross_design = BindingContext("run-1", "designB", "route", "exec-1", "ROUTED_EXTRACTED", "tt")
    assert any("design" in x for x in ev.binding_errors(cross_design))

    cross_corner = BindingContext("run-1", "designA", "route", "exec-1", "ROUTED_EXTRACTED", "ff")
    assert any("corner" in x for x in ev.binding_errors(cross_corner))


def test_binding_rejects_stage_tool_view_and_provenance_mismatch():
    good = BindingContext(
        "run-1", "designA", "fill", "exec-1", "FILL_EXTRACTED", "ss",
        "lib-tt-v2", "pex-deck-v3",
    )
    ev = Evidence.from_run("sta", Status.PASS, "ok", binding_context=good)
    for field, value in (
        ("stage", "route"),
        ("tool_execution_id", "exec-2"),
        ("analysis_view", "ROUTED_EXTRACTED"),
        ("library_provenance", "lib-tt-v1"),
        ("extraction_provenance", "pex-deck-v2"),
    ):
        kwargs = good.to_dict()
        kwargs[field] = value
        problems = ev.binding_errors(BindingContext.from_dict(kwargs))
        assert any(field in x for x in problems), (field, problems)

    # Unbound corner cannot satisfy a corner-specific gate.
    ev.corner = ""
    assert any("corner" in x for x in ev.binding_errors(good))


def test_evidence_new_binding_and_dependency_fields_roundtrip(tmp_path):
    p = _mk(tmp_path / "rtl.v", "module x; endmodule\n")
    deps = DependencySet.from_paths({"rtl": {"x": p}})
    ctx = BindingContext("r", "d", "place", "e", "PLACE_ESTIMATE", "tt")
    ev = Evidence.from_run("syn", Status.PASS, "ok", dependencies=deps, binding_context=ctx)
    ev2 = Evidence.from_dict(ev.to_dict())
    assert ev2.run_id == "r" and ev2.design == "d" and ev2.corner == "tt"
    assert ev2.dependency_set().manifest() == deps.manifest()
    assert ev2.binding_errors(ctx) == []


# ---------------------------------------------------------------------------
# SR-04 h8: run output directory isolation
# ---------------------------------------------------------------------------
def test_prepare_run_output_dir_rejects_stale_contents(tmp_path):
    base = tmp_path / "runs"
    first = prepare_run_output_dir(base, "run-1")
    assert os.path.isdir(first) and os.listdir(first) == []

    # Drop a stale artifact, then a fresh run in the same directory is refused.
    _mk(os.path.join(first, "old.rpt"), "stale\n")
    with pytest.raises(StaleOutputError):
        prepare_run_output_dir(base, "run-1")
    with pytest.raises(StaleOutputError):
        assert_clean_output_dir(first)

    # A separate run id is clean; exist_ok explicitly allows intentional resume.
    second = prepare_run_output_dir(base, "run-2")
    assert second != first
    assert prepare_run_output_dir(base, "run-1", exist_ok=True) == first


def test_artifact_path_prevents_directory_traversal(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    good = artifact_path(out, "sta.rpt")
    assert os.path.realpath(good).startswith(os.path.realpath(str(out)) + os.sep)
    with pytest.raises(StaleOutputError):
        artifact_path(out, "../stale.rpt")


# ---------------------------------------------------------------------------
# SR-05: explicit view compatibility contracts (no total order)
# ---------------------------------------------------------------------------
def test_physical_database_never_satisfies_extracted_timing():
    assert not view_compatible("FILL_EXTRACTED", "PHYSICAL_DATABASE")
    assert not view_compatible("ROUTED_EXTRACTED", "PHYSICAL_DATABASE")
    assert not view_compatible("GR_ESTIMATE", "PHYSICAL_DATABASE")
    # A database is not a timing report, regardless of its stage rank.
    assert not contract_compatible(
        ViewContract("timing_report", "fill", "sta_setup", "extracted_filled", "filled"),
        ViewContract("database", "fill", "none", "none", "unfilled"),
    )


def test_estimates_cannot_satisfy_routed_or_filled_extraction():
    assert not view_compatible("ROUTED_EXTRACTED", "GR_ESTIMATE")
    assert not view_compatible("ROUTED_EXTRACTED", "PLACE_ESTIMATE")
    assert not view_compatible("FILL_EXTRACTED", "ROUTED_EXTRACTED")
    assert view_compatible("FILL_EXTRACTED", "FILL_EXTRACTED")
    assert view_compatible("ROUTED_EXTRACTED", "FILL_EXTRACTED")
    # More accurate parasitics may satisfy an estimate requirement.
    assert view_compatible("GR_ESTIMATE", "ROUTED_EXTRACTED")


def test_view_corner_mismatch_rejected_with_explicit_contracts():
    required = ViewContract(
        "timing_report", "route", "sta_setup", "extracted_routed", "unfilled",
        "setup", "tt", "lib-v1", "pex-v1",
    )
    same = ViewContract(
        "timing_report", "route", "sta_setup", "extracted_routed", "unfilled",
        "setup", "tt", "lib-v1", "pex-v1",
    )
    other_corner = ViewContract(
        "timing_report", "route", "sta_setup", "extracted_routed", "unfilled",
        "setup", "ss", "lib-v1", "pex-v1",
    )
    assert contract_compatible(required, same)
    assert not contract_compatible(required, other_corner)


def test_view_library_and_extraction_provenance_mismatch_rejected():
    required = ViewContract(
        "timing_report", "fill", "sta_setup", "extracted_filled", "filled",
        "setup", "tt", "lib-v2", "pex-v3",
    )
    wrong_lib = ViewContract(
        "timing_report", "fill", "sta_setup", "extracted_filled", "filled",
        "setup", "tt", "lib-v1", "pex-v3",
    )
    wrong_pex = ViewContract(
        "timing_report", "fill", "sta_setup", "extracted_filled", "filled",
        "setup", "tt", "lib-v2", "pex-v2",
    )
    assert not contract_compatible(required, wrong_lib)
    assert not contract_compatible(required, wrong_pex)


def test_post_fill_requires_filled_extraction_and_analysis_proof():
    filled_report = ViewContract(
        "timing_report", "fill", "sta_setup", "extracted_filled", "filled",
        "setup", "tt", "lib-v2", "pex-v3",
    )
    assert post_fill_proof_ok(filled_report)
    assert post_fill_proof_ok(filled_report, require_extraction_provenance=True)

    # Physical DB / estimate / unfilled routed extraction are not post-fill proof.
    assert not post_fill_proof_ok(
        ViewContract("database", "fill", "none", "none", "unfilled")
    )
    assert not post_fill_proof_ok(
        ViewContract("timing_report", "route", "sta_setup", "estimate_gr", "unfilled", "setup")
    )
    assert not post_fill_proof_ok(
        ViewContract("timing_report", "route", "sta_setup", "extracted_routed", "unfilled", "setup")
    )
    # Without an extraction deck identity, strict post-fill fails.
    no_prov = ViewContract(
        "timing_report", "fill", "sta_setup", "extracted_filled", "filled", "setup"
    )
    assert post_fill_proof_ok(no_prov)
    assert not post_fill_proof_ok(no_prov, require_extraction_provenance=True)

    # A post-fill gate rejects the database through the compatibility API too.
    fill_gate = ViewContract(
        "timing_report", "fill", "sta_setup", "extracted_filled", "filled", "setup"
    )
    assert not contract_compatible(
        fill_gate, ViewContract("database", "fill", "none", "none", "unfilled")
    )
    assert contract_compatible(fill_gate, filled_report)


def test_view_artifact_type_and_analysis_type_separation():
    # A DRC/physical-check report cannot satisfy a timing gate even if the
    # implementation stage is later.
    assert not contract_compatible(
        ViewContract("timing_report", "route", "sta_setup", "extracted_routed", "unfilled", "setup"),
        ViewContract("physical_check", "fill", "drc", "none", "filled", ""),
    )
    # Setup proof cannot satisfy a hold-only requirement.
    assert not contract_compatible(
        ViewContract("timing_report", "route", "sta_hold", "extracted_routed", "unfilled", "hold"),
        ViewContract("timing_report", "route", "sta_setup", "extracted_routed", "unfilled", "setup"),
    )
    # A "both" analysis result can satisfy a setup-only requirement.
    assert contract_compatible(
        ViewContract("timing_report", "route", "sta_setup", "extracted_routed", "unfilled", "setup"),
        ViewContract("timing_report", "route", "both", "extracted_routed", "unfilled", "both"),
    )
    # A single-mode result cannot satisfy a "both" requirement.
    assert not contract_compatible(
        ViewContract("timing_report", "route", "both", "extracted_routed", "unfilled", "both"),
        ViewContract("timing_report", "route", "sta_setup", "extracted_routed", "unfilled", "setup"),
    )


def test_view_contract_serialization_roundtrip():
    c = ViewContract(
        "timing_report", "fill", "sta_setup", "extracted_filled", "filled",
        "setup", "tt", "lib", "pex",
    )
    assert ViewContract.from_dict(c.to_dict()) == c
    ctx = BindingContext("r", "d", "fill", "e", "FILL_EXTRACTED", "ss")
    assert BindingContext.from_dict(ctx.to_dict()) == ctx
