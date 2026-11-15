# ============================================================================
# Phase 14 regression - STAGE 09: GLOBAL ROUTING
#
# Exact Phase 13 validated procedure.
#
# Outputs: 09_groute.odb, global_route.guide, congestion.rpt
# ============================================================================
source [file join [file dirname [info script]] common.tcl]

puts "===== stage 09 global route ====="

read_tech $::env(REG_LIB_TT)
read_db $RUN/07_pdn.odb
set block [ord::get_db_block]

global_route -guide_file $RUN/global_route.guide -congestion_report_file $RUN/congestion.rpt
power_report "after global route"

write_db $RUN/09_groute.odb
puts "STAGE09_DONE"
exit 0
