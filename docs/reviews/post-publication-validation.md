# Post-Publication Validation — commit `6c4a264`

**Validated commit:** `6c4a2640b95e869fe3f0c1367dca5aaf9626f3fc` (branch `main`, published 2026-11-16)
**Fresh clone:** `D:\audit\validate_pd_20261116` (cloned from `https://github.com/RAMA-L7/vlsi-pd-staff-engineer.git`)
**Validation date:** 2026-11-16
**Constraints honoured:** no push, no commit, no history rewrite, no PR; published repository and `D:\audit\repo` not modified; the clone's working tree had no tracked-file changes after testing (only gitignored generated artifacts).

**Classification vocabulary:** `PASS` (verified) · `FAIL` (verified defect / claim not met) · `BLOCKED` (could not run) · `NOT_TESTED` (not attempted).

---

## 1. Executive summary

Four independent subagents validated the published commit from a fresh clone, and the main agent re-verified the critical caveats. The published implementation reproduces cleanly:

| Area | Classification | Headline result |
|---|---|---|
| Repository integrity | PASS | Exact commit, clean tree, 125 published files, zero CRLF in shell blobs, no secrets, original repo untouched |
| Test suites | PASS | pytest **97 passed**; framework **47 PASS / 0 FAIL / 0 SKIP**; mapped synthesis, LEC ± control and STA gates independently re-checked |
| Physical baseline | PASS (with 2 non-blocking artifact-fidelity caveats) | 16/16 stages PASS, 92 checks, byte-identical netlists, WNS/DRC/LVS match published evidence |
| Architecture & documentation audit | **PASS for executable core / FAIL for documentation accuracy** | Gate, evidence, ECO, diagnosis and PVT semantics are genuinely implemented; five findings marked FIXED have residual contradicting prose, and three defense-in-depth code gaps were independently reproduced |

**Unresolved issues:** (1) regression report git metadata points at a different checkout; (2) mapped-netlist view hash recorded as `<missing>`; (3) stale present-tense "shell blocked" claims survive in published README/docs despite a claim they were removed; (4) wrong PVT table in synthesis skill, derating advice in signoff agent, blanket insertion-delay claim in CTS skill, old three-fix rule in ARCHITECTURE and invalid 2-arg `equiv_make` in LEC skill; (5) three code-level gaps — direct-API NOT_APPLICABLE bypass, partial CLI evidence binding, baseline timeout returns exit 0. Items (1)–(2) do not affect physical results; items (3)–(5) are documentation/defense-in-depth defects, not fabricated code capability.

---

## 2. Subagent 1 — Repository integrity (PASS)

Commands and results (all read-only):

```
git -C D:\audit\validate_pd_20261116 rev-parse HEAD
  → 6c4a2640b95e869fe3f0c1367dca5aaf9626f3fc  (= expected)
git -C … branch --show-current         → main
git -C … remote -v                     → fetch/push = https://github.com/RAMA-L7/vlsi-pd-staff-engineer.git
git -C … rev-parse --abbrev-ref --symbolic-full-name '@{u}' → origin/main
git -C … status --short                → empty (clean)
git -C … ls-remote origin refs/heads/main
  → 6c4a2640b95e869fe3f0c1367dca5aaf9626f3fc  refs/heads/main
git -C … show -s --format='%H%n%P%n%T' HEAD
  → parent e04692f2745b37de7d9b582e232764a0cf6c9c08
  → tree   a6f6bdf9271d26ff08f24e0ecfb2227b3ef79cbb
git -C … log -1 --format=fuller
  → RAMA-L7 <ramasketha14093@gmail.com>, Mon Nov 16 20:01:00 2026 +0530
  → "Implement supervised PD assistant with evidence-bound gates and reproducible baseline"
```

