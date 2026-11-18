"""SIMULATED stage-gating regression tests for SR-02 (acceptance before downstream).

All stages, tools and artifacts in this module are FAKES.  No EDA tool, WSL
session, network or credential is used.  Two layers are exercised:

1. The pure policy model embedded in ``regression/checks/acceptance.py``
   (delimited by STAGE GATING MODEL markers) - this is the executable spec that
   ``regression/run_regression.ps1`` mirrors.
2. ``SimulatedStageFlow`` - a deliberately small fake orchestrator that applies
   the same policy to fake stages, so the *behaviour* (not just the helpers) is
   checked: zero-exit invalid output, descendant invalidation, resume after
   deleted/modified artifacts, intermediate failure, and fresh ValidateOnly.
3. The real ``acceptance.py`` CLI, run via subprocess against a fake run dir, to
   prove that a second validation observes changed evidence rather than reusing
   a prior PASS.

The tests are labelled SIMULATED throughout and are deterministic.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ACCEPTANCE = REPO / "regression" / "checks" / "acceptance.py"
RUNNER = REPO / "regression" / "run_regression.ps1"


@pytest.fixture(scope="module")
def gate():
    """Load only the pure SR-02 model from acceptance.py (no CLI side effects)."""
    src = ACCEPTANCE.read_text(encoding="utf-8", errors="replace")
    start = src.index("# --- BEGIN STAGE GATING MODEL (SR-02)")
    end = src.index("# --- END STAGE GATING MODEL")
    ns = {}
    exec(compile(src[start:end], "acceptance_stage_gating_model", "exec"), ns)
    return ns


def _required(model, stage):
    return {name: "PASS" for name in model["REQUIRED_CHECKS"][stage]}


def _all_stage_checks(model):
    return {stage: _required(model, stage) for stage in model["STAGE_DEPS"]}


def _full_context():
    return {
        "RTL": "rtl-h", "SDC": "sdc-h", "LIB": "lib-h", "PDK": "pdk-h",
        "DECK": "deck-h", "PDN": "pdn-h", "TOOLS": "tools-h", "CONFIG": "config-h",
    }


def _prior_ledger(model):
    prior = {}
    for stage in model["STAGE_DEPS"]:
        prior[stage] = {
            "qualification": "QUALIFIED_PASS",
            "evidence_persisted": True,
            "context": _full_context(),
            "script_hash": "script-" + stage,
        }
    return prior


SIM_ARTIFACTS = {
    "01_synthesis": ["synth/mapped.v"],
    "04_floorplan": ["04_floorplan.odb"],
    "11_extract": ["11_final.spef"],
    "12_sta": ["sta/sta_summary.txt"],
    "13_gds": ["design_pdn.gds"],
}


class FakeAcceptance:
    """SIMULATED acceptance provider; records every fresh invocation."""

    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.calls = []

    def check(self, stage, checks):
        self.calls.append(stage)
        result = dict(checks)
        result.update(self.overrides.get(stage, {}))
        return result


class SimulatedStageFlow:
    """SIMULATED orchestrator mirroring run_regression.ps1's SR-02 loop.

    Fake stages either 'execute' (given an exit code and check statuses) or are
    skipped on a genuinely revalidated resume.  Dependencies must achieve
    authoritative acceptance before a consumer is authorised.
    """

    def __init__(self, model, *, execution=None, prior=None, resume=False,
                 validate_only=False, changed_tags=(), changed_stages=(),
                 artifact_state=None, context=None, persistence_ok=True,
                 requested=None):
        self.m = model
        self.execution = execution or {}       # stage -> (exit_code, checks)
        self.prior = prior or {}
        self.resume = resume
        self.validate_only = validate_only
        self.context = context or _full_context()
        self.persistence_ok = persistence_ok
        self.artifact_state = artifact_state or {}
        self.acceptance = FakeAcceptance()
        self.script_hashes = {s: "script-" + s for s in model["STAGE_DEPS"]}
        self.invalidated = model["affected_by_change"](
            changed_tags, changed_stages)
        if resume and not self.prior:
            self.invalidated.update(model["STAGE_DEPS"])
        self.requested = requested
        self.results = {}
        self.executed = []

    def _resume_ok(self, stage):
        artifacts = {}
        for rel in SIM_ARTIFACTS.get(stage, []):
            artifacts[rel] = self.artifact_state.get(rel, "OK")
        return self.m["resume_allowed"](
            stage, self.prior.get(stage), self.context,
            self.script_hashes[stage], artifacts, self.invalidated)

    def run(self):
        m = self.m
        order = [s for s in m["STAGE_DEPS"]
                 if self.requested is None or s in self.requested]
        if self.validate_only and self.requested:
            expanded = set()
            for s in order:
                expanded.add(s)
                expanded.update(m["transitive_deps"](s))
            order = [s for s in m["STAGE_DEPS"] if s in expanded]

        blocked = False
        for stage in order:
            r = {"process": "NOT_RUN", "engineering": "NOT_TESTED",
                 "qualification": "NOT_QUALIFIED", "blocked": [],
                 "evidence_persisted": False}
            self.results[stage] = r

            if blocked:
                r["blocked"].append("earlier stage not accepted")
                continue

            # Prerequisite evidence must be accepted before authorisation.
            for dep in m["transitive_deps"](stage):
                if dep in self.results:
                    prev = self.results[dep]
                    if prev["qualification"] not in ("QUALIFIED_PASS", "VALIDATION_PASS"):
                        r["blocked"].append("dependency %s not accepted" % dep)
                elif self.resume:
                    ok, why = self._resume_ok(dep)
                    if not ok:
                        r["blocked"].append("dependency %s not resumable: %s" % (dep, why))
                else:
                    r["blocked"].append("dependency %s not executed in this run" % dep)
            if r["blocked"]:
                blocked = True
                continue

            skip = False
            if self.validate_only:
                skip = True
                r["process"] = "VALIDATION_ONLY"
            elif self.resume and stage not in self.invalidated and self._resume_ok(stage)[0]:
                skip = True
                r["process"] = "COMPLETED"  # reused, but revalidated
            else:
                exit_code, checks = self.execution.get(stage, (0, _required(m, stage)))
                self.executed.append(stage)
                r["process"] = m["process_status"](exit_code, True)
                r["_checks_input"] = checks

            # Fresh acceptance, even for skipped / resume / validate-only stages.
            base = r.get("_checks_input", _required(m, stage))
            checks = self.acceptance.check(stage, base)
            eng, _ = m["acceptance_status"](checks, m["REQUIRED_CHECKS"][stage])
            if r["process"] == "FAILED":
                eng = "FAIL"
            elif r["process"] == "NOT_RUN":
                eng = "INCONCLUSIVE"
            ev = self.persistence_ok
            if not ev and eng == "PASS":
                eng = "INCONCLUSIVE"
            r["engineering"] = eng
            r["evidence_persisted"] = ev
            r["qualification"] = m["qualification"](
                r["process"], eng, ev, validation_only=self.validate_only)
            r["_checks"] = checks

            if r["qualification"] not in ("QUALIFIED_PASS", "VALIDATION_PASS"):
                blocked = True
        return self.results


# --------------------------------------------------------------------------- SIMULATED tests

def test_policy_tables_are_complete(gate):
    """SIMULATED: every stage has deps declared and a non-empty required set."""
    assert len(gate["STAGE_DEPS"]) == 16
    for stage, deps in gate["STAGE_DEPS"].items():
        assert gate["REQUIRED_CHECKS"].get(stage), stage
        assert gate["STAGE_INPUT_TAGS"].get(stage), stage
        for dep in deps:
            assert dep in gate["STAGE_DEPS"]
    # Authoritative dependency edges.
    assert gate["STAGE_DEPS"]["15_lvs"] == ["13_gds", "11_extract"]
    assert "12_sta" in gate["descendants"]("11_extract")
    assert gate["descendants"]("15_lvs") == []


def test_zero_exit_invalid_output_blocks_consumer(gate):
    """SIMULATED: exit 0 with a FAIL acceptance check must block consumers."""
    flow = SimulatedStageFlow(
        gate,
        execution={s: (0, _required(gate, s)) for s in gate["STAGE_DEPS"]},
        # simulation: tool exits 0 but the mapped netlist is invalid
        )

    class _Invalid(FakeAcceptance):
        def check(self, stage, checks):
            result = super().check(stage, checks)
            if stage == "01_synthesis":
                result["mapped_netlist_exists"] = "FAIL"
            return result

    flow.acceptance = _Invalid()
    results = flow.run()
    assert results["01_synthesis"]["process"] == "COMPLETED"  # zero exit
    assert results["01_synthesis"]["engineering"] == "FAIL"
    assert results["01_synthesis"]["qualification"] == "NOT_QUALIFIED"
    assert "02_lec_generic" not in flow.executed
    assert "04_floorplan" not in flow.executed
    assert "15_lvs" not in flow.executed
    assert results["02_lec_generic"]["blocked"]


def test_zero_exit_inconclusive_evidence_blocks_consumer(gate):
    """SIMULATED: zero exit + missing required check is INCONCLUSIVE, not PASS."""
    flow = SimulatedStageFlow(
        gate,
        execution={s: (0, _required(gate, s)) for s in gate["STAGE_DEPS"]},
    )

    class _Missing(FakeAcceptance):
        def check(self, stage, checks):
            result = super().check(stage, checks)
            if stage == "03_lec_mapped":
                result.pop("all_partitions_proved", None)
            return result

    flow.acceptance = _Missing()
    results = flow.run()
    assert results["03_lec_mapped"]["process"] == "COMPLETED"
    assert results["03_lec_mapped"]["engineering"] == "INCONCLUSIVE"
    assert results["03_lec_mapped"]["qualification"] == "NOT_QUALIFIED"
    assert "04_floorplan" not in flow.executed


def test_changed_inputs_invalidate_descendants(gate):
    """SIMULATED: changed Liberty/PDK/RTL invalidate the stage and descendants."""
    lib_affected = gate["affected_by_change"](changed_tags=("LIB",))
    assert "12_sta" in lib_affected
    assert "13_gds" in lib_affected   # directly tagged LIB, and downstream of 11_extract
    assert "15_lvs" in lib_affected
    rtl_affected = gate["affected_by_change"](changed_tags=("RTL",))
    assert "01_synthesis" in rtl_affected
    assert "15_lvs" in rtl_affected
    script_affected = gate["affected_by_change"](
        changed_tags=(), changed_stages=("04_floorplan",))
    assert {"05_placement", "06_cts", "11_extract", "15_lvs"} <= script_affected


def test_changed_context_blocks_resume_and_reexecutes_descendants(gate):
    """SIMULATED: a changed input seen on resume forces fresh downstream work."""
    changed_ctx = _full_context()
    changed_ctx["LIB"] = "lib-h-CHANGED"
    flow = SimulatedStageFlow(
        gate, resume=True, prior=_prior_ledger(gate), context=changed_ctx,
        execution={s: (0, _required(gate, s)) for s in gate["STAGE_DEPS"]},
    )
    results = flow.run()
    assert "12_sta" in flow.executed          # its Liberty changed
    assert results["12_sta"]["qualification"] == "QUALIFIED_PASS"
    # Stage 04 is invalidated by LIB too; every descendant re-executes.
    assert "04_floorplan" in flow.executed
    assert "15_lvs" in flow.executed
    ok, why = gate["resume_allowed"](
        "12_sta", _prior_ledger(gate)["12_sta"], changed_ctx,
        "script-12_sta", {}, set(gate["affected_by_change"](changed_tags=("LIB",))))
    assert not ok
    assert any("LIB" in w for w in why)


def test_deleted_or_modified_artifacts_block_resume(gate):
    """SIMULATED: deleted/modified artifacts cannot resume PASS."""
    prior = _prior_ledger(gate)["11_extract"]
    script = "script-11_extract"
    ok_missing, why_missing = gate["resume_allowed"](
        "11_extract", prior, _full_context(), script,
        {"11_final.spef": "MISSING"}, set())
    ok_modified, why_modified = gate["resume_allowed"](
        "11_extract", prior, _full_context(), script,
        {"11_final.spef": "MODIFIED"}, set())
    assert not ok_missing and any("missing" in w for w in why_missing)
    assert not ok_modified and any("modified" in w for w in why_modified)
    ok_clean, _ = gate["resume_allowed"](
        "11_extract", prior, _full_context(), script,
        {"11_final.spef": "OK"}, set())
    assert ok_clean

    # A missing artifact makes the resume ineligible: the stage must be
    # re-executed rather than skipped on the strength of a prior PASS.
    flow = SimulatedStageFlow(
        gate, resume=True, prior=_prior_ledger(gate),
        artifact_state={"11_final.spef": "MISSING"},
        execution={s: (0, _required(gate, s)) for s in gate["STAGE_DEPS"]},
    )
    results = flow.run()
    assert "11_extract" in flow.executed
    assert results["11_extract"]["qualification"] == "QUALIFIED_PASS"
    assert "11_extract" in flow.acceptance.calls
    assert results["12_sta"]["process"] == "COMPLETED"  # fresh, not stale resume


def test_intermediate_failure_prevents_dependent_execution(gate):
    """SIMULATED: a failed acceptance mid-flow stops every dependent stage."""
    flow = SimulatedStageFlow(
        gate,
        execution={s: (0, _required(gate, s)) for s in gate["STAGE_DEPS"]},
    )
    flow.acceptance = FakeAcceptance({"01_synthesis": {"cell_counts_recorded": "FAIL"}})
    results = flow.run()
    assert results["01_synthesis"]["engineering"] == "FAIL"
    assert flow.executed == ["00_stage00", "01_synthesis"]
    for stage in gate["STAGE_DEPS"]:
        if stage not in ("00_stage00", "01_synthesis"):
            assert stage not in flow.executed
            assert results[stage]["process"] == "NOT_RUN"
    assert set(gate["blocked_by"]("01_synthesis")) >= {
        "02_lec_generic", "04_floorplan", "11_extract", "15_lvs"}


def test_evidence_persistence_failure_blocks_qualification(gate):
    """SIMULATED: persistence failure downgrades PASS to NOT_QUALIFIED."""
    assert gate["qualification"]("COMPLETED", "PASS", True) == "QUALIFIED_PASS"
    assert gate["qualification"]("COMPLETED", "PASS", False) == "NOT_QUALIFIED"
    assert gate["qualification"]("FAILED", "PASS", True) == "NOT_QUALIFIED"
    assert gate["qualification"]("COMPLETED", "INCONCLUSIVE", True) == "NOT_QUALIFIED"

    flow = SimulatedStageFlow(
        gate, persistence_ok=False,
        execution={s: (0, _required(gate, s)) for s in gate["STAGE_DEPS"]},
    )
    results = flow.run()
    assert results["00_stage00"]["engineering"] == "INCONCLUSIVE"
    assert results["00_stage00"]["qualification"] == "NOT_QUALIFIED"
    assert flow.executed == ["00_stage00"]


def test_partial_stage_run_requires_prerequisites(gate):
    """SIMULATED: -Stages cannot silently assume upstream PASS."""
    order, problems = gate["validate_execution_order"](["02_lec_generic"])
    assert problems  # '01_synthesis' (and 00) unmet
    order, problems = gate["validate_execution_order"](
        ["02_lec_generic"], already_accepted=["00_stage00", "01_synthesis"])
    assert order == ["02_lec_generic"] and not problems

    flow = SimulatedStageFlow(
        gate, requested=["02_lec_generic"],
        execution={s: (0, _required(gate, s)) for s in gate["STAGE_DEPS"]},
    )
    results = flow.run()
    assert results["02_lec_generic"]["blocked"]
    assert "02_lec_generic" not in flow.executed


def test_validate_only_is_fresh_and_does_not_reuse_prior_pass(gate):
    """SIMULATED: ValidateOnly revalidates every stage with fresh checks."""
    assert gate["validate_only_is_fresh"]() is True
    flow = SimulatedStageFlow(
        gate, validate_only=True, prior=_prior_ledger(gate),
        execution={s: (0, _required(gate, s)) for s in gate["STAGE_DEPS"]},
    )
    # Prior ledger says PASS, but this fresh validation says stage 03 FAILs.
    flow.acceptance = FakeAcceptance(
        {"03_lec_mapped": {"designs_equivalent": "FAIL"}})
    results = flow.run()
    assert flow.acceptance.calls[:4] == [
        "00_stage00", "01_synthesis", "02_lec_generic", "03_lec_mapped"]
    assert flow.executed == []                                  # no stage executed
    assert results["01_synthesis"]["process"] == "VALIDATION_ONLY"
    assert results["01_synthesis"]["qualification"] == "VALIDATION_PASS"
    assert results["03_lec_mapped"]["engineering"] == "FAIL"
    assert results["03_lec_mapped"]["qualification"] == "NOT_QUALIFIED"
    assert results["04_floorplan"]["process"] == "NOT_RUN"

    # A clean ValidateOnly re-checks every stage - it never reuses a prior PASS.
    clean = SimulatedStageFlow(
        gate, validate_only=True, prior=_prior_ledger(gate),
        execution={s: (0, _required(gate, s)) for s in gate["STAGE_DEPS"]},
    )
    clean_results = clean.run()
    assert set(clean.acceptance.calls) == set(gate["STAGE_DEPS"])
    assert all(r["qualification"] == "VALIDATION_PASS"
               for r in clean_results.values())
    assert clean.executed == []


def test_acceptance_cli_observes_changed_evidence(tmp_path):
    """Real acceptance.py CLI, fake run dir: a second call is a fresh check."""
    run = tmp_path / "run_fake"
    (run / "logs").mkdir(parents=True)
    (run / "stage00").mkdir()
    (run / "stage00" / "stage00.log").write_text("stage00 ok\n", encoding="utf-8")
    (run / "stage00" / "stage00_report.txt").write_text("report\n", encoding="utf-8")
    result_json = run / "stage00" / "stage00_result.json"
    result_json.write_text(json.dumps({"overall": "PASS", "mandatory_failures": 0}),
                           encoding="utf-8")

    def invoke(out):
        proc = subprocess.run(
            [sys.executable, str(ACCEPTANCE), str(run), str(run / "logs"),
             "--out", str(out)],
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        return proc, json.loads(out.read_text(encoding="utf-8"))

    first_proc, first = invoke(run / "acceptance_first.json")
    assert first_proc.returncode == 0, first_proc.stdout + first_proc.stderr
    first_s00 = {c["name"]: c["status"] for c in first["checks"]
                 if c["stage"] == "00_stage00"}
    assert first_s00.get("stage00_overall_pass") == "PASS"

    # Mutate the authoritative evidence and re-validate - must NOT reuse PASS.
    result_json.write_text(json.dumps({"overall": "FAIL", "mandatory_failures": 2}),
                           encoding="utf-8")
    second_proc, second = invoke(run / "acceptance_second.json")
    second_s00 = {c["name"]: c["status"] for c in second["checks"]
                  if c["stage"] == "00_stage00"}
    assert second_proc.returncode != 0
    assert second_s00.get("stage00_overall_pass") == "FAIL"
    assert second_s00.get("stage00_no_mandatory_failures") == "FAIL"
    assert second != first


def test_runner_script_encodes_the_same_policy():
    """Production script contains the SR-02 mechanisms the model specifies."""
    src = RUNNER.read_text(encoding="utf-8", errors="replace")
    for token in (
        "SR-02 gate policy", "RequiredChecks", "Test-StagePrereqs",
        "Test-StageResumeValid", "Get-TransitiveDeps", "Get-Descendants",
        "FRESH independent acceptance", "--out", "Save-Ledger",
        "evidence_persistence_failures", "VALIDATION_PASS", "QUALIFIED_PASS",
        "process_status", "engineering_status", "qualification_status",
    ):
        assert token in src, token
    # The old post-loop-only acceptance pattern must be gone.
    assert "manifest finalize(pre)" not in src
    assert "if (-not $ValidateOnly) {\n  Write-Host \"\"\n  Write-Host \"--------- independent acceptance checks\"" not in src
