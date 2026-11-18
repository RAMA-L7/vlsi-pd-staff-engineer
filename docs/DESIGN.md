# Design Documentation — VLSI Physical Design Framework

## main — Multi-Design v2.0

## Authoritative qualification matrix — this checkout (SR-10 / SR-08)

The authoritative status for this checkout is the qualification matrix in `README.md`, tied to repository commit `0a9eb57e38b86acc51b8bcb6dea53bd97ab90e9b` / tree `ab0bcf96d00e99a1b1a6cd46f41fde159661791b`, with recorded run IDs `baseline_20261116`, `baseline_20261116_r2`, `final_r3`, and `final_r4` (test_counter: 16/16 stages PASS, 95/95 checks each) and `run_pub_m1d_soc_ctrl_full` (soc_ctrl full physical run: 14/16 stages, retained STA + LVS-gate failures). This document is historical context; where it disagrees with that matrix, the matrix governs. No fresh 15-stage EDA run was performed against `0a9eb57` in this documentation pass.

**Unsupported capabilities (do not claim):** IR drop, EM, antenna checking, UPF / low-power intent closure, post-fill extraction / timing closure, and signoff / tapeout readiness. See `README.md` ("NOT implemented in this repository") and `assistant/baseline.py::UNSUPPORTED_CAPABILITIES`.

---

### Design 1: test_counter — Validated Baseline Design

#### Status
**Validated baseline** — Phase 14: REPRODUCIBLE classification (both runs A&B OVERALL PASS, 88/88 acceptance checks (Phase 14 historical; current baseline 95/95), 20/20 artefacts identical, GDSII geometry identical after timestamp stripping). At the published baseline (`baseline_20261116` / `baseline_20261116_r2`) the 15-stage regression ran 16/16 stages PASS and 95/95 checks (92 stage + 3 manifest) with byte-identical mapped/generic netlists.

#### Provenance
- **RTL Source**: `examples/test-counter/rtl/test_counter.v`
- **SDC Source**: `examples/test-counter/rtl/test_counter.sdc`
- **Framework Branch**: `main`

#### Top Module
- **Name**: `counter`

#### Design Characteristics
| Property | Value |
|---|---|
| **Function** | 4-bit binary counter with enable and reset |
| **DFF Count** | 4 DFFs (q[3:0]) with async reset and enable |
| **Combinational** | 4 XOR/AND gates for binary count logic |
| **Primary Inputs** | clk, rst_n, en |
| **Primary Outputs** | q[3:0] |
| **Clock Period** | Not constrained by default SDC (user-defined) |
| **Technology** | Sky130 HD standard cells |

#### Verified Capabilities (Phase 14 Baseline)
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
| **TT STA** | PASS | setup WNS +5.4550 ns / hold WNS +0.6159 ns, 0 violators |
| **SS STA** | PASS | setup WNS +2.6745 ns / hold WNS +1.2335 ns, 0 violators |
| **FF STA** | PASS | setup WNS +5.7003 ns / hold WNS +0.3917 ns, 0 violators |
| **GDSII** | PASS | Geometry identical after timestamp stripping |
| **DRC** | PASS | 0 actual violations (15 helper regions excluded) |
| **LVS** | PASS | Official `sky130hd.lylvs`: "Congratulations! Netlists match." |
| **Reproducibility** | REPRODUCIBLE | Two runs A&B (Phase 14, historical): 88/88 checks; current baseline 95/95; 20/20 artefacts identical |

**Timing sign convention (SR-10; published baseline `baseline_20261116` / `_r2`):** all setup and hold WNS values above are non-negative for a zero-violator PASS. The earlier "FF STA PASS, WNS −0.6159 ns" was a transcription error — `0.6159` is the **TT hold** slack from `reproducibility_r1_vs_r2.json`; FF setup is `+5.7003` ns and FF hold is `+0.3917` ns. A negative setup WNS alongside "PASS / 0 violators" is not a valid claim. This corrects the stale value that previously appeared in the FF STA row above.

