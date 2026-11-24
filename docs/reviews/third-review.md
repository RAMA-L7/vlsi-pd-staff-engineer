# Third independent review — finding verification and remediation (TR-00 .. TR-04)

**Reviewer role:** principal PD methodology engineer / software verification lead (internal
remediation pass). **Independent rating is deliberately not assigned here.**

## Starting identity (exact)

| Item | Value |
|---|---|
| Commit reviewed | `d8f2117e0e09df03e633ff2789faa699e09e748d` (`docs: refresh documentation to published state`) |
| Tree | `e9beaa22f2ecb272d21b756cd628ea2d46345c7e` |
| Branch / remote | `main` / `origin`, aligned at `d8f2117` |
| Working tree | clean at start; no unrelated user changes present |
| External review target | `45e4251370c39ed27e2ccd6a7a804ed90eaf4472` (ancestor of the reviewed commit) |
| Protected repo | `D:\audit\repo` at `41728cd…`, 34 pre-existing changes — untouched |

**Starting test baseline:** `pytest -q` → **323 passed, 1 failed**. The single failure
(`tests/test_foundation.py::test_readme_has_one_current_matrix_and_corrected_timing`)
encoded the superseded base SHA `0a9eb57…` as a current-identity assertion; the published
identity is `45e4251…`. This was a stale guard, not an engineering failure; it was corrected
to assert the published full SHA plus the base SHA as historical provenance.

## Findings (static observations verified against the current checkout)

| ID | Severity | Paths | Verified? | Disposition |
|---|---|---|---|---|
| TR-01 | P1 | `assistant/eco.py`, `assistant/controller.py`, `tests/test_eco*.py`, `tests/test_failure_injection.py`, `tests/test_next_phase_robustness.py` | YES — blanket `NA-RULE:NO-MACROS` on `eco_physical_drc` in all 7 substantive classes (`eco.py:112-160`) | **FIXED** (stage/scope-aware applicability) |
| TR-02 | P1 | `assistant/eco.py` (`run_experiment`, `_collect_verification`) | YES — `evaluate()` at old `eco.py:908` ran after verification/unlisted checks with no re-hash, re-gate or revision binding | **FIXED** (immutable candidate revision, read-only evaluation, pre-acceptance revalidation) |
| TR-03 | P2 | `assistant/eco.py`, `assistant/runner.py` | YES — `list(verify())` at old `eco.py:588` unbounded; `duration_s` comparison at `698-710` post-hoc, self-reported, production `timeout=None` | **FIXED** (monotonic deadline, streamed evidence, process-tree termination, late-write detection) |
| TR-04 | P2 | `regression/run_regression.ps1`, `tests/test_stage_gating.py`, new integration tests | YES — 10/12 scenarios ran only against `SimulatedStageFlow`; no test invoked the real runner | **FIXED** (additive DI seams + 9 real-runner integration tests with simulated tools) |

No finding was disproved. All four were reproduced in code before remediation. No finding
was left unresolved at the code level; external/physical limitations are listed separately.

---

## TR-01 — correct physical-check applicability

**Root cause.** `CHANGE_CLASS_POLICY` hard-coded the same `eco_physical_drc` N/A reason in
every substantive change class. The rule token `NA-RULE:NO-MACROS` is corroborated only by
`has_macros=False` (a macro inventory fact); nothing relates it to physical scope. Standard-cell
resizing, buffer insertion, placement and routing changes can introduce DRC violations with no
macros, and `regression/stages/14_drc.sh` is a real executable DRC stage for standard cells.

**Implementation.**
- New `EcoStage` (`rtl/generic/mapped/place/route/fill`) and `EcoChangeScope` (change class,
  target stage, `physical_implementation_present`, mandatory `stage_fact_source`). Scope facts
  cannot be inferred from the caller's `description`.
- New `CheckApplicability` and `applicability_for()` resolve each required check from the
  recorded scope. `eco_lec` and `eco_sta` remain mandatory with stage-specific requirements
  declared in `STAGE_CHANGE_REQUIREMENTS`.
