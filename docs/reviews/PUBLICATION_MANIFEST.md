# Publication Manifest — vlsi-pd-staff-engineer

Generated: 2026-11-18T17:00:38Z
Repository: `RAMA-L7/vlsi-pd-staff-engineer` (branch `main`, local `D:\audit\m1g_mapping_20261117`)
Base commit: `0a9eb57e38b86acc51b8bcb6dea53bd97ab90e9b`

**Total files committed: 57** (35 modified + 22 new).

All files below are part of the authorized publication commit. No deleted files.
Validation workspace artifacts (`regression/workspace/`, `regression/evidence/`) are gitignored and excluded.

## Core documentation (docs/) (5)

- `docs/ARCHITECTURE.md` — modified
- `docs/DESIGN.md` — modified
- `docs/REGRESSION.md` — modified
- `docs/SETUP.md` — modified
- `docs/VALIDATION.md` — modified

## Core framework (assistant/) (8)

- `assistant/baseline.py` — modified
- `assistant/cli.py` — modified
- `assistant/controller.py` — modified
- `assistant/diagnosis.py` — modified
- `assistant/eco.py` — modified
- `assistant/evidence.py` — modified
- `assistant/pins.json` — modified
- `assistant/tools.py` — modified

## Design examples (1)

- `examples/soc_ctrl/rtl/soc_ctrl.sdc` — modified

## Regression infrastructure (regression/) (8)

- `regression/README.md` — modified
- `regression/checks/acceptance.py` — modified
- `regression/checks/lvs_compare.py` — modified
- `regression/checks/stage00_preflight.py` — modified
- `regression/configs/soc_ctrl_10ns.json` — modified
- `regression/run_regression.ps1` — modified
- `regression/stages/12_sta.sh` — modified
- `regression/stages/15_lvs.sh` — modified

## Research study (docs/research/) (12)

- `docs/research/BENCHMARK_SPECS.md` — new
- `docs/research/EXPERIMENTAL_PROTOCOL.md` — new
- `docs/research/MODEL_MATRIX_PLAN.md` — new
- `docs/research/MODEL_SET_DECISION.md` — new
- `docs/research/RUN_RECORD_TEMPLATE.json` — new
- `docs/research/SCORE_RECORD_TEMPLATE.json` — new
- `docs/research/SCORING_RUBRIC.md` — new
- `docs/research/STUDY_STATUS.md` — new
- `docs/research/pilots/pilot_t1_claude_fable_r1/BLOCKED.json` — new
- `docs/research/pilots/pilot_t1_deepseek_free_r1/BLOCKED.json` — new
- `docs/research/pilots/pilot_t1_longcat_free_r1/BLOCKED.json` — new
- `docs/research/pilots/pilot_t1_qwen4_max_r1/BLOCKED.json` — new

## Review & qualification reports (docs/reviews/) (6)

- `docs/reviews/FINAL_INDEPENDENT_QUALIFICATION_REPORT.md` — new
- `docs/reviews/NEXT_PHASE_TECHNICAL_REPORT.md` — new
- `docs/reviews/final-validation-report.md` — new
- `docs/reviews/post-publication-validation.md` — modified
- `docs/reviews/remediation-tracker.md` — modified
- `docs/reviews/second-review.md` — new

## Root documentation (1)

- `README.md` — modified

## Tests (tests/) (16)

- `tests/test_baseline.py` — modified
- `tests/test_cli_evidence.py` — modified
- `tests/test_cli_exit_codes.py` — new
- `tests/test_controller_gates.py` — modified
- `tests/test_eco.py` — modified
- `tests/test_eco_verification.py` — new
- `tests/test_eqy_override.py` — modified
- `tests/test_evidence_dependencies.py` — new
- `tests/test_failure_injection.py` — modified
- `tests/test_foundation.py` — modified
- `tests/test_gate_api.py` — new
- `tests/test_lvs_compare.py` — modified
- `tests/test_m1d_integration.py` — modified
- `tests/test_next_phase_robustness.py` — new
- `tests/test_sta_parser.py` — modified
- `tests/test_stage_gating.py` — new

## Excluded from commit

- `regression/workspace/` and `regression/evidence/` — generated flow artifacts (gitignored).
- `__pycache__/`, `*.pyc` — Python caches (gitignored).
- External evidence roots under `D:\audit\` — validated-host paths, outside repository.
- Session-local model logs and harness smoke PASS artifacts from the blocked pilot study (not model results; a clarifying note was placed in the external study workspace only).
- No credentials, tokens, secrets or private configuration were found or included.
