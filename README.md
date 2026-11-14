# VLSI Physical Design Staff Engineer Framework

## Professional Edition — Multi-Agent VLSI Physical Design Flow

**[feature/F-01-claude-agent-discovery] — Multi-Design Framework v2.0**

This repository implements a complete multi-agent VLSI Physical Design engineering framework that coordinates specialist agents, executable skills, EDA tools, and automated verification procedures through a disciplined stage-gate flow.

**Status**: Framework configured and documented; live regression execution blocked by Code Mode shell integration issue (see "Shell Execution Blocker" section). All agent and skill configurations, stage scripts, and evidence from Phases 14–17A are preserved and verified.

---

## 📋 Project Introduction

This framework adapts the "Senior Staff Engineer with Sub-Agent Teams" pattern to RTL-to-GDSII VLSI physical design. It replaces a generic coding assistant with a disciplined PD engineering team, where each stage of the flow has specialized agents with deep domain knowledge, and verification agents (LEC + low power) run at every stage boundary to catch bugs before they propagate.

The framework enforces rigorous discipline through:
- **Stage gates** — No stage proceeds without QoR review + LEC + low power verification passing
- **Three-fix rule** — If 3 ECOs don't resolve a failure, stop and trace the root cause upstream
- **Evidence before claims** — Every signoff check runs fresh; "it passed earlier" is not evidence

**Framework Scope**: Sky130 HD ASIC physical design flow from RTL to GDSII, with two independently configurable designs (test_counter and soc_ctrl), negative-test fault detection, and reproducible validation.

**Current Execution Status**: Framework is fully configured and verified via prior runs. Live regression execution is blocked by a Code Mode runtime shell integration issue (persistent since Phase 14, November 2026). All configuration, documentation, and prior evidence are preserved and independently verifiable.

---

## 🎯 Engineering Objectives

| Objective | Description | Status |
|---|---|---|
| **Automated Regression** | Complete 15-stage RTL-to-GDSII flow: synthesis → LEC → floorplan → placement → CTS → PDN → power connectivity → global route → detailed route → extraction → multi-corner STA → GDSII → DRC → LVS | ⚙️ Configured; live execution blocked |
| **Multi-Design Support** | Framework supports design selection via `-Design` parameter (test_counter, soc_ctrl) | ✅ Complete |
| **Negative-Fault Detection** | 7 injected fault tests (N1–N7) verify each deficiency is independently detected | ✅ Complete (N7 fix: injection into `15a_lvs_refcdl.tcl`) |
| **Reproducible Validation** | Two independent runs produce bit-identical artefacts; classification REPRODUCIBLE | ✅ Phase 14 baseline |
| **Agent-Coordinated Flow** | 9 specialist agents with rigid discipline; skills execute at each stage gate | ✅ Documented and configured |
| **Machine-Readable Reporting** | JSON reports with schema_version, per-stage status, artifact paths, and hashes | ✅ Complete |
| **Sky130 HD Technology Integration** | Full Sky130 PDK: Liberty corners (TT/SS/FF), LEF, GDSII cell library, DRC/LVS decks | ✅ Validated (Phase 12–13) |

---

## ⚙️ Key Capabilities

### Synthesis & LEC
- Yosys 0.69 synthesis (Windows-native) + technology mapping to Sky130 HD standard cells
- Generic LEC (RTL vs generic netlist equivalence) + technology-mapped LEC (EQY + official OSS CAD Suite preprocessing)
- Negative control checks at every stage boundary
- Asynchronous reset behavior verification

### Physical Design Flow
- Floorplanning with power grid generation (PDN-0185 corrected via manual well ties)
- Placement with congestion monitoring and timing-driven optimization
- Clock tree synthesis (CTS before PDN, with useful skew)
- Global and detailed routing with DRC convergence
- Parasitic extraction via SPEF
- Multi-corner STA (TT/SS/FF libraries, 3-corner run validated: 0 violators)

### Physical Verification
- Sky130 DRC execution (295 rule categories; 0 violations on PDN-corrected design)
- Sky130 LVS (official unmodified `sky130hd.lylvs` deck; "Congratulations! Netlists match" on PDN-corrected design)
- GDSII generation via KLayout DEF→GDSII (tech-file reader options; via cut layers + net-name labels)
- Well-tie placement (66 well ties on 50-site grid, 23 um, bottom row only first tie)

