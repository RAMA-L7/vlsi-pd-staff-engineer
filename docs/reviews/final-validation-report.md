# Final Validation Report — Second Review Remediation

**Post-publication update (2026-11-18T17:02:17Z / 2026-11-18T22:32:17+05:30 IST):** the worktree content audited/described below was committed as `45e4251370c39ed27e2ccd6a7a804ed90eaf4472` (tree `00b0418ff4fde3cda377f0175bb92c5b1b32e1ca`) and pushed fast-forward to `origin/main` (verified). SHA values, path counts and "publication not authorized / no commit-push" statements below reflect the state **at the time of that pass** and are retained for traceability; they are not the current publication status. See `docs/reviews/PUBLICATION_REPORT.md`.


**Status:** COMPLETE (local, uncommitted) — independent adversarial audit completed: READY for independent review.
**Reviewed commit:** `0a9eb57e38b86acc51b8bcb6dea53bd97ab90e9b`
**Reviewed tree:** `ab0bcf96d00e99a1b1a6cd46f41fde159661791b`
**Repository:** `D:\audit\m1g_mapping_20261117`, branch `main`, `origin/main` = same commit
**Starting working tree:** clean; only review docs added initially.
**Final working tree at second-review finalization:** 42 local paths (33 modified + 9 new); at independent-audit finalization 43 paths; publication adds `docs/research/`. These counts are historical snapshots; use the publication manifest for the exact committed set.
**Original preserved repo:** `D:\audit\repo` at `41728cd…` with 34 pre-existing changes, untouched.
**Evidence directory:** `D:\audit\second_review_evidence_20261118\`

## 1. Exact reviewed identity
- HEAD `0a9eb57e38b86acc51b8bcb6dea53bd97ab90e9b`, tree `ab0bcf96d00e99a1b1a6cd46f41fde159661791b`.
- No remote change occurred; no publication authorized or performed.

## 2. Finding-by-finding dispositions

| ID | Severity | Disposition | Key change / evidence |
|---|---|---|---|
| SR-01 ECO verification/rollback | S1 | **VERIFIED → FIXED** | `assistant/eco.py`: change-class policy, mandatory fresh verification, transactional rollback with verified restoration, persistent history, safe paths, full-workspace snapshot, metric finiteness checks. Required regression: WNS gain + LEC FAIL ⇒ no PASS, restored files, zero downstream calls. 41 ECO tests. |
| SR-02 Stage acceptance before downstream | S1 | **VERIFIED → FIXED** | `regression/run_regression.ps1` + `acceptance.py`: explicit deps/required checks, fresh per-stage acceptance inside loop, persistence failures block PASS, resume revalidation, invalidation closure, ValidateOnly fresh, 3-field verdict. 12 simulated stage-gating tests; PowerShell AST parse OK. |
| SR-03 Gate API | S1 | **VERIFIED → FIXED** | `assistant/controller.py`: registered specs, one validation boundary, no empty gates, tool/internal check kinds, recursive upstream + cycle detection, persisted inputs, waiver policy/expiry/revision, non-waivable functional proof, waived disposition, fact-aware N/A, cancel semantics. 61 gate tests. |
| SR-04 Evidence dependencies | S1 | **VERIFIED → FIXED** | `assistant/evidence.py`: DependencySet with paths+hashes, 10 dependency categories, rehash at evaluation, run/design/stage/tool/view binding, stale-rebind refusal, output isolation. 28 evidence tests. |
| SR-05 View contracts | S1 | **VERIFIED → FIXED** | Explicit `ViewContract`/`contract_compatible` replaces total order; physical DB cannot satisfy extracted timing; GR estimate cannot satisfy routed extraction; corner/library/extraction provenance checked; post-fill proof required. |
| SR-06 CLI exit codes | S1 | **VERIFIED → FIXED** | `assistant/cli.py`: documented 0/1/2/3/4/124/125 contract; STA/PVT/gate-evaluate return nonzero on failure/blocked; structured JSON preserved; central boundary wired into gate-record/gate-evaluate. 28 CLI tests incl. real subprocess exit codes. |
| SR-07 Physical correctness | S1/S2 | **VERIFIED → FIXED** | SDC clk self-delay removed (10 ns/2 ns unchanged); LVS comparator exits nonzero on FAIL/INCONCLUSIVE; `15_lvs.sh` pipefail; real `check_setup` probe in correct Tcl context; unconstrained parser/gate; deck identity from live files via env; design fallback → INCONCLUSIVE; LVS hard-coded top already fixed at baseline. 31 targeted tests; SOC SS failure preserved honestly. |
| SR-08 Reproducibility/portability | S2 | **VERIFIED → FIXED** | `tools.py` env/config overrides + extraction/deck pins; `pins.json` SDC/extraction/deck/upstream collateral pins; `baseline.py` isolated output dirs + design-aware hashes; preflight no-ops removed and CLI `preflight` added; README/SETUP/REGRESSION updated with platform/minimums/one command/matrix. 37 baseline/foundation tests. |
| SR-09 Adversarial tests | S2 | **VERIFIED → FIXED** | New tests: ECO verification exception/timeout/NaN/path escape/unlisted file, gate empty/cycle/waiver/N/A, evidence stale/cross-design/cross-corner/view, stage dependency bypass, CLI exit codes. 304 tests passed at second-review finalization; current integrated suite is 324 passed. |
| SR-10 Documentation cleanup | S3 | **VERIFIED → FIXED** | README/DESIGN/VALIDATION/SETUP/REGRESSION reconciled: single matrix tied to commit/run IDs, dated history, soc_ctrl tracked+failure preserved, FF setup +5.7003 vs TT hold +0.6159 clarified, script vs agent distinction, NO-MACROS, PPA conditions. |

Historical A–N and post-publication findings remain fixed as previously documented; their trackers are unchanged except for the new section.

## 3. Key changes and file references
- ECO: `assistant/eco.py`, `tests/test_eco.py`, `tests/test_eco_verification.py`.
- Gate: `assistant/controller.py`, `tests/test_controller_gates.py`, `tests/test_gate_api.py`.
- Evidence/views: `assistant/evidence.py`, `tests/test_evidence_hashing.py`, `tests/test_evidence_dependencies.py`.
- CLI: `assistant/cli.py`, `tests/test_cli_exit_codes.py`, `tests/test_cli_evidence.py`.
- Runner: `regression/run_regression.ps1`, `regression/checks/acceptance.py`, `tests/test_stage_gating.py`.
- Physical: `examples/soc_ctrl/rtl/soc_ctrl.sdc`, `regression/configs/soc_ctrl_10ns.json`, `regression/checks/lvs_compare.py`, `regression/stages/12_sta.sh`, `regression/stages/15_lvs.sh`, `assistant/diagnosis.py`, `tests/test_sta_parser.py`, `tests/test_lvs_compare.py`, `tests/test_eqy_override.py`.
- Repro/docs: `assistant/tools.py`, `assistant/pins.json`, `assistant/baseline.py`, `regression/checks/stage00_preflight.py`, `README.md`, `docs/SETUP.md`, `docs/REGRESSION.md`, `regression/README.md`, `docs/DESIGN.md`, `docs/VALIDATION.md`, `tests/test_baseline.py`, `tests/test_foundation.py`, `tests/test_failure_injection.py`, `tests/test_m1d_integration.py`.

## 4. Exact commands and observed results

```
cd D:\audit\m1g_mapping_20261117
D:\tools\venv\Scripts\python.exe -m pytest tests\ -q -rs
  -> 304 passed in 32.75s (independent final rerun)                      [audit/FOLLOWUP_pytest_rerun.txt]

