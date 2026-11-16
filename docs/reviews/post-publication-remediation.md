# Post-Publication Remediation Report

**Working clone:** `D:\audit\validate_pd_20261116` (fresh clone of published `main` @ `6c4a2640b95e869fe3f0c1367dca5aaf9626f3fc`)
**Date:** 2026-11-16
**Scope:** fix every finding confirmed in `docs/reviews/post-publication-validation.md` (sections 4.1, 5.2, 5.3, 6).
**Constraints honoured:** all changes local and uncommitted; no push, PR or history rewrite; published remote unchanged; `D:\audit\repo` untouched; no production RTL/SDC/PDK/signoff data modified; no evidence fabricated and no gate weakened.

Classification vocabulary: **PASS** (fixed and verified) · **FAIL** (not fixed / defect remains) · **BLOCKED** (could not be fixed) · **NOT_TESTED** (not attempted).

---

## 1. Executive summary

All 12 confirmed findings were remediated by three independent subagents with disjoint file ownership, then verified by the main agent:

| Area | Classification | Verification |
|---|---|---|
| Documentation residuals (D/F/G/J/K/M, stale shell claims, report currency) | PASS | full pytest 130 passed; grep verification; line-level review |
| Gate validation — NOT_APPLICABLE at every recording boundary | PASS | negative tests + independent probe (empty reason refused, no state written) |
| CLI evidence provenance enforcement | PASS | negative tests + independent probe (incomplete → exit 2, no state; full provenance stored) |
| Baseline timeout exit status | PASS | unit tests + independent probe (timeout → return 124) |
| Baseline Git HEAD provenance | PASS | real baseline report `git_head = 6c4a264`, branch `main` |
| Mapped-netlist hash generation | PASS | real manifest `sha256 = e1e8d19f…` matches file bytes |
| Independent audit of every remediation | PASS (after follow-up) | 11/12 PASS on first audit; the one partial FAIL (M-residual) was immediately fixed and re-verified (see §4) |

**Baseline reproduction:** `python -m assistant baseline --run-id remediated_20261116 --out D:\audit\pd_remediation_evidence_20261116` → `status: PASS`, `return_code: 0`, `exit_code: 0`, 16/16 stages PASS, 92 checks; netlists byte-identical to published run 1, WNS/SDC-body/stage maps identical.

---

## 2. Findings and dispositions

| # | Finding (from post-publication-validation.md) | Classification | Fix | Verification |
|---|---|---|---|---|
| 1 | D-residual: invalid 2-arg `equiv_make reference revised` in `skills/lec/SKILL.md` | PASS | Replaced with proven recipe: `design -stash gold/gate`, `design -copy-from … -as`, 3-arg `equiv_make gold gate equiv`, gate-side `flatten`, `equiv_status -assert`, explicit verdict required | line-level review; grep no 2-arg call remains |
| 2 | F-residual: wrong PVT table in `skills/synthesis/SKILL.md` | PASS | Corrected to SS 1.60V/100C (`ss_100C_1v60.lib`, local file gap), TT 1.80V/25C, FF 1.95V/−40C; pointers to `assistant/liberty.py` + `synthesis_targets.json` | line-level review; values match config + Liberty headers |
| 3 | G-residual: blanket "every ps of insertion delay eats setup margin" | PASS | `skills/cts/SKILL.md:54-55,235` qualified: common delay cancels for same-clock reg-to-reg; I/O paths affected; cross-reference corrected §4 | line-level review |
| 4 | J-residual: "apply derating" in `agents/signoff-engineer.md` | PASS | Replaced with real-corner requirement, partial-corner labelling, ±10% derate prohibited absent recorded reviewer-approved waiver; aligned with `skills/signoff/SKILL.md` | line-level review |
| 5 | K-residual: present-tense "shell blocked" claims in README and docs | PASS | `README.md:27,312,762`, `docs/ARCHITECTURE.md`, `REGRESSION.md`, `VALIDATION.md`, `DESIGN.md`: blocker labelled historical; baseline command documented as working; self-contradiction removed | grep: no present-tense blocker claims in instructional prose |
| 6 | M-residual: universal three-fix rule in `docs/ARCHITECTURE.md` | PASS | `:237`, `:524`, `:691`, `:738` reframed as stop-and-reassess heuristics; "upstream" = hypothesis requiring discriminating test. Same universal claim also corrected in `agents/routing-engineer.md:31-34` and `agents/synthesis-engineer.md:36-40` | line-level review + `git grep` for "trace root cause upstream" / "structural and upstream" (only heuristic/negative statements remain) |
| 7 | Report currency: `remediation-report.md:186` + all-unchecked §7 checklist | PASS | Statement corrected to published-at-6c4a264 with local follow-up; §7 explicitly labelled a reviewer checklist, not a completion claim | line-level review |
| 8 | NA validation: direct-API `NOT_APPLICABLE` bypass | PASS | `controller.py`: single shared `validate_applicability_reason()` invoked from `record_evidence()` before any state mutation; `mark_not_applicable()` delegates to it | negative tests + independent probe: empty reason → `ApplicabilityError`, no state file, gate not PASS |
| 9 | CLI evidence binding: empty tool versions / logs / reports / hashes | PASS | `cli.py` `gate-record` gains repeatable `--report/--log/--artifact` (hashed), `--tool-version`, `--input/config/constraint/library/pdk-hash`; `validate_provenance()` refuses incomplete substantive records before state write | negative + positive CLI tests; independent probe: incomplete → exit 2, no state; full provenance stored |
| 10 | Baseline timeout returned shell exit 0 | PASS | `baseline.py`: `timed_out`/`start_failed` flags, `baseline_exit_signal()` → 124 timeout / 125 spawn / 1 other incomplete; manifest `exit_code` stays `null` | unit tests + independent probe: timeout → return 124, `status: TIMEOUT` |
| 11 | Baseline Git HEAD recorded wrong checkout | PASS | `baseline.py` passes `env["REG_REPO"] = repo_root` to the regression; `run_regression.ps1` untouched | real baseline report: `git_head = 6c4a264…`, `git_branch = main`; unit test asserts env |
| 12 | Mapped-netlist view hash `<missing>` | PASS | `normalize_wsl_path()` + `_hash_view_artifact()`; recorded path preserved, hash computed from translated path | real manifest: `sha256 = e1e8d19f…` equals file bytes; WSL + native-path unit tests |

