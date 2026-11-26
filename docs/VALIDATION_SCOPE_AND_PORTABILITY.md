# Validation scope and portability disclosure — fixed candidate (R-05)

**Document type:** documentation-only scope disclosure. No source, test, evidence,
frozen candidate or protected repository was changed by this document.
**Candidate in scope:** fixed candidate `D:\audit\conditional_closure_sprint_20261126\WORKING_COPY`,
full-tree identity `e591c30abf0cadc0ecb9a3c1f50f9ec6b7c335524a6bcc68841bf4ff514a2eb2`
(753 files), byte-identical to the prior stability candidate
(`GOVERNANCE/PHASE0.json`; `EVIDENCE/PHASE0_IDENTITY.md`).
**No readiness, release, quality, portability-certification or signoff claim.**

Status vocabulary used throughout:

- **INDEPENDENTLY VERIFIED** — an observed result exists in a recorded run/log on
  the stated host, or was independently re-derived from raw evidence.
- **STATIC REASONING ONLY** — inferred from source/document inspection; no
  execution of that behaviour is recorded.
- **NOT TESTED** — no result exists. `NOT_TESTED` is never converted to a PASS,
  and historical results are never inherited by this candidate.

---

## 1. Exactly what was validated, and on which host

All execution evidence for this candidate comes from **one Windows x86-64 host**
(identified as host `mi` in the independent reassessment; `D:\` roots), using
`D:\tools\venv\Scripts\python.exe` (**Python 3.10.11**) and **pytest 9.2.0**.
Hygiene used in the recorded runs: `PYTHONDONTWRITEBYTECODE=1`, `python -B`,
`-p no:cacheprovider`, fresh `--basetemp` / separate TEMP/TMP, and a fresh OS
process per stress repetition.

| Area | What was actually observed on that host | Status | Evidence |
|---|---|---|---|
| Full pytest suite | Three consecutive runs: **613 passed / 5 skipped / rc=0** each (512.72 s, 493.64 s, 535.86 s). These are closure-sprint runs on this host only; independent runs under concurrent assessment load produced 2 clean and 1 failed (612 passed / 1 failed / 5 skipped), plus a load-sensitive targeted cancellation failure. A clean streak is **not** a general property. | **INDEPENDENTLY VERIFIED on this host only** | `D:\audit\stability_commit_sprint_20261126\REPORTS\FULL_REGRESSION_REPORT.md`; raw `VALIDATION\FULL_RUN_1.txt`…`3.txt`, `VALIDATION\FULL_RUNS_SUMMARY.json` |
| Containment / PID-oracle determinism | r3 primary isolated 62/64 rc=0 (2 honest failures under load); r4 target 24/24; orphan 20/20; injected-termination 20/20; whole-file 5/5 each; probe 56/56. Independent re-review confirmed 0/24 → 62/64 and falsified the orphan `delay` timing claim | **INDEPENDENTLY VERIFIED on this host only** | `...\REPORTS\S1_CONTAINMENT_DETERMINISM.md` §4; `D:\audit\final_independent_reassessment_20261126\REPORTS\CONTAINMENT_REASSESSMENT.md` §§2–7; `SCRATCH\C\runs\` |
| Cancellation, persistence, crash recovery, concurrency | 246 repetitions / 21 case types; 0 pre-boundary durable PASS, 0 deadlocks; 52/52 fresh-process deadline-invocation rc=0 (184/184 test executions) | **INDEPENDENTLY VERIFIED on this host only** | `...\REPORTS\S3_STRESS_PERSISTENCE_CANCELLATION.md` §§3–4; `AGENT_RECORDS\S3\` |
| Security regressions (targeted) | EXT-01 4, EXT-02 22, EXT-03 5, EXT-05 8, EXT-06 17, EXT-07 9 targeted tests passed; all rc=0; compile 89 files / 0 errors | **INDEPENDENTLY VERIFIED on this host only** | `...\REPORTS\FULL_REGRESSION_REPORT.md` §targeted suites; `...\REPORTS\STABILITY_SPRINT_REPORT.md` §4 |
| Commit-boundary semantics | 13 targeted tests and a 5-case process matrix rc=0; advisory read-only helper confirmed to have no production importer. Scope note: pre-boundary durable state only. A cross-process cancellation after the pre-downstream guard recheck can still deliver a PASS-shaped object to the downstream callback (unresolved High finding); see `docs/EXTERNAL_ASSESSMENT_STATUS.md`. | **INDEPENDENTLY VERIFIED on this host only** | `...\REPORTS\COMMIT_BOUNDARY_SEMANTICS.md`; `D:\audit\final_independent_reassessment_20261126\REPORTS\ARCHITECTURE_SCOPE_REVIEW.md` §2.6 |
| Real EDA (OpenROAD/OpenSTA/KLayout/Yosys EQY) | 16/16 stages, 98/98 checks, exit 0 were established from **raw logs of the frozen/earlier tree**, audited read-only; KLayout 0.30.13 pin-correspondence re-run was KLayout-only (netgen unavailable) | **INDEPENDENTLY VERIFIED as an audit of earlier evidence — NOT a re-run on this candidate** | `D:\audit\independent_external_review_20261125\REPORTS\EDA_REVIEW.md` §§1–3; `D:\audit\final_independent_reassessment_20261126\REPORTS\EDA_SCOPE_REASSESSMENT.md`; `REPORTS\AGENT_F_ROOT_CAUSE.md` §"Real-tool re-run" |
| Production `verification_tools=` acceptance with a real tool | The authority wiring is real code, but the test invokes only `python -c "print('managed-tool ok')"`; other S2/S3 runs use labelled simulated verification | **STATIC REASONING ONLY for authority wiring; real-tool acceptance NOT TESTED** | `tests\test_ext03_eco_authority.py:57–66`; `ARCHITECTURE_SCOPE_REVIEW.md` §3; `FINDINGS_REGISTER.md` R-05 |

WSL appeared **only as an EDA tool host for the frozen/earlier tree**. It was not
used as a second pytest host, not used to run the containment/cancellation
harnesses, and no real-tool EDA flow was executed against the fixed candidate.

## 2. Environment status matrix

| Environment / claim | Status | Basis / limit |
|---|---|---|
| One Windows x86-64 host `mi`, Python 3.10.11, pytest 9.2.0, `D:\` roots, PowerShell | **INDEPENDENTLY VERIFIED** (full suite, containment, cancellation, targeted security regressions) | Reports and raw logs cited in §1 |
| A second Windows host (any version/build) | **NOT TESTED** | No second host was used; no cross-host result exists or is claimed |
| Another Windows version (other than the validated host), another Python or pytest version | **NOT TESTED** | Host-specific versions are recorded, not portability ranges |
| Native Linux / native POSIX (pytest suite, containment, cancellation, crash harness) | **NOT TESTED** | POSIX branches exist in source but are marked `pragma: no cover`; no POSIX test was executed |
| WSL2 Ubuntu-24.04 as a pytest / test-execution host | **NOT TESTED** | WSL was used only for EDA tool execution on the earlier tree |
| Another WSL distribution, macOS, or any other OS | **NOT TESTED** | No evidence exists |
| Real-tool EDA on the fixed candidate (`e591c30a…`) | **NOT TESTED** | Earlier EDA evidence is attributed to the frozen/earlier tree; `REPRODUCING.md` §7 procedure was not executed on this candidate |
| Real-tool / production `verification_tools=` acceptance on the fixed candidate | **NOT TESTED** | Only a `python -c` managed-tool stub and labelled simulated verification were used |
| Windows KLayout native execution | **BLOCKED / NOT TESTED** on the validated host (missing Qt5 system libraries); WSL paths are host-local | `docs/SETUP.md` §2; `docs/REGRESSION.md` stage 13–15 sections |
| End-to-end containment on detached/WSL2 descendant trees | **NOT TESTED** (declared unsupported by design) | `docs/THREAT_MODEL.md` §3.2; `execution_trust.py` A5 |

## 3. Environment-specific dependencies (source anchors)

These dependencies are part of the *validated environment*, not portable
properties. They are recorded so that a second-host result is never inferred.

1. **Windows python-launcher re-exec chain.** The containment root cause was a
   variable 2–4 stage `python.exe → python3.10.exe` re-exec producing two pidfile
   writers and a Toolhelp32 snapshot race. The fix is confined to the two
   containment test harnesses (single-writer pidfile + bounded waits). POSIX
   behaviour of those harness changes is **STATIC REASONING ONLY / NOT TESTED by
   execution**. Anchors: `S1_CONTAINMENT_DETERMINISM.md` §2 and §6;
   `CONTAINMENT_REASSESSMENT.md` §2; `assistant/runner.py:529–537` (`_snapshot_pids`
   Windows/POSIX branches, `pragma: no cover`).
2. **`DETACHED_PROCESS` / `CREATE_NEW_PROCESS_GROUP`.** Windows-only creation
   flags in the containment tests; the Windows detached test is skipped on
   POSIX (pre-existing skip, unchanged). Anchors:
   `tests/test_r3_r205_containment.py:63–65,266`; `tests/test_r4_containment.py:200–202`;
   `CONTAINMENT_REASSESSMENT.md` §8. Native POSIX detached-tree containment:
   **NOT TESTED**.
3. **`taskkill /T /F`.** The validated tree-kill path uses Windows `taskkill`
   (`runner.py:851–872`); a direct-kill helper separately uses `taskkill /F` on
   Windows and `SIGKILL` on POSIX (`runner.py:556–568`). A test explicitly skips
   off Windows (`tests/test_third_review_regressions.py:451`). The POSIX
   `start_new_session`/`killpg` path (`runner.py:488–503,880–886`) is
   **STATIC REASONING ONLY / NOT TESTED**.
4. **Windows `OpenProcess` PID oracle vs POSIX `/proc`.** Production and test
   liveness oracles are OS-specific: `OpenProcess`/`GetExitCodeProcess` on
   Windows; `/proc/<pid>/stat` + `os.kill(pid,0)` on POSIX. Anchors:
   `assistant/runner.py:577–628`; `assistant/r61_identity.py:12–15,198–222,272–279`;
   `assistant/execution.py:365–411,466–468`;
   `tests/test_r3_r205_containment.py:78–98`; `tests/test_r4_containment.py:72–93`.
   Only the Windows branch was executed.
5. **`os.kill(pid, 0)` zombie behaviour.** The *test-local* `_pid_alive` helpers
   use bare `os.kill(pid,0)` and report a zombie as alive; the runner's
   `_pid_alive_independent` adds a `/proc` state check rejecting `Z`/`X`
   (`runner.py:612–627`). A POSIX-only false failure is plausible by static
   reasoning; it is **NOT TESTED** by execution. Anchor:
   `CONTAINMENT_REASSESSMENT.md` §8, residual #6.
6. **WSL/KLayout path conventions.** `/mnt/d/tools/KLayout/bin/klayout.sh` is a
   recorded host-local path (`assistant/tools.py:212`;
   `regression/checks/stage00_preflight.py:219`; `docs/SETUP.md:38,64`), not a
   portable default. Other hosts must re-probe; **NOT TESTED** elsewhere.
7. **Bounded observation windows.** The 0.8 s PID probe, 1.0 s liveness window,
   1.5 s detached probe and 30 s lock timeout are host-load dependent and fail
   honestly; they are not guarantees on slower hosts. Anchors:
   `S1_CONTAINMENT_DETERMINISM.md` §6; `CONTAINMENT_REASSESSMENT.md` §9 item 2;
   `INDEPENDENT_AUDIT_ADDENDUM` Claim 3 as cited in `ARCHITECTURE_SCOPE_REVIEW.md` §2.7.

## 4. Claim classification summary

- **INDEPENDENTLY VERIFIED (execution, one Windows host):** full suite ×3;
  containment/harness stress; cancellation/persistence/crash/concurrency stress;
  targeted security regressions; commit-boundary process matrix.
- **INDEPENDENTLY VERIFIED (read-only audit of earlier raw evidence, not a
  re-run):** frozen/earlier-tree EDA stage execution and KLayout pin-correspondence
  re-run. This does not transfer to the fixed candidate.
- **STATIC REASONING ONLY:** POSIX `killpg`/snapshot/zombie-handling branches,
  read-only helper behaviour, environment-override wiring, WSL KLayout path
  semantics on other hosts.
- **NOT TESTED:** every environment and capability listed as `NOT TESTED` in §2,
  including any second host, any native POSIX suite, and any real-tool EDA on the
  fixed candidate. No second-host result was fabricated to close R-05.

## 5. Documentation changes made under R-05

All edits are confined to `D:\audit\conditional_closure_sprint_20261126\AGENT_F_COPY`
(documentation only); the authoritative list is in
`D:\audit\conditional_closure_sprint_20261126\REPORTS\R05_PORTABILITY_SCOPE_REPORT.md`:

- Added this file (`docs/VALIDATION_SCOPE_AND_PORTABILITY.md`).
- `README.md`: extended the portability caveat with the fixed-candidate scope and
  a pointer to this disclosure.
- `docs/SETUP.md`: replaced the generic "Windows 10/11" / generic Linux-side
  rows with one-validated-host wording and explicit `NOT_TESTED` markers.
- `docs/REPRODUCING.md`: added a current-candidate scope note to §7 and §8 so the
  documented procedures cannot be read as a second-host or fixed-candidate EDA result.
- `docs/RELEASE_QUALIFICATION.md`: added a fixed-candidate validation-scope row
  with explicit `NOT_TESTED` environments.
- `docs/THREAT_MODEL.md`: expanded §3.2 "Other hosts / portability" with the
  environment-specific dependencies and `NOT_TESTED` boundary.

No implementation file, test file, assertion, skip/xfail marker, frozen artifact
or protected repository was modified. No commit, push or publication was made.
