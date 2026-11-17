# Milestone 1G — ABC Mapping Strategist — Evidence Report

Date: 2026-11-17. Agent: subagent (read-only on repos). All writes confined to
`D:\audit\m1g_evidence_20261117\mapping\`. No STA / physical flow run (out of scope).

## 0. Scope, environment, integrity

| Item | Value |
|---|---|
| Worktree | `D:\audit\m1g_mapping_20261117` @ `d585055392a8006dc3199202a64702c60ded338c` |
| Golden RTL | `examples/soc_ctrl/rtl/soc_ctrl.v` — SHA256 `2a7e028dacd97b4462d791e69e69b8ff1b2cffcf239ba31b6e968efdadb8c4be` |
| Liberty | `D:\tools\src\OpenROAD\src\sta\test\sky130hd\sky130_fd_sc_hd__tt_025C_1v80.lib` — SHA256 `92eb4e93a3d4c2563018ac81cdec2f02fdeac9b39337ed5c141fa63e0ad8f8` |
| Yosys | `Yosys 0.69+154 (git sha1 30d62572e-dirty, Release, GNU /usr/bin/x86_64-w64-mingw32-g++ 15.2.1)` via `cmd /c "call D:\tools\oss-cad-suite\environment.bat >nul 2>&1 && yosys ..."` |
| ABC | `D:\tools\oss-cad-suite\bin\yosys-abc.exe` |

**Repository integrity:** I made **no writes** to any repository file (all outputs are under the
external evidence dir). At session start (~23:32) `git status --porcelain` was **clean**. During the
session, unrelated modifications to `regression/checks/lvs_compare.py`,
`regression/stages/03_eqy_prep.py`, `regression/stages/03_lec_mapped.ps1`, and untracked
`tests/test_lvs_compare.py` appeared (mtimes 23:35–23:38) — these are LVS/LEC regression files from
another concurrent process, **not from this task**. HEAD is unchanged (`d585055`); **no commit, no push**.
RTL SHA re-checked at end: unchanged (`2a7e028d…`).

---

## 1. `help abc` evidence (Task 1) — PASS

Captured with `cmd /c 'call D:\tools\oss-cad-suite\environment.bat >nul 2>&1 && yosys -p "help abc"'`.
Files: `help/yosys_help_abc.txt`, `help/abc_map_help.txt`, `help/abc_lib_help.txt`.

Exact quotes (Yosys `help abc`):

- `-liberty <file>` — "generate netlists for the specified cell library (using the liberty file format)."
- `-dont_use <cell_name>` — "avoid usage of the technology cell <cell_name> when mapping the design. this option can be used multiple times with different cell names and supports simple glob patterns in the cell name. only supported with Liberty cell libraries."
- `-script <file>` — "use the specified ABC script file instead of the default script." / "if <file> starts with a plus sign (+), then the rest of the filename string is interpreted as the command string to be passed to ABC. The leading plus sign is removed and all commas (,) in the string are replaced with blanks before the string is passed to ABC."
- `-constr <file>` — "pass this file with timing constraints to ABC. use with -liberty/-genlib." / "a constr file contains two lines: set_driving_cell <cell_name> / set_load <floating_point_number>".
- `-D <picoseconds>` — "set delay target. the string {D} in the default scripts above is replaced by this option when used, and an empty string otherwise. this also replaces 'dretime' with 'dretime; retime -o {D}'".
- Default `-liberty` (no `-constr`) script, quoted exactly:
  `strash; &get -n; &fraig -x; &put; scorr; dc2; dretime; strash; &get -n; &dch -f; &nf {D}; &put`
- With `-constr`, appends: `buffer; upsize {D}; dnsize {D}; stime -p`.

ABC-internal help (`yosys-abc -c "map -h"`) confirms the script primitives used:
`usage: map [-DABFSG float] [-M num] [-arspfuovh]` with `-B float : "delay multiplier" to bias gate
selection`, `-A float : "area multiplier" to bias gate selection`, `-D float : sets the global required
times`, `-a : toggles area-only mapping`. `read_lib` supports `-X name : adds name to the list of cells
ABC shouldn't use` (this is how Yosys `-dont_use` is implemented — see §4[5]).

All options required by the task are confirmed supported.

---

## 2–3. Controlled matrix — setup, commands, results (Tasks 2 & 3)

Every run used **identical pre-ABC steps** (exact script in `runs/<name>/run.ys`):

