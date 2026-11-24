# Third-review validation report (2026-11-19)

**Scope:** verification of external review findings TR-01..TR-04 against the current checkout,
implementation, adversarial regressions, real-runner integration, real EDA evidence and a
portable review package. No numerical rating is assigned internally.

## 1. Content identity

| Item | Starting value | Final value (this pass) |
|---|---|---|
| Commit | `d8f2117e0e09df03e633ff2789faa699e09e748d` | same (changes are local/uncommitted) |
| Tree | `e9beaa22f2ecb272d21b756cd628ea2d46345c7e` | working tree modified by this pass |
| Branch / remote | `main` / `origin` at `d8f2117` | unchanged; no commit/push performed |
| Reviewed external target | `45e4251…` (ancestor) | unchanged |
| `D:\audit\repo` | `41728cd…`, 34 changes | untouched |

## 2. Exact commands and observed results

| Command | Result |
|---|---|
| `python -m pytest -q` (before changes) | **323 passed, 1 failed** — stale README SHA guard (`test_foundation.py:216`) |
| `python -m pytest -q` (after changes) | **353 passed** (324 original + 20 TR-01/02/03 + 9 TR-04) |
| `python -m pytest -q tests/test_third_review_regressions.py` | **20 passed** |
| `python -m pytest -q tests/test_runner_integration_simulated.py` | **9 passed** (real `run_regression.ps1`, simulated tools) |
| `python -m pytest -q <7 safety suites>` (Phase 5 reaudit) | **149 passed** |
| `python regression/checks/stage00_preflight.py <repo-root> --out-dir <isolated>` | **OVERALL=PASS**, mandatory_failures=0, **22/22** collateral hashes MATCH |
| `wsl -d Ubuntu-24.04 -- bash -lc "cd /mnt/d/audit/m1g_mapping_20261117 && bash examples/test-counter/test_framework.sh"` | **47 PASS / 0 FAIL / 0 SKIP** |
| `python -m assistant baseline --design test_counter --run-id tr_baseline_r1 --out <isolated>` | **PASS**, 16/16 stages, exit 0 |
| `python -m assistant baseline --design test_counter --run-id tr_baseline_r2 --out <isolated>` | **PASS**, 16/16 stages, exit 0 |
| `python regression/checks/compare_runs.py <run_r1> <run_r2> <out>` | **REPRODUCIBLE**: 20/20 deterministic artefacts identical, 98/98 checks each, GDSII geometry identical (timestamp records excluded) |
| `python -m assistant baseline --design soc_ctrl --run-id tr_soc_ctrl_failure --out <isolated>` | **exit 1 / INCOMPLETE** — reproduced failure; `12_sta` FAIL, `13_gds`/`14_drc`/`15_lvs` NOT_TESTED; SS WNS `−2.3378 ns`, TNS `−4.0984`, 3 violators (TT/FF clean). Constraints untouched. |

