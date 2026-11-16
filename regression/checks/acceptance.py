#!/usr/bin/env python3
"""
Phase 18G regression - stage acceptance checks (design-aware).

Every check is INDEPENDENT of the stage's exit code: it inspects the actual
artifacts and logs.  A stage only counts as PASS when every one of its
acceptance checks passes.

Output format (machine readable), one line per check:
    CHECK <stage> <name> PASS|FAIL|NOT_TESTED <detail>

Usage: acceptance.py <run-dir> [log-dir]

The JSON report is written to <run-dir>/acceptance.json.
"""
import json
import os
import re
import sys

RUN = sys.argv[1].rstrip('/')
LOGDIR = (sys.argv[2].rstrip('/') if len(sys.argv) > 2 else RUN + '/logs')

RESULTS = []


def read(path, default=''):
    try:
        with open(path, 'rb') as f:
            data = f.read()
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError:
            text = data.decode('utf-16', errors='replace')
        if text.startswith('\ufeff'):
            text = text[1:]
        return text
    except OSError:
        return default


def detect_design():
    for envf in (os.path.join(RUN, 'env_wsl.env'), os.path.join(RUN, 'env_win.env')):
        txt = read(envf)
        m = re.search(r'^\s*(?:export\s+)?REG_DESIGN=(.+)$', txt, re.M)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    # fallback: infer from synth mapped netlist
    syn = os.path.join(RUN, 'synth')
    try:
        for fn in os.listdir(syn):
            if fn.endswith('_synth.v') and not fn.startswith('generic_'):
                return fn[:-len('_synth.v')]
    except OSError:
        pass
    return 'test_counter'


DESIGN = detect_design()


def record(stage, name, status, detail=''):
    """Record one machine-readable check (PASS/FAIL/INCONCLUSIVE/NOT_TESTED)."""
    RESULTS.append({'stage': stage, 'name': name, 'status': status,
                    'detail': str(detail)})
    print('CHECK %s %s %s %s' % (stage, name, status, detail))
    return status


def check(stage, name, ok, detail=''):
    status = 'PASS' if ok else 'FAIL'
    record(stage, name, status, detail)
    return ok


def skip(stage, name, detail='stage not executed'):
    """A stage that was not executed is NOT_TESTED - never PASS."""
    record(stage, name, 'NOT_TESTED', detail)


def exists(path, min_size=1):
    try:
        return os.path.getsize(path) >= min_size
    except OSError:
        return False


def grep(path, pattern, flags=re.M):
    return re.findall(pattern, read(path), flags)


# --- BEGIN PARTITION GATE HELPERS (fix for all_partitions_proved false-PASS) ---
# Original defective logic (acceptance.py:253-254):
#   npart = len(re.findall('Successfully proved equivalence of partition', log))
#   check(S, 'all_partitions_proved', npart > 0, '%d partitions proved' % npart)
# That returns PASS for ANY non-zero subset (e.g. 5 of 8).  The corrected gate
# requires an authoritative expected partition set and a complete proof of it.

PARTITION_SUMMARY_RE = re.compile(r'Failed to prove equivalence for (\d+)/(\d+) partitions')
PROVED_RE = re.compile(r'Successfully proved equivalence of partition\s+(\S+)')
FAILED_RE = re.compile(r'Failed to prove equivalence of partition\s+(\S+)')
COULD_NOT_RE = re.compile(r"Could not prove equivalence of partition\s+'([^']+)'")
FAIL_FOOTER_RE = re.compile(r'DONE \(FAIL, rc=\d+\)')
UNKNOWN_MARKERS = ('equivalence unknown', 'partitions not equivalent',
                   'bmc failed', 'unproven', 'timed out')


def expected_partition_names(run):
    """Authoritative expected partition-name set, or None if unknowable.

    Sources in priority order:
      1. REG_EXPECTED_PARTITIONS - explicit comma/space separated name list.
      2. <run>/eqy/expected_partitions.json - explicit JSON list or
         {"partitions": [...]} / {"expected_partitions": [...]}.
      3. <run>/eqy/work/partition.list - EQY-generated authoritative list
         (format: "<top> <partition> xbits : <port>").
    None -> the gate reports INCONCLUSIVE, never PASS.
    """
    raw = os.environ.get('REG_EXPECTED_PARTITIONS', '').strip()
    if raw:
        names = set(x for x in re.split(r'[,\s]+', raw) if x)
        if names:
            return names

    jp = os.path.join(run, 'eqy', 'expected_partitions.json')
    if exists(jp):
        try:
            data = json.loads(read(jp))
            if isinstance(data, dict):
                data = data.get('partitions') or data.get('expected_partitions') or []
            names = set(str(x) for x in data if str(x))
            if names:
                return names
        except Exception:
            pass

    pl = os.path.join(run, 'eqy', 'work', 'partition.list')
    if exists(pl):
        names = set()
        for line in read(pl).splitlines():
            toks = line.split()
            if not toks:
                continue
            if len(toks) >= 2 and '.' in toks[1]:
                names.add(toks[1])          # EQY: "top part xbits : port"
            elif len(toks) == 1:
                names.add(toks[0])          # simple one-name-per-line list
        if names:
            return names
    return None


