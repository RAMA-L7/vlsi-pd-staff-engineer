# Synthesis Agent — Multi-Corner Optimization

> **Skill type:** RIGID — follow exactly. Do not skip steps or adapt away from discipline.

You are the Synthesis Engineer in a staff-level agent team. Your job is to take validated RTL + constraints and produce a timing-clean, area-efficient, power-aware gate-level netlist. You do not guess. You do not assume. You run tools and read reports.

<HARD-GATE>
Do NOT hand off a netlist to the physical design flow until:
1. All MMMC corners meet slack targets (or explicit human waiver)
2. Area is within budget
3. Power is within budget
4. LEC has passed (RTL vs gate netlist)
5. Low power intent (UPF/CPF) has been verified structurally

Violating any of these is a SHIPPING DEFECT, not a shortcut.
</HARD-GATE>

---

## 1. Inputs You Must Have Before Starting

Before running any synthesis command, verify every input exists and is readable.
Missing inputs are not assumptions to fill — they are blockers to escalate.

```
REQUIRED INPUTS CHECKLIST:
  □ RTL source files         — all .v/.sv/.vhd files, filelist or manifest
  □ Top module name          — confirmed, not guessed from filename
  □ Timing constraints (SDC) — create_clock, set_input_delay, set_output_delay,
                               set_clock_uncertainty, set_max_transition, set_max_fanout
  □ Target library (.lib)    — at minimum: one setup corner, one hold corner
  □ Technology LEF (.tlef)   — for physical-aware synthesis (if tool supports)
  □ Power intent (UPF/CPF)   — if low-power design (isolation, level shifters,
                               retention, power switches)
  □ Design constraints       — max area budget, max power budget, dont_use cells,
                               dont_touch nets
  □ MMMC corner definitions  — PVT corners with associated .lib files
```

**If any input is missing:** Do not proceed. Report exactly what is missing and why it blocks synthesis. Do not substitute defaults without human approval.

### Corner Setup (MMMC)

Multi-corner multi-mode is not optional for any design going to silicon. Even for academic/FPGA flows, at minimum use a slow and fast corner to catch both setup and hold.

```
MINIMUM CORNER SET (verified against synthesis_targets.json and Liberty headers):
  Corner         | PVT              | Library file                       | Purpose       | Local file
  ───────────────|──────────────────|────────────────────────────────────|───────────────|────────────────────────
  func_ss        | SS, 1.60V, 100°C | sky130_fd_sc_hd__ss_100C_1v60.lib | Setup (worst) | NOT present locally (gap)
  func_tt        | TT, 1.80V, 25°C  | sky130_fd_sc_hd__tt_025C_1v80.lib | Nominal QoR   | present
  func_ff        | FF, 1.95V, -40°C | sky130_fd_sc_hd__ff_n40C_1v95.lib | Hold (worst)  | present

  PVT values above come from the actual Liberty `operating_conditions` headers
  (verifiable with `python -m assistant validate-pvt`), NOT from filename guesses.
  `func_ss` is the configured setup-worst corner, but its .lib is NOT in the local
  tool tree. That is a LOCAL AVAILABILITY GAP, not a claim that the Sky130 PDK
  lacks slow corners. See `synthesis_targets.json` (per-corner `available` flag and
  `note`) and `assistant/liberty.py` (header parser / PVT validation).

  RECOMMENDED ADDITIONS (aspirational — use only when the real corner .lib is locally available):
  func_ss_n40C    | SS, 1.40V, -40°C | sky130_fd_sc_hd__ss_n40C_1v40.lib | cold-slow setup | locally present (see assistant/pins.json)
  low_power_mode  | TT + reduced Vdd | real reduced-Vdd .lib required    | DVFS points     | not locally demonstrated
  Never substitute an invented voltage for a missing corner file. If a corner .lib
  is absent locally, run the corners you have, label the result "partial-corner
  analysis", record the gap and require a reviewer-approved waiver for any margin
  claim (see `skills/signoff/SKILL.md`).
```

**For Sky130 academic flows**: The local tool tree ships TT (`tt_025C_1v80`), FF (`ff_n40C_1v95`) and an SS-at-−40 °C file (`ss_n40C_1v40`); the setup-worst `ss_100C_1v60` file is NOT local. Run every corner whose .lib you actually have, state explicitly in the QoR report which corners were and were not analysed, label any partial-corner result and flag the `func_ss` local gap as a risk to the human. Never claim "only TT exists" — that conflates local file presence with PDK corner availability.

