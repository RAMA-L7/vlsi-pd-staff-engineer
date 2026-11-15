# ============================================================================
# Phase 14 regression - common OpenROAD configuration.
#
# Sourced by every physical stage.  All paths come from the environment, which
# the orchestrator exports, so the regression never depends on files from an
# earlier run.
# ============================================================================

set PLAT   $::env(REG_PLATFORM)
set PLATHS $::env(REG_PLATFORM_HS)
set RUN    $::env(REG_RUN)
set TIE    sky130_fd_sc_hd__tapvpwrvgnd_1

proc read_tech {lib} {
  global PLAT
  read_lef $PLAT/lef/sky130_fd_sc_hd.tlef
  read_lef $PLAT/lef/sky130_fd_sc_hd_merged.lef
  read_liberty $lib
}

# Bottom-row origin and the sorted row list, used by the floorplan stage.
proc row_info {block} {
  set rows [odb::dbBlock_getRows $block]
  set info {}
  foreach r $rows {
    set o [odb::dbRow_getOrigin $r]
    lappend info [list [lindex $o 0] [lindex $o 1]]
  }
  set info [lsort -integer -index 1 $info]
  return $info
}

# Census of the routing shapes on the design, used to prove that the power
# grid actually produced geometry.
proc power_report {tag} {
  set block [ord::get_db_block]
  array set lc {}
  set total 0
  foreach net [$block getNets] {
    foreach sw [$net getSWires] {
      foreach w [$sw getWires] {
        set tl [$w getTechLayer]
        if {$tl eq "NULL"} { incr lc(VIA) ; incr total ; continue }
        incr lc([$tl getName]) ; incr total
      }
    }
  }
  set out {}
  foreach k [lsort [array names lc]] { lappend out "$k=$lc($k)" }
  puts "--- routing shape census ($tag): $out  total=$total"
  foreach net [$block getNets] {
    set name [$net getName]
    if {$name ne "VDD" && $name ne "VSS"} { continue }
    puts "    NET $name iterms=[$net getITermCount] swires=[llength [$net getSWires]]"
  }
}

# The number of routed power shapes present in a DEF SPECIALNETS section.
# The DEF writes them as e.g. "      + ROUTED met5 1600 + SHAPE STRIPE ...".
proc count_specialnet_shapes {defpath} {
  set fh [open $defpath r]
  set txt [read $fh]
  close $fh
  set n 0
  foreach line [split $txt "\n"] {
    if {[regexp {^\s*\+?\s*(ROUTED|NEW)\s+met[1-5]\s} $line]} { incr n }
  }
  return $n
}
