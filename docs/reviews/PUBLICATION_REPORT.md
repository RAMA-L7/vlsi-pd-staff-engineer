# Publication Report — vlsi-pd-staff-engineer

**Event:** Authorized publication of the qualified baseline, remediation, technical reports,
research preparation and coordination-related project documentation to GitHub `main`.
**Timestamp:** 2026-11-18T17:02:17Z / 2026-11-18T22:32:17+05:30 IST
**Repository:** `RAMA-L7/vlsi-pd-staff-engineer`, branch `main`, remote `origin`
**Final status:** SUCCESS — push verified against remote `origin/main`.

---

## 1. Commits

| Item | Value |
|---|---|
| Previous commit (base) | `0a9eb57e38b86acc51b8bcb6dea53bd97ab90e9b` (tree `ab0bcf96d00e99a1b1a6cd46f41fde159661791b`) |
| New commit (HEAD) | `45e4251370c39ed27e2ccd6a7a804ed90eaf4472` (tree `00b0418ff4fde3cda377f0175bb92c5b1b32e1ca`) |
| Parent | `0a9eb57e38b86acc51b8bcb6dea53bd97ab90e9b` (single parent, no merge) |
| Commit message | `docs: publish qualified baseline and research preparation` |
| Author / committer | RAMA-L7 (repository-configured identity; no credentials in content) |
| Change size | 57 files, 11268 insertions(+), 921 deletions(-) |

## 2. Push result

- Command: `git push origin main` — exit 0.
- Remote response: `0a9eb57..45e4251  main -> main` (fast-forward, no force).
- No PR, merge, branch-protection bypass, install or unrelated-project modification.

## 3. Final remote / main verification

| Check | Value |
|---|---|
| `git ls-remote origin refs/heads/main` | `45e4251370c39ed27e2ccd6a7a804ed90eaf4472` |
| `git rev-parse origin/main` (after fetch) | `45e4251370c39ed27e2ccd6a7a804ed90eaf4472` |
| `git rev-parse HEAD` | `45e4251370c39ed27e2ccd6a7a804ed90eaf4472` |
| HEAD == origin/main | YES |
| Working tree | clean (0 changes) |
| Key published blobs | README, FINAL_INDEPENDENT_QUALIFICATION_REPORT, STUDY_STATUS, BLOCKED pilot — all present |
| `D:\audit\repo` | untouched — HEAD `41728cd…`, 34 pre-existing changes |

## 4. README changes

README.md was rewritten as the publication-facing entry point:
- Purpose and capabilities: clarified as a deterministic, scripted RTL-to-GDSII regression
  plus advisory agent/skill guidance; no claim that live AI agents executed EDA runs.
- Supervised PD workflow: stage-gate lifecycle retained, with an authoritative
  "Automated executor vs AI-agent orchestration" table added.
- Verification and acceptance gates: one authoritative qualification matrix added at top,
  explicitly superseding dated historical tables.
- Framework architecture/agent workflow: documented with `assistant/`, `regression/` layers.
- Validation results: pytest 324 passed; framework 47/0/0; preflight PASS 22/22 hashes;
  real baseline PASS 16/16 with byte-identical netlists `3148c5c3…` / `7120e545…`.
- Historical 2026-11-18 verdict recorded as **READY WITH LIMITATIONS**; the 2026-11-26 external assessment verdict is **NOT READY, 2 / 5 — Weak** (`docs/EXTERNAL_ASSESSMENT_STATUS.md`).
- Known limitations: foundry signoff BLOCKED; soc_ctrl routed SS timing FAIL/BLOCKED
  (best routed `maj3_2` WNS −0.852429 ns, TNS −0.920336 ns, 2 violators); other hosts
  NOT_TESTED; macro example NOT_APPLICABLE; IR/EM/antenna/UPF/post-fill UNSUPPORTED.
- Research status: protocol complete / pilot blocked by model access; no scores claimed.
- Setup/reproduction: one documented baseline command, `docs/SETUP.md` reference, host-path caveat.
- Links to detailed reports and evidence added.
- Stale claims corrected: branch `main` (not `feature/F-01-…`); `6c4a264` qualified as a
  historical ancestor, not current `origin/main`; timing metrics qualified with current best
  values; academic-only qualifiers on PASS rows; personal email redacted.

## 5. Files committed (57 total: 35 updated, 22 added, 0 deleted)

