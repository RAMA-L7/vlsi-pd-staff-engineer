#!/usr/bin/env python3
"""
Phase 30 - Project initialization CLI (executable).
Workflow: detect OS/tools, validate versions, accept project dir, register RTL, identify top, configure SDC,
accept user-supplied PDK path, validate Liberty/LEF, create isolated run dirs, generate project config,
run Stage-00, generate evidence manifest. Non-destructive by default.
"""
import argparse, json, os, platform, shutil, subprocess, sys, time

def which(cmd): return shutil.which(cmd)

def detect_tools():
    tools = {}
    for t in ['git','python','yosys','openroad','eqy','klayout','iverilog']:
        p = which(t)
        tools[t] = {"available": bool(p), "path": p}
    # Try version probes
    ver_cmds = {'git':['git','--version'],'python':[sys.executable,'--version'],'yosys':['yosys','--version']}
    for t,cmd in ver_cmds.items():
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            tools[t]['version'] = (r.stdout or r.stderr).strip().splitlines()[0][:120] if (r.stdout or r.stderr) else None
        except Exception:
            tools[t]['version'] = None
    return tools

def main():
    ap = argparse.ArgumentParser(description='Initialize a VLSI regression project (Phase 30)')
    ap.add_argument('--project-dir', required=True)
    ap.add_argument('--project-name', default=None)
    ap.add_argument('--top', default=None)
    ap.add_argument('--rtl', action='append', default=[])
    ap.add_argument('--sdc', default=None)
    ap.add_argument('--pdk-root', default=os.environ.get('REG_PDK_ROOT',''))
    ap.add_argument('--liberty', action='append', default=[])
    ap.add_argument('--lef', action='append', default=[])
    ap.add_argument('--force', action='store_true', help='Allow overwrite of generated config if exists (never deletes user files)')
    ap.add_argument('--run-stage00', action='store_true')
    ap.add_argument('--out-config', default='project_config.json')
    args = ap.parse_args()

    proj = os.path.abspath(args.project_dir)
    os.makedirs(proj, exist_ok=True)
    cfg_path = os.path.join(proj, args.out_config)
    if os.path.exists(cfg_path) and not args.force:
        print(f"ERROR: config exists: {cfg_path} (use --force to overwrite generated config)")
        return 2

    report = {"os": platform.platform(), "python": sys.version.split()[0], "tools": detect_tools(), "steps": []}
    report['steps'].append({"step":"detect_os","status":"PASS","detail":report['os']})

    # Register RTL
    rtl = args.rtl or []
    # Try to auto-detect in rtl/ if none provided
    if not rtl:
        rdir = os.path.join(proj,'rtl')
        if os.path.isdir(rdir):
            for fn in os.listdir(rdir):
                if fn.endswith('.v') or fn.endswith('.sv'):
                    rtl.append(os.path.join('rtl',fn))
    report['steps'].append({"step":"register_rtl","status":"PASS" if rtl else "WARN","detail":rtl})

    # Top module heuristic
    top = args.top
    if not top and rtl:
        # parse first module name
        try:
            p = os.path.join(proj, rtl[0]) if not os.path.isabs(rtl[0]) else rtl[0]
            with open(p,'r',encoding='utf-8-sig',errors='replace') as f: txt=f.read()
            import re; m=re.search(r'^\s*module\s+(\w+)', txt, re.M)
            if m: top=m.group(1)
        except Exception: pass
    report['steps'].append({"step":"identify_top","status":"PASS" if top else "WARN","detail":top})

    # Validate PDK/tech
    pdk_root = args.pdk_root
    if pdk_root and not os.path.isabs(pdk_root):
        pdk_root_abs = os.path.abspath(os.path.join(proj,pdk_root))
    else:
        pdk_root_abs = pdk_root
    pdk_ok = (not pdk_root_abs) or os.path.isdir(pdk_root_abs)
    report['steps'].append({"step":"validate_pdk_root","status":"PASS" if pdk_ok else "FAIL","detail":pdk_root_abs})

    libs = args.liberty or []
    lefs = args.lef or []
    tech_issues = []
    for x in libs:
        ap = x if os.path.isabs(x) else os.path.join(pdk_root_abs or proj, x)
        if not os.path.isfile(ap): tech_issues.append(f"liberty missing: {ap}")
    for x in lefs:
        ap = x if os.path.isabs(x) else os.path.join(pdk_root_abs or proj, x)
        if not os.path.isfile(ap): tech_issues.append(f"lef missing: {ap}")
    report['steps'].append({"step":"validate_liberty_lef","status":"PASS" if not tech_issues else "FAIL","detail":tech_issues})

    # Create isolated run dirs
    runs_dir = os.path.join(proj,'runs')
    os.makedirs(runs_dir, exist_ok=True)
    report['steps'].append({"step":"create_run_dirs","status":"PASS","detail":runs_dir})

    cfg = {
        "schema_version":"30.1",
        "project_name": args.project_name or os.path.basename(proj),
        "top_module": top,
        "rtl_sources": rtl,
        "sdc": args.sdc,
        "clock_definitions": [],
        "pdk": {"root": pdk_root_abs, "liberty": libs, "lef": lefs, "rc":"", "drc_deck":"", "lvs_deck":""},
        "floorplan": {},
        "regression_targets": [],
        "created_utc": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    }
    with open(cfg_path,'w',encoding='utf-8') as f: json.dump(cfg,f,indent=2)
    report['steps'].append({"step":"generate_config","status":"PASS","detail":cfg_path})

    # Stage-00
    stage00 = {"status":"NOT_RUN"}
    if args.run_stage00:
        here = os.path.dirname(__file__)
        candidates = [
            os.path.join(here,'stage00_preflight.py'),
            os.path.join(here,'checks','stage00_preflight.py'),
            os.path.join(here,'regression','checks','stage00_preflight.py'),
        ]
        s00 = next((p for p in candidates if os.path.isfile(p)), None)
        if s00:
            try:
                r = subprocess.run([sys.executable, s00, cfg_path, '--out-dir', os.path.join(proj,'runs','stage00')], capture_output=True, text=True, timeout=120)
                stage00 = {"status":"PASS" if r.returncode==0 else "FAIL","returncode":r.returncode,"stdout_tail":(r.stdout or '')[-1000:],"stderr_tail":(r.stderr or '')[-1000:]}
            except Exception as e:
                stage00 = {"status":"ERROR","detail":str(e)}
        else:
            stage00 = {"status":"NOT_TESTED","detail":"stage00_preflight.py not found"}
    report['stage00'] = stage00
    report['steps'].append({"step":"run_stage00","status":stage00.get('status','NOT_RUN')})

    # Evidence manifest
    em = {"schema_version":"30.1","project":cfg.get('project_name'),"config":cfg_path,"generated_utc":time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),"report":report}
    em_path = os.path.join(proj,'runs','init_evidence_manifest.json')
    with open(em_path,'w',encoding='utf-8') as f: json.dump(em,f,indent=2)
    report['steps'].append({"step":"evidence_manifest","status":"PASS","detail":em_path})

    out_rep = os.path.join(proj,'runs','init_report.json')
    with open(out_rep,'w',encoding='utf-8') as f: json.dump(report,f,indent=2)
    overall = "PASS" if all(s['status'] in ('PASS','WARN','NOT_RUN','NOT_TESTED') for s in report['steps']) else "FAIL"
    print(json.dumps({"overall":overall,"config":cfg_path,"report":out_rep,"evidence_manifest":em_path,"steps":report['steps']}, indent=2))
    return 0 if overall=="PASS" else 1

if __name__ == '__main__':
    sys.exit(main())
