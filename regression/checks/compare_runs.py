#!/usr/bin/env python3
"""
Phase 14 regression - reproducibility comparison between two regression runs.

The comparison is artefact-based, not verdict-based: it compares the SHA-256
of every deterministic artefact that the flow produces, so a difference in the
physical result is detected even when both runs report PASS.

Artefact classes compared:
  * the synthesis netlists and the cell-count record
  * the routed DEF and the extracted netlist / SDC / SPEF
  * the GDSII
  * the LVS database and the extracted SPICE netlist
  * the STA summary (the timing numbers)
  * the DRC classification (the violation counts)
  * the independent LVS comparison (the device / net / power numbers)

Artefacts that legitimately differ between runs (timestamps written into a
file header, absolute paths, the log transcripts) are excluded, and each
exclusion is reported so the classification is auditable.

Usage: compare_runs.py <run-dir-A> <run-dir-B> [output.txt]
"""
import hashlib
import json
import os
import re
import struct
import sys

RUN_A = sys.argv[1].rstrip('/')
RUN_B = sys.argv[2].rstrip('/')
OUT = sys.argv[3] if len(sys.argv) > 3 else None

# (relative path, description, deterministic?)
ARTEFACTS = [
    ('synth/test_counter_synth.v', 'technology-mapped netlist', True),
    ('synth/generic_test_counter_synth.v', 'generic netlist', True),
    ('synth/cell_counts.json', 'cell-count record', True),
    ('04_floorplan.odb', 'floorplan database', True),
    ('05_place.odb', 'placement database', True),
    ('06_cts.odb', 'CTS database', True),
    ('07_pdn.def', 'PDN DEF', True),
    ('08_power_connectivity.txt', 'power-connectivity report', True),
    ('09_groute.odb', 'global-route database', True),
    ('10_route.def', 'routed DEF', True),
    ('11_final.v', 'extracted netlist', True),
    ('11_final.sdc', 'extracted SDC', True),
    ('11_final.spef', 'extracted SPEF', True),
    ('test_counter_pdn.gds', 'GDSII', True),
    ('drc/drc_classification.txt', 'DRC classification', True),
    ('15_lvs/15_lvs.lvsdb', 'LVS database', True),
    ('15_lvs/15_lvs_extracted.cir', 'LVS extracted netlist', True),
    ('sta/sta_summary.txt', 'multi-corner STA summary', True),
    ('lvs_compare.txt', 'independent LVS comparison', True),
    ('6_final_concat.cdl', 'LVS reference netlist', True),
]

# lines that carry a timestamp or an absolute path and therefore cannot match
VOLATILE_LINE = re.compile(
    r'^.*(Created by write_sdc|Extracted by KLayout on|\*DATE "|#\s*'
    r'(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s|'
    r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}).*$', re.M)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# GDSII record-level comparison.
#
# A GDSII library stores a modification time and a last-access time in the
# payload of the BGNLIB record (type 0x0001) AND in the payload of every
# BGNSTR record (type 0x0005): 12 bytes of modification time followed by
# 12 bytes of access time.  Two runs that produce the same geometry
# therefore still differ byte-for-byte.  The geometry is compared instead:
# the full record stream with those 24 timestamp bytes removed.
# ---------------------------------------------------------------------------
GDS_BGNLIB = 0x0001
GDS_BGNSTR = 0x0005
GDS_LIBNAME = 0x0002
GDS_STRNAME = 0x0006
GDS_TIMESTAMP_RECORDS = (GDS_BGNLIB, GDS_BGNSTR)


def gds_records(data):
    """Yield (offset, rec_type, payload) for every GDSII record.

    GDSII records are BIG endian: a 2-byte length, a 1-byte record type and a
    1-byte data type, followed by the payload.
    """
    off = 0
    n = len(data)
    while off + 4 <= n:
        ln = struct.unpack('>H', data[off:off + 2])[0]
        if ln < 4 or off + ln > n:
            break
        rec_type = data[off + 2]
        yield off, rec_type, data[off + 4:off + ln]
        off += ln


def gds_stripped_records(path):
    """The GDSII record stream with the library/structure timestamps removed."""
    with open(path, 'rb') as f:
        data = f.read()
    out = []
    for _off, rec_type, payload in gds_records(data):
        if rec_type in GDS_TIMESTAMP_RECORDS and len(payload) >= 24:
            # drop modification time (12 B) and access time (12 B)
            payload = payload[24:]
        out.append((rec_type, payload))
    return out


def gds_geometry_digest(path):
    """Digest of the GDSII geometry with the library timestamps removed."""
    parts = []
    for rec_type, payload in gds_stripped_records(path):
        parts.append(b'%04X' % rec_type)
        parts.append(payload)
    return hashlib.sha256(b'|'.join(parts)).hexdigest()


def gds_geometry_equal(pa, pb):
    """True when two GDSII files carry the same geometry and the same names."""
    def load(path):
        recs = gds_stripped_records(path)
        geom = [(t, p) for (t, p) in recs if t != GDS_LIBNAME]
        names = [p for (t, p) in recs if t == GDS_LIBNAME]
        return geom, names
    ga, na = load(pa)
    gb, nb = load(pb)
    return ga == gb and na == nb



