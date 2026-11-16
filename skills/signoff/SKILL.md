# Signoff Agent

> **Skill type:** RIGID — evidence before claims. Every check runs fresh.
> No "it passed earlier." No "should be fine." Run it. Now. Show the output.

You are the final gate before tapeout. Everything downstream of you is
a fabrication mask. A bug that passes signoff becomes a silicon defect.
Your standard is: would you sign your name on this checklist and send
it to the foundry?

<HARD-GATE>
NO SIGNOFF CLAIM WITHOUT FRESH EVIDENCE.

"Tests pass" means you ran the test in THIS session and the
output shows PASS. Not "it passed yesterday." Not "routing
agent said it was clean." Fresh. Now.

Claiming signoff without evidence is not efficiency. It is
professional negligence.
</HARD-GATE>

---

## 1. Inputs and Outputs

```
INPUTS:
  design_routed.def    Routed DEF (DRC clean from routing agent)
  design_routed.v      Routed netlist (with hold buffers)
  design_routed.sdc    Timing constraints
  design.upf           Power intent (if low power)
  *.lib                Liberty libraries (ALL corners)
  *.lef                Cell/tech LEF
  rtl/design.v         Original RTL (for final LEC)

OUTPUTS (tapeout package):
  signoff_timing.rpt   Multi-corner STA results
  signoff_drc.rpt      Final DRC (0 violations)
  signoff_lvs.rpt      LVS clean
  signoff_ir_drop.rpt  IR drop analysis
  signoff_em.rpt       Electromigration check
  signoff_antenna.rpt  Antenna check
  signoff_lec.rpt      Final LEC (routed vs RTL)
  signoff_lp.rpt       Final low power verification
  design_final.gds     GDSII output
  tapeout_checklist.md Signed checklist
```

---

## 2. Signoff Checks (in order)

### Check 1: Parasitic Extraction

```
Extract actual wire parasitics from the routed layout.
This replaces ALL previous timing estimates with real numbers.

OpenSTA (with SPEF):
  # If SPEF available from extraction tool:
  read_spef design_routed.spef
  
  # Or estimate from OpenROAD (GR ESTIMATE - NOT extraction):
  estimate_parasitics -global_routing
  # VIEW LABEL: this produces a GR_ESTIMATE view. It is an
  # approximation of global-route RC and must NEVER be reported
  # as "extracted parasitics" or used to close a routed-extraction
  # signoff gate. For tapeout use a dedicated extraction tool
  # (QRC, StarRC); for this flow the routed-extraction view is
  # stage 11's SPEF only.

After extraction, ALL previous timing reports are obsolete.
Re-run everything from scratch.

POST-FILL: metal fill changes parasitics. A post-fill
extraction + timing closure step is NOT implemented in this
flow - do not claim it. (View ladder: PLACE_ESTIMATE ->
GR_ESTIMATE -> ROUTED_EXTRACTED -> FILL_EXTRACTED; this flow
demonstrates up to ROUTED_EXTRACTED.)
```

### Check 2: Multi-Corner STA

```
Timing must be met across ALL PVT corners. A design that
passes at TT but fails at SS is NOT signed off.

CORNERS (Sky130 typical):
  TT: tt_025C_1v80  (typical)
  SS: ss_100C_1v60  (slow — worst setup)
  FF: ff_n40C_1v95  (fast — worst hold)

FOR EACH CORNER:
  read_liberty <corner>.lib
  read_spef design_routed.spef  ;# or estimate
  
  # Setup check
  report_checks -path_delay max \
    -fields {slew cap input_pins nets fanout} \
    -group_count 50
  
  # Hold check
  report_checks -path_delay min \
    -fields {slew cap} \
    -group_count 50

SIGNOFF CRITERIA:
  Setup: WNS ≥ 0 at SS corner (worst case for setup)
  Hold:  WHS ≥ 0 at FF corner (worst case for hold)
  
  Both conditions must be met simultaneously.
  
  If SS setup passes but FF hold fails:
  → Need hold buffers (but they can't break SS setup)
  → This is the "corner divergence" pattern from forensic debugging
  → May need re-synthesis with multi-corner optimization

YOUR SKY130 REALITY (corrected):
  The public Sky130 PDK publishes MULTIPLE corners (tt, ss, ff
  variants); "only TT exists" is false. What varies is which
  .lib FILES are present in your LOCAL tool tree - that is a
  local-availability question, not a PDK-scope question.

  - Check local availability first (assistant/liberty.py
    validates each claim against the actual Liberty header).
  - If a corner's FILE is missing locally: run the corners you
    have, label the result "partial-corner analysis", and record
    the missing file as a gap. Do NOT invent margins.
  - Arbitrary +/-10% derating as a substitute for a missing
    corner is PROHIBITED here: it is not tied to any measured
    RC/device data. If a margin is required, it must come from
    a real corner library or from an explicit, recorded,
    reviewer-approved waiver that names the scope and the risk.
  - This is a known limitation, not a process failure.
```

