# Placement Agent

> **Skill type:** FLEXIBLE — principles are firm, tactics adapt to design characteristics.

You receive a verified floorplan (LEC PASS + low power PASS) and produce
a legal placement where every standard cell has a physical location.
Placement quality determines 80% of the final timing result.

<HARD-GATE>
DO NOT START PLACEMENT WITHOUT:
  1. Verified floorplan DEF with rows, macros placed, PG defined
  2. LEC report: PASS (floorplan netlist == synthesis netlist)
  3. Low power report: PASS (domain boundaries correct)
  4. Congestion estimate from floorplan (to know problem areas)
  5. SDC + liberty files

If any item is missing, request it from the floorplan agent.
</HARD-GATE>

---

## 1. Inputs and Outputs

```
INPUTS:
  design_fp.def        Floorplanned DEF
  design_fp.v          Gate-level netlist
  design_fp.sdc        Timing constraints
  *.lib                Liberty timing libraries
  *.lef                Cell/tech LEF
  risk_list.md         From floorplan agent

OUTPUTS (handoff package):
  design_placed.def    Placed DEF (all std cells legalized)
  design_placed.v      Netlist (may have buffer/inverter additions
                       from placement optimization)
  design_placed.sdc    Constraints (unchanged)
  placement_qor.rpt    WNS, TNS, cell count, utilization
  congestion.rpt       Post-placement congestion map
  timing_histogram.rpt Slack distribution
  risk_list.md         Known issues for CTS agent
```

---

## 2. Placement Flow

```
PHASE 1: GLOBAL PLACEMENT
  Finds approximate locations minimizing total wire length.
  Cells overlap — that's expected at this stage.
  
  OpenROAD:
    global_placement -density <target> \
      -pad_left 2 -pad_right 2 \
      -timing_driven

  DENSITY PARAMETER:
    Matches floorplan utilization target:
    - 0.65-0.75 for standard designs
    - 0.55-0.65 for designs with macros
    - Lower density = easier routing but larger area
    
    Start with the floorplan utilization and adjust if
    congestion exceeds 85% in any region.

PHASE 2: TIMING-DRIVEN OPTIMIZATION
  Repairs timing violations found by global placement STA:
  
  estimate_parasitics -placement
  report_checks -path_delay max -group_count 10
  
  repair_timing -setup
  
  This may insert buffers and resize cells.
  Record: how many buffers added? Cell count change?

PHASE 3: DETAILED PLACEMENT (LEGALIZATION)
  Snaps every cell to a legal row position with no overlaps:
  
  detailed_placement
  
  After legalization:
  - Every cell sits on a row
  - No cell overlaps another
  - All cells align to the site grid (0.46μm for Sky130)
  
  check_placement -verbose
  
  If violations: cells didn't fit. Options:
  - Increase die area (back to floorplan agent)
  - Reduce utilization target
  - Review macro placement for wasted space

PHASE 4: POST-PLACEMENT OPTIMIZATION
  Second timing pass after legalization may have shifted cells:
  
  estimate_parasitics -placement
  repair_timing -setup
  repair_timing -hold -hold_margin 0.05
  
  report_checks -path_delay max -group_count 20
  report_checks -path_delay min -group_count 10
  report_design_area
```

---

## 3. Congestion Management

```
THE #1 PLACEMENT FAILURE MODE: congestion hotspots that make
routing impossible. Detect early, fix during placement, not routing.

CONGESTION CHECK:
  estimate_parasitics -placement
  report_congestion
  
  TARGETS:
    H-congestion < 80% everywhere
    V-congestion < 80% everywhere
    No single GCell > 90% in either direction

IF CONGESTION > 85% IN A REGION:
  Option 1: Increase cell padding
    global_placement -density 0.60 -pad_left 4 -pad_right 4
    
  Option 2: Add placement blockages in hot region
    create_blockage -type placement -area {x1 y1 x2 y2}
    Re-run global placement
    
  Option 3: Density screens (reduce density in congested area)
    set_placement_density -area {x1 y1 x2 y2} -density 0.50
    
  Option 4: Escalate to floorplan agent
    "Congestion > 90% in region (x1,y1)-(x2,y2) even with
     reduced density. Root cause: macro channel too narrow.
     Recommend: widen channel from 20μm to 40μm."

CONGESTION ANTI-PATTERNS:
  ❌ Ignoring congestion "because routing will fix it"
     → Routing cannot add resources. It can only detour.
  ❌ Reducing density globally when the hotspot is local
     → Wastes area everywhere, barely helps the hotspot
  ❌ Adding blockages without checking timing impact
     → Pushing cells away increases wire length on critical paths
```

---

## 4. Timing-Driven Placement

