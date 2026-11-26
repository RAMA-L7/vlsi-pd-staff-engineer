"""EXT-01 (Agent A) — append-only evidence manifest regressions.

Security invariant: a missing / deleted / unbound / inconsistent evidence
record must NEVER yield CLEAN_PASS, ``qualified=True`` or downstream stage
admission. These tests ADD coverage; they do not weaken any existing test.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from assistant import execution_trust as et
from assistant.controller import GateController
from assistant.evidence import Evidence
from assistant.r6_authority import get_trust
from assistant.statuses import Status

KEY = "agent-a-ext01-regression-key"
CHECK = "eco_sta"
CMD = [sys.executable, "-c", "print('AGENT_A_EXT01_REGRESSION')"]


def _utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _proof(check_id: str) -> dict:
    t = get_trust()
    now = _utc()
    values = dict(
        nonce=secrets.token_hex(16),
        tool_id="agent-a-ext01-regression",
        check_id=check_id,
        image_realpath="",
        image_sha256="",
        approved_images=(),
        approved_images_match=True,
        descendant_count=0,
        job_member_count=1,
        root_job_member=True,
        argv_sha256=str(et._sha256_argv(CMD)),
        boundary_id="agent-a-ext01-regression-boundary",
        context=et._canonical_context(t.context),
        started_utc=now,
        ended_utc=now,
        exit_code=0,
        state="TRUSTED",
        reason="AGENT_A EXT-01 regression proof",
        execution_digest="",
        candidate_digest="",
        consumed_digest="",
        dependency_digest="",
        sampling_window_s=0.0,
        sampling_samples=0,
        containment_claim="none",
        key_ephemeral=bool(getattr(t, "ephemeral", False)),
        sampling_limits_unsupported=True,
        mac="",
    )
    values["execution_digest"] = et.ExecutionBoundary._execution_digest(values)
    return et.ExecutionProof(**values).sign(t).to_dict()


def _evidence(tmp_path: Path, check_id: str, status: Status) -> Evidence:
    report = tmp_path / ("r_" + secrets.token_hex(5) + ".rpt")
    report.write_text("EXT-01 regression report\n", encoding="utf-8")
    ev = Evidence.from_run(
        check_id, status, "EXT-01 regression evidence", command=CMD,
        exit_code=0, analysis_view="ROUTED_EXTRACTED",
        tool_versions={"sta": "1.0"}, reports=[str(report)],
    )
    ev.execution_proof = _proof(check_id)
    return ev


def _seed(tmp_path: Path, *, fail: bool):
    state = tmp_path / "state.json"
    ctl = GateController(str(state))
    ctl.record_evidence(_evidence(tmp_path, CHECK, Status.PASS))
    fail_eid = ""
    if fail:
        ev_fail = _evidence(tmp_path, CHECK, Status.FAIL)
        ctl.record_evidence(ev_fail)
        fail_eid = ev_fail.evidence_id
    return state, fail_eid


NOKEY_EDIT = r'''
import json,sys
state,fid=sys.argv[1],sys.argv[2]
raw=json.load(open(state,encoding="utf-8"))
for key in ("evidence","evidence_provenance","evidence_integrity",
            "r6_evidence_envelopes","submissions"):
    raw.get(key,{}).pop(fid,None)
for cid in list(raw.get("check_index",{})):
    raw["check_index"][cid]=[i for i in raw["check_index"][cid] if str(i)!=fid]
b=raw.get("ledger_bindings",{})
b.get("evidence/v1",{}).pop(fid,None)
b.get("check_index/v1",{}).pop("eco_sta",None)
b.get("evidence_manifest/v1",{}).pop("all",None)
owner=next((n for n,e in (raw.get("used_proof_nonces") or {}).items()
            if e==fid),None)
if owner:
    b.get("proof_nonce/v1",{}).pop(owner,None)
    raw.get("used_proof_nonces",{}).pop(owner,None)
json.dump(raw,open(state,"w",encoding="utf-8"),indent=2,sort_keys=True)
print("NOKEY_EDIT_OK",flush=True)
'''


def _nokey_edit(state: Path, fid: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "ASSISTANT_LEDGER_KEY"}
    return subprocess.run(
        [sys.executable, "-B", "-c", NOKEY_EDIT, str(state), fid],
        capture_output=True, text=True, timeout=60, env=env,
    )


def _authorization(state: Path) -> dict:
    ctl = GateController(str(state))
    try:
        gd = ctl.evaluate_gate("STA", [CHECK])
        q, d = bool(gd.qualified), gd.disposition
    except Exception as exc:
        q, d = False, f"EXC:{type(exc).__name__}"
    ok, why = ctl.can_enter_stage("ROUTE", [("STA", [CHECK])])
    return {
        "q": q,
        "d": d,
        "downstream": bool(ok),
        "problems": ctl.verify_ledger_integrity(),
        "why": why,
    }


def test_ext01_honest_gate_still_authorizes(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_LEDGER_KEY", KEY)
    state, _ = _seed(tmp_path, fail=False)
    raw = json.loads(state.read_text(encoding="utf-8"))
    assert raw["evidence_manifest"]
    assert raw["ledger_bindings"]["evidence_manifest/v1"]["all"]
    out = _authorization(state)
    assert out["q"] is True and out["d"] == "CLEAN_PASS"
    assert out["downstream"] is True and out["problems"] == []


def test_ext01_keyfree_fail_deletion_never_authorizes(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_LEDGER_KEY", KEY)
    state, fail_eid = _seed(tmp_path, fail=True)
    assert _authorization(state)["q"] is False
    cp = _nokey_edit(state, fail_eid)
    assert cp.returncode == 0 and "NOKEY_EDIT_OK" in cp.stdout
    out = _authorization(state)
    assert out["problems"], "the erased evidence must leave an integrity problem"
    assert out["q"] is False
    assert out["d"] != "CLEAN_PASS"
    assert out["downstream"] is False


def test_ext01_missing_manifest_never_authorizes(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_LEDGER_KEY", KEY)
    state, _ = _seed(tmp_path, fail=False)
    raw = json.loads(state.read_text(encoding="utf-8"))
    raw.pop("evidence_manifest", None)
    raw["ledger_bindings"].pop("evidence_manifest/v1", None)
    state.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
    out = _authorization(state)
    assert out["q"] is False and out["downstream"] is False
    assert any("evidence_manifest/v1" in p for p in out["problems"])


def test_ext01_rewritten_manifest_without_key_never_authorizes(tmp_path, monkeypatch):
    monkeypatch.setenv("ASSISTANT_LEDGER_KEY", KEY)
    state, fail_eid = _seed(tmp_path, fail=True)
    raw = json.loads(state.read_text(encoding="utf-8"))
    raw["evidence_manifest"] = [
        eid for eid in raw["evidence_manifest"] if eid != fail_eid
    ]
    raw["evidence"].pop(fail_eid, None)
    raw["evidence_integrity"].pop(fail_eid, None)
    state.write_text(json.dumps(raw, indent=2, sort_keys=True), encoding="utf-8")
    out = _authorization(state)
    assert out["q"] is False and out["downstream"] is False
    assert any("evidence_manifest/v1" in p for p in out["problems"])
