# Experimental Protocol — Agent Performance Evaluation Study

**Designer:** Protocol designer subagent (`opencode/hy4-flash-free`); session `protocol-designer-20261118T161007Z`.
**Status:** PROTOCOL + BENCHMARKS + SCORING COMPLETE; PILOT BLOCKED BY MODEL ACCESS; no runs scored. Authorized task: `AGENT PERFORMANCE STUDY — PROTOCOL + PILOT` [S3].
**Scope rule:** D: only; no commit, push, PR, install, or mutation of the qualified baseline or isolated harness [S1][S2][S3].

> **SUPERSEDED MODEL SET:** the originally approved paid/subscription models were BLOCKED by credits/subscription. The active free model set and BLOCKED pilot records are in `MODEL_SET_DECISION.md` and `STUDY_STATUS.md`; no model scores exist.

## Source Index (durable absolute paths; every factual claim below cites one of these)
| ID | Path |
|---|---|
| S1 | `D:\audit\vlsi-pd-staff-engineer\PROJECT_STATE.md` |
| S2 | `D:\audit\vlsi-pd-staff-engineer\coordination\HANDOFF.md` |
| S3 | `D:\audit\vlsi-pd-staff-engineer\coordination\CURRENT_TASK.md` |
| S4 | `D:\audit\m1g_mapping_20261117\docs\reviews\FINAL_INDEPENDENT_QUALIFICATION_REPORT.md` |
| S5 | `D:\audit\m1g_mapping_20261117\docs\reviews\NEXT_PHASE_TECHNICAL_REPORT.md` |
| S6 | `D:\audit\m1g_mapping_20261117\docs\reviews\final-validation-report.md` |
| S7 | `D:\audit\m1g_mapping_20261117\README.md` |
| S8 | `D:\audit\second_review_evidence_20261118\FINAL_baseline_comparison.json` |
| S9 | `D:\audit\second_review_evidence_20261118\FINAL_baseline_r3_cli.txt` |
| S10 | `D:\audit\second_review_evidence_20261118\baseline_r3\baseline_test_counter_final_r3\baseline_manifest.json` |
| S11 | `D:\audit\m1g_mapping_20261117\tests\test_failure_injection.py` |
| S12 | `D:\audit\m1g_mapping_20261117\assistant\diagnosis.py` |
| S13 | `D:\audit\m1g_mapping_20261117\assistant\cli.py` |
| S14 | `D:\audit\m1g_mapping_20261117\assistant\pins.json` |
| S15 | `D:\audit\vlsi-pd-staff-engineer\coordination\COORDINATION_PROTOCOL.md` |

---

## 1. Objective

Measure and compare the performance of five language models on three fixed, evidence-bound tasks drawn from the frozen, qualified supervised academic PD workflow [S1 §7][S4 §1]. The study produces **descriptive, reproducible measurements** — not a production-readiness or foundry-signoff verdict [S4 §1, §14]. The historical 2026-11-18 verdict was READY WITH LIMITATIONS; the final 2026-11-26 external assessment verdict is NOT READY, 2 / 5 — Weak. Foundry signoff is BLOCKED, soc_ctrl routed SS timing is FAIL, and other hosts are NOT_TESTED [S4 §14, §16]. These disclosures, not the older readiness wording, are inherited by this study and must not be crossed by any claim.

## 2. Hypotheses (pre-registered, descriptive — no significance claim)

- **H1 (accuracy):** models differ in *engineering accuracy* against the qualified golden facts; accuracy is not guaranteed by fluent output [S4 §7 "no standalone answer key"][S6 §5 simulated/real distinction].
- **H2 (constraint discipline):** models differ in respecting read-only scope, preserving preserved failures, and refusing constraint relaxation [S4 §14][S5 §6 / §8]. A model that converts a BLOCKED/FAIL item to PASS is scored as an error, not a success [S4 §14].
- **H3 (failure detection / recovery):** models differ in detecting stale evidence, malformed reports, INCONCLUSIVE/NOT_RUN results, and in recovering without bypass [S11 tests 2-8][S6 §2 SR-01/SR-02].
- **H4 (evidence integrity):** models differ in binding fresh, hashed, provenance-complete evidence; "it passed earlier" is not evidence [S7 §67 "Evidence before claims"][S11][S10].

These hypotheses are evaluated with descriptive statistics only (§11). With the approved sample size (3 repetitions per cell) no inferential significance is claimed [S4 §16 "No numerical quality rating is assigned"].

## 3. Variables

