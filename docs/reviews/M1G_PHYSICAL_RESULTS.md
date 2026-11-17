# Milestone 1G — Physical Design Engineer: targeted place→CTS→route→extract→STA on Candidate B

**Date:** 2026-11-18
**Role:** Physical Design Engineer (subagent)
**Worktree:** `D:\audit\m1g_mapping_20261117` @ `d585055392a8006dc3199202a64702c60ded338c` (pre-existing local verification fixes only)
**Evidence (outside all repos):** `D:\audit\m1g_evidence_20261117\physical\`
**RunId:** `m1g_cand_b_route`
**Run dir (preserved):** `D:\audit\m1g_evidence_20261117\physical\scratch_flow\regression\workspace\run_m1g_cand_b_route`
**Constraints:** approved canonical 10.0 ns SDC (`create_clock -period 10.0000`, 2 ns input / 2 ns output delays), unmodified.

---

## 0. Verdict summary (PASS / FAIL / BLOCKED / NOT_TESTED)

| # | Item | Status |
|---|---|---|
| 1 | Full 13-stage place→CTS→route→extract→STA flow executed on candidate B | **PASS** (00–11 PASS, 12 executed; stage 12 FAIL = timing only) |
| 2 | Stage 01 mapped netlist equals published candidate-B netlist `f2cabe8a…` | **PASS** (exact SHA match, see §2) |
| 3 | Candidate RTL + canonical SDC hashes unchanged; no tracked worktree edit | **PASS** (§8) |
| 4 | Placement legality / power integrity / router DRC / SPEF | **PASS** (§4) |
| 5 | Congestion report | **NOT_TESTED** (no `congestion.rpt` emitted by this flow) |
| 6 | Routed SS WNS ≥ 0 and TNS = 0 (genuine closure at 10 ns) | **FAIL** — SS WNS **−1.8325 ns**, TNS **−4.6857 ns**, 4 violators |
| 7 | Independent Stage 2 routed-STA harness cross-check | **PASS** (numbers match exactly, §5) |
| 8 | Signoff claim | **NOT TESTED / NOT CLAIMED** (academic OpenROAD flow only) |
| 9 | Official KLayout DRC (stage 14) / LVS (stage 15) / IR / EM / antenna | **NOT_TESTED** (outside requested 00–12 stage list) |

**Overall determination:** Candidate B is **NOT a genuine routed timing-closure candidate** at the approved 10 ns constraint. It is measurably better than the golden routed baseline on WNS (+0.5053 ns) but still violates by **1.8325 ns** and is worse on TNS. The pre-route +3.3607 ns estimate is confirmed to be an estimate, not predictive of routed timing.

---

## 1. Exact identifiers / hashes

| Object | SHA-256 | Note |
|---|---|---|
| Candidate B RTL (used) | `c16a81a42052ae2d1ac7fa02c759bf43cf98dcad71f9edb11e7f1d27ab95ca38` | matches task `c16a81a4…` |
| Published candidate-B mapped netlist | `f2cabe8a6a081740856725944c6562b2b5d0190b39d6bc66cb97ada222c4fc04` | matches task `f2cabe8a…` |
| **Stage 01 mapped netlist (this run)** | **`f2cabe8a6a081740856725944c6562b2b5d0190b39d6bc66cb97ada222c4fc04`** | **exact match** |
| Golden canonicalized netlist | `a84eb9558026703f9047fe020ded6948fceeccb7a34f097abf55f461b2d26da9` | NOT produced — STOP condition not triggered |
| Canonical SDC (raw / LF-normalized) | `640071510528b53e4433e9e16f2196b624f047bf35b1979897aa23623a4f826d` / `2a9126e716f834f255fe358aa8962ae619ffec50418e1104690a49eda47be644` | unchanged |
| Golden promoted RTL (original worktree) | `2a7e028dacd97b4462d791e69e69b8ff1b2cffcf239ba31b6e968efdadb8c4be` | unchanged |
| 10 ns reference config | `13fe60bf87cb687614b0d4c7942b6b477d1b7f9c8efda8eaa0fbc93a4317d707` | unchanged (reference-only; SDC is authoritative) |
| Routed DEF | `1ea93b0bcfcb0865da716d8bdd19dbaf1ad37df6def53a08ce8866fbdc2d67b4` | |
| Routed SPEF | `ae5aedee358381cb3f3cace027a34b6b1050123816582bff8276bdd03026dc82` | |

**Run command (verbatim form):**
```
$env:PATH = 'D:\tools\venv\Scripts;D:\tools\oss-cad-suite\lib;D:\tools\oss-cad-suite\bin;' + $env:PATH
python -m assistant.baseline --repo-root <scratch root> --design soc_ctrl --run-id m1g_cand_b_route \
  --stages 00_stage00,01_synthesis,02_lec_generic,03_lec_mapped,04_floorplan,05_placement,06_cts,\