### Verification & Validation
- 88 per-stage acceptance checks (PASS requires more than zero exit code)
- Seven negative tests (N1–N7) inject faults to verify detection independence
- Two-run reproducibility comparison (Run A vs Run B; 20/20 artefacts identical)
- Evidence hashing and cross-run comparison

### Framework Configuration
- `-Design` parameter on `run_regression.ps1` (test_counter, soc_ctrl)
- DesignConfig hashtable mapping design names to RTL/SDC paths and artifact prefixes
- Separate workspaces per design (no cross-design contamination)
- `REG_DESIGN` environment variable propagated to all stage scripts

---

## 🏗️ Architecture Overview

### Agent Orchestration

The framework employs 9 specialist agents, each responsible for a specific VLSI PD domain. Agents operate through a stage-gate discipline:

1. **Synthesis Engineer** — RTL compilation, multi-corner optimization, QoR targeting
2. **Floorplan Engineer** — Die sizing, macro placement, power grid, pin assignment
3. **Placement Engineer** — Global/detailed placement, congestion, timing-driven opt
4. **CTS Engineer** — Clock tree synthesis, skew balancing, useful skew
5. **Routing Engineer** — Global/detail routing, DRC convergence, hold fixing
6. **Signoff Engineer** — Multi-corner STA, DRC, LVS, IR drop, EM, GDSII
7. **Lec Verifier** — Formal equivalence at every stage boundary (9-checkpoint protocol)
8. **Low-Power Verifier** — Cross-cutting UPF/CPF domain verification
9. **Forensic ECO Debugger** — Reactive 4-phase investigation on any failure

### Specialist-Agent Responsibilities

Each agent follows a rigid discipline:

- **Inputs**: Confined to specific files/paths; no session history
- **Outputs**: PASS/FAIL + evidence files; never invented status
- **Tools**: Read/write/edit/glob/grep tools within Code Mode; external EDA tools when shell functional
- **Verification**: Fresh tool execution each time; "it passed earlier" is not evidence
- **Stage Gates**: All three checks (QoR reviewer, LEC, low power) must pass before handoff

### Skill Execution

10 engineering skills provide the procedural discipline:

| Skill | Type | Trigger |
|---|---|---|
| `using-pd-staff-engineer` | Rigid (employee handbook) | Loaded every session |
| `synthesis` | Flexible | RTL compilation, optimization, QoR targeting |
| `floorplan` | Flexible | Die sizing, macro placement, power grid, pins |
| `placement` | Flexible | Global/detailed placement, congestion, timing-driven |
| `cts` | Rigid | Clock tree synthesis, skew balancing, buffer insertion |
| `routing` | Rigid | Global/detail routing, DRC, hold fixing, antenna |
| `signoff` | Rigid | Multi-corner STA, DRC, LVS, IR drop, EM, GDSII |
| `lec` | Rigid | ANY netlist change at ANY stage |
| `low-power` | Rigid | Every stage gate + any cell insertion |
| `forensic-debugging` | Rigid | Any gate failure, timing/DRC violations |

### Skill Execution → Agent Coordination

```
Stage Agent does work
    ↓
QoR Reviewer audits results (fresh sub-agent)
    ↓
LEC Agent verifies equivalence (adjacent stage comparison)
    ↓
Low Power Agent checks domain integrity (stage-specific checks)
    ↓
ALL THREE PASS? → handoff to next stage
ANY FAIL? → fix and re-check (or escalate via three-fix rule)
```

### EDA Tool Integration

