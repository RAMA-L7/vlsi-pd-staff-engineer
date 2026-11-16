# Initial Review (External Static Review)

> **Provenance:** This document records an **external static review** of the
> `RAMA-L7/vlsi-pd-staff-engineer` repository. Its findings are
> **static observations that require verification against the current checkout**
> before any change is made. Nothing below is claimed as a reproduced failure
> until revalidation evidence exists. Revalidation status for every finding is
> tracked in `docs/reviews/remediation-tracker.md`.

---

## INITIAL REVIEW

### Verdict

Worth developing into a supervised PD analysis and ECO assistant. Current
evidence does not justify autonomous RTL-to-GDSII or signoff claims.

### Ratings

| Dimension | Rating |
|---|---|
| Process and organizational concept | 7/10 |
| Educational value | 6/10 |
| Technical accuracy | 4/10 |
| Executable automation | 3/10 |
| Validation and reproducibility | 2/10 |
| Production/signoff readiness | 1/10 |
| **Overall** | **4/10** |

### Strengths

- Explicit stage ownership and handoff artifacts.
- Verification after netlist changes.
- Evidence before closure claims.
- Root-cause investigation and regression checks around ECOs.

### Findings to verify

- **A.** Stage gates appear primarily prompt-based. Session-start instructions
  do not themselves enforce transitions or bind verification results to exact
  inputs.
- **B.** `examples/test-counter/test_framework.sh` appears to resolve
  repository-root files relative to the example directory.
- **C.** Its synthesis test uses generic synthesis without Sky130 technology
  mapping, despite subsequent Liberty-based STA.
- **D.** Its LEC setup appears to misuse saved designs and invokes `equiv_make`
  without the required output module argument.
- **E.** STA can apparently pass without any reported slack when the process
  returns zero.
- **F.** `synthesis_targets.json` mislabels Sky130 PVT values. For example,
  `tt_025C_1v80` means 25 °C and 1.80 V. The claim that public Sky130 has only
  TT is incorrect; distinguish unavailable local files from unavailable PDK
  corners.
- **G.** CTS guidance treats `set_clock_latency -source` as a useful-skew
  implementation and oversimplifies the effect of common insertion delay on
  register-to-register setup.
- **H.** LEC guidance suggests exclusions for added registers, clock gating, or
  isolation that could hide verification gaps.
- **I.** Pipeline-latency changes are allowed despite requiring architectural
  approval and a different verification contract.
- **J.** Signoff guidance blurs global-route RC estimates and routed extraction,
  suggests arbitrary ±10 % margins as a fallback for missing corners, and does
  not demonstrate complete post-fill extraction/timing closure.
- **K.** LVS, EM, and low-power verification coverage is described more broadly
  than the demonstrated executable integration.
- **L.** Name-based isolation/level-shifter counts do not establish correct
  crossing behavior, supplies, controls, retention, or power sequencing.
- **M.** Fixed macro-placement rules and the three-fix rule are sometimes
  presented as universal conclusions rather than investigation heuristics.
- **N.** Published validation is narrow and does not establish a reproducible
  complete physical implementation flow.

These findings were static observations, not reproduced failures. Revalidate
each one. If a finding is incorrect or already fixed, document the evidence
rather than making unnecessary changes.
