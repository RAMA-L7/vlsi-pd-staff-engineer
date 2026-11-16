# Milestone 1D — Controlled Promotion and Flow Integration Report

**Date:** 2026-11-17
**Frozen reference:** `origin/main` @ `27b5d1c7a6a3328ddde559730acbb698be623b2a` — unchanged; no commits, pushes or PRs
**Controlled worktree:** `D:\audit\m1d_promotion_20261116` @ `27b5d1c` — all changes local/uncommitted, staged diff empty
**Preserved original:** `D:\audit\repo` @ `41728cd…`, 34 pre-existing changes — untouched
**Evidence:** `D:\audit\m1d_evidence_20261116\` (`rtl\`, `synthesis\`, `acceptance\`, `sdc\`, `docs\`, `verify\`, `audit\RELEASE_AUDIT.md`)
**Approved decisions enforced:** `sum = qa+qb+cin`; clock 10 ns; 7 ns rejected. No publication authorized.

## 1. Executive verdict

**Controlled promotion and flow integration: ACHIEVED. Release readiness: FAIL / BLOCKED.**

The worktree now contains the approved arithmetic correction, the `dont_use` fix, the acceptance-gate fix, the 10 ns SDC/config artifacts and the documentation corrections. Regression and mapped LEC all pass genuinely — including a full 15-stage soc_ctrl physical run performed only after mapped LEC passed. That physical run exposed two blockers: **SS-corner setup timing** and a **stage-15 LVS gate failure** traced to a hard-coded comparator top name. No fix was attempted (read-only release audit scope); no publication was performed.

| Milestone intent / criterion | Result |
|---|---|
| Start from frozen 27b5d1c, fresh controlled worktree | **PASS** |
| Approved arithmetic correction applied only in controlled worktree | **PASS** — promoted RTL SHA `2a7e028d…`; canonical `7d3d6300…` untouched |
| Synthesis `dont_use` integration | **PASS** — `01_synthesis.ps1:64` patched, hash verified |
| Acceptance false-PASS fix integrated | **PASS** — `acceptance.py` raw SHA `79314a35…`, 11 tests |
| 10 ns SDC/config integrated | **PASS (worktree only)** — SDC `2a9126e7…`; config is a **reference artifact**, not consumed by the runner, not production-applied |
| 45 documentation corrections applied | **PASS** — exactly 45; 9 non-enumerated stale strings flagged for a supplemental proposal |
| Full pytest / framework regression | **PASS** — 130 passed; 47 PASS / 0 FAIL / 0 SKIP |
| test_counter baseline | **PASS** — 16/16 stages, 92 checks |
| soc_ctrl mapped LEC (genuine, before physical) | **PASS** — EQY 8/8 both in stage-03-only and full-flow runs, negative control rejected, Sky130 HD only |
| False-PASS discipline | **PASS** — patched helper rejects 5/8 and negative control, accepts genuine 8/8 |
| soc_ctrl full physical flow | **FAIL / BLOCKED** — 14/16 stages PASS; `12_sta` and `15_lvs` FAIL |
| Repository integrity / no publication | **PASS** — remote and preserved repo unchanged |

---

## 2. Integration results (subagents 1–5)

1. **RTL promotion.** Canonical RTL copied from preserved repo into worktree and patched with the approved 4 assignments. Result SHA `2a7e028dacd97b4462d791e69e69b8ff1b2cffcf239ba31b6e968efdadb8c4be`; Yosys 0.33 `Found and reported 0 problems` (canonical: 4 undriven warnings); diff exactly 5 added / 0 removed; canonical untouched.
2. **Synthesis `dont_use`.** One-line patch applied to `regression/stages/01_synthesis.ps1`; line 64 = `abc -liberty $Lib -dont_use sky130_fd_sc_hd__lpflow_*`; byte-identity verified by reverse patch; no other file touched.
3. **Acceptance gate.** 197-line patch applied to `regression/checks/acceptance.py`; resulting raw SHA `79314a35…`; 11 tests pass — rejects 5/8, silent 7/8 and negative control, accepts genuine 8/8.
4. **SDC/config.** Canonical SDC copied with exact hash `2a9126e7…`; `regression/configs/soc_ctrl_10ns.json` added (validated: 10.0 ns, top `soc_ctrl`, SDC hash pin, 7 ns explicitly rejected). Honest scope: **reference artifact only** — runner has no reference to it; not production-applied or published. OpenSTA confirms `clk_period_ns = 10.000000`.
5. **Docs.** 45 enumerated replacements applied verbatim across 5 files (17+3+11+7+7), full diff and apply log archived; 9 non-enumerated "created" strings remain and need a supplemental proposal.

---

## 3. Independent verification (subagent 6)

- pytest **130 passed**; WSL framework **47 PASS / 0 FAIL / 0 SKIP**; test_counter baseline **16/16 stages PASS**, 92 checks.
- **soc_ctrl stages 00–03** using the integrated inputs (no overrides): all four stages PASS. EQY positive exit 0 with all 8 partitions `Successfully proved designs equivalent` / `DONE (PASS, rc=0)`; negative control exit 2; zero false-PASS markers; mapped netlist 3 cell types / 20 instances (`dfxtp_1`×8, `maj3_1`×4, `xnor2_1`×8), 0 lpflow. `report.json` inputs confirm promoted RTL `2a7e028d…` and SDC `2a9126e7…` — not test_counter.
- Because mapped LEC passed, the **full 15-stage physical flow** was run in the controlled worktree. First attempt hit an environmental `yosys exit=-1073741515` (OSS-CAD `lib/` missing from PATH); retried with corrected PATH and all 16 stages executed.

### Full-flow results (14/16 PASS)

| Stage family | Result | Evidence |
|---|---|---|
| 00–11 (preflight → extraction) | **PASS** | genuine EDA logs; GDS `c30adc7b…`, SPEF `0af3811d…`, routed DEF `3d3aa013…` |
| 12 STA | **FAIL** | SPEF loaded, TT/SS/FF present. TT WNS +4.3030 ns, FF +5.0573 ns (0 violated); **SS setup WNS −2.3378 ns, TNS −4.0984, 3 violated paths** |
| 13 GDS | **PASS** | geometry generated |
| 14 DRC | **PASS** | 295 rule-category evaluations, **0 actual violations** |
| 15 LVS | **FAIL (gate as-run)** | official KLayout `sky130hd.lylvs` verdict **PASS** (`Congratulations! Netlists match`, exit 0); secondary comparator 7 FAIL checks |
| 03 mapped LEC | **PASS again** | EQY 8/8, `DONE (PASS, rc=0)`, negative `DONE (FAIL, rc=2)` |

92 checks total: 83 PASS / 9 FAIL (2 STA + 7 LVS).

---

## 4. LVS split adjudicated (subagent 7)

The official KLayout LVS is a **genuine PASS** on the routed `soc_ctrl` netlist with the official deck and reference CDL. The stage-15 FAIL is a **false negative from a tooling defect**, not a design mismatch: `regression/checks/lvs_compare.py:39` hard-codes `TOP = 'TEST_COUNTER'`, so for `soc_ctrl` both parsed netlists report `TOP CIRCUIT MISSING` and the comparator returns early with 7 cascading FAILs. The same comparator passes test_counter (242/242/242), proving the failure tracks the design name. Non-comparable `5 vs 438` is flat-transistor versus hierarchical-master counting; `VNB|VSS` is a PDN-derived net already handled by `canon()`.

**Classification:** LVS cannot be called clean *by the gate as-run*; the comparator fix and re-run are required before release.

---

## 5. Release audit final verdict (subagent 7)

| Audit item | Result |
|---|---|
| Re-derive all hashes/verdicts/counts | **PASS** |
| soc_ctrl run identity (promoted RTL/SDC, not test_counter) | **PASS** |
| Genuine mapped LEC + acceptance patch exercised | **PASS** |
| LVS split | **BLOCKED / FAIL-as-run** (tooling false negative, fix required) |
| Intent vs release readiness | Intent **PASS**; clean-release **FAIL/BLOCKED** |
| Repository integrity | **PASS** |

### Required before publication
1. Close SS-corner setup timing (WNS ≥ 0 at 10 ns) and re-run STA.
2. Fix `lvs_compare.py` top-name handling and re-run stage 15 to a clean gate PASS.
3. Obtain explicit publication authority (none given).
4. Wire the 10 ns config through change control (currently reference-only).
5. Add a supplemental proposal for the 9 un-enumerated doc strings.
6. Fix manifest `design_hashes`/yosys-version provenance and deterministic OSS-CAD PATH.
7. Decide Windows Yosys 0.69 attestation.

---

## 6. Evidence paths

- Release audit: `D:\audit\m1d_evidence_20261116\audit\RELEASE_AUDIT.md`
- Verification: `D:\audit\m1d_evidence_20261116\verify\VERIFICATION_REPORT.md` (+ pytest/framework/baseline outputs, full-flow `report.json`, LVS logs)
- Integration reports: `D:\audit\m1d_evidence_20261116\{rtl,synthesis,acceptance,sdc,docs}\`
- Controlled worktree: `D:\audit\m1d_promotion_20261116\` (uncommitted; promoted RTL `examples/soc_ctrl/rtl/soc_ctrl.v`)
- Full-flow run workspace: `D:\audit\m1d_promotion_20261116\regression\workspace\run_m1d_soc_ctrl_full\`
