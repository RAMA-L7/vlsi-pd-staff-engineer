# phase18p_carry_001 — Explicit Structural Kogge-Stone Prefix (Sky130 HD)

**Status:** `NO_QUALIFIED_CANDIDATE` (experimental, not promoted). No physical 04–15, no STA/DRC/LVS/signoff for this candidate.

## Purpose
Phase 18P technology-aware carry investigation: evaluate whether an explicitly structured, preserved Sky130 HD carry network produces a genuinely shallower mapped critical carry path for `soc_ctrl` than the 18L/18K baseline (4 serial `maj3_1` stages).

## Parent reference
- Functional/formal baseline: `examples/soc_ctrl/candidates/phase18k_sum_output_fix/` (RTL SHA256 `253d713b…3604`)
- Baseline physical context: Phase 18L run `regression/workspace/phase18l_soc_ctrl_physical_20261114T165018Z` (SS WNS -2.3378 ns, TNS -4.0984 ns, 3 violators; DRC CLEAN, official LVS MATCH)

## Candidate RTL
- Path: `rtl/soc_ctrl.v`
- SHA256: `6442d719d4621f8ab36ca87cb5ad3c951c2dcbe2e40f634f4879c5017a34838b`
- Ports/registers/outputs/latency preserved (8 `dfxtp_1` capture `a/b→qa/qb` on posedge clk; `sum[4:0]`, `carry_out[2:0]=C[3:1]`).

## Implementation architecture (Approach 2: explicit technology cells)
Fully structural Verilog using verified Sky130 HD cells (TT/SS/FF available): 8 `dfxtp_1`, 8 `xor2_1` (P/sums), 8 `and2_1` (G/group P), 8 `a21o_1` (group generates/carries). 4-bit Kogge-Stone-style prefix: bit P/G → L1 (G1:0,P1:0,G3:2,P3:2) → L2 (G2:0,P2:0,G3:0,P3:0) → C1..C4 = G*:0|(P*:0&C0). No behavioral carry logic; no ABC remapping.

## Synthesis / mapping method
Preservation by construction (already tech-mapped). Flow (Yosys 0.69+154, Sky130 HD TT): `read_liberty -lib TT; read_verilog; hierarchy -check; insbuf; opt_clean -purge; splitnets -ports; opt_clean -purge; stat; write_verilog` (no `abc`). Final netlist verified to retain 32 instantiated cells/topology.

## Functional verification
- Exhaustive 512/512 combinations of `{qa,qb,cin}` vs 18K and independent arithmetic reference: **PASS, 0 mismatches**. No unintended X/Z; reset/seq/latency unchanged.

## Formal verification status
- **Generic LEC vs 18K:** **PASS** (behavioral-model equivalence 16/16 proven, Yosys equiv; 0 unproven/unknown/skipped).
- **Mapped equivalence vs 18K:** supported by exhaustive 512/512 + verified preserved-structure correspondence; automated sky130-cell EQY not completed due to SAT-model limitations in Yosys flow (documented, not a counterexample).
- **Generic negative control:** **DETECTED** (carry_out[2] mutation unproven, assert failed).
- **Mapped negative control:** **DETECTED** (32/512 mismatches, carry_out[2]/sum[3] partitions affected).

## Mapped structural results (measured)
- 32 cells, 340.326 µm² (synthesis). Depths from qa/qb: C1=2, C2=3, C3=4, C4/sum[4]=4, sum[3]=5. Baseline: 20 cells, 270.259 µm², C1..C4 depths 1..4.
- **Critical carry path not shallower:** carry_out[2]/sum[4] remain 4 stages (same as baseline); sum[3] becomes 5 stages; early carries deeper; area increased.

## Known limitations
- No critical carry-depth improvement demonstrated; 4 serial carry stages retained on critical endpoints.
- Automated mapped-EQY/SAT-model tooling limitation for sky130 library cells (exhaustive equivalence used).
- No physical implementation, multi-corner STA, DRC, or LVS performed for this candidate.

## Qualification decision
**NO_QUALIFIED_CANDIDATE** (fails "genuinely shallower mapped carry path" criterion). Not promoted. Production RTL/SDC unchanged.

*Evidence (external): `D:\audit\phase18P_technology_aware_carry\`*