07_pdn,08_power_connectivity,09_global_route,10_detailed_route,11_extract,12_sta \
  --out D:\audit\m1g_evidence_20261117\physical\runner\m1g_cand_b_route
```
`-RtlOverride`/`-SdcOverride` were not needed: `baseline.py` has no such flags, so the scratch copy's canonical RTL path was replaced with the candidate RTL and the SDC was left untouched. Runner log confirms `RTL : …/scratch_flow/examples/soc_ctrl/rtl/soc_ctrl.v` and run SDC `create_clock -name clk -period 10.0000`.

---

## 2. Scratch-flow setup and documented deviations (all outside repos)

Copied the whole worktree to `physical\scratch_flow\` (git HEAD `d585055…`, same 3 pre-existing modified files + 2 untracked tests). **No tracked worktree file was edited.** Scratch-only changes:

1. `examples/soc_ctrl/rtl/soc_ctrl.v` → replaced with candidate B RTL (`c16a81a4…`).
2. `regression/stages/01_synthesis.ps1` → (a) added `-dont_use sky130_fd_sc_hd__maj3_* -dont_use sky130_fd_sc_hd__fa_* -dont_use sky130_fd_sc_hd__xor3_*` to the `abc -liberty` line (the required netlist recipe); (b) removed `-flatten` from `synth`, (c) added `setattr -mod -unset keep_hierarchy` / `setattr -unset keep_hierarchy` / `flatten`, (d) removed `splitnets -ports`. (b–d) were required to reproduce candidate B's published **flat, packed-bus** netlist byte-for-byte; verified by 5 standalone Yosys recipe variants in `reports/` (v4 recipe → `f2cabe8a…`).
3. `regression/stages/02_lec_generic.ps1` → negative-control self-test corruption changed from inverter-flip to primary-output net corruption (`assign sum[4] = c[4];` → `assign sum[4] = c[3];`). Candidate B's generic netlist is AND/OR/XOR-only (no inverter), so the original self-test could not run. Verified the adapted self-test is detected (negative exit = 1). Positive LEC unchanged: 28/28 compare points proved.
4. `regression/stages/03_lec_mapped.ps1` → EQY gold forced to the pristine canonical golden RTL in the *original, unmodified* worktree (`2a7e028d…`). This is the documented Milestone-1G `-RtlOverride` behavior; without it EQY deterministically fails the `sum[4]/carry_out[2]/carry_out[1]` partitions. Result: **10/10 partitions proved, EQY "Successfully proved designs equivalent"**; negative control rejected (exit 2).

Adapted scratch scripts preserved at `reports/scratch_01_synthesis_ADAPTED.ps1`, `reports/scratch_02_lec_generic_ADAPTED.ps1`, `reports/scratch_03_lec_mapped_ADAPTED.ps1`. Preserved probe run dir: `run_m1g_cand_b_synth_probe` (stage 00+01 only).

---

## 3. Stage table (13 stages, RunId `m1g_cand_b_route`)

| Stage | Status | Key evidence |
|---|---|---|
| 00_stage00 | PASS | preflight overall=PASS, 0 mandatory failures |
| 01_synthesis | PASS | mapped SHA `f2cabe8a…`; 24 cells; 4 Sky130 types |
| 02_lec_generic | PASS | positive 28/28 proved; negative control detected (exit 1) |
| 03_lec_mapped | PASS | EQY 10/10 partitions proved; negative control exit 2 |
| 04_floorplan | PASS | 120×60 µm die, 66 well ties, 18 pins |
| 05_placement | PASS | 24 functional cells contiguous; `detailed_placement` ran; no violations |
| 06_cts | PASS | 8 clock sinks; clock tree built |
| 07_pdn | PASS | VDD/VSS grid + SPECIALNETS geometry |
| 08_power_connectivity | PASS | `POWER_PINS VDD=121 VSS=121 BAD=0`; `WELL_TIES 67/67` |
| 09_global_route | PASS | `global_route.guide` written |
| 10_detailed_route | PASS | routed ODB/DEF written; `router_violations_zero PASS` |
| 11_extract | PASS | SPEF with 37 *CAP, 37 *RES, 37 nets |
| 12_sta | **FAIL** | only 2 failing acceptance checks: `no_timing_violators` (4) and `wns_non_negative` |

Runner acceptance: **66 passed, 2 failed, 27 not tested, 95 total**; OVERALL INCONCLUSIVE (the 2 failures are exactly the expected SS setup checks).

---

## 4. Physical checks (quoted from runner log / artifacts)

**Placement legality:**
```
CHECK 05_placement functional_cells_contiguous PASS 24 functional cells form one contiguous block (10580 .. 115460 dbu)
CHECK 05_placement detailed_placement_completed PASS detailed_placement ran
CHECK 05_placement no_placement_violations PASS check_placement reported no violations
```

**Power (`08_power_connectivity.txt`):**
```
POWER_PINS VDD=121 VSS=121 BAD=0
WELL_TIES total=67 connected_to_VSS=67
POWER_CONNECTIVITY_RESULT PASS
```

**Router DRC / congestion / extraction:**
```
CHECK 10_detailed_route router_violations_zero PASS the router reported 0 violations
detailed_route_drc.rpt = 0 bytes
CHECK 11_extract spef_generated PASS .../11_final.spef
CHECK 11_extract spef_covers_design_nets PASS 37 nets in the SPEF
```
No `congestion.rpt` was emitted → congestion **NOT_TESTED** (same as Stage 2). The clean DRC statement is strictly the **OpenROAD router** violation report; it is NOT the public KLayout stage-14 DRC deck (NOT_TESTED).

---

## 5. Timing results — routed (SPEF-based, propagated clocks) vs baselines

### 5.1 WNS/TNS table (ns, 10 ns clock, 2 ns I/O delays)

| Corner | setup WNS | setup TNS | setup violators | hold WNS | hold TNS |
|---|---|---|---|---|---|
| **SS** (slow-slow, primary) | **−1.8325** | **−4.6857** | **4** | +0.6341 | 0.0 |
| TT | +4.9571 | 0.0 | 0 | +1.7040 | 0.0 |
| FF | +5.3035 | 0.0 | 0 | +1.8094 | 0.0 |

Exact (from `sta/sta_summary.txt` and the independent harness, identical):
`SS setup_wns -1.8324693392474176`, `setup_tns -4.6857221651259175`; `TT 4.957081108122514 / 0.0`; `FF 5.303504896472255 / 0.0`.

### 5.2 Three requested endpoint slacks (SS setup, `sta/sta_ss.rpt`, required 8.0000 ns each)

| Endpoint | Startpoint | Arrival | Required | Slack |
|---|---|---|---|---|
| `sum[3]` | `_04_` (FF) | 9.8325 | 8.0000 | **−1.8325 (VIOLATED)** — WNS |
| `sum[4]` | `_04_` (FF) | 9.4406 | 8.0000 | **−1.4406 (VIOLATED)** |
| `carry_out[2]` | `_04_` (FF) | 8.7449 | 8.0000 | **−0.7449 (VIOLATED)** |
| `sum[2]` (4th) | `_04_` (FF) | 8.6678 | 8.0000 | −0.6678 (VIOLATED) |

Worst path cell arc (verbatim): `_04_/Q 1.9511 → _17_/X (xor2_1) 1.9602 → _12_/X (a21o_1) 1.5029 → _13_/X (a21o_1) 1.0434 → _14_/X (a21o_1) 1.0907 → _01_/X (xor2_1) 1.0886 → sum[3] 0.0005`, with propagated clock network delay 1.1952 ns and 2 ns output external delay.

### 5.3 Comparison — honest signoff-vs-estimate labeling

| Design / mode | SS WNS | SS TNS | Label |
|---|---|---|---|
| Golden **routed** baseline (Stage 2, `m1e2_phys_base`) | −2.3378 | −4.0984 | routed (academic OpenROAD, SPEF) |
| **Candidate B routed (this run)** | **−1.8325** | **−4.6857** | routed (academic OpenROAD, SPEF) — **NOT signoff** |
| Candidate B **pre-route estimate** (M1G STA) | +3.3607 | 0.0 | zero-wire / ideal-clock **ESTIMATE** — NOT signoff |
| Golden **pre-route estimate** (M1G STA) | −1.0188 | −1.0188 | zero-wire / ideal-clock ESTIMATE |

Determinations:
- Candidate B routed WNS is **+0.5053 ns better** than golden routed (−1.8325 vs −2.3378), but routed TNS is **0.5873 ns worse** (−4.6857 vs −4.0984) with one extra violator (4 vs 3).
- Candidate B routed timing is **5.1932 ns worse** than its own pre-route estimate (+3.3607 → −1.8325). The pre-route result is explicitly labeled an estimate; **no signoff claim is made for either**.
- Neither golden nor candidate B achieves SS WNS ≥ 0 / TNS = 0 after routing.

### 5.4 Independent harness cross-check

Copied the proven Stage 2 harness (`measure.ps1`, `measure_ss.tcl`) into `physical\sta\` and ran it on `11_final.v` + `11_final.spef` + run `11_final.sdc` (read-only). It produced `STA_MEASUREMENT_OK` with the exact same WNS/TNS to full floating-point precision and the same 8 output endpoint ordering (markers at `sta/logs/m1g_cand_b_route.markers.tsv`). This is a **routed, SPEF-based** measurement, not an estimate.

---

## 6. Integrity / preservation (PASS)

```
Original worktree HEAD : d585055392a8006dc3199202a64702c60ded338c  (unchanged)
git status --porcelain:  M regression/checks/lvs_compare.py
                         M regression/stages/03_eqy_prep.py
                         M regression/stages/03_lec_mapped.ps1
                        ?? tests/test_eqy_override.py
                        ?? tests/test_lvs_compare.py        (all pre-existing; none touched)
