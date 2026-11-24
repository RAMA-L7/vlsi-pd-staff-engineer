"""R3 regression for R2-02: complete, deterministic dependency closure.

All providers and collateral in this file are SIMULATED / injected. No real
EDA tool, no live inference and no external service is used. A temporary
``pins.json`` (mirroring the real ``portable_config.collateral`` *list* schema)
is written per test and ``assistant.eco.__file__`` is redirected to it, so the
same tests exercise the vulnerable baseline and the fixed implementation.

EXPLOIT tests FAIL on the vulnerable code (``_dependency_snapshot()`` returns a
single ``pins.json`` key and never reads the collateral list) and PASS after
the fix. GUARD tests assert that the fix does not weaken any existing gate.
"""
from __future__ import annotations

import json
import os
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
from assistant.statuses import Status

SIM = "SIMULATED R2-02 dependency-closure regression"
BASELINE = "module d; endmodule\n"
# On the vulnerable baseline this symbol does not exist; using a local alias
# keeps the module importable so every exploit test can fail for the RIGHT
# reason (no exception raised / wrong snapshot), not a collection error.
DepError = getattr(eco_mod, "EcoDependencyError", RuntimeError)

ENV_BASE_VARS = (
    "REG_PLATFORM_SKY130HD", "ASSISTANT_PLATFORM_HD",
    "REG_PLATFORM_SKY130HS", "ASSISTANT_PLATFORM_HS",
)


def _make_entries(hd, hs, n=22):
    """Build n schema-faithful collateral declarations with real temp files."""
    entries = []
    for i in range(n):
        key = "collateral_%02d.dat" % i
        base = "platform_hs" if i % 7 == 0 else "platform_hd"
        root = hs if base == "platform_hs" else hd
        rel = "collateral_%02d.dat" % i
        (root / rel).write_bytes(("payload-%02d" % i).encode("utf-8"))
        entries.append({
            "key": key,
            "path": rel,
            "base": base,
            "sha256": "0" * 64,  # canonical 64 hex; closure hashes actual bytes
            "required": i < 12,  # 12 required + 10 optional, like the real pins
            "used_by": "SIMULATED dependency-closure regression",
        })
    return entries


@pytest.fixture
def pins(tmp_path, monkeypatch):
    """A hermetic pins.json written to a temp dir and bound into eco.__file__."""
    hd = tmp_path / "sky130hd"
    hs = tmp_path / "sky130hs"
    hd.mkdir()
    hs.mkdir()
    for var in ENV_BASE_VARS:
        monkeypatch.delenv(var, raising=False)
    entries = _make_entries(hd, hs)

    state = {"path": None}

    def write(entries_override=None, *, fallbacks=None, raw=None, top=None):
        manifest = top if top is not None else {
            "portable_config": {
                "fallbacks": fallbacks or {
                    "platform_hd": str(hd), "platform_hs": str(hs),
                },
                "collateral": entries if entries_override is None else entries_override,
            }
        }
        p = tmp_path / "pins.json"
        p.write_text(raw if raw is not None else json.dumps(manifest), encoding="utf-8")
        monkeypatch.setattr(eco_mod, "__file__", str(p))
        state["path"] = p
        return p

    def path_of(entry):
        """Resolve an entry's declared path against its declared base."""
        root = hs if entry.get("base") == "platform_hs" else hd
        return root / entry["path"]

    write()
    return SimpleNamespace(
        hd=hd, hs=hs, entries=entries, write=write, path=lambda: state["path"],
        path_of=path_of, monkeypatch=monkeypatch, tmp_path=tmp_path,
    )


# ---------------------------------------------------------------------------
# Snapshot-level EXPLOIT tests
# ---------------------------------------------------------------------------

def test_all_22_collateral_hashed(pins):
    """EXPLOIT: the snapshot must contain every declared collateral, not just pins.json."""
    snap = eco_mod.EcoManager._dependency_snapshot()
    expected = {"pins.json"} | {e["key"] for e in pins.entries}
    # R5: resolution-environment bindings are also part of the accepted closure.
    resolution_keys = {k for k in snap if k.startswith("@resolution.")}
    assert {k for k in snap if not k.startswith("@")} == expected, (
        "vulnerable closure hashes only pins.json; expected 22 collateral entries, "
        f"got {sorted(snap)}"
    )
    assert resolution_keys, "R5: resolution environment must be bound"
    assert all(
        len(v) == 64 or v.startswith("<")  # explicit missing/optional sentinel
        for k, v in snap.items()
        if not k.startswith("@")
    ), "each file entry must carry a SHA-256 or an explicit sentinel"


