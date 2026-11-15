# ============================================================================
# Phase 14 regression - STAGE 06: CLOCK TREE SYNTHESIS
#
# Exact Phase 13 validated procedure.
#
# CTS runs BEFORE the PDN.  The clock-tree buffers are inserted here, and the
# global connections plus the met1 follow-pin straps must cover them as well.
# With the PDN first (the platform order) the clock buffers' power pins were
# left on no net at all.
#
# Outputs: 06_cts.odb
# ============================================================================
source [file join [file dirname [info script]] common.tcl]

puts "===== stage 06 CTS ====="

read_tech $::env(REG_LIB_TT)
read_db $RUN/05_place.odb
set block [ord::get_db_block]

# The clock definition lives in the SDC; reading the ODB does not restore the
# timing constraints, so the SDC has to be read again before CTS can find the
# clock net.
read_sdc $::env(REG_SDC)

clock_tree_synthesis -sink_clustering_enable -repair_clock_nets
set_propagated_clock [all_clocks]
detailed_placement
check_placement -verbose

puts "--- clock report ---"
foreach c [all_clocks] {
  puts "  Clock [get_name $c]"
}
catch {report_clock_properties} m
puts "  report_clock_properties: $m"
catch {report_check_types -max_slew 0 -max_capacitance 0 -max_fanout 0 -violators} m
puts "  report_check_types: $m"

write_db $RUN/06_cts.odb
puts "STAGE06_DONE"
exit 0
