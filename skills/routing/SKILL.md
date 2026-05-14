# Routing Agent

> **Skill type:** RIGID — DRC clean is non-negotiable. Zero violations at handoff.

You receive a verified CTS design (LEC PASS + low power PASS — every
clock buffer domain-checked) and connect all nets with metal wires.
Routing is where the design becomes physically real. Every DRC violation
you leave behind is a manufacturing defect.

<HARD-GATE>
DO NOT START ROUTING WITHOUT:
  1. CTS DEF with clock tree cells placed
  2. LEC report: PASS (CTS netlist vs placed netlist)
  3. Low power report: PASS (ALL CTS buffers domain-verified)
  4. CTS timing report (know your remaining margin)
  5. CTS risk list (congestion hotspots, hold violations, tight paths)

IF LOW POWER CHECK FAILED ON CTS BUFFERS: DO NOT ROUTE.
Go back to CTS agent. Bug #3 cannot be fixed at routing level.
</HARD-GATE>

---

## 1. Inputs and Outputs

```
INPUTS:
  design_cts.def       CTS DEF
  design_cts.v         CTS netlist
  design_cts.sdc       Propagated clock SDC
  *.lib                Liberty libraries
  *.lef                Cell/tech LEF
  risk_list.md         From CTS agent

OUTPUTS (handoff package):
  design_routed.def    Routed DEF (all nets connected)
  design_routed.v      Netlist (may have hold buffers added)
  design_routed.sdc    Constraints (unchanged)
  route_timing.rpt     Post-route STA (final numbers)
  drc.rpt              DRC report (must be: 0 violations)
  antenna.rpt          Antenna check results
  hold_fix.rpt         Hold buffer insertion summary
  risk_list.md         Known issues for signoff agent
```

---

## 2. Routing Flow

```
PHASE 1: GLOBAL ROUTING
  Assigns each net to routing regions (GCells) without
  determining exact wire positions. Identifies congestion.
  
  OpenROAD:
    global_route -guide_file route.guide \
      -congestion_iterations 50 \
      -allow_congestion

  After global routing:
    report_congestion
    
  If any GCell > 100% overflow:
  → Routing cannot succeed. Options:
     a) Reduce cell density in that region (back to placement)
     b) Add routing blockages to redirect traffic
     c) Increase die area (back to floorplan)
  
  Do NOT proceed to detail routing with > 5% overflow.

PHASE 2: DETAIL ROUTING
  Determines exact wire positions, via locations, and
  metal layer assignments. Enforces all DRC rules.
  
  OpenROAD:
    detailed_route -output_drc drc_initial.rpt \
      -output_maze drc_maze.log

  Detail routing iterates to resolve DRC violations.
  Monitor: does violation count decrease each iteration?
  
  If violations INCREASE between iterations:
  → Congestion death spiral. Stop and escalate.

PHASE 3: HOLD FIXING
  After routing, fix hold timing violations by inserting
  delay buffers on short data paths:
  
  estimate_parasitics -global_routing
  repair_timing -hold -hold_margin 0.05
  
  This inserts buffers. CRITICAL CHECKS:
  1. Every hold buffer must be in the correct power domain
     (the low power agent checks this at the gate)
  2. Hold buffers must not violate setup on the same path
  3. Hold buffers need to be placed legally (legalize after)
  
  detailed_placement -incremental
  
  After hold fixing: re-run detail routing for new connections:
  detailed_route -output_drc drc_after_hold.rpt -incremental

PHASE 4: ANTENNA REPAIR
  Long metal segments connected to gate inputs accumulate
  charge during manufacturing, potentially damaging the gate oxide.
  
  repair_antennas -iterations 5
  
  Antenna fixes: insert diodes near gate inputs, or break
  long wires with via hops to upper metals.
  
  After antenna repair:
  detailed_route -incremental -output_drc drc_final.rpt

PHASE 5: FINAL DRC CHECK
  check_drc -report drc_final.rpt
  
  TARGET: 0 violations.
  
  If violations remain after incremental routing:
  - Categorize: spacing? width? via enclosure? shorts?
  - If clustered in one region: local congestion issue
  - If scattered: may be a tech LEF issue or tool bug
  - Invoke forensic debugging if > 3 fix attempts fail
```

---

## 3. DRC Convergence Strategy

```
DRC FIXING FOLLOWS THE THREE-FIX RULE.

ATTEMPT 1: Incremental detail routing
  detailed_route -incremental -output_drc drc_fix1.rpt
  If violations reduced by > 50%: progress. Continue.

ATTEMPT 2: Targeted area re-route
  For remaining violations, re-route just that region:
  detailed_route -incremental -output_drc drc_fix2.rpt
  If violations still > 0 but decreasing: continue.

ATTEMPT 3: More aggressive repair
  detailed_route -incremental \
    -output_drc drc_fix3.rpt \
    -droute_end_iter 64
  
  If violations still > 0 after attempt 3:
  → THREE-FIX RULE TRIGGERED
  → Stop. Classify remaining violations.
  → Escalate to forensic debugging agent.
  
  Common escalation: "DRC violations in region X cannot be
  resolved by routing alone. Root cause: placement density
  too high in that region. Recommend: reduce density from
  78% to 65% and re-place."
```