Logs: `D:\audit\third_review_evidence_20261119\`
(`FULL_PYTEST.txt`, `PHASE5_SAFETY_REAUDIT.txt`, `PREFLIGHT.txt`, `FRAMEWORK.txt`,
`BASELINE_COMPARISON.txt`, `PHYSICAL_RUNS.json`, `SOC_CTRL_FAILURE.txt`).

## 3. Test categories (reported separately)

| Category | Count / result | What it proves |
|---|---|---|
| Unit tests (`assistant/` APIs, statuses, hashing, evidence, controller) | included in 353 passed | Policy/status/validation logic in-process |
| Simulated integration tests (adversarial, labelled simulated providers) | 20 passed | TR-01 applicability, TR-02 mutation/revision binding, TR-03 deadlines/partial evidence/late writes |
| Real-runner integration with simulated tools | 9 passed | Actual PowerShell SR-02 scheduler, dependency blocking, acceptance parsing, persistence, resume, ValidateOnly; downstream blocking proven by launch sentinels |
| Real EDA execution | 2 × 16/16 PASS + reproducibility comparison | End-to-end `test_counter` RTL-to-GDSII on the supported host; not signoff, academic decks only |

No simulated result is described as a real EDA result. `simulated` labels are mandatory in the
ECO path (`strict=False` without a label is refused by the controller).

## 4. Negative controls

- LEC FAIL with WNS gain never PASSes (pre-existing `test_simulated_wns_gain_with_lec_fail_never_passes`).
- Forged non-waivable N/A, stale evidence, malformed/incomplete reports, NaN/infinity metrics,
  cross-design/corner reuse, waiver expiry/scope: covered by the 149-test Phase 5 reaudit.
- Zero tool exit + FAIL acceptance blocks the first consumer (real runner, sentinel-proven).
- Nonzero tool exit, missing acceptance JSON, absent mandatory check, manifest persistence
  failure, partial-stage prerequisite gap: each blocks downstream in the real runner.
- `evaluate()` mutating declared/undeclared/SDC/report files, dependency change, pre-acceptance
  cancellation, persistence failure and stale downstream revision: each rejects and rolls back.
- Slow verifier, forged/omitted `duration_s`, partial output + hang, cooperative cancellation,
  late worker write and real child/grandchild process tree: deadline enforced or detected.

## 5. Baseline versus candidate metrics

The engineering baseline is unchanged by this pass: no timing constraint, functional check,
physical check, evidence requirement or rollback guarantee was relaxed. The two real baselines
produced identical generic netlist `3148c5c3…`, mapped netlist `7120e545…`, routed DEF
`8d71072b…`, and identical STA/LVS/DRC summaries after volatile-line exclusion. GDSII geometry
is identical; raw GDS/SPEF bytes differ only in timestamp/path records (the comparison tool
excludes and reports those exclusions).

## 6. Physical limitations (preserved, not "fixed")

- Foundry signoff: **BLOCKED** — no licensed foundry tool/deck/runset/calibration/authority.
- `soc_ctrl` routed SS timing: **FAIL/BLOCKED** — best routed `maj3_2` WNS `−0.852429 ns`,
  TNS `−0.920336 ns`, 2 violators; constraints unchanged. A fresh reproduction run
  (`tr_soc_ctrl_failure`) failed at `12_sta` with SS WNS `−2.3378 ns` / 3 violators and left
  GDS/DRC/LVS NOT_TESTED, confirming the failure is preserved rather than suppressed
  (`SOC_CTRL_FAILURE.txt`). No closure was attempted and no constraint was relaxed.
- Other hosts: **NOT_TESTED** — portability is configured and preflight-verified on one host
  only; no second-host execution is claimed.
- Macro flow: **NOT_APPLICABLE** (`NO-MACROS`); IR/EM/antenna/UPF/post-fill: **UNSUPPORTED**.
- GDS generation and public-deck DRC/LVS do not imply signoff.
- Real ECO lifecycle (functional half): `tests/test_failure_injection.py::test_injected_netlist_corruption_detected_by_lec`
  runs **real Yosys**: it synthesizes the golden RTL, flips a Boolean operator in the netlist,
  runs an actual `equiv_make`/`equiv_induct`/`equiv_status -assert` proof and asserts non-zero
  exit plus "unproven" text. The ECO transaction then rejects the candidate with verified
  rollback and zero downstream execution (simulated downstream callback). This was re-run in
  the 353-test suite. A **real-tool routed-DRC ECO failure** is NOT_TESTED — no `eco_physical_drc`
  tool provider exists in `assistant/`; the applicable physical check is executed by
  `regression/stages/14_drc.sh` in the full flow, not as an injectable ECO provider.

## 7. Evidence manifest and reproduction

Evidence root: `D:\audit\third_review_evidence_20261119\`. Portable bundle:
`REVIEW_PACKAGE\` with `SOURCE_IDENTITY.json`, `SHA256SUMS.txt`, `REPRODUCTION.md`, review
documents, baseline manifests and text logs. Large generated run directories remain outside
Git; retrieval is via the documented commands in `REPRODUCTION.md`.

Reproduction commands are listed in §2. Supported platform: Windows + WSL `Ubuntu-24.04`,
Python 3.10.11, Yosys/EQY/OpenROAD/KLayout with Sky130 HD collateral pinned in
`assistant/pins.json`; `REG_*` overrides configure paths; preflight reports exact missing
requirements.

## 8. Remaining limitations and unresolved findings by severity

| Severity | Item | Status |
|---|---|---|
| P1 | None of TR-01/TR-02/TR-03 | FIXED and regression-covered |
| P2 | TR-04 runner timeout/cancellation scenario | NOT_TESTED at runner level (ECO deadline path covered separately) |
| P2 | Acceptance-CLI mutation scenario at runner level | covered at provider level only |
| P2 | Parallel stage policy tables (PS1 + acceptance.py) remain duplicated | UNRESOLVED (recorded risk; one JSON source not introduced) |
| P2 | Non-cooperative Python verifier threads | cannot be forcibly killed; detected/blocked, not claimed as hard deadline |
| P2 | WSL2 Linux-side grandchildren | require cooperative cleanup; host tree-kill alone insufficient |
| S1 | Foundry signoff | BLOCKED (external authority) |
| S1 | soc_ctrl timing closure | FAIL/BLOCKED (unchanged constraints) |
| S2 | Second-host portability | NOT_TESTED |

## 9. Independent reviewer checklist

1. Confirm identity: `git rev-parse HEAD` matches §1; working tree state disclosed.
2. Run `pytest -q`: expect all tests pass, with 20 TR-01/02/03 and 9 runner-integration tests.
3. Inspect `assistant/eco.py`: `applicability_for`, `CandidateRevision`,
   `_pre_acceptance_revalidation`, monotonic `_collect_verification`; verify no NO-MACROS DRC N/A.
4. Inspect `assistant/runner.py`: monotonic timing, process-group launch, tree termination.
5. Inspect `regression/run_regression.ps1`: DI seams default to production behaviour; SR-02 loop intact.
6. Run the two baseline commands and `compare_runs.py`; confirm REPRODUCIBLE.
7. Confirm preserved blocker classification in README §"Current blockers".
8. Verify the portable bundle hashes with `SHA256SUMS.txt`.
9. Decide the rating independently; this report deliberately assigns none.

## Round-2 revalidation (2026-11-19)

- Baseline exploits reproduced before fixing: F-02 verifier mutation → PASS with mutated RTL;
  F-03 deadline expiry during evaluate → PASS; F-01 simulated evidence → gate PASS.
- After fixes: F-02 → FAIL + verified rollback; F-03 → INCONCLUSIVE + deadline_exceeded;
  F-01 → `SIMULATED_PASS`, qualified=false, CLI exit 1.
- 9 new exploit regressions in `tests/test_remediation_round2.py`; 9 new F-01/F-07
  regressions in `tests/test_gate_api.py`; 20 real-runner integration tests in
  `tests/test_runner_integration_simulated.py` (vulnerable baseline 8 failed / 12 passed;
  fixed 20 passed).
- Full pytest: **383 passed**. Real EDA: one `test_counter` baseline rerun after runner
  change (`rem_r2_baseline`, see evidence package) — historical results preserved separately.
- Remaining: uncontained non-cooperative Python callbacks, WSL2/detached process trees,
  coverage limited to pins-listed collateral, duplicated stage-policy tables, no physical
  ECO DRC provider, second host NOT_TESTED.

## Round-3 revalidation (2026-11-19)

- Before-fix exploits all reproduced (`D:\audit\third_remediation_round3_20261119\LOGS/PHASE0_REPRO.txt`).
- After-fix lead verification: 8/8 FIXED (`LOGS/LEAD_VERIFICATION.txt`).
- New exploit regressions: `test_r3_r201_*` (15), `test_r3_r202_*` (15),
  `test_r3_r203_*` (10), `test_r3_r204_*` (7), `test_r3_r205_*` (8).
- Full pytest: 438 passed. Real-runner integration: 20 passed.
- Source reconstruction: complete 200-file bundle; clean reconstruction 0 hash mismatches;
  438 passed + 20 runner tests pass from reconstructed checkout.
- Remaining: cooperative/unsupported containment paths (WSL2, detached, uncooperative Python
  threads) are detected/classified, not prevented; no physical ECO DRC provider; second host
  NOT_TESTED. No readiness declaration.

## Round-4 revalidation (2026-11-20)

- Nine prior P1 findings remediated; lead personally reproduced baseline and post-fix behavior.
- Cross-workstream integration (4 combined attacks) all PASS.
- Flaky test `test_partial_evidence_survives_deadline`: 7/10 pass in isolation with variable runtimes;
  a test-timing assumption (worker must yield evidence within a 50 ms deadline), not an implementation
  defect. Evidence preserved in `LEAD_VERIFICATION/flaky_investigation.txt`.
- Remaining honest limitations: WSL2/uncooperative-thread containment classified not prevented;
  post-hoc detection windows; full state-file-write attacker remains out of trust model.
- No physical experiments rerun; historical results preserved.