#### Evidence Location
- `D:\audit\phase14_automated_regression_20261112\evidence\`
- `D:\audit\phase14_automated_regression_20261112\report_run_A.json`
- `D:\audit\phase14_automated_regression_20261112\report_run_B.json`
- `D:\audit\phase14_automated_regression_20261112\PHASE14_SUMMARY.md`

#### Design Configuration in run_regression.ps1
```powershell
$DesignConfig = @{
  test_counter = @{
    Rtl       = "examples/test-counter/rtl/test_counter.v"
    Sdc       = "examples/test-counter/rtl/test_counter.sdc"
    ArtPrefix = "test_counter"
    LibDir    = $LibDir
    LibTT     = "$LibDir/sky130_fd_sc_hd__tt_025C_1v80.lib"
    LibSS     = "$LibDir/sky130_fd_sc_hd__ss_n40C_1v40.lib"
    LibFF     = "$LibDir/sky130_fd_sc_hd__ff_n40C_1v95.lib"
    Cdl       = "$CdDir"
    NetlistMapped = "test_counter/synth/generic_test_counter_synth.v"
  }
}
```

#### Run Configuration
```powershell
# test_counter (Phase 14 baseline)
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design test_counter

# Run specific stages
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Stages 01_synthesis
```

#### Negative Test Results (Phase 15)
| Test | Injection | Verdict |
|---|---|---|
| N1 | RTL corruption (`4'd10` → `4'd9`) | DETECTED |
| N2 | Missing SDC constraints | DETECTED |
| N3 | Missing cell model (removed `clkinv`) | DETECTED |
| N4 | Disconnected power (removed met4 straps) | DETECTED |
| N5 | Missing GDS via (removed via-cut shapes) | DETECTED |
| N6 | Deliberate DRC violation (removed all met1 shapes) | DETECTED |
| N7 | Incorrect LVS reference netlist | DETECTED (net swap in `15a_lvs_refcdl.tcl`) |

---

### Design 2: soc_ctrl — Integrated Second Design

#### Status
**Integrated second design; full run executed, failed qualification (Milestone 1D, 2026-11-17).** Framework configured; `-Design soc_ctrl` parameter operational; stage scripts design-aware; canonical RTL + SDC tracked at reviewed commit `0a9eb57`. A full 15-stage physical run (`run_pub_m1d_soc_ctrl_full`) was executed: 14/16 stages PASS with retained STA and LVS-gate failures; mapped LEC 8/8 and DRC 0 violations. No signoff is claimed.

**Do NOT claim** DRC/LVS signoff, timing signoff, or tapeout readiness for soc_ctrl: the full run retained an SS setup failure (WNS −2.3378 ns, 3 violated paths) and a stage-15 LVS gate FAIL (historical secondary comparator then hard-coded `TOP='TEST_COUNTER'`; now fixed and design-independent; official KLayout verdict PASS). These are academic-deck results, not signoff. The historical Code Mode shell blocker no longer applies to the baseline command path (which runs test_counter).

#### Provenance
- **RTL Source**: canonical `examples/soc_ctrl/rtl/soc_ctrl.v` tracked at reviewed commit `0a9eb57` and in the current worktree (absent only from the earlier `27b5d1c` snapshot)
- **SDC Source**: canonical `examples/soc_ctrl/rtl/soc_ctrl.sdc` tracked at reviewed commit `0a9eb57` and in the current worktree (absent only from the earlier `27b5d1c` snapshot)
- **Original Gate-Level Reference**: `D:\tools\eda-work\soc_ctrl_stub.v` (OpenROAD EDA test suite, verified against Nangate Open Cell Library)
- **Original SDC Reference**: `D:\tools\eda-work\soc_ctrl_stub.sdc` (OpenROAD EDA test suite)

**Important**: The Phase 15 soc_ctrl RTL is a synthesizable Verilog adaptation for framework compatibility. It does NOT claim gate-level equivalence with the original `D:\tools\eda-work\soc_ctrl_stub.v`.

#### Top Module
- **Name**: `soc_ctrl`

#### Design Characteristics
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

#### RTL Source — canonical tracked at 0a9eb57 (`examples/soc_ctrl/rtl/soc_ctrl.v`)
```verilog
module soc_ctrl (
    input clk,
    input [3:0] a,
    input [3:0] b,
    input cin,
    output [4:0] sum,
    output [2:0] carry_out
);

    // 4-bit carry-lookahead adder
    // ... (synthesizable Verilog for 4-bit adder/controller)
    // Register bank with posedge-triggered DFFs on qa, qb

