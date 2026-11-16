# Milestone 1 — Multi-Design Qualification Report

**Date:** 2026-11-16
**Stable reference:** `origin/main` @ `27b5d1c7a6a3328ddde559730acbb698be623b2a` (unchanged; no commits, pushes or PRs)
**Qualification clone:** `D:\audit\mq_pd_20261116` — fresh clone at the stable commit, clean tree before and after all work
**Evidence:** `D:\audit\mq_evidence_20261116\` (`physical_flow\`, `synthesis_liberty\`, `lec\`, `rtl_investigation\`, `EVIDENCE_AUDIT.md`)
**Method:** five independent subagents (RTL investigation, physical flow, synthesis/Liberty, formal/LEC, evidence audit), orchestrated by the main agent; no production RTL/SDC/PDK/signoff data modified.

Classification vocabulary: **PASS** · **FAIL** · **BLOCKED** (missing input/access) · **NOT_TESTED**.

---

## 1. Executive summary — objective NOT fully achieved

| Milestone 1 objective | Classification | Result |
|---|---|---|
| Run complete flow on `test_counter` | **PASS** | 16/16 stages PASS, 95/95 checks, genuine EDA logs, full physical closure |
| Audit `soc_ctrl` independently | **BLOCKED** (published docs: **FAIL**) | Canonical RTL/SDC absent from published tree; preflight fails, 15 stages NOT_TESTED |
| Investigate undriven `sum[3:0]` | **PASS** | Historical defect reproduced; fix proposals require approval; **not applied to production RTL** |
| Validate synthesis / LEC / floorplan / PDN / placement / CTS / routing / STA | **PASS for test_counter only** | All stage families PASS with measured QoR; soc_ctrl stage families NOT_TESTED |
| Capture genuine EDA reports and execution evidence | **PASS** | Scripts, logs, netlists, reports and manifests archived outside the repo |
| **Multi-design qualification achieved** | **NOT ACHIEVED** | Only one production design was qualified; the second is blocked by missing design inputs |

The assistant demonstrably **diagnoses and verifies a real design end-to-end** (test_counter), and correctly **blocks and reports** a design whose mandatory inputs are absent rather than fabricating a PASS. It cannot yet claim multi-design qualification: a second production design must be supplied and authorized.

---

## 2. test_counter — production flow PASS

Command:
```
cd D:\audit\mq_pd_20261116
& D:\tools\venv\Scripts\python.exe -m assistant baseline --run-id mq_test_counter --timeout 9000 --out D:\audit\mq_evidence_20261116\physical_flow
```
Result: `exit_code 0`, `return_code 0`, `status PASS`, `stages_recorded 16`, `notes []`; regression `ACCEPTANCE: 95 passed, 0 failed, 0 not tested` (92 stage checks + 3 manifest checks); `overall PASS`; `git_head=27b5d1c…`, `git_branch=main`.

| Stage family | Status | Key measured evidence |
|---|---|---|
| 00 preflight | PASS | 4/4 checks |
| 01 synthesis | PASS | 13 Sky130 cell types / 20 instances mapped (`dfflibmap` + `abc -liberty`); generic netlist 6 internal types; 0 `$`-cells in mapped |
| 02 generic LEC | PASS | 6/6 compare points proven; negative control 1 unproven, rejected by `equiv_status -assert` |
| 03 mapped LEC (EQY) | PASS | 3/3 partitions proved; negative control rejected |
| 04 floorplan | PASS | 13 pins, 66 well ties, DEF/ODB written |
| 05 placement | PASS | 20 cells placed, `check_placement` clean |
| 06 CTS | PASS | 4 sinks, clock tree built, clock report emitted |
| 07 PDN | PASS | VDD/VSS geometry in SPECIALNETS, ODB/DEF written |
| 08 power connectivity | PASS | `POWER_PINS VDD=113 VSS=113 BAD=0`, 67/67 well ties connected |
| 09/10 routing | PASS | guide written; detailed router 0 violations |
| 11 extraction | PASS | SPEF 30 nets, 30 `*CAP`, 30 `*RES` |
| 12 STA (TT/SS/FF, SPEF) | PASS | 30 paths/corner, **0 violated**; FF setup WNS 5.7003, SS 2.6745, TT 5.4550; TNS 0.0; hold WNS positive at all corners |
| 13 GDS | PASS | 15 stdcells merged, 538 vias, 382 route shapes, 15 ports labelled |
| 14 DRC | PASS | 295 rule-category evaluations, **0 actual violations** |
| 15 LVS | PASS | official deck, verdict **match**, layout=240 / schematic=240 devices, 0 unmatched |

Views: generic + mapped netlists (sha256 `3148c5c3…` / `e1e8d19f…`), routed DEF, SPEF, GDS. Genuine stage logs (floorplan 1.9 KB, detailed route 13.7 KB, DRC 65 KB, STA 34 KB, LVS 5.9 KB, `acceptance.json` 15.6 KB, `report.json` 76 KB) archived in `regression/workspace/run_mq_test_counter\`.

---

## 3. soc_ctrl — BLOCKED, honestly reported

- The regression config (`run_regression.ps1:119-120`) requires `examples/soc_ctrl/rtl/soc_ctrl.v` and `.sdc`. The published commit contains **no** `examples/soc_ctrl/rtl/` directory and **no** `.sdc` under `examples/soc_ctrl` — only two candidate RTLs plus READMEs (`git ls-tree -r HEAD -- examples/soc_ctrl` = 4 blobs).
- Documented CLI `python -m assistant baseline … --design soc_ctrl` fails at argparse (`unrecognized arguments: --design soc_ctrl`, exit 2); the direct module `python -m assistant.baseline --design soc_ctrl` is the working entry point (CLI ergonomics gap).
- Using the correct entry point: `exit_code 1`, `status INCOMPLETE`, `overall INCONCLUSIVE`; stage 00 preflight reports `mandatory_failures=2` — `rtl_exists:soc_ctrl.v` and `sdc_exists`; all 15 downstream stages are skipped / NOT_TESTED.
- README/docs claiming canonical `soc_ctrl` RTL/SDC exist are **inaccurate** in this commit.
- No workaround was applied: no canonical inputs invented, no tracked config or RTL edited.

**Required to proceed:** (a) commit authoritative canonical `soc_ctrl.v` + `.sdc` with provenance, or (b) explicit written authorization to use a labelled candidate via `-RtlOverride` plus a generated SDC as an *experimental* input — never as a baseline claim; plus an intent decision on `sum[3:0]` and run authorization.

---

## 4. Undriven `sum[3:0]` investigation — PASS (diagnosis only)

- Historical evidence: Phase-15 canonical RTL (SHA `7d3d6300…`, present only in `D:\audit\repo`) had `sum[0]`–`sum[3]` with no driver; `fa0_sum..fa3_sum` were computed but unused. Generic netlist produced `4'hx`, mapped netlist constant 0, EQY gold X; Yosys later collapsed them to GROUND → routing DRT-0305. `sum[4]` and `carry_out[2:0]` were driven.
- Independent reproduction: Yosys reported exactly 4 warnings `Wire soc_ctrl.\sum [3]…[0] is used but has no driver` / `Found and reported 4 problems` on the canonical RTL; the external approved fix candidate gives 0 problems.
- Fix history: Phase 18I proposal (`sum[3]=fa3_sum … sum[0]=fa0_sum`) was **proposal only, approval required**; Phase 18K records approval **in an isolated candidate revision only** (SHA `253d713b…`); Phase 22 independently confirmed 512/512. These fixed RTLs are **not** in the published commit.
- The two candidates that *are* published drive `sum[3:0]` (structural sanity 0 problems; exhaustive 512/512 functional), but they are experimental candidates, not canonical production RTL. **No production RTL was modified.**

