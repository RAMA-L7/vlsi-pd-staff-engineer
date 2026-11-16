# Milestone 1D — INDEPENDENT RELEASE AUDIT

**Date:** 2026-11-17
**Auditor:** independent subagent (read-only)
**Subject:** `D:\audit\m1d_promotion_20261116` @ frozen `27b5d1c7a6a3328ddde559730acbb698be623b2a`
**Output location (only write):** `D:\audit\m1d_evidence_20261116\audit\RELEASE_AUDIT.md`

## 0. Scope and integrity declaration

This audit was performed **read-only on every repository**. No file in
`D:\audit\m1d_promotion_20261116`, `D:\audit\repo`, any run workspace, or any
pre-existing evidence artifact was modified. No git write operation was performed
(no `add`/`commit`/`push`/`PR`). The only file written is this report, outside
all repositories. All hashes, statuses and verdicts below were **re-derived from
primary artifacts** (on-disk files, `report.json`, `acceptance.json`, stage logs,
patches, git object database and `git ls-remote`), not taken from the reports'
self-assertions.

**Method:** SHA-256 recomputed with `Get-FileHash`; git state read with `rev-parse`,
`status --porcelain`, `diff --cached`, `ls-tree`; remote read with `ls-remote`
(read-only); JSON parsed with `ConvertFrom-Json`; stage statuses re-derived from
`report.json.stages[*].status` and `.checks[*]`; LVS conflict adjudicated from the
KLayout log, the secondary comparator log and the comparator source code.

---

## 1. FINAL VERDICT (summary)

| # | Milestone 1D question | Classification |
|---|---|---|
| 1 | Every hash / verdict / count re-derived against primary artifacts | **PASS** (all claimed values reproduced; see §2–§4) |
| 2 | Step 5/6 `soc_ctrl` identity (promoted RTL/SDC, not `test_counter`) | **PASS** |
| 3 | Mapped LEC genuineness in both runs + acceptance.py patch exercised | **PASS** |
| 4 | LVS split adjudication | **BLOCKED / FAIL-as-run** — official KLayout PASS is genuine; framework stage 15 scored FAIL by a hard-coded-comparator defect; **not** relabelled PASS |
| 5 | Completion intent vs READY-FOR-RELEASE | Intent **PASS**; clean-release **FAIL/BLOCKED** (STA SS + LVS gate) |
| 6 | Repository integrity (worktree / repo / remote / no publication) | **PASS** |
| 7 | Unresolved issues & authorizations needed | listed in §9 |

**Bottom line:** The controlled promotion, flow integration and genuine mapped-LEC
prerequisite are **achieved within the stated boundaries** and no publication
occurred. The worktree is **NOT READY-FOR-RELEASE / NOT a clean signoff-equivalent
PASS**: SS-corner setup timing is not closed (WNS −2.3378 ns, 3 violated paths) and
the LVS acceptance gate is FAIL as-run. Both are blockers to release; neither was
fixed (out of scope).

---

## 2. Step 1 — worktree state and hashes: **PASS**

Evidence: `verify\step1_worktree_state.txt`; primary re-derivation performed live.

| Item | Claimed | Re-derived | Result |
|---|---|---|---|
| Worktree HEAD | `27b5d1c7…` | `27b5d1c7a6a3328ddde559730acbb698be623b2a` | **PASS** |
| Staged diff | empty | empty (`git diff --cached --name-only`) | **PASS** |
| Modified (7) | README + 4 docs + acceptance.py + 01_synthesis.ps1 | identical | **PASS** |
| Untracked | `examples/soc_ctrl/rtl/`, `regression/configs/` | `soc_ctrl.v`, `soc_ctrl.sdc`, `soc_ctrl_10ns.json` (directory form in report is consistent with `-uall` file form) | **PASS** |
| `soc_ctrl.v` SHA-256 | `2a7e028d…` | `2a7e028dacd97b4462d791e69e69b8ff1b2cffcf239ba31b6e968efdadb8c4be` | **PASS** |
| `soc_ctrl.sdc` SHA-256 | `2a9126e7…` | `2a9126e716f834f255fe358aa8962ae619ffec50418e1104690a49eda47be644` | **PASS** |
| `acceptance.py` SHA-256 | `79314a35…` | `79314a35ffd516fda3ce9a3358485aef0fc317fd7c30cae40c215cc797d0749e` | **PASS** |
| `01_synthesis.ps1` line 64 | `abc -liberty $Lib -dont_use sky130_fd_sc_hd__lpflow_*` | identical (line 64) | **PASS** |

