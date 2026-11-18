# Second Review — Supervised PD Assistant (static findings to revalidate)

**Review baseline:** commit `0a9eb57e38b86acc51b8bcb6dea53bd97ab90e9b` (tree to be recorded in final report)
**Repository:** `D:\audit\m1g_mapping_20261117`, `origin/main` = `RAMA-L7/vlsi-pd-staff-engineer`
**Starting working tree:** clean (no pre-existing changes)
**Mode:** hypotheses; every item must be verified against the current source before remediation. Disproved/already-fixed items will be recorded with evidence. No commit, push or publication is authorized in this pass.

Severity: **S1** = false PASS / failed rollback / gate bypass; **S2** = reproducibility or correctness gap; **S3** = documentation/claim hygiene.

---

## Priority 1 — false-PASS and failed-rollback paths

### SR-01 — ECO verification does not control acceptance (`assistant/eco.py`) — S1
Hypothesized sub-findings:
1. `verify()` evidence recorded without statuses determining acceptance.
2. Experiment can be retained when WNS improves but LEC FAILs.
3. Verification optional.
4. Exceptions from `verify()` escape rollback handling.
5. No registered change-class → required-check policy.
6. No transactional rollback across apply/verify/evaluate.
7. Rollback restoration not verified.
8. Experiment history may omit failed attempts/evidence.
9. NaN/infinity/malformed metrics or missing protected metrics not rejected.
10. Checkpoint/rollback paths not constrained to the experiment workspace.
11. Unlisted file modifications can survive an experiment.

Required outcome: required fresh verification for every change class; FAIL/INCONCLUSIVE/UNSUPPORTED/missing/timeout/cancel/exception all reject and roll back; explicit N/A policy; persistent history; safe paths; no unlisted changes.

Required regression: ECO improves WNS but corrupts function; LEC rejects; no PASS; files restored; downstream stage not run.

### SR-02 — Acceptance before downstream execution (`regression/run_regression.ps1`, `assistant/`) — S1
Hypothesized:
1. Acceptance checks run after the stage loop.
2. Zero-exit stage advances before engineering acceptance.
3. Resume trusts prior PASS + artifact existence without input validation.
4. No explicit stage dependencies/required checks.
5. Evidence/provenance persistence failure can still yield PASS.
6. Partial-stage runs do not validate prerequisites.
7. Resume does not revalidate inputs/outputs/tools/config/dependencies.
8. Changed inputs do not invalidate descendants.
9. `ValidateOnly` may not perform fresh validation.
10. Process completion, engineering acceptance and qualification not distinguished.

Required regressions: zero-exit invalid output blocks consumer; changed RTL/SDC/Liberty/PDK/stage script invalidates results; deleted/modified artifacts cannot resume PASS; intermediate failure prevents dependent execution.

### SR-03 — Central gate API (`assistant/controller.py` and callers) — S1
Hypothesized:
1. Direct API bypasses CLI provenance validation.
2. PASS with `exit_code=None`.
3. Empty required-check list can PASS.
4. Upstream gates trust historical PASS without reevaluation.
5. Waiver expiry not enforced.
6. No registered gate specifications.
7. Pure internal vs tool-executed checks not distinguished.
8. Dependency cycles not detected.
9. Input registrations not safely persisted across restart.
10. Waiver identity/scope/policy/input revision not enforced.
11. Non-waivable functional proof failures not explicit.
12. Waived disposition not distinguished from clean PASS.
13. Applicability based only on reason string, not validated design facts.
14. Cancellation does not block new work until valid restart.

### SR-04 — Evidence dependency revalidation (`assistant/evidence.py`) — S1
Hypothesized:
1. Staleness covers `input_hashes` only.
2. Missing-report checks read metadata, not current file state.
3. No unified dependency model/persistent paths+hashes.
4. RTL/SDC/netlists/libraries/PDK/config/scripts/tools/extraction/deck collateral not included.
5. Dependencies and evidence artifacts not rehashed at evaluation.
6. Old report can be rebound to new input hashes.
7. Reports not bound to run/design/stage/tool/view.
8. Run output directories not isolated.
9. Cross-design/cross-corner evidence not rejected.

