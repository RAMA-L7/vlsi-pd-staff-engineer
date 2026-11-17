# Milestone 1G — Technology Mapping and Verification Infrastructure Report

**Date:** 2026-11-18
**Starting reference:** published commit `d585055392a8006dc3199202a64702c60ded338c`
**Controlled worktree:** `D:\audit\m1g_mapping_20261117` @ `d585055` (local uncommitted fixes only)
**Evidence:** `D:\audit\m1g_evidence_20261117\` (independent technical review: `review\REVIEW_REPORT.md`)
**Constraint state:** 10 ns period and 2 ns I/O delays unchanged; no uncertainty/derate/exception added. No production RTL change; no commit/push/PR.

## 1. Verdict

| Success criterion | Result |
|---|---|
| At least one genuinely different mapped architecture | **PASS** — 8 distinct netlist SHAs; candidates A/B have different mapped netlists |
| Genuine mapped LEC PASS | **PASS** — 16/16 points each, Sky130 HD, negative control rejected |
| Measurable SS timing improvement | **PRE-ROUTE PASS / ROUTED FAIL** — B: +3.3607 ns estimate vs −1.8325 ns routed |
| No weakened verification gates | **PASS** — EQY and LVS fixes tighten/relocate reference selection, keep all negative controls |
| Independent review | **PASS** — evidence re-derived, overstatements flagged |
| **Genuine routed SS timing closure** | **NO — FAIL** |

## 2. Mapping bottleneck (root cause)

`yosys synth` first canonicalizes ALL equivalent RTLs into the same generic network (`8 DFF + 12 NAND + 8 XOR`). ABC then has a deterministic cheapest SCL cover for that network because Sky130 HD provides a dedicated area-efficient majority cell:

```
sky130_fd_sc_hd__maj3_1  function "(A&B) | (A&C) | (B&C)"  area 10.0096
ABC RESULTS: maj3_1 cells: 4 / xnor2_1 cells: 8
```

So the 4-level maj3 ripple is ABC's unique optimum for the current 4-bit arithmetic network, not an accident of RTL style. Eight ABC settings produced different netlists (`-dont_use maj3_*` → `c50de40a…`, `-dont_use maj3_1` → `5a979b2f…`, `-constr` → `0517964d…`, script variants, etc.), but every one was worse in depth or area; `-D` had no effect. The baseline is depth 4 / 270.2592 µm² and remains the best-quality mapped result.

Library characterization confirmed the real trade-off: `maj3_1` is slow at SS (~1.87 ns); `a21o_1`/`o21a_1` AO21/OA21 cells are ~0.73–0.83 ns and area-efficient. Hand-built AO21 ripple is ~3.65 ns vs 7.07 ns for maj3 ripple at ideal STA, and a Brent–Kung prefix reaches `carry_out` in ~2.86 ns. `xor3_*` is 2.37× the area and slower — not a speed lever. Restricting ABC away from maj3/fa/ha produces a legal netlist 1.94× faster in ideal STA at +6.8% area, but that gain did not survive routing.

## 3. Genuinely different mapped candidates

| Candidate | RTL SHA | Mapped SHA | Cells | Area (µm²) | Depth | LEC |
|---|---|---|---:|---:|---|---|
| Golden | `2a7e028d…` | `a84eb955…` | 20 | 270.2592 | 4 | reference |
| A — structural prefix | `6117e9c6…` | `236594c3…` | 32 | 415.3984 | 5 | 16/16 PASS |
| B — AND/OR/AO21 lookahead | `c16a81a4…` | `f2cabe8a…` | 24 | 285.2736 | 5 | 16/16 PASS |

All candidates passed exhaustive 512-case simulation, Yosys `check` 0 problems, and Sky130 HD mapped LEC with 3-arg `equiv_make`, flattened both sides, `equiv_status -assert`, explicit `Equivalence successfully proven!`, and a rejected negative control (faulted `sum[0]` → 1 unproven, exit 1). No black boxes, Nangate, undef/X markers, or false PASS. Both candidates are area/depth worse than golden.

## 4. Timing: pre-route promise, routed failure

Pre-route (zero-wire/ideal-clock, screening only) at 10 ns:

| Design | SS WNS | TNS |
|---|---:|---:|
| Golden | −1.0188 | −1.0188 |
| maj3_2 upsized | +0.2049 | 0 |
| no-maj3 | +2.9538 | 0 |
| Candidate A | +2.3324 | 0 |
| **Candidate B** | **+3.3607** | **0** |

Candidate B was then taken through a targeted place→CTS→route→extract→STA run with its different netlist preserved (`m1g_cand_b_route`, netlist SHA `f2cabe8a…` verified at stage 01):

- stages 00–11 PASS, placement legal, power clean, router DRC 0 violations, SPEF generated (37 nets).
- **Routed SS WNS −1.8325 ns, TNS −4.6857 ns, 4 violators** — worse TNS and more violators than golden (−2.3378/−4.0984, 3 violators); WNS only +0.5053 ns better.
- Worst path migrated from `cin→sum[4]` (pre-route) to FF `_04_→sum[3]` (routed); 5.1932 ns gap is explained by real SPEF parasitics and 1.1952 ns propagated clock delay. Both STA harnesses agree to full precision.
- Pre-route is a screening estimate only and must never be used as a closure claim.

## 5. Verification infrastructure fixes (implemented, uncommitted)

- **EQY `-RtlOverride`:** root cause was gold/gate cone selection (candidate's parallel `sum[4]` left a carry input unused in one EQY partition). Fix selects the canonical promoted RTL as gold for override runs; golden and candidate stage-03 both PASS 8/8, broken control still FAIL; 8 new tests. No check skipped, no INCONCLUSIVE→PASS.
- **LVS comparator:** removed the final hard-coded `TOP='TEST_COUNTER'` fallback. Top now comes only from `REG_DESIGN`, run manifest (`<design>_design.cdl`, `acceptance.json`/`report.json`), or unique common root-cell detection; ambiguity/unknown yields explicit FAIL/INCONCLUSIVE. 12 tests + 15 synthetic negative controls PASS; genuine soc_ctrl (340/340) and test_counter (242/242) LVS PASS with official KLayout verdict unchanged.
- Router DRC remains separate from academic KLayout DRC; foundry signoff remains BLOCKED.

## 6. Remaining limitation / authorization required

ABC's unique cheapest maj3 cover, Sky130 HD's slow maj3 and lack of a fast XOR3, and real parasitics mean the 4-bit adder cannot be closed by synthesis-script or RTL changes alone within this flow. Next step requires one of:

1. commercial synthesis/technology mapping or a different/enriched cell library;
2. a changed architecture beyond the 4-bit adder (approved separately);
3. an approved constraint change (10 ns / 2 ns I/O are currently fixed);
4. signoff DRC/LVS/extraction policy and tooling for any candidate.

No physical implementation of candidate A was run (B was the best pre-route candidate and failed routed closure); no additional equivalent candidates were generated.

## 7. Integrity

Worktree `D:\audit\m1g_mapping_20261117` remains at `d585055` with only the local EQY/LVS verification fixes and two new test files uncommitted; staged diff empty. `D:\audit\repo` untouched at `41728cd` with 34 changes; remote `main` still `d585055`. No commit, push or PR; nothing published; no signoff or qualification claim made.

## 8. Evidence paths

- `mapping\REPORT.md`, `results.csv/json`, `runs\*`
- `library\report.md`, `lib_characterization.md/json`, `sky130_fd_sc_hd__ss_nomaj3.lib`, `sta_*.log`
- `rtl\M1G_REPORT.md`, `cand_a_prefix\`, `cand_b_andor\`, `lec\`, `sim\`
- `eqy\REPORT.md`, `diff.patch`, `verify_stage03_*.log`
- `lvs\` (12 tests + genuine comparator logs)
- `sta\M1G_STA_REPORT.md`, `logs\<tag>.log`
- `physical\M1G_PHYSICAL_REPORT.md`, `runs\run_m1g_cand_b_route\`
- `review\REVIEW_REPORT.md`