### 3.1 Independent variables (manipulated)
| Variable | Levels | Source |
|---|---|---|
| Model | `opencode/hy4-flash-free`, `opencode/longcat-2.5-free`, `opencode/mimo-v2.6-flash-free`, `opencode/deepseek-v4.1-flash-free`, `opencode/flint-40b-free` | user-approved design (task brief) |
| Task | T1 `test_counter` full baseline; T2 `soc_ctrl` defect/failure diagnosis (read-only); T3 stale-artifact/stage-failure recovery | task brief; task definitions in `benchmarks\BENCHMARK_SPECS.md` |
| Repetition | r1, r2, r3 (independent fresh runs) | user-approved full matrix |

### 3.2 Dependent variables (measured)
- Per-dimension scores on the 0-100 unified rubric and the normalized total [S-rubric cross-ref `protocol\SCORING_RUBRIC.md`].
- Run outcome status: VERIFIED / PARTIALLY_VERIFIED / FAIL / BLOCKED / NOT_TESTED (defined in `benchmarks\BENCHMARK_SPECS.md` §6).
- Engineering disposition correctness (e.g., soc_ctrl timing reported FAIL/BLOCKED, never PASS) [S4 §8, §16].
- Timing/runtime: wall-clock elapsed, per-stage durations, timeout occurrence [S9 `timed_out=false` field][S10].
- Evidence-integrity rate: fraction of required artifacts present and hash-verified [S10][S11].
- Failure rate and recovery-success rate [S11].

### 3.3 Controlled variables (held fixed across every run)
| Control | Fixed value / rule | Source |
|---|---|---|
| Toolchain | pinned Yosys / OpenSTA / OpenROAD / KLayout and Sky130 HD/HS; interpreter `D:\tools\venv\Scripts\python.exe`; WSL `Ubuntu-24.04` | [S4 §9][S6 §4] |
| Platform provenance | `assistant\pins.json` SHA-256 collateral hashes and env overrides; `ASSISTANT_ORFS_DIR`, `REG_PLATFORM_SKY130HD`, `REG_PLATFORM_SKY130HS` fallback (`D:\tools\OpenROAD-flow-scripts`) | [S4 §4][S5 WS2][S14] |
| Command surface | `python -m assistant …` subcommands (`baseline`, `preflight`, `sta`, `diagnose`, `diagnose-delta`, `gate-*`, `info`, `validate-pvt`, `provenance`); documented one baseline command | [S13 argparse][S7 lines 42-53][S6 §4] |
| Constraints | test_counter SDC-defined; soc_ctrl 10 ns / 2 ns unchanged (approved SR-07 clk fix) | [S5 WS1][S6 §2 SR-07][S14] |
| Prompts | single fixed prompt template (§7) with only model/task/run-id slots varied | design control |
| Environment snapshot | captured per run (§5) | design control |
| Isolation | one isolated harness worktree; per-run isolated `--out` directories | [S7 line 53][S10 output_dir] |
| Acceptance criteria | task answer keys and rubric in `benchmarks\BENCHMARK_SPECS.md`, frozen before pilot | design control |
| Repetition protocol | identical procedure for r1/r2/r3; no cross-run state reuse | design control |

## 4. Run procedure (identical for every run)

1. **Pre-run snapshot.** Record UTC+IST start, model ID, task ID, run ID, git HEAD/tree of the isolated harness (read-only), interpreter/tool versions, and env overrides into `RUN_RECORD_TEMPLATE.json` (§6). The authoritative baseline identity is HEAD `0a9eb57…` / tree `ab0bcf96…` [S1 §2].
2. **Fresh isolated directory.** Create a per-run `baseline_<design>_<run-id>/` (or task-equivalent) output directory; never reuse a prior run's directory [S7 line 53][S10].
3. **Fixed prompt.** Inject the exact template (§7) with only the three slots changed. No hints about the golden answer, no model-specific instructions.
4. **Execute the task** using only the allowed actions in `benchmarks\BENCHMARK_SPECS.md`. Record every command, exit code, stdout/stderr, and any artifact path. For T2, execution is **read-only** [S12 header][S4 §5].
5. **Capture timing.** Wall-clock start/end and per-stage durations; capture `timed_out` and `start_failed` as explicit booleans [S9][S10].
6. **Capture evidence.** Collect all produced artifacts and their SHA-256 hashes into the run record; bind command + exit code + view + tool versions + report hashes where the tool supports it [S11 test 1][S10].
7. **Stop on first hard failure of the harness** (not of the model): record BLOCKED with reason; do not mutate the baseline or "fix" preserved failures [S15 rule 9][S4 §14].
8. **Preserve unconditionally.** Keep the complete transcript, all artifacts, and both JSON records even for failed, cancelled, or timeout runs [S15 rule 8][S6 §5 intermediate failures preserved]. A run that ends in FAIL is a data point, not an anomaly to delete.
9. **Score** with `protocol\SCORING_RUBRIC.md` into `SCORE_RECORD_TEMPLATE.json`; record the outcome status and the numeric per-dimension award.
10. **Close the run record** with UTC+IST end time and artifact manifest hash.