### Check 3: DRC (Final)

```
Re-run DRC from scratch. Do NOT trust the routing agent's report.

check_drc -report signoff_drc.rpt

MUST BE: 0 violations.

If violations found:
→ Routing agent's incremental fixes may have introduced new ones
→ Return to routing agent for clean-up
→ If DRC was clean at routing handoff and is dirty now:
   something changed between routing and signoff. Investigate.
```

### Check 4: LVS (Layout vs Schematic)

```
Verify the physical layout matches the logical netlist.
Every connection in the netlist must exist in the layout.
Every connection in the layout must exist in the netlist.

OpenROAD doesn't have built-in LVS. Options:
  - KLayout with built-in LVS scripts (for Sky130)
  - Netgen (open-source LVS)
  - Calibre / ICV (commercial)

LVS MUST CHECK:
  - Net connectivity matches
  - Device counts match
  - Device parameters match (W/L for transistors)
  - No shorts between unconnected nets
  - No opens on connected nets

SIGNOFF CRITERIA: LVS CLEAN (0 mismatches)
```

### Check 5: IR Drop Analysis

```
Verify power grid delivers adequate voltage to all cells.

Static IR drop:
  analyze_power_grid -net VDD
  
  Target: IR drop < 5% of VDD at every cell
  For Sky130 1.8V: max IR drop < 90mV anywhere

Dynamic IR drop (if tool supports):
  Accounts for switching activity — worst case is when
  many cells switch simultaneously.
  
  Target: dynamic IR drop < 10% of VDD

IF IR DROP EXCEEDS LIMITS:
  → Power grid insufficient. Return to floorplan agent.
  → Options: wider straps, denser strap pitch, more via connections
  → This is a floorplan-level fix, not a routing fix.
```

### Check 6: Electromigration (EM)

```
Verify current density in all wires is within foundry limits.
Wires carrying too much current will physically degrade over time.

High-risk nets:
  - Clock nets (high switching frequency)
  - Power/ground straps (high DC current)
  - High-fanout data nets

IF EM VIOLATIONS:
  → Widen the violating wires
  → Add parallel routing paths
  → For power straps: increase width at floorplan level
```

### Check 7: Antenna Check (Final)

```
check_antennas -report signoff_antenna.rpt

MUST BE: 0 violations.

If the routing agent's antenna fix didn't catch everything,
the signoff agent can insert additional diodes:
  repair_antennas -iterations 10
  detailed_route -incremental
  
Then re-check DRC (diode insertion changes the layout).
```

### Check 8: Final LEC (full-circle)

```
THE MOST IMPORTANT LEC IN THE FLOW.

This compares the FINAL routed netlist against the ORIGINAL RTL.
Every transformation across the entire flow is verified in one check.

REFERENCE: rtl/design.v (original RTL)
REVISED:   design_routed.v (final netlist)

This catches:
  - Synthesis bugs
  - CTS bugs
  - Hold buffer insertion bugs
  - ECO mistakes
  - Any logic corruption across the entire flow

SIGNOFF CRITERIA: ALL compare points equivalent.
No unmapped points without documented justification.
```

### Check 9: Final Low Power Verification

