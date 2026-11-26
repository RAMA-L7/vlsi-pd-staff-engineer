"""EXT-07 PHASE 1/2: read-only independent recalculation of frozen RC provenance.

Reads ONLY frozen evidence. Writes ONLY into AGENT_G_COPY/EVIDENCE.
"""
import hashlib
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

RC = Path(r"D:\audit\release_candidate_20261125")
BUNDLE = RC / "EVIDENCE_BUNDLE"
SPRINT = Path(r"D:\audit\rc_closure_sprint_20261125")
PROD = Path(r"D:\audit\m1g_mapping_20261117")
REPO = Path(r"D:\audit\repo")
OUT = Path(r"D:\audit\security_reopening_sprint_20261125\AGENT_G_COPY\EVIDENCE")
STD_EX = {".git", "__pycache__", ".pytest_cache", "workspace", "evidence", "regression"}
START_EX = {".git", "__pycache__", ".pytest_cache", "workspace", "evidence"}


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(mapping):
    """Same published algorithm: sha256(json.dumps({rel: sha}, sort_keys=True)).encode()."""
    return sha_bytes(json.dumps(mapping, sort_keys=True).encode("utf-8"))


def walk_tree(root, excluded):
    out = {}
    for p in sorted(root.rglob("*")):
        try:
            if p.is_file() and not any(x in p.parts for x in excluded):
                out[p.relative_to(root).as_posix()] = sha_file(p)
        except OSError:
            pass
    return out


def git_identity(root):
    def g(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True).stdout.strip()
    return dict(root=str(root), head=g("rev-parse", "HEAD"), head_tree=g("rev-parse", "HEAD^{tree}"),
                branch=g("branch", "--show-current"),
                dirty=len(g("status", "--porcelain=v1").splitlines()) if g("status", "--porcelain=v1") else 0)


def main():
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    zpath = BUNDLE / "rc_evidence_bundle.zip"
    meta = json.loads((BUNDLE / "BUNDLE_META.json").read_text(encoding="utf-8"))

    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        member_digests = {n: sha_bytes(z.read(n)) for n in names}
        zip_sha = sha_file(zpath)

    manifest = {}
    for line in (BUNDLE / "SHA256_MANIFEST.txt").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "  " in line:
            d, rel = line.split("  ", 1)
            manifest[rel] = d.strip()
    mismatches = sorted(r for r, d in manifest.items()
                        if r not in member_digests or member_digests[r] != d)
    extra = sorted(set(member_digests) - set(manifest))

    src = {n[len("SOURCE/"):]: d for n, d in member_digests.items() if n.startswith("SOURCE/")}
    evid = {n[len("EVIDENCE/"):]: d for n, d in member_digests.items() if n.startswith("EVIDENCE/")}

    # Starting identity recorded at RC setup (pre-remediation, includes regression/)
    start_lines = (RC / "GOVERNANCE" / "STARTING_SHA256.txt").read_text(encoding="utf-8").splitlines()
    start_map = {}
    for line in start_lines:
        if "  " in line:
            d, rel = line.split("  ", 1)
            start_map[rel] = d.strip()

    rc_std = walk_tree(RC / "WORKING_COPY", STD_EX)
    rc_full = walk_tree(RC / "WORKING_COPY", START_EX)
    sp_std = walk_tree(SPRINT / "WORKING_COPY", STD_EX)
    sp_full = walk_tree(SPRINT / "WORKING_COPY", START_EX)

    sp_bundle_meta = json.loads((SPRINT / "BUNDLE" / "BUNDLE_META.json").read_text(encoding="utf-8"))

    result = dict(
        utc_started=started,
        utc_finished=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        python=sys.version.split()[0],
        algorithm="sha256(json.dumps({posix_relpath: sha256(file_bytes)}, sort_keys=True).encode('utf-8'))",
        frozen_bundle_meta=meta,
        frozen_bundle=dict(zip_path=str(zpath), zip_sha256=zip_sha, zip_members=len(names),
                           manifest_entries=len(manifest), mismatches=mismatches, extra_zip_members=extra,
                           source_members=len(src), evidence_members=len(evid),
                           top_level_counts={k: sum(1 for n in names if n.startswith(k + "/"))
                                             for k in ("SOURCE", "EVIDENCE", "REPORTS", "GOVERNANCE", "AGENT_RECORDS")}),
        identities=dict(
            starting_working_tree_recorded=dict(files=len(start_map), tree=tree_digest(start_map),
                                                source="GOVERNANCE/STARTING_SHA256.txt",
                                                semantics="RC setup working copy BEFORE remediation; includes regression/; NOT archive content"),
            starting_identity_json_rc_tree=dict(
                value=json.loads((RC / "GOVERNANCE" / "STARTING_IDENTITY.json").read_text(encoding="utf-8"))["rc_tree_sha256"],
                semantics="field copied verbatim into BUNDLE_META.source_tree by rc_bundle.py"),
            shipped_archive_source_tree=dict(files=len(src), tree=tree_digest(src),
                                             semantics="sha256 of SOURCE/ member bytes inside rc_evidence_bundle.zip"),
            rc_live_working_tree_standardized=dict(files=len(rc_std), tree=tree_digest(rc_std)),
            rc_live_working_tree_full_incl_regression=dict(files=len(rc_full), tree=tree_digest(rc_full)),
            sprint_live_working_tree_standardized=dict(files=len(sp_std), tree=tree_digest(sp_std)),
            sprint_live_working_tree_full_incl_regression=dict(files=len(sp_full), tree=tree_digest(sp_full)),
            git=dict(production=git_identity(PROD), repo=git_identity(REPO),
                     semantics="git object-model tree of HEAD; distinct from any file-content tree digest"),
        ),
        sprint_bundle_meta=sp_bundle_meta,
        contradiction=dict(
            meta_source_tree=meta["source_tree"],
            actual_archive_source_tree=tree_digest(src),
            equal=(meta["source_tree"] == tree_digest(src)),
            meta_matches_starting_tree=(meta["source_tree"] == tree_digest(start_map)),
        ),
    )
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "EXT07_ORIGINAL_HASH_RECALC.json"
    p.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(dict(recalc_written=str(p), **result["contradiction"],
                          identities={k: v.get("tree") or v.get("value") for k, v in result["identities"].items() if k != "git"}), indent=2))
    return 0 if not mismatches and not extra else 2


if __name__ == "__main__":
    sys.exit(main())