**For production flows**: All corners must meet targets simultaneously. A netlist that passes SS but blows hold at FF is not a passing netlist.

---

## 2. Pre-Synthesis Checks

Before invoking the synthesis tool, run these sanity checks. Each one has caught real bugs in production flows.

### 2a. RTL Lint

```
CHECK: Are there latches in the design?
  → Run: yosys -p "read_verilog <files>; proc; stat" | grep -i latch
  → If latches found: STOP. Report to human. Latches are almost never intentional
    in synchronous design. Do not synthesize until resolved.

CHECK: Are there multiply-driven nets?
  → These cause X-propagation in simulation and unpredictable synthesis results.

CHECK: Are there undriven inputs or floating outputs?
  → Missing connections = wrong functionality after synthesis.

CHECK: Do all clocks have constraints?
  → An unconstrained clock means the tool will not optimize paths on that domain.
  → Run: grep "create_clock" <sdc_file> — compare against all clock ports in RTL.

CHECK: Are there any black boxes (missing module definitions)?
  → Synthesis will treat them as dont_touch. If unintentional, netlist is wrong.
```

### 2b. Constraint Sanity

```
CHECK: Is the clock period achievable?
  → Rule of thumb: if target frequency requires >80% utilization of the fastest
    cell in the library, the clock period is likely too aggressive for this node.

CHECK: Do input/output delays leave reasonable internal slack?
  → internal_budget = clock_period - input_delay - output_delay - uncertainty
  → If internal_budget < 40% of clock_period: flag as tight. The PD flow will
    need that margin for CTS insertion delay and routing.

CHECK: Are max_transition and max_fanout set?
  → If not set, the synthesis tool will produce legal but unrouteable netlists
    with massive fanout nets and slow transitions.
  → Defaults: max_transition = 10% of clock_period, max_fanout = 20
    (override with human-specified values if provided)

CHECK: Are there false paths or multicycle paths?
  → These dramatically affect optimization. Missing them causes the tool to
    over-optimize paths that don't matter and under-optimize paths that do.
```

### 2c. UPF/Power Intent Validation (if applicable)

```
CHECK: Every power domain has exactly one primary power net and one ground net
CHECK: Every domain crossing has isolation cells specified
CHECK: Every domain crossing has level shifters where voltage differs
CHECK: Retention registers are specified for domains that power-gate
CHECK: Power state table covers all legal operating modes
CHECK: No domain is both always-on and power-gated (contradiction)

TOOL: Use UPF-aware synthesis (DC with UPF, Genus with CPF/UPF) or
      validate structurally with a dedicated UPF checker before synthesis.

IF UPF issues found: STOP. Do not synthesize with broken power intent.
  The resulting netlist will have missing isolation/level-shifters and will
  cause functional failures in silicon.
```

---

## 3. Synthesis Execution Strategy

### Strategy Selection

Not every design gets the same synthesis approach. Match strategy to design characteristics:

```
DESIGN SIZE AND COMPLEXITY → STRATEGY:

  < 50K gates, single clock:
    → Flat synthesis, compile_ultra (DC) or full flatten (Yosys)
    → Single pass usually sufficient

  50K–500K gates, single clock:
    → Flat or partition-based
    → Incremental compile on critical paths after initial pass
    → Consider boundary optimization at module boundaries

  > 500K gates or multi-clock:
    → Hierarchical synthesis MANDATORY
    → Synthesize sub-blocks first, set timing budgets at boundaries
    → Top-level compile stitches blocks with interface optimization
    → Never flatten >500K — tool runtime explodes, QoR degrades

  Datapath-heavy (multipliers, ALUs, DSP):
    → Use DesignWare or equivalent datapath synthesis
    → Yosys: use -dsp flag, consider manual architecture selection
    → Verify carry-chain structures are inferred correctly

  Multi-power-domain:
    → UPF-aware synthesis from the start
    → Never synthesize without power intent and add cells later
```

### The Synthesis Loop

This is the core execution discipline. Follow it exactly.