## 3. Integration-report hash verification: **PASS**

| Artifact | Claim | Re-derived from primary file | Result |
|---|---|---|---|
| `D:\audit\repo\...\soc_ctrl.v` (canonical) | `7d3d6300…` | `7d3d6300002d21f5152f841abdfe1dec9a7cf290c772b543c0dbd521ef2e752a` | **PASS** |
| `D:\audit\repo\...\soc_ctrl.sdc` (canonical) | `2a9126e7…` | `2a9126e716f834f255fe358aa8962ae619ffec50418e1104690a49eda47be644` | **PASS** |
| `mq1a_evidence\rtl\proposed_sum_fix.patch` | `dec69839…` | `dec6983960b947c191335d79d5eb781f45285b275582cac3003bd9a5dfac1145` | **PASS** |
| `m1c_evidence\synthesis\01_synthesis_dont_use.patch` | `973fe422…` | `973fe4228423cd8fec23ba1b9fbeea3eb9f4af2a57314b3a90c8577f4ec192cc` | **PASS** |
| `synthesis\before.ps1` | `3090bd0d…` | `3090bd0d9f625eec79bab9990b82d29eeffdd13f6c3f131a92a6ba511fc3a0fa` | **PASS** |
| `synthesis\after.ps1` | `EDB088B0D…` | `edb088b0d9b6cf61bb4a5917717bdf9378b3594d71ce6f32f878bab25c4d` | **PASS** |
| `m1c_evidence\sdc\soc_ctrl_config_10ns.json` | `ceda602a…` | `ceda602a28ee9e712da55579a4dd30343bb0fd09f9183569fb63d993905b4a66` | **PASS** |
| worktree `regression\configs\soc_ctrl_10ns.json` | `ceda602a…` (byte-identical) | identical | **PASS** |
| `acceptance\acceptance_fixed.py` | `79314a35…` | `79314a35…` | **PASS** |
| original `acceptance.py` pre-edit | `c9d1e5c1…` | reconstructed by reverse-applying `acceptance\acceptance_git.diff` to the patched file in an isolated temp dir → `c9d1e5c1991b25459d83c3197ce4e6f4e0a7615c9e5482639f316854c933110e` | **PASS** |

**Integration content checks**

- RTL fix is exactly the approved `sum = qa+qb+cin`: `diff_canonical_vs_promoted.txt` shows
  **5 added / 0 removed** (`assign sum[3:0] = fa3_sum…fa0_sum`); worktree RTL lines
  86–126 confirm `faN_sum = qa[N]^qb[N]^carry[N]`, `carry[0]=cin`. **PASS**
- Yosys `check`: canonical log `Found and reported 4 problems` (4 undriven `sum`),
  promoted log `Found and reported 0 problems`. **PASS**
- Synthesis patch: `git diff --stat` = 1 file / 1 insertion / 1 deletion; byte-identity
  of the unpatched remainder demonstrated by reverse-apply in the synthesis report.
  **PASS**
- Acceptance patch: `git diff --stat` = `146 insertions(+), 11 deletions(-)`; old
  defective gate confirmed at `HEAD` (`check(..., npart > 0, ...)`); new helper
  `evaluate_partition_proof()` present. **PASS**
