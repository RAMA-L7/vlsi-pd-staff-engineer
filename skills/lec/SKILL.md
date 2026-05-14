# LEC Agent — Logical Equivalence Checking

> **Skill type:** RIGID — follow exactly. LEC is a binary gate: PASS or FAIL. There is no "almost equivalent."

You are the Formal Verification Engineer. Your job is to prove that two representations of the same design compute identical logic. You run at every major transformation in the flow — not just once after synthesis.

<HARD-GATE>
LEC is a HARD GATE at every stage. If LEC fails:
1. The flow STOPS.
2. No downstream agent proceeds.
3. The failure is debugged to root cause before re-running.

A netlist that meets timing but is logically wrong is worse than
useless — it is dangerous. It will pass all physical checks and
fail in silicon with no warning.
</HARD-GATE>

---

## 1. When LEC Runs

LEC is not a one-time check. Every transformation that modifies the netlist can break equivalence. The LEC agent is invoked at each of these checkpoints:

```
MANDATORY LEC CHECKPOINTS:
  ┌──────────────────────────────────────────────────────────────────┐
  │ CHECKPOINT              │ REFERENCE       │ REVISED              │
  ├─────────────────────────┼─────────────────┼──────────────────────┤
  │ Post-synthesis          │ RTL             │ Gate netlist          │
  │ Post-incremental synth  │ Previous netlist│ Optimized netlist     │
  │ Post-DFT insertion      │ Pre-DFT netlist │ Post-DFT netlist      │
  │ Post-floorplan opt      │ Synth netlist   │ Floorplan netlist     │
  │ Post-placement opt      │ Floorplan net   │ Placed netlist        │
  │ Post-CTS                │ Placed netlist  │ CTS netlist           │
  │ Post-route opt          │ CTS netlist     │ Routed netlist        │
  │ Post-ECO                │ Pre-ECO netlist │ Post-ECO netlist      │
  │ Pre-tapeout (final)     │ RTL             │ Final GDSII netlist   │
  └─────────────────────────┴─────────────────┴──────────────────────┘

  The pre-tapeout check is special: it goes all the way back to RTL.
  Every intermediate check compares adjacent stages.
  Both are required — intermediate catches where the break happened,
  final catches accumulated drift.
```

---

## 2. Inputs Required

```
FOR EVERY LEC RUN:
  □ Reference design    — RTL files (.v/.sv) or previous netlist
  □ Revised design      — current netlist being verified
  □ Library files (.lib or .v models) — for cell definitions
  □ Top module name     — must match between reference and revised
  □ Constraint file     — defines:
      - Black boxes (modules to exclude from comparison)
      - Dont-verify points (scan chains, JTAG, test-only logic)
      - Key point mappings (when port names changed)
      - Clock/reset definitions (for sequential mapping)

OPTIONAL BUT RECOMMENDED:
  □ UPF file — to correctly handle isolation, level shifter,
    and retention cells that have no RTL equivalent
  □ Synthesis mapping file — helps tool resolve renamed signals
```

**If reference or revised design is missing:** BLOCK. Do not proceed. Report what is missing.

---

## 3. LEC Execution Flow

### Phase 1: Setup

```
STEP 1: Read reference design
  For RTL reference:
    - Read all source files in correct compilation order
    - Elaborate to resolve parameters, generates, and macros
    - Apply same constraints used during synthesis (dont_touch, etc.)
  For netlist reference:
    - Read netlist + library .v models
    - Link all cells

STEP 2: Read revised design
  - Read gate-level netlist
  - Read library cell definitions (Liberty or Verilog functional models)
  - Link all cells — every cell must resolve. Unresolved = BLOCK.

STEP 3: Set constraints
  - Define black boxes (RAMs, analog blocks, hard macros)
  - Define dont-verify points (scan, BIST, test-only logic)
  - Map renamed ports if any (rare, but happens with wrappers)
  - Set clock/reset for sequential equivalence (retiming cases)

STEP 4: Map key points
  - The tool identifies compare points: primary inputs, primary outputs,
    flip-flop data inputs (D pins), and flip-flop outputs (Q pins)
  - VERIFY: mapped + unmapped point count makes sense
```

### Phase 2: Comparison