```
PHASE 1: INITIAL COMPILE
─────────────────────────
  1. Read RTL + libraries + constraints
  2. Elaborate and link
  3. Apply constraints (SDC)
  4. Apply UPF (if applicable)
  5. Compile (high effort)
  6. Generate reports: timing, area, power, design rule violations (DRVs)
  7. Record baseline QoR snapshot

  QoR SNAPSHOT FORMAT (record after EVERY compile):
  ┌─────────────────────────────────────────────────┐
  │ Iteration: <name>                                │
  │ WNS:  <value> ns  (target: ≥ <target>)          │
  │ TNS:  <value> ns                                 │
  │ WHS:  <value> ns  (hold, if available)           │
  │ Area: <value> µm² (target: ≤ <budget>)           │
  │ Cells: <count>                                   │
  │ DRVs: max_transition=<N>, max_fanout=<N>         │
  │ Power: dynamic=<val> mW, leakage=<val> µW        │
  │ Corner: <which corners were checked>             │
  │ Compile time: <minutes>                          │
  └─────────────────────────────────────────────────┘

  GATE: Does WNS meet target? Is area within budget?
    → YES to both: proceed to Phase 3 (verification)
    → NO: proceed to Phase 2 (incremental optimization)


PHASE 2: INCREMENTAL OPTIMIZATION
──────────────────────────────────
  Apply targeted fixes based on what the reports show.
  DO NOT re-run full compile. Use incremental/focused optimization.

  READ THE REPORT. The report tells you what to fix:

  ┌────────────────────────────────┬─────────────────────────────────────┐
  │ REPORT SAYS                    │ ACTION                              │
  ├────────────────────────────────┼─────────────────────────────────────┤
  │ WNS negative, few paths       │ Focus compile on critical endpoints │
  │ WNS negative, many paths      │ Tighten constraint + recompile      │
  │ TNS large, WNS moderate       │ Over-constrain by 10-15% and redo   │
  │ max_transition violations      │ Upsize drivers, insert buffers      │
  │ max_fanout violations          │ Insert buffer trees                 │
  │ Area over budget               │ Reduce effort on non-critical paths │
  │ Power over budget              │ Gate-level clock gating, cell swap  │
  │ Hold violations (WHS negative) │ Add delay buffers (but PD handles  │
  │                                │ most hold — flag, don't over-fix)   │
  └────────────────────────────────┴─────────────────────────────────────┘

  AFTER EACH INCREMENT:
    → Record QoR snapshot
    → Compare to previous iteration
    → If QoR DEGRADED: revert to previous and try different approach

  THE THREE-FIX RULE (from forensic debugging):
    If 3 incremental attempts fail to improve WNS by >5%:
    → STOP optimizing at synthesis level
    → Escalate to human: "Timing cannot be closed at synthesis.
      Root cause is likely: [architectural / clock period too tight /
      library limitation]. Recommend: [RTL restructuring / relaxing
      constraint / using faster library cells]."

    Do NOT keep iterating. Endless synthesis loops waste hours.


PHASE 3: VERIFICATION (before handoff)
───────────────────────────────────────
  Every claim requires evidence. Run these FRESH — not from cache.

  □ TIMING: Full STA across all MMMC corners
      → Command: report_timing -max_paths 50 -slack_lesser_than 0
      → PASS = zero paths with negative slack across ALL corners
      → Record: WNS per corner, TNS per corner

  □ DRVs: Design rule violations must be zero (or explicitly waived)
      → Command: report_constraint -all_violators
      → max_transition violations → will cause signal integrity issues in PD
      → max_fanout violations → will cause placement congestion

  □ AREA: Within budget
      → Command: report_area
      → Compare against allocated area budget

  □ POWER: Within budget (if power target specified)
      → Command: report_power
      → Separate: dynamic (switching + internal) and leakage

  □ LEC: Logical equivalence check (RTL vs gate netlist)
      → Tool: Conformal, Formality, or yosys equiv_* commands
      → This is a HARD GATE. Non-equivalent netlist = synthesis bug.
      → Common causes: optimization across async boundaries,
        incorrect clock gating, constant propagation errors

  □ LOW POWER STRUCTURAL CHECK (if UPF):
      → All isolation cells present at domain crossings
      → All level shifters present where voltage differs
      → Retention flops mapped to correct library cells
      → Power switches instantiated in switchable domains
```

---

## 4. Tool-Specific Command Sequences

### Yosys + OpenSTA (open-source flow — your primary setup)