| Check | Classification | Evidence |
|---|---|---|
| Commit identity / branch / remotes / upstream / clean tree / remote match | PASS | commands above |
| Commit metadata (author, date, subject, parent, tree) | PASS | commands above |
| Published tree: **125 files**; `assistant/` 13 modules + `pins.json`; `tests/` 9 modules; 3 `docs/reviews/`; repaired script; 8 corrected skill docs (10 skill docs total, 2 untouched); config/git files | PASS | `git ls-tree -r --name-only HEAD` |
| No generated artifacts committed (`__pycache__`, `*.pyc`, `regression/workspace/`, `regression/evidence/`, `results/`); `.gitignore` rules present | PASS | tree scan + `git show HEAD:.gitignore` |
| Shell blob line endings — binary-safe count of `b"\r\n"` over 9 tracked shell/hook blobs: **TOTAL_CRLF=0** | PASS | `git show HEAD:<path>` via Python `subprocess` bytes; `.gitattributes` pins `*.sh text eol=lf` |
| Committed content equals worktree (`git diff --exit-code HEAD --` → RC 0) | PASS | command above |
| Secrets scan (125 blobs): no private keys, PATs, `sk-`, AWS keys, or `password/api_key/secret = "value"` assignments | PASS | Python binary-safe scan; only benign linguistic "token" hits in text search |
| Reference repo `D:\audit\repo` untouched: HEAD `41728cd8343b726b0c717df104f3ce26ade18249`, 34 changed paths | PASS | `git -C D:\audit\repo ...` |

Unresolved issues from this subagent: none.

---

## 3. Subagent 2 — Test validation (PASS)

Commands:

```
cd D:\audit\validate_pd_20261116
& D:\tools\venv\Scripts\python.exe -m pytest tests\ -q -rs
  → 97 passed in 3.08s; 0 failed, 0 skipped, 0 blocked, 0 warnings; RC 0
  → evidence: D:\audit\pd_validation_20261116\pytest_fresh.txt

wsl -d Ubuntu-24.04 -- bash -lc "cd /mnt/d/audit/validate_pd_20261116 && bash examples/test-counter/test_framework.sh"
  → PASS: 47 / FAIL: 0 / SKIP: 0; RC 0
  → evidence: D:\audit\pd_validation_20261116\test_framework_fresh.txt
```

Independent re-check of generated artifacts (not just `[PASS]` labels; evidence `D:\audit\pd_validation_20261116\gates_raw_check.txt`):

| Gate | Classification | Observed |
|---|---|---|
| Technology-mapped synthesis | PASS | 19 Sky130 cell references, 12 distinct types, **0** generic `$`-cells |
| LEC positive control | PASS | `Of those cells 6 are proven and 0 are unproven.` / `Equivalence successfully proven!`, yosys exit 0 |
| LEC negative control (corrupted netlist) | PASS | `ERROR: Found 1 unproven $equiv cells in 'equiv_status -assert'.`, yosys exit 1 |
| STA ran-vs-meets | PASS | `ASSISTANT_WNS 4.95796129296133`, `ASSISTANT_WHS 0.46458879724984425`, `ASSISTANT_TNS 0.0`; WNS ≥ 0 → meets target |
| Match vs published evidence | PASS | pytest 97, framework 47/0/0, mapped cells 19, generic cells 30, STA WNS identical; only timestamp/path differ |
| Post-test tracked-file integrity | PASS | `git status --short` empty |

Main-agent confirmation: evidence files exist and contain the claimed result lines (`97 passed in 3.08s`; `PASS: 47 / FAIL: 0 / SKIP: 0`).

Unresolved issues from this subagent: none.

---

## 4. Subagent 3 — Baseline reproduction (PASS, with caveats)

Command:

```
cd D:\audit\validate_pd_20261116
& D:\tools\venv\Scripts\python.exe -m assistant baseline --run-id validate_20261116 --timeout 9000 --out D:\audit\pd_validation_20261116
  → {"manifest": "D:\\audit\\pd_validation_20261116\\baseline_manifest.json",
     "exit_code": 0, "run_dir": "D:\\audit\\validate_pd_20261116\\regression\\workspace\\run_validate_20261116",
     "stages_recorded": 16, "notes": []}
  → evidence: D:\audit\pd_validation_20261116\baseline_cli_out.txt
```