- Docs: `git diff --numstat` over the 5 owned files = 17/3/11/7/7 = **45/45**;
  `APPLY_LOG.txt` ends `TOTAL APPLIED: 45`. The reports' correction of the
  proposal's "47" to **45** is upheld. **PASS**
- SDC: `opensta_clock_verify.log` → `clk_period_ns = 10.000000`, `clk_name = clk`,
  two pre-existing `Warning 441` (input delay on clock port). Constraint-load only,
  not closure — correctly not claimed as timing signoff. **PASS**

---

## 4. Verification-report steps 1–8 re-derived

| Step | Claim | Re-derivation from primary artifacts | Result |
|---|---|---|---|
| 2 | full pytest 130 passed | `step2_pytest.txt`: `130 passed in 8.45s` | **PASS** |
| 3 | WSL framework 47/0/0 | `step3_framework_regression.txt`: `PASS: 47 / FAIL: 0 / SKIP: 0` | **PASS** |
| 4 | test_counter baseline 16/16, 92 checks | `run_m1d_test_counter\report.json`: `overall=PASS`, 16/16 stage PASS, **92 checks = 92 PASS / 0 FAIL / 0 NOT_TESTED**; manifest `status=PASS, exit_code=0` | **PASS** |
| 5 | soc_ctrl 00–03, mapped LEC | `run_m1d_soc_ctrl_stages03\report.json`: 4/4 stages PASS, `overall=INCONCLUSIVE` (70 checks NOT_TESTED, expected for partial run); EQY 8/8, negative rejected | **PASS (genuine)** |
| 6 | full physical flow | re-derived in §5 below | **FAIL / BLOCKED** |
| 7 | false-PASS scan + acceptance patch | `step7_falsepass_scan_FINAL.txt`, `step7_lecv_verdicts.txt`, `step7_fullretry_lecs.txt`, `step7_acceptance_patch_probe.txt`; all positives clean, all negatives rejected; probe reads the patched worktree file and yields PASS 8/8, FAIL 6/8 (negative), FAIL 5/8 (synthetic) | **PASS** |
| 8 | final git status unchanged | re-checked at audit end: HEAD `27b5d1c…`, staged diff empty, same 7 modified + 3 untracked; repo `41728cd…`/34 | **PASS** |

**First-attempt full-flow failure (Step 6a):** `verify\baseline_m1d_soc_ctrl_full.log:42`
shows `yosys exit=-1073741515` (= `0xC0000135` STATUS_DLL_NOT_FOUND) at generic
synthesis; `run_m1d_soc_ctrl_full\report.json` has only 2 stages, `01_synthesis`
`FAIL exit=10`, `overall=INCONCLUSIVE`. The log line
`DESIGN: soc_ctrl` / RTL `...\soc_ctrl.v` confirms even this failed attempt used the
promoted design. Environmental, not design failure — **PASS (as characterised)**.
The retry corrected PATH only; no tracked file changed.

---

## 5. TASK 2 & 3 — soc_ctrl identity, mapped LEC genuineness, full-flow re-derivation

### 5.1 soc_ctrl identity — **PASS**

`report.json.inputs` for **both** soc_ctrl runs pin the promoted files (authoritative
identity, since manifest `design_hashes` are hard-coded to `test_counter`, see §8):

- `run_m1d_soc_ctrl_stages03\report.json`: `soc_ctrl.v = 2a7e028d…`, `soc_ctrl.sdc = 2a9126e7…`
- `run_m1d_soc_ctrl_full_retry\report.json`: `soc_ctrl.v = 2a7e028d…`, `soc_ctrl.sdc = 2a9126e7…`

Baseline logs show `DESIGN: soc_ctrl`. **No run used `test_counter` for Step 5/6.**
The auditor-added `--design soc_ctrl` (NOTE A) was necessary and correct: without it
the framework defaults to `test_counter`; with it `report.json` inputs prove soc_ctrl.
**No FAIL on identity.**

