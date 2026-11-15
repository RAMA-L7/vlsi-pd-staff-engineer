#!/usr/bin/env bash
# ============================================================================
# Phase 14 regression - STAGE 12: MULTI-CORNER STA (TT / SS / FF)
#
# Exact Phase 13 validated procedure: OpenSTA 3.1.0 with define_corners and
# the three genuine Sky130 Liberty corners, the routed netlist, the OpenROAD
# SDC and the OpenROAD SPEF parasitics.
#
# The corner markers use the exact Phase 13 spelling so the report format is
# unchanged:
#   === TT CORNER (typical, 25C, 1.80V) ===
#
# In addition to the per-path reports, the AUTHORITATIVE WNS and TNS are
# requested from OpenSTA itself (report_worst_slack / report_tns) for every
# corner and every delay type, so the recorded numbers do not depend on the
# number of reported paths.
#
# Outputs: sta/sta_all.log, sta/sta_tt.rpt, sta/sta_ss.rpt, sta/sta_ff.rpt,
#          sta/sta_summary.txt
# ============================================================================
set -u
: "${REG_RUN:?}"; : "${REG_STA:?}"; : "${REG_LIBDIR:?}"
echo "DEBUG_REG_RUN=$REG_RUN" >&2

OUT="$REG_RUN/sta"
mkdir -p "$OUT"

echo "=== stage 12 multi-corner STA ==="

cat > "$OUT/sta.cmd" << EOF
define_corners tt ss ff
read_liberty -corner tt $REG_LIBDIR/sky130_fd_sc_hd__tt_025C_1v80.lib
read_liberty -corner ss $REG_LIBDIR/sky130_fd_sc_hd__ss_n40C_1v40.lib
read_liberty -corner ff $REG_LIBDIR/sky130_fd_sc_hd__ff_n40C_1v95.lib
read_verilog $REG_RUN/11_final.v
link_design $REG_DESIGN
read_sdc $REG_RUN/11_final.sdc
read_spef $REG_RUN/11_final.spef
puts "STA_TRACE read_liberty_corner_count 3"
puts "STA_TRACE read_sdc $REG_RUN/11_final.sdc"
puts "STA_TRACE read_spef $REG_RUN/11_final.spef"
puts "STA_TRACE linked_design $REG_DESIGN"
puts "=== TT CORNER (typical, 25C, 1.80V) ==="
report_checks -path_delay max -corner tt -group_path_count 5 -digits 4
puts "--- HOLD ---"
report_checks -path_delay min -corner tt -group_path_count 5 -digits 4
puts "--- WNS ---"
puts "setup_wns [sta::worst_slack -corner tt -max]"
puts "hold_wns [sta::worst_slack -corner tt -min]"
puts "--- TNS ---"
puts "setup_tns [sta::total_negative_slack -corner tt -max]"
puts "hold_tns [sta::total_negative_slack -corner tt -min]"
puts "=== SS CORNER (slow, -40C, 1.40V) ==="
report_checks -path_delay max -corner ss -group_path_count 5 -digits 4
puts "--- HOLD ---"
report_checks -path_delay min -corner ss -group_path_count 5 -digits 4
puts "--- WNS ---"
puts "setup_wns [sta::worst_slack -corner ss -max]"
puts "hold_wns [sta::worst_slack -corner ss -min]"
puts "--- TNS ---"
puts "setup_tns [sta::total_negative_slack -corner ss -max]"
puts "hold_tns [sta::total_negative_slack -corner ss -min]"
puts "=== FF CORNER (fast, -40C, 1.95V) ==="
report_checks -path_delay max -corner ff -group_path_count 5 -digits 4
puts "--- HOLD ---"
report_checks -path_delay min -corner ff -group_path_count 5 -digits 4
puts "--- WNS ---"
puts "setup_wns [sta::worst_slack -corner ff -max]"
puts "hold_wns [sta::worst_slack -corner ff -min]"
puts "--- TNS ---"
puts "setup_tns [sta::total_negative_slack -corner ff -max]"
puts "hold_tns [sta::total_negative_slack -corner ff -min]"
puts "PHASE14_STA_DONE"
EOF

