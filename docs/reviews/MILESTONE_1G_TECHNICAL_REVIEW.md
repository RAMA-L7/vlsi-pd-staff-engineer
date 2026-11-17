# Milestone 1G — INDEPENDENT TECHNICAL REVIEW

**Date:** 2026-11-18
**Reviewer role:** independent technical reviewer (subagent).
**Mandate:** READ-ONLY on all repositories; no commit / push / stage; writes confined to
`D:\audit\m1g_evidence_20261117\review\`. This report is the only artifact written.
**Evidence base:** `D:\audit\m1g_evidence_20261117\` (mapping, library, rtl, eqy, lvs, sta,
physical) plus read-only git/hash inspection of `D:\audit\m1g_mapping_20261117`,
`D:\audit\repo`, and archived `D:\audit\m1e_stage2_evidence_20261117`,
`D:\audit\m1f_evidence_20261117`.

---

## 0. Executive verdict

| Question | Answer |
|---|---|
| (a) At least one genuinely different mapped architecture? | **PASS — MET** |
| (b) Genuine mapped LEC PASS? | **PASS — MET** |
| (c) Measurable SS timing improvement? | **PRE-ROUTE: PASS · ROUTED: FAIL** (see §4c) |
| (d) No weakened verification gates? | **PASS — MET** (with disclosed caveats, §2/§8) |
| (e) Independent review? | **PASS — this report** |
| **Did 1G achieve genuine ROUTED timing closure?** | **NO** |

**Bottom line:** 1G produced genuinely different mapped netlists with genuine formal
equivalence and correctly preserved verification gates. The reported timing improvement is
**real only in the pre-route, zero-wire, ideal-clock estimate**. The one candidate taken
through place→CTS→route→extract→SPEF STA (**candidate B**) **fails** routed SS closure at the
unchanged 10 ns constraint: WNS **−1.8324693392474176 ns**, TNS **−4.6857221651259175 ns**,
**4 violators**; versus the golden routed baseline its WNS is +0.5053 ns better but its TNS
is 0.5873 ns **worse** with one **extra** violator (4 vs 3). No signoff claim is made or
warranted. The pre-route +3.3607 ns is an estimate, not predictive of routed timing.

---

## 1. AUDIT TASK 1 — Re-derivation from primary logs

All figures below were re-derived by direct file hashing and grep of primary logs, independent
of the subagent reports.

### 1.1 Distinct netlist SHAs — CONFIRMED (≥3 required; 8 found)

Direct `Get-FileHash -Algorithm SHA256` of `mapping/runs/*/mapped.v`:

| SHA-256 (12) | Run(s) |
|---|---|
| `a84eb9558026` | baseline, delay_D1, delay_D50, delay_D500 |
| `5a979b2fdea3` | dontuse_maj3_1 |
| `0517964d5a25` | constr_timing |
| `c50de40af296` | dontuse_maj3_star, dontuse_maj3_all3 |
| `5a3998be0afd` | script_strash_refactor_rw_map |
| `c10f0b7caf3c` | script_fraig_rewriteZ_mapB |
| `3d394bd7ee34` | script_map_areaA10 |
| `aa3d010df041` | script_map_delayB1 |

→ **8 distinct SHAs**, matching `mapping/REPORT.md` §2–3. Candidate netlists hashed directly:
`236594c3f44ccbc183cef15667b72d1170dedf2042a50ae70d923524cf36351b` (A),
`f2cabe8a6a081740856725944c6562b2b5d0190b39d6bc66cb97ada222c4fc04` (B). The physical
stage-01 netlist `physical/scratch_flow/regression/workspace/run_m1g_cand_b_route/synth/soc_ctrl_synth.v`
re-hashes to exactly `f2cabe8a…`, confirming the routed run used the published candidate-B
netlist, not a re-synthesized surrogate.

### 1.2 Mapped LEC verdicts — CONFIRMED

| Log | Primary evidence (exact lines) | Verdict |
|---|---|---|
| `rtl/lec/lec_gold_vs_cand_a.log` | L1772 `Of those cells 16 are proven and 0 are unproven.`; L1773 `Equivalence successfully proven!` | 16/16 PASS |
| `rtl/lec/lec_gold_vs_cand_b.log` | L1601 `Of those cells 16 are proven and 0 are unproven.`; L1602 `Equivalence successfully proven!` | 16/16 PASS |
| `rtl/lec/lec_negative_control.log` | L1638 `Proved 15…`; L1651 `15 are proven and 1 are unproven`; L1652 unproven `\sum_gold [0] \sum_gate [0]`; L1654 `ERROR: Found 1 unproven $equiv cells in 'equiv_status -assert'.` | correctly REJECTED |

Harness verified from the `.ys` scripts: `read_liberty` (real Sky130 HD TT), `prep -top soc_ctrl`,
`flatten`, `rename`, `design -stash`, 3-argument `equiv_make gold gate equiv`, `hierarchy`,
`equiv_simple`, `equiv_induct`, `equiv_status -assert`. The negative-control script is
identical except for the faulted gate netlist — so the PASS is sensitive, not vacuous.
The cosmetic-only LEC copy `rtl/lec/cand_b_mapped_lec.v` was byte-compared to
`rtl/cand_b_andor/cand_b_mapped.v`: after substituting `cb_`→`_` they are **byte-identical**
(no logic/port change). The discarded `*_noprep.log` shows `Setting undriven nets to undef`
on all eight output buses followed by a false `Equivalence successfully proven!` — correctly
excluded. **No unsupported claim found here.**

### 1.3 Pre-route candidate B — CONFIRMED

`sta/logs/cand_b_andor.log`: L25 `STA_MEAS tag=cand_b_andor corner=ss metric=setup_wns
value=3.3607291234489214`; L26 `setup_tns value=0.0`; L16 `STA_MODE pre_route zero_wire
ideal_clock spef=NOT_READ`; L20 `STA_SDC read from …soc_ctrl.sdc (NOT modified by this harness)`.
Golden `sta/logs/golden_baseline.log` SS WNS `−1.018758439776894`. **+3.3607 is confirmed as a
pre-route estimate only.**

### 1.4 Routed candidate B — CONFIRMED (two independent harnesses)

- Runner: `physical/scratch_flow/regression/workspace/run_m1g_cand_b_route/sta/sta_summary.txt`
  L3 `SS,1,4,-1.8325,-4.6857,…` and L5 `total_violators,4`; `logs/12_sta.log` L6/L8 same.
- Independent harness: `physical/sta/logs/m1g_cand_b_route.log` L22 `setup_wns
  −1.8324693392474176`, L23 `setup_tns −4.6857221651259175`; four VIOLATED endpoints at
  L338–341 (`sum[3]` −1.8325, `sum[4]` −1.4406, `carry_out[2]` −0.7449, `sum[2]` −0.6678).
- Golden routed baseline `m1e2_phys_base` = −2.3378 / −4.0984 / 3 violators, confirmed in
  `D:\audit\m1e_stage2_evidence_20261117\physical\M1E_STAGE2_PHYSICAL_REPORT.md` L94.

**All four task-1 re-derivations PASS. No unsupported numeric claim detected.**

---

## 2. AUDIT TASK 2 — Constraints, negative controls, gate integrity

### 2.1 Constraints unchanged — CONFIRMED

Direct read/hash of `D:\audit\m1g_mapping_20261117\examples\soc_ctrl\rtl\soc_ctrl.sdc`:
SHA-256 `640071510528b53e4433e9e16f2196b624f047bf35b1979897aa23623a4f826d`; contains
`create_clock -period 10.0`, `set_input_delay … 2.0 [all_inputs]`, `set_output_delay … 2.0
[all_outputs]`, clock input delay 0.0, `set_max_transition 1.5`, `set_max_fanout 30`.
**No** `set_clock_uncertainty`, `set_clock_latency`, `set_*_derate`, `set_false_path`,
`set_multicycle_path`, or `set_data_check`. The pre-route harness in
`sta/measure_pre_route.tcl|.ps1` re-hashes to the 1F originals exactly
(`3736b03c…` / `cee27255…`), so it is a verbatim copy — no measurement-method weakening.

### 2.2 Negative controls — CONFIRMED PRESERVED

| Domain | Negative control | Result |
|---|---|---|
| Mapped LEC (rtl) | faulty sum[0] XOR→XNOR | `lec_negative_control.log`: 15/1, `equiv_status -assert` ERROR, exit 1 |
| EQY stage 03 (eqy) | corrupted netlist D(a[0])→D(_02_) | `verify_stage03_candidate.log`: positive 8/8 exit 0; negative `carry_out.0`/`sum.0` fail, exit 2 |
| Generic LEC (physical) | `sum[4]=c[4]`→`c[3]` corruption | `lec_generic_negative.log`: 27 proved / 1 unproven + ERROR, detected |
| EQY stage 03 (physical) | corrupted netlist | exit 2, rejected (physical report §2 item 4 / §3) |
| LVS comparator | synthetic suite + ambiguous/unknown tops | `pytest_test_lvs_compare.log`: 12 passed; `test_synthetic_negative_controls_preserved` + `test_ambiguous_auto_detection_is_inconclusive_not_pass` + `test_unknown_top_from_env_fails_explicitly` all PASS |

### 2.3 No INCONCLUSIVE→PASS conversion — CONFIRMED

- EQY false negative was a **structural FAIL→PASS** fix (gold-cone selection), not an
  inconclusive rescue; the negative control still FAILs. `eqy/diff.patch` and
  `eqy/test_results.txt` (8 passed) show explicit preserved-gate assertions: `if ($rcPos -ne
  0)`, `if ($rcNeg -eq 0)`, `"Successfully proved designs equivalent"`, `exit 132/133`,
  plus "must not add black-boxing" checks (`setundef -anyseq` / `(* blackbox *)` absent).
- LVS hardening (`lvs/exact_diff_and_state.txt`) **removes** the hard-coded `TOP =
  'TEST_COUNTER'`, derives top from `REG_DESIGN` → run-dir manifest → unique common root, and
  **adds** explicit `LVS_VERDICT: FAIL` / `INCONCLUSIVE` with rc=1 for unknown/ambiguous
  tops and a stricter `len(ldev) == len(sdev) > 0` topology condition. This is a
  strengthening. `published_checks.log` corroborates real matches on archived published-run
  artifacts (KLayout `INFO : Congratulations! Netlists match.`; comparator `LVS_VERDICT: PASS`
  for soc_ctrl and test_counter with env **and** manifest-only resolution).
- Physical scratch adaptations (report §2) are documented and confined outside repos;
  positive proofs (28/28 generic LEC, 10/10 EQY) and negative controls remain active.

### 2.4 Are the EQY fix and LVS hardening honest? — **YES, with disclosed caveats**

Both fixes change *how the reference is selected*, not *whether equivalence/topology is
checked*. Both keep or tighten the failing negative controls and add regression tests. The
EQY fix is logically valid because candidate≡golden is independently established (512/512
sim + 16/16 LEC); the fix deliberately does not re-prove that precondition (disclosed in
`eqy/REPORT.md` §6.1). Residual caveats are itemized in §8.

---

## 3. AUDIT TASK 3 — Pre-route optimism vs routed failure

**Gap:** +3.3607 ns (pre-route) → −1.8325 ns (routed) = **5.1932 ns**, with a sign flip from
"all paths MET, TNS 0" to "4 violating paths, TNS −4.6857".

**Assessment: the gap is legitimate physics plus a deliberately estimate-only methodology —
NOT evidence of a falsified or weakened flow.** Supported reasons:

1. **Parasitics/wireload.** Pre-route is `zero_wire … spef=NOT_READ`; the routed run extracts
   a real SPEF with 37 nets / 37 *CAP / 37 *RES (physical report §4, §5.4). The routed worst
   path traverses 6 logic cells across a 120×60 µm die; long-net RC dominates at 10 ns.
2. **Propagated clock network.** Routed STA includes **1.1952 ns** propagated clock-network
   delay on the worst path (physical report §5.2); pre-route assumes ideal clocks (0).
3. **Critical path moved.** Pre-route worst path was `cin → sum[4]`; routed worst path is a
   register-to-output path `_04_/Q → … → sum[3]`. Placement changed which path is critical —
   exactly what a zero-wire model cannot predict.
4. **Independent confirmation of the routed number.** The proven 1F Stage-2 harness
   (`physical/sta/measure.ps1|ss.tcl`) reproduced WNS/TNS to full floating-point precision on
   `11_final.v + 11_final.spef + 11_final.sdc` — this is a routed, SPEF-based measurement, not
   an estimate.
5. **No gate weakening or data selection** was found in either harness (§2). The pre-route
   STA report explicitly labels results "PRE-ROUTE / zero-wire / ideal-clock ESTIMATE — NOT
   signoff" (line 4, 16, 105) and makes "no signoff claim".

**Therefore:** the pre-route number is an honest screening estimate with a clearly stated
limitation, and the routed failure is the authoritative result for closure.

**Should pre-route ever be used as a closure claim? NO.** Closure requires routed, SPEF-based,
propagated-clock STA at the target corner with real parasitics, ideally with signoff-quality
extraction plus DRC/LVS. In this case pre-route would have been actively misleading: it
reported +3.36 ns margin where routed timing actually violates by 1.83 ns. Its legitimate use
is relative architectural screening only.

---

## 4. AUDIT TASK 4 — Success-criteria classification

| Criterion | Classification | Evidence |
|---|---|---|
| **(a) ≥1 genuinely different mapped architecture** | **MET (strong)** | 8 distinct mapped SHAs re-hashed (§1.1); candidates A/B differ from golden and each other in cell types/topology (`rtl/M1G_REPORT.md` §2, §5); physical stage-01 netlist re-hash = `f2cabe8a…`. |
| **(b) Genuine mapped LEC PASS** | **MET (strong)** | 16/16 for A and B; negative control 15/1 rejected; genuine 3-arg `equiv_make` harness; cosmetic-only renaming proven byte-identical; noprep false-PASS discarded (§1.2). Also EQY 8/8 + negative FAIL and physical 10/10 EQY + negative FAIL. |
| **(c) Measurable SS timing improvement** | **PRE-ROUTE MET / ROUTED NOT MET** | Pre-route: cand B +3.3607 vs golden −1.0188, TNS 0 — real and reproducible. Routed: cand B −1.8325 / −4.6857 / 4 violators; WNS +0.5053 vs golden **but TNS 0.5873 worse and +1 violator** → no genuine routed improvement/closure. |
| **(d) No weakened verification gates** | **MET (documented caveats)** | SDC/harness SHAs unchanged (§2.1); all negative controls preserved (§2.2); no INCONCLUSIVE→PASS (§2.3); EQY/LVS fixes strengthen or preserve gates (§2.4). Caveats in §8. |
| **(e) Independent review** | **MET** | This report re-derived all four required primary claims and adjudicated conflicts without writing to any repo. |

**Overall 1G verdict: GENUINE ROUTED TIMING CLOSURE — NO.**
Evidence: `physical/M1G_PHYSICAL_REPORT.md` §0 item 6 = **FAIL**, §3 stage 12 = FAIL,
§5 WNS/TNS table, and the two independent routed harnesses (§1.4). The milestone achieved
*different + equivalent + gate-preserving* architectures and an *estimate-only* timing win;
it did not achieve closure at the approved 10 ns constraint, and no signoff is claimed.

---

## 5. AUDIT TASK 5 — Remaining technology/tool limitation and next authorization

**Exact limitation (root cause, from primary reports):**

- `mapping/REPORT.md` §4/§7: Yosys `synth` canonicalizes any semantically equivalent RTL to
  one generic NAND/XOR/DFF network; ABC's cost-based SCL mapper (`&nf`) then picks a **unique
  cheapest majority cover** because the Sky130 HD Liberty contains an area-efficient
  `maj3_1` (`(A&B)|(A&C)|(B&C)`, area 10.0096). Result: every 4-bit-adder RTL collapses to
  `8×dfxtp_1 + 4×maj3_1 + 8×xnor2_1`, SHA `a84eb955…`, depth 4, 270.2592 µm² — this is the
  depth/area optimum; **no ABC option beats it**. `-D` has no effect; `-constr` only upsizes.
- Every deliberately different mapping is **strictly worse**: maj3_2 upsize +1.85% area at
  same depth; maj3-free NAND/NOR/AOI +8.34% area / depth 5; classic-map scripts +15.7–17.6%
  area / depth 6; delay-biased script +10.19% area / depth 9 (`mapping/REPORT.md` §5).
- `library/report.md`: the library's `maj3` is **slow** (SS ripple 7.0736 ns vs AO21 ripple
  3.6537 ns, BK prefix 2.8643 ns to cout); there is **no fast XOR3** (`xor3_1` 2.37× area and
  slower at SS); no literal `ao21/oa21` names (only `a21o/o21a`); Yosys `read_liberty` fails
  on the full Liberty (`Missing function on output GCLK of dlclkp_1`), requiring an ABC-only
  workaround; ABC's cost model is not SS-delay-driven.
- **Parasitics erase the pre-route gain**: +3.3607 → −1.8325 (5.19 ns), and candidate B's
  routed TNS/violators are worse than golden (−4.6857 vs −4.0984, 4 vs 3).

**Authorization / capability required next (any one, with trade-offs):**

1. **Commercial synthesis / technology mapping** (e.g. Design Compiler/Innovus-class) or a
   non-ABC mapper with richer timing-driven structural mapping, to escape ABC's deterministic
   canonical maj3 cover. Current toolchain is open-source Yosys 0.69 + ABC + academic
   OpenROAD.
2. **A different / enriched cell library**: faster carry cells or a genuine fast XOR3, and a
   Liberty the mainstream front ends can ingest natively. The present Sky130 HD library has
   no viable fast XOR3 and a slow maj3.
3. **Changed architecture beyond the 4-bit adder**: deeper pipelining / retiming, wider word
   width, or a structurally different datapath. Note that within the 4-bit adder the mapping
   is already depth/area optimal — a different 4-bit decomposition only costs area/depth.
4. **Constraint (authorization) change**: the approved 10 ns period / 2 ns I/O delays are the
   fixed target. Relaxing I/O delays, changing the period, or permitting legitimate timing
   exceptions would change the goalpost and requires explicit approval — it is *not* a
   technical fix. The 1F Stage-2 report reached only −0.2234 ns even with heavy physical
   tuning and names "RTL carry-chain depth or SDC I/O delays" as the needed authorization.

Also still **NOT_TESTED** for signoff: KLayout stage-14 DRC, stage-15 LVS of candidate B's
routed layout, IR/EM/antenna, congestion, and qualified parasitic extraction (QRC/StarRC).
The "clean DRC" reported is **OpenROAD router violations = 0 only**.

---

## 6. AUDIT TASK 6 — Repository integrity

Direct git/hash inspection on 2026-11-18:

| Claim | Observed | Status |
|---|---|---|
| Worktree `D:\audit\m1g_mapping_20261117` HEAD = d585055 | `d585055392a8006dc3199202a64702c60ded338c` | CONFIRMED |
| Local uncommitted **verification fixes only** | Modified: `regression/checks/lvs_compare.py`, `regression/stages/03_eqy_prep.py`, `regression/stages/03_lec_mapped.ps1`; untracked: `tests/test_eqy_override.py`, `tests/test_lvs_compare.py`. No RTL/SDC edit. | CONFIRMED |
| `D:\audit\repo` = 41728cd / 34 dirty | HEAD `41728cd8343b726b0c717df104f3ce26ade18249`; 34 porcelain entries | CONFIRMED |
| Remote = d585055 / no publication | Worktree `origin/main` = `origin/HEAD` = `d585055…` (HEAD not ahead → no push). Staged diffs **empty** in both repos; no commits made. | CONFIRMED for the worktree clone — see caveat §8.6 for `D:\audit\repo`'s remote ref. |
| Golden RTL/SDC unchanged | RTL `2a7e028d…`, SDC `64007151…` re-hashed unchanged | CONFIRMED |

No publication, commit, push, or stage was performed in this review.

---

## 7. Adjudicated conflicts

1. **STA report "all alternatives WNS≥0 / TNS 0" vs physical report "stage 12 FAIL".**
   Not a real contradiction: the former is an explicitly labeled pre-route zero-wire estimate;
   the latter is routed SPEF-based STA. Adjudication: **routed result governs closure**;
   pre-route is screening only (§3).
2. **Mapping report "golden is optimal, no alternative beats it" vs STA report "golden is the
   only SS setup failure".** Different metrics: area/depth optimum ≠ cell-delay optimum.
   `library/report.md` independently shows maj3 is the slow carry cell. Adjudication: **both
   correct**; the area/depth optimum fails the 10 ns SS setup because of maj3 cell delay,
   not because of depth.
3. **Physical report "golden routed baseline −2.3378/−4.0984" vs M1E report headline "best legal
   −0.2234".** Different runs: `m1e2_phys_base` (the baseline used for comparison) is
   −2.3378/−4.0984 (M1E report L94); −0.2234 is the heavily tuned `m1e2_phys_size_clk8`
   experiment (L101). Adjudication: **physical report's baseline comparison is correct.**
4. **Library report "4×maj3 chain is a structural baseline, not ABC's natural behavior" vs
   mapping golden netlist being 4×maj3.** Different flows (library report used a behavioral
   adder; mapping used the golden RTL/M1F recipe). The netlist under review is the verified
   4×maj3 `a84eb955…`. Adjudication: **no impact on milestone claims**; flagged as context.
5. **`12_sta.log` line `SS,1,4,…` (setup_paths=1 but setup_violated=4).** Cosmetic parser
   quirk; WNS/TNS and the 4 violators are consistent across `sta_summary.txt`, the runner
   log, the independent harness, and the report. Adjudication: **no substantive impact.**
6. **EQY report §5 "D:\audit\repo origin/main = d585055" vs observed `e04692f…`.** Imprecise:
   d585055 is the **worktree clone's** `origin/main`; the separate `D:\audit\repo` clone has
   `origin/main = origin/HEAD = e04692f…`. Adjudication: **report wording inaccurate, but
   no-publication conclusion still holds** (clean staged diffs, HEADs unchanged, worktree
   remote ref equals its HEAD). Listed in §8.

---

## 8. Unsupported / overstated claims flagged

- **F-1 — "measurable SS timing improvement" without routing qualifier (STA report).** The
  STA report's determination "Does any reach WNS≥0? YES" is true only for pre-route estimates.
  If read as a closure claim it is overstated; the report does carry explicit NOT-signoff
  disclaimers, and the physical report corrects it. **Flag: qualifier required.**
- **F-2 — LVS "genuine LVS PASS" scope.** `lvs/` validates the *comparator* and quotes
  KLayout "Netlists match" from **archived published m1d runs** (`published_checks.log`);
  it is not a fresh KLayout LVS of candidate B's routed layout (stage 15 was NOT_TESTED in
  the physical flow). **Flag: accurate for soc_ctrl/test_counter reference artifacts, not for
  candidate B's routed layout.**
- **F-3 — EQY override fix relies on an external precondition.** For the override path,
  stage 03 checks the mapped netlist against the canonical golden RTL, relying on prior
  candidate≡golden proof; a genuinely broken RTL override candidate was NOT_TESTED
  (`eqy/REPORT.md` §6.1, §6.3). **Flag: disclosed dependency, not a gate weakening.**
- **F-4 — Generic LEC negative control detects via "No SAT model available for cell u_pg3"
  rather than a clean unproven equivalence point** (`lec_generic_negative.log` tail). It still
  fails as required. **Flag: diagnostic clarity only.**
- **F-5 — EQY report integrity wording** (conflict 6 above). **Flag: minor factual imprecision.**
- **F-6 — Physical "overall determination" is about candidate B only.** Candidate A
  (`236594c3…`) was NOT_TESTED in the physical flow (report §8.6); no routed result exists for
  A. **Flag: do not generalize B's routed failure/near-win to A.**

---

## 9. Final verdict and unresolved issues

**Final verdict: 1G did NOT achieve genuine routed timing closure — NO.**
It DID achieve: (a) genuinely different mapped architectures, (b) genuine mapped LEC PASS,
(d) preserved verification gates, and (e) an independent review. Item (c) is met only as a
**pre-route estimate**; routed timing fails (WNS −1.8325, TNS −4.6857, 4 violators) and TNS
plus violator count are worse than golden.

**Unresolved / open:**
1. Routed SS closure remains open for candidate B at 10 ns (and candidate A was never routed).
2. No signoff: stage-14 KLayout DRC, stage-15 LVS of the candidate-B routed layout, IR/EM,
   antenna, congestion, and qualified extraction are NOT_TESTED.
3. The 5.19 ns pre-route→routed gap is explained (parasitics + propagated clock + path
   migration), but no physical/timing optimization was attempted to close it in 1G.
4. Items F-1…F-6 above should be corrected or qualified in any downstream summary.
5. Next-step authorization is required per §5 (commercial synthesis/mapping, different cell
   library, architecture change beyond the 4-bit adder, or an approved constraint change).

*End of independent review. No repositories were modified; no commit, push, or stage performed.*
