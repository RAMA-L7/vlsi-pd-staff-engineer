# ============================================================
# soc_ctrl.sdc — Timing constraints for soc_ctrl design
# 4-bit ripple-carry adder/controller with input register bank
# ============================================================

create_clock -name clk -period 10.0 [get_ports clk]

# Input delays (typical values for Sky130 @ 100MHz)
# Exclude clk itself: constraining a port to a clock defined on that same port
# is illegal (OpenSTA "Warning 441: set_input_delay relative to a clock defined
# on the same port/pin not allowed") and the old 0 ns clk "override" did not
# remove the illegal constraint.  Data I/O delay stays at 2.0 ns.
# OpenSTA 3.1.0 has no remove_from_collection; the -filter form selects all
# input ports except clk without hard-coding design port names.
set_input_delay -clock clk 2.0 [get_ports -filter {direction == input && name != clk}]

# Output delays (typical values for Sky130 @ 100MHz)
set_output_delay -clock clk 2.0 [all_outputs]

# Max transition and capacitance
set_max_transition 1.5 [current_design]
set_max_fanout 30 [current_design]