wsl -d Ubuntu-24.04 -- bash -lc "cd /mnt/d/audit/m1g_mapping_20261117 && bash examples/test-counter/test_framework.sh"
  -> PASS: 47 / FAIL: 0 / SKIP: 0              [FINAL_framework.txt]

$env:ASSISTANT_ORFS_DIR='D:\tools\OpenROAD-flow-scripts'
$env:REG_PLATFORM_SKY130HD='D:\tools\OpenROAD-flow-scripts\flow\platforms\sky130hd'
$env:REG_PLATFORM_SKY130HS='D:\tools\OpenROAD-flow-scripts\flow\platforms\sky130hs'
D:\tools\venv\Scripts\python.exe -m assistant baseline --run-id final_r3 --timeout 9000 --out D:\audit\second_review_evidence_20261118\baseline_r3
  -> status PASS, return_code 0, 16/16 stages [FINAL_baseline_r3_cli.txt]
D:\tools\venv\Scripts\python.exe -m assistant baseline --run-id final_r4 --timeout 9000 --out D:\audit\second_review_evidence_20261118\baseline_r4
  -> status PASS, return_code 0, 16/16 stages [FINAL_baseline_r4_cli.txt]
```

Targeted test evidence: `eco/`, `gate/`, `evidence/`, `cli/`, `runner/`, `physical_fix/`, `repro_fix/`, `tests_fix/`, `docs_fix/` subdirectories under `D:\audit\second_review_evidence_20261118\`.

## 5. Test classification
- **Passed:** 304 unit/integration tests; 47 framework checks; 2 real baselines; targeted EDA probes; PowerShell AST parse; preflight real+simulated probes.
- **Failed:** 0 at final verification. (Intermediate failures during development are preserved in evidence subdirectories.)
- **Skipped:** 1 in `physical_fix` targeted run (WSL-specific path probe under native Windows); no required check skipped.
- **Blocked:** foundry signoff DRC/LVS (no licensed tool/deck); full macro example (NO-MACROS); live multi-stage soc_ctrl closure (external authorization required); real two-run baseline initially blocked by vanished workstation ORFS checkout until validated overrides supplied.
- **Simulated:** ECO/gate/evidence/stage-gating failure-injection providers explicitly labelled SIMULATED; no credentials or live inference.

## 6. Evidence manifest and reproduction
Manifest: `D:\audit\second_review_evidence_20261118\` with per-workstream reports and logs. Reproduce the baseline with the documented command and the three platform env overrides above (the default ORFS path on the original developer host no longer exists; `assistant/tools.py` reports this via `preflight`).

## 7. Baseline-versus-candidate comparison
`FINAL_baseline_comparison.json`: r3 vs r4 stage maps identical, overall PASS/PASS. Generic netlists identical (`3148c5c3…`); mapped netlists identical (`7120e545…`); generated SDC/SPEF/GDS raw hashes differ only by timestamps — normalized content matches; `sta_summary.txt` byte-identical (TT setup +5.4892, SS +2.8824, FF +5.7174, 0 violators).

## 8. Remaining limitations / unsupported capabilities
IR drop, EM, antenna, UPF verification, post-fill extraction/closure, qualified signoff and tapeout readiness remain unsupported. soc_ctrl SS timing remains a preserving FAIL from prior milestones (−1.8325 ns routed best); Milestone 1G documented the mapping limitation. Macro-containing example is NOT_APPLICABLE (NO-MACROS). Foundry signoff DRC is BLOCKED; academic/public KLayout results are not signoff. No full suite was run after every minor edit; final suite is authoritative.

## 9. Unresolved issues with severity
- S2: full foundry signoff policy/tooling absent (external decision).
- S2: soc_ctrl timing closure requires commercial mapping/enriched library/architecture/constraint authorization; failing evidence preserved.
- S3: an optional CLI shell-exit helper remains local in `cli.py` (submission/evaluation use the central boundary).
- S3: ORFS platform must be supplied via validated overrides on this host; default workstation path is absent.

## 10. Independent review checklist
- [x] Re-run `pytest tests\ -q` → 304 passed (final audit rerun).
- [ ] Reproduce ECO WNS-gain + LEC-FAIL rejection and verify zero downstream calls.
- [ ] Attempt zero-exit invalid stage output and confirm consumer blocked.
- [ ] Modify/delete a report/artifact and confirm gate blocks after restart.
- [ ] Exercise CLI failure exit codes as real subprocesses.
- [ ] Cross-design/cross-corner/physical-DB-as-extracted reuse attempts all block.
- [ ] Verify waivers with expired scope/policy cannot PASS.
- [ ] Confirm no production RTL/SDC/PDK/signoff config changed beyond the approved SDC clk-delay fix and config pin.
- [ ] Confirm documentation claims match demonstrated runs and preserved failures.

## 11. Independent adversarial audit (final)

Auditor verdict: **READY for independent review**. U-A (CLI N/A bypass), U-B (direct API strictness opt-in) and U-C/D/E (S3 residuals) are closed and re-derived from primary artifacts. No remaining S1/S2 false-PASS path found. Evidence: `D:\audit\second_review_evidence_20261118\audit\FINAL_AUDIT.md` and `FINAL_AUDIT_FOLLOWUP.md`.