### SR-05 — Unsafe view ordering (`view_compatible()`) — S1
Hypothesized total order lets `PHYSICAL_DATABASE` satisfy `FILL_EXTRACTED`. Required: explicit contracts separating artifact type, implementation stage, analysis/check type, parasitic source, fill status, timing mode/corner, library/extraction provenance. Physical DB must not satisfy extracted timing; GR estimate must not satisfy routed extraction; corner-specific evidence must not satisfy another; post-fill requires filled-design extraction+analysis proof.

### SR-06 — CLI exit codes (`assistant/cli.py`) — S1
Hypothesized STA, PVT validation and blocked gate evaluation can return shell exit zero. Required documented contract: zero only on successful validation; nonzero for engineering failure, blocked/incomplete, invalid input, timeout, tool-launch failure; structured JSON preserved; inspection commands distinct from validation. Tests must exercise real process exit codes.

---

## Priority 2 — physical-design correctness

### SR-07 — Flow/tool correctness audit — S1/S2
Verify against pinned tools/docs:
- Technology mapping and complete cell resolution.
- Formal preparation, complete partition proof, negative controls.
- No automatic dont-verify/black-box escape.
- Clock/generated clock/propagated clock/I/O constraint correctness; unconstrained-path detection.
- Setup/hold at each implemented view.
- Liberty PVT metadata/units.
- Routed extraction vs estimation.
- Physical clock-network ECOs vs timing annotation only.
- DRC deck identity and helper-category justification.
- LVS design/top selection, power connectivity, authoritative verdict.
- Partial/malformed report handling.
- Final verification against correct reference after transformations.
- Interface latency/behavior preservation by default.
- Fix hard-coded `test_counter` top in soc_ctrl LVS comparator if still present.
- Investigate soc_ctrl SS timing under approved constraints; preserve failing baseline; measured verified improvements only, no silent relaxation/behavior change/path exclusion; honest failure if unclosed.
- Mark IR drop/EM/antenna/UPF/post-fill unsupported unless genuinely implemented.

---

## Priority 3 — reproducibility and portability — S2
- Replace workstation-specific assumptions with validated config.
- Pin tool/flow/PDK/library/extraction/deck provenance.
- Document modified upstream collateral with legally distributable reproduction/patch.
- Preflight command reporting exact missing dependencies.
- Supported setup path and one documented baseline command.
- Clean isolated output directories.
- Baseline reproduced twice; semantic outputs compared; timestamp differences explained.
- Explicit supported platform/minimum versions.
- No claim of independent portability for developer-machine-only runs.

---

## Priority 4 — tests that challenge the system — S2
Add meaningful tests (not count-driven) for: failed formal proof despite WNS gain; partial partition proof; verification exception/timeout; cancellation during ECO; failed rollback; changed inputs after upstream PASS and after process restart; modified/deleted reports/artifacts; missing provenance via API and CLI; empty/incomplete gate definitions; expired/improperly scoped waivers; invalid N/A; cross-design/cross-corner reuse; physical DB as extracted timing; NaN/inf/missing metrics; zero-exit invalid tool output; resume/partial-stage bypass; CLI failure exit codes; stage dependency enforcement. Simulated providers/tools labelled; no credentials/live inference in default unit tests; real EDA integration separated.

---

## Priority 5 — benchmark and documentation cleanup — S3
- One current qualification matrix tied to commit/run IDs.
- Historical results in dated sections.
- Resolve contradictory soc_ctrl status/input-presence claims.
- Resolve negative WNS alongside PASS/zero-violation claims.
- Distinguish automated scripts from AI-agent orchestration.
- Distinguish supported capability from demonstrated successful run.
- Demonstrate on controller/datapath/macro example where supported.
- Retain legitimate failure cases.
- PPA claims only from measured analyses with explicit conditions.

---

## Disposition vocabulary
`VERIFIED` (finding reproduced), `DISPROVED` (evidence shows not present), `ALREADY FIXED` (change + test evidence), `FIXED`, `BLOCKED`, `NOT_TESTED`. Final dispositions will be recorded in `remediation-tracker.md` and `final-validation-report.md`.