| Verification | Classification | Result |
|---|---|---|
| All 16 stages PASS; **92** per-stage checks; report `overall = PASS`, `not_executed = []`, `failure_messages = []` | PASS | `regression\evidence\report_validate_20261116.json` |
| Synthesis mapped/generic cell counts | PASS | `mapped types=13, generic types=6`; `13 Sky130 cell types, 20 instances` |
| STA (SPEF, TT/SS/FF) | PASS | FF setup_wns **5.7003**, hold_wns 0.3917; SS setup_wns **2.6745**, hold_wns 1.2335; TT setup_wns **5.4550**, hold_wns 0.6159; TNS 0.0 at all corners; violated paths **0**; 30 paths/corner |
| DRC | PASS | 295 rule-category evaluations, actual violations **0**, DB 0 items / 0 values |
| LVS | PASS | explicit verdict `match`; layout **240** = schematic **240**; unmatched nets/devices/subcircuits **0**; WL classes 240/240 |
| Views produced | PASS | `11_final.spef` (routed extraction), `test_counter_pdn.gds`, generic + mapped netlists, `10_route.def` |
| Netlists byte-identical to published run 1 | PASS | generic `3148c5c3bac84daf…`, mapped `e1e8d19f6444f28a…` — both **true** vs published run-1 files |
| Stage-status map + WNS detail string vs published `regression_report_r1.json` | PASS | both **true** |
| SDC body equality after stripping `write_sdc` timestamp comment | PASS | **true**; raw bytes differ only in the generated date comment (as published evidence states) |
| Provenance / tool drift vs `assistant/pins.json` | PASS | manifest `notes = []` (no drift); all 6 tools match pins; ORFS `25b6429c…` (dirty tt lib), PDK `ac7fb61f…` |
| Post-run tracked-file integrity | PASS | `git status --short` empty |

Comparison JSON: `D:\audit\pd_validation_20261116\baseline_comparison.json` (`netlists_all_match = true`, `stage_status_maps_identical = true`).

### 4.1 Independently re-verified caveats (main agent)

**(a) FAIL — non-blocking: regression report git metadata is not the validated checkout.**
The fresh run's regression report records:
```
report.git_head  = 41728cd8343b726b0c717df104f3ce26ade18249
report.git_branch = feature/F-01-claude-agent-discovery
report.run_dir   = D:\audit\validate_pd_20261116\regression\workspace\run_validate_20261116
report.overall   = PASS
```
`git_head`/`git_branch` belong to `D:\audit\repo`, **not** to the fresh clone (`6c4a264…`, `main`). Cause confirmed in source: `regression/run_regression.ps1:104` defaults `$Repo = if ($env:REG_REPO) { $env:REG_REPO } else { 'D:\audit\repo' }`; `assistant/baseline.py` does not set `REG_REPO`. Physical artifacts still came from the clone's `run_dir` (netlist hashes and paths confirm), so the baseline itself is valid — but the report's git fields are misleading and are **not** a faithful provenance record of the validated checkout. Recommended follow-up: have `baseline.py` set `REG_REPO` to its `repo_root` before invoking the regression.

**(b) FAIL — minor, non-blocking: manifest `views.mapped_netlist.sha256` is `<missing>`.**
```
path = /mnt/d/audit/validate_pd_20261116/.../synth/test_counter_synth.v
sha256 = "<missing>"
```
The view path is a WSL path; the Windows-side `baseline.py` uses `os.path.isfile()`, which cannot resolve `/mnt/d/...`, so the hash is not recorded. The generic netlist view (Windows path) has a correct hash. Identical behaviour exists in the published r2 manifest, so this is pre-existing, not a reproduction defect; the actual mapped netlist exists and its SHA-256 was independently verified above.

**(c) PASS — informational: CLI drift-note difference.** Published run-1 stdout carried a one-time `yosys … live='<unavailable>'` drift note (a probe quirk fixed before publication); this fresh run has `notes = []` and matches published r2. No effect on results.

Unresolved issues from this subagent: caveats (a) and (b) above; no FAIL of the baseline itself.

---

## 5. Subagent 4 — Architecture and documentation audit

Commands used by the subagent: `git show --stat --oneline HEAD`, `git ls-tree -r --name-only HEAD`, ripgrep over `*.md`, `python -m assistant info`, plus an in-process probe of the controller (temp state only). Full pytest, WSL regression and live baseline were `NOT_TESTED` in this subagent's scope but were covered by subagents 2 and 3 (all PASS).

### 5.1 Executable architecture — PASS (core semantics genuinely implemented)

