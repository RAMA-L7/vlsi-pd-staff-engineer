# Floorplan Agent

> **Skill type:** FLEXIBLE — principles are firm, tactics adapt to the design.

You receive a verified synthesis netlist (LEC PASS + low power PASS) and
produce a floorplan that gives placement and routing room to succeed. A bad
floorplan is the #1 root cause of timing closures that fail at the last mile.

<HARD-GATE>
DO NOT START FLOORPLANNING WITHOUT:
  1. Verified gate netlist (.v) from synthesis agent
  2. LEC report: PASS
  3. Low power report: PASS (if UPF design)
  4. Updated SDC (propagated from synthesis)
  5. QoR summary with timing targets

If any item is missing, request it from the synthesis agent.
</HARD-GATE>

---

## 1. Inputs and Outputs

```
INPUTS:
  design_synth.v       Gate-level netlist
  design.sdc           Timing constraints (propagated)
  design.upf           Power intent (if low power)
  *.lib                Liberty timing libraries
  *.lef                Library Exchange Format (tech + cell LEF)
  synthesis_targets.json  QoR targets

OUTPUTS (handoff package):
  design_fp.def        Floorplanned DEF (die, rows, macros, pins, PG)
  design_fp.v          Netlist (should be IDENTICAL to input — floorplan
                       doesn't change logic, but tool may annotate)
  design_fp.sdc        Constraints (unchanged, carried forward)
  floorplan_qor.rpt    Utilization, aspect ratio, macro count
  congestion_est.rpt   Early congestion estimate
  power_grid.rpt       IR drop estimate on initial PG
  risk_list.md         Known issues for placement agent
```

---

## 2. Die Sizing

```
UTILIZATION TARGETS (Sky130 guidance - HEURISTIC starting points,
finding M: not fixed laws; always re-validate against THIS design's
synthesis area, macro count, pin density and routing demand):
  No macros:     65-75% utilization target
  With macros:   55-65% utilization target (macros eat routing resources)
  High-perf:     50-60% (need routing room for buffers + shielding)

FORMULA:
  die_area = (std_cell_area + macro_area) / target_utilization
  
  For Sky130 with your design:
    std_cell_area ≈ 16,779 (from synthesis QoR)
    target_util = 0.70 (no macros)
    die_area ≈ 16,779 / 0.70 ≈ 23,970 μm²
    die_side ≈ √23,970 ≈ 155 μm (if square)

ASPECT RATIO:
  Default to 1.0 (square) unless:
  - Pin constraints force a specific shape
  - Package cavity dictates aspect ratio
  - Datapath designs often benefit from 2:1 (wide)
  
  OpenROAD command:
    initialize_floorplan -die_area {0 0 <width> <height>} \
      -core_area {<margin> <margin> <width-margin> <height-margin>} \
      -site unithd

CORE MARGIN:
  Minimum 5μm on all sides for Sky130
  Increase to 10μm if design has many I/O pads
  The margin provides space for I/O ring, power rings, corner cells
```

---

## 3. Macro Placement

This is the single most impactful decision in the entire PD flow.
A misplaced macro causes timing failures that no amount of ECO can fix.

```
MACRO PLACEMENT PRINCIPLES:

  1. DATA FLOW ALIGNMENT
     Place macros along the data flow direction.
     If data enters from the left pins, flows through logic,
     and writes to SRAM on the right, the SRAM goes on the right.
     
     Do NOT place macros based on area packing efficiency.
     A tightly packed macro arrangement that fights the data flow
     creates long wires that blow timing.

  2. CHANNEL WIDTH
     Leave routing channels between macros (these micron numbers are
     HEURISTIC starting points, finding M - validate against this
     design's layer pitch, bus widths and router DRC rules; a channel
     that passes this estimate can still starve on real congestion):
       Minimum: 20μm between macros (Sky130)
       Preferred: 40μm for high-fanout buses
       Between macro and core boundary: 10μm minimum
     
     Channels that are too narrow → congestion death spiral later.
     Check: can the router fit the expected wire count through
     the channel? Estimate: bus_width × pitch × 2 (for both directions).

  3. MACRO ORIENTATION
     Pin-facing matters more than area efficiency.
     Orient macros so their data pins face the logic they connect to.
     
     OpenROAD:
       place_macro <macro_name> -x <x> -y <y> -orientation {R0|R90|R180|R270|MX|MY}

  4. HALO / KEEPOUT
     Add placement halos around macros to prevent std cells
     from being placed too close (causes DRC and congestion):
     (halo size 5um is a heuristic starting point, finding M - size it
      from this design's pin-access and via requirements, not from this
      default)
       set_macro_halo -halo_x 5 -halo_y 5  ;# μm, Sky130

  5. BLOCKAGES
     Add placement blockages in areas where you don't want cells:
     - Under power grid straps (if thick metal)
     - In routing channels between macros
     - Near I/O pads
     
     create_blockage -type placement -area {x1 y1 x2 y2}
     create_blockage -type routing -area {x1 y1 x2 y2} -layer met4

MACRO PLACEMENT ANTI-PATTERNS:
  ❌ Macros in the center of the die (blocks all cross-die paths)
  ❌ Macros flush against each other (no routing channel)
  ❌ Macro pins facing the die boundary (long detour wires)
  ❌ Macros placed without considering clock tree path
     (CTS buffers need to reach all flops — macro walls block them)
```

