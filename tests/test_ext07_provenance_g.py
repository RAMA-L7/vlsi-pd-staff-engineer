"""EXT-07 (Agent G): provenance consistency tests.

All frozen inputs are READ-ONLY. These tests assert that the labelled identity
utility distinguishes git object identity, baseline working-tree identity, and
actual shipped archive content identity, and that no frozen artifact changed.
"""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_ext07 import provenance_identity as pi  # noqa: E402

RC = pi.FROZEN_RC
BUNDLE = pi.FROZEN_BUNDLE
COPY = Path(__file__).resolve().parents[1]
EVIDENCE = COPY / "EVIDENCE"

EXPECTED_ARCHIVE_SOURCE = "7842979a44a0cb432eeacdd9e6e63b5a790fdba9c51346259040842a3277d0c8"
EXPECTED_START_BASELINE = "67dff40684d1da6f83402a3a300193fa14a33a6025cd6394810d35f51cb76c8b"
EXPECTED_ZIP_SHA = "e8ecb9190e7237694419d430e1e799cd6dbf99683f3cde3d4ae21dfb36cd0cef"
EXPECTED_MANIFEST_SHA = "8f8eaacb46f3e209af20ec16580368529bf7b125da64a47aefaa90f811cdd39a"
EXPECTED_META_SHA = hashlib.sha256(
    (BUNDLE / "BUNDLE_META.json").read_bytes()).hexdigest()
EXPECTED_GIT_TREE = "e9beaa22f2ecb272d21b756cd628ea2d46345c7e"


@pytest.fixture(scope="module")
def record():
    rec = pi.build_record()
    pi.validate_record(rec)
    return rec


def _source_tree_from_zip_directly():
    """Independent of the module helper -- recomputed straight from zip bytes."""
    src = {}
    with zipfile.ZipFile(BUNDLE / "rc_evidence_bundle.zip") as zf:
        for name in zf.namelist():
            if name.startswith("SOURCE/"):
                src[name[len("SOURCE/"):]] = hashlib.sha256(zf.read(name)).hexdigest()
    return src, hashlib.sha256(json.dumps(src, sort_keys=True).encode("utf-8")).hexdigest()


def test_frozen_contradiction_reproduced():
    meta = json.loads((BUNDLE / "BUNDLE_META.json").read_text(encoding="utf-8"))
    _, actual = _source_tree_from_zip_directly()
    assert meta["source_tree"] == EXPECTED_START_BASELINE
    assert actual == EXPECTED_ARCHIVE_SOURCE
    assert meta["source_tree"] != actual, "frozen metadata must be shown contradictory, not silent"


def test_exclusion_sets_are_materially_different():
    assert "regression" in pi.EX_STANDARDIZED
    assert "regression" not in pi.EX_START_BASELINE
    std = pi.walk_tree(RC / "WORKING_COPY", pi.EX_STANDARDIZED)
    full = pi.walk_tree(RC / "WORKING_COPY", pi.EX_START_BASELINE)
    assert (len(std), len(full)) == (179, 235)
    assert pi.tree_digest(std) == EXPECTED_ARCHIVE_SOURCE
    assert pi.tree_digest(full) == "cc70203ac66ca866deda4cef1ab2b5501a79318b7717572c8975b1a3870a2269"
    assert pi.tree_digest(std) != pi.tree_digest(full)


def test_starting_baseline_is_not_archive_content(record):
    ids = record["identities"]
    assert ids["starting_working_tree_baseline"]["files"] == 231
    assert ids["starting_working_tree_baseline"]["value"] == EXPECTED_START_BASELINE
    assert ids["archive_source_content_tree"]["files"] == 179
    assert ids["archive_source_content_tree"]["value"] == EXPECTED_ARCHIVE_SOURCE
    assert (ids["starting_working_tree_baseline"]["value"]
            != ids["archive_source_content_tree"]["value"])
    assert record["disposition"]["field_equals_starting_baseline"] is True
    assert record["disposition"]["field_equals_shipped_archive_source"] is False
    assert record["disposition"]["corrected_label_for_shipped_source"] == "archive_source_content_tree"