---

## 5. Secondary findings (for future milestones, not fixed here)

| # | Finding | Classification | Milestone |
|---|---|---|---|
| 1 | CLI `baseline` subcommand lacks `--design` (only direct module has it) | FAIL — usability | 4 |
| 2 | `assistant/baseline.py` `design_hashes.rtl/sdc` always hash test_counter regardless of `--design` | FAIL — provenance bug | 2 |
| 3 | README/docs stale: "88/88 checks", canonical soc_ctrl inputs claimed present | FAIL — documentation | 2/4 |
| 4 | `synthesis_targets.json` `func_ss` points to unavailable `ss_100C_1v60` (1.60V/100C); only local slow corner is `ss_n40C_1v40` (1.40V/−40C) | documented gap, still inconsistent | 2 |
| 5 | Candidate README hashes match LF-normalized bytes, not CRLF working files | FAIL — hash provenance | 2 |
| 6 | 3× `sky130_fd_sc_hd__lpflow_isobufsrc_1` isolation cells in baseline mapped netlist; eight Liberty isolation-cell functions uninvestigated | OBSERVED | 2 |
| 7 | Stale pre-remediation `lec_debug/lec.log` (4 unproven) sits beside passing LEC evidence; missing archived `logs\02/03_lec*.log` cited by report | FAIL — evidence hygiene | 2 |
| 8 | `keep_hierarchy` trap in candidate LEC (needs `setattr -unset keep_hierarchy`; `-noattr` unsupported in Yosys 0.69); candidate conclusion depends on scratch cell models | OBSERVED | 2 |

---

## 6. Evidence paths

- Evidence audit (consolidated adjudication): `D:\audit\mq_evidence_20261116\EVIDENCE_AUDIT.md`
- Physical flow: `D:\audit\mq_evidence_20261116\physical_flow\` (`cli_test_counter.txt`, `cli_soc_ctrl.txt`, `baseline_manifest_test_counter.json`, `baseline_manifest_soc_ctrl.json`, `test_counter_summary.txt`, `experimental_cand002_synth\`)
- Run workspaces: `D:\audit\mq_pd_20261116\regression\workspace\run_mq_test_counter\`, `…\run_mq_soc_ctrl\`
- Synthesis/Liberty: `D:\audit\mq_evidence_20261116\synthesis_liberty\` (4 `.ys`, 4 `.log`, 4 `.v`)
- Formal/LEC: `D:\audit\mq_evidence_20261116\lec\` (scripts, logs, stdout, netlists, mutated RTL)
- RTL investigation: `D:\audit\mq_evidence_20261116\rtl_investigation\` (`REPORT.md`, Yosys/iverilog logs)

---

## 7. Conclusion and next step

The supervised assistant **diagnoses and verifies a real design end-to-end** and **correctly blocks** an unqualified design instead of producing false evidence. Multi-design qualification is **not achieved** because the second design's authoritative inputs are absent from the repository and the `sum[3:0]` intent is unresolved. The stable commit `27b5d1c` remains untouched. Recommended next step: obtain explicit authorization/design inputs for a canonical `soc_ctrl` (or a clearly-labelled experimental candidate run), then re-run Milestone 1 for that design before starting Milestone 2.