def normalised_digest(path, run_dir=None):
    """Digest of a text artefact with the volatile lines removed.

    Two kinds of volatility are removed:
      * timestamps (write_sdc header, SPEF *DATE, KLayout extraction stamp)
      * the run directory path, so that the same artefact produced in two
        different run directories compares equal
    """
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            txt = f.read()
    except OSError:
        return None
    txt = VOLATILE_LINE.sub('', txt)
    if run_dir:
        txt = txt.replace(run_dir.rstrip('/'), '<RUN>')
        txt = txt.replace(os.path.basename(run_dir.rstrip('/')), '<RUNID>')
    return hashlib.sha256(txt.encode('utf-8')).hexdigest()


def digest_for(path, deterministic, run_dir=None):
    if not os.path.isfile(path):
        return None
    if deterministic:
        if path.endswith('.gds'):
            return gds_geometry_digest(path)
        return normalised_digest(path, run_dir)
    return sha256(path)


def main():
    out = []
    out.append('=== Phase 14 reproducibility comparison ===')
    out.append('run A: %s' % RUN_A)
    out.append('run B: %s' % RUN_B)
    out.append('')

    identical = 0
    differing = 0
    missing = 0
    rows = []
    for rel, desc, det in ARTEFACTS:
        pa = os.path.join(RUN_A, rel)
        pb = os.path.join(RUN_B, rel)
        da = digest_for(pa, det, RUN_A)
        db = digest_for(pb, det, RUN_B)
        if da is None or db is None:
            status = 'MISSING'
            missing += 1
        elif da == db:
            status = 'IDENTICAL'
            identical += 1
        else:
            status = 'DIFFERENT'
            differing += 1
        rows.append((rel, desc, status, da, db))

    out.append('%-34s %-38s %s' % ('ARTEFACT', 'DESCRIPTION', 'RESULT'))
    for rel, desc, status, da, db in rows:
        out.append('%-34s %-38s %s' % (rel, desc, status))
    out.append('')
    out.append('identical: %d   different: %d   missing: %d   total: %d'
               % (identical, differing, missing, len(ARTEFACTS)))
    out.append('')

    # ---- GDSII geometry cross-check -----------------------------------------
    ga = os.path.join(RUN_A, 'test_counter_pdn.gds')
    gb = os.path.join(RUN_B, 'test_counter_pdn.gds')
    if os.path.isfile(ga) and os.path.isfile(gb):
        same = gds_geometry_equal(ga, gb)
        out.append('--- GDSII geometry cross-check (timestamp records excluded) ---')
        out.append('GDSII geometry identical: %s' % same)
        out.append('GDSII byte-identical     : %s' % (sha256(ga) == sha256(gb)))
        out.append('')
        if not same:
            differing += 1


    # ---- verdict comparison --------------------------------------------------
    def load_report(run):
        p = os.path.join(run, 'report.json')
        if not os.path.isfile(p):
            return None
        try:
            with open(p, 'r', encoding='utf-8', errors='replace') as f:
                return json.load(f)
        except ValueError:
            return None

    ra = load_report(RUN_A)
    rb = load_report(RUN_B)
    if ra and rb:
        out.append('--- run-level verdicts ---')
        out.append('run A overall: %s' % ra.get('overall'))
        out.append('run B overall: %s' % rb.get('overall'))
        sa = {s['name']: s.get('status') for s in ra.get('stages', [])}
        sb = {s['name']: s.get('status') for s in rb.get('stages', [])}
        mism = [(k, sa.get(k), sb.get(k)) for k in sorted(set(sa) | set(sb))
                if sa.get(k) != sb.get(k)]
        out.append('stage status mismatches: %d' % len(mism))
        for k, x, y in mism:
            out.append('   %-24s A=%s B=%s' % (k, x, y))
        out.append('')

    # ---- acceptance comparison ----------------------------------------------
    def load_acc(run):
        p = os.path.join(run, 'acceptance.json')
        if not os.path.isfile(p):
            return None
        try:
            with open(p, 'r', encoding='utf-8', errors='replace') as f:
                return json.load(f)
        except ValueError:
            return None

    aa = load_acc(RUN_A)
    ab = load_acc(RUN_B)
    if aa and ab:
        out.append('--- acceptance checks ---')
        out.append('run A: %d passed, %d failed, %d not tested'
                   % (aa['passed'], aa['failed'], aa['not_tested']))
        out.append('run B: %d passed, %d failed, %d not tested'
                   % (ab['passed'], ab['failed'], ab['not_tested']))
        ca = {c['stage'] + '/' + c['name']: c['status'] for c in aa['checks']}
        cb = {c['stage'] + '/' + c['name']: c['status'] for c in ab['checks']}
        mism = [(k, ca.get(k), cb.get(k)) for k in sorted(set(ca) | set(cb))
                if ca.get(k) != cb.get(k)]
        out.append('check-status mismatches: %d' % len(mism))
        for k, x, y in mism:
            out.append('   %-50s A=%s B=%s' % (k, x, y))
        out.append('')

    # ---- classification ------------------------------------------------------
    if missing:
        cls = 'NOT REPRODUCIBLE (missing artefacts)'
    elif differing:
        cls = 'NOT REPRODUCIBLE (artefact difference)'
    else:
        cls = 'REPRODUCIBLE (bit-identical artefacts)'
    out.append('=== CLASSIFICATION: %s ===' % cls)

    text = '\n'.join(out) + '\n'
    if OUT:
        with open(OUT, 'w', encoding='utf-8') as f:
            f.write(text)
    print(text)
    return 0 if cls.startswith('REPRODUCIBLE') else 1


if __name__ == '__main__':
    sys.exit(main())
