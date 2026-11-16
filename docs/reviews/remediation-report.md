# Remediation Report — VLSI PD Staff Engineer Framework

**Date:** 2026-11-16
**Checkout:** fresh clone of `RAMA-L7/vlsi-pd-staff-engineer` (`main` @ `e04692f2`), working tree `D:\audit\wt_pd_assistant`
**Preserved original:** `D:\audit\repo` untouched (HEAD `41728cd8…`, 34 changes) — the review target was revalidated against it before remediation
**Source review:** `docs/reviews/initial-review.md` (findings A–N) · **Tracker:** `docs/reviews/remediation-tracker.md`
**Evidence directory:** `D:\audit\pd_assistant_evidence_20261116\` (17 artifacts, referenced below)

---

## 1. Executive summary

All 14 findings (A–N) were revalidated against the checkout and then remediated:

- **Foundation repaired and executed:** `examples/test-counter/test_framework.sh` went from an immediate CRLF crash (exit 2) to **47 PASS / 0 FAIL / 0 SKIP** with technology-mapped synthesis, a sound LEC (positive + negative control) and an STA verdict that separates "ran" from "meets targets".
- **Executable gate supervision implemented:** new `assistant/` package (13 modules) provides deterministic, evidence-bound gate control (6 statuses, input-hash staleness, view ladder, scoped/recorded/visible waivers, N/A rule registry, cancel/restart, bypass refusal), Liberty PVT validation against actual headers, read-only diagnosis with observation/hypothesis/heuristic separation, and controlled ECO experiments (checkpoint, one bounded change, protected metrics, rollback, authorization, 3-failure reassessment).
- **Reproducible physical baseline:** `python -m assistant baseline` ran the 15-stage OpenROAD regression twice — **16/16 stages PASS both runs**, byte-identical netlists, timestamp-only SDC diff, full provenance (tool/flow/PDK/library/platform hashes).
- **Tests:** **97 passed, 0 failed, 0 skipped** (pytest, `D:\tools\venv\Scripts\python.exe`, Python 3.10.11). Includes a real yosys failure-injection test (corrupted netlist must be rejected by LEC).
- **Docs corrected** for findings F–M (PVT labels, CTS useful-skew, LEC exclusions, latency authorization, signoff view/derate/post-fill, capability claims, low-power counts, floorplan heuristics, three-fix rule) and stale README "shell blocked" claims removed.

**No finding is claimed as fixed without executed evidence or a guarding test.**

---

## 2. Findings disposition table

| ID | Disposition | What changed | Executed evidence / guarding test |
|---|---|---|---|
| A | FIXED | `assistant/controller.py` + `assistant/evidence.py`: gate decisions computed only from hash-bound evidence; prompt-gates documented as advisory | `tests/test_controller_gates.py` (25 tests); CLI demo `gate_cli_demo.txt` (PASS from evidence; both bypass modes **REFUSED**) |
| B | FIXED | `test_framework.sh`: `REPO_ROOT` derived from script location; root vs example files split; run from any CWD | Script run 47/0/0 (`test_framework_after.txt`); `test_foundation.py::test_script_resolves_repo_root_not_example_dir` |
| C | FIXED | Script TEST 5: `read_liberty -ignore_miss_func` + `synth` + `dfflibmap` + `abc -liberty`; STA refuses non-mapped netlists | Script run: mapped netlist 19 Sky130 cells, 0 `$`-cells; `test_foundation.py::test_script_technology_mapped_synthesis` |
| D | FIXED | Script TEST 6: `async2sync` + stash/copy-from + 3-arg `equiv_make` + gate-side `flatten` + `equiv_status -assert` + required verdict string; negative control added | Positive LEC PASS **and** corrupted netlist REJECTED (`test_framework_after.txt`); `test_failure_injection.py::test_injected_netlist_corruption_detected_by_lec` (real yosys run) |
| E | FIXED | Script TEST 7 + `assistant/diagnosis.py::sta_verdict`: clock/unresolved/unconstrained/malformed/absent-slack rejections; `ASSISTANT_WNS` Tcl marker; ran ≠ meets | Script run WNS parsed; `tests/test_sta_parser.py` (11 tests) |
| F | FIXED | `synthesis_targets.json` PVT corrected (TT 1.80 V/25 C, FF 1.95 V/−40 C, SS 1.60 V/100 C + local-availability notes); `assistant/liberty.py` validator | `python -m assistant validate-pvt` → **overall PASS** vs real Liberty headers; `tests/test_liberty_pvt.py` (original wrong claims FAIL, corrected PASS) |
| G | FIXED | `skills/cts/SKILL.md` §4: constraint knob ≠ physical skew; insertion-delay common-path cancellation caveat; setup+hold on extracted netlist | Doc change (manual review); tracker row G |
| H | FIXED | `skills/lec/SKILL.md` new §7 Exclusions and Waivers (never automatic / explicit / scoped / recorded / independent coverage; proof failures not auto-waivable) | Waiver semantics enforced + tested in `test_controller_gates.py` |
| I | FIXED | `allow_pipelining:false`, `allow_retiming:false`, `max_latency_increase_cycles:0`, `latency_policy`; authorization notes in synthesis/forensic skills; `assistant/eco.py` Authorization pre-validated | `tests/test_eco.py` (11 tests): restricted change → **BLOCKED** without recorded authorization |
| J | FIXED | View ladder enforced (`view_compatible`); ±10 % derate removed from `skills/signoff/SKILL.md`; GR estimate labelled; post-fill marked NOT implemented | `test_evidence_hashing.py` + `test_controller_gates.py::test_estimate_cannot_satisfy_routed_gate` |
| K | FIXED | README "NOT implemented (do not claim)" table: IR drop / EM / antenna / post-fill / qualified signoff / autonomous GDSII; stale "shell blocked" claims corrected | `test_foundation.py::test_readme_claims_match_demonstrated_capabilities`; `python -m assistant info` |
| L | FIXED | `skills/low-power/SKILL.md`: count parity = heuristic; counts-only verdict = INCONCLUSIVE | `diagnosis` epistemic separation tested (`test_sta_parser.py::test_diagnosis_separates_epistemic_categories`) |
| M | FIXED | Floorplan util/channel/halo numbers labelled heuristic starting points; three-fix rule reframed as stop-and-reassess (not automatic "upstream") in forensic + orchestrator skills + README | Doc change (manual review); tracker row M |
| N | FIXED | `assistant/baseline.py` one documented command + `assistant/pins.json`; benchmark + failure-injection suite; this report | Two full baseline runs (16/16 PASS ×2), `reproducibility_r1_vs_r2.json`, `provenance.json` |

---

## 3. Exact commands and results (reproduction instructions)

All commands run from `D:\audit\wt_pd_assistant` unless noted.

### 3.1 Foundation script — before / after

```powershell
# BEFORE (original checkout): immediate failure
wsl -d Ubuntu-24.04 -- bash -lc "cd /mnt/d/audit/wt_pd_assistant && bash examples/test-counter/test_framework.sh"
# → $'\r': command not found, exit 2      [test_framework_before.txt]