### 5.2 Full-flow stage statuses re-derived from `run_m1d_soc_ctrl_full_retry\report.json` — **14/16 confirmed**

`report.json.overall = "FAIL"`; `stages` = 16. Re-derived matrix (checks PASS/FAIL per stage):

| Stage | Status | Checks |
|---|---|---|
| 00_stage00 | PASS | 4/4 |
| 01_synthesis | PASS | 8/8 |
| 02_lec_generic | PASS | 5/5 |
| 03_lec_mapped | PASS | 5/5 |
| 04_floorplan … 11_extract | PASS (all 8) | all PASS |
| **12_sta** | **FAIL** | 7 PASS / **2 FAIL** (`no_timing_violators`, `wns_non_negative`) |
| 13_gds | PASS | 7/7 |
| 14_drc | PASS | 6/6 |
| **15_lvs** | **FAIL** | 7 PASS / **7 FAIL** |

Totals: **14 PASS, 2 FAIL**; 92 checks = **83 PASS / 9 FAIL** (2 STA + 7 LVS), 0 NOT_TESTED.
The verification report's "14/16 PASS" is confirmed exactly.

- **STA (12):** `sta\sta_summary.txt` — SS `setup_wns=-2.3378`, `setup_tns=-4.0984`,
  **3 violated**, hold clean; TT `+4.3030`/0; FF `+5.0573`/0; `total_violators,3`.
  Timing is **not closed** at SS against the approved 10 ns / WNS≥0 target. **FAIL**
- **DRC (14):** `drc\drc_classification.txt` — `ACTUAL_VIOLATIONS: 0`, 15 helper
  regions excluded (`report.json` check `all_rule_categories_execute` = 295 evaluations).
  **PASS**

### 5.3 Mapped LEC genuineness — **PASS in both runs**

- Stages03 run `03_lec_mapped`: 5/5 checks PASS, including `all_partitions_proved`
  (`8/8 … zero failed/unknown/unproven`), `designs_equivalent`,
  `negative_control_rejected`, `genuine_sky130_models_used`, `official_preprocessor_used`.
- Full retry `03_lec_mapped`: same 5/5 PASS; EQY positive log ends
  `Successfully proved designs equivalent` / `DONE (PASS, rc=0)` with all 8 soc_ctrl
  partitions listed; EQY negative log `DONE (FAIL, rc=2)` with `soc_ctrl.carry_out.0`
  and `soc_ctrl.sum.0` rejected (`equivalence unknown` / `partitions not equivalent`).
- `baseline_*.log`: `EQY_POSITIVE_ACTUAL_EXIT=0`, `EQY_NEGATIVE_ACTUAL_EXIT=2` in both runs.
- Mapped netlists (`eqy\soc_ctrl_synth_eqy.v`, `synth\soc_ctrl_synth.v`): only
  `sky130_fd_sc_hd__dfxtp_1`, `__maj3_1`, `__xnor2_1`; **0 `lpflow` occurrences**.
- False-PASS scan: no residual failure markers in any positive EQY log; negative
  controls all fail; generic-LEC `unproven` appears only in pre-proof context and
  final `0 are unproven. Equivalence successfully proven!`.
- **acceptance.py patch exercised:** `report.json` check detail is the patched
  helper's exact string `8/8 partitions proved; zero failed/unknown/unproven`
  (old code emitted only `"%d partitions proved" % npart` with `npart > 0`); and
  `step7_acceptance_patch_probe.py` AST-extracts `evaluate_partition_proof()` from the
  patched worktree file, yielding PASS genuine / FAIL negative / FAIL synthetic 5/8.

**Conclusion: mapped LEC is genuine in both stage-03-only and full-flow runs; no false
PASS; no unsupported claim.**

---

## 6. TASK 4 — LVS split adjudication (do not soften a FAIL)

### Primary evidence (quoted)