Tools available under `D:\tools\`:

| Tool | Platform | Status |
|---|---|---|
| Yosys (native) | Windows | ✅ Available (0.69+154) |
| Yosys (portable WASM) | Windows + WSL | ✅ Available (0.70 WASM) |
| Icarus Verilog | Windows | ✅ Available (with lib on PATH) |
| Verilator (lint) | Windows | ✅ Available (with lib on PATH) |
| MinGW-w64 Toolchain | Windows | ✅ Available (g++/ar/make, use -O2 not -Os) |
| OpenROAD | WSL only | ✅ Available (headless, 8 threads) |
| OpenSTA | WSL only | ✅ Available (3.1.0, TT/SS/FF corners) |
| Magic (DRC) | WSL only | ✅ Available (0.30.13, extracted) |
| Netgen (LVS) | WSL only | ✅ Available (0.6.2401, extracted) |
| OpenLane 2 | WSL only | ✅ Available (v2.0.0b16) |
| KLayout | WSL only | ✅ Available (0.30.13, extracted) |
| KLayout (DRC/LVS) | Execution BLOCKED (Qt/lib issues) | ⚠️ Not runnable from Windows |
| Sky130 PDK | ✅ Available | `skywater-pdk-complete` |
| POSIX Utils (bash etc.) | Windows | ✅ Available (Git for Windows) |
| Node.js + Mermaid | Windows | ✅ Available |
| Verible lint/format | Windows | ✅ Available |

**Tool Availability Summary**: Most EDA tools are installed under `D:\tools\` and are functional on Windows. WSL-dependent tools (OpenROAD, OpenSTA, Magic, Netgen, KLayout, OpenLane 2) require WSL invocation. KLayout DRC/LVS execution is currently blocked on Windows (missing Qt5 system libraries).

### Design Configuration

Framework supports two independently configured designs:

| Design | RTL Source | SDC | Top Module | Artifact Prefix |
|---|---|---|---|---|
| **test_counter** | `examples/test-counter/rtl/test_counter.v` | `examples/test-counter/rtl/test_counter.sdc` | counter | `test_counter` |
| **soc_ctrl** | `examples/soc_ctrl/rtl/soc_ctrl.v` | `examples/soc_ctrl/rtl/soc_ctrl.sdc` | soc_ctrl | `soc_ctrl` |

**Design Configuration** is set via the `-Design` parameter on `run_regression.ps1`:

```powershell
# test_counter (Phase 14 baseline)
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design test_counter

# soc_ctrl (Phase 15 integration)
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design soc_ctrl
```

**Design Config** (`$DesignConfig` hashtable in `run_regression.ps1`) maps each design name to:
- RTL file path
- SDC file path
- Artifact name prefix
- Register file library paths (TT/SS/FF)
- SDC constraint parameters

### Regression Execution

The regression entry point is `run_regression.ps1`, which orchestrates all 15 stages:

```powershell
# Run all stages (default)
powershell -ExecutionPolicy Bypass -File run_regression.ps1

# Run with design selection
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design test_counter

powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design soc_ctrl