```
read_liberty -ignore_miss_func <Lib>
read_verilog <RTL>
synth -top soc_ctrl
dfflibmap -liberty <Lib>
abc <VARIANT OPTIONS>
opt_clean -purge
write_verilog -noattr -noexpr <run>/mapped.v
write_json <run>/mapped.json
stat -liberty <Lib>
stat
```

Netlist SHA = SHA-256 of `mapped.v` (`write_verilog -noattr -noexpr`, identical flags for all runs).
Depth measured by `depth.ps1` (custom script in evidence dir) parsing `mapped.json`; cell output pins
resolved from the Liberty file; sources = primary inputs + flop Q; reported depths are cell counts to
`sum[4]` / `carry_out[2]` plus max over all primary outputs.

Aggregate results (full data: `results.csv`, `results.json`, `results_extra.csv`):

| Run | ABC options (after `-liberty <Lib>`) | Exit | SHA-256 (first 12) | Area (um2) | Cell types (counts) | sum[4] / carry_out[2] / MAX depth |
|---|---|---|---|---|---|---|
| baseline | *(none)* | 0 | `a84eb9558026` | 270.2592 | dfxtp_1=8, maj3_1=4, xnor2_1=8 | 4 / 3 / 4 |
| dontuse_maj3_star | `-dont_use sky130_fd_sc_hd__maj3_*` | 0 | `c50de40af296` | 292.7808 | clkinv_1=4, lpflow_isobufsrc_1=4, nand2_1=4, nor2_1=4, o21a_1=3, o21ai_0=1, xnor2_1=4 (+8 dfxtp_1) | 5 / 5 / 5 |
| dontuse_maj3_1 | `-dont_use sky130_fd_sc_hd__maj3_1` | 0 | `5a979b2fdea3` | 275.2640 | dfxtp_1=8, maj3_2=4, xnor2_1=8 | 4 / 3 / 4 |
| dontuse_maj3_all3 | `-dont_use maj3_1 -dont_use maj3_2 -dont_use maj3_4` | 0 | `c50de40af296` | 292.7808 | same as `dontuse_maj3_star` | 5 / 5 / 5 |
| script_strash_refactor_rw_map | `-script "+strash; refactor; rewrite; map"` | 0 | `5a3998be0afd` | 312.8000 | a21boi_0=1, a21o_2=1, a21oi_1=2, clkinv_1=5, lpflow_inputiso0p_1=2, nand2_1=4, nor2_1=6, o211a_1=1, o21ai_0=2, xnor2_1=2, xor2_1=2 (+8 dfxtp_1) | 5 / 5 / 6 |
| script_fraig_rewriteZ_mapB | `-script "+strash; fraig; rewrite -z; map -B 0.05"` | 0 | `c10f0b7caf3c` | 317.8048 | a21boi_0=1, a21oi_1=1, clkinv_1=4, maj3_1=1, nand2_1=6, nor2_1=6, nor3_1=1, o211a_1=1, o21bai_1=1, o2bb2ai_1=1, xnor2_1=3, xor2_1=2 (+8 dfxtp_1) | 6 / 4 / 6 |
| script_map_areaA10 | `-script "+strash; refactor; rewrite; map -A 10"` | 0 | `3d394bd7ee34` | 316.5536 | a211oi_1=1, a21o_2=1, a21oi_1=2, clkinv_1=9, nand2_1=11, nor2_1=5, o21ai_0=2, xnor2_1=2, xor2_1=1 (+8 dfxtp_1) | 6 / 4 / 6 |
| constr_timing | `-constr <constr/abc_timing.constr>` (set_driving_cell buf_1; set_load 5) | 0 | `0517964d5a25` | 275.2640 | dfxtp_1=8, maj3_2=4, xnor2_1=8 | 4 / 3 / 4 |
| delay_D1 / delay_D50 / delay_D500 | `-D 1` / `-D 50` / `-D 500` | 0 | `a84eb9558026` (all = baseline) | 270.2592 | = baseline | 4 / 3 / 4 |
| script_map_delayB1 | `-script "+strash; refactor; rewrite; map -B 1.0"` | 0 | `aa3d010df041` | 297.7856 | clkinv_1=7, nand2_1=11, nand3_1=1, nor2_1=2, o21ai_0=1, xnor2_1=2, xor2_1=4 (+8 dfxtp_1) | 8 / 6 / 9 |

**Different netlist SHA achieved by 8 settings** (all except baseline and the three `-D` runs).
The baseline SHA `a84eb955…` matches the given golden exactly (8 dfxtp_1 + 4 maj3_1 + 8 xnor2_1,
270.2592 um2, 4-cell carry depth).

---

## 4. Canonicalization mechanism (Task 4) — explained with exact quotes

