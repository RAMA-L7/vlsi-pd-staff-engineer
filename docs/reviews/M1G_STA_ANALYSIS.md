# Milestone 1G — STA Engineer: PRE-ROUTE SS timing comparison of genuinely different mapped netlists

**Date:** 2026-11-17/18  **Role:** STA Engineer (subagent)
**Mode:** PRE-ROUTE / zero-wire / ideal-clock ESTIMATE — **NOT signoff** (no SPEF, no parasitics, no physical implementation).
**Tool:** OpenSTA 3.1.0 (`/mnt/d/tools/src/OpenSTA/build/sta`) under WSL `Ubuntu-24.04`.
**Harness:** verbatim copy of Stage 1F `measure_pre_route.tcl/.ps1` from `D:\audit\m1f_evidence_20261117\sta\` (originals unmodified; copies at `D:\audit\m1g_evidence_20261117\sta\`). SHA256 of copies: `.tcl`=3736B03C…, `.ps1`=CEE27255… (identical to source).

## Scope / status summary
| # | Item | Status |
|---|------|--------|
| 1 | Verify all 6 netlist SHAs, SDC constraint lines, same 3 corner libs | **PASS** |
| 2 | Measure each netlist (SS WNS/TNS, TT/FF WNS, hold min, worst path, key slacks) | **PASS** |
| 3 | Comparison table + explicit improvement/WNS>=0 statements | **PASS** |
| 4 | No repository file changed; no constraint modified; no physical implementation | **PASS** |

> Overall **PASS** with caveat: these are pre-route zero-wire ideal-clock estimates, not signoff; final timing requires parasitic extraction (SPEF) after physical implementation, which is explicitly out of scope for M1G.

---

## 1. Input verification (PASS)

### 1.1 Netlist SHA256 — all 6 match task-specified prefixes (re-verified after run)
| Design | Path (relative to evidence root) | SHA256 (full) | Task prefix |
|---|---|---|---|
| golden baseline | mapping/runs/baseline/mapped.v | A84EB9558026703F9047FE020DED6948FCEECCB7A34F097ABF55F461B2D26DA9 | a84eb955 ✅ |
| maj3_2 upsized | mapping/runs/dontuse_maj3_1/mapped.v | 5A979B2FDEA38AE78AC84E6CF191E5B6F2AA7E39499B4E1E22ACD146EBE8725C | 5a979b2f ✅ |
| constrained timing | mapping/runs/constr_timing/mapped.v | 0517964D5A2505208F4F408A3F8735895C4531F295AE8E4966982608660DA762 | 0517964d ✅ |
| no-maj3 | mapping/runs/dontuse_maj3_star/mapped.v | C50DE40AF296267993D50411D4DAA544C1DCAA82E23517BEF36FB2603ADA778D | c50de40a ✅ |
| structural cand A | rtl/cand_a_prefix/cand_a_mapped.v | 236594C3F44CCBC183CEF15667B72D1170DEDF2042A50AE70D923524CF36351B | 236594c3 ✅ |
| structural cand B | rtl/cand_b_andor/cand_b_mapped.v | F2CABE8A6A081740856725944C6562B2B5D0190B39D6BC66CB97ADA222C4FC04 | f2cabe8a ✅ |

All 6 are distinct SHAs (genuinely different netlists). The two equal-result variants `dontuse_maj3_1` and `constr_timing` differ by 8 lines of cell-instance naming only (`maj3_2` vs `xnor2_1` instance labels swapped on instances _04_/_08_/_11_/_13_), i.e. electrically equivalent topology with different netlist identity — hence identical STA.

### 1.2 SDC — unchanged, canonical 10 ns / 2 ns I/O, no timing exceptions
`D:\audit\m1g_mapping_20261117\examples\soc_ctrl\rtl\soc_ctrl.sdc`, SHA256 `640071510528B53E4433E9E16F2196B624F047BF35B1979897AA23623A4F826D` (unchanged before/after).
Effective constraint lines:
```
create_clock -name clk -period 10.0 [get_ports clk]
set_input_delay  -clock clk 2.0 [all_inputs]
set_output_delay -clock clk 2.0 [all_outputs]
set_input_delay  -clock clk 0.0 [get_ports clk]
set_max_transition 1.5 [current_design]
set_max_fanout     30 [current_design]
```
No `set_clock_uncertainty`, `set_clock_latency`, `set_*_derate`, `set_data_check`, `set_false_path`, `set_multicycle_path`, or any exception. Set once, read by all 6 runs; harness never writes it. Log line: `STA_SDC read from /mnt/d/audit/.../soc_ctrl.sdc (NOT modified by this harness)`.

### 1.3 Corner libs — identical 3 libs for every netlist
All from `/mnt/d/tools/src/OpenROAD/src/sta/test/sky130hd/`:
- `sky130_fd_sc_hd__tt_025C_1v80.lib` (TT)
- `sky130_fd_sc_hd__ss_n40C_1v40.lib` (SS)
- `sky130_fd_sc_hd__ff_n40C_1v95.lib` (FF)

Log line (each run): `STA_MODE pre_route zero_wire ideal_clock spef=NOT_READ` / `STA_LOADED corner_count=3 design=soc_ctrl (PRE-ROUTE, no SPEF)`.

---

## 2. Measurements (PASS)

Exact `STA_MEAS` lines are quoted in Appendix A and in the per-run logs under `D:\audit\m1g_evidence_20261117\sta\logs\`.

### 2.1 SS (slow-slow) setup — primary comparison corner, 10 ns clock
| Design | SS setup WNS (ns) | SS setup TNS (ns) | Worst path startpoint → endpoint | sum[4] slack | sum[3] slack | carry_out[2] slack |
|---|---|---|---|---|---|---|
| golden baseline | **−1.0188** (VIOLATED) | −1.0188 | `cin` → `sum[4]` | −1.0188 VIOLATED | +0.1362 MET | +0.6440 MET |
| maj3_2 upsized | +0.2049 | 0.0 | `cin` → `sum[4]` | +0.2049 MET | +1.1005 MET | +1.5763 MET |
| constrained timing | +0.2049 | 0.0 | `cin` → `sum[4]` | +0.2049 MET | +1.1005 MET | +1.5763 MET |
| no-maj3 | +2.9538 | 0.0 | `cin` → `sum[3]` | +3.0187 MET | +2.9538 MET | +3.2633 MET |
| structural cand A | +2.3324 | 0.0 | `u_qb0` (FF) → `sum[3]` | +3.3113 MET | +2.3324 MET | +3.2184 MET |
| structural cand B | **+3.3607** | 0.0 | `cin` → `sum[4]` | +3.3607 MET | +3.4202 MET | +3.9771 MET |

The golden baseline's only SS setup violation is the `cin → sum[4]` ripple path through two weak `maj3_1` cells; every alternative fixes it.

### 2.2 Other corners
| Design | TT setup WNS | FF setup WNS | Hold WNS (min across TT/SS/FF = FF corner) | Hold TNS |
|---|---|---|---|---|
| golden baseline | +4.5320 | +5.2278 | +2.0158 | 0.0 |
| maj3_2 upsized | +4.7568 | +5.2992 | +2.0158 | 0.0 |
| constrained timing | +4.7568 | +5.2992 | +2.0158 | 0.0 |
| no-maj3 | +5.1771 | +5.4687 | +2.0158 | 0.0 |
| structural cand A | +5.5586 | +5.7562 | +2.0158 | 0.0 |
| structural cand B | +5.2954 | +5.5455 | +2.0158 | 0.0 |

Hold is clean for all designs (hold WNS all positive; min = +2.0158 ns at FF, hold TNS = 0). Setup WNS values are all positive at TT and FF for every design.

---

## 3. Comparison table (PASS) — delta vs golden at SS

Golden SS setup WNS = −1.018758 ns (TNS −1.018758 ns).

| Design | Mapped SHA (12) | Cells | Area (µm²) | Max comb depth | SS WNS (ns) | SS TNS (ns) | Δ WNS vs golden (ns) |
|---|---|---|---|---|---|---|---|
| golden baseline (maj3_1 ripple) | a84eb9558026 | 20 (8×dfxtp_1, 4×maj3_1, 8×xnor2_1) | 270.2592 | 4 | −1.0188 | −1.0188 | — (reference) |
| maj3_2 upsized | 5a979b2fdea3 | 20 (8×dfxtp_1, 4×maj3_2, 8×xnor2_1) | 275.2640 | 4 | +0.2049 | 0.0 | **+1.2237** |
| constrained timing | 0517964d5a25 | 20 (same composition as maj3_2) | 275.2640 | 4 | +0.2049 | 0.0 | **+1.2237** |
| no-maj3 | c50de40af296 | 32 (mixed: clkinv/nand/nor/o21a/lpflow_isobufsrc/xnor/dfxtp) | 292.7808 | 5 | +2.9538 | 0.0 | **+3.9725** |
| structural cand A (prefix/mux) | 236594c3f44c | 32 (8×dfxtp_1, 4×a21o_1, 8×and2_1, 4×mux2_1, 4×xor2_1, 4×xor3_1) | 415.3984 | 5 | +2.3324 | 0.0 | **+3.3512** |
| structural cand B (AND/OR/XOR SOP) | f2cabe8a6a08 | 24 (8×dfxtp_1, 4×and2_0, 8×xor2_1, 4×a21o_1) | 285.2736 | 5 | +3.3607 | 0.0 | **+4.3795** |

Cell/area sources: `mapping/results.json`, `rtl/M1G_REPORT.md`, cross-checked against `yosys.log` / `synth_*.log` (`Chip area for top module '\soc_ctrl'`).

### Explicit determinations
- **Does ANY alternative have WNS > golden WNS (measurable improvement)? — YES.** All five alternatives have SS WNS strictly greater than golden's −1.0188 ns: improvements of +1.2237 ns (maj3_2 upsized, constrained timing), +3.3512 ns (cand A), +3.9725 ns (no-maj3), +4.3795 ns (cand B).
- **Does any reach WNS ≥ 0? — YES.** All five alternatives have SS WNS ≥ 0 and SS TNS = 0 (all timing paths MET at slow-slow). Best is structural candidate B (+3.3607 ns), followed by no-maj3 (+2.9538 ns), cand A (+2.3324 ns), then the two maj3_2 variants (+0.2049 ns). The golden baseline is the **only** design that violates SS setup.
- **Not a signoff result:** these are pre-route estimates; no parasitic/wire delay, ideal clocks, no physical implementation. Post-route timing could differ; no signoff claim is made.
- **Notable finding:** at this 10 ns constraint the mapping "area/depth optimum" (golden, 270.26 µm² / depth 4) is the sole SS setup failure, while every deliberately-restricted or structurally-different netlist passes — but the passing alternatives cost +1.9% to +53.7% area. This is a pre-route observation only.

---

## 4. Integrity confirmation (PASS)
- Re-hashed all 6 netlists and the SDC after all runs: **all MATCH** pre-run values (list in precheck_repo_state.txt and §1.1).
- Git state of `D:\audit\m1g_mapping_20261117` before and after identical: HEAD `d585055392a8006dc3199202a64702c60ded338c`; 3 pre-existing modified regression files (`regression/checks/lvs_compare.py`, `regression/stages/03_eqy_prep.py`, `regression/stages/03_lec_mapped.ps1`) and 2 untracked tests — **all pre-existing, none touched by this task**. `publish_m1d_20261117` clean before/after. Evidence tree is not a git repo.
- SDC never edited; only read by OpenSTA. No constraint changes.
- No physical implementation, placement, routing, SPEF extraction, or full regression run. Only 6 read-only OpenSTA measurement invocations.
- All writes confined to `D:\audit\m1g_evidence_20261117\sta\` (harness copies, logs/, markers, precheck, this report).

---

## Appendix A — Exact STA_MEAS lines (SS, setup + hold)

```
STA_MEAS tag=golden_baseline corner=ss metric=setup_wns value=-1.018758439776894
STA_MEAS tag=golden_baseline corner=ss metric=setup_tns value=-1.018758439776894
STA_MEAS tag=golden_baseline corner=ss metric=hold_wns  value=2.0741555726702607
STA_MEAS tag=golden_baseline corner=ss metric=hold_tns  value=0.0