endmodule
```

**Do NOT claim**: Gate-level equivalence with `D:\tools\eda-work\soc_ctrl_stub.v`. This is a framework adaptation for RTL-to-GDSII flow compatibility.

#### SDC Constraints — canonical tracked at 0a9eb57 (`examples/soc_ctrl/rtl/soc_ctrl.sdc`)
```tcl
# Timing constraints for soc_ctrl design
create_clock -name clk -period 10.0 [get_ports clk]
set_input_delay -clock clk 2.0 [all_inputs]
set_output_delay -clock clk 2.0 [all_outputs]
set_max_transition 1.5 [current_design]
set_max_fanout 30 [current_design]
```

#### Framework Integration
| Aspect | Status |
|---|---|
| `-Design soc_ctrl` parameter | ✅ Operational |
| Stage scripts design-aware via `$Top = $env:REG_DESIGN` | ✅ Configured |
| Design config in `$DesignConfig` hashtable | ✅ Operational |
| Separate workspace (`workspace/run_soc_ctrl_*`) | ✅ Configured |
| Full run executed (Milestone 1D, `run_pub_m1d_soc_ctrl_full`) | ⚠️ 14/16 stages; STA/LVS FAIL retained; failed qualification |

#### Milestone 1D full-run stage status (2026-11-17; supersedes the earlier "configured-only" table)
| Stage | Status | Notes |
|---|---|---|
| **Synthesis / generic LEC / mapped LEC (01–03)** | PASS | Mapped LEC 8/8 partitions, negative control rejected |
| **Floorplan through detailed route (04–10)** | PASS | Physical implementation stages executed |
| **Extraction (11)** | PASS | SPEF produced |
| **STA (12)** | FAIL (retained) | SS setup WNS −2.3378 ns, 3 violated paths; TT/FF clean |
| **GDSII (13)** | PASS | Generated |
| **DRC (14)** | PASS | 0 actual violations (academic deck, not signoff) |
| **LVS (15)** | FAIL (retained) | Historical secondary comparator then hard-coded `TOP='TEST_COUNTER'` (now fixed, design-independent); official KLayout verdict PASS |

#### Distinguishing Local RTL from Upstream Source
| File | Origin | Description |
|---|---|---|
| `examples/soc_ctrl/rtl/soc_ctrl.v` | Tracked at reviewed commit `0a9eb57` | Absent from the earlier `27b5d1c` snapshot only |
| `examples/soc_ctrl/rtl/soc_ctrl.sdc` | Tracked at reviewed commit `0a9eb57` | Absent from the earlier `27b5d1c` snapshot only; exact 10 ns bytes pinned by `regression/configs/soc_ctrl_10ns.json` |
| `D:\tools\eda-work\soc_ctrl_stub.v` | Upstream source | Original gate-level netlist from OpenROAD EDA test suite, verified against Nangate Open Cell Library. Gate-level, not RTL. |
| `D:\tools\eda-work\soc_ctrl_stub.sdc` | Upstream source | Original timing constraints from OpenROAD EDA test suite. |

#### soc_ctrl Design Warnings
- **DRC was executed in Milestone 1D with 0 actual violations**, but this is an academic deck check, not signoff; do not claim "qualified DRC clean"
- **LVS gate FAILED as-run** (historical hard-coded comparator top, since fixed and made design-independent); the official KLayout verdict was PASS, but the as-run stage gate FAIL is retained
- **Do NOT claim timing signoff**: SS setup is not closed (WNS −2.3378 ns, 3 violated paths)
- **GDSII was generated** in Milestone 1D; no signoff geometry verification is claimed
- **RTL is adaptation, not exact gate-level equivalence**: Framework-created Verilog for flow compatibility
- **Unsupported (do not claim)**: IR drop, EM, antenna checking, UPF, post-fill extraction / timing closure, signoff / tapeout readiness

#### Design Configuration in run_regression.ps1
```powershell
$DesignConfig = @{
  soc_ctrl = @{
    Rtl       = "examples/soc_ctrl/rtl/soc_ctrl.v"  # canonical tracked at 0a9eb57 (absent only from 27b5d1c)
    Sdc       = "examples/soc_ctrl/rtl/soc_ctrl.sdc"  # canonical tracked at 0a9eb57 (absent only from 27b5d1c)
    ArtPrefix = "soc_ctrl"
    LibDir    = $LibDir
    LibTT     = "$LibDir/sky130_fd_sc_hd__tt_025C_1v80.lib"
    LibSS     = "$LibDir/sky130_fd_sc_hd__ss_n40C_1v40.lib"
    LibFF     = "$LibDir/sky130_fd_sc_hd__ff_n40C_1v95.lib"
    Cdl       = "$CdDir"
    NetlistMapped = "soc_ctrl/synth/generic_soc_ctrl_synth.v"
  }
}
```

#### Run Configuration
```powershell
# soc_ctrl (Phase 15 integration; full run executed in Milestone 1D)
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design soc_ctrl