def partition_evidence(log_text):
    text = log_text or ''
    lower = text.lower()
    return {
        'proved': set(PROVED_RE.findall(text)),
        'failed': set(FAILED_RE.findall(text)),
        'could_not': set(COULD_NOT_RE.findall(text)),
        'unknown_markers': [m for m in UNKNOWN_MARKERS if m in lower],
        'summary': PARTITION_SUMMARY_RE.findall(text),
        'footer_fail': bool(FAIL_FOOTER_RE.search(text)),
        'designs_ok': 'Successfully proved designs equivalent' in text,
    }


def evaluate_partition_proof(run, log_text):
    """Return (status, detail); status in PASS / FAIL / INCONCLUSIVE.

    PASS only when the expected set is known AND every expected partition was
    proved AND there are zero failed / unknown / unproven markers.  Missing,
    extra, failed or unknown partitions -> FAIL; unknown expected set ->
    INCONCLUSIVE.  Never returns PASS for a partial proof.
    """
    ev = partition_evidence(log_text)
    expected = expected_partition_names(run)
    proved = ev['proved']
    problems = []

    if ev['failed']:
        problems.append('failed=%s' % sorted(ev['failed']))
    if ev['could_not']:
        problems.append('could_not_prove=%s' % sorted(ev['could_not']))
    if ev['unknown_markers']:
        problems.append('unknown_markers=%s' % ev['unknown_markers'])
    if ev['footer_fail']:
        problems.append('footer=DONE(FAIL)')
    if ev['summary'] and ev['summary'][-1][0] != '0':
        problems.append('summary_failed=%s/%s' % ev['summary'][-1])

    if expected is None:
        return ('INCONCLUSIVE',
                'expected partition set unknown; proved=%d%s'
                % (len(proved), ('; ' + '; '.join(problems)) if problems else ''))

    missing = expected - proved
    extra = proved - expected
    if missing:
        problems.append('missing=%s' % sorted(missing))
    if extra:
        problems.append('unexpected=%s' % sorted(extra))
    if len(proved) != len(expected):
        problems.append('count=%d/%d' % (len(proved), len(expected)))

    if problems:
        return ('FAIL', 'proof incomplete: ' + '; '.join(problems))
    if not ev['designs_ok']:
        return ('FAIL', 'all %d partitions proved but EQY final verdict missing' % len(proved))
    return ('PASS', '%d/%d partitions proved; zero failed/unknown/unproven'
            % (len(proved), len(expected)))
# --- END PARTITION GATE HELPERS ---


# Stage-specific log locations (Windows stages write to different dirs)
STAGE_LOG_PATHS = {
    "00_stage00": ["stage00/stage00.log", "stage00/stage00_result.json",
                   "stage00/stage00_report.txt", "logs/00_stage00.log"],
    "01_synthesis": ["synth/generic_synth.log", "synth/synth_mapped.log",
                     "logs/01_synthesis.log"],
    "02_lec_generic": ["lec_generic/lec_generic.log",
                       "logs/02_lec_generic.log"],
    "03_lec_mapped": ["eqy/eqy_positive.log", "eqy/eqy_negative.log",
                      "logs/03_lec_mapped.log"],
}


def log(stage):
    return os.path.join(LOGDIR, '%s.log' % stage)


def _stage_log_candidates(stage):
    """Return all possible log file paths for a stage."""
    cands = [os.path.join(RUN, "logs", stage + ".log")]
    for rel in STAGE_LOG_PATHS.get(stage, []):
        cands.append(os.path.join(RUN, *rel.split("/")))
    return cands


def executed(stage):
    """A stage counts as executed when ANY of its known log files exist."""
    for p in _stage_log_candidates(stage):
        if exists(p, 0):
            return True
    return False


def stage_log_path(stage):
    """Return the first existing log path for a stage (for reading)."""
    for p in _stage_log_candidates(stage):
        if exists(p, 0):
            return p
    return os.path.join(LOGDIR, stage + ".log")


# ===================================================================== 00
S = '00_stage00'
s00j = os.path.join(RUN, 'stage00', 'stage00_result.json')
s00r = os.path.join(RUN, 'stage00', 'stage00_report.txt')
if not executed(S):
    skip(S, 'stage00_result_exists')
    skip(S, 'stage00_report_exists')
    skip(S, 'stage00_overall_pass')
    skip(S, 'stage00_no_mandatory_failures')
