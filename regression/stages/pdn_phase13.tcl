####################################
# PHASE 13 power grid configuration
#
# Derived from the official Sky130 platform file
#   flow/platforms/sky130hd/pdn.tcl
# with ONE documented addition: the via stacks of the power grid are pinned to
# the platform's own routing vias.
#
# WHY: with the unmodified platform file, OpenROAD's PDN engine invents its
# own via cells (via2_3_1600_480_1_5_320_320, via3_4_..., via4_5_...) whose
# metal enclosures do not satisfy the platform via rules
# (LAYER via2 ENCLOSURE BELOW 0.04 0.085 / ABOVE 0.065 0.065).  The result is
# a DRC violation of the "enclosure on 2 opposite edges" rules
# (li.5, m1.5, via1.5a, m2.5, via2.5, via3.5).
# The platform's own vias M1M2_PR, M2M3_PR, M3M4_PR and M4M5_PR (the ones the
# signal router uses) have the correct enclosures.
####################################
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDD$} -power
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDDPE$}
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {^VDCE$}
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {VPWR}
add_global_connection -net {VDD} -inst_pattern {.*} -pin_pattern {VPB}
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSS$} -ground
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {^VSSE$}
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {VGND}
add_global_connection -net {VSS} -inst_pattern {.*} -pin_pattern {VNB}
global_connect
####################################
# voltage domains
####################################
set_voltage_domain -name {CORE} -power {VDD} -ground {VSS}
####################################
# standard cell grid
####################################
define_pdn_grid -name {grid} -voltage_domains {CORE} -pins {met5}
add_pdn_stripe -grid {grid} -layer {met1} -width {0.48} -pitch {5.44} -offset {0} -followpins
add_pdn_stripe -grid {grid} -layer {met4} -width {1.600} -pitch {27.140} -offset {13.570}
add_pdn_stripe -grid {grid} -layer {met5} -width {1.600} -pitch {27.200} -offset {13.600}
# PHASE 13 CHANGE: pin the via stacks to the platform routing vias so that the
# enclosure rules of the Sky130 via layers are respected.
# NOTE: the via names are the tech LEF names (M1M2_PR, ...), NOT VIA_-prefixed.
# OpenROAD looks them up with dbTech::findVia, which matches the LEF name.
add_pdn_connect -grid {grid} -layers {met1 met4} -fixed_vias {M1M2_PR M2M3_PR M3M4_PR}
add_pdn_connect -grid {grid} -layers {met4 met5} -fixed_vias {M4M5_PR}
####################################
# macro grids
####################################
####################################
# grid for: CORE_macro_grid_1
####################################
define_pdn_grid -name {CORE_macro_grid_1} -voltage_domains {CORE} -macro \
  -orient {R0 R180 MX MY} -halo {2.0 2.0 2.0 2.0} -default -grid_over_boundary
add_pdn_connect -grid {CORE_macro_grid_1} -layers {met4 met5}
####################################
# grid for: CORE_macro_grid_2
####################################
define_pdn_grid -name {CORE_macro_grid_2} -voltage_domains {CORE} -macro \
  -orient {R90 R270 MXR90 MYR90} -halo {2.0 2.0 2.0 2.0} -default -grid_over_boundary
add_pdn_connect -grid {CORE_macro_grid_2} -layers {met4 met5}