STA_MEAS tag=variant_maj3_2_upsized corner=ss metric=setup_wns value=0.20490454357675456
STA_MEAS tag=variant_maj3_2_upsized corner=ss metric=setup_tns value=0.0
STA_MEAS tag=variant_maj3_2_upsized corner=ss metric=hold_wns  value=2.0741555726702607
STA_MEAS tag=variant_maj3_2_upsized corner=ss metric=hold_tns  value=0.0

STA_MEAS tag=variant_constr_timing corner=ss metric=setup_wns value=0.20490454357675456
STA_MEAS tag=variant_constr_timing corner=ss metric=setup_tns value=0.0
STA_MEAS tag=variant_constr_timing corner=ss metric=hold_wns  value=2.0741555726702607
STA_MEAS tag=variant_constr_timing corner=ss metric=hold_tns  value=0.0

STA_MEAS tag=variant_no_maj3 corner=ss metric=setup_wns value=2.953782191333843
STA_MEAS tag=variant_no_maj3 corner=ss metric=setup_tns value=0.0
STA_MEAS tag=variant_no_maj3 corner=ss metric=hold_wns  value=2.0741555726702607
STA_MEAS tag=variant_no_maj3 corner=ss metric=hold_tns  value=0.0

STA_MEAS tag=cand_a_prefix corner=ss metric=setup_wns value=2.3324103308919897
STA_MEAS tag=cand_a_prefix corner=ss metric=setup_tns value=0.0
STA_MEAS tag=cand_a_prefix corner=ss metric=hold_wns  value=2.0741555726702607
STA_MEAS tag=cand_a_prefix corner=ss metric=hold_tns  value=0.0