1. Official KLayout LVS — `15_lvs\lvs.log` (final two lines):
   ```
   Writing LVS database: .../15_lvs/15_lvs.lvsdb ..
   Total elapsed: 1.530s  Memory: 443.00M
   INFO : Congratulations! Netlists match.
   ```
   and `logs\15_lvs.log`:
   ```
   --- 15c official sky130hd.lylvs ---
     klayout exit=0
       INFO : Congratulations! Netlists match.
   ```
   `report.json` stage 15 scores `verdict_match`, `official_deck_used`,
   `genuine_sky130_cdl_used`, `reference_from_physical_database`,
   `topcell_configured`, `lvs_database_written`, `extracted_netlist_written` all PASS.
   The reference netlist came from the routed ODB (`CDL masters parsed: 437`,
   `Instances written: 90`), the official `sky130hd.cdl` was used, `top_cell=soc_ctrl`.

2. Secondary comparator — `lvs_compare.txt` / `logs\15_lvs.log` §15d:
   ```
   layout netlist : .../15_lvs_extracted.cir
      subcircuits: 5
   schematic netlist: .../6_final_concat.cdl
      subcircuits: 438

   TOP CIRCUIT MISSING (layout=False schematic=False)
   ```
   The seven downstream `acceptance.py` checks carry literal `=?` placeholders:
   `device_counts_match`, `no_unmatched_nets`, `no_unmatched_devices`,
   `no_unmatched_subcircuits`, `power_connectivity_correct`,
   `power_structure_verified`, `independent_structure_match` — all **FAIL**.

### Root cause (decisive, from comparator source)

`regression\checks\lvs_compare.py`:
```
39: TOP = 'TEST_COUNTER'
...
407: lt = lay.get(TOP);  st = sch.get(TOP)
409: if lt is None or st is None:
410:     out.append("TOP CIRCUIT MISSING (layout=%s schematic=%s)" % ...)
415:     return 1
```
The top-cell name is **hard-coded to `TEST_COUNTER`**. The soc_ctrl extracted netlist
top is `.SUBCKT soc_ctrl VDD VNB|VSS …` (`15_lvs_extracted.cir:3`) and the reference
top is `soc_ctrl`; therefore `lay.get('TEST_COUNTER')` and `sch.get('TEST_COUNTER')`
both return `None`, the comparator returns early with status 1 and emits **no**
`DEVICES/UNMATCHED_*/POWER/WL_*` metrics. `acceptance.py:623–639` regexes then find no
matches and record `=?` → 7 FAILs. This is a **cascade from one tooling defect**, not
seven independent mismatch findings.

Corroboration: the same comparator, with the same code, **PASSES** the test_counter
baseline (`acceptance_m1d_test_counter.txt`: `no_unmatched_subcircuits PASS
unmatched subcircuits=0`; `independent_structure_match PASS Weisfeiler-Lehman
device-colour classes matched=242 of 242/242`). The failure tracks the design name,
not the netlists.

The `5 vs 438 subcircuits` figure is also non-comparable: the KLayout extracted
`.cir` is a **flat transistor-level** netlist (top + 4), while the reference CDL is
**hierarchical** with 438 Sky130 cell masters. The `VNB|VSS` ground name is a
PDN-derived combined net; `canon()` in the comparator explicitly handles `|`-joined
power nets, so it is not itself a mismatch.

### Adjudication

- **Official tool verdict:** **genuine PASS.** KLayout `sky130hd.lylvs` is the
  authoritative Sky130 LVS signoff engine; it matched genuine, physically-derived
  netlists with exit 0.
- **Framework / regression gate verdict:** **FAIL as-run** (stage 15). I do **not**
  relabel this to PASS.