---

## 4. Power Grid Design

```
POWER GRID LAYERS (Sky130 typical):
  met1: std cell power rails (horizontal, built into rows)
  met4: vertical power straps
  met5: horizontal power straps (top metal)

STRAP SIZING:
  Width: Start with 2μm straps, increase if IR drop > 5% VDD
  Pitch: 40-60μm for met4 vertical straps
         80-120μm for met5 horizontal straps
  
  OpenROAD:
    add_global_connection -net VDD -pin_pattern VDD -power
    add_global_connection -net VSS -pin_pattern VSS -ground
    
    set_voltage_domain -power VDD -ground VSS
    
    define_pdn_grid -name core_grid \
      -pins {met5} \
      -straps {met4 met5} \
      -connect {{met4 met5}}
    
    add_pdn_stripe -layer met4 -width 2.0 -pitch 50 -offset 5
    add_pdn_stripe -layer met5 -width 2.0 -pitch 100 -offset 5
    
    add_pdn_connect -layers {met4 met5}
    add_pdn_connect -layers {met1 met4}

FOR LOW-POWER DESIGNS (UPF):
  Each power domain needs its own grid:
  - Always-on domain: connected to permanent VDD
  - Switchable domains: connected through power switches
  - Power switches placed at domain boundaries
  - Isolation cells powered by always-on supply
  
  CRITICAL: the power grid must match the UPF domain map.
  If PD_DSP spans coordinates (100,50) to (300,200), the
  switchable VDD_DSP straps must cover exactly that region.
  Gaps = cells with no power. Overlap with always-on = shorts.

IR DROP CHECK (early estimate):
  run_ir_drop -vsrc VDD -vgnd VSS
  
  Target: IR drop < 5% of VDD (< 90mV for Sky130 1.8V)
  If exceeded: increase strap width or reduce strap pitch
```

---

## 5. Pin Assignment

```
PRINCIPLES:
  - Place pins where they minimize wire length to their loads
  - Group bus signals together (data[31:0] on same edge, ordered)
  - Clock pins on the edge closest to the clock tree root
  - Power/ground pins distributed evenly around the die

  OpenROAD:
    place_pins -hor_layers met3 -ver_layers met2 \
      -corner_avoidance <distance> \
      -min_distance 2

  For constrained designs:
    place_pin -pin_name clk -layer met3 -location {x y}
    place_pin -pin_name rst -layer met3 -location {x y}

PIN PLACEMENT ANTI-PATTERNS:
  ❌ All pins on one edge (forces long wires to opposite side)
  ❌ Clock pin far from center (asymmetric clock tree)
  ❌ Bus pins scattered (prevents efficient bus routing)
```

---

## 6. Row and Site Definition

```
OpenROAD (Sky130):
  # Standard cell rows
  initialize_floorplan -site unithd \
    -die_area {0 0 <w> <h>} \
    -core_area {<m> <m> <w-m> <h-m>}
  
  # Verify rows are created
  set row_count [llength [get_rows]]
  puts "Created $row_count placement rows"
  
  # Check row height matches library
  # Sky130 unithd: height = 2.72μm, site width = 0.46μm

ENDCAP AND TAPCELL:
  Every row needs endcap cells at the boundaries and
  well-tap cells at regular intervals:
  
  tapcell -endcap_cpp 2 \
    -distance 14 \
    -tapcell_master sky130_fd_sc_hd__tapvpwrvgnd_1 \
    -endcap_master sky130_fd_sc_hd__decap_3
```

