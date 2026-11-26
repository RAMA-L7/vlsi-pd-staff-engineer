"""Unambiguous identity semantics for evidence bundles (EXT-07 remediation).

Problem addressed
------------------
`rc_bundle.py` writes a single unqualified field ``BUNDLE_META.source_tree`` whose
value is copied from ``GOVERNANCE/STARTING_IDENTITY.json:rc_tree_sha256``. That value
is the *pre-remediation RC working-copy tree* (231 files, includes ``regression/``),
not the identity of the ``SOURCE/`` content actually shipped in
``rc_evidence_bundle.zip`` (179 files, ``regression/`` excluded). A reader cannot
tell which identity semantics the field denotes, so the field constitutes a false
provenance assertion even though the underlying hashes are correct.

This module emits **labelled** identities only. Every digest carries:
  * an explicit semantic label (one of SEMANTICS),
  * the scope it was computed over,
  * the exclusion set / algorithm used,
  * the source artifact it was derived from.

It is read-only with respect to frozen evidence; it writes only to an explicit
output path supplied by the caller (normally inside AGENT_G_COPY/EVIDENCE).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

SCHEMA = "ext07.unambiguous-identity/1.0"

# Frozen, read-only inputs (never written by this module).
FROZEN_RC = Path(r"D:\audit\release_candidate_20261125")
FROZEN_BUNDLE = FROZEN_RC / "EVIDENCE_BUNDLE"
FROZEN_SPRINT = Path(r"D:\audit\rc_closure_sprint_20261125")
PRODUCTION_CHECKOUT = Path(r"D:\audit\m1g_mapping_20261117")
REPOSITORY = Path(r"D:\audit\repo")

# Two distinct published exclusion sets -- the difference is material.
EX_STANDARDIZED = frozenset({".git", "__pycache__", ".pytest_cache", "workspace", "evidence", "regression"})
EX_START_BASELINE = frozenset({".git", "__pycache__", ".pytest_cache", "workspace", "evidence"})

# Explicit semantic labels. A digest without one of these labels is not emitted.
SEMANTICS = {
    "git_head_commit_object": (
        "Git commit object id of HEAD (40 hex). Git object-model identity; not a file-content tree digest."
    ),
    "git_head_tree_object": (
        "Git tree object id of HEAD (40 hex). Enumerates git-tracked objects only; "
        "excludes dirty working-tree changes and ignores exclusion sets."
    ),
    "working_tree_standardized": (
        "Content-tree digest of a working copy using EX_STANDARDIZED "
        "(regression/ excluded). This is the identity the RC/Sprint 'SOURCE/' bundle trees use."
    ),
    "working_tree_full_incl_regression": (
        "Content-tree digest of a working copy using EX_START_BASELINE "
        "(regression/ retained). A different scope from the standardized tree."
    ),
    "starting_working_tree_baseline": (
        "Content-tree digest of the pre-remediation RC working copy recorded by rc_setup.py; "
        "baseline for the remediation diff. It is NOT the identity of any shipped archive content."
    ),
    "archive_source_content_tree": (
        "Content-tree digest computed from the bytes of SOURCE/ members inside a zip archive. "
        "This is the only digest that answers 'what source does this bundle ship?'."
    ),
    "archive_manifest_file": (
        "SHA-256 of the manifest document file itself. Identifies the manifest bytes, "
        "not the source tree it enumerates."
    ),
    "archive_zip_file": (
        "SHA-256 of the archive file itself. Identifies the container bytes, "
        "not the tree of its contents."
    ),
}

# The ambiguous field name that must never be emitted by this module.
FORBIDDEN_BARE_KEYS = frozenset({"source_tree", "tree", "tree_sha256"})

ALGORITHM = "sha256(json.dumps({posix_relpath: sha256(file_bytes)}, sort_keys=True).encode('utf-8'))"


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(mapping: dict[str, str]) -> str:
    """Published algorithm: digest of sorted JSON mapping of path -> file digest."""
    return hash_bytes(json.dumps(mapping, sort_keys=True).encode("utf-8"))


def walk_tree(root: Path, excluded: frozenset[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        try:
            if p.is_file() and not any(part in excluded for part in p.parts):
                out[p.relative_to(root).as_posix()] = hash_file(p)
        except OSError:
            pass
    return out


def zip_prefix_tree(zf: zipfile.ZipFile, prefix: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in zf.namelist():
        if name.startswith(prefix) and not name.endswith("/"):
            out[name[len(prefix):]] = hash_bytes(zf.read(name))
    return out


def read_digest_manifest(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "  " in line and not line.startswith("#"):
            digest, rel = line.split("  ", 1)
            out[rel] = digest.strip()
    return out


def git_identity(root: Path) -> dict[str, str]:
    def run(*args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True).stdout.strip()
    return {"git_head_commit_object": run("rev-parse", "HEAD"),
            "git_head_tree_object": run("rev-parse", "HEAD^{tree}")}


def identity(value: str, label: str, scope: str, source: str, files: int | None = None,
             exclusion_set: frozenset[str] | None = None) -> dict:
    """Build one labelled identity record; refuses unknown labels."""
    if label not in SEMANTICS:
        raise ValueError(f"unlabelled identity semantic: {label}")
    rec = {"label": label, "semantics": SEMANTICS[label], "value": value,
           "scope": scope, "derived_from": source}
    if files is not None:
        rec["files"] = files
    if exclusion_set is not None:
        rec["exclusion_set"] = sorted(exclusion_set)
    return rec


def build_record(bundle: Path = FROZEN_BUNDLE, rc_root: Path = FROZEN_RC,
                 sprint_root: Path = FROZEN_SPRINT) -> dict:
    """Recompute all relevant identities and return an explicitly labelled record."""
    zpath = bundle / "rc_evidence_bundle.zip"
    manifest_path = bundle / "SHA256_MANIFEST.txt"
    with zipfile.ZipFile(zpath) as zf:
        source_map = zip_prefix_tree(zf, "SOURCE/")
        archive_source_tree = tree_digest(source_map)
        zip_members = len(zf.namelist())
    rc_std = walk_tree(rc_root / "WORKING_COPY", EX_STANDARDIZED)
    rc_full = walk_tree(rc_root / "WORKING_COPY", EX_START_BASELINE)
    sp_std = walk_tree(sprint_root / "WORKING_COPY", EX_STANDARDIZED)
    sp_full = walk_tree(sprint_root / "WORKING_COPY", EX_START_BASELINE)
    start_map = read_digest_manifest(rc_root / "GOVERNANCE" / "STARTING_SHA256.txt")
    frozen_meta = json.loads((bundle / "BUNDLE_META.json").read_text(encoding="utf-8"))

    ids = {
        "archive_source_content_tree": identity(
            archive_source_tree, "archive_source_content_tree",
            "SOURCE/ members of rc_evidence_bundle.zip", str(zpath), len(source_map), EX_STANDARDIZED),
        "archive_zip_file": identity(hash_file(zpath), "archive_zip_file",
                                     "container bytes", str(zpath)),
        "archive_manifest_file": identity(hash_file(manifest_path), "archive_manifest_file",
                                          "manifest document bytes", str(manifest_path)),
        "starting_working_tree_baseline": identity(
            tree_digest(start_map), "starting_working_tree_baseline",
            "pre-remediation RC working copy (baseline)",
            str(rc_root / "GOVERNANCE" / "STARTING_SHA256.txt"), len(start_map), EX_START_BASELINE),
        "rc_working_tree_standardized": identity(
            tree_digest(rc_std), "working_tree_standardized",
            "RC WORKING_COPY at audit time", str(rc_root / "WORKING_COPY"), len(rc_std), EX_STANDARDIZED),
        "rc_working_tree_full_incl_regression": identity(
            tree_digest(rc_full), "working_tree_full_incl_regression",
            "RC WORKING_COPY at audit time", str(rc_root / "WORKING_COPY"), len(rc_full), EX_START_BASELINE),
        "sprint_working_tree_standardized": identity(
            tree_digest(sp_std), "working_tree_standardized",
            "Sprint WORKING_COPY", str(sprint_root / "WORKING_COPY"), len(sp_std), EX_STANDARDIZED),
        "sprint_working_tree_full_incl_regression": identity(
            tree_digest(sp_full), "working_tree_full_incl_regression",
            "Sprint WORKING_COPY", str(sprint_root / "WORKING_COPY"), len(sp_full), EX_START_BASELINE),
    }
    for label, root in (("production", PRODUCTION_CHECKOUT), ("repository", REPOSITORY)):
        git_ids = git_identity(root)
        ids[f"{label}_git_head_commit"] = identity(
            git_ids["git_head_commit_object"], "git_head_commit_object",
            f"git HEAD of {label}", str(root))
        ids[f"{label}_git_head_tree"] = identity(
            git_ids["git_head_tree_object"], "git_head_tree_object",
            f"git HEAD of {label}", str(root))

    return {
        "schema": SCHEMA,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "identity_algorithm": ALGORITHM,
        "semantics_registry": SEMANTICS,
        "read_only_inputs": [str(bundle), str(rc_root), str(sprint_root),
                             str(PRODUCTION_CHECKOUT), str(REPOSITORY)],
        "archive": {"zip_members": zip_members,
                    "manifest_entries": len(read_digest_manifest(manifest_path))},
        "identities": ids,
        "frozen_bundle_meta_observed": frozen_meta,
        "disposition": {
            "frozen_source_tree_field": frozen_meta.get("source_tree", "<absent>"),
            "field_is_unqualified": "source_tree" in frozen_meta,
            "field_equals_starting_baseline": (
                frozen_meta.get("source_tree") == ids["starting_working_tree_baseline"]["value"]),
            "field_equals_shipped_archive_source": (
                frozen_meta.get("source_tree") == ids["archive_source_content_tree"]["value"]),
            "corrected_label_for_shipped_source": "archive_source_content_tree",
            "corrected_label_for_baseline": "starting_working_tree_baseline",
            "note": (
                "Frozen BUNDLE_META.json is NOT modified. This record supplies the missing "
                "semantic labels for a future corrected bundle; both digests are valid but "
                "denote different identities."
            ),
        },
    }


def validate_record(record: dict) -> None:
    """Fail closed if any emitted identity is unlabelled or ambiguously named."""
    registry = set(record["semantics_registry"])
    if set(record["semantics_registry"]) != set(SEMANTICS):
        raise AssertionError("semantics registry mismatch")
    if registry & FORBIDDEN_BARE_KEYS:
        raise AssertionError("bare/ambiguous identity label in registry")
    for key, rec in record["identities"].items():
        # Fail closed with AssertionError even if a bare key injected a non-dict value.
        if not isinstance(rec, dict):
            raise AssertionError(f"identity {key} is not a labelled record")
        if set(rec) & FORBIDDEN_BARE_KEYS:
            raise AssertionError(f"bare/ambiguous identity key in {key}")
        if rec["label"] not in registry:
            raise AssertionError(f"unregistered label for {key}: {rec['label']}")
        if rec["label"] in ("git_head_commit_object", "git_head_tree_object"):
            if len(rec["value"]) != 40:
                raise AssertionError(f"git object id must be 40 hex: {key}")
        elif len(rec["value"]) != 64:
            raise AssertionError(f"content digest must be 64 hex: {key}")
    for field in FORBIDDEN_BARE_KEYS:
        if field in record["identities"]:
            raise AssertionError(f"ambiguous identity field emitted: {field}")


def corrected_meta_proposal(record: dict) -> dict:
    """A NEW, unambiguous metadata document -- never a replacement for frozen files."""
    ids = record["identities"]
    return {
        "schema": SCHEMA,
        "status": "NEW_EVIDENCE_ONLY__FROZEN_BUNDLE_UNCHANGED",
        "utc": record["utc"],
        "archive_zip_file_sha256": ids["archive_zip_file"]["value"],
        "archive_source_content_tree_sha256": ids["archive_source_content_tree"]["value"],
        "archive_source_content_files": ids["archive_source_content_tree"]["files"],
        "starting_working_tree_baseline_sha256": ids["starting_working_tree_baseline"]["value"],
        "starting_working_tree_baseline_files": ids["starting_working_tree_baseline"]["files"],
        "rc_working_tree_standardized_sha256": ids["rc_working_tree_standardized"]["value"],
        "sprint_working_tree_standardized_sha256": ids["sprint_working_tree_standardized"]["value"],
        "production_git_head_tree_object": ids["production_git_head_tree"]["value"],
        "semantics": {k: SEMANTICS[k] for k in (
            "archive_source_content_tree", "starting_working_tree_baseline",
            "working_tree_standardized", "git_head_tree_object", "archive_zip_file")},
        "provenance_note": (
            "Different labels denote different identities. The frozen BUNDLE_META.source_tree "
            "value equals starting_working_tree_baseline, NOT archive_source_content_tree. "
            "A future corrected bundle must emit labelled fields, not a bare source_tree."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--evidence-dir", default=str(Path(__file__).resolve().parent.parent / "EVIDENCE"))
    args = ap.parse_args(argv)
    out = Path(args.evidence_dir)
    out.mkdir(parents=True, exist_ok=True)
    record = build_record()
    validate_record(record)
    (out / "EXT07_UNAMBIGUOUS_IDENTITIES.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "EXT07_CORRECTED_META_PROPOSAL.json").write_text(
        json.dumps(corrected_meta_proposal(record), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "schema": SCHEMA,
        "written": [str(out / "EXT07_UNAMBIGUOUS_IDENTITIES.json"),
                    str(out / "EXT07_CORRECTED_META_PROPOSAL.json")],
        "archive_source_content_tree": record["identities"]["archive_source_content_tree"]["value"],
        "starting_working_tree_baseline": record["identities"]["starting_working_tree_baseline"]["value"],
        "frozen_bundle_meta_source_tree": record["frozen_bundle_meta_observed"].get("source_tree"),
        "validation": "PASS",
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
