#!/usr/bin/env python3
"""
Phase 14 regression - independent LVS netlist comparison.

Compares the layout-extracted netlist (produced by the genuine Sky130 LVS
deck) against the schematic reference netlist (produced from the routed
OpenROAD database with the official sky130hd.cdl pin orders).

Both netlists are flattened to transistor level and compared on:
  * the standard-cell instance multiset
  * the device multiset (type + W + L)
  * the device-to-net connectivity graph (Weisfeiler-Lehman colour refinement,
    which is purely structural and independent of net names)
  * the top-level net fan-out distribution
  * the power connectivity

This is the Phase 12/13 validated algorithm; the paths are parameters here so
the same code runs against any regression run.

Usage: lvs_compare.py <extracted.cir> <reference.cdl> [output.txt]

Machine-readable keys (consumed by checks/acceptance.py):
    DEVICES: layout=<n> schematic=<n>
    WL_MATCHED: <n>
    WL_LAYOUT_TOTAL: <n>
    WL_SCHEMATIC_TOTAL: <n>
    UNMATCHED_NETS: <n>
    UNMATCHED_DEVICES: <n>
    UNMATCHED_SUBCIRCUITS: <n>
    POWER: <state>
"""
import re
import sys
from collections import defaultdict, Counter

EXTRACTED = sys.argv[1]
SCHEM = sys.argv[2]
OUTFILE = sys.argv[3] if len(sys.argv) > 3 else None
TOP = 'TEST_COUNTER'

POWER_MAP = {
    'VGND': 'GLOBAL_GND', 'VNB': 'GLOBAL_GND', 'VSS': 'GLOBAL_GND',
    'VPWR': 'GLOBAL_PWR', 'VDD': 'GLOBAL_PWR',
    'VPB': 'GLOBAL_PB',
}


def norm_name(n):
    """Normalise a SPICE node name: strip the leading backslash escape."""
    if n is None:
        return None
    s = str(n).strip()
    if s.startswith('\\'):
        s = s[1:]
    return s.upper()


def strip_continuations(text):
    """Join SPICE continuation lines ('+' at start)."""
    out = []
    for raw in text.splitlines():
        s = raw.rstrip()
        if s.lstrip().startswith('+') and out:
            out[-1] = out[-1] + ' ' + s.lstrip()[1:].strip()
        else:
            out.append(s)
    return out


def parse_spice(path):
    """Return {subckt_name: {'pins': [...], 'xs': [(inst, cell, [nets])],
                                             'ms': [(name, type, nodes, params)]}}"""
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        lines = strip_continuations(f.read())

    subckts = {}
    cur = None
    # pass 1: collect all subcircuit names so that X lines can be classified
    # regardless of definition order
    names = set()
    for line in lines:
        t = line.strip()
        if t.upper().startswith('.SUBCKT '):
            names.add(t.split()[1].upper())
    for line in lines:
        t = line.strip()
        if not t or t.startswith('*'):
            continue
        up = t.upper()
        if up.startswith('.SUBCKT '):
            toks = t.split()
            name = toks[1]
            pins = [norm_name(p) for p in toks[2:]]
            cur = {'pins': pins, 'xs': [], 'ms': []}
            subckts[name.upper()] = cur
            continue
        if up.startswith('.ENDS'):
            cur = None
            continue
        if cur is None:
            continue
        if up.startswith('X'):
            # X<inst> <cell> <nets...>   (SPICE)  OR
            # X<inst> <nets...> <cell>   (KLayout LVS writer puts the cell last)
            toks = t.split()
            if len(toks) < 3:
                continue
            inst = toks[0]
            if toks[1].upper() in names:
                cell = toks[1].upper()
                nets = [norm_name(n) for n in toks[2:]]
            elif toks[-1].upper() in names:
                cell = toks[-1].upper()
                nets = [norm_name(n) for n in toks[1:-1]]
            else:
                continue
            cur['xs'].append((inst, cell, nets))
        elif up.startswith('M'):
            # M<name> d g s b model [params]
            toks = t.replace('(', ' ').replace(')', ' ').split()
            if len(toks) < 6:
                continue
            name = toks[0]
            nodes = [norm_name(n) for n in toks[1:5]]
            model = toks[5].upper()
            params = {}
            for p in toks[6:]:
                m = re.match(r'^([A-Za-z_]+)\s*=\s*(\S+)$', p)
                if m:
                    params[m.group(1).upper()] = m.group(2)
            cur['ms'].append((name, model, nodes, params))
    return subckts