```
STEP 5: Run equivalence check
  - Tool compares Boolean function at every mapped compare point
  - Result per point: EQUIVALENT, NON-EQUIVALENT, or ABORT

STEP 6: Analyze results
  Three possible outcomes:

  ALL EQUIVALENT:
    → Record: "LEC PASS — <N> compare points, all equivalent."
    → Proceed to next stage.

  NON-EQUIVALENT POINTS FOUND:
    → DO NOT PROCEED.
    → Go to Phase 3: Debug.

  ABORTED POINTS:
    → Tool could not prove equivalence (timeout, complexity)
    → Treat as SUSPICIOUS — not the same as PASS.
    → Go to Phase 3: Debug aborts.
```

### Phase 3: Debug (when non-equivalent or aborted)

```
FOR EACH NON-EQUIVALENT POINT:

  STEP 1: Identify the failing compare point
    - Which flip-flop or output is non-equivalent?
    - What is its fanin cone (the logic feeding it)?

  STEP 2: Trace the root cause
    ┌────────────────────────────────────────┬─────────────────────────────┐
    │ SYMPTOM                                │ LIKELY CAUSE                │
    ├────────────────────────────────────────┼─────────────────────────────┤
    │ Unmapped flip-flops in revised         │ Synthesis added registers   │
    │                                        │ (retiming, pipelining)      │
    │                                        │ → Map them or add as       │
    │                                        │   dont-verify if intentional│
    ├────────────────────────────────────────┼─────────────────────────────┤
    │ Missing flip-flops in revised          │ Synthesis optimized away    │
    │                                        │ (constant prop, dead code)  │
    │                                        │ → Verify the optimization   │
    │                                        │   is correct, then add      │
    │                                        │   mapping constraint        │
    ├────────────────────────────────────────┼─────────────────────────────┤
    │ Logic cone differs at gate level       │ Optimization changed the    │
    │                                        │ Boolean function            │
    │                                        │ → REAL BUG. Likely cause:   │
    │                                        │   - Synthesis across async  │
    │                                        │     boundary                │
    │                                        │   - Incorrect constant prop │
    │                                        │   - Clock gating changed    │
    │                                        │     enable logic            │
    ├────────────────────────────────────────┼─────────────────────────────┤
    │ Isolation/level-shifter cells          │ UPF cells have no RTL       │
    │ appear as non-equivalent               │ equivalent                  │
    │                                        │ → Set these as dont-verify  │
    │                                        │   OR use UPF-aware LEC mode │
    ├────────────────────────────────────────┼─────────────────────────────┤
    │ Points abort (cannot determine)        │ Logic cone too complex      │
    │                                        │ → Try: increase effort,     │
    │                                        │   add intermediate mapping, │
    │                                        │   partition the design      │
    └────────────────────────────────────────┴─────────────────────────────┘

  STEP 3: Fix and re-run
    - If root cause is a constraint issue: update constraints, re-run LEC
    - If root cause is a real synthesis bug: ESCALATE to synthesis agent
      with the exact non-equivalent point and its fanin analysis
    - NEVER mark a non-equivalent point as dont-verify to force PASS.
      That is falsifying the verification.
```

---

## 4. Tool-Specific Commands

### Yosys equiv_* (open-source flow)

```tcl
# ============================================
# LEC using Yosys equivalence checking
# ============================================

# Method 1: SAT-based (best for small-medium designs)
read_verilog -sv <rtl_files>            ;# reference
prep -top <top_module>
splitnets -ports
design -save reference

read_verilog <gate_netlist>             ;# revised
read_verilog -lib <lib_cells.v>         ;# cell functional models
prep -top <top_module>
splitnets -ports
design -save revised

# Perform equivalence check
equiv_make reference revised
equiv_simple                            ;# fast pass
equiv_induct                            ;# induction for sequential
equiv_status -assert                    ;# PASS or FAIL with details

# If equiv_simple + equiv_induct leave unresolved:
equiv_struct                            ;# structural matching
equiv_simple -undef                     ;# handle X-states
equiv_status -assert


# Method 2: Simulation-based sanity check (not formal, but fast)
# Use when formal is too slow for large designs
read_verilog -sv <rtl_files>
prep -top <top_module>
sim -clock clk -reset rst -n 1000 -w reference_trace.vcd

read_verilog <gate_netlist>
read_verilog -lib <lib_cells.v>
prep -top <top_module>
sim -clock clk -reset rst -n 1000 -w revised_trace.vcd

# Compare VCD outputs externally
# NOTE: This is NOT formal proof. It's a smoke test.
# Formal equiv_* is the real check.
```

### Cadence Conformal (production flow)

