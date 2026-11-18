# Validation Documentation — Engineering Milestones Phases 1–17

## main — Multi-Design v2.0

## Authoritative qualification matrix — this checkout (SR-10 / SR-08)

The authoritative status for this checkout is the qualification matrix in `README.md`, tied to repository commit `0a9eb57e38b86acc51b8bcb6dea53bd97ab90e9b` / tree `ab0bcf96d00e99a1b1a6cd46f41fde159661791b`, with recorded run IDs `baseline_20261116`, `baseline_20261116_r2`, `final_r3`, and `final_r4` (test_counter: 16/16 stages PASS, 95/95 checks each) and `run_pub_m1d_soc_ctrl_full` (soc_ctrl full physical run: 14/16 stages, retained STA + LVS-gate failures). All phase milestones below are historical context; where they disagree with that matrix, the matrix governs. No fresh 15-stage EDA run was performed against `0a9eb57` in this documentation pass.

**Current baseline setup WNS (SR-10; `baseline_20261116` / `_r2`):** FF `+5.7003` ns, SS `+2.6745` ns, TT `+5.4550` ns, all with 0 violators. The value `−0.6159` is **not** a setup WNS: it was a transcription error in an older `DESIGN.md` draft (it is the TT **hold** slack from `reproducibility_r1_vs_r2.json`). A negative setup WNS alongside "PASS / 0 violators" is not a valid claim. No soc_ctrl setup signoff is claimed — its full run retained an SS setup failure (WNS −2.3378 ns, 3 violated paths).

**Unsupported capabilities (do not claim):** IR drop, EM, antenna checking, UPF / low-power intent closure, post-fill extraction / timing closure, and signoff / tapeout readiness. See `README.md` ("NOT implemented in this repository") and `assistant/baseline.py::UNSUPPORTED_CAPABILITIES`.

---

### Overview

This document summarizes the engineering milestones achieved across Phases 1 through 17 of the VLSI Physical Design Staff Engineer framework. All achievements are evidence-based and independently verified via read-only tool inspection of prior run directories.

### 📜 Executive Summary

| Milestone Category | Achievement | Evidence Source |
|---|---|---|
| Agent Discovery and Runtime Validation | F-01 agent discovery and runtime validation | Phase 1 |
| LEC Command Repair | F-03 LEC command repair (generic + EQY preprocessing) | Phase 3 |
| Real OpenROAD Physical Design Execution | OpenROAD flow with multi-corner STA, DRC, LVS | Phases 10–13 |
| Executable Synthesis and PD Skills | Yosys + OpenSTA + OpenROAD scripts functional | Phases 6–13 |
| KLayout 0.30.13 Local Installation | Extracted under `D:\\tools\\KLayout\\` (WSL only) | Phase 12 |
| Sky130 GDSII Generation | DEF→GDSII via KLayout tech-file reader options | Phases 9–13 |
| Sky130 DRC Execution | 295 rule categories, 0 violations (15 helper regions excluded) | Phase 13 |
| Nine Official SkyWater Library Repositories Recovered | Functional models, UDP definitions, Liberty corners | Phase 12 |
| Genuine Functional Models and UDP Definitions | Sky130 TT/SS/FF Liberty models | Phase 12 |
| TT, SS and FF Liberty Integration | 3-corner STA with 0 violators at every corner | Phase 12–13 |
| Technology-Mapped LEC using EQY | EQY + official Sky130 HD preprocessing | Phase 14 |
| Genuine Sky130 LVS PASS | Official `sky130hd.lylvs`: "Congratulations! Netlists match." | Phase 13 |
| Automated Regression | 15-stage flow with acceptance checks | Phase 14 |
| 88 Acceptance Checks | All checks pass per run (both runs A & B) | Phase 14 |
| Two Reproducible Runs | Run A & B: 88/88 checks (historical), 20/20 artefacts identical | Phase 14; current baseline 95/95 |
| Twenty Identical Compared Artifacts | Bit-identical across runs after timestamp stripping | Phase 14 |
| Seven Negative Tests Detected | N1–N7 all DETECTED independently | Phase 15 |
| Multi-Design Framework Integration | `-Design` parameter; `test_counter` + `soc_ctrl` | Phase 15 |
| REPRODUCIBLE Classification Maintained for test_counter | Across Phase 14–17A | Phase 14–17A |
| Shell Failure Diagnosis and Documentation (historical, Phase 16–17A) | Code Mode runtime shell issue — resolved for the documented baseline command path | Phase 16–17A |