git log --oneline -3    : d585055, 27b5d1c, 6c4a264 (unchanged)
git diff --cached --stat: (empty)
RTL SHA 2a7e028d… / SDC raw 6400715… / LF-norm 2a9126e7… / config 13fe60bf…  (unchanged)
```
No commit, no push, no production edit, no full historical regression. All writes confined to `D:\audit\m1g_evidence_20261117\physical\`.

---

## 7. Evidence index

```
D:\audit\m1g_evidence_20261117\physical\
├── M1G_PHYSICAL_REPORT.md                      (this report)
├── scratch_flow\                               (isolated worktree copy; only documented scratch edits)
│   └── regression\workspace\run_m1g_cand_b_route\   (PRESERVED full 13-stage run dir)
│       ├── synth\soc_ctrl_synth.v              (f2cabe8a…)
│       ├── detailed_route_drc.rpt              (0 bytes)
│       ├── 11_final.spef / .v / .sdc / .def
│       ├── 08_power_connectivity.txt
│       ├── sta\{sta_summary.txt,sta_ss.rpt,sta_tt.rpt,sta_ff.rpt,sta_all.log}
│       └── report.json / acceptance.txt / summary.md / run_manifest.json
├── runner\m1g_cand_b_route\{baseline_manifest.json, baseline_m1g_cand_b_route.log, route_stdout.txt}
├── runner\m1g_cand_b_synth_probe\              (stage 00+01 hash-verification probe)
├── sta\{measure.ps1,measure_ss.tcl,measure_stdout.txt,logs\m1g_cand_b_route.{log,markers.tsv}}
├── reports\{scratch_01/02/03_*_ADAPTED.ps1, flatten_check.*, lec_probe.*, v1..v5 recipe scripts/logs/netlists}
```

---

## 8. Unresolved issues / caveats

1. **SS closure FAILS** at WNS/TNS −1.8325 / −4.6857 ns (4 primary-output violations, all launched from `_04_`). The largest slack deficit is on `sum[3]`.
2. **Academic-only:** no qualified signoff extraction (QRC/StarRC), no official KLayout DRC (stage 14), no LVS (stage 15), no IR/EM/antenna. "Clean DRC" means OpenROAD router violations = 0 only.
3. **Congestion NOT_TESTED** — the flow emitted no `congestion.rpt`.
4. Scratch-only harness adaptations (§2) were necessary: stage 02 negative-control self-test and stage 03 EQY gold selection. Both positive equivalence proofs remained intact and are recorded. None of these touched the tested netlist, SDC, or physical implementation.
5. The golden routed baseline numbers (−2.3378 / −4.0984) are quoted from `D:\audit\m1e_stage2_evidence_20261117\physical\M1E_STAGE2_PHYSICAL_REPORT.md`; no full historical regression was re-run here.
6. Candidate A (`236594c3…`) was **NOT_TESTED** in this milestone (task: run B first; B did not fail operationally, it failed timing).