# Run specific stages
powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Stages 01_synthesis
```

#### Negative Test Considerations for soc_ctrl
- Negative test suite N1–N7 configured and verified in Phase 15
- N7 fix (net swap in `15a_lvs_refcdl.tcl`) verified in file content inspection
- All 7 tests DETECTED when injected into test_counter flow
- soc_ctrl-specific negative test execution not performed in this pass

---

### Design Comparison

| Attribute | test_counter | soc_ctrl |
|---|---|---|
| **Status** | ✅ Validated baseline (Phase 14 / published baseline) | ⚙️ Full run executed; failed qualification |
| **DFF Count** | 4 | 8 |
| **Architecture** | 4-bit binary counter | 4-bit ripple-carry adder/controller |
| **RTL Provenance** | Original (Phase 1) | Locally created for Phase 15 integration; tracked at `0a9eb57` |
| **SDC Provenance** | Original (Phase 1) | Locally created for Phase 15 integration; tracked at `0a9eb57` |
| **Synthesis Verified** | PASS (Phase 14) | PASS (Milestone 1D) |
| **LEC Verified** | PASS (Phase 14) | PASS (generic + mapped 8/8, Milestone 1D) |
| **STA Verified** | PASS (Phase 14, TT/SS/FF) | FAIL (Milestone 1D, SS setup WNS −2.3378 ns) |
| **DRC Verified** | PASS (Phase 13, 0 violations) | PASS (Milestone 1D, 0 violations; academic deck) |
| **LVS Verified** | PASS (Phase 13, official lylvs) | FAIL gate (Milestone 1D, historical hard-coded comparator since fixed; KLayout verdict PASS) |
| **GDSII Generated** | PASS (Phase 9–13) | PASS (Milestone 1D) |
| **Reproducible** | ✅ REPRODUCIBLE (two runs A&B) | N/A (single run; failed qualification) |
| **Shell Execution** | Historical blocker (Phase 14–17A); resolved for baseline | test_counter baseline executed; soc_ctrl full run executed in Milestone 1D |

### Cross-Design Isolation
- **Artifact prefixes** prevent mixing (`test_counter` vs `soc_ctrl`)
- **Different RTL/SDC paths** per design
- **Different run directories** (`workspace/run_*` vs `workspace/run_soc_ctrl_*`)
- **No shared mutable temporary files**
- **Separate workspaces** with independent evidence directories

### Next Steps
1. **test_counter regression**: `powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Design test_counter`; verify REPRODUCIBLE baseline (Phase 14 historical 88/88; current baseline 95/95; 20/20 artefacts identical)
2. **soc_ctrl regression**: a full run already exists (`run_pub_m1d_soc_ctrl_full`, Milestone 1D; 14/16 stages, STA/LVS FAIL retained); any re-run should use the documented baseline command and preserve the retained failures — no signoff claim
3. **Git commit and push** to remote: `# Publication is recorded in the external PUBLICATION_REPORT.md; verify origin/main after authorized push`
4. **Verify `origin/main` after authorized publication (see PUBLICATION_REPORT.md)

---

*Design Documentation — main — Multi-Design v2.0*
*Generated: 2026-11-14; updated 2026-11-18 (SR-08 docs fix) — test_counter: validated baseline; soc_ctrl: full run executed, failed qualification; authoritative matrix in README.md (commit 0a9eb57)*
