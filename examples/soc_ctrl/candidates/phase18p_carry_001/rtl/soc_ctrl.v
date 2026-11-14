`default_nettype none
// Phase 18P candidate 001: technology-aware parallel-prefix carry (4-bit)
// Explicit Sky130 HD cell instantiation to preserve shallow carry structure.
// Parent functional reference: phase18k_sum_output_fix (253d713b...)
// Ports/register behavior/outputs/latency preserved: qa,qb capture on posedge clk.
// Arithmetic: sum = qa + qb + cin (5-bit), carry_out[2:0]=C[3:1], sum[4]=C[4].
//
// Prefix (Kogge-Stone style, 4-bit):
//   P[i]=qa[i]^qb[i], G[i]=qa[i]&qb[i]
//   (G1:0,P1:0)=((G1|(P1&G0)), P1&P0), (G3:2,P3:2)=((G3|(P3&G2)), P3&P2)
//   (G2:0,P2:0)=((G2|(P2&G1:0)), P2&P1:0), (G3:0,P3:0)=((G3:2|(P3:2&G1:0)), P3:2&P1:0)
//   C1=G0|(P0&C0), C2=G1:0|(P1:0&C0), C3=G2:0|(P2:0&C0), C4=G3:0|(P3:0&C0)
//   sum[i]=P[i]^C[i], carry_out={C3,C2,C1}
module soc_ctrl (
    input  wire       clk,
    input  wire [3:0] a,
    input  wire [3:0] b,
    input  wire       cin,
    output wire [4:0] sum,
    output wire [2:0] carry_out
);
    wire [3:0] qa;
    wire [3:0] qb;

    // Input registers (preserve sequential behavior)
    sky130_fd_sc_hd__dfxtp_1 u_qa0 (.CLK(clk), .D(a[0]), .Q(qa[0]));
    sky130_fd_sc_hd__dfxtp_1 u_qa1 (.CLK(clk), .D(a[1]), .Q(qa[1]));
    sky130_fd_sc_hd__dfxtp_1 u_qa2 (.CLK(clk), .D(a[2]), .Q(qa[2]));
    sky130_fd_sc_hd__dfxtp_1 u_qa3 (.CLK(clk), .D(a[3]), .Q(qa[3]));
    sky130_fd_sc_hd__dfxtp_1 u_qb0 (.CLK(clk), .D(b[0]), .Q(qb[0]));
    sky130_fd_sc_hd__dfxtp_1 u_qb1 (.CLK(clk), .D(b[1]), .Q(qb[1]));
    sky130_fd_sc_hd__dfxtp_1 u_qb2 (.CLK(clk), .D(b[2]), .Q(qb[2]));
    sky130_fd_sc_hd__dfxtp_1 u_qb3 (.CLK(clk), .D(b[3]), .Q(qb[3]));

    // Bit-level propagate/generate
    wire [3:0] p;
    wire [3:0] g;
    sky130_fd_sc_hd__xor2_1 u_p0 (.A(qa[0]), .B(qb[0]), .X(p[0]));
    sky130_fd_sc_hd__xor2_1 u_p1 (.A(qa[1]), .B(qb[1]), .X(p[1]));
    sky130_fd_sc_hd__xor2_1 u_p2 (.A(qa[2]), .B(qb[2]), .X(p[2]));
    sky130_fd_sc_hd__xor2_1 u_p3 (.A(qa[3]), .B(qb[3]), .X(p[3]));
    sky130_fd_sc_hd__and2_1 u_g0 (.A(qa[0]), .B(qb[0]), .X(g[0]));
    sky130_fd_sc_hd__and2_1 u_g1 (.A(qa[1]), .B(qb[1]), .X(g[1]));
    sky130_fd_sc_hd__and2_1 u_g2 (.A(qa[2]), .B(qb[2]), .X(g[2]));
    sky130_fd_sc_hd__and2_1 u_g3 (.A(qa[3]), .B(qb[3]), .X(g[3]));

    // Prefix level 1 (span 2): (G1:0,P1:0), (G3:2,P3:2)
    wire g10, p10, g32, p32;
    // G1:0 = G1 | (P1 & G0)
    sky130_fd_sc_hd__a21o_1 u_g10 (.A1(p[1]), .A2(g[0]), .B1(g[1]), .X(g10));
    sky130_fd_sc_hd__and2_1 u_p10 (.A(p[1]), .B(p[0]), .X(p10));
    // G3:2 = G3 | (P3 & G2)
    sky130_fd_sc_hd__a21o_1 u_g32 (.A1(p[3]), .A2(g[2]), .B1(g[3]), .X(g32));
    sky130_fd_sc_hd__and2_1 u_p32 (.A(p[3]), .B(p[2]), .X(p32));

    // Prefix level 2: (G2:0,P2:0), (G3:0,P3:0)
    wire g20, p20, g30, p30;
    // G2:0 = G2 | (P2 & G1:0)
    sky130_fd_sc_hd__a21o_1 u_g20 (.A1(p[2]), .A2(g10), .B1(g[2]), .X(g20));
    sky130_fd_sc_hd__and2_1 u_p20 (.A(p[2]), .B(p10), .X(p20));
    // G3:0 = G3:2 | (P3:2 & G1:0)
    sky130_fd_sc_hd__a21o_1 u_g30 (.A1(p32), .A2(g10), .B1(g32), .X(g30));
    sky130_fd_sc_hd__and2_1 u_p30 (.A(p32), .B(p10), .X(p30));

    // Carries C1..C4
    wire c1, c2, c3, c4;
    wire c0;
    assign c0 = cin; // alias
    // C1 = G0 | (P0 & C0)
    sky130_fd_sc_hd__a21o_1 u_c1 (.A1(p[0]), .A2(c0), .B1(g[0]), .X(c1));
    // C2 = G1:0 | (P1:0 & C0)
    sky130_fd_sc_hd__a21o_1 u_c2 (.A1(p10), .A2(c0), .B1(g10), .X(c2));
    // C3 = G2:0 | (P2:0 & C0)
    sky130_fd_sc_hd__a21o_1 u_c3 (.A1(p20), .A2(c0), .B1(g20), .X(c3));
    // C4 = G3:0 | (P3:0 & C0)
    sky130_fd_sc_hd__a21o_1 u_c4 (.A1(p30), .A2(c0), .B1(g30), .X(c4));

    // Sums
    sky130_fd_sc_hd__xor2_1 u_s0 (.A(p[0]), .B(c0), .X(sum[0]));
    sky130_fd_sc_hd__xor2_1 u_s1 (.A(p[1]), .B(c1), .X(sum[1]));
    sky130_fd_sc_hd__xor2_1 u_s2 (.A(p[2]), .B(c2), .X(sum[2]));
    sky130_fd_sc_hd__xor2_1 u_s3 (.A(p[3]), .B(c3), .X(sum[3]));
    assign sum[4] = c4;

    assign carry_out[0] = c1;
    assign carry_out[1] = c2;
    assign carry_out[2] = c3;
endmodule
`default_nettype wire
