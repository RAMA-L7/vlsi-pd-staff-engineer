#!/usr/bin/env bash
# ============================================================================
# Phase 14 regression - STAGE 13: GDSII GENERATION
#
# Uses the validated Phase 12/13 DEF->GDSII converter
# (stages/def2gds_phase13.rb), which carries all four fixes:
#   1. reader options from the technology file, so the LEF is read with the DEF
#      and the named vias get their cut geometry
#   2. the genuine standard-cell geometry from sky130_fd_sc_hd.gds is merged in
#   3. the DEF net names are written as text labels on the routing layers
#   4. only REAL top-level ports may carry a label (a label on an internal net
#      makes the LVS extractor promote it to a top-level pin)
#
# Outputs: <run>/${REG_DESIGN}_pdn.gds
# ============================================================================
set -u
: "${REG_RUN:?}"; : "${REG_KLAYOUT:?}"; : "${REG_PLATFORM:?}"; : "${REG_CELLS_GDS:?}"

OUT="$REG_RUN/gds"
mkdir -p "$OUT"
GDS="$REG_RUN/${REG_DESIGN}_pdn.gds"
RB="$REG_STAGE_DIR/def2gds_phase13.rb"

echo "=== stage 13 GDSII generation ==="

export REG_GDS_TECH="$REG_PLATFORM/sky130hd.lyt"
export REG_GDS_DEF="$REG_RUN/11_final.def"
export REG_GDS_CELLS="$REG_CELLS_GDS"
export REG_GDS_OUT="$GDS"
export REG_GDS_LEF1="$REG_PLATFORM/lef/sky130_fd_sc_hd.tlef"
export REG_GDS_LEF2="$REG_PLATFORM/lef/sky130_fd_sc_hd_merged.lef"

"$REG_KLAYOUT" -b -r "$RB" > "$OUT/def2gds.log" 2>&1
rc=$?
echo "  klayout exit=$rc"
grep -vE "Warning: Invalid via" "$OUT/def2gds.log" | tail -16 | sed 's/^/    /'
if [ $rc -ne 0 ]; then echo "ERROR: GDSII generation failed"; exit 100; fi
if [ ! -s "$GDS" ]; then echo "ERROR: GDSII not produced"; exit 101; fi
if ! grep -q 'PHASE14_GDS_DONE' "$OUT/def2gds.log"; then
  echo "ERROR: the converter did not reach the end"; exit 102
fi
echo "  GDSII: $(ls -la "$GDS" | awk '{print $5}') bytes"
echo "STAGE13_DONE"
exit 0