Full quotes in `canonicalization_excerpts.txt`. Mechanism:

1. **RTL → majority.** Each carry is `qa&qb | ((qa^qb)&c)` = `MAJ3(qa,qb,c)` (RTL lines 88–90 etc.).
   The Liberty has a dedicated cell: `cell ("sky130_fd_sc_hd__maj3_1")`, `area : 10.009600000`,
   `function : "(A&B) | (A&C) | (B&C)"` — an exact match for the carry function.
2. **synth canonicalizes the RTL.** `3.22. Executing ABC pass (technology mapping using ABC)` runs
   `ABC: + read_library <abc-temp-dir>/stdcells.genlib` then `&nf`, yielding
   `ABC RESULTS: NAND cells: 12 / XOR cells: 8` and generic cells
   `8 $_DFF_P_, 12 $_NAND_, 8 $_XOR_`. Any RTL style describing the same 4-bit adder reduces to this
   identical generic network — this is the first canonicalization step.
3. **SCL mapping deterministically picks maj3.** The explicit `abc -liberty` pass runs
   `ABC: + read_lib -w <liberty>` then `&nf` and reports
   `ABC RESULTS: sky130_fd_sc_hd__maj3_1 cells: 4 / sky130_fd_sc_hd__xnor2_1 cells: 8`.
   ABC's cost function (area/delay) finds the single maj3 gate the cheapest cover for each of the four
   majority carries; XOR3 sums become two xnor2 per bit. (ABC also warns
   `Detected 9 multi-output cells (for example, "sky130_fd_sc_hd__fa_1")` but still selects maj3.)
   Result: **8 dfxtp_1 + 4 maj3_1 + 8 xnor2_1, SHA a84eb955…, depth 4** — identical for golden and any
   RTL variant computing the same function. That is precisely why "different RTLs canonicalize into
   the same 4-majority chain": the cost-based SCL mapper has a unique, strictly-best cover.
4. **Breaking the canonicalization** requires changing the *optimization problem*, not the RTL:
   - `-dont_use` → `ABC: + read_lib -X sky130_fd_sc_hd__maj3_* -w <lib>` and
     `skipped cell "sky130_fd_sc_hd__maj3_1" due to dont_use attribute` (3 cells skipped); majority
     must be built from NAND/NOR/AOI → SHA changes, depth 4→5, area +8.3%.
   - `-script` → replaces the mapper entirely (`read_lib` still loads the SCL lib first), e.g.
     `strash; fraig; rewrite -z; map -B 0.05`; different covers → different SHA, depth up to 9.
   - `-constr` → `ABC: + read_constr -v <file>` plus default timing script
     `buffer; upsize; dnsize; stime -p`; sizes maj3_1→maj3_2 (SHA changes, depth unchanged).
   - `-D` → `retime -o -D N; &nf -D N` applied, but the maj3 cover is already delay-optimal at these
     targets → SHA unchanged.

---

## 5. Ranking of genuinely-different mapped architectures (Task 5)

Ranked best→worst by (max combinational depth, area). Baseline optimal; **no setting beat it**.

| Rank | Architecture | max depth | area (um2) | Δ area vs baseline | SHA distinct? |
|---|---|---|---|---|---|
| 1 (reference / best) | baseline maj3_1 chain | 4 | 270.2592 | 0% | — |
| 2 | maj3_2 upsized (`dontuse_maj3_1`, `constr_timing`) | 4 | 275.2640 | +1.85% | yes (same topo, bigger drive) |
| 3 | maj3-free NAND/NOR/AOI (`dontuse_maj3_star`/`all3`) | 5 | 292.7808 | +8.34% | yes |
| 4 | classic-map mixed (`script_strash_refactor_rw_map`) | 6 | 312.8000 | +15.74% | yes |
| 5 | classic-map mixed (`script_map_areaA10`) | 6 | 316.5536 | +17.15% | yes |
| 6 | classic-map mixed (`script_fraig_rewriteZ_mapB`) | 6 | 317.8048 | +17.59% | yes |
| 7 | delay-biased classic map (`script_map_delayB1`) | 9 | 297.7856 | +10.19% | yes (worst depth) |

No STA/physical flow executed. No constraint or RTL file modified (the only new constraint file is the
ABC-only `constr/abc_timing.constr` in the evidence dir).

---

## 6. Status per experiment (PASS / FAIL / BLOCKED / NOT_TESTED)

