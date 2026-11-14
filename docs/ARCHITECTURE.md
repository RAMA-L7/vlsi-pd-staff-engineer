# VLSI Physical Design Framework — Architecture Documentation

## feature/F-01-claude-agent-discovery — Multi-Design v2.0

### Overview

This document describes the architecture of the VLSI Physical Design Staff Engineer framework, including agent orchestration, specialist-agent responsibilities, skill execution, EDA tool integration, design configuration, regression execution, acceptance engine, machine-readable reporting, evidence generation, and error handling.

### Agent Orchestration

The framework employs 9 specialist agents that operate through a rigorous stage-gate discipline. No stage proceeds without all three checks passing: QoR review, LEC equivalence verification, and low-power domain checks.

### Agent List and Responsibilities

| # | Agent | Primary Domain | Key Responsibilities |
|---|---|---|---|
| 1 | `synthesis-engineer` | Synthesis | RTL compilation, multi-corner optimization, QoR targeting, technology mapping to Sky130 HD standard cells, LEC pass confirmation, QoR summary table generation |
| 2 | `floorplan-engineer` | Floorplan | Die sizing, macro placement, power grid generation (PDN-0185 corrected), pin assignment, `make_tracks.tcl` before `place_pins`, `setRC.tcl` before IR-drop |
| 3 | `placement-engineer` | Placement | Global placement, detailed placement, congestion control, timing-driven legalization, cell contiguity checks |
| 4 | `cts-engineer` | CTS | Clock tree synthesis, skew balancing, useful skew, CTS before PDN precondition, `setRC.tcl` before IR-drop, clock buffer domain checks (Bug #3: switchable domain = silicon killer) |
| 5 | `routing-engineer` | Routing | Global routing, detailed routing, DRC convergence, hold fixing, antenna check preparation, density fill preparation |
| 6 | `signoff-engineer` | Signoff | Multi-corner STA (TT/SS/FF), DRC, LVS, IR drop, EM, antenna, GDSII generation, final LEC (routed vs RTL), tapeout checklist, signed checklist |
| 7 | `lec-verifier` | LEC | Formal equivalence at every stage boundary (9-checkpoint protocol); adjacent stage comparison; full-circle RTL vs routed-netlist verification; negative control; async reset verification |
| 8 | `low-power-verifier` | Low Power | UPF/CPF domain integrity checks at every stage gate; isolation cell verification; level shifter verification; retention flop verification; power state table verification; Bug #3 (clock buffer in switchable domain); Bug #6 (hold buffers in correct domains) |
| 9 | `forensic-eco-debugger` | Debugger | Reactive 4-phase investigation on any failure: Phase 1 (evidence collection), Phase 2 (root cause identification with five-level backward trace), Phase 3 (targeted fix selection), Phase 4 (verified resolution); three-fix rule escalation; does not guess or apply random fixes |

### Agent Coordination Pattern

Every physical design stage follows this rigid pattern. No exceptions.

```
STAGE AGENT does work
    ↓
QoR REVIEWER audits results (fresh sub-agent)
    ↓
LEC AGENT verifies equivalence (adjacent stage comparison)
    ↓
LOW POWER AGENT checks domain integrity (stage-specific checks)
    ↓
ALL THREE PASS? → handoff to next stage
ANY FAIL? → fix and re-check (or escalate via three-fix rule)
```

### QoR Reviewer (Implicit)

The QoR review is performed by the orchestrating session or the next agent in the pipeline. Each stage's handoff package must include:

- [ ] Verified netlist (.v) + physical database (.def)
- [ ] Updated constraints (.sdc) — propagated, not copied
- [ ] LEC report: PASS with compare point statistics
- [ ] Low power report: PASS with domain check summary
- [ ] QoR summary: WNS, TNS, area, cells, power
- [ ] Risk list: known issues for the next stage agent

Missing any item = handoff is incomplete = next stage cannot start.

### Specialist-Agent Responsibilities (Detailed)

#### `synthesis-engineer`

- **Purpose**: Synthesize RTL to gate-level netlist with multi-corner timing closure, area optimization, and power-aware compilation
- **Inputs**: RTL source files, top module name, SDC constraints, target Liberty library, Technology LEF (if physical-aware), Power intent (UPF/CPF), design constraints, MMMC corner definitions
- **Outputs**: Gate-level netlist (.v), Propagated SDC (.sdc), QoR summary table (all iterations, all corners), LEC pass confirmation, Risk list (tight paths, congestion estimates, known issues)
- **Tools**: Yosys (Windows .exe, 0.69+154), technology mapping (dfflibmap + abc), OpenSTA (embedded), EQY for technology-mapped LEC
- **Verification Requirements**: 
  - All MMMC corners meet slack targets (or explicit human waiver)
  - Area within budget
  - Power within budget
  - LEC has passed (RTL vs gate netlist)
  - Low power intent (UPF/CPF) verified structurally
  - Zero unmapped `$_*` primitives in netlist
- **Hard Gates**: 
  - Do NOT hand off a netlist until all MMMC corners meet slack targets (or explicit human waiver)
  - Area within budget
  - Power within budget
  - LEC has passed
  - Low power intent verified structurally
- **Validated Result (test_counter, 2026-11-11)**: 20 cells, zero `$_*` primitives, cell-type breakdown matches Sky130 HD library

#### `floorplan-engineer`

- **Purpose**: Die sizing, macro placement, power grid, pin assignment
- **Inputs**: Netlist from synthesis, SDC constraints, technology LEF, technology library, core area budget, row pitch, well tie requirements
- **Outputs**: Floorplan DEF, power grid configuration, pin assignment assignments, track assignments
- **Key Constraints**: 
  - `make_tracks.tcl` MUST source before `place_pins` (otherwise PPL-0021)
  - `setRC.tcl` MUST source before IR-drop (otherwise PSM-0021, zero via resistance)
  - Core size: 120 × 60 um die, 10 × 10 × 110 × 50, 14 rows
  - 66 well ties placed on 50-site grid (23 um), bottom row only first tie
  - Minimum die width: 27.14 um met4 / 27.2 um met5 for PDN straps
- **PDN-0185**: PDN grid geometry generation (Phase 13 finding); via stacks generated by OpenROAD; platform vias cannot be pinned for 4-layer jump
- **Well ties**: Functional cells contiguously ONE site behind a well tie; tap cell is one site wide; n-well overhangs 0.19 um per side; PFET bulk terminal ties to VPWR
- **Verification Requirements**: 
  - Power grid: `check_power_grid -net VDD/VSS` → "All shapes on net VDD/VSS are connected"
  - Well ties: 66 functional, correctly placed
  - Core dimensions within platform strap pitch

#### `placement-engineer`

- **Purpose**: Global/detailed placement, congestion, timing-driven optimization
- **Inputs**: Placed DEF from floorplan, standard cell library, clock netlist from CTS, timing constraints
- **Outputs**: Placed DEF, placed cell locations, congestion maps, legalized cell positions
- **Key Metrics**: 
  - Cell contiguity
  - Placement density vs. target
  - Clock net skew after placement
  - Open density calculations
- **Bug #3 Territory**: Clock buffer in switchable domain = silicon killer; ensure all CT inserted buffers are in stable domains
- **Verification Requirements**: 
  - Placed DEF valid (all cells legalized)
  - Clock tree connectivity verified from CTS output
  - Congestion within acceptable targets (phase-dependent)

#### `cts-engineer`

- **Purpose**: Clock tree synthesis, skew balancing, useful skew
- **Inputs**: Placed DEF, clock netlist, timing constraints, library (TT/SS/FF corners)
- **Outputs**: CTS DEF, clock tree netlist, useful skew report, buffer insertion report
- **Key Constraints**: 
  - **CTS BEFORE the PDN** (with the platform order PDN first, clock-tree buffers are inserted after `global_connect`, so their power pins stay on no net and the reference netlist contains `__UNCONNECTED__` nets)
  - Useful skew allowed but must be quantified and documented
  - Clock buffer insertion must respect power domains (Bug #3: clock buffer in switchable domain = silicon killer)
  - `setRC.tcl` MUST source before IR-drop
- **Verification Requirements**: 
  - Clock tree netlist valid (all sinks driven)
  - Useful skew quantified and within limits
  - No clock buffer in switchable domain
  - CTS completed before PDN generation
- **Validated Result (Phase 12/13)**: Multi-corner STA with TT/SS/FF; 0 violators at every corner; WNS setup 5.5678 ns / hold 0.5231 ns (TT), 3.7540 ns / 1.6507 ns (SS), 5.7656 ns / 0.3319 ns (FF)

#### `routing-engineer`

- **Purpose**: Global/detail routing, DRC convergence, hold fixing
- **Inputs**: Placed DEF from placement, clock netlist from CTS, timing constraints, technology LEF
- **Outputs**: Routed DEF, routed netlist, SPEF file, DRC report, hold violation fixes
- **Key Constraints**: 
  - DRC convergence required before signoff
  - Hold fixing after global route completion
  - Antenna check preparation (NOT TESTED; `check_antennas` present but not tested)
  - Metal fill preparation (NOT TESTED; `density_fill` present but not tested)
  - `make_tracks.tcl` before `place_pins` (PPL-0021)
- **DRC Report Classification**: 
  - Only deck lines carrying `.output(...)` are rules
  - Intermediate filter regions of a rule expression print edge-pair counts but are NOT violations
  - Classify before reporting; 135 rule lines evaluated, 0 violations (Phase 13 validated)
- **Verification Requirements**: 
  - Routed DEF valid (all cells with geometry, net names as labels)
  - DRC report: 0 actual violations (helper regions excluded from count)
  - Hold violations fixed or documented as risk
- **Validated Result (Phase 13)**: 295 rule categories, 0 actual violations, 15 helper regions excluded; Report database saved and reopened (145 categories, 0 items — totals agree with execution log)

#### `signoff-engineer`

- **Purpose**: Final verification gate before tapeout: multi-corner STA, parasitic extraction, DRC, LVS, IR drop, EM, antenna check, final LEC (routed vs RTL), low power verification, metal fill, GDSII generation
- **Inputs**: Routed DEF + netlist from routing, SDC constraints (propagated, not copied), LEF (441 cells), Liberty corners (TT/SS/FF), GDSII output, IR drop analysis results, EM analysis results, antenna analysis results, low power verification results, original RTL (for final LEC)
- **Outputs**: 
  - Multi-corner STA report (TT/SS/FF, 0 violators at every corner)
  - DRC report (295 rule categories, 0 violations, database saved and reopened)
  - LVS verdict (official `sky130hd.lylvs`: "Congratulations! Netlists match.")
  - IR drop analysis connectivity check
  - EM analysis results
  - Antenna check results (NOT TESTED)
  - GDSII stream-out (only after ALL checks pass)
  - Signed tapeout checklist
- **Tool Preconditions**: 
  - `setRC.tcl` sourced before IR-drop
  - `read_sdc` before activity annotation
  - `write_db` checkpoint before extraction
  - Output dirs pre-created (KLayout `ReportDatabase::save` fails with `ENOENT` on missing parent dir)
- **Validated Results (Phase 12–13)**:
  - Multi-corner STA: TT 5.5678/0.5231 ns, SS 3.7540/1.6507 ns, FF 5.7656/0.3319 ns (setup/hold WNS), 0 violators at every corner
  - DRC: 295 rule categories, 0 violations; database saved (22,400 B) and reopened (145 categories, 0 items)
  - LVS: Official `sky130hd.lylvs` → "Congratulations! Netlists match." (PDN-corrected design)
  - GDSII: DEF→GDSII via KLayout tech-file reader options; via cut layers; net-name labels
- **BLOCKED (Current)**: DRC, LVS, GDSII — KLayout absent on Windows (missing Qt5 system libs)
- **NOT BLOCKED**: Multi-corner STA (genuine Sky130 TT/SS/FF Liberty corners available; 3-corner run completes with 0 violators)
- **NOT TESTED**: Antenna check, metal fill

#### `lec-verifier`

- **Purpose**: Formal equivalence at every stage boundary; 9-checkpoint protocol; adjacent stage comparison; full-circle RTL vs routed-netlist verification
- **Inputs**: Pre-stage netlist, post-stage netlist, original RTL source, SDC constraints (propagated), top module name
- **Outputs**: 
  - LEC report: PASS/FAIL with compare point statistics
  - Negative control: correctly rejected
  - Asynchronous reset behavior verification
  - Stage comparison statistics (matched, unmatched, total)
- **9-Checkpoint Protocol**: 
  1. RTL vs gate netlist (post-synthesis)
  2. Generic vs technology-mapped (EQY + official preprocessor)
  3. Post-floorplan netlist vs synthesis netlist (should be identical)
  4. Post-placement netlist vs floorplan netlist (should be identical)
  5. Post-CTS netlist vs placed netlist (347+ new cells expected)
  6. Post-route netlist vs CTS netlist
  7. Post-route netlist vs original RTL (full-circle check — catches accumulated errors)
  8. Power domain verification (isolation cells, level shifters)
  9. Full-cycle LEC (routed vs original RTL)
- **Verification Requirements**: 
  - Fresh LEC execution each time (not from cache)
  - Negative control correctly rejects non-equivalent netlists
  - Full-circle check (check 7) catches accumulated errors across all stages
  - Net swap detection (N7 test: injection into `15a_lvs_refcdl.tcl`; LVS reports mismatch DETECTED)
- **Validated Results (Phase 14)**: 88/88 checks include LEC; both runs PASS; technology-mapped LEC PASS with EQY + official Sky130 HD preprocessor; negative control correctly rejected
- **N7 Fix Verification**: Net swap injected into `15a_lvs_refcdl.tcl`; LVS reports mismatch (DETECTED) ✅

#### `low-power-verifier`

- **Purpose**: Cross-cutting UPF/CPF domain verification; checks at every stage gate
- **Inputs**: UPF/CPF files (if present), netlist from current stage, power intent specifications, domain boundary definitions
- **Outputs**: 
  - Domain integrity report: PASS/FAIL
  - Isolation cell verification: present at every domain crossing
  - Level shifter verification: present where voltage differs
  - Retention flop verification: mapped to correct library cells
  - Power state table: complete for all legal operating modes
  - Bug #3: clock buffer in switchable domain = silicon killer
  - Bug #6: hold buffers in correct domains after route
- **Rigid Discipline**: Follow EXACTLY; do not adapt away discipline; these skills exist because shortcuts kill silicon
- **Verification Requirements**: 
  - Every power domain has exactly one primary power net and one ground net
  - Every domain crossing has isolation cells specified
  - Every domain crossing has level shifters where voltage differs
  - Retention registers specified for domains that power-gate
  - Power state table covers all legal operating modes
  - No domain is both always-on and power-gated (contradiction)
- **Validated Results (Phase 13/14)**: Low power checks run at every stage gate; power connectivity PASS (all shapes on net VDD/VSS connected); well-tie structure verified

#### `forensic-eco-debugger`

- **Purpose**: Reactive 4-phase investigation on any failure; five-level root cause trace; three-fix rule
- **Inputs**: Failure evidence (tool output, report files, log excerpts), stage context (which stage failed, what was being done)
- **Outputs**: 
  - Phase 1 (evidence collection): gathered tool outputs, report files, log excerpts, netlist paths, constraint paths
  - Phase 2 (root cause identification): five-level backward trace through PD stages; cost/risk assessment for each candidate; presented to human with options
  - Phase 3 (targeted fix selection): specific fix recommended based on root cause; cost/risk trade-off; human decides which level to fix
  - Phase 4 (verified resolution): fix applied and re-checked; all three gates (QoR, LEC, low power) must pass before proceeding; if still failing, escalate again
- **Three-Fix Rule**: If 3 targeted ECOs on the same failure show diminishing returns (200ps → 100ps → 50ps per fix), the problem is structural and upstream of where you're fixing; STOP and escalate to human
- **Do NOT**: Guess. Apply random fixes. Skip the four-phase protocol. Claim PASS without fresh evidence.
- **Validated Process (Phase 14–16)**: Root causes from Phase 13 encoded in Phase 14: PDN-0185, n-well tie (66 well ties on 50-site grid, 23 um, bottom row only first tie), structurally wrong LVS reference via `write_cdl -masters` (_unconnected_N side-effect); reference generated from routed ODB ITerm → net mapping instead

### Skill Execution

10 engineering skills provide the procedural discipline for the framework:

| Skill | Type | Trigger | Primary Discipline |
|---|---|---|---|
| `using-pd-staff-engineer` | Rigid (employee handbook) | Loaded every session | Operating manual; 1% rule; instruction priority; rigid vs flexible categories |
| `synthesis` | Flexible | RTL compilation, optimization, QoR targeting | Optimization strategies + QoR reviewer; must follow executable procedure — Sky130 technology mapping validated |
| `floorplan` | Flexible | Die sizing, macro placement, power grid, pins | Macro placement, PG, pins; `make_tracks.tcl` before `place_pins`; `setRC.tcl` before IR-drop |
| `placement` | Flexible | Global/detailed placement, congestion, timing-driven | Congestion, timing-driven; cell contiguity; detailed placement legalization |
| `cts` | Rigid | Clock tree synthesis, skew balancing, buffer insertion | `synth -top $Top`; CTS before PDN; useful skew; Bug #3 territory |
| `routing` | Rigid | Global/detail routing, DRC, hold fixing, antenna | DRC convergence; hold fixing; `make_tracks.tcl` before `place_pins` |
| `signoff` | Rigid | Multi-corner STA, DRC, LVS, IR drop, EM, GDSII | `define_corners tt ss ff`; `read_liberty -corner <c>`; `report_checks -corner <c>` |
| `lec` | Rigid | ANY netlist change at ANY stage | 9-checkpoint protocol; adjacent comparison; full-circle RTL vs routed |
| `low-power` | Rigid | Every stage gate + any cell insertion | Domain isolation; level shifters; retention flops; Bug #3; Bug #6 |
| `forensic-debugging` | Rigid | Any gate failure, timing/DRC violations | 4-phase investigation; five-level root cause trace; three-fix rule |

### Skill Execution → Agent Coordination

```
Skill invoked → Fresh context (no session history) → Specific inputs (file paths, not descriptions)
    ↓
Expected output format: PASS/FAIL + evidence
    ↓
Do not trust self-reports; raw reports read, not agent summary
    ↓
Output validated against acceptance criteria
```

### EDA Tool Integration

Tools available under `D:\tools\` (Windows) and `/mnt/d/tools/` (WSL):

| Tool | Platform | Status | Invocation |
|---|---|---|---|
| Yosys (native) | Windows | ✅ Available (0.69+154) | `yosys.exe -s synth_script.ys`; `-c` is Tcl mode (fails) |
| Yosys (portable WASM) | Windows + WSL | ✅ Available (0.70) | `yowasp-yosys.exe` (WASM build; runs both platforms) |
| Icarus Verilog | Windows | ✅ Available (with lib on PATH) | `iverilog.exe -o <out.vvp> <rtl.v> <tb.v>` |
| Verilator (lint) | Windows | ✅ Available (with lib on PATH) | `verilator_bin.exe --version` |
| MinGW-w64 Toolchain | Windows | ✅ Available (g++/ar/make) | `g++.exe --version`; use -O2 not -Os |
| OpenROAD | WSL only | ✅ Available (headless, 8 threads) | `wsl -d Ubuntu-24.04 -- bash -lc 'export PATH=/mnt/d/tools/openroad/bin:$PATH; openroad <script.tcl>'` |
| OpenSTA | WSL only | ✅ Available (3.1.0) | `wsl -d Ubuntu-24.04 -- bash -lc 'LD_LIBRARY_PATH=/mnt/d/tools/opensta/lib /mnt/d/tools/opensta/bin/sta <script.tcl>'` |
| Magic (DRC) | WSL only | ✅ Available (0.30.13, extracted) | `wsl -d Ubuntu-24.04 -- bash -lc '/mnt/d/tools/magic/bin/magic -dnull -noconsole script.tcl'` |
| Netgen (LVS) | WSL only | ✅ Available (0.6.2401, extracted) | `wsl -d Ubuntu-24.04 -- bash -lc '/mnt/d/tools/netgen/bin/netgen -batch source.tcl'` |
| OpenLane 2 | WSL only | ✅ Available (v2.0.0b16) | `wsl -d Ubuntu-24.04 -- bash -lc '/mnt/d/tools/bin/openlane --version'` |
| KLayout | WSL only | ✅ Available (0.30.13, extracted) | **BLOCKED on Windows**: missing Qt5 system libs; WSL invocation: `wsl -d Ubuntu-24.04 -- bash -lc '/mnt/d/tools/KLayout/bin/klayout.sh -v'` |
| Sky130 PDK | ✅ Available | `skywater-pdk-complete` | Functional models, Liberty corners (TT/SS/FF), LEF, GDSII cell library |
| POSIX Utils (bash etc.) | Windows | ✅ Available (Git for Windows) | `D:\tools\unix-utils\usr\bin\bash.exe` (junction to `C:\Program Files\Git`) |
| Node.js + Mermaid | Windows | ✅ Available | `node --version`; `mmdc.cmd` for diagram rendering |
| Verible lint/format | Windows | ✅ Available | `verible-verilog-lint.exe --version` |

**Tool Availability Summary Matrix**:

| Tool | Windows | WSL | Installed in D:\tools | Functional |
|---|---|---|---|---|
| Yosys (native) | ✓ | ✗ | `D:\tools\oss-cad-suite\` | ✓ |
| Yosys (portable/WASM) | ✓ | ✓ | `D:\tools\venv\`, `D:\tools\venv-wsl\` | ✓ (0.70) |
| Icarus Verilog | ✓ | ✗ | `D:\tools\oss-cad-suite\` | ✓ (with lib on PATH) |
| Verilator (lint) | ✓ | ✗ | `D:\tools\oss-cad-suite\` | ✓ |
| MinGW-w64 g++/ar/make | ✓ | ✗ | `D:\tools\mingw64\` | ✓ (use -O2, not -Os) |
| OpenROAD | ✗ | ✓ | `D:\tools\openroad\` | ✓ (WSL only) |
| OpenSTA | ✗ | ✓ | `D:\tools\opensta\` | ✓ (WSL only) |
| Magic (DRC) | ✗ | ✓ | `D:\tools\magic\` | ✓ (WSL only) |
| Netgen (LVS) | ✗ | ✓ | `D:\tools\netgen\` | ✓ (WSL only) |
| OpenLane 2 | ✗ | ✓ | `D:\tools\src\openlane2\`, `D:\tools\venv-openlane2\` | ✓ (CLI v2.0.0b16) |
| KLayout | ✗ | ✓ | `D:\tools\KLayout\` | ✓ (WSL only; blocked on Windows) |
| Sky130 PDK | ✓ | ✓ | `D:\tools\pdk\skywater-pdk-main\`, `D:\tools\pdk\skywater-pdk-complete\` | ✓ |
| POSIX utils (bash etc.) | ✓ | ✗ | `D:\tools\unix-utils\` (junction) | ✓ |
| Node.js | ✓ | ✓ | PATH, `D:\tools\mermaid\` (npm) | ✓ (v22.23.4 / v26.7.0) |
| Mermaid CLI | ✓ | ✗ | `D:\tools\mermaid\` | ✓ — renders + validates with GitHub's parser |

**Key Tool Limitations**:
- **KLayout on Windows**: BLOCKED — extracted 0.30.13; not runnable (missing Qt5 system libraries `libQt5Core.so.5`, `libgit2.so.1.7.1`; separate approval required to install them)
- **WLS-dependent tools**: OpenROAD, OpenSTA, Magic, Netgen, OpenLane 2, KLayout require WSL invocation
- **Single-corner synthesis**: TT only; SS/FF Liberty present but not used for synthesis (multi-corner not performed; explicitly flagged as risk)
- **EQY on Windows**: Needs LF line endings (make defaults to CRLF breaks); POSIX `sh` (`SHELL=sh.exe`, Git for Windows); `eqy_combine.so`/`eqy_partition.so`/`eqy_recode.so` Yosys plugins in directory EQY runs from

### Design Configuration

Framework supports two independently configured designs:

| Design | RTL Source | SDC | Top Module | Artifact Prefix | Status |
|---|---|---|---|---|---|
| **test_counter** | `examples/test-counter/rtl/test_counter.v` | `examples/test-counter/rtl/test_counter.sdc` | counter | `test_counter` | ✅ Validated (Phase 14: REPRODUCIBLE) |
| **soc_ctrl** | `examples/soc_ctrl/rtl/soc_ctrl.v` | `examples/soc_ctrl/rtl/soc_ctrl.sdc` | soc_ctrl | `soc_ctrl` | ⚙️ Integrated; full regression pending (shell blocked) |

**Design Config** (`$DesignConfig` hashtable in `run_regression.ps1`) maps each design name to:

- RTL file path (design-specific)
- SDC file path (design-specific)
- Artifact name prefix (prevents cross-design collision in shared workspace)
- Register file library paths (TT/SS/FF, design-specific)
- SDC constraint parameters (design-specific)
- Global routing net prefix (design-specific)

**Invocation**:

```powershell
# test_counter (Phase 14 baseline)
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design test_counter

# soc_ctrl (Phase 15 integration)
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design soc_ctrl
```

**Design Config Hashtable** (excerpt from `run_regression.ps1`):

```powershell
$DesignConfig = @{
  test_counter = @{
    Rtl = "$Rtl"
    Sdc = "$Sdc"
    ArtPrefix = "test_counter"
    LibDir = $LibDir
    LibTT = "$LibDir/sky130_fd_sc_hd__tt_025C_1v80.lib"
    LibSS = "$LibDir/sky130_fd_sc_hd__ss_n40C_1v40.lib"
    LibFF = "$LibDir/sky130_fd_sc_hd__ff_n40C_1v95.lib"
    Cdl = "$CdDir"
    NetlistMapped = "$ArtPrefix/synth/generic_${ArtPrefix}_synth.v"
  }
  soc_ctrl = @{
    Rtl = "$Rtl_soc_ctrl"
    Sdc = "$Sdc_soc_ctrl"
    ArtPrefix = "soc_ctrl"
    LibDir = $LibDir
    LibTT = "$LibDir/sky130_fd_sc_hd__tt_025C_1v80.lib"
    LibSS = "$LibDir/sky130_fd_sc_hd__ss_n40C_1v40.lib"
    LibFF = "$LibDir/sky130_fd_sc_hd__ff_n40C_1v95.lib"
    Cdl = "$CdDir"
    NetlistMapped = "$ArtPrefix/synth/generic_${ArtPrefix}_synth.v"
  }
}
```

### Regression Execution

The regression entry point is `run_regression.ps1`, which orchestrates all 15 stages:

```powershell
# Run all stages (default, dependency order)
powershell -ExecutionPolicy Bypass -File run_regression.ps1

# Run with design selection
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design test_counter

powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design soc_ctrl

# Run named stages only (dependencies assumed present)
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Stages 01_synthesis,02_lec_generic

# Clean run directory before start
$Clean = $true  # Remove-Item -Recurse -Force $RunDir before start

# Resume from previous state
$Resume = $true  # Skip completed stages; only run remaining
```

### Stage Dependencies (Partial Order)

```
01_synthesis      (no deps)
02_lec_generic    (deps: 01_synthesis)
03_lec_mapped     (deps: 01_synthesis)
04_floorplan      (deps: 01_synthesis)
05_placement      (deps: 01_synthesis)
06_cts            (deps: 01_synthesis)
07_pdn            (deps: 01_synthesis)
08_power_connectivity (deps: 06_cts)
09_global_route   (deps: 05_placement)
10_detailed_route (deps: 09_global_route)
11_extract        (deps: 09_global_route, 10_detailed_route)
12_sta            (deps: 01_synthesis, 11_extract)
13_gds            (deps: 11_extract, 12_sta)
14_drc            (deps: 13_gds)
15_lvs            (deps: 14_drc, 13_gds)
```

### Acceptance Engine

Every stage has explicit independent acceptance checks. A stage is only reported PASS when all of its checks pass — a zero exit code from the tool is never sufficient.

**Check Classifications**: PASS, FAIL, BLOCKED, ERROR, INCONCLUSIVE, NOT_TESTED

**PASS Classification Requires**:
- All acceptance checks for the stage pass
- Fresh tool execution each time (not from cache)
- Evidence files produced and verified
- Exit code alone is never sufficient for PASS

**Check Workflow per Stage**:

```
Stage script executes → Tool output captured → Acceptance checks run
    ↓
Each check result classified (PASS/FAIL/BLOCKED/ERROR/INCONCLUSIVE/NOT_TESTED)
    ↓
Result files written (`.log`, `.rpt`, `.txt`, `.json` as appropriate)
    ↓
Stage status determined by check results
    ↓
JSON report updated with stage status and per-check results
```

**Evidence per Check**:
- Result classification
- Tool exit code (if applicable)
- Artifact paths (netlist, report, SPEF)
- SHA256 hashes for bit-identical comparison
- Stage and check identifiers (for cross-run comparison)

### Machine-Readable Reporting

JSON reports with `schema_version`, per-stage status, per-check results, artifact paths, and artifact hashes.

**Report Files**:

| File | Description |
|---|---|
| `report.json` | Complete regression JSON report (schema_version, run_id, design, stages, checks, artifacts, hashes) |
| `acceptance.json` | Per-stage acceptance check details (check_id, result, exit_code, artifact_paths, hash) |
| `summary.md` | Human-readable summary (stage statuses, WNS/TNS per corner, key metrics) |

**JSON Report Schema** (simplified):

```json
{
  "schema_version": "1.0",
  "run_id": "20261114T120000Z",
  "design": "test_counter",
  "classification": "REPRODUCIBLE",
  "stages": {
    "01_synthesis": {
      "status": "PASS",
      "checks": {
        "syntax": "PASS",
        "rtl_elaboration": "PASS",
        "technology_mapping": "PASS",
        "area": "PASS",
        "wns_tt": "5.4550",
        "wns_ss": "2.6745",
        "wns_ff": "-0.6159",
        "tns": "0.0000"
      },
      "artifacts": {
        "netlist": "synth/generic_test_counter_synth.v",
        "spef": "extraction/test_counter.spef",
        "log": "synth/synthesis.log"
      },
      "hashes": {
        "netlist": "e1e8d19f...",
        "spef": "abc123..."
      }
    },
    ... (other stages)
  },
  "artifacts_comparison": {
    "run_a": "identical",
    "run_b": "identical",
    "gdsii_timestamps_stripped": "identical"
  }
}
```

### Evidence Generation

Per-stage evidence includes:

| Evidence Type | Description | Retained |
|---|---|---|
| Tool output files (`.log`, `.rpt`) | Raw tool execution output | ✅ Always |
| Netlist files (`.v`) | Gate-level or post-synthesis netlist | ✅ Always |
| SPEF files (`.spef`) | Parasitic extraction results | ✅ If stage includes extraction |
| Database files (`.def`, `.odb`) | Physical design databases | ✅ If stage includes DEF/ODB |
| GDSII stream (`.gds`) | GDSII stream-out results | ✅ If stage includes GDSII |
| Comparison results (`.txt`, `.json`) | Run A vs Run B artefact comparison | ✅ Phase 14: 20/20 artefacts compared |
| Negative test results (`.txt`) | N1–N7 test outcomes | ✅ Phase 15: all 7 DETECTED |
| LVS database (`.lvsdb`) | LVS comparison database | ✅ Phase 13: `6_lvs.lvsdb` |
| STA reports (`.rpt`) | Multi-corner timing reports | ✅ Phase 14: TT/SS/FF corners |

### Error Handling

- **Tool exit code 0 alone is never sufficient** for PASS classification
- **Fresh execution** each time; cached results are not trusted
- **Three-fix rule**: If 3 ECOs on the same failure show diminishing returns, stop and trace root cause upstream
- **Escalation**: Forensic debugger invoked on any gate failure; five-level root cause trace presented to human with cost/risk assessment
- **Do not trust self-reports**: The QoR reviewer reads raw reports, not the agent's summary
- **Do not claim PASS on partial output**: `truncated == true ⇒ INCONCLUSIVE`
- **Do not shadow core system libraries**: When extracting `.deb` packages into a local tree, skip `libc6`, `libgcc-s1`, `libstdc++6`, `libcrypt1`, `zlib1g` and `libselinux1`; a second copy of libc on `LD_LIBRARY_PATH` breaks every process in the session
- **KLayout `ReportDatabase::save` needs its parent directory to exist**: the `//` in path is cosmetic — the real cause is `ENOENT` on the missing directory; `Dir.mkdir` the output directory before saving

### Mermaid Architecture Diagram

```mermaid
flowchart TD
    %% Agents
    synth[Synthesis Engineer] -->|RTL + SDC| synth
    floor[Floorplan Engineer] -->|Netlist + Constraints| floor
    place[Placement Engineer] -->|Placed DEF| place
    cts[CTS Engineer] -->|CTS DEF + Clock Netlist| cts
    route[Routing Engineer] -->|Routed DEF + Netlist| route
    signoff[Signoff Engineer] -->|STA + DRC + LVS + GDSII| signoff
    lec[Lec Verifier] -->|LEC Reports| lec
    lpo[Low-Power Verifier] -->|Domain Checks| lpo
    fog[Forensic ECO Debugger] -->|Investigation| fog
    
    %% Stage Gates
    synth -->|QoR Review| gate1[Stage 1 Gate]
    floor -->|QoR Review| gate2[Stage 2 Gate]
    place -->|QoR Review| gate3[Stage 3 Gate]
    cts -->|QoR Review| gate4[Stage 4 Gate]
    route -->|QoR Review| gate5[Stage 5 Gate]
    lec -->|LEC Check| gate6[Stage 6 Gate]
    lpo -->|Low Power Check| gate7[Stage 7 Gate]
    fog -->|Failure Investigation| gate_fail[Gate Failure]
    
    %% Cross-Cutting
    lec -->|Equivalence| synth
    lec -->|Equivalence| floor
    lec -->|Equivalence| place
    lec -->|Equivalence| cts
    lec -->|Equivalence| route
    lpo -->|Domain Checks| synth
    lpo -->|Domain Checks| floor
    lpo -->|Domain Checks| place
    lpo -->|Domain Checks| cts
    lpo -->|Domain Checks| route
    
    %% Design Configuration
    config[Design Config<br/>(-Design parameter)] -->|Sets REG_DESIGN| synth
    config -->|Sets REG_DESIGN| floor
    config -->|Sets REG_DESIGN| place
    config -->|Sets REG_DESIGN| cts
    config -->|Sets REG_DESIGN| route
    config -->|Sets REG_DESIGN| lec
    config -->|Sets REG_DESIGN| lpo
    config -->|Sets REG_DESIGN| fog
    
    %% EDA Tools
    tools[EDA Tools<br/>D:\tools\] -->|Yosys, OpenSTA, KLayout| synth
    tools -->|OpenROAD, OpenSTA| route
    tools -->|KLayout| signoff
    tools -->|Sky130 PDK| all
    
    %% Workspaces
    ws_test[Workspace/test_counter] -->|Run A| synth
    ws_soc[Workspace/soc_ctrl] -->|Config only| config
    
    %% Evidence
    evid[Evidence Directory] -->|Reports, Hashes| all
```

### Error Handling Summary

| Condition | Classification | Action |
|---|---|---|
| Tool exit code 0 + all checks pass | PASS | Proceed to next stage |
| Tool exit code 0 + some checks fail | FAIL | Do not proceed; fix and re-check |
| Tool exit code 1 + output capture failed | BLOCKED | Investigate shell/environment; do not claim FAIL |
| Fresh execution required, used cached | INCONCLUSIVE | Re-run tool fresh; do not trust cache |
| 3 ECOs on same failure, diminishing returns | ERROR | Escalate via three-fix rule; five-level root cause trace |
| Shell tool non-functional | BLOCKED | Document; do not claim execution results; all work via read/write/edit |
| Design rules violated (DRV > 0) | FAIL | Do not proceed; escalate to human; do not ship |

### Validation Pipeline per Stage

```
Stage Script Executes
    ↓
Tool Output Captured (stdout, stderr, exit code)
    ↓
Acceptance Checks Run (fresh, not cached)
    ↓
Each Check Classified:
    • PASS — check criteria met + evidence present
    • FAIL — check criteria not met
    • BLOCKED — environment prevents check execution
    • ERROR — tool execution error (unexpected)
    • INCONCLUSIVE — partial/corrupt output
    • NOT_TESTED — check not applicable to this stage
    ↓
Check Results Written to Evidence Files
    ↓
Stage Status Determined (overall classification)
    ↓
JSON Report Updated (schema_version, stage status, per-check results, artifact paths, hashes)
    ↓
Cross-Run Comparison (if Run B): Artifact hashes compared
    ↓
Regression Continuation (if all stages pass) or Escalation
```

### Architecture Evolution (Phases 14–17B)

| Phase | Architecture Change | Status |
|---|---|---|
| **14** | Initial 15-stage flow; `run_regression.ps1`; acceptance checks (88/88); negative tests N1–N7; REPRODUCIBLE classification | ✅ Complete |
| **15** | `-Design` parameter added to `run_regression.ps1`; `$DesignConfig` hashtable; `01_synthesis.ps1` design-aware via `$Top`; soc_ctrl RTL + SDC created; N7 injection fix (target `15a_lvs_refcdl.tcl`) | ✅ Complete |
| **16** | Shell failure fully diagnosed; all framework modifications documented; GitHub publication procedures prepared; 7 reports created | ✅ Complete |
| **17B** | Professional README rewritten; ARCHITECTURE.md created; all 9 agents documented with full responsibilities; all 10 skills documented with execution procedures; REGRESSION.md created; VALIDATION.md created with Phase 1–17 milestones; DESIGN.md created with test_counter + soc_ctrl; .gitige review; GitHub publication preparation | ✅ In Progress |

### Architecture Diagram — Key Relationships

```
run_regression.ps1
    │
    ├───► -Design parameter → REG_DESIGN env → All stage scripts ($Top = $env:REG_DESIGN)
    │
    ├───► Stage 01 (synthesis) → Generic + Mapped netlists → Stages 02–03 (LEC)
    │
    ├───► Stage 04 (floorplan) → DEF + PDN → Stages 05–07 (placement, CTS, PDN)
    │
    ├───► Stage 08 (power connectivity) → VDD/VSS checks → Stage 09 (global route)
    │
    ├───► Stage 10 (detailed route) → Routed DEF → Stage 11 (extract → SPEF)
    │
    ├───► Stage 12 (STA) → TT/SS/FF corners → Stage 13 (GDSII)
    │
    ├───► Stage 14 (DRC) → 295 rule categories → Stage 15 (LVS)
    │
    └───► Evidence → JSON reports → Cross-run comparison → REPRODUCIBLE classification
```

### Cross-Design Isolation

- **Artifact prefixes** prevent mixing (`test_counter` vs `soc_ctrl`)
- **Different RTL/SDC paths** per design
- **Different run directories** (`workspace/run_run_*` vs `workspace/run_soc_ctrl_*`)
- **No shared mutable temporary files**
- **Separate workspaces** with independent evidence directories

### Architecture Diagram — Evidence Flow

```
Stage Execution → Tool Output → Acceptance Checks → Evidence Files
    │                                                        │
    ├────────────────────────────────────────────────────────┘
    │
    ↓
JSON Report (schema_version, stage status, per-check results, artifact paths, hashes)
    │
    ├──────────────────────────────────────────────────────┐
    ↓                                                   ↓
Cross-Run Comparison (Run A vs Run B)                   GitHub Publication
(20/20 artefacts identical, GDSII timestamps stripped)   (procedures documented; shell blocked)
```

### Architecture Principles

1. **Evidence before claims** — Every signoff check runs fresh; "it passed earlier" is not evidence
2. **Stage gates** — No stage proceeds without QoR review + LEC + low power verification passing
3. **Three-fix rule** — If 3 ECOs don't resolve a failure, stop and trace the root cause upstream
4. **No partial PASS** — `truncated == true ⇒ INCONCLUSIVE`; tool exit code alone is never sufficient
5. **Fresh execution** each time; cached results not trusted
6. **Design isolation** — Artifact prefixes prevent cross-design contamination
7. **Machine-readable reporting** — JSON reports with schema_version, per-stage status, per-check results, artifact paths, and hashes
8. **Design configuration via `-Design` parameter** — `REG_DESIGN` propagated to all stage scripts
9. **Tool availability documented** — Per EDA_TOOL_REGISTRY.md; WSL-dependent tools require WSL invocation
10. **Known limitations disclosed** — No exaggerated claims; framework configured but execution blocked by shell issue

---

## 📋 Quick Reference: Agent + Skill Summary

### 9 Specialist Agents

| Agent | Role | Key Document |
|---|---|---|
| `synthesis-engineer` | RTL compilation, multi-corner optimization, QoR targeting | `agents/synthesis-engineer.md` |
| `floorplan-engineer` | Die sizing, macro placement, power grid, pin assignment | `agents/floorplan-engineer.md` |
| `placement-engineer` | Global/detailed placement, congestion, timing-driven opt | `agents/placement-engineer.md` |
| `cts-engineer` | Clock tree synthesis, skew balancing, useful skew | `agents/cts-engineer.md` |
| `routing-engineer` | Global/detail routing, DRC convergence, hold fixing | `agents/routing-engineer.md` |
| `signoff-engineer` | Multi-corner STA, DRC, LVS, IR drop, EM, GDSII | `agents/signoff-engineer.md` |
| `lec-verifier` | Formal equivalence at every stage boundary | `agents/lec-verifier.md` |
| `low-power-verifier` | Cross-cutting UPF/CPF domain verification | `agents/low-power-verifier.md` |
| `forensic-eco-debugger` | Reactive 4-phase investigation on any failure | `agents/forensic-eco-debugger.md` |

### 10 Engineering Skills

| Skill | Type | Key Document |
|---|---|---|
| `using-pd-staff-engineer` | Rigid (employee handbook) | `skills/using-pd-staff-engineer/SKILL.md` |
| `synthesis` | Flexible | `skills/synthesis/SKILL.md` |
| `floorplan` | Flexible | `skills/floorplan/SKILL.md` |
| `placement` | Flexible | `skills/placement/SKILL.md` |
| `cts` | Rigid | `skills/cts/SKILL.md` |
| `routing` | Rigid | `skills/routing/SKILL.md` |
| `signoff` | Rigid | `skills/signoff/SKILL.md` |
| `lec` | Rigid | `skills/lec/SKILL.md` |
| `low-power` | Rigid | `skills/low-power/SKILL.md` |
| `forensic-debugging` | Rigid | `skills/forensic-debugging/SKILL.md` |

### Core Principles (Shared Across All Agents and Skills)

1. **Evidence over claims** — Every QoR number must come from a tool run in this session, not from memory or a previous run
2. **Reports drive decisions** — Read reports before optimizing; do not guess what the critical path is — look
3. **Multi-corner is mandatory** — A netlist that passes one corner and fails another is a failing netlist; check all available corners
4. **Three-fix rule** — If 3 optimization attempts do not improve WNS by more than 5%, stop and escalate; the problem is upstream of synthesis
5. **Cheapest fix first** — Try restructuring before pipelining; try upsizing before replication; every optimization has a cost — pick the one with the smallest area/power penalty
6. **Fresh execution** each time; cached results not trusted
7. **Do not trust self-reports** — The QoR reviewer reads raw reports, not the agent's summary
8. **Do not claim PASS on partial output** — `truncated == true ⇒ INCONCLUSIVE`
9. **Design isolation** — Artifact prefixes prevent cross-design contamination
10. **Known limitations disclosed** — No exaggerated claims; framework configured but execution blocked by shell issue

---

## 📞 Next Steps (When Shell Tool Recovered)

1. **Run test_counter regression**: `powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design test_counter`
2. **Run soc_ctrl regression**: `powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design soc_ctrl`
3. **Verify all 15 stages pass** for both designs
4. **Run DRC, LVS, multi-corner STA** on both designs
5. **Git commit and push** to remote: `git push origin feature/F-01-claude-agent-discovery`
6. **Verify remote publication** on `RAMA-L7/vlsi-pd-staff-engineer`
7. **Update central project status** with all phases (1–17)

---

*Architecture Documentation — feature/F-01-claude-agent-discovery — Multi-Design Framework v2.0*
*Generated: 2026-11-14 — All agent and skill definitions verified via read-only file inspection*