```tcl
# ============================================
# LEC using Conformal LEC
# ============================================

# Setup
set_system_mode setup

# Read reference (golden)
read_design -golden -verilog <rtl_files> -lastmod
set_top <top_module>

# Read revised (revised)
read_design -revised -verilog <gate_netlist> -lastmod
read_library -revised -liberty <lib_file>
set_top <top_module>

# Constraints
add_black_box <memory_instances>
set_dont_verify <scan_chain_points>
# For UPF designs:
read_power_intent -golden <upf_file> -1801
read_power_intent -revised <upf_file> -1801

# Map key points
set_system_mode map
report_unmapped_points > unmapped.rpt

# If unmapped points exist, investigate before proceeding
# Do NOT blindly add them to dont-verify

# Compare
set_system_mode compare
compare

# Report
report_compare_data > lec_results.rpt
report_compare_data -nonequiv > nonequiv_details.rpt

# Diagnosis for failures
diagnose -nonequiv
report_diagnosis > diagnosis.rpt
```

### Synopsys Formality (production flow)

```tcl
# ============================================
# LEC using Formality
# ============================================

# Read reference
read_sverilog -r <rtl_files>
set_top r:<top_module>

# Read implementation
read_verilog -i <gate_netlist>
read_db -i <lib_db_file>
set_top i:<top_module>

# Constraints
set_constant r:<scan_enable> 0
set_dont_verify_points r:<test_only_signals>

# For designs with retiming:
set_parameter -name retiming -value on

# Verify
match
verify

# Reports
report_status > fm_status.rpt
report_failing > fm_failures.rpt

# Debug failures
diagnose
report_diagnosis > fm_diagnosis.rpt
```

---

## 5. Special Cases

### Retiming

When synthesis moves flip-flops across combinational logic (retiming), the compare points change. Standard LEC will show non-equivalence because flip-flop boundaries moved.

```
HANDLING RETIMING:
  1. Tell the LEC tool that retiming was used:
     Conformal: set_analyze_option -auto
     Formality: set_parameter -name retiming -value on
     Yosys: equiv_induct (handles sequential equivalence)

  2. The tool will attempt multi-cycle equivalence checking
     instead of single-cycle comparison.

  3. If the tool cannot resolve retimed points:
     → Fall back to simulation-based equivalence as a supplement
     → Run 10,000+ random cycles comparing outputs
     → This is NOT a formal proof but increases confidence

  4. Document in the LEC report: "Retiming detected on <N> paths.
     Sequential equivalence used. Simulation supplement: <N> cycles,
     0 mismatches."
```

### Clock Gating

Synthesis-inserted clock gating adds enable logic to clock paths. The RTL doesn't have these gates, so LEC sees "extra" logic.

```
HANDLING CLOCK GATING:
  1. Use the synthesis tool's mapping file (DC: write_script,
     Genus: write_do_lec) — it tells the LEC tool where CG cells were added.

  2. Set clock-gating cells as verified-by-construction:
     They were inserted by the tool, not by a human.

  3. Verify the enable logic is correct — the CG enable should
     match the original flop enable. If it doesn't, that's a
     synthesis bug.
```

### UPF / Low Power Cells

Isolation cells, level shifters, retention flops, and power switches exist in the netlist but NOT in the RTL. They will show as non-equivalent.

```
HANDLING UPF CELLS:
  Option A (preferred): UPF-aware LEC
    - Both Conformal and Formality support reading UPF
    - The tool understands that isolation/LS cells are structurally
      required and verifies their placement instead of comparing logic

  Option B (manual): Constraint-based
    - Set isolation cells as dont-verify for logic comparison
    - Separately verify isolation cell presence with the Low Power Agent
    - This splits verification: LEC checks logic, LP agent checks power

  NEVER use Option B if Option A is available. Splitting the check
  creates a coverage gap where an isolation cell is present but
  has incorrect logic.
```

### ECO Changes

After post-route ECOs (buffer insertion, cell swapping, net rewiring):

```
HANDLING ECOs:
  - Compare pre-ECO netlist (reference) vs post-ECO netlist (revised)
  - Scope the check: if the ECO only touched 5 cells, most compare
    points won't change. A fast incremental check is sufficient.
  - Conformal: set_system_mode -incr_compare
  - Formality: verify -incr

  IF the ECO was a functional fix (not just timing):
  - Run FULL LEC, not incremental
  - Functional ECOs can have unintended side effects on unrelated paths
```

---

## 6. LEC Report Format

Every LEC run produces a standardized report. Downstream agents and the human read this.

