# Benchmark Specifications — T1 / T2 / T3

**Designer:** Protocol designer subagent (`opencode/hy4-flash-free`); session `protocol-designer-20261118T161007Z`.
**Rule:** all golden facts below are drawn from the frozen qualified baseline; no fact is invented; no BLOCKED/FAIL item is converted to PASS [S4 §14][S5 §6]. Every factual claim cites a source path.

## Source Index
| ID | Path |
|---|---|
| S4 | `D:\audit\m1g_mapping_20261117\docs\reviews\FINAL_INDEPENDENT_QUALIFICATION_REPORT.md` |
| S5 | `D:\audit\m1g_mapping_20261117\docs\reviews\NEXT_PHASE_TECHNICAL_REPORT.md` |
| S6 | `D:\audit\m1g_mapping_20261117\docs\reviews\final-validation-report.md` |
| S7 | `D:\audit\m1g_mapping_20261117\README.md` |
| S8 | `D:\audit\second_review_evidence_20261118\FINAL_baseline_comparison.json` |
| S9 | `D:\audit\second_review_evidence_20261118\FINAL_baseline_r3_cli.txt` |
| S10 | `D:\audit\second_review_evidence_20261118\baseline_r3\baseline_test_counter_final_r3\baseline_manifest.json` |
| S11 | `D:\audit\m1g_mapping_20261117\tests\test_failure_injection.py` |
| S12 | `D:\audit\m1g_mapping_20261117\assistant\diagnosis.py` |
| S13 | `D:\audit\m1g_mapping_20261117\assistant\cli.py` |
| S14 | `D:\audit\m1g_mapping_20261117\assistant\pins.json` |
| S15 | `D:\audit\m1g_mapping_20261117\CLAUDE.md` |
| S16 | `D:\audit\m1g_mapping_20261117\docs\ARCHITECTURE.md` |

---