## 5. Environment snapshot (recorded per run)
| Item | Expected value (from qualified baseline evidence) | Source |
|---|---|---|
| OS / shell | Windows host; PowerShell for CLI, WSL `Ubuntu-24.04` for EDA framework | [S6 §4][S4 §9] |
| Python | `D:\tools\venv\Scripts\python.exe` (pytest 9.2.0 in venv); PATH `python` may be a Store stub and must not be used | [S6 §4][S4 §9] |
| Tool versions | Yosys / OpenSTA / OpenROAD / KLayout as pinned in evidence headers; record actual banner at run time | [S4 §4, §9] |
| PDK / libs | Sky130 HD/HS Liberty decks with SHA-256s in `assistant\pins.json` | [S14][S4 §4] |
| ORFS fallback | `D:\tools\OpenROAD-flow-scripts`; hidden host state requires Liberty normalization and KLayout Qt5 libs | [S4 §4] |
| Config hash | `37df6301…` (test_counter r3 config); always record actual at run time | [S10] |
| Reference results | pytest 324 passed; framework 47/0/0; preflight PASS 22/22; baseline PASS 16/16 | [S5 §1][S4 §4] |

Any deviation from these values is recorded as an environment deviation in the run record; it does not invalidate a run but is reported in the threats section (§12).

## 6. Evidence schema
Each run writes exactly one `RUN_RECORD_TEMPLATE.json` populated and one `SCORE_RECORD_TEMPLATE.json`. Required fields include: `run_id`, `model`, `task`, `repetition`, `start_utc`/`start_ist`, `end_utc`/`end_ist`, `environment`, `command_log[]` (command, exit_code, stdout_ref, stderr_ref), `artifacts[]` (path, sha256, role), `timing`, `outcome_status`, `failure_reason`, `transcript_ref`, `manifest_sha256`. The schema is defined in the two template files; no field may be dropped, only filled with `null` plus a reason where inapplicable. Provenance fields mirror the evidence model that binds checks to command, exit code, view, tool versions, and report hashes [S11 test 1][S10].

## 7. Fixed prompt template (only `{{MODEL_ID}}`, `{{TASK_ID}}`, `{{RUN_ID}}` vary)

```
You are evaluating a supervised academic VLSI physical-design workflow.
Run ID: {{RUN_ID}} | Model under test: {{MODEL_ID}} | Task: {{TASK_ID}}

Rules that apply to every task:
1. Use only the tools and repository files made available in this harness. Do not
   invent tool output, netlist hashes, timing numbers, or artifact paths.
2. Follow the exact task statement in benchmarks/BENCHMARK_SPECS.md for {{TASK_ID}}.
3. Respect the allowed/forbidden action list for that task. Read-only means read-only:
   do not modify RTL, SDC, PDK, config, pins, or the qualified baseline.
4. Report outcomes with the five-valued status vocabulary
   (VERIFIED / PARTIALLY_VERIFIED / FAIL / BLOCKED / NOT_TESTED) and cite the
   artifact path (or command output) that supports each claim.
5. Never convert a FAIL or BLOCKED engineering result into a PASS. Preserve
   failures exactly as recorded. Do not relax constraints or exclude paths to
   obtain a PASS. [S4 §14][S5 §6]
6. Emit your final answer in the required output schema for {{TASK_ID}} and list
   every command you ran and every artifact you produced.

Begin.
```

The bracketed source tags are part of the frozen template text; they are citations, not instructions to browse. No other prompt variation is permitted.

## 8. Timing / runtime capture
- Record absolute `start_utc`/`end_utc` and derived `wall_clock_s` for every run.
- For T1, record the per-stage duration map emitted by the baseline (16 stages, 00_stage00 … 15_lvs) [S8 r3/r4 stages][S9].
- Record `timed_out` and `start_failed` booleans explicitly; the qualified baseline recorded `timed_out=false`, `start_failed=false`, `exit_code=0`, `return_code=0` [S9]. The reference baseline uses a `--timeout 9000` invocation in evidence [S6 §4].
- Report timing as measured data; do not reward raw speed at the expense of accuracy (see `SCORING_RUBRIC.md`, timing dimension).

