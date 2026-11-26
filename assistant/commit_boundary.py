"""Durable commit-boundary semantics and reader-side reconciliation (S2).

This module is a **read-only interpretation helper**. It changes no control
flow in :mod:`assistant.controller` or :mod:`assistant.eco` and grants no
authorization: real downstream authorization still goes exclusively through
``GateController.can_enter_stage`` / ``evaluate_gate``.

Why it exists
-------------
Two durable artifacts are written at different instants:

* ``state.json`` -- the run-level ledger. ``run_status`` is the **authoritative**
  value for downstream authorization. A cancellation commits here under
  ``ledger_critical_section``.
* ``exp.json`` -- an append-only log of ECO transaction records. A record is
  published by a single atomic ``os.replace(tmp, exp.json)``; that rename is the
  **durable commit boundary** for that transaction.

The record's ``cancelled`` field is a *commit-time observation*: it is True only
when the authoritative commit guard had latched a cancellation at the instant of
the rename. It is deliberately not retroactive (doing so would rewrite an
append-only, transaction-token-bound record and would re-open EXT-01).

Precise boundary semantics
--------------------------
1. PRE-boundary cancellation (``run_status=CANCELLED`` durable in ``state.json``
   strictly *before* the record's ``os.replace``): EXT-04's guard reads the
   durable status inside the same lock and **blocks PASS**. No qualified durable
   PASS may be produced; the terminal record is FAIL/INCONCLUSIVE/UNQUALIFIED.
2. POST-boundary cancellation (``CANCELLED`` becomes durable strictly *after*
   the rename, e.g. a writer is killed after ``os.replace`` while holding the
   lock and a canceller commits once the OS releases it): a PASS record may
   coexist with ``run_status=CANCELLED``. The record remains a truthful snapshot
   of the transaction at commit -- "no cancellation was durable yet" -- but it
   is **not** authorization for downstream work.
3. Process-crash windows: a crash before the rename leaves no published record;
   a crash after the rename leaves the published record exactly as committed.
   In both cases the authoritative run-level status is whatever ``state.json``
   durably holds, and it alone decides downstream authorization.
4. Therefore a durable PASS record with ``run_status=CANCELLED`` is a
   *historical transaction fact superseded at the run level*, not a grant. A
   consumer that reads ``exp.json`` must cross-check ``state.json``; this helper
   performs that cross-check and flags the contradiction explicitly.

Fail-closed rule: any unreadable/corrupt/missing artifact is treated as
``CANCELLED``/unauthorized.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

QUALIFIED_CLASSES = ("PRODUCTION", "SIMULATED")
_UNREADABLE = "<unreadable>"


@dataclass(frozen=True)
class ReconciledRecord:
    """One transaction record reconciled against the authoritative run status."""

    transaction_token: str
    raw_status: str
    raw_qualification_class: str
    raw_cancelled: bool
    durable_run_status: str
    effective_status: str
    effective_qualified: bool
    contradiction: bool
    interpretation: str

    def to_dict(self) -> dict:
        return asdict(self)


def _read_json(path: Path | str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - fail closed
        return {}


def _run_status(state_path: Path | str) -> str:
    data = _read_json(state_path)
    status = data.get("run_status")
    if not isinstance(status, str) or not status:
        return _UNREADABLE
    return status


def reconcile_record(record: dict, durable_run_status: str) -> ReconciledRecord:
    """Reconcile a single ``exp.json`` record against ``state.json`` run status.

    ``raw_*`` fields are the transaction facts as published at the commit
    boundary. ``effective_*`` fields are the run-level interpretation that a
    consumer must use. ``contradiction`` is True exactly for the post-boundary
    artifact: a committed PASS whose run was later cancelled.
    """
    raw_status = str(record.get("status", ""))
    raw_class = str(record.get("qualification_class", ""))
    raw_cancelled = bool(record.get("cancelled", False))
    token = str(record.get("transaction_token", ""))
    run_cancelled = durable_run_status == "CANCELLED"
    run_unknown = durable_run_status == _UNREADABLE
    raw_pass = raw_status == "PASS" and raw_class in QUALIFIED_CLASSES
    contradiction = bool(raw_pass and run_cancelled)

    if run_cancelled:
        effective_status = "CANCELLED"
        effective_qualified = False
        if contradiction:
            interpretation = (
                "post-boundary artifact: PASS was durably committed before the "
                "run was cancelled; the record is a truthful commit-time fact but "
                "is superseded at the run level and grants no downstream authority"
            )
        else:
            interpretation = (
                "run-level CANCELLED; non-PASS record consistent with pre-boundary "
                "cancellation closure"
            )
    elif run_unknown:
        effective_status = "UNREADABLE"
        effective_qualified = False
        interpretation = (
            "durable run status unreadable/corrupt/missing; fail closed: no "
            "downstream authority may be derived from this record"
        )
    else:
        effective_status = raw_status
        effective_qualified = raw_pass
        interpretation = (
            "run-level status is not CANCELLED; record interpreted as committed"
            + (" (authoritative qualification must still be re-derived by the gate)"
               if raw_pass else "")
        )

    return ReconciledRecord(
        transaction_token=token,
        raw_status=raw_status,
        raw_qualification_class=raw_class,
        raw_cancelled=raw_cancelled,
        durable_run_status=durable_run_status,
        effective_status=effective_status,
        effective_qualified=effective_qualified,
        contradiction=contradiction,
        interpretation=interpretation,
    )


def reconcile_experiment_log(exp_path: Path | str, state_path: Path | str) -> dict:
    """Read both durable artifacts (without writing them) and reconcile them.

    Returns a JSON-serialisable dict with the authoritative run status, the
    reconciled records, raw vs effective qualified-PASS counts, the explicit
    contradiction list, and an advisory authorization mirror. The mirror is
    advisory only: :class:`GateController` remains the authorization authority.
    """
    exp = _read_json(exp_path)
    durable = _run_status(state_path)
    records = [reconcile_record(r, durable) for r in exp.get("experiments", [])
               if isinstance(r, dict)]
    contradictions = [r.to_dict() for r in records if r.contradiction]
    run_cancelled = durable == "CANCELLED"
    return {
        "durable_run_status": durable,
        "records": [r.to_dict() for r in records],
        "raw_qualified_pass_count": sum(
            1 for r in records
            if r.raw_status == "PASS" and r.raw_qualification_class in QUALIFIED_CLASSES
        ),
        "effective_qualified_pass_count": sum(1 for r in records if r.effective_qualified),
        "contradiction_count": len(contradictions),
        "contradictions": contradictions,
        "downstream_run_status_ok": (not run_cancelled) and durable != _UNREADABLE,
        "authorization_note": (
            "advisory only; authoritative downstream authorization is "
            "GateController.can_enter_stage/ evaluate_gate, which key on state.json"
        ),
    }


__all__ = [
    "QUALIFIED_CLASSES",
    "ReconciledRecord",
    "reconcile_record",
    "reconcile_experiment_log",
]
