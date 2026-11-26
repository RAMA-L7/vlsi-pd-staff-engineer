# Threat model — release-candidate working copy

**Recorded UTC:** 2026-11-25T13:59:20Z
**Document type:** documentation/audit only. No source code was changed by this pass.
**Scope statement:** This document describes the threat model of the **ledger-backed gate/evidence authorization subsystem** used by the supervised academic physical-design (PD) workflow. It is not a qualification verdict, not a signoff claim, and no numerical rating is assigned. All findings are anchored to the release-candidate (RC) working copy whose recorded starting identity is in `GOVERNANCE/STARTING_IDENTITY.json`; the coordinator will restamp the final identity.

## 1. Assets protected by the subsystem

| Asset | Where it lives | Why it matters |
|---|---|---|
| Gate definitions and authoritative gate specs | durable ledger `state.json` (`gate_definitions`, `gate_specs`) | Determine which checks a stage boundary requires |
| Evidence records / MAC-bound envelopes | durable ledger (`evidence`, `r6_evidence_envelopes`) | Carry status (PASS/FAIL), provenance (real vs simulated tool), command, reports and digests |
| `check_index/v1` | durable ledger (`check_index`: `check_id -> [evidence_id]`) | Lookup hint and MAC-bound authorization association between a check and its evidence |
| Proof-nonce ownership | durable ledger (`used_proof_nonces`) | Prevents one execution proof from authorizing more than one evidence item |
| Waivers, summaries, run status, terminal outcomes | durable ledger | Must not turn failed engineering evidence into an authorized PASS |
| Cancellation / acceptance terminal state | durable ledger plus in-process latch | A cancelled run must not acquire a persistent PASS at commit time |
| Trust anchor | external `ASSISTANT_LEDGER_KEY` environment value, or an in-memory ephemeral key | Never serialized into the ledger, logs or repr |

## 2. Trust boundaries

| # | Boundary | Enforcement observed in source | Failure mode when violated |
|---|---|---|---|
| B1 | **Ledger file → process** (data at rest is untrusted) | HMAC-SHA256 over canonical JSON, domain-separated (`evidence/v1`, `authorization/v1`, `gate_definition/v1`, `dependency/v1`, `execution/v1`, `tool_spec/v1`, `check_index/v1`) and context-bound (session / repo / revision) in `assistant/trust.py`; legacy in-ledger key `_integrity_key` is purged and never used (`controller.py`) | Unverifiable bindings are not admitted as authoritative; evaluation fails closed |
| B2 | **Process → process** (key provenance) | With a provisioned external key, the session id is stable so MACs verify in CLI subprocesses; with no key, a per-process ephemeral key is generated and never written (`trust.py`) | A fresh process without the external key cannot verify persisted MACs; persisted authorization does not carry over |
| B3 | **Managed execution → proof** | Frozen approved-image manifest `[(realpath, sha256)]`, signed exact-argv allowlist, fresh nonce, execution/candidate/consumed/dependency digests (`assistant/execution_trust.py`) | Unknown image or argv is `UNTRUSTED`/`UNKNOWN`; no TRUSTED proof is issued |
| B4 | **Proof → check authorization** (A7-P0-01 remediation) | `check_id` is inside the MAC-bound proof payload (`execution_trust._canonical_proof_payload`); `check_index/v1` associations are MAC-bound; authoritative index membership is rebuilt from MAC-verified evidence, never from the raw index (`controller.py`) | A proof for check X cannot be transplanted onto evidence for check Y; substituted, duplicated, dangling, deleted or cross-check-reused index entries make resolution non-authorizing |
| B5 | **Reader → freshly committed state** (concurrency) | `GateController.latest_evidence()` / `all_evidence()` read a fresh durable snapshot under the cross-process state lock; `_mutate()` acquires the lock, re-reads durable state, rebinds derived views, validates invariants, then `fsync` + `os.replace` in one transaction (`controller.py`); `_save()` fsyncs before rename | A stale in-memory PASS cannot be returned to ECO after another process committed a FAIL; a transaction that would lose a FAIL or introduce a new `check_index` inconsistency aborts and writes nothing |
| B6 | **Cancellation → persistence** (P0-A regression) | `cancel()` sets an immediate in-process latch synchronously before the slower durable cross-process transaction; commit-time re-check observes both latch and durable state (`controller.py`, `eco.py`) | A cancel racing the persistence step cannot hand a cancelled run a terminal PASS |
| B7 | **Supervised caller → ledger** | CLI `gate-record` intentionally accepts a caller-supplied status in this supervised workflow (historical qualification A-1); automated tool checks are validated independently | A human/agent caller is trusted; this is *not* an autonomous false-PASS resistance claim |
| B8 | **Host OS / hypervisor → everything** | No containment claim beyond observed job membership at sampling instants (`execution_trust.py` A5) | Host compromise is outside the guaranteed boundary |