"$REG_STA" "$OUT/sta.cmd" > "$OUT/sta_all.log" 2>&1
rc=$?
echo "  sta exit=$rc"
if [ $rc -ne 0 ]; then echo "ERROR: OpenSTA failed"; tail -20 "$OUT/sta_all.log"; exit 90; fi
if ! grep -q 'PHASE14_STA_DONE' "$OUT/sta_all.log"; then
  echo "ERROR: OpenSTA did not reach the end of the script"; tail -20 "$OUT/sta_all.log"; exit 91
fi

# ---- split the per-corner reports and compute WNS/TNS -----------------------
python3 - "$OUT/sta_all.log" "$OUT" << 'PYEOF'
import re, sys

log, out = sys.argv[1], sys.argv[2]
txt = open(log, encoding='utf-8', errors='replace').read()

secs = re.split(r'^=== (TT|SS|FF) CORNER \(.*\) ===$', txt, flags=re.M)
summary = []
for i in range(1, len(secs), 2):
    corner = secs[i]
    body = secs[i + 1]
    open('%s/sta_%s.rpt' % (out, corner.lower()), 'w').write(body)

    # the "--- WNS ---" / "--- TNS ---" markers delimit the authoritative values
    parts = re.split(r'^--- (WNS|TNS|HOLD) ---$', body, flags=re.M)
    # parts[0] = setup paths, then alternating marker/content
    setup_paths = parts[0]
    hold_body = ''
    wns_body = ''
    tns_body = ''
    for k in range(1, len(parts) - 1, 2):
        marker = parts[k]
        content = parts[k + 1]
        if marker == 'HOLD':
            hold_body = content
        elif marker == 'WNS':
            wns_body = content
        elif marker == 'TNS':
            tns_body = content

    def pathstats(block):
        slacks = re.findall(r'^\s*([0-9]+\.[0-9]+)\s+slack \((?:MET|VIOLATED)\)', block, re.M)
        viol = len(re.findall(r'slack \(VIOLATED\)', block))
        return len(slacks), viol

    ns, vs = pathstats(setup_paths)
    nh, vh = pathstats(hold_body)

    # The authoritative per-corner values come from OpenSTA itself
    # (sta::worst_slack / sta::total_negative_slack with -corner).
    def labelled(block, label):
        m = re.search(r'^\s*%s\s+(-?[0-9]+\.[0-9]+)\s*$' % label, block, re.M)
        return float(m.group(1)) if m else None

    wns_max = labelled(wns_body, 'setup_wns')
    wns_min = labelled(wns_body, 'hold_wns')
    tns_max = labelled(tns_body, 'setup_tns')
    tns_min = labelled(tns_body, 'hold_tns')

    summary.append((corner, ns, vs, wns_max, tns_max, nh, vh, wns_min, tns_min))

with open('%s/sta_summary.txt' % out, 'w') as f:
    f.write('corner,setup_paths,setup_violated,setup_wns_ns,setup_tns_ns,hold_paths,hold_violated,hold_wns_ns,hold_tns_ns\n')
    total_viol = 0
    for c, ns, vs, ws, ts, nh, vh, wh, th in summary:
        f.write('%s,%d,%d,%s,%s,%d,%d,%s,%s\n' % (
            c, ns, vs,
            '%.4f' % ws if ws is not None else 'NA',
            '%.4f' % ts if ts is not None else 'NA',
            nh, vh,
            '%.4f' % wh if wh is not None else 'NA',
            '%.4f' % th if th is not None else 'NA'))
        total_viol += vs + vh
    f.write('total_violators,%d\n' % total_viol)
print(open('%s/sta_summary.txt' % out).read())
PYEOF

echo "STAGE12_DONE"
exit 0
