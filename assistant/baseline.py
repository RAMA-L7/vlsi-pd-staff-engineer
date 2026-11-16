"""One reproducible physical baseline (finding N).

``assistant/baseline.py`` is the single documented command that runs the
supported public design (``examples/test-counter``, top ``test_counter``)
through the repository's implemented 15-stage OpenROAD-based regression, with
full provenance capture:

* tool versions / flow / PDK / library / platform hashes (``assistant/tools``)
* design RTL + SDC hashes, configuration hash
* stage logs, checkpoints, mapped netlists, physical artifacts
* timing views and parasitic provenance actually produced
* physical-check results actually produced (never invented)

The regression itself is unchanged (it is the baseline under comparison);
this wrapper only *records* what ran.  Distinctions made explicit in the
output manifest:

* generic vs mapped synthesis (stage 01 produces BOTH netlists)
* placement / global-route estimates vs routed extraction (stage 11 SPEF)
* GDS generation vs verified physical correctness (stage 14 DRC, 15 LVS)
* academic checks (DRC deck / LVS) vs qualified signoff (NOT claimed)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict

from .evidence import utc_now
from .hashing import sha256_file, stable_json_hash
from .liberty import parse_liberty_pvt
from .tools import default_provenance

STAGE_NAME_RE = re.compile(r"^(\d{2})[_-](\w+)")


@dataclass
class StageRecord:
    stage: str
    status: str = "NOT_RUN"
    checks: list[dict] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)


@dataclass
class BaselineManifest:
    command: list[str]
    exit_code: int | None
    run_dir: str
    created_utc: str = field(default_factory=utc_now)
    design: str = "test_counter"
    provenance: dict = field(default_factory=dict)
    design_hashes: dict = field(default_factory=dict)
    config_hash: str = ""
    stages: list[dict] = field(default_factory=list)
    views: dict = field(default_factory=dict)
    physical_checks: dict = field(default_factory=dict)
    distinctions: dict = field(default_factory=dict)
    unsupported: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


#: What this baseline does NOT demonstrate (never claim these).
UNSUPPORTED_CAPABILITIES = [
    "IR drop analysis (no executable stage in this flow)",
    "EM (electromigration) analysis (no executable stage in this flow)",
    "Antenna violation checking (no executable stage in this flow)",
    "Post-metal-fill extraction and post-fill timing closure (not implemented)",
    "Qualified/signoff-grade parasitic extraction (academic OpenROAD "
    "extraction only; no QRC/StarRC)",
    "Multi-corner closure beyond the three locally available Sky130 "
    "Liberty files (tt/ss/ff as listed in assistant/pins.json)",
    "Tapeout readiness (never claimed)",
]

DISTINCTIONS = {
    "generic_vs_mapped_synthesis": (
        "stage 01 emits generic_${top}_synth.v (generic) AND "
        "mapped_${top}_synth.v (abc -liberty, technology-mapped); LEC/STA "
        "claims state which netlist they used"
    ),
    "estimate_vs_extraction": (
        "placement (stage 05) and global route (stage 09) report ESTIMATES; "
        "only stage 11 extraction produces routed parasitics (SPEF) used by "
        "stage 12 STA"
    ),
    "gds_vs_physical_correctness": (
        "stage 13 producing a GDS file is generation only; physical "
        "correctness claims rest on stage 14 DRC and stage 15 LVS results"
    ),
    "academic_vs_signoff": (
        "DRC (KLayout sky130hd deck) and LVS (netgen official deck) are "
        "academic/public checks; no qualified signoff is claimed"
    ),
}


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def collect_stage_results(run_dir: str) -> tuple[list[StageRecord], dict, dict]:
    """Parse the regression's own JSON report if present.

    Never fabricates results: stages without recorded results stay NOT_RUN.
    """
    stages: list[StageRecord] = []
    views: dict = {}
    physical: dict = {}

    report_path = None
    for name in sorted(os.listdir(run_dir)) if os.path.isdir(run_dir) else []:
        if name.endswith(".json") and "report" in name.lower():
            report_path = os.path.join(run_dir, name)
    if report_path is None:
        # fall back to any top-level JSON with a stages key
        if os.path.isdir(run_dir):
            for name in sorted(os.listdir(run_dir)):
                if not name.endswith(".json"):
                    continue
                p = os.path.join(run_dir, name)
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    continue
                if isinstance(data, dict) and "stages" in data:
                    report_path = p
                    break

    data: dict = {}
    if report_path:
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}

    raw_stages = data.get("stages") if isinstance(data.get("stages"), list) else []
    for st in raw_stages:
        if not isinstance(st, dict):
            continue
        rec = StageRecord(
            stage=str(st.get("stage", st.get("name", "?"))),
            status=str(st.get("status", "NOT_RUN")),
            checks=[
                c for c in (st.get("checks") or []) if isinstance(c, dict)
            ],
            artifacts=[
                a for a in (st.get("artifacts") or []) if isinstance(a, str)
            ],
        )
        stages.append(rec)
        # Named netlist views from the stage's own checks (authoritative:
        # stage 01 records mapped/generic netlist paths in check details).
        for chk in rec.checks:
            name = str(chk.get("name", ""))
            detail = str(chk.get("detail", ""))
            if name in ("mapped_netlist_exists", "generic_netlist_exists") and detail:
                key = "mapped_netlist" if "mapped" in name else "generic_netlist"
                views.setdefault(key, detail)
        # Physical-check results actually produced
        if "drc" in rec.stage.lower():
            physical["drc"] = {"status": rec.status, "stage": rec.stage}
        if "lvs" in rec.stage.lower():
            physical["lvs"] = {"status": rec.status, "stage": rec.stage}

    # Views: infer from artifacts actually present in the run dir.
    if os.path.isdir(run_dir):
        for dirpath, _dirs, files in os.walk(run_dir):
            for fn in files:
                low = fn.lower()
                if low.endswith(".spef") or low.endswith(".spef.gz"):
                    views["routed_extraction"] = os.path.join(dirpath, fn)
                if low.endswith(".def") and "route" in low:
                    views["routed_def"] = os.path.join(dirpath, fn)
                if low.endswith(".gds") or low.endswith(".gds.gz"):
                    views["gds"] = os.path.join(dirpath, fn)
                if "mapped" in low and low.endswith(".v"):
                    views["mapped_netlist"] = os.path.join(dirpath, fn)
                if low.startswith("generic_") and low.endswith(".v"):
                    views["generic_netlist"] = os.path.join(dirpath, fn)

    if not stages and report_path is None:
        notes_missing = "no regression JSON report found in run dir"
    return stages, views, physical


def run_baseline(
    repo_root: str | None = None,
    *,
    run_id: str = "",
    stages: list[str] | None = None,
    timeout_s: float = 10800.0,
    out_dir: str | None = None,
    design: str = "test_counter",
    dry_run: bool = False,
) -> BaselineManifest:
    """The one documented command: run + record the reproducible baseline."""
    root = repo_root or _repo_root()
    reg = os.path.join(root, "regression", "run_regression.ps1")
    if not os.path.isfile(reg):
        raise FileNotFoundError(f"regression entry point not found: {reg}")

    argv = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        reg,
        "-Design",
        design,
    ]
    if run_id:
        argv += ["-RunId", run_id]
    if stages:
        argv += ["-Stages", ",".join(stages)]

    out = out_dir or os.path.join(root, "regression", "evidence")
    os.makedirs(out, exist_ok=True)

    prov = default_provenance(root)
    design_hashes = {}
    for label, rel in (
        ("rtl", os.path.join("examples", "test-counter", "rtl", "test_counter.v")),
        ("sdc", os.path.join("examples", "test-counter", "rtl", "test_counter.sdc")),
        ("synthesis_targets", "synthesis_targets.json"),
        ("run_regression", os.path.join("regression", "run_regression.ps1")),
    ):
        p = os.path.join(root, rel)
        design_hashes[label] = sha256_file(p) if os.path.isfile(p) else "<missing>"

    manifest = BaselineManifest(
        command=argv,
        exit_code=None,
        run_dir="",
        design=design,
        provenance=prov.to_dict(),
        design_hashes=design_hashes,
        config_hash=stable_json_hash(
            {"design": design, "stages": stages or "all", "repo": root}
        ),
        unsupported=list(UNSUPPORTED_CAPABILITIES),
        distinctions=dict(DISTINCTIONS),
    )

    if dry_run:
        manifest.notes.append("dry run: command not executed")
        manifest.exit_code = None
        return manifest

    log_path = os.path.join(out, f"baseline_{run_id or 'run'}.log")
    with open(log_path, "wb") as logf:
        logf.write(f"$ {' '.join(argv)}\n".encode())
        logf.flush()
        try:
            proc = subprocess.run(
                argv,
                cwd=root,
                stdout=logf,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
                shell=False,
            )
            manifest.exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            manifest.exit_code = None
            manifest.notes.append(f"baseline timed out after {timeout_s}s")
        except OSError as exc:
            manifest.exit_code = None
            manifest.notes.append(f"failed to start regression: {exc}")

    # Locate the run directory the regression created (most recent run_*).
    ws = os.path.join(root, "regression", "workspace")
    run_dir = ""
    if os.path.isdir(ws):
        candidates = sorted(
            (
                os.path.join(ws, d)
                for d in os.listdir(ws)
                if d.startswith("run_")
            ),
            key=lambda p: os.path.getmtime(p),
            reverse=True,
        )
        if candidates:
            run_dir = candidates[0]
    manifest.run_dir = run_dir

    if run_dir:
        stages_rec, views, physical = collect_stage_results(run_dir)
        manifest.stages = [asdict(s) for s in stages_rec]
        manifest.views = {
            k: {"path": v, "sha256": sha256_file(v) if os.path.isfile(v) else "<missing>"}
            for k, v in views.items()
        }
        manifest.physical_checks = physical
        if not stages_rec:
            manifest.notes.append(
                "run dir found but no stage results parsed; stages remain NOT_RUN"
            )
    else:
        manifest.notes.append(
            "no regression workspace run directory found; stages remain NOT_RUN"
        )

    # Parity check of pinned tool versions vs live probes (report, never accept silently).
    pinned_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pins.json")
    if os.path.isfile(pinned_path):
        with open(pinned_path, "r", encoding="utf-8") as f:
            pinned = json.load(f)
        live = {t["name"]: t["version"] for t in prov.to_dict()["tools"]}
        drift = []
        for t in pinned.get("tools", []):
            name, pv = t.get("name"), t.get("version", "")
            if name in live and live[name] and pv and live[name] != pv:
                if "sha256" in str(pv) or "sha256" in str(live[name]):
                    continue
                drift.append(f"{name}: pinned={pv!r} live={live[name]!r}")
        if drift:
            manifest.notes.append("tool version drift vs pins.json: " + "; ".join(drift))

    return manifest


def save_manifest(manifest: BaselineManifest, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest.to_dict(), f, indent=2, sort_keys=True)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m assistant.baseline",
        description=(
            "Run the reproducible physical baseline (one documented command) "
            "and record full provenance."
        ),
    )
    ap.add_argument("--repo-root", default=None)
    ap.add_argument("--run-id", default="")
    ap.add_argument("--stages", default="", help="comma-separated stage names")
    ap.add_argument("--timeout", type=float, default=10800.0)
    ap.add_argument("--out", default=None, help="manifest output directory")
    ap.add_argument("--design", default="test_counter")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    stages = [s.strip() for s in args.stages.split(",") if s.strip()] or None
    manifest = run_baseline(
        args.repo_root,
        run_id=args.run_id,
        stages=stages,
        timeout_s=args.timeout,
        out_dir=args.out,
        design=args.design,
        dry_run=args.dry_run,
    )
    out_dir = args.out or os.path.join(
        args.repo_root or _repo_root(), "regression", "evidence"
    )
    path = save_manifest(manifest, os.path.join(out_dir, "baseline_manifest.json"))
    print(json.dumps({"manifest": path, "exit_code": manifest.exit_code,
                      "run_dir": manifest.run_dir,
                      "stages_recorded": len(manifest.stages),
                      "notes": manifest.notes}, indent=2))
    if manifest.exit_code not in (0, None):
        return manifest.exit_code
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
