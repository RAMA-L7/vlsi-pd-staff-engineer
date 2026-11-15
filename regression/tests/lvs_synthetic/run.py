#!/usr/bin/env python3
"""Portable synthetic LVS comparator regression tests (Phase 30-32/34)."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# comparator in regression/checks relative to regression/tests/lvs_synthetic
CMP = os.path.abspath(os.path.join(HERE, '..', '..', 'checks', 'lvs_compare.py'))
PY = sys.executable

CASES = [
    ("correct_equivalent", "lay_ok.cir", "expect_eq", True),
    ("renamed_internal_nets", "lay_renamed.cir", "expect_eq", True),
    ("pin_swap", "lay_pin_swap.cir", "expect_eq", False),
    ("net_swap_internal", "lay_net_swap.cir", "expect_eq", False),
    ("pin_name_mismatch", "lay_pinname.cir", "expect_pin_names", False),
    ("pin_order_mismatch", "lay_pinorder.cir", "expect_pin_order", False),
    ("pin_count_mismatch", "lay_pincount.cir", "expect_pin_count", False),
    ("missing_device", "lay_missing_dev.cir", "expect_eq", False),
    ("extra_device", "lay_extra_dev.cir", "expect_eq", False),
    ("device_type_mismatch", "lay_devtype.cir", "expect_eq", False),
    ("device_param_mismatch", "lay_param2.cir", "expect_eq", False),
    ("gate_source_swap", "lay_gs_swap.cir", "expect_eq", False),
    ("short", "lay_short2.cir", "expect_eq", False),
    ("open_disconnect", "lay_open.cir", "expect_eq", False),
    ("top_cell_mismatch", "lay_top_mismatch.cir", "expect_top_missing", True),
]


def main():
    ref = os.path.join(HERE, 'ref.cdl')
    results = []
    for name, lay_name, kind, exp in CASES:
        lay = os.path.join(HERE, lay_name)
        out_txt = os.path.join(HERE, name + '.out.txt')
        p = subprocess.run([PY, CMP, lay, ref, out_txt], capture_output=True, text=True)
        txt = open(out_txt, 'r', errors='replace').read() if os.path.exists(out_txt) else (p.stdout + p.stderr)
        r = {"case": name, "rc": p.returncode}
        for k in ["PIN_NAMES_MATCH", "PIN_ORDER_MATCH", "PIN_COUNT_MATCH", "TOPOLOGY_EQUIVALENT", "LVS_TOPOLOGY_EQUIVALENT", "UNMATCHED_DEVICES", "UNMATCHED_NETS", "UNMATCHED_SUBCIRCUITS"]:
            for line in txt.splitlines():
                if line.startswith(k + ":"):
                    r[k] = line.split(":", 1)[1].strip()
        r["top_missing"] = "TOP CIRCUIT MISSING" in txt
        ok = True
        notes = []
        if kind == "expect_eq":
            v = (r.get("TOPOLOGY_EQUIVALENT") == "True")
            if v != exp:
                ok = False
            notes.append(f"TOPOLOGY_EQUIVALENT={r.get('TOPOLOGY_EQUIVALENT')}")
        elif kind == "expect_pin_names":
            v = (r.get("PIN_NAMES_MATCH") == "True")
            if v != exp:
                ok = False
            notes.append(f"PIN_NAMES_MATCH={r.get('PIN_NAMES_MATCH')}")
        elif kind == "expect_pin_order":
            v = (r.get("PIN_ORDER_MATCH") == "True")
            if v != exp:
                ok = False
            notes.append(f"PIN_ORDER_MATCH={r.get('PIN_ORDER_MATCH')}")
        elif kind == "expect_pin_count":
            v = (r.get("PIN_COUNT_MATCH") == "True")
            if v != exp:
                ok = False
            notes.append(f"PIN_COUNT_MATCH={r.get('PIN_COUNT_MATCH')}")
        elif kind == "expect_top_missing":
            if r["top_missing"] != exp:
                ok = False
            notes.append(f"top_missing={r['top_missing']}")
        r["verdict"] = "PASS" if ok else "FAIL"
        r["notes"] = "; ".join(notes)
        results.append(r)
        print(r['case'], r['verdict'], r['notes'])
    summary = {"total": len(results), "pass": sum(1 for x in results if x['verdict'] == 'PASS'), "fail": sum(1 for x in results if x['verdict'] == 'FAIL'), "results": results}
    print(json.dumps({"total": summary["total"], "pass": summary["pass"], "fail": summary["fail"]}, indent=2))
    return 0 if summary['fail'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