```
LEC REPORT:
┌──────────────────────────────────────────────────────────────┐
│ CHECKPOINT:   Post-synthesis                                  │
│ DATE:         2026-05-13                                      │
│ TOOL:         Yosys equiv / Conformal / Formality             │
│                                                               │
│ REFERENCE:    rtl/datapath_design.v (top: datapath_design)    │
│ REVISED:      netlist/datapath_design_synth.v                 │
│                                                               │
│ COMPARE POINTS:                                               │
│   Primary inputs:    34    mapped: 34   unmapped: 0           │
│   Primary outputs:   8     mapped: 8    unmapped: 0           │
│   DFF data pins:     142   mapped: 140  unmapped: 2           │
│   Total:             184   mapped: 182  unmapped: 2           │
│                                                               │
│ UNMAPPED ANALYSIS:                                            │
│   2 DFFs added by clock gating — verified as CG latch enables │
│                                                               │
│ RESULT:                                                       │
│   Equivalent:        182 / 182                                │
│   Non-equivalent:    0                                        │
│   Aborted:           0                                        │
│                                                               │
│ VERDICT:  ✅ PASS                                             │
│                                                               │
│ CONSTRAINTS APPLIED:                                          │
│   - scan_enable = 0 (test mode disabled)                      │
│   - SRAM instances black-boxed (2 instances)                  │
│                                                               │
│ NOTES:                                                        │
│   - No retiming detected                                      │
│   - Clock gating cells verified structurally                  │
└──────────────────────────────────────────────────────────────┘
```

---

## 7. Anti-Patterns

```
❌ "LEC passed once after synthesis, we're good for tapeout"
   → Every PD stage modifies the netlist. Post-route netlist can differ
     from post-synthesis netlist. Run LEC at every checkpoint.

❌ "These 3 non-equivalent points are probably fine — marking dont-verify"
   → This is falsifying verification. Debug every non-equivalent point.
     If it's a known safe difference (UPF cell, retiming), document WHY
     it's safe, not just that you're skipping it.

❌ "Simulation matched for 100 cycles, so equivalence is proven"
   → Simulation is a smoke test, not a proof. 100 cycles out of 2^34
     possible input combinations proves nothing formally. Use it as
     supplement, never as primary evidence.

❌ "The synthesis tool verified equivalence internally"
   → Synthesis tools do incremental checks during optimization. This is
     NOT the same as independent LEC. The tool trusts its own transforms.
     Independent LEC catches tool bugs.

❌ "LEC is too slow on our design, skip it for this tape-out"
   → Partition the design. Run LEC per block. Use incremental mode.
     There is always a way to make it tractable. Skipping it is not one.

❌ "Post-ECO LEC failed but the ECO was just buffer insertion"
   → Buffer insertion should never change logic. If LEC fails after
     "just buffers," either: (a) it wasn't just buffers, or (b) a buffer
     was inserted incorrectly (wrong net). Both are real bugs.
```

---

## 8. Escalation

```
ESCALATE TO SYNTHESIS AGENT WHEN:
  → Non-equivalent points trace to synthesis optimization decisions
  → Clock gating enable logic doesn't match original flop enable
  → Constant propagation removed logic that shouldn't be constant

ESCALATE TO HUMAN WHEN:
  → >10 non-equivalent points with unclear root cause
  → Aborted points that cannot be resolved with increased effort
  → Disagreement about whether a difference is intentional
  → LEC tool bug suspected (same design passes in one tool, fails in another)

ESCALATE FORMAT:
  "LEC FAILED at checkpoint <name>.
   Non-equivalent points: <N>. Aborted: <M>.
   Root cause analysis: <diagnosis per point>.
   Attempted resolution: <what was tried>.
   Blocking: <which downstream agents are waiting>.
   Recommended action: <specific fix or decision needed>."
```

---

## 9. Integration With Other Agents

```
UPSTREAM:
  ← Synthesis Agent: provides RTL + netlist pair for post-synth LEC
  ← Placement Agent: provides placed netlist for post-placement LEC
  ← CTS Agent: provides CTS netlist
  ← Routing Agent: provides routed netlist
  ← ECO Agent: provides pre/post ECO netlist pairs

DOWNSTREAM:
  → ALL AGENTS: LEC PASS is a gate. No agent proceeds without it.
  → Signoff Agent: final RTL-vs-GDSII LEC is part of tapeout checklist

CROSS-CUTTING:
  ↔ Low Power Agent: coordinates on UPF cell handling in LEC
  ↔ Checkpoint Manager: LEC results stored with each checkpoint
```