def test_every_emitted_identity_is_labelled(record):
    for key, rec in record["identities"].items():
        assert rec["label"] in pi.SEMANTICS, key
        assert rec["semantics"] == pi.SEMANTICS[rec["label"]], key
        assert rec["scope"] and rec["derived_from"], key
        assert not (set(rec) & pi.FORBIDDEN_BARE_KEYS), key
    assert not (set(record["identities"]) & pi.FORBIDDEN_BARE_KEYS)
    with pytest.raises(AssertionError):
        bad = json.loads(json.dumps(record))
        bad["identities"]["source_tree"] = EXPECTED_START_BASELINE
        pi.validate_record(bad)


def test_git_identity_is_distinct_from_content_identity(record):
    ids = record["identities"]
    assert ids["production_git_head_tree"]["value"] == EXPECTED_GIT_TREE
    assert len(ids["production_git_head_commit"]["value"]) == 40
    assert len(ids["production_git_head_tree"]["value"]) == 40
    assert ids["production_git_head_tree"]["value"] != EXPECTED_ARCHIVE_SOURCE
    assert ids["archive_source_content_tree"]["value"] != EXPECTED_START_BASELINE


def test_archive_manifest_and_zip_hashes_are_separate_semantics(record):
    ids = record["identities"]
    assert ids["archive_zip_file"]["value"] == EXPECTED_ZIP_SHA
    assert ids["archive_manifest_file"]["value"] not in (
        EXPECTED_ARCHIVE_SOURCE, EXPECTED_START_BASELINE, EXPECTED_ZIP_SHA)
    assert record["archive"]["zip_members"] == 195
    assert record["archive"]["manifest_entries"] == 195


def test_frozen_artifacts_were_not_modified():
    assert pi.hash_file(BUNDLE / "rc_evidence_bundle.zip") == EXPECTED_ZIP_SHA
    assert hashlib.sha256((BUNDLE / "BUNDLE_META.json").read_bytes()).hexdigest() == EXPECTED_META_SHA
    assert pi.hash_file(BUNDLE / "SHA256_MANIFEST.txt") == EXPECTED_MANIFEST_SHA


def test_new_evidence_carries_both_labelled_fields():
    identities = json.loads((EVIDENCE / "EXT07_UNAMBIGUOUS_IDENTITIES.json").read_text(encoding="utf-8"))
    proposal = json.loads((EVIDENCE / "EXT07_CORRECTED_META_PROPOSAL.json").read_text(encoding="utf-8"))
    assert identities["schema"] == proposal["schema"] == pi.SCHEMA
    assert "source_tree" not in proposal
    assert "source_tree" not in identities["identities"]
    assert proposal["archive_source_content_tree_sha256"] == EXPECTED_ARCHIVE_SOURCE
    assert proposal["starting_working_tree_baseline_sha256"] == EXPECTED_START_BASELINE
    assert proposal["archive_source_content_tree_sha256"] != proposal["starting_working_tree_baseline_sha256"]
    assert proposal["status"] == "NEW_EVIDENCE_ONLY__FROZEN_BUNDLE_UNCHANGED"


def test_sprint_bundle_already_uses_explicit_labels():
    """The sprint generator labels its start value; only the RC generator omitted labels."""
    meta = json.loads((pi.FROZEN_SPRINT / "BUNDLE" / "BUNDLE_META.json").read_text(encoding="utf-8"))
    assert "source_tree" not in meta
    assert meta["source_tree_final"] == "2cb2a5a88e52914b62f0d64561b21de2b15940a1bfa276e9494227ced7f80b37"
    assert meta["source_tree_at_start"] == EXPECTED_ARCHIVE_SOURCE