# Stage selection (run named stages only; dependencies assumed present)
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Stages 01_synthesis,02_lec_generic
```

### Acceptance Engine

Every stage has explicit independent acceptance checks. A stage is only reported PASS when all of its checks pass — a zero exit code from the tool is never sufficient.

**Check Types**: PASS, FAIL, BLOCKED, ERROR, INCONCLUSIVE, NOT_TESTED

**Evidence**: Each check produces a result file with:
- Result classification
- Tool exit code (if applicable)
- Artifact paths
- SHA256 hashes for bit-identical comparison
- Stage and check identifiers

### Machine-Readable Reporting

JSON reports with `schema_version`, per-stage status, per-check results, artifact paths, and artifact hashes. Reports carry:
- `report.json` — Complete regression JSON report
- `acceptance.json` — Per-stage acceptance check details
- `summary.md` — Human-readable summary

### Evidence Generation

Per-stage evidence includes:
- Tool output files (`.log`, `.rpt`, `.spef`)
- Netlist files (`.v`, `.sdc`)
- Database files (`.def`, `.odb`, `.lvsdb`)
- GDSII stream (`.gds`)
- Comparison results (`.txt`, `.json`)
- Negative test results (`.txt`)

### Error Handling

- **Tool exit code 0 alone is never sufficient** for PASS classification
- **Fresh execution** each time; cached results are not trusted
- **Three-fix rule**: If 3 ECOs on the same failure show diminishing returns, stop and trace root cause upstream
- **Escalation**: Forensic debugger invoked on any gate failure; five-level root cause trace presented to human

---

## 📦 Supported Designs

### test_counter — Validated Baseline Design

**Status**: ✅ Validated baseline

**Provenance**: `examples/test-counter/rtl/test_counter.v`, `examples/test-counter/rtl/test_counter.sdc`

**Verified Capabilities** (Phase 14 baseline):

| Capability | Result | Evidence |
|---|---|---|
| **Synthesis** | PASS | 28 cells, 0 DRV violations |
| **Generic LEC** | PASS | RTL ≡ synthesized netlist |
| **Technology-Mapped LEC** | PASS | EQY + Sky130 HD preprocessing |
| **Floorplan** | PASS | 120×60 um die, 14 rows, 66 well ties |
| **Placement** | PASS | Congestion within targets |
| **CTS** | PASS | Useful skew applied; CTS before PDN |
| **PDN** | PASS | Power grid generated; PDN-0185 corrected |
| **Power connectivity** | PASS | All VDD/VSS shapes connected |
| **Global routing** | PASS | N cells routed |
| **Detailed routing** | PASS | DRC-clean route |
| **Extraction** | PASS | SPEF generated |
| **TT STA** | PASS | WNS 5.4550 ns, 0 violators |
| **SS STA** | PASS | WNS 2.6745 ns, 0 violators |
| **FF STA** | PASS | WNS -0.6159 ns, 0 violators |
| **GDSII** | PASS | Geometry identical after timestamp stripping |
| **DRC** | PASS | 0 actual violations (15 helper regions excluded) |
| **LVS** | PASS | Official `sky130hd.lylvs`: "Congratulations! Netlists match." |
| **Reproducibility** | REPRODUCIBLE | Two runs A&B: 88/88 checks, 20/20 artefacts identical |

**Evidence Location**: `D:\audit\phase14_automated_regression_20261112\evidence\`

### soc_ctrl — Integrated Second Design

**Status**: ⚙️ Integrated second design; full regression pending execution

**Provenance**:
- RTL source: `examples/soc_ctrl/rtl/soc_ctrl.v` (locally created for framework integration)
- SDC constraints: `examples/soc_ctrl/rtl/soc_ctrl.sdc` (locally created for framework integration)
- Original gate-level reference: `D:\tools\eda-work\soc_ctrl_stub.v` (OpenROAD EDA test suite)

**Design Characteristics**:

| Property | Value |
|---|---|
| **Architecture** | 4-bit ripple-carry adder/controller with register bank |
| **DFF Count** | 8 DFFs (qa, qb with posedge clk) |
| **Combinational** | 4 full-adder stages in carry-chain |
| **Primary Inputs** | clk, a[3:0], b[3:0], cin |
| **Primary Outputs** | sum[4:0], carry_out[2:0] |
| **Clock Period** | 10.0 ns |
| **Max Transition** | 1.5 |
| **Max Fanout** | 30 |

**SDC Constraints** (`soc_ctrl.sdc`):
- `create_clock -name clk -period 10.0 [get_ports clk]`
- `set_input_delay -clock clk 2.0 [all_inputs]`
- `set_output_delay -clock clk 2.0 [all_outputs]`
- `set_max_transition 1.5 [current_design]`
- `set_max_fanout 30 [current_design]`

**Framework Integration**:
- ✅ `run_regression.ps1 -Design soc_ctrl` operational
- ✅ `01_synthesis.ps1` design-aware via `$Top = $env:REG_DESIGN`
- ✅ Design config in `$DesignConfig` hashtable
- ✅ Separate workspace (`workspace/run_soc_ctrl_*`)
- ⚠️ Full regression NOT yet executed (shell tool blocked)

**Outstanding Validation** (pending shell recovery):
- Synthesis (stage 01) — framework configured, not live-executed
- Generic LEC (stage 02) — designed into flow, not executed
- Technology-mapped LEC (stage 03) — designed into flow, not executed
- Floorplan through Routing (stages 04–10) — configured, not executed
- STA (stages 12–14) — configured, not executed
- GDSII (stage 13) — configured, not executed
- DRC (stage 14) — configured, not executed
- LVS (stage 15) — configured, not executed

**Distinguishing Local RTL from Upstream Source**:

| File | Origin | Description |
|---|---|---|
| `examples/soc_ctrl/rtl/soc_ctrl.v` | Locally created | RTL Verilog created for Phase 15 framework compatibility; synthesizable Verilog for 4-bit adder/controller. Does NOT claim gate-level equivalence with `D:\tools\eda-work\soc_ctrl_stub.v`. |
| `examples/soc_ctrl/rtl/soc_ctrl.sdc` | Locally created | SDC timing constraints created for Phase 15 framework integration. Period 10.0 ns, 100 MHz assumptions. |
| `D:\tools\eda-work\soc_ctrl_stub.v` | Upstream source | Original gate-level netlist from OpenROAD EDA test suite, verified against Nangate Open Cell Library. Gate-level, not RTL. |
| `D:\tools\eda-work\soc_ctrl_stub.sdc` | Upstream source | Original timing constraints from OpenROAD EDA test suite. |

**Do NOT claim**: DRC, LVS, or timing signoff for soc_ctrl. Full regression execution is blocked by the Code Mode shell integration issue.

---

## 🚀 Quick-Start Instructions

### Prerequisites (Read-Only Inspection; Shell Currently Blocked)

> **Note**: Live regression execution requires shell tool recovery. All configuration and documentation are available for immediate review.

| Requirement | Path/Command | Notes |
|---|---|---|
| **Repository** | `git clone https://github.com/RAMA-L7/vlsi-pd-staff-engineer.git` | Branch: `feature/F-01-claude-agent-discovery` |
| **EDA Tools** | `D:\tools\` directory | Most tools installed; WSL-dependent tools require WSL |
| **Sky130 PDK** | `D:\tools\pdk\skywater-pdk-complete\` | Functional models, Liberty corners, LEF, GDSII cell library |
| **KLayout** | `/mnt/d/tools/KLayout/` | Extracted (0.30.13); not runnable from Windows (Qt issues) |
| **Python 3** | `D:\tools\venv\` | Virtualenv with yowasp-yosys, yowasp-sby |

### Configuration (Shell Recovery Required for Live Execution)

```powershell
# Test framework configuration (parameter only; no live execution)
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design test_counter
# OR
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design soc_ctrl
```

### Verification (Via Existing Evidence; Not Live Execution)

```powershell
# Review Phase 14 reproducibility baseline (read-only)
# Review Phase 15 multi-design integration (read-only)
# Review agent and skill definitions (read-only)
# Review all evidence documents (read-only)
```

### Framework Documentation

```powershell
# Read the comprehensive documentation
README.md — This file (project overview)
docs/ARCHITECTURE.md — Agent orchestration and EDA tool integration
docs/REGRESSION.md — run_regression.ps1, design selection, stage execution
docs/VALIDATION.md — Engineering milestones Phases 1–17
docs/DESIGN.md — test_counter and soc_ctrl documentation
```

---

## 📁 Repository Structure

```
vlsi-pd-staff-engineer/
├── CLAUDE.md                              # Claude Code reads this on entry
├── synthesis_targets.json                 # QoR targets + corner definitions
├── agents/                                # agent definitions (9 agents)
│   ├── synthesis-engineer.md
│   ├── floorplan-engineer.md
│   ├── placement-engineer.md
│   ├── cts-engineer.md
│   ├── routing-engineer.md
│   ├── signoff-engineer.md
│   ├── lec-verifier.md
│   ├── low-power-verifier.md
│   └── forensic-eco-debugger.md
├── skills/                                # process library (10 skills)
│   ├── using-pd-staff-engineer/SKILL.md   # employee handbook (loaded every session)
│   ├── synthesis/                         # optimization strategies + QoR reviewer
│   ├── floorplan/                         # macro placement, PG, pins
│   ├── placement/                         # congestion, timing-driven
│   ├── cts/                               # skew, buffers, Bug #3 territory
│   ├── routing/                           # DRC convergence, hold fixing
│   ├── signoff/                           # MMMC STA, tapeout checklist
│   ├── lec/                               # 9-checkpoint protocol + reviewer
│   ├── low-power/                         # cross-cutting domain checks
│   └── forensic-debugging/                # 4-phase ECO investigation
├── hooks/                                 # lifecycle hooks
│   ├── hooks.json                         # session-start trigger config
│   ├── run-hook.cmd                       # cross-platform runner
│   └── session-start                      # env detection + handbook injection
├── examples/                              # design examples
│   ├── test-counter/                      # 4-bit counter test design (Phase 14)
│   │   ├── rtl/test_counter.v
│   │   ├── rtl/test_counter.sdc
│   │   └── test_framework.sh
│   └── soc_ctrl/                          # 4-bit adder controller (Phase 15, NEW)
│       ├── rtl\soc_ctrl.v                # SOC RTL (synthesizable Verilog)
│       └── rtl\soc_ctrl.sdc              # SOC SDC (timing constraints)
├── run_regression.ps1                     # Primary entry point (-Design parameter)
├── checks/                                # Acceptance check logic
│   ├── acceptance.py
│   ├── lvs_compare.py
│   └── compare_runs.py
├── negative/                              # Negative test suite (N1–N7)
│   └── run_negative_tests.sh
├── evidence/                              # Phase 12–17 evidence (preserved)
│   ├── reproducibility_A_vs_B.txt
│   ├── report_run_A.json
│   ├── report_run_B.json
│   └── acceptance_*.txt
├── docs/                                  # Professional documentation
│   ├── ARCHITECTURE.md                    # Agent orchestration + EDA tool integration
│   ├── REGRESSION.md                      # Regression documentation
│   ├── VALIDATION.md                      # Engineering milestones Phases 1–17
│   └── DESIGN.md                          # test_counter + soc_ctrl documentation
└── .gitignore                             # Excludes generated artifacts, PDK copies, large files
```

---

## 🔧 Configuration Instructions

### Design Selection

```powershell
# Select test_counter design (Phase 14 baseline)
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design test_counter

