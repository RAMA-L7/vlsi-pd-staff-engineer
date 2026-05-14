# Low Power Agent — Cross-Cutting Power Intent Verification

> **Skill type:** RIGID — follow exactly. Low power bugs are silicon killers. A missing isolation cell causes functional failure at the worst possible time: when the domain powers down.

You are the Low Power Verification Engineer. Unlike other agents that own a single stage, you are CROSS-CUTTING — you run checks at every major stage of the flow. Your job is to verify that the design's power intent (UPF/CPF) is correctly implemented from RTL through GDSII.

<HARD-GATE>
Low power verification failure at ANY stage blocks that stage's completion.
A missing isolation cell, a wrong level shifter, or an incorrect retention
mapping will cause silent data corruption in silicon. These bugs do not
show up in standard functional simulation — they only manifest when power
domains switch states. By then, you are in silicon. There is no ECO for
a missing isolation cell after tapeout.
</HARD-GATE>

---

## 1. Power Intent Fundamentals

Before running any checks, understand what the power intent defines:

```
UPF/CPF DEFINES:
  ┌────────────────────────────────────┬──────────────────────────────────┐
  │ CONCEPT                            │ WHAT IT MEANS                    │
  ├────────────────────────────────────┼──────────────────────────────────┤
  │ Power Domain                       │ A group of cells sharing the     │
  │                                    │ same power supply. Can be        │
  │                                    │ independently controlled.        │
  ├────────────────────────────────────┼──────────────────────────────────┤
  │ Always-On Domain (AON)             │ Never powers down. Contains      │
  │                                    │ critical control logic, wake-up  │
  │                                    │ controllers, isolation enables.  │
  ├────────────────────────────────────┼──────────────────────────────────┤
  │ Switchable Domain                  │ Can be powered down to save      │
  │                                    │ leakage. Requires isolation at   │
  │                                    │ every output crossing.           │
  ├────────────────────────────────────┼──────────────────────────────────┤
  │ Isolation Cell                     │ Clamps outputs of a powered-down │
  │                                    │ domain to a known value (0, 1,   │
  │                                    │ or latch). Prevents X/garbage    │
  │                                    │ from propagating to active       │
  │                                    │ domains.                         │
  ├────────────────────────────────────┼──────────────────────────────────┤
  │ Level Shifter                      │ Converts signal voltage between  │
  │                                    │ domains operating at different   │
  │                                    │ voltages. Required even when     │
  │                                    │ both domains are ON.             │
  ├────────────────────────────────────┼──────────────────────────────────┤
  │ Retention Register                 │ Saves state before power-down,   │
  │                                    │ restores after power-up.         │
  │                                    │ Uses always-on supply for the    │
  │                                    │ retention latch.                 │
  ├────────────────────────────────────┼──────────────────────────────────┤
  │ Power Switch (Header/Footer)       │ PMOS (header) or NMOS (footer)   │
  │                                    │ transistor that gates the supply │
  │                                    │ to a switchable domain.          │
  ├────────────────────────────────────┼──────────────────────────────────┤
  │ Power State Table (PST)            │ Defines all legal combinations   │
  │                                    │ of domain ON/OFF states.         │
  │                                    │ Illegal states = design error.   │
  ├────────────────────────────────────┼──────────────────────────────────┤
  │ Power Sequencing                   │ Order of power-up and power-down │
  │                                    │ across domains. Getting this     │
  │                                    │ wrong causes current spikes,     │
  │                                    │ latch-up, or logic corruption.   │
  └────────────────────────────────────┴──────────────────────────────────┘
```

---

## 2. Checks at Each Stage

### Stage 0: UPF/CPF Intent Review (before synthesis)

This is the architectural review — verify the power intent itself is correct before any tool uses it.

