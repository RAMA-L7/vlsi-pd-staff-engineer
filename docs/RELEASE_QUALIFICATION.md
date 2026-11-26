# Release-candidate qualification matrix

**Recorded UTC:** 2026-11-25T13:59:20Z
**Document type:** documentation/audit only. No source code was changed by this pass.
**Supported scope:** a **supervised academic physical-design workflow** with simulated-tool integration tests and scripted EDA execution where available. There is **no production signoff, foundry qualification, tapeout authorization, or autonomous RTL-to-GDSII claim**. **No numerical quality rating** is assigned. Historical verdicts in `docs/reviews/` are retained as history and are not re-asserted here.

## 1. Current source identity (recorded, not re-stamped by this pass)

| Item | Recorded value | Verification status |
|---|---|---|
| Main checkout | `D:\audit\m1g_mapping_20261117` at `d8f2117e0e09df03e633ff2789faa699e09e748d`, branch `main`, **38 dirty paths preserved** | Verified by read-only `git rev-parse HEAD` / `git status --porcelain` (38 entries) |
| `origin/main` | `fa2bc751b3add19d4db250d01dac8de4f88ca347` (`refs/heads/main`) | Verified by `git ls-remote origin refs/heads/main` |
| Reviewed commit | `fa2bc751b3add19d4db250d01dac8de4f88ca347`; 228 reviewed content files; tree SHA-256 `265d90310e354a9162b5455c35f75b61e301ab79eab41d46896a240f672df615` | Recorded in `GOVERNANCE/STARTING_IDENTITY.json` |
| Remediation source | R7.2 working copy; 231 files; `identical_to_r72: true` | Recorded in `GOVERNANCE/STARTING_IDENTITY.json` |
| RC working copy (starting identity) | `D:\audit\release_candidate_20261125\WORKING_COPY`; 231 files; tree SHA-256 `67dff40684d1da6f83402a3a300193fa14a33a6025cd6394810d35f51cb76c8b8b` | Re-derived from `GOVERNANCE/STARTING_SHA256.txt` (231 records) by `sha256(json.dumps(path_to_hash, sort_keys=True))` — reproducible and matches |
| Live-tree drift observed during this audit | At ~13:52Z, after the 13:28:16Z snapshot, `assistant/controller.py` and `assistant/eco.py` no longer matched the manifest hashes (recorded `7f9c9201…` / `2922deb6…`; observed `31467a7c…` / `7ac183fc…`) | Observation only; this agent did not edit `assistant/` or `tests/`. **Coordinator must restamp the final identity before release.** The three documents written here also change the tree |

## 2. Authoritative qualification matrix

Classification vocabulary: `VERIFIED` = directly checked here; `REPRODUCED` = the stated behaviour was observed; `COLLECTED` = discovered but not executed here; `HISTORICAL` = recorded previously and not re-run for this RC; `NOT_TESTED` = no result; `BLOCKED` / `FAIL` = known negative result or unsupported capability.