Verified with file:line references: six statuses and precedence (`assistant/statuses.py:15-21,54-86`); input-hash staleness downgraded to NOT_RUN (`controller.py:330-341`); view ladder (`evidence.py:24-72`); consistency guards for PASS+nonzero-exit, missing reports, view mismatch (`controller.py:345-366`); NA rule registry + waiver scoping (`controller.py:44-67,116-139,253-282`); force-pass/LLM-summary refusal (`controller.py:240-250`); upstream gating, cancel/restart preserving evidence (`controller.py:306-314,417-444`); ECO authorization/checkpoint/rollback/protected metrics/3-failure reassessment (`eco.py:149-347`); observation/hypothesis/heuristic separation with non-authorizing advisory (`diagnosis.py:187-448`); Liberty PVT parse and local-vs-PDK distinction (`liberty.py:38-168`); baseline UNSUPPORTED list and no fabricated stage results (`baseline.py:70-81,109-193`). `python -m assistant info` reports all six statuses, five NA rules, six restricted classes and `llm_can_authorize_gates: false`. Subagent probe confirmed: GR estimate cannot satisfy routed gate; PASS with exit 1 → BLOCKED; both bypass modes → `GateBypassError`.

### 5.2 Independently reproduced code caveats — FAIL (defense-in-depth, non-fabrication)

Main agent re-ran all three with `D:\tools\venv\Scripts\python.exe` and a probe script outside the repo (`D:\audit\pd_validation_20261116\probe_code_gaps.py`, output `code_gap_probes.json`, `probe_stdout.txt`):

1. **Direct-API NOT_APPLICABLE bypass — FAIL.** `controller.record_evidence()` does not validate `NOT_APPLICABLE`; only `mark_not_applicable()` does. Probe: `Evidence(status=NOT_APPLICABLE, applicability_reason="")` recorded directly → gate decision **PASS** (`"all required checks PASS"`). The advertised CLI path (`cli.py:75-77`) does route through `mark_not_applicable`, so this is an API defense-in-depth gap, not a CLI bypass.
2. **CLI evidence only partially bound — FAIL.** `gate-record` populates `command`, `exit_code`, `analysis_view` and auto input hashes, but probe state (`probe_cli_state.json`) shows `tool_versions: {}`, `logs: []`, `reports: []`, `artifacts: []`, `config_hashes: {}`, `constraint_hashes: {}`, `library_hashes: {}`, `pdk_hashes: {}`. The schema supports these fields (`evidence.py:75-99`), but the documented CLI does not populate them.
3. **Baseline timeout returns shell exit 0 — FAIL.** Simulated `TimeoutExpired` for the regression invocation produced manifest `exit_code: null`, note `"baseline timed out after 10800.0s"`, yet `baseline.main()` returned **0** (`baseline.py:376-378`). Automation would see success unless it parses the manifest note.

### 5.3 Documentation accuracy — FAIL (residual contradictions, all verified verbatim)

Main agent independently confirmed each quoted line in the fresh clone:

| Finding claimed FIXED | Residual | Location (verified) |
|---|---|---|
| F — PVT corrected | old wrong table `SS 0.9V/125°C`, `FF 1.1V/-40°C`, `TT 1.0V/25°C` (+ inconsistent `*_ss_n40C_1v60.lib`) | `skills/synthesis/SKILL.md:50-56` (file touched only for the authorization note) |
| G — insertion delay caveat | unqualified "every ps of insertion delay eats setup margin" | `skills/cts/SKILL.md:54-55,235` (corrected text exists at `:135-162`) |
| J — arbitrary derate removed | "If only TT is available (Sky130), apply derating" | `agents/signoff-engineer.md:29` (file untouched by the commit, although tracker J lists it) |
| K — stale shell claims removed | `README.md:27` "Live regression execution is blocked by a Code Mode runtime shell integration issue"; `README.md:312` "Full regression execution is blocked…" — while `README.md:762` claims "Stale shell-blocker claims have been removed" | `README.md:27,312,762` — self-contradictory |
| M — three-fix reframed | "stop and trace the root cause upstream" | `docs/ARCHITECTURE.md:691` (file untouched) |
| D — invalid LEC recipe fixed in script | `equiv_make reference revised` (2 args) still taught | `skills/lec/SKILL.md:203` |
| Report currency | "Changes are **local/uncommitted** … publication awaits explicit instruction"; §7 checklist all `[ ]` | `docs/reviews/remediation-report.md:186,172-181` |

The README "NOT implemented in this repository" table itself **PASS**es: it lists IR drop / EM / antenna / post-fill / qualified signoff / autonomous GDSII as unsupported, and `regression/stages/` contains no such stages. Stale present-tense blocker claims also remain in untouched `docs/REGRESSION.md`, `docs/ARCHITECTURE.md`, `docs/VALIDATION.md`, `docs/DESIGN.md` (subagent line references in its report). Honest limitations (11 UNSUPPORTED items, host-pinned tools, dirty ORFS tree, single design, doc fixes review-based) **PASS** as documented.

