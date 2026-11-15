#!/usr/bin/env python3
"""
Phase 33 - artifact manifest CLI wrapper.
Delegates validation/lifecycle to manifest_manager.py while preserving basic interface.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import manifest_manager as mm
except Exception:
    mm = None


def legacy_generate(run_dir, out_path=None):
    # minimal legacy manifest for compatibility
    run_id = os.path.basename(os.path.normpath(run_dir))
    m = {
        "schema_version": "30.1",
        "run_id": run_id,
        "project_id": run_id,
        "stages": [],
    }
    op = out_path or os.path.join(run_dir, 'artifact_manifest.json')
    with open(op, 'w', encoding='utf-8') as f:
        json.dump(m, f, indent=2)
    return op, m


def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(2)
    cmd, run_dir = sys.argv[1], sys.argv[2]
    if cmd == 'validate':
        mp = None
        if '--manifest' in sys.argv:
            mp = sys.argv[sys.argv.index('--manifest')+1]
        # prefer run_manifest.json when not specified
        if not mp:
            cand = os.path.join(run_dir, 'run_manifest.json')
            if os.path.isfile(cand):
                mp = cand
        if mm:
            r = mm.validate_manifest(run_dir, manifest_path=mp, require_final=False)
            print(json.dumps(r, indent=2))
            sys.exit(0 if r['status'] == 'PASS' else 1)
        else:
            print(json.dumps({"status":"FAIL","errors":["manifest_manager unavailable"]}))
            sys.exit(1)
    elif cmd == 'generate':
        op = None
        if '--out' in sys.argv:
            op = sys.argv[sys.argv.index('--out')+1]
        if mm and os.path.isfile(mm.manifest_path_for(run_dir)):
            # copy run manifest as artifact manifest
            src = mm.manifest_path_for(run_dir)
            if op:
                import shutil
                shutil.copyfile(src, op)
                print(f"generated: {op}")
            else:
                print(f"generated: {src}")
            sys.exit(0)
        p, m = legacy_generate(run_dir, op)
        print(f"generated: {p} ({len(m['stages'])} stages)")
        sys.exit(0)
    else:
        print(__doc__); sys.exit(2)


if __name__ == '__main__':
    main()