# ============================================================
# soc_ctrl.sdc — Timing constraints for soc_ctrl design
# 4-bit ripple-carry adder/controller with input register bank
# ============================================================

create_clock -name clk -period 10.0 [get_ports clk]

# Input delays (typical values for Sky130 @ 100MHz)
set_input_delay -clock clk 2.0 [all_inputs]

# Output delays (typical values for Sky130 @ 100MHz)
set_output_delay -clock clk 2.0 [all_outputs]

# Don't time the clock input itself
set_input_delay -clock clk 0.0 [get_ports clk]

# Max transition and capacitance
set_max_transition 1.5 [current_design]
set_max_fanout 30 [current_design]