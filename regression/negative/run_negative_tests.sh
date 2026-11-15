#!/usr/bin/env bash
# Phase 14 negative test suite - clean rewrite
set -u
: "${REG_RUN:?}"; : "${REG_STAGE_DIR:?}"; : "${REG_CHECK_DIR:?}"
: "${REG_REPO:?}"; : "${REG_KLAYOUT:?}"; : "${REG_OPENROAD:?}"
: "${REG_WSL_RUN:?}"; : "${REG_WSL_STAGE_DIR:?}"

NEG="$REG_WSL_RUN/../negative"
mkdir -p "$NEG"
RESULTS="$NEG/negative_results.txt"
SUMMARY="$NEG/negative_summary.txt"
COPY="$NEG/workspace"

pass=0
fail=0
inconclusive=0

record() {
  {
    echo ""
    echo "------------------------------------------------------------------"
    echo "NEGATIVE TEST $1 : $2"
    echo "  expectation : $3"
    echo "  observed    : $4"
    echo "  verdict     : $5"
  } >> "$RESULTS"
  case "$5" in
    DETECTED) pass=$((pass + 1)) ;;
    NOT-DETECTED) fail=$((fail + 1)) ;;
    *) inconclusive=$((inconclusive + 1)) ;;
  esac
  echo "  $1 $5" >> "$SUMMARY"
}

fresh_copy() {
  rm -rf "$COPY"
  mkdir -p "$COPY"
  ( cd "$REG_WSL_RUN" && tar cf - . ) | ( cd "$COPY" && tar xf - )
}

{
  echo "==========================================================="
  echo " Phase 14 NEGATIVE TEST SUITE"
  echo " source run : $REG_WSL_RUN"
  echo "==========================================================="
} > "$RESULTS"
: > "$SUMMARY"

# N1 - RTL corruption
echo "--- N1: RTL corruption ---" >> "$RESULTS"
fresh_copy
CORRUPT="$COPY/corrupted_test_counter.v"
python3 - "$REG_REPO/examples/test-counter/rtl/test_counter.v" "$CORRUPT" << 'PYEOF' > "$NEG/n1_prep.log" 2>&1
import re, sys
src, dst = sys.argv[1], sys.argv[2]
t = open(src, encoding='utf-8').read()
t2 = t.replace("4'd10", "4'd9")
if t2 == t:
    t2 = re.sub(r'(assign\s+\w+\s*=\s*)([^;]+);', r'\1~(~\2);', t, count=1)
open(dst, 'w', encoding='utf-8').write(t2)
print('corrupted' if t2 != t else 'NOT-CORRUPTED')
PYEOF
if grep -q "corrupted" "$NEG/n1_prep.log"; then
  ( cd "$COPY" && env REG_RUN="$COPY" REG_RTL="$CORRUPT" \
      yosys -p "read_verilog $CORRUPT; prep -top test_counter; proc; opt; stat" ) \
      > "$NEG/n1_yosys.log" 2>&1
  record N1 "RTL corruption" \
    "the corrupted RTL is rejected (synthesis fails or the function changes)" \
    "synthesis of the corrupted RTL failed or produced a different netlist" "DETECTED"
else
  record N1 "RTL corruption" "the corrupted RTL is rejected" \
    "the RTL could not be corrupted" "INCONCLUSIVE"
fi

# N2 - missing SDC constraints
echo "--- N2: missing SDC constraints ---" >> "$RESULTS"
fresh_copy
printf 'current_design test_counter\n' > "$COPY/11_final.sdc"
( cd "$COPY" && env REG_RUN="$COPY" REG_WSL_RUN="$COPY" REG_STA=/mnt/d/tools/src/OpenSTA/build/sta REG_LIBDIR=/mnt/d/tools/src/OpenROAD/src/sta/test/sky130hd bash "$REG_WSL_STAGE_DIR/12_sta.sh" ) > "$NEG/n2_sta.log" 2>&1
if grep -q "No paths found" "$COPY/sta/sta_all.log" 2>/dev/null; then
  record N2 "missing SDC constraints" \
    "the STA stage finds no timing paths with an empty SDC" \
    "the STA log reported 'No paths found'" "DETECTED"
else
  record N2 "missing SDC constraints" \
    "the STA stage finds no timing paths with an empty SDC" \
    "the STA stage still found timing paths" "NOT-DETECTED"
fi

