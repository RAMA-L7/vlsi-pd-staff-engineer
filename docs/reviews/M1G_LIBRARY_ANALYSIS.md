# M1G — Library Engineer report: Sky130 HD cells for a faster 4-bit adder carry topology

Date: 2026-11-17. All measurements are from the real public Sky130 HD Liberty files in
`D:\tools\src\OpenROAD\src\sta\test\sky130hd\`, opened **read-only**. All files written by this
agent are confined to `D:\audit\m1g_evidence_20261117\library\`.

Per-item status: **1 PASS · 2 PASS · 3 PASS · 4 PASS (limitation documented) · 5 PASS**.

---

## 0. Inputs and method

| Corner | File | SHA-256 |
|---|---|---|
| TT | `sky130_fd_sc_hd__tt_025C_1v80.lib` | `92EB4E93A3D4C2563018AC81CD...0AD8F8` (matches supplied `92eb4e93…`) |
| SS | `sky130_fd_sc_hd__ss_n40C_1v40.lib` | `BBC8D07174EF50A2734607DF0EA0D1C...FF46` |
| FF | `sky130_fd_sc_hd__ff_n40C_1v95.lib` | `3924C861014075FD5BCD9807317A895...9336` |

`sky130hd_tt.lib` in the same directory is a Windows junction to the TT file, not a separate library.

Two independent measurement methods were used, both grounded in the real Liberty:

1. **Table extraction (item 1):** `parse_liberty.py` parses each cell's `area`, output
   `function`, input-pin `capacitance`, and bilinear-interpolates `cell_rise`/`cell_fall` from
   the 2-D timing tables at a **defined nominal point: input transition = 0.100 ns, output
   load = 0.010 pF**. Liberty units: `time_unit = 1ns`, `capacitive_load_unit = 1pf`.
   The reported per-pin delay is the max over all rise/fall arcs for that pin at that point.
   Output: `lib_characterization.json`, `lib_characterization.md`.
2. **Tool flow (items 2–3):** Yosys 0.69 + ABC (`yosys-abc`) mapped a 4-bit adder with an
   explicit majority carry expression to the real Liberty; OpenSTA (run under WSL Ubuntu
   with `LD_LIBRARY_PATH=/mnt/d/tools/opensta/lib`) performed SS STA on the resulting and on
   hand-built structural netlists with a virtual 100 ns clock and zero I/O delay. The longest
   path's **data arrival time** is the reported delay. These delays use real fanout loads
   (not the fixed nominal 0.010 pF), so they differ from method 1 — both are reported.

---

## ITEM 1 — Cell inventory (function, area, input cap, delay) : **PASS**

Full 428-cell inventory saved to `all_cell_names.txt`; 87 relevant variants characterized.

### 1a. Naming finding (important)
There are **no cells literally named `ao21*` or `oa21*`** in this Liberty. The Sky130 HD
functional equivalents exist under these names (all quoted verbatim from the Liberty):

| Wanted | Actual family in this Liberty | Example output `function` |
|---|---|---|
| AO21 | `sky130_fd_sc_hd__a21o_1/2/4` | `"(A1&A2) \| (B1)"` |
| AOI21 | `sky130_fd_sc_hd__a21oi_0/1/2/4` | `"(!A1&!B1) \| (!A2&!B1)"` (for `_1`) |
| OA21 | `sky130_fd_sc_hd__o21a_1/2/4` | `"(A1&B1) \| (A2&B1)"` |
| OAI21 | `sky130_fd_sc_hd__o21ai_0/1/2/4` | OAI function |
| AOI22 / OAI22 | `a22oi_*`, `o22ai_*` | — |

Exact Liberty quote for `sky130_fd_sc_hd__maj3_1` (line 83102):
`area : 10.009600000;` · `cell_footprint : "sky130_fd_sc_hd__maj3";` ·
output `X` `function : "(A&B) | (A&C) | (B&C)";`

### 1b. Key characterized cells (SS corner; delay at nominal 0.100 ns / 0.010 pF)

| Cell | Area | Liberty output `function` | Input caps (pF) | SS pin delays (ns) | SS max |
|---|---:|---|---|---|---:|
| `maj3_1` | 10.0096 | `(A&B) \| (A&C) \| (B&C)` | A .00237 B .00224 C .00273 | A 1.8390 B 1.8708 C 1.7983 | 1.8708 |
| `maj3_2` | 11.2608 | same | C .00331 | C 1.4564 | 1.5481 |
| `maj3_4` | 13.7632 | same | C .00447 | C 1.6702 | 1.7663 |
| `xor3_1` | **23.7728** | `(A&!B&!C) \| (!A&B&!C) \| (!A&!B&C) \| (A&B&C)` | A .00215 B .00502 C .00313 | A 1.9726 B 1.6210 C 1.2194 | 1.9726 |
| `xor3_2` | 25.0240 | same | C .00313 | C 1.4287 | 2.1273 |
| `xor3_4` | 27.5264 | same | C .00312 | C 1.9121 | 2.5262 |
| `xor2_1` | 8.7584 | `(A&!B) \| (!A&B)` | A .00385 B .00397 | A .9443 B .8333 | 0.9443 |
| `xor2_4` | 27.5264 | same | A .01544 | A .6275 | 0.6275 |
| `xnor2_1` | 8.7584 | `(!A&!B) \| (A&B)` | A .00385 B .00402 | A .5194 B .5095 | 0.5194 |
| **`a21o_1` (AO21)** | **7.5072** | `(A1&A2) \| (B1)` | A1 .00210 A2 .00203 B1 .00213 | A1 .6833 A2 .7274 B1 .6647 | 0.7274 |
| `a21o_2` | 8.7584 | same | — | A2 .8769 | 0.8769 |
| `a21o_4` | 15.0144 | same | — | A2 .9153 | 0.9153 |
| `o21a_1` (OA21) | 7.5072 | `(A1&B1) \| (A2&B1)` | A1 .00206 A2 .00214 B1 .00204 | A1 .8305 A2 .7836 B1 .5165 | 0.8305 |
| `a21oi_1` (AOI21) | 5.0048 | `(!A1&!B1) \| (!A2&!B1)` | — | A2 .9284 | 0.9284 |
| `and2_1` | 6.2560 | `(A&B)` | A .00125 B .00126 | A .5862 B .6870 | 0.6870 |
| `or2_1` | 6.2560 | `(A) \| (B)` | A .00124 B .00125 | A 1.1337 B 1.0744 | 1.1337 |
| `nand2_1` | 3.7536 | `(!A) \| (!B)` | A .00203 B .00203 | A .3551 B .3710 | 0.3710 |
| `mux2_1` | 11.2608 | `(A0&!S) \| (A1&S)` | A0 .00139 A1 .00170 S .00296 | S 1.6550 | 1.6550 |
| `mux2_4` | 15.0144 | same | S .00453 | S 1.2252 | 1.2252 |
| `dfxtp_1` | 20.0192 | `IQ` | CLK .00146 D .00138 | CLK 1.4179 | 1.4179 |
| `buf_1` | 3.7536 | `(A)` | A .00178 | A .5158 | 0.5158 |
| `buf_4` | 7.5072 | `(A)` | A .00208 | A .5170 | 0.5170 |
| `fa_1` | 20.0192 | `(A&B) \| (A&CIN) \| (B&CIN)`; sum term | A .00596 CIN .00414 | A 2.6940 | 2.6940 |

TT/FF values for every cell are in `lib_characterization.md` / `.json` (not repeated here).

**Headline:** `xor3_1` is **2.37× the area** of `maj3_1` and **slower at SS**
(1.9726 vs 1.8708 ns). `a21o_1` is **25% smaller** than `maj3_1` and **2.6× faster**
(0.7274 vs 1.8708 ns) at the same nominal point.

---

## ITEM 2 — Can a shallower carry topology exist with genuine cells? : **PASS**

Yes — but the win is *speed*, not raw depth: an AO21/prefix network is at most one cell
shallower in the carry recurrence and is much faster per cell.

Carry identity used: `c[i+1] = g[i] | (p[i] & c[i])` with `g=a&b` (AND2), `p=a|b` (OR2).
`a21o(p, c, g)` implements exactly `(A1&A2)|B1 = (p&c)|g` in **one genuine cell**.

Five SS STA experiments (real fanout loads, zero I/O delay):

| Topology (netlist) | Carry implementation | Carry-path cells | SS critical path | Path to `cout` |
|---|---|---:|---:|---:|
| `net_maj3_ripple.v` | 4× `maj3_1` ripple (the "current" chain) | 4 | 7.0736 ns | **7.0736 ns** |
| `net_ao21_ripple.v` | 4× `a21o_1` + parallel `and2`/`or2` | 4 (same depth) | 3.6537 ns | **3.6537 ns** |
| `net_bk_prefix.v` | Brent–Kung prefix, `a21o`/`and2`/`or2` | **3 in carry network** | 3.4708 ns | **2.8643 ns** |
| `net_xor3sum.v` | BK prefix + `xor3_1` sums | 3 | **3.7519 ns** (slower) | 2.8643 ns |
| ABC full-lib synth | ABC choice: 2 `maj3_1` + AOI/OAI + XNOR | mixed | 4.9863 ns | — |

- **AO21 ripple** is the same cell depth as 4×maj3 but **1.94× faster** at SS
  (7.0736 → 3.6537 ns). This alone answers the speed question affirmatively.
- **Brent–Kung prefix** reduces the carry-propagation recurrence to **3 AO21 cells**
  (one fewer than the 4-cell maj3 chain). Critical path:
  `a[1] → or2(p1) → a21o(g10) → a21o(g30) → a21o(cy4) → cout = 2.8643 ns`, i.e.
  **2.47× faster** than the 4×maj3 chain. Counting the parallel `and2` that generates `g`,
  the total from primary input is 4 cells — equal to maj3 — so "strictly fewer cells end-to-end"
  is only true of the carry recurrence itself, not of total gate count.
- **XOR3 for sums is counterproductive here:** 1 cell per sum bit, but it is slower
  (0.8399 ns vs 0.5479 ns at real load in the same design) and 2.37× the area. The whole-design
  critical path got *worse* (3.4708 → 3.7519 ns) when XOR2+carry sums were replaced by XOR3.
- Mux-based carry (`mux2(g? …)`, select = XNOR) and OAI/AOI alternatives exist and are legal,
  but Liberty delays (mux2_1 S-pin 1.6550 ns SS; a21oi_1 0.9284 ns) make them slower than AO21.

**Conclusion:** A faster legal carry topology exists (AO21 ripple or BK prefix using
`a21o_*`/`and2_*`/`or2_*`). It is not dramatically shallower in total cell count, but it is
2–2.5× faster at SS. XOR3 is explicitly *not* the reason — it is a slow, large cell in this library.

---

## ITEM 3 — Restricting cells to force ABC away from maj3 : **PASS**

The full Liberty has `dont_use : 0` (ABC: "0 dont_use"), so restriction must be done by
removing cells from the Liberty presented to ABC (or by a mapping-agent dont-use pattern).

Script `filter_liberty.py` produced `sky130_fd_sc_hd__ss_nomaj3.lib` in the evidence dir by
dropping **exactly 12 cells**:

```
sky130_fd_sc_hd__maj3_1, maj3_2, maj3_4
sky130_fd_sc_hd__fa_1, fa_2, fa_4
sky130_fd_sc_hd__fah_1, fahcin_1, fahcon_1
sky130_fd_sc_hd__ha_1, ha_2, ha_4
```

Yosys + ABC (`synth_full.ys`, `synth_nomaj3.ys`; logs `yosys_full.log`, `yosys_nomaj3.log`):

| Synthesis | Cells chosen | Cell count | Total area | SS critical path |
|---|---|---:|---:|---:|
| Full Liberty | 2×`maj3_1`, 4×`xnor2_1`, 3×`nand2_1`, `a21boi_0`, `a21oi_1`, `o21ai_0`, `nand3_1`, `nor2_1`, `nor3_1`, 2 tie cells | 17 | 110.1056 | **4.9863 ns** |
| maj3/fa/ha removed | `o21ai_0`×3, `nor2_1`×4, `xnor2_1`×3, `nand2_1`×2, `nand2b_1`×2, `and2_0`, `a21oi_1`, `nor2b_1`, `nor3_1`, `nand3_1`, `o31ai_1`, 1 tie cell | 21 | 117.6128 | **2.5669 ns** |

- **Legal:** every selected cell is a real Sky130 HD cell; no maj3/fa/ha appears in the
  restricted netlist (`netlist_nomaj3.v`). ABC reports 322 usable cells (vs 334 full).
- **Faster at SS:** 4.9863 → 2.5669 ns = **1.94× faster**, at **+6.8% area**
  (110.1056 → 117.6128) and +4 cells. This is the concrete answer to item 3.
- Note: even in the *full* library ABC did **not** build a 4×maj3 chain — it used only 2
  maj3 cells. The "4-cell maj3 chain" is therefore a structural baseline, not ABC's natural
  behavior for behavioral Verilog.

Recommended dont-use patterns for the mapping agent (exact):
```
sky130_fd_sc_hd__maj3_[124]
sky130_fd_sc_hd__fa_[124]
sky130_fd_sc_hd__fah*        # fah_1, fahcin_1, fahcon_1
sky130_fd_sc_hd__ha_[124]
sky130_fd_sc_hd__xor3_[124]  # optional: slow+large, see item 2
sky130_fd_sc_hd__xnor3_[124] # optional, same reason
```

---

## ITEM 4 — Library/tool limitations (stated, not papered over) : **PASS**

1. **No fast XOR3.** `xor3_1` area 23.7728 (2.37× maj3) and SS 1.9726 ns (slower than
   maj3's 1.8708 ns). All three drives (`_1/_2/_4`) are worse than `_1`. XOR3 is not a
   viable speed lever here.
2. **No `ao21`/`oa21` literal names** — only `a21o`/`o21a` families (see item 1a). A mapping
   agent searching for the literal patterns will find nothing.
3. **Yosys `read_liberty` fails on the full liberty:**
   `ERROR: Missing function on output GCLK of cell sky130_fd_sc_hd__dlclkp_1`.
   Workaround used: skip `read_liberty`, let ABC's own reader consume the file via
   `abc -liberty`. ABC then reports: *334 cells (94 skipped: 63 seq; 13 tri-state; 18 no
   func; 0 dont_use; 0 with 2 outputs; 0 with 3+ outputs)* and warns about 9 multi-output
   cells (e.g. `fa_1`). The restricted lib gives 322 cells.
4. **ABC cost model:** it does not prefer a pure maj3 chain and does not optimize purely to
   SS delay; its structural choice (2 maj3 + AOI/OAI/XNOR) is area/structural-cost driven.
   Forcing maj3 out is what produced the 1.94× SS gain, not an ABC "fast" setting.
5. **OpenSTA is a Linux ELF** (`D:\tools\opensta\bin\sta`); on this Windows host it was run
   via WSL Ubuntu-24.04 with `LD_LIBRARY_PATH=/mnt/d/tools/opensta/lib`. No native `sta.exe`,
   `yosys.exe` PATH entry, or Python on PATH was available (used bundled
   `D:\tools\oss-cad-suite\lib\python3.exe` and OSS-CAD-Suite environment script).
6. **Constant/tie cells** (`lpflow_inputiso1p_1`, `lpflow_isobufsrc_1`) appeared as tie
   generators in ABC output; they are legitimate liberty cells but should be allowed in the
   mapping flow to avoid dangling inputs.
7. **"Strictly shallower than 4 cells end-to-end" is NOT achieved.** Counting the parallel
   `and2`/`or2` g/p generation, the prefix design is also 4 cells from primary input; only
   the carry recurrence is 3 cells. The benefit is lower cell delay, not fewer stages.

No number in this report was invented: all delays are either Liberty table interpolations
at the stated nominal point (method 1) or OpenSTA data-arrival times from the named netlists
(method 2).

---

## ITEM 5 — No repository modified : **PASS**

See `repo_integrity.txt`. Liberty SHAs unchanged (TT = supplied `92eb4e93…`); OpenSTA git
tree clean; pre-existing OpenROAD git-status noise was present at session start and no path
under `src/sta/test/sky130hd` appears in it. Every written artifact is inside
`D:\audit\m1g_evidence_20261117\library\`.

---

## Recommended cell set for the mapping agent

**Allow (legal + fast):**
```
sky130_fd_sc_hd__a21o_[124]      sky130_fd_sc_hd__a21oi_[0124]
sky130_fd_sc_hd__o21a_[124]      sky130_fd_sc_hd__o21ai_[0124]
sky130_fd_sc_hd__and2_[0124]     sky130_fd_sc_hd__or2_[0124]
sky130_fd_sc_hd__nand2_[1248]    sky130_fd_sc_hd__nor2_[1248]
sky130_fd_sc_hd__xor2_[124]      sky130_fd_sc_hd__xnor2_[124]
sky130_fd_sc_hd__inv_[1246812]   sky130_fd_sc_hd__buf_[124681216]
sky130_fd_sc_hd__mux2_[1248]     sky130_fd_sc_hd__dfxtp_[124]
sky130_fd_sc_hd__conb_1          (tie cell, if needed)
```

**Don't-use / restrict (to force ABC off maj3):**
```
sky130_fd_sc_hd__maj3_[124]      sky130_fd_sc_hd__fa_[124]
sky130_fd_sc_hd__fah*            sky130_fd_sc_hd__ha_[124]
```

**Restrict for speed/area (optional but advised):**
```
sky130_fd_sc_hd__xor3_[124]      sky130_fd_sc_hd__xnor3_[124]
```

**Preferred carry topology to request from the mapping agent:** AO21 ripple for minimal
risk (3.6537 ns SS, same depth as maj3), or Brent–Kung prefix with `a21o_*` + `and2_*` +
`or2_*` (2.8643 ns to cout) if the extra prefix logic is acceptable. Do not request XOR3-based
sums.

---

## Evidence paths (all under `D:\audit\m1g_evidence_20261117\library\`)

| File | Purpose |
|---|---|
| `all_cell_names.txt`, `cell_inventory.txt` | 428-cell and 66-cell filtered inventories |
| `lib_characterization.json`, `lib_characterization.md` | Full area/function/cap/delay tables, 87 cells × tt/ss/ff |
| `parse_liberty.py`, `target_cells.txt` | Extraction script and target list |
| `filter_liberty.py`, `sky130_fd_sc_hd__ss_nomaj3.lib` | maj3-restricted Liberty (evidence-only copy) |
| `adder4.v`, `synth_full.ys`, `synth_nomaj3.ys` | Synthesis inputs |
| `yosys_full.log`, `yosys_nomaj3.log` | ABC cell-choice logs |
| `netlist_full.v`, `netlist_nomaj3.v` | ABC-mapped netlists |
| `net_maj3_ripple.v`, `net_ao21_ripple.v`, `net_bk_prefix.v`, `net_xor3sum.v` | Structural topology netlists |
| `sta_generic_wsl.tcl`, `sta_full_wsl.tcl`, `sta_nomaj3_wsl.tcl` | STA scripts |
| `sta_*.log` | OpenSTA reports (SS data-arrival times) |
| `compare_area.py`, `area_comparison.txt` | Area/count computation |
| `repo_integrity.txt` | SHA and git-status verification |

## Unresolved / out of scope

- Input slew for method 1 is fixed at 0.100 ns; no automated slew/load sweep beyond the
  Liberty tables was performed (the tables themselves are in the JSON).
- FF-corner STA was not run (only TT/SS/FF *cell* data were extracted); item 2/3 timing
  comparisons are SS as requested.
- Total-area comparison counts standard-cell area only; routing/wireload was not annotated
  (virtual clock, ideal nets). Relative conclusions are unaffected.
- `dont_use` was implemented by Liberty filtering, not by an OpenROAD `set_dont_use` flow,
  because this repo's full Liberty cannot be read by Yosys's Liberty frontend.