def test_snapshot_is_deterministic_and_sorted(pins):
    """GUARD/EXPLOIT: repeated snapshots are byte-identical and key-sorted."""
    a = eco_mod.EcoManager._dependency_snapshot()
    b = eco_mod.EcoManager._dependency_snapshot()
    assert a == b
    assert list(a) == sorted(a)


def test_mutation_after_snapshot_is_detected(pins):
    """EXPLOIT: mutating a pinned collateral file changes its hash."""
    a = eco_mod.EcoManager._dependency_snapshot()
    target = pins.path_of(pins.entries[0])
    target.write_bytes(b"MUTATED-payload")
    b = eco_mod.EcoManager._dependency_snapshot()
    assert b[pins.entries[0]["key"]] != a[pins.entries[0]["key"]], (
        "a post-verification collateral mutation went unnoticed"
    )


def test_substitution_is_detected(pins):
    """EXPLOIT: swapping a required collateral for different bytes is detected."""
    a = eco_mod.EcoManager._dependency_snapshot()
    target = pins.path_of(pins.entries[1])
    other = pins.hd / "substitute.dat"
    other.write_bytes(b"a completely different file")
    target.write_bytes(other.read_bytes())  # same path, different content
    b = eco_mod.EcoManager._dependency_snapshot()
    assert b[pins.entries[1]["key"]] != a[pins.entries[1]["key"]]


def test_deletion_of_required_collateral_is_fail_closed(pins):
    """EXPLOIT: a missing required file must fail closed, not be silently skipped."""
    eco_mod.EcoManager._dependency_snapshot()  # baseline: all present
    pins.path_of(pins.entries[0]).unlink()
    with pytest.raises(DepError):
        eco_mod.EcoManager._dependency_snapshot()


def test_duplicate_key_is_fail_closed(pins):
    """EXPLOIT: two declarations sharing a key are an invalid closure."""
    entries = list(pins.entries)
    dup = dict(entries[0])
    dup["path"] = "collateral_duplicate.dat"
    (pins.hd / dup["path"]).write_bytes(b"dup")
    entries.append(dup)
    pins.write(entries)
    with pytest.raises(DepError):
        eco_mod.EcoManager._dependency_snapshot()


def test_path_traversal_is_fail_closed(pins):
    """EXPLOIT: '..' / absolute paths must be rejected outright."""
    for bad in ("../outside.dat", "/etc/passwd", "a/../../outside.dat"):
        entries = list(pins.entries)
        entries[0] = dict(entries[0], path=bad)
        pins.write(entries)
        with pytest.raises(DepError):
            eco_mod.EcoManager._dependency_snapshot()


def test_unsafe_symlink_is_fail_closed(pins, monkeypatch):
    """EXPLOIT: a symlink anywhere in a collateral path is rejected.

    Windows test hosts cannot create symlinks without elevation (WinError
    1314), so the islink predicate is injected. On the vulnerable code the
    injectable keyword does not exist -> TypeError -> the test fails, as it
    must, before the fix.
    """
    target = str(pins.path_of(pins.entries[0]))

    def fake_islink(p):
        return str(p) == target or os.path.basename(str(p)) == pins.entries[0]["path"]

    with pytest.raises(DepError):
        eco_mod.EcoManager._dependency_snapshot(_islink=fake_islink)


def test_empty_and_malformed_declarations_are_fail_closed(pins):
    """EXPLOIT: empty / non-object / malformed manifests cannot yield a PASS closure."""
    # empty collateral list
    pins.write([])
    with pytest.raises(DepError):
        eco_mod.EcoManager._dependency_snapshot()
    # collateral of the wrong type
    pins.write("not-a-list")
    with pytest.raises(DepError):
        eco_mod.EcoManager._dependency_snapshot()
    # malformed JSON
    pins.write(raw="{ this is not json")
    with pytest.raises(DepError):
        eco_mod.EcoManager._dependency_snapshot()
    # missing portable_config
    pins.write(top={"something_else": {}})
    with pytest.raises(DepError):
        eco_mod.EcoManager._dependency_snapshot()


def test_invalid_entry_fields_are_fail_closed(pins):
    """EXPLOIT: invalid entry structure (no key / bad base / bad required) is rejected."""
    for field, value in (("key", ""), ("base", "bogus"), ("required", "yes")):
        entries = list(pins.entries)
        entries[0] = dict(entries[0], **{field: value})
        pins.write(entries)
        with pytest.raises(DepError):
            eco_mod.EcoManager._dependency_snapshot()


def test_optional_missing_is_explicit_and_non_fatal(pins):
    """GUARD: absent optional collateral is recorded explicitly, not omitted."""
    opt = pins.entries[-1]  # i=21 is optional (i >= 12)
    pins.path_of(opt).unlink()
    snap = eco_mod.EcoManager._dependency_snapshot()
    assert opt["key"] in snap
    assert snap[opt["key"]] == "<missing-optional>"