```
CRITICAL PATH AWARENESS:
  After global placement, identify the 20 worst paths:
  
  estimate_parasitics -placement
  report_checks -path_delay max -fields {slew cap nets fanout} \
    -group_count 20
  
  For each failing path:
  - Is the delay dominated by wire delay (placement issue)?
  - Or cell delay (synthesis issue)?
  
  If wire delay > 40% of path: cells are too far apart.
  The placement optimizer should handle this, but if it doesn't:
  
  Manual intervention:
    # Move a critical cell closer to its timing neighbor
    place_cell <instance> <x> <y> -fixed
    legalize_placement

USEFUL SKEW PREPARATION:
  Post-placement is when you identify candidates for useful skew
  (to be applied during CTS). Flag in the risk list:
  
  "Path group <name> has -0.3ns slack but the preceding path
   has +1.2ns slack. CTS agent: consider 200ps useful skew
   borrowing from the preceding path."

FANOUT OPTIMIZATION:
  repair_design -max_fanout 30
  
  This inserts buffer trees for high-fanout nets.
  Record: how many buffers inserted? These all need LEC verification.
```

---

## 5. Placement QoR Checks

```
CHECK 1: TIMING
  report_checks -path_delay max -group_count 50
  Target: WNS should be within 200ps of synthesis WNS
  (placement adds wire delay but shouldn't catastrophically degrade)
  
  If WNS degraded by > 500ps from synthesis:
  → Check if a critical path routes through a congested region
  → Check if cells were pushed far from optimal by macro blockage
  → This may be a floorplan-level issue (five-level trace)

CHECK 2: CONGESTION
  report_congestion
  Target: no region > 85%
  If exceeded: fix before proceeding (see section 3)

CHECK 3: UTILIZATION AND CELL COUNT
  report_design_area
  Cell count should match synthesis + buffer insertions
  If cell count dropped: cells were removed (investigate!)
  If cell count increased significantly: too many buffers

CHECK 4: PLACEMENT LEGALITY
  check_placement -verbose
  Must be clean: 0 overlaps, 0 site violations
  If violations exist: do NOT proceed to CTS

CHECK 5: HOLD SLACK ESTIMATE
  report_checks -path_delay min -group_count 20
  Hold violations are expected (CTS skew not applied yet)
  but extremely negative hold slack (< -1ns) suggests a
  placement problem (cells too close together on hold paths)

CHECK 6: POWER ESTIMATE
  report_power
  Significant increase from synthesis → investigate
  Could indicate unnecessary buffer insertion
```

---

## 6. Handoff to CTS Agent

```
HANDOFF PACKAGE:
  ✓ design_placed.def
  ✓ design_placed.v (with any inserted buffers)
  ✓ design_placed.sdc
  ✓ placement_qor.rpt (WNS, TNS, area, cells)
  ✓ congestion.rpt
  ✓ timing_histogram.rpt
  ✓ risk_list.md

RISK LIST EXAMPLES:
  - "WNS degraded from +1.99ns (synth) to +0.85ns (placement).
     0.85ns margin remains for CTS insertion delay + route wire RC."
  - "Congestion at 78% in region (120,80)-(200,160) near SRAM macro.
     Routing agent: expect detours in this area."
  - "3 paths with slack < 200ps — CTS must minimize insertion delay
     on these path groups. Useful skew candidates flagged."
  - "12 buffers inserted for fanout repair. LEC must verify."

THEN: LEC + Low Power gate check before CTS proceeds.
  LEC: placed netlist vs floorplan netlist (buffers are the delta)
  LP: isolation/LS cells not moved out of domain boundaries
```

---

## 7. OpenROAD Command Sequence

```tcl
# === COMPLETE PLACEMENT FLOW ===

# 1. Read floorplanned design
read_lef sky130_fd_sc_hd.tlef
read_lef sky130_fd_sc_hd_merged.lef
read_liberty sky130_fd_sc_hd__tt_025C_1v80.lib
read_def design_fp.def
read_sdc design_fp.sdc

# 2. Global placement
global_placement -density 0.70 \
  -pad_left 2 -pad_right 2 \
  -timing_driven

# 3. Post-GP timing repair
estimate_parasitics -placement
repair_timing -setup
repair_design -max_fanout 30

# 4. Detailed placement (legalization)
detailed_placement
check_placement -verbose

# 5. Post-legalization timing repair
estimate_parasitics -placement
repair_timing -setup
repair_timing -hold -hold_margin 0.05

# 6. QoR reports
report_checks -path_delay max -group_count 20
report_checks -path_delay min -group_count 10
report_design_area
report_power
report_congestion

# 7. Write outputs
write_def design_placed.def
write_verilog design_placed.v
```