---

## 3. Commands and results

```
# Full suite (fresh clone, after all remediations)
cd D:\audit\validate_pd_20261116
& D:\tools\venv\Scripts\python.exe -m pytest tests\ -q -rs
  → 130 passed in 6.09s; 0 failed, 0 skipped
  → D:\audit\pd_validation_20261116\pytest_postremediation.txt

# Framework regression (WSL)
wsl -d Ubuntu-24.04 -- bash -lc "cd /mnt/d/audit/validate_pd_20261116 && bash examples/test-counter/test_framework.sh"
  → PASS: 47 / FAIL: 0 / SKIP: 0, exit 0
  → D:\audit\pd_validation_20261116\test_framework_postremediation.txt

# Real physical baseline
& D:\tools\venv\Scripts\python.exe -m assistant baseline --run-id remediated_20261116 --timeout 9000 --out D:\audit\pd_remediation_evidence_20261116
  → {"exit_code": 0, "stages_recorded": 16, "notes": [], "timed_out": false,
     "start_failed": false, "status": "PASS", "return_code": 0}
  → D:\audit\pd_remediation_evidence_20261116\baseline_cli_out.txt

# Main-agent independent code probes
& D:\tools\venv\Scripts\python.exe D:\audit\pd_validation_20261116\verify_code_fixes.py
  → NA empty reason refused, no state written; CLI incomplete evidence exit 2, no state;
    baseline timeout return_code 124 (manifest exit_code null)
  → D:\audit\pd_validation_20261116\fix_verify\
```

Baseline comparison (Python binary-safe SHA-256): generic and mapped netlists match published run 1 (`3148c5c3…`, `e1e8d19f…`); SDC body (timestamp comment stripped) matches; WNS detail string identical (FF setup 5.7003, SS 2.6745, TT 5.4550; 0 violations); stage-status maps identical — `D:\audit\pd_remediation_evidence_20261116\baseline_comparison.json`.

---

## 4. Independent audit

An independent review subagent read the complete diff, all new tests, the tracker and the evidence, and independently re-probed findings 8–12. Result: **11 of 12 findings PASS; finding 6 (M-residual) initially FAIL — partial, documentation-only**.

- All code gaps (8–12) were independently reproduced as fixed: empty/invalid NA reasons raise before state write; incomplete CLI evidence exits 2 with no state; timeout returns 124 while `exit_code` stays `null`; `REG_REPO` points at the validated clone; mapped-netlist hash equals the file bytes.
- Findings 1–5 and 7 were confirmed fixed with concrete `file:line` evidence. No change weakened a gate, fabricated evidence or touched production data; `git diff --cached` empty, remote still `6c4a264`, `D:\audit\repo` still `41728cd` with 34 changes.
- **Follow-up for finding 6:** the audit found two additional universal assertions in `docs/ARCHITECTURE.md:237` ("the problem is structural and upstream") and `:524` ("stop and trace root cause upstream"). The main agent reframed both as stop-and-reassess heuristics with "upstream" explicitly a hypothesis requiring a discriminating test. Also corrected a cosmetic stale "expect 97 passed" line in the reviewer checklist (`remediation-report.md:177` → current suite count 130). **Final classification for all 12 findings: PASS.**
- Pre-existing, documented quirk confirmed by the audit: a fresh CLI `gate-evaluate` after a `--input-hash` record returns BLOCKED because the input registry is in-memory only; unchanged and not a weakening.

---

## 5. Remaining limitations / unresolved issues

- All changes are local/uncommitted in `D:\audit\validate_pd_20261116`; publication was not requested and was not performed.
- `assistant/baseline.py --dry-run` now returns shell exit 1 (`status: INCOMPLETE`) because no command executes; this is intentional strictness (a dry run is not a completed baseline) and is reported in the JSON status payload.
- CLI input hashes registered via `--input-hash` are in-memory per controller instance (pre-existing design quirk); a separate `gate-evaluate` process does not persist the input registry. Not weakened or changed in this pass.
- Historical KLayout Windows Qt5 limitation and other limitations listed in `docs/reviews/remediation-report.md` remain unchanged and out of scope.

---

## 6. Evidence paths

- `D:\audit\pd_validation_20261116\pytest_postremediation.txt`
- `D:\audit\pd_validation_20261116\test_framework_postremediation.txt`
- `D:\audit\pd_validation_20261116\verify_code_fixes.py`, `fix_verify\`
- `D:\audit\pd_remediation_evidence_20261116\baseline_cli_out.txt`, `baseline_manifest.json`, `baseline_comparison.json`
- `D:\audit\validate_pd_20261116\regression\evidence\report_remediated_20261116.json`
- `D:\audit\validate_pd_20261116\regression\workspace\run_remediated_20261116\`
- Source findings: `D:\audit\validate_pd_20261116\docs\reviews\post-publication-validation.md`
