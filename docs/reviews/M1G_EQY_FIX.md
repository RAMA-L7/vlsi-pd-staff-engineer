# Milestone 1G — Verification Engineer report: stage-03 EQY `-RtlOverride` false-negative

**Date:** 2026-11-17  **Role:** Verification Engineer (subagent). Read-only on all repositories except the three owned stage files + one new test file. No commit / push / stage.
**Worktree:** `D:\audit\m1g_mapping_20261117` @ `d585055392a8006dc3199202a64702c60ded338c` (unchanged).
**Evidence writes only:** `D:\audit\m1g_evidence_20261117\eqy\`.

## Executive verdict

| Item | Verdict |
|---|---|
| 1. Inspect scripts + reproduce the false negative | **PASS** — reproduced deterministically (3/8 partitions, `rc=2`) |
| 2. Root-cause (no assumption) | **PASS** — gold/gate source selection; other four candidates ruled out with evidence |
| 3. Minimal correction, gates preserved, new tests | **PASS** — 2 small edits + 1 new test file; 8/8 tests pass |
| 4a. Golden RTL → stage 03 positive | **PASS** — `DONE (PASS, rc=0)`, 8/8 partitions |
| 4b. Candidate RTL override → stage 03 positive | **PASS** — `DONE (PASS, rc=0)`, 8/8 partitions (override detected, canonical gold selected) |
| 4c. Corrupted netlist negative control → FAIL | **PASS** — `DONE (FAIL, rc=2)`, 2/8 partitions fail in both runs |
| 5. Scope / integrity / no publication | **PASS** — RTL+SDC unchanged, repo untouched, HEAD + remotes unchanged |
| Unresolved / disclosed | see §6 |

## 1. Reproduction of the false negative

Scratch reproduction reusing the genuine-PDK prepared EQY inputs from the preserved 1F run (all invocations via the real `03_eqy_lf.py` LF wrapper and the OSS CAD environment). Full logs: `repro/repro_*.log`; consolidated quotes: `reproduce_false_negative.txt`.

- **Candidate RTL as EQY gold** (reproduced raw override): `EXIT=2`,
  `Warning: Failed to prove equivalence for 3/8 partitions:` →
  `Failed to prove equivalence of partition soc_ctrl.sum.4` /
  `soc_ctrl.carry_out.2` / `soc_ctrl.carry_out.1` → `DONE (FAIL, rc=2)`.
  Identical to the preserved 1F primary log `stage03_fail_eqy_positive.log`.
- **Canonical golden RTL as EQY gold:** `EXIT=0`, all 8 `Successfully proved equivalence of partition ...`, `DONE (PASS, rc=0)`.
- **Golden gold + corrupted netlist:** `EXIT=2`, `Failed to prove equivalence for 2/8 partitions` (`soc_ctrl.sum.0`, `soc_ctrl.carry_out.0`), `DONE (FAIL, rc=2)` — harness still sensitive.

## 2. Root cause — GOLD/GATE SOURCE SELECTION

Full writeup with `file:line` references: `rootcause.txt`. Summary:

- `run_regression.ps1:132-133` overwrites `$Rtl` with the override candidate; `03_lec_mapped.ps1:32` passes `REG_RTL_WSL` to `03_eqy_prep.py:20`; `03_eqy_prep.py:318` writes it as `[gold]`. So the stock override asks EQY to prove *candidate RTL == mapped netlist*.
- Preserved `work/matched.ids` shows **only top-level ports** matched (no internal match points) → EQY partitions per output.
- `work/partitions/soc_ctrl.sum.4.sv` has a free miter input `\__pi_carry_out__2` wired to both gold and gate. The candidate (Kogge-Stone, `candidates/kogge_stone/soc_ctrl.v:56,63`) computes `sum[4] = c[4]` in parallel and **ignores** it (hence `stage03_fail_eqy_positive.log`: `Partition soc_ctrl.sum.4 contains 1 unused gold inputs`); the byte-identical ripple/maj3 mapped netlist **uses** it. With the input free, SAT=unknown / SBY="partitions not equivalent" — a structural false negative, not a design defect. The golden RTL (`examples/soc_ctrl/rtl/soc_ctrl.v:114,121`) derives `sum[4]` from the carry chain, matching the netlist cone → 8/8 PASS.

**Ruled out with evidence:** `formal_pdk.v` preprocessing (byte-identical `051C0DE6…` in both fail and pass runs); `keep_hierarchy` (absent; `[script]` identical); wrong top (`detected top module: soc_ctrl`; all partitions `soc_ctrl.*`); unsupported model (cells `dfxtp_1/maj3_1/xnor2_1`, previously proved 92/92 by Yosys LEC).

## 3. Exact diff (also in `diff.patch`)

`regression/stages/03_eqy_prep.py` — accept optional 6th arg `EQY_GOLD`, use it for `[gold]`, default to `RTL`:
```
+EQY_GOLD = sys.argv[6] if len(sys.argv) > 6 and sys.argv[6].strip() else RTL
 ...
-""" % {'rtl': to_windows(RTL), 'gate': to_windows(gate), 'top': TOP}
+""" % {'rtl': to_windows(EQY_GOLD), 'gate': to_windows(gate), 'top': TOP}
 NEG = POS.replace(to_windows(gate), to_windows(broken))
