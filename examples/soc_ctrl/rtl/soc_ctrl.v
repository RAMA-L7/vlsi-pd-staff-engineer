`default_nettype none
// ============================================================================
// soc_ctrl RTL source
// Original gate-level netlist: D:\tools\eda-work\soc_ctrl_stub.v
// Part of the OpenROAD EDA test suite, verified against Nangate Open Cell Library.
// This RTL representation enables the full RTL-to-GDSII regression flow.
// ============================================================================

// *************************************************************************
// Module: soc_ctrl
// Purpose: 4-bit ripple-carry adder/controller with input register bank
// *************************************************************************

module soc_ctrl (
    input wire       clk,    // Clock signal
    input wire [3:0] a,      // 4-bit operand A
    input wire [3:0] b,      // 4-bit operand B
    input wire       cin,    // Carry-in input
    output wire [4:0] sum, // 5-bit sum output
    output wire [2:0] carry_out // 3-bit carry-out output
);

// *************************************************************************
// Internal signals
// *************************************************************************

    // Register bank: pipelined inputs for setup/hold analyzability
    reg [3:0] qa, qb;

    // Ripple-carry chain
    wire [4:0] carry; // carry[0] = cin internally

    // Full-adder stage signals (internal nodes)
    // Stage 0
    wire fa0_nA, fa0_nB, fa0_sum, fa0_carry_out;

    // Full-adder stage 1
    wire fa1_nA, fa1_nB, fa1_sum, fa1_carry_out;

    // Full-adder stage 2
    wire fa2_nA, fa2_nB, fa2_sum, fa2_carry_out;

    // Full-adder stage 3
    wire fa3_nA, fa3_nB, fa3_sum, fa3_carry_out;

// *************************************************************************
// Register bank: DFFs on rising clock edge
// *************************************************************************

    // Input register bank: gives the design real launch flops so setup and hold
    // are both analysable against the same clock.
    always @(posedge clk) begin
        qa <= a;
        qb <= b;
    end

// *************************************************************************
// Full-adder stages - each stage is a ripple-carry cell
// *************************************************************************

    // -----------------------
    // Stage 0: least significant bit
    // -----------------------
    // Sum = qa[0] XOR qb[0] XOR carry[0]
    // Carry = (qa[0] AND qb[0]) OR ((qa[0] XOR qb[0]) AND carry[0])

    // carry[0] is initialized to cin
    assign carry[0] = cin;

    // Stage 0 sum computation (XOR chain via library cells)
    // Using structural instantiation matching the gate-level netlist
    // The synthesized netlist will map to Sky130 equivalent cells

    // Instantiate full adder stage 0 using structural gates
    // (Synthesis will optimize/flatten as appropriate)
    wire [2:0] fa0_internal;
    // Simplified: the actual gate-level netlist uses 9 cells per FA;
    // at RTL we describe the function, and Yosys/Mapping handles the rest.
    // For the regression flow, we need the design to be synthesizable
    // to the target technology.

    // Using behavioral description for the full adder:
    // The actual gate-level netlist uses NAND2_X1 and INV_X1 cells;
    // at RTL we can use the ^ (XOR) operator, which synthesizes to
    // the same function using the target library cells.
    assign fa0_sum = qa[0] ^ qb[0] ^ carry[0];
    // Carry computation
    wire qa0_qb0_and = qa[0] & qb[0];
    wire xor_qa_qb = qa[0] ^ qb[0];
    assign carry[1] = qa0_qb0_and | (xor_qa_qb & carry[0]);

    // -----------------------
    // Stage 1
    // -----------------------
    assign fa1_sum = qa[1] ^ qb[1] ^ carry[1];
    wire qa1_qb1_and = qa[1] & qb[1];
    wire xor_qa1_qb1 = qa[1] ^ qb[1];
    assign carry[2] = qa1_qb1_and | (xor_qa1_qb1 & carry[1]);

    // -----------------------
    // Stage 2
    // -----------------------
    assign fa2_sum = qa[2] ^ qb[2] ^ carry[2];
    wire qa2_qb2_and = qa[2] & qb[2];
    wire xor_qa2_qb2 = qa[2] ^ qb[2];
    assign carry[3] = qa2_qb2_and | (xor_qa2_qb2 & carry[2]);

    // -----------------------
    // Stage 3 (most significant bit)
    // -----------------------
    assign fa3_sum = qa[3] ^ qb[3] ^ carry[3];
    wire qa3_qb3_and = qa[3] & qb[3];
    wire xor_qa3_qb3 = qa[3] ^ qb[3];
    assign carry[4] = qa3_qb3_and | (xor_qa3_qb3 & carry[3]);

// *************************************************************************
// Output assignments
// *************************************************************************

    // MSB of sum is the final carry
    assign sum[4] = carry[4];
    // Lower sum bits from full-adder stage outputs (PROPOSED fix)
    assign sum[3] = fa3_sum;
    assign sum[2] = fa2_sum;
    assign sum[1] = fa1_sum;
    assign sum[0] = fa0_sum;

    // carry_out provides the top 3 carry bits (carry[4] is sum[4])
    assign carry_out = carry[3:1];

// *************************************************************************
// End of module
// *************************************************************************

endmodule
`default_nettype wire