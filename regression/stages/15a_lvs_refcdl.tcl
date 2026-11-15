# ============================================================================
# Phase 14 regression - LVS reference netlist generator
#
# Exact Phase 13 validated procedure.  `write_cdl -masters` is NOT used: in
# this OpenROAD build it emits every cell OUTPUT pin as `_unconnected_N` (the
# LEF pin order puts the output 6th, the CDL masters file puts it last), so the
# generated reference would be structurally wrong.  The reference is therefore
# generated from the routed ODB's ITerm -> net mapping.
#
# Two further rules from Phase 13:
#   * the subcircuit name goes LAST on an X line
#     (X<inst> <node1> ... <nodeN> <cell>) - KLayout's SPICE reader treats the
#     last token as the cell name
#   * physical-only cells (well ties) are excluded: the LVS deck extracts TAP
#     regions into SUB/NWELL rather than into a tap subcircuit
#
# Output: <REG_REF_CDL>
# ============================================================================
read_db $::env(REG_RUN)/11_final.odb
set OUT $::env(REG_REF_CDL)
set CDLP $::env(REG_CDL)

array set pinorder {}
set fh [open $CDLP r]
while {[gets $fh line] >= 0} {
  if {[string match ".SUBCKT *" $line]} {
    set toks [split $line]
    set cur [lindex $toks 1]
    set pinorder($cur) [lrange $toks 2 end]
  }
}
close $fh
puts "CDL masters parsed: [array size pinorder]"

set block [ord::get_db_block]

# --- top-level ports (ODB BTerms) ---
set ports {}
foreach bt [odb::dbBlock_getBTerms $block] {
  lappend ports [odb::dbBTerm_getName $bt]
}
puts "Block terminals: [llength $ports] -> $ports"

set fh [open $OUT w]
puts $fh "* Phase 14 LVS reference netlist for $::env(REG_DESIGN)"
puts $fh "* Generated from the routed ODB: run/11_final.odb"
puts $fh "* Cell pin order taken from the official sky130hd.cdl"
puts $fh "* Power pins use the physical global power nets (VDD / VSS) as in the layout"
puts $fh "*"
puts $fh ".SUBCKT $::env(REG_DESIGN) [join $ports { }]"

# Physical-only cells (well/substrate taps).  The Sky130 LVS deck extracts TAP
# regions into SUB/NWELL instead of a tap subcircuit, so they must not appear
# in the reference netlist.
array set phys {sky130_fd_sc_hd__tapvpwrvgnd_1 1}
set ninst 0
set nphys 0
set nskip 0
foreach inst [odb::dbBlock_getInsts $block] {
  incr ninst
  set master [odb::dbMaster_getName [odb::dbInst_getMaster $inst]]
  if {[info exists phys($master)]} {
    incr nphys
    continue
  }
  if {![info exists pinorder($master)]} {
    incr nskip
    continue
  }
  array set pnet {}
  foreach it [odb::dbInst_getITerms $inst] {
    set pin [odb::dbMTerm_getName [odb::dbITerm_getMTerm $it]]
    set net [odb::dbITerm_getNet $it]
    if {$net eq "NULL"} {
      set pnet($pin) "__UNCONNECTED__"
    } else {
      set pnet($pin) [odb::dbNet_getName $net]
    }
  }
  set nets {}
  foreach p $pinorder($master) {
    if {[info exists pnet($p)]} {
      lappend nets $pnet($p)
    } else {
      lappend nets "__UNCONNECTED__"
    }
  }
  # KLayout's SPICE/CDL reader expects the subcircuit name LAST on an X line:
  #   X<inst> <node1> ... <nodeN> <subckt>
  # Writing the cell name second makes the reader treat the nets as circuit
  # names, which produces a broken reference netlist.
  puts $fh "X[odb::dbInst_getName $inst] [join $nets { }] $master"
}
puts $fh ".ENDS $::env(REG_DESIGN)"
close $fh
puts "Instances written: $ninst  (physical-only excluded: $nphys, no CDL master: $nskip)"
puts "PHASE14_REFCDL_DONE"
exit 0