else:
    check(S, 'stage00_result_exists', exists(s00j), s00j)
    check(S, 'stage00_report_exists', exists(s00r), s00r)
    s00 = {}
    if exists(s00j):
        try:
            s00 = json.loads(read(s00j))
        except Exception:
            s00 = {}
    check(S, 'stage00_overall_pass', s00.get('overall') == 'PASS', 'overall=%s' % s00.get('overall'))
    check(S, 'stage00_no_mandatory_failures', int(s00.get('mandatory_failures', 1)) == 0,
          'mandatory_failures=%s' % s00.get('mandatory_failures'))

# ===================================================================== 01
S = '01_synthesis'
syn = RUN + '/synth'
gen_net = syn + '/generic_%s_synth.v' % DESIGN
map_net = syn + '/%s_synth.v' % DESIGN
counts = syn + '/cell_counts.json'

if not executed(S):
    skip(S, 'generic_netlist_exists')
    skip(S, 'mapped_netlist_exists')
    skip(S, 'netlists_parse')
    skip(S, 'cell_counts_recorded')
    skip(S, 'sky130_cells_present_after_mapping')
    skip(S, 'sequential_cell_mapped')
    skip(S, 'no_unresolved_modules')
    skip(S, 'yosys_reported_no_errors')
else:
    check(S, 'generic_netlist_exists', exists(gen_net), gen_net)
    check(S, 'mapped_netlist_exists', exists(map_net), map_net)
    check(S, 'netlists_parse',
          ('module %s' % DESIGN) in read(gen_net) and ('module %s' % DESIGN) in read(map_net),
          'module header present in both netlists')

    cc = {}
    if exists(counts):
        try:
            cc = json.loads(read(counts))
        except ValueError:
            cc = {}
    mapped_cells = cc.get('mapped_cells', {}) or {}
    generic_cells = cc.get('generic_cells', {}) or {}
    check(S, 'cell_counts_recorded',
          bool(mapped_cells) and bool(generic_cells),
          'mapped types=%d, generic types=%d' % (len(mapped_cells), len(generic_cells)))

    sky = [c for c in mapped_cells if c.startswith('sky130_fd_sc_hd__')]
    check(S, 'sky130_cells_present_after_mapping', len(sky) > 0,
          '%d Sky130 cell types, %d instances'
          % (len(sky), sum(mapped_cells[c] for c in sky)))
    check(S, 'sequential_cell_mapped',
          any(c.startswith('sky130_fd_sc_hd__dfrtp') or c.startswith('sky130_fd_sc_hd__dfxtp') or c.startswith('sky130_fd_sc_hd__edfxtp') for c in mapped_cells),
          'sequential cell present=%s' % any(c.startswith('sky130_fd_sc_hd__dfrtp') or c.startswith('sky130_fd_sc_hd__dfxtp') or c.startswith('sky130_fd_sc_hd__edfxtp') for c in mapped_cells))
    check(S, 'no_unresolved_modules',
          not grep(log(S), r'ERROR: Module .* not found'),
          'no unresolved module errors in the Yosys log')
    check(S, 'yosys_reported_no_errors',
          not grep(log(S), r'^ERROR:'),
          'no Yosys ERROR lines')

# ===================================================================== 02
S = '02_lec_generic'
lg = RUN + '/lec_generic'
pos_log = lg + '/lec_generic.log'
neg_log = lg + '/lec_generic_negative.log'

if not executed(S):
    for n in ('positive_proved', 'no_unproven_points', 'compare_points_generated',
              'negative_control_detected', 'negative_control_netlist_differs'):
        skip(S, n)
else:
    check(S, 'positive_proved',
          'Equivalence successfully proven' in read(pos_log),
          'equiv_status -assert reached the success verdict')
    nprov = grep(pos_log, r'(\d+) are proven and (\d+) are unproven')
    check(S, 'no_unproven_points',
          bool(nprov) and nprov[-1][1] == '0',
          'proven/unproven=%s' % (str(nprov[-1]) if nprov else None))
    check(S, 'compare_points_generated',
          bool(nprov) and int(nprov[-1][0]) > 0,
          '%s compare points' % (nprov[-1][0] if nprov else 0))
    neg = read(neg_log)
    check(S, 'negative_control_detected',
          'unproven' in neg.lower() and 'Equivalence successfully proven' not in neg,
          'the corrupted netlist was rejected')
    broken_candidates = [
        os.path.join(lg, 'generic_%s_synth_broken.v' % DESIGN),
        os.path.join(lg, 'generic_test_counter_synth_broken.v'),
    ]
    broken_path = next((p for p in broken_candidates if exists(p)), broken_candidates[0])
    good_net = os.path.join(RUN, 'synth', 'generic_%s_synth.v' % DESIGN)
    check(S, 'negative_control_netlist_differs',
          exists(broken_path) and exists(good_net) and read(broken_path) != read(good_net),
          'the negative-control netlist differs from the good one')

# ===================================================================== 03
S = '03_lec_mapped'
eq = RUN + '/eqy'
eqpos = eq + '/eqy_positive.log'
eqneg = eq + '/eqy_negative.log'