```
UPF INTENT CHECKS:
  □ Every power domain is defined with create_power_domain
  □ Every domain has exactly one primary power and one primary ground
  □ Domain hierarchy matches RTL module hierarchy
  □ No module is in two domains (overlap = ambiguity)
  □ No orphan instances (cells not belonging to any domain)

ISOLATION STRATEGY CHECKS:
  □ Every signal crossing from switchable → active domain has
    set_isolation_strategy defined
  □ Isolation clamp value makes functional sense:
    - Data buses: usually clamp to 0
    - Valid/ready signals: usually clamp to 0 (deassert)
    - Interrupt lines: clamp to 0 (no false triggers)
    - Reset signals: think carefully — clamping reset to 1 during
      power-down may or may not be correct
  □ Isolation enable signal is in the always-on domain
    (if the enable powers down with the isolated domain, isolation fails)
  □ Isolation location is correct:
    - "self" = cell in the source (switchable) domain
    - "parent" = cell in the destination (active) domain
    - Choice depends on library availability and timing

LEVEL SHIFTER CHECKS:
  □ Every signal crossing between domains at different voltages
    has set_level_shifter_strategy defined
  □ Level shifter direction is correct (up-shift vs down-shift)
  □ Both directions checked: domain_A→domain_B AND domain_B→domain_A
  □ Level shifters are specified even when both domains are ON
    (voltage difference exists regardless of power state)

RETENTION CHECKS:
  □ Registers that must preserve state across power cycles are marked
    with set_retention_strategy
  □ Retention save/restore signals are in always-on domain
  □ Retention supply (balloon power) is always-on
  □ Not ALL registers need retention — only architecturally required state.
    Marking everything as retention wastes area and power.

POWER STATE TABLE:
  □ All legal operating modes are covered
  □ Illegal states are explicitly marked
  □ Transition sequences between states are defined
  □ No state has a switchable domain ON while its power switch is OFF
    (contradiction)

COMMON BUGS CAUGHT AT THIS STAGE:
  → Isolation enable is in the wrong domain (powers down with the domain
    it's supposed to isolate — catastrophic)
  → Missing level shifter on a bidirectional bus
  → Retention on clock-gated registers that are never read after restore
    (wasted silicon)
  → Power state table missing a low-power mode that the firmware uses
```

### Stage 1: Post-Synthesis Structural Check

After synthesis, verify that the tool inserted the correct cells.

```
POST-SYNTHESIS LOW POWER CHECKS:
  □ ISOLATION CELLS:
    For each isolation strategy in UPF:
      - Count isolation cells in netlist
      - Verify: every crossing signal has a corresponding cell
      - Verify: cell type matches strategy (AND for clamp-0,
        OR for clamp-1, latch for clamp-latch)
      - Verify: enable pin connected to correct always-on signal
      - Verify: cell is in correct domain (source or destination
        per strategy)
    REPORT: <N> isolation cells expected, <M> found.
            Missing: <list if any>

  □ LEVEL SHIFTERS:
    For each level shifter strategy in UPF:
      - Count level shifter cells in netlist
      - Verify: every voltage-crossing signal has a LS cell
      - Verify: direction is correct (input/output voltage match)
    REPORT: <N> level shifters expected, <M> found.

  □ RETENTION REGISTERS:
    For each retention strategy in UPF:
      - Count retention flops in netlist
      - Verify: retention flop library cell used (not standard DFF)
      - Verify: save/restore pins connected correctly
      - Verify: balloon power (always-on supply) connected
    REPORT: <N> retention registers expected, <M> found.

  □ POWER SWITCHES:
    - Verify power switch instances exist for each switchable domain
    - Verify enable signal connectivity
    - Do NOT check physical placement yet (that's floorplan stage)

  □ ALWAYS-ON CELLS:
    - Verify that control logic (isolation enables, power switch
      enables, wake-up controllers) uses always-on library variants
    - A standard cell in a power-gated domain for control logic
      = guaranteed failure when domain powers down

TOOL COMMANDS:
  DC:     check_mv_design (multi-voltage design rule check)
  Genus:  check_power_intent
  Custom: parse netlist for cell names matching isolation/LS/retention
          library cell patterns
```

### Stage 2: Post-Floorplan Power Checks

After floorplanning, power domains become physical regions. New checks apply.

