# Milestone 1B — Formal Equivalence Closure Report

**Date:** 2026-11-16
**Frozen foundation:** `origin/main` @ `27b5d1c7a6a3328ddde559730acbb698be623b2a` — unchanged; no commits, pushes or PRs
**Workspace:** `D:\audit\mq_pd_20261116` @ `27b5d1c` (clean throughout)
**Preserved original:** `D:\audit\repo` @ `41728cd…`, 34 pre-existing changes — untouched
**Evidence:** `D:\audit\m1b_evidence_20261116\` (`rtl\`, `formal\`, `library\`, `synthesis\`, `sta\`, `experimental\`, `audit\AUDIT_REPORT.md`)
**Authorization honoured:** correction applied **only** to an isolated experimental candidate in the evidence area; canonical RTL unchanged.

## 1. Final verdict — FAIL against the stated completion criteria

Milestone 1B produced a genuine, independently audited **8/8 mapped-LEC PASS for the isolated experimental candidate**, resolved the isolation-cell SAT question with real tool mechanisms, and separated libraries correctly. However, the completion criteria are **not all met**:

- the 8/8 proof applies to an **experimental candidate**, not the canonical production design;
- the timing configuration is only a **proposal** (`PROPOSAL / NOT APPROVED / NOT APPLIED`);
- production intent for `sum[3:0]` remains formally **UNRESOLVED**.

The independent auditor therefore classified the milestone **FAIL** (scope/authorization), not a technical failure of the experimental verification.

| Completion criterion | Classification | Basis |
|---|---|---|
| RTL intent documented and evidence-supported | **PASS (scoped)** | Equation `{carry[4],fa3_sum,…,fa0_sum}` strongly evidenced; production intent explicitly UNRESOLVED; approval limited to isolated candidates |
| All 8 mapped LEC points genuinely proven | **PASS experimental / FAIL production** | EQY `Successfully proved designs equivalent`, `DONE (PASS, rc=0)`, 8/8, Sky130 HD; canonical still fails 3/8 |
| No undriven signals silently converted to equivalent X values | **PASS for experimental** | Candidate drives all 8 outputs; zero X/undef markers; canonical still ties `sum[3:0]` to GROUND |
| Formal and synthesis libraries consistent | **PASS** | Sky130 HD TT (SHA `92eb4e93…`) used for both; no Nangate in the mapped-LEC closure |
| Timing constraints have an approved configuration | **FAIL** | Only `soc_ctrl_config_proposal.json` exists; no design-authority or architectural approval |
| Independent evidence review | **PASS with caveats** | Auditor verified all hashes/verdicts; flagged one subagent reporting error (see §4) |

---

## 2. Six workstream results

1. **RTL intent (PASS).** Canonical `soc_ctrl.v` SHA `7d3d6300…` unchanged; 4 Yosys "no driver" warnings for `sum[3:0]`; `fa0_sum..fa3_sum` computed (RTL L86/95/103/111) but unused; intended `sum = qa+qb+cin` established from Phase 18I/18K/Phase-22 evidence; approval explicitly "isolated candidate revision only"; patch `dec69839…` adds exactly 4 assigns and yields 0 Yosys problems on a scratch copy; 512/512 exhaustive sim corroborated.
2. **Formal closure (PASS, experimental only).** Canonical recheck (`m1b_canonical_recheck`) reproduced stage 03 FAIL: `Failed to prove equivalence for 3/8 partitions: soc_ctrl.sum.4, soc_ctrl.carry_out.2, soc_ctrl.carry_out.1`. Experimental candidate (canonical + patch, SHA `2a7e028d…`) run `m1b_exp_corrected`: all 8 partitions `Successfully proved`, negative control rejected (exit 2), `DONE (PASS, rc=0)`; false-PASS grep clean (no `unproven`, `BMC failed`, `Setting undriven nets to undef`, `4'x`); mapped netlist uses 8 `dfxtp_1`, 4 `maj3_1`, 8 `xnor2_1`, zero lpflow cells.
3. **Library modeling (PASS).** `lpflow_inputiso1p_1` has a genuine combinational function `"(A) | (SLEEP)"` in the Sky130 Liberty (L80196) — not a missing function. SAT error arises from an unflattened power-aware cell hierarchy; independent Yosys with gate-side `prep -flatten` proves 8/8. `abc -liberty -dont_use sky130_fd_sc_hd__lpflow_*` is a supported, genuine option. No SAT model was fabricated.
4. **Synthesis mapping (PASS).** Stage 01 (`01_synthesis.ps1:57-74`) never reads `synthesis_targets.json` and never passes `-dont_use` to ABC (log: `0 dont_use`); two lpflow cells appeared in the canonical netlist. Scratch `-dont_use` runs eliminate them (21 cells, 0 lpflow) with scratch equivalence 25/25. Recommended constraint change documented, **not applied**.
5. **STA/config (PASS for audit; FAIL for approval).** Confirmed 7 ns `synthesis_targets.json` vs 10 ns canonical SDC, different top module, fanout/transition divergences, missing `constraints/design.sdc`. Proposal `soc_ctrl_config_proposal.json` written (SHA `d24964a0…`) with rationale; no external 10 ns config found; OpenSTA 10 ns load verified (constraint-load only, not closure); approval explicitly required.
6. **Independent audit (PASS).** All hashes and verdicts re-derived from primary artifacts; repository integrity confirmed; final verdict FAIL only on the scope/approval criteria above.