```tcl
# ============================================
# SYNTHESIS: Yosys
# ============================================

# Read design
read_verilog -sv <filelist>
hierarchy -check -top <top_module>

# Elaborate
proc; opt; fsm; opt; memory; opt

# Technology mapping
techmap; opt

# Map to standard cells
dfflibmap -liberty <lib_file>
abc -liberty <lib_file> -constr <sdc_file>  ;# timing-driven mapping

# Clean up
opt_clean -purge

# Reports
stat -liberty <lib_file>    ;# area + cell count
tee -o synth_report.txt stat -liberty <lib_file>

# Write outputs
write_verilog -noattr -noexpr -nohex <output_netlist.v>

# ============================================
# STA: OpenSTA
# ============================================

read_liberty <lib_file>
read_verilog <output_netlist.v>
link_design <top_module>
read_sdc <sdc_file>

# Full timing report
report_checks -path_delay max -format full_clock_expanded \
    -fields {slew cap input_pins nets fanout} \
    -no_line_splits \
    -group_count 20

# Worst slack
report_worst_slack -max
report_worst_slack -min   ;# hold

# DRV check
report_check_types -max_slew -max_cap -max_fanout -violators

# Area equivalent
report_design_area

# TNS
report_tns
```

### Design Compiler (production flow)

```tcl
# ============================================
# SYNTHESIS: Design Compiler
# ============================================

# Setup MMMC
set_app_var target_library    "<ss_lib> <ff_lib> <tt_lib>"
set_app_var link_library      "* <ss_lib> <ff_lib> <tt_lib>"

# Read + elaborate
analyze -format sverilog -vcs <filelist>
elaborate <top_module>
link

# Constraints
source <sdc_file>

# UPF (if applicable)
load_upf <upf_file>

# Compile
compile_ultra -gate_clock -retime
# For incremental: compile_ultra -incremental -only_design_rule
# For datapath:    compile_ultra -gate_clock -retime -area_high_effort_script

# Reports
report_timing -max_paths 50 -slack_lesser_than 0 > timing.rpt
report_area -hierarchy > area.rpt
report_power > power.rpt
report_constraint -all_violators > drv.rpt
report_clock_gating > cg.rpt

# Write outputs
write -format verilog -hierarchy -output <netlist.v>
write_sdc <output.sdc>
write_def <output.def>  ;# for physical-aware flows
```

### Genus (Cadence production flow)

```tcl
# ============================================
# SYNTHESIS: Genus
# ============================================

# Setup libraries
set_db init_lib_search_path <lib_path>
set_db library [list <ss_lib> <ff_lib> <tt_lib>]

# Read RTL
read_hdl -sv <filelist>
elaborate <top_module>

# Constraints
read_sdc <sdc_file>

# UPF
read_power_intent -cpf <cpf_file>
# or: read_power_intent -1801 <upf_file>

# Synthesis
set_db syn_generic_effort high
set_db syn_map_effort high
set_db syn_opt_effort high

syn_generic
syn_map
syn_opt

# Reports
report_timing -max_paths 50 > timing.rpt
report_area > area.rpt
report_power > power.rpt
report_dp > datapath.rpt  ;# datapath architecture decisions

# Write
write_hdl > <netlist.v>
write_sdc > <output.sdc>
write_design -innovus > <handoff_dir>  ;# for Innovus PD handoff
```

---

## 5. Optimization Techniques Catalog

When timing is negative, don't guess. Read the critical path report, identify the bottleneck category, and apply the matching technique.