# AFTER (remediated):
wsl -d Ubuntu-24.04 -- bash -lc "cd /mnt/d/audit/wt_pd_assistant && bash examples/test-counter/test_framework.sh"
# → PASS=47 FAIL=0 SKIP=0 exit 0          [test_framework_after.txt]
```

Covered per test: structure from repo root, hook EOL/parse, tool detection, Liberty header PVT, generic **and** mapped synthesis, LEC positive + negative control, STA ran-vs-meets, hook run from repo root.

### 3.2 Test suite

```powershell
cd D:\audit\wt_pd_assistant
& D:\tools\venv\Scripts\python.exe -m pytest tests\ -q
# → 97 passed in ~2.5s                     [pytest_after.txt]
```

Test classification: **97 passed · 0 failed · 0 skipped · 0 blocked** (all suites executable in this environment).

### 3.3 PVT validation vs actual Liberty

```powershell
& D:\tools\venv\Scripts\python.exe -m assistant validate-pvt --targets synthesis_targets.json --lib-dir "D:\tools\src\OpenROAD\src\sta\test\sky130hd"
# → {"overall": "PASS", corners: tt PASS / ff PASS / ss NOT_RUN (file not present locally — explicitly not a PDK-scope claim)}
```

### 3.4 Provenance pin

```powershell
& D:\tools\venv\Scripts\python.exe -m assistant provenance --out D:\audit\pd_assistant_evidence_20261116\provenance.json
# → yosys 0.69+154, EQY v0.69, yosys(WSL) 0.33, openroad bazel-nostamp, KLayout 0.30.13,
#   netgen sha256 919231b2…, ORFS commit 25b6429c… (dirty tt lib recorded),
#   PDK commit ac7fb61f…, 3 Liberty sha256s, platform LEF sha256   [provenance.json]
```

### 3.5 Reproducible physical baseline (the one documented command)

```powershell
& D:\tools\venv\Scripts\python.exe -m assistant baseline --run-id baseline_20261116     # run 1
& D:\tools\venv\Scripts\python.exe -m assistant baseline --run-id baseline_20261116_r2  # run 2
# both → exit_code 0, stages_recorded 16, overall PASS            [regression_report_r1/r2.json]
```

**Stage results (identical both runs):** 00 stage00, 01 synthesis, 02 generic LEC, 03 mapped LEC, 04 floorplan, 05 placement, 06 CTS, 07 PDN, 08 power connectivity, 09 global route, 10 detailed route, 11 extraction, 12 multi-corner STA, 13 GDSII, 14 DRC, 15 LVS — **16/16 PASS** (92 per-stage checks in run 1, 92 in run 2; `regression_report_r1/r2.json`).

**Key measured values (run 1 = run 2):**

- Synthesis: 13 Sky130 cell types / 20 instances mapped; generic 6 types vs mapped 13 types.
- STA (SPEF, TT/SS/FF): FF setup_wns **+5.7003 ns**, hold_wns **+0.3917 ns**; SS setup_wns **+2.6745 ns**; violated paths **0**; 30 analysed paths per corner.
- DRC: 295 rule-category evaluations, **actual violations = 0** (helper regions excluded per deck semantics).
- LVS: official deck, verdict **match**, device counts layout 240 = schematic 240, unmatched nets/devices/subcircuits **0**, Weisfeiler-Lehman colour classes 240/240.
- Views produced: `generic_netlist`, `mapped_netlist`, `routed_def`, `routed_extraction` (SPEF), `gds`.

**Reproducibility (run 1 vs run 2):** stage status maps identical; generic + mapped netlist SHA-256 **identical** (`3148c5c3…` / `e1e8d19f…`); SDC body identical after stripping the `write_sdc` timestamp comment; STA WNS/TNS strings identical — `reproducibility_r1_vs_r2.json`.

### 3.6 Gate supervision demo (executable)

```powershell
python -m assistant gate-record   --state <s> --check sta --status PASS --reason "…WNS 5.7003…" --view ROUTED_EXTRACTED
python -m assistant gate-evaluate --state <s> --gate sta_gate --required sta --view ROUTED_EXTRACTED  → PASS (from evidence)
python -m assistant gate-bypass-attempt --state <s> --gate sta_gate --mode force    → REFUSED
python -m assistant gate-bypass-attempt --state <s> --gate sta_gate --mode summary  → REFUSED
```

Full transcript: `gate_cli_demo.txt`; state: `gate_state_demo.json`.

### 3.7 Failure-injection suite

`tests/test_failure_injection.py` (8/8 passed): functional netlist corruption → LEC rejects (real yosys); missing constraints → STA FAIL; changed input → stale evidence blocks; missing library → NOT_RUN blocks; malformed report → FAIL; ECO worsening a protected metric → ROLLED_BACK with original file restored; formal timeout → INCONCLUSIVE blocks; forced bypass → REFUSED.

---

## 4. Baseline vs remediated

| Aspect | Before (original checkout) | After (this pass) |
|---|---|---|
| `test_framework.sh` | CRLF crash, exit 2, no result | 47 PASS / 0 FAIL / 0 SKIP |
| Synthesis exercised by script | generic only | generic **and** `abc -liberty` mapped; STA refuses generic |
| LEC in script | invalid 2-arg `equiv_make`, no verdict assertion | proven recipe + negative control (corruption rejected) |
| STA in script | exit-0 = pass, slack may be empty | clock/unresolved/unconstrained/malformed rejected; WNS parsed; ran ≠ meets |
| PVT claims | wrong voltages/temps, "only TT" | corrected + validated PASS against Liberty headers |
| Gate enforcement | prompt text only | executable, hash-bound, bypass-refused |
| Latency changes | `allow_pipelining:true`, +1 cycle offered freely | default preserve; authorization required & enforced |
| Capability claims | IR/EM/antenna implied | explicit UNSUPPORTED table; stale "shell blocked" claims corrected |
| Reproducible baseline | none | one command, 16/16 PASS, reproduced twice, full provenance |

**Unchanged on purpose (the baseline under comparison):** the 15-stage regression itself (`regression/run_regression.ps1` + stages) — remediation only *records* what it runs.

---

## 5. Unsupported / unresolved (never claimed)

Verified **not** demonstrated by this repository (also in README "NOT implemented" table and `assistant/baseline.py::UNSUPPORTED_CAPABILITIES`):

1. IR drop analysis — no executable stage.
2. EM (electromigration) — no executable stage.
3. Antenna checking — no executable stage.
4. Post-metal-fill extraction / post-fill timing closure — flow stops at routed extraction + STA.
5. Qualified/signoff-grade parasitic extraction — academic OpenROAD extraction only (no QRC/StarRC).
6. MMMC beyond the three locally present Sky130 Liberty files (tt/ss/ff); `ss_100C_1v60` file not present locally → that corner check is `NOT_RUN`, not PASS.
7. Tapeout readiness / autonomous RTL-to-GDSII.
8. `soc_ctrl` full regression — configured, **not executed** in this pass; no DRC/LVS/STA claimed for it.
9. UPF parsing — no UPF parser implemented; low-power checks are structural heuristics.
10. Tool-level LEC exclusion mechanics (Conformal/Formality) — documented rules only; tools not licensed here.
11. OpenROAD binary reports `bazel-nostamp` — flow pinned by ORFS commit + dirty-tree record instead of a version string (recorded in provenance).

## 6. Risks / limitations of this pass

- **Local host pinning:** provenance is host-specific (`D:\tools\…`, WSL `Ubuntu-24.04`); on another machine pins will report drift rather than silently pass — intended, but reproduction requires the same tool tree.
- **ORFS dirty tree:** the pinned ORFS checkout has a locally modified `tt` Liberty (`dirty_files` recorded); its hash is captured, but a pristine check-out would differ.
- **Single design:** reproducibility demonstrated on `test_counter` only (small, 4-bit); scalability of LEC/STA to `soc_ctrl` unproven in this pass.
- **SDC output timestamp:** `11_final.sdc` embeds a generation timestamp (raw file hash differs between runs); body (constraints) verified identical.
- **Doc fixes are review-based:** findings G, H, L, M corrections are prose — guarded only by review, not by an executable doc-lint (except README claims + tracker presence tests).
- **Gate controller scope:** it governs only checks wired to it; the prompt-level process for humans/LLMs remains and is documented as advisory.

## 7. Reviewer checklist

- [ ] `docs/reviews/initial-review.md` faithfully mirrors the external review (A–N).
- [ ] `docs/reviews/remediation-tracker.md` maps every finding → severity/files/status/remediation/implementation/tests/limitations.
- [ ] Re-run `test_framework.sh` in WSL → expect 47/0/0.
- [ ] Re-run `python -m pytest tests\ -q` → expect 97 passed.
- [ ] Re-run `python -m assistant baseline --run-id <new>` → expect 16/16 PASS; diff `synth/*.v` hashes against `reproducibility_r1_vs_r2.json`.
- [ ] `python -m assistant validate-pvt --targets synthesis_targets.json …` → PASS.
- [ ] `python -m assistant gate-bypass-attempt …` → REFUSED (both modes).
- [ ] Confirm `D:\audit\repo` still at `41728cd8…` with 34 changes (review target preserved).
- [ ] Verify README "NOT implemented" table vs `regression/stages/` contents (0 IR/EM/antenna stages).
- [ ] Evidence directory `D:\audit\pd_assistant_evidence_20261116\` contains the logs cited above (17 files).

## 8. Path to review handoff

1. This report + tracker + initial review are the review package (`docs/reviews/`).
2. Changes are **local/uncommitted** in `D:\audit\wt_pd_assistant` — publication (branch + PR to `RAMA-L7/vlsi-pd-staff-engineer`) awaits explicit instruction; the preserved reference checkout is `D:\audit\repo`.
3. Evidence directory must be archived alongside any PR description (it is intentionally outside the repo so no generated artifacts are committed; `regression/workspace/` is git-ignored).
