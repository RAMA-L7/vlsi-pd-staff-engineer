# Next-Phase Technical Report — Evidence-Driven Investigation of Remaining Blockers

**Session:** `tech-lead-20261118T150226Z`
**Start:** 2026-11-18T15:02:26Z / 2026-11-18T20:32:26+05:30 IST
**Stop:** 2026-11-18T15:25:04Z / 2026-11-18T20:55:04+05:30 IST
**Worktree:** `D:\audit\m1g_mapping_20261117`
**Baseline:** HEAD `0a9eb57e38b86acc51b8bcb6dea53bd97ab90e9b`, tree `ab0bcf96d00e99a1b1a6cd46f41fde159661791b`
**Final state:** 41 local uncommitted paths, staged diff empty, remote `origin/main` `0a9eb57…` unchanged, `D:\audit\repo` untouched (`41728cd…`, 34 changes). **No commit, push, PR, merge, install or publication.**

## 1. Executive summary
Five orchestrated workstreams investigated every feasible remaining blocker. Two produced implemented improvements (ORFS portability configuration, 13 framework robustness tests); three produced evidence-backed classifications without claiming closure (soc_ctrl timing/mapping, physical signoff, macro readiness).

- **Full suite after integration: 324 passed** (304 prior + 13 robustness + 7 ORFS tests).
- **Framework regression: 47 PASS / 0 FAIL / 0 SKIP.**
- **Preflight: PASS**, 22 platform collateral hashes MATCH, no missing mandatory/optional dependencies.
- **Real baseline without manual ORFS overrides: PASS**, 16/16 stages, netlist hashes identical to authoritative r3/r4.
- **soc_ctrl routed SS timing remains BLOCKED**; best measured result still negative (−0.852429 ns WNS, −0.920336 ns TNS, 2 violators at approved 10 ns/2 ns).
- **Foundry signoff remains BLOCKED**; academic KLayout results are not signoff.
- **NO-MACROS is VERIFIED / NOT_APPLICABLE** with evidence.

No BLOCKED, INCONCLUSIVE or NOT_TESTED item was converted to PASS without evidence.

## 2. Workstream results and classifications

### WS1 — soc_ctrl timing and mapping — PARTIALLY_VERIFIED
- **Baseline re-verified:** golden RTL `2a7e028d…`, current SDC `ab66de1e…` (approved SR-07 clk fix), pre-route SS control −1.018758 ns exactly. **VERIFIED**
- `abc9 -liberty` unavailable in Yosys 0.69 and 0.33 (syntax error). **BLOCKED**
- `abc -constr` with approved SDC: unrecognized SDC tokens; netlist equals prior variant `0517964d…`. **VERIFIED negative result**
- WSL Yosys 0.33 vs 0.69: different text SHA `7d31dc61…`, structurally equivalent, same cells/area/timing. **VERIFIED**
- New high-drive forced `maj3_4` lever: mapped SHA `82d8874d…`, 20 cells, 285.2736 µm², depth 4, pre-route SS −0.787722 ns (+0.231 ns real gain). Routed SS −1.697260 / −2.417851, 2 violators. **VERIFIED improvement, still FAIL**
- New routed `maj3_2`: SS **−0.852429 / −0.920336**, 2 violators — best routed result, still FAIL. **VERIFIED**
- Routed no-maj3: mapped LEC 6/10 partitions unproven (`clkinv`/`lpflow_isobufsrc`); failure preserved. **BLOCKED**
- Overall SS closure: **BLOCKED** — requires commercial/timing-driven mapper, enriched library, approved architecture change beyond 4-bit adder, or approved constraint change.

**Root cause:** deterministic `synth`→ABC majority-ripple canonicalization; no SDC-aware ABC path; slow Sky130 HD `maj3` at SS (maj3_2 1.548 ns, maj3_4 1.766 ns, maj3_1 1.871 ns per cell); routed SPEF + ~1.1 ns propagated clock and 2 ns output delays. Pre-route optimism is not closure.