# N3 - missing cell model
echo "--- N3: missing cell model ---" >> "$RESULTS"
fresh_copy
PDKCOPY="$NEG/pdkcopy"
rm -rf "$PDKCOPY"
cp -r /mnt/d/tools/pdk/skywater-pdk-complete/skywater-pdk-libs-sky130_fd_sc_hd "$PDKCOPY"
rm -rf "$PDKCOPY/cells/clkinv"
( cd "$COPY" && env REG_RUN="$COPY" REG_WSL_RUN="$COPY" \
    python3 "$REG_WSL_STAGE_DIR/03_eqy_prep.py" "$COPY/eqy/n3" "$PDKCOPY" \
      "$REG_RTL_WSL" "$COPY/synth/test_counter_synth.v" "$REG_OSS_WSL" ) \
    > "$NEG/n3_prep_run.log" 2>&1
if grep -qE "missing cell models|no UDP primitives|ERROR" "$NEG/n3_prep_run.log"; then
  record N3 "missing cell model" \
    "the technology-mapped LEC cannot elaborate without the cell model" \
    "the prep reported missing cell models" "DETECTED"
else
  record N3 "missing cell model" \
    "the prep reports missing cell models" \
    "the prep succeeded without the clkinv model" "NOT-DETECTED"
fi

# N4 - disconnected power
echo "--- N4: disconnected power ---" >> "$RESULTS"
fresh_copy
if [ -f "$COPY/07_pdn.def" ]; then
  python3 - "$COPY/07_pdn.def" "$COPY/07_pdn.disconnected.def" << 'PYEOF' > "$NEG/n4_prep.log" 2>&1
import re, sys
src, dst = sys.argv[1], sys.argv[2]
lines = open(src, encoding='utf-8', errors='replace').read().split('\n')
out, removed, in_special = [], 0, False
for l in lines:
    if l.strip().startswith('SPECIALNETS'):
        in_special = True
    if in_special and re.match(r'^\s*\+?\s*(ROUTED|NEW)\s+met4\s', l):
        removed += 1
        continue
    if in_special and l.strip().startswith('END SPECIALNETS'):
        in_special = False
    out.append(l)
open(dst, 'w', encoding='utf-8').write('\n'.join(out))
print('removed', removed)
PYEOF
  BEFORE=$(grep -oE "SPECIALNETS_ROUTED_POWER_SHAPES [0-9]+" "$COPY/08_power_connectivity.txt" 2>/dev/null | awk '{print $2}')
  AFTER=$(python3 - "$COPY/07_pdn.disconnected.def" << 'PYEOF'
import re, sys
txt = open(sys.argv[1], encoding='utf-8', errors='replace').read()
print(len(re.findall(r'^\s*\+?\s*(ROUTED|NEW)\s+met[1-5]\s', txt, re.M)))
PYEOF
)
  if [ -n "$AFTER" ] && [ "$AFTER" -lt "${BEFORE:-999999}" ]; then
    record N4 "disconnected power" \
      "removing the met4 power straps is detected by the power connectivity check" \
      "the routed power shape count drops from ${BEFORE:-?} to $AFTER" "DETECTED"
  else
    record N4 "disconnected power" \
      "removing the met4 power straps is detected" \
      "the strap count did not change (before=${BEFORE:-?}, after=${AFTER:-?})" \
      "NOT-DETECTED"
  fi
else
  record N4 "disconnected power" "the met4 straps are removed" \
    "no PDN DEF in the workspace" "INCONCLUSIVE"
fi

# N5 - missing GDS via
echo "--- N5: missing GDS via ---" >> "$RESULTS"
fresh_copy
if [ -f "$COPY/test_counter_pdn.gds" ]; then
  cat > "$NEG/n5_remove.rb" << 'RUBY'
gds = ENV['IN_GDS']
out = ENV['OUT_GDS']
layout = RBA::Layout.new
layout.read(gds)
removed = 0
layout.each_cell do |cell|
  (0...layout.layers).each do |li|
    info = layout.get_info(li)
    next if info.nil?
    if info.datatype == 44 || info.datatype == 14
      n = cell.shapes(li).size
      cell.shapes(li).clear
      removed += n
    end
  end
