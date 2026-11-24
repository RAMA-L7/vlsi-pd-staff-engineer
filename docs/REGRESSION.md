# Regression Documentation — VLSI Physical Design Framework

## main — Multi-Design v2.0

### Test-only injection seams (TR-04, 2026-11-19)

`run_regression.ps1` accepts additive parameters `-StageExecutor`, `-AcceptanceExecutor`,
`-ManifestExecutor`, `-EvidenceDirOverride`, `-WslDistro` and `-SkipToolProbe`. All default to
the production behaviour. `tests/test_runner_integration_simulated.py` uses them to invoke the
**actual** runner with fake tools ("Real runner integration with simulated tools"); the SR-02
scheduling, dependency-authorization, persistence, resume and verdict control flow is unchanged.

### Authoritative status and commands (SR-08 / SR-10)

This checkout is published commit `45e4251370c39ed27e2ccd6a7a804ed90eaf4472` (tree
`00b0418ff4fde3cda377f0175bb92c5b1b32e1ca`). The single current qualification
matrix is in `README.md` ("Authoritative qualification matrix — this checkout");
all dated results below are historical context. Recorded baseline runs:
`baseline_20261116` and `baseline_20261116_r2` (16/16 stages, 95/95 checks) on
the published implementation at commit `6c4a264`; soc_ctrl full physical run
`run_pub_m1d_soc_ctrl_full` (retained SS-timing and LVS-gate failures). No fresh
15-stage run was performed against `0a9eb57` in this documentation pass.

**Portable config source of truth (WORKSTREAM 2):** `assistant/pins.json` →
`portable_config` resolves absent developer-host defaults (validated fallback:
ORFS HEAD `25b6429c…`, host-specific, not a portability claim) and pins exact
SHA-256 hashes for platform collateral with `required` flags. Environment
overrides win; the baseline wrapper injects the resolved overrides into the
runner, and preflight reports every collateral file's resolved source and
hash under `platform_config` / `dependencies.platform`. A missing required
file or hash mismatch is a FAIL; an optional absence is reported only. See
`docs/SETUP.md` §3A.

**Preflight command (exact missing dependencies):**

```powershell
& D:\tools\venv\Scripts\python.exe regression\checks\stage00_preflight.py `
    regression\project_config.template.json --out-dir <isolated-dir> `
    --require-tool yosys --require-tool openroad --require-tool eqy
# Exit 0=PASS, 1=FAIL, 2=usage. Writes stage00_result.json/stage00_report.txt.
```

The report includes a `dependencies` block listing each mandatory/optional
tool, the exact executable/path searched, the override variable that can supply
it, and `missing_mandatory` / `missing_optional` arrays. The Microsoft Store
`python.exe` alias is rejected as a false PASS; `--require-tool NAME` promotes
an optional tool to mandatory for CI. Workspace isolation now genuinely fails
when the output directory overlaps an RTL/SDC source directory, and the
undriven-output heuristic no longer has an `or True` no-op.

**One documented baseline command:**

```powershell
& D:\tools\venv\Scripts\python.exe -m assistant baseline --design test_counter --run-id <ID>
# soc_ctrl: replace --design test_counter with --design soc_ctrl
# --clean       re-create the isolated output dir (safe reuse of a run id)
# --no-isolate  legacy shared output dir under --out
# --dry-run     provenance only; never returns success (exit 1)
# The CLI `python -m assistant baseline --design ...` forwards --design to the
# same implementation and propagates 0/1/124/125 unchanged (SR-06 contract).
```

Each invocation writes `baseline_<design>_<run-id>/baseline_manifest.json` and
its log under `<--out>` (default `<repo>/regression/evidence/`); the regression
workspace remains isolated per RunId under `regression/workspace/run_<RunId>/`.

### run_regression.ps1 — Primary Entry Point

The regression entry point is `run_regression.ps1`, a PowerShell script that orchestrates the complete 15-stage RTL-to-GDSII flow for a selected design.

### Design Selection via `-Design` Parameter

```powershell
# Run all stages with design selection
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design test_counter

powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design soc_ctrl

# Run all stages (default, no design selection — uses test_counter by default)
powershell -ExecutionPolicy Bypass -File run_regression.ps1
```

**Default design**: `test_counter` (if `-Design` not specified)

### Design Configuration

The `-Design` parameter sets `REG_DESIGN` in both `$EnvWsl` and `$EnvWin` hashtables, which all 15 stage scripts read via `$env:REG_DESIGN`. The `DesignConfig` hashtable maps each design name to its RTL/SDC paths and artifact name prefix:

```powershell
$DesignConfig = @{
  test_counter = @{
    Rtl       = "$Rtl"           # examples/test-counter/rtl/test_counter.v
    Sdc       = "$Sdc"           # examples/test-counter/rtl/test_counter.sdc
    ArtPrefix = "test_counter"   # prevents cross-design collision
    LibDir    = $LibDir          # D:\tools\pdk\skywater-pdk-complete\
    LibTT     = "$LibDir/sky130_fd_sc_hd__tt_025C_1v80.lib"
    LibSS     = "$LibDir/sky130_fd_sc_hd__ss_n40C_1v40.lib"
    LibFF     = "$LibDir/sky130_fd_sc_hd__ff_n40C_1v95.lib"
    Cdl       = "$CdDir"         # D:\rta-real-world-benchmark\02_projects\OpenROAD-flow-scripts\flow\platforms\sky130hd\cdl\sky130hd.cdl
    NetlistMapped = "$ArtPrefix/synth/generic_${ArtPrefix}_synth.v"
  }
  soc_ctrl = @{
    Rtl       = "$Rtl_soc_ctrl"      # examples/soc_ctrl/rtl/soc_ctrl.v — canonical ABSENT from published 27b5d1c
    Sdc       = "$Sdc_soc_ctrl"      # examples/soc_ctrl/rtl/soc_ctrl.sdc — canonical ABSENT from published 27b5d1c
    ArtPrefix = "soc_ctrl"           # prevents cross-design collision
    LibDir    = $LibDir              # D:\tools\pdk\skywater-pdk-complete\
    LibTT     = "$LibDir/sky130_fd_sc_hd__tt_025C_1v80.lib"
    LibSS     = "$LibDir/sky130_fd_sc_hd__ss_n40C_1v40.lib"
    LibFF     = "$LibDir/sky130_fd_sc_hd__ff_n40C_1v95.lib"
    Cdl       = "$CdDir"             # same sky130hd.cdl
    NetlistMapped = "$ArtPrefix/synth/generic_${ArtPrefix}_synth.v"
  }
}
```

**Stage scripts read `REG_DESIGN` to determine behavior**:

```powershell
# Example from 01_synthesis.ps1
$Top = $env:REG_DESIGN
# synth -top $Top (not hardcoded to test_counter)
# Output file names: generic_<design>_synth.v
# SHA256 hash paths: design-specific
```

### test_counter Configuration

- **RTL**: `examples/test-counter/rtl/test_counter.v`
- **SDC**: `examples/test-counter/rtl/test_counter.sdc`
- **Top Module**: `test_counter`
- **Artifact Prefix**: `test_counter`
- **Expected Output**: `test_counter_synth.v`, `test_counter_lec_generic.log`, etc.
- **Baseline (Phase 14)**: REPRODUCIBLE — two runs A&B OVERALL PASS, 88/88 acceptance checks (historical; current baseline 95/95), 20/20 artefacts identical

### soc_ctrl Configuration

- **RTL**: canonical `examples/soc_ctrl/rtl/soc_ctrl.v` **tracked at reviewed commit `0a9eb57`** (absent only from the earlier `27b5d1c` snapshot)
- **SDC**: canonical `examples/soc_ctrl/rtl/soc_ctrl.sdc` **tracked at `0a9eb57`**; approved 10 ns bytes pinned by `regression/configs/soc_ctrl_10ns.json` and by `assistant/pins.json:design_inputs.soc_ctrl_sdc` (`ab66de1e…`)
- **Top Module**: `soc_ctrl`
- **Artifact Prefix**: `soc_ctrl`
- **Expected Output**: `soc_ctrl_synth.v`, `soc_ctrl_lec_generic.log`, etc.
- **Framework Integration**: `-Design soc_ctrl` parameter operational; stage scripts configured; the Milestone 1D run `run_pub_m1d_soc_ctrl_full` executed all stages with retained failures (SS setup WNS −2.3378 ns; stage-15 comparator gate FAIL). No fresh run was performed against `0a9eb57` in this documentation pass.
- **Known Limitations**:
  - Original soc_ctrl reference is a gate-level netlist, not RTL; the framework RTL is an adaptation
  - DRC was executed (0 actual violations) but is an academic deck check, not signoff; LVS gate FAILED as-run; SS timing is not closed
  - Do NOT claim DRC/LVS/timing signoff or full physical qualification for soc_ctrl

### Stage Execution

The regression runs 15 stages in dependency order. Each stage has independent acceptance checks; a stage is only PASS when all its checks pass.

### Stage Dependencies (Partial Order)

```
01_synthesis       (no deps)
02_lec_generic     (deps: 01_synthesis)
03_lec_mapped      (deps: 01_synthesis)
04_floorplan       (deps: 01_synthesis)
05_placement       (deps: 01_synthesis)
06_cts             (deps: 01_synthesis)
07_pdn             (deps: 01_synthesis)
08_power_connectivity (deps: 06_cts)
09_global_route    (deps: 05_placement)
10_detailed_route  (deps: 09_global_route)
11_extract         (deps: 09_global_route, 10_detailed_route)
12_sta             (deps: 01_synthesis, 11_extract)
13_gds             (deps: 11_extract, 12_sta)
14_drc             (deps: 13_gds)
15_lvs             (deps: 14_drc, 13_gds)
```

### Stage-by-Stage Documentation

#### Stage 01: Synthesis

**Command** (via `01_synthesis.ps1`):

```powershell
# Design-aware: uses $Top = $env:REG_DESIGN
# Yosys script mode: -s (not -c Tcl mode)
yowsys -s synth_script.ys  # or per-stage TCL execution
```

**Outputs**:
- `synth/generic_<design>_synth.v` — Generic (unmapped) netlist
- `synth/test_counter_synth.v` or `soc_ctrl_synth.v` — Technology-mapped netlist
- `synth/synthesis.log` — Execution log
- QoR summary: WNS, TNS, area, cell count across corners