# ---------------------------------------------------------------------------
# Integration tests through run_experiment / acceptance revalidation
# ---------------------------------------------------------------------------

def _make_env(tmp_path, monkeypatch):
    ws = tmp_path / "ws2"
    ws.mkdir()
    (ws / "design.v").write_text(BASELINE, encoding="utf-8")
    ctl = GateController(str(tmp_path / "state2.json"))
    for fact in ("has_macros", "has_upf", "has_clock_gates", "has_analog"):
        ctl.register_design_fact(fact, False, source="SIMULATED inventory")
    eco = EcoManager(
        str(ws), ctl,
        checkpoint_dir=str(tmp_path / "ckpt2"),
        experiment_log=str(tmp_path / "exp2.json"),
    )
    rep = tmp_path / "sim_report2.txt"
    rep.write_text("SIMULATED check report: all checks clean\n", encoding="utf-8")
    return SimpleNamespace(ws=ws, ctl=ctl, eco=eco, rep=rep, monkeypatch=monkeypatch)


def _ev(rep, cid):
    return Evidence.from_run(
        cid, Status.PASS,
        reason="SIMULATED", command=["sim"], exit_code=0,
        analysis_view="MAPPED_NETLIST", tool_versions={"sim": "1"},
        reports=[str(rep)],
    )


def _provider(env, on_verify=None):
    def verify():
        if on_verify is not None:
            on_verify()
        return [
            _ev(env.rep, "eco_lec"),
            _ev(env.rep, "eco_sta"),
            _ev(env.rep, "eco_physical_drc"),
        ]
    return verify


def _run(env, **kw):
    kw.setdefault("simulated", SIM)
    return env.eco.run_experiment(
        change_scope=EcoChangeScope(
            "logic_optimization", EcoStage.MAPPED, True, "SIMULATED recorded scope"
        ),
        experiment_id="r2-02",
        description="SIMULATED R2-02 dependency-closure regression",
        change_class="logic_optimization",
        changed_files=["design.v"],
        apply_change=lambda: None,
        evaluate=lambda: {"wns": 1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0)],
        verify=kw.pop("verify", None),
        downstream_stage=kw.pop("downstream", None),
        verification_timeout_s=kw.pop("timeout", 10.0),
        **kw,
    )


def test_dependency_change_after_verification_rejects(pins, tmp_path, monkeypatch):
    """EXPLOIT: mutation between verification and acceptance must block PASS."""
    env = _make_env(tmp_path, monkeypatch)
    target = pins.path_of(pins.entries[0])

    def mutate():
        target.write_bytes(b"mutated-after-verification")

    result = _run(env, verify=_provider(env, on_verify=mutate))
    assert result.status != "PASS", "mutated collateral was accepted with PASS"
    assert "dependency" in result.reason.lower(), result.reason


def test_dependency_change_during_acceptance_rejects(pins, tmp_path, monkeypatch):
    """EXPLOIT: mutation at acceptance-time revalidation must block PASS."""
    env = _make_env(tmp_path, monkeypatch)
    target = pins.path_of(pins.entries[0])
    original = eco_mod.EcoManager._dependency_snapshot
    calls = {"n": 0}

    def wrapped():
        calls["n"] += 1
        if calls["n"] == 2:  # the acceptance revalidation call
            target.write_bytes(b"mutated-during-acceptance")
        return original()

    monkeypatch.setattr(
        eco_mod.EcoManager, "_dependency_snapshot", staticmethod(wrapped)
    )
    result = _run(env, verify=_provider(env))
    assert calls["n"] >= 2
    assert result.status != "PASS", "collateral changed during acceptance was accepted"
    assert "dependency" in result.reason.lower(), result.reason


def test_clean_run_still_passes(pins, tmp_path, monkeypatch):
    """GUARD: the complete closure must not block a clean, unmutated candidate."""
    env = _make_env(tmp_path, monkeypatch)
    result = _run(env, verify=_provider(env))
    assert result.status == "PASS", (result.status, result.reason)


def test_malformed_manifest_blocks_acceptance(pins, tmp_path, monkeypatch):
    """EXPLOIT: a malformed closure must block a PASS at run_experiment level."""
    env = _make_env(tmp_path, monkeypatch)
    pins.write([])  # empty collateral declaration
    called = []

    def verify():
        called.append(1)
        return [_ev(env.rep, "eco_lec")]

    result = _run(env, verify=verify)
    assert result.status == "BLOCKED", (result.status, result.reason)
    assert called == [], "verification must not run against an unestablished closure"