```
Complete power domain verification on the final design:
  - All isolation cells present and correctly connected
  - All level shifters present at domain crossings
  - All retention registers properly connected
  - All always-on paths use always-on library cells
  - Power state table matches UPF intent
  - Clock tree buffers in correct domains (final re-check)

SIGNOFF CRITERIA: PASS on all low power checks.
```

---

## 3. The Tapeout Checklist

```
Every item must have FRESH evidence (command output or report file).
"Previously verified" is NOT acceptable.

TAPEOUT CHECKLIST:
  ┌─────────────────────────────────────────────────────────────┐
  │ CHECK                    │ STATUS │ EVIDENCE FILE            │
  ├──────────────────────────┼────────┼──────────────────────────┤
  │ STA setup (all corners)  │        │ signoff_timing.rpt       │
  │ STA hold (all corners)   │        │ signoff_timing.rpt       │
  │ DRC (0 violations)       │        │ signoff_drc.rpt          │
  │ LVS (0 mismatches)       │        │ signoff_lvs.rpt          │
  │ Antenna (0 violations)   │        │ signoff_antenna.rpt      │
  │ IR drop (< 5% VDD)       │        │ signoff_ir_drop.rpt      │
  │ EM (within limits)        │        │ signoff_em.rpt           │
  │ LEC (RTL vs final)       │        │ signoff_lec.rpt          │
  │ Low power (all clean)    │        │ signoff_lp.rpt           │
  │ Metal fill added         │        │ fill_report.rpt          │
  │ GDSII generated          │        │ design_final.gds         │
  └──────────────────────────┴────────┴──────────────────────────┘

  ALL items must show PASS with the evidence file present.
  Any FAIL → design is NOT tapeout-ready.
  
  Any item where evidence is "from a previous run" →
  RE-RUN IT. Evidence must be from this signoff session.
```

---

## 4. GDSII Generation

```
Only after ALL checks pass:

# Add metal fill for density
add_fill -area {<die_area>}

# Final DRC after fill (fill can introduce violations)
check_drc -report drc_post_fill.rpt

# Generate GDSII
write_gds design_final.gds

# Or use KLayout for final GDSII merge:
# klayout -b -r merge_gds.py \
#   -rd input=design_routed.def \
#   -rd output=design_final.gds
```

---

## 5. Signoff Failure Escalation

```
IF ANY SIGNOFF CHECK FAILS:

Timing failure → forensic debugging agent
  (trace root cause through five levels)

DRC failure → routing agent (if routing-level)
  → placement agent (if congestion-level)
  → floorplan agent (if area-level)

LVS failure → investigate: which net mismatches?
  Usually a routing bug or an ECO that wasn't properly
  implemented. Compare the LVS report against the last
  clean LEC report.

IR drop failure → floorplan agent (power grid redesign)

LEC failure → THIS IS CRITICAL. Something in the flow
  corrupted the logic. Trace which stage introduced it
  using adjacent-stage LECs from the gate history.

Low power failure → low power agent for diagnosis.
  If it's a CTS buffer issue: CTS agent.
  If it's a missing isolation cell: synthesis agent.

ALL ESCALATIONS include the evidence file, not just
a description of the failure.
```

---

## 6. OpenROAD/OpenSTA Signoff Sequence

```tcl
# === SIGNOFF FLOW ===

# 1. Read final design
read_lef sky130_fd_sc_hd.tlef
read_lef sky130_fd_sc_hd_merged.lef
read_def design_routed.def
read_sdc design_routed.sdc

# 2. Parasitic extraction
estimate_parasitics -global_routing
# (or read_spef if external extraction available)

# 3. STA — TT corner
read_liberty sky130_fd_sc_hd__tt_025C_1v80.lib
report_checks -path_delay max -group_count 50
report_checks -path_delay min -group_count 50

# 4. DRC
check_drc -report signoff_drc.rpt

# 5. Antenna
check_antennas -report signoff_antenna.rpt

# 6. IR drop
analyze_power_grid -net VDD

# 7. Area and power
report_design_area
report_power

# 8. Metal fill
add_fill

# 9. Post-fill DRC
check_drc -report drc_post_fill.rpt

# 10. GDSII
write_gds design_final.gds
```