---

## 7. Floorplan QoR Checks

Before declaring floorplan complete, verify:

```
CHECK 1: UTILIZATION
  report_design_area
  Target: 55-75% (design dependent)
  Too high (>80%): placement will struggle, routing congestion
  Too low (<40%): wasted area, longer average wire lengths

CHECK 2: MACRO PLACEMENT
  - All macros placed and fixed
  - Channels between macros ≥ 20μm
  - Macro pins facing their logic connections
  - No macro blocking the natural clock tree path

CHECK 3: POWER GRID
  - IR drop estimate < 5% VDD
  - All power domains have straps
  - Power switch placement matches UPF

CHECK 4: PIN ASSIGNMENT
  - All pins assigned to legal locations
  - Bus signals grouped and ordered
  - Clock pin position allows balanced CTS

CHECK 5: EARLY CONGESTION
  estimate_parasitics -placement
  report_congestion
  Target: no region > 85% congestion
  
  If congestion hotspots exist:
  - Add routing blockages to redirect
  - Increase channel width between macros
  - Consider spreading the floorplan (increase die area)

CHECK 6: ROW INTEGRITY
  - All rows legal height and pitch
  - Endcaps placed at row boundaries
  - Tapcells at regular intervals
```

---

## 8. Handoff to Placement Agent

```
HANDOFF PACKAGE:
  ✓ design_fp.def (die + rows + macros + pins + PG)
  ✓ design_fp.v (netlist, unchanged from synthesis)
  ✓ design_fp.sdc (constraints, unchanged)
  ✓ design.upf (if low power)
  ✓ floorplan_qor.rpt (utilization, aspect ratio)
  ✓ congestion_est.rpt (early estimate)
  ✓ power_grid.rpt (IR drop estimate)
  ✓ risk_list.md (known issues)

RISK LIST EXAMPLES:
  - "Utilization is 73% with 2 SRAMs — placement may be tight
     in the NE quadrant. Consider spreading if congestion > 80%."
  - "Clock pin placed on south edge, most flops in north half —
     CTS may need extra insertion delay. Monitor skew."
  - "Met5 power straps at 100μm pitch — thin margin for IR drop.
     If dynamic power increases post-CTS, revisit strap width."

THEN: LEC + Low Power gate check before placement proceeds.
  LEC: floorplan netlist vs synthesis netlist (should be identical)
  LP: power domain boundaries match UPF physical regions
```

---

## 9. OpenROAD Command Sequence (Sky130)

```tcl
# === COMPLETE FLOORPLAN FLOW ===

# 1. Read inputs
read_lef sky130_fd_sc_hd.tlef
read_lef sky130_fd_sc_hd_merged.lef
read_liberty sky130_fd_sc_hd__tt_025C_1v80.lib
read_verilog design_synth.v
link_design <top_module>
read_sdc design.sdc

# 2. Initialize floorplan
initialize_floorplan -die_area {0 0 200 200} \
  -core_area {10 10 190 190} \
  -site unithd

# 3. Place macros (if any)
# place_macro SRAM_0 -x 20 -y 20 -orientation R0

# 4. Create power grid
add_global_connection -net VDD -pin_pattern VDD -power
add_global_connection -net VSS -pin_pattern VSS -ground
set_voltage_domain -power VDD -ground VSS

define_pdn_grid -name core_grid -pins {met5}
add_pdn_stripe -layer met1 -width 0.48 -followpins
add_pdn_stripe -layer met4 -width 2.0 -pitch 50 -offset 10
add_pdn_stripe -layer met5 -width 2.0 -pitch 100 -offset 10
add_pdn_connect -layers {met1 met4}
add_pdn_connect -layers {met4 met5}

# 5. Place pins
place_pins -hor_layers met3 -ver_layers met2

# 6. Add endcaps and tapcells
tapcell -endcap_cpp 2 -distance 14 \
  -tapcell_master sky130_fd_sc_hd__tapvpwrvgnd_1 \
  -endcap_master sky130_fd_sc_hd__decap_3

# 7. Verify
report_design_area
report_power

# 8. Write outputs
write_def design_fp.def
write_verilog design_fp.v
```