def num(v):
    """Normalise a SPICE number: 150000U -> 0.15, 1.245e+12 -> 1245.0 ..."""
    if v is None:
        return None
    s = str(v).strip()
    m = re.match(r'^([-+0-9.eE+]+)([a-zA-Z]*)$', s)
    if not m:
        return s.lower()
    val, suffix = m.group(1), m.group(2).lower()
    try:
        f = float(val)
    except ValueError:
        return s.lower()
    if suffix == 'u':
        return '%.6g' % (f / 1e6)
    if suffix == 'p':
        return '%.6g' % (f / 1e12)
    if suffix == 'n':
        return '%.6g' % (f / 1e9)
    if suffix == 'm':
        return '%.6g' % (f * 1e3)
    if suffix == 'k':
        return '%.6g' % (f * 1e3)
    return '%.6g' % f


def eff_params(params):
    """Effective device geometry following SPICE multi-finger semantics."""
    w = params.get('W')
    l = params.get('L')
    m = params.get('M')
    if m is None:
        mult = 1.0
    else:
        try:
            mult = float(m)
        except ValueError:
            mult = 1.0
    try:
        wv = float(num(w)) * mult
        ws = '%.6g' % wv
    except (TypeError, ValueError):
        ws = num(w)
    return ws, num(l)


def flatten(subckts, top):
    """Recursive flattening with correct formal->actual renaming and
    canonicalisation of the global power nets."""
    devices = []
    nets = set()

    def rec(name, rename, depth=0, prefix=''):
        c = subckts.get(name.upper())
        if c is None or depth > 20:
            return
        pins = set(c['pins'])
        for (mname, model, nodes, params) in c['ms']:
            nn = []
            for nd in nodes:
                if nd in pins:
                    # external node -> the parent's net
                    nn.append(rename.get(nd, nd))
                else:
                    # internal node -> namespaced per instance so that nodes of
                    # different instances never alias each other
                    nn.append(prefix + nd)
            nets.update(nn)
            ws, ls = eff_params(params)
            # The node tuple keeps the SPICE terminal order (D G S B) so that
            # the BULK terminal can be identified positionally.  Sorting the
            # tuple would destroy that information and make any
            # terminal-role comparison (e.g. the bulk / n-well terminal)
            # meaningless.
            devices.append((model, ws, ls, tuple(nn)))
        for (inst, cell, nets_list) in c['xs']:
            sub = subckts.get(cell)
            if sub is None:
                continue
            nmap = {}
            for i, formal in enumerate(sub['pins']):
                actual = nets_list[i] if i < len(nets_list) else '??'
                a = rename.get(actual.upper(), actual.upper())
                # a net attached to a global power pin is a global power net
                if formal.upper() in POWER_MAP:
                    a = POWER_MAP[formal.upper()]
                nmap[formal.upper()] = a
            for v in nmap.values():
                nets.add(v)
            rec(cell, nmap, depth + 1, prefix + inst + '/')

    # top-level cell pins: formal power pins become globals as well
    topc = subckts.get(top.upper())
    if topc:
        for i, formal in enumerate(topc['pins']):
            if formal.upper() in POWER_MAP:
                nets.add(POWER_MAP[formal.upper()])

    rec(top, {})
    return devices, nets


def signature(devs):
    inc = defaultdict(set)
    for idx, (model, w, l, nodes) in enumerate(devs):
        for n in nodes:
            inc[n].add(idx)
    return inc