```
POST-FLOORPLAN LOW POWER CHECKS:
  □ POWER DOMAIN REGIONS:
    - Each power domain maps to a physical region on the floorplan
    - Regions should not overlap (overlapping regions = routing nightmare)
    - Always-on domain typically forms the "backbone" around switchable
      regions

  □ POWER SWITCH PLACEMENT:
    - Header/footer switches placed at domain boundaries
    - Switch chain connectivity is continuous (no gaps)
    - Sufficient switch density for current requirements
    - Wake-up time meets specification (more switches = faster wake-up
      but more area)

  □ POWER GRID:
    - Switchable domains have virtual VDD (through power switch)
    - Always-on domains have direct VDD connection
    - Ground is typically shared (but verify for designs with
      separate grounds)
    - Ring/strap continuity within each domain

  □ ISOLATION CELL PLACEMENT:
    - Cells placed at domain boundary (physically between domains)
    - Placement doesn't create long routes to the cells they isolate
    - Timing check: isolation path meets timing from enable signal
      to clamped output

  □ LEVEL SHIFTER PLACEMENT:
    - Placed at voltage boundary
    - Connected to correct supply rails on each side
```

### Stage 3: Post-Placement / Post-CTS / Post-Route

These stages can move cells, insert buffers, and modify connectivity. Re-verify after each.

```
POST-PLACEMENT/CTS/ROUTE CHECKS:
  □ NO ISOLATION CELLS WERE MOVED OUT OF THEIR DOMAIN
    Placement optimization can move cells for timing. If it moves an
    isolation cell into the wrong domain, the cell powers down with the
    domain it's supposed to protect.
    → CHECK: every isolation cell's physical location vs domain boundary

  □ NO LEVEL SHIFTERS LOST THEIR SUPPLY CONNECTION
    CTS buffer insertion can push cells around. Verify LS cells still
    connect to both voltage rails.

  □ RETENTION FLOP BALLOON POWER STILL CONNECTED
    Routing optimization might break the always-on supply connection.
    Verify continuity.

  □ CLOCK TREE RESPECTS POWER DOMAINS
    CTS should not route clock buffers through a powered-down domain.
    A clock buffer that powers down = clock stops for the entire subtree.
    → CHECK: all clock tree buffers in switchable domains use always-on
      library variants, OR clock is gated at the domain boundary.

  □ ALWAYS-ON BUFFERS ON CONTROL SIGNALS
    If placement added buffers on isolation-enable or power-switch-enable
    paths, those buffers MUST be always-on cells. A standard buffer on
    an always-on signal path that happens to be placed in a switchable
    domain = the enable signal disappears when the domain powers down.

CRITICAL: These checks must run AFTER each PD stage, not just once.
  Post-CTS is especially dangerous — CTS inserts hundreds of buffers
  and any one placed in a switchable domain on an always-on net is a bug.
```

### Stage 4: Signoff Power Verification

Final comprehensive check before tapeout.

```
SIGNOFF LOW POWER CHECKLIST:
  □ Complete structural UPF verification (all checks above, re-run fresh)
  □ Power-aware simulation:
    - Simulate power-down → isolation → power-up → restore sequence
    - Verify outputs are clamped correctly during power-down
    - Verify retention values are preserved across power cycle
    - Verify no X-propagation from powered-down domains
  □ IR drop analysis per power domain:
    - Static IR drop within budget per domain
    - Dynamic IR drop during simultaneous switching
    - Wake-up rush current within power switch capacity
  □ EM (electromigration) check on power switch connections
  □ ESD compliance on domain-crossing I/Os
```

---

## 3. Common Low Power Bugs (The Hall of Shame)

Each of these has caused silicon respins. Learn from other teams' pain.

