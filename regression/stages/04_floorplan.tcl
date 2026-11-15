# ============================================================================
# Phase 14 regression - STAGE 04: FLOORPLAN
#
# Exact Phase 13 validated procedure (the flow that produced the Phase 13
# LVS/DRC PASS):
#
#   initialize_floorplan -die_area {0 0 120 60} -core_area {10 10 110 50}
#   source <platform>/make_tracks.tcl
#   set_routing_layers -signal met1-met5
#   place_pins -hor_layers met3 -ver_layers met2
#   manual well ties on a 50-site (23 um) grid
#
# WHY 120 x 60 um: with the platform order (PDN first) the Phase 8 run was
# BLOCKED by PDN-0185 (core 22.08 um < 27 um strap pitch) and the design was
# routed with NO power grid at all.  120 x 60 um is the minimum that still
# admits the official met4 (27.14 um) and met5 (27.2 um) strap pitches.
#
# WHY manual well ties: `place_tapcells` ignores existing instances in this
# OpenROAD build, and the well tie must be exactly one site (460 dbu) wide and
# site-aligned so that the n-well of the tie overlaps the n-well of the first
# functional cell (one site behind it).
#
# Outputs: 04_floorplan.odb
# ============================================================================
source [file join [file dirname [info script]] common.tcl]

puts "===== stage 04 floorplan ====="

read_tech $::env(REG_LIB_TT)
read_verilog $::env(REG_NETLIST_MAPPED)
link_design $::env(REG_DESIGN)
read_sdc $::env(REG_SDC)
set_dont_use {sky130_fd_sc_hd__probe_p_8 sky130_fd_sc_hd__probec_p_8}

initialize_floorplan -die_area {0 0 120 60} -core_area {10 10 110 50} -site unithd
source $PLAT/make_tracks.tcl
set_routing_layers -signal met1-met5
place_pins -hor_layers met3 -ver_layers met2

set block [ord::get_db_block]
set dbu [$block getDefUnits]
set rowinfo [row_info $block]
set rx [lindex [lindex $rowinfo 0] 0]
set ry [lindex [lindex $rowinfo 0] 1]
puts "  bottom row origin: $rx $ry (dbu); rows: [llength $rowinfo]; dbu=$dbu"

# ---- well ties (manual, site aligned) ---------------------------------------
# The tie is exactly one site wide (460 dbu).  Ties are placed on a 50-site
# (23 um) grid in every row; in the bottom row only the first tie is used, so
# that the functional cluster can occupy the space in between contiguously.
set tiecount 0
set rowidx 0
foreach ri $rowinfo {
  set tx [lindex $ri 0]
  set ty [lindex $ri 1]
  set k 0
  set px $tx
  while {$px < 105000} {
    if {$rowidx == 0 && $k > 0} {
      # bottom row: only the first tie here; the rest go after the cluster
      break
    }
    set nm [format "PDN_TIE_R%d_%02d" $rowidx $k]
    place_inst -name $nm -cell $TIE -location [list [expr {double($px)/$dbu}] [expr {double($ty)/$dbu}]] -orientation R0
    incr tiecount
    incr k
    incr px 23000
  }
  incr rowidx
}
puts "  placed $tiecount well ties"

# The pins must be assigned before the DB is written; place_pins has run.
set pincount [llength [odb::dbBlock_getBTerms $block]]
puts "  top-level pins assigned: $pincount"

report_design_area
write_db $RUN/04_floorplan.odb
puts "STAGE04_DONE"
exit 0
