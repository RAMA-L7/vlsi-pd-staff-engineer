#!/usr/bin/env python3
"""
Run-level manifest lifecycle and validation (Phase 33).
Supports schema 30.1 style fields with atomic updates and strict validation.
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime

SCHEMA_VERSION = "30.1"
SUPPORTED_SCHEMAS = {"30.1", "33.1"}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def utc_now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def parse_ts(ts):
    if ts is None:
        return None
    if not isinstance(ts, str):
        return None
    s = ts.strip()
    # normalize Z
    if s.endswith('Z'):
        s2 = s[:-1] + '+00:00'
    else:
        s2 = s
    # Python 3.10 fromisoformat doesn't accept >6 fractional digits in some cases; trim
    m = re.match(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.(\d+))?([+-]\d{2}:\d{2})$', s2)
    if m:
        base, _, frac, tz = m.group(1), m.group(2), m.group(3), m.group(4)
        if frac and len(frac) > 6:
            frac = frac[:6]
        s3 = base + ('.' + frac if frac else '') + tz
        try:
            return datetime.fromisoformat(s3)
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s2)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None


def load_json(path):
    with open(path, 'r', encoding='utf-8-sig') as f:
        return json.load(f)


def atomic_write_json(path, data):
    d = os.path.dirname(path) or '.'
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.tmp_manifest_', suffix='.json', dir=d)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(data, f, indent=2, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def to_wsl_path(p):
    if not p:
        return p
    m = re.match(r'^([A-Za-z]):[\\/](.*)$', p)
    if m:
        drive = m.group(1).lower()
        rest = m.group(2).replace('\\', '/')
        return f"/mnt/{drive}/{rest}"
    return p


def resolve_existing_path(run_dir, p):
    """Resolve artifact path across Windows/WSL forms."""
    if not p:
        return p
    candidates = []
    ap = rel_or_abs(run_dir, p)
    candidates.append(ap)
    if is_abs_any(p):
        candidates.append(p)
    # try WSL form of Windows path
    for c in list(candidates):
        if c and re.match(r'^[A-Za-z]:', c):
            candidates.append(to_wsl_path(c))
    # try Windows form of WSL path
    for c in list(candidates):
        if not c:
            continue
        m = re.match(r'^/mnt/([a-zA-Z])/(.*)$', c)
        if m:
            candidates.append(f"{m.group(1).upper()}:/" + m.group(2))
            candidates.append(f"{m.group(1).upper()}:\\" + m.group(2).replace('/', '\\'))
    # dedupe preserving order
    seen = set(); uniq = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c); uniq.append(c)
    for c in uniq:
        if os.path.exists(c):
            return c
    return ap


def norm_path(p):
    if p is None:
        return None
    # normalize WSL and Windows paths to comparable form
    m = re.match(r'^/mnt/([a-zA-Z])/(.*)$', p)
    if m:
        p = f"{m.group(1).upper()}:/" + m.group(2)
    m2 = re.match(r'^([A-Za-z]):[\\/](.*)$', p)
    if m2:
        p = f"{m2.group(1).upper()}:/" + m2.group(2).replace('\\', '/')
    return os.path.normcase(os.path.normpath(p))


def is_under(child, parent):
    try:
        cp = os.path.commonpath([norm_path(child), norm_path(parent)])
        return cp == norm_path(parent)
    except Exception:
        return False


def is_abs_any(p):
    if not p:
        return False
    return os.path.isabs(p) or bool(re.match(r'^[A-Za-z]:[\\/]', p)) or bool(re.match(r'^/mnt/[a-zA-Z]/', p))


def rel_or_abs(run_dir, p):
    if not p:
        return p
    if is_abs_any(p):
        return p
    return os.path.join(run_dir, p)


def artifact_info(run_dir, path, required=True):
    ap = rel_or_abs(run_dir, path)
    exists = os.path.isfile(ap)
    info = {
        "path": ap,
        "relative_path": os.path.relpath(ap, run_dir) if is_under(ap, run_dir) else ap,
        "required": bool(required),
        "exists": exists,
        "sha256": None,
        "size": 0,
    }
    if exists:
        st = os.stat(ap)
        info["size"] = st.st_size
        info["sha256"] = sha256_file(ap)
        info["mtime_utc"] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(st.st_mtime))
    return info


def manifest_path_for(run_dir):
    return os.path.join(run_dir, 'run_manifest.json')


def init_manifest(run_dir, run_id=None, project_name=None, project_id=None, top_module=None, schema_version=SCHEMA_VERSION, tool_versions=None):
    os.makedirs(run_dir, exist_ok=True)
    rid = run_id or os.path.basename(os.path.normpath(run_dir))
    m = {
        "schema_version": schema_version,
        "run_id": rid,
        "project_id": project_id or project_name or "",
        "project_name": project_name or project_id or "",
        "top_module": top_module or "",
        "run_status": "INITIALIZED",
        "validation_status": "NOT_VALIDATED",
        "created_utc": utc_now(),
        "updated_utc": utc_now(),
        "tool_versions": tool_versions or {},
        "environment": {},
        "stages": [],
    }
    atomic_write_json(manifest_path_for(run_dir), m)
    return m


def load_manifest(run_dir, path=None):
    return load_json(path or manifest_path_for(run_dir))


def _find_stage(manifest, stage_id):
    for i, s in enumerate(manifest.get('stages', [])):
        if s.get('stage_id') == stage_id or s.get('stage') == stage_id:
            return i, s
    return None, None


def upsert_stage(run_dir, stage_record, manifest_path=None):
    mp = manifest_path or manifest_path_for(run_dir)
    m = load_manifest(run_dir, mp) if os.path.isfile(mp) else init_manifest(run_dir)
    idx, _ = _find_stage(m, stage_record.get('stage_id') or stage_record.get('stage'))
    # normalize stage_id key
    sr = dict(stage_record)
    if 'stage' in sr and 'stage_id' not in sr:
        sr['stage_id'] = sr['stage']
    if idx is None:
        m.setdefault('stages', []).append(sr)
    else:
        m['stages'][idx] = sr
    m['updated_utc'] = utc_now()
    atomic_write_json(mp, m)
    return m


def finalize_manifest(run_dir, run_status=None, tool_versions=None, manifest_path=None):
    mp = manifest_path or manifest_path_for(run_dir)
    m = load_manifest(run_dir, mp)
    if tool_versions:
        tv = m.get('tool_versions') or {}
        tv.update(tool_versions)
        m['tool_versions'] = tv
    if run_status:
        m['run_status'] = run_status
    m['completed_utc'] = utc_now()
    m['updated_utc'] = utc_now()
    atomic_write_json(mp, m)
    return m


def validate_manifest(run_dir, manifest_path=None, require_final=False, expected_run_id=None, expected_project_id=None, allowed_stage_ids=None):
    mp = manifest_path or manifest_path_for(run_dir)
    errors = []
    warnings = []
    result = {"path": mp, "status": "FAIL", "errors": errors, "warnings": warnings}
    if not os.path.isfile(mp):
        errors.append("manifest file missing")
        return result
    try:
        m = load_json(mp)
    except Exception as e:
        errors.append(f"invalid JSON: {e}")
        return result

    run_id = m.get('run_id')
    project_id = m.get('project_id') or m.get('project_name')
    schema = m.get('schema_version')

    if not schema:
        errors.append("missing schema_version")
    elif schema not in SUPPORTED_SCHEMAS:
        errors.append(f"invalid or unsupported schema_version: {schema}")
    if not run_id:
        errors.append("missing run_id")
    if not project_id:
        errors.append("missing project_id/project_name")

    if expected_run_id and run_id and run_id != expected_run_id:
        errors.append(f"wrong run_id: {run_id} != {expected_run_id}")
    if expected_project_id and project_id and project_id != expected_project_id:
        errors.append(f"wrong project_id: {project_id} != {expected_project_id}")

    # top-level timestamps
    for k in ['created_utc', 'updated_utc', 'completed_utc', 'generated_utc']:
        if k in m and m[k] is not None and parse_ts(m[k]) is None:
            errors.append(f"invalid timestamp: {k}")

    stages = m.get('stages')
    if stages is None:
        errors.append("missing stages")
        stages = []
    if not isinstance(stages, list):
        errors.append("stages must be a list")
        stages = []

    seen = set()
    run_dir_abs = os.path.abspath(run_dir)

    allowed = set(allowed_stage_ids or [])
    for i, s in enumerate(stages):
        sid = s.get('stage_id') or s.get('stage')
        if not sid:
            errors.append(f"stage[{i}] missing stage_id")
        else:
            if sid in seen:
                errors.append(f"duplicate stage entry: {sid}")
            seen.add(sid)
            if allowed and sid not in allowed:
                errors.append(f"stage {sid}: wrong stage_id (not allowed)")

        s_run = s.get('run_id')
        if s_run and run_id and s_run != run_id:
            errors.append(f"stage {sid}: wrong run_id {s_run} != {run_id}")

        status = s.get('stage_status') or s.get('status')
        ec = s.get('stage_exit_code') if 'stage_exit_code' in s else s.get('exit_code')
        ev = s.get('evidence_class', 'FRESH_EXECUTION')

        # tool version checks
        tv = s.get('tool_versions')
        tv2 = s.get('tool_version')
        if tv is not None and isinstance(tv, (dict, list, str)) and not tv and status in ('PASS', 'FAIL', 'PARTIAL', 'BLOCKED'):
            errors.append(f"stage {sid}: missing tool version")
        if tv2 is not None and tv2 == '' and status in ('PASS', 'FAIL', 'PARTIAL', 'BLOCKED'):
            errors.append(f"stage {sid}: missing tool version")

        # timestamps
        st_ts = s.get('start_timestamp') or s.get('started_utc')
        en_ts = s.get('end_timestamp') or s.get('finished_utc')
        pst = parse_ts(st_ts) if st_ts else None
        pen = parse_ts(en_ts) if en_ts else None
        if st_ts and pst is None:
            errors.append(f"stage {sid}: invalid timestamp start")
        if en_ts and pen is None:
            errors.append(f"stage {sid}: invalid timestamp end")
        if pst and pen and pen < pst:
            errors.append(f"stage {sid}: end timestamp earlier than start timestamp")

        # PASS/FAIL/exit consistency for fresh executions
        if status == 'PASS' and ev == 'FRESH_EXECUTION' and ec not in (0, None):
            errors.append(f"stage {sid}: nonzero exit code with PASS status ({ec})")
        if status == 'FAIL' and ev == 'FRESH_EXECUTION' and ec == 0:
            errors.append(f"stage {sid}: zero exit code with FAIL status")

        def check_artifacts(items, kind):
            for a in (items or []):
                ap = a.get('path') or a.get('artifact_path')
                if not ap:
                    errors.append(f"stage {sid}: {kind} missing path")
                    continue
                ap_abs = rel_or_abs(run_dir, ap)
                # allow Windows or WSL forms referring to same run workspace
                if not is_under(ap_abs, run_dir_abs):
                    errors.append(f"stage {sid}: artifact reference outside run workspace: {ap}")
                required = a.get('required', True)
                resolved = resolve_existing_path(run_dir, ap)
                exists = os.path.isfile(resolved)
                if required and status == 'PASS' and ev == 'FRESH_EXECUTION' and not exists:
                    errors.append(f"stage {sid}: missing required output: {ap}")
                if exists:
                    actual = sha256_file(resolved)
                    expected = a.get('sha256')
                    if expected and expected != actual:
                        errors.append(f"stage {sid}: incorrect SHA256 for {ap}")
                    expected_size = a.get('size') if 'size' in a else a.get('bytes')
                    if expected_size is not None and int(expected_size) != os.path.getsize(resolved):
                        errors.append(f"stage {sid}: size mismatch for {ap}")

        check_artifacts(s.get('output_artifacts') or s.get('outputs'), 'output')
        for a in (s.get('logs') or []):
            ap = a.get('path')
            if not ap:
                continue
            ap_abs = rel_or_abs(run_dir, ap)
            if not is_under(ap_abs, run_dir_abs):
                errors.append(f"stage {sid}: log reference outside run workspace: {ap}")
            required = a.get('required', False)
            resolved = resolve_existing_path(run_dir, ap)
            exists = os.path.isfile(resolved)
            if required and status in ('PASS', 'FAIL') and not exists:
                errors.append(f"stage {sid}: missing log: {ap}")
            if exists and a.get('sha256') and a.get('sha256') != sha256_file(resolved):
                errors.append(f"stage {sid}: incorrect SHA256 for log {ap}")

    rs = m.get('run_status')
    if require_final and rs in (None, 'INITIALIZED', 'RUNNING'):
        errors.append('manifest not finalized')

    result['run_id'] = run_id
    result['project_id'] = project_id
    result['schema_version'] = schema
    result['status'] = 'PASS' if not errors else 'FAIL'
    return result


def _cli():
    import argparse
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd')
    p_init = sub.add_parser('init')
    p_init.add_argument('run_dir')
    p_init.add_argument('--run-id')
    p_init.add_argument('--project-id')
    p_init.add_argument('--project-name')
    p_init.add_argument('--top-module')
    p_init.add_argument('--schema-version', default=SCHEMA_VERSION)
    p_init.add_argument('--tool-versions-json')
    p_init.add_argument('--manifest-path')

    p_upd = sub.add_parser('upsert-stage')
    p_upd.add_argument('run_dir')
    p_upd.add_argument('--stage-json-file', required=True)
    p_upd.add_argument('--manifest-path')

    p_fin = sub.add_parser('finalize')
    p_fin.add_argument('run_dir')
    p_fin.add_argument('--run-status')
    p_fin.add_argument('--tool-versions-json')
    p_fin.add_argument('--manifest-path')

    p_val = sub.add_parser('validate')
    p_val.add_argument('run_dir')
    p_val.add_argument('--manifest-path')
    p_val.add_argument('--require-final', action='store_true')
    p_val.add_argument('--expected-run-id')
    p_val.add_argument('--expected-project-id')
    p_val.add_argument('--allowed-stage-ids')

    args = p.parse_args()
    if args.cmd == 'init':
        tv = {}
        if args.tool_versions_json:
            try: tv = json.loads(args.tool_versions_json)
            except Exception: tv = {"_raw": args.tool_versions_json}
        m = init_manifest(args.run_dir, run_id=args.run_id, project_id=args.project_id, project_name=args.project_name, top_module=args.top_module, schema_version=args.schema_version, tool_versions=tv)
        if args.manifest_path and os.path.abspath(args.manifest_path) != os.path.abspath(manifest_path_for(args.run_dir)):
            atomic_write_json(args.manifest_path, m)
        print(json.dumps({"status":"OK","path":manifest_path_for(args.run_dir),"run_id":m.get("run_id")}))
        return 0
    elif args.cmd == 'upsert-stage':
        sr = load_json(args.stage_json_file)
        m = upsert_stage(args.run_dir, sr, manifest_path=getattr(args,'manifest_path',None))
        print(json.dumps({"status":"OK","stages":len(m.get('stages',[]))}))
        return 0
    elif args.cmd == 'finalize':
        tv = None
        if args.tool_versions_json:
            try: tv = json.loads(args.tool_versions_json)
            except Exception: tv = None
        m = finalize_manifest(args.run_dir, run_status=args.run_status, tool_versions=tv, manifest_path=getattr(args,'manifest_path',None))
        print(json.dumps({"status":"OK","run_status":m.get('run_status')}))
        return 0
    elif args.cmd == 'validate':
        allowed = None
        if getattr(args, 'allowed_stage_ids', None):
            allowed = [s.strip() for s in args.allowed_stage_ids.split(',') if s.strip()]
        r = validate_manifest(
            args.run_dir,
            manifest_path=getattr(args,'manifest_path',None),
            require_final=args.require_final,
            expected_run_id=getattr(args,'expected_run_id',None),
            expected_project_id=getattr(args,'expected_project_id',None),
            allowed_stage_ids=allowed,
        )
        print(json.dumps(r, indent=2))
        return 0 if r['status']=='PASS' else 1
    else:
        p.print_help(); return 2


if __name__ == '__main__':
    sys.exit(_cli())