- `eco_physical_drc` requires a fresh PASS whenever a physical implementation is present —
  including standard-cell-only placed/routed designs. N/A is permitted only with the new
  registered rule `NA-RULE:NO-PHYSICAL-IMPLEMENTATION`, corroborated by the recorded fact
  `eco_physical_implementation_present=False` via the controller's existing validated-fact path.
- `run_experiment()` fails closed (`BLOCKED`) when no scope is supplied. Power-intent changes
  on a physical implementation are **BLOCKED/UNSUPPORTED** (no power-intent verifier exists).
- No check provider was invented: `verify()` remains caller-supplied; the only real DRC
  executor remains `regression/stages/14_drc.sh`.

**Regressions.** `tests/test_third_review_regressions.py`:
`test_standard_cell_routed_eco_with_drc_violation_is_rejected`,
`test_netlist_only_experiment_is_logically_complete_but_physically_unqualified`,
`test_no_macros_cannot_exempt_physical_drc_for_placed_standard_cells`,
`test_missing_capability_blocks_power_intent_physical_qualification`,
`test_caller_prose_cannot_change_applicability`.

**Remaining limitation.** "Netlist-only" applicability depends on the recorded scope source
being truthful. The controller corroborates the fact but cannot independently prove that a
hidden placed database does not exist; callers must bind `stage_fact_source` to real run
evidence. No physical DRC provider was added to `assistant/` (out of scope).

---

## TR-02 — verify the exact state that is accepted

**Root cause.** Verification and the unlisted-change check ran before `evaluate()`; after
`evaluate()` only metric comparison and one cancellation flag stood between a silently mutated
candidate and a persisted PASS plus downstream execution. No revision/evidence binding existed.

**Implementation.**
- `CandidateRevision` records `stable_json_hash(snapshot)` of the whole verified workspace,
  the snapshot, evidence IDs, verification report and measured duration. It is stored on
  `ExperimentResult` (`candidate_revision`, `candidate_snapshot`) and bound into
  `verification["candidate_revision"]` / `["downstream_candidate_revision"]`.
- `evaluate()` is explicitly read-only: snapshots immediately before and after are compared;
  any difference rejects with `FAIL`, rolls back and blocks downstream.
- `_pre_acceptance_revalidation()` re-hashes the workspace against the verified revision,
  re-checks unlisted changes and cancellation, re-runs `revalidate_evidence()` on every
  recorded evidence ID, and verifies each is still the latest for its check before acceptance.
- Downstream receives the `ExperimentResult` carrying the accepted revision; a test proves the
  workspace hash at downstream invocation equals the accepted revision.
- Rollback checks are quiescence-aware (`_verify_candidate_quiescent`) and record
  `late_mutation_detected` / `rollback_verified` honestly.
- **Concurrency boundary:** one `EcoManager` owns the workspace and log for a transaction.
  No cross-process lock is implemented; concurrent writers are detected as unlisted changes,
  not synchronized. This is documented in the `run_experiment` docstring.

**Regressions.** `test_post_verification_mutation_blocks_acceptance` (declared, undeclared,
SDC, report), `test_dependency_change_between_verification_and_acceptance_blocks`,
`test_cancellation_before_acceptance_blocks_and_rolls_back`,
`test_final_evidence_persistence_failure_blocks`,
`test_downstream_receives_bound_immutable_revision`.

**Remaining limitation.** Net-zero mutation inside `evaluate()` (write then restore identical
bytes) is not observable by hashing; only the final accepted bytes are bound to evidence. A
malicious callback could still mutate state outside the workspace; out of scope for this engine.

---

## TR-03 — real verification deadlines

**Root cause.** `list(verify())` materialized an arbitrary callback synchronously; the only
"timeout" compared a provider-supplied optional `duration_s` to a limit after the fact, with
production always passing `None`. Cancellation was cooperative and never observed mid-call.