### WS2 — ORFS platform portability — VERIFIED (this host)
**Implemented changes (local, uncommitted):**
- `assistant/pins.json`: new `portable_config` block — host fallbacks for ORFS/HD/HS/lib dir, 22 collateral files with SHA-256 and `required` flags, override precedence env → API → fallback → default.
- `assistant/tools.py`: `load_pins`, `validate_portable_config`, `apply_portable_fallbacks`, `resolve_platform_config`, `validate_platform_collateral`.
- `assistant/baseline.py`: resolves config once, passes to provenance, injects resolved overrides into runner env only when caller value empty.
- `regression/checks/stage00_preflight.py`: 22 collateral checks, schema `30.stage00.3`, `dependencies.platform`.
- `docs/SETUP.md` §3A, `docs/REGRESSION.md` lines 16–21, `tests/test_foundation.py`, `tests/test_baseline.py` (7 new tests).

**Verification:** preflight BEFORE PASS with zero collateral checks; AFTER (overrides cleared) PASS with 22 MATCH checks and `portable_fallback_applied=[orfs_dir,platform_hd,platform_hs]`; negative control `REG_PLATFORM_SKY130HS=bogus` → exit 2, FAIL, missing RC rules. Real baseline without overrides → PASS, 16/16, netlists `3148c5c3…`/`7120e545…` identical to r3/r4. Targeted tests 44 passed; CLI sanity 26 passed. **Other hosts: NOT_TESTED.**

### WS3 — Physical signoff readiness — BLOCKED (external)
- Router DRC 0, academic KLayout public-deck DRC 0, historical public LVS PASS, live deck hashes matching pins/acceptance: **VERIFIED** (academic/router only).
- Foundry signoff: no licensed Calibre/IC Validator/Assura, no `LM_LICENSE_FILE`, no foundry deck/runset/calibration/signoff authority. **BLOCKED**
- Candidate-B stage-14/stage-15: not executed. **NOT_TESTED**
- Antenna/IR/EM/post-fill/QRC/StarRC: unsupported. **NOT_APPLICABLE to current academic scope**
- Academic results must never aggregate into foundry PASS (prior terminology overstatement already adjudicated).

### WS4 — Macro readiness — VERIFIED / NOT_APPLICABLE
- `python -m assistant info` reports `NA-RULE:NO-MACROS`, rule at `controller.py:84`, fact `has_macros=False` at `:95`.
- Both example RTLs behavioral, zero macro instances; mapped netlist contains only `sky130_fd_sc_hd__*` standard cells; no macro abstract views in the flow; floorplan/placement scripts place only standard cells.
- Documentation consistent. Macro support would require an authorized macro design, pinned macro LEF/Liberty, executable macro placement stage, and macro-aware DRC/LVS. **NO-MACROS VERIFIED / NOT_APPLICABLE.**

### WS5 — Framework robustness — VERIFIED
- 12 targeted existing suites: 242 passed (authoritative re-run).
- New `tests/test_next_phase_robustness.py`: 13 tests, all labelled SIMULATED, covering artifact corruption after recording, pinned dependency mutation, persisted-state mutation, waiver expiry boundary, resume with modified stage script, ECO stray-file rollback, CLI malformed state/corrupted artifact, and `can_enter_stage` failure propagation. **13 passed.**
- Combined targeted + new: 255 passed. No production defects found.
- Advisory: `EcoManager.snapshot_workspace()` hashes files only; an empty directory left by an ECO could survive rollback unreported — **NOT_TESTED** (no data-integrity impact).

## 3. Fixes implemented
1. ORFS portable configuration and platform-collateral validation (WS2), including preflight expansion and 7 new tests.
2. 13 adversarial robustness tests (WS5), no production code changed.
No other production files were changed by this phase; WS1/WS3/WS4 were read-only/experiment-only.

