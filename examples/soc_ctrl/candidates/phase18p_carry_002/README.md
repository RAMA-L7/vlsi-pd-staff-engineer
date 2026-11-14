# phase18p_carry_002 — Hierarchical Behavioral Prefix (Sky130 HD synthesis)

**Status:** `NO_QUALIFIED_CANDIDATE` (experimental, not promoted). No physical 04–15, no STA/DRC/LVS/signoff for this candidate.

## Purpose
Phase 18P Approach 1: hierarchical Boolean/prefix carry structure with synthesis-boundary attributes, evaluated for a shallower mapped carry path vs baseline.

## Parent reference
- `examples/soc_ctrl/candidates/phase18k_sum_output_fix/` (RTL SHA256 `253d713b…3604`)

## Candidate RTL
- Path: `rtl/soc_ctrl.v`
- SHA256: `55905fc9e3daa6a90305c597fcafa71193cbee491728c83da5e3465556de772b`
- Hierarchical modules (`pg_bit`, `prefix4_carry`) with `(* keep_hierarchy = "yes" *)`; ports/seq behavior/outputs/latency preserved.

## Implementation architecture
Behavioral 4-bit prefix carry (group P/G L1/L2, C1..C4) expressed in separate modules intended to encourage structure preservation.

## Synthesis method
Yosys 0.69+154, Sky130 HD TT: `synth -top soc_ctrl -flatten; dfflibmap -liberty; abc -liberty` (RC=0). **Result:** hierarchy partially retained but internal carry logic rewritten by ABC into a 4-stage ripple `a21o_1` chain (C1→C2→C3→C4) with `xor3_1` sums; preservation intent not effective.

## Functional verification
- Exhaustive 512/512 combinations vs 18K: **PASS, 0 mismatches**.

## Formal verification status
- **Generic equivalence:** **PASS** by exhaustive 512/512 (0 mismatches). Automated Yosys equiv assertion was inconclusive due to hierarchy-flattening/tooling limitation in this environment (documented, no counterexample).
- **Mapped LEC:** **NOT_TESTED** (automated EQY/SAT-model limitations); candidate structurally disqualified.
- **Negative controls:** not completed as full automated campaign (candidate disqualified); no inconclusive result claimed as PASS.

## Mapped structural results (measured)
- 24 cells total (8 `dfxtp_1`, 4 `and2_0`, 4 `xor2_1`, 4 `xor3_1`, 4 `a21o_1`), 345.331 µm². Carry depths from qa/qb: C1=2, C2=3, C3=4, **C4/sum[4]=5** (worse than baseline 4).
- **No improvement:** deeper critical carry path and higher area than baseline.

## Known limitations
- Carry structure collapsed/worsened under ABC mapping.
- Full formal qualification (mapped EQY, complete negative-control campaign) not completed; not required for decision given structural disqualification.
- No physical implementation, STA, DRC, or LVS performed.

## Qualification decision
**NO_QUALIFIED_CANDIDATE** (fails structural/depth criterion). Not promoted. Production RTL/SDC unchanged.

*Evidence (external): `D:\audit\phase18P_technology_aware_carry\`*