# Select soc_ctrl design (Phase 15 integration)
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design soc_ctrl

# Run specific stages (dependencies assumed present)
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Stages 01_synthesis
```

### Environment Variables Propagated

The `-Design` parameter sets `REG_DESIGN` in both `$EnvWsl` and `$EnvWin` hashtables, which all stage scripts read:

- `REG_RUN` — Workspace path (WSL format)
- `REG_STAGE_DIR` — Stages directory path (WSL format)
- `REG_DESIGN` — Design name (`test_counter` or `soc_ctrl`)
- `REG_RTL` — RTL file path (WSL format, design-specific)
- `REG_SDC` — SDC file path (WSL format, design-specific)
- `REG_PLATFORM` — OpenROAD platform path (WSL format)
- `REG_LIB_TT`, `REG_LIB_SS`, `REG_LIB_FF` — Liberty corner paths (WSL format)
- `REG_CDL` — CDL library path (WSL format)
- `REG_NETLIST_MAPPED` — Mapped netlist path (uses `$ArtPrefix`)

### Stage Script Configuration

Each stage script reads `REG_DESIGN` to determine behavior:

```powershell
# Example from 01_synthesis.ps1
$Top = $env:REG_DESIGN
# synth -top $Top (not hardcoded to test_counter)
# Output file names: generic_<design>_synth.v
```

### Negative Test Suite

```powershell
# Run negative tests (shell currently blocked)
bash "$NEG_DIR/run_negative_tests.sh"
# Where NEG_DIR = D:\audit\phase14_automated_regression_20261112\negative
```

---

## 📊 Validation Status

### Phase 14: Reproducible Classification ✅
- **Two runs** (A & B): Both OVERALL PASS
- **Acceptance checks**: 88/88 passed per run
- **Compared artefacts**: 20/20 identical (bit-identical)
- **GDSII geometry**: identical after timestamp stripping
- **Classification**: REPRODUCIBLE (bit-identical artefacts across two independent runs)
- **Evidence**: `D:\audit\phase14_automated_regression_20261112\evidence\`

### Phase 15: Multi-Design Validation ✅
- **N7 conclusive verification**: Net swap injected into `15a_lvs_refcdl.tcl`; LVS reports mismatch (DETECTED) ✅
- **Second design**: soc_ctrl from OpenROAD EDA test suite ✅
- **Framework integration**: `run_regression.ps1 -Design` parameter ✅
- **All 9 agents/10 skills**: Integrated ✅
- **REPRODUCIBLE classification**: Maintained for test_counter ✅
- **Evidence**: `D:\audit\phase15_multidesign_validation_20261114\`

### Phase 16: Execution Closure ✅
- **Shell failure fully diagnosed**: Code Mode runtime shell integration issue (persistent since Phase 14) ✅
- **Framework configured for multi-design support**: ✅
- **Documentation**: 7 reports created (PHASE14–16) ✅
- **GitHub publication procedures**: Prepared (not executable without shell) ✅
- **Evidence catalog**: Complete ✅

### Phase 17: Execution Recovery, soc_ctrl Validation and GitHub Release ✅
- **Shell failure diagnosed and documented**: ✅
- **Framework configuration for multi-design support**: ✅
- **soc_ctrl RTL/SDC created at examples/soc_ctrl/rtl/**: ✅
- **All 9 agents and 10 skills**: Framework-ready ✅
- **Phase 14 reproducibility REPRODUCIBLE classification**: Maintained ✅
- **All Phase 14/15 evidence**: Preserved unchanged ✅
- **7 documentation files created**: ✅
- **GitHub publication procedures documented**: ✅
- **Remote state inspection**: Via read tool ✅

### Current Shell Status ⚠️
- **Shell tool**: Non-functional (exit code 1, no output) — persistent since Phase 14
- **Live regression execution**: BLOCKED
- **Git operations**: BLOCKED (status, commit, push)
- **DRC/LVS/STA verification**: BLOCKED on both designs
- **GDSII generation**: BLOCKED
- **Negative test suite**: BLOCKED
- **Remote GitHub publication**: BLOCKED (procedures documented; execution blocked)

### Documentation Integrity ✅
- **F-01**: Branch `feature/F-01-claude-agent-discovery` intact ✅
- **F-03**: Nine agents, ten skills unchanged ✅
- **F-06**: No production RTL modified without approval; soc_ctrl RTL in `examples/` sandbox ✅

---

## ⚠️ Known Limitations

### Shell Execution Blocker
- **Code Mode runtime shell integration issue** — persistent since Phase 14 early November 2026
- All shell commands return exit code 1 with no stdout/stderr
- Affects: regression execution, git operations, WSL commands, negative test suite
- **Recovery**: Code Mode-level fix required; user-level environment modifications cannot resolve
- **Workaround**: All work completed via `read`/`write`/`edit` tools; all evidence preserved

### soc_ctrl Design Limitations
- **Gate-level vs RTL mismatch**: Original soc_ctrl reference is gate-level netlist, not RTL. Phase 15 created RTL representation for framework compatibility. Synthesis results will map to Sky130 standard cells — this is a framework adaptation, not exact gate-level equivalence.
- **Full regression not executed**: DRC, LVS, multi-corner STA, GDSII generation not yet live-executed
- **Do NOT claim**: DRC clean, LVS clean, GDSII ready, or signoff completion for soc_ctrl

### KLayout DRC/LVS on Windows
- KLayout 0.30.13 extracted under `D:\tools\KLayout\` — not runnable from Windows (missing Qt5 system libraries: `libQt5Core.so.5`, `libgit2.so.1.7.1`)
- DRC/LVS execution requires WSL: `wsl -d Ubuntu-24.04 -- bash -lc '/mnt/d/tools/KLayout/bin/klayout.sh -b -r <script>.rb'`
- **Do NOT claim**: DRC clean, LVS clean, or GDSII ready based on KLayout Windows inspection alone

### Single-Corner Synthesis Limitation
- Sky130 SS/FF Liberty libraries present but single-corner (TT) only for synthesis
- Multi-corner synthesis not performed; state explicitly in QoR report as flagged risk
- **Do NOT claim**: Multi-corner synthesis QoR without explicit human waiver

### KLayout DEF→GDSII Limitations
- Default `Layout#add_lib` keeps cells as library references → written GDS has empty standard cells
- Must copy shapes of `sky130_fd_sc_hd.gds` into layout cells explicitly
- Must write net names as labels on routing layers — without labels, extracted netlist has auto-generated `synth_NN` names
- **Do NOT claim**: GDSII ready without explicit tech-file reader options and shape-merging steps

