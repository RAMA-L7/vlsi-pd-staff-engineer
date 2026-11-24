"""R4 binding exploits: acceptance is bound at the AUTHORIZATION boundary.

All providers, clocks and collateral here are SIMULATED / hermetic / injected.
No real EDA tool, no live inference, no external service and no production
collateral is touched.

EXPLOIT tests in this module FAIL against the inherited code and PASS after the
binding fix:

  * A4-C / A2-01  the pinned dependency closure is re-hashed only BEFORE
                  acceptance. A dependency that mutates (or is deleted) after
                  acceptance leaves a durable PASS carrying a stale
                  ``dependency_revision``.
  * A4-A          the immutable accepted snapshot is sealed (best-effort
                  chmod) and PASS is committed BEFORE the final integrity
                  re-hash. A barrier-synchronised writer landing after that
                  re-hash leaves a durable PASS pointing at mutated bytes.
  * A4-D          with no downstream stage there is no post-commit re-hash at
                  all, so a post-commit mutation also leaves a durable PASS.

GUARD tests hold before and after; no earlier gate is weakened.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import assistant.eco as eco_mod
from assistant.controller import GateController
from assistant.eco import (
    EcoChangeScope,
    EcoManager,
    EcoStage,
    ProtectedMetric,
)
from assistant.evidence import Evidence
from assistant.hashing import sha256_file, stable_json_hash
from assistant.statuses import Status

SIM = "SIMULATED R4 binding exploit regression"
ACCEPTED = "module d; endmodule  // accepted candidate\n"
MUTANT = "module d; assign x = 1'b0; // MUTATED BY POST-SEAL WRITER\n"

DepError = getattr(eco_mod, "EcoDependencyError", RuntimeError)

ENV_BASE_VARS = (
    "REG_PLATFORM_SKY130HD", "ASSISTANT_PLATFORM_HD",
    "REG_PLATFORM_SKY130HS", "ASSISTANT_PLATFORM_HS",
)


# ---------------------------------------------------------------------------
# Environment / provider helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def env(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "design.v").write_text(ACCEPTED, encoding="utf-8")
    ctl = GateController(str(tmp_path / "state.json"))
    for fact in ("has_macros", "has_upf", "has_clock_gates", "has_analog"):
        ctl.register_design_fact(fact, False, source="SIMULATED inventory")
    eco = EcoManager(
        str(ws), ctl,
        checkpoint_dir=str(tmp_path / "ckpt"),
        experiment_log=str(tmp_path / "exp.json"),
    )
    rep = tmp_path / "sim_report.txt"
    rep.write_text("SIMULATED clean verification report\n", encoding="utf-8")
    return SimpleNamespace(
        ws=ws, ctl=ctl, eco=eco, rep=rep, tmp_path=tmp_path,
        monkeypatch=monkeypatch,
    )


@pytest.fixture
def hermetic_deps(tmp_path, monkeypatch):
    """A hermetic pins.json with REAL collateral files under temp roots."""
    hd = tmp_path / "sky130hd"
    hs = tmp_path / "sky130hs"
    hd.mkdir()
    hs.mkdir()
    for var in ENV_BASE_VARS:
        monkeypatch.delenv(var, raising=False)
    entries = []
    for i in range(4):
        key = "collateral_%02d.dat" % i
        base = "platform_hs" if i % 2 == 0 else "platform_hd"
        root = hs if base == "platform_hs" else hd
        rel = key
        (root / rel).write_bytes(("SIMULATED-payload-%02d" % i).encode("utf-8"))
        entries.append({
            "key": key, "path": rel, "base": base,
            "sha256": "0" * 64,
            "required": True,
            "used_by": SIM,
        })
    manifest = {
        "portable_config": {
            "fallbacks": {"platform_hd": str(hd), "platform_hs": str(hs)},
            "collateral": entries,
        }
    }
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(eco_mod, "__file__", str(pins))

    def path_of(index):
        e = entries[index]
        return (hs if e["base"] == "platform_hs" else hd) / e["path"]

    return SimpleNamespace(pins=pins, entries=entries, path_of=path_of)


def _ev(rep, cid):
    return Evidence.from_run(
        cid, Status.PASS, reason="SIMULATED", command=["sim"], exit_code=0,
        analysis_view="MAPPED_NETLIST", tool_versions={"sim": "1"},
        reports=[str(rep)],
    )


def _provider(rep):
    def verify():
        return [_ev(rep, cid) for cid in ("eco_lec", "eco_sta", "eco_physical_drc")]
    return verify


def _scope():
    return EcoChangeScope(
        "logic_optimization", EcoStage.MAPPED, True, "SIMULATED recorded scope"
    )


def _run(e, **kw):
    kw.setdefault("simulated", SIM)
    return e.eco.run_experiment(
        change_scope=kw.pop("scope", _scope()),
        experiment_id=kw.pop("experiment_id", "r4-binding"),
        description="SIMULATED R4 binding exploit regression",
        change_class="logic_optimization",
        changed_files=["design.v"],
        apply_change=kw.pop("apply_change", lambda: None),
        evaluate=kw.pop("evaluate", lambda: {"wns": 1.0}),
        protected_metrics=[ProtectedMetric("wns", 0.0)],
        verify=kw.pop("verify", None),
        downstream_stage=kw.pop("downstream", None),
        verification_timeout_s=kw.pop("timeout", None),
        **kw,
    )


def _immutable_design(dirpath):
    if not dirpath:
        return None
    p = Path(dirpath) / "design.v"
    return p.read_text(encoding="utf-8") if p.is_file() else None


def _hash_tree(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""
        for name in files:
            rel = f"{rel_dir}/{name}".lstrip("/")
            out[rel] = sha256_file(os.path.join(dirpath, name))
    return out


def _assert_non_qualifying(e, result, *, reason_contains=""):
    hist = e.eco.history()
    assert len(hist) == 1, f"expected ONE durable terminal record, got {hist}"
    rec = hist[0]
    assert rec["status"] != "PASS", f"durable PASS survived mutation: {rec}"
    assert result.status != "PASS", f"returned PASS after mutation: {result}"
    assert rec["status"] == result.status, (rec["status"], result.status)
    assert rec["late_mutation_detected"] is True, rec
    assert result.late_mutation_detected is True, result
    assert rec["qualification_class"] == "NON_QUALIFYING", rec
    if reason_contains:
        assert reason_contains.lower() in rec["reason"].lower(), rec


# ---------------------------------------------------------------------------
# GUARD: clean acceptance still binds every artifact.
# ---------------------------------------------------------------------------
def test_clean_run_guard_binds_candidate_deps_and_immutable(env):
    e = env
    seen = {}

    def downstream(result):
        seen["dir"] = result.immutable_snapshot_dir
        seen["bytes"] = _immutable_design(result.immutable_snapshot_dir)
        seen["dep_rev"] = result.dependency_revision

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    assert result.status == "PASS"
    hist = e.eco.history()
    assert len(hist) == 1 and hist[0]["status"] == "PASS", hist
    assert seen["bytes"] == ACCEPTED
    assert seen["dep_rev"] and len(seen["dep_rev"]) == 64
    assert stable_json_hash(_hash_tree(seen["dir"])) == result.candidate_revision
    # The bound dependency revision is still the one in the durable record.
    assert hist[0]["dependency_revision"] == seen["dep_rev"]


# ---------------------------------------------------------------------------
# A4-C / A2-01: dependency mutated after acceptance (in-process seam).
# ---------------------------------------------------------------------------
def _fake_dep_seam(monkeypatch, state, snapshot_a, snapshot_b):
    def fake_dep(*a, **kw):
        return dict(snapshot_b if state["flipped"] else snapshot_a)
    monkeypatch.setattr(
        eco_mod.EcoManager, "_dependency_snapshot", staticmethod(fake_dep)
    )


def test_dependency_mutation_after_acceptance_is_rejected(env, monkeypatch):
    e = env
    a = {"pins.json": "pinhash", "collateral_x": "A"}
    b = {"pins.json": "pinhash", "collateral_x": "B"}
    state = {"flipped": False}
    _fake_dep_seam(monkeypatch, state, a, b)

    def downstream(_result):
        state["flipped"] = True  # collateral mutates AFTER acceptance

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    _assert_non_qualifying(e, result, reason_contains="dependency")


def test_dependency_deletion_after_acceptance_is_rejected(env, monkeypatch):
    e = env
    a = {"pins.json": "pinhash", "collateral_x": "A"}
    state = {"deleted": False}

    def fake_dep(*a_args, **kw):
        if state["deleted"]:
            raise DepError("SIMULATED required collateral deleted after acceptance")
        return dict(a)
    monkeypatch.setattr(
        eco_mod.EcoManager, "_dependency_snapshot", staticmethod(fake_dep)
    )

    def downstream(_result):
        state["deleted"] = True

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    _assert_non_qualifying(e, result, reason_contains="dependency")


def test_dependency_substitution_post_acceptance_real_file_is_rejected(
    env, hermetic_deps
):
    e = env
    target = hermetic_deps.path_of(0)

    def downstream(_result):
        target.write_bytes(b"SIMULATED-SUBSTITUTED-AFTER-ACCEPTANCE")

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    _assert_non_qualifying(e, result, reason_contains="dependency")


def test_dependency_required_deletion_post_acceptance_real_file_is_rejected(
    env, hermetic_deps
):
    e = env
    target = hermetic_deps.path_of(1)

    def downstream(_result):
        os.remove(str(target))

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    _assert_non_qualifying(e, result, reason_contains="dependency")


def test_stale_revision_reference_cannot_launder_dependency_flip(
    env, monkeypatch
):
    """A downstream callback that rewrites ``result.dependency_revision`` must
    not launder the accepted identity: the binding authority is the frozen
    revision captured at acceptance, not the mutable result object."""
    e = env
    a = {"pins.json": "pinhash", "collateral_x": "A"}
    b = {"pins.json": "pinhash", "collateral_x": "B"}
    state = {"flipped": False}
    _fake_dep_seam(monkeypatch, state, a, b)
    expected = stable_json_hash(a)

    def downstream(result):
        state["flipped"] = True
        result.dependency_revision = stable_json_hash(b)  # forged stale reference

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    _assert_non_qualifying(e, result, reason_contains="dependency")
    # The durable terminal still states the revision that was AUTHORIZED.
    assert result.dependency_revision == expected, result
    assert e.eco.history()[0]["dependency_revision"] == expected


# ---------------------------------------------------------------------------
# A4-A: mutation in the window AFTER the final integrity rehash.
# ---------------------------------------------------------------------------
def _wrap_rehash_with_post_seal_writer(e, *, barrier):
    """Wrap the rehash so a clean verdict is followed by a real writer.

    ``barrier=True`` holds the wrapper until a daemon thread has landed the
    write (the authoritative Agent-4 A4-A reproduction). ``barrier=False``
    writes inline (the simpler A2 variant). Both keep the ORIGINAL signature
    ``(revision, immutable_dir)`` so the wrapper is transparent to the fix.
    """
    original = e.eco._accepted_integrity_violations
    state = {"dir": "", "writes": 0, "mutated": False}
    sealed = threading.Event()
    released = threading.Event()

    def writer():
        if barrier and not sealed.wait(5):
            return
        p = Path(state["dir"]) / "design.v"
        try:
            os.chmod(p, 0o600)  # best-effort chmod is not a seal
        except OSError:
            pass
        p.write_text(MUTANT, encoding="utf-8")
        state["mutated"] = True
        released.set()

    if barrier:
        thread = threading.Thread(target=writer, name="r4-post-seal-writer", daemon=True)
        thread.start()
    else:
        thread = None

    def wrapped(revision, immutable_dir):
        problems = original(revision, immutable_dir)
        state["dir"] = immutable_dir
        if not problems and not state["mutated"]:
            state["writes"] += 1
            if barrier:
                sealed.set()
                released.wait(5)  # hold the return until the writer lands
            else:
                writer()
        return problems

    e.monkeypatch.setattr(e.eco, "_accepted_integrity_violations", wrapped)
    return state, thread


def test_post_seal_immutable_mutation_barrier_is_detected(env):
    e = env
    state, thread = _wrap_rehash_with_post_seal_writer(e, barrier=True)
    try:
        result = _run(e, verify=_provider(e.rep), downstream=lambda _r: None)
    finally:
        if thread is not None:
            thread.join(timeout=5)
    assert state["mutated"] is True, "the post-seal writer never landed"
    if thread is not None:
        assert not thread.is_alive(), "daemon writer did not finish"
    _assert_non_qualifying(e, result)
    # Honest residual: the bytes on disk are NOT restored by post-hoc hashing.
    # What MUST change is the durable terminal state.
    assert state["dir"], "no immutable path was handed to the integrity check"
    assert _immutable_design(state["dir"]) == MUTANT


def test_immutable_snapshot_tamper_after_seal_is_detected(env):
    e = env
    state, _thread = _wrap_rehash_with_post_seal_writer(e, barrier=False)
    result = _run(e, verify=_provider(e.rep), downstream=lambda _r: None)
    assert state["mutated"] is True
    _assert_non_qualifying(e, result)


def test_mutation_between_rehash_and_final_persist_is_detected(env, monkeypatch):
    """A writer landing during the final persist must still be caught by the
    last-boundary recheck and must amend the already-committed record."""
    e = env
    original_check = e.eco._accepted_integrity_violations
    state = {"done": False, "dir": ""}

    def hooked_check(*a, **k):
        problems = original_check(*a, **k)
        if not state["done"] and len(a) > 1 and a[1]:
            state["done"] = True
            state["dir"] = a[1]
            p = Path(state["dir"]) / "design.v"
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
            p.write_text(MUTANT, encoding="utf-8")
        return problems

    monkeypatch.setattr(e.eco, "_accepted_integrity_violations", hooked_check)

    def downstream(_result):
        raise RuntimeError("SIMULATED downstream failure -> final persist path")

    result = _run(e, verify=_provider(e.rep), downstream=downstream)
    assert state["done"] is True
    _assert_non_qualifying(e, result)


def test_post_commit_mutation_without_downstream_is_detected(env, monkeypatch):
    """A4-D: the final binding recheck must also run with no downstream."""
    e = env
    original_check = e.eco._accepted_integrity_violations
    state = {"done": False}

    def hooked_check(*a, **k):
        problems = original_check(*a, **k)
        if not state["done"]:
            state["done"] = True
            (e.ws / "design.v").write_text(MUTANT, encoding="utf-8")
        return problems

    monkeypatch.setattr(e.eco, "_accepted_integrity_violations", hooked_check)
    result = _run(e, verify=_provider(e.rep), downstream=None)
    assert state["done"] is True
    _assert_non_qualifying(e, result)
