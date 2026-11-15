# ============================================================================
# Phase 14 regression - DEF to GDSII conversion.
#
# This is the VALIDATED Phase 12/13 converter (Sky130), carried over
# unmodified except that the three input/output paths come from the
# environment instead of being hard-coded.  All four Phase 12/13 fixes are
# preserved:
#
#   1. Reader options come from the technology file (sky130hd.lyt) via
#      Technology#load_layout_options, so the LEF is read together with the
#      DEF and the named vias (L1M1_PR_MR, M1M2_PR) get their cut geometry.
#   2. Genuine sky130_fd_sc_hd.gds standard-cell geometry is merged into the
#      layout cells.
#   3. The DEF net names are written as text labels on the routing layers,
#      which is what OpenROAD's write_gds does and what the Sky130 LVS deck
#      expects (connect(LI, LITXT), connect(MET1, MET1TXT), ...).  Without
#      these the extracted netlist has auto-generated names (synth_NN) and the
#      top-level circuits cannot be correlated.
#   4. The DEF pin labels are re-anchored onto the metal they name: in the
#      DEF the pin label sits at the die edge while the route stops ~0.1 um
#      short of it, so the text was attached to no net at all.
#
# Only REAL top-level ports may carry a text label in the top cell: a label on
# an internal net makes the LVS extractor promote that net to a top-level pin,
# which then has no counterpart in the schematic and breaks the comparison.
#
# Environment:
#   REG_GDS_TECH      technology file (sky130hd.lyt)
#   REG_GDS_DEF       input DEF
#   REG_GDS_CELLS     standard-cell GDS (sky130_fd_sc_hd.gds)
#   REG_GDS_OUT       output GDSII
#   REG_GDS_LEF1      technology LEF
#   REG_GDS_LEF2      standard-cell LEF
# ============================================================================

tech_path = ENV['REG_GDS_TECH']
def_path  = ENV['REG_GDS_DEF']
cell_gds  = ENV['REG_GDS_CELLS']
out_path  = ENV['REG_GDS_OUT']
lef1      = ENV['REG_GDS_LEF1']
lef2      = ENV['REG_GDS_LEF2']

%w[REG_GDS_TECH REG_GDS_DEF REG_GDS_CELLS REG_GDS_OUT REG_GDS_LEF1 REG_GDS_LEF2].each do |k|
  raise "environment variable #{k} is not set" if ENV[k].nil? || ENV[k].empty?
end

# routing layer -> label layer
LABEL_FOR = {
  [67, 20] => [67, 5],    # li1   -> li1.label
  [68, 20] => [68, 5],    # met1  -> met1.label
  [69, 20] => [69, 5],    # met2  -> met2.label
  [70, 20] => [70, 5],    # met3  -> met3.label
  [71, 20] => [71, 5],    # met4  -> met4.label
  [72, 20] => [72, 5],    # met5  -> met5.label
}

tech = RBA::Technology.new
tech.load(tech_path)
puts "TECH: #{tech.name} dbu=#{tech.dbu}"

opts = tech.load_layout_options
cfg = opts.lefdef_config
cfg.lef_files = [lef1, lef2]

layout = RBA::Layout.new
layout.dbu = tech.dbu
layout.read(def_path, opts)
puts "DEF read. cells=#{layout.cells} top=#{layout.top_cell.name}"

# --- read genuine std cell GDS geometry ---
lib = RBA::Layout.new
lib.read(cell_gds)
merged = 0
lib.each_cell do |c|
  tgt = layout.cell(c.name)
  next if tgt.nil?
  (0...lib.layers).each do |li|
    info = lib.get_info(li)
    tgt_li = layout.layer(info)
    next if tgt_li.nil?
    c.shapes(li).each { |s| tgt.shapes(tgt_li).insert(s.dup) }
  end
  merged += 1
end
puts "std cells merged with geometry: #{merged}"

tc = layout.top_cell