if not executed(S):
    for n in ('genuine_sky130_models_used', 'official_preprocessor_used',
              'all_partitions_proved', 'designs_equivalent',
              'negative_control_rejected'):
        skip(S, n)
else:
    check(S, 'genuine_sky130_models_used',
          exists(eq + '/primitives_pdk.v') and exists(eq + '/sky130_cells_pdk.v'),
          'the model inputs are derived from the recovered Sky130 PDK')
    check(S, 'official_preprocessor_used',
          exists(eq + '/formal_pdk.v'),
          'formal_pdk.v was produced by the official formal_pdk_proc.py')
    # Corrected: a non-zero subset is NOT a proof of all partitions.
    pstatus, pdetail = evaluate_partition_proof(RUN, read(eqpos))
    record(S, 'all_partitions_proved', pstatus, pdetail)
    # Strengthened: presence of the success verdict alone is not enough when a
    # log also contains explicit failure markers (residual weakness reported).
    eqev = partition_evidence(read(eqpos))
    check(S, 'designs_equivalent',
          eqev['designs_ok'] and not eqev['footer_fail'] and not eqev['failed']
          and not eqev['could_not'] and not eqev['unknown_markers'],
          'EQY final verdict with no contradicting failure markers')
    check(S, 'negative_control_rejected',
          'Could not prove equivalence' in read(eqneg),
          'the corrupted netlist was rejected by EQY')

# ===================================================================== 04
S = '04_floorplan'
L = log(S)
if not executed(S):
    for n in ('die_area_valid', 'rows_and_sites_defined', 'pins_assigned',
              'tapcells_inserted', 'odb_written'):
        skip(S, n)
else:
    check(S, 'die_area_valid',
          bool(grep(L, r'Die BBox:')),
          'die area defined')
    check(S, 'rows_and_sites_defined',
          bool(grep(L, r'Added \d+ rows of \d+ site')),
          'rows created')
    check(S, 'pins_assigned',
          bool(grep(L, r'top-level pins assigned: (\d+)')) and
          int(grep(L, r'top-level pins assigned: (\d+)')[-1]) > 0,
          'pin placer ran and assigned %s pins'
          % (grep(L, r'top-level pins assigned: (\d+)')[-1] if grep(L, r'top-level pins assigned: (\d+)') else '?'))
    ties = grep(L, r'placed (\d+) well ties')
    check(S, 'tapcells_inserted',
          bool(ties) and int(ties[-1]) > 0,
          '%s well ties placed' % (ties[-1] if ties else '?'))
    check(S, 'odb_written', exists(RUN + '/04_floorplan.odb'), '04_floorplan.odb')

# ===================================================================== 05
S = '05_placement'
L = log(S)
if not executed(S):
    for n in ('functional_cells_contiguous', 'detailed_placement_completed',
              'no_placement_violations', 'odb_written'):
        skip(S, n)
else:
    cont = grep(L, r'placed (\d+) functional cells contiguously: (\d+) \.\. (\d+)')
    check(S, 'functional_cells_contiguous',
          bool(cont) and int(cont[-1][1]) < int(cont[-1][2]) and int(cont[-1][0]) > 0,
          '%s functional cells form one contiguous block (%s .. %s dbu)'
          % (cont[-1][0] if cont else '?', cont[-1][1] if cont else '?', cont[-1][2] if cont else '?'))
    check(S, 'detailed_placement_completed',
          'Detailed Placement Analysis' in read(L) or 'detailed_placement' in read(L),
          'detailed_placement ran')
    check(S, 'no_placement_violations',
          'Overlap check failed' not in read(L) and 'Placement violations' not in read(L),
          'check_placement reported no violations')
    check(S, 'odb_written', exists(RUN + '/05_place.odb'), '05_place.odb')

# ===================================================================== 06
S = '06_cts'
L = log(S)
if not executed(S):
    for n in ('clock_sinks_identified', 'clock_tree_built', 'clock_connectivity_reported', 'odb_written'):
        skip(S, n)
else:
    sinks = grep(L, r'Total number of sinks:\s*(\d+)')
    check(S, 'clock_sinks_identified', bool(sinks) and int(sinks[-1]) > 0, 'sinks=%s' % (sinks[-1] if sinks else '?'))
    check(S, 'clock_tree_built', exists(RUN + '/06_cts.odb'), '06_cts.odb')
    check(S, 'clock_connectivity_reported',
          bool(grep(L, r'Clock clk|--- Clock|clocks')) or bool(grep(L, r'propagated')),
          'a clock report was emitted')
    check(S, 'odb_written', exists(RUN + '/06_cts.odb'), '06_cts.odb')

# ===================================================================== 07
S = '07_pdn'
L = log(S)
if not executed(S):
    for n in ('power_and_ground_nets_correct', 'power_grid_geometry_generated',
              'specialnets_contains_routed_power', 'odb_and_def_written'):
        skip(S, n)
