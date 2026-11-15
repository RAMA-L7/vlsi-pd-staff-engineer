# ============================================================================
# Phase 14 regression - STAGE 07: PDN GENERATION
#
# Exact Phase 13 validated procedure:
#   source pdn_phase13.tcl   (official sky130hd/pdn.tcl with the platform vias
#                             pinned with -fixed_vias, so the Sky130 via
#                             enclosure rules are respected)
#   global_connect
#   pdngen
#
# Outputs: 07_pdn.odb, 07_pdn.def
# ============================================================================
source [file join [file dirname [info script]] common.tcl]

puts "===== stage 07 PDN ====="

read_tech $::env(REG_LIB_TT)
read_db $RUN/06_cts.odb
set block [ord::get_db_block]

source $::env(REG_PDN_TCL)
global_connect

puts "--- pdngen ---"
pdngen
power_report "after pdngen"

write_def $RUN/07_pdn.def
write_db $RUN/07_pdn.odb
puts "STAGE07_DONE"
exit 0