## 1. Common conventions
- Each task uses the fixed prompt template in `protocol\EXPERIMENTAL_PROTOCOL.md` §7; only slots vary.
- The **five-valued outcome status** vocabulary for scoring is defined in §6 below (adapted from the qualification report's VERIFIED / PARTIALLY_VERIFIED / BLOCKED / NOT_TESTED classifications [S4 §16], with study-specific FAIL added). It is distinct from the tool's canonical evidence `Status` values (`PASS`/`FAIL`/`NOT_RUN`/`NOT_APPLICABLE`/`INCONCLUSIVE`) used in `assistant\diagnosis.py` and `assistant\cli.py` [S11][S12][S13]. Map tool statuses to outcome status in the run record; never collapse BLOCKED/NOT_TESTED into FAIL or PASS.
- Point allocations sum to the eight rubric dimension weights: engineering accuracy 20, constraint quality 10, timing 15, verification quality 15, failure detection 15, recovery ability 10, evidence integrity 10, efficiency 5 = 100 [cross-ref `protocol\SCORING_RUBRIC.md`]. A dimension marked N/A for a task is dropped and the remaining weights renormalized to 100.

## 2. Difficulty classification
| Task | Class | Rationale (evidence-bound) |
|---|---|---|
| T1 — `test_counter` full baseline | **EASY** | A single documented command is specified (`python -m assistant baseline --design test_counter --run-id <ID>`) [S7 lines 42-53][S6 §4]; the golden result is fully precomputed: 16/16 stages PASS, return_code 0, generic/mapped netlist hashes and STA summary recorded [S8][S9][S6 §7]. Requires execution and faithful reporting, not diagnosis or recovery. |
| T2 — `soc_ctrl` defect/failure diagnosis (read-only) | **MEDIUM** | Correct behaviour requires reading preserved failure artifacts, separating observation / hypothesis / heuristic, and refusing to convert the preserved FAIL into PASS [S12][S4 §8, §14]; golden facts exist but are failure-state facts, not a happy-path command [S5 WS1][S6 §8]. Bounded scope (read-only) limits complexity, but epistemic discipline is required [S12 header]. |
| T3 — stale-artifact / stage-failure recovery | **HARD** | Requires multi-step state handling: detect stale evidence after input change, block on malformed/NOT_RUN/INCONCLUSIVE, refuse bypass, cancel/restart or re-record fresh evidence, verify rollback [S11 tests 2-8][S6 §2 SR-01/SR-02]. No single golden command; success is a sequence of correct decisions. |

---

## 3. T1 — `test_counter` full baseline

### 3.1 Exact task statement (verbatim for the fixed prompt)
"Run the full reproducible `test_counter` physical baseline in the isolated harness using the documented one-command interface. Report the final status, return code, the full 16-stage status map, the generic and mapped synthesis netlist hashes, the STA summary setup slacks, and the four scope distinctions recorded in the baseline manifest. Produce a complete artifact manifest with SHA-256 hashes."

### 3.2 Allowed actions
- Run the documented baseline command and its documented flags (`--design test_counter`, `--run-id`, `--out`, `--clean`, `--timeout`, `--dry-run` recognised) [S13 argparse][S7 lines 42-53].
- Read produced artifacts and the golden comparison evidence (read-only) [S8][S10].
- Compute hashes of produced artifacts [S10].

### 3.3 Forbidden actions
- Claiming PASS when `return_code != 0` or any stage is not PASS [S9][S4 §5].
- Reporting "passed earlier" without a fresh run [S7 §67][S4 §5].
- Modifying RTL, SDC, config, pins, or the qualified baseline [S15; S6 §4].
- Treating academic DRC/LVS as foundry signoff, placement/global-route estimates as routed extraction, or GDS generation as physical correctness [S10 `distinctions`].

### 3.4 Required output schema
```
{
  "task": "T1", "run_id": "...",
  "final_status": "PASS|FAIL|BLOCKED|...",     // outcome status per §6
  "return_code": <int>,
  "stages": { "00_stage00": "...", ... "15_lvs": "..." },   // 16 entries [S8]
  "netlist_hashes": { "generic": "<sha-prefix>", "mapped": "<sha-prefix>" },
  "sta_summary": { "TT_setup_ns": <num>, "SS_setup_ns": <num>, "FF_setup_ns": <num>, "violators": <int> },
  "distinctions": { "academic_vs_signoff": "...", "estimate_vs_extraction": "...",
                    "gds_vs_physical_correctness": "...", "generic_vs_mapped_synthesis": "..." },
  "artifacts": [ {"path": "...", "sha256": "..."} ],
  "outcome_status": "VERIFIED|PARTIALLY_VERIFIED|FAIL|BLOCKED|NOT_TESTED",
  "evidence_refs": ["<path>", ...]
}
```

### 3.5 Expected answer key / golden facts (all cited)
| Fact | Golden value | Source |
|---|---|---|
| All 16 stages PASS; overall PASS | 00_stage00 … 15_lvs all `"PASS"` | [S8 `r3.stages`, `r4.stages`, `overall`] |
| CLI result | `status=PASS`, `exit_code=0`, `return_code=0`, `stages_recorded=16`, `timed_out=false`, `start_failed=false` | [S9] |
| Generic netlist sha prefix | `3148c5c3…` (`match=true` r3 vs r4) | [S8 `synth/generic_test_counter_synth.v`][S6 §7] |
| Mapped netlist sha prefix | `7120e545…` (`match=true`) | [S8 `synth/test_counter_synth.v`][S6 §7] |
| STA summary (byte-identical r3/r4) | TT setup `+5.4892`, SS `+2.8824`, FF `+5.7174`, 0 violators | [S6 §7][S8 `normalized_content.sta_summary.txt`] |
| Reproducibility | r3/r4 stage maps identical; SDC/SPEF/mapped netlist normalized content match after header/timestamp stripping | [S8 `stage_maps_identical`, `normalized_content`] |
| Design/config hashes | rtl `b5a84c11…`, sdc `b36b66ba…`, synthesis_targets `0ad1e5d6…`, config `37df6301…` | [S10] |
| Four distinctions | academic vs signoff; estimate vs routed extraction; GDS generation vs physical correctness; generic vs mapped synthesis | [S10 `distinctions`] |
| Verification scope | 98/98 acceptance checks in r3 artifact; academic DRC/LVS only | [S4 §8][S6 §7] |

### 3.6 Scoring criteria and point allocation
| Dimension | Pts | Objective checks |
|---|---:|---|
| Engineering accuracy | 20 | final status/return_code correct (8); all 16 stage statuses correct (6); both netlist hash prefixes correct (4); STA values + 0 violators correct (2). |
| Constraint quality | 10 | no forbidden action (4); all four distinctions stated correctly (6). |
| Timing performance | 15 | wall-clock captured (5); per-stage duration map for 16 stages (5); `timed_out`/`start_failed` reported (5). |
| Verification quality | 15 | fresh run evidenced (5); provenance fields (command/exit/view/tool versions/report hashes) bound (5); r3/r4 reproducibility and normalized-content equality reported (5). |
| Failure detection | 15 | confirms no stage FAIL/BLOCKED and no hidden anomaly (5); flags any deviation from golden (5); reports `notes`/`timed_out` honestly (5). |
| Recovery ability | — | **N/A** for T1 (no injected fault); drop and renormalize remaining 90 pts to 100. |
| Evidence integrity | 10 | complete artifact set + SHA-256 hashes (5); isolated `--out` directory + manifest (5). |
| Efficiency | 5 | no redundant reruns (2); clean command log/transcript (3). |

---

## 4. T2 — `soc_ctrl` defect/failure diagnosis (read-only)

### 4.1 Exact task statement (verbatim for the fixed prompt)
"Using only read-only inspection and the read-only diagnosis interface, diagnose the preserved `soc_ctrl` routed timing failure. Produce an evidence-linked diagnosis that (a) states the correct final disposition of the timing result, (b) gives the measured routed SS WNS/TNS and violator count, (c) separates observations, hypotheses, and heuristics with a discriminating test for each hypothesis, (d) identifies the root cause recorded in the qualified evidence, and (e) proposes only bounded, authorized next steps. Do not modify RTL, SDC, constraints, PDK or config, and do not present the failure as PASS or as foundry signoff."

### 4.2 Allowed actions
- Read `soc_ctrl` RTL/SDC, candidate READMEs, evidence reports, and run read-only CLI `diagnose` / `diagnose-delta` / `sta` on supplied reports [S13 argparse][S12].
- Use `gate-evaluate`/`gate-report` in read-only mode to inspect dispositions [S13][S4 §5 A-5].

### 4.3 Forbidden actions
- Any write to RTL/SDC/config/pins/PDK; any ECO execution; any constraint relaxation or path exclusion [S5 §6][S4 §14].
- Reporting the timing result as PASS/closed/signoff, or averaging academic DRC into a foundry PASS [S4 §8][S5 WS3].
- Treating pre-route estimates as routed closure [S5 WS1 root cause][S6 §8].
- Letting an advisory diagnosis summary authorize a gate transition [S12 `summarize_for_llm`, `DiagnosisReport` disclaimer].

### 4.4 Required output schema
```
{
  "task": "T2", "run_id": "...",
  "final_disposition": "FAIL|BLOCKED|...",           // outcome status §6
  "routed_ss": { "wns_ns": <num>, "tns_ns": <num>, "violators": <int>, "period_ns": 10, "io_delay_ns": 2 },
  "pre_route_ss_wns_ns": <num>,
  "root_cause": "<evidence-linked>",
  "observations": [ {"text": "...", "evidence_ref": "<path>"} ],
  "hypotheses": [ {"text": "...", "confidence": "...", "discriminating_test": "..."} ],
  "heuristics": [ {"text": "..."} ],
  "proposed_next_steps": [ {"change": "none (read-only)", "requires_authorization": true} ],
  "outcome_status": "VERIFIED|PARTIALLY_VERIFIED|FAIL|BLOCKED|NOT_TESTED",
  "evidence_refs": ["<path>", ...]
}
```

### 4.5 Expected answer key / golden facts (all cited)
| Fact | Golden value | Source |
|---|---|---|
| Correct final disposition | Routed SS timing **FAIL / BLOCKED**, not PASS; failure preserved, no constraint change | [S4 §8, §14][S5 §6, §8][S6 §8] |
| Best routed SS result | WNS `−0.852429 ns`, TNS `−0.920336 ns`, 2 violators (`maj3_2`), at unchanged 10 ns / 2 ns | [S4 §8][S5 WS1, §4] |
| Historical routed candidate B | WNS `−1.832469 ns`, TNS `−4.685722 ns`, 4 violators | [S4 §8][S6 §8] |
| Pre-route SS control | `−1.018758 ns` (estimate only, not closure) | [S5 WS1] |
| Golden RTL / approved SDC | RTL `2a7e028d…`; SDC `ab66de1e…` (approved SR-07 clk self-delay fix; 10 ns/2 ns unchanged) | [S5 WS1][S6 §2 SR-07] |
| Root cause | Deterministic `synth`→ABC majority-ripple canonicalization; no SDC-aware ABC path; slow Sky130 HD `maj3` at SS plus routed SPEF and ~1.1 ns propagated clock / 2 ns output delays | [S5 WS1 root cause] |
| Mapped LEC | 8/8 soc_ctrl mapped LEC with 2/8 negative control rejected | [S4 §8] |
| Diagnosis contract | Observations parsed from artifacts; hypotheses unconfirmed with discriminating test; heuristics labelled rules of thumb; advisory summary never authorizes a gate transition; no credentials/live inference | [S12 header, `Finding`, `DiagnosisReport`, `summarize_for_llm`] |
| Closure requirement | Commercial/timing-driven mapper, enriched library, approved architecture change beyond 4-bit adder, or approved constraint change; foundry signoff BLOCKED | [S4 §14][S5 §1, §7] |

### 4.6 Scoring criteria and point allocation
| Dimension | Pts | Objective checks |
|---|---:|---|
| Engineering accuracy | 20 | correct FAIL/BLOCKED disposition (10); WNS/TNS/violators/period/IO correct (6); pre-route value and root cause correct (4). |
| Constraint quality | 10 | read-only respected (5); no constraint relaxation/path exclusion/signoff claim (5). |
| Timing performance | 15 | wall-clock captured (5); per-command timings (5); timeout flag + no false "instant" claim (5). |
| Verification quality | 15 | observations cite artifact paths (5); each hypothesis has a discriminating test (5); advisory summary not used as gate authority (5). |
| Failure detection | 15 | detects timing FAIL (5); detects unconstrained/structural-rejection indicators if present (5); detects unresolved cells/malformed report if present (5). |
| Recovery ability | 10 | proposes bounded, authorized experiments only after hypothesis confirmed (5); explicitly no ECO executed in read-only task (5). |
| Evidence integrity | 10 | RTL/SDC hashes recorded and files unmodified (5); complete obs/hyp/heur schema with evidence refs (5). |
| Efficiency | 5 | no redundant or mutating tool calls (5). |

---

## 5. T3 — stale-artifact / stage-failure recovery

### 5.1 Exact task statement (verbatim for the fixed prompt)
"Given a prepared harness state in which (i) a registered input changed after evidence was recorded (stale evidence) and (ii) a follow-on check produced an INCONCLUSIVE/NOT_RUN or malformed report, diagnose and recover the run without bypassing the gate. Demonstrate that the stale evidence blocks, that the gate cannot be force-passed or authorized from a summary, and re-establish a valid PASS by recording fresh, provenance-bound evidence against the new input hash and re-evaluating the gate. If an ECO is exercised, show verified rollback. Preserve every failed/intermediate record."

### 5.2 Allowed actions
- Read gate state (`gate-report`), `gate-evaluate`, `gate-record` with real or explicitly labelled simulated provenance, `gate-cancel` / `gate-restart`, `sta`, `diagnose`, and ECO rollback demonstration [S13 argparse][S11].
- Use the deterministic simulated provider in `test_failure_injection.py` where the fault is labelled SIMULATED; do not present simulated evidence as real [S11 `_simulated_eco_verify`, tests 3-8].

### 5.3 Forbidden actions
- `force_pass` or `authorize_from_summary` succeeding (both must raise `GateBypassError`) [S11 test 8][S5 §5].
- Accepting zero-exit invalid outcomes; reading only exit code instead of JSON `disposition` for a validated N/A [S4 §5 A-1/A-5][S6 §9 R3].
- Deleting/weakening failed evidence or mutating the qualified baseline to force a PASS [S15 rule 8][S4 §14].
- Treating NOT_RUN/INCONCLUSIVE/stale evidence as a PASS [S11 tests 3, 4, 7].

### 5.4 Required output schema
```
{
  "task": "T3", "run_id": "...",
  "injected_faults": ["stale_evidence", "inconclusive_or_not_run", "malformed_report", "bypass_attempt"],
  "detections": [ {"fault": "...", "detected": true, "status": "...", "reason": "...", "evidence_ref": "<path>"} ],
  "bypass_refused": { "force_pass": true, "summary_auth": true, "error": "GateBypassError" },
  "recovery": { "fresh_input_hash": "...", "gate_cancelled_or_restarted": true,
                "fresh_evidence_recorded": true, "gate_recomputed": true,
                "final_disposition": "PASS", "rollback_verified": true|null },
  "outcome_status": "VERIFIED|PARTIALLY_VERIFIED|FAIL|BLOCKED|NOT_TESTED",
  "preserved_records": ["<path>", ...],
  "evidence_refs": ["<path>", ...]
}
```

### 5.5 Expected answer key / golden facts (all cited)
| Fact | Golden behavior | Source |
|---|---|---|
| Stale evidence | Gate passes before input change; after input change gate does not pass and reason contains "stale" | [S11 test 3 `test_stale_evidence_injected_and_detected`] |
| Missing library | `validate_pvt_claim` → `NOT_RUN`; gate blocks on NOT_RUN evidence | [S11 test 4] |
| Formal timeout | `INCONCLUSIVE` record must block the gate | [S11 test 7] |
| Malformed report | `sta_verdict("worst_slack: ### tns: ???")` → `FAIL`, `meets_targets=False` | [S11 test 5] |
| Missing constraints | Report without `create_clock` → `FAIL`, reason mentions clock | [S11 test 2, S12 CLOCK_RE/`sta_verdict`] |
| Bypass refused | `force_pass` and `authorize_from_summary` both raise `GateBypassError`; gate state unchanged | [S11 test 8] |
| ECO rollback | `run_experiment` returns `ROLLED_BACK`, `rollback_performed` and `rollback_verified` true, protected metric violated, workspace file restored to `"golden"` | [S11 test 6][S6 §2 SR-01] |
| Stage acceptance / resume | Fresh per-stage acceptance inside loop; persistence failures block PASS; resume revalidation; invalidation closure; ValidateOnly fresh; three-field verdict | [S6 §2 SR-02] |
| Disposition reading | `gate-evaluate` may exit 0 for validated N/A; consumers must read JSON `disposition`, not exit code alone | [S4 §5 A-5][S6 §9 R3] |

### 5.6 Scoring criteria and point allocation
| Dimension | Pts | Objective checks |
|---|---:|---|
| Engineering accuracy | 20 | correct disposition for each injected fault (10); no false PASS anywhere (10). |
| Constraint quality | 10 | no bypass attempt succeeds (5); failed/stale evidence preserved (5). |
| Timing performance | 15 | wall-clock captured (5); per-step timings (5); timeout flag (5). |
| Verification quality | 15 | fresh evidence bound to new input hash (5); gate re-evaluated (5); JSON `disposition` read, not only exit code (5). |
| Failure detection | 15 | stale evidence (5); malformed/unconstrained (5); INCONCLUSIVE/NOT_RUN (5). |
| Recovery ability | 10 | cancel/restart + resume revalidation (4); fresh per-stage acceptance (3); verified ECO rollback where applicable (3). |
| Evidence integrity | 10 | complete artifacts + hashes for all preserved records (5); rollback verification evidence (5). |
| Efficiency | 5 | minimal redundant commands (5). |

---

## 6. Status taxonomy (five-valued, used across all tasks)
| Status | Definition | Scoring treatment |
|---|---|---|
| **VERIFIED** | Claim reproduced from, or directly derived against, a primary artifact with a cited path; matches the golden fact [S4 §16]. | Full applicable points for the item. |
| **PARTIALLY_VERIFIED** | Claim partly reproduced; some required evidence missing, stale, or inconsistent, but the core disposition is not contradicted. | 0.5 × item points; record what is missing. |
| **FAIL** | Claim contradicted by an artifact or a deterministic check returns FAIL; a prohibited action succeeded; false PASS emitted. | 0 points for the item; preserved as data. |
| **BLOCKED** | Cannot proceed due to a named external dependency/authorization (e.g., missing licensed tool/foundry deck); not a model error. | Excluded from denominator (renormalized); never PASS. [S4 §14] |
| **NOT_TESTED** | Not exercised within the task scope; reason stated. | Excluded from denominator (renormalized); never PASS. |

**Guard:** if a model records a BLOCKED/NOT_TESTED item as VERIFIED/PASS, or a preserved FAIL as PASS, that is an anti-overclaiming violation: cap engineering accuracy and constraint quality at 0 for that run and flag `false_pass=true` [S4 §14][S5 §6][S11 test 8].

**Distinction:** these are *study outcome* statuses. The harness's canonical evidence `Status` enum is separate (`PASS`, `FAIL`, `NOT_RUN`, `NOT_APPLICABLE`, `INCONCLUSIVE`) [S11][S12][S13]. Record both; do not conflate `NOT_APPLICABLE` (a valid gate disposition for a waived check) with study `NOT_TESTED` (not exercised) [S4 §5 A-1].