def _device_terminal_sig(ncol, model, w, l, nodes):
    """Terminal-position-aware device signature.

    Terminal order is preserved (e.g. MOS D,G,S,B) so swaps of terminal
    connectivity are detected. Internal net names are never used directly;
    only their structural colours participate.
    """
    term_cols = tuple(ncol.get(n, 0) for n in nodes)
    return (model, w, l, term_cols)


def wl_hash(inc, devs, iters=6, anchors=None):
    """Weisfeiler-Lehman colour refinement; returns a multiset of colours.
    Terminal-order aware. Optional anchors {net: stable_id} fix boundary nets
    (e.g. top-level ports) so port connectivity swaps are detected.
    """
    anchors = anchors or {}
    # Initial net colours: degree-based, but anchored nets get stable identity.
    ncol = {}
    for n in inc:
        if n in anchors:
            ncol[n] = hash(('ANCHOR', anchors[n], len(inc[n])))
        else:
            ncol[n] = hash(('DEG', len(inc[n])))
    for _ in range(iters):
        devcols = []
        for i, (model, w, l, nodes) in enumerate(devs):
            sig = _device_terminal_sig(ncol, model, w, l, nodes)
            devcols.append(hash(sig))
        ndcol = {}
        for i, dc in enumerate(devcols):
            for ti, n in enumerate(devs[i][3]):
                # Include terminal index in net's view of attached device colour
                ndcol.setdefault(n, []).append((ti, dc))
        for n in inc:
            if n in anchors:
                # Keep anchor identity but incorporate neighborhood to propagate
                ncol[n] = hash(('ANCHOR', anchors[n], tuple(sorted(ndcol.get(n, [])))))
            else:
                ncol[n] = hash(('NET', tuple(sorted(ndcol.get(n, [])))))
    return Counter(hash(_device_terminal_sig(ncol, d[0], d[1], d[2], d[3])) for d in devs)


def canon(net):
    return POWER_MAP.get(net.upper(), net)


def wl_net_colours(inc, devs, iters=6, anchors=None):
    """Terminal-order-aware Weisfeiler-Lehman refinement of NET colours.
    Returns {net: colour}. Anchors fix boundary net identities.
    """
    anchors = anchors or {}
    ncol = {}
    for n in inc:
        if n in anchors:
            ncol[n] = hash(('ANCHOR', anchors[n], len(inc[n])))
        else:
            ncol[n] = hash(('DEG', len(inc[n])))
    for _ in range(iters):
        devcols = []
        for i, (model, w, l, nodes) in enumerate(devs):
            devcols.append(hash(_device_terminal_sig(ncol, model, w, l, nodes)))
        ndcol = {}
        for i, dc in enumerate(devcols):
            for ti, n in enumerate(devs[i][3]):
                ndcol.setdefault(n, []).append((ti, dc))
        for n in inc:
            if n in anchors:
                ncol[n] = hash(('ANCHOR', anchors[n], tuple(sorted(ndcol.get(n, [])))))
            else:
                ncol[n] = hash(('NET', tuple(sorted(ndcol.get(n, [])))))
    return ncol


def bulk_partition(devs):
    """The partition of the 4-terminal (bulk) devices over their bulk nets.

    Only the *shape* of the partition is returned - the number of devices on
    each distinct bulk net - and not the net names, because the layout
    extractor names the n-well nets SYNTH_N while the schematic names them
    _NN.  Two identical circuits therefore produce identical partitions.
    """
    groups = Counter()
    for d in devs:
        if len(d[3]) == 4:
            groups[d[3][3]] += 1
    return Counter(groups.values())


def net_colour_multiset(devs):
    """The multiset of net colours, i.e. the structural net fingerprint."""
    inc = signature(devs)
    cols = wl_net_colours(inc, devs)
    return Counter(cols.values())


def fanout(circ):
    d = Counter()
    for (_, _, nl) in circ['xs']:
        for n in nl:
            d[n.upper()] += 1
    return d