## 3. Adversary model

### 3.1 In scope

- **Ledger-only attacker.** Can read and edit every byte of `state.json` and its artifacts (reports, logs), but does not hold the external key and cannot alter code or the live process. This is the primary adversary for A7-P0-01.
- **Local user / same-user process without the key.** Can run processes, inherit or set environment variables, start/stop helper processes, and race commits. If that process obtains or supplies the provisioned key it becomes a **key holder** and is out of scope (§4).
- **Replay attacker.** Reuses proofs, MACs, evidence ids or nonces from another session, repo, revision, check, domain or run.
- **Concurrency attacker.** Two processes write/read the same ledger: commit a FAIL while another reader holds a pre-FAIL snapshot; attempt to lose an append-only FAIL; interleave qualification, nonce consumption and history writes; cancel during `fsync`/replace.
- **Tool-substitution attacker.** Copies e.g. `cmd.exe` into a writable venv, or supplies an unapproved argv (`-c` style), hoping for a TRUSTED proof.
- **Supervised-caller mistake.** A caller asks for a bypass, force-PASS, N/A waiver or LLM-summary shortcut.

### 3.2 Explicitly out of scope (not claimed resistant)

- **Compromised host**, admin/SYSTEM-level attacker, malicious kernel/driver, or an attacker able to modify the Python process memory or installed package contents.
- **Key holder**: anyone who can read `ASSISTANT_LEDGER_KEY` from the environment, set it for a subprocess, or extract the in-memory key. The external key is the trust anchor; possession of it permits re-signing. Tests use only throwaway test keys.
- **WSL2 guest containment**: guest-to-host or host-to-guest escape, Linux namespace/job escape, `setsid`, process hollowing, and spawn-execute-exit between observation samples are declared unsupported (`execution_trust.py` A5).
- **Foundry signoff / tapeout**: no licensed Calibre/IC Validator/Assura, no foundry deck/runset/calibration/authority; academic DRC/LVS results are not signoff.
- **Network service attacker**: this is a local supervised workflow; no remote API or multi-tenant exposure is claimed.
- **Social engineering of the authorized caller**, or mislabelling academic results as production results. These are process/claim-integrity issues, not ledger cryptographic properties.
- **Other hosts / portability**: pins and fallbacks were validated on one developer host only. For the fixed candidate (`e591c30a…`), all executed validation (full suite, containment, cancellation, security regressions) is from that **one Windows host** (Python 3.10.11, pytest 9.2.0). **NOT_TESTED**: any second host, native POSIX execution, WSL as a test host, and real-tool EDA on the fixed candidate. Environment-specific dependencies are the Windows python-launcher re-exec chain, `DETACHED_PROCESS`, `taskkill /T /F`, the Windows `OpenProcess` PID oracle vs POSIX `/proc`, bare `os.kill(pid,0)` zombie semantics in the test-local oracle, and host-local WSL KLayout paths; POSIX branches are source-only. See `docs/VALIDATION_SCOPE_AND_PORTABILITY.md`.

## 4. What is resisted (with mechanism and local evidence)