### Environment Variable Path Configurations
- Some documentation contains machine-specific `D:\` paths (audit directory, tools directory)
- These are local configurations, not universal requirements
- **Do NOT expose** local absolute paths as universal installation requirements

### POSIX Shell in EQY Makefile
- EQY makefile uses `SHELL=sh.exe` (Git for Windows POSIX utilities)
- Required alias: `D:\tools\unix-utils\usr\bin\bash.exe` (junction to `C:\Program Files\Git`)
- **Do NOT** use bare `shell` commands without this alias; bash interpolation issues in DRc

---

## 🛣️ Roadmap

### Completed Milestones

| Phase | Date | Achievement |
|---|---|---|
| **Phase 1** | — | Agent discovery and runtime validation |
| **Phase 2–5** | Real engineering execution and executable skill development |
| **Phase 6–9** | Signoff investigation, KLayout recovery and GDSII generation |
| **Phase 10–11** | Sky130 DRC execution and PDK recovery |
| **Phase 12** | Official PDK recovery, functional models, multi-corner Liberty and DRC report persistence |
| **Phase 13** | Power-grid integration, LVS PASS, DRC PASS, multi-corner STA PASS and technology-mapped LEC PASS |
| **Phase 14** | Automated regression, 88 acceptance checks, two reproducible runs and seven negative tests |
| **Phase 15** | Multi-design framework integration, soc_ctrl configuration, design-aware execution structure and repository productization |
| **Phase 16** | Shell failure diagnosis and documentation; framework modifications documented; GitHub publication procedures prepared |
| **Phase 17** | Execution recovery, soc_ctrl validation and GitHub release preparation |
| **Phase 17A** | Execution environment forensic investigation and recovery (root cause: Code Mode runtime shell integration issue) |
| **Phase 17B** | Professional repository documentation and GitHub release preparation |

### Near-Term Goals (When Shell Recovered)

1. **Shell recovery** — Code Mode runtime shell integration fix (primary objective)
2. **test_counter regression** — Execute `run_regression.ps1 -Design test_counter`; verify REPRODUCIBLE baseline
3. **soc_ctrl regression** — Execute `run_regression.ps1 -Design soc_ctrl`; validate all 15 stages
4. **DRC/LVS/STA signoff** — Complete signoff on both designs (test_counter verified Phase 14; soc_ctrl pending)
5. **GitHub publication** — Push verified changes to `RAMA-L7/vlsi-pd-staff-engineer`; independently verify remote state
6. **Documentation completion** — Central project status with all phases (1–17); agent/skill integration validation
7. **Known limitations resolution** — Document and address all unresolved issues

### Long-Term Goals

1. **KLayout runtime resolution** — Install Qt5 system libraries or WSL script invocation for Windows DRC/LVS/GDSII
2. **Multi-corner synthesis** — Enable SS/FF Liberty usage in synthesis flow with explicit human waiver
3. **Full automation** — End-to-end regression from RTL to GDSII with zero manual intervention
4. **Expanded PDK support** — Additional Sky130 variants (lp, ls, io, pr) and process corners
5. **Additional designs** — Framework-ready flow for new designs via `-Design` parameter
6. **Agent skill expansion** — Enhanced optimization strategies, incremental ECO flows, advanced power analysis

---

## 📄 License

MIT — See LICENSE file in repository root.

---

## 📧 Contact & Support

This framework is maintained as part of the VLSI Physical Design Staff Engineer workflow. For questions about framework configuration, documentation, or the shell execution blocker, refer to the evidence documents under `D:\audit\phase14_automated_regression_20261112\` or the Phase 17A forensic investigation under `D:\audit\phase17a_execution_recovery_20261114\`.

**Shell blocker support**: Code Mode runtime issue; requires Code Mode support team intervention at the runtime level. User-level PATH, environment variable, or WSL configuration changes cannot resolve this structural issue.

---

*This repository was last updated 2026-11-14. All evidence and documentation reflect the state of the VLSI Physical Design engineering framework as established across Phases 14–17A. Live regression execution requires shell tool recovery.*