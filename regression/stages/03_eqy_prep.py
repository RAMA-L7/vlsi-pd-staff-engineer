#!/usr/bin/env python3
"""
Phase 18G regression - prepare the EQY inputs for the technology-mapped LEC.

Optimized to avoid full PDK tar-copy over 9p mounts: copies only required
UDP primitives and the specific cell models used by the mapped netlist.
"""
import io
import os
import re
import shutil
import subprocess
import sys
import time

OUT = sys.argv[1]
PDK = sys.argv[2]
# Normalize the PDK path: the 9p mount uses 'skywater-pdk-libs-' not 'sky130-pdk-libs-'
PDK = re.sub(r'sky130-pdk-libs-', 'skywater-pdk-libs-', PDK)
RTL = sys.argv[3]
NETLIST = sys.argv[4]
OSS = sys.argv[5]

os.makedirs(OUT, exist_ok=True)

def read_file(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()

def to_windows(path):
    p = path.replace('\\', '/')
    m = re.match(r'^/mnt/([a-zA-Z])/(.*)$', p)
    if m:
        return '%s:/%s' % (m.group(1).upper(), m.group(2))
    return p

def retry_op(fn, attempts=20, delay=1.0, what='operation'):
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            print('%s attempt %d failed: %s' % (what, i + 1, e))
            time.sleep(delay)
    raise RuntimeError('could not complete %s after %d attempts: %s' % (what, attempts, last))

# Stage only needed files locally to avoid 9p issues during repeated reads
STAGE = '/tmp/phase14_pdk_stage'
if os.path.isdir(STAGE):
    shutil.rmtree(STAGE, ignore_errors=True)
os.makedirs(os.path.join(STAGE, 'models'), exist_ok=True)
os.makedirs(os.path.join(STAGE, 'cells'), exist_ok=True)

PDK_MODELS = os.path.join(PDK, 'models')
PDK_CELLS = os.path.join(PDK, 'cells')

print('PDK:', PDK)
print('STAGE:', STAGE)

# Read netlist and detect top module
ntxt = read_file(NETLIST)
m_top = re.search(r'^\s*module\s+([A-Za-z_][A-Za-z0-9_$]*)\s*\(', ntxt, re.M)
if not m_top:
    sys.exit('could not detect top module in netlist: %s' % NETLIST)
TOP = m_top.group(1)
print('detected top module:', TOP)

# Derive the standard cells the design actually uses from the netlist
CELLS = sorted(set(re.findall(r'\b(sky130_fd_sc_hd__[A-Za-z0-9_]+)\b', ntxt)))
print('cells used by the design (%d): %s' % (len(CELLS), ' '.join(CELLS)))
if not CELLS:
    sys.exit('no Sky130 cells found in the netlist')

# Collect required UDP primitive files from source PDK and copy them
def collect_prims():
    if not os.path.isdir(PDK_MODELS):
        raise RuntimeError('missing PDK models dir: %s' % PDK_MODELS)
    prim_files = []
    for root, dirs, files in os.walk(PDK_MODELS):
        for fn in files:
            if not fn.startswith('sky130_fd_sc_hd__udp_') or not fn.endswith('.v'):
                continue
            if re.search(r'\.(blackbox|tb|symbol)\.v$', fn):
                continue
            if (fn.endswith('_pp_pg_n.v')
                    or fn.startswith('sky130_fd_sc_hd__udp_mux')
                    or fn.startswith('sky130_fd_sc_hd__udp_pwrgood')):
                prim_files.append(os.path.join(root, fn))
    prim_files = sorted(set(prim_files))
    if not prim_files:
        raise RuntimeError('no UDP primitives found under %s' % PDK_MODELS)
    out_models = os.path.join(STAGE, 'models')
    os.makedirs(out_models, exist_ok=True)
    copied = []
    for src in prim_files:
        dst = os.path.join(out_models, os.path.basename(src))
        shutil.copy2(src, dst)
        copied.append(dst)
    return copied

prim_files = retry_op(collect_prims, attempts=10, delay=1.0, what='collect UDP primitives')
print('UDP primitive files selected/copied: %d' % len(prim_files))

# Write the genuine-PDK primitive files as evidence
prim_evidence = os.path.join(OUT, 'primitives_pdk.v')
with open(prim_evidence, 'w', encoding='utf-8') as f:
    for pf in prim_files:
        f.write(read_file(pf))
        f.write('\n')
nprim_ev = read_file(prim_evidence).count('\nprimitive ')
print('primitives (genuine PDK evidence): %d files, %d primitive definitions' % (len(prim_files), nprim_ev))

# Use the oss-cad-suite's primitives.v as the primitives input to the preprocessor
prim = os.path.join(OSS, 'examples', 'eqy', 'spm', 'primitives.v')
nprim = read_file(prim).count('\nprimitive ')
print('primitives (preprocessor input): oss-cad-suite primitives.v (%d primitive definitions)' % nprim)

# Collect required cell model files
def collect_cells():
    if not os.path.isdir(PDK_CELLS):
        raise RuntimeError('missing PDK cells dir: %s' % PDK_CELLS)
    cell_paths = []
    missing = []
    for c in CELLS:
        base = re.sub(r'_\d+$', '', c)
        base = re.sub(r'^sky130_fd_sc_hd__', '', base)
        cell_dir = os.path.join(PDK_CELLS, base)
        expected_file = os.path.join(cell_dir, '%s.v' % c)
        if os.path.isfile(expected_file):
            cell_paths.append(expected_file)
            base_cell = re.sub(r'_\d+$', '', c)
            included = None
            for suffix in ['.functional.pp.v', '.behavioral.pp.v', '.functional.v', '.behavioral.v']:
                f = os.path.join(cell_dir, '%s%s' % (base_cell, suffix))
                if os.path.isfile(f):
                    included = f
                    break
            if included and included not in cell_paths:
                cell_paths.append(included)
        else:
            missing.append(c)
    if missing:
        raise RuntimeError('missing cell models: %s' % missing)
    staged = []
    for src in cell_paths:
        dst = os.path.join(STAGE, 'cells', os.path.basename(src))
        shutil.copy2(src, dst)
        staged.append(dst)
    return cell_paths, staged

src_cell_paths, staged_cell_paths = retry_op(collect_cells, attempts=10, delay=1.0, what='collect cell models')
print('cell models: %d files' % len(src_cell_paths))

cellf = os.path.join(OUT, 'sky130_cells_pdk.v')
with open(cellf, 'w', encoding='utf-8') as f:
    for cp in src_cell_paths:
        f.write(read_file(cp))
        f.write('\n')

# Preprocess with formal_pdk_proc.py
proc = os.path.join(OSS, 'examples', 'eqy', 'spm', 'formal_pdk_proc.py')
formal = os.path.join(OUT, 'formal_pdk.v')
cmd = [sys.executable, proc, prim, cellf, '-o', formal]
r = subprocess.run(cmd, capture_output=True, text=True)
if r.returncode != 0:
    sys.stderr.write(r.stdout + r.stderr)
    sys.exit('formal_pdk_proc.py failed')

udp_wrappers = r'''
(* noblackbox *) module sky130_fd_sc_hd__udp_dff$NSR_pp$PG$N (Q,SET,RESET,CLK_N,D,NOTIFIER,VPWR,VGND);
output Q; input SET,RESET,CLK_N,D,NOTIFIER,VPWR,VGND;
sky130_fd_sc_hd__udp_dff$NSR base (Q,SET,RESET,CLK_N,D);
endmodule
(* noblackbox *) module sky130_fd_sc_hd__udp_dff$P_pp$PG$N (Q,D,CLK,NOTIFIER,VPWR,VGND);
output Q; input D,CLK,NOTIFIER,VPWR,VGND;
sky130_fd_sc_hd__udp_dff$P base (Q,D,CLK);
endmodule
(* noblackbox *) module sky130_fd_sc_hd__udp_dff$PR_pp$PG$N (Q,D,CLK,RESET,NOTIFIER,VPWR,VGND);
output Q; input D,CLK,RESET,NOTIFIER,VPWR,VGND;
sky130_fd_sc_hd__udp_dff$PR base (Q,D,CLK,RESET);
endmodule
(* noblackbox *) module sky130_fd_sc_hd__udp_dff$PS_pp$PG$N (Q,D,CLK,SET,NOTIFIER,VPWR,VGND);
output Q; input D,CLK,SET,NOTIFIER,VPWR,VGND;
sky130_fd_sc_hd__udp_dff$PS base (Q,D,CLK,SET);
endmodule
(* noblackbox *) module sky130_fd_sc_hd__udp_dlatch$P_pp$PG$N (Q,D,GATE,NOTIFIER,VPWR,VGND);
output Q; input D,GATE,NOTIFIER,VPWR,VGND;
sky130_fd_sc_hd__udp_dlatch$P base (Q,D,GATE);
endmodule
(* noblackbox *) module sky130_fd_sc_hd__udp_dlatch$lP_pp$PG$N (Q,D,GATE,NOTIFIER,VPWR,VGND);
output Q; input D,GATE,NOTIFIER,VPWR,VGND;
sky130_fd_sc_hd__udp_dlatch$lP base (Q,D,GATE);
endmodule
(* noblackbox *) module sky130_fd_sc_hd__udp_dlatch$PR_pp$PG$N (Q,D,GATE,RESET,NOTIFIER,VPWR,VGND);
output Q; input D,GATE,RESET,NOTIFIER,VPWR,VGND;
sky130_fd_sc_hd__udp_dlatch$PR base (Q,D,GATE,RESET);
endmodule
(* noblackbox *) module sky130_fd_sc_hd__udp_pwrgood_pp$PG (UDP_OUT,UDP_IN,VPWR,VGND);
output UDP_OUT; input UDP_IN,VPWR,VGND;
assign UDP_OUT = UDP_IN;
endmodule
(* noblackbox *) module sky130_fd_sc_hd__udp_pwrgood$l_pp$PG (UDP_OUT,UDP_IN,VPWR,VGND);
output UDP_OUT; input UDP_IN,VPWR,VGND;
assign UDP_OUT = UDP_IN;
endmodule
(* noblackbox *) module sky130_fd_sc_hd__udp_pwrgood$l_pp$PG$S (UDP_OUT,UDP_IN,VPWR,VGND,SLEEP);
output UDP_OUT; input UDP_IN,VPWR,VGND,SLEEP;
assign UDP_OUT = UDP_IN;
endmodule
'''
with open(formal, 'a', encoding='utf-8') as f:
    f.write(udp_wrappers)
print('preprocessed model: %s (%d bytes)' % (formal, os.path.getsize(formal)))

# Bus-normalised gate netlist (preserve original behavior for escaped bus ports)
t = read_file(NETLIST)
groups = {}
for m in re.finditer(r'\\([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]', t):
    groups.setdefault(m.group(1), set()).add(int(m.group(2)))
groups = {k: sorted(v) for k, v in groups.items() if len(v) >= 2}
print('bus groups (all):', groups)

if groups:
    lines = t.split('\n')
    hdr = None
    for i, ln in enumerate(lines):
        if re.match(r'^\s*module\s+' + re.escape(TOP) + r'\b', ln):
            j = i
            while j < len(lines) and not lines[j].strip().endswith(');'):
                j += 1
            hdr = (i, j)
            break
    if hdr is None:
        print('warning: module header not found for bus normalization, using netlist as-is')
    else:
        i, j = hdr
        header = ' '.join(lines[i:j + 1])
        m = re.search(r'module\s+' + re.escape(TOP) + r'\s*\((.*?)\);', header, re.S)
        if m:
            ports = [p.strip() for p in m.group(1).split(',')]
            port_groups = set()
            for p in ports:
                mm = re.match(r'^\\([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]$', p)
                if mm and mm.group(1) in groups:
                    port_groups.add(mm.group(1))
            groups = {k: v for k, v in groups.items() if k in port_groups}
            print('bus groups (ports only):', groups)

            new_ports, seen = [], set()
            for p in ports:
                mm = re.match(r'^\\([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]$', p)
                if mm and mm.group(1) in groups:
                    g = mm.group(1)
                    if g in seen:
                        continue
                    seen.add(g)
                    new_ports.append(g)
                else:
                    new_ports.append(p)
            lines[i:j + 1] = ['module %s(%s);' % (TOP, ', '.join(new_ports))]

            direction = {}
            for ln in lines:
                mm = re.match(r'^\s*(input|output)\s+\\([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]\s*;\s*$', ln)
                if mm and mm.group(2) in groups:
                    direction[mm.group(2)] = mm.group(1)

            out_lines = []
            for ln in lines:
                if re.match(r'^\s*(input|output|wire)\s+\\\w+\[\d+\]\s*;\s*$', ln):
                    continue
                for g in groups:
                    for i2 in groups[g]:
                        ln = ln.replace('\\%s[%d] ' % (g, i2), '%s[%d] ' % (g, i2))
                out_lines.append(ln)
            lines = out_lines

            decls = []
            for g, idxs in groups.items():
                decls.append('  %s [%d:0] %s ;' % (direction.get(g, 'input'), max(idxs), g))
            last = 0
            for i, ln in enumerate(lines):
                if re.match(r'^\s*(input|output)\s+', ln):
                    last = i
            if decls:
                lines.insert(last + 1, '\n'.join(decls))
            t = '\n'.join(lines)

gate = os.path.join(OUT, '%s_synth_eqy.v' % TOP)
with open(gate, 'w', encoding='utf-8') as f:
    f.write(t)
print('gate netlist: %s' % gate)

# Negative control netlist: corrupt first sequential cell D pin
ff_types = ['sky130_fd_sc_hd__dfrtp_1', 'sky130_fd_sc_hd__dfxtp_1', 'sky130_fd_sc_hd__edfxtp_1']
i = -1
for fft in ff_types:
    i = t.find(fft)
    if i > 0:
        break
if i <= 0:
    sys.exit('no supported sequential cell found in netlist; cannot create negative control')
seg = t[i:i + 500]
m = re.search(r'\.D\(\s*([^)]+?)\s*\)', seg)
if not m:
    sys.exit('D pin not found in sequential cell instance: %r' % seg[:200])
cur = m.group(1).strip()
wrong = '_02_' if cur != '_02_' else '_01_'
seg2 = seg[:m.start()] + '.D(%s)' % wrong + seg[m.end():]
b = t[:i] + seg2 + t[i + 500:]
broken = os.path.join(OUT, '%s_synth_broken.v' % TOP)
with open(broken, 'w', encoding='utf-8') as f:
    f.write(b)
print('negative control: D(%s) -> D(%s)' % (cur, wrong))

# EQY configurations
POS = """[gold]
read_verilog -formal %(rtl)s

[gate]
read -sv formal_pdk.v %(gate)s

[script]
hierarchy -check -top %(top)s
prep
async2sync

[strategy sat]
use sat
depth 3

[strategy sby]
use sby
depth 3
engine smtbmc bitwuzla
""" % {'rtl': to_windows(RTL), 'gate': to_windows(gate), 'top': TOP}
NEG = POS.replace(to_windows(gate), to_windows(broken))
with open(os.path.join(OUT, '%s.eqy' % TOP), 'w') as f:
    f.write(POS)
with open(os.path.join(OUT, '%s_broken.eqy' % TOP), 'w') as f:
    f.write(NEG)
print('EQY configurations written: %s.eqy, %s_broken.eqy' % (TOP, TOP))
print('EQY_PREP_DONE')