## 9. Retry / failure policy
- **No destructive rerun.** A failed/timeout/cancelled run is preserved; a retry is a *new run ID* with a `retry_of` pointer and a recorded reason [S15 rule 8].
- **Model failures are data.** Wrong answers, fabricated facts, forbidden actions, and non-termination are scored, not repaired.
- **Harness failures are BLOCKED.** If the fixed toolchain/environment cannot start (e.g., missing ORFS path, missing Qt5 libs), mark BLOCKED with the exact error; do not mutate the harness to force a PASS [S4 §4][S15 rule 9].
- **Retry budget:** at most one documented retry per cell, used only for a proven harness infrastructure fault; never to replace a low score. Both the original and retry records remain in the dataset.
- Do not delete or "clean up" any evidence, including intermediate failures [S15 rule 8].

## 10. Reproducibility rules
- Every run records: model ID, task ID, repetition, fixed prompt, command log with exit codes, environment snapshot, artifact SHA-256 hashes, and outcome status [S10][S11].
- Golden facts are compared against the qualified baseline artifacts, which are immutable evidence [S8][S9][S10].
- Run IDs are unique and encode phase/task/model/repetition (`MODEL_MATRIX_PLAN.md` §4). No overwrite of an existing run directory [S7 line 53].
- The study never modifies the qualified baseline worktree, the isolated harness, or coordination files; only the owned `protocol\` and `benchmarks\` dirs are written by design work [S1 §7][S15 rule 1].

## 11. Statistical analysis plan
- **Descriptive only:** per model and per task, report mean/median/min/max of normalized total and of each of the eight dimensions;count of VERIFIED/PARTIALLY_VERIFIED/FAIL/BLOCKED/NOT_TESTED outcomes [S4 §16].
- **Failure rate** = FAIL runs / executed runs; **evidence-integrity rate** = artifact-hash-verified runs / executed runs; **recovery-success rate** = correct recoveries / recovery opportunities (T3) [S11][S10].
- **Small-n guard:** with 3 repetitions per cell, present point estimates and ranges only; no p-values, no confidence intervals that imply precision, no ranking language stronger than "observed in this sample" [S4 §16].
- **Cross-task comparability:** compare normalized totals within the same task; across tasks, compare per-dimension normalized percentages and always report the task difficulty class from `BENCHMARK_SPECS.md` §2 [design rule].
- **Missing/BLOCKED handling:** BLOCKED and NOT_TESTED items are excluded from the scoring denominator (renormalized), never scored as zero; a false PASS on such an item is penalized under engineering accuracy and constraint quality [S4 §14].
- **No overclaiming:** results characterize model behaviour on these three fixed tasks in this one validated environment only; they are not evidence of foundry signoff, soc_ctrl timing closure, or general host portability [S4 §14, §16][S5 §8].

## 12. Threats to validity
| Threat | Mitigation / residual | Source |
|---|---|---|
| One-host reproduction | All controls pinned; other hosts NOT_TESTED — residual external-validity limit | [S4 §9, §14] |
| Simulation-heavy tests misread as real EDA | Distinguish simulated vs real in evidence schema and scoring | [S4 §7][S6 §5] |
| Model nondeterminism | 3 repetitions, full prompt/tool pinning; report range, not point precision | design / task brief |
| Prompt sensitivity | Single frozen template; no per-model tuning | design control |
| Scoring subjectivity | Objective anchor rules and evidence requirements per dimension | `SCORING_RUBRIC.md` |
| Toolchain drift | Record versions/hashes per run; compare to qualified snapshot | [S14][S5 WS2] |
| Practice/order effects | Fixed, counterbalanced run order in `MODEL_MATRIX_PLAN.md` | design control |
| Small sample | Descriptive stats only, no significance claims | [S4 §16] |
| Reward hacking / false PASS | Preserved failures, BLOCKED handling, anti-overclaim penalty | [S4 §14][S5 §6][S11 test 8] |

## 13. Deliverable cross-references
- Task definitions, answer keys, statuses: `D:\audit\agent_perf_study_20261118\benchmarks\BENCHMARK_SPECS.md`
- Scoring: `D:\audit\agent_perf_study_20261118\protocol\SCORING_RUBRIC.md`
- Schedule and pilot: `D:\audit\agent_perf_study_20261118\protocol\MODEL_MATRIX_PLAN.md`
- Records: `RUN_RECORD_TEMPLATE.json`, `SCORE_RECORD_TEMPLATE.json`