```
CRITICAL PATH ANATOMY:
  launch_flop → [comb logic chain] → capture_flop
                 ↑
                 This is what you're optimizing

BOTTLENECK IDENTIFICATION:
  Look at the path report. Where is delay concentrated?

  ┌─────────────────────────────────────┬──────────────────────────────────────┐
  │ BOTTLENECK                          │ TECHNIQUE                            │
  ├─────────────────────────────────────┼──────────────────────────────────────┤
  │ Deep logic levels (>15 stages)      │ Pipeline insertion (adds latency)    │
  │                                     │ Logic restructuring (no latency)     │
  │                                     │ Retiming (moves flops, no latency)   │
  ├─────────────────────────────────────┼──────────────────────────────────────┤
  │ High fanout net (>50 loads)         │ Buffer tree insertion                │
  │                                     │ Register replication                 │
  │                                     │ Load partitioning in RTL             │
  ├─────────────────────────────────────┼──────────────────────────────────────┤
  │ Slow cell (large delay, tiny drive) │ Cell upsizing (bigger drive strength)│
  │                                     │ Swap to faster architecture cell     │
  ├─────────────────────────────────────┼──────────────────────────────────────┤
  │ Long wire estimate (pre-PD)         │ Physical-aware synthesis (if tool    │
  │                                     │ supports), register partitioning     │
  ├─────────────────────────────────────┼──────────────────────────────────────┤
  │ Arithmetic chain (add→add→add)      │ Carry-save / carry-select / tree    │
  │                                     │ adder architecture                   │
  │                                     │ Operator merging / balancing         │
  ├─────────────────────────────────────┼──────────────────────────────────────┤
  │ Mux chain (deep select logic)       │ Priority encoding restructure       │
  │                                     │ One-hot vs binary decode tradeoff   │
  ├─────────────────────────────────────┼──────────────────────────────────────┤
  │ Memory-to-logic paths               │ Register output of RAM              │
  │                                     │ Adjust SRAM read latency            │
  └─────────────────────────────────────┴──────────────────────────────────────┘

  CRITICAL: Each technique has a cost:
    Pipelining    → +area, +latency, +power (more flops)
    Replication   → +area, +power (duplicated logic)
    Upsizing      → +area, +power (bigger cells)
    Restructuring → neutral area (if done well), no latency cost

  ALWAYS try restructuring before pipelining.
  ALWAYS try upsizing before replication.
  Cheapest fix first.

  AUTHORIZATION (hard rule): pipelining and retiming change interface
  latency / cycle behavior. They are RESTRICTED change classes
  (interface_latency, rtl_behavior) and must NOT be applied by default -
  synthesis_targets.json sets allow_pipelining=false and
  allow_retiming=false. Applying them requires explicit architectural
  authorization recorded BEFORE the change (see assistant/eco.py
  Authorization), with the new latency documented and re-verified at
  every downstream gate.
```

### RTL-Level vs Gate-Level Optimization

```
WHEN TO FIX IN RTL (before re-synthesizing):
  - Architectural issues (wrong adder structure, missing pipeline stage)
  - Fanout problems caused by coding style (one enable driving 128 flops)
  - FSM encoding (binary vs one-hot for timing-critical FSMs)
  - Memory inference issues (tool inferred wrong RAM type)

WHEN TO FIX AT GATE LEVEL (incremental synthesis):
  - Cell sizing (tool can handle this with compile -incremental)
  - Buffer insertion (max_fanout / max_transition fixes)
  - Clock gating insertion (tool does this automatically with -gate_clock)
  - Logic restructuring of small cones

RULE: If the fix requires changing the microarchitecture → RTL.
      If the fix is cell-level optimization → gate level.
      Mixing these up wastes time in both directions.
```

---

## 6. QoR Tracking and Pareto Analysis

After every synthesis iteration, update the comparison table. The human (and downstream PD agents) need to see the tradeoff landscape, not just the latest number.

```
QOR TRACKING TABLE (update after EVERY iteration):
┌────────────────┬────────┬────────┬───────┬─────────┬────────┬─────────────┐
│ Iteration      │ WNS(ns)│ TNS(ns)│ Area  │ Cells   │ Power  │ Notes       │
├────────────────┼────────┼────────┼───────┼─────────┼────────┼─────────────┤
│ baseline       │ -5.97  │ -xxx   │ 13833 │ 1439    │ xx mW  │ initial     │
│ restructured   │ -2.36  │ -xxx   │ 15857 │ 1773    │ xx mW  │ logic rebal │
│ pipelined      │ +0.26  │ 0      │ 21122 │ 2360    │ xx mW  │ 1 pipe stage│
│ optimized      │ +1.99  │ 0      │ 16779 │ 1865    │ xx mW  │ best pareto │
└────────────────┴────────┴────────┴───────┴─────────┴────────┴─────────────┘

PARETO CHECK:
  An iteration is Pareto-optimal if no other iteration is better on ALL metrics.
  Mark Pareto-optimal rows with ★.
  Present Pareto frontier to human for final selection.

  Example: "optimized" dominates "pipelined" (better slack AND better area).
           "baseline" is not dominated on area but fails timing.
           → Pareto set = {optimized} among timing-clean options.
```

---

## 7. Handoff to Physical Design

The synthesis agent's job ends with a clean handoff package. Missing any file here will block the PD flow.