# --- read the top-level port nets from the DEF PINS section ----------------
port_nets = {}
if File.exist?(def_path)
  in_pins = false
  File.readlines(def_path).each do |l|
    if l =~ /^PINS\s+\d+\s*;/
      in_pins = true
      next
    end
    if in_pins && l =~ /^END PINS/
      break
    end
    next unless in_pins
    if l =~ /^\s*-\s+(\S+).*?\+?\s*NET\s+(\S+)/
      port_nets[$2] = $1
    end
  end
end
puts "DEF top-level port nets: #{port_nets.size} -> #{port_nets.keys.inspect}"

# --- step 1: re-anchor the DEF pin labels onto the metal they name ---
routing_layers = LABEL_FOR.keys.map { |(l, d)| layout.layer(RBA::LayerInfo.new(l, d)) }.compact
n_pin = 0
label_layers = LABEL_FOR.values.map { |(l, d)| layout.layer(RBA::LayerInfo.new(l, d)) }.compact
label_layers.each do |lli|
  tc.shapes(lli).each do |s|
    next unless s.is_text?
    nm = s.text.string
    pt = s.text.trans.disp
    target = nil
    routing_layers.each do |ri|
      next if target
      tc.shapes(ri).each do |d|
        next if target
        bb = d.bbox
        next unless bb.left <= pt.x && bb.right >= pt.x && bb.bottom <= pt.y && bb.top >= pt.y
        target = d
      end
    end
    next if target.nil?
    # move the label onto the shape itself (its own path point if possible)
    cpt = nil
    if target.is_path?
      cpt = target.path.each_point.first
    else
      cpt = target.bbox.center
    end
    s.text = RBA::Text.new(nm, RBA::Trans.new(cpt))
    n_pin += 1
  end
end
puts "pin labels re-anchored onto metal: #{n_pin}"

# --- step 2: emit DEF net names as labels on the routing layers ---
n_labels = 0
seen = {}
LABEL_FOR.each do |(lnum, dt), (llnum, ldt)|
  li = layout.layer(RBA::LayerInfo.new(lnum, dt))
  lli = layout.layer(RBA::LayerInfo.new(llnum, ldt))
  next if li.nil? || lli.nil?
  tc.shapes(li).each do |s|
    nm = s.property(1)
    next if nm.nil? || nm.to_s.empty?
    # only label real top-level ports (see above)
    next unless port_nets.key?(nm.to_s)
    key = "#{lnum}/#{dt}:#{nm}"
    next if seen.key?(key)
    pt = nil
    if s.is_path?
      pt = s.path.each_point.first
    elsif s.is_box?
      pt = s.box.center
    elsif s.is_polygon?
      pt = s.polygon.bbox.center
    end
    next if pt.nil?
    tc.shapes(lli).insert(RBA::Text.new(nm, RBA::Trans.new(pt)))
    seen[key] = true
    n_labels += 1
  end
end
puts "net-name labels written: #{n_labels}"

# --- geometry census for the acceptance checks ---
# The via cut geometry lives in the standard-cell instances (mcon, via1..via4),
# so the census covers the whole layout, not only the top cell.
via_cuts = 0
power_shapes = 0
layout.each_cell do |cell|
  (0...layout.layers).each do |li|
    info = layout.get_info(li)
    next if info.nil?
    cell.shapes(li).each do |s|
      via_cuts += 1 if info.datatype == 14 || info.datatype == 44
      if info.datatype == 20 && [68, 69, 70, 71, 72].include?(info.layer)
        power_shapes += 1
      end
    end
  end
end
top_shapes = 0
(0...layout.layers).each do |li|
  info = layout.get_info(li)
  next if info.nil?
  top_shapes += tc.shapes(li).size
end
puts "TOP_CELL_SHAPES: #{top_shapes}"
puts "VIA_CUT_SHAPES: #{via_cuts}"
puts "ROUTING_LAYER_SHAPES: #{power_shapes}"

layout.write(out_path)
puts "GDSII_WRITE_OK: #{out_path}"
puts "PHASE14_GDS_DONE"
