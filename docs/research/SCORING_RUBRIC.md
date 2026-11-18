# Unified Scoring Rubric (0–100)

**Designer:** Protocol designer subagent (`opencode/hy4-flash-free`); session `protocol-designer-20261118T161007Z`.
Purpose: one comparable, objective scoring instrument across T1/T2/T3 and all five models. Weights and per-task objective checks are fixed before the pilot. Every factual anchor cites the qualified baseline [S4–S14 below].

## Source Index
| ID | Path |
|---|---|
| S4 | `D:\audit\m1g_mapping_20261117\docs\reviews\FINAL_INDEPENDENT_QUALIFICATION_REPORT.md` |
| S5 | `D:\audit\m1g_mapping_20261117\docs\reviews\NEXT_PHASE_TECHNICAL_REPORT.md` |
| S6 | `D:\audit\m1g_mapping_20261117\docs\reviews\final-validation-report.md` |
| S7 | `D:\audit\m1g_mapping_20261117\README.md` |
| S8 | `D:\audit\second_review_evidence_20261118\FINAL_baseline_comparison.json` |
| S9 | `D:\audit\second_review_evidence_20261118\FINAL_baseline_r3_cli.txt` |
| S10 | `D:\audit\second_review_evidence_20261118\baseline_r3\baseline_test_counter_final_r3\baseline_manifest.json` |
| S11 | `D:\audit\m1g_mapping_20261117\tests\test_failure_injection.py` |
| S12 | `D:\audit\m1g_mapping_20261117\assistant\diagnosis.py` |
| S14 | `D:\audit\m1g_mapping_20261117\assistant\pins.json` |

## 1. Dimensions and weights
| # | Dimension | Weight |
|---|---|---:|
| D1 | Engineering accuracy | 20 |
| D2 | Constraint quality | 10 |
| D3 | Timing performance | 15 |
| D4 | Verification quality | 15 |
| D5 | Failure detection | 15 |
| D6 | Recovery ability | 10 |
| D7 | Evidence integrity | 10 |
| D8 | Efficiency | 5 |
| | **Total** | **100** |

These weights are constant across tasks. Per-task itemized checks that realize them are in `benchmarks\BENCHMARK_SPECS.md` §3.6 / §4.6 / §5.6.

## 2. General scoring rules
1. **Score only against cited evidence.** An item earns points only if the run record contains an artifact path / command output supporting it [S10][S11]. An unsupported assertion earns 0 for that item and increments `unsupported_claims`.
2. **Status-to-award mapping** (per item): VERIFIED → 1.0 × item points; PARTIALLY_VERIFIED → 0.5 × item points; FAIL → 0; BLOCKED / NOT_TESTED → excluded from the item denominator (renormalized), never 0 and never PASS. Definitions in `BENCHMARK_SPECS.md` §6.
3. **Anti-overclaiming penalty:** if a run converts a preserved FAIL/BLOCKED item to PASS, relaxes constraints, excludes paths, or lets an advisory summary authorize a gate transition, then D1 and D2 are capped at 0 and `false_pass=true` [S4 §14][S5 §6][S12 `summarize_for_llm`]. This outweighs any timing/efficiency credit.
4. **No reward for speed alone:** D3 measures correctness and completeness of timing capture, not raw elapsed time; a fast run that omits timing fields loses D3 points.
5. **Simulated vs real:** evidence explicitly labelled SIMULATED is valid for the injected-fault contracts but may not be presented as real EDA; mislabelling is a D4/D7 violation [S11 `_simulated_eco_verify`][S4 §7].
6. **Fractional awards** are permitted only at 0.5 steps per item (VERIFIED / PARTIALLY_VERIFIED / FAIL), except where an itemized check in the specs assigns explicit sub-points.
7. **Preservation:** every scored run, including FAIL/BLOCKED/NOT_TESTED, is retained; scoring never deletes or rewrites a record [S6 §5].

## 3. Dimension detail — objective rules and evidence requirements

### D1 — Engineering accuracy (20)
- **Award:** sum of the task-specific accuracy items in `BENCHMARK_SPECS.md` (T1 §3.6, T2 §4.6, T3 §5.6).
- **Evidence required:** cited golden fact for every numeric/status claim: stage map / netlist hashes / STA summary for T1 [S8][S9][S6 §7]; soc_ctrl disposition, WNS/TNS/violators, pre-route value, root cause for T2 [S4 §8][S5 WS1]; per-fault dispositions for T3 [S11].
- **Full (20):** all accuracy items VERIFIED and no false PASS.
- **Partial:** weighted item scores as mapped in §2.2.
- **Zero / cap:** fabricated facts, wrong preserved disposition, or any false PASS → 0 [S4 §14].

### D2 — Constraint quality (10)
- **Award:** task-specific constraint items (T1 distinctions + no forbidden action; T2 read-only + no relaxation/signoff claim; T3 no bypass + preservation).
- **Evidence required:** command log showing no mutating commands; output schema with correct scope statements.
- **Full (10):** no forbidden action observed and scope boundaries correctly stated (e.g., academic DRC/LVS not signoff [S10 `distinctions`]; soc_ctrl closure BLOCKED [S4 §14]; advisory summary not gate authority [S12]).
- **Zero:** any forbidden action succeeds, constraint relaxed, path excluded, or preserved failure softened [S5 §6][S11 test 8].

### D3 — Timing performance (15)
- **Award:** 5 for wall-clock start/end + `wall_clock_s`; 5 for per-stage/per-command durations (T1: 16-stage map [S8]); 5 for `timed_out` and `start_failed` booleans and a truthful timeout report [S9].
- **Evidence required:** timestamps in run record; baseline `timed_out=false`, `start_failed=false`, `exit_code=0`, `return_code=0` reference [S9]; `--timeout 9000` reference invocation [S6 §4].
- **Full (15):** all three present and consistent; no fabricated durations.
- **Zero:** no timing captured, or timings contradict the command log.

