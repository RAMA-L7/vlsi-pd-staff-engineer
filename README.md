# VLSI Physical Design Staff Engineer

A multi-agent framework for RTL-to-GDSII physical design using Claude Code. Adapts the [Senior Staff Engineer with Sub-Agent Teams](https://github.com/FareedKhan-dev/claude-code-staff-engineer) pattern to VLSI physical design, with stage-specific agents, cross-cutting verification, and gate checkpoints at every boundary.

**Toolchain:** Yosys + OpenSTA + OpenROAD on Sky130 PDK

## What this is

A set of agent definitions, skills, and hooks for Claude Code that make it work like a disciplined PD engineering team instead of a generic coding assistant. Each PD stage has a specialized agent with deep domain knowledge, and verification agents (LEC + low power) run at every stage boundary to catch bugs before they propagate.

The framework enforces three rules that prevent the most common AI agent failures in PD work:

1. **Stage gates** — no stage proceeds without QoR review + LEC + low power verification passing
2. **Three-fix rule** — if 3 ECOs don't resolve a failure, stop and trace the root cause upstream
3. **Evidence before claims** — every signoff check runs fresh, no "it passed earlier"

## Architecture

```
RTL ──→ Synthesis ──→ Floorplan ──→ Placement ──→ CTS ──→ Routing ──→ Signoff ──→ GDSII
           │             │             │            │          │           │
           └──── GATE ───┴──── GATE ───┴─── GATE ──┴── GATE ──┴── GATE ──┘
                  │              │             │          │          │
              QoR review    QoR review    QoR review  QoR review  All 9
              LEC (RTL→gate) LEC (adj)   LEC (adj)   LEC (adj)  signoff
              Low power     Low power    Low power   Low power   checks
                                                    (Bug #3!)   (Bug #6)
                  │
          ┌───── ANY GATE FAILS ─────┐
          ▼                          ▼
   Forensic ECO Debugger      Three-fix rule
   (4-phase investigation)    (escalate to human)
```

## Agents (9 total)

| Agent | Role | Skill lines |
|-------|------|-------------|
| `synthesis-engineer` | RTL compilation, multi-corner optimization, QoR targeting | 641 |
| `floorplan-engineer` | Die sizing, macro placement, power grid, pin assignment | 366 |
| `placement-engineer` | Global/detailed placement, congestion, timing-driven opt | 302 |
| `cts-engineer` | Clock tree synthesis, skew balancing, useful skew | 314 |
| `routing-engineer` | Global/detail routing, DRC convergence, hold fixing | 308 |
| `signoff-engineer` | Multi-corner STA, DRC, LVS, IR drop, EM, GDSII | 378 |
| `lec-verifier` | Formal equivalence at every stage boundary | 515 |
| `low-power-verifier` | Cross-cutting UPF/CPF domain verification | 581 |
| `forensic-eco-debugger` | Reactive 4-phase investigation on any failure | 615 |

## Quick start

### Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) (`npm install -g @anthropic-ai/claude-code`)
- [Yosys](https://github.com/YosysHQ/yosys) (synthesis + LEC)
- [OpenSTA](https://github.com/The-OpenROAD-Project/OpenSTA) (timing analysis)
- [OpenROAD](https://github.com/The-OpenROAD-Project/OpenROAD) (floorplan through routing)
- Sky130 PDK liberty/LEF files

### Install

```bash
git clone https://github.com/RAMA-L7/vlsi-pd-staff-engineer.git
cd vlsi-pd-staff-engineer

# Get Sky130 library (if using Docker ORFS)
mkdir -p lib
docker cp $(docker create --rm openroad/orfs:latest):/OpenROAD-flow-scripts/flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib ./lib/

# Make hooks executable
chmod +x hooks/session-start hooks/run-hook.cmd
```

### Test the framework

```bash
cd examples/test-counter
chmod +x test_framework.sh
./test_framework.sh
```

This synthesizes a 4-bit counter, runs LEC, and verifies the hook system works. All tests should pass before using your real design.

### Use with Claude Code

```bash
cd vlsi-pd-staff-engineer
claude
```

Then tell it what to do:

```
> Run synthesis on examples/test-counter/rtl/test_counter.v
> Fix the timing violation on the critical path
> Run the full flow from RTL to signoff
```

The session-start hook auto-detects your tools, loads the employee handbook, and enforces the stage-gate discipline throughout.

## Sample run

Below is the output from the framework test on the included 4-bit counter design. The test validates project structure, tool detection, synthesis, LEC, and the hook system.

```
========================================
 VLSI PD Staff Engineer — Framework Test
========================================

--- TEST 1: Project structure ---
  ✓ PASS: CLAUDE.md exists
  ✓ PASS: synthesis_targets.json exists
  ✓ PASS: agents/synthesis-engineer.md exists
  ✓ PASS: agents/lec-verifier.md exists
  ✓ PASS: agents/low-power-verifier.md exists
  ✓ PASS: agents/forensic-eco-debugger.md exists
  ✓ PASS: agents/floorplan-engineer.md exists
  ✓ PASS: agents/placement-engineer.md exists
  ✓ PASS: agents/cts-engineer.md exists
  ✓ PASS: agents/routing-engineer.md exists
  ✓ PASS: agents/signoff-engineer.md exists
  ✓ PASS: skills/using-pd-staff-engineer/SKILL.md exists
  ✓ PASS: skills/synthesis/SKILL.md exists
  ... (all 25 structure checks pass)

--- TEST 3: Tool detection ---
  ✓ PASS: Yosys found: Yosys 0.56
  ✓ PASS: OpenROAD available via Docker (openroad/orfs:latest)
  ✓ PASS: Node.js v22.x (≥18, good for Claude Code)
  ✓ PASS: Claude Code found

--- TEST 4: Sky130 library ---
  ✓ PASS: Sky130 liberty found: ./lib/sky130_fd_sc_hd__tt_025C_1v80.lib

--- TEST 5: Yosys synthesis ---
  ✓ PASS: Yosys synthesis completed (exit 0)
  ✓ PASS: Synthesized to 28 cells
  ✓ PASS: Gate-level netlist written

--- TEST 6: Yosys LEC ---
  ✓ PASS: LEC passed — RTL ≡ synthesized netlist

--- TEST 8: Session-start hook ---
  ✓ PASS: Session-start hook ran successfully
  ✓ PASS: Hook detected Yosys
  ✓ PASS: Hook loaded the employee handbook

========================================
 RESULTS: 35 PASS, 0 FAIL, 2 SKIP
========================================
```

## Project structure

```
vlsi-pd-staff-engineer/
├── CLAUDE.md                              # Claude Code reads this on entry
├── synthesis_targets.json                 # QoR targets + corner definitions
├── agents/                                # agent definitions (9 agents)
│   ├── synthesis-engineer.md
│   ├── floorplan-engineer.md
│   ├── placement-engineer.md
│   ├── cts-engineer.md
│   ├── routing-engineer.md
│   ├── signoff-engineer.md
│   ├── lec-verifier.md
│   ├── low-power-verifier.md
│   └── forensic-eco-debugger.md
├── skills/                                # process library (10 skills, 4,232 lines)
│   ├── using-pd-staff-engineer/SKILL.md   # employee handbook (loaded every session)
│   ├── synthesis/                         # optimization strategies + QoR reviewer
│   ├── floorplan/                         # macro placement, PG, pins
│   ├── placement/                         # congestion, timing-driven
│   ├── cts/                               # skew, buffers, Bug #3 territory
│   ├── routing/                           # DRC convergence, hold fixing
│   ├── signoff/                           # MMMC STA, tapeout checklist
│   ├── lec/                               # 9-checkpoint protocol + reviewer
│   ├── low-power/                         # cross-cutting domain checks
│   └── forensic-debugging/                # 4-phase ECO investigation
├── hooks/                                 # lifecycle hooks
│   ├── hooks.json                         # session-start trigger config
│   ├── run-hook.cmd                       # cross-platform runner
│   └── session-start                      # env detection + handbook injection
├── examples/
│   └── test-counter/                      # 4-bit counter test design
│       ├── rtl/test_counter.v
│       ├── rtl/test_counter.sdc
│       └── test_framework.sh              # validation script
└── docs/                                  # additional documentation
```

## Key concepts

### Stage gates

Every PD stage must pass three checks before the next stage starts. The checks are different at each stage — for example, post-CTS low power verification checks every inserted clock buffer's power domain (Bug #3), while post-route verification checks hold buffers (Bug #6).

### The three-fix rule

If 3 targeted ECOs on the same failure show diminishing returns, the problem is structural and upstream. The forensic debugger stops, runs a five-level root cause trace, and presents options to the human with cost/risk assessment.

### The forensic debugging agent

A reactive agent dispatched when any gate fails. It follows a rigid four-phase protocol: evidence collection, root cause identification (with a five-level backward trace through the PD stages), targeted fix selection, and verified resolution. It does not guess. It does not apply random fixes.

### The employee handbook

Loaded at every session start via the hook system. Establishes the 1% rule (if a skill might apply, you must use it), instruction priority (human > skills > defaults), rigid vs flexible skill categories, and the complete stage-gate flow definition.

## Customization

### For your own design

1. Replace `synthesis_targets.json` with your design's clock period, area budget, and library paths
2. Put your RTL in `rtl/` and SDC in the same directory
3. If you have UPF/CPF files, put them alongside the RTL — the low power agent activates automatically
4. Launch Claude Code from the project root

### For a different PDK

The skills reference Sky130 cell names and OpenROAD commands. For a different PDK, update the buffer lists in the CTS skill, the site name in the floorplan skill, and the library paths in `synthesis_targets.json`. The verification skills (LEC, low power, forensic debugging) are PDK-agnostic.

## Credits

This project adapts the [Senior Staff Engineer with Sub-Agent Teams](https://github.com/FareedKhan-dev/claude-code-staff-engineer) pattern by [Fareed Khan](https://github.com/FareedKhan-dev) to VLSI physical design. The original article describes how to build a disciplined multi-agent engineering team in Claude Code — this repo applies that same structure to the RTL-to-GDSII flow.

## License

MIT
