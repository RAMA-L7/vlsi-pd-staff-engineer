# ============================================================================
# Phase 14 regression - STAGE 10: DETAILED ROUTING
#
# Exact Phase 13 validated procedure.
#
# Outputs: 10_route.odb, 10_route.def, detailed_route_drc.rpt
# ============================================================================
source [file join [file dirname [info script]] common.tcl]

puts "===== stage 10 detailed route ====="

read_tech $::env(REG_LIB_TT)
read_db $RUN/09_groute.odb
set block [ord::get_db_block]

detailed_route -output_drc $RUN/detailed_route_drc.rpt -output_maze $RUN/detailed_route_maze.log -verbose 1
power_report "after detailed route"

write_db $RUN/10_route.odb
write_def $RUN/10_route.def
puts "STAGE10_DONE"
exit 0