### 🏆 Phase-by-Phase Milestones

#### Phase 1: Agent Discovery and Runtime Validation
- **Achievement**: F-01 agent discovery and runtime validation
- **Status**: ✅ Complete
- **Details**: Nine specialist agents identified and validated for runtime execution within Code Mode environment. Agent definitions (`agents/*.md`) confirmed present and well-structured.

#### Phase 2–5: Real Engineering Execution and Executable Skill Development
- **Achievement**: Development of executable skills across synthesis, floorplan, placement, CTS, routing, signoff, LEC, low-power, and forensic debugging domains
- **Status**: ✅ Complete
- **Details**: All 10 engineering skills created and verified. Skill procedures (`skills/*/SKILL.md`) follow rigid discipline with evidence-over-claims principles.

#### Phase 6–9: Signoff Investigation, KLayout Recovery and GDSII Generation
- **Achievement**: KLayout 0.30.13 extraction, DEF→GDSII with tech-file reader options, Sky130 GDSII generation
- **Status**: ✅ Complete
- **Details**: KLayout extracted under `D:\\tools\\KLayout\\` (0.30.13). GDSII generation via KLayout DEF→GDSII with via cut layers and net-name labels. 8 standard cells merged with genuine geometry. 538 via cut shapes present. 382 shapes on routing layers. 15 real top-level port labels preserved.

#### Phase 10–11: Sky130 DRC Execution and PDK Recovery
- **Achievement**: Sky130 DRC execution (295 rule categories, 0 violations); PDK recovery
- **Status**: ✅ Complete
- **Details**: DRC deck `sky130hd.lydrc` (64,758 B) evaluated 295 rule categories. 0 actual violations (15 helper regions excluded). Nine official SkyWater library repositories recovered with functional models, Liberty corners (TT/SS/FF), LEF definitions, and UDP definitions.

#### Phase 12: Official PDK Recovery, Functional Models and Multi-Corner Liberty and DRC Report Persistence
- **Achievement**: Sky130 PDK fully recovered; functional models; UDP definitions; multi-corner Liberty integration; DRC report persistence
- **Status**: ✅ Complete
- **Details**: SkyWater PDK libraries under `D:\\tools\\pdk\\` fully operational. TT/SS/FF Liberty corners available. DBC report persistence verified (parent directory must exist before `ReportDatabase::save`).

#### Phase 13: Power-Grid Integration, LVS PASS, DRC PASS and Technology-Mapped LEC PASS
- **Achievement**: Power-grid integration (PDN-0185 corrected); LVS PASS; DRC PASS; technology-mapped LEC PASS; multi-corner STA PASS
- **Status**: ✅ Complete
- **Details**:
  - PDN-0185: PDN grid geometry correction (66 well ties on 50-site grid, 23 um, bottom row only, first tie)
  - LVS: Official `sky130hd.lylvs` → "Congratulations! Netlists match." on PDN-corrected design
  - DRC: 295 rule categories, 0 violations (15 helper regions excluded from violation count)
  - Technology-mapped LEC: PASS with EQY + official Sky130 HD preprocessing
  - Multi-corner STA: TT/SS/FF, 0 violators at every corner

#### Phase 14: Automated Regression, 88 Acceptance Checks, Two Reproducible Runs and Seven Negative Tests
- **Achievement**: Complete 15-stage automated regression; 88/88 acceptance checks (Phase 14 historical; current baseline 95/95) per run; two independent runs (A & B) OVERALL PASS; 20/20 artefacts identical; seven negative tests (N1–N7) all DETECTED
- **Status**: ✅ Complete
- **Details**:
  - REPRODUCIBLE classification: Both runs A&B OVERALL PASS
  - 88/88 acceptance checks pass per run (Phase 14 historical); current baseline 95/95
  - 20/20 artefacts compared: bit-identical
  - GDSII geometry identical after timestamp stripping
  - N7 negative test: net swap injected into `15a_lvs_refcdl.tcl`; LVS reports mismatch (DETECTED) ✅
  - Negative test suite: all 7 DETECTED independently
  - Evidence: `D:\\audit\\phase14_automated_regression_20261112\\evidence\\`

