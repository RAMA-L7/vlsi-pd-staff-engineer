"""Command-line entry points for the supervised PD assistant.

Deterministic only: no LLM calls, no network.  Every subcommand prints a
JSON result that can be archived as evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .controller import (
    APPLICABILITY_RULES,
    ApplicabilityError,
    GateBypassError,
    GateController,
    RESTRICTED_CHANGE_CLASSES,
    Waiver,
)
from .diagnosis import diagnose_stage_delta, diagnose_timing, summarize_for_llm
from .evidence import (
    Evidence,
    ProvenanceError,
    bind_file,
    hash_named_files,
    parse_tool_versions,
    validate_provenance,
)
from .liberty import validate_targets_file
from .statuses import Status, parse_status
from .tools import default_provenance


def _out(obj) -> int:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))
    return 0


def _err(kind: str, detail: str, code: int = 2) -> int:
    """Print a clear JSON error and return a non-zero exit code."""
    print(json.dumps({"error": kind, "detail": detail}, indent=2, sort_keys=True))
    return code


# ---------------------------------------------------------------- provenance
def cmd_provenance(args) -> int:
    prov = default_provenance(args.repo_root or os.getcwd())
    data = prov.to_dict()
    if args.out:
        prov.save(args.out)
        data["saved_to"] = args.out
    return _out(data)


# ----------------------------------------------------------------- pvt check
def cmd_validate_pvt(args) -> int:
    result = validate_targets_file(args.targets, lib_dir=args.lib_dir)
    return _out(result)


# --------------------------------------------------------------- sta check
def cmd_sta(args) -> int:
    from .diagnosis import sta_verdict

    with open(args.report, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    verdict = sta_verdict(
        text,
        target_wns_ns=args.target_wns,
        expect_paths=not args.no_expect_paths,
        require_clock=not args.no_require_clock,
    )
    return _out(verdict)


# --------------------------------------------------------------------- gate
def cmd_gate_record(args) -> int:
    is_na = args.status == Status.NOT_APPLICABLE.value

    # Bind every supplied artefact/hash. Missing files or malformed NAME=VALUE
    # tokens are refused up front so nothing partial is ever persisted.
    try:
        reports = [bind_file(p) for p in args.report]
        logs = [bind_file(p) for p in args.log]
        artifacts = [bind_file(p) for p in args.artifact]
        tool_versions = parse_tool_versions(args.tool_version)
        input_hashes = hash_named_files(args.input_hash)
        config_hashes = hash_named_files(args.config_hash)
        constraint_hashes = hash_named_files(args.constraint_hash)
        library_hashes = hash_named_files(args.library_hash)
        pdk_hashes = hash_named_files(args.pdk_hash)
    except ProvenanceError as exc:
        return _err("ProvenanceError", str(exc))

    ev = Evidence(
        check_id=args.check,
        status=parse_status(args.status),
        reason=args.reason,
        command=[args.command] if args.command else [],
        exit_code=args.exit_code,
        analysis_view=args.view,
        tool_versions=tool_versions,
        input_hashes=input_hashes,
        config_hashes=config_hashes,
        constraint_hashes=constraint_hashes,
        library_hashes=library_hashes,
        pdk_hashes=pdk_hashes,
        reports=reports,
        logs=logs,
        artifacts=artifacts,
        applicability_reason=args.na_reason or "",
    )

    # Enforce provenance completeness for substantive statuses BEFORE any
    # state write. NOT_APPLICABLE is exempt; its reason is validated by the
    # controller's shared NA validator during recording.
    if not is_na:
        problems = validate_provenance(ev)
        if problems:
            return _err("IncompleteProvenance", "; ".join(problems))

    ctl = GateController(args.state)
    if is_na:
        recorded = ctl.mark_not_applicable(ev)
    else:
        recorded = ctl.record_evidence(ev)
    return _out({"recorded": recorded.evidence_id, "status": recorded.status.value})


def cmd_gate_evaluate(args) -> int:
    ctl = GateController(args.state)
    required = [c.strip() for c in args.required.split(",") if c.strip()]
    upstream = [g.strip() for g in (args.upstream or "").split(",") if g.strip()]
    gd = ctl.evaluate_gate(
        args.gate,
        required,
        stage=args.stage or "",
        required_view=args.view or None,
        upstream_gates=upstream,
    )
    return _out(gd.to_dict())


def cmd_gate_report(args) -> int:
    ctl = GateController(args.state)
    return _out(ctl.report())


def cmd_gate_cancel(args) -> int:
    ctl = GateController(args.state)
    ctl.cancel(args.reason)
    return _out({"run_status": ctl.run_status, "reason": args.reason})


def cmd_gate_restart(args) -> int:
    ctl = GateController(args.state)
    msg = ctl.restart()
    return _out({"run_status": ctl.run_status, "message": msg})


def cmd_gate_bypass_attempt(args) -> int:
    """Deliberately attempt a bypass - expected to fail (used in tests/demo)."""
    ctl = GateController(args.state)
    try:
        if args.mode == "force":
            ctl.force_pass(args.gate, justification="requested bypass")
        else:
            ctl.authorize_from_summary(args.gate)
    except GateBypassError as exc:
        return _out({"bypass": "REFUSED", "error": str(exc)})
    return _out({"bypass": "ALLOWED (unexpected!)", "gate": args.gate})  # pragma: no cover


# ----------------------------------------------------------------- diagnose
def cmd_diagnose(args) -> int:
    with open(args.report, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    rep = diagnose_timing(
        text,
        check_id=args.check,
        target_wns_ns=args.target_wns,
        previous_wns_ns=args.previous_wns,
    )
    data = rep.to_dict()
    if args.advisory:
        data["advisory_summary"] = summarize_for_llm(rep)
    return _out(data)


def cmd_diagnose_delta(args) -> int:
    with open(args.before, "r", encoding="utf-8") as f:
        before = json.load(f)
    with open(args.after, "r", encoding="utf-8") as f:
        after = json.load(f)
    rep = diagnose_stage_delta(before, after, worse_is=args.worse_is)
    return _out(rep.to_dict())


# ------------------------------------------------------------------- baseline
def cmd_baseline(args) -> int:
    from .baseline import main as baseline_main

    argv = ["--repo-root", args.repo_root] if args.repo_root else []
    if args.run_id:
        argv += ["--run-id", args.run_id]
    if args.stages:
        argv += ["--stages", args.stages]
    if args.out:
        argv += ["--out", args.out]
    if args.timeout is not None:
        argv += ["--timeout", str(args.timeout)]
    if args.dry_run:
        argv.append("--dry-run")
    return baseline_main(argv)


# ----------------------------------------------------------------------- meta
def cmd_info(args) -> int:
    return _out(
        {
            "assistant_version": __version__,
            "statuses": [s.value for s in Status],
            "na_rules": sorted(APPLICABILITY_RULES),
            "restricted_change_classes": sorted(RESTRICTED_CHANGE_CLASSES),
            "llm_can_authorize_gates": False,
        }
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="assistant",
        description=(
            "Deterministic supervision for the VLSI PD flow: evidence-bound "
            "gates, PVT validation, read-only diagnosis, controlled ECOs."
        ),
    )
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="capabilities and rules")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("provenance", help="pin tool/flow/PDK/library versions")
    p.add_argument("--repo-root", default=None)
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_provenance)

    p = sub.add_parser("validate-pvt", help="validate synthesis_targets PVT vs Liberty")
    p.add_argument("--targets", required=True)
    p.add_argument("--lib-dir", default=None)
    p.set_defaults(func=cmd_validate_pvt)

    p = sub.add_parser("sta", help="deterministic STA report verdict")
    p.add_argument("--report", required=True)
    p.add_argument("--target-wns", type=float, default=0.0)
    p.add_argument("--no-expect-paths", action="store_true")
    p.add_argument("--no-require-clock", action="store_true")
    p.set_defaults(func=cmd_sta)

    p = sub.add_parser("gate-record", help="record evidence for a check")
    p.add_argument("--state", required=True)
    p.add_argument("--check", required=True)
    p.add_argument("--status", required=True, choices=[s.value for s in Status])
    p.add_argument("--reason", required=True)
    p.add_argument("--command", default="")
    p.add_argument("--exit-code", type=int, default=None)
    p.add_argument("--view", default="UNKNOWN")
    p.add_argument("--na-reason", default="")
    p.add_argument("--report", action="append", default=[], metavar="PATH",
                   help="existing report file (repeatable); bound by SHA-256")
    p.add_argument("--log", action="append", default=[], metavar="PATH",
                   help="existing log file (repeatable); bound by SHA-256")
    p.add_argument("--artifact", action="append", default=[], metavar="PATH",
                   help="existing output artifact (repeatable); bound by SHA-256")
    p.add_argument("--tool-version", action="append", default=[], metavar="NAME=VERSION",
                   help="tool version binding (repeatable, non-empty)")
    p.add_argument("--input-hash", action="append", default=[], metavar="NAME=PATH",
                   help="hash an existing input file (repeatable)")
    p.add_argument("--config-hash", action="append", default=[], metavar="NAME=PATH",
                   help="hash an existing config file (repeatable)")
    p.add_argument("--constraint-hash", action="append", default=[], metavar="NAME=PATH",
                   help="hash an existing constraint file (repeatable)")
    p.add_argument("--library-hash", action="append", default=[], metavar="NAME=PATH",
                   help="hash an existing library file (repeatable)")
    p.add_argument("--pdk-hash", action="append", default=[], metavar="NAME=PATH",
                   help="hash an existing PDK file (repeatable)")
    p.set_defaults(func=cmd_gate_record)

    p = sub.add_parser("gate-evaluate", help="evaluate a gate from evidence")
    p.add_argument("--state", required=True)
    p.add_argument("--gate", required=True)
    p.add_argument("--required", required=True, help="comma-separated check ids")
    p.add_argument("--stage", default="")
    p.add_argument("--view", default="")
    p.add_argument("--upstream", default="")
    p.set_defaults(func=cmd_gate_evaluate)

    p = sub.add_parser("gate-report", help="dump controller state")
    p.add_argument("--state", required=True)
    p.set_defaults(func=cmd_gate_report)

    p = sub.add_parser("gate-cancel", help="cancel the run safely")
    p.add_argument("--state", required=True)
    p.add_argument("--reason", required=True)
    p.set_defaults(func=cmd_gate_cancel)

    p = sub.add_parser("gate-restart", help="restart a cancelled run")
    p.add_argument("--state", required=True)
    p.set_defaults(func=cmd_gate_restart)

    p = sub.add_parser("gate-bypass-attempt", help="attempt a bypass (must be refused)")
    p.add_argument("--state", required=True)
    p.add_argument("--gate", required=True)
    p.add_argument("--mode", choices=["force", "summary"], default="force")
    p.set_defaults(func=cmd_gate_bypass_attempt)

    p = sub.add_parser("diagnose", help="read-only timing diagnosis")
    p.add_argument("--report", required=True)
    p.add_argument("--check", default="sta")
    p.add_argument("--target-wns", type=float, default=0.0)
    p.add_argument("--previous-wns", type=float, default=None)
    p.add_argument("--advisory", action="store_true")
    p.set_defaults(func=cmd_diagnose)

    p = sub.add_parser("diagnose-delta", help="compare stage metrics between runs")
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    p.add_argument("--worse-is", choices=["higher", "lower"], default="lower")
    p.set_defaults(func=cmd_diagnose_delta)

    p = sub.add_parser("baseline", help="run the reproducible physical baseline")
    p.add_argument("--repo-root", default=None)
    p.add_argument("--run-id", default="")
    p.add_argument("--stages", default="")
    p.add_argument("--out", default=None)
    p.add_argument("--timeout", type=float, default=10800.0,
                   help="regression timeout in seconds")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_baseline)

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except ApplicabilityError as exc:
        return _err("ApplicabilityError", str(exc))
    except ProvenanceError as exc:
        return _err("ProvenanceError", str(exc))
    except GateBypassError as exc:
        print(json.dumps({"error": "GateBypassError", "detail": str(exc)}), file=sys.stderr)
        return 3
    except FileNotFoundError as exc:
        print(json.dumps({"error": "FileNotFoundError", "detail": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
