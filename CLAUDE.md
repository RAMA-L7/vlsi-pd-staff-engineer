# VLSI Physical Design Staff Engineer

Multi-agent framework for RTL-to-GDSII physical design using
Yosys + OpenSTA + OpenROAD on Sky130 PDK.

## Quick Start

This project uses Claude Code's agent/skill/hook system to run
a disciplined PD flow with verification gates between every stage.

### Stage Agents (RTL-to-GDSII pipeline)

- `synthesis-engineer` — RTL compilation, multi-corner optimization, QoR targeting
- `floorplan-engineer` — die sizing, macro placement, power grid, pin assignment
- `placement-engineer` — global/detailed placement, congestion, timing-driven opt
- `cts-engineer` — clock tree synthesis, skew balancing, useful skew
- `routing-engineer` — global/detail routing, DRC convergence, hold fixing
- `signoff-engineer` — multi-corner STA, DRC, LVS, IR drop, EM, GDSII

### Cross-Cutting Agents (run at every stage boundary)

- `lec-verifier` — formal equivalence checking at every stage boundary
- `low-power-verifier` — UPF/CPF domain verification (different checks per stage)

### Reactive Agent (dispatched on failure)

- `forensic-eco-debugger` — four-phase investigation, five-level trace, three-fix rule

### Stage Gate Rules

Every PD stage must pass three checks before the next stage starts:
1. QoR reviewer approves metrics (fresh sub-agent audit)
2. LEC verifies equivalence against the previous stage
3. Low power agent checks domain integrity

### Three-Fix Rule

If 3 ECOs don't resolve a failure, STOP and escalate. Do not
attempt ECO #4 without human approval and five-level root cause
trace.

### Tool Environment

Session-start hook auto-detects: Yosys, OpenSTA, OpenROAD, PDK path,
library files, and QoR targets from synthesis_targets.json.

## Project Structure

```
vlsi-pd-staff-engineer/
├── CLAUDE.md                              # this file
├── synthesis_targets.json                 # QoR targets + corner definitions
├── agents/                                # team roster (9 agents)
│   ├── synthesis-engineer.md              #   RTL → gate netlist
│   ├── floorplan-engineer.md              #   die + macros + power grid
│   ├── placement-engineer.md              #   cell placement + legalization
│   ├── cts-engineer.md                    #   clock tree synthesis
│   ├── routing-engineer.md                #   metal wires + DRC clean
│   ├── signoff-engineer.md                #   final verification + GDSII
│   ├── lec-verifier.md                    #   equivalence at every boundary
│   ├── low-power-verifier.md              #   domain checks at every stage
│   └── forensic-eco-debugger.md           #   reactive failure investigation
├── skills/                                # process library (10 skills)
│   ├── using-pd-staff-engineer/SKILL.md   #   culture deck (loaded every session)
│   ├── synthesis/SKILL.md                 #   641 lines — optimization strategies
│   ├── synthesis/qor-reviewer-prompt.md   #   QoR audit sub-agent
│   ├── floorplan/SKILL.md                 #   macro placement, PG, pins
│   ├── placement/SKILL.md                 #   congestion, timing-driven
│   ├── cts/SKILL.md                       #   skew, buffers, Bug #3 territory
│   ├── routing/SKILL.md                   #   DRC convergence, hold fixing
│   ├── signoff/SKILL.md                   #   MMMC STA, tapeout checklist
│   ├── lec/SKILL.md                       #   9 checkpoint protocol
│   ├── lec/lec-reviewer-prompt.md         #   LEC audit sub-agent
│   ├── low-power/SKILL.md                 #   cross-cutting domain checks
│   └── forensic-debugging/SKILL.md        #   four-phase ECO investigation
├── hooks/                                 # management layer
│   ├── hooks.json                         #   session-start trigger
│   ├── run-hook.cmd                       #   cross-platform runner
│   └── session-start                      #   env detection + handbook load
└── commands/                              # custom CLI commands (future)
```