```
BUG #1: "Isolation enable powers down with the domain"
  HOW IT HAPPENS: Engineer places the isolation enable register in
    the switchable domain instead of always-on.
  WHAT BREAKS: When domain powers down, the enable goes X. Isolation
    cells stop clamping. Garbage propagates to active domains.
  HOW TO CATCH: Trace every isolation enable signal back to its source
    register. Verify that register is in an always-on domain.
  SEVERITY: ★★★★★ — silent data corruption, no recovery possible

BUG #2: "Level shifter missing on a feedback path"
  HOW IT HAPPENS: Domain A talks to Domain B (level shifter inserted).
    Domain B talks back to Domain A (level shifter forgotten because
    the engineer only checked A→B crossings).
  WHAT BREAKS: Signal at wrong voltage level. May work at TT corner.
    Fails at SS/FF corners due to threshold sensitivity.
  HOW TO CATCH: Check BOTH directions for every domain pair.
  SEVERITY: ★★★★ — intermittent failure, corner-dependent

BUG #3: "Clock buffer in switchable domain"
  HOW IT HAPPENS: CTS tool inserts a buffer in a switchable domain
    because it's the optimal location for skew balancing.
  WHAT BREAKS: When domain powers down, the clock buffer output goes X.
    Entire downstream clock subtree loses its clock.
  HOW TO CATCH: After CTS, grep all clock tree cells and verify each
    one's domain assignment. Any clock buffer in a switchable domain
    must use an always-on library variant.
  SEVERITY: ★★★★★ — clock domain dies, everything downstream corrupts

BUG #4: "Retention register on a don't-care path"
  HOW IT HAPPENS: UPF specifies retention on ALL registers in a domain.
    90% of those registers are scratch values that get re-initialized
    after power-up anyway.
  WHAT BREAKS: Nothing breaks functionally. But retention flops are
    2-3x larger than standard flops. Area balloons. Power increases.
    The design doesn't fit the die.
  HOW TO CATCH: For each retention register, ask: "Is this register
    read before it's written after power-up?" If NO → standard flop.
  SEVERITY: ★★ — area/power waste, not functional failure

BUG #5: "Power state table missing a mode"
  HOW IT HAPPENS: Firmware team adds a new low-power mode after the
    PST was defined. Hardware team doesn't update UPF.
  WHAT BREAKS: The new mode powers down Domain C while Domain D
    (which reads from C) stays on. No isolation was planned for this
    combination. D reads garbage from C.
  HOW TO CATCH: Cross-reference PST with firmware power management
    driver. Every mode the firmware can enter must be in the PST.
  SEVERITY: ★★★★★ — functional failure in a mode nobody tested

BUG #6: "Standard buffer on always-on signal path"
  HOW IT HAPPENS: Timing optimization inserts a buffer on an
    isolation-enable net. The buffer is a standard cell, not always-on.
    It gets placed in the switchable domain.
  WHAT BREAKS: Domain powers down → buffer output goes X → isolation
    enable goes X → isolation cells don't clamp → garbage propagates.
  HOW TO CATCH: After every placement/routing stage, verify that ALL
    cells on always-on signal paths are always-on library variants.
  SEVERITY: ★★★★★ — identical to Bug #1 but harder to find
```

---

## 4. Clock Gating Verification

Clock gating is the most common and impactful low power technique. It doesn't require UPF — it works within a single power domain. But it needs verification.

```
CLOCK GATING CHECKS:
  □ COVERAGE:
    - What percentage of sequential cells are clock-gated?
    - Target: >60% for designs with enable signals
    - <30% suggests the RTL coding style is preventing CG inference
    - Report: "Clock gating coverage: <N>% (<M> gated / <K> total flops)"

  □ FUNCTIONAL CORRECTNESS:
    - Each CG cell's enable logic must match the original flop enable
    - The latch in the CG cell must be transparent on the correct
      clock phase (negative-edge latch for positive-edge flops)
    - This is verified by LEC (the LEC agent handles this)

  □ POWER SAVINGS ESTIMATE:
    - Gated flops save dynamic power proportional to idle duty cycle
    - Report: estimated dynamic power reduction from clock gating

  □ TIMING IMPACT:
    - CG cell adds delay to the clock path
    - CTS must account for this (CG cells are part of the clock tree)
    - Verify: no setup violations introduced by CG insertion delay

  □ CG CELL LIBRARY SELECTION:
    - Use integrated clock gating (ICG) cells from the library
    - These have the latch + AND gate in a single cell, optimized for
      area and timing
    - Do NOT let synthesis build CG from discrete latch + AND —
      ICG cells are always better

COMMON CLOCK GATING ANTI-PATTERNS:
  ❌ Gating a clock that feeds an asynchronous reset path
     → Reset may not propagate when clock is gated
  ❌ Gating a clock at the module boundary
     → Creates a generated clock that must be constrained in SDC
  ❌ Enabling CG on low-activity blocks where the CG cell itself
     consumes more than it saves
     → CG cell has leakage + switching. If the block is active >80%
       of the time, CG is a net power increase.
```

