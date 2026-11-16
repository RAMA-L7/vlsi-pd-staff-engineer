"""Deterministic gate controller: stage states, evidence binding, blocking.

Design rules enforced here (see docs/reviews/remediation-tracker.md, finding A):

* Gate decisions are computed ONLY from recorded evidence records.
* LLM/human summaries are stored as advisory text and are never consulted by
  ``evaluate_gate``; ``force_pass`` raises ``GateBypassError``.
* Evidence whose registered inputs changed since recording is STALE and no
  longer counts (the check falls back to NOT_RUN until re-executed).
* A required check that is not PASS (or explicitly waived / validated
  NOT_APPLICABLE) blocks the gate, and a blocked gate blocks downstream
  stages.
* NOT_APPLICABLE requires an ``NA-RULE:`` applicability reason that passes
  validation at record time.
* Waivers are explicit, scoped, recorded and surfaced in every gate report.
* State is persisted atomically so a cancelled run can be restarted without
  losing evidence.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from typing import Callable, Iterable, Mapping, Sequence

from .evidence import Evidence, view_compatible, utc_now
from .hashing import sha256_file
from .statuses import Status, blocks, parse_status


class GateBypassError(RuntimeError):
    """Raised when something tries to authorize a gate without evidence."""


class ApplicabilityError(ValueError):
    """Raised when a NOT_APPLICABLE reason fails validation."""


class StateError(RuntimeError):
    """Raised for illegal state transitions (e.g. work while cancelled)."""


#: Applicability reasons must start with an explicit rule token and explain
#: why the check does not apply.  Rule ids live in APPLICABILITY_RULES.
APPLICABILITY_RULES: dict[str, str] = {
    "NA-RULE:NO-UPF": "design has no UPF/CPF power-intent file in scope",
    "NA-RULE:NO-MACROS": "design contains no hard macros",
    "NA-RULE:NO-CLOCK-GATES": "design contains no integrated clock gates",
    "NA-RULE:NO-ANALOG": "design contains no analog/hard IP",
    "NA-RULE:COMBO-ONLY": "design is purely combinational (no sequential elements)",
}

MIN_REASON_LEN = 20
MIN_WAIVER_REASON_LEN = 20

#: Change classes that require explicit architectural authorization.
RESTRICTED_CHANGE_CLASSES = frozenset(
    {
        "rtl_behavior",
        "interface_latency",
        "clock_definition",
        "timing_exception",
        "power_intent",
        "verification_exclusion",
    }
)


@dataclass
class Waiver:
    check_id: str
    scope: str
    reason: str
    approved_by: str
    created_utc: str = field(default_factory=utc_now)
    expires_utc: str | None = None
    waiver_id: str = field(default_factory=lambda: f"W{utc_now()}".replace(":", ""))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping) -> "Waiver":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})  # type: ignore[attr-defined]

    def covers(self, gate_id: str, check_id: str) -> bool:
        """A waiver is scoped: it must name the check and the gate scope."""
        if self.check_id != check_id:
            return False
        if self.scope in ("*", "all", gate_id):
            return True
        # scope may be "gate:xxx" or a comma list
        tokens = [t.strip() for t in self.scope.replace(";", ",").split(",")]
        return gate_id in tokens or f"gate:{gate_id}" in tokens


@dataclass
class GateDecision:
    gate_id: str
    decision: str  # PASS | BLOCKED
    reason: str
    checks: dict[str, dict] = field(default_factory=dict)
    waivers: list[dict] = field(default_factory=list)
    advisory_summaries: int = 0
    decided_utc: str = field(default_factory=utc_now)

    @property
    def passed(self) -> bool:
        return self.decision == "PASS"

    def to_dict(self) -> dict:
        return asdict(self)


def validate_applicability_reason(reason: str) -> str:
    """Validate + normalize a NOT_APPLICABLE reason.

    This is the single validator for the NA-RULE registry and substantive-
    reason rules. It is invoked from ``record_evidence`` so *every* recording
    boundary (direct API, CLI, ``mark_not_applicable``) is covered.
    """
    text = (reason or "").strip()
    if len(text) < MIN_REASON_LEN:
        raise ApplicabilityError(
            f"applicability reason must be at least {MIN_REASON_LEN} characters "
            f"(got {len(text)})"
        )
    if not text.startswith("NA-RULE:"):
        raise ApplicabilityError(
            "applicability reason must start with an explicit NA-RULE:<id> token"
        )
    rule_id = text.split()[0].rstrip(":").replace("NA-RULE:", "NA-RULE:")
    rule_token = text.split()[0]
    if rule_token not in APPLICABILITY_RULES:
        raise ApplicabilityError(
            f"unknown applicability rule {rule_token!r}; known rules: "
            + ", ".join(sorted(APPLICABILITY_RULES))
        )
    # The reason must also say something beyond the rule id.
    if len(text) <= len(rule_token) + MIN_REASON_LEN // 2:
        raise ApplicabilityError(
            "applicability reason must justify the rule, not only name it"
        )
    return text


# Backwards-compatible private alias (kept so existing callers do not break).
_validate_applicability_reason = validate_applicability_reason


class GateController:
    """Owns evidence records, waivers and gate evaluation for one workspace."""

    def __init__(self, state_path: str | os.PathLike[str]):
        self.state_path = str(state_path)
        self._inputs: dict[str, str] = {}  # label -> path (registered)
        self._state: dict = {
            "version": 1,
            "run_status": "ACTIVE",
            "cancel_reason": "",
            "evidence": {},       # evidence_id -> record
            "check_index": {},    # check_id -> [evidence_id, ...] (ordered)
            "waivers": [],
            "summaries": [],      # advisory only, never read by evaluate_gate
            "gate_history": [],
            "created_utc": utc_now(),
            "updated_utc": utc_now(),
        }
        if os.path.exists(self.state_path):
            with open(self.state_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self._state.update(loaded)

    # -- persistence ----------------------------------------------------------
    def _save(self) -> None:
        self._state["updated_utc"] = utc_now()
        directory = os.path.dirname(os.path.abspath(self.state_path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".state-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2, sort_keys=True)
            os.replace(tmp, self.state_path)  # atomic on POSIX and Windows
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # -- input registry -------------------------------------------------------
    def register_input(self, label: str, path: str) -> str:
        """Register a file whose change invalidates dependent evidence."""
        self._inputs[label] = str(path)
        return self.current_input_hashes().get(label, "<missing>")

    def register_inputs(self, mapping: Mapping[str, str]) -> None:
        for label, path in mapping.items():
            self.register_input(label, path)

    def current_input_hashes(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for label, path in self._inputs.items():
            try:
                out[label] = sha256_file(path)
            except OSError:
                out[label] = "<missing>"
        return out

    # -- evidence -------------------------------------------------------------
    def record_evidence(self, ev: Evidence, *, allow_while_cancelled: bool = False) -> Evidence:
        if self._state["run_status"] == "CANCELLED" and not allow_while_cancelled:
            raise StateError(
                "run is CANCELLED; restart() before recording new evidence"
            )
        # Central NOT_APPLICABLE validation: every recording boundary must
        # carry a valid NA-RULE reason. Validate BEFORE mutating state so an
        # invalid/empty reason is never persisted and can never influence a
        # gate decision.
        if ev.status is Status.NOT_APPLICABLE:
            ev.applicability_reason = validate_applicability_reason(
                ev.applicability_reason
            )
            ev.reason = ev.applicability_reason
        # Bind evidence to the currently registered inputs (only labels that
        # were registered at record time become staleness anchors).
        if not ev.input_hashes:
            ev.input_hashes = self.current_input_hashes()
        self._state["evidence"][ev.evidence_id] = ev.to_dict()
        idx = self._state["check_index"].setdefault(ev.check_id, [])
        idx.append(ev.evidence_id)
        self._save()
        return ev

    def latest_evidence(self, check_id: str) -> Evidence | None:
        ids = self._state["check_index"].get(check_id) or []
        if not ids:
            return None
        return Evidence.from_dict(self._state["evidence"][ids[-1]])

    def all_evidence(self, check_id: str | None = None) -> list[Evidence]:
        if check_id is None:
            recs = self._state["evidence"].values()
        else:
            ids = self._state["check_index"].get(check_id) or []
            recs = [self._state["evidence"][i] for i in ids]
        return [Evidence.from_dict(r) for r in recs]

    # -- advisory summaries (never authorize anything) ------------------------
    def record_llm_summary(self, text: str, *, author: str = "llm") -> dict:
        entry = {"author": author, "text": text, "created_utc": utc_now()}
        self._state["summaries"].append(entry)
        self._save()
        return entry

    @property
    def summaries(self) -> list[dict]:
        return list(self._state["summaries"])

    def force_pass(self, gate_id: str, justification: str = "") -> None:
        """Deliberately unsupported: gate transitions need evidence."""
        raise GateBypassError(
            f"refusing to force PASS for gate {gate_id!r}: gate decisions are "
            f"computed from recorded evidence only (justification={justification!r})"
        )

    def authorize_from_summary(self, gate_id: str) -> None:
        raise GateBypassError(
            "LLM/human summaries are advisory and cannot authorize a gate transition"
        )

    # -- applicability & waivers ---------------------------------------------
    def mark_not_applicable(self, ev: Evidence) -> Evidence:
        # Set the status, then delegate to record_evidence(), which applies
        # the single shared NA validator. No separate/divergent validation.
        ev.status = Status.NOT_APPLICABLE
        return self.record_evidence(ev)

    def add_waiver(self, waiver: Waiver) -> Waiver:
        if len(waiver.reason.strip()) < MIN_WAIVER_REASON_LEN:
            raise ValueError(
                f"waiver reason must be at least {MIN_WAIVER_REASON_LEN} characters"
            )
        if not waiver.scope.strip():
            raise ValueError("waiver must declare an explicit scope")
        if not waiver.approved_by.strip():
            raise ValueError("waiver must record who approved it")
        self._state["waivers"].append(waiver.to_dict())
        self._save()
        return waiver

    def waivers_for(self, gate_id: str, check_id: str) -> list[Waiver]:
        return [
            Waiver.from_dict(w)
            for w in self._state["waivers"]
            if Waiver.from_dict(w).covers(gate_id, check_id)
        ]

    def waivers(self) -> list[Waiver]:
        return [Waiver.from_dict(w) for w in self._state["waivers"]]

    # -- gate evaluation ------------------------------------------------------
    def evaluate_gate(
        self,
        gate_id: str,
        required_checks: Sequence[str],
        *,
        stage: str = "",
        required_view: str | None = None,
        upstream_gates: Iterable[str] = (),
    ) -> GateDecision:
        if self._state["run_status"] == "CANCELLED":
            raise StateError(
                f"run is CANCELLED ({self._state['cancel_reason']}); restart() "
                "before evaluating gates"
            )

        current = self.current_input_hashes()
        check_report: dict[str, dict] = {}
        blocking: list[str] = []
        waived_visible: list[dict] = []
        na_visible: list[dict] = []

        # Upstream gates must have passed first.
        for up in upstream_gates:
            hist = self._last_decision(up)
            if hist is None or hist.get("decision") != "PASS":
                blocking.append(f"upstream gate {up} not PASS")
                check_report[f"upstream:{up}"] = {
                    "status": Status.NOT_RUN.value,
                    "reason": "upstream gate has no PASS decision",
                }

        for check_id in required_checks:
            ev = self.latest_evidence(check_id)
            waivers = self.waivers_for(gate_id, check_id)
            entry: dict = {"waived": [w.to_dict() for w in waivers]}

            if ev is None:
                entry.update(
                    status=Status.NOT_RUN.value,
                    reason="no evidence recorded for required check",
                )
                blocking.append(f"{check_id}: NOT_RUN (no evidence)")
                check_report[check_id] = entry
                continue

            stale = ev.stale_inputs(current)
            if stale:
                entry.update(
                    status=Status.NOT_RUN.value,
                    reason="STALE evidence: inputs changed since recording: "
                    + ", ".join(sorted(stale)),
                    stale=True,
                    evidence_id=ev.evidence_id,
                )
                blocking.append(f"{check_id}: NOT_RUN (stale evidence)")
                check_report[check_id] = entry
                continue

            status = ev.status

            # Consistency guards: exit code alone never proves correctness,
            # and PASS with a failing process or missing reports is rejected.
            if status is Status.PASS and ev.exit_code not in (None, 0):
                status = Status.FAIL
                entry["reason"] = (
                    f"inconsistent evidence: PASS recorded but exit_code="
                    f"{ev.exit_code}"
                )
            elif status is Status.PASS and ev.missing_report_artifacts():
                status = Status.FAIL
                entry["reason"] = (
                    "missing report artifacts: "
                    + ", ".join(ev.missing_report_artifacts())
                )
            elif status is Status.PASS and required_view and not view_compatible(
                required_view, ev.analysis_view
            ):
                status = Status.FAIL
                entry["reason"] = (
                    f"analysis view mismatch: gate requires {required_view}, "
                    f"evidence is {ev.analysis_view}"
                )
            else:
                entry["reason"] = ev.reason

            if status is Status.NOT_APPLICABLE:
                # already validated at record time
                na_visible.append(
                    {"check": check_id, "reason": ev.applicability_reason}
                )

            waived = bool(waivers) and blocks(status)
            if waivers:
                waived_visible.extend(w.to_dict() for w in waivers)

            entry.update(
                status=status.value,
                evidence_id=ev.evidence_id,
                analysis_view=ev.analysis_view,
                exit_code=ev.exit_code,
            )
            check_report[check_id] = entry

            if blocks(status, waived=waived):
                blocking.append(f"{check_id}: {status.value}")

        decision = "PASS" if not blocking else "BLOCKED"
        reason = (
            "all required checks PASS"
            if decision == "PASS"
            else "; ".join(blocking)
        )
        gd = GateDecision(
            gate_id=gate_id,
            decision=decision,
            reason=reason,
            checks=check_report,
            waivers=waived_visible,
            advisory_summaries=len(self._state["summaries"]),
        )
        self._state["gate_history"].append(
            {"stage": stage, **gd.to_dict()}
        )
        self._save()
        return gd

    def _last_decision(self, gate_id: str) -> dict | None:
        for entry in reversed(self._state["gate_history"]):
            if entry.get("gate_id") == gate_id:
                return entry
        return None

    def can_enter_stage(
        self, stage: str, gates: Sequence[tuple[str, Sequence[str]]], *,
        required_view: str | None = None, upstream: Sequence[str] = ()
    ) -> tuple[bool, list[str]]:
        """Evaluate every gate of *stage*; return (allowed, blocking reasons)."""
        reasons: list[str] = []
        for gate_id, checks in gates:
            gd = self.evaluate_gate(
                gate_id, checks, stage=stage, required_view=required_view,
                upstream_gates=upstream,
            )
            if not gd.passed:
                reasons.append(f"{gate_id}: {gd.reason}")
        return (not reasons), reasons

    # -- cancellation / restart ----------------------------------------------
    def cancel(self, reason: str) -> None:
        self._state["run_status"] = "CANCELLED"
        self._state["cancel_reason"] = reason
        self._save()

    def restart(self) -> str:
        """Reopen a cancelled run, preserving all recorded evidence."""
        prev = self._state["run_status"]
        self._state["run_status"] = "ACTIVE"
        self._state["cancel_reason"] = ""
        self._save()
        return f"run restarted from {prev}; evidence preserved"

    @property
    def run_status(self) -> str:
        return self._state["run_status"]

    def report(self) -> dict:
        """Full visible state: evidence, waivers (visible), summaries (advisory)."""
        return {
            "run_status": self._state["run_status"],
            "cancel_reason": self._state["cancel_reason"],
            "inputs": dict(self._inputs),
            "current_input_hashes": self.current_input_hashes(),
            "evidence_count": len(self._state["evidence"]),
            "waivers": list(self._state["waivers"]),
            "advisory_summaries": list(self._state["summaries"]),
            "gate_history": list(self._state["gate_history"]),
        }