---

## 3. False-PASS discipline demonstrated

- A prior Yosys run on canonical RTL printed "8/8 proven" while `sum[3:0]` were X on both sides — correctly reclassified **NOT PROVEN** in Milestone 1A.
- This milestone corrected that: the experimental 8/8 proof shows all 8 outputs driven by real gates, no zero-ties, no X/undef markers, and a rejected negative control.
- A genuine harness weakness was found and documented (not fixed, out of scope): `acceptance.py::all_partitions_proved` uses `npart > 0`, so the canonical run misleadingly logged "PASS 5 partitions proved"; only `designs_equivalent` caught the failure.

---

## 4. Adjudicated conflict

The synthesis workstream reported "4/8 failing partitions"; the independent auditor established the authoritative count is **3/8** from both genuine canonical positive EQY logs (`m1b_canonical_recheck\eqy\eqy_positive.log` SHA `ecda10b7…`, and the Milestone 1A log). The 4/8 figure came from `eqy\work_broken\logfile.txt`, which is the **negative control** with a deliberately corrupted netlist. Formal subagent's 3/8 is correct; canonical is defective either way.

---

## 5. Unmet criteria / next decisions

1. **Promote the fix?** The experimental correction must be promoted into canonical `soc_ctrl` with explicit production authorization, then fully re-synthesized, LEC-verified, and physically implemented — including timing closure.
2. **Ratify a soc_ctrl timing configuration**: top `soc_ctrl`, 10 ns, approved uncertainty, SDC tracked/published (SHA `2a9126e7…`), fanout/transition aligned.
3. **Resolve production intent**: authoritative spec/testbench for `sum[3:0]`; Phase 18I still records UNRESOLVED.
4. **Tooling follow-ups (out of scope here)**: wire `dont_use_cells` into stage 01; fix `acceptance.py::all_partitions_proved`; correct the synthesis workstream's 4/8 report to 3/8.

No physical implementation was attempted. The canonical RTL, preserved repo, published config and frozen commit are all unchanged.

---

## 6. Evidence paths

- Independent audit: `D:\audit\m1b_evidence_20261116\audit\AUDIT_REPORT.md`
- Formal closure: `D:\audit\m1b_evidence_20261116\formal\M1B_WORKSTREAM2_FORMAL_REPORT.md` (+ `runs\m1b_canonical_recheck\`, `runs\m1b_exp_corrected\`, EQY logs)
- RTL intent: `D:\audit\m1b_evidence_20261116\rtl\INTENT_AND_APPROVAL_SCOPE.md`
- Library findings: `D:\audit\m1b_evidence_20261116\library\LIBRARY_FINDINGS.md`
- Synthesis findings: `D:\audit\m1b_evidence_20261116\synthesis\` (+ `recommended_constraint_change.txt`, `scratch_dont_use\`)
- STA/config proposal: `D:\audit\m1b_evidence_20261116\sta\soc_ctrl_config_proposal.json`, `CONFIG_RATIONALE.md`
- Isolated experimental candidate: `D:\audit\m1b_evidence_20261116\experimental\examples\soc_ctrl\rtl\soc_ctrl.v` (SHA `2a7e028d…`) — **EXPERIMENTAL, not signoff**
