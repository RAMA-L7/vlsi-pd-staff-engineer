# Milestone 1G — Publication Summary

**Baseline:** `d585055392a8006dc3199202a64702c60ded338c`
**Scope:** verification-infrastructure fixes plus mapping/architecture investigation evidence. This publication does **not** claim routed timing closure or design qualification.

## Published code changes

| File | Change |
|---|---|
| `regression/checks/lvs_compare.py` | Removed the last hard-coded `TOP='TEST_COUNTER'` fallback. Top cell is resolved only from `REG_DESIGN`, run-dir manifest (`<design>_design.cdl`, `acceptance.json`/`report.json`), or unique common root-cell detection. Ambiguous/unknown tops yield explicit FAIL/INCONCLUSIVE (rc=1), never PASS. All device/WL/unmatched/power/topology checks and report keys are preserved, with an added `LVS_VERDICT`. |
| `regression/stages/03_eqy_prep.py` | Accepts an optional explicit EQY gold source (6th argv, defaults to RTL for compatibility). Under the supported `-RtlOverride` flow this points EQY at the canonical promoted RTL, avoiding a partition false negative caused by output-cone differences in structurally dissimilar but equivalent candidate RTLs. |
| `regression/stages/03_lec_mapped.ps1` | Detects `-RtlOverride`, derives the canonical promoted RTL path from `REG_REPO`/`REG_DESIGN`, and passes it as EQY gold (WSL path converted). Golden/candidate positive runs and the corrupted-netlist negative control still run unchanged. |
| `tests/test_lvs_compare.py` | 12 targeted tests: soc_ctrl/test_counter via env and manifest, no-hard-coded-fallback scan, unique-root detection, unknown/ambiguous top FAIL/INCONCLUSIVE, one-sided missing top FAIL, and preservation of negative controls. |
| `tests/test_eqy_override.py` | 8 targeted tests for EQY gold selection and prep-script wiring, including override and default paths. |

## Published reports/evidence

- `docs/reviews/MILESTONE_1G_REPORT.md` — consolidated result.
- `docs/reviews/MILESTONE_1G_TECHNICAL_REVIEW.md` — independent review.
- `docs/reviews/M1G_MAPPING_ANALYSIS.md`, `M1G_LIBRARY_ANALYSIS.md`, `M1G_CANDIDATES.md`, `M1G_STA_ANALYSIS.md`, `M1G_PHYSICAL_RESULTS.md`, `M1G_EQY_FIX.md`.
- `docs/reviews/m1g_evidence/` — mapping scripts, EQY/LVS diffs, targeted test logs and genuine comparator checks needed to reproduce the findings.

## Verification performed for this publication

```
cd D:\audit\m1g_mapping_20261117
& D:\tools\venv\Scripts\python.exe -m pytest tests\test_eqy_override.py tests\test_lvs_compare.py -q -rs
  -> 20 passed in 7.07s       [m1g_evidence/published_targeted_tests.txt]
```

No full historical regression and no physical flow were repeated for publication. The routed physical result and all negative controls are preserved in the reports above.

## Key engineering result (honest status)

- ABC canonicalization root cause identified: `synth` generic NAND/XOR cover plus Sky130 HD's dedicated `maj3_1` cell gives a unique cheapest 4-level majority chain (netlist `a84eb955…`).
- Eight genuinely different mapped netlists were produced (e.g. `236594c3…`, `f2cabe8a…`, `c50de40a…`); two structural candidates proved equivalent with Sky130 HD LEC (16/16 points, negative control rejected).
- Pre-route STA showed apparent SS improvement (best candidate B +3.3607 ns vs golden −1.0188 ns), but the targeted routed flow for candidate B gave **SS WNS −1.8325 ns, TNS −4.6857 ns, 4 violators** at the unchanged 10 ns / 2 ns I/O SDC.
- **No routed timing closure. No design qualification. No foundry signoff claim.**
- Router DRC was 0 violations; academic KLayout DRC remains separate from foundry signoff (BLOCKED).

## Excluded from this publication

- All `__pycache__/`, `.pytest_cache/`, gitignored `regression/workspace/` and `regression/evidence/` artifacts.
- Scratch ODB/DEF/SPEF/netlist/log files under `D:\audit\m1g_evidence_20261117\` (large generated experiment outputs; summarized in the published reports).
- Any RTL, SDC, PDK, Liberty, synthesis_targets.json or signoff configuration change — none was made.
- No canonical RTL replacement: the promoted `soc_ctrl.v` (SHA `2a7e028d…`) and canonical 10 ns SDC remain unchanged.