+print('EQY gold source: %s' % EQY_GOLD)
```
`regression/stages/03_lec_mapped.ps1` — on override, derive canonical promoted RTL from `REG_REPO` + `REG_DESIGN`, pass its WSL path as 6th prep arg (33 added lines; full text in `diff.patch`). All existing gates (`prep rc`, `rcPos != 0`, `rcNeg == 0`, `Successfully proved designs equivalent`, `exit 132/133`) and the negative-control generation are untouched. `03_eqy_lf.py` unchanged.

**Test count:** `tests/test_eqy_override.py` — **8 passed** (`pytest ... -q`: `........ 8 passed in 3.63s`; `test_results.txt`). Tests: explicit gold honoured; default/blank fallback; negative-control gate still corrupted while gold unchanged; PowerShell block switches to canonical on override; no-override keeps RTL; gates-not-weakened guard; candidate/golden SHA pin.

## 4. Targeted verification — corrected stage 03 (full logs `verify_stage03_*.log`)

| Case | `EQY_POSITIVE_ACTUAL_EXIT` | positive verdict | `EQY_NEGATIVE_ACTUAL_EXIT` | negative verdict | stage exit |
|---|---|---|---|---|---|
| (a) golden RTL | 0 | `Successfully proved designs equivalent`; `DONE (PASS, rc=0)`; 8/8 | 2 | `Failed to prove equivalence for 2/8 partitions` → `DONE (FAIL, rc=2)` | 0 |
| (b) candidate override | 0 | `Successfully proved designs equivalent`; `DONE (PASS, rc=0)`; 8/8 | 2 | `Failed to prove equivalence for 2/8 partitions` (`sum.0`, `carry_out.0`) → `DONE (FAIL, rc=2)` | 0 |

For (b) the stage logged: `-RtlOverride detected: EQY gold = canonical promoted RTL: D:\audit\m1g_mapping_20261117\examples\soc_ctrl\rtl\soc_ctrl.v` and prep logged `EQY gold source: /mnt/d/audit/m1g_mapping_20261117/examples/soc_ctrl/rtl/soc_ctrl.v`. Per-run artifacts under `verify_golden/eqy/` and `verify_candidate/eqy/` (eqy_prep.log, eqy_positive.log, eqy_negative.log, generated `.eqy` configs).

No checks were skipped, no cells black-boxed, no INCONCLUSIVE converted to PASS: the negative control still **fails** with a non-zero exit in both runs.

## 5. Integrity / scope (`integrity.txt`, `baseline.txt`)

- Worktree HEAD `d585055…` unchanged; last commit unchanged. RTL SHA `2a7e028d…`, SDC SHA `64007151…` — unchanged.
- Files I modified: `regression/stages/03_eqy_prep.py`, `regression/stages/03_lec_mapped.ps1`; created `tests/test_eqy_override.py`. (`03_eqy_lf.py` untouched — hash identical to baseline.)
- Pre-existing/parallel changes **not mine**: `M regression/checks/lvs_compare.py`, `?? tests/test_lvs_compare.py` (present by the time my baseline was captured; another subagent's LVS work). I did not touch them.
- `D:\audit\repo`: HEAD `41728cd…`, 34 status entries — identical to session start. `origin/main`/`origin/HEAD` still `d585055…` → no push. No commit or stage performed.

## 6. Unresolved / disclosed issues

1. **Precondition relied upon:** candidate≡golden is established by prior Milestone-1F evidence (512/512 sim, 92/92 LEC, byte-identical mapped netlists), not re-derived by stage 03. The task states this as the precondition; the fix deliberately does not duplicate that proof (a MINIMAL correction). Consequence: for the override path, stage 03 checks the mapped netlist against the canonical golden spec rather than against the candidate RTL — intentional, and still rejects a genuinely broken candidate/netlist.
2. **Maintenance note:** the PS1 derives the canonical RTL from a two-entry design→`rtl_rel` map mirroring `run_regression.ps1`'s `DesignConfig`. A future design added to the runner must be added there too; unknown designs fall back to the original `REG_RTL` behaviour (no false-negative fix, but no regression).
3. **NOT_TESTED:** full regression suite, other designs, other `-RtlOverride` candidates (brent_kung / behavioral_add), and a genuinely *broken* RTL override candidate — out of scope by task instruction (targeted verification only).

## 7. Evidence index

`D:\audit\m1g_evidence_20261117\eqy\`:
`baseline.txt`, `reproduce_false_negative.txt`, `rootcause.txt`, `diff.patch`, `test_results.txt`, `verify_stage03_golden.log`, `verify_stage03_candidate.log`, `integrity.txt`, `REPORT.md`; setup/driver scripts `repro_setup.ps1`, `repro_run.ps1`, `verify_stage03.ps1`, `integrity_check.ps1`; raw EQY logs `repro/repro_*.log` and prepared configs `repro/*.eqy`; end-to-end run artifacts `verify_golden/eqy/*`, `verify_candidate/eqy/*`.
Primary 1F inputs (read-only): `D:\audit\m1f_evidence_20261117\physical\stage03_fail_eqy_{positive,negative}.log`, `...physical\runs\run_m1f_cand_base\eqy\work\*`, candidate RTL `...rtl\candidates\kogge_stone\soc_ctrl.v`.
