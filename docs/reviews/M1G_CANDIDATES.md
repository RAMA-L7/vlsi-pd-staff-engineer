# Milestone 1G — Alternative Carry Engineer — Evidence Report

Date: 2026-11-17. All writes confined to `D:\audit\m1g_evidence_20261117\rtl\`.
No commit / push / PR. No production RTL or SDC change. No STA / physical flow.

## 0. Scope and integrity

| Item | Value |
|---|---|
| Worktree | `D:\audit\m1g_mapping_20261117` @ `d585055392a8006dc3199202a64702c60ded338c` |
| Golden RTL | `examples/soc_ctrl/rtl/soc_ctrl.v` SHA256 `2a7e028dacd97b4462d791e69e69b8ff1b2cffcf239ba31b6e968efdadb8c4be` (unchanged) |
| Golden SDC | `examples/soc_ctrl/rtl/soc_ctrl.sdc` SHA256 `640071510528b53e4433e9e16f2196b624f047bf35b1979897aa23623a4f826d` (unchanged, 10 ns / 2 ns) |
| Liberty | `D:\tools\src\OpenROAD\src\sta\test\sky130hd\sky130_fd_sc_hd__tt_025C_1v80.lib` SHA256 `92eb4e93a3d4c2563018ac81cdec2f02fdeaced9b39337ed5c141fa63e0ad8f8` (Sky130 HD TT) |
| Yosys | `0.69+154 (git sha1 30d62572e-dirty, Release, ...)` |
| Icarus | `iverilog` / `vvp` from oss-cad-suite |
| Golden mapped netlist | reproduced from worktree RTL with the M1F recipe → SHA256 `a84eb9558026703f9047fe020ded6948fceeccb7a34f097abf55f461b2d26da9` (exact match to golden reference) |

Concurrent-worktree note: `git status --short` showed pre-existing/unrelated modifications
(`regression/checks/lvs_compare.py`, `regression/stages/03_eqy_prep.py`,
`regression/stages/03_lec_mapped.ps1`, untracked `tests/test_eqy_override.py`,
`tests/test_lvs_compare.py`, mtimes 23:35–23:40) from another process. This agent wrote
**only** under the evidence dir. HEAD unchanged; no commit/push. Independently corroborated
by the mapping agent's report (`mapping/REPORT.md` §0).

## 1. Mapping lever (justification)

At session start `mapping/` was empty, so the task-prescribed fallback was used. The mapping
agent's findings later appeared (`mapping/REPORT.md`, §2–5) and confirm the choice:
`-dont_use sky130_fd_sc_hd__maj3_*` breaks the canonical maj3 ripple
(`ABC: read_lib -X sky130_fd_sc_hd__maj3_* ...`, `skipped cell ... due to dont_use`),
changing the mapped SHA from `a84eb955…` to `c50de40a…`, depth 4→5, area +8.3%.
No `-D` value changes the golden mapping. Both candidates therefore add
`-dont_use sky130_fd_sc_hd__maj3_*` (plus `-dont_use lpflow_*` from the golden recipe).

Synthesis recipe (exact scripts `cand_a_prefix/synth_cand_a.ys`, `cand_b_andor/synth_cand_b.ys`):

```
read_liberty -ignore_miss_func <Lib>
read_verilog <candidate RTL>
synth -top soc_ctrl
dfflibmap -liberty <Lib>
abc -liberty <Lib> -dont_use sky130_fd_sc_hd__lpflow_* -dont_use sky130_fd_sc_hd__maj3_* ...
opt_clean -purge
[flatten / attribute cleanup]
stat -liberty <Lib>
write_verilog -noattr -noexpr <mapped.v>
```

## 2. Candidates

### Candidate A — `cand_a_prefix/` — structural Sky130 HD prefix/mux carry (PASS)
- RTL: `cand_a_prefix/soc_ctrl.v`, SHA256 `6117e9c69746929b31eed8d9cc0168eea93429d14c2c2f89af87c30fefcccd0b`
- Architecture: 8×`dfxtp_1` input regs; bit P/G via `xor2_1`/`and2_1`; Kogge-Stone style group
  G/P via `a21o_1`/`and2_1`; carries via genuine `mux2_1` (`C=P?Cin:G`); sums via `xor3_1`.
  Every cell is explicitly instantiated in RTL, so the topology survives ABC mapping.
- Mapped netlist: `cand_a_prefix/cand_a_mapped.v`, SHA256 `236594c3f44ccbc183cef15667b72d1170dedf2042a50ae70d923524cf36351b`
  **≠ a84eb955…** (genuinely different).
- Cells: 32 total = `dfxtp_1`×8, `a21o_1`×4, `and2_1`×8, `mux2_1`×4, `xor2_1`×4, `xor3_1`×4.
- Area (Yosys `stat -liberty`, TT): **415.398400 µm²**. Max comb depth **5**
  (sum[3]=5, sum[4]=4, carry_out[2]=4). `analyze_depth.py` output in `synth_cand_a.log`.
- Yosys `check`: `Found and reported 0 problems` (`check_cand_a.log`).
- Exhaustive sim: 512/512 cases, 0 mismatches vs golden **and** independent arithmetic
  reference (`sim/sim_cand_a.log`, `TOTAL_CASES=512 MISMATCHES=0 SIM_RESULT=PASS`).
- Mapped LEC vs golden: **PASS** — `Of those cells 16 are proven and 0 are unproven.` /
  `Equivalence successfully proven!`, exit 0 (`lec/lec_gold_vs_cand_a.log`).

### Candidate B — `cand_b_andor/` — AND/OR/XOR-only SOP carry-lookahead (PASS)
- RTL: `cand_b_andor/soc_ctrl.v`, SHA256 `c16a81a42052ae2d1ac7fa02c759bf43cf98dcad71f9edb11e7f1d27ab95ca38`
- Architecture: hierarchical `pg_bit` + `cla_sop4` with `keep_hierarchy` attributes; only
  plain `& | ^` operators (classic SOP lookahead `C4=G3|P3G2|P3P2G1|P3P2P1G0|P3P2P1P0Cin`);
  no `+`, no explicit cells. Mapped with `maj3_*`, `fa_*`, `xor3_*` disabled.
- Mapped netlist (flat): `cand_b_andor/cand_b_mapped.v`, SHA256 `f2cabe8a6a081740856725944c6562b2b5d0190b39d6bc66cb97ada222c4fc04`
  **≠ a84eb955…** and ≠ candidate A.
- Cells: 24 total = `dfxtp_1`×8, `and2_0`×4, `xor2_1`×8, `a21o_1`×4.
- Area: **285.273600 µm²**. Max comb depth **5**
  (sum[3]=5, sum[4]=5, carry_out[2]=4).
- Yosys `check`: `Found and reported 0 problems` (`check_cand_b.log`).
- Exhaustive sim: 512/512, 0 mismatches (`sim/sim_cand_b.log`).
- Mapped LEC vs golden: **PASS** — 16/16 proven, exit 0 (`lec/lec_gold_vs_cand_b.log`).
  Note: gate netlist copy `lec/cand_b_mapped_lec.v` differs from `cand_b_mapped.v` only in
  cosmetic instance names (`_NN_`→`cb_NN_`) to avoid a spurious non-output name collision
  created by `prep` (`_13_` existed in both netlists but denoted different internal nodes).
  Ports, cells, connectivity and logic are unchanged; no equivalence point was suppressed.

## 3. Negative control (rejects — required behaviour)

- Faulted netlist: `lec/negative_ctrl_mapped.v` SHA256 `2af2c2879957029e1029373fd1e18b2a651a16d985016b45818787a874be9dd4`
  (candidate B with the sum[0] driver changed `xor2_1`→`xnor2_1`, inverting sum[0]).
  LEC copy: `lec/negative_ctrl_mapped_lec.v` SHA256 `e5942e22004f316b21a5d1ff381623aa55e10a5d37e63a9a38e17363586e6f04`.
- Script `lec/lec_negative_control.ys`, log `lec/lec_negative_control.log`.
- Result: **1 unproven point** — `\sum_gold [0] \sum_gate [0]` — `Of those cells 15 are proven
  and 1 are unproven.` / `ERROR: Found 1 unproven $equiv cells in 'equiv_status -assert'.`
  Exit **1**. Correctly rejected → proofs are sensitive, not vacuous.

## 4. LEC harness / no false PASS

Genuine harness (all three final runs): `read_liberty -ignore_miss_func <Lib>` → read netlist →
`prep -top soc_ctrl` → `flatten` → `rename soc_ctrl gold|gate` → `design -stash` →
`design -copy-from gold -as gold gold` / `... gate gate` → 3-arg `equiv_make gold gate equiv` →
`hierarchy -top equiv` → `equiv_simple` → `equiv_induct` → `equiv_status -assert`.
Logs contain `Grouping SAT models for \sum / \carry_out / \qa / \qb` and contain **no**
`Setting undriven nets to undef`, **no** `No SAT model`, **no** black boxes, no Nangate.
A discarded experimental harness (`*_noprep.ys`) produced a **false PASS** (undriven nets
set to undef, both sides X) and is explicitly excluded — its logs are retained only as
negative evidence of that pitfall.

## 5. Ranked results (mapped depth / area)

| Rank | Design | Mapped netlist SHA256 (first 12) | Cells | Area (µm²) | Max depth | Δ area vs golden | Different from golden? |
|---:|---|---|---:|---:|---:|---:|---|
| 1 (reference/best) | Golden | `a84eb9558026` | 20 | 270.259200 | 4 | — | — |
| 2 | Candidate B (AND/OR/XOR SOP, maj3/fa/xor3 disabled) | `f2cabe8a6a08` | 24 | 285.273600 | 5 | +5.56% | **YES** |
| 3 | Candidate A (structural prefix/mux cells) | `236594c3f44c` | 32 | 415.398400 | 5 | +53.72% | **YES** |

Both candidates are genuinely different mapped netlists; neither improves depth (4→5) or
area. Consistent with the mapping agent's finding that ABC's maj3 cover is already the
depth/area optimum and every deliberate restriction is strictly worse.

## 6. Status summary

| Item | Status |
|---|---|
| Candidate A — structurally different RTL, survives mapping | PASS |
| Candidate A — exhaustive 512-case sim vs golden | PASS |
| Candidate A — Yosys `check` 0 problems | PASS |
| Candidate A — mapped netlist SHA differs from golden | PASS |
| Candidate A — mapped LEC vs golden (16/16, exit 0) | PASS |
| Candidate B — structurally different RTL, survives mapping | PASS |
| Candidate B — exhaustive 512-case sim vs golden | PASS |
| Candidate B — Yosys `check` 0 problems | PASS |
| Candidate B — mapped netlist SHA differs from golden | PASS |
| Candidate B — mapped LEC vs golden (16/16, exit 0) | PASS |
| Negative control (faulted sum[0]) rejected, exit 1 | PASS |
| Depth/area measured and ranked | PASS |
| No false PASS (no black boxes/undef/X in final LEC) | PASS |
| Golden RTL/SDC unchanged; HEAD unchanged; no commit/push | PASS |
| STA / physical implementation | NOT_TESTED (out of scope) |
| Mapped depth/area improvement vs golden | FAIL (both candidates worse: depth 5, area +5.6% / +53.7%) |

## 7. Unresolved issues

1. Neither candidate beats golden on mapped depth or area; both are strictly worse. This is
   a measured result, not a tooling failure, and matches `mapping/REPORT.md` §5/§7.
2. Unrelated concurrent modifications to `regression/*` and two untracked test files exist in
   the shared worktree (23:35–23:40); not made by this agent, no commit/push. Golden files
   and HEAD verified unchanged.
3. Candidate B's LEC required cosmetic internal instance renaming to avoid a spurious
   `prep`-generated name collision; no output/point was blacklisted or suppressed.
4. `-dont_use` lever is a mapping restriction (area penalty); it does not improve timing.
   No timing closure claim is made (no STA run).

## 8. Evidence index (all under `rtl/`)

- `baseline/synth_golden_repro.ys|.log`, `baseline/golden_mapped_repro.v` (SHA a84eb955…)
- `cand_a_prefix/soc_ctrl.v`, `check_cand_a.ys|.log`, `synth_cand_a.ys|.log`, `cand_a_mapped.v`
- `cand_b_andor/soc_ctrl.v`, `check_cand_b.ys|.log`, `synth_cand_b.ys|.log`, `cand_b_mapped.v`
- `sim/tb_compare.v`, `sim/sky130_cells_sim.v`, `sim/soc_ctrl_golden.v`,
  `sim/soc_ctrl_cand_a.v`, `sim/soc_ctrl_cand_b.v`, `sim/sim_cand_a.log`, `sim/sim_cand_b.log`
- `lec/lec_gold_vs_cand_a.ys|.log`, `lec/lec_gold_vs_cand_b.ys|.log`,
  `lec/lec_negative_control.ys|.log`, `lec/negative_ctrl_mapped.v`,
  `lec/cand_b_mapped_lec.v`, `lec/negative_ctrl_mapped_lec.v`,
  `lec/*_noprep.ys|.log` (discarded false-pass harness, retained as negative evidence)
- `analyze_depth.py` (cell counts + combinational depth parser)
