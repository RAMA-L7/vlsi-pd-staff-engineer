# Release-Candidate Remediation Report (Round RC)

**Recorded UTC:** 2026-11-25T14:00:40Z · **last updated:** 2026-11-25T14:02Z
**Scope:** DOCS ONLY documentation of the release-candidate (RC) preparation under
`D:\audit\release_candidate_20261125\`. No source-code change is made by this report,
no commit/push/publication is performed, no readiness declaration or numerical
rating is assigned by this document. All security findings below are dispositions
recorded from the cited audit evidence, not new testing claims by this writer.

## (a) Starting and final content identity

**Governance record (authoritative starting snapshot):**
`D:\audit\release_candidate_20261125\GOVERNANCE\STARTING_IDENTITY.json`, UTC
`2026-11-25T13:28:16Z` (SHA-256 `b76feb49e4e44de250ffd1441bc92562c3b326fca83b6791793cb98f619f0736`):

| Item | Recorded value |
|---|---|
| Reviewed commit | `fa2bc751b3add19d4db250d01dac8de4f88ca347` (short `fa2bc75`) |
| Reviewed content files / tree | 228 / SHA-256 `265d90310e354a9162b5455c35f75b61e301ab79eab41d46896a240f672df615` |
| Remediation source | R7.2 `WORKING_COPY` (231 files) |
| RC starting content / tree | 231 files / SHA-256 `67dff40684d1da6f83402a3a300193fa14a33a6025cd6394810d35f51cb76c8b` |
| `identical_to_r72` at start | `true` |
| Main checkout | `d8f2117e0e09df03e633ff2789faa699e09e748d`, branch `main`, 38 dirty paths |

Independent re-check at report time (2026-11-25T14:00Z): main checkout
`D:\audit\m1g_mapping_20261117` still has HEAD `d8f2117e0e09df03e633ff2789faa699e09e748d`
and exactly 38 dirty paths — **preserved, untouched**. Per-file starting hashes are in
`GOVERNANCE\STARTING_SHA256.txt`.

**Final content identity (post RC fix, before/independent of the two RC docs).**
The RC fix changed exactly two content files relative to `STARTING_SHA256.txt`:

| File | Starting SHA-256 | Final SHA-256 |
|---|---|---|
| `assistant/controller.py` | `7f9c9201b18248a497ebbd3d80e441f0d294b1dac3bef35dcaf899457f0445f9` | `31467a7c45f6f9dc86692c36c54bcb6a0248d1b93a51a87162afa1a8add40b3c` |
| `assistant/eco.py` | `2922deb64c5b27b3a1df9a4488b0ea06d8cc606a83207836c397797380624df5` | `7ac183fcc6cc98b302a40df39ad9824a2f8a93ddb4c12c783475808c837345d7` |

Using the same method as `D:\audit\publication_evidence_20261118\rc_setup.py`
(content files under `WORKING_COPY`; exclusions `.git`, `__pycache__`,
`.pytest_cache`, `workspace`, `evidence`; SHA-256 of `json.dumps(tree, sort_keys=True)`):

- RC content tree **immediately after the code fix, before RC documentation**:
  231 files, SHA-256 `67c116b39249b287f88774696776f2ff279e39edf6aeee4090c1cd20890d50ad`
  (historical snapshot, no longer reproducible verbatim because other workstream
  documentation was added concurrently).
- Stable **audited source-content identity**, excluding all `docs/**` documentation
  (audit records, including this report and the tracker) under the same hashing
  method: **178 files**, SHA-256
  `36877744fe3afb25c0e3d09fb1d901e91160d52b13a11c2837bfc93fae84e00f`.

At 14:01Z other workstreams had concurrently added `docs/RELEASE_QUALIFICATION.md`,
`docs/REPRODUCING.md` and `docs/THREAT_MODEL.md` (235 content files in total), so
the fully assembled tree digest is a moving target during documentation
assembly. The coordinator's final report (`FINAL_REPORT.md`) is the authoritative
place for that final digest; this report does not claim it.

## (b) A7-P0-01 — reproduction and closure evidence

**Finding (P0, from Independent Audit Round 7):** an unauthenticated
`check_index` let one genuine `eco_sta` evidence item be appended to
`check_index["eco_lvs"]`; a gate requiring both checks then qualified with a clean
ledger integrity scan. Root cause: `GateController._evaluate_gate` resolved evidence
by index without requiring `ev.check_id == check_id`; `check_index` was not MAC-bound.
Original coordinator reproduction: `D:\audit\independent_audit_round7_20261124\LEAD_VERIFICATION\coord_check_index_p0.py`,
3/3 qualified with `ledger_problems=[]`.

**Exact RC reproduction commands (executed harnesses, preserved in evidence):**

```powershell
& "D:\tools\venv\Scripts\python.exe" "D:\audit\publication_evidence_20261118\rc_phase1_repro2.py"
& "D:\tools\venv\Scripts\python.exe" -c "import json;d=json.load(open(r'D:\audit\release_candidate_20261125\EVIDENCE\A7P0_BEFORE_AFTER.json'));print(d['reviewed_fa2bc75']['reproduced'],d['release_candidate']['reproduced'])"
```

`rc_phase1_repro2.py` copies reviewed `fa2bc75` content (R6.3 `WORKING_COPY`) into
`VULNERABLE_REVIEWED_191rvo6c` (excluding `.git`) and runs the same embedded
ledger-only exploit against both that checkout and the RC `WORKING_COPY`, with
`ASSISTANT_LEDGER_KEY=rc-phase1-key`. The first attempt
(`rc_phase1_repro.py`, read-only `.git` objects) was superseded by `repro2`; only
the `repro2` results are authoritative.

**Observed results** (`EVIDENCE\A7P0_BEFORE_AFTER.json` and `.txt`, SHA-256
`5d2c408cb50345947765518f22102f8fa3c2524e64f3cf78b5fb40ad34ddc8a5`); the
verifier one-liner printed `True False`:

| Check | `reviewed_fa2bc75` (vulnerable) | Release candidate (fixed) |
|---|---|---|
| `have_boundary` | `true` | `true` |
| `evidence_check_id` | `eco_sta` | `eco_sta` |
| `qualified` | `true` | `false` |
| `decision` | `CLEAN_PASS` | `BLOCKED` |
| `downstream_ok` | `true` | `false` |
| `ledger_problems` | `[]` | `check_index/v1:eco_lvs:check-mismatch:59ffe7e3621c4303`, `check_index/v1:eco_lvs:unbound-index`, `check_index/v1:eco_sta:cross-check-reuse:59ffe7e3621c4303` |
| `reproduced` | `true` | `false` |

Per-case durable state is preserved at
`EVIDENCE\A7P0_reviewed_fa2bc75\p0_dgr647zj\state.json` and
`EVIDENCE\A7P0_release_candidate\p0_cpm4yizp\state.json` (plus `r.rpt` reports and
the RC lock sidecar). **A7-P0-01 was reproduced on the reviewed commit and closed
in the RC**; no cross-check proof reuse, no downstream authorization, and three
explicit integrity problems in the fixed tree.

## (c) Finding dispositions (all six)

| Finding | Severity | Where fixed | Disposition | Decisive evidence |
|---|---|---|---|---|
| **A7-P0-01** — unauthenticated `check_index` cross-check proof reuse | P0 | R7.1 (proof-side MAC-bound `check_id`; authority binding; MAC-bound `check_index/v1`) | **FIXED AND VERIFIED** (closed in RC) | §(b): before `qualified=true/CLEAN_PASS/downstream_ok=true/ledger_problems=[]`; RC `qualified=false/BLOCKED/downstream_ok=false/3 integrity problems` |
| **C4** — concurrent benign write erases blocking FAIL → stale PASS qualifies | P0-class | R7.2: single `_mutate` transaction + fresh-under-lock reads | **FIXED AND VERIFIED** | R7.2 `PHASE0\BASELINE_C4_C3.json` (baseline `after_qualified=true`, 3/3 reproduced) and `R71_HARNESS_RESULTS.json` (`after_qualified=false`, `BLOCKED`, verdict PASS/severity NONE); R7.2 `FINAL_REPORT.md` (13 reps denied) |
| **C3** — two gates qualify from one proof nonce via stale save | P1 | R7.2: nonce consumed in the same committed transaction; fresh reads | **FIXED AND VERIFIED** | R7.2 `R71_HARNESS_RESULTS.json`: `qualified_winners=1`, durable registry has one owner, second child `q=false`; pre-fix `qualified_winners=2` |
| **D4** — ECO acceptance uses stale controller evidence view after concurrent FAIL | P1 | R7.2: fresh durable reads at `latest_evidence`/`all_evidence` and immutable accepted snapshot | **FIXED AND VERIFIED** | R7.2 `FINAL_REPORT.md`: `accepted=false`, `controller_latest == durable_latest`, clean `EcoMissingEvidenceError` |
| **Nonce TOCTOU** (A7-P1-01; cross-process double-claim) | P1 | R7.2: single transaction + fresh durable reads; 30 s lock fails closed | **FIXED AND VERIFIED** | R7.1 adjudication `exploitable=true` (2/5, 6/6 one-winner); R7.2 `FINAL_REPORT.md` C3/nonce/writers 13 reps denied; `REPORTS\AGENT4_NONCE_RECON.md`: post-fix `winners=1`, fresh reclaim denied |
| **P0-A cancel-at-persistence regression** — concurrent `cancel()` at the PASS-persistence barrier leaves durable PASS | P0-class | RC: immediate in-process cancellation latch observed by `_AcceptanceGuard` | **FIXED AND VERIFIED** | Pre-fix R7.2 `LEAD_VERIFICATION\CANCEL_STRESS.json` `total=20, fails=3` (Agent 3: 4/20); RC `EVIDENCE\CANCEL_STRESS_AFTER.txt` `total=30, fails=0, detail=[]` (SHA-256 `725368bf147cb1e3b0116dc1c6b7f6f5503a09ce26ef5d43e7bf554953fa6af5`) |

**Open security findings among these six: none.** Open *validation* items are
listed in §(f) (full suite PENDING, real EDA NOT_TESTED, reconstruction/manifest
pending) and are not dispositions of the six findings.

RC P0-A delta (verified by direct diff against R7.2):
`assistant/controller.py` adds `self._cancel_latch`, sets it synchronously at the
top of `cancel()` before `_mutate`, exposes `cancellation_requested()`, and clears
it on restart; `assistant/eco.py` changes the guard's `cancelled` callback to
`self.controller.cancellation_requested() or self.controller.run_status == "CANCELLED"`.
A failed cancel transaction still leaves the latch set (fail closed).

**Exact cancel-stress commands:**

```powershell
# before fix (R7.2, 20 reps)
& "D:\tools\venv\Scripts\python.exe" "D:\audit\third_remediation_round7_2_20261124\LEAD_VERIFICATION\coord_cancel_stress.py"
# after fix (RC, 30 reps)
& "D:\tools\venv\Scripts\python.exe" "D:\audit\publication_evidence_20261118\rc_cancel_stress.py"
```

Both drive
`tests/test_r3_r201_acceptance_lifecycle.py::test_race_barrier_cancel_at_persistence_is_detected`.

## (d) Architecture changes / consolidation

The RC inherits the R7.1/R7.2 consolidation and adds one fail-closed cancellation
latch; no new parallel authority was introduced. Four boundaries are authoritative:

1. **One transaction authority** — `GateController._mutate()`
   (`assistant/controller.py:1358`): cross-process lock → fresh durable reload →
   invariant validation → fsync+replace → adopted state; re-entrant so
   qualification, nonce consumption and history commit atomically. All 12 `_save()`
   call sites are routed through it; the inline nonce writer was removed (R7.2).
   RC sets the immediate cancel latch *before* entering this path.
2. **One evidence representation** — `Evidence` (`assistant/evidence.py:720`),
   MAC-bound `check_index/v1`, canonical `Status` values
   (`assistant/statuses.py`), one dependency/revalidation model and one
   view-compatibility contract; ledger integrity verification detects deleted,
   unbound and cross-check references (three problems in the RC attack case).
3. **One proof-verification boundary** — `ExecutionBoundary`
   (`assistant/execution_trust.py:724`) with a MAC-bound `check_id` set at the
   trusted runner boundary; `r6_authority`/`state_machine` require
   `proof.check_id == envelope.check_id == required check`; `run_managed()` via
   `VerificationContext.spawn()` is the production execution path, with
   WSL2/detached containment classified honestly rather than silently trusted.
4. **One acceptance state machine** — `_AcceptanceGuard`
   (`assistant/eco.py:123`) is the single source of truth for ECO terminal state:
   irreversible first-invalidation latch, `transaction_token` record identity,
   parent chaining for re-entrant transactions, deadline/cancel/mutation reasons;
   gate-level terminal states come from `assistant/state_machine.py` and the
   canonical statuses in `assistant/statuses.py`. RC makes cancellation visible
   at the commit guard even when the durable cancel transaction is still in flight.

## (e) Validation results (separated by class)

| Class | Result at report time |
|---|---|
| **Unit / pytest (RC tree)** | **PENDING** — full-suite run still in progress; `EVIDENCE\FULL_SUITE.txt` at 14:00Z shows progress markers to 76% with two intermediate `F` positions and several `s` skips, no final summary. Coordinator will finalize. Do not infer a pass/fail count. |
| **Targeted security regressions** | RC cancel stress 30/30 pass (`0` fails). Inherited from R7.2: `r72_*` 15/15; R7.1 `r71` 12/12; `r63` 17/17; historical targeted 140/140. The inherited R7.2 full suite was 551 passed / 6 failed / 5 skipped; these results predate the RC latch and were not re-adjudicated by this report. |
| **Stage-00 preflight** | `EVIDENCE\PREFLIGHT.txt` and `PREFLIGHT\stage00_report.txt`: `OVERALL=PASS`, `mandatory_failures=0`; all mandatory tool/config/collateral checks PASS. Three optional checks FAIL/NOT_TESTED (see §(f)). |
| **Simulated-tool integration** | **NOT_TESTED on the RC tree** — simulated runner/fake-executable integration evidence belongs to earlier rounds (SR-04/TR-04/R7.2); no fresh RC simulated-tool integration run is recorded in `EVIDENCE\`. |
| **Real EDA execution** | **NOT_TESTED for the RC tree.** One attempted baseline is recorded and failed to produce stages: `EVIDENCE\EDA_RUN_A.txt` → `status=INCOMPLETE`, `exit_code=1`, `stages_recorded=0`, note `run dir found but no stage results parsed; stages remain NOT_RUN`; `baseline_rc_20261125_a.log` contains a PowerShell `git rev-parse` NativeCommandError. No fresh stage 00–15 results, netlist, SPEF or STA report exists for RC. Historical EDA runs on older trees are not evidence for this tree. |

## (f) Failed / skipped / blocked / unsupported checks

- **Full suite:** PENDING (in progress at report time); intermediate `F` markers are
  not a final result and are not adjudicated here. Coordinator finalizes
  `EVIDENCE\FULL_SUITE.txt`.
- **Inherited failures/skips:** R7.2 recorded 551 passed / 6 failed / 5 skipped
  (timing/persistence/fixture issues, pre-existing skips). Not re-adjudicated after
  the R7.2 P0 halt or the RC latch; not claimed fixed.
- **Preflight optional failures:** `rtl_yosys_elaboration` = `NOT_TESTED - yosys
  unavailable or no RTL provided`; `sdc_exists` = FAIL (`no SDC specified`);
  `pdk_root_exists` = FAIL (`no PDK specified`). Mandatory checks all PASS; no
  mandatory dependency missing.
- **Real EDA baseline:** INCOMPLETE, 0 stages parsed (see §(e)); blocked for this
  tree, not a product PASS or FAIL verdict.
- **Unsupported by declared scope** (from `baseline_manifest.json`): IR drop, EM,
  antenna, post-metal-fill extraction/closure, qualified/signoff-grade parasitic
  extraction, multi-corner closure beyond locally available Sky130 tt/ss/ff,
  tapeout readiness, macro-based designs. These are `UNSUPPORTED`, not skipped PASS.
- **Containment limitations (inherited, classified not prevented):** non-cooperative
  Python threads cannot be forcibly killed (detection/rehash only); WSL2/detached
  grandchildren are not universally containable and fail closed as
  `authorization_safe=False`/`wsl_uncontained`.
- **Availability/durability caveats:** 30 s ledger lock timeout fails closed;
  Windows directory fsync is effectively a no-op; lock sidecar persists.
- **Reconstruction / evidence bundle / final manifest:** PENDING —
  `rc_bundle.py` is prepared but `EVIDENCE_BUNDLE\` does not exist at report time.

## (g) Reproducibility status

| Item | Status |
|---|---|
| Reviewed content `fa2bc75` copy | Reproducible from R6.3 `WORKING_COPY` (see `rc_phase1_repro2.py`); starting hashes in `GOVERNANCE\STARTING_SHA256.txt` |
| A7-P0-01 before/after | Reproduced with exact command in §(b); summary `A7P0_BEFORE_AFTER.json`, per-case `state.json` preserved |
| P0-A cancel regression | Exact commands in §(c); pre-fix 3/20 vs RC 0/30, raw JSON preserved |
| Content identity | Reproducible with `rc_setup.py::tree()` method; starting and final digests in §(a) |
| Preflight | Reproducible via `regression\checks\stage00_preflight.py`; result JSON + report preserved |
| Real EDA baseline | Attempt recorded but **not reproducible as a PASS** — INCOMPLETE, 0 stages |
| Full pytest suite | PENDING — coordinator finalization required |
| Reconstruction/zip bundle | PENDING — not yet generated |

## (h) Evidence bundle location and verification

Primary evidence root:
`D:\audit\release_candidate_20261125\EVIDENCE\` with
`A7P0_BEFORE_AFTER.json/.txt`, `A7P0_reviewed_fa2bc75\p0_*\`,
`A7P0_release_candidate\p0_*\`, `CANCEL_STRESS_AFTER.txt`, `FULL_SUITE.txt`,
`PREFLIGHT.txt`, `PREFLIGHT\stage00_*`, `EDA_RUN_A.txt`,
`EDA_RUN_A\baseline_test_counter_rc_20261125_a\`.
Governance: `GOVERNANCE\STARTING_IDENTITY.json`, `STARTING_SHA256.txt`.
Prior rounds: `D:\audit\third_remediation_round7_1_20261124\` and
`D:\audit\third_remediation_round7_2_20261124\` (`FINAL_REPORT.md`, `PHASE0\`,
`ADVERSARIAL\`, `LEAD_VERIFICATION\`, `REPORTS\`).
Harnesses: `D:\audit\publication_evidence_20261118\rc_setup.py`,
`rc_phase1_repro.py`/`rc_phase1_repro2.py`, `rc_cancel_stress.py`, `rc_bundle.py`.

Verification commands already exercised by this report:

```powershell
& "D:\tools\venv\Scripts\python.exe" -c "import json;d=json.load(open(r'D:\audit\release_candidate_20261125\EVIDENCE\A7P0_BEFORE_AFTER.json'));print(d['reviewed_fa2bc75']['reproduced'],d['release_candidate']['reproduced'])"
Get-FileHash -Algorithm SHA256 -LiteralPath "D:\audit\release_candidate_20261125\EVIDENCE\A7P0_BEFORE_AFTER.json","D:\audit\release_candidate_20261125\EVIDENCE\CANCEL_STRESS_AFTER.txt","D:\audit\release_candidate_20261125\GOVERNANCE\STARTING_IDENTITY.json"
```

Expected: `True False` and hashes `5d2c408c…`, `725368bf…`, `b76feb49…` (full
values in §(a)–(c)). Bundle: `rc_bundle.py` will create
`EVIDENCE_BUNDLE\rc_evidence_bundle.zip`, `SHA256_MANIFEST.txt`, `BUNDLE_META.json`
and `REPRODUCE.md`; verify with the one-liner in `REPRODUCE.md` after the
coordinator generates it. Until then the bundle is **PENDING**, not verified.
`D:\audit` paths are local review paths, not public evidence access paths; no
publication is authorized.

## (i) Residual risks

1. Full pytest suite is not finalized; two intermediate failures and inherited
   6 failures / 5 skips are unresolved for this tree.
2. No real EDA execution against the RC tree: physical/timing/DRC/LVS claims for
   this exact content are `NOT_TESTED`.
3. Reconstruction, final evidence bundle and final manifest are pending; no
   independent fresh-clone verification of the RC tree has been recorded here.
4. Inherited cooperation limits: non-cooperative threads, WSL2/detached process
   trees; fail-closed classification substitutes for containment, not proof of it.
5. Lock-timeout (availability), Windows directory-fsync no-op, persistent lock
   sidecar, and manifest provenance gaps (`<missing>` PDK/library hashes, dirty
   ORFS flow) remain as recorded in `EDA_RUN_A\baseline_manifest.json`.
6. This report is a documentation pass; it does not re-run the adversarial
   harnesses or re-adjudicate earlier round verdicts.

## (j) Release decision recommendation

**READY FOR EXTERNAL RC EVALUATION**

All six tracked findings are fixed and verified in the cited evidence; the
reviewed P0 reproduces on `fa2bc75` and is closed in RC, and the P0-A
cancel-at-persistence regression is closed with 0/30 failures. This recommendation
is a hand-off for **external** RC evaluation only — it is **not** a signoff, not a
claim of qualified signoff/tapeout readiness, and not a numerical rating. The
external reviewer must assign any rating after the coordinator finalizes the full
suite and after real EDA / reconstruction evidence is available; items in §(f)
remain open at the time of this report.