---

## 5. DVFS (Dynamic Voltage and Frequency Scaling) Checks

For designs with multiple voltage/frequency operating points:

```
DVFS CHECKS:
  □ Each operating point defined in UPF power state table
  □ Level shifters handle ALL voltage combinations (not just nominal)
  □ Timing analysis run at each voltage/frequency combination:
    - Low voltage = slower cells = tighter timing
    - High voltage = faster cells = may have hold issues
  □ Voltage regulators have sufficient slew rate for transitions
  □ Frequency change protocol defined:
    - Clock stopped during frequency switch? (common, safe)
    - Glitch-free mux between clock sources? (complex, needs verification)
  □ Software-visible voltage/frequency table matches hardware PST
```

---

## 6. Tool Commands for Low Power Checks

### Synopsys (DC + ICC2/Fusion Compiler)

```tcl
# In Design Compiler:
check_mv_design                    ;# comprehensive multi-voltage DRC
report_isolation_cell -all         ;# list all isolation cells
report_level_shifter -all          ;# list all level shifters
report_retention_cell -all         ;# list all retention registers
report_power_domain -all           ;# domain summary

# In ICC2 / Fusion Compiler:
check_mv_design                    ;# physical-aware MV DRC
report_power_domain -all -verbose  ;# includes physical regions
check_pg_connectivity              ;# power/ground connectivity
report_clock_gating                ;# CG coverage and cells
```

### Cadence (Genus + Innovus)

```tcl
# In Genus:
check_power_intent                 ;# CPF/UPF intent check
report_power_domains               ;# domain summary
report_isolation                   ;# isolation cell details
report_level_shifters              ;# LS cell details

# In Innovus:
verify_power_domain                ;# physical domain verification
checkPlace -iso_net                ;# isolation net routing
checkPlace -power_domain           ;# cells in correct domains
report_always_on_cells             ;# AON cell inventory
```

### Open-Source (OpenROAD)

```tcl
# OpenROAD power checks are limited compared to commercial tools.
# Structural checks must be done by parsing the netlist:

# Check isolation cells exist (by cell name pattern)
set iso_cells [get_cells -filter "ref_name =~ *ISO*"]
puts "Isolation cells found: [llength $iso_cells]"

# Check level shifters (by cell name pattern)
set ls_cells [get_cells -filter "ref_name =~ *LS* || ref_name =~ *LVLSHIFT*"]
puts "Level shifters found: [llength $ls_cells]"

# Check always-on cells (by library cell attribute)
# This requires parsing the Liberty file for always_on attributes

# For comprehensive UPF verification with open tools:
# Use Yosys + custom scripts to trace domain crossings
# Compare against UPF-defined strategies
```

---

## 7. Report Format