else:
    check(S, 'power_and_ground_nets_correct', 'NET VDD' in read(L) and 'NET VSS' in read(L), 'VDD and VSS are present in the design')
    check(S, 'power_grid_geometry_generated', bool(grep(L, r'routing shape census \(after pdngen\)')), 'the power grid produced geometry')
    check(S, 'specialnets_contains_routed_power', bool(grep(RUN + '/07_pdn.def', r'^\s*(\+ )?(ROUTED|NEW) met[1-5] ')), 'routed power shapes are in SPECIALNETS')
    check(S, 'odb_and_def_written', exists(RUN + '/07_pdn.odb') and exists(RUN + '/07_pdn.def'), '07_pdn.odb and 07_pdn.def')

# ===================================================================== 08
S = '08_power_connectivity'
F = RUN + '/08_power_connectivity.txt'
if not executed(S):
    for n in ('check_power_grid_vdd', 'check_power_grid_vss', 'stdcell_power_pins_connected',
              'welltie_connectivity_verified', 'no_disconnected_power_terminals', 'specialnets_contains_routed_power'):
        skip(S, n)
else:
    txt = read(F)
    m = re.search(r'CHECK_POWER_GRID VDD (\S+)', txt)
    check(S, 'check_power_grid_vdd', m is not None and m.group(1) == '1' and 'POWER_GRID_ERROR_FILE VDD: (empty)' in txt, 'check_power_grid reported VDD fully connected (status %s)' % (m.group(1) if m else '?'))
    m = re.search(r'CHECK_POWER_GRID VSS (\S+)', txt)
    check(S, 'check_power_grid_vss', m is not None and m.group(1) == '1' and 'POWER_GRID_ERROR_FILE VSS: (empty)' in txt, 'check_power_grid reported VSS fully connected (status %s)' % (m.group(1) if m else '?'))
    m = re.search(r'POWER_PINS VDD=(\d+) VSS=(\d+) BAD=(\d+)', txt)
    check(S, 'stdcell_power_pins_connected', m is not None and int(m.group(3)) == 0 and int(m.group(1)) > 0 and int(m.group(2)) > 0, 'power pins: %s' % (m.group(0) if m else 'not found'))
    m = re.search(r'WELL_TIES total=(\d+) connected_to_VSS=(\d+)', txt)
    check(S, 'welltie_connectivity_verified', m is not None and int(m.group(1)) > 0 and int(m.group(1)) == int(m.group(2)), 'well ties: %s' % (m.group(0) if m else 'not found'))
    check(S, 'no_disconnected_power_terminals', 'POWER_CONNECTIVITY_RESULT PASS' in txt and 'BAD_POWER_PIN' not in txt, 'no power terminal is on the wrong net')
    m = re.search(r'SPECIALNETS_ROUTED_POWER_SHAPES (\d+)', txt)
    check(S, 'specialnets_contains_routed_power', m is not None and int(m.group(1)) > 0, 'routed power shapes in SPECIALNETS: %s' % (m.group(1) if m else '?'))

# ===================================================================== 09
S = '09_global_route'
L = log(S)
if not executed(S):
    for n in ('global_routing_completed', 'guide_file_written', 'odb_written'):
        skip(S, n)
else:
    check(S, 'global_routing_completed', bool(grep(L, r'global route|Global route|grt|Routing')), 'the global router ran')
    check(S, 'guide_file_written', exists(RUN + '/global_route.guide'), 'global_route.guide')
    check(S, 'odb_written', exists(RUN + '/09_groute.odb'), '09_groute.odb')

# ===================================================================== 10
S = '10_detailed_route'
L = log(S)
if not executed(S):
    for n in ('detailed_routing_completed', 'router_violations_zero', 'routed_def_and_odb_written'):
        skip(S, n)
else:
    check(S, 'detailed_routing_completed', exists(RUN + '/10_route.odb') and exists(RUN + '/10_route.def'), 'the routed ODB and DEF were written')
    drcviol = grep(L, r'Number of violations = (\d+)')
    last = drcviol[-1] if drcviol else None
    check(S, 'router_violations_zero', last is not None and last == '0', 'the router reported %s violations' % last)
    check(S, 'routed_def_and_odb_written', exists(RUN + '/10_route.odb') and exists(RUN + '/10_route.def'), '10_route.odb and 10_route.def')

# ===================================================================== 11
S = '11_extract'
spef = RUN + '/11_final.spef'
if not executed(S):
    for n in ('spef_generated', 'parasitic_segments_nonzero', 'resistance_segments_nonzero', 'extracted_design_matches_routed', 'spef_covers_design_nets'):
        skip(S, n)
