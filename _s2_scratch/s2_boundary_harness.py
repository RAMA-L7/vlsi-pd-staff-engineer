"""S2 independent commit-boundary / cancellation harness.

All coordination uses on-disk marker files with bounded waits; no wall-clock
sleep is used to win a race. The source under test is never modified here.

Subcommands:
  writer <work> <source> <mode>     mode: crash_after_replace | pre_lock_cancel | normal
  cancel <work> <source> <trigger:0|1> [idx]
  lockholder <work> <source>
  verify <work> <source>
  run <case> <run_dir> <source> [reps]
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True

LEDGER_KEY = "S2-COMMIT-BOUNDARY-KEY-20261126"
REASON = "S2 cross-process cancel at commit boundary"
BASELINE = "module s2_dut; endmodule\n"
SIM = "S2 simulated commit-boundary harness"
FACTS = ("has_macros", "has_upf", "has_clock_gates", "has_analog", "has_analog_devices")


def emit(tag, payload):
    print(tag + " " + json.dumps(payload, sort_keys=True, default=str), flush=True)


def wait_marker(path: Path, timeout: float = 45.0):
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("marker not observed: " + str(path))
        time.sleep(0.005)


def write_marker(path: Path, text: str = "OK"):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def read_run_status(state_path: Path) -> str:
    try:
        return str(json.loads(state_path.read_text(encoding="utf-8")).get("run_status"))
    except Exception:
        return "<unreadable>"


def setup(work: Path, source: str):
    sys.path.insert(0, source)
    from assistant.controller import GateController
    from assistant.eco import EcoManager

    ws = work / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "design.v").write_text(BASELINE, encoding="utf-8")
    ctl = GateController(str(work / "state.json"))
    for fact in FACTS:
        ctl.register_design_fact(fact, False, source="S2 harness recorded inventory")
    eco = EcoManager(str(ws), ctl, checkpoint_dir=str(work / "ckpt"),
                     experiment_log=str(work / "exp.json"))
    rep = work / "report.txt"
    rep.write_text("S2 simulated report: clean\n", encoding="utf-8")
    return ctl, eco, rep


def make_verify(rep: Path):
    from assistant.evidence import Evidence
    from assistant.statuses import Status

    def verify():
        return [
            Evidence.from_run(cid, Status.PASS, "S2 simulated", command=["sim"],
                              exit_code=0, analysis_view="MAPPED_NETLIST",
                              tool_versions={"sim": "1"}, reports=[str(rep)])
            for cid in ("eco_lec", "eco_sta", "eco_physical_drc")
        ]
    return verify


def run_experiment(eco, verify, downstream=None):
    from assistant.eco import EcoChangeScope, EcoManager, EcoStage, ProtectedMetric
    return eco.run_experiment(
        experiment_id="s2-boundary", description="S2 commit-boundary probe",
        change_class="logic_optimization", changed_files=["design.v"],
        apply_change=lambda: None, evaluate=lambda: {"wns": 1.0},
        protected_metrics=[ProtectedMetric("wns", 0.0)],
        verify=verify, verification_tools=None, verification_timeout_s=5.0,
        simulated=SIM, analysis_mode=False, downstream_stage=downstream,
        change_scope=EcoChangeScope("logic_optimization", EcoStage.MAPPED, True,
                                    "S2 recorded scope"),
    )


# ---------------------------------------------------------------------------
# child processes
# ---------------------------------------------------------------------------
def child_writer(work: Path, source: str, mode: str):
    ctl, eco, rep = setup(work, source)
    verify = make_verify(rep)
    write_marker(work / "writer_ready.0", read_run_status(work / "state.json"))

    if mode == "pre_lock_cancel":
        # Cancel is durably committed BEFORE the writer ever acquires the
        # _save_log lock (i.e. strictly before the commit boundary).
        ctl.cancel("S2 pre-lock cancel")
        write_marker(work / "pre_cancel_done.0", read_run_status(work / "state.json"))
        result = run_experiment(eco, verify)
        emit("WRITER_RESULT", {"mode": mode, "status": result.status,
                               "qualification_class": result.qualification_class,
                               "cancelled": result.cancelled})
        return

    import assistant.eco as eco_mod
    real_replace = eco_mod.os.replace
    fired = {"v": False}

    def hooked_replace(src, dst, *a, **kw):
        if str(dst).endswith("exp.json") and not fired["v"]:
            fired["v"] = True
            # Start the canceller while THIS process already holds the state
            # lock (the hook runs inside _save_log's critical section).
            write_marker(work / "go_cancel.0", "GO")
            wait_marker(work / "cancel_started.0")
            real_replace(src, dst, *a, **kw)          # publish PASS record
            write_marker(work / "published.0", "OK")
            if mode == "crash_after_replace":
                # Abrupt death AFTER the boundary, BEFORE the post-write
                # recheck, while still holding the advisory lock. The OS
                # releases that lock on process exit.
                sys.stdout.flush(); sys.stderr.flush()
                os._exit(91)
            return None  # normal mode: return value unused by _write
        return real_replace(src, dst, *a, **kw)

    eco_mod.os.replace = hooked_replace
    try:
        result = run_experiment(eco, verify)
        emit("WRITER_RESULT", {"mode": mode, "status": result.status,
                               "qualification_class": result.qualification_class,
                               "cancelled": result.cancelled})
    finally:
        eco_mod.os.replace = real_replace


def child_cancel(work: Path, source: str, trigger: bool, idx: int = 0):
    wait_marker(work / f"writer_ready.{idx}")
    sys.path.insert(0, source)
    from assistant.controller import GateController
    ctl = GateController(str(work / "state.json"))
    if trigger:
        wait_marker(work / f"go_cancel.{idx}")
    write_marker(work / f"cancel_started.{idx}", read_run_status(work / "state.json"))
    ctl.cancel(REASON)
    done = {
        "pid": os.getpid(),
        "durable_run_status": read_run_status(work / "state.json"),
        "latch": ctl.cancellation_requested(),
        "reason": REASON,
    }
    tmp = work / f"cancel_done.{idx}.tmp"
    tmp.write_text(json.dumps(done, sort_keys=True), encoding="utf-8")
    os.replace(tmp, work / f"cancel_done.{idx}")
    emit("CANCEL_CHILD_DONE", done)


def child_lockholder(work: Path, source: str):
    wait_marker(work / "writer_ready.0")
    sys.path.insert(0, source)
    from assistant.controller import ledger_critical_section
    with ledger_critical_section(work / "state.json"):
        write_marker(work / "lock_held.0", str(os.getpid()))
        sys.stdout.flush()
        os._exit(23)


def child_verify(work: Path, source: str):
    sys.path.insert(0, source)
    from assistant.controller import GateController
    from assistant.eco import EcoManager

    state = work / "state.json"
    exp = work / "exp.json"
    raw_state = json.loads(state.read_text(encoding="utf-8")) if state.exists() else {}
    raw_exp = json.loads(exp.read_text(encoding="utf-8")) if exp.exists() else {}
    recs = raw_exp.get("experiments", [])
    qualified_raw = [r for r in recs if r.get("status") == "PASS"
                     and r.get("qualification_class") in ("PRODUCTION", "SIMULATED")]
    out = {
        "pid": os.getpid(),
        "durable_run_status": raw_state.get("run_status"),
        "state_sha256": hashlib.sha256(state.read_bytes()).hexdigest() if state.exists() else None,
        "exp_sha256": hashlib.sha256(exp.read_bytes()).hexdigest() if exp.exists() else None,
        "records": [{k: r.get(k) for k in
                     ("status", "qualification_class", "cancelled", "deadline_exceeded",
                      "invalidation_kind", "transaction_token")} for r in recs],
        "raw_qualified_pass_count": len(qualified_raw),
        "durable_qualified_pass_raw": bool(qualified_raw),
    }
    ctl = GateController(str(state))
    out["fresh_controller_run_status"] = ctl.run_status
    try:
        gd = ctl.evaluate_gate("STA", ["eco_sta"])
        out["fresh_gate_qualified"] = bool(getattr(gd, "qualified", False))
        out["fresh_gate_disposition"] = str(getattr(gd, "disposition", ""))
    except Exception as exc:
        out["fresh_gate_qualified"] = False
        out["fresh_gate_disposition"] = "error:%s:%s" % (type(exc).__name__, exc)
    try:
        allowed, why = ctl.can_enter_stage("ROUTE", [("STA", ["eco_sta"])])
        out["fresh_downstream_allowed"] = bool(allowed)
        out["fresh_downstream_why"] = [str(x) for x in (why or [])]
    except Exception as exc:
        out["fresh_downstream_allowed"] = False
        out["fresh_downstream_why"] = ["error:%s:%s" % (type(exc).__name__, exc)]
    fresh_eco = EcoManager(str(work / "ws"), ctl, checkpoint_dir=str(work / "ckpt_verify"),
                            experiment_log=str(exp))
    out["fresh_history"] = [{"status": r.get("status"),
                             "qualification_class": r.get("qualification_class"),
                             "cancelled": r.get("cancelled", False)}
                            for r in fresh_eco.history()]
    # Optional reader-side reconciliation helper (added by the fix).
    try:
        from assistant.commit_boundary import reconcile_experiment_log
        out["reconciliation"] = reconcile_experiment_log(exp, state)
    except ImportError:
        out["reconciliation"] = None
    emit("FRESH_VERIFY", out)


def spawn(script, args, work, *, env_extra=None):
    env = {**os.environ, "ASSISTANT_LEDGER_KEY": LEDGER_KEY,
           "PYTHONPATH": str(Path(sys.argv[0]).resolve().parent.parent),
           "PYTHONDONTWRITEBYTECODE": "1"}
    if env_extra:
        env.update(env_extra)
    log_path = work / ("child_" + args[0] + "_" + str(os.getpid()) + ".out")
    log = open(log_path, "w", encoding="utf-8")
    p = subprocess.Popen([sys.executable, "-B", str(script)] + args,
                         cwd=str(work), stdout=log, stderr=subprocess.STDOUT, env=env)
    return p, log


def finish(procs_logs):
    rcs = []
    for p, log in procs_logs:
        try:
            rcs.append(p.wait(timeout=90))
        except Exception:
            p.terminate()
            rcs.append(p.wait(timeout=30))
        log.close()
    return rcs


def verify_fresh(work, source):
    script = Path(__file__).resolve()
    env = {**os.environ, "ASSISTANT_LEDGER_KEY": LEDGER_KEY, "PYTHONPATH": source,
           "PYTHONDONTWRITEBYTECODE": "1"}
    cp = subprocess.run([sys.executable, "-B", str(script), "verify", str(work), source],
                        cwd=str(work), env=env, capture_output=True, text=True, timeout=120)
    payload = None
    for line in cp.stdout.splitlines():
        if line.startswith("FRESH_VERIFY "):
            payload = json.loads(line[len("FRESH_VERIFY "):])
    return {"rc": cp.returncode, "payload": payload,
            "stdout_tail": cp.stdout[-1500:], "stderr_tail": cp.stderr[-1000:]}


# ---------------------------------------------------------------------------
# orchestrator
# ---------------------------------------------------------------------------
def orchestrate(case: str, run_dir: str, source: str, reps: int = 1):
    source = str(Path(source).resolve())
    script = Path(__file__).resolve()
    all_results = []
    for rep in range(reps):
        work = Path(run_dir) if reps == 1 else Path(run_dir) / f"rep{rep}"
        work.mkdir(parents=True, exist_ok=True)
        procs_logs = []
        obs = {"case": case, "rep": rep, "work": str(work), "source": source}

        if case in ("crash_after_replace", "post_lock_cancel", "concurrent_cancel_commit"):
            mode = {"crash_after_replace": "crash_after_replace",
                    "post_lock_cancel": "normal",
                    "concurrent_cancel_commit": "normal"}[case]
            procs_logs.append(spawn(script, ["cancel", str(work), source, "1"], work))
            procs_logs.append(spawn(script, ["writer", str(work), source, mode], work))
            if case == "post_lock_cancel":
                # cancel only after the writer has fully returned (strictly
                # after the boundary and after lock release)
                wait_marker(work / "writer_ready.0")
                wait_marker(work / "published.0", timeout=90)
            rcs = finish(procs_logs)
            obs["child_rcs"] = rcs
            obs["order"] = "writer-and-canceller-concurrent"

        elif case == "pre_lock_cancel":
            # single process: cancel durably before writer's save lock
            p, log = spawn(script, ["writer", str(work), source, "pre_lock_cancel"], work)
            obs["child_rcs"] = finish([(p, log)])
            obs["order"] = "cancel-before-boundary"

        elif case == "lock_recovery":
            procs_logs.append(spawn(script, ["lockholder", str(work), source], work))
            # Need writer_ready marker for lockholder; create it via a tiny
            # setup in-process (fresh state.json), then wait for lock held.
            sys.path.insert(0, source)
            from assistant.controller import GateController
            ctl = GateController(str(work / "state.json"))
            for fact in FACTS:
                ctl.register_design_fact(fact, False, source="S2 lock-recovery setup")
            write_marker(work / "writer_ready.0", "ACTIVE")
            wait_marker(work / "lock_held.0", timeout=30)
            obs["child_rcs"] = finish(procs_logs)  # lockholder rc should be 23
            # Fresh process (this orchestrator) cancels after lock-holder death.
            try:
                ctl.cancel("S2 cancel after lock-holder crash")
                obs["lock_recovery_cancel"] = {
                    "ok": True, "run_status": ctl.run_status,
                    "latch": ctl.cancellation_requested(),
                }
            except Exception as exc:
                obs["lock_recovery_cancel"] = {"ok": False,
                                               "error": f"{type(exc).__name__}: {exc}"}
            obs["order"] = "lock-holder-crash-then-fresh-cancel"

        else:
            raise ValueError("unknown case: " + case)

        obs["verify"] = verify_fresh(work, source)
        p = obs["verify"]["payload"] or {}
        obs["verdict"] = {
            "durable_run_status": p.get("durable_run_status"),
            "raw_qualified_pass_count": p.get("raw_qualified_pass_count"),
            "fresh_downstream_allowed": p.get("fresh_downstream_allowed"),
            "fresh_gate_qualified": p.get("fresh_gate_qualified"),
        }
        emit("S2_CASE_SUMMARY", {"case": case, "rep": rep, **obs["verdict"],
                                 "verify_rc": obs["verify"]["rc"]})
        all_results.append(obs)
        (work / "result.json").write_text(json.dumps(obs, indent=2, sort_keys=True,
                                                     default=str), encoding="utf-8")
    (Path(run_dir) / "suite_result.json").write_text(
        json.dumps({"case": case, "results": all_results}, indent=2, sort_keys=True,
                   default=str), encoding="utf-8")
    print("SUITE_RESULT " + str(Path(run_dir) / "suite_result.json"), flush=True)


def main():
    cmd = sys.argv[1]
    if cmd == "writer":
        child_writer(Path(sys.argv[2]), sys.argv[3], sys.argv[4])
    elif cmd == "cancel":
        child_cancel(Path(sys.argv[2]), sys.argv[3], sys.argv[4] == "1",
                     int(sys.argv[5]) if len(sys.argv) > 5 else 0)
    elif cmd == "lockholder":
        child_lockholder(Path(sys.argv[2]), sys.argv[3])
    elif cmd == "verify":
        child_verify(Path(sys.argv[2]), sys.argv[3])
    elif cmd == "run":
        reps = int(sys.argv[5]) if len(sys.argv) > 5 else 1
        orchestrate(sys.argv[2], sys.argv[3], sys.argv[4], reps=reps)
    else:
        raise SystemExit("usage: s2_boundary_harness.py writer|cancel|lockholder|verify|run ...")


if __name__ == "__main__":
    main()
