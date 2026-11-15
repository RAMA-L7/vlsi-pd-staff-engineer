# ============================================================================
# Phase 14 regression - STAGE 11: PARASITIC EXTRACTION AND FINAL EXPORT
#
# Exact Phase 13 validated procedure:
#   source <platform>/setRC.tcl
#   define_process_corner -ext_model_index 0 X
#   set_extraction_rules_file <sky130hs>/rcx_patterns.rules
#       (the sky130hd copy is a 30-byte pointer that segfaults OpenROAD)
#   extract_parasitics
#   write_spef
#   write_def / write_verilog / write_sdc / write_db
#
# Outputs: 11_final.odb, 11_final.def, 11_final.v, 11_final.sdc, 11_final.spef
# ============================================================================
source [file join [file dirname [info script]] common.tcl]

puts "===== stage 11 extraction + export ====="

read_tech $::env(REG_LIB_TT)
read_db $RUN/10_route.odb
set block [ord::get_db_block]

# The SDC must be loaded in this session, otherwise write_sdc below emits an
# empty constraint file (reading an ODB does not restore the timing
# constraints).  The propagated clock flag matters too: without it OpenSTA
# uses ideal clocks and the extracted parasitics are not exercised.
read_sdc $::env(REG_SDC)
catch {set_propagated_clock [all_clocks]}

puts "--- layer RC ---"
source $PLAT/setRC.tcl

puts "--- parasitic extraction ---"
define_process_corner -ext_model_index 0 X
set_extraction_rules_file $PLATHS/rcx_patterns.rules
extract_parasitics
write_spef $RUN/11_final.spef
catch {file delete $::env(REG_DESIGN).totCap}

puts "--- export ---"
write_def  $RUN/11_final.def
write_verilog $RUN/11_final.v -remove_cells {sky130_fd_sc_hd__tapvpwrvgnd_1}
write_sdc  $RUN/11_final.sdc
write_db   $RUN/11_final.odb

puts "--- design / net statistics ---"
report_design_area
power_report "after extraction"
puts "STAGE11_DONE"
exit 0