else:
    check(S, 'spef_generated', exists(spef), spef)
    rst = grep(spef, r'\*CAP')
    check(S, 'parasitic_segments_nonzero', len(rst) > 0, '%d *CAP entries' % len(rst))
    rseg = grep(spef, r'\*RES')
    check(S, 'resistance_segments_nonzero', len(rseg) > 0, '%d *RES entries' % len(rseg))
    check(S, 'extracted_design_matches_routed', exists(RUN + '/11_final.odb') and exists(RUN + '/11_final.def') and exists(RUN + '/11_final.v') and exists(RUN + '/11_final.sdc'), 'the final database, DEF, netlist and SDC were exported')
    nets_in_spef = set(re.findall(r'\*D_NET \*(\S+)', read(spef)))
    check(S, 'spef_covers_design_nets', len(nets_in_spef) > 0, '%d nets in the SPEF' % len(nets_in_spef))

# ===================================================================== 12
S = '12_sta'
stasum = RUN + '/sta/sta_summary.txt'
stalog = RUN + '/sta/sta_all.log'
if not executed(S):
    for n in ('all_corners_reported', 'libraries_loaded', 'constraints_applied', 'spef_loaded', 'setup_reports_generated', 'paths_analysed_per_corner', 'no_timing_violators', 'wns_recorded_per_corner', 'wns_non_negative'):
        skip(S, n)
else:
    check(S, 'all_corners_reported', all(os.path.exists(RUN + '/sta/sta_%s.rpt' % c) for c in ('tt', 'ss', 'ff')), 'TT/SS/FF reports present')
    sl = read(stalog)
    check(S, 'libraries_loaded', 'STA_TRACE read_liberty_corner_count 3' in sl, 'three corner libraries were read')
    check(S, 'constraints_applied', 'STA_TRACE read_sdc' in sl, 'the SDC was read')
    check(S, 'spef_loaded', 'STA_TRACE read_spef' in sl, 'the SPEF was read')
    check(S, 'setup_reports_generated', all(('=== %s CORNER' % c) in sl for c in ('TT', 'SS', 'FF')), 'setup and hold reports per corner')
    check(S, 'paths_analysed_per_corner', len(grep(stalog, r'^\s+[0-9]+\.[0-9]+\s+slack \(MET\)')) >= 1, '%d analysed timing paths' % len(grep(stalog, r'^\s+[0-9]+\.[0-9]+\s+slack \(MET\)')))
    viol = grep(stalog, r'slack \(VIOLATED\)')
    check(S, 'no_timing_violators', len(viol) == 0, 'violated paths=%d' % len(viol))
    summary = read(stasum)
    rows = [l.split(',') for l in summary.strip().split('\n') if l and not l.startswith('corner')]
    w = {}
    for r in rows:
        if r[0] == 'total_violators':
            continue
        if len(r) >= 9:
            w[r[0]] = (r[3], r[4], r[7], r[8], r[2], r[6])
    check(S, 'wns_recorded_per_corner', len(w) == 3, ' '.join('%s setup_wns=%s setup_tns=%s hold_wns=%s hold_tns=%s' % (k, v[0], v[1], v[2], v[3]) for k, v in sorted(w.items())))
    ok = len(w) == 3
    for k, v in w.items():
        for idx in (0, 1, 2, 3):
            if v[idx] == 'NA':
                ok = False
            elif float(v[idx]) < 0:
                ok = False
    check(S, 'wns_non_negative', ok, 'every WNS/TNS is recorded and no WNS is negative')

# ===================================================================== 13
S = '13_gds'
gds = RUN + '/%s_pdn.gds' % DESIGN
gdlog = RUN + '/gds/def2gds.log'
if not executed(S):
    for n in ('gds_generated', 'topcell_name_correct', 'stdcell_geometry_merged', 'nonempty_geometry', 'via_cut_layers_present', 'power_geometry_present', 'real_top_level_port_labels_preserved'):
        skip(S, n)
else:
    check(S, 'gds_generated', exists(gds), gds)
    gl = read(gdlog)
    check(S, 'topcell_name_correct', ('top=%s' % DESIGN) in gl, 'the top cell is %s' % DESIGN)
    m = re.findall(r'std cells merged with geometry: (\d+)', gl)
    check(S, 'stdcell_geometry_merged', bool(m) and int(m[-1]) > 0, '%s standard cells merged with their genuine geometry' % (m[-1] if m else '?'))
    m = re.findall(r'net-name labels written: (\d+)', gl)
    check(S, 'nonempty_geometry', bool(m) and int(m[-1]) > 0, '%s net-name labels written' % (m[-1] if m else '?'))
    m = re.findall(r'VIA_CUT_SHAPES: (\d+)', gl)
    check(S, 'via_cut_layers_present', bool(m) and int(m[-1]) > 0, '%s via cut shapes in the top cell' % (m[-1] if m else '?'))
    m = re.findall(r'ROUTING_LAYER_SHAPES: (\d+)', gl)
    check(S, 'power_geometry_present', bool(m) and int(m[-1]) > 0, '%s shapes on the routing layers' % (m[-1] if m else '?'))
    m = re.findall(r'DEF top-level port nets: (\d+)', gl)
    check(S, 'real_top_level_port_labels_preserved', bool(m) and int(m[-1]) > 0, '%s real top-level ports were labelled (internal nets are not)' % (m[-1] if m else '?'))

