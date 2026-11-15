#!/usr/bin/env bash
# ============================================================================
# Phase 14 regression - STAGE 14: DRC
#
# Exact Phase 12/13 validated procedure:
#   the ORIGINAL official sky130hd.lydrc deck (unmodified) is executed by
#   KLayout's DRC engine, with $in_gds and $report_file set.
#
#   The report directory is created BEFORE the save - that is the Phase 12 fix
#   (KLayout's ReportDatabase::save fails with errno=2 when the parent
#   directory does not exist).
#
# Outputs: <run>/tests/sky130hd_drc_report.rpt
#          <run>/drc/drc.log
#          <run>/drc/drc_classification.txt
# ============================================================================
set -u
: "${REG_RUN:?}"; : "${REG_KLAYOUT:?}"; : "${REG_DRC_DECK:?}"

OUT="$REG_RUN/drc"
mkdir -p "$OUT"
RPT_DIR="$REG_RUN/tests"
mkdir -p "$RPT_DIR"
RPT="$RPT_DIR/sky130hd_drc_report.rpt"
GDS="$REG_RUN/${REG_DESIGN}_pdn.gds"
[ -f "$GDS" ] || { echo "ERROR: missing $GDS"; exit 110; }

echo "=== stage 14 DRC ==="

cat > "$OUT/drc.rb" << EOF
require 'date'
# Phase 14 regression - Sky130 DRC with the report database persisted and
# reopened.  The ORIGINAL official deck is used UNMODIFIED.
deck_path = '$REG_DRC_DECK'
in_gds    = '$GDS'
rpt_dir   = '$RPT_DIR'
rpt_file  = '$RPT'

Dir.mkdir(rpt_dir) unless Dir.exist?(rpt_dir)

deck = File.read(deck_path)
ruby = deck[/<text>(.*)<\\/text>/m, 1].to_s
ruby = ruby.gsub('&gt;', '>').gsub('&lt;', '<').gsub('&amp;', '&')
ruby = ruby.gsub('&quot;', '"').gsub('&apos;', "'")

\$in_gds = in_gds
\$report_file = rpt_file

drc = DRC::DRCEngine.new
drc._generator = in_gds
drc._start("DRC: sky130hd.lydrc")
drc.instance_eval(ruby, deck_path)
drc._finish
puts "=== DRC DECK EXECUTED ==="

if File.exist?(rpt_file)
  rd = RBA::ReportDatabase.new('reopen')
  rd.load(rpt_file)
  cats = []
  rd.each_category { |c| cats << c.name }
  items = 0
  values = 0
  cells = []
  rd.each_cell do |c|
    cells << c.name
    c.each_item do |i|
      items += 1
      i.each_value { |v| values += 1 }
    end
  end
  puts "REPORT FILE: #{rpt_file} (#{File.size(rpt_file)} bytes)"
  puts "REOPEN OK: categories=#{cats.length} cells=#{cells.inspect} items=#{items} values=#{values}"
else
  puts "REPORT FILE MISSING: #{rpt_file}"
end
puts "PHASE14_DRC_DONE"
EOF

"$REG_KLAYOUT" -b -r "$OUT/drc.rb" > "$OUT/drc.log" 2>&1
rc=$?
echo "  klayout exit=$rc"
grep -E "REPORT FILE|REOPEN OK|DRC DECK EXECUTED|PHASE14_DRC_DONE|ERROR" "$OUT/drc.log" | sed 's/^/    /'
if [ $rc -ne 0 ]; then echo "ERROR: DRC execution failed"; exit 111; fi
if ! grep -q 'PHASE14_DRC_DONE' "$OUT/drc.log"; then echo "ERROR: DRC did not complete"; exit 112; fi

# ---- classify: rule lines (.output(...)) vs helper regions -------------------
python3 - "$REG_DRC_DECK" "$OUT/drc.log" "$OUT/drc_classification.txt" << 'PYEOF'
import re, sys
deck, log, out = sys.argv[1], sys.argv[2], sys.argv[3]
d = open(deck, encoding='utf-8', errors='replace').read()
m = re.search(r'<text>(.*)</text>', d, re.S)
r = m.group(1)
for a, b in (('&gt;', '>'), ('&lt;', '<'), ('&amp;', '&'), ('&quot;', '"'), ('&apos;', "'")):
    r = r.replace(a, b)
rule = {i for i, l in enumerate(r.split('\n'), 1) if '.output(' in l}
counts = {}
cur = None
evals = 0
for raw in open(log, encoding='utf-8', errors='replace'):
    mm = re.match(r'^"[^"]*" in: sky130hd\.lydrc:(\d+)', raw)
    if mm:
        cur = int(mm.group(1)); evals += 1; continue
    mm2 = re.match(r'^\s*Edge pairs: (\d+) \(flat\)\s+(\d+) \(hierarchical\)', raw)
    if mm2 and cur is not None:
        counts[cur] = (int(mm2.group(1)), int(mm2.group(2)))
viol = [(k, counts[k]) for k in sorted(counts) if k in rule and (counts[k][0] or counts[k][1])]
helpers = [(k, counts[k]) for k in sorted(counts) if k not in rule and (counts[k][0] or counts[k][1])]
with open(out, 'w') as f:
    f.write('rule_lines_defined: %d\n' % len(rule))
    f.write('rule_lines_evaluated: %d\n' % len([k for k in counts if k in rule]))
    f.write('region_evaluations_logged: %d\n' % evals)
    f.write('edge_pairs_lines: %d\n' % len(counts))
    f.write('ACTUAL_VIOLATIONS: %d\n' % len(viol))
    for k, v in viol:
        f.write('  VIOLATION line %d flat=%d hier=%d\n' % (k, v[0], v[1]))
    f.write('HELPER_REGIONS_WITH_NONZERO_COUNTS: %d (not violations)\n' % len(helpers))
    for k, v in helpers:
        f.write('  helper line %d flat=%d hier=%d\n' % (k, v[0], v[1]))
print(open(out).read())
PYEOF

echo "STAGE14_DONE"
exit 0