**Acceptance Checks**:
- RTL elaboration successful
- Technology mapping produces Sky130 HD cells (zero `$_*` primitives)
- Area within budget
- WNS meets target across TT corner (primary)
- LEC: RTL vs gate netlist pass confirmation
- Low power: Structural UPF/CPF check (if applicable)

**Validated Result (Phase 14, test_counter)**:
- 28 cells synthesized
- Zero `$_*` unmapped primitives
- WNS TT: 5.4550 ns (0 violators)
- Area: within budget
- LEC: PASS (RTL ≡ synthesized netlist)

#### Stage 02: Generic LEC

**Purpose**: Verify RTL vs generic netlist equivalence (post-synthesis, pre-technology-mapping)

**Command**: LEC tool (Yosys `equiv_*` or formality/conformal depending on availability)

**Outputs**:
- `lec_generic/lec_generic.log` — Positive control (PASS)
- `lec_generic/lec_generic_negative.log` — Negative control (correctly rejects)

**Acceptance Checks**:
- RTL and generic netlist are functionally equivalent
- Negative control correctly identifies non-equivalence
- Asynchronous reset behavior verified where applicable

**Validated Result (Phase 14, test_counter)**:
- Generic LEC PASS — RTL ≡ synthesized netlist
- Negative control correctly rejects modified RTL

#### Stage 03: Technology-Mapped LEC

**Purpose**: Verify technology-mapped netlist equivalence using EQY and official Sky130 HD preprocessing

**Command**: EQY `equiv_*` with `formal_pdk_proc.py` Sky130 preprocessor

**Outputs**:
- `eqy/eqy_positive.log` — Positive control
- `eqy/eqy_negative.log` — Negative control

**Acceptance Checks**:
- Technology-mapped netlist equivalent to RTL
- EQY preprocessing produces valid Equality Quotient
- Negative control correctly rejects
- Sky130 HD cell library models validated

**Validated Result (Phase 14, test_counter)**:
- Technology-mapped LEC PASS — EQY + official Sky130 preprocessing
- "Successfully proved designs equivalent" (EQY report)
- Negative control correctly rejected

#### Stage 04: Floorplan

**Purpose**: Die sizing, macro placement, power grid, pin assignment

**Command** (OpenROAD via WSL or MCP):

```bash
# Via WSL (preferred for ORFS flow)
wsl -d Ubuntu-24.04 -- bash -lc '
  export PATH=/mnt/d/tools/openroad/bin:$PATH
  openroad floorplan.tcl
'

# Via MCP (preferred for verification)
interactive_openroad_exec floorplan.tcl
# or
interactive_openroad_query floorplan_tcl
```

**Outputs**:
- `04_floorplan.odb` — OpenROAD database
- Floorplan DEF
- Power grid configuration
- Pin assignments
- Track assignments (`make_tracks.tcl` before `place_pins`)
- `setRC.tcl` sourced before IR-drop

**Acceptance Checks**:
- Core dimensions within platform strap pitch (27.14 um met4 / 27.2 um met5)
- Power grid: `check_power_grid -net VDD/VSS` → "All shapes on net VDD/VSS are connected"
- 66 well ties placed on 50-site grid (23 um), bottom row only first tie
- `make_tracks.tcl` sourced before `place_pins` (PPL-0021 prevented)
- `setRC.tcl` sourced before IR-drop (PSM-0021 prevented)
- Pin assignments valid (all primary ports assigned)

**Validated Result (Phase 13, test_counter)**:
- 120 × 60 um die, 14 rows, 66 well ties
- Power grid: all VDD/VSS shapes connected
- LVS PASS on PDN-corrected design with official `sky130hd.lylvs`

#### Stage 05: Placement

**Purpose**: Global/detailed placement, congestion, timing-driven optimization

**Command** (OpenROAD via WSL or MCP):

```bash
# Via WSL
wsl -d Ubuntu-24.04 -- bash -lc '
  export PATH=/mnt/d/tools/openroad/bin:$PATH
  openroad placement.tcl
'

# Via MCP
interactive_openroad_exec placement.tcl
```

**Outputs**:
- `05_place.odb` — OpenROAD database
- Placed DEF
- Cell locations
- Congestion maps
- Timing-driven optimization results

**Acceptance Checks**:
- All cells legalized (no DRC-clean violations at placement stage)
- Clock net connectivity from CTS verified
- Congestion within acceptable targets
- No overlapping cells
- Power domain integrity maintained

**Validated Result (Phase 12–13, test_counter)**:
- Placement DEF valid
- Cell contiguity verified
- Congestion within targets

#### Stage 06: CTS (Clock Tree Synthesis)

**Purpose**: Clock tree synthesis, skew balancing, useful skew

**Important**: CTS is performed BEFORE the PDN in this flow; with the platform default order (PDN first) clock-tree buffers would be inserted after `global_connect`, leaving their power pins unconnected - hence the enforced CTS-to-PDN order and the reference netlist contains `__UNCONNECTED__` nets).

**Command** (OpenROAD via WSL or MCP):

```bash
# Via WSL
wsl -d Ubuntu-24.04 -- bash -lc '
  export PATH=/mnt/d/tools/openroad/bin:$PATH
  openroad cts.tcl
'

# Via MCP
interactive_openroad_exec cts.tcl
```

