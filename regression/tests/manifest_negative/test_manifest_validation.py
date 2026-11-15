#!/usr/bin/env python3
"""Portable negative/positive tests for manifest_manager validation (Phase 33)."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MM = os.path.abspath(os.path.join(HERE, '..', '..', 'checks', 'manifest_manager.py'))
PY = sys.executable


def sha256_path(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def run_validate(run_dir, manifest_path, extra=None):
    cmd = [PY, MM, 'validate', run_dir, '--manifest-path', manifest_path]
    if extra:
        cmd += extra
    p = subprocess.run(cmd, capture_output=True, text=True)
    try:
        vr = json.loads(p.stdout) if p.stdout.strip().startswith('{') else {}
    except Exception:
        vr = {'raw': p.stdout[-400:], 'stderr': p.stderr[-200:]}
    return p.returncode, vr


def main():
    with tempfile.TemporaryDirectory(prefix='mm_neg_') as td:
        run = os.path.join(td, 'active_run')
        os.makedirs(run, exist_ok=True)
        with open(os.path.join(run, 'a.txt'), 'w', newline='\n') as f:
            f.write('A\n')
        outside = os.path.join(td, 'outside.txt')
        with open(outside, 'w', newline='\n') as f:
            f.write('X\n')

        base = {
            "schema_version": "30.1", "run_id": "active_run", "project_id": "proj",
            "project_name": "proj", "top_module": "top",
            "run_status": "COMPLETED", "validation_status": "NOT_VALIDATED",
            "created_utc": "2026-11-15T10:00:00Z", "updated_utc": "2026-11-15T10:05:00Z",
            "completed_utc": "2026-11-15T10:05:00Z",
            "tool_versions": {"yosys": "0.69"},
            "stages": [{
                "stage_id": "01_synthesis", "stage": "01_synthesis", "run_id": "active_run",
                "stage_status": "PASS", "status": "PASS", "stage_exit_code": 0, "exit_code": 0,
                "start_timestamp": "2026-11-15T10:00:10Z", "end_timestamp": "2026-11-15T10:01:00Z",
                "started_utc": "2026-11-15T10:00:10Z", "finished_utc": "2026-11-15T10:01:00Z",
                "tool_versions": {"yosys": "0.69"}, "input_artifacts": [],
                "output_artifacts": [{
                    "path": os.path.join(run, 'a.txt'), "required": True, "exists": True,
                    "sha256": sha256_path(os.path.join(run, 'a.txt')),
                    "size": os.path.getsize(os.path.join(run, 'a.txt'))
                }],
                "logs": [], "evidence_class": "FRESH_EXECUTION"
            }]
        }

        results = []

        def add(name, expect_fail, rc, vr, extra_ok=None):
            failed = (rc != 0 or vr.get('status') != 'PASS')
            has_errors = len(vr.get('errors', [])) > 0
            ok = (failed == expect_fail) and ((has_errors or not expect_fail) if expect_fail else vr.get('status') == 'PASS')
            if extra_ok is not None:
                ok = ok and extra_ok
            results.append({"id": name, "verdict": "PASS" if ok else "FAIL", "expect_fail": expect_fail, "rc": rc, "status": vr.get('status'), "errors": vr.get('errors', [])[:4]})
            print(name, results[-1]['verdict'], vr.get('status'), rc)

        # 1 missing manifest
        rc, vr = run_validate(run, os.path.join(td, 'missing.json'))
        add('01_missing_manifest', True, rc, vr)

        # 2 invalid json
        pth = os.path.join(td, 'bad.json'); open(pth, 'w').write('{')
        rc, vr = run_validate(run, pth); add('02_invalid_json', True, rc, vr)

        # 3 wrong run id
        m = copy.deepcopy(base); m['run_id'] = 'other'
        pth = os.path.join(td, 'wrun.json'); json.dump(m, open(pth, 'w'))
        rc, vr = run_validate(run, pth); add('03_wrong_run_id', True, rc, vr)

        # 4 wrong project id (with expected)
        m = copy.deepcopy(base); m['project_id'] = 'wrong'; m['project_name'] = 'wrong'
        pth = os.path.join(td, 'wproj.json'); json.dump(m, open(pth, 'w'))
        rc, vr = run_validate(run, pth, ['--expected-project-id', 'proj']); add('04_wrong_project_id', True, rc, vr)

        # 5 wrong stage id (allowed list)
        m = copy.deepcopy(base); m['stages'][0]['stage_id'] = '99_bogus'; m['stages'][0]['stage'] = '99_bogus'
        pth = os.path.join(td, 'wstage.json'); json.dump(m, open(pth, 'w'))
        rc, vr = run_validate(run, pth, ['--allowed-stage-ids', '01_synthesis,00_stage00']); add('05_wrong_stage_id', True, rc, vr)

        # 6 missing required output
        m = copy.deepcopy(base); m['stages'][0]['output_artifacts'][0]['path'] = os.path.join(run, 'nope.txt')
        pth = os.path.join(td, 'miss.json'); json.dump(m, open(pth, 'w'))
        rc, vr = run_validate(run, pth); add('06_missing_required_output', True, rc, vr)

        # 7 incorrect sha256
        m = copy.deepcopy(base); m['stages'][0]['output_artifacts'][0]['sha256'] = '0'*64
        pth = os.path.join(td, 'sha.json'); json.dump(m, open(pth, 'w'))
        rc, vr = run_validate(run, pth); add('07_incorrect_sha256', True, rc, vr)

        # 8 nonzero exit with PASS
        m = copy.deepcopy(base); m['stages'][0]['stage_exit_code'] = 2; m['stages'][0]['exit_code'] = 2
        pth = os.path.join(td, 'nzp.json'); json.dump(m, open(pth, 'w'))
        rc, vr = run_validate(run, pth); add('08_nonzero_exit_with_pass', True, rc, vr)

        # 9 zero exit with FAIL
        m = copy.deepcopy(base); m['stages'][0]['stage_status'] = 'FAIL'; m['stages'][0]['status'] = 'FAIL'
        pth = os.path.join(td, 'zf.json'); json.dump(m, open(pth, 'w'))
        rc, vr = run_validate(run, pth); add('09_zero_exit_with_fail', True, rc, vr)

        # 10 stale/outside workspace artifact
        oldd = os.path.join(td, 'old_run'); os.makedirs(oldd, exist_ok=True)
        with open(os.path.join(oldd, 'a.txt'), 'w') as f: f.write('OLD\n')
        m = copy.deepcopy(base); m['stages'][0]['output_artifacts'][0]['path'] = os.path.join(oldd, 'a.txt')
        m['stages'][0]['output_artifacts'][0]['sha256'] = sha256_path(os.path.join(oldd, 'a.txt'))
        m['stages'][0]['output_artifacts'][0]['size'] = os.path.getsize(os.path.join(oldd, 'a.txt'))
        pth = os.path.join(td, 'stale.json'); json.dump(m, open(pth, 'w'))
        rc, vr = run_validate(run, pth); add('10_stale_outside_workspace', True, rc, vr)

        # 11 duplicate stage
        m = copy.deepcopy(base); m['stages'].append(copy.deepcopy(m['stages'][0]))
        pth = os.path.join(td, 'dup.json'); json.dump(m, open(pth, 'w'))
        rc, vr = run_validate(run, pth); add('11_duplicate_stage', True, rc, vr)

        # 12 missing tool version
        m = copy.deepcopy(base); m['stages'][0]['tool_versions'] = {}
        pth = os.path.join(td, 'tv.json'); json.dump(m, open(pth, 'w'))
        rc, vr = run_validate(run, pth); add('12_missing_tool_version', True, rc, vr)

        # 13 invalid timestamp
        m = copy.deepcopy(base); m['stages'][0]['start_timestamp'] = 'bad'
        pth = os.path.join(td, 'ts.json'); json.dump(m, open(pth, 'w'))
        rc, vr = run_validate(run, pth); add('13_invalid_timestamp', True, rc, vr)

        # 14 end before start
        m = copy.deepcopy(base); m['stages'][0]['end_timestamp'] = '2026-11-15T09:00:00Z'
        pth = os.path.join(td, 'ebs.json'); json.dump(m, open(pth, 'w'))
        rc, vr = run_validate(run, pth); add('14_end_before_start', True, rc, vr)

        # 15 outside workspace ref
        m = copy.deepcopy(base); m['stages'][0]['output_artifacts'][0]['path'] = outside
        m['stages'][0]['output_artifacts'][0]['sha256'] = sha256_path(outside)
        m['stages'][0]['output_artifacts'][0]['size'] = os.path.getsize(outside)
        pth = os.path.join(td, 'outref.json'); json.dump(m, open(pth, 'w'))
        rc, vr = run_validate(run, pth); add('15_outside_workspace', True, rc, vr)

        # 16 corrupted after manifest
        with open(os.path.join(run, 'c.txt'), 'w', newline='\n') as f: f.write('ORIG\n')
        m = copy.deepcopy(base); m['stages'][0]['output_artifacts'][0] = {
            'path': os.path.join(run, 'c.txt'), 'required': True, 'exists': True,
            'sha256': sha256_path(os.path.join(run, 'c.txt')), 'size': os.path.getsize(os.path.join(run, 'c.txt'))
        }
        pth = os.path.join(td, 'corr.json'); json.dump(m, open(pth, 'w'))
        with open(os.path.join(run, 'c.txt'), 'w', newline='\n') as f: f.write('CORRUPTED\n')
        rc, vr = run_validate(run, pth); add('16_corrupted_output', True, rc, vr)

        # 17 incomplete manifest
        m = {"schema_version": "30.1"}
        pth = os.path.join(td, 'inc.json'); json.dump(m, open(pth, 'w'))
        rc, vr = run_validate(run, pth); add('17_incomplete_manifest', True, rc, vr)

        # 18 invalid schema
        m = copy.deepcopy(base); m['schema_version'] = '99.9'
        pth = os.path.join(td, 'schema.json'); json.dump(m, open(pth, 'w'))
        rc, vr = run_validate(run, pth); add('18_invalid_schema', True, rc, vr)

        # valid
        pth = os.path.join(td, 'valid.json'); json.dump(base, open(pth, 'w'))
        rc, vr = run_validate(run, pth); add('valid_completed_manifest', False, rc, vr)

        total = len(results)
        passed = sum(1 for r in results if r['verdict'] == 'PASS')
        failed = total - passed
        print(json.dumps({'total': total, 'pass': passed, 'fail': failed}, indent=2))
        return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
