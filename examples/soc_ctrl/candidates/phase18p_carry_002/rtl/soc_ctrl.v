`default_nettype none
// Phase 18P candidate 002: hierarchical boolean parallel carry (Approach 1)
// Separate modules for P/G and prefix carry to encourage structure preservation.
// Functional reference: phase18k_sum_output_fix.
module pg_bit(input wire a, input wire b, output wire p, output wire g);
    assign p = a ^ b;
    assign g = a & b;
endmodule

module prefix4_carry(
    input  wire [3:0] p,
    input  wire [3:0] g,
    input  wire       cin,
    output wire [4:1] c
);
    wire g10, p10, g32, p32, g20, p20, g30, p30;
    // level 1
    assign g10 = g[1] | (p[1] & g[0]);
    assign p10 = p[1] & p[0];
    assign g32 = g[3] | (p[3] & g[2]);
    assign p32 = p[3] & p[2];
    // level 2
    assign g20 = g[2] | (p[2] & g10);
    assign p20 = p[2] & p10;
    assign g30 = g32 | (p32 & g10);
    assign p30 = p32 & p10;
    // carries
    assign c[1] = g[0] | (p[0] & cin);
    assign c[2] = g10  | (p10  & cin);
    assign c[3] = g20  | (p20  & cin);
    assign c[4] = g30  | (p30  & cin);
endmodule

(* keep_hierarchy = "yes" *)
module soc_ctrl (
    input  wire       clk,
    input  wire [3:0] a,
    input  wire [3:0] b,
    input  wire       cin,
    output wire [4:0] sum,
    output wire [2:0] carry_out
);
    reg [3:0] qa, qb;
    wire [3:0] p, g;
    wire [4:1] c;

    always @(posedge clk) begin
        qa <= a;
        qb <= b;
    end

    pg_bit pg0(.a(qa[0]), .b(qb[0]), .p(p[0]), .g(g[0]));
    pg_bit pg1(.a(qa[1]), .b(qb[1]), .p(p[1]), .g(g[1]));
    pg_bit pg2(.a(qa[2]), .b(qb[2]), .p(p[2]), .g(g[2]));
    pg_bit pg3(.a(qa[3]), .b(qb[3]), .p(p[3]), .g(g[3]));

    (* keep_hierarchy = "yes" *)
    prefix4_carry u_carry(.p(p), .g(g), .cin(cin), .c(c));

    assign sum[0] = p[0] ^ cin;
    assign sum[1] = p[1] ^ c[1];
    assign sum[2] = p[2] ^ c[2];
    assign sum[3] = p[3] ^ c[3];
    assign sum[4] = c[4];
    assign carry_out = c[3:1];
endmodule
`default_nettype wire