STA_MEAS tag=cand_b_andor corner=ss metric=setup_wns value=3.3607291234489214
STA_MEAS tag=cand_b_andor corner=ss metric=setup_tns value=0.0
STA_MEAS tag=cand_b_andor corner=ss metric=hold_wns  value=2.0741555726702607
STA_MEAS tag=cand_b_andor corner=ss metric=hold_tns  value=0.0
```
(TT/FF STA_MEAS lines are in each log; hold min across all corners = FF 2.0158362216711323 ns.)

## Appendix B — Evidence paths
```
D:\audit\m1g_evidence_20261117\sta\
  measure_pre_route.tcl / measure_pre_route.ps1   (verbatim harness copies)
  precheck_repo_state.txt                          (baseline integrity snapshot)
  M1G_STA_REPORT.md                                (this report)
  logs\
    golden_baseline.log / .markers.tsv
    variant_maj3_2_upsized.log / .markers.tsv
    variant_constr_timing.log / .markers.tsv
    variant_no_maj3.log / .markers.tsv
    cand_a_prefix.log / .markers.tsv
    cand_b_andor.log / .markers.tsv
```

## Appendix C — Unresolved issues / limitations
- None blocking. Pre-route estimates only; **no signoff** without post-route SPEF (out of scope per task).
- `dontuse_maj3_1` and `constr_timing` are distinct netlists but electrically equivalent (instance-naming-only difference) → identical STA; reported separately as required.
- Pre-existing (not introduced here) modified/untracked regression files exist in `m1g_mapping_20261117`; left untouched.