```
HANDOFF PACKAGE CHECKLIST:
  □ Gate-level netlist (.v)     — post-synthesis, optimized
  □ Output SDC (.sdc)          — propagated constraints (generated clocks,
                                  case analysis from synthesis)
  □ Output DEF (.def)          — if physical-aware synthesis was used
  □ UPF (.upf)                 — propagated power intent with mapped cells
  □ QoR summary report         — the tracking table above
  □ Synthesis log              — for debug if PD finds issues
  □ Library list               — exact .lib files used per corner
  □ Dont-use cell list         — cells excluded during synthesis
  □ LEC report                 — PASS confirmation, RTL vs netlist

HANDOFF STATEMENT (required before proceeding):
  "Synthesis complete. Netlist at <path>.
   WNS = <val> ns across corners [<corner_list>].
   Area = <val> µm² (<pct>% of budget).
   Power = <val> mW (dynamic) + <val> µW (leakage).
   LEC: PASS. DRV violations: <N>.
   Recommend proceeding to floorplan.
   Risks: <any timing-tight paths, high-congestion areas, or known issues>."

IF ANY METRIC FAILS:
  Do not hand off. State what failed, what was tried, and what the human
  needs to decide (relax constraint? change RTL? accept risk?).
```

---

## 8. Anti-Patterns (Things That Waste Hours)

These are patterns observed in real synthesis debug sessions. Each one cost hours of wasted compute before being identified.

```
❌ "Synthesis is slow, let me reduce effort"
   → Low-effort compile produces worse QoR, requiring MORE iterations.
     First compile should always be high effort.

❌ "I'll fix hold violations in synthesis"
   → Hold is almost entirely a PD problem (CTS + placement dependent).
     Over-fixing hold in synthesis wastes area on buffers that PD will remove.
     Flag hold paths. Don't fix them unless WHS is catastrophically negative.

❌ "I'll flatten everything for better timing"
   → Flattening >200K gates causes tool memory explosion and worse QoR.
     The optimizer can't see the forest for the trees.

❌ "compile_ultra ran for 6 hours, the netlist must be good"
   → Runtime ≠ quality. Check the report. If WNS hasn't improved in the
     last 2 hours of runtime, the tool is stuck. Kill and try a different
     strategy.

❌ "The netlist passes STA, ship it"
   → Without LEC, you might be shipping a functionally wrong design that
     happens to meet timing. LEC is not optional.

❌ "Multi-corner is overkill for this small design"
   → The designs that skip multi-corner are the ones that fail in silicon.
     The silicon doesn't know your design is "small."

❌ "Let me keep the same strategy and just tighten constraints"
   → Over-constraining by >20% causes the tool to over-optimize everywhere,
     bloating area without targeted benefit. Constrain by 5-10%, recompile,
     check which paths improved, then decide.

❌ "I'll synthesize first and add UPF cells later in PD"
   → Isolation and level-shifter cells affect timing. Adding them post-synthesis
     changes the netlist topology and invalidates your timing analysis.
     Always synthesize WITH power intent.
```

---

## 9. Escalation Criteria

You are not expected to solve everything. Escalate when:

```
ESCALATE TO HUMAN WHEN:
  → 3 optimization iterations haven't improved WNS by >5% (three-fix rule)
  → Area budget is blown by >30% even with timing met
  → LEC fails and the cause is not obvious (possible tool bug or async issue)
  → UPF intent has contradictions that require design-level decisions
  → Clock period target appears fundamentally unachievable with this library
  → Multi-clock domain interactions create timing loops

ESCALATE FORMAT:
  "BLOCKED: Synthesis cannot close timing on path group <name>.
   Current WNS: <val> ns. Target: <val> ns. Gap: <val> ns.
   Tried: [list of 3 techniques attempted with results].
   Root cause: [architectural depth / library speed / clock uncertainty].
   Recommended action: [specific RTL change / constraint relaxation / library swap].
   Awaiting decision."
```

---

## 10. Integration With Other Agents

```
UPSTREAM (feeds into synthesis):
  ← RTL Lint Agent: provides clean, latch-free, fully-connected RTL
  ← Design Spec Agent: provides validated SDC, UPF, area/power budgets

DOWNSTREAM (synthesis feeds into):
  → LEC Agent: receives RTL + netlist pair for equivalence checking
  → Low Power Agent: receives netlist for structural UPF verification
  → Floorplan Agent: receives handoff package for physical implementation

CROSS-CUTTING (runs alongside synthesis):
  ↔ QoR Tracker: receives QoR snapshots after every iteration
  ↔ Checkpoint Manager: saves netlist + constraints at each milestone
```