| Attack | Resistance | Local audit evidence / source anchor |
|---|---|---|
| Re-point one check at another check's evidence (A7-P0-01) | `check_id` MAC-bound in proof; `check_index/v1` MAC-bound; structural check: duplicate, dangling evidence id, mismatched `record.check_id`, deleted evidence, cross-check reuse | `EVIDENCE/A7P0_BEFORE_AFTER.json`: reviewed `fa2bc75` reproduced with `qualified=true`, `decision=CLEAN_PASS`, `downstream_ok=true`; RC `qualified=false`, `decision=BLOCKED`, `downstream_ok=false` with three `check_index` integrity problems (`eco_lvs:check-mismatch`, `eco_lvs:unbound-index`, `eco_sta:cross-check-reuse`) |
| Forge provenance from ledger contents | Provenance bound with the same external key; no secret key field in any record; legacy `_integrity_key` purged | `assistant/trust.py`, `controller.py` §R6 P0-1 |
| Replay a valid MAC in another domain/session/repo/revision | Domain tags + context binding; `new_session()` invalidates old MACs | `trust.py` self-test (cross-domain, tamper, malformed, session rotation) |
| Reuse one execution proof for two evidence items | `nonce -> evidence_id` ownership ledger under the external key; transaction rejects unbound nonce owners | `controller.py` `_validate_transaction`, `used_proof_nonces` |
| Trust an unapproved tool image or argv | Frozen image manifest (never extended from observed processes) plus exact-argv allowlist; arbitrary `-c` rejected | `execution_trust.py` A1/A2 |
| Stale PASS after concurrent FAIL / input change | Fresh durable reads under cross-process lock for all GA readers; fingerprint check; append-only FAIL cannot be lost | `controller.py` `latest_evidence()`, `_mutate()`, `durable_state_is_current()` |
| Cancel-at-persistence regression (P0-A) | Immediate latch set before the durable transaction; re-check after commit | `EVIDENCE/CANCEL_STRESS_AFTER.txt`: 30/30, `fails: 0`; historical `3/20` before-fix stress in a local audit artifact (`D:\audit\third_remediation_round7_2_20261124\LEAD_VERIFICATION\CANCEL_STRESS.json`) — local, not public evidence |
| Force-pass / summary / waiver shortcuts | Fail-closed gate evaluation; strict N/A rules; summaries are advisory only | `tests/r6_a8_adversarial.py`, `tests/r71_evidence_check_binding.py` (collection verified for this RC; full run not re-executed here) |

## 5. Residual risks and qualifications

1. **Key custody is decisive.** An env-provided key is readable by other processes of the same user; an ephemeral key gives live in-process proofs only and fails closed for persisted derivation. Neither mechanism survives a key holder or host compromise.
2. **Sampling is not containment.** TRUSTED means MAC-valid proof from a manifest-admitted image with observed job membership at sample instants; it does not mean the OS process tree was contained for its entire lifetime.
3. **The index is a hint, not a source of truth.** Authorization comes from MAC-bound evidence and envelopes. A ledger-only edit of `check_index` can still cause a *denial* (BLOCKED) — fail closed is intentional.
4. **Historical A-1 qualification remains.** The supervised CLI `gate-record` primitive accepts caller-supplied PASS; this is a trust decision of the workflow, not an autonomous validator.
5. **Runtime re-validation is partial for this RC.** The before/after A7-P0-01 probe and cancel stress are recorded locally; the full pytest suite and real EDA were not re-executed for this RC (see `RELEASE_QUALIFICATION.md` and `REPRODUCING.md`). Local `D:\audit` paths are audit artifacts, not a public evidence bundle.

## 6. References

- Source inspected read-only: `assistant/trust.py`, `assistant/execution_trust.py`, `assistant/controller.py`, `assistant/transaction.py`, `assistant/eco.py`, `tests/r71_evidence_check_binding.py`.
- Local audit artifacts: `GOVERNANCE/STARTING_IDENTITY.json`, `EVIDENCE/A7P0_BEFORE_AFTER.json`, `EVIDENCE/CANCEL_STRESS_AFTER.txt`.
- Historical qualifications: `docs/reviews/FINAL_INDEPENDENT_QUALIFICATION_REPORT.md`, `docs/reviews/remediation-tracker.md` (historical text, not re-asserted as current status).
