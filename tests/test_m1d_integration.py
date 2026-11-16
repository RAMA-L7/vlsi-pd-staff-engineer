"""Milestone 1D integration guards: promoted inputs, flow config, partition gate.

These tests pin the locally integrated (published in this commit) soc_ctrl
promotion inputs and the corrected acceptance-gate logic.  They do NOT claim
full physical soc_ctrl qualification — the published evidence records SS
timing and stage-15 LVS as open failures.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

PROMOTED_RTL_SHA = "2a7e028dacd97b4462d791e69e69b8ff1b2cffcf239ba31b6e968efdadb8c4be"
CANONICAL_SDC_SHA = "2a9126e716f834f255fe358aa8962ae619ffec50418e1104690a49eda47be644"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_promoted_soc_ctrl_inputs_present_and_hash_bound():
    rtl = REPO / "examples" / "soc_ctrl" / "rtl" / "soc_ctrl.v"
    sdc = REPO / "examples" / "soc_ctrl" / "rtl" / "soc_ctrl.sdc"
    assert rtl.is_file() and sdc.is_file()
    assert _sha256(rtl) == PROMOTED_RTL_SHA
    assert _sha256(sdc) == CANONICAL_SDC_SHA
    # Approved arithmetic correction must be present in the promoted RTL.
    text = rtl.read_text(encoding="utf-8", errors="replace")
    for bit in range(4):
        assert f"assign sum[{bit}] = fa{bit}_sum;" in text


def test_synthesis_stage_applies_dont_use():
    script = (REPO / "regression" / "stages" / "01_synthesis.ps1").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "abc -liberty $Lib -dont_use sky130_fd_sc_hd__lpflow_*" in script


def test_ten_ns_reference_config_is_present_and_approved():
    cfg_path = REPO / "regression" / "configs" / "soc_ctrl_10ns.json"
    assert cfg_path.is_file()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8", errors="replace"))
    assert cfg.get("clock", {}).get("period_ns") == 10.0 or cfg.get("period_ns") == 10.0
    assert cfg.get("top_module") == "soc_ctrl"
    blob = json.dumps(cfg)
    assert "2a9126e7" in blob
    assert "NOT approved" in blob or "not approved" in blob


@pytest.fixture
def partition_gate():
    """Load only the patched partition-gate helpers from acceptance.py.

    acceptance.py runs the full checker at import time, so isolate the
    explicitly delimited helper block instead of importing the module.
    """
    src = (REPO / "regression" / "checks" / "acceptance.py").read_text(
        encoding="utf-8", errors="replace"
    )
    start = src.index("# --- BEGIN PARTITION GATE HELPERS")
    end = src.index("# --- END PARTITION GATE HELPERS")
    ns = {
        "os": os,
        "re": re,
        "json": json,
        "exists": lambda p: os.path.isfile(p),
        "read": lambda p: open(p, encoding="utf-8", errors="replace").read(),
    }
    exec(compile(src[start:end], "acceptance_helpers", "exec"), ns)
    return ns["evaluate_partition_proof"]


def _log(proved, failed=()):
    lines = [
        f"Successfully proved equivalence of partition {name}" for name in proved
    ]
    lines += [
        f"Failed to prove equivalence of partition {name}" for name in failed
    ]
    if failed:
        lines.append(
            f"Failed to prove equivalence for {len(failed)}/{len(proved) + len(failed)} partitions"
        )
        lines.append("DONE (FAIL, rc=2)")
    else:
        lines.append("Successfully proved designs equivalent")
        lines.append("DONE (PASS, rc=0)")
    return "\n".join(lines)


def test_partition_gate_accepts_genuine_full_proof(partition_gate, tmp_path, monkeypatch):
    names = [f"p{i}" for i in range(8)]
    monkeypatch.setenv("REG_EXPECTED_PARTITIONS", ",".join(names))
    status, detail = partition_gate(str(tmp_path), _log(names))
    assert status == "PASS", detail
    assert "8" in detail


def test_partition_gate_rejects_partial_five_of_eight(partition_gate, tmp_path, monkeypatch):
    names = [f"p{i}" for i in range(8)]
    monkeypatch.setenv("REG_EXPECTED_PARTITIONS", ",".join(names))
    status, detail = partition_gate(str(tmp_path), _log(names[:5], names[5:]))
    assert status == "FAIL"
    assert "missing" in detail or "failed" in detail


def test_partition_gate_inconclusive_without_expected_set(partition_gate, tmp_path, monkeypatch):
    monkeypatch.delenv("REG_EXPECTED_PARTITIONS", raising=False)
    status, detail = partition_gate(str(tmp_path), _log([f"p{i}" for i in range(5)]))
    assert status == "INCONCLUSIVE"
    assert "expected partition set unknown" in detail