#### Phase 15: Multi-Design Framework Integration, soc_ctrl Configuration, Design-Aware Execution Structure and Repository Productization
- **Achievement**: `-Design` parameter added to `run_regression.ps1`; `$DesignConfig` hashtable; soc_ctrl RTL + SDC created; design-aware stage scripts; N7 fix (target `15a_lvs_refcdl.tcl`); all 9 agents/10 skills integrated; REPRODUCIBLE classification maintained for test_counter
- **Status**: ✅ Complete
- **Details**:
  - `-Design` parameter on `run_regression.ps1` (test_counter, soc_ctrl)
  - `$DesignConfig` hashtable mapping design names to RTL/SDC paths and artifact prefixes
  - `01_synthesis.ps1` design-aware via `$Top = $env:REG_DESIGN`
- soc_ctrl RTL: canonical `examples/soc_ctrl/rtl/soc_ctrl.v` tracked at reviewed commit `0a9eb57` and in the current worktree (absent only from the earlier `27b5d1c` snapshot)
- soc_ctrl SDC: canonical `examples/soc_ctrl/rtl/soc_ctrl.sdc` tracked at reviewed commit `0a9eb57` and in the current worktree (absent only from the earlier `27b5d1c` snapshot)
  - All 9 agents integrated with design-aware paths
  - All 10 skills configured for both designs
  - REPRODUCIBLE classification maintained for test_counter
  - N7 fix verified: net swap injected into `15a_lvs_refcdl.tcl`; LVS mismatch DETECTED
  - Evidence: `the in-repo milestone reports `docs/reviews/MILESTONE_1D_REPORT.md` and `docs/reviews/MILESTONE_1G_REPORT.md``

#### Phase 16: Shell Failure Diagnosis and Documentation; Framework Modifications Documented; GitHub Publication Procedures Prepared
- **Achievement**: Shell failure fully diagnosed (historical Code Mode runtime shell integration issue, Phase 14–17A; no longer applies to the documented baseline command path); all framework modifications documented; GitHub publication procedures prepared; 7 reports created
- **Status**: ✅ Complete
- **Details**:
  - Primary blocker (historical, Phase 16–17A): Code Mode runtime shell integration issue (exit code 1, no output); resolved for the documented baseline command path
  - Diagnosed across PHASE16_SHELL_DIAGNOSIS.md files (4 files in phase17a_execution_recovery_20261114)\n  - Forensic investigation; root cause identified
  - Framework modifications documented (all via read/write/edit tools)
  - GitHub publication procedures prepared (historically not executable at the time; publication later completed at 6c4a264)
  - Evidence catalog complete across Phases 14–16
  - 7 Phase 14–16 reports created

#### Phase 17: Execution Plan Created; Repository Documentation Created (README, ARCHITECTURE.md, REGRESSION.md, VALIDATION.md, DESIGN.md)
- **Achievement**: Execution plan created; professional repository documentation framework
- **Status**: ✅ Complete
- **Details**:
  - Execution plan: Phase 17_EXECUTION_PLAN.md
  - README: rewritten professional edition
  - ARCHITECTURE.md: agent orchestration, skill execution, EDA tool integration
  - REGRESSION.md: run_regression.ps1, design selection, stage execution, acceptance checks
  - VALIDATION.md: engineering milestones Phases 1–17
  - DESIGN.md: test_counter and soc_ctrl documentation

#### Phase 17A (historical): Execution Environment Forensic Investigation and Recovery (Root Cause: Code Mode Runtime Shell Integration Issue)
- **Achievement**: Root cause identified; forensic investigation completed; 4 recovery report files
- **Status**: ✅ Complete
- **Details**:
  - Root cause (historical, Phase 14–17A): Code Mode runtime shell integration issue; no longer applies to the documented baseline command path
  - At the time, shell commands returned exit code 1 with no stdout/stderr
  - At the time, user-level environment modifications could not resolve it; a Code Mode-level fix was assumed necessary
  - 4 forensic report files created documenting investigation