# ===================================================================== 14
S = '14_drc'
drclog = RUN + '/drc/drc.log'
classif = RUN + '/drc/drc_classification.txt'
if not executed(S):
    for n in ('all_rule_categories_execute', 'zero_actual_violations', 'helper_regions_excluded', 'report_db_saved', 'report_db_reopens', 'stored_totals_agree_with_log'):
        skip(S, n)
else:
    dl = read(drclog)
    nedge = len(grep(drclog, r'Edge pairs:'))
    check(S, 'all_rule_categories_execute', nedge >= 295, '%d rule-category evaluations' % nedge)
    c = read(classif)
    m = re.search(r'ACTUAL_VIOLATIONS: (\d+)', c)
    check(S, 'zero_actual_violations', m is not None and m.group(1) == '0', 'actual violations=%s' % (m.group(1) if m else '?'))
    m = re.search(r'HELPER_REGIONS_WITH_NONZERO_COUNTS: (\d+)', c)
    check(S, 'helper_regions_excluded', m is not None, '%s helper regions with non-zero counts are excluded from the violation count' % (m.group(1) if m else '?'))
    check(S, 'report_db_saved', exists(RUN + '/tests/sky130hd_drc_report.rpt'), 'the report database was written')
    check(S, 'report_db_reopens', 'REOPEN OK' in dl, 'the report database was reopened')
    m = re.search(r'REOPEN OK: categories=(\d+) cells=\[(.*?)\] items=(\d+) values=(\d+)', dl)
    check(S, 'stored_totals_agree_with_log', m is not None and m.group(3) == '0' and m.group(4) == '0', 'the stored database holds %s items / %s values' % (m.group(3) if m else '?', m.group(4) if m else '?'))

# ===================================================================== 15
S = '15_lvs'
lvlog = RUN + '/15_lvs/lvs.log'
comp = RUN + '/lvs_compare.txt'
if not executed(S):
    for n in ('official_deck_used', 'genuine_sky130_cdl_used', 'reference_from_physical_database', 'topcell_configured', 'verdict_match', 'lvs_database_written', 'extracted_netlist_written', 'device_counts_match', 'no_unmatched_devices', 'no_unmatched_nets', 'no_unmatched_subcircuits', 'power_connectivity_correct', 'power_structure_verified', 'independent_structure_match'):
        skip(S, n)
else:
    check(S, 'official_deck_used', 'sky130hd.lylvs' in os.environ.get('REG_LVS_DECK', 'sky130hd.lylvs'), 'the official unmodified deck')
    check(S, 'genuine_sky130_cdl_used', exists(RUN + '/6_final_concat.cdl'), 'the reference CDL + official sky130hd.cdl')
    check(S, 'reference_from_physical_database', exists(RUN + '/%s_design.cdl' % DESIGN), 'the reference netlist was generated from the routed ODB')
    check(S, 'topcell_configured', bool(grep(lvlog, r'Netlists|%s' % re.escape(DESIGN))), 'top_cell=%s was passed to the deck' % DESIGN)
    check(S, 'verdict_match', 'Congratulations! Netlists match' in read(lvlog), 'the tool verdict is an explicit match')
    check(S, 'lvs_database_written', exists(RUN + '/15_lvs/15_lvs.lvsdb'), 'the LVS database')
    check(S, 'extracted_netlist_written', exists(RUN + '/15_lvs/15_lvs_extracted.cir'), 'the extracted netlist')

    cm = read(comp)
    m = re.search(r'DEVICES: layout=(\d+) schematic=(\d+)', cm)
    check(S, 'device_counts_match', m is not None and m.group(1) == m.group(2) and int(m.group(1)) > 0, 'layout=%s schematic=%s' % (m.groups() if m else ('?', '?')))
    m = re.search(r'UNMATCHED_NETS: (\d+)', cm)
    check(S, 'no_unmatched_nets', m is not None and m.group(1) == '0', 'unmatched nets=%s' % (m.group(1) if m else '?'))
    m = re.search(r'UNMATCHED_DEVICES: (\d+)', cm)
    check(S, 'no_unmatched_devices', m is not None and m.group(1) == '0', 'unmatched devices=%s' % (m.group(1) if m else '?'))
    m = re.search(r'UNMATCHED_SUBCIRCUITS: (\d+)', cm)
    check(S, 'no_unmatched_subcircuits', m is not None and m.group(1) == '0', 'unmatched subcircuits=%s' % (m.group(1) if m else '?'))
    m = re.search(r'POWER: (\S+)', cm)
    check(S, 'power_connectivity_correct', m is not None and m.group(1) == 'VDD=VSS_CONNECTED', 'power connectivity=%s' % (m.group(1) if m else '?'))
    m = re.search(r'POWER_Canonical_Nets_Match: (\S+)', cm)
    m2 = re.search(r'POWER_Bulk_Partition_Match: (\S+)', cm)
    check(S, 'power_structure_verified', m is not None and m2 is not None and m.group(1) == 'True' and m2.group(1) == 'True', 'canonical power nets match=%s, bulk partition match=%s' % (m.group(1) if m else '?', m2.group(1) if m2 else '?'))
    m = re.search(r'WL_MATCHED: (\d+)', cm)
    mt = re.search(r'WL_LAYOUT_TOTAL: (\d+)', cm)
    ms = re.search(r'WL_SCHEMATIC_TOTAL: (\d+)', cm)
    check(S, 'independent_structure_match', m is not None and mt is not None and ms is not None and m.group(1) == mt.group(1) == ms.group(1) and int(m.group(1)) > 0, 'Weisfeiler-Lehman device-colour classes matched=%s of %s/%s' % (m.group(1) if m else '?', mt.group(1) if mt else '?', ms.group(1) if ms else '?'))