- **Adjudicated classification: BLOCKED / FAIL-as-run, false negative.** The weight
  of primary evidence shows the stage-15 FAIL is caused by the hard-coded
  `TOP = 'TEST_COUNTER'` comparator bug, not by an LVS mismatch. But because the
  gate is red and the comparator never produced comparison metrics, LVS **cannot be
  called clean by the regression gate as-run**. It requires a design-agnostic fix to
  `lvs_compare.py` and a re-run before LVS can be recorded as a clean PASS.

Per instruction, the FAIL is **not softened**: stage 15 remains FAIL/BLOCKED in the
release ledger.

---

## 7. TASK 5 — completion intent and READY-FOR-RELEASE classification

The user's boundaries define Milestone 1D intent as: **controlled promotion +
flow integration**, **genuine mapped LEC before physical**, **no publication**.

| Boundary / intent item | Status | Basis |
|---|---|---|
| Approved changes promoted into controlled worktree only | **PASS** | RTL `2a7e028d…` (sum=qa+qb+cin), SDC `2a9126e7…` (10 ns), dont_use patch, acceptance patch, 10 ns config (reference-only), 45 doc corrections — all present, uncommitted |
| Genuine mapped LEC before physical flow | **PASS** | Step 5 8/8 genuine, negative rejected, zero false-PASS, Sky130 HD only; full flow run only after Step 5 passed |
| Flow integration executed | **PASS (evidence produced)** | 16 stages ran on retry; GDS/mapped/SPEF/DEF artifacts with hashes |
| No publication | **PASS** | staged diff empty, no commit, remote `27b5d1c…` |
| **Clean signoff-equivalent / READY-FOR-RELEASE** | **FAIL / BLOCKED** | STA SS setup unclosed (WNS −2.3378, 3 violated); LVS gate FAIL as-run (§6) |

**Decision:** The worktree **satisfies the controlled-promotion and
integration intent** and is a legitimate, honest evidence package. It is
**NOT READY-FOR-RELEASE**: STA SS closure and the LVS acceptance FAIL are release
blockers. Publication was neither performed nor authorized, and the promoted
RTL/config are absent from the published commit (§8).

---

## 8. TASK 6 — repository integrity: **PASS**

| Check | Result |
|---|---|
| Worktree HEAD | `27b5d1c7a6a3328ddde559730acbb698be623b2a` (frozen) — **PASS** |
| Staged diff | empty — **PASS** |
| New commits | none (`git log`/HEAD unchanged) — **PASS** |
| `D:\audit\repo` HEAD | `41728cd8343b726b0c717df104f3ce26ade18249` — **PASS** |
| `D:\audit\repo` changed-path count | **34** — **PASS** |
| Remote (live read-only `git ls-remote origin HEAD refs/heads/main`) | both `27b5d1c7a6a3328ddde559730acbb698be623b2a` — **PASS** |
| Promoted files in published tree | `git ls-tree HEAD -- examples/soc_ctrl/rtl/soc_ctrl.v|soc_ctrl.sdc, regression/configs/soc_ctrl_10ns.json` → **absent** (only `candidates/phase18p_carry_00{1,2}` exist) — **PASS** |
| Final status re-check (end of audit) | unchanged from Step 1 — **PASS** |

**Provenance quality issue (not an identity FAIL):** the manifest `design_hashes`
field is hard-coded to `test_counter` (`rtl=b5a84c11…`, `sdc=b36b66b…`; verified to be
the actual test-counter hashes). For the soc_ctrl runs the authoritative identity is
`report.json.inputs`, which is correct (§5.1). Flagged, not a blocker to the identity
conclusion, but it must be fixed before manifests are trusted as provenance.

**Tool-version note:** manifests report `yosys live='<unavailable>'` (version probe
empty); logs show Yosys 0.69+154. Provenance-quality only. **NOT_TESTED** for a
clean version-pin attestation.

---

## 9. TASK 7 — unresolved issues and authorizations required before publication

