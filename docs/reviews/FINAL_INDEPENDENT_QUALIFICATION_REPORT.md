# Final Independent Qualification Report — vlsi-pd-staff-engineer

**Post-publication update (2026-11-18T17:02:17Z / 2026-11-18T22:32:17+05:30 IST):** the worktree content audited/described below was committed as `45e4251370c39ed27e2ccd6a7a804ed90eaf4472` (tree `00b0418ff4fde3cda377f0175bb92c5b1b32e1ca`) and pushed fast-forward to `origin/main` (verified). SHA values, path counts and "publication not authorized / no commit-push" statements below reflect the state **at the time of that pass** and are retained for traceability; they are not the current publication status. See `docs/reviews/PUBLICATION_REPORT.md`.


**Auditor:** Lead independent auditor (Hy4 flash Free)
**Session:** `lead-auditor-20261118T152925Z`
**Start:** 2026-11-18T15:29:25Z / 2026-11-18T20:59:25+05:30 IST
**Stop:** 2026-11-18T15:48:00Z / 2026-11-18T21:18:00+05:30 IST (approx; final log entry timestamp authoritative)
**Baseline:** worktree `D:\audit\m1g_mapping_20261117` @ HEAD `0a9eb57e38b86acc51b8bcb6dea53bd97ab90e9b`, tree `ab0bcf96d00e99a1b1a6cd46f41fde159661791b`; 42 local uncommitted paths, staged diff empty.
**Isolated audit copy:** `D:\audit\independent_qualification_20261118\isolated_worktree\repo` — 42 paths, normalized content identical to source; all destructive probes performed there.
**Evidence root:** `D:\audit\independent_qualification_20261118\`.

## 1. Executive verdict

**READY WITH LIMITATIONS** for external evaluation of the *supervised academic PD workflow*.

This is not a claim of production signoff, foundry qualification, tapeout readiness, autonomous RTL-to-GDSII capability, or general host portability. The project is technically credible, reproducible on the validated host, and robust against the tested false-PASS paths. It is **not ready** to be represented as a fully qualified multi-design PD flow: foundry signoff remains BLOCKED, soc_ctrl routed SS timing remains FAIL, and fresh-environment reproducibility is one-host-only.

The verdict is based on independently re-run tests, real baselines, primary-artifact re-hashing and adversarial probes — not on prior PASS claims or test counts alone.

## 2. Audit scope and methodology
- Four independent subagents (A framework, B physical evidence, C reproducibility, D external evaluation) audited an isolated copy with disjoint evidence directories and their own session logs.
- Lead independently re-verified critical findings: documentation defects D-01..D-04, preflight mismatch F1, and git/remote/original-repo integrity.
- Methods: direct git/hash inspection; full pytest; WSL framework script; preflight in two forms; real `test_counter` baseline in isolated copy; primary-artifact re-hash (netlists, SPEF, DEF, GDS, STA, EQY, LVS, decks); adversarial probes with labelled simulated providers; PowerShell `-ValidateOnly` probes against a copied `regression\` directory; documentation claim/evidence cross-check.
- No commit, push, PR, merge, install or unrelated-project modification. Original `D:\audit\repo` untouched (`41728cd`, 34 changes).

## 3. Independent verification results

| Area | Auditor | Classification | Result |
|---|---|---|---|
| Framework integrity & failure propagation | A | VERIFIED (with S3 qualification A-1) | No reproducible S1/S2 false-PASS path. 31 adversarial probes; ECO FAIL+WNS-gain rolls back with no downstream call; stale/artifact/wavier/N/A/cycle/CLI/rollback controls verified. |
| Physical-design & EDA evidence credibility | B | VERIFIED (academic) / BLOCKED (foundry) | All key hashes re-derived. test_counter 16/16; soc_ctrl mapped LEC genuine; routed SS FAIL preserved; academic DRC/LVS credible; foundry signoff absent. |
| Reproducibility & fresh-environment readiness | C | VERIFIED (this host) / PARTIALLY_VERIFIED (fresh env) | 324 pytest, framework 47/0/0, preflight 22 hashes MATCH, real baseline 16/16 with byte-identical netlists. Documented preflight command F1 failed; supported CLI form passed. |
| Benchmark & external-evaluation quality | D | VERIFIED / PARTIALLY_VERIFIED | Failure-injection contract suite genuine; no standalone benchmark answer key/rubric; four documentation defects found and corrected; limitations explicit. |
| Lead verification of critical findings | Lead | VERIFIED | D-01..D-04 confirmed directly; F1 confirmed directly (documented template command exit 1 vs supported CLI exit 0); remote/original repo unchanged. |

## 4. Reproducibility results
- Auditor C independently ran `pytest tests\ -q -rs` in the isolated copy: **324 passed in 28.52s**, exit 0.
- WSL framework from isolated copy: **PASS 47 / FAIL 0 / SKIP 0**, exit 0.
- Supported preflight: **PASS**, exit 0, 22/22 collateral hashes MATCH, no missing mandatory/optional dependencies, portable fallbacks applied.
- Real baseline from isolated copy without manual ORFS overrides: **PASS**, return_code 0, 16/16 stages. Generic netlist `3148c5c3…`, mapped `7120e545…`, routed DEF `8d71072b…`, STA summary `272DE50D…` — all byte-identical to published r3 evidence.
- Negative controls: bogus `REG_PLATFORM_SKY130HS` → preflight FAIL, exit 1, missing RC rules; wrong-content file → FAIL, exit 1, HASH_MISMATCH.
- **Hidden host state:** validated fallback resolves to `D:\tools\OpenROAD-flow-scripts`; ORFS tree is dirty and requires one Liberty LF-normalization to hash `ec0e1067…`; WSL KLayout needs Qt5 libs; other hosts NOT_TESTED.

## 5. Framework integrity results
- Acceptance before downstream execution: VERIFIED in gate model and PowerShell source; failed acceptance blocks descendants.
- No false PASS: stale evidence after input change (including restart), modified/deleted reports, zero-exit invalid outcomes, forged persisted outcomes, non-waivable N/A, empty gates, expired/improper waivers, force-pass and LLM-summary bypass attempts, unauthorized restricted ECO, ECO FAIL with improved WNS — all rejected/blocked.
- CLI exit codes 0/1/2/3 correct; `gate-evaluate` exit 0 for validated N/A must be read with JSON `disposition` (A-5, S3).
- A-1 qualification: CLI `gate-record` can accept caller-supplied PASS with exit 0 and no independent engineering validator because the supervised workflow intentionally trusts an authorized caller; this is not an autonomous false-PASS path, but the claim "zero exit never sufficient" applies to automated tool checks, not the supervised CLI record primitive. Documented as S3.
- A-2/A-3/A-4 are fail-closed S3 quirks (partial ValidateOnly overall INCONCLUSIVE; tool check records parsed FAIL with exit 0 rather than non-zero exit; CLI `--input-hash` evidence stale because inputs are not registered) — no false PASS.

## 6. Adversarial test results
- Auditor A: 31 labelled-simulated probes, 0 unexplained anomalies.
- Auditor C: preflight negative controls and real baseline negative hash tests.
- Auditor D: failure-injection suite genuine (8/8 in prior evidence), but no standalone benchmark answer key; expected answers are pytest assertions.
- No adversarial probe produced a PASS for failed engineering evidence.

## 7. Benchmark credibility
- VERIFIED: failure-injection detection contracts, real EDA baselines and framework self-tests are distinguished in documentation.
- PARTIALLY_VERIFIED: no external benchmark specification/answer-key/rubric; simulation-heavy tests are disclosed but "324 passed" should not be read as 324 real-tool runs.
- PARTIALLY_VERIFIED: qualification matrix ties demonstrated rows to `6c4a264`; a later real baseline on the current `0a9eb57` worktree exists in next-phase evidence but is not fully reflected in the matrix (D-06, informational).

## 8. Physical-design evidence assessment
- VERIFIED: test_counter full closure (16/16 stages, 98/98 acceptance checks in r3 artifact); mapped Sky130 cells, no unresolved modules; EQY 8/8 soc_ctrl mapped LEC with 2/8 negative control rejected; Yosys mapped LEC 16/16 for candidates A/B with genuine negative control.
- VERIFIED FAIL: soc_ctrl routed SS timing remains negative. Historical candidate B `−1.832469 / −4.685722`, 4 violators; best next-phase `maj3_2` `−0.852429 / −0.920336`, 2 violators. Constraints unchanged (10 ns / 2 ns). Failures preserved.
- VERIFIED: router DRC 0; academic KLayout public-deck DRC 0 on earlier GDS; official public LVS PASS historical; live deck hashes match pins/acceptance.
- BLOCKED: foundry signoff (no licensed Calibre/IC Validator/Assura, no license file, no foundry deck/runset/calibration/authority). Academic results are not signoff.
- NOT_TESTED: candidate-B stage-14/15 DRC/LVS, antenna, IR/EM, post-fill, QRC/StarRC, macro flow.

## 9. Fresh-environment readiness
- Setup path is documented (Python 3.10.11 venv, pytest 9.2.0, WSL Ubuntu-24.04, Yosys/OpenSTA/OpenROAD/KLayout, sky130hd libs/decks, ORFS fallback config).
- Fresh-environment readiness is **PARTIALLY_VERIFIED**: one-host reproduction strong; other hosts NOT_TESTED; hidden state includes WSL tool availability, dirty ORFS Liberty normalization and KLayout Qt5 libraries.
- F1 defect found and corrected: `SETUP.md` documented a raw preflight invocation with `project_config.template.json` that fails on placeholder `top.v`/`top.sdc`; the supported `python -m assistant preflight` form passes.

## 10. External evaluation readiness
- VERIFIED: limitations and unsupported capabilities explicit; failures preserved; simulated vs real EDA and academic vs signoff separated; reviewer checklist present but 8/9 unchecked (D-05).
- Documentation defects D-01..D-04 were confirmed and **corrected** in this audit: stale "publishes directly to origin/main", stale branch name, "38 paths" vs 42, "284 tests" vs 304/324.
- No unimplemented capability is claimed; no numerical rating assigned.

## 11. Confirmed defects and disposition
| ID | Severity | Defect | Disposition |
|---|---|---|---|
| D-01 | S3 | README claimed this pass publishes directly to `origin/main` | CORRECTED — now states local/uncommitted, publication not authorized |
| D-02 | S3 | README branch banner `feature/F-01-claude-agent-discovery` | CORRECTED — `main` |
| D-03 | S3 | final-validation-report said 38 paths vs actual 42 | CORRECTED — 42 with audit additions |
| D-04 | S3 | final-validation-report SR-09 "284 tests" | CORRECTED — 304 at finalization / 324 current integrated suite |
| F1 | S3 | SETUP documented template-config preflight command fails | CORRECTED — supported CLI preflight documented, template noted as advanced/requires filled config |
| A-1 | S3 | Supervised CLI gate-record trusts caller-supplied PASS | DOCUMENTED QUALIFICATION; not fixed (trust model, no autonomous false PASS) |
| A-2..A-5 | S3 | Fail-closed boundary/reporting quirks | DOCUMENTED; no fix required |
| B-D1 | S3 | Historical experiment manifest_valid FAIL / stage not-executed metadata | PRESERVED as historical evidence; no fix |
| B-D2 | S3 | Historical "Signoff DRC" terminology residual | PRESERVED and already adjudicated; academic only |

No S1 or S2 false-PASS defect remained after the audit's minimal corrections.

## 12. Risk register
| Risk | Severity | Mitigation / status |
|---|---|---|
| Foundry signoff mislabelled as PASS | S1 claim-integrity | Explicit BLOCKED; academic-only labels; documentation verified |
| soc_ctrl timing failure hidden or softened | S2 | Failures preserved in evidence and docs; no constraint relaxation |
| Fresh environment cannot reproduce | S2 | One-host VERIFIED; other hosts NOT_TESTED; setup/fallback documented |
| Simulation-heavy test count overinterpreted | S3 | Labels and this report explicitly distinguish simulated from real EDA |
| Publication before authorization | S1 process | No commit/push; CURRENT_TASK and HANDOFF record authorization withheld |
| Hidden ORFS/PDK host state | S2 | pins/portable_config + preflight hash checks; other hosts NOT_TESTED |

## 13. Evidence paths
- Lead: `D:\audit\independent_qualification_20261118\lead\` (F1 probes, patch, post-doc-fix pytest).
- Auditor A: `...\auditorA\AUDITOR_A_REPORT.md`, probes.
- Auditor B: `...\auditorB\AUDITOR_B_REPORT.md`, primary artifact re-hashes.
- Auditor C: `...\auditorC\AUDITOR_C_REPORT.md`, pytest/framework/preflight/baseline outputs.
- Auditor D: `...\auditorD\AUDITOR_D_REPORT.md`, documentation defect evidence.
- Isolated copy: `D:\audit\independent_qualification_20261118\isolated_worktree\repo`.
- Source prior evidence: `D:\audit\second_review_evidence_20261118\`, `D:\audit\m1g_evidence_20261117\`.

## 14. Remaining blockers
1. Foundry signoff BLOCKED — licensed tool + foundry deck/runset/calibration/authority required.
2. soc_ctrl routed SS timing FAIL — commercial/timing-driven mapper, enriched library, approved architecture change beyond 4-bit adder, or approved constraint change required.
3. Publication not authorized — 43 local paths (42 plus this report), staged diff empty, remote unchanged.
4. Other-host portability NOT_TESTED.
5. Candidate-B stage-14/15 and macro flow NOT_TESTED/NOT_APPLICABLE.

## 15. Recommended next steps
1. Preserve this report and the audit evidence; do not publish without explicit authorization.
2. If authorized to publish, commit the local paths as a direct fast-forward with no rebase/squash, keeping all failures and limitations in the commit message and README matrix.
3. For genuine closure: obtain authorization/resources for commercial synthesis or enriched library; run routed STA; then run signoff-grade DRC/LVS with a foundry deck.
4. For external evaluators: brief them that the demonstrated baseline belongs to `6c4a264` and current worktree `0a9eb57`; tests are mostly simulated detection contracts; foundry signoff and soc_ctrl timing are BLOCKED.

## 16. Final classification
| Major finding | Classification |
|---|---|
| Framework integrity / false-PASS controls | VERIFIED (S3 qualifications documented) |
| Reproducibility on validated host | VERIFIED |
| Fresh-environment readiness | PARTIALLY_VERIFIED |
| Benchmark credibility | PARTIALLY_VERIFIED |
| Physical evidence (academic) | VERIFIED |
| Foundry signoff | BLOCKED |
| soc_ctrl routed timing closure | BLOCKED (current best FAIL) |
| Macro readiness | VERIFIED / NOT_APPLICABLE |
| External evaluation documentation | VERIFIED after corrections |
| Publication | BLOCKED (authorization withheld) |
| Other hosts / candidate-B signoff stages | NOT_TESTED |

**Final verdict: READY WITH LIMITATIONS.** No numerical quality rating is assigned.
