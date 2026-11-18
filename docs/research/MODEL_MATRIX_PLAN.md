# Model Matrix Plan — 45-Run Schedule, Pilot, Naming, Artifacts

**Designer:** Protocol designer subagent (`opencode/hy4-flash-free`); session `protocol-designer-20261118T161007Z`.
**Approved design:** 5 models × 3 tasks × 3 repetitions = 45 full runs; pilot = 2 models × 1 task × 1 repetition (2 runs) [task brief; `AGENT PERFORMANCE STUDY — PROTOCOL + PILOT` in `D:\audit\vlsi-pd-staff-engineer\coordination\CURRENT_TASK.md`].
**Boundary:** design only; no runs executed by this designer; no commit/push/install; owned dirs `protocol\`, `benchmarks\` only [S1][S2].

> **SUPERSEDED MODEL SET:** the originally approved paid/subscription models were BLOCKED by credits/subscription. The active free model set and BLOCKED pilot records are in `MODEL_SET_DECISION.md` and `STUDY_STATUS.md`; no model scores exist.

## Source Index
| ID | Path |
|---|---|
| S1 | `D:\audit\vlsi-pd-staff-engineer\PROJECT_STATE.md` |
| S2 | `D:\audit\vlsi-pd-staff-engineer\coordination\HANDOFF.md` |
| S4 | `D:\audit\m1g_mapping_20261117\docs\reviews\FINAL_INDEPENDENT_QUALIFICATION_REPORT.md` |
| S6 | `D:\audit\m1g_mapping_20261117\docs\reviews\final-validation-report.md` |
| S7 | `D:\audit\m1g_mapping_20261117\README.md` |
| S8 | `D:\audit\second_review_evidence_20261118\FINAL_baseline_comparison.json` |
| S9 | `D:\audit\second_review_evidence_20261118\FINAL_baseline_r3_cli.txt` |
| S10 | `D:\audit\second_review_evidence_20261118\baseline_r3\baseline_test_counter_final_r3\baseline_manifest.json` |
| S11 | `D:\audit\m1g_mapping_20261117\tests\test_failure_injection.py` |
| S12 | `D:\audit\m1g_mapping_20261117\assistant\diagnosis.py` |
| S14 | `D:\audit\m1g_mapping_20261117\assistant\pins.json` |

---

## 1. Model keys (fixed)
| Model ID (exact) | Short key |
|---|---|
| `opencode/hy4-flash-free` | `hy4flash` |
| `opencode/longcat-2.5-free` | `longcat25` |
| `opencode/mimo-v2.6-flash-free` | `mimo26flash` |
| `opencode/deepseek-v4.1-flash-free` | `deepseek41` |
| `opencode/flint-40b-free` | `flint40b` |

Model IDs are used verbatim in run records; short keys only in run IDs.

## 2. Full 45-run schedule

### T1 — `test_counter` full baseline (15 runs)
| Run ID | Model | Rep |
|---|---|---|
| APES-FULL-T1-hy4flash-r1 / r2 / r3 | opencode/hy4-flash-free | 1,2,3 |
| APES-FULL-T1-longcat25-r1 / r2 / r3 | opencode/longcat-2.5-free | 1,2,3 |
| APES-FULL-T1-mimo26flash-r1 / r2 / r3 | opencode/mimo-v2.6-flash-free | 1,2,3 |
| APES-FULL-T1-deepseek41-r1 / r2 / r3 | opencode/deepseek-v4.1-flash-free | 1,2,3 |
| APES-FULL-T1-flint40b-r1 / r2 / r3 | opencode/flint-40b-free | 1,2,3 |

### T2 — `soc_ctrl` defect/failure diagnosis, read-only (15 runs)
| Run ID | Model | Rep |
|---|---|---|
| APES-FULL-T2-hy4flash-r1 / r2 / r3 | opencode/hy4-flash-free | 1,2,3 |
| APES-FULL-T2-longcat25-r1 / r2 / r3 | opencode/longcat-2.5-free | 1,2,3 |
| APES-FULL-T2-mimo26flash-r1 / r2 / r3 | opencode/mimo-v2.6-flash-free | 1,2,3 |
| APES-FULL-T2-deepseek41-r1 / r2 / r3 | opencode/deepseek-v4.1-flash-free | 1,2,3 |
| APES-FULL-T2-flint40b-r1 / r2 / r3 | opencode/flint-40b-free | 1,2,3 |

### T3 — stale-artifact / stage-failure recovery (15 runs)
| Run ID | Model | Rep |
|---|---|---|
| APES-FULL-T3-hy4flash-r1 / r2 / r3 | opencode/hy4-flash-free | 1,2,3 |
| APES-FULL-T3-longcat25-r1 / r2 / r3 | opencode/longcat-2.5-free | 1,2,3 |
| APES-FULL-T3-mimo26flash-r1 / r2 / r3 | opencode/mimo-v2.6-flash-free | 1,2,3 |
| APES-FULL-T3-deepseek41-r1 / r2 / r3 | opencode/deepseek-v4.1-flash-free | 1,2,3 |
| APES-FULL-T3-flint40b-r1 / r2 / r3 | opencode/flint-40b-free | 1,2,3 |

**Total = 45 runs.** Each cell is an independent fresh run with a unique run ID and output directory [S7 line 53][S10].

## 3. Execution order (order-effect control)
Execute in three rounds keyed by repetition:
- **Round 1 (r1):** models in listed order (hy4flash, longcat25, mimo26flash, deepseek41, flint40b); each model runs T1 → T2 → T3.
- **Round 2 (r2):** models in reverse order (flint40b, deepseek41, mimo26flash, longcat25, hy4flash); each model runs T3 → T2 → T1.
- **Round 3 (r3):** models rotated by one (longcat25, mimo26flash, deepseek41, flint40b, hy4flash); each model runs T2 → T3 → T1.

This counterbalances model and task order across the three repetitions; it does not change any run's fixed prompt, tools, or acceptance criteria. A retry uses a new `-retry<n>` suffix and inherits the original cell's ordering slot (see §4).

## 4. Naming convention
```
APES-<PHASE>-<TASK>-<MODELKEY>-r<REP>[-retry<N>]
```
- `PHASE` = `PILOT` or `FULL`.
- `TASK` = `T1`, `T2`, `T3`.
- `MODELKEY` = one of the five short keys in §1.
- `REP` = `1..3` for full; `1` for pilot.
- Optional `-retry<N>` for documented infrastructure retries (original record retained) — see `EXPERIMENTAL_PROTOCOL.md` §9.

Examples: `APES-PILOT-T1-longcat25-r1`, `APES-FULL-T3-flint40b-r2`, `APES-FULL-T1-hy4flash-r1-retry1`.
Run directory: `<out>/<run_id>/` with per-run isolated `baseline_<design>_<run_id>/` subdirs for T1 [S10 `output_dir`].

## 5. Pilot selection (recommendation — study lead decides)

**Pilot recommendation (active):** `T1`, 1 repetition, models `opencode/deepseek-v4.1-flash-free` and `opencode/longcat-2.5-free` (free set). The original paid/subscription pilot models are BLOCKED; preserved in MODEL_SET_DECISION.md.

**Rationale:**
1. **Task = T1.** It is the only task with a single documented command and a fully precomputed golden answer (16/16 stages PASS, `return_code=0`, generic `3148c5c3…` / mapped `7120e545…` netlists, byte-identical STA summary) [S7 lines 42-53][S8][S9][S10][S4 §4]. A pilot on T1 therefore validates the highest-risk plumbing — command execution, artifact capture, hash verification, timing capture, and rubric calibration — without confounding those checks with diagnosis or recovery complexity (`BENCHMARK_SPECS.md` §2).
2. **Two models from different provider namespaces.** `opencode/longcat-2.5-free` (`opencode`) and `opencode/flint-40b-free` (`opencode-go`) exercise the harness/model-provider boundary twice; if that portability fails, it is discovered before committing to 45 runs. The qualified baseline itself warns that other hosts/providers are NOT_TESTED [S4 §14], so this is the cheapest place to surface that risk.
3. **Calibration value.** Their two outputs provide a minimal contrast pair for checking that the scoring anchors produce consistent, evidence-cited awards before the full matrix.

**If the lead instead prioritizes harness stress-testing over calibration**, the defensible alternative is `T3` with the same two models, because T3 exercises gate-state transitions and bypass refusal (`BENCHMARK_SPECS.md` §5); this is noted as an option, not the recommendation. The lead's choice is recorded in the pilot run IDs; no design file is changed.

## 6. Required per-run artifacts (all tasks)
Every run directory must contain:
1. `run_record.json` — populated `RUN_RECORD_TEMPLATE.json` (identity, timestamps, environment, command log, artifacts, timing, status).
2. `score_record.json` — populated `SCORE_RECORD_TEMPLATE.json` (dimension awards, normalization, status, evidence refs).
3. `transcript.txt` (or equivalent) — complete model interaction log.
4. `commands.jsonl` — every command, arguments, exit code, stdout/stderr refs.
5. `artifacts/` — all task outputs with SHA-256 hashes and role labels.
6. `manifest.json` — artifact path/hash manifest; `manifest_sha256` recorded in the run record [S10].
7. `environment.json` — interpreter, tool versions, env overrides, git HEAD/tree read at run time, PDK/library pin hashes [S14].
8. `timing.json` — wall-clock, per-stage/per-command durations, `timed_out`, `start_failed` [S9].
9. `outcome.json` — five-valued outcome status, failure reason, `false_pass`, `excluded_items`.

For **T1** specifically: the full 16-stage status map, generic/mapped netlist hashes, STA summary, and the four manifest distinctions [S8][S10].
For **T2**: the read-only diagnosis schema and an explicit statement that RTL/SDC/config were unmodified and the preserved FAIL was not converted to PASS [S4 §14][S12].
For **T3**: detection records for each injected fault, bypass-refusal evidence, recovery steps, and all preserved intermediate/failed records [S11][S6 §2].

**Preservation rule:** none of the above may be deleted, rewritten, or "cleaned up", including failed/timeout records [S2 rule 8][S6 §5]. Failed runs are retained as data.
