# Milestone 1A — Close the soc_ctrl Qualification Gap

**Date:** 2026-11-16
**Frozen foundation:** `origin/main` @ `27b5d1c7a6a3328ddde559730acbb698be623b2a` — unchanged; no commits, pushes or PRs
**Workspace:** `D:\audit\mq_pd_20261116` (fresh clone at the frozen commit, clean throughout)
**Preserved original:** `D:\audit\repo` @ `41728cd…`, 34 pre-existing changes — untouched
**Evidence:** `D:\audit\mq1a_evidence_20261116\` (`rtl\`, `constraints\`, `synthesis\`, `formal\`, `EVIDENCE_REVIEW.md`)
**User decisions honoured:** (1) locate canonical inputs first; (2) investigate and **propose** a `sum[3:0]` fix only — **no RTL modification anywhere**.

Classification vocabulary: **PASS** · **FAIL** · **BLOCKED** · **NOT_TESTED** · **NOT PROVEN**.

---

## 1. Executive summary

Milestone 1A established the facts needed to qualify `soc_ctrl`, but **soc_ctrl qualification remains BLOCKED**. The canonical inputs were located and verified, the constraint source validated, the undriven-bit defect reproduced, and a fix proposal produced — but mapped LEC is unproven and the defects require an authorization decision before any production change.

| Objective | Classification | Result |
|---|---|---|
| Locate canonical RTL/SDC | **PASS (located) / BLOCKED (published tree)** | Canonical files exist in preserved `D:\audit\repo` but are absent/untracked in published `27b5d1c` |
| Verify source files and hashes | **PASS** | All hashes independently reproduced by 5 subagents + evidence review |
| Investigate four undriven `sum[3:0]` bits | **PASS** | 4 Yosys "no driver" warnings reproduced on canonical; 0 on fixed candidates |
| Establish intended behavior | **PASS (bounded)** | Docs indicate `sum[3:0] = {fa3_sum…fa0_sum}`; formal intent still recorded UNRESOLVED; approval was limited to isolated candidates |
| Run preflight, synthesis, LEC | **PASS / PASS / BLOCKED** | 00/01/02 PASS; 03 mapped LEC FAIL/UNPROVEN; physical stages NOT_TESTED |
| Keep candidates separate from production signoff | **PASS** | Experimental candidates, Nangate cross-checks and scratch fix all labelled; no pooling of verdicts |
| Independent evidence review | **PASS** | No fabricated/unsupported claims; classifications honest |
| **soc_ctrl qualification possible now?** | **BLOCKED** | Five specific blockers in §6 |

---

## 2. Canonical inputs and constraint source

| File | Absolute path | SHA-256 | Status |
|---|---|---|---|
| Canonical RTL | `D:\audit\repo\examples\soc_ctrl\rtl\soc_ctrl.v` (4992 B, LF) | `7d3d6300002d21f5152f841abdfe1dec9a7cf290c772b543c0dbd521ef2e752a` | Matches historical Phase-15 SHA; untracked in preserved repo, absent from published commit |
| Canonical SDC | `D:\audit\repo\examples\soc_ctrl\rtl\soc_ctrl.sdc` (689 B, LF) | `2a9126e716f834f255fe358aa8962ae619ffec50418e1104690a49eda47be644` | 10.0 ns `clk`, 2 ns I/O delays, max transition 1.5 ns, fanout 30 |
| Approved isolated fix candidate | `…\candidates\phase18k_sum_output_fix\rtl\soc_ctrl.v` | `253d713be6960661ffcc05c2e2d205d825b489d43bfd2b1d65b20ed4060a3604` | Candidate only; approval explicitly limited to isolated revisions |
| Phase-22 confirmation candidate | `D:\audit\wt_phase22_B_rtl\…\phase22_soc_ctrl_connectivity_001\rtl\soc_ctrl.v` | `ac45d7cad09b4d722f3b7a707abd0d1bec7a020dc7db6368d29d204edff0461c` | Candidate only |

SDC audit: OpenSTA 3.1.0 loads the canonical SDC against a mapped netlist with 10.00 ns period / 0.00–5.00 waveform and two benign Warning 441 messages (`[all_inputs]` includes `clk`); no unconstrained endpoints. `synthesis_targets.json` is test_counter-oriented (7 ns, `datapath_design`, missing `constraints/design.sdc`) — a real config mismatch, not modified in this pass. A scratch `EXPERIMENTAL_soc_ctrl_generated.sdc` was written only in the evidence dir and is explicitly not signoff.

---

## 3. Undriven `sum[3:0]` — diagnosis and proposal

**Reproduced on canonical RTL (Yosys 0.33 and 0.69):**
```
Warning: Wire soc_ctrl.\sum [3] is used but has no driver.   (also [2], [1], [0])
Found and reported 4 problems.
```
`sum[4]` and `carry_out[2:0]` are driven. Fixed candidates (18K, Phase-22) report 0 problems. The RTL declares `output wire [4:0] sum` but assigns only `sum[4] = carry[4]`.

**Intended behavior (documented, bounded):** `{sum[3],sum[2],sum[1],sum[0]} = {fa3_sum,fa2_sum,fa1_sum,fa0_sum}`; Phase 18I records intent as UNRESOLVED and labels the implementation incomplete; Phase 18K records approval only in an isolated candidate revision; Phase 22 confirms 512/512 in a candidate. No authorization exists to change canonical production RTL.

**Proposal only:** `D:\audit\mq1a_evidence_20261116\rtl\proposed_sum_fix.patch` adds the four `assign` statements. Applied only to a scratch copy in the evidence dir, after which Yosys reports 0 problems and LEC proves 8/8 genuinely. **No repository, candidate or production RTL was modified.**

---

## 4. Preflight / synthesis / LEC on canonical inputs

Command used the direct regression entry point with read-only overrides (`-RtlOverride`/`-SdcOverride` pointing at the preserved repo, `REG_REPO` set to the clone) — see `synthesis\MQ1A_REPORT.md`.

| Stage | Result | Evidence |
|---|---|---|
| 00 preflight | **PASS** | Mandatory input checks pass with overrides; optional tool-PATH checks "not found" (tools run via OSS-CAD `environment.bat`) |
| 01 synthesis | **PASS** | 9 Sky130 cell types / 21 instances, `dfflibmap` + `abc -liberty` proven from log; no Yosys ERROR; `setundef -zero` makes `sum[3:0]=4'h0` |
| 02 generic LEC | **PASS** | 21/21 compare points proven; negative control rejected |
| 03 mapped LEC | **FAIL / UNPROVEN** | EQY: `Failed to prove equivalence for 3/8 partitions: soc_ctrl.sum.4, soc_ctrl.carry_out.2, soc_ctrl.carry_out.1` with concrete counterexamples |
| 04–15 physical | **NOT_TESTED** | Skipped by the mandatory stage-03 gate |

