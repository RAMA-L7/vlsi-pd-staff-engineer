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
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict

from .evidence import utc_now
from .hashing import sha256_file, stable_json_hash
from .liberty import parse_liberty_pvt
from .tools import ENV_KEYS, default_provenance, resolve_platform_config

STAGE_NAME_RE = re.compile(r"^(\d{2})[_-](\w+)")

#: Canonical RTL/SDC inputs per design (mirrors run_regression.ps1's
#: DesignConfig).  A design not registered here falls back to the conventional
#: ``examples/<design>/rtl/<design>.{v,sdc}`` layout, recorded as ``<missing>``
#: when absent.  This makes ``--design`` affect the provenance, not just the
#: command line (SR-08 / CLI --design parity).
DESIGN_INPUTS: dict[str, tuple[str, str]] = {
    "test_counter": (
        os.path.join("examples", "test-counter", "rtl", "test_counter.v"),
        os.path.join("examples", "test-counter", "rtl", "test_counter.sdc"),
    ),
    "soc_ctrl": (
        os.path.join("examples", "soc_ctrl", "rtl", "soc_ctrl.v"),
        os.path.join("examples", "soc_ctrl", "rtl", "soc_ctrl.sdc"),
    ),
}


def design_input_paths(design: str) -> tuple[str, str]:
    """Return (rtl_rel, sdc_rel) used to hash a design's canonical inputs."""
    if design in DESIGN_INPUTS:
        return DESIGN_INPUTS[design]
    return (
        os.path.join("examples", design, "rtl", f"{design}.v"),
        os.path.join("examples", design, "rtl", f"{design}.sdc"),
    )


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
    #: Isolated directory that holds this invocation's log + manifest.  Empty
    #: only for manifests constructed by hand (tests / callers using --out).
    output_dir: str = ""
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
    #: Regression invocation could not be started (``exit_code`` stays None).
    start_failed: bool = False
    #: Regression invocation hit the wall-clock timeout (``exit_code`` stays None).
    timed_out: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def completed_ok(self) -> bool:
        """True only when the regression actually ran and exited with code 0.

        A ``None`` exit code (timeout, spawn failure or a deliberate dry run)
        is *never* a successful completion, even though it is recorded
        honestly in ``exit_code``.
        """
        return not self.start_failed and not self.timed_out and self.exit_code == 0


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
    "Macro-based example designs (NO-MACROS): no macro/abstract flow is "
    "implemented or demonstrated; the supported examples are standard-cell "
    "only (test_counter datapath, soc_ctrl controller)",
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


# Shell-facing exit codes for a baseline that never genuinely completed.  The
# manifest keeps ``exit_code = None`` in these cases (the tool never exited),
# but automation must not read that as success.
BASELINE_TIMEOUT_EXIT = 124
BASELINE_SPAWN_EXIT = 125
BASELINE_INCOMPLETE_EXIT = 1

_WSL_MNT_RE = re.compile(r"^/mnt/([A-Za-z])/(.*)$")


def baseline_exit_signal(manifest: BaselineManifest) -> int:
    """Return the shell exit code that faithfully reports the baseline outcome.

    ``0`` only when the regression actually ran and exited 0; otherwise a
    conventional non-zero code (124 timeout, 125 spawn failure, 1 other
    incomplete).  The manifest's ``exit_code`` is left untouched.
    """
    if manifest.timed_out:
        return BASELINE_TIMEOUT_EXIT
    if manifest.start_failed:
        return BASELINE_SPAWN_EXIT
    if manifest.completed_ok:
        return 0
    if manifest.exit_code is not None:
        return manifest.exit_code
    return BASELINE_INCOMPLETE_EXIT


def normalize_wsl_path(path: str) -> str:
    """Map a WSL ``/mnt/<letter>/...`` path to a native Windows path.

    Python on Windows cannot resolve ``/mnt/d/...``, so such a path (as
    recorded by the regression's WSL-side checks) cannot be hashed as-is.
    This helper converts it to ``<letter>:/...`` for on-disk lookups only.

    * no-op on non-Windows hosts;
    * no-op for native Windows paths and for paths that are not WSL mounts;
    * the drive letter is matched case-insensitively.

    The caller must keep the recorded path verbatim.
    """
    if os.name != "nt":
        return path
    m = _WSL_MNT_RE.match(path)
    if not m:
        return path
    return f"{m.group(1).upper()}:/{m.group(2)}"


