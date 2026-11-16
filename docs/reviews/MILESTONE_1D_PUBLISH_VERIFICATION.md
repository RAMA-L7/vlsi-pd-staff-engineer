# Milestone 1D — Publish Verification Summary (2026-11-17)

**Source commit:** `27b5d1c7a6a3328ddde559730acbb698be623b2a` (frozen historical reference, preserved)
**Publish worktree:** fresh clone of `origin/main` at `27b5d1c`, with the reviewed M1D changes applied locally and committed by this publication.
**Evidence:** `D:\audit\m1d_publish_evidence_20261117\` (outside the repository).

## Published scope

- Approved `soc_ctrl` arithmetic promotion (`sum = qa+qb+cin`) at `examples/soc_ctrl/rtl/soc_ctrl.v` (SHA `2a7e028dacd97b4462d791e69e69b8ff1b2cffcf239ba31b6e968efdadb8c4be`).
- Canonical 10 ns SDC (`2a9126e716f834f255fe358aa8962ae619ffec50418e1104690a49eda47be644`) and design-specific reference config `regression/configs/soc_ctrl_10ns.json` (10.0 ns; 7 ns explicitly not approved). The config is a reference artifact; the runner does not consume it yet.
- Synthesis `dont_use` fix in `regression/stages/01_synthesis.ps1` (`abc -liberty $Lib -dont_use sky130_fd_sc_hd__lpflow_*`).
- Acceptance false-PASS fix in `regression/checks/acceptance.py` (partition proof requires the full expected partition set; incomplete proofs are FAIL/INCONCLUSIVE).
- README + architecture/design/regression/validation documentation corrections (45 enumerated replacements) and 6 new regression guards in `tests/test_m1d_integration.py`.
- Milestone 1/1A/1B/1C/1D reports and the M1D independent release audit under `docs/reviews/`.

## Verification commands and results

```
cd D:\audit\publish_m1d_20261117
& D:\tools\venv\Scripts\python.exe -m pytest tests\ -q -rs
  -> 136 passed                                   [pytest.txt]

wsl -d Ubuntu-24.04 -- bash -lc "cd /mnt/d/audit/publish_m1d_20261117 && bash examples/test-counter/test_framework.sh"
  -> PASS: 47 / FAIL: 0 / SKIP: 0                  [framework.txt]

& D:\tools\venv\Scripts\python.exe -m assistant baseline --run-id pub_m1d_test_counter --timeout 9000 --out <evidence>
  -> status PASS, return_code 0, 16/16 stages      [baseline_test_counter.txt]

& D:\tools\venv\Scripts\python.exe -m assistant.baseline --design soc_ctrl --run-id pub_m1d_soc_ctrl_lec --stages 00_stage00,01_synthesis,02_lec_generic,03_lec_mapped --timeout 9000 --out <evidence>
  -> stages 00-03 all PASS; EQY positive 8/8 partitions, DONE (PASS, rc=0);
     negative control rejected; patched acceptance reports "8/8 partitions proved; zero failed/unknown/unproven"
     [soc_ctrl_lec_cli.txt, run workspace eqy logs]

& D:\tools\venv\Scripts\python.exe -m assistant.baseline --design soc_ctrl --run-id pub_m1d_soc_ctrl_full --timeout 9000 --out <evidence>
  -> 14/16 stages PASS; overall FAIL
     [soc_ctrl_full_cli.txt]
```

## Honest qualification status (preserved, not softened)

| Check | Result |
|---|---|
| test_counter full flow | **PASS** — 16/16 stages, 95/95 checks |
| soc_ctrl mapped LEC (EQY, Sky130 HD) | **PASS** — 8/8 partitions genuinely proved, negative control rejected |
| soc_ctrl STA | **FAIL** — SS setup WNS −2.3378 ns, TNS −4.0984, 3 violated paths; TT +5.0573 ns and FF clean |
| soc_ctrl DRC | **PASS** — 0 actual violations |
| soc_ctrl LVS (gate as-run) | **FAIL** — official KLayout verdict genuinely PASS (`Congratulations! Netlists match`), but secondary comparator fails because `regression/checks/lvs_compare.py:39` hard-codes `TOP = 'TEST_COUNTER'` |
| soc_ctrl full physical qualification | **NOT CLAIMED** |

The STA failure and the LVS comparator failure are preserved as evidence. No SS timing closure is claimed, and the LVS gate failure is not presented as a design mismatch.

## Out-of-scope (not included)

Unfinished Milestone 1E fixes (SS timing closure, `lvs_compare.py` top-name fix, config wiring), experimental candidate RTL outside this worktree, canonical baseline copies in `D:\audit\repo`, and temporary/generated artifacts (gitignored workspace/evidence directories).