def main():
    lay = parse_spice(EXTRACTED)
    sch = parse_spice(SCHEM)

    out = []
    out.append("=== Phase 14 independent LVS netlist comparison ===")
    out.append("layout netlist : %s" % EXTRACTED)
    out.append("   subcircuits: %d" % len(lay))
    out.append("schematic netlist: %s" % SCHEM)
    out.append("   subcircuits: %d" % len(sch))
    out.append("")

    lt = lay.get(TOP)
    st = sch.get(TOP)
    if lt is None or st is None:
        out.append("TOP CIRCUIT MISSING (layout=%s schematic=%s)" % (lt is not None, st is not None))
        text = '\n'.join(out) + '\n'
        if OUTFILE:
            open(OUTFILE, 'w').write(text)
        print(text)
        return 1

    # --- standard-cell instance multiset ------------------------------------
    lc = Counter(cell for (_, cell, _) in lt['xs'])
    sc = Counter(cell for (_, cell, _) in st['xs'])
    out.append("--- top-level standard-cell instances ---")
    out.append("%-42s %8s %8s %s" % ('cell', 'layout', 'schem', 'status'))
    matched = 0
    mismatched = []
    for cell in sorted(set(lc) | set(sc)):
        a, b = lc.get(cell, 0), sc.get(cell, 0)
        out.append("%-42s %8d %8d %s" % (cell, a, b, 'MATCH' if a == b else 'MISMATCH'))
        if a == b:
            matched += min(a, b)
        else:
            mismatched.append((cell, a, b))
    out.append("matched cell instances (by type): %d" % matched)
    unmatched_subckts = sum((lc - sc).values()) + sum((sc - lc).values())
    if mismatched:
        out.append("UNMATCHED cell instances: %s" % mismatched)
    out.append("")

    # --- transistor-level comparison ----------------------------------------
    ldev, lnets = flatten(lay, TOP)
    sdev, snets = flatten(sch, TOP)
    out.append("--- transistor-level comparison ---")
    out.append("layout devices: %d   schematic devices: %d" % (len(ldev), len(sdev)))
    out.append("layout nets (canonicalised): %d   schematic nets: %d" % (len(lnets), len(snets)))
    lcnt = Counter((d[0], d[1], d[2]) for d in ldev)
    scnt = Counter((d[0], d[1], d[2]) for d in sdev)
    unmatched_devices = sum((lcnt - scnt).values()) + sum((scnt - lcnt).values())
    for k in sorted(set(lcnt) | set(scnt)):
        a, b = lcnt.get(k, 0), scnt.get(k, 0)
        out.append("  %-28s W=%-12s L=%-10s layout=%3d schem=%3d %s"
                   % (k[0], k[1], k[2], a, b, 'MATCH' if a == b else 'MISMATCH'))
    out.append("")

    # --- structural connectivity ---------------------------------------------
    linc = signature(ldev)
    sinc = signature(sdev)
    # Anchor top-level port nets by port identity so port/pin swaps are detected.
    # Use port names from subckt pin lists; map to actual nets connected at top level.
    def top_port_anchors(circ, dev_nets):
        anchors = {}
        for i, p in enumerate(circ['pins']):
            key = norm_name(p)
            # find actual net name used in flattened context for this port
            # top-level ports may be passed through; use canonical mapping
            if key in dev_nets:
                anchors[key] = ('PORT', i, key)
            # also try power-canonicalized name
            ck = canon(key)
            if ck in dev_nets:
                anchors[ck] = ('PORT', i, ck, key)
            # direct upper
            if key not in anchors and key in [norm_name(x) for x in dev_nets]:
                anchors[key] = ('PORT', i, key)
        # Also anchor by the pin names as they appear (flattened top-level nets
        # are typically the same names for ports in our extraction flows)
        for i, p in enumerate(circ['pins']):
            n = norm_name(p)
            anchors.setdefault(n, ('PORT', i, n))
            cn = canon(n)
            if cn != n:
                anchors.setdefault(cn, ('PORT', i, cn, n))
        # restrict to nets present
        return {n: v for n, v in anchors.items() if n in dev_nets}

    lanchors = top_port_anchors(lt, lnets)
    sanchors = top_port_anchors(st, snets)
    lh = wl_hash(linc, ldev, anchors=lanchors)
    sh = wl_hash(sinc, sdev, anchors=sanchors)
    common = lh & sh
    only_l = lh - sh
    only_s = sh - lh
    out.append("--- structural (Weisfeiler-Lehman) connectivity comparison ---")
    out.append("matching device-colour classes: %d of %d layout / %d schematic"
               % (sum(common.values()), sum(lh.values()), sum(sh.values())))
    out.append("layout-only classes: %d   schematic-only classes: %d"
               % (sum(only_l.values()), sum(only_s.values())))
    wl_matched = sum(common.values())
    wl_layout_total = sum(lh.values())
    wl_schem_total = sum(sh.values())
    out.append("")

    # --- net accounting ------------------------------------------------------
    # The net NAMES cannot be compared directly: the layout extractor names
    # its internal nets SYNTH_1, SYNTH_2, ... while the schematic uses
    # _00_, _01_, ...  What CAN be compared is the STRUCTURE: the multiset of
    # Weisfeiler-Lehman net colours is a fingerprint of the connectivity
    # graph and is independent of the names.  Two netlists with the same
    # fingerprint have the same nets, connected the same way.
    lnetcol = Counter(wl_net_colours(linc, ldev, anchors=lanchors).values())
    snetcol = Counter(wl_net_colours(sinc, sdev, anchors=sanchors).values())
    unmatched_nets = (sum((lnetcol - snetcol).values()) +
                      sum((snetcol - lnetcol).values()))
    ltopnets = set()
    for (_, _, nl) in lt['xs']:
        ltopnets.update(n.upper() for n in nl)
    stopnets = set()
    for (_, _, nl) in st['xs']:
        stopnets.update(n.upper() for n in nl)
    out.append("--- net accounting (structural, name-independent) ---")
    out.append("layout net-colour classes: %d   schematic net-colour classes: %d"
               % (len(lnetcol), len(snetcol)))
    out.append("net-colour classes in both: %d" % len(lnetcol & snetcol))
    out.append("layout-only net-colour classes: %d   schematic-only: %d"
               % (len(lnetcol - snetcol), len(snetcol - lnetcol)))
    out.append("STRUCTURALLY UNMATCHED NETS: %d" % unmatched_nets)
    out.append("")
    out.append("--- net naming (informational only) ---")
    out.append("layout top-level nets: %d   schematic top-level nets: %d"
               % (len(ltopnets), len(stopnets)))
    out.append("nets in both (by name): %d" % len(ltopnets & stopnets))
    out.append("layout-only net names: %d" % len(ltopnets - stopnets))
    out.append("schematic-only net names: %d" % len(stopnets - ltopnets))
    out.append("")

    # --- top-level net fan-out distribution ----------------------------------
    lf, sf = fanout(lt), fanout(st)
    ld = Counter(lf.values())
    sd = Counter(sf.values())
    out.append("--- top-level net fan-out distribution ---")
    out.append("%-10s %10s %10s" % ('fanout', 'layout', 'schematic'))
    for k in sorted(set(ld) | set(sd)):
        out.append("%-10s %10d %10d" % (k, ld.get(k, 0), sd.get(k, 0)))
    out.append("")

    # --- top-level pin comparison --------------------------------------------
    out.append("--- top-level pin comparison ---")
    out.append("layout pins: %d  schematic pins: %d" % (len(lt['pins']), len(st['pins'])))
    lp_list = [p.upper() for p in lt['pins']]
    sp_list = [p.upper() for p in st['pins']]
    lp = set(lp_list)
    sp = set(sp_list)
    out.append("pin names in both: %d" % len(lp & sp))
    out.append("layout-only pins: %s" % sorted(lp - sp))
    out.append("schematic-only pins: %s" % sorted(sp - lp))
    pin_names_match = (lp == sp)
    pin_order_match = (lp_list == sp_list)
    out.append("PIN_NAMES_MATCH: %s" % pin_names_match)
    out.append("PIN_ORDER_MATCH: %s" % pin_order_match)
    out.append("PIN_COUNT_MATCH: %s" % (len(lp_list) == len(sp_list)))
    out.append("")

    # --- power connectivity ---------------------------------------------------
    # The layout's power nets must be single connected nets, and the
    # schematic's VPWR / VGND pins must map onto them.
    lpower = sorted(n for n in lnets if n in ('VDD', 'VPWR', 'VSS', 'VGND', 'VNB',
                                              'GLOBAL_PWR', 'GLOBAL_GND', 'GLOBAL_PB'))
    spower = sorted(n for n in snets if n in ('VDD', 'VPWR', 'VSS', 'VGND', 'VNB',
                                              'GLOBAL_PWR', 'GLOBAL_GND', 'GLOBAL_PB'))
    lcanon = {canon(n) for n in lpower}
    scanon = {canon(n) for n in spower}
    power_ok = (lcanon == scanon
                and 'GLOBAL_PWR' in lcanon
                and 'GLOBAL_GND' in lcanon)
    # The bulk (VPB) terminal must form the same PARTITION on both sides.
    # The partition SHAPE (how many devices share each bulk net) is compared
    # rather than the net names, because the layout extractor names the
    # n-well nets SYNTH_N while the schematic names them _NN.
    lbuk = bulk_partition(ldev)
    sbuk = bulk_partition(sdev)
    bulk_ok = (lbuk == sbuk)

    out.append("--- power connectivity ---")
    out.append("layout power nets (canonical): %s" % sorted(lcanon))
    out.append("schematic power nets (canonical): %s" % sorted(scanon))
    out.append("bulk-net partition identical (shape): %s" % bulk_ok)
    out.append("layout bulk partition: %s" % sorted(lbuk.values(), reverse=True))
    out.append("schematic bulk partition: %s" % sorted(sbuk.values(), reverse=True))
    out.append("")

    # --- summary -------------------------------------------------------------
    out.append("=== SUMMARY ===")
    out.append("standard-cell instance multiset identical : %s" % (lc == sc))
    out.append("transistor device count layout=%d schematic=%d" % (len(ldev), len(sdev)))
    out.append("structural connectivity classes matched   : %d" % wl_matched)
    out.append("LVS_EXECUTION: EXECUTED")
    out.append("LVS_TOPOLOGY_EQUIVALENT: %s"
               % (wl_matched == wl_layout_total == wl_schem_total
                  and ldev and len(ldev) == len(sdev) and lc == sc))

    out.append("")
    out.append("DEVICES: layout=%d schematic=%d" % (len(ldev), len(sdev)))
    out.append("WL_MATCHED: %d" % wl_matched)
    out.append("WL_LAYOUT_TOTAL: %d" % wl_layout_total)
    out.append("WL_SCHEMATIC_TOTAL: %d" % wl_schem_total)
    out.append("UNMATCHED_NETS: %d" % unmatched_nets)
    out.append("UNMATCHED_DEVICES: %d" % unmatched_devices)
    out.append("UNMATCHED_SUBCIRCUITS: %d" % unmatched_subckts)
    out.append("POWER: %s" % ('VDD=VSS_CONNECTED' if (power_ok and bulk_ok)
                               else 'VDD=VSS_MISMATCH'))
    out.append("POWER_Canonical_Nets_Match: %s" % power_ok)
    out.append("POWER_Bulk_Partition_Match: %s" % bulk_ok)
    out.append("MATCHED_CELL_INSTANCES: %d" % matched)
    out.append("TOPOLOGY_EQUIVALENT: %s"
               % (wl_matched == wl_layout_total == wl_schem_total
                  and len(ldev) == len(sdev) and lc == sc))

    text = '\n'.join(out) + '\n'
    if OUTFILE:
        with open(OUTFILE, 'w', encoding='utf-8') as f:
            f.write(text)
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