**Key Constraints**:
- **CTS BEFORE the PDN** (critical; see above)
- Useful skew allowed but must be quantified and documented
- Clock buffer insertion must respect power domains (Bug #3: clock buffer in switchable domain = silicon killer)
- `setRC.tcl` sourced before IR-drop

**Outputs**:
- `06_cts.odb` — OpenROAD database
- CTS DEF
- Clock tree netlist
- Useful skew report
- Buffer insertion report

**Acceptance Checks**:
- Clock tree reaches all sequential cells
- Useful skew quantified and within limits
- No clock buffer in switchable domain
- CTS completed before PDN generation (precondition)
- Clock tree valid (all sinks driven)

**Validated Result (Phase 12–13, test_counter)**:
- Multi-corner STA with TT/SS/FF; 0 violators at every corner
- WNS setup 5.5678 ns / hold 0.5231 ns (TT), 3.7540 ns / 1.6507 ns (SS), 5.7656 ns / 0.3319 ns (FF)

#### Stage 07: PDN (Power Distribution Network)

**Purpose**: Power grid generation

**Command** (OpenROAD via WSL or MCP):

```bash
# Via WSL
wsl -d Ubuntu-24.04 -- bash -lc '
  export PATH=/mnt/d/tools/openroad/bin:$PATH
  openroad pdn.tcl
'

# Via MCP
interactive_openroad_exec pdn.tcl
```

**Key Constraints**:
- PDN-0185: PDN grid geometry generation (Phase 13 finding)
- Via stacks generated by OpenROAD (platform vias cannot be pinned for 4-layer jump)
- Well ties: 66 functional, correctly placed (bottom row only, first tie)
- Core size: 120 × 60 um die, 10 × 10 × 110 × 50, 14 rows
- CTS before PDN (precondition; see Stage 06)

**Outputs**:
- `07_pdn.def` — OpenROAD DEF
- Power grid configuration
- Via cut shapes
- Well tie placements

**Acceptance Checks**:
- Power grid: `check_power_grid -net VDD/VSS` → "All shapes on net VDD/VSS are connected"
- 66 well ties placed correctly (bottom row only, first tie)
- Via cut shapes present and valid
- Core dimensions within platform strap pitch
- No DRC violations at PDN stage (helper regions excluded from count)

**Validated Result (Phase 13, test_counter)**:
- PDN grid generated with corrected geometry
- `check_power_grid` → "All shapes on net VDD/VSS are connected"
- 66 well ties on 50-site grid (23 um), bottom row only first tie
- LVS PASS after PDN correction (official `sky130hd.lylvs`)

#### Stage 08: Power Connectivity

**Purpose**: VDD/VSS verification, power shape count checks

**Command** (OpenROAD TCL or per-stage script):

```bash
# Via WSL or direct TCL
wsl -d Ubuntu-24.04 -- bash -lc '
  export PATH=/mnt/d/tools/openroad/bin:$PATH
  openroad power_connectivity.tcl
'

# Or per-stage TCL script
openroad -c "source power_connectivity.tcl"
```

**Outputs**:
- `08_power_connectivity.txt` — Power connectivity report
- VDD/VSS shape counts
- Net connectivity verification

**Acceptance Checks**:
- VDD and VSS nets fully connected (no fragmented rails)
- Power shape count meets expectations (post-PDN-0185 correction)
- No disconnected power shapes
- Via count meets census expectations

**Validated Result (Phase 13, test_counter)**:
- Power connectivity PASS
- All VDD/VSS shapes connected
- Power-shape count corrected (PDN-0185 fix)

#### Stage 09: Global Routing

**Purpose**: Global routing of design

**Command** (OpenROAD via WSL or MCP):

```bash
# Via WSL
wsl -d Ubuntu-24.04 -- bash -lc '
  export PATH=/mnt/d/tools/openroad/bin:$PATH
  openroad global_route.tcl
'

# Via MCP
interactive_openroad_exec global_route.tcl
```

**Outputs**:
- `09_groute.odb` — OpenROAD database
- Global routed DEF
- NTE (non-routed) analysis
- Routability metrics

**Acceptance Checks**:
- Global routing completion percentage
- No critical DRC violations introduced by global route
- Clock net connectivity maintained
- Power net connectivity maintained
- Design rule violations < threshold (global route stage)

**Validated Result (Phase 12–13, test_counter)**:
- Global routing completion % within targets
- No new DRC violations introduced
- Clock and power net connectivity maintained

#### Stage 10: Detailed Routing

**Purpose**: Detailed routing of design

**Command** (OpenROAD via WSL or MCP):

```bash
# Via WSL
wsl -d Ubuntu-24.04 -- bash -lc '
  export PATH=/mnt/d/tools/openroad/bin:$PATH
  openroad detailed_route.tcl
'

# Via MCP
interactive_openroad_exec detailed_route.tcl
```

**Outputs**:
- `10_route.def` — OpenROAD detailed routed DEF
- Routed netlist
- SPEF file (parasitic extraction)
- DRC report (preliminary)
- Hold violation fixes

**Acceptance Checks**:
- Detailed routing completion % within targets
- DRC violations from routing identified and classified (helper regions vs. actual violations)
- Hold violations fixed or documented as risk
- SPEF valid (parasitic extraction from routed DEF)
- All cells with geometry and net-name labels

**Validated Result (Phase 12–13, test_counter)**:
- Detailed route.def valid
- DRC: 295 rule categories, 0 actual violations (15 helper regions excluded)
- SPEF generated and valid
- Hold violations addressed

#### Stage 11: Extraction

**Purpose**: Parasitic extraction via SPEF

**Command** (OpenROAD KLayout or per-stage script):

```bash
# Via WSL with KLayout (currently blocked on Windows)
# Direct OpenROAD extraction
wsl -d Ubuntu-24.04 -- bash -lc '
  export PATH=/mnt/d/tools/openroad/bin:$PATH
  openroad extract.tcl
'

# Or per-stage TCL
openroad -c "source extract.tcl"
```

**Outputs**:
- `11_final.spef` — Extracted parasitics
- SPEF file with RC values
- Extracted netlist connections

**Acceptance Checks**:
- SPEF file valid (parseable, non-zero RC values)
- Extracted nets match routed netlist
- No negative capacitance values
- All power-net parasitics correct (VPWR, VPB)
- All ground-net parasitics correct (VSS, VBB)

**Validated Result (Phase 13, test_counter)**:
- SPEF generated with valid RC values
- Power-net parasitics correct
- Ground-net parasitics correct

#### Stage 12: STA (Static Timing Analysis)

**Purpose**: Multi-corner STA (TT/SS/FF libraries)

**Command** (OpenSTA via WSL):

```bash
# Via WSL
wsl -d Ubuntu-24.04 -- bash -lc '
  LD_LIBRARY_PATH=/mnt/d/tools/opensta/lib
  /mnt/d/tools/opensta/bin/sta -version
  # Or: openroad sta script
'

# Via OpenROAD MCP
report_worst_slack -max -corner tt
report_worst_slack -min -corner tt  ;# hold
report_checks -corner tt ss ff
define_corners tt ss ff
read_liberty -corner tt <lib_tt>
read_liberty -corner ss <lib_ss>
read_liberty -corner ff <lib_ff>
```

**Key Syntax** (OpenSTA 3.1.0; no `foreach`; expand loops by hand):

```tcl
define_corners tt ss ff
read_liberty -corner tt <lib_file>
read_liberty -corner ss <lib_file>
read_liberty -corner ff <lib_file>
read_verilog <netlist.v>
link_design <top_module>
read_sdc <sdc_file>
report_checks -corner tt -max_paths 50
report_checks -corner ss -max_paths 50
report_checks -corner ff -max_paths 50
report_worst_slack -max -corner tt   ;# setup WNS (TT)
report_worst_slack -min -corner tt   ;# hold WNS (TT)
report_worst_slack -max -corner ss   ;# setup WNS (SS)
report_worst_slack -min -corner ss   ;# hold WNS (SS)
report_worst_slack -max -corner ff   ;# setup WNS (FF)
report_worst_slack -min -corner ff   ;# hold WNS (FF)
report_tns -max -corner tt
report_tns -max -corner ss
report_tns -max -corner ff
```

**Acceptance Checks**:
- TT corner: setup WNS ≥ 0, hold WNS ≥ 0
- SS corner: setup WNS ≥ 0, hold WNS ≥ 0
- FF corner: setup WNS ≥ 0, hold WNS ≥ 0
- 3 corners all pass (no violators)
- TNS reported per corner
- Setup and hold checks across all paths

**Validated Result (Phase 12–13, test_counter)**:
- TT: WNS 5.5678 ns / hold 0.5231 ns (0 violators)
- SS: WNS 3.7540 ns / hold 1.6507 ns (0 violators)
- FF: WNS 5.7656 ns / hold 0.3319 ns (0 violators)
- 3-corner run completes cleanly

#### Stage 13: GDSII

**Purpose**: DEF → GDSII conversion

**Important**: KLayout DEF→GDSII must use technology file's reader options so named vias get cut geometry (L1M1_PR_MR, M1M2_PR); default `Layout#add_lib` keeps cells as library references → written GDS has empty standard cells. Must copy shapes of `sky130_fd_sc_hd.gds` into layout cells explicitly. Must write net names as labels on routing layers — without labels, extracted netlist has auto-generated `synth_NN` names and top-level circuits cannot be correlated.

**Command** (KLayout via WSL; currently blocked on Windows):

```bash
# Via WSL (KLayout 0.30.13; not runnable on Windows)
wsl -d Ubuntu-24.04 -- bash -lc '
  /mnt/d/tools/KLayout/bin/klayout.sh -b -rd tech=sky130hd.lyt \
    -rd in_gds=design.gds -rd top_cell=top -rd out_gds=output.gds \
    -r script.rb
'

# Or per KLayout script
/mnt/d/tools/KLayout/bin/klayout.sh -b -r <script>.rb
```

**Windows Status**: BLOCKED — KLayout 0.30.13 extracted under `D:\tools\KLayout\`; not runnable (missing Qt5 system libraries: `libQt5Core.so.5`, `libgit2.so.1.7.1`); WSL invocation requires separate approval.

**Alternative (not yet validated)**:
- OpenROAD write_gds (not compiled in this build: `openroad_gui_compiled`=0, `openroad_gpu_compiled`=0)
- Third-party GDSII writers (not currently integrated)

**Outputs** (when executed):
- `test_counter.gds` or `soc_ctrl.gds` — GDSII stream
- Standard cell geometry (with tech-file reader options)
- Via cut layers
- Net-name labels on routing layers
- Top-cell hierarchy

**Acceptance Checks**:
- GDSII stream valid (parseable by KLayout or GDS II viewer)
- Standard cells have genuine geometry (not empty references)
- Via cut layers present
- Top-level port labels present and valid
- GDSII geometry consistent with DEF (after shape merging)

**Validated Result (Phase 9–13, test_counter)**:
- GDSII generated via KLayout DEF→GDSII with tech-file reader options
- 8 standard cells merged with genuine geometry
- 538 via cut shapes present
- 382 shapes on routing layers
- 15 real top-level port labels preserved
- LVS capable GDSII (via cut layers + net-name labels)

#### Stage 14: DRC

**Purpose**: Sky130 DRC execution

**Command** (KLayout via WSL; currently blocked on Windows):

```bash
# Via WSL (KLayout 0.30.13)
wsl -d Ubuntu-24.04 -- bash -lc '
  /mnt/d/tools/KLayout/bin/klayout.sh -b -rd tech=sky130hd.lydrc \
    -rd in_gds=design.gds -rd top_cell=top -rd out_rpt=report.rpt \
    -r drc_script.rb
'

# Or per KLayout drc script
/mnt/d/tools/KLayout/bin/klayout.sh -b -r drc_script.rb
```

**Windows Status**: BLOCKED — KLayout 0.30.13 extracted under `D:\tools\KLayout\`; not runnable (missing Qt5 system libraries).

**When Available** (validated Phase 12–13):
- `sky130hd.lydrc` deck (64,758 B)
- 295 rule categories evaluated
- 0 actual violations (15 helper regions excluded from violation count)
- Report database saved and reopened (145 categories, 0 items — totals agree with execution log)
- DRC report persistence: parent directory must exist before `ReportDatabase::save` (ENOENT on missing dir)

**Acceptance Checks**:
- 295 rule categories evaluated
- 0 actual violations (helper regions excluded from violation count)
- Report database saved and reopens verified
- Stored totals agree with execution log (0 items / 0 values)
- Only deck lines carrying `.output(...)` are rules; intermediate filter regions are NOT violations
- Classification before reporting (helper regions vs. actual violations)

**Validated Result (Phase 13, test_counter)**:
- 295 rule categories evaluated
- 0 actual violations
- 15 helper regions with non-zero counts excluded from violation count
- Report database saved (22,400 B) and reopened — 145 categories, 0 items; totals agree with log

#### Stage 15: LVS

**Purpose**: LVS (Layout vs Schematic) verification

**Command** (KLayout via WSL; currently blocked on Windows):

```bash
# Via WSL (KLayout 0.30.13)
wsl -d Ubuntu-24.04 -- bash -lc '
  /mnt/d/tools/KLayout/bin/klayout.sh -b -rd tech=sky130hd.lylvs \
    -rd in_gds=design.gds -rd cdl_file=reference.cdl -rd top_cell=top \
    -rd report_file=lvs_report.txt -r lvs_script.rb
'

# Or per LVS script
/mnt/d/tools/KLayout/bin/klayout.sh -b -r lvs_script.rb
```

**Windows Status**: BLOCKED — KLayout 0.30.13 extracted under `D:\tools\KLayout\`; not runnable on Windows.

**Validated Procedure (Phase 12–13, test_counter)**:

**Reference Netlist Generation**:
- Generate from routed ODB's ITerm → net mapping (NOT `write_cdl -masters`)
- `write_cdl -masters` emits every cell OUTPUT pin as `_unconnected_N` (LEF pin order puts output 6th, CDL masters puts it last)
- On `X` line: subcircuit name goes LAST (`X<inst> <nodes...> <cell>`); KLayout's SPICE reader takes the last token as cell name
- Only real top-level ports may carry a GDS text label; label on internal net makes extractor promote to top-level pin with no schematic counterpart → breaks comparison

**LVS Deck**: Official unmodified `sky130hd.lylvs`

**Reference Netlist** (`6_final_concat.cdl`):
- Design CDL generated from post-route OpenROAD netlist (`test_counter_final.v`)
- With official `sky130hd.cdl` cell pin orders
- Concatenated with official `sky130hd.cdl`

**LVS Execution**:
- KLayout SPICE reader: last token on X line = cell name
- GDSII read with tech-file reader options (via cut labels, not `Layout#add_lib`)
- Extracted netlist compared against reference

**Validated Result (Phase 13, test_counter)**:
- Official `sky130hd.lylvs` → "INFO: Congratulations! Netlists match."
- LVS database: `6_lvs.lvsdb` (231,754 B)
- 23 standard cells recognised, 240 transistors extracted
- Independently verified: 240 devices and 130 canonicalised nets on both sides
- All 13 device W/L classes match; all 14 cell transistor counts match
- Weisfeiler-Lehman refinement → 240/240 device-colour classes matched
- 0 layout-only, 0 schematic-only

**Acceptance Checks**:
- Reference netlist generated from routed ODB (not `write_cdl -masters`)
- Subcircuit name on X line goes LAST
- Only real top-level ports carry GDS text labels
- KLayout LVS deck: official, unmodified `sky130hd.lylvs`
- Netlists match: "Congratulations! Netlists match."
- LVS database produced and valid

**Execution Status**: LVS (stage 15) was executed in the documented baseline (16/16 stages PASS, 92 checks). The historically reported shell blocker (Phase 17B) no longer applies to the baseline command path; no separate soc_ctrl LVS run was performed.

### Acceptance Checks

Every stage has explicit independent acceptance checks. A stage is only reported PASS when all of its checks pass — a zero exit code from the tool is never sufficient.

**Check Classifications**: PASS, FAIL, BLOCKED, ERROR, INCONCLUSIVE, NOT_TESTED

**PASS Classification Requires**:
- All acceptance checks for the stage pass
- Fresh tool execution each time (not from cache)
- Evidence files produced and verified
- Exit code alone is never sufficient for PASS

### JSON Reporting

JSON reports with `schema_version`, per-stage status, per-check results, artifact paths, and artifact hashes.

**Report Files**:
- `report.json` — Complete regression JSON report
- `acceptance.json` — Per-stage acceptance check details
- `summary.md` — Human-readable summary

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
        "setup_wns_tt": "5.4550",
        "setup_wns_ss": "2.6745",
        "setup_wns_ff": "5.7003",
        "hold_wns_tt": "0.6159",
        "hold_wns_ss": "1.2335",
        "hold_wns_ff": "0.3917",
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

**Timing sign convention (SR-10):** setup and hold WNS are reported separately
and are non-negative for a zero-violator PASS. The older `wns_ff: -0.6159`
value was a sign/field transcription error (`0.6159` is TT hold slack); the
recorded FF setup WNS is `+5.7003` ns with FF hold `+0.3917` ns
(`D:\audit\second_review_evidence_20261118\repro\06_reproducibility_r1_vs_r2.json`).

### Artifact Organization

**Per-Design Workspace**:

```
workspace/run_<run_id>/
├── 01_synthesis/
│   ├── synth.v
│   ├── synth.log
│   └── synth_rpt.txt
├── 02_lec_generic/
│   ├── lec_generic.log
│   └── lec_generic_negative.log
├── ... (stages 03–15)
├── report.json          # Complete regression JSON report
├── acceptance.json      # Per-stage acceptance check details
└── summary.md           # Human-readable summary
```

**Cross-Design Isolation**:
- `test_counter`: `workspace/run_run_A` (Phase 14 baseline)
- `soc_ctrl`: `workspace/run_soc_ctrl_*` (Phase 17, when executed)
- Artifact prefixes (`test_counter` vs `soc_ctrl`) prevent mixing
- No shared mutable temporary files

### Negative Tests

Seven negative tests (N1–N7) inject faults/deficiencies into the regression flow to verify that each is independently detected.

**Test Results (Phase 15, 2026-11-14; historical)**:

| Test | Injection | Verdict | Notes |
|---|---|---|---|
| N1 | RTL corruption (`4'd10` → `4'd9`) | DETECTED | Synthesis failed on corrupted RTL |
| N2 | Missing SDC constraints | DETECTED | STA reported "No paths found" |
| N3 | Missing cell model (removed `clkinv`) | DETECTED | Prep reported missing UDP primitives |
| N4 | Disconnected power (removed met4 straps) | DETECTED | Power-shape count dropped |
| N5 | Missing GDS via (removed via-cut shapes) | DETECTED | Census detected via-cut removal |
| N6 | Deliberate DRC violation (removed all met1 shapes) | DETECTED | DRC reported actual violations |
| N7 | Incorrect LVS reference netlist | DETECTED | Net swap in `15a_lvs_refcdl.tcl`; LVS mismatch detected |

**N7 Fix Detail** (Phase 14 Exec 2 + Phase 15):
- **Root cause (Phase 14 Exec 1)**: N7 was NOT-DETECTED because the net-swap fault was injected into `6_final_concat.cdl`, which Stage 15 (`15_lvs.sh`) overwrites via `cat "$REF" "$REG_CDL" > "$CONCAT"`; also Stage 15 lacked full environment variables
- **Fix**: Net swap now injected into `15a_lvs_refcdl.tcl` (the reference-netlist generator) via Python preprocessing; swaps the first two net names in the X-line output
- **Verification**: The fault survives Stage 15 execution; LVS reports mismatch (DETECTED) ✅

**Negative Test Workflow** (post-fix):

1. `fresh_copy()` — clones the source run workspace to a clean copy under `$NEG/workspace`
2. `python3 - "$COPY/stages/15a_lvs_refcdl.tcl"` — injects net swap into the reference-netlist generator
3. `env REG_RUN="$COPY" ... bash "$COPY/stages/15_lvs.sh"` — runs LVS with full env
4. Grep `$COPY/15_lvs/lvs.log` for "Congratulations! Netlists match" / mismatch / error
5. `record N7` with verdict DETECTED / NOT-DETECTED / INCONCLUSIVE

### Failure Classification

| Result | Description | Action |
|---|---|---|
| **PASS** | All acceptance checks pass; fresh evidence present | Proceed to next stage |
| **FAIL** | One or more checks fail | Do not proceed; fix and re-check (or escalate via three-fix rule) |
| **BLOCKED** | Environment/tooling prevents check execution | Document blocker; do not claim FAIL or PASS |
| **ERROR** | Tool execution error (unexpected) | Investigate tool/env; re-run fresh; do not claim result |
| **INCONCLUSIVE** | Partial/corrupt output; fresh run recommended | Re-run tool fresh; do not claim result based on partial output |
| **NOT_TESTED** | Check not applicable to this stage | Document; do not count in pass/fail tallies |

### Reproducibility

**Phase 14 Baseline (2026-11-12; historical)**: REPRODUCIBLE — two independent runs (A & B) both OVERALL PASS with 88/88 acceptance checks (historical; 2026-11-16 baseline runs `baseline_20261116` / `_r2` recorded 95/95) across all stages; all 20 compared artefacts are bit-identical; GDSII geometry identical after timestamp stripping.

**Reproducibility Verification**:
- Run A and Run B both produce OVERALL PASS
- 88/88 acceptance checks pass per run (Phase 14 historical); current baseline 95/95
- 20/20 compared artefacts are bit-identical
- GDSII geometry identical after BGNLIB type-0x0001 timestamp records stripped (24+ bytes)
- Per-stage status mismatches: 0; Check-status mismatches: 0

**Artifact Comparison (Run A vs Run B)**:

| Artefact | Run A | Run B | Result |
|---|---|---|---|
| synth/test_counter_synth.v | IDENTICAL | IDENTICAL | ✅ |
| synth/generic_test_counter_synth.v | IDENTICAL | IDENTICAL | ✅ |
| ... | ... | ... | ... |
| test_counter_pdn.gds | IDENTICAL | IDENTICAL | ✅ |
| drc/drc_classification.txt | IDENTICAL | IDENTICAL | ✅ |
| 15_lvs/15_lvs.lvsdb | IDENTICAL | IDENTICAL | ✅ |
| 15_lvs/15_lvs_extracted.cir | IDENTICAL | IDENTICAL | ✅ |
| sta/sta_summary.txt | IDENTICAL | IDENTICAL | ✅ |
| lvs_compare.txt | IDENTICAL | IDENTICAL | ✅ |
| 6_final_concat.cdl | IDENTICAL | IDENTICAL | ✅ |

**GDSII Geometry Note**: Byte-identical except for BGNLIB type-0x0001 timestamp records (≥24 bytes), which are stripped before comparison.

### Shell Execution Blocker (historical, Phase 14–17A) — Resolved for Baseline

**Historical status (Phase 14–17A)**: the Code Mode shell issue returned exit code 1 with no output; at that time all 15 stages were reported blocked for live execution from the Code Mode environment.

**Current status (2026-11-16)**: the documented baseline command (`python -m assistant baseline`) executes all 16 stages PASS (95 checks: 92 stage + 3 manifest) on test_counter; live LEC/STA gates pass. soc_ctrl was simply not executed in this pass.

**Workaround (historical)**: Phase 17B work was completed via `read`/`write`/`edit` tools; evidence preserved and framework configuration verified via file inspection.

**Recovery actions (historical; baseline recovery achieved)**:
- `powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design test_counter` → verify REPRODUCIBLE
- `powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design soc_ctrl` → verify all 15 stages
- Git commit and push to remote
- Verify `origin/main` after authorized publication (see PUBLICATION_REPORT.md)

### Command Examples

```powershell
# Design selection (configuration only; no live execution)
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design test_counter
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design soc_ctrl

# Specific stages (dependencies assumed present)
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Stages 01_synthesis

# Clean run directory
$Clean = $true  # Remove-Item -Recurse -Force $RunDir before start

# Resume from previous state
$Resume = $true  # Skip completed stages; only run remaining

# Environment variables propagated by -Design parameter
# REG_DESIGN, REG_RTL, REG_SDC, REG_LIB_TT, REG_LIB_SS, REG_LIB_FF, REG_CDL, REG_NETLIST_MAPPED
```

### Distinguishing Configured Capabilities from Executed Validation

**Configured (framework supported, not live-executed)**:
- `run_regression.ps1 -Design test_counter` — parameter operational, stage scripts configured
- `run_regression.ps1 -Design soc_ctrl` — parameter operational, stage scripts configured
- All 15 stage scripts configured with design-aware paths
- Negative test suite N1–N7 configured (N7 fix verified in file content)
- Agent and skill configurations documented
- EDA tool paths documented (EDA_TOOL_REGISTRY.md)
- Framework limitations documented

**Executed (live verification)**:
- Full baseline regression on test_counter via `python -m assistant baseline`: 16/16 stages PASS, 95 checks (92 stage + 3 manifest), byte-identical netlists, reproduced twice (Phase 14 baseline plus 2026-11-16 validation)
- Live LEC (positive + negative control) and STA ran-vs-meets gates — PASS
- DRC, LVS, multi-corner STA and GDSII — executed as stages of the documented baseline (test_counter only)

**Not executed in this pass**:
- soc_ctrl full regression (framework configured; baseline design was test_counter)
- soc_ctrl DRC/LVS/STA/GDSII (no results claimed)
- Remote publication - not executed in this historical validation pass; completed later: `45e4251` pushed fast-forward to `origin/main` and verified (see `docs/reviews/PUBLICATION_REPORT.md`)

**Clear Distinction**:
- Configured: Framework supported, scripts in place
- Executed: Live tool invocation, fresh results, independent verification — demonstrated for test_counter via the documented baseline command

---

*Regression Documentation — main — Multi-Design v2.0*
*Generated: 2026-11-14 — All stage scripts, acceptance criteria, and evidence procedures verified via read-only file inspection*