#### Phase 17B: Professional Repository Documentation and GitHub Release Preparation
- **Achievement**: Professional README rewritten; ARCHITECTURE.md created; all 9 agents documented; all 10 skills documented; REGRESSION.md created; VALIDATION.md created; DESIGN.md created; .gitignore review; GitHub publication preparation
- **Status**: ✅ COMPLETE (2026-11-18 final reconciliation)
- **Details** (this phase):
  - README.md: Professional rewrite with project introduction, engineering objectives, key capabilities, architecture overview, 9 agents, 10 skills, supported EDA tools, Sky130 HD technology integration, regression architecture, supported designs, quick-start instructions, configuration instructions, validation status, known limitations, roadmap
  - ARCHITECTURE.md: Full architecture documentation with Mermaid diagrams; 9 agents with responsibilities; 10 skills with execution procedures; EDA tool integration; design configuration; regression execution; acceptance engine; machine-readable reporting; evidence generation; error handling
  - REGRESSION.md: run_regression.ps1 documentation; -Design parameter; test_counter and soc_ctrl configuration; 15 stages with acceptance checks; JSON reporting; artifact organization; negative tests; failure classification; reproducibility; clear distinction between configured capabilities and executed validation
  - VALIDATION.md: Engineering milestones Phases 1–17; evidence-based status labels; historical shell limitation disclosed (resolved for baseline path); Do NOT claim soc_ctrl full-flow validation
  - DESIGN.md: test_counter (validated baseline) and soc_ctrl (full run executed, failed qualification) documented separately; RTL provenance; SDC; regression integration; stage results; distinguish locally created RTL and SDC from upstream source material

### 📋 Validation Status Summary

#### Framework Configuration Status
| Component | Status | Notes |
|---|---|---|
| **9 Specialist Agents** | ✅ Documented and configured | All agent definitions in `agents/` unchanged |
| **10 Engineering Skills** | ✅ Documented and configured | All skill files in `skills/` unchanged |
| **15-Stage Regression Flow** | ✅ Scripts configured | `run_regression.ps1` with `-Design` parameter |
| **Design Selection** | ✅ `-Design` parameter operational | test_counter, soc_ctrl |
| **Sky130 PDK Integration** | ✅ Available and verified | Functional models, Liberty corners, LEF, GDSII cell library |
| **EDA Tool Paths** | ✅ Documented (EDA_TOOL_REGISTRY.md) | Most tools under `D:\\tools\\`; WSL-dependent noted |
| **Reproducible Baseline** | ✅ REPRODUCIBLE (Phase 14) | Two runs A&B; 88/88 checks (Phase 14 historical; current baseline 95/95); 20/20 artefacts identical |
| **Negative Test Suite** | ✅ Configured and verified | N1–N7 all DETECTED; N7 fix in `15a_lvs_refcdl.tcl` |
| **soc_ctrl Integration** | ✅ Framework configured; full run executed, failed qualification | RTL + SDC tracked at `0a9eb57`; Milestone 1D full run `run_pub_m1d_soc_ctrl_full` (14/16 stages, retained STA + LVS-gate FAIL) |
| **Shell Execution Blocker (historical)** | ⚠️ Resolved for baseline | Historically reported Code Mode runtime issue (Phase 14–17A); documented baseline command now executes 16/16 stages PASS |

#### Execution Status
| Design | Regression Status | Evidence |
|---|---|---|
| **test_counter** | REPRODUCIBLE baseline (Phase 14 + 2026-11-16) | Two runs A&B OVERALL PASS; 88/88 checks (Phase 14 historical); 20/20 artefacts identical; 2026-11-16 baseline command 16/16 stages PASS, 92 stage + 3 manifest = 95 checks |
| **soc_ctrl** | Full run executed (Milestone 1D, `run_pub_m1d_soc_ctrl_full`); failed qualification | 14/16 stages; mapped LEC 8/8, DRC 0 violations; STA FAIL (SS WNS −2.3378 ns, 3 paths) and LVS-gate FAIL retained; no signoff |

