"""Hashing + evidence binding: input changes, staleness, view compatibility."""
import os

import pytest

from assistant.evidence import (
    Evidence,
    ProvenanceError,
    bind_file,
    hash_named_files,
    parse_tool_versions,
    validate_provenance,
    view_compatible,
)
from assistant.hashing import (
    fingerprint,
    sha256_bytes,
    sha256_file,
    stable_json_hash,
)
from assistant.statuses import Status


def test_sha256_stable(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("hello", encoding="utf-8")
    h1 = sha256_file(str(p))
    h2 = sha256_file(str(p))
    assert h1 == h2 == sha256_bytes(b"hello")
    fp = fingerprint(str(p))
    assert fp["sha256"] == h1 and fp["size"] == 5


def test_missing_file_hash_mapping(tmp_path):
    from assistant.hashing import hash_mapping

    out = hash_mapping({"ok": str(tmp_path / "x.v"), "bad": "/no/such/file"})
    assert out["bad"] == "<missing>"


def test_stable_json_hash_order_independent():
    assert stable_json_hash({"a": 1, "b": 2}) == stable_json_hash({"b": 2, "a": 1})


def test_evidence_roundtrip(tmp_path):
    rpt = tmp_path / "sta.rpt"
    rpt.write_text("WNS: -0.1", encoding="utf-8")
    ev = Evidence.from_run(
        check_id="sta_tt",
        status=Status.PASS,
        reason="meets target",
        command=["sta", "-exit"],
        exit_code=0,
        analysis_view="ROUTED_EXTRACTED",
        tool_versions={"sta": "3.1"},
        input_hashes={"rtl": "abc"},
        reports=[str(rpt)],
    )
    d = ev.to_dict()
    ev2 = Evidence.from_dict(d)
    assert ev2.status is Status.PASS
    assert ev2.reports[0]["sha256"] == ev.reports[0]["sha256"]
    assert ev2.input_hashes == {"rtl": "abc"}


def test_staleness_detects_changed_input(tmp_path):
    ev = Evidence.from_run(
        check_id="lec", status=Status.PASS, reason="proven", input_hashes={"rtl": "old"}
    )
    assert not ev.is_stale({"rtl": "old"})
    assert ev.is_stale({"rtl": "new"})
    assert ev.stale_inputs({"rtl": "new"}) == ["rtl"]
    # missing label counts as stale too
    assert ev.is_stale({})


def test_missing_report_artifact_detected(tmp_path):
    ev = Evidence.from_run(
        check_id="sta",
        status=Status.PASS,
        reason="ok",
        reports=[str(tmp_path / "absent.rpt")],
    )
    assert ev.missing_report_artifacts()


def test_view_compatible_ordering():
    # estimates can never satisfy routed-extraction requirements
    assert not view_compatible("ROUTED_EXTRACTED", "GR_ESTIMATE")
    assert not view_compatible("ROUTED_EXTRACTED", "PLACE_ESTIMATE")
    assert view_compatible("ROUTED_EXTRACTED", "ROUTED_EXTRACTED")
    assert view_compatible("ROUTED_EXTRACTED", "FILL_EXTRACTED")
    assert view_compatible(None, "ANYTHING")
    # a routed-extraction result does not satisfy a fill gate
    assert not view_compatible("FILL_EXTRACTED", "ROUTED_EXTRACTED")


# ------------------------------------------------------- CLI provenance helpers
def test_bind_file_stores_path_and_sha256_and_requires_existence(tmp_path):
    rpt = tmp_path / "sta.rpt"
    rpt.write_text("WNS: 0.5", encoding="utf-8")
    entry = bind_file(str(rpt))
    assert entry["path"] == str(rpt)
    assert entry["sha256"] == sha256_file(str(rpt))
    with pytest.raises(ProvenanceError):
        bind_file(str(tmp_path / "absent.rpt"))


def test_parse_tool_versions_and_hash_named_files(tmp_path):
    lib = tmp_path / "tt.lib"
    lib.write_text("liberty", encoding="utf-8")
    assert parse_tool_versions(["yosys=0.9", "opensta=2.6"]) == {
        "yosys": "0.9", "opensta": "2.6"
    }
    with pytest.raises(ProvenanceError):
        parse_tool_versions(["yosys="])
    assert hash_named_files([f"tt={lib}"]) == {"tt": sha256_file(str(lib))}
    with pytest.raises(ProvenanceError):
        hash_named_files([f"tt={tmp_path / 'absent.lib'}"])


def test_validate_provenance_flags_incomplete_and_exempts_na(tmp_path):
    rpt = tmp_path / "sta.rpt"
    rpt.write_text("WNS: 0.5", encoding="utf-8")
    incomplete = Evidence.from_run(
        check_id="sta", status=Status.PASS, reason="ok"
    )
    problems = validate_provenance(incomplete)
    assert any("command" in p for p in problems)
    assert any("exit-code" in p for p in problems)
    assert any("analysis view" in p for p in problems)
    assert any("report" in p for p in problems)
    assert any("tool version" in p for p in problems)

    complete = Evidence.from_run(
        check_id="sta", status=Status.PASS, reason="ok",
        command=["sta"], exit_code=0, analysis_view="ROUTED_EXTRACTED",
        tool_versions={"sta": "3.1"}, reports=[str(rpt)],
    )
    assert validate_provenance(complete) == []

    na = Evidence.from_run(
        check_id="upf", status=Status.NOT_APPLICABLE, reason="",
        applicability_reason="NA-RULE:NO-UPF design has no UPF file in scope",
    )
    assert validate_provenance(na) == []
