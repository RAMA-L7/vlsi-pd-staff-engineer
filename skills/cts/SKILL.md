# CTS Agent (Clock Tree Synthesis)

> **Skill type:** RIGID — follow exactly. CTS is where Bug #3 lives.

You receive a verified placed design (LEC PASS + low power PASS) and build
the clock distribution network. CTS is the most aggressive netlist
transformation in the PD flow — you will insert hundreds of buffers into a
verified netlist. Every one of them must be in the correct power domain.

<HARD-GATE>
DO NOT START CTS WITHOUT:
  1. Legally placed DEF with no placement violations
  2. LEC report: PASS (placed netlist vs floorplan netlist)
  3. Low power report: PASS (isolation/LS in correct domains)
  4. SDC with clock definitions
  5. Risk list from placement agent (margin remaining)

AFTER CTS COMPLETES:
  The low power check on CTS output is THE MOST CRITICAL
  verification in the entire PD flow. Every inserted buffer
  must be checked. Do not skip this. Bug #3 kills silicon.
</HARD-GATE>

---

## 1. Inputs and Outputs

```
INPUTS:
  design_placed.def    Legally placed design
  design_placed.v      Post-placement netlist (with buffers)
  design_placed.sdc    Timing constraints with clock definitions
  *.lib                Liberty libraries
  *.lef                Cell/tech LEF

OUTPUTS (handoff package):
  design_cts.def       CTS DEF (clock tree cells placed)
  design_cts.v         CTS netlist (hundreds of new cells)
  design_cts.sdc       Updated SDC (propagated clocks)
  cts_timing.rpt       Post-CTS STA (setup + hold)
  clock_tree.rpt       Skew, insertion delay, levels, DRVs
  clock_buffer_list.rpt Every inserted buffer + its power domain
  congestion.rpt       Updated congestion after CTS cell insertion
  risk_list.md         Known issues for routing agent
```

---

## 2. CTS Configuration

```
CLOCK TREE TARGETS:
  Skew target:       < 50ps (balanced across all sink groups)
  Insertion delay:   minimize (every ps of insertion delay
                     reduces setup margin for the routing agent)
  Max transition:    match SDC max_transition constraint
  Max capacitance:   match SDC max_capacitance constraint

BUFFER SELECTION (Sky130):
  Clock buffers:   sky130_fd_sc_hd__clkbuf_1/2/4/8/16
  Clock inverters: sky130_fd_sc_hd__clkinv_1/2/4/8/16
  
  CTS will auto-select drive strengths based on load.
  For low-power designs: ensure always-on variants exist
  in the library. If the library lacks always-on clock buffers,
  FLAG THIS AS A BLOCKER — standard buffers in switchable
  domains will cause Bug #3.

  OpenROAD CTS config:
    set_wire_rc -clock \
      -resistance <r_per_um> \
      -capacitance <c_per_um>
    
    clock_tree_synthesis -root_buf sky130_fd_sc_hd__clkbuf_16 \
      -buf_list {sky130_fd_sc_hd__clkbuf_1 
                 sky130_fd_sc_hd__clkbuf_2
                 sky130_fd_sc_hd__clkbuf_4
                 sky130_fd_sc_hd__clkbuf_8
                 sky130_fd_sc_hd__clkbuf_16} \
      -sink_clustering_enable \
      -balance_levels
```

---

## 3. Clock Domain Handling

```
SINGLE CLOCK:
  Standard CTS — one tree from clock port to all sequential sinks.

MULTIPLE CLOCKS:
  Each clock gets its own tree. Inter-clock paths (CDC) are
  handled by SDC false_path or set_clock_groups, NOT by CTS.
  
  CTS must NOT try to balance skew between independent clocks.
  It MUST balance skew within each clock domain.

GENERATED CLOCKS:
  Dividers, multipliers, gated clocks — these create sub-trees.
  CTS must preserve the relationship between parent and generated
  clock. The SDC defines this:
    create_generated_clock -source <parent> -divide_by 2 ...
  
  CTS reads these and builds appropriate sub-trees.

CLOCK GATING:
  Clock gating cells (ICGs) are in the clock path.
  CTS must treat them as intermediate nodes, not sinks.
  The tree goes: root → CG_cell → downstream_sinks
  
  OpenROAD handles this automatically if ICGs are properly
  identified in the liberty file (clock_gating_integrated_cell).
```

---

## 4. Useful Skew

```
WHAT: Intentionally unbalance clock arrival to borrow time
from a path with excess slack.

WHEN: A specific endpoint fails setup by a small margin, AND
the path feeding that endpoint's launch flop has excess slack.

EXAMPLE:
  Path A → reg_b: slack = -0.08ns (fails)
  Path X → reg_a: slack = +0.60ns (excess)
  
  By delaying reg_b's clock by 100ps:
  - Path A → reg_b: slack improves by 100ps → +0.02ns (passes)
  - Path reg_b → next: slack decreases by 100ps (check it still passes)

IMPLEMENTATION IN OpenROAD:
  CTS doesn't directly support useful skew targets per-sink.
  Instead, apply post-CTS clock buffer insertion:
  
  # After CTS, adjust specific sink timing:
  set_clock_latency -source -rise 0.1 [get_pins reg_b/CLK]
  
  OR manually insert a delay buffer in the clock path to reg_b.

CAUTION:
  Every ps of useful skew borrowed from setup makes hold harder.
  Check BOTH setup and hold after applying useful skew.
  The routing agent will need to insert hold buffers on the
  paths where you borrowed time.
```

---

