"""Hashing + evidence binding: input changes, staleness, view compatibility."""
import os

from assistant.evidence import Evidence, view_compatible
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