---

## 4. Timing Closure Through Routing

```
POST-ROUTE STA:
  estimate_parasitics -global_routing
  report_checks -path_delay max \
    -fields {slew cap input_pins nets fanout} \
    -group_count 50
  
  Wire RC is now real (estimated from actual routes, not
  placement-based wire models). Timing will shift.

EXPECTED CHANGES FROM CTS:
  - Setup WNS may degrade by 100-300ps (wire RC)
  - Some paths improve (shorter routes than estimated)
  - Hold violations should be mostly fixed

IF POST-ROUTE WNS < 0:
  Do NOT immediately apply ECOs.
  Check: is the signoff STA (with extracted parasitics)
  the same as the estimate? Often the global-route estimate
  is pessimistic. Wait for signoff.
  
  If the violation is large (> 200ps): invoke forensic
  debugging agent. The root cause is likely upstream.
  
  If the violation is small (< 100ps): may be fixable
  with post-route optimization:
  
  repair_timing -setup
  detailed_route -incremental
```

---

## 5. Routing-Specific Checks

```
CHECK 1: DRC CLEAN
  check_drc -report drc_final.rpt
  MUST BE: 0 violations
  No exceptions. No "we'll fix it in signoff."

CHECK 2: CONNECTIVITY
  Every net must be fully connected. No opens.
  check_connectivity -report connectivity.rpt
  
  Opens = manufacturing short circuit to ground.

CHECK 3: ANTENNA
  check_antennas -report antenna.rpt
  All antenna ratios within foundry limits.

CHECK 4: TIMING
  report_checks -path_delay max -group_count 50
  report_checks -path_delay min -group_count 50
  
  Setup: WNS should be ≥ 0 (or close, for signoff to confirm)
  Hold: WNS should be ≥ 0 after hold fixing

CHECK 5: CONGESTION (final)
  report_congestion
  Should show no overflow (all nets routed within resources)

CHECK 6: POWER
  report_power
  Wire switching power now included (first time it's real)
```

---

## 6. Handoff to Signoff Agent

```
HANDOFF PACKAGE:
  ✓ design_routed.def (all nets routed, DRC clean)
  ✓ design_routed.v (with hold buffers)
  ✓ design_routed.sdc
  ✓ route_timing.rpt (post-route STA)
  ✓ drc.rpt (0 violations)
  ✓ antenna.rpt (clean)
  ✓ hold_fix.rpt (buffers inserted, count + domains)
  ✓ risk_list.md

RISK LIST ITEMS:
  - "Post-route WNS is +0.03ns — razor thin margin. Signoff
     with extracted parasitics may push this negative."
  - "Hold buffers inserted: 45 cells. All in PD_ALWAYS_ON
     (LP verified). But routing agent cannot verify these
     against signoff-extracted parasitics — signoff must re-check."
  - "Region (120,80)-(200,160) had congestion at 92% during
     routing. Wires detoured through upper metals. Crosstalk
     risk in this area — signoff SI analysis should focus here."

THEN: LEC + Low Power gate check
  LEC: routed netlist vs CTS netlist (hold buffers are the delta)
  LP: hold buffers in correct domains (Bug #6 check)
```

---

## 7. OpenROAD Command Sequence

```tcl
# === COMPLETE ROUTING FLOW ===

# 1. Read CTS design
read_lef sky130_fd_sc_hd.tlef
read_lef sky130_fd_sc_hd_merged.lef
read_liberty sky130_fd_sc_hd__tt_025C_1v80.lib
read_def design_cts.def
read_sdc design_cts.sdc

# 2. Global routing
set_routing_layers -signal met1-met5 -clock met3-met5
global_route -guide_file route.guide \
  -congestion_iterations 50

# 3. Detail routing
detailed_route -output_drc drc_initial.rpt

# 4. Post-route timing
estimate_parasitics -global_routing
report_checks -path_delay max -group_count 20

# 5. Hold fixing
repair_timing -hold -hold_margin 0.05
detailed_placement -incremental
detailed_route -incremental -output_drc drc_hold.rpt

# 6. Antenna repair
repair_antennas -iterations 5
detailed_route -incremental -output_drc drc_antenna.rpt

# 7. Final DRC
check_drc -report drc_final.rpt

# 8. Final QoR
estimate_parasitics -global_routing
report_checks -path_delay max -group_count 50
report_checks -path_delay min -group_count 50
report_design_area
report_power

# 9. Write outputs
write_def design_routed.def
write_verilog design_routed.v
```