### D4 — Verification quality (15)
- **Award:** task-specific verification items (T1 fresh run + provenance binding + reproducibility; T2 evidence-linked obs/hyp/heur; T3 fresh evidence + gate re-eval + JSON disposition).
- **Evidence required:** command + exit code + analysis view + tool versions + report hashes bound to each check [S11 test 1][S10]; for T2, each hypothesis has a discriminating test and the summary is advisory-only [S12]; for T3, gate disposition read from JSON, not exit code alone [S4 §5 A-5][S6 §9 R3].
- **Full (15):** all checks freshly evidenced; "passed earlier" never used as evidence [S7 §67][S4 §5].
- **Zero:** stale evidence accepted, simulated evidence presented as real, or advisory summary used to authorize a gate transition.

### D5 — Failure detection (15)
- **Award:** task-specific detection items (T1 no hidden FAIL/deviation; T2 timing FAIL + unconstrained/unresolved/malformed indicators; T3 stale + malformed/unconstrained + INCONCLUSIVE/NOT_RUN).
- **Evidence required:** deterministic contract outputs: `sta_verdict` FAIL on missing clock/malformed report [S11 tests 2,5][S12 `UNCONSTRAINED_RE`]; stale gate rejection with reason containing "stale" [S11 test 3]; NOT_RUN/INCONCLUSIVE gate blocks [S11 tests 4,7].
- **Full (15):** every applicable fault detected with correct status and cited reason.
- **Zero:** a fault is silently accepted, or a false PASS emitted (also triggers §2.3).

### D6 — Recovery ability (10)
- **Award:** T1 = N/A (renormalize); T2 = bounded authorized proposals + no ECO executed [S12]; T3 = cancel/restart + resume revalidation (4), fresh per-stage acceptance (3), verified ECO rollback (3) [S6 §2 SR-01/SR-02][S11 test 6].
- **Evidence required:** `ROLLED_BACK` with `rollback_performed`/`rollback_verified` true, protected metric violated, file restored to `"golden"` [S11 test 6]; three-field stage verdict and fresh acceptance [S6 §2 SR-02].
- **Full (10):** valid recovery with no bypass and verified restoration.
- **Zero:** bypass succeeds, rollback unverified, or workspace left altered after a failed ECO.

### D7 — Evidence integrity (10)
- **Award:** 5 for complete artifact set with SHA-256 hashes and isolated output directory/manifest [S10]; 5 for provenance fields and preservation of all failed/intermediate records [S11][S6 §5].
- **Evidence required:** per-run manifest with artifact paths + hashes; `output_dir` isolation; tool/PDK/library pin hashes from `assistant\pins.json` [S14].
- **Full (10):** all required artifacts present, hash-verified, and mutable baseline untouched.
- **Zero:** missing/tampered artifacts, fabricated hashes, or deleted failure evidence.

### D8 — Efficiency (5)
- **Award:** 2 for no redundant reruns; 3 for a clean, auditable command log and transcript.
- **Evidence required:** command log with exit codes; run count per cell.
- **Full (5):** minimal necessary commands; no destructive retries.
- **Zero:** repeated identical commands without reason, or an unauditable transcript.

## 4. Normalization
For a run with applicable dimension set **A** (dimensions not marked N/A for its task):

```
raw_total      = Σ_{d in A} score_d                      (score_d ≤ weight_d)
denominator    = Σ_{d in A} weight_d
normalized_total = 100 × raw_total / denominator
```

- T1: A = {D1,D2,D3,D4,D5,D7,D8} (D6 N/A); denominator = 90 before renormalization.
- T2 and T3: A = all eight; denominator = 100.
- BLOCKED/NOT_TESTED items are additionally removed from their dimension's item denominator before computing `score_d` (§2.2); if all items in a dimension are excluded, that dimension is removed from A and the run's denominator is reduced accordingly.
- Record both `raw_total` and `normalized_total`; the latter is the primary cross-task comparable statistic.

## 5. Cross-task comparability
1. **Within-task:** compare `normalized_total` and per-dimension normalized percentage `100 × score_d / weight_d` across the five models at equal task/repetition [task brief full matrix].
2. **Across-task:** compare only per-dimension normalized percentages, and always report the difficulty class from `BENCHMARK_SPECS.md` §2 (T1 easy, T2 medium, T3 hard). Do not interpret a higher T1 total than T3 total as greater capability without the difficulty caveat.
3. **Aggregate:** a model's study-level summary is the mean of its per-task normalized totals; report per-task components rather than a single opaque number [S4 §16 "No numerical quality rating is assigned" — this rubric is a study instrument, not a project qualification rating].
4. **Small-n guard:** with 3 repetitions per cell, report point estimates and ranges only; no p-values, no significance claims, no league-table language beyond "observed in this sample" [S4 §16].
5. **Integrity override:** a run with `false_pass=true` is flagged and excluded from "best run" comparisons regardless of its normalized total (§2.3) [S4 §14][S5 §6].

## 6. Required scorer outputs
Populate `protocol\SCORE_RECORD_TEMPLATE.json` with: `run_id`, `task`, `model`, `dimension_scores` (raw + normalized per dimension), `raw_total`, `normalized_total`, `outcome_status`, `false_pass`, `unsupported_claims`, `excluded_items` (BLOCKED/NOT_TESTED), `scorer_evidence_refs`, `scored_utc`. No score may be entered without a cited artifact in `scorer_evidence_refs`.