## 4. Validation results and before/after metrics

| Check | Before this phase | After this phase |
|---|---|---|
| Full pytest | 304 passed | **324 passed** |
| Framework | 47/0/0 | **47/0/0** |
| Preflight collateral checks | 0 platform checks | **22 MATCH, 0 missing** |
| Manual ORFS overrides for baseline | required | **not required (validated fallback)** |
| Real baseline | PASS with overrides | **PASS without overrides** |
| Best routed soc_ctrl SS WNS | −1.832469 ns (cand B) | **−0.852429 ns (maj3_2)** — still FAIL |
| Foundry signoff | BLOCKED | **BLOCKED (unchanged)** |

## 5. Evidence paths
- Consolidated: `D:\audit\next_phase_evidence_20261118\integration\` (FULL_SUITE_AFTER_WORKSTREAMS.txt, FINAL_FRAMEWORK.txt, FINAL_PREFLIGHT.json).
- WS1: `...\timing_mapping\reports\WORKSTREAM1_FINAL_REPORT.md` (7,718 evidence files preserved).
- WS2: `...\orfs\WORKSTREAM2_REPORT.md`, `owned_files_diff_vs_HEAD.patch`, `baseline_after\`.
- WS3: `...\signoff\SIGNOFF_READINESS_REPORT.md`, `PROBE_LOG.md`.
- WS4: `...\macro\MACRO_READINESS_REPORT.md`, `assistant_info_raw.txt`.
- WS5: `...\robustness\REPORT.md`, `combined_targeted_plus_new.txt`, `tests\test_next_phase_robustness.py` in worktree.

## 6. Remaining blockers / risk assessment
- **S1 process:** publication not authorized (41 local paths). No commit/push/PR.
- **S2:** soc_ctrl SS timing/mapping closure BLOCKED; failing evidence preserved; no constraint relaxation or path exclusion.
- **S2:** foundry signoff BLOCKED (licensed tool/deck/authority absent).
- **S3:** macro example NO-MACROS (NOT_APPLICABLE); other hosts NOT_TESTED for ORFS fallback.
- **Risk:** academic DRC must not be mislabelled foundry signoff; pre-route timing estimates must not be treated as routed closure; preserved failures must not be silently fixed.

## 7. Recommended next phase
1. If authorized: review and publish the 41 local paths (ORFS portability + robustness tests + prior second-review fixes) as a direct fast-forward commit, without touching preserved soc_ctrl failures or signoff claims.
2. For actual timing closure, obtain authorization for commercial/timing-driven synthesis or an enriched cell library; no open-tool mapping lever remains untested that is likely to close at 10 ns.
3. For foundry signoff, acquire licensed tool + versioned foundry deck/runset/calibration and define signoff waiver authority; implement S1–S8 readiness preparations from WS3 as a separately authorized task.
4. Macro work requires an authorized macro design and abstract views.

## 8. Final classification
| Item | Classification |
|---|---|
| WS1 baseline and mapping experiments | VERIFIED |
| WS1 routed soc_ctrl timing closure | BLOCKED |
| WS1 `abc9 -liberty` / routed no-maj3 | BLOCKED |
| WS2 ORFS portability (this host) | VERIFIED |
| WS2 other hosts | NOT_TESTED |
| WS3 academic DRC/LVS evidence | VERIFIED |
| WS3 foundry signoff | BLOCKED |
| WS3 candidate-B stage-14/15 | NOT_TESTED |
| WS4 NO-MACROS applicability | VERIFIED / NOT_APPLICABLE |
| WS5 robustness tests and defect search | VERIFIED |
| WS5 ECO empty-directory residue | NOT_TESTED (advisory) |
| Integrated full suite / framework / preflight | VERIFIED |
| Publication | BLOCKED (authorization withheld) |

No item was assigned a numerical quality rating; that remains with the independent reviewer.