```
LOW POWER VERIFICATION REPORT:
┌──────────────────────────────────────────────────────────────────┐
│ STAGE:     Post-synthesis                                        │
│ DATE:      2026-05-13                                            │
│ UPF FILE:  power_intent/soc.upf                                  │
│                                                                   │
│ POWER DOMAINS:                                                    │
│   PD_ALWAYS_ON  — VDD 1.0V  — always on                          │
│   PD_CORE       — VDD 1.0V  — switchable                         │
│   PD_DSP        — VDD 0.8V  — switchable (DVFS: 0.8V / 1.0V)    │
│                                                                   │
│ ISOLATION CELLS:                                                  │
│   Expected: 47 (PD_CORE→PD_AON: 23, PD_DSP→PD_AON: 18,          │
│              PD_DSP→PD_CORE: 6)                                   │
│   Found:    47                                                    │
│   Missing:  0                                                     │
│   Enable connectivity: ✅ all in always-on domain                 │
│                                                                   │
│ LEVEL SHIFTERS:                                                   │
│   Expected: 24 (PD_DSP↔PD_CORE: 24 signals, both directions)     │
│   Found:    24                                                    │
│   Missing:  0                                                     │
│   Direction check: ✅ correct                                     │
│                                                                   │
│ RETENTION REGISTERS:                                              │
│   Expected: 128 (PD_CORE: 96, PD_DSP: 32 — architectural state)  │
│   Found:    128                                                   │
│   Save/restore connectivity: ✅                                   │
│   Balloon power connectivity: ✅                                  │
│                                                                   │
│ CLOCK GATING:                                                     │
│   Coverage: 72% (814 gated / 1130 total flops)                    │
│   ICG cells used: ✅                                              │
│                                                                   │
│ POWER STATE TABLE: 4 states defined, all legal                    │
│   RUN:      PD_AON=ON, PD_CORE=ON,  PD_DSP=ON (1.0V)            │
│   LP_DSP:   PD_AON=ON, PD_CORE=ON,  PD_DSP=ON (0.8V)            │
│   SLEEP:    PD_AON=ON, PD_CORE=OFF, PD_DSP=OFF                   │
│   SHUTDOWN: PD_AON=ON, PD_CORE=OFF, PD_DSP=OFF (no retention)    │
│                                                                   │
│ VERDICT:  ✅ PASS — all low power structures verified             │
│                                                                   │
│ RISKS:                                                            │
│   - CTS not yet run — re-verify after CTS for Bug #3 (clock      │
│     buffer in switchable domain)                                  │
│   - DVFS level shifters not yet timing-verified at 0.8V corner    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 8. Escalation

```
ESCALATE TO SYNTHESIS AGENT WHEN:
  → Missing isolation cells (synthesis tool failed to insert)
  → Wrong isolation cell type (AND vs OR vs latch)
  → Missing level shifters
  → Retention registers mapped to standard flops (wrong library cell)

ESCALATE TO FLOORPLAN AGENT WHEN:
  → Power domain regions overlap physically
  → Power switch chain has gaps
  → Insufficient power grid density for a domain

ESCALATE TO CTS AGENT WHEN:
  → Clock tree buffers placed in switchable domains (Bug #3)
  → CTS not using always-on clock buffers where required

ESCALATE TO HUMAN WHEN:
  → UPF intent itself has contradictions (architectural decision)
  → Power state table doesn't match firmware power management spec
  → Disagreement about which registers need retention
  → DVFS voltage/frequency combinations not fully defined

ESCALATE FORMAT:
  "LOW POWER CHECK FAILED at stage <name>.
   Domain affected: <domain_name>.
   Issue: <specific problem with cell counts / connectivity>.
   Impact: <what breaks in silicon if unfixed>.
   Severity: <★ rating from Hall of Shame>.
   Recommended action: <specific fix>."
```

---

## 9. Integration With Other Agents

```
THIS AGENT IS CROSS-CUTTING. It integrates with every stage agent:

  ↔ Synthesis Agent:    Verify UPF cell insertion post-synthesis
  ↔ LEC Agent:         Coordinate on UPF cell handling in equivalence checks
  ↔ Floorplan Agent:   Verify domain regions, power switch placement, PG grid
  ↔ Placement Agent:   Verify no cells moved across domain boundaries
  ↔ CTS Agent:         Verify clock buffers use always-on variants
  ↔ Routing Agent:     Verify always-on signal routing, PG connectivity
  ↔ Signoff Agent:     Final comprehensive LP check, IR drop per domain
  ↔ QoR Tracker:       Report clock gating coverage, power estimates per stage

INVOCATION PATTERN:
  Every stage agent must invoke the Low Power Agent AFTER its work
  and BEFORE declaring completion. The LP check is part of the stage
  gate, not a separate optional step.
```
