# Milestone 1C — Production Qualification Readiness Report

**Date:** 2026-11-16
**Frozen foundation:** `origin/main` @ `27b5d1c7a6a3328ddde559730acbb698be623b2a` — unchanged; no commits, pushes or PRs
**Workspace:** `D:\audit\mq_pd_20261116` @ `27b5d1c` (clean throughout)
**Preserved original:** `D:\audit\repo` @ `41728cd…`, 34 pre-existing changes — untouched
**Evidence:** `D:\audit\m1c_evidence_20261116\` (`rtl\`, `formal\`, `synthesis\`, `sdc\`, `acceptance\`, `docs\`, `audit\RELEASE_AUDIT.md`)
**Approved decisions:** `sum = qa+qb+cin`; clock period **10 ns** (7 ns explicitly rejected). No agent chose either value.

## 1. Final verdict — FAIL (release scope)

Milestone 1C produced a fully prepared, independently verified production-readiness package, but **does not pass the completion criteria**. The blockers are structural consequences of the imposed boundaries (no production edits, no commits/pushes): the canonical RTL is still defective, canonical mapped LEC still fails, the SDC/config cannot be tracked, and the flow/gate patches are prepared but not applied.

| Completion criterion | Classification | Basis |
|---|---|---|
| Approved RTL behavior | **FAIL (release) / decision PASS** | Approved intent implemented in the isolated promotion candidate (`2a7e028d…`); canonical `7d3d6300…` still defective and unchanged |
| Genuine canonical mapped LEC | **FAIL** | Canonical still fails 3/8 (`sum.4`, `carry_out.2`, `carry_out.1`); genuine 8/8 proof applies only to promotion candidate |
| Approved and tracked SDC | **BLOCKED** | 10 ns approved and proposal pinned to `2a9126e7…`; SDC untracked/absent from `27b5d1c`; tracking requires forbidden commits |
| Consistent Sky130 HD library usage | **PASS** | Sky130 HD TT (SHA `92eb4e93…`) for synthesis and formal; zero Nangate hits |
| Correct synthesis `dont_use` handling | **FAIL (unmet) / fix BLOCKED** | Gap confirmed; one-line patch prepared and verified; not applied |
| Acceptance logic rejects incomplete proofs | **FAIL (unmet) / fix BLOCKED** | `npart > 0` false-PASS confirmed; fixed copy + 197-line patch + 11 passing tests; not applied |
| Independent audit passes | **PASS (audit executed), milestone FAIL** | Auditor verified evidence and confirmed the blockers |

---

## 2. Seven subagent results

1. **RTL intent/spec (PASS).** Approved equation documented in `rtl\APPROVED_INTENT_SPEC.md`; promotion candidate verified 0 undriven problems vs canonical's 4 warnings; diff = exactly 5 added lines (comment + 4 assigns); canonical hash unchanged.
2. **Formal (PASS, promotion only).** Stage 00–03: promotion run `m1c_promotion_formal` all PASS; EQY positive exit 0 with all 8 partitions proved; negative control exit 2; zero false-PASS markers; mapped netlist 3 types/20 instances (8 `dfxtp_1`, 4 `maj3_1`, 8 `xnor2_1`), 0 lpflow, 0 undriven. Canonical recheck still 3/8 FAIL. Stages 04–15 NOT_TESTED.
3. **Synthesis (PASS).** Stage 01 (`:63-64`) never applies `dont_use_cells`; one-line local patch `01_synthesis_dont_use.patch` prepared, `git apply --check` clean, canonical scratch mapping drops 2 lpflow cells; no-op on already-clean promotion candidate; not applied.
4. **SDC (PASS, tracking BLOCKED).** Canonical SDC SHA `2a9126e7…` 10 ns authoritative; published config SHA `0ad1e5d6…` 7 ns rejected; `soc_ctrl_config_10ns.json` proposal labelled `APPROVED VALUE 10 ns / PROPOSAL NOT APPLIED`; OpenSTA 10.00 ns verified (constraint-load only).
5. **Acceptance (PASS).** `regression/checks/acceptance.py:253-257` `npart > 0` false-PASS confirmed; corrected local copy + patch; 11 tests reject 5/8 and 7/8, accept genuine 8/8; not applied pending release authority.
6. **Docs reconciliation (PARTIAL).** Milestone status matrix, 4/8→3/8 correction, and doc correction proposals prepared. Independent audit found the summary claim "47 proposed line replacements" unsupported — tables enumerate **45** (25 + 20); substance is correct, applied = 0.
7. **Independent release audit (PASS as audit).** All hashes/verdicts re-derived; repository integrity confirmed; verdict FAIL as above; audit saved at `audit\RELEASE_AUDIT.md`.

---

## 3. Key technical results

- **Genuine promotion proof:** EQY `Successfully proved designs equivalent`, `DONE (PASS, rc=0)`, 8/8 partitions, negative control 2/8 rejected, 16/16 strategy statuses PASS, zero forbidden markers, Sky130 HD throughout. This is the strongest result in the milestone — but it is evidence for the **promotion candidate**, not production.
- **Canonical remains honest baseline:** 4 undriven `sum[3:0]`, 3/8 mapped LEC failure from the authoritative positive EQY log. Not fixed, not softened.
- **False-PASS discipline:** the earlier vacuous "8/8" (X-on-both-sides) and the `npart > 0` gate weakness are both documented; the acceptance fix is prepared but awaits sign-off.
- **No physical implementation:** stages 04–15 NOT_TESTED in all runs; no GDS/DEF/SPEF artifacts produced.

## 4. Unmet items / required authorizations

1. Apply the approved `sum[3:0]` fix to canonical production RTL, then re-run full synthesis/LEC/physical/timing closure.
2. Commit the 10 ns SDC (`2a9126e7…`) and design-specific config, superseding the 7 ns config — requires change control.
3. Apply the `dont_use` flow fix (stage 01) and decide config wiring / `dfflibmap` gap.
4. Apply the acceptance-gate fix with release-authority sign-off.
5. Apply the 45 documented corrections (and fix the "47" count in the proposal summary).
6. Authorize physical implementation if signoff is ultimately required.

Until these authorizations exist, all remain BLOCKED and Milestone 1C remains FAIL. No boundary was relaxed.

## 5. Evidence paths

- Independent audit: `D:\audit\m1c_evidence_20261116\audit\RELEASE_AUDIT.md`
- Approved spec: `D:\audit\m1c_evidence_20261116\rtl\APPROVED_INTENT_SPEC.md`
- Formal reports/runs: `D:\audit\m1c_evidence_20261116\formal\M1C_WORKSTREAM2_FORMAL_REPORT.md`, `formal\runs\run_m1c_promotion_formal\`, `formal\runs\run_m1c_canonical_recheck\`
- Synthesis patch: `D:\audit\m1c_evidence_20261116\synthesis\01_synthesis_dont_use.patch`
- 10 ns config package: `D:\audit\m1c_evidence_20261116\sdc\soc_ctrl_config_10ns.json`, `soc_ctrl_authoritative_sdc_package.md`
- Acceptance patch/tests: `D:\audit\m1c_evidence_20261116\acceptance\acceptance_false_pass.patch`, `test_output.log`
- Reconciliation: `D:\audit\m1c_evidence_20261116\docs\QUALIFICATION_STATUS.md`, `REPORT_CORRECTION_3of8.md`, `DOC_CORRECTION_PROPOSAL.md`
- Promotion candidate (evidence area only): `D:\audit\m1c_evidence_20261116\promotion\examples\soc_ctrl\rtl\soc_ctrl.v` (SHA `2a7e028d…`)
