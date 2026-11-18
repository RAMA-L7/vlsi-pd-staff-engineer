# Agent Performance Evaluation Study — Status

**Status:** PROTOCOL COMPLETE / PILOT BLOCKED BY MODEL ACCESS.

This repository contains the frozen experimental protocol, benchmark specifications,
scoring rubric, run/score templates and the preserved BLOCKED pilot records for the
agent performance evaluation study. No model-executed benchmark runs exist yet; no
performance scores or model comparisons are claimed.

## What is included
- `EXPERIMENTAL_PROTOCOL.md` — fixed method, variables, run procedure, evidence schema, statistics plan.
- `BENCHMARK_SPECS.md` — T1/T2/T3 task definitions, golden facts, allowed/forbidden actions, output schemas.
- `SCORING_RUBRIC.md` — 0–100 objective rubric across engineering accuracy, constraints, timing, verification, failure detection, recovery, evidence integrity and efficiency.
- `MODEL_MATRIX_PLAN.md` — 5 models × 3 tasks × 3 repetitions = 45 planned runs; pilot plan.
- `RUN_RECORD_TEMPLATE.json`, `SCORE_RECORD_TEMPLATE.json` — per-run records.
- `MODEL_SET_DECISION.md` — transition from paid/subscription models to free models after access blockers.
- `pilots/*/BLOCKED.json` — four preserved BLOCKED records (two paid-model attempts, two free-model attempts).

## Current blocker
Pilot execution failed before any task started:
- `opencode/claude-fable-5-2`: insufficient pay-as-you-go credits.
- `opencode-go/qwen4-max`: OpenCode Go subscription required.
- `opencode/deepseek-v4.1-flash-free` and `opencode/longcat-2.5-free`: free usage limit reached.

No model result, score or PASS conversion was produced. Do not interpret this study as evidence of model performance until runs are executed under the fixed protocol.

## Next action
Obtain model access (credits, subscription or reset free quota), then execute the pilot and full matrix with the frozen protocol. Preserve every run, including failures.