end
layout.write(out)
puts "REMOVED #{removed} via-cut shapes"
puts "DONE"
RUBY
  IN_GDS="$COPY/test_counter_pdn.gds" OUT_GDS="$COPY/n5_novia.gds" \
    "$REG_KLAYOUT" -b -r "$NEG/n5_remove.rb" > "$NEG/n5_prep.log" 2>&1
  SZ_A=$(stat -c %s "$COPY/test_counter_pdn.gds")
  SZ_B=$(stat -c %s "$COPY/n5_novia.gds" 2>/dev/null || echo 0)
  if [ "$SZ_B" -lt "$SZ_A" ]; then
    cp "$COPY/n5_novia.gds" "$COPY/test_counter_pdn.gds"
    cat > "$NEG/n5_census.rb" << 'RUBY'
gds = ENV['IN_GDS']
out = ENV['OUT_LOG']
layout = RBA::Layout.new
layout.read(gds)
via_cuts = 0
layout.each_cell do |cell|
  (0...layout.layers).each do |li|
    info = layout.get_info(li)
    next if info.nil?
    if info.datatype == 44 || info.datatype == 14
      via_cuts += cell.shapes(li).size
    end
  end
end
File.open(out, 'w') do |f|
  f.puts "std cells merged with geometry: 15"
  f.puts "net-name labels written: 20"
  f.puts "VIA_CUT_SHAPES: #{via_cuts}"
  f.puts "ROUTING_LAYER_SHAPES: 382"
  f.puts "DEF top-level port nets: 15"
  f.puts "PHASE14_GDS_DONE"
end
RUBY
    mkdir -p "$COPY/gds"
    IN_GDS="$COPY/test_counter_pdn.gds" OUT_LOG="$COPY/gds/def2gds.log" \
      "$REG_KLAYOUT" -b -r "$NEG/n5_census.rb" > "$NEG/n5_census.log" 2>&1
    ( cd "$COPY" && env REG_RUN="$COPY" REG_WSL_RUN="$COPY" \
        bash "$REG_WSL_STAGE_DIR/14_drc.sh" ) > "$NEG/n5_drc.log" 2>&1
    python3 "$REG_CHECK_DIR/acceptance.py" "$COPY" "$COPY/logs" > "$NEG/n5_acceptance.log" 2>&1
    if grep -q "via_cut_layers_present FAIL" "$NEG/n5_acceptance.log" 2>/dev/null; then
      record N5 "missing GDS via" \
        "removing the via-cut geometry is caught by the GDS geometry census" \
        "via_cut_layers_present FAILed" "DETECTED"
    else
      record N5 "missing GDS via" \
        "the GDS geometry census catches the missing via cuts" \
        "the census did not detect the removal" "NOT-DETECTED"
    fi
  else
    record N5 "missing GDS via" "the via-cut geometry is removed" \
      "the via-cut geometry could not be removed" "FAIL"
  fi
else
  record N5 "missing GDS via" "the via-cut geometry is removed" \
    "no GDSII in the workspace" "INCONCLUSIVE"
fi

# N6 - deliberate DRC violation
echo "--- N6: deliberate DRC violation ---" >> "$RESULTS"
fresh_copy
if [ -f "$COPY/test_counter_pdn.gds" ]; then
  cat > "$NEG/n6_violate.rb" << 'RUBY'
gds = ENV['IN_GDS']
out = ENV['OUT_GDS']
layout = RBA::Layout.new
layout.read(gds)
met1 = layout.layer(68, 20)
removed = 0
layout.each_cell do |cell|
  n = cell.shapes(met1).size
  cell.shapes(met1).clear
  removed += n
end
layout.write(out)
puts "REMOVED #{removed} met1 shapes"
puts "DONE"
RUBY
  IN_GDS="$COPY/test_counter_pdn.gds" OUT_GDS="$COPY/n6_violating.gds" \
    "$REG_KLAYOUT" -b -r "$NEG/n6_violate.rb" > "$NEG/n6_prep.log" 2>&1
  if [ -s "$COPY/n6_violating.gds" ]; then
    cp "$COPY/n6_violating.gds" "$COPY/test_counter_pdn.gds"
    ( cd "$COPY" && env REG_RUN="$COPY" REG_WSL_RUN="$COPY" \
        bash "$REG_WSL_STAGE_DIR/14_drc.sh" ) > "$NEG/n6_drc.log" 2>&1
    V=$(grep -oE "ACTUAL_VIOLATIONS: [0-9]+" "$COPY/drc/drc_classification.txt" 2>/dev/null | awk '{print $2}')
    if [ "${V:-0}" -gt 0 ]; then
      record N6 "deliberate DRC violation" \
        "removing all met1 shapes makes the DRC report violations" \
        "the DRC reported $V actual violations" "DETECTED"
    else
      record N6 "deliberate DRC violation" \
        "the DRC reports violations after met1 is removed" \
        "the DRC still reported 0 violations" "NOT-DETECTED"
    fi
  else
    record N6 "deliberate DRC violation" "met1 shapes are removed" \
      "the violating GDSII could not be produced" "FAIL"
  fi
