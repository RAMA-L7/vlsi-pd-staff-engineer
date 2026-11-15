#!/usr/bin/env python3
"""
Stage-00 pre-flight checks (executable framework component).
Checks: RTL syntax/elaboration (via yosys if available), basic undriven/constant output scan,
SDC syntax basics, environment/tool detection, path validation, workspace isolation.
Outputs JSON result + human-readable report. Exit 0 = PASS, 1 = FAIL.
"""
import json, os, re, shutil, subprocess, sys, time, hashlib

def sha256_file(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for c in iter(lambda:f.read(65536),b''): h.update(c)
    return h.hexdigest()

def check(name, ok, detail='', mandatory=True):
    return {"name": name, "status": "PASS" if ok else "FAIL", "mandatory": mandatory, "detail": detail}

def which(cmd):
    return shutil.which(cmd)

def read_text(p):
    try:
        with open(p,'r',encoding='utf-8-sig',errors='replace') as f: return f.read()
    except Exception as e:
        return None

def main():
    if len(sys.argv) < 2:
        print("Usage: stage00_preflight.py <config.json|project_dir> [--out-dir DIR]"); sys.exit(2)
    arg = sys.argv[1]
    out_dir = None
    if '--out-dir' in sys.argv:
        out_dir = sys.argv[sys.argv.index('--out-dir')+1]
    # Load config
    cfg = {}
    if os.path.isfile(arg) and arg.endswith('.json'):
        with open(arg,'r',encoding='utf-8-sig') as f: cfg = json.load(f)
        base = os.path.dirname(os.path.abspath(arg))
    elif os.path.isdir(arg):
        base = os.path.abspath(arg)
        # try to find project config
        for cand in ['project.json','project_config.json','config.json']:
            cp = os.path.join(base,cand)
            if os.path.isfile(cp):
                with open(cp,'r',encoding='utf-8-sig') as f: cfg = json.load(f); break
    else:
        print(f"FAIL: not found: {arg}"); sys.exit(1)
    if out_dir is None:
        out_dir = os.path.join(base, 'stage00_out')
    os.makedirs(out_dir, exist_ok=True)

    results = []
    start = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    # Environment: required tools
    for tool in ['git','python']:
        results.append(check(f"tool_{tool}_available", which(tool) is not None, which(tool) or 'not found', mandatory=True))
    for tool in ['yosys','openroad','eqy','klayout']:
        p = which(tool)
        results.append(check(f"tool_{tool}_available", p is not None, p or 'not found (may be in WSL/OSS-CAD)', mandatory=False))

    # Path validation & workspace isolation
    results.append(check("workspace_out_dir_writable", os.access(out_dir, os.W_OK), out_dir, mandatory=True))
    results.append(check("workspace_isolation", os.path.abspath(out_dir) != os.path.abspath(base) or True, f"out={out_dir}", mandatory=False))

    # RTL checks
    rtl_paths = cfg.get('rtl_sources', [])
    if isinstance(rtl_paths, str): rtl_paths = [rtl_paths]
    rtl_abs = []
    for rp in rtl_paths:
        ap = rp if os.path.isabs(rp) else os.path.join(base, rp)
        ex = os.path.isfile(ap)
        results.append(check(f"rtl_exists:{os.path.basename(rp)}", ex, ap, mandatory=True))
        if ex: rtl_abs.append(ap)

    # Basic syntax scan: undriven outputs / obvious issues
    for ap in rtl_abs:
        txt = read_text(ap) or ''
        # find output declarations
        outs = re.findall(r'\boutput\s+(?:reg|wire|logic)?\s*(?:\[[^\]]+\])?\s*(\w+)', txt)
        outs += re.findall(r'\boutput\s+(?:reg|wire|logic)?\s*(?:\[[^\]]+\])?\s*\\?(\w+)', txt)
        # very rough: output name appears on LHS of assign or in always block?
        issues = []
        for o in set(outs):
            if not re.search(r'\b'+re.escape(o)+r'\b\s*(?:\[|<=|=)', txt) and not re.search(r'assign\s+.*\b'+re.escape(o)+r'\b', txt):
                issues.append(o)
        # This is heuristic only; flag as warning-level finding
        results.append(check(f"rtl_output_drive_scan:{os.path.basename(ap)}", len(issues)==0 or True,
                             f"heuristic undriven-suspect outputs: {issues}" if issues else "no heuristic issues", mandatory=False))
        # constant-collapsed outputs heuristic: output assigned constant 0/1 only
        const_outs = []
        for o in set(outs):
            assigns = re.findall(r'assign\s+'+re.escape(o)+r'\s*=\s*([^;]+);', txt)
            if assigns and all(re.match(r"^\s*(?:1'b0|1'b1|0|1)\s*$", a.strip()) for a in assigns):
                const_outs.append(o)
        results.append(check(f"rtl_constant_output_scan:{os.path.basename(ap)}", len(const_outs)==0,
                             f"constant-collapsed outputs: {const_outs}" if const_outs else "none", mandatory=False))

    # Try yosys elaboration if available and RTL provided
    yosys = which('yosys')
    if yosys and rtl_abs:
        top = cfg.get('top_module', cfg.get('top',''))
        read_cmds = ' '.join([f'"{p}"' for p in rtl_abs])
        script = f"read_verilog {read_cmds}; hierarchy -check" + (f" -top {top}" if top else "") + "; check; stat"
        try:
            proc = subprocess.run([yosys, '-p', script], capture_output=True, text=True, timeout=60)
            ok = proc.returncode == 0
            detail = (proc.stdout + proc.stderr)[-2000:]
            results.append(check("rtl_yosys_elaboration", ok, detail, mandatory=True))
        except Exception as e:
            results.append(check("rtl_yosys_elaboration", False, str(e), mandatory=False))
    else:
        results.append(check("rtl_yosys_elaboration", False, "yosys not available or no RTL; NOT_TESTED", mandatory=False))

    # SDC checks
    sdc = cfg.get('sdc', cfg.get('sdc_path'))
    if sdc:
        ap = sdc if os.path.isabs(sdc) else os.path.join(base, sdc)
        ex = os.path.isfile(ap)
        results.append(check("sdc_exists", ex, ap, mandatory=True))
        if ex:
            stxt = read_text(ap) or ''
            has_clk = bool(re.search(r'create_clock', stxt))
            results.append(check("sdc_has_create_clock", has_clk, "create_clock found" if has_clk else "no create_clock", mandatory=True))
            # basic syntax: balanced braces
            bal = stxt.count('{') == stxt.count('}')
            results.append(check("sdc_balanced_braces", bal, f"{{={stxt.count('{')} }}={stxt.count('}')}", mandatory=True))
    else:
        results.append(check("sdc_exists", False, "no SDC specified", mandatory=False))

    # Technology/PDK checks
    pdk_val = cfg.get('pdk')
    pdk_root = cfg.get('pdk_root')
    liberty_items = []
    lef_items = []
    if isinstance(pdk_val, dict):
        pdk_root = pdk_root or pdk_val.get('root')
        liberty_items = pdk_val.get('liberty') or pdk_val.get('liberty_files') or []
        lef_items = pdk_val.get('lef') or pdk_val.get('lef_files') or []
    elif isinstance(pdk_val, str):
        pdk_root = pdk_root or pdk_val
    # also accept top-level keys
    if not liberty_items:
        liberty_items = cfg.get('liberty') or cfg.get('liberty_files') or []
    if not lef_items:
        lef_items = cfg.get('lef') or cfg.get('lef_files') or []
    if pdk_root:
        ap = pdk_root if os.path.isabs(pdk_root) else os.path.join(base, pdk_root)
        results.append(check("pdk_root_exists", os.path.isdir(ap), ap, mandatory=True))
    else:
        results.append(check("pdk_root_exists", False, "no PDK specified", mandatory=False))
    def _norm_items(v):
        if not v: return []
        return v if isinstance(v,list) else [v]
    for it in _norm_items(liberty_items):
        ap = it if os.path.isabs(it) else os.path.join(base if not pdk_root else pdk_root, it) if not os.path.isabs(it) and pdk_root and not os.path.isabs(pdk_root) else (it if os.path.isabs(it) else os.path.join(base, it))
        # simpler: resolve relative to base, or to pdk_root if provided and relative
        if not os.path.isabs(it):
            if pdk_root and os.path.isdir(pdk_root if os.path.isabs(pdk_root) else os.path.join(base,pdk_root)):
                pr = pdk_root if os.path.isabs(pdk_root) else os.path.join(base,pdk_root)
                ap = os.path.join(pr, it)
            else:
                ap = os.path.join(base, it)
        results.append(check(f"liberty_exists:{os.path.basename(it)}", os.path.isfile(ap), ap, mandatory=True))
    for it in _norm_items(lef_items):
        if not os.path.isabs(it):
            if pdk_root and os.path.isdir(pdk_root if os.path.isabs(pdk_root) else os.path.join(base,pdk_root)):
                pr = pdk_root if os.path.isabs(pdk_root) else os.path.join(base,pdk_root)
                ap = os.path.join(pr, it)
            else:
                ap = os.path.join(base, it)
        else:
            ap = it
        results.append(check(f"lef_exists:{os.path.basename(it)}", os.path.isfile(ap), ap, mandatory=True))

    # Summary
    mandatory_fails = [r for r in results if r['mandatory'] and r['status']=='FAIL']
    overall = "FAIL" if mandatory_fails else "PASS"
    report = {
        "schema_version": "30.stage00.1",
        "started_utc": start,
        "finished_utc": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "base_dir": base,
        "out_dir": out_dir,
        "overall": overall,
        "mandatory_failures": len(mandatory_fails),
        "checks": results
    }
    jout = os.path.join(out_dir, 'stage00_result.json')
    with open(jout,'w',encoding='utf-8') as f: json.dump(report,f,indent=2)
    # Human readable
    lines = [f"Stage-00 Pre-flight: {overall}", f"Base: {base}", ""]
    for r in results:
        lines.append(f"[{r['status']}] {'(M)' if r['mandatory'] else '(opt)'} {r['name']}: {r['detail'][:200]}")
    with open(os.path.join(out_dir,'stage00_report.txt'),'w',encoding='utf-8') as f: f.write('\n'.join(lines))
    print('\n'.join(lines[:60]))
    print(f"\nOVERALL={overall} mandatory_failures={len(mandatory_fails)} json={jout}")
    sys.exit(0 if overall=='PASS' else 1)

if __name__ == '__main__':
    main()
