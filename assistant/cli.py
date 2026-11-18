"""Command-line entry points for the supervised PD assistant.

Deterministic only: no LLM calls, no network.  Every subcommand prints a
JSON result that can be archived as evidence.

Shell exit-code contract (SR-06)
--------------------------------
The shell exit code is a *validation signal*, not a description of what the
process did:

======  =====================================================================
  code  meaning
======  =====================================================================
    0   The requested validation succeeded under the declared policy, or an
        inspection/reporting command completed successfully.
    1   Engineering validation failed or is blocked/incomplete: verdict
        FAIL / INCONCLUSIVE / UNSUPPORTED / NOT_RUN, or a BLOCKED gate.
    2   Invalid input: missing/unreadable/malformed file, bad provenance,
        invalid argument, bad gate definition, or a cancelled run.
    3   A prohibited force-pass / bypass was refused by policy.
    4   A prohibited bypass unexpectedly succeeded (security failure).
  124   A validation tool timed out (mirrors ``assistant.baseline``).
  125   A validation tool could not be launched (mirrors ``assistant.baseline``).
======  =====================================================================

The structured JSON on stdout is printed *unchanged* for every outcome, so
automation can read both the exit code and the full engineering decision.
Validation commands (``sta``, ``validate-pvt``, ``gate-evaluate``) exit 0 only
on success; inspection/reporting commands (``info``, ``provenance``,
``gate-report``, ``diagnose``, ``diagnose-delta``) always exit 0 when they run
successfully -- they report data, they do not validate it.  A waived PASS
still exits 0 under the declared waiver policy, but its ``disposition`` field
in the JSON distinguishes it from a CLEAN_PASS.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

from . import __version__
from .controller import (
    APPLICABILITY_RULES,
    ApplicabilityError,
    GateBypassError,
    GateController,
    GateDefinitionError,
    GateValidationError,
    RESTRICTED_CHANGE_CLASSES,
    StateError,
    Waiver,
    assert_valid_gate_submission,
    evaluate_gate_from_state,
    validate_applicability_for_check,
)
from .diagnosis import diagnose_stage_delta, diagnose_timing, summarize_for_llm
from .evidence import (
    Evidence,
    ProvenanceError,
    bind_file,
    hash_named_files,
    parse_tool_versions,
)
from .liberty import validate_targets_file
from .statuses import Status, parse_status
from .tools import default_provenance


# ------------------------------------------------------------- exit contract
EXIT_OK = 0
EXIT_VALIDATION_FAILED = 1
EXIT_USAGE = 2
EXIT_BYPASS_REFUSED = 3
EXIT_BYPASS_SUCCEEDED = 4
#: Timeout / spawn failure are only reachable through tool-launching paths
#: (``baseline``). Keep the numbers identical to ``assistant.baseline``.
EXIT_TIMEOUT = 124
EXIT_TOOL_LAUNCH = 125

# The authoritative *evidence-submission* boundary is
# ``controller.assert_valid_gate_submission`` (SR-03); CLI recording calls it
# with ``strict_provenance=True`` below, and gate evaluation goes through
# ``controller.evaluate_gate_from_state``.
# TODO(SR-06): no central *shell-exit* mapping is exported yet, so the
# status -> exit-code enforcement remains local to this module. Replace
# ``_status_exit`` and the per-command mappings with a central boundary when
# the gate agent exports one. Dependency: assistant/controller.py (SR-03 owner).


def _out_code(obj, code: int) -> int:
    """Print the structured result unchanged, then return *code*.

    Validation commands use this so a non-zero shell signal never strips the
    machine-readable verdict from stdout.
    """
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))
    return code


def _out(obj) -> int:
    """Inspection/reporting success: structured JSON, shell exit 0."""
    return _out_code(obj, EXIT_OK)


def _err(kind: str, detail: str, code: int = EXIT_USAGE) -> int:
    """Print a structured JSON error and return a non-zero exit code."""
    return _out_code({"error": kind, "detail": detail}, code)


def _status_exit(status) -> int:
    """Map a check verdict to the shell exit contract.

    Only a clean ``PASS`` maps to 0.  FAIL, INCONCLUSIVE, UNSUPPORTED, NOT_RUN
    and NOT_APPLICABLE-as-a-requested-validation all map to non-zero.
    """
    try:
        st = parse_status(status)
    except (ValueError, TypeError, AttributeError):
        return EXIT_VALIDATION_FAILED
    return EXIT_OK if st is Status.PASS else EXIT_VALIDATION_FAILED


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
    """Validation command: 0 only when every declared PVT claim validates."""
    result = validate_targets_file(args.targets, lib_dir=args.lib_dir)
    return _out_code(result, _status_exit(result.get("overall", "NOT_RUN")))


# --------------------------------------------------------------- sta check
def cmd_sta(args) -> int:
    """Validation command: 0 only when the STA report passes its targets."""
    from .diagnosis import sta_verdict

    with open(args.report, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    verdict = sta_verdict(
        text,
        target_wns_ns=args.target_wns,
        expect_paths=not args.no_expect_paths,
        require_clock=not args.no_require_clock,
    )
    return _out_code(verdict, _status_exit(verdict.get("status", "NOT_RUN")))


# --------------------------------------------------------------------- gate
def _coerce_design_fact_value(text: str):
    """Coerce a CLI ``NAME=VALUE`` token into the typed value rules expect.

    Applicability contracts compare against Python booleans/numbers (e.g.
    ``has_upf=False``), so plain strings would silently fail corroboration.
    """
    token = (text or "").strip()
    lowered = token.lower()
    if lowered in ("true", "yes", "on", "1"):
        return True
    if lowered in ("false", "no", "off", "0"):
        return False
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        return token


def _parse_design_facts(items) -> dict:
    """Parse repeatable ``--design-fact NAME=VALUE`` tokens (never touches state)."""
    facts: dict = {}
    for item in items or []:
        if "=" not in item:
            raise ProvenanceError(
                f"design fact must be NAME=VALUE, got {item!r}"
            )
        name, value = item.split("=", 1)
        name = name.strip()
        if not name:
            raise ProvenanceError("design fact name must be non-empty")
        facts[name] = _coerce_design_fact_value(value)
    return facts


def _non_waivable_spec(ctl, check_id: str):
    """Return a registered spec that declares *check_id* non-waivable, if any."""
    spec = ctl.check_specs.get(check_id)
    if spec is not None and not spec.waivable:
        return spec
    for gate_spec in ctl.gate_specs.values():
        candidate = (getattr(gate_spec, "checks", None) or {}).get(check_id)
        if candidate is not None and not candidate.waivable:
            return candidate
    return None


def cmd_gate_record(args) -> int:
    is_na = args.status == Status.NOT_APPLICABLE.value

    # Design facts are parsed before any state is touched. Missing/malformed
    # facts are invalid input (exit 2, structured JSON, no state file).
    try:
        design_facts = _parse_design_facts(args.design_fact)
    except ProvenanceError as exc:
        return _err("ProvenanceError", str(exc))

    if is_na:
        # U-A: validate applicability for the TARGET CHECK against corroborating
        # design facts BEFORE constructing/altering state. This rejects missing
        # facts, unknown/invalid rules and non-waivable checks alike.
        try:
            normalized = validate_applicability_for_check(
                args.na_reason, args.check, design_facts
            )
        except ApplicabilityError as exc:
            return _err("ApplicabilityError", str(exc))

        ctl = GateController(args.state)
        if _non_waivable_spec(ctl, args.check) is not None:
            return _err(
                "NonWaivableCheck",
                f"non-waivable check {args.check!r} cannot be NOT_APPLICABLE; "
                "genuine PASS evidence is required",
            )
        # Persist the corroborating inventory so the central boundary inside
        # record_evidence sees the same facts, then record via the U-A helper.
        for name, value in design_facts.items():
            ctl.register_design_fact(name, value, source="CLI --design-fact")
        recorded = ctl.mark_not_applicable_for_check(
            args.check, normalized, design_facts=design_facts
        )
        return _out({"recorded": recorded.evidence_id, "status": recorded.status.value})

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
        applicability_reason="",
    )

    # Enforce the authoritative submission boundary BEFORE any state write.
    # Substantive statuses always use strict provenance; recording below is
    # strict-by-default and never opts out for real tool evidence.
    try:
        assert_valid_gate_submission(ev, strict_provenance=True)
    except GateValidationError as exc:
        return _err("IncompleteProvenance", str(exc))

    ctl = GateController(args.state)
    recorded = ctl.record_evidence(ev)
    return _out({"recorded": recorded.evidence_id, "status": recorded.status.value})


def cmd_gate_evaluate(args) -> int:
    """Validation command: 0 only when the gate decision is a PASS."""
    required = [c.strip() for c in args.required.split(",") if c.strip()]
    upstream = [g.strip() for g in (args.upstream or "").split(",") if g.strip()]
    gd = evaluate_gate_from_state(
        args.state,
        args.gate,
        required,
        stage=args.stage or "",
        required_view=args.view or None,
        upstream_gates=upstream,
    )
    # The full decision (including CLEAN_PASS vs WAIVED_PASS disposition) is
    # preserved in stdout; the shell signal reports only success vs failure.
    return _out_code(
        gd.to_dict(), EXIT_OK if gd.passed else EXIT_VALIDATION_FAILED
    )


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
        # A refused force-pass is a policy-blocked outcome, never a success.
        return _out_code(
            {"bypass": "REFUSED", "error": str(exc)}, EXIT_BYPASS_REFUSED
        )
    # Reaching this branch means a prohibited bypass actually succeeded; that
    # is a security failure and must be loud for any automation watching exit.
    return _out_code(
        {"bypass": "ALLOWED (unexpected!)", "gate": args.gate},
        EXIT_BYPASS_SUCCEEDED,
    )  # pragma: no cover


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
    # Always forward --design so `python -m assistant baseline --design X` is
    # equivalent to `python -m assistant.baseline --design X`. The default
    # matches baseline.main's own default; baseline_exit_signal still decides
    # the shell exit code, so the SR-06 contract is unchanged.
    argv += ["--design", args.design]
    if args.dry_run:
        argv.append("--dry-run")
    return baseline_main(argv)


# ------------------------------------------------------------------ preflight
def _repo_root() -> str:
    """Worktree root that owns this assistant package."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cmd_preflight(args) -> int:
    """Validation command: run the real stage-00 dependency checker.

    The checker writes its full JSON report to ``<out-dir>/stage00_result.json``
    and prints a human summary to stdout. We re-emit that report as structured
    JSON (so automation never parses prose), preserving the exact missing
    dependency names in ``missing_mandatory`` / ``missing_optional``. Exit 0 is
    reserved for "all mandatory dependencies present"; missing dependencies and
    invalid inputs map to 2, other engineering failures to 1.
    """
    repo_root = _repo_root()
    checker = args.checker or os.path.join(
        repo_root, "regression", "checks", "stage00_preflight.py"
    )
    if not os.path.isfile(checker):
        return _err(
            "MissingPreflightChecker",
            f"preflight checker not found: {checker}",
            EXIT_USAGE,
        )

    out_dir = args.out or tempfile.mkdtemp(prefix="assistant-preflight-")
    result_path = os.path.join(out_dir, "stage00_result.json")
    # Never read a stale report left in a caller-supplied output directory.
    if os.path.isfile(result_path):
        try:
            os.remove(result_path)
        except OSError:
            pass
    cmd = [sys.executable, checker, args.config or repo_root, "--out-dir", out_dir]
    for tool in args.require_tool or []:
        cmd += ["--require-tool", tool]
    try:
        proc = subprocess.run(
            cmd, cwd=repo_root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        return _err(
            "PreflightTimeout", f"preflight timed out after {args.timeout}s",
            EXIT_TIMEOUT,
        )
    except OSError as exc:
        return _err(
            "PreflightLaunchError", f"could not launch preflight: {exc}",
            EXIT_TOOL_LAUNCH,
        )

    checker_out = (proc.stdout or "").strip()
    checker_err = (proc.stderr or "").strip()
    if not os.path.isfile(result_path):
        # Usage errors / bad config make the checker exit before writing JSON.
        return _err(
            "PreflightResultMissing",
            f"no structured result at {result_path}; checker exit={proc.returncode}; "
            f"stdout={checker_out!r}; stderr={checker_err!r}",
            EXIT_USAGE,
        )
    try:
        with open(result_path, "r", encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, ValueError) as exc:
        return _err(
            "PreflightResultInvalid", f"cannot read {result_path}: {exc}",
            EXIT_USAGE,
        )

    deps = report.get("dependencies") or {}
    missing_mandatory = list(deps.get("missing_mandatory") or [])
    missing_optional = list(deps.get("missing_optional") or [])
    overall = report.get("overall", "FAIL")
    payload = {
        "checker": checker,
        "repo_root": repo_root,
        "command": cmd,
        "checker_exit_code": proc.returncode,
        "checker_stdout": checker_out,
        "checker_stderr": checker_err,
        "overall": overall,
        "mandatory_failures": report.get("mandatory_failures"),
        "dependencies": deps,
        "missing_mandatory": missing_mandatory,
        "missing_optional": missing_optional,
        "checks": report.get("checks") or [],
        "result_json": result_path,
    }
    if proc.returncode == 2 or missing_mandatory:
        return _out_code(payload, EXIT_USAGE)
    if proc.returncode == 0 and overall == "PASS":
        return _out_code(payload, EXIT_OK)
    return _out_code(payload, EXIT_VALIDATION_FAILED)


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
    p.add_argument("--design-fact", action="append", default=[], metavar="NAME=VALUE",
                   help="validated design fact for N/A corroboration (repeatable)")
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
    p.add_argument("--design", default="test_counter",
                   help="design top/module to baseline (default: test_counter)")
    p.add_argument("--stages", default="")
    p.add_argument("--out", default=None)
    p.add_argument("--timeout", type=float, default=10800.0,
                   help="regression timeout in seconds")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_baseline)

    p = sub.add_parser("preflight", help="run the real stage-00 dependency checker")
    p.add_argument("--config", default=None,
                   help="config JSON or project dir (default: worktree repo root)")
    p.add_argument("--out", default=None, help="preflight output directory")
    p.add_argument("--require-tool", action="append", default=[], metavar="NAME",
                   help="promote an optional EDA tool to mandatory (repeatable)")
    p.add_argument("--checker", default=None,
                   help="override checker script path (testing/diagnostics only)")
    p.add_argument("--timeout", type=float, default=120.0,
                   help="preflight subprocess timeout in seconds")
    p.set_defaults(func=cmd_preflight)

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
        return _err("GateBypassError", str(exc), EXIT_BYPASS_REFUSED)
    except GateValidationError as exc:
        return _err("GateValidationError", str(exc))
    except GateDefinitionError as exc:
        return _err("GateDefinitionError", str(exc))
    except StateError as exc:
        return _err("StateError", str(exc))
    except FileNotFoundError as exc:
        return _err("FileNotFoundError", str(exc))
    except OSError as exc:
        return _err("OSError", str(exc))
    except (ValueError, TypeError) as exc:
        # Malformed JSON / bad numeric input / unknown status.
        return _err("InvalidInput", str(exc))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