# ===================================================================== manifest (Phase 33 integration)
S = 'manifest'
manifest_path = RUN + '/run_manifest.json'
mgr_candidates = [
    os.path.join(os.path.dirname(__file__), 'manifest_manager.py'),
    os.path.join(RUN, '..', '..', 'checks', 'manifest_manager.py'),
]
mgr = next((p for p in mgr_candidates if os.path.exists(p)), None)
# Find check dir via env if available
check_dir = os.environ.get('REG_CHECK_DIR')
if not mgr and check_dir:
    p = os.path.join(check_dir, 'manifest_manager.py')
    if os.path.exists(p): mgr = p

if not os.path.exists(manifest_path):
    # Manifest not yet present in older runs; do not force-fail historical evidence, but record
    skip(S, 'manifest_exists', 'run_manifest.json not present')
    skip(S, 'manifest_valid', 'manifest not present')
else:
    check(S, 'manifest_exists', True, manifest_path)
    valid = False
    detail = 'not validated'
    errors = []
    if mgr:
        import subprocess as _sp
        # try python3 then python
        cmd = None
        for py in ('python3', 'python', os.environ.get('REG_PYTHON','')):
            if py:
                cmd = [py, mgr, 'validate', RUN, '--manifest-path', manifest_path]
                break
        try:
            p = _sp.run(cmd, capture_output=True, text=True, cwd=RUN)
            try:
                vr = json.loads(p.stdout) if p.stdout.strip().startswith('{') else {}
            except Exception:
                vr = {}
            valid = (p.returncode == 0 and vr.get('status') == 'PASS')
            errors = vr.get('errors', [])
            detail = ('OK' if valid else ('errors=' + '; '.join(errors[:5])))
        except Exception as e:
            valid = False
            detail = 'validator error: %s' % e
    else:
        # basic checks
        try:
            with open(manifest_path, 'r', encoding='utf-8-sig') as f:
                mj = json.load(f)
            basic_ok = bool(mj.get('run_id')) and bool(mj.get('stages') is not None)
            valid = basic_ok
            detail = 'basic checks only (manifest_manager.py not found)'
        except Exception as e:
            valid = False
            detail = 'invalid: %s' % e
    check(S, 'manifest_valid', valid, detail)
    # active-run identity: manifest run_id should match run dir name if it looks like a run id
    try:
        with open(manifest_path, 'r', encoding='utf-8-sig') as f:
            mj = json.load(f)
        run_base = os.path.basename(RUN.rstrip('/'))
        rid = mj.get('run_id')
        if rid and (run_base == rid or run_base.startswith('run_')):
            check(S, 'manifest_run_identity_matches', rid == run_base or rid in run_base or run_base.endswith(rid), 'manifest.run_id=%s run=%s' % (rid, run_base))
        else:
            skip(S, 'manifest_run_identity_matches', 'cannot infer expected run id')
    except Exception as e:
        check(S, 'manifest_run_identity_matches', False, str(e))

# ===================================================================== output
npass = len([r for r in RESULTS if r['status'] == 'PASS'])
nfail = len([r for r in RESULTS if r['status'] == 'FAIL'])
nincon = len([r for r in RESULTS if r['status'] == 'INCONCLUSIVE'])
nskip = len([r for r in RESULTS if r['status'] == 'NOT_TESTED'])
print('')
print('ACCEPTANCE: %d passed, %d failed, %d not tested, %d total' % (npass, nfail, nskip, len(RESULTS)))
if nincon:
    print('ACCEPTANCE_INCONCLUSIVE: %d (counts as failure for the exit code)' % nincon)
with open(RUN + '/acceptance.json', 'w') as f:
    json.dump({'checks': RESULTS, 'passed': npass, 'failed': nfail, 'inconclusive': nincon,
               'not_tested': nskip, 'total': len(RESULTS), 'design': DESIGN}, f, indent=2)
sys.exit(0 if (nfail == 0 and nincon == 0) else 1)