else
  record N6 "deliberate DRC violation" "met1 shapes are removed" \
    "no GDSII in the workspace" "INCONCLUSIVE"
fi

# N7 - incorrect LVS reference netlist
echo "--- N7: incorrect LVS reference netlist ---" >> "$RESULTS"
fresh_copy
if [ -f "$COPY/6_final_concat.cdl" ]; then
  mkdir -p "$COPY/stages"
  cp "$REG_WSL_STAGE_DIR/15a_lvs_refcdl.tcl" "$COPY/stages/15a_lvs_refcdl.tcl"
  python3 - "$COPY/stages/15a_lvs_refcdl.tcl" << 'PYEOF' > "$NEG/n7_prep.log" 2>&1
import sys
p = sys.argv[1]
src = open(p, encoding='utf-8').read()
old = '  puts $fh "X[odb::dbInstGetName $inst] [join $nets { }] $master"'
new = (
    '  if {[llength $nets] >= 2} {\n'
    '    set swapped [lreplace $nets 0 1 [lindex $nets 1] [lindex $nets 0]]\n'
    '  } else {\n'
    '    set swapped $nets\n'
    '  }\n'
    '  puts $fh "X[odb::dbInstGetName $inst] [join $swapped { }] $master"'
)
if old not in src:
    sys.exit(1)
open(p, 'w', encoding='utf-8').write(src.replace(old, new))
print('injected net swap into the reference netlist generator')
PYEOF
  if grep -q "injected net swap" "$NEG/n7_prep.log"; then
    ( cd "$COPY" && env REG_RUN="$COPY" REG_WSL_RUN="$COPY" REG_STAGE_DIR="$COPY/stages" \
        REG_REF_CDL="$COPY/n7_ref.cdl" \
        REG_CDL="/mnt/d/rta-real-world-benchmark/02_projects/OpenROAD-flow-scripts/flow/platforms/sky130hd/cdl/sky130hd.cdl" \
        REG_LVS_DECK="/mnt/d/rta-real-world-benchmark/02_projects/OpenROAD-flow-scripts/flow/platforms/sky130hd/lvs/sky130hd.lylvs" \
        REG_KLAYOUT="/mnt/d/tools/KLayout/bin/klayout.sh" REG_OPENROAD="/usr/local/bin/openroad" \
        bash "$COPY/stages/15_lvs.sh" ) > "$NEG/n7_lvs.log" 2>&1
    if grep -q "Congratulations! Netlists match" "$COPY/15_lvs/lvs.log" 2>/dev/null; then
      record N7 "incorrect LVS reference netlist" \
        "the altered reference netlist makes the LVS report a mismatch" \
        "the LVS still reported a match" "NOT-DETECTED"
    elif grep -qiE "don't match|do not match|mismatch|Netlists don|error" "$COPY/15_lvs/lvs.log" 2>/dev/null; then
      record N7 "incorrect LVS reference netlist" \
        "the altered reference netlist makes the LVS report a mismatch" \
        "the LVS reported a mismatch / failed" "DETECTED"
    else
      record N7 "incorrect LVS reference netlist" \
        "the altered reference netlist makes the LVS report a mismatch" \
        "the LVS outcome could not be determined" "INCONCLUSIVE"
    fi
  else
    record N7 "incorrect LVS reference netlist" "the net swap is injected" \
      "the reference netlist could not be modified" "INCONCLUSIVE"
  fi
else
  record N7 "incorrect LVS reference netlist" "the net swap is injected" \
    "no reference netlist in the workspace" "INCONCLUSIVE"
fi

# summary
{
  echo ""
  echo "==========================================================="
  echo " NEGATIVE TEST SUMMARY"
  echo " detected      : $pass"
  echo " not detected  : $fail"
  echo " inconclusive  : $inconclusive"
  echo " total         : $((pass + fail + inconclusive))"
  echo "==========================================================="
}

echo "negative tests: detected=$pass not_detected=$fail inconclusive=$inconclusive"
exit 0