#### Current Execution Status
- **Shell tool**: functional for the documented baseline command path (the reported Code Mode issue is a historical Phase 14–17A record)
- **Live regression execution**: test_counter baseline executed (`baseline_20261116` / `_r2`) — 16/16 stages PASS, 95 checks (92 stage + 3 manifest), reproduced twice; soc_ctrl full physical run executed in Milestone 1D (`run_pub_m1d_soc_ctrl_full`; 14/16 stages, STA/LVS FAIL retained), no fresh run against `0a9eb57`
- **Git operations**: available (local validation edits uncommitted; implementation historical published ancestor 6c4a264; current origin/main is 0a9eb57)
- **DRC/LVS/STA verification**: executed for test_counter as baseline stages (results in `docs/reviews/post-publication-validation.md`)
- **GDSII generation**: executed for test_counter as a baseline stage
- **Negative test suite**: framework script passes 47/0/0; unit and failure-injection suites pass
- **Remote GitHub publication**: historical published ancestor 6c4a264; current origin/main 0a9eb57; no new publication in this validation pass

### 🔍 Shell Execution Blocker — Historical Disclosure (Phases 14–17A)

> **Status (2026-11-16):** the blocker below is a historical record. It no longer applies to the documented baseline command path, which executes the full test_counter regression (16/16 stages PASS, 95 checks = 92 stage + 3 manifest). See `docs/reviews/post-publication-validation.md`.

**Issue (historical, Phase 14–17A)**: Code Mode runtime shell integration issue — reported from Phase 14 onward; resolved for the documented baseline command path

**Symptoms (historical, at the time)**:
- Shell commands returned exit code 1 with no stdout/stderr output
- Affected: regression execution, git operations, WSL commands, negative test suite
- Was the primary blocker for live execution and GitHub publication at the time

**Root Cause (historical)**: Code Mode runtime-level shell integration issue as reported at the time; no longer applies to the documented baseline command path

**Workaround**: All Phase 17B work completed via `read`/`write`/`edit` tools; all evidence preserved; framework configuration verified via file inspection

**Impact (historical, at the time):**
- Live regression execution was reported blocked on both designs
- DRC, LVS, multi-corner STA were not live-executed in Phase 17B (true for that dated phase only; later baseline runs executed them for test_counter, and the Milestone 1D soc_ctrl run executed STA/DRC/LVS with retained failures — see authoritative matrix above)
- GDSII generation and verification were reported blocked
- Git operations were reported blocked
- Negative test suite execution was reported blocked
- Remote GitHub publication was reported blocked

**Current (2026-11-16 validation):** the documented baseline command executes the full test_counter regression (16/16 stages PASS, 95 checks = 92 stage + 3 manifest, byte-identical netlists) and live LEC/STA gates pass; the historical blocker no longer applies. soc_ctrl's full physical run was executed in Milestone 1D (`run_pub_m1d_soc_ctrl_full`, 2026-11-17) and failed qualification (STA/LVS failures retained); no fresh run was performed against `0a9eb57` in this documentation pass.

**Recovery actions (historical plan; baseline recovery achieved):**
1. `powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design test_counter` → verify REPRODUCIBLE baseline
2. `powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design soc_ctrl` → verify all 15 stages
3. Git commit and push to remote: `# Publication is recorded in the external PUBLICATION_REPORT.md; verify origin/main after authorized push`
4. Verify `origin/main` after authorized publication (see PUBLICATION_REPORT.md)
5. Update central project status with all phases (1–17)

### 📄 License

MIT — See LICENSE file in repository root.

### 📧 Contact & Support

This framework is maintained as part of the VLSI Physical Design Staff Engineer workflow. For questions about framework configuration, documentation, or the historically reported shell execution blocker, refer to the evidence documents under `D:\\audit\\phase14_automated_regression_20261112\\` or the Phase 17A forensic investigation under `D:\\audit\\phase17a_execution_recovery_20261114\\`.

**Historical shell blocker note**: a Code Mode runtime issue was reported in Phases 14–17A; it no longer affects the documented baseline command path, which executes 16/16 stages PASS on test_counter.

---\n*Validation Documentation — main — Multi-Design v2.0*\n*Generated: 2026-11-14 — All milestones evidence-based; verified via read-only file inspection across Phases 1–17A*]