Complete list grouped by category is in `PUBLICATION_MANIFEST.md`. Summary:

- **Root documentation (1):** README.md (updated)
- **Core documentation (5):** docs/ARCHITECTURE.md, DESIGN.md, REGRESSION.md, SETUP.md, VALIDATION.md (updated)
- **Core framework (8):** assistant/baseline.py, cli.py, controller.py, diagnosis.py, eco.py, evidence.py, pins.json, tools.py (updated)
- **Design examples (1):** examples/soc_ctrl/rtl/soc_ctrl.sdc (updated)
- **Regression infrastructure (8):** regression/README.md, checks/acceptance.py, checks/lvs_compare.py, checks/stage00_preflight.py, configs/soc_ctrl_10ns.json, run_regression.ps1, stages/12_sta.sh, stages/15_lvs.sh (updated)
- **Review & qualification reports (6):** FINAL_INDEPENDENT_QUALIFICATION_REPORT.md, NEXT_PHASE_TECHNICAL_REPORT.md, final-validation-report.md, second-review.md (added); post-publication-validation.md, remediation-tracker.md (updated)
- **Research study (12, all added):** docs/research/BENCHMARK_SPECS.md, EXPERIMENTAL_PROTOCOL.md, MODEL_MATRIX_PLAN.md, MODEL_SET_DECISION.md, RUN_RECORD_TEMPLATE.json, SCORE_RECORD_TEMPLATE.json, SCORING_RUBRIC.md, STUDY_STATUS.md, pilots/{pilot_t1_claude_fable_r1,pilot_t1_deepseek_free_r1,pilot_t1_longcat_free_r1,pilot_t1_qwen4_max_r1}/BLOCKED.json
- **Tests (16):** 10 updated (test_baseline.py, test_cli_evidence.py, test_controller_gates.py, test_eco.py, test_eqy_override.py, test_failure_injection.py, test_foundation.py, test_lvs_compare.py, test_m1d_integration.py, test_sta_parser.py); 6 added (test_cli_exit_codes.py, test_eco_verification.py, test_evidence_dependencies.py, test_gate_api.py, test_next_phase_robustness.py, test_stage_gating.py)

Deleted files: none.

## 6. Validation commands and results

| Check | Result |
|---|---|
| `python -m pytest -q` | PASS — 324 passed |
| WSL `examples/test-counter/test_framework.sh` | PASS — 47 PASS / 0 FAIL / 0 SKIP |
| `stage00_preflight.py <repo-root> --out-dir …` | PASS — OVERALL=PASS, 0 mandatory failures, 22/22 hashes MATCH |
| `python -m assistant baseline --design test_counter --run-id pubfinal20261118 …` | PASS — 16/16 stages; netlists `3148c5c3…` / `7120e545…` |

Known documentation discrepancy (not a regression): the documented config-file preflight form
uses placeholder `rtl/top.v` / `constraints/top.sdc` and returns FAIL with 2 mandatory failures;
all collateral/dependency checks still pass. The authoritative repo-root form was re-run and
verified PASS. Logs: `final_validation/`.

## 7. Publication manifest path

`D:\audit\publication_evidence_20261118\PUBLICATION_MANIFEST.md`
(also mirrored as `D:\audit\publication_evidence_20261118\STAGED_FILES.txt`).

## 8. Excluded files and reasons

- `regression/workspace/`, `regression/evidence/` — generated flow artifacts, gitignored.
- `__pycache__/`, `*.pyc` — Python caches, gitignored.
- External evidence roots under `D:\audit\` — validated-host paths, outside the repository.
- Session-local model logs and harness smoke PASS artifacts from the blocked pilot study —
  not model results; no PASS conversion; a clarifying note was placed only in the external
  study workspace.
- No credentials, tokens, secrets or private configuration were found or included. The one
  personal email previously present in a historical report was redacted before staging
  (verified: 0 occurrences in published content).

## 9. Integrity and blockers preserved

Frozen engineering results and all honest classifications retained: foundry signoff **BLOCKED**;
soc_ctrl routed SS timing **FAIL/BLOCKED**; other hosts **NOT_TESTED**; macro example
**NOT_APPLICABLE**; research pilot **BLOCKED** (model access). No signoff, tapeout, autonomous
RTL-to-GDSII or publication-quality claim beyond this authorized documentation commit.