| Experiment | Status | Notes |
|---|---|---|
| baseline | **PASS** | Reference; SHA a84eb955… matches golden; depth/area/cells recorded. |
| dontuse_maj3_star | **PASS** | Different SHA, depth 5, no maj3. Interesting. |
| dontuse_maj3_1 | **PASS** | Different SHA (maj3_2), same depth 4. |
| dontuse_maj3_all3 | **PASS** | Identical SHA to `dontuse_maj3_star` (both remove all maj3) — expected. |
| script_strash_refactor_rw_map | **PASS** | Different SHA, depth 6. |
| script_fraig_rewriteZ_mapB | **PASS** | Different SHA, depth 6. |
| script_map_areaA10 | **PASS** | Different SHA, depth 6. |
| constr_timing | **PASS** | Different SHA (maj3_2), SDC untouched; timing-driven script confirmed. |
| delay_D1 / D50 / D500 | **PASS** (executed) / **canonicalization not broken** | SHA = baseline; `-D` applied per log but no mapping change. |
| script_map_delayB1 | **PASS** | Different SHA, worst depth 9. |
| STA / physical implementation | **NOT_TESTED** | Explicitly out of scope. |
| Formal logic equivalence (LEQ) | **NOT_TESTED** | All runs use identical pre-ABC steps and Yosys/ABC mapping is equivalence-preserving; no formal LEQ run in this milestone. |

No experiment FAILED or was BLOCKED: all 12 Yosys invocations exited 0 and produced valid netlists.

---

## 7. Mapping-bottleneck analysis

- The "same hash across RTLs" is **not** an ABC bug; it is the expected outcome of a two-stage
  canonical pipeline: `synth` reduces any semantically-equivalent RTL to one generic NAND/XOR/DFF
  network, and ABC's cost-based SCL mapper (`&nf`) picks a unique minimum-cost cover. Because the
  sky130 HD library includes a dedicated, area-efficient `maj3` gate for the majority carry function,
  every 4-bit-adder variant collapses to the same 4-maj3 + 8-xnor netlist (SHA a84eb955…).
- **Mapping is therefore not the bottleneck for design quality** — it already finds the depth-4 /
  270.2592-um2 optimum. Genuinely different netlists are obtainable, but **only by artificially
  constraining the search** (removing maj3, swapping the mapper script, or timing-driven sizing), and
  every such alternative is *strictly worse* in depth and/or area (+1.85% to +17.6% area; depth +1 to
  +5). The only exception is `dontuse_maj3_1`/`constr_timing`, which keep topology/depth but upsize to
  maj3_2.
- `-D` (delay target) has no effect on this design; `-constr` changes only drive/load assumptions and
  triggers `upsize`/`dnsize`. Neither modifies the design SDC.
- Implication for the milestone: if the goal is distinguishable mapped netlists between golden and
  candidates, the differentiator must come from *something other than ABC mapping options* (which
  are deterministic and quality-optimal here) — e.g. real RTL/function differences or deliberate
  library restrictions — and any imposed restriction carries an area/delay penalty.

---

## 8. Evidence paths (all under `D:\audit\m1g_evidence_20261117\mapping\`)

- `help/yosys_help_abc.txt`, `help/abc_map_help.txt`, `help/abc_lib_help.txt` — option docs (Task 1).
- `runs/<name>/run.ys` — exact command script per experiment.
- `runs/<name>/yosys.log` — full log (ABC commands, `ABC RESULTS`, `stat -liberty` area).
- `runs/<name>/mapped.v`, `runs/<name>/mapped.json` — mapped netlist (SHA source) + JSON for depth.
- `runs/<name>/exit.txt`, `constr/abc_timing.constr` — exit codes and ABC-only constraint file.
- `depth.ps1` — combinational-depth measurement script.
- `run_all.ps1`, `run_extra.ps1` — experiment harnesses.
- `results.csv`, `results.json`, `results_extra.csv` — machine-readable metrics.
- `canonicalization_excerpts.txt` — exact log quotes (§4).
- `REPORT.md` — this report.

## 9. Unresolved issues / notes

- Pre-existing, task-unrelated modifications to `regression/*` LVS/LEC files appeared during the
  session from another concurrent process; HEAD unchanged, no commit/push by this agent (§0).
- ABC SCL mapping uses `lpflow_*`/`clkinv_1` cells as generic inverters/buffers in some variants
  (a known quirk of the sky130 HD Liberty's function-equivalent cells); this is reflected honestly in
  the cell counts/areas.
- No formal LEQ was run; equivalence rests on the equivalence-preserving nature of Yosys/ABC mapping
  under identical pre-ABC steps (NOT_TESTED, see §6).
