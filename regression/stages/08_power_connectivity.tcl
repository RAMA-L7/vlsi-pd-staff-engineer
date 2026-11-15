# ============================================================================
# Phase 14 regression - STAGE 08: POWER CONNECTIVITY
#
# An INDEPENDENT check that the power grid actually connects:
#   * check_power_grid -net VDD / -net VSS on the routed-power database
#   * every standard-cell power pin (VPWR/VPB/VGND/VNB) is on the right net
#   * the well ties are on the VSS net and the n-well is tied to VDD
#   * SPECIALNETS contains routed power shapes
#
# This stage is deliberately separate from the PDN stage so that the power
# connectivity is verified on its own artifacts rather than trusted from the
# generation step.
#
# Outputs: 08_power_connectivity.txt
# ============================================================================
source [file join [file dirname [info script]] common.tcl]

puts "===== stage 08 power connectivity ====="

read_tech $::env(REG_LIB_TT)
read_db $RUN/07_pdn.odb
set block [ord::get_db_block]

set out [open $RUN/08_power_connectivity.txt w]
proc emit {msg} { global out; puts $out $msg; puts $msg }

set allok 1

# ---- 1. check_power_grid on VDD and VSS -------------------------------------
foreach n {VDD VSS} {
  set ef "$RUN/check_power_grid_${n}.err"
  catch {file delete $ef}
  catch {check_power_grid -net $n -error_file $ef} m
  emit "CHECK_POWER_GRID $n $m"
  # check_power_grid returns 1 and prints "All shapes on net $n are connected"
  # when the grid is fully connected; a non-empty error file means otherwise.
  if {$m != 1} { set allok 0 ; emit "POWER_GRID_STATUS $n not-connected (status $m)" }
  if {[file exists $ef] && [file size $ef] > 0} {
    set fh [open $ef r]
    emit "POWER_GRID_ERROR_FILE $n:"
    emit [read $fh]
    close $fh
    set allok 0
  } else {
    emit "POWER_GRID_ERROR_FILE $n: (empty)"
  }
}

# ---- 2. standard-cell power pins are on the right nets ---------------------
set vddpins 0
set vsspins 0
set badpins 0
foreach inst [odb::dbBlock_getInsts $block] {
  set master [odb::dbMaster_getName [odb::dbInst_getMaster $inst]]
  foreach it [odb::dbInst_getITerms $inst] {
    set mterm [odb::dbITerm_getMTerm $it]
    set pin [odb::dbMTerm_getName $mterm]
    set net [odb::dbITerm_getNet $it]
    set netname ""
    if {$net ne "NULL"} { set netname [odb::dbNet_getName $net] }
    switch -- $pin {
      VPWR - VPB {
        incr vddpins
        if {$netname ne "VDD"} { incr badpins ; emit "BAD_POWER_PIN [odb::dbInst_getName $inst] $pin -> '$netname' (expected VDD)" }
      }
      VGND - VNB {
        incr vsspins
        if {$netname ne "VSS"} { incr badpins ; emit "BAD_POWER_PIN [odb::dbInst_getName $inst] $pin -> '$netname' (expected VSS)" }
      }
      default {}
    }
  }
}
emit "POWER_PINS VDD=$vddpins VSS=$vsspins BAD=$badpins"
if {$badpins > 0} { set allok 0 }

# ---- 3. the well ties are present and on the grid ---------------------------
set ties 0
set tiesonnet 0
foreach inst [odb::dbBlock_getInsts $block] {
  set master [odb::dbMaster_getName [odb::dbInst_getMaster $inst]]
  if {$master ne $TIE} { continue }
  incr ties
  set ok 0
  foreach it [odb::dbInst_getITerms $inst] {
    set pin [odb::dbMTerm_getName [odb::dbITerm_getMTerm $it]]
    if {$pin eq "VGND" || $pin eq "VNB"} {
      set net [odb::dbITerm_getNet $it]
      if {$net ne "NULL" && [odb::dbNet_getName $net] eq "VSS"} { set ok 1 }
    }
  }
  if {$ok} { incr tiesonnet }
}
emit "WELL_TIES total=$ties connected_to_VSS=$tiesonnet"
# NOTE: this Tcl build does not provide the word comparison operators
# (-eq / -ne / -gt / -lt / -ge / -le), so the symbolic operators are used.
if {$ties == 0} { set allok 0 }
if {$tiesonnet != $ties} { set allok 0 }

# ---- 4. the VDD/VSS nets carry geometry -------------------------------------
foreach n {VDD VSS} {
  set net [odb::dbBlock_findNet $block $n]
  if {$net eq "NULL"} { emit "POWER_NET $n MISSING" ; set allok 0 ; continue }
  set sw [llength [$net getSWires]]
  emit "POWER_NET $n swires=$sw"
  if {$sw == 0} { set allok 0 }
}

# ---- 5. SPECIALNETS carries routed power shapes -----------------------------
set nspecial [count_specialnet_shapes $RUN/07_pdn.def]
emit "SPECIALNETS_ROUTED_POWER_SHAPES $nspecial"
if {$nspecial == 0} { set allok 0 }

if {$allok} {
  emit "POWER_CONNECTIVITY_RESULT PASS"
} else {
  emit "POWER_CONNECTIVITY_RESULT FAIL"
}
close $out

if {$allok} {
  puts "  power connectivity: PASS"
} else {
  puts "  power connectivity: FAIL - see $RUN/08_power_connectivity.txt"
}
puts "STAGE08_DONE"
exit 0
