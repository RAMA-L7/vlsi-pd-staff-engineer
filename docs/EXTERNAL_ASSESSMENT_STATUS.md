# External assessment status (2026-11-26)

This note records the verdict and mandatory limitations of the final independent
external quality assessment of candidate `a17bc701…` (757 files). It does not
change code, tests or prior evidence. Earlier dated verdicts are historical and
are superseded by this assessment.

## Verdict

- **Verdict: NOT READY.** This is a supervised academic/research demonstration,
  not a qualified engineering artifact.
- **Quality rating: 2 / 5 — Weak.** Confidence HIGH for findings on this host;
  validation breadth is one Windows environment. No rating is inherited from
  prior reviews.

## Mandatory limitations

1. **Declared scope:** supervised academic/research demonstration only. No
   production readiness, tapeout authorization, silicon signoff, enterprise
   readiness or autonomous RTL-to-GDSII claim is supported. Academic DRC/LVS
   results are not signoff; foundry signoff is BLOCKED.
2. **Unresolved High finding — downstream callback:** a cross-process
   cancellation landing between the final guard recheck and the downstream
   callback can deliver a PASS-shaped `ExperimentResult` (`status=PASS`,
   `qualification_class=PRODUCTION`, `cancelled=false`) to downstream code
   (`assistant/eco.py:3350–3386`; probes P22/P24) while durable state and the
   ledger gate fail closed. Downstream consumers must re-check the ledger and
   must not trust the in-process object. Absolute cancellation-to-downstream
   safety is not claimed.
3. **Reproducibility:** three clean 613-passed/5-skipped runs are authentic, but
   independent runs under concurrent assessment load produced 2 clean and
   1 failed (612 passed/1 failed/5 skipped), plus a load-sensitive targeted
   cancellation failure. A clean streak is not demonstrated as a general
   property; timing-sensitive tests remain load-fragile. Default `pytest -q`
   collects 618 tests; 71 security-critical `tests/r*.py` suites are explicit-run
   only.
4. **Validation breadth:** one Windows host, Python 3.10.11, pytest 9.2.0. A
   second host, a second interpreter, POSIX/WSL-native execution and real-tool
   EDA on the final candidate are **NOT_TESTED**; WSL EDA evidence belongs to an
   earlier tree.
5. **EDA scope:** real-tool EDA evidence (Yosys/OpenSTA/KLayout) belongs to an
   earlier tree; the final candidate has no real-tool EDA rerun. LVS equivalence
   shown is topology-only. I/O pin correspondence was unresolved (32 layout vs
   15 schematic pins) and is now fail-closed. "Genuine LVS PASS" without the
   topology-only qualifier, I/O-level LVS equivalence, signoff and tapeout
   claims are not supported.
6. **Advisory helper:** `assistant/commit_boundary.py` is a read-only, unwired
   interpretation helper; it enforces no authorization. `GateController` and
   `state.json:run_status` are the downstream authority. Agent/skill guidance is
   advisory process guidance, not live AI-agent execution or gate authority.
7. **Evidence integrity and residuals:** the bundle contains 602 files (claim
   601); three bundle-root meta files are unmanifested and two stale verification
   documents inside the bundle report 13,915 files / 14,670 entries; the prior
   external-review manifest has one stale entry; one historical report cites a
   stale assertion hash. Candidate identity and its own 1,356-entry manifest
   verify. R-07, R-09 and R-11 are escalated as evidence-trust limitations;
   R-08 is corrected; R-10 is accepted with containment. Latent High concerns:
   two MAC systems with differing key labels/canonicalization and non-PASS
   records defaulting to `qualification_class="PRODUCTION"`. Operational limits:
   orphan temp files at pre-replace crashes, no directory fsync, no monotonic
   rollback anchor, module-global identity roots.

No finding is presented as resolved without supporting evidence; no rating is
inherited from prior reviews. Source: `final_external_quality_assessment_20261126/REPORTS/`.
