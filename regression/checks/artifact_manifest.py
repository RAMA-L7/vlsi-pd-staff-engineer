#!/usr/bin/env python3
"""
Phase 30 - Artifact manifest schema and validator.

Schema (per stage entry):
  schema_version, project_id, run_id, stage, tool_name, tool_version,
  started_utc, finished_utc, inputs[{path,sha256}], outputs[{path,sha256,required}],
  logs[{path,sha256}], exit_code, status, evidence_class
evidence_class: FRESH_EXECUTION | FRESH_ACCEPTANCE_VALIDATION | HISTORICAL_EVIDENCE | NOT_TESTED

Usage:
  artifact_manifest.py validate <run_dir> [--manifest manifest.json]
  artifact_manifest.py generate <run_dir> [--out manifest.json]
"""
import hashlib, json, os, sys, time

SCHEMA_VERSION = "30.1"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def file_info(path, required=True):
    exists = os.path.isfile(path)
    info = {"path": path, "exists": exists, "required": required}
    if exists:
        st = os.stat(path)
        info["bytes"] = st.st_size
        info["sha256"] = sha256_file(path)
        info["mtime_utc"] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(st.st_mtime))
    else:
        info["bytes"] = 0
        info["sha256"] = None
    return info


def load_json(path):
    with open(path, 'r', encoding='utf-8-sig') as f:
        return json.load(f)


def validate(run_dir, manifest_path=None):
    errors = []
    warnings = []
    if manifest_path is None:
        manifest_path = os.path.join(run_dir, 'artifact_manifest.json')
    if not os.path.isfile(manifest_path):
        return {"status": "FAIL", "errors": [f"manifest missing: {manifest_path}"], "warnings": []}
    try:
        m = load_json(manifest_path)
    except Exception as e:
        return {"status": "FAIL", "errors": [f"manifest unreadable: {e}"], "warnings": []}

    if m.get('schema_version') != SCHEMA_VERSION:
        warnings.append(f"schema_version {m.get('schema_version')} != {SCHEMA_VERSION}")
    run_id = m.get('run_id')
    if not run_id:
        errors.append("run_id missing")

    for stg in m.get('stages', []):
        sid = stg.get('stage', '?')
        if stg.get('run_id') != run_id:
            errors.append(f"{sid}: run_id mismatch")
        ec = stg.get('exit_code')
        if ec is not None and not isinstance(ec, int):
            errors.append(f"{sid}: exit_code not int")
        for section in ('outputs', 'logs', 'inputs'):
            for item in stg.get(section, []):
                p = item.get('path')
                req = item.get('required', section != 'inputs')
                if not p:
                    errors.append(f"{sid}: {section} entry missing path")
                    continue
                exists = os.path.isfile(p)
                if req and not exists:
                    errors.append(f"{sid}: required {section} missing: {p}")
                if exists and item.get('sha256'):
                    actual = sha256_file(p)
                    if actual != item['sha256']:
                        errors.append(f"{sid}: hash mismatch for {p}")
                if exists and item.get('bytes') is not None:
                    if os.path.getsize(p) != item['bytes']:
                        warnings.append(f"{sid}: size changed for {p}")
        status = stg.get('status')
        if status == 'PASS':
            # PASS requires all required outputs present and exit_code==0 or explicitly validated
            req_missing = [o['path'] for o in stg.get('outputs', []) if o.get('required', True) and not os.path.isfile(o.get('path',''))]
            if req_missing:
                errors.append(f"{sid}: PASS but required outputs missing: {req_missing}")
            if stg.get('exit_code') not in (0, None) and stg.get('evidence_class') == 'FRESH_EXECUTION':
                errors.append(f"{sid}: PASS but exit_code={stg.get('exit_code')}")

    ok = len(errors) == 0
    return {"status": "PASS" if ok else "FAIL", "errors": errors, "warnings": warnings,
            "run_id": run_id, "stages": len(m.get('stages', []))}


def generate(run_dir, out_path=None):
    # Generate a best-effort manifest from existing run artifacts (historical/fresh acceptance)
    run_id = os.path.basename(run_dir.rstrip('/\\'))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "project_id": None,
        "run_id": run_id,
        "generated_utc": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "stages": []
    }
    # Try to read stage_status.json and acceptance.json
    status_path = os.path.join(run_dir, 'stage_status.json')
    acc_path = os.path.join(run_dir, 'acceptance.json')
    status = {}
    if os.path.isfile(status_path):
        try:
            sj = load_json(status_path)
            for s in sj.get('stages', []):
                status[s.get('name')] = s
        except Exception:
            pass
    acc = {}
    if os.path.isfile(acc_path):
        try:
            aj = load_json(acc_path)
            for c in aj.get('checks', []):
                acc.setdefault(c.get('stage'), []).append(c)
        except Exception:
            pass
    # Known artifact patterns per stage (minimal)
    stage_artifacts = {
        "01_synthesis": {"outputs": ["synth/generic_soc_ctrl_synth.v","synth/soc_ctrl_synth.v","synth/cell_counts.json"], "logs": ["synth/generic_synth.log","synth/synth_mapped.log"]},
        "02_lec_generic": {"outputs": [], "logs": ["lec_generic/lec_generic.log","lec_generic/lec_generic_negative.log"]},
        "03_lec_mapped": {"outputs": [], "logs": ["eqy/eqy_positive.log","eqy/eqy_negative.log"]},
        "04_floorplan": {"outputs": ["04_floorplan.odb","04_floorplan.def"], "logs": ["logs/04_floorplan.log"]},
    }
    # Also detect generic test_counter artifacts
    for stg, spec in stage_artifacts.items():
        st = status.get(stg, {})
        # adapt for test_counter naming
        outs = []
        for rel in spec["outputs"]:
            p = os.path.join(run_dir, rel)
            if not os.path.exists(p):
                # try test_counter variant
                alt = rel.replace('soc_ctrl', 'test_counter')
                p2 = os.path.join(run_dir, alt)
                if os.path.exists(p2): p = p2
            if os.path.exists(p):
                outs.append(file_info(p, required=True))
        logs = [file_info(os.path.join(run_dir, rel), required=True) for rel in spec["logs"] if os.path.exists(os.path.join(run_dir, rel))]
        evidence_class = "FRESH_EXECUTION" if st.get('executed') else "NOT_TESTED"
        manifest["stages"].append({
            "stage": stg, "run_id": run_id,
            "tool_name": None, "tool_version": None,
            "started_utc": st.get('started'), "finished_utc": st.get('ended'),
            "inputs": [], "outputs": outs, "logs": logs,
            "exit_code": st.get('exit_code'), "status": st.get('status'),
            "evidence_class": evidence_class,
            "acceptance_checks": len(acc.get(stg, []))
        })
    if out_path is None:
        out_path = os.path.join(run_dir, 'artifact_manifest.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    return out_path, manifest


def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(2)
    cmd, run_dir = sys.argv[1], sys.argv[2]
    if cmd == 'validate':
        mp = None
        if '--manifest' in sys.argv:
            mp = sys.argv[sys.argv.index('--manifest')+1]
        res = validate(run_dir, mp)
        print(json.dumps(res, indent=2))
        sys.exit(0 if res['status'] == 'PASS' else 1)
    elif cmd == 'generate':
        op = None
        if '--out' in sys.argv:
            op = sys.argv[sys.argv.index('--out')+1]
        p, m = generate(run_dir, op)
        print(f"generated: {p} ({len(m['stages'])} stages)")
        sys.exit(0)
    else:
        print(__doc__); sys.exit(2)

if __name__ == '__main__':
    main()