**Implementation.**
- `_collect_verification()` computes `deadline = time.monotonic() + timeout_s` and streams
  evidence from a supervised worker thread with bounded queue waits. On deadline or
  cancellation it marks the partial report `incomplete`/`deadline_exceeded`, records measured
  monotonic wall-clock (`measured_duration_s`) independently of provider fields, raises
  `EcoTimeoutError`/`EcoCancellationError`, and the existing transaction handler rolls back
  and blocks downstream.
- `VerificationContext` gives cooperative providers `check()`, `remaining()`, `measured_duration_s`
  and a managed `spawn()` that delegates to `assistant.runner.run_command`.
- `assistant/runner.py` now uses `time.monotonic()`, starts child processes in their own group,
  and on timeout performs tree termination (`taskkill /T /F` on Windows, `killpg` on POSIX)
  with explicit `termination`/`termination_detail` and `pid` on `RunResult`. Failure to kill
  is reported, never assumed.
- Provider `duration_s` is now informational only (validated finite/non-negative, recorded per
  check); it cannot enforce or defeat a deadline. Aggregate budget is the overall deadline.
- Non-cooperative Python callbacks cannot be forcibly killed in-process. The framework
  **abandons** them on deadline and detects late writes after rollback
  (`late_mutation_detected`); it does not claim a hard deadline for that path.

**Regressions.** `test_slow_verifier_deadline_is_enforced_and_rolls_back`,
`test_partial_evidence_survives_deadline`, `test_false_short_provider_duration_does_not_defeat_deadline`,
`test_missing_provider_duration_is_still_deadline_bounded`,
`test_context_aware_provider_can_cooperatively_cancel`,
`test_late_worker_mutation_after_rollback_is_detected`,
`test_process_tree_termination_kills_descendants` (real child/grandchild processes).

**Remaining limitation.** Arbitrary Python threads cannot be forcibly terminated; WSL2 is a
separate VM, so Linux-side grandchildren need cooperative cleanup. The enforced path is
`VerificationContext.spawn()` on the supported Windows/WSL host; other paths are detected, not
claimed as hard deadlines.

---

## TR-04 — test the real runner

**Root cause.** `tests/test_stage_gating.py` used `SimulatedStageFlow`, a Python mirror of the
PowerShell SR-02 loop, plus a textual grep of the runner. Mirror tests cannot detect a
production regression in the real scheduler.

**Implementation.** Additive, default-preserving DI seams in `regression/run_regression.ps1`:
`-StageExecutor`, `-AcceptanceExecutor`, `-ManifestExecutor`, `-EvidenceDirOverride`,
`-WslDistro`, `-SkipToolProbe`. The SR-02 gating loop, dependency authorization, acceptance
parsing, persistence, resume and verdict code are unchanged. New
`tests/test_runner_integration_simulated.py` invokes the **actual** `run_regression.ps1` with
fake tool/acceptance/manifest executables, isolated evidence output and a temp git repo,
asserting on real `report.json`, `stage_status.json`, `validation_status.json`, `gate_ledger.json`
and launch sentinels.

Covered scenarios: full 16-stage pass and final report; zero tool exit + engineering FAIL
blocking the first consumer; nonzero tool exit; missing/malformed acceptance output; absent
mandatory check; evidence persistence failure; partial-stage prerequisite enforcement;
ValidateOnly observing a new failure without execution; changed-input descendant invalidation
on resume.

**Remaining limitation.** The 12 review scenarios are covered at runner level except the
acceptance-CLI mutation case (already covered at provider level in
`test_acceptance_cli_observes_changed_evidence`) and a timeout/cancellation-in-runner case
(runner has no deadline seam; the ECO deadline path is covered separately). These are recorded
as NOT_TESTED at runner level in `third-review-validation.md` rather than claimed.
Spec consolidation remains partial: `acceptance.py` and `run_regression.ps1` still maintain
parallel stage tables; one JSON source was not introduced in this pass (risk recorded).