| Area | Classification | Basis / evidence (local audit artifact unless marked public source) | Identity / date | Qualification and limits |
|---|---|---|---|---|
| Current source identity | VERIFIED | `GOVERNANCE/STARTING_IDENTITY.json`, `GOVERNANCE/STARTING_SHA256.txt`, live git probes | 2026-11-25T13:28:16Z snapshot; re-checked 13:59Z | Starting identity reproduced from manifest; live-tree drift noted in §1; final identity is coordinator-owned |
| A7-P0-01 vulnerable behaviour on reviewed source | REPRODUCED | `EVIDENCE/A7P0_BEFORE_AFTER.json` (`reviewed_fa2bc75`): `qualified=true`, `decision=CLEAN_PASS`, `downstream_ok=true`, `ledger_problems=[]`, `reproduced=true`; worktree `VULNERABLE_REVIEWED_191rvo6c` | Reviewed `fa2bc75` | Unauthenticated `check_index` proof inheritance reproduced; this is a *vulnerability* result, not a product capability |
| A7-P0-01 rejection in RC | VERIFIED (local evidence) | Same JSON (`release_candidate`): `qualified=false`, `decision=BLOCKED`, `downstream_ok=false`, `reproduced=false`; three `check_index` integrity problems: `eco_lvs:check-mismatch:59ffe7e3621c4303`, `eco_lvs:unbound-index`, `eco_sta:cross-check-reuse:59ffe7e3621c4303` | RC starting identity | Rejection path observed under the audited probe; not a full-suite result |
| Remediation primitives present in source | VERIFIED (source inspection only) | `assistant/execution_trust.py` (MAC-bound `check_id`), `assistant/controller.py` (`DOMAIN_CHECK_INDEX="check_index/v1"`, MAC-bound index, fresh GA reads, `_mutate` single transaction with `fsync`+`os.replace`), `assistant/trust.py`, `assistant/transaction.py`, `assistant/eco.py` (immediate cancel latch) | RC starting identity | Presence verified; runtime behaviour is evidenced only by the two rows above and the stress row below |
| P0-A cancel-at-persistence regression | VERIFIED (local evidence) + HISTORICAL before | `EVIDENCE/CANCEL_STRESS_AFTER.txt`: `total=30`, `fails=0`; before-fix `3/20` in `D:\audit\third_remediation_round7_2_20261124\LEAD_VERIFICATION\CANCEL_STRESS.json`; probe script `D:\audit\publication_evidence_20261118\rc_cancel_stress.py` | 2026-11-25 RC / historical pre-fix | Local artifacts only; different sample sizes (20 vs 30), not a comparative statistical claim |
| Python test suite for this RC | COLLECTED; full run NOT_TESTED | `python -m pytest --collect-only -q` -> **562 tests collected in 2.37s** (read-only, this host). `EVIDENCE/FULL_SUITE.txt` exists but is **0 bytes** | 2026-11-25T13:59Z | Collection is not a PASS/FAIL result. No full-suite outcome is asserted for this RC |
| Simulated-tool integration tests | COLLECTED; RC full run NOT_TESTED; HISTORICAL results elsewhere | `tests/r71_evidence_check_binding.py`, `tests/r72_regressions.py`, `tests/r72_transaction_integrity.py`, `tests/test_runner_integration_simulated.py`, etc.; tests use simulated providers and throwaway test keys (`tests/r63_binding_helper.py`, `monkeypatch.setenv("ASSISTANT_LEDGER_KEY", …)`) | Historical: `docs/SETUP.md` records 353 passed on 2026-11-19 | Tests are detection-contract/simulated checks; their count must not be read as real EDA runs or as an RC result |
| Real EDA tools on this host | PRESENT; RC runs NOT_TESTED | `D:\tools\oss-cad-suite\bin`: `yosys.exe`, `iverilog.exe`, `sby.exe`, `nextpnr-*.exe`, `vvp.exe`; `Get-Command openroad,sta,OpenSTA` -> not on PATH; `D:\tools\OpenROAD-flow-scripts` exists (ORFS fallback in `assistant/pins.json`) | 2026-11-25 | Tool presence is not tool execution. No RC real-synthesis/STA/P&R result is claimed; exact procedure in `REPRODUCING.md` |
| Real EDA baselines | HISTORICAL only | `README.md`, `docs/VALIDATION.md`, `docs/reviews/PUBLICATION_REPORT.md`: `test_counter` 16/16 stages and byte-identical mapped/generic netlists in earlier run IDs; `pubfinal20261118` on published ancestor content | 2026-11-16 / 2026-11-18 | Not re-run against RC identity; historical results do not transfer to this working copy |
| soc_ctrl routed SS timing | FAIL (historical, retained) | `README.md` §qualification matrix and `FINAL_INDEPENDENT_QUALIFICATION_REPORT.md` §8: negative setup WNS with violators at unchanged 10 ns / 2 ns; full run `run_pub_m1d_soc_ctrl_full` 14/16 stages | Historical (2026-11-17/18) | Not re-run for RC. No constraint relaxation or hidden failure is claimed |
| Foundry signoff / tapeout | BLOCKED / OUT OF SCOPE | Historical report §8/§14: no licensed Calibre/IC Validator/Assura, no foundry deck/runset/calibration/authority | Standing | Academic DRC/LVS are not signoff; no production claim |
| Other-host portability | NOT_TESTED | `docs/SETUP.md` §1–3A: pins recorded on one host; portable fallbacks validated on that host only (ORFS HEAD `25b6429c…`); WSL2 and KLayout Qt5 are hidden host state | Standing | A different host must re-probe and report drift, not inherit these results |
| Fixed candidate (`e591c30a…`) validation scope | INDEPENDENTLY VERIFIED on one Windows host only; every other environment NOT_TESTED | Full suite ×3 (613/5, rc=0), containment, cancellation/persistence stress and targeted security regressions on host `mi`, Python 3.10.11 / pytest 9.2.0; WSL EDA evidence belongs to the frozen/earlier tree; no second host, no native POSIX suite, no real-tool EDA on the fixed candidate | 2026-11-26; `D:\audit\stability_commit_sprint_20261126\REPORTS\`; `D:\audit\final_independent_reassessment_20261126\REPORTS\` | Full disclosure and environment matrix: `docs/VALIDATION_SCOPE_AND_PORTABILITY.md`; do not inherit these results across hosts |
| Agent performance study | PROTOCOL COMPLETE / PILOT BLOCKED | `docs/research/STUDY_STATUS.md`: four preserved BLOCKED pilot records; no model-executed runs or scores | 2026-11-18 | No performance score, ranking or model comparison is claimed |

## 3. Failed, unsupported and untested — consolidated

- **Failed (retained):** soc_ctrl routed SS timing; historical vulnerable A7-P0-01 behaviour on `fa2bc75`.
- **Blocked / unsupported:** foundry signoff and tapeout authorization; host-compromise and key-holder adversaries (see `THREAT_MODEL.md` §3.2); process containment beyond sampling (`execution_trust.py` A5).
- **Untested for this RC:** full pytest suite execution; real EDA baseline (synthesis/STA/P&R/DRC/LVS) against RC identity; second-host reproduction; candidate-B stage-14/15 closures; macro flow; timing/IR/EM signoff.
- **Evidence gaps:** `EVIDENCE/FULL_SUITE.txt` is empty; no RC real-EDA log is present in `EVIDENCE/`; all audit paths under `D:\audit` are local and are **not** a public evidence bundle.

## 4. Open blockers

1. Final source identity is not yet restamped after post-snapshot source edits (two `assistant/` files) and after the three documentation files in `docs/` were added: coordinator action required.
2. Full suite and real-EDA execution for the RC identity have not been performed/recorded; a reviewer cannot infer a full-suite outcome from collection or from historical counts.
3. A7-P0-01 rejection and P0-A stress results exist only as local artifacts; independent reproduction by a second host/reviewer is NOT_TESTED.
4. soc_ctrl routed SS timing remains a retained historical FAIL; no authorization exists for an architecture or constraint change.
5. Foundry signoff capability is absent; academic physical results must be labelled as such.
6. Host portability (WSL2 tools, ORFS/PDK collateral, KLayout Qt5) is one-host-only; other hosts require re-probing.

## 5. Claim rules for reviewers

- Do not describe this RC as production-qualified, tapeout-authorized, complete, or free of remaining defects.
- Do not convert `qualified=true` in the *vulnerability reproduction* row into a product claim; it means the vulnerable source inherited authorization.
- Do not convert simulated-test counts or historical 16/16 baselines into current RC evidence.
- Do not assign a numerical rating to the workflow or to model performance.
- Preserve every FAIL and BLOCKED row; qualification without its limitations is not this document's position.