| # | Issue | Classification | Authorization/decision needed |
|---|---|---|---|
| 1 | STA SS setup WNS −2.3378 ns, TNS −4.0984, 3 violated paths | **FAIL / BLOCKED** | Decision on timing closure approach and authority to modify RTL/constraints/physical flow, then re-run STA to WNS≥0 at SS |
| 2 | LVS stage 15 `FAIL` as-run (hard-coded `TOP='TEST_COUNTER'` in `lvs_compare.py:39`; metrics `=?`) | **BLOCKED / FAIL-as-run** | Authority to fix the comparator to derive top cell from the design, re-run LVS, and record a clean gate PASS; official KLayout PASS stands meanwhile |
| 3 | Publication / commit / push / PR | **NOT AUTHORIZED / NOT PERFORMED** | Explicit release authority; none granted in this milestone |
| 4 | `soc_ctrl_10ns.json` is reference-only, not wired into the runner | **BLOCKED (by design)** | Separate approved change-control to wire the config and supersede the 7 ns config |
| 5 | Nine un-enumerated "RTL+SDC created"/"Locally created" doc strings remain (`docs\M1D_DOCS_REPORT.md` §2c) | **BLOCKED** | Proposal augmentation; task forbade improvising beyond the 45-row table |
| 6 | Manifest `design_hashes` hard-coded to test_counter; yosys version probe empty | **NOT_TESTED / quality** | Authority to fix provenance reporting before manifests are used as release evidence |
| 7 | OSS-CAD `lib/` PATH non-determinism caused first full-flow `yosys` DLL failure | **NOT_TESTED (environment)** | CI/environment fix for deterministic PATH; retry succeeded with PATH correction only |
| 8 | Windows Yosys 0.69 absent; verification used WSL Yosys 0.33 | **NOT_TESTED** | Decision whether WSL 0.33 is acceptable for signoff attestation (M1C already accepted it) |

No code, config or comparator fix was attempted during this audit, per read-only scope.

---

## 10. Evidence index (primary artifacts consulted)

- `m1d_evidence_20261116\verify\VERIFICATION_REPORT.md`, `step1_worktree_state.txt`,
  `step2_pytest.txt`, `step3_framework_regression.txt`, `step4_test_counter_baseline.txt`,
  `step5_soc_ctrl_stages03.txt`, `step6_soc_ctrl_full.txt`, `step6_soc_ctrl_full_RETRY.txt`,
  `step7_*.txt/.py`, `baseline_m1d_{test_counter,soc_ctrl_stages03,soc_ctrl_full,soc_ctrl_full_retry}.log`
- Worktree run data (gitignored, read-only):
  `regression\workspace\run_m1d_{test_counter,soc_ctrl_stages03,soc_ctrl_full,soc_ctrl_full_retry}\report.json`,
  `acceptance.json`, `eqy\eqy_{positive,negative}.log`, `synth\soc_ctrl_synth.v`,
  `sta\sta_summary.txt`, `sta\sta_all.log`, `drc\drc_classification.txt`,
  `15_lvs\lvs.log`, `15_lvs\15_lvs_extracted.cir`, `lvs_compare.txt`, `logs\15_lvs.log`,
  `soc_ctrl_pdn.gds`, `11_final.spef`, `10_route.def`
- Source/patches: `regression\checks\acceptance.py`, `regression\checks\lvs_compare.py`,
  `regression\stages\01_synthesis.ps1`, `regression\configs\soc_ctrl_10ns.json`,
  `m1c_evidence_20261116\...` patches/config, `mq1a_evidence_20261116\rtl\proposed_sum_fix.patch`
- Integration reports: `rtl\REPORT.md`, `synthesis\report.md`, `acceptance\REPORT.md`,
  `sdc\m1d_sdc_integration_report.md`, `docs\M1D_DOCS_REPORT.md`
- Prior basis: `m1c_evidence_20261116\MILESTONE_1C_REPORT.md` (approved decisions:
  `sum = qa+qb+cin`, 10 ns; 7 ns rejected)

**END OF AUDIT — no repository was modified; no publication performed.**