Acceptance: `24 passed, 1 failed, 70 not tested`; overall `INCONCLUSIVE`.

**Cause of mapped LEC failure:** ABC mapped `sum[4]`/`carry_out[2]` logic to `lpflow_inputiso1p_1` low-power isolation cells; the formal power-aware model has no combinational SAT model for them. EQY **proved** `sum.0–3`, so the undriven bits are **not** the cause. Because sby produced concrete counterexamples, this is not certified as a pure false positive — it is an unproven mapped equivalence.

---

## 5. Formal verification and the false-PASS trap

Independent Yosys LEC (recipe: `async2sync` → stash/copy-from → 3-arg `equiv_make gold gate equiv` → gate-side flatten → `equiv_simple/induct` → `equiv_status -assert`) on test_counter: positive 6/6 proven, negative rejected — harness sound.

**Canonical RTL vs its own synthesized netlist** produced the tool string `Of those cells 8 are proven and 0 are unproven. / Equivalence successfully proven!` — but the log shows:
```
Setting undriven nets to undef: \sum_gold [3:0]
sum_gold (…), sum_gate ({ \_23_.ZN_gate 4'x }) -> sum
```
This is a **vacuous/false PASS** for `sum[3:0]`: both sides are X and treated as don't-care. The correct classification is **NOT PROVEN** for those 4 points, overriding the tool's success string. Even the 4 meaningful points only prove synthesis preserved a defective RTL.

Scratch application of the proposed fix yields a genuine 8/8 proof with all 8 points driven. Approved 18K candidate vs canonical correctly yields 4 unproven sum points. Cross-comparison caveat from the evidence review: the formal subagent used a Nangate Liberty while the synthesis run used Sky130 HD — those verdicts must never be pooled into a single equivalence claim.

---

## 6. Blockers and next decisions

1. **Canonical defect formally unproven + intent unresolved** — `sum[3:0]` cannot be proven on the canonical RTL; a decision-maker must authorize the intended behavior for production.
2. **Mapped LEC unproven** — needs a corrected `lpflow_inputiso1p_1` SAT model or a mapping constraint that avoids the isolation cell; not an RTL fix.
3. **Canonical inputs untracked/absent from published commit** — cannot run the documented `soc_ctrl` flow from `27b5d1c` without supplying/verifying them.
4. **Physical stages 04–15 unrun** — blocked by stage 03.
5. **No timing signoff** — only ideal-clock, single-corner OpenSTA load check; 7 ns config vs 10 ns SDC mismatch unresolved.

No physical implementation should be attempted until (1) and (2) are resolved and the canonical inputs are made available with verified provenance. The proposed fix patch is evidence for a decision, not an applied change.

---

## 7. Evidence paths

- Consolidated independent review: `D:\audit\mq1a_evidence_20261116\EVIDENCE_REVIEW.md`
- RTL/provenance/fix proposal: `D:\audit\mq1a_evidence_20261116\rtl\` (`REPORT.md`, `proposed_sum_fix.patch`, `fix_rationale.md`, Yosys check logs)
- Constraints: `D:\audit\mq1a_evidence_20261116\constraints\MILESTONE_1A_CONSTRAINTS_AUDIT.md` (+ OpenSTA logs, EXPERIMENTAL SDC)
- Synthesis/preflight/LEC: `D:\audit\mq1a_evidence_20261116\synthesis\MQ1A_REPORT.md` (+ `regression_full.log`, `stage_status.json`)
- Formal verification: `D:\audit\mq1a_evidence_20261116\formal\REPORT.md` (+ `baseline\`, `canonical\`, `proposed_fix\`, `candidate18k\`)
- Regression run workspace: `D:\audit\mq_pd_20261116\regression\workspace\run_mq1a_canonical_synth\`
