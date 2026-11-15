#!/usr/bin/env bash
# ============================================================================
# Phase 14 regression - STAGE 15: LVS
#
#   15a  generate the reference netlist from the routed ODB (OpenROAD)
#   15b  concatenate it with the OFFICIAL sky130hd.cdl
#   15c  run the ORIGINAL, UNMODIFIED official sky130hd.lylvs deck in KLayout
#   15d  independent transistor-level comparison of the two netlists
#
# Outputs: 15_lvs/15_lvs.lvsdb, 15_lvs/15_lvs_extracted.cir,
#          ${REG_DESIGN}_design.cdl, 6_final_concat.cdl, lvs_compare.txt
# ============================================================================
set -u
: "${REG_RUN:?}"; : "${REG_KLAYOUT:?}"; : "${REG_OPENROAD:?}"
: "${REG_PLATFORM:?}"; : "${REG_CDL:?}"; : "${REG_LVS_DECK:?}"

OUT="$REG_RUN/15_lvs"
mkdir -p "$OUT"
REF="$REG_RUN/${REG_DESIGN}_design.cdl"
CONCAT="$REG_RUN/6_final_concat.cdl"

echo "=== stage 15 LVS ==="

# ---- 15a reference netlist from the physical database ----------------------
echo "--- 15a reference netlist from the ODB ---"
REG_REF_CDL="$REF" REG_CDL="$REG_CDL" "$REG_OPENROAD" -no_init -exit \
  "$REG_STAGE_DIR/15a_lvs_refcdl.tcl" > "$OUT/refcdl.log" 2>&1
rc=$?
echo "  openroad exit=$rc"
grep -E "CDL masters|Block terminals|Instances written|PHASE14_REFCDL_DONE" "$OUT/refcdl.log" | sed 's/^/    /'
if [ $rc -ne 0 ]; then echo "ERROR: reference netlist generation failed"; tail -20 "$OUT/refcdl.log"; exit 120; fi
if [ ! -s "$REF" ]; then echo "ERROR: reference netlist not produced"; exit 124; fi

# ---- 15b concatenate with the official CDL ---------------------------------
cat "$REF" "$REG_CDL" > "$CONCAT"
echo "--- 15b concatenated CDL: $(grep -c '^.SUBCKT' "$CONCAT") subcircuits ---"

# ---- 15c the official deck -------------------------------------------------
# The official deck writes the extracted netlist next to the CWD as
# <top_cell>_extracted.cir, so the deck is executed inside the stage output
# directory.
echo "--- 15c official sky130hd.lylvs ---"
rm -f "$OUT/15_lvs.lvsdb" "$OUT/15_lvs_extracted.cir" "$OUT/${REG_DESIGN}_extracted.cir"
( cd "$OUT" && "$REG_KLAYOUT" -b \
  -rd in_gds="$REG_RUN/${REG_DESIGN}_pdn.gds" \
  -rd cdl_file="$CONCAT" \
  -rd top_cell=$REG_DESIGN \
  -rd report_file="$OUT/15_lvs.lvsdb" \
  -r "$REG_LVS_DECK" ) > "$OUT/lvs.log" 2>&1
rc=$?
echo "  klayout exit=$rc"
grep -E "Netlists|Congratul" "$OUT/lvs.log" | tail -3 | sed 's/^/    /'
if [ $rc -ne 0 ]; then echo "ERROR: LVS execution failed"; tail -30 "$OUT/lvs.log"; exit 121; fi
if ! grep -q "Congratulations! Netlists match" "$OUT/lvs.log"; then
  echo "ERROR: LVS did not report a match"
  grep -E "Mismatch|not match|failed|Error" "$OUT/lvs.log" | tail -20 | sed 's/^/    /'
  exit 122
fi

# The official deck writes the extracted netlist next to the ACTIVE CELLVIEW,
# i.e. next to the input GDS: <run>/<top_cell>_extracted.cir
if [ -s "$REG_RUN/${REG_DESIGN}_extracted.cir" ]; then
  cp "$REG_RUN/${REG_DESIGN}_extracted.cir" "$OUT/15_lvs_extracted.cir"
fi

# ---- 15d independent transistor-level comparison ---------------------------
echo "--- 15d independent netlist comparison ---"
python3 "$REG_CHECK_DIR/lvs_compare.py" \
  "$OUT/15_lvs_extracted.cir" "$CONCAT" "$REG_RUN/lvs_compare.txt" 2>&1 | sed 's/^/    /'
rc=$?
if [ $rc -ne 0 ]; then echo "ERROR: the independent LVS comparison failed"; exit 123; fi

echo "  LVS database: $(ls -la "$OUT/15_lvs.lvsdb" 2>/dev/null | awk '{print $5}') bytes"
echo "  extracted netlist: $(ls -la "$OUT/15_lvs_extracted.cir" 2>/dev/null | awk '{print $5}') bytes"
echo "STAGE15_DONE"
exit 0
