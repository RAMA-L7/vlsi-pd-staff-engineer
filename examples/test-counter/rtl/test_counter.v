// ============================================================
// test_counter.v — Tiny test design for PD framework validation
//
// 4-bit counter with enable, async reset, and a combinational
// output. Small enough to synthesize in seconds, but has:
//   - Sequential logic (4 flip-flops)
//   - Combinational logic (adder + comparator)
//   - A real critical path (carry chain through the adder)
//   - An async reset (tests constraint handling)
//
// Use this to verify every stage of the agent framework
// before running your real design.
// ============================================================

module test_counter (
    input  wire       clk,
    input  wire       rst_n,     // async active-low reset
    input  wire       enable,
    input  wire [3:0] threshold,
    output reg  [3:0] count,
    output wire       at_max,
    output wire       over_threshold
);

    // Counter logic
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            count <= 4'b0;
        else if (enable)
            count <= count + 4'b1;
    end

    // Combinational outputs
    assign at_max = (count == 4'hF);
    assign over_threshold = (count > threshold);

endmodule