def _hash_view_artifact(path: str) -> str:
    """Hash a recorded view path; translate WSL mount paths on Windows only.

    The caller keeps ``path`` verbatim in the manifest.  Returns
    ``"<missing>"`` only when the file truly cannot be found or read.
    """
    candidates = [path]
    normalized = normalize_wsl_path(path)
    if normalized != path:
        candidates.append(normalized)
    for candidate in candidates:
        if os.path.isfile(candidate):
            try:
                return sha256_file(candidate)
            except OSError:
                return "<missing>"
    return "<missing>"


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
    isolated: bool = True,
    clean: bool = False,
) -> BaselineManifest:
    """The one documented command: run + record the reproducible baseline.

    By default every invocation gets its own isolated output directory under
    ``out_dir`` (``--no-isolate`` restores the legacy shared directory).
    ``clean=True`` (CLI ``--clean``) removes that directory before writing,
    which is useful when deliberately reusing a run id.  Neither option
    touches the regression workspace: each RunId still has its own
    ``regression/workspace/run_<RunId>``.
    """
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

    base_out = out_dir or os.path.join(root, "regression", "evidence")
    if isolated:
        stamp = run_id or utc_now().replace(":", "").replace("-", "")
        out = os.path.join(base_out, f"baseline_{design}_{stamp}")
    else:
        out = base_out
    if clean and os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out, exist_ok=True)

    # WORKSTREAM 2: resolve once from the portable source of truth
    # (assistant/pins.json -> portable_config).  When the recorded developer-host
    # defaults are absent, validated fallbacks are applied; the same facts feed
    # provenance and are injected into the regression subprocess below so the
    # runner (which already honours these overrides) uses them too.  An
    # explicit caller environment override always wins.
    cfg = resolve_platform_config()
    prov = default_provenance(root, config=cfg)
    rtl_rel, sdc_rel = design_input_paths(design)
    design_hashes = {}
    for label, rel in (
        ("rtl", rtl_rel),
        ("sdc", sdc_rel),
        ("synthesis_targets", "synthesis_targets.json"),
        ("run_regression", os.path.join("regression", "run_regression.ps1")),
    ):
        p = os.path.join(root, rel)
        design_hashes[label] = sha256_file(p) if os.path.isfile(p) else "<missing>"

    manifest = BaselineManifest(
        command=argv,
        exit_code=None,
        run_dir="",
        output_dir=out,
        design=design,
        provenance=prov.to_dict(),
        design_hashes=design_hashes,
        config_hash=stable_json_hash(
            {"design": design, "stages": stages or "all", "repo": root}
        ),
        unsupported=list(UNSUPPORTED_CAPABILITIES),
        distinctions=dict(DISTINCTIONS),
    )
    if design not in DESIGN_INPUTS:
        manifest.notes.append(
            f"design {design!r} has no registered input paths; hashed the conventional "
            "examples/<design>/rtl layout (missing files recorded as <missing>)"
        )
    for key in cfg.get("portable_fallback_applied", []):
        manifest.notes.append(
            f"portable fallback applied: {key}={cfg['values'][key]!r} "
            "(source of truth: assistant/pins.json portable_config; "
            "explicit environment overrides take precedence)"
        )

    if dry_run:
        manifest.notes.append("dry run: command not executed")
        manifest.exit_code = None
        return manifest

    log_path = os.path.join(out, f"baseline_{run_id or 'run'}.log")
    with open(log_path, "wb") as logf:
        logf.write(f"$ {' '.join(argv)}\n".encode())
        logf.flush()
        # run_regression.ps1 defaults $Repo to an unrelated checkout; point it
        # at the validated root so the report's git provenance is faithful.
        env = os.environ.copy()
        env["REG_REPO"] = root
        # Inject only keys that were resolved via portable fallback (i.e. no
        # caller override existed).  Never clobber an explicit caller value.
        for key in cfg.get("portable_fallback_applied", []):
            var = (ENV_KEYS.get(key) or (None,))[0]
            if var and not str(env.get(var, "")).strip():
                env[var] = cfg["values"][key]
        try:
            proc = subprocess.run(
                argv,
                cwd=root,
                stdout=logf,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
                shell=False,
                env=env,
            )
            manifest.exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            manifest.exit_code = None
            manifest.timed_out = True
            manifest.notes.append(f"baseline timed out after {timeout_s}s")
        except OSError as exc:
            manifest.exit_code = None
            manifest.start_failed = True
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
            k: {"path": v, "sha256": _hash_view_artifact(v)}
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
    ap.add_argument("--design", default="test_counter",
                    help="design top/module whose canonical RTL/SDC are hashed")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-isolate", action="store_true",
                    help="write the manifest/log directly into --out (legacy shared dir)")
    ap.add_argument("--clean", action="store_true",
                    help="remove this invocation's isolated output dir before writing "
                         "(safe because the dir is unique per design/run id)")
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
        isolated=not args.no_isolate,
        clean=args.clean,
    )
    out_dir = manifest.output_dir or args.out or os.path.join(
        args.repo_root or _repo_root(), "regression", "evidence"
    )
    path = save_manifest(manifest, os.path.join(out_dir, "baseline_manifest.json"))
    signal = baseline_exit_signal(manifest)
    if signal == 0:
        status = "PASS"
    elif manifest.timed_out:
        status = "TIMEOUT"
    elif manifest.start_failed:
        status = "SPAWN_FAILED"
    else:
        status = "INCOMPLETE"
    print(json.dumps({
        "manifest": path,
        "design": manifest.design,
        "output_dir": out_dir,
        "exit_code": manifest.exit_code,
        "run_dir": manifest.run_dir,
        "stages_recorded": len(manifest.stages),
        "notes": manifest.notes,
        "timed_out": manifest.timed_out,
        "start_failed": manifest.start_failed,
        "status": status,
        "return_code": signal,
    }, indent=2))
    return signal


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
