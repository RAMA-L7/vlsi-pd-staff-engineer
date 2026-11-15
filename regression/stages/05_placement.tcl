# ============================================================================
# Phase 14 regression - STAGE 05: PLACEMENT
#
# Exact Phase 13 validated procedure:
#
#   The functional cells are placed CONTIGUOUSLY, one site behind the first
#   well tie of the bottom row.  The well-tie n-well (origin-190 .. origin+650)
#   then overlaps the first functional cell's n-well (origin-190 .. origin+1570)
#   -> a single continuous n-well, tied to VPWR through that tie.
#
#   WHY: the Sky130 LVS deck reaches VPWR from the n-well only through a
#   well tie (NWELL -> NTAP -> LICON -> LI -> MCON -> MET1).  Scattered cells
#   gave each n-well its own island, so the PFET bulk terminal could not be
#   correlated with the schematic.
#
#   The cell order is the lexicographic order of the instance names, which is
#   the order the validated Phase 13 run produced.
#
# Outputs: 05_place.odb
# ============================================================================
source [file join [file dirname [info script]] common.tcl]

puts "===== stage 05 placement ====="

read_tech $::env(REG_LIB_TT)
read_db $RUN/04_floorplan.odb
set block [ord::get_db_block]
set dbu [$block getDbUnitsPerMicron]

set rowinfo [row_info $block]
set rx [lindex [lindex $rowinfo 0] 0]
set ry [lindex [lindex $rowinfo 0] 1]
puts "  bottom row origin: $rx $ry (dbu)"

# ---- contiguous placement of the functional cells ----------------------------
set x [expr {$rx + 460}]
set names {}
foreach inst [odb::dbBlock_getInsts $block] {
  set mn [odb::dbMaster_getName [odb::dbInst_getMaster $inst]]
  if {$mn eq $TIE} { continue }
  lappend names [odb::dbInst_getName $inst]
}
set names [lsort -dictionary $names]
foreach nm $names {
  set inst [odb::dbBlock_findInst $block $nm]
  set w [odb::dbMaster_getWidth [odb::dbInst_getMaster $inst]]
  place_inst -name $nm -location [list [expr {double($x)/$dbu}] [expr {double($ry)/$dbu}]] -orientation R0
  incr x $w
}
set xend $x
puts "  placed [llength $names] functional cells contiguously: [expr {$rx+460}] .. $xend (dbu)"

# one more well tie immediately after the cluster so its n-well also overlaps
place_inst -name PDN_TIE_R0_END -cell $TIE -location [list [expr {double($xend)/$dbu}] [expr {double($ry)/$dbu}]] -orientation R0

detailed_placement
check_placement -verbose

puts "--- placement report ---"
report_design_area
write_db $RUN/05_place.odb
puts "STAGE05_DONE"
exit 0