## 5. Post-CTS Verification (CRITICAL)

```
THIS IS THE MOST IMPORTANT SECTION IN THIS SKILL.

CTS inserted hundreds of cells. Each must be verified.

CHECK 1: CLOCK TREE QoR
  report_cts
  
  Verify:
  - Skew < target (50ps)
  - Insertion delay reasonable (not > 1ns for small designs)
  - No clock DRVs (max_transition on clock nets)
  - Buffer count makes sense for design size
  - Tree depth/levels consistent across sink groups

CHECK 2: TIMING (post-CTS STA)
  estimate_parasitics -placement
  report_checks -path_delay max -group_count 50
  report_checks -path_delay min -group_count 20
  
  EXPECTED:
  - Setup WNS will degrade from placement (CTS adds insertion delay)
  - Some hold violations (expected — routing fixes these)
  
  RED FLAGS:
  - WNS degraded by > 1ns → CTS insertion delay too high
  - Paths that were fine post-placement now fail by large margin
  - Large number of new hold violations (> 50) → excessive skew

CHECK 3: CLOCK BUFFER DOMAIN CHECK (Bug #3 prevention)
  Generate a list of EVERY inserted buffer with its:
  - Instance name
  - Cell type (clkbuf_4 vs always-on variant)
  - Physical location (x, y)
  - Power domain (from UPF or physical region)
  
  For EACH buffer in a switchable domain:
    Is it an always-on library variant?
    → YES: OK
    → NO: VIOLATION — Bug #3. Flow stops.
  
  This check is performed by the low power agent, not CTS.
  But CTS must PRODUCE the clock_buffer_list.rpt that enables it.

CHECK 4: CONGESTION UPDATE
  report_congestion
  CTS cells add to congestion. If a region was at 78% before CTS
  and is now at 88%, flag it for the routing agent.
```

---

## 6. CTS Failure Modes

```
FAILURE: Skew target not met
  Cause: Large die with uneven sink distribution
  Fix: Re-cluster sinks, adjust CTS constraints
  If 3 attempts fail: escalate to placement agent
  (cells may need to be moved for better balance)

FAILURE: Excessive insertion delay
  Cause: Too many buffer levels, wrong root buffer size
  Fix: Increase root buffer drive strength, reduce levels
  Impact: Every ps of insertion delay eats setup margin

FAILURE: Clock DRVs (transition violations)
  Cause: Long clock wires with insufficient buffering
  Fix: Add buffers to split long segments
  Impact: DRVs on clock nets cause unreliable clocking

FAILURE: CTS can't reach some sinks
  Cause: Macro blockage between clock root and sinks
  Fix: Floorplan issue — macro blocking clock path
  Escalate: to floorplan agent to adjust macro position

FAILURE: Low power violation (Bug #3)
  Cause: CTS placed standard buffer in switchable domain
  Fix: Swap to always-on variant, or constrain CTS to avoid
       placing buffers in switchable regions
  THIS IS NOT OPTIONAL. Fix before proceeding.
```

---

## 7. Handoff to Routing Agent

```
HANDOFF PACKAGE:
  ✓ design_cts.def
  ✓ design_cts.v (with all CTS-inserted cells)
  ✓ design_cts.sdc (propagated clocks)
  ✓ cts_timing.rpt (setup + hold)
  ✓ clock_tree.rpt (skew, insertion delay, levels)
  ✓ clock_buffer_list.rpt (every buffer + domain)
  ✓ congestion.rpt (updated)
  ✓ risk_list.md

CRITICAL RISK LIST ITEMS:
  - Remaining timing margin (WNS) for routing wire delay
  - Number of hold violations (routing must fix with buffers)
  - Congestion hotspots (routing must navigate around)
  - Any useful skew applied (affects hold fixing strategy)
  - Paths with < 200ps margin (routing must prioritize)

THEN: LEC + Low Power gate check (MOST CRITICAL IN FLOW)
  LEC: CTS netlist vs placed netlist (hundreds of new cells)
  LP: EVERY CTS buffer checked for domain + cell type
```

---

## 8. OpenROAD Command Sequence

```tcl
# === COMPLETE CTS FLOW ===

# 1. Read placed design
read_lef sky130_fd_sc_hd.tlef
read_lef sky130_fd_sc_hd_merged.lef
read_liberty sky130_fd_sc_hd__tt_025C_1v80.lib
read_def design_placed.def
read_sdc design_placed.sdc

# 2. Set wire RC for clock nets
set_wire_rc -clock \
  -resistance 1.3e-01 \
  -capacitance 7.7e-02

# 3. Run CTS
clock_tree_synthesis \
  -root_buf sky130_fd_sc_hd__clkbuf_16 \
  -buf_list {sky130_fd_sc_hd__clkbuf_1
             sky130_fd_sc_hd__clkbuf_2
             sky130_fd_sc_hd__clkbuf_4
             sky130_fd_sc_hd__clkbuf_8
             sky130_fd_sc_hd__clkbuf_16} \
  -sink_clustering_enable \
  -balance_levels

# 4. Post-CTS optimization
set_propagated_clock [all_clocks]
estimate_parasitics -placement
repair_timing -setup
repair_timing -hold -hold_margin 0.05

# 5. CTS QoR
report_cts
report_checks -path_delay max -group_count 20
report_checks -path_delay min -group_count 20
report_design_area
report_congestion

# 6. Generate clock buffer list for LP verification
# (extract from CTS report or netlist diff)

# 7. Write outputs
write_def design_cts.def
write_verilog design_cts.v
```