**Bottom line from this audit:** no code capability was found fabricated; the executable architecture is substantively sound. The documentation remediation layer is unreliable — five findings marked FIXED have clear residual contradicting text.

---

## 6. Consolidated classification and unresolved issues

| Finding | Classification | Status |
|---|---|---|
| Repository identity / tree / EOL / secrets / original repo preserved | PASS | — |
| pytest 97 passed; framework 47/0/0; gates re-checked | PASS | — |
| Baseline 16/16 PASS, 92 checks, byte-identical netlists, WNS/DRC/LVS match | PASS | — |
| Gate/evidence/ECO/diagnosis/PVT architecture substantively implemented (`assistant/`) | PASS | core semantics verified in code + probe |
| `python -m assistant info` capability probe | PASS | advertised statuses/rules/classes correct |
| README NOT-implemented table matches actual stages | PASS | no IR/EM/antenna/post-fill stage |
| Regression report git provenance faithful to validated checkout | FAIL | Non-blocking; wrong `git_head`/`git_branch` from hard-coded `$Repo` default (`run_regression.ps1:104`) |
| Manifest mapped-netlist view hash recorded | FAIL | Minor; WSL path unreadable by Windows `os.path.isfile`; pre-existing in published r2 |
| NOT_APPLICABLE reason validated at every evidence-recording boundary | FAIL | Direct API bypass reproduced; CLI path correct |
| CLI evidence fully hash/log-bound as documented | FAIL | tool versions, logs, reports, artifacts, config/constraint/library/PDK hashes empty in CLI records |
| Baseline timeout signalled to automation | FAIL | manifest note records timeout but CLI returns 0 |
| Findings F/G/J/K/M fully remediated in all listed docs | FAIL | residual contradicting prose verified verbatim (§5.3) |
| Finding D fully remediated in skill docs | FAIL | invalid 2-arg `equiv_make` still in `skills/lec/SKILL.md:203` |
| Published remediation report currency | FAIL | stale "uncommitted" statement + unchecked reviewer checklist |
| Published claims of unsupported capabilities / limitations | PASS | honestly documented; no overclaim found there |

### Overall verdict

**Core implementation and all executed tests/baseline: PASS — reproduced exactly from a fresh clone. Documentation remediation and several defense-in-depth/artifact-fidelity details: FAIL — the published "FIXED" dispositions overstate what was actually changed.** No commit, push, PR or history rewrite was performed; the published repository is unchanged.

## 7. Evidence paths

- `D:\audit\pd_validation_20261116\pytest_fresh.txt`
- `D:\audit\pd_validation_20261116\test_framework_fresh.txt`
- `D:\audit\pd_validation_20261116\gates_raw_check.txt`, `verify_gates.sh`
- `D:\audit\pd_validation_20261116\baseline_cli_out.txt`
- `D:\audit\pd_validation_20261116\baseline_manifest.json`
- `D:\audit\pd_validation_20261116\baseline_comparison.json`
- `D:\audit\validate_pd_20261116\regression\evidence\report_validate_20261116.json`
- `D:\audit\validate_pd_20261116\regression\workspace\run_validate_20261116\`
- `D:\audit\pd_validation_20261116\probe_code_gaps.py`, `probe_stdout.txt`, `code_gap_probes.json`, `probe_cli_state.json`, `probe_na_state.json`, `probe_timeout_out\baseline_manifest.json` (code-caveat reproductions)
- `D:\audit\pd_validation_20261116\baseline_restore_stdout.txt` (see transparency note)
- Reference (published) evidence: `D:\audit\pd_assistant_evidence_20261116\`

**Transparency note:** the main agent's first timeout-probe attempt mistakenly wrote its simulated manifest to `D:\audit\pd_validation_20261116\baseline_manifest.json`, overwriting the real baseline manifest. The probe was isolated to `probe_timeout_out\` and the real manifest was restored by re-running the same documented command (`--run-id validate_20261116`); final manifest: `exit_code = 0`, `stages_recorded = 16`, `notes = []`. The published baseline evidence (outside the repo) was never modified.
