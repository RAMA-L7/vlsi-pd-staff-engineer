# ============================================================
# test_counter.sdc — Timing constraints for framework test
# Clock: 10ns period (100 MHz) — easy target for Sky130
# ============================================================

create_clock -name clk -period 10.0 [get_ports clk]

# Input/output delays (typical values)
set_input_delay  -clock clk 2.0 [all_inputs]
set_output_delay -clock clk 2.0 [all_outputs]

# Don't time the clock input itself
set_input_delay  -clock clk 0.0 [get_ports clk]

# Async reset — false path for STA
set_false_path -from [get_ports rst_n]

# Max transition and capacitance
set_max_transition 1.5 [current_design]
set_max_fanout 20 [current_design]
