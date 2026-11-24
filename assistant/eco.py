"""Controlled ECO experiments: checkpoints, bounded change, rollback, gates.

Rules enforced (mission requirements, findings I/M and second-review SR-01):

* Every experiment starts from an isolated, *whole-workspace* checkpoint, so
  changes to files that were not declared as part of the experiment are both
  detected and reverted.
* Exactly ONE bounded change per experiment.
* Changes in ``RESTRICTED_CHANGE_CLASSES`` (RTL behavior, interface latency,
  clock definition, timing exception, power intent, verification exclusion)
  require explicit architectural authorization recorded *before* the change.
* Every substantive change class has a registered policy of required checks.
  Verification is mandatory: a candidate is kept only on fresh PASS evidence
  for each required check. FAIL / INCONCLUSIVE / UNSUPPORTED / NOT_RUN /
  missing evidence / timeout / cancellation / exceptions all reject and roll
  the experiment back. Checks that do not apply are declared explicitly via
  an ``NOT_APPLICABLE`` policy entry with a validated reason - never by
  silently omitting them.
* apply_change -> verify -> evaluate -> policy -> persistence -> downstream
  stage is one transaction. Any exception rolls the whole workspace back and
  the rollback is verified by re-hashing every file against the checkpoint.
* Protected metrics must be finite numbers; NaN, infinity, malformed values
  and missing metrics reject and roll back.
* Protected metrics are compared against the baseline; a regression rolls the
  experiment back automatically and preserves the failed evidence.
* Every attempt (including failures, evidence IDs and reasons) is persisted
  in the experiment history.
* Three consecutive failed attempts trigger a mandatory reassessment - never
  an automatic "it must be upstream" claim.
* Constraints may never be silently relaxed to improve numbers: relaxing any
  registered constraint/protected file is itself a restricted change class.
"""
from __future__ import annotations

import gzip
import inspect
import json
import math
import os
import queue
import re
import shutil
import stat
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from .controller import GateController, RESTRICTED_CHANGE_CLASSES, GateBypassError
from .evidence import Evidence, utc_now, validate_provenance, revalidate_evidence
from .hashing import sha256_bytes, sha256_file, stable_json_hash
# R4/A5-A: the managed-execution registry used by _verify_candidate_quiescent.
# runner.py does not import eco.py, so this cannot create an import cycle.
from .runner import ThreadContainment
from .statuses import Status, parse_status


class EcoError(RuntimeError):
    """Base class for ECO-specific errors."""


class EcoAuthorizationError(EcoError):
    """Raised when a restricted change is attempted without authorization."""


class EcoBudgetExceeded(EcoError):
    """Raised when the failure budget forces reassessment."""


class EcoPolicyError(EcoError):
    """Raised when a change class has no registered verification policy."""


class EcoRejected(EcoError):
    """Internal transaction rejection carrying the result status to record."""

    def __init__(self, reason: str, *, status: str = "ROLLED_BACK") -> None:
        super().__init__(reason)
        self.status = status


class EcoVerificationError(EcoRejected):
    """A required verification check did not produce an acceptable result."""

    def __init__(self, reason: str, *, status: str = "FAIL",
                 verification_report: dict | None = None,
                 measured_duration_s: float | None = None) -> None:
        super().__init__(reason, status=status)
        self.verification_report = verification_report
        self.measured_duration_s = measured_duration_s


class EcoMissingEvidenceError(EcoVerificationError):
    def __init__(self, reason: str, **kwargs) -> None:
        super().__init__(reason, status="INCONCLUSIVE", **kwargs)


class EcoTimeoutError(EcoVerificationError):
    def __init__(self, reason: str, **kwargs) -> None:
        super().__init__(reason, status="INCONCLUSIVE", **kwargs)


class EcoCancellationError(EcoVerificationError):
    def __init__(self, reason: str, **kwargs) -> None:
        super().__init__(reason, status="INCONCLUSIVE", **kwargs)


class EcoDependencyError(EcoError):
    """Raised when the pinned dependency closure cannot be established (R2-02).

    The closure is authoritative: a missing required collateral file, a
    malformed/empty manifest, a duplicate declaration, path traversal or an
    unsafe symlink must abort acceptance rather than silently shrinking the
    set of hashed inputs.
    """


class _AcceptanceGuard:
    """Single source of truth for the terminal state of one ECO transaction.

    One guard is created per :meth:`EcoManager.run_experiment`. It stays
    authoritative from verification through evaluation, final revalidation,
    persistence, downstream authorization and the FINAL integrity re-hash
    (R2-01 / R4-A1-F1). ``latch()`` records the FIRST invalidation reason under
    a lock; it can never be cleared or overwritten. ``check()`` re-evaluates the
    deadline and cancellation on every call so an expiry that happens *during*
    a non-cooperative callback (or the final re-hash) is observed as soon as
    the callback returns. ``invalidate()`` latches the same irreversible state
    for non-clock terminal transitions (e.g. a post-acceptance mutation), so
    the guard - not a caller-side flag - is the authority for every non-PASS
    terminal state.

    ``transaction_token`` is the unique identity of this transaction. It is
    stamped onto every durable record and is the only key an amendment may use
    to find "this transaction's own record"; ``history[-1]`` is never assumed.

    ``parent`` links nested transactions so a re-entrant acceptance (a callback
    that re-enters the manager) observes an outer transaction's invalidation
    and cannot restore PASS.
    """

    def __init__(
        self,
        *,
        deadline: float | None,
        cancelled: Callable[[], bool],
        parent: "_AcceptanceGuard | None" = None,
        transaction_token: str | None = None,
    ) -> None:
        self.deadline = deadline
        self._cancelled = cancelled
        self.parent = parent
        self.transaction_token = transaction_token or uuid.uuid4().hex
        self._lock = threading.Lock()
        self.invalidation_reason: str | None = None
        self.invalidation_kind: str | None = None  # "deadline" | "cancellation" | "mutation"

    def expired(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline

    def latch(self, label: str) -> None:
        """Atomically latch the first invalidation; never overwrite it."""
        with self._lock:
            if self.invalidation_reason is not None:
                return
            if self._cancelled():
                self.invalidation_reason = f"run was cancelled {label}"
                self.invalidation_kind = "cancellation"
            elif self.expired():
                self.invalidation_reason = (
                    f"overall verification deadline expired {label}; result is invalid"
                )
                self.invalidation_kind = "deadline"

    def invalidate(self, reason: str, kind: str) -> None:
        """Irreversibly latch an explicit terminal reason on this transaction.

        Used for terminal transitions that are not deadline/cancel driven
        (post-acceptance candidate mutation, forbidden re-acceptance, ...).
        The first latched reason wins, exactly as in :meth:`latch`; a later
        caller cannot overwrite it with PASS or with a softer reason.
        """
        with self._lock:
            if self.invalidation_reason is not None:
                return
            self.invalidation_reason = reason
            self.invalidation_kind = kind

    def latched_deadline_exceeded(self) -> bool:
        """``deadline_exceeded`` derived from the LATCHED reason, not a clock re-read.

        A callback that extends ``deadline`` after expiry must not turn a
        terminal deadline record into ``deadline_exceeded=False`` (F4).
        """
        return self.chain_reason()[1] == "deadline"

    def chain_invalidated(self) -> bool:
        """True if this guard or any parent guard is already invalidated."""
        node: _AcceptanceGuard | None = self
        while node is not None:
            node.latch("during re-entrant acceptance")
            if node.invalidation_reason is not None:
                return True
            node = node.parent
        return False

    def chain_reason(self) -> tuple[str | None, str | None]:
        node: _AcceptanceGuard | None = self
        while node is not None:
            if node.invalidation_reason is not None:
                return node.invalidation_reason, node.invalidation_kind
            node = node.parent
        return None, None

    def check(self, label: str) -> None:
        self.latch(label)
        reason = self.invalidation_reason
        if reason is None:
            return
        if self.invalidation_kind == "cancellation":
            raise EcoCancellationError(reason)
        raise EcoTimeoutError(reason)


@dataclass(frozen=True)
class CheckRequirement:
    """A check that belongs to a change class's required-check set.

    Applicability is NOT a property of the change class alone (TR-01). It is
    computed from recorded implementation facts by :func:`applicability_for`.
    ``na_reason`` is retained as an optional explicit reason for backwards
    compatibility but is no longer derived from the absence of macros.
    """

    check_id: str
    na_reason: str | None = None


class EcoStage(str, Enum):
    """Implementation stage an ECO candidate targets (TR-01)."""

    RTL = "rtl"
    GENERIC = "generic"
    MAPPED = "mapped"
    PLACE = "place"
    ROUTE = "route"
    FILL = "fill"


@dataclass(frozen=True)
class EcoChangeScope:
    """Recorded, source-backed facts that decide check applicability.

    Caller prose (``description``) is advisory only and is never consulted by
    the applicability decision.  ``stage_fact_source`` must identify where the
    implementation facts came from (run id, manifest, tool report); an empty
    source fails closed.
    """

    change_class: str
    target_stage: EcoStage
    physical_implementation_present: bool
    stage_fact_source: str

    def __post_init__(self) -> None:
        if self.change_class not in CHANGE_CLASS_POLICY:
            raise ValueError(f"unknown change class: {self.change_class!r}")
        if not isinstance(self.target_stage, EcoStage):
            raise ValueError("target_stage must be an EcoStage")
        if not isinstance(self.physical_implementation_present, bool):
            raise ValueError("physical_implementation_present must be a bool")
        if not str(self.stage_fact_source).strip():
            raise ValueError("stage_fact_source must identify the source of the facts")

    def to_dict(self) -> dict:
        return {
            "change_class": self.change_class,
            "target_stage": self.target_stage.value,
            "physical_implementation_present": self.physical_implementation_present,
            "stage_fact_source": self.stage_fact_source,
        }


@dataclass(frozen=True)
class CheckApplicability:
    """Resolved applicability of one check for one concrete candidate."""

    check_id: str
    applicable: bool
    reason: str
    na_reason: str | None = None


@dataclass(frozen=True)
class CandidateRevision:
    """Immutable identity of the exact candidate whose evidence was validated.

    R4: ``dependency_revision`` binds the pinned dependency closure into the
    same frozen identity as the candidate bytes. It is deliberately NOT part
    of the mutable ``ExperimentResult`` handed to downstream, so a callback
    cannot rewrite the value that the final integrity check compares against.
    """

    revision_id: str
    snapshot: Mapping[str, str]
    experiment_id: str
    evidence_ids: tuple[str, ...]
    verification: Mapping[str, object]
    measured_duration_s: float
    created_utc: str
    dependency_revision: str = ""

    def to_dict(self) -> dict:
        return {
            "revision_id": self.revision_id,
            "snapshot": dict(self.snapshot),
            "experiment_id": self.experiment_id,
            "evidence_ids": list(self.evidence_ids),
            "verification": dict(self.verification),
            "measured_duration_s": self.measured_duration_s,
            "created_utc": self.created_utc,
            "dependency_revision": self.dependency_revision,
        }


def _managed_containment_detail(result: object) -> str:
    """Single, honest description of why a managed result is non-authoritative."""
    return (
        f"containment={getattr(result, 'containment', 'none')} "
        f"reason={getattr(result, 'containment_reason', '')} "
        f"wsl_uncontained={getattr(result, 'wsl_uncontained', False)} "
        f"error={getattr(result, 'error', '')}"
    )


def _raise_uncontained(result: object, *, check_id: str, prefix: str) -> None:
    """Fail closed: an uncontained managed result must never become evidence."""
    raise EcoVerificationError(
        f"{prefix} {check_id!r} is not authorization-safe: "
        f"{_managed_containment_detail(result)}; result is non-authoritative",
        status="INCONCLUSIVE",
    )


@dataclass
class VerificationContext:
    """Contract handed to verification providers (TR-02/TR-03).

    * ``deadline`` is a ``time.monotonic()`` instant; ``remaining()`` is the
      enforceable budget. ``check()`` raises on expiry or cancellation.
    * Providers that can be interrupted MUST poll ``check()`` or use
      :meth:`spawn` so the framework can terminate the whole process tree.
    * A plain Python callback that ignores these signals cannot be forcibly
      killed in-process; the framework abandons it on deadline and detects any
      late mutation after rollback instead of claiming a hard deadline.
    """

    deadline: float | None
    cancelled: Callable[[], bool]
    started: float = field(default_factory=time.monotonic)

    def remaining(self) -> float:
        if self.deadline is None:
            return float("inf")
        return max(0.0, self.deadline - time.monotonic())

    def expired(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline

    def check(self) -> None:
        if self.cancelled():
            raise EcoCancellationError("verification cancelled by operator")
        if self.expired():
            raise EcoTimeoutError("verification deadline exceeded (monotonic)")
        if not math.isfinite(self.remaining()) and self.deadline is not None:
            raise EcoTimeoutError("verification deadline invalid")

    @property
    def measured_duration_s(self) -> float:
        return time.monotonic() - self.started

    #: Last result returned by :meth:`spawn`, for acceptance reporting.  It is
    #: set only on the authoritative path; a refused result never reaches here.
    last_managed_result: object | None = field(default=None, repr=False, compare=False)

    def spawn(
        self, command, *, log_dir: str, name: str,
        timeout: float | None = None,
        cwd: str | os.PathLike[str] | None = None,
    ):
        """Launch a tool through the managed, tree-terminating runner.

        HONEST CLASSIFICATION (R4/A5-A/B/C):

        * HARD-CONTAINED: a direct Windows/POSIX child tree launched here is
          tracked by PID and, on timeout/cancel, killed with ``taskkill /T /F``
          (or ``killpg``) and CONFIRMED gone by two independent PID oracles.
          Deadline termination is enforced, not merely requested.
        * COOPERATIVE-ONLY: cancellation for an already-running tool is polled
          at the spawn boundary and mid-flight; a tool that ignores it is
          bounded by the monotonic timeout.  This is detection, not prevention.
        * UNSUPPORTED: WSL/Linux descendants live in a separate PID namespace
          and cannot be host-confirmed.  Every WSL path is classified
          ``unsupported``; this is classification ONLY, never prevention.
        * In-process Python workers cannot be forcibly killed.  They are tracked
          by :class:`ThreadContainment` and detected post-rollback (A5-C).

        Every result is gated on ``authorization_safe`` before the caller can
        turn it into evidence (A5-B fail-closed).
        """
        self.check()
        # R6 P0-3 guard: reject encoded/wrapped unsupported execution identities
        # (hex/base64 WSL payloads) before the managed runner launches them.
        from .r6_authority import r6_execution_guard
        r6_execution_guard(command)
        from .runner import run_managed  # R2-05/R2-08: managed, tree-terminating runner
        deadline = self.deadline
        if timeout is not None:
            own = time.monotonic() + timeout
            deadline = own if deadline is None else min(deadline, own)
        if deadline is not None and time.monotonic() >= deadline:
            raise EcoTimeoutError("no remaining deadline for spawned verification tool")
        # run_managed enforces the monotonic deadline, polls cancellation
        # mid-flight, and CONFIRMS process-tree termination before returning.
        result = run_managed(
            command, log_dir=log_dir, name=name, deadline=deadline,
            cancelled=self.cancelled, cwd=cwd,
        )
        self.check()
        containment = str(getattr(result, "containment", "none") or "none")
        # R2-05/R4: never claim a hard deadline/containment on an unconfirmed path.
        if containment in ("tree_attempt_unconfirmed", "wrapper_orphan_detected",
                           "unsupported", "cooperative_only"):
            _raise_uncontained(result, check_id=name, prefix="managed verification tool")
        # A5-B: the authoritative fail-closed gate.  ``authorization_safe`` is
        # True only for a clean, host-contained run; ``wsl_uncontained`` is the
        # explicit WSL hook.  An orphan, an unconfirmed tree kill, a cooperative
        # only run or a WSL path can never qualify evidence.
        if (
            getattr(result, "wsl_uncontained", False)
            or not getattr(result, "authorization_safe", False)
        ):
            _raise_uncontained(result, check_id=name, prefix="authorization gate")
        self.last_managed_result = result
        return result


@dataclass(frozen=True)
class ManagedToolSpec:
    """One production verification tool, launched only via ``ctx.spawn``.

    ``command`` is passed verbatim to :func:`assistant.runner.run_managed`.
    ``status_on_success`` is the evidence status when the tool exits 0;
    ``status_on_failure`` is used for any non-zero exit.  A non-PASS success
    status is allowed so a provider can emit an authoritative FAIL/INCONCLUSIVE
    verdict from a real tool without fabricating evidence.
    """

    check_id: str
    command: tuple[str, ...] | str
    status_on_success: str = "PASS"
    status_on_failure: str = "FAIL"
    reason: str = "managed tool verification"
    analysis_view: str = "MAPPED_NETLIST"
    tool_name: str = "managed-tool"
    tool_version: str = "1.0"
    timeout_s: float | None = None
    #: Workspace-relative files the tool writes; bound to the evidence as
    #: reports when present.  Logs from the managed runner are always bound.
    report_files: tuple[str, ...] = ()


def managed_tool_verification(
    specs: Sequence[ManagedToolSpec],
    *,
    log_dir: str,
    cwd: str | os.PathLike[str] | None = None,
) -> Callable[[VerificationContext], Iterable[Evidence]]:
    """Build the SUPPORTED production verify() provider backed by managed execution.

    This is the concrete bridge that makes R2-08's ``spawn()`` real: every check
    is launched through :meth:`VerificationContext.spawn`, the framework's only
    tool-launch API, which routes to ``runner.run_managed`` with an enforced
    monotonic deadline, cancellation polling and confirmed process-tree
    termination.  A tool whose result is not ``authorization_safe`` (WSL
    uncontained, orphan, unconfirmed tree kill, cooperative-only) is refused by
    ``spawn()`` before any evidence is emitted — see A5-B/A5-C in FINDINGS.md.

    HONEST CLASSIFICATION: hard containment applies only to the direct
    Windows/POSIX child tree with confirmed PIDs.  WSL2 Linux descendants and
    in-process Python threads are UNSUPPORTED/cooperative-only: they are
    detected and fail closed, never prevented.
    """
    ordered = tuple(specs)
    if not ordered:
        raise ValueError("managed_tool_verification needs at least one tool spec")
    seen: set[str] = set()
    for spec in ordered:
        if not str(spec.check_id).strip():
            raise ValueError("empty check_id in managed verification spec")
        if spec.check_id in seen:
            raise ValueError(f"duplicate managed verification check {spec.check_id!r}")
        seen.add(spec.check_id)

    def verify(ctx: VerificationContext):
        for spec in ordered:
            ctx.check()
            result = ctx.spawn(
                spec.command, log_dir=log_dir, name=spec.check_id,
                timeout=spec.timeout_s, cwd=cwd,
            )
            # Defence in depth: spawn already enforces this; re-check at the
            # evidence boundary so no future refactor can bypass the gate.
            if (
                getattr(result, "wsl_uncontained", False)
                or not getattr(result, "authorization_safe", False)
            ):
                _raise_uncontained(result, check_id=spec.check_id, prefix="managed evidence")
            code = result.exit_code
            if code is None:
                raise EcoVerificationError(
                    f"managed tool {spec.check_id!r} returned no exit code: "
                    f"{_managed_containment_detail(result)}",
                    status="INCONCLUSIVE",
                )
            code = int(code)
            status = spec.status_on_success if code == 0 else spec.status_on_failure
            logs = [
                p for p in (result.combined_log, result.stdout_log, result.stderr_log)
                if p and os.path.isfile(p)
            ]
            reports: list[str] = []
            for rel in spec.report_files:
                path = os.path.join(str(cwd), rel) if cwd is not None else rel
                if os.path.isfile(path):
                    reports.append(path)
            _ev = Evidence.from_run(
                check_id=spec.check_id,
                status=status,
                reason=f"{spec.reason} (managed, containment={result.containment})",
                command=list(result.command) or (
                    [spec.command] if isinstance(spec.command, str) else list(spec.command)
                ),
                exit_code=code,
                analysis_view=spec.analysis_view,
                tool_versions={spec.tool_name: spec.tool_version},
                logs=logs,
                reports=reports,
                duration_s=result.duration_s,
            )
            # R6.2: bind the MAC-bound structural ExecutionProof to the evidence.
            # The controller mints an authorizing envelope ONLY when this is
            # present and TRUSTED; caller-supplied fields cannot forge it.
            _proof = getattr(result, "execution_proof", None) or getattr(result, "proof", None)
            if _proof is not None:
                _pd = _proof.to_dict() if hasattr(_proof, "to_dict") else dict(_proof)
                object.__setattr__(_ev, "execution_proof", _pd)
            yield _ev
            ctx.check()

    return verify


# TR-01: a standard-cell design CAN have placed/routed geometry. Physical DRC
# applicability is decided by ``eco_physical_implementation_present`` (a recorded
# fact), never by NO-MACROS or by the caller's description.
_PHYSICAL_NA_REASON = (
    "NA-RULE:NO-PHYSICAL-IMPLEMENTATION no placed or routed physical "
    "implementation exists for this ECO candidate; physical verification is "
    "deferred, not passed"
)
# Public alias for tests/callers that must bind evidence to the registered rule.
PHYSICAL_DRC_NA_REASON = _PHYSICAL_NA_REASON

#: Required check sets per change class (no applicability encoded here).
CHANGE_CLASS_POLICY: dict[str, tuple[CheckRequirement, ...]] = {
    "logic_optimization": (
        CheckRequirement("eco_lec"), CheckRequirement("eco_sta"),
        CheckRequirement("eco_physical_drc"),
    ),
    "rtl_behavior": (
        CheckRequirement("eco_lec"), CheckRequirement("eco_sta"),
        CheckRequirement("eco_physical_drc"),
    ),
    "interface_latency": (
        CheckRequirement("eco_lec"), CheckRequirement("eco_sta"),
        CheckRequirement("eco_physical_drc"),
    ),
    "clock_definition": (
        CheckRequirement("eco_lec"), CheckRequirement("eco_sta"),
        CheckRequirement("eco_physical_drc"),
    ),
    "timing_exception": (
        CheckRequirement("eco_lec"), CheckRequirement("eco_sta"),
        CheckRequirement("eco_physical_drc"),
    ),
    "power_intent": (
        CheckRequirement("eco_lec"), CheckRequirement("eco_sta"),
        CheckRequirement("eco_physical_drc"),
    ),
    "verification_exclusion": (
        CheckRequirement("eco_lec"), CheckRequirement("eco_sta"),
        CheckRequirement("eco_physical_drc"),
    ),
    # Explicit inapplicable policy: the only class for which verification may
    # be omitted, and only if the workspace is byte-identical afterwards.
    "no_op": (),
}

#: What each target stage requires. These are declarations of required work,
#: not pretend providers: a missing capability still blocks qualification.
STAGE_CHANGE_REQUIREMENTS: dict[EcoStage, str] = {
    EcoStage.RTL: "formal equivalence plus appropriately qualified timing evidence; physical qualification pending",
    EcoStage.GENERIC: "formal equivalence plus appropriately qualified timing evidence; physical qualification pending",
    EcoStage.MAPPED: "legality, connectivity and applicable timing for cell resizing/buffer insertion; later physical checks required",
    EcoStage.PLACE: "placement legality, connectivity and applicable timing; later physical checks required",
    EcoStage.ROUTE: "affected placement/routing repair, extraction/timing, connectivity, DRC and equivalence",
    EcoStage.FILL: "final physically qualified candidate: reuse routed checks with final extraction, DRC, LVS and STA",
}


def policy_for(change_class: str) -> tuple[CheckRequirement, ...]:
    """Return the registered required-check set (no implicit default)."""
    try:
        return CHANGE_CLASS_POLICY[change_class]
    except (KeyError, TypeError) as exc:
        raise EcoPolicyError(
            f"no registered verification policy for change class {change_class!r}; "
            "known classes: " + ", ".join(sorted(CHANGE_CLASS_POLICY))
        ) from exc


def applicability_for(
    change_class: str,
    scope: EcoChangeScope,
    design_facts: Mapping[str, object] | None = None,
) -> tuple[CheckApplicability, ...]:
    """Resolve required checks against recorded implementation facts (TR-01).

    ``has_macros=False`` never exempts physical verification. The only accepted
    exemption is a recorded ``eco_physical_implementation_present=False`` fact
    for a netlist-only experiment, corroborated by the controller's registered
    applicability rule.
    """
    out: list[CheckApplicability] = []
    for req in policy_for(change_class):
        if req.check_id != "eco_physical_drc":
            out.append(CheckApplicability(
                req.check_id, True,
                f"mandatory for change class {change_class!r}; "
                f"target stage {scope.target_stage.value}: "
                + STAGE_CHANGE_REQUIREMENTS[scope.target_stage],
            ))
            continue
        if scope.physical_implementation_present:
            out.append(CheckApplicability(
                "eco_physical_drc", True,
                f"physical implementation present at stage {scope.target_stage.value}; "
                "routed DRC required even when the design contains no macros",
            ))
        else:
            recorded = (design_facts or {}).get("eco_physical_implementation_present")
            # Controller facts are {value, source, evidence_ref}; accept a bare
            # value too so callers can pass a plain mapping.
            present = recorded.get("value") if isinstance(recorded, Mapping) else recorded
            if present is not None and bool(present) is not False:
                raise EcoPolicyError(
                    "conflicting recorded facts: scope declares no physical "
                    "implementation but the controller records "
                    f"eco_physical_implementation_present={present!r}"
                )
            out.append(CheckApplicability(
                "eco_physical_drc", False,
                "netlist-only exploration; physical qualification remains pending",
                na_reason=_PHYSICAL_NA_REASON,
            ))
    return tuple(out)


def _as_finite_float(value: object, label: str) -> float:
    """Accept only real, finite numbers (no bool, NaN, infinity or text)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"{label}: malformed metric value {value!r} "
            f"({type(value).__name__}); a finite number is required"
        )
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label}: NaN/infinity is not a valid metric value")
    return number


@dataclass
class Authorization:
    change_class: str
    scope: str
    reason: str
    authorized_by: str
    created_utc: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        # An authorization without a real reason/approver is not an
        # authorization - validate at construction, not at use time.
        if len(str(self.reason).strip()) < 20:
            raise ValueError("authorization reason must be at least 20 characters")
        if not str(self.authorized_by).strip():
            raise ValueError("authorization must record who authorized it")
        if not str(self.scope).strip():
            raise ValueError("authorization must declare an explicit scope")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProtectedMetric:
    name: str
    baseline: float
    tolerance: float = 0.0
    direction: str = "higher_is_better"  # or "lower_is_better" / "equal"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("protected metric name must be a non-empty string")
        _as_finite_float(self.baseline, f"{self.name}.baseline")
        tolerance = _as_finite_float(self.tolerance, f"{self.name}.tolerance")
        if tolerance < 0:
            raise ValueError(f"{self.name}.tolerance must not be negative")
        if self.direction not in ("higher_is_better", "lower_is_better", "equal"):
            raise ValueError(
                f"{self.name}: unknown direction {self.direction!r}"
            )

    def violated(self, value: float) -> bool:
        value = _as_finite_float(value, self.name)
        if self.direction == "higher_is_better":
            return value < self.baseline - self.tolerance
        if self.direction == "lower_is_better":
            return value > self.baseline + self.tolerance
        return abs(value - self.baseline) > self.tolerance


@dataclass(frozen=True)
class VerifiedCandidate:
    """R5 consumed-state binding: hash-verified view of the accepted snapshot.

    Downstream consumes bytes through ``read_bytes`` which re-hashes against the
    bound revision on EVERY read. A mutable path alone is never authoritative:
    if bytes are replaced after acceptance, reads fail closed instead of handing
    mutated content to a consumer.
    """

    revision_id: str
    snapshot: Mapping[str, str]
    root: str
    # R6 Stage 5: immutable verified bytes captured at acceptance. When present,
    # read_bytes returns these exact bytes — no path re-read, no mutable dict,
    # no TOCTOU. The legacy snapshot/root path remains only as a compatibility
    # fallback for callers that construct this class without contents.
    contents: Mapping[str, object] = field(default_factory=dict, repr=False, compare=False)

    def read_bytes(self, rel: str) -> bytes:
        safe = str(rel).replace("\\", "/")
        _immutable = self.contents.get(safe)
        if _immutable is not None:
            return _immutable.read_bytes()
        digest = self.snapshot.get(safe)
        if digest is None:
            raise EcoError(f"consumed-state binding: undeclared file {rel!r}")
        root_real = os.path.realpath(self.root)
        target = os.path.realpath(os.path.join(self.root, safe))
        try:
            if os.path.commonpath([root_real, target]) != root_real:
                raise EcoError(f"consumed-state binding: path escapes snapshot {rel!r}")
        except ValueError:
            raise EcoError(f"consumed-state binding: path escapes snapshot {rel!r}")
        with open(target, "rb") as f:
            data = f.read()
        if sha256_bytes(data) != digest:
            raise EcoError(
                f"consumed-state binding: {rel!r} mutated after verification "
                "(refusing to hand mutated bytes to downstream)"
            )
        return data

    def verified_paths(self) -> tuple[str, ...]:
        return tuple(sorted(self.snapshot))


@dataclass
class ExperimentResult:
    experiment_id: str
    change_class: str
    description: str
    status: str  # PASS | FAIL | INCONCLUSIVE | ROLLED_BACK | BLOCKED
    reason: str
    protected: dict = field(default_factory=dict)
    rollback_performed: bool = False
    rollback_verified: bool = False
    attempt_number: int = 1
    evidence_ids: list[str] = field(default_factory=list)
    verification: dict = field(default_factory=dict)
    created_utc: str = field(default_factory=utc_now)
    # TR-02/TR-03: binding to the exact verified candidate and deadline facts.
    candidate_revision: str | None = None
    candidate_snapshot: dict = field(default_factory=dict)
    change_scope: dict = field(default_factory=dict)
    measured_verification_s: float | None = None
    deadline_exceeded: bool = False
    late_mutation_detected: bool = False
    worker_uncontained: bool = False
    dependency_revision: str | None = None
    downstream_failed: bool = False
    downstream_error: str = ""
    qualification_class: str = "PRODUCTION"
    # R2-04: absolute path to the verified, read-only copy of the accepted
    # candidate. Downstream consumers are handed this immutable snapshot (which
    # matches ``candidate_revision``) rather than the live, mutable workspace.
    immutable_snapshot_dir: str = ""
    # R4: unique identity of the transaction that owns this durable record.
    # An amendment may only replace the record carrying its own token; this
    # prevents a re-entrant nested acceptance from redirecting it (A4-B).
    transaction_token: str = ""
    consumed_state: object | None = field(default=None, repr=False, compare=False)
    # R6.1 P0-A: explicit, semantically distinct cancellation marker. It is
    # True only when the authoritative commit guard latched a cancellation; a
    # deadline or mutation terminal leaves it False.
    cancelled: bool = False

    def to_dict(self) -> dict:
        # R6 Stage 5: consumed_state holds immutable VerifiedContent bytes —
        # runtime-only and not JSON-serializable. Persist a safe identity summary.
        _cs = self.consumed_state
        try:
            object.__setattr__(self, "consumed_state", None)
            d = asdict(self)
        finally:
            object.__setattr__(self, "consumed_state", _cs)
        if _cs is not None:
            d["consumed_state"] = {
                "revision_id": getattr(_cs, "revision_id", None),
                "paths": sorted(getattr(_cs, "snapshot", {})),
            }
        return d


# ---------------------------------------------------------------------------
# A6-01 (R4): physical-artifact applicability is derived from file CONTENT and
# dimensions, never from a fixed suffix/marker vocabulary.  The inherited
# scanner accepted ``top.oas``, ``top.gds.gz`` and ``routing_plan.txt`` as
# "netlist-only" because those names missed its whitelist, even though both the
# pre-apply guard and the post-apply re-derivation called the same function.
#
# Detection layers (no single layer is authoritative):
#   1. binary content signatures: GDSII HEADER records, OASIS magic, gzip magic
#      with decompression of the inner stream, and other archive magics;
#   2. declared type indicators in the bytes themselves (DEF/LEF/SPEF/routing
#      report text), matched on word boundaries;
#   3. conservative classification of layout/routing/extraction names and their
#      directory dimensions, including compound and renamed artifacts;
#   4. FAIL CLOSED: empty, malformed, unreadable, unrecognized-binary, symlink
#      and outside-workspace targets are all reported as findings.  Any finding
#      blocks the ``NA-RULE:NO-PHYSICAL-IMPLEMENTATION`` exemption.
#
# A benign finding is never emitted for a file unless its bytes are readable
# text/source with no physical indicator and its name has no physical token.
# ---------------------------------------------------------------------------

_PHYSICAL_SCAN_BYTES = 256 * 1024
_PHYSICAL_SCAN_EXCLUDED_DIRS = frozenset({".git", "__pycache__", ".pytest_cache"})

_OASIS_MAGIC = b"%SEMI-OASIS"
_GZIP_MAGIC = b"\x1f\x8b"
_BZIP2_MAGIC = b"BZh"
_XZ_MAGIC = b"\xfd7zXZ\x00"
_ZIP_MAGIC = b"PK\x03\x04"

#: Strong name dimensions: the name alone is enough to require physical checks.
_PHYSICAL_STRONG_TOKENS = frozenset({
    "gds", "gdsii", "oas", "oasis", "def", "lef", "odb", "spef", "sfp",
    "kdb", "guide", "route", "routing", "routed", "floorplan", "layout",
    "place", "placement", "cts", "pdn", "drc", "lvs", "extract",
    "parasitic", "antenna", "irdrop",
})
#: Weak dimensions: plausible physical collateral, not proof of layout.
_PHYSICAL_WEAK_TOKENS = frozenset({
    "db", "dat", "bin", "gz", "bz2", "xz", "zip", "via", "metal",
    "poly", "diff", "well", "track", "cell", "macro", "strm", "stream",
})

_STRONG_CONTENT_MARKERS = (
    "oasis", "gdsii", "gds2", "spef", "floorplan", "routed", "routing",
    "end design", "end lef", "place and route", "place-and-route",
    "physical implementation", "parasitic extraction",
    "extracted parasitics",
)
_WEAK_CONTENT_MARKERS = (
    "route", "placement", "place", "cts", "pdn", "drc", "lvs", "layout",
    "extraction", "extract", "metal", "via", "track", "antenna",
    "ir drop", "beginlib", "endlib",
)


@dataclass(frozen=True)
class ArtifactFinding:
    """One workspace entry whose physical classification is not benign."""

    rel: str
    kind: str  # "physical" | "uncertain"
    reason: str


@dataclass(frozen=True)
class PhysicalAssessment:
    """Result of scanning a candidate workspace for physical collateral."""

    findings: tuple[ArtifactFinding, ...]

    @property
    def hits(self) -> list[str]:
        return [f.rel for f in self.findings]

    @property
    def blocks(self) -> bool:
        return bool(self.findings)

    def summary(self, limit: int = 10) -> str:
        return ", ".join(
            f"{f.rel} ({f.kind}: {f.reason})" for f in self.findings[:limit]
        )


def _scan_tokens(text: str) -> set[str]:
    """Lower-case alphanumeric dimensions of a name/path component."""
    return {part for part in re.split(r"[^a-z0-9]+", str(text).lower()) if part}


def _has_marker(text: str, marker: str) -> bool:
    """Word-boundary marker match; non-alphanumeric markers use containment."""
    if marker[:1].isalnum() and marker[-1:].isalnum():
        return re.search(
            r"(?<![a-z0-9])" + re.escape(marker) + r"(?![a-z0-9])", text
        ) is not None
    return marker in text


def _content_markers(head: bytes) -> tuple[set[str], set[str]]:
    text = head.decode("utf-8", errors="ignore").lower()
    strong = {m for m in _STRONG_CONTENT_MARKERS if _has_marker(text, m)}
    weak = {m for m in _WEAK_CONTENT_MARKERS if _has_marker(text, m)}
    return strong, weak


def _is_gdsii(head: bytes) -> bool:
    """GDSII stream HEADER record: 2-byte length (6), type 0x00, dtype 0x02."""
    return (
        len(head) >= 6
        and int.from_bytes(head[:2], "big") == 6
        and head[2] == 0x00
        and head[3] == 0x02
    )


def _inner_stream(head: bytes) -> bytes | None:
    """Best-effort decompression of a gzip head (bounded by the caller)."""
    if not head.startswith(_GZIP_MAGIC):
        return None
    try:
        return gzip.decompress(head)
    except Exception:  # noqa: BLE001 - malformed compression is evidence, not an error
        return None


def _looks_binary(head: bytes) -> bool:
    sample = head[:4096]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    printable = sum(1 for b in sample if 32 <= b <= 126 or b in (9, 10, 13))
    return printable / len(sample) < 0.85


def _path_within(root: str, path: str) -> bool:
    try:
        return os.path.commonpath([root, path]) == root
    except (ValueError, OSError):  # different drives / incomparable paths
        return False


def _looks_like_coordinate_table(head: bytes) -> bool:
    """R5-A7-01: numeric/tabular layout data (coordinate rows) in a text file.

    Standard-cell layouts can be serialized as text coordinate tables (X/Y
    positions, widths, layer numbers). Treat repeated numeric tuple rows as
    physical collateral rather than benign source text.
    """
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        return False
    rows = 0
    for line in text.splitlines()[:200]:
        parts = line.split()
        if len(parts) >= 3:
            numeric = 0
            for p in parts[:6]:
                try:
                    float(p)
                    numeric += 1
                except ValueError:
                    continue
            if numeric >= 3:
                rows += 1
                if rows >= 5:
                    return True
    return False


def _looks_like_encoded_blob(head: bytes) -> bool:
    """R5-A7-01: base64/hex-encoded binary (e.g. an encoded GDS/OASIS stream)."""
    try:
        text = head.decode("ascii").strip()
    except (UnicodeDecodeError, AttributeError):
        return False
    if len(text) < 64:
        return False
    sample = text[:256]
    import string as _string
    allowed = set(_string.ascii_letters + _string.digits + "+/=")
    if not sample or not set(sample) <= allowed:
        return False
    # High-entropy-looking base64 with little whitespace => encoded binary.
    distinct = len(set(sample))
    return distinct >= 24 and ("=" in sample or len(sample) >= 128)


def _classify_artifact(rel: str, full: str, workspace_real: str) -> tuple[str, str]:
    """Classify one candidate entry as ``benign``, ``physical`` or ``uncertain``."""
    name = os.path.basename(full)
    tokens = _scan_tokens(name)
    for part in os.path.dirname(rel).replace("\\", "/").split("/"):
        if part and part != ".":
            tokens |= _scan_tokens(part)
    strong_names = tokens & _PHYSICAL_STRONG_TOKENS
    weak_names = tokens & _PHYSICAL_WEAK_TOKENS

    is_link = os.path.islink(full)
    target_outside = False
    read_path = full
    if is_link:
        target_real = os.path.realpath(full)
        target_outside = not _path_within(workspace_real, target_real)
        tokens |= _scan_tokens(os.path.basename(target_real))
        strong_names = tokens & _PHYSICAL_STRONG_TOKENS
        weak_names = tokens & _PHYSICAL_WEAK_TOKENS
        read_path = target_real
        if not os.path.isfile(read_path):
            return (
                "uncertain",
                "symlink/junction does not resolve to an in-workspace file; "
                "physical collateral cannot be ruled out",
            )

    try:
        with open(read_path, "rb") as handle:
            head = handle.read(_PHYSICAL_SCAN_BYTES)
    except OSError as exc:
        return "uncertain", f"unreadable candidate file ({type(exc).__name__})"

    if not head:
        if strong_names:
            return "physical", "empty file with physical name dimension"
        return "uncertain", "empty file: content cannot rule out physical collateral"

    if _OASIS_MAGIC in head[:32]:
        return "physical", "OASIS magic bytes"
    if _is_gdsii(head):
        return "physical", "GDSII HEADER record signature"
    if head.startswith(_GZIP_MAGIC):
        inner = _inner_stream(head)
        if inner is not None and (
            _OASIS_MAGIC in inner[:32] or _is_gdsii(inner)
        ):
            return "physical", "compressed stream contains GDSII/OASIS"
        if strong_names:
            return "physical", "compressed artifact with physical name dimension"
        return "uncertain", "compressed artifact; inner content could not be verified"
    if head.startswith((_BZIP2_MAGIC, _XZ_MAGIC, _ZIP_MAGIC)):
        return "uncertain", "archive/compressed artifact; inner content not verified"

    strong_markers, weak_markers = _content_markers(head)
    if strong_markers:
        return "physical", "physical content markers: " + ", ".join(sorted(strong_markers))
    if strong_names:
        return "physical", "physical name dimensions: " + ", ".join(sorted(strong_names))

    weak = weak_markers | weak_names
    if len(weak) >= 2:
        return "physical", "combined physical indicators: " + ", ".join(sorted(weak))
    if weak:
        return "uncertain", "weak physical indicator: " + ", ".join(sorted(weak))
    if is_link and target_outside:
        return "uncertain", "symlink resolves outside the workspace; target not vouched"
    if _looks_binary(head):
        return "uncertain", "unrecognized binary content; physical classification excluded"
    if _looks_like_encoded_blob(head):
        return "uncertain", "encoded/base64 text may contain physical layout; fail closed"
    if _looks_like_coordinate_table(head):
        return "uncertain", "numeric coordinate-table text may be physical layout; fail closed"
    return "benign", "clearly non-physical text/source content"


def _assess_physical_artifacts(workspace: str) -> PhysicalAssessment:
    """Walk *workspace* and fail closed on any non-benign classification."""
    findings: list[ArtifactFinding] = []
    errors: list[str] = []

    def onerror(exc: OSError) -> None:
        errors.append(str(exc))

    try:
        workspace_real = os.path.realpath(workspace)
        for dirpath, dirnames, filenames in os.walk(workspace, onerror=onerror):
            dirnames[:] = [
                d for d in dirnames if d not in _PHYSICAL_SCAN_EXCLUDED_DIRS
            ]
            # A symlinked directory (incl. a Windows junction) may hide any
            # number of physical files outside the walked tree. Never follow it.
            for name in list(dirnames):
                full_dir = os.path.join(dirpath, name)
                if os.path.islink(full_dir):
                    rel_dir = os.path.relpath(full_dir, workspace).replace(os.sep, "/")
                    findings.append(ArtifactFinding(
                        rel_dir, "uncertain",
                        "symlink/junction directory; physical collateral not scanned",
                    ))
                    dirnames.remove(name)
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, workspace).replace(os.sep, "/")
                try:
                    kind, reason = _classify_artifact(rel, full, workspace_real)
                except Exception as exc:  # noqa: BLE001 - fail closed on any surprise
                    kind, reason = "uncertain", (
                        f"classification error ({type(exc).__name__})"
                    )
                # R6 Stage 6: content-signature classification. The R5 heuristic
                # defaults unknown content to benign; the new classifier fails
                # closed on encoded/compressed/unknown physical data.
                try:
                    from .applicability import classify as _r6_classify, Applicability as _App
                    with open(full, "rb") as fh:
                        _data = fh.read(1 << 20)
                    _app = _r6_classify(full, _data)
                    # Only POSITIVE physical-content detection is added here.
                    # UNCERTAIN/UNSUPPORTED from the new classifier are left to
                    # the existing _classify_artifact gate, which already fails
                    # closed on uncertain findings; treating every ordinary text
                    # file as uncertain would make the whole workspace unscannable.
                    if _app == _App.SUPPORTED_APPLICABLE:
                        kind = "physical"
                        reason = "R6 content-signature: physical layout detected"
                except Exception as exc:  # noqa: BLE001
                    kind, reason = "uncertain", (
                        f"R6 classification error ({type(exc).__name__})"
                    )
                if kind != "benign":
                    findings.append(ArtifactFinding(rel, kind, reason))
    except Exception as exc:  # noqa: BLE001 - an unscannable workspace is uncertain
        errors.append(f"workspace scan failed: {type(exc).__name__}: {exc}")

    for err in errors:
        findings.append(ArtifactFinding(
            "<workspace-scan>", "uncertain",
            "workspace could not be fully scanned: " + err[:200],
        ))
    return PhysicalAssessment(tuple(findings))


class EcoManager:
    """Runs bounded ECO experiments with checkpoint/rollback discipline."""

    MAX_FAILURES_BEFORE_REASSESSMENT = 3
    _ABSENT = "<absent>"
    _UNREADABLE = "<unreadable>"

    def __init__(
        self,
        workspace: str,
        controller: GateController,
        *,
        checkpoint_dir: str | None = None,
        experiment_log: str | None = None,
    ):
        self.workspace = str(workspace)
        self.controller = controller
        os.makedirs(self.workspace, exist_ok=True)
        self.workspace_real = os.path.realpath(self.workspace)
        # checkpoint_dir / experiment_log are trusted, caller-declared roots.
        # All paths *derived* from them are validated to stay inside them.
        self.checkpoint_dir = checkpoint_dir or os.path.join(
            workspace, ".eco_checkpoints"
        )
        self.experiment_log = experiment_log or os.path.join(
            workspace, ".eco_experiments.json"
        )
        os.makedirs(self.checkpoint_dir, exist_ok=True)
        self._authorizations: list[Authorization] = []
        self._history: list[dict] = []
        self._consecutive_failures = 0
        self._attempt = 0
        # R2-01: the guard for the transaction currently executing on this
        # manager (linked to any parent for re-entrant acceptance).
        self._active_guard: _AcceptanceGuard | None = None
        # R4: experiment_id -> transaction_token for IN-FLIGHT transactions.
        # A duplicate/re-entrant acceptance for the same caller-visible id is
        # refused; distinct ids may nest (they receive distinct tokens and
        # their records are independent).
        self._transactions_lock = threading.Lock()
        self._active_transactions: dict[str, str] = {}
        self._load_log()

    # -- path safety ----------------------------------------------------------
    def _safe_rel(self, rel: object) -> str:
        """Validate a workspace-relative path (no absolute/traversal form)."""
        if not isinstance(rel, str) or not rel.strip():
            raise ValueError("path must be a non-empty relative string")
        if "\x00" in rel:
            raise ValueError("path contains a NUL byte")
        normal = rel.replace("\\", "/")
        if normal.startswith("/") or (len(normal) > 1 and normal[1] == ":"):
            raise ValueError(f"absolute path is not allowed: {rel!r}")
        parts = normal.split("/")
        if any(p in ("", ".", "..") for p in parts):
            raise ValueError(f"path must stay inside the workspace: {rel!r}")
        return "/".join(parts)

    @staticmethod
    def _require_under(path: str, root: str, label: str) -> str:
        root_real = os.path.realpath(root)
        path_real = os.path.realpath(path)
        try:
            common = os.path.commonpath([root_real, path_real])
        except ValueError as exc:
            raise ValueError(f"{label} escapes {root!r}: {path!r}") from exc
        if common != root_real:
            raise ValueError(f"{label} escapes {root!r}: {path!r}")
        return path_real

    def _validate_declared_files(self, files: Sequence[str]) -> list[str]:
        out: list[str] = []
        for rel in files:
            safe = self._safe_rel(rel)
            self._require_under(
                os.path.join(self.workspace, safe), self.workspace, "changed file path"
            )
            out.append(safe)
        if not out:
            raise ValueError("at least one changed file must be declared")
        return out

    def _excluded_rels(self) -> set[str]:
        excluded = {".git", "__pycache__", ".pytest_cache"}
        for path in (self.checkpoint_dir, self.experiment_log):
            try:
                self._require_under(path, self.workspace, "internal path")
            except ValueError:
                continue  # trusted root intentionally outside the workspace
            rel = os.path.relpath(os.path.realpath(path), self.workspace_real)
            excluded.add(rel.replace(os.sep, "/"))
        return excluded

    @staticmethod
    def _is_excluded(rel: str, excluded: set[str]) -> bool:
        return any(rel == ex or rel.startswith(ex + "/") for ex in excluded)

    # -- whole-workspace snapshot --------------------------------------------
    def snapshot_workspace(self) -> dict[str, str]:
        """Hash every workspace file (excluding VCS/cache/internal ECO paths)."""
        excluded = self._excluded_rels()
        out: dict[str, str] = {}
        for dirpath, dirnames, filenames in os.walk(self.workspace):
            rel_dir = os.path.relpath(dirpath, self.workspace).replace(os.sep, "/")
            if rel_dir == ".":
                rel_dir = ""
            dirnames[:] = [
                d for d in dirnames
                if not self._is_excluded(f"{rel_dir}/{d}".lstrip("/"), excluded)
            ]
            for name in filenames:
                rel = f"{rel_dir}/{name}".lstrip("/")
                if self._is_excluded(rel, excluded):
                    continue
                try:
                    out[rel] = sha256_file(os.path.join(dirpath, name))
                except OSError:
                    out[rel] = self._UNREADABLE
        return out

    # -- immutable accepted snapshot (R2-04) ---------------------------------
    @staticmethod
    def _hash_dir(root: str) -> dict[str, str]:
        """Hash every file under ``root`` keyed by its relative POSIX path."""
        out: dict[str, str] = {}
        for dirpath, _dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
            if rel_dir == ".":
                rel_dir = ""
            for name in filenames:
                rel = f"{rel_dir}/{name}".lstrip("/")
                try:
                    out[rel] = sha256_file(os.path.join(dirpath, name))
                except OSError:
                    out[rel] = EcoManager._UNREADABLE
        return out

    @staticmethod
    def _freeze_tree(root: str) -> None:
        """Best-effort read-only marking of an immutable snapshot tree."""
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                path = os.path.join(dirpath, name)
                try:
                    mode = os.stat(path).st_mode
                    os.chmod(path, mode & ~(
                        stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
                    ))
                except OSError:
                    pass  # verification, not chmod, is the authoritative seal

    @staticmethod
    def _rmtree_readonly(root: str) -> None:
        """Remove a previously frozen snapshot tree, restoring write bits."""
        def _onerror(func, path, _exc_info):
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
                func(path)
            except OSError:
                pass

        shutil.rmtree(root, onerror=_onerror)

    def _materialize_immutable_snapshot(self, revision: CandidateRevision) -> str:
        """Create a verified, read-only copy of the accepted candidate.

        The copy is stored under the trusted ``checkpoint_dir`` (excluded from
        workspace snapshots) and handed to downstream via
        ``ExperimentResult.immutable_snapshot_dir``. Every file is re-hashed
        after copy and must match the bound revision; a concurrent writer that
        mutates a file during materialization therefore fails closed instead of
        being silently baked into the "immutable" snapshot (R2-04).
        """
        dest = os.path.join(
            self.checkpoint_dir, f"accepted_{revision.revision_id}"
        )
        if os.path.isdir(dest):
            self._rmtree_readonly(dest)
        os.makedirs(dest, exist_ok=True)
        for rel, digest in sorted(revision.snapshot.items()):
            if digest in (self._ABSENT, self._UNREADABLE):
                raise EcoRejected(
                    "cannot materialize immutable accepted snapshot: bound "
                    f"revision contains an absent/unreadable file: {rel}",
                    status="FAIL",
                )
            safe = self._safe_rel(rel)
            src = os.path.join(self.workspace, safe)
            target = os.path.join(dest, safe)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(src, target)
        copied = self._hash_dir(dest)
        if dict(copied) != dict(revision.snapshot):
            bad = sorted(
                rel for rel in set(copied) | set(revision.snapshot)
                if copied.get(rel) != revision.snapshot.get(rel)
            )
            raise EcoRejected(
                "immutable accepted snapshot does not match the bound revision "
                "(concurrent writer during snapshot materialization): "
                + ", ".join(bad[:20]),
                status="FAIL",
            )
        self._freeze_tree(dest)
        return dest

    def _accepted_integrity_violations(
        self, revision: CandidateRevision, immutable_dir: str
    ) -> list[str]:
        """Re-validate the WHOLE accepted binding after downstream (R2-04/R4).

        Re-hashes the live workspace and the immutable copy against the single
        bound candidate revision, and RE-ESTABLISHES the pinned dependency
        closure against ``revision.dependency_revision`` (A4-C / A2-01). The
        dependency authority is the frozen revision captured at acceptance,
        not the mutable result handed to downstream, so this check is
        authoritative even if a downstream callback flips an injectable seam
        or rewrites ``result.dependency_revision``.

        Any divergence is reported so acceptance can fail closed rather than
        claim a stale PASS. The signature is intentionally unchanged: callers
        (including test seams) pass only ``(revision, immutable_dir)``.
        """
        problems: list[str] = []
        live = self.snapshot_workspace()
        if dict(live) != dict(revision.snapshot):
            bad = sorted(
                rel for rel in set(live) | set(revision.snapshot)
                if live.get(rel) != revision.snapshot.get(rel)
            )
            problems.append(
                "candidate mutated after acceptance (downstream consumed mutable "
                "bytes): " + ", ".join(bad[:20])
            )
        if immutable_dir:
            frozen = self._hash_dir(immutable_dir)
            if dict(frozen) != dict(revision.snapshot):
                bad = sorted(
                    rel for rel in set(frozen) | set(revision.snapshot)
                    if frozen.get(rel) != revision.snapshot.get(rel)
                )
                problems.append(
                    "immutable accepted snapshot was modified after acceptance: "
                    + ", ".join(bad[:20])
                )
        # R4-A4-C/A2-01: dependencies are part of the accepted identity. They
        # were checked pre-commit only; downstream is inside the authorization
        # window, so a post-acceptance closure change (or a failed rehash after
        # a required file is deleted) must produce a non-qualifying terminal.
        bound_deps = getattr(revision, "dependency_revision", "")
        if bound_deps:
            try:
                current_deps = stable_json_hash(self._dependency_snapshot())
            except EcoDependencyError as exc:
                problems.append(
                    "acceptance-relevant dependency closure could not be "
                    f"re-established after acceptance: {type(exc).__name__}: {exc}"
                )
            else:
                if current_deps != bound_deps:
                    problems.append(
                        "acceptance-relevant dependency changed after acceptance "
                        "(post-downstream dependency closure mismatch): expected "
                        f"{bound_deps[:12]}, got {current_deps[:12]}"
                    )
        return problems

    def _unlisted_changes(
        self, declared: Iterable[str], before: Mapping[str, str]
    ) -> list[str]:
        declared_set = set(declared)
        current = self.snapshot_workspace()
        problems: list[str] = []
        for rel, digest in current.items():
            if rel in declared_set:
                continue
            if before.get(rel, self._ABSENT) != digest:
                problems.append(rel)
        for rel in before:
            if rel not in declared_set and rel not in current:
                problems.append(rel + " (deleted)")
        return sorted(problems)

    # -- persistence ----------------------------------------------------------
    def _load_log(self) -> None:
        if os.path.isfile(self.experiment_log):
            try:
                with open(self.experiment_log, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._authorizations = [
                    Authorization(**a) for a in data.get("authorizations", [])
                ]
                self._history = [dict(e) for e in data.get("experiments", [])]
                self._consecutive_failures = int(data.get("consecutive_failures", 0))
                self._attempt = int(data.get("attempt", 0))
            except (OSError, ValueError, TypeError, KeyError):
                pass

    def _save_log(self) -> None:
        """Atomically persist history stamped with the guard's AUTHORITATIVE state.

        R6.1 P0-A: the commit-time stamp is derived from the active guard's
        *latched invalidation* — ``chain_invalidated()`` invokes ``latch()``,
        which is the only place that observes controller cancellation — NOT
        from a fresh clock read. A cancellation/deadline/mutation accepted
        before this write is serialized therefore produces a non-authorizing
        terminal record in the SAME atomic write. After the write the guard is
        re-checked; if it became invalidated inside the write window the record
        is corrected in a second atomic write (fail closed), so no durable
        qualified PASS can survive.
        """
        guard = getattr(self, "_active_guard", None)
        tok = ""
        if guard is not None:
            try:
                tok = str(getattr(guard, "transaction_token", "") or "")
            except Exception:  # noqa: BLE001 - fail closed
                tok = ""

        def _guard_state():
            """(invalidated, reason, kind, deadline_exceeded, cancelled).

            ``chain_invalidated()`` latches the guard chain first (observing
            ``_cancelled()`` and freezing the FIRST terminal reason), then
            ``chain_reason()`` reads the authoritative (reason, kind) pair.
            ``latched_deadline_exceeded()`` derives the deadline flag from that
            latched kind — never from a fresh ``time.monotonic()`` read.
            """
            if guard is None:
                return False, None, None, False, False
            try:
                invalidated = bool(guard.chain_invalidated())
                reason, kind = guard.chain_reason()
                deadline_exceeded = bool(guard.latched_deadline_exceeded())
            except Exception:  # noqa: BLE001 - any guard failure fails closed
                return True, "commit guard evaluation failed", "guard_error", False, False
            if invalidated and reason is None:
                reason, kind = "commit guard invalidation", "invalidated"
            return (
                invalidated or reason is not None,
                reason, kind, deadline_exceeded,
                bool(kind == "cancellation"),
            )

        def _write(invalidated, reason, kind, deadline_exceeded, cancelled) -> None:
            stamped: list[dict] = []
            for rec in self._history:
                r = dict(rec)
                if (
                    invalidated
                    and r.get("status") == "PASS"
                    and tok
                    and r.get("transaction_token") == tok
                ):
                    r["status"] = "FAIL"
                    r["qualification_class"] = "UNQUALIFIED"
                    r["deadline_exceeded"] = bool(deadline_exceeded)
                    # Explicit, semantically distinct cancellation marker.
                    r["cancelled"] = bool(cancelled)
                    r["invalidation_reason"] = reason or "commit guard invalidation"
                    r["invalidation_kind"] = kind or "invalidated"
                    r["reason"] = (
                        "R6 commit-time guard: "
                        + str(r["invalidation_reason"])
                        + "; " + str(r.get("reason", ""))
                    )
                    r["commit_guard_expired"] = True
                    # Reflect the downgrade in memory so no caller observes a
                    # qualified PASS that the durable ledger does not contain.
                    rec.clear()
                    rec.update(r)
                stamped.append(r)
            data = {
                "authorizations": [a.to_dict() for a in self._authorizations],
                "experiments": stamped,
                "consecutive_failures": self._consecutive_failures,
                "attempt": self._attempt,
                "updated_utc": utc_now(),
            }
            tmp = self.experiment_log + ".r6." + uuid.uuid4().hex + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.experiment_log)

        invalidated, reason, kind, deadline_exceeded, cancelled = _guard_state()
        _write(invalidated, reason, kind, deadline_exceeded, cancelled)

        # R6.1 P0-A (4): re-check the guard AFTER the atomic write. A cancel
        # takes no ECO lock and can land between the evaluation above and
        # ``os.replace``, leaving a durable PASS. RAISE HERE (inside _save_log)
        # so that _persist_record's 3-retry loop re-evaluates the guard and
        # rewrites the record as FAIL. A post-commit amendment above this layer
        # would leave the durable PASS intact and violate the invariant.
        inv2, reason2, kind2, dlex2, cancelled2 = _guard_state()
        if inv2 and not invalidated:
            # Correct the stale PASS immediately (defence in depth), THEN raise
            # so the retry loop also re-evaluates and rewrites FAIL. Correction
            # is not *relied upon* — the raise/fail-closed is authoritative.
            _write(inv2, reason2, kind2, dlex2, cancelled2)
            detail = str(reason2 or "commit guard invalidation")
            if kind2 == "cancellation":
                raise EcoCancellationError(
                    f"{detail}; cancellation observed after atomic write; "
                    "refusing durable PASS"
                )
            if kind2 == "deadline":
                raise EcoTimeoutError(
                    f"{detail}; deadline observed after atomic write; "
                    "refusing durable PASS"
                )
            raise EcoRejected(detail, status="INCONCLUSIVE")

    @staticmethod
    def _record_index(history: list[dict], transaction_token: str) -> int:
        """Find THIS transaction's own record; an empty token matches nothing."""
        if not transaction_token:
            return -1
        for idx, rec in enumerate(history):
            if rec.get("transaction_token") == transaction_token:
                return idx
        return -1

    def _persist_record(
        self, result: ExperimentResult, transaction_token: str, *,
        reject_duplicate_pass: bool = False,
    ) -> None:
        """Append or atomically replace THIS transaction's single durable record.

        Record identity is the unique ``transaction_token`` stamped on the
        record by the active guard - never ``history[-1]``. An APPEND of a
        second PASS for one token is a duplicate/re-entrant acceptance and is
        refused; an in-place amendment of the same token's PASS (e.g. adding
        ``downstream_failed`` metadata) is a legitimate single-record update.
        A non-PASS terminal always supersedes an earlier record for that token
        so there is exactly one authoritative terminal state (R4).
        """
        data = result.to_dict()
        data["transaction_token"] = transaction_token
        idx = self._record_index(self._history, transaction_token)
        if reject_duplicate_pass and idx >= 0 and data["status"] == "PASS":
            raise EcoRejected(
                f"duplicate PASS acceptance for transaction {transaction_token}",
                status="INCONCLUSIVE",
            )
        previous = self._history
        if idx >= 0:
            self._history = previous[:idx] + [data] + previous[idx + 1:]
        else:
            self._history = previous + [data]
        last_error: Exception | None = None
        for _attempt in range(3):  # transient persistence failures are retried
            try:
                self._save_log()
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
        self._history = previous
        raise last_error  # type: ignore[misc]

    def _commit(self, result: ExperimentResult) -> None:
        """Persist an attempt (including failures) before it can be returned.

        The in-memory history is only updated after the atomic file replace
        succeeds, so a failed persistence can never leave a phantom PASS in
        ``history()``. R2-01: a PASS is refused once the active guard has been
        invalidated - a retry or a re-entrant caller cannot restore it. R4: the
        record is identified by the active guard's unique transaction token.
        """
        if result.status == "PASS" and not self._pass_allowed():
            guard = self._active_guard
            reason, kind = guard.chain_reason() if guard is not None else (None, None)
            if kind == "cancellation":
                raise EcoCancellationError(reason or "run cancelled before persistence")
            raise EcoTimeoutError(reason or "deadline expired before persistence")
        guard = self._active_guard
        token = guard.transaction_token if guard is not None else uuid.uuid4().hex
        self._persist_record(result, token, reject_duplicate_pass=True)

    @staticmethod
    def _call_downstream(
        downstream_stage: Callable[[ExperimentResult], None],
        result: ExperimentResult,
    ) -> None:
        """Accept both ``f(result)`` and a zero-argument downstream callback."""
        try:
            signature = inspect.signature(downstream_stage)
            signature.bind(result)
        except (TypeError, ValueError):
            downstream_stage()
        else:
            downstream_stage(result)

    # -- authorization -------------------------------------------------------
    def authorize(self, auth: Authorization) -> Authorization:
        if auth.change_class not in RESTRICTED_CHANGE_CLASSES:
            raise ValueError(
                f"unknown restricted change class {auth.change_class!r}; known: "
                + ", ".join(sorted(RESTRICTED_CHANGE_CLASSES))
            )
        if len(auth.reason.strip()) < 20:
            raise ValueError("authorization reason must be at least 20 characters")
        if not auth.authorized_by.strip():
            raise ValueError("authorization must record who authorized it")
        self._authorizations.append(auth)
        self._save_log()
        return auth

    def has_authorization(self, change_class: str, scope: str = "*") -> bool:
        for a in self._authorizations:
            if a.change_class != change_class:
                continue
            if a.scope in ("*", scope) or scope in a.scope.split(","):
                return True
        return False

    def require_authorization(self, change_class: str, scope: str = "*") -> None:
        if change_class in RESTRICTED_CHANGE_CLASSES and not self.has_authorization(
            change_class, scope
        ):
            raise EcoAuthorizationError(
                f"change class {change_class!r} requires explicit architectural "
                "authorization (recorded Authorization with reason and author) "
                "before the experiment may run"
            )

    # -- checkpoints ---------------------------------------------------------
    def checkpoint(self, files: Sequence[str], label: str) -> str:
        """Snapshot declared files AND the whole workspace before any change."""
        declared = self._validate_declared_files(files)
        safe_label = re.sub(r"[^A-Za-z0-9_.-]", "_", str(label))[:80] or "exp"
        ckpt = os.path.join(self.checkpoint_dir, f"{int(time.time() * 1000)}_{safe_label}")
        self._require_under(ckpt, self.checkpoint_dir, "checkpoint path")
        os.makedirs(ckpt, exist_ok=True)
        full = self.snapshot_workspace()
        for rel in full:
            safe = self._safe_rel(rel)
            src = os.path.join(self.workspace, safe)
            dst = os.path.join(ckpt, safe)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        manifest = {
            "version": 2,
            "declared": {rel: full.get(rel, self._ABSENT) for rel in declared},
            "full": full,
            "created_utc": utc_now(),
        }
        with open(os.path.join(ckpt, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)
        return ckpt

    def _read_manifest(self, ckpt: str) -> dict:
        self._require_under(ckpt, self.checkpoint_dir, "rollback checkpoint path")
        path = os.path.join(ckpt, "manifest.json")
        with open(path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        if manifest.get("version") == 2:
            full = manifest["full"]
            declared = manifest["declared"]
        else:  # legacy flat manifest
            full = {k: v for k, v in manifest.items() if k != "version"}
            declared = full
        for rel in list(full) + list(declared):
            safe = self._safe_rel(rel)
            self._require_under(
                os.path.join(self.workspace, safe), self.workspace, "checkpoint file path"
            )
        return manifest

    def _restore_from_manifest(self, ckpt: str, full: Mapping[str, str]) -> list[str]:
        restored: list[str] = []
        # Restore the whole workspace to the checkpoint state: files created
        # after the checkpoint are removed as well as modified files being
        # overwritten.
        targets = dict(full)
        for rel in self.snapshot_workspace():
            targets.setdefault(rel, self._ABSENT)
        for rel, digest in targets.items():
            safe = self._safe_rel(rel)
            dst = os.path.join(self.workspace, safe)
            src = os.path.join(ckpt, safe)
            if digest == self._ABSENT:
                if os.path.isfile(dst):
                    os.remove(dst)
                    restored.append(rel + " (removed)")
            elif os.path.isfile(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                restored.append(rel)
        return restored

    def rollback(self, ckpt: str) -> list[str]:
        """Restore the *whole* checkpointed workspace; validate all paths."""
        manifest = self._read_manifest(ckpt)
        full = manifest.get("full") if manifest.get("version") == 2 else manifest
        return self._restore_from_manifest(ckpt, full)

    def _rollback_and_verify(self, ckpt: str, before: Mapping[str, str]) -> tuple[bool, str]:
        """Roll back and re-hash every file; never claim success without proof."""
        try:
            self.rollback(ckpt)
        except Exception as exc:  # noqa: BLE001 - a failed rollback must be visible
            return False, f"FAILED ({type(exc).__name__}: {exc})"
        current = self.snapshot_workspace()
        bad: list[str] = []
        for rel, digest in before.items():
            if current.get(rel, self._ABSENT) != digest:
                bad.append(rel)
        for rel in current:
            if rel not in before:
                bad.append(rel + " (unexpected)")
        if bad:
            return False, "FAILED verification: " + ", ".join(sorted(bad))
        return True, "verified by hash"

    # -- verification --------------------------------------------------------
    # -- integrity helpers (round-2 remediation) -----------------------------
    def _check_overall(self, deadline: float | None, label: str) -> None:
        """Enforce the overall deadline/cancellation at ANY acceptance boundary.

        When a transaction guard is active, the guard is the single source of
        truth: every call re-evaluates the deadline/cancellation and latches a
        terminal, irreversible invalidation on first detection. This keeps the
        decision consistent even when expiry happens inside a callback that
        does not itself poll a deadline.
        """
        guard = self._active_guard
        if guard is not None:
            guard.check(label)
            return
        # No active transaction (direct/legacy call): stateless enforcement.
        if self.controller.run_status == "CANCELLED":
            raise EcoCancellationError(f"run was cancelled {label}")
        if deadline is not None and time.monotonic() >= deadline:
            raise EcoTimeoutError(
                f"overall verification deadline expired {label}; result is invalid"
            )

    def _pass_allowed(self) -> bool:
        """R2-01: a PASS may only be persisted while no guard is invalidated."""
        guard = self._active_guard
        return guard is None or not guard.chain_invalidated()

    def _terminal_fields(
        self, reason: str, kind: str = "invalidated"
    ) -> tuple[str, bool]:
        """Latch a terminal reason on the guard and derive authoritative fields.

        The guard is the single source of truth: its FIRST latched reason (and
        kind) wins, whether it came from ``check()`` (deadline/cancel), from
        ``invalidate()`` (mutation / forbidden re-acceptance) or from a parent
        guard via the chain. ``deadline_exceeded`` is derived from the latched
        KIND, never from a later clock/deadline re-read (F4).
        """
        guard = self._active_guard
        if guard is None:
            return reason, False
        guard.invalidate(reason, kind)
        latched_reason, latched_kind = guard.chain_reason()
        return (latched_reason or reason), (latched_kind == "deadline")

    @staticmethod
    def _terminal_from(
        base: ExperimentResult, *, reason: str, deadline_exceeded: bool
    ) -> ExperimentResult:
        """Clone an accepted result into ONE consistent terminal non-PASS record."""
        data = base.to_dict()
        data.update({
            "status": "INCONCLUSIVE",
            "reason": reason,
            "deadline_exceeded": bool(deadline_exceeded),
            "qualification_class": "INVALIDATED",
        })
        return ExperimentResult(**data)

    @classmethod
    def _non_pass_terminal_from_durable(
        cls,
        base: ExperimentResult,
        durable: dict,
        revision: CandidateRevision,
        immutable_dir: str,
        downstream_error: str | None = None,
    ) -> ExperimentResult:
        """Build the caller's terminal so it MIRRORS the authoritative record.

        R6.1 P0-A: the durable record is the single source of truth for the
        post-commit state. The returned status/qualification/deadline/cancel
        fields are copied from it so a consumer can never observe a more
        authorizing result than the ledger contains (the lifecycle invariant
        ``result.status == durable.status``).
        """
        terminal = cls._terminal_from(
            base,
            reason=str(
                durable.get("invalidation_reason")
                or durable.get("reason")
                or "commit guard invalidation"
            ),
            deadline_exceeded=bool(durable.get("deadline_exceeded", False)),
        )
        status = str(durable.get("status") or "FAIL")
        terminal.status = "FAIL" if status == "PASS" else status
        terminal.qualification_class = str(
            durable.get("qualification_class") or "UNQUALIFIED"
        )
        terminal.cancelled = bool(durable.get("cancelled", False))
        cls._bind_terminal_identity(terminal, revision, immutable_dir)
        if downstream_error is not None:
            terminal.downstream_failed = True
            terminal.downstream_error = downstream_error
        return terminal

    @staticmethod
    def _mutation_terminal(
        base: ExperimentResult, *, reason: str, deadline_exceeded: bool
    ) -> ExperimentResult:
        """Clone an accepted result into a single, non-qualifying terminal record.

        Used when the bound candidate revision is found to have changed after
        acceptance (R2-04): the manager cannot guarantee downstream consumed
        immutable bytes, so it fails closed rather than claim a stale PASS.
        """
        terminal = EcoManager._terminal_from(
            base, reason=reason, deadline_exceeded=deadline_exceeded
        )
        terminal.late_mutation_detected = True
        terminal.qualification_class = "NON_QUALIFYING"
        return terminal

    @staticmethod
    def _bind_terminal_identity(
        terminal: ExperimentResult,
        revision: CandidateRevision,
        immutable_dir: str,
    ) -> ExperimentResult:
        """Restore the AUTHORIZED identity on a terminal record (R4 binding).

        Downstream receives the mutable ``accepted`` result object; a callback
        may overwrite its candidate/dependency revision fields or its
        ``immutable_snapshot_dir``. A terminal record must still describe the
        exact transaction that was authorized, using the frozen revision and
        the locally captured immutable path as the authority.
        """
        terminal.candidate_revision = revision.revision_id
        terminal.candidate_snapshot = dict(revision.snapshot)
        terminal.dependency_revision = revision.dependency_revision
        terminal.immutable_snapshot_dir = immutable_dir
        return terminal

    @staticmethod
    def _dependency_snapshot(
        *,
        _islink=None,
        _isfile=None,
        _sha256=None,
    ) -> dict[str, str]:
        """Hash the complete pinned dependency closure (R2-02 / F-10 residual).

        ``pins.json -> portable_config -> collateral`` is a **list** of
        declarations (a legacy ``dict`` form is also accepted).  Every declared
        entry's *resolved* path is hashed with SHA-256 and recorded under its
        stable ``key``; the manifest itself is hashed under ``pins.json`` so an
        edit to the declaration is bound too.  The returned mapping always has
        sorted, stable keys so ``stable_json_hash`` over it is deterministic.

        Fail-closed conditions raise :class:`EcoDependencyError` rather than
        silently shrinking the closure: malformed/empty manifest, invalid or
        duplicate entries, path traversal, absolute paths, unsafe symlinks and
        missing/unreadable ``required=true`` files.  Absent optional files are
        recorded explicitly (``<missing-optional>`` / ``<unreadable-optional>``).

        The optional keyword arguments are injectable test seams (default to
        the real OS predicates / hasher); production call sites pass none.
        """
        import json as _json
        from . import tools as _tools

        islink = _islink or os.path.islink
        isfile = _isfile or os.path.isfile
        digest = _sha256 or sha256_file

        # 1. The manifest itself is part of the closure.
        pins_path = Path(__file__).resolve().parent / "pins.json"
        try:
            raw = pins_path.read_text(encoding="utf-8")
            pins = _json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            raise EcoDependencyError(
                f"pins.json unreadable or malformed: {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(pins, dict):
            raise EcoDependencyError("pins.json: top-level value must be an object")

        out: dict[str, str] = {"pins.json": sha256_bytes(raw.encode("utf-8"))}
        resolved_paths: dict[str, str] = {}

        # 2. Structural validation of portable_config (fail closed).
        pc = pins.get("portable_config")
        if not isinstance(pc, dict):
            raise EcoDependencyError("portable_config: missing or not an object")
        fallbacks = pc.get("fallbacks")
        if not isinstance(fallbacks, dict) or not fallbacks:
            raise EcoDependencyError("portable_config.fallbacks: missing or empty")
        for fb_key, fb_val in fallbacks.items():
            if not isinstance(fb_val, str) or not fb_val.strip():
                raise EcoDependencyError(
                    f"portable_config.fallbacks.{fb_key}: non-empty string required"
                )

        collateral = pc.get("collateral")
        entries: list[tuple[str, dict]] = []
        if isinstance(collateral, list):
            if not collateral:
                raise EcoDependencyError("portable_config.collateral: empty")
            seen: set[str] = set()
            for i, item in enumerate(collateral):
                if not isinstance(item, dict):
                    raise EcoDependencyError(
                        f"portable_config.collateral[{i}]: not an object"
                    )
                key = item.get("key")
                if not isinstance(key, str) or not key.strip():
                    raise EcoDependencyError(
                        f"portable_config.collateral[{i}]: missing/invalid key"
                    )
                if key in seen:
                    raise EcoDependencyError(
                        f"portable_config.collateral[{i}]: duplicate key {key!r}"
                    )
                seen.add(key)
                entries.append((key, item))
        elif isinstance(collateral, dict):
            # Legacy/compat form: {label: path-string | entry-mapping}.
            if not collateral:
                raise EcoDependencyError("portable_config.collateral: empty")
            for label in sorted(collateral):
                item = collateral[label]
                if isinstance(item, str):
                    item = {"path": item}
                if not isinstance(item, dict) or not isinstance(label, str) or not label:
                    raise EcoDependencyError(
                        f"portable_config.collateral[{label!r}]: invalid entry"
                    )
                entries.append((label, item))
        else:
            raise EcoDependencyError(
                "portable_config.collateral: must be a non-empty list or object"
            )

        # 3. Resolve the platform roots exactly as the consuming toolchain does
        #    (env override > portable fallback > recorded default); fail closed
        #    if the resolver reports a structurally invalid source.
        cfg = _tools.resolve_platform_config(pins=pins)
        if cfg.get("portable_config_errors"):
            raise EcoDependencyError(
                "portable_config invalid: " + "; ".join(cfg["portable_config_errors"])
            )
        bases = {
            "platform_hd": cfg["values"]["platform_hd"],
            "platform_hs": cfg["values"]["platform_hs"],
        }
        # R5: bind the RESOLUTION ENVIRONMENT, not just file bytes. A downstream
        # that repoints REG_PLATFORM_* to a pristine mirror changes this binding
        # even if the relative paths are identical.
        out["@resolution.platform_hd"] = str(bases["platform_hd"])
        out["@resolution.platform_hs"] = str(bases["platform_hs"])

        # 4. Hash every declared entry's resolved path with explicit safety.
        for key, item in entries:
            rel = item.get("path") or item.get("resolved")
            if not isinstance(rel, str) or not rel.strip() or "\x00" in rel:
                raise EcoDependencyError(f"{key}: path must be a non-empty string")
            normalized = rel.replace("\\", "/")
            if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
                raise EcoDependencyError(f"{key}: absolute paths are not allowed")
            parts = [p for p in normalized.split("/") if p]
            if not parts or any(p in (".", "..") for p in parts):
                raise EcoDependencyError(f"{key}: path traversal is not allowed")
            declared_sha = item.get("sha256")
            if declared_sha is not None and (
                not isinstance(declared_sha, str) or len(declared_sha) != 64
                or any(c not in "0123456789abcdefABCDEF" for c in declared_sha)
            ):
                raise EcoDependencyError(f"{key}: sha256 must be 64 hex characters")
            base_name = item.get("base", "platform_hd")
            if base_name not in bases:
                raise EcoDependencyError(f"{key}: invalid base {base_name!r}")
            required = item.get("required", True)
            if not isinstance(required, bool):
                raise EcoDependencyError(f"{key}: required must be a boolean")

            base_real = os.path.realpath(str(bases[base_name]))
            target = os.path.join(base_real, *parts)
            target_real = os.path.realpath(target)
            try:
                common = os.path.commonpath([base_real, target_real])
            except ValueError:  # different drives / incomparable paths
                common = None
            if common != base_real:
                raise EcoDependencyError(f"{key}: resolved path escapes its base")
            cursor = base_real
            for part in parts:
                cursor = os.path.join(cursor, part)
                if islink(cursor):
                    raise EcoDependencyError(f"{key}: unsafe symlink in collateral path")

            if not isfile(target_real):
                if required:
                    raise EcoDependencyError(
                        f"{key}: required collateral missing: {target_real}"
                    )
                out[key] = "<missing-optional>"
                continue
            try:
                out[key] = digest(target_real)
            except OSError as exc:
                if required:
                    raise EcoDependencyError(
                        f"{key}: required collateral unreadable: {exc}"
                    ) from exc
                out[key] = "<unreadable-optional>"

        return {k: out[k] for k in sorted(out)}

    @staticmethod
    def _physical_assessment(workspace: str) -> PhysicalAssessment:
        """R4/A6-01: authoritative, fail-closed physical-collateral assessment."""
        return _assess_physical_artifacts(workspace)

    @staticmethod
    def _physical_artifacts(workspace: str) -> list[str]:
        """Backward-compatible view: every physical *or uncertain* relative path.

        Callers must treat a non-empty result as "physical verification required";
        uncertainty is deliberately reported as a hit so the N/A exemption is
        never granted on an inconclusive classification.
        """
        return sorted(EcoManager._physical_assessment(workspace).hits)

    @staticmethod
    def _source_corroborated(source: str, registered_inputs: Mapping[str, str]) -> bool:
        """R2-07: a production stage-fact source must be a REAL corroborated artifact.

        Accepted only when the source is an existing file path, or a label that is
        actually registered as an input with an existing file. Free-form prose or
        arbitrary tokens are NOT corroboration.
        """
        src = str(source or "").strip()
        if not src or any(ch in src for ch in "\x00\r\n"):
            return False
        if os.path.isfile(src):
            return True
        # A bare label must correspond to a registered input whose file exists.
        if src in registered_inputs and os.path.isfile(str(registered_inputs[src])):
            return True
        # No spaces / path separators but not registered -> not corroborated.
        return False

    def _collect_verification(
        self,
        policy: tuple[CheckApplicability, ...],
        verify: Callable[..., Iterable[Evidence]] | None,
        *,
        change_class: str,
        started_utc: str,
        previous_ids: set[str],
        timeout_s: float | None,
        evidence_ids: list[str],
        simulated: str = "",
        cancellation: Callable[[], bool] | None = None,
        candidate_snapshot: Mapping[str, str] | None = None,
        candidate_revision: str = "",
        dependency_revision: str = "",
        overall_deadline: float | None = None,
    ) -> dict:
        """Collect verification under a real monotonic deadline (TR-02/TR-03).

        Evidence is streamed and recorded as it arrives, so partial results
        survive a deadline or cancellation. The deadline is enforced by the
        framework clock, not by a provider-supplied ``duration_s`` field.
        Non-cooperative Python callbacks are abandoned on deadline (they cannot
        be forcibly killed in-process); any late mutation is detected after
        rollback by :meth:`_verify_candidate_quiescent`.
        """
        report: dict = {
            "policy": "required_checks", "checks": {}, "applicability": {},
            "incomplete": False, "deadline_exceeded": False,
            "measured_duration_s": 0.0,
            "candidate_revision_bound": candidate_revision,
            "dependency_revision_bound": dependency_revision,
        }
        cancelled = cancellation or (lambda: self.controller.run_status == "CANCELLED")

        if not policy:  # explicit no_op policy
            report["policy"] = "explicitly_inapplicable"
            return report
        if verify is None:
            raise EcoMissingEvidenceError(
                f"verification is required for change class {change_class!r}: "
                "verify() was not provided"
            )
        if cancelled():
            raise EcoCancellationError(
                f"run is CANCELLED ({self.controller.report()['cancel_reason']}); "
                "verification refused", verification_report=report,
            )

        limit: float | None = None
        if timeout_s is not None:
            limit = _as_finite_float(timeout_s, "verification_timeout_s")
            if limit < 0:
                raise EcoVerificationError(
                    f"verification_timeout_s must not be negative: {limit}"
                )
        deadline = time.monotonic() + limit if limit is not None else None
        if deadline is not None and overall_deadline is not None:
            deadline = min(deadline, overall_deadline)
        elif overall_deadline is not None:
            deadline = overall_deadline
        ctx = VerificationContext(deadline=deadline, cancelled=cancelled)
        # R4/A5-C: every verification worker is registered in the managed
        # containment registry.  A worker outliving its bounded join is detected
        # by a post-rollback quiesce + rehash and reported as uncontained; it is
        # never claimed as forcibly terminated (CPython cannot kill threads).
        self._tc = ThreadContainment(snapshot_fn=self.snapshot_workspace)
        self._last_worker_uncontained = False
        self._active_verification_worker = None

        # Backwards-compatible adapter: zero-argument providers keep working;
        # providers accepting (context) or (ctx=...) get an enforceable contract.
        accepts_ctx = False
        try:
            params = list(inspect.signature(verify).parameters.values())
            accepts_ctx = bool(params) and params[0].name in ("ctx", "context") and params[0].kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        except (TypeError, ValueError):
            accepts_ctx = False
        call = (lambda: verify(ctx)) if accepts_ctx else verify

        results: "queue.Queue[tuple[str, object]]" = queue.Queue()

        def worker() -> None:
            try:
                for item in call():
                    results.put(("evidence", item))
                    ctx.check()  # cooperative stop for context-aware providers
                results.put(("done", None))
            except BaseException as exc:  # noqa: BLE001 - forwarded to caller
                results.put(("error", exc))

        thread = threading.Thread(target=worker, name="eco-verifier", daemon=True)
        self._active_verification_worker = thread
        self._tc.register(thread, name="eco-verifier")
        thread.start()

        seen: dict[str, list[Evidence]] = defaultdict(list)
        while True:
            if cancelled():
                report["incomplete"] = True
                report["measured_duration_s"] = ctx.measured_duration_s
                raise EcoCancellationError(
                    "run was cancelled during verification",
                    verification_report=report,
                    measured_duration_s=ctx.measured_duration_s,
                )
            if deadline is not None and time.monotonic() >= deadline:
                report["incomplete"] = True
                report["deadline_exceeded"] = True
                report["measured_duration_s"] = ctx.measured_duration_s
                raise EcoTimeoutError(
                    f"verification deadline exceeded after "
                    f"{ctx.measured_duration_s:.3f}s (limit {limit:.3f}s); "
                    "partial evidence preserved",
                    verification_report=report,
                    measured_duration_s=ctx.measured_duration_s,
                )
            try:
                kind, payload = results.get(timeout=0.02)
            except queue.Empty:
                continue
            if kind == "done":
                break
            if kind == "error":
                report["incomplete"] = True
                report["measured_duration_s"] = ctx.measured_duration_s
                if isinstance(payload, EcoRejected):
                    if isinstance(payload, EcoTimeoutError):
                        report["deadline_exceeded"] = True
                    report["measured_duration_s"] = ctx.measured_duration_s
                    payload.verification_report = report
                    payload.measured_duration_s = ctx.measured_duration_s
                    raise payload
                raise EcoVerificationError(
                    f"verify() raised {type(payload).__name__}: {payload}",
                    status="INCONCLUSIVE", verification_report=report,
                    measured_duration_s=ctx.measured_duration_s,
                )
            ev = payload
            if not isinstance(ev, Evidence):
                raise EcoVerificationError(
                    f"verify() returned a non-Evidence object: {ev!r}",
                    verification_report=report,
                )
            seen[ev.check_id].append(ev)
            # F-01/F-02: an ECO gate may only be satisfied by tool-derived,
            # authoritative evidence bound to the candidate revision frozen
            # BEFORE verification started.
            eclass = str(getattr(ev, "evidence_class", "TOOL") or "TOOL").upper()
            # A recorded ``simulated_by`` label is just as disqualifying as an
            # explicit SIMULATED evidence class: neither may back a production
            # applicability decision, including a DRC N/A claim.
            is_simulated = eclass != "TOOL" or bool(getattr(ev, "simulated_by", ""))
            if is_simulated and not simulated.strip():
                raise EcoVerificationError(
                    f"required check {ev.check_id}: simulated/internal evidence "
                    f"({eclass or 'SIMULATED'}) cannot qualify a production ECO "
                    "candidate",
                    verification_report=report,
                )
            if is_simulated:
                report["evidence_class"] = "SIMULATED"
            if candidate_revision:
                ev.input_hashes["__candidate_revision__"] = candidate_revision
            if dependency_revision:
                ev.input_hashes["__dependencies__"] = dependency_revision
            try:
                if simulated.strip():
                    recorded = self.controller.record_evidence(
                        ev, strict=False, simulated=simulated
                    )
                else:
                    recorded = self.controller.record_evidence(ev)
            except Exception as exc:  # noqa: BLE001
                status = "INCONCLUSIVE" if cancelled() else "FAIL"
                raise EcoVerificationError(
                    f"recording verification evidence failed: "
                    f"{type(exc).__name__}: {exc}",
                    status=status, verification_report=report,
                ) from exc
            evidence_ids.append(recorded.evidence_id)

        report["measured_duration_s"] = ctx.measured_duration_s
        if candidate_snapshot is not None:
            after = self.snapshot_workspace()
            if dict(after) != dict(candidate_snapshot):
                diffs = sorted(
                    rel for rel in set(after) | set(candidate_snapshot)
                    if after.get(rel) != candidate_snapshot.get(rel)
                )
                report["incomplete"] = True
                raise EcoRejected(
                    "candidate mutated during verification "
                    "(evidence no longer describes the accepted state): "
                    + ", ".join(diffs[:20]), status="FAIL",
                )
        if cancelled():
            report["incomplete"] = True
            raise EcoCancellationError(
                "run was cancelled during verification",
                verification_report=report,
                measured_duration_s=ctx.measured_duration_s,
            )

        for requirement in policy:
            report["applicability"][requirement.check_id] = {
                "applicable": requirement.applicable,
                "reason": requirement.reason,
                "na_reason": requirement.na_reason,
            }
            matches = seen.get(requirement.check_id, [])
            if not matches:
                raise EcoMissingEvidenceError(
                    f"required check {requirement.check_id}: no fresh evidence "
                    "returned by verify()", verification_report=report,
                )
            if len(matches) != 1:
                raise EcoVerificationError(
                    f"required check {requirement.check_id}: ambiguous evidence "
                    f"({len(matches)} records for one check)",
                    verification_report=report,
                )
            ev = matches[0]
            status = parse_status(ev.status)
            entry = {
                "check_id": requirement.check_id,
                "status": status.value,
                "evidence_id": ev.evidence_id,
                "reason": ev.reason,
                "reported_duration_s": ev.duration_s,
            }
            report["checks"][requirement.check_id] = entry

            if not requirement.applicable:
                if status is not Status.NOT_APPLICABLE:
                    raise EcoVerificationError(
                        f"required check {requirement.check_id} is explicitly "
                        f"inapplicable for {change_class!r} but reported "
                        f"{status.value}: {ev.reason}",
                        verification_report=report,
                    )
                if ev.applicability_reason != requirement.na_reason:
                    raise EcoVerificationError(
                        f"required check {requirement.check_id}: NOT_APPLICABLE "
                        "reason does not match the recorded implementation facts: "
                        f"{ev.applicability_reason!r}",
                        verification_report=report,
                    )
                # R4/A6-01: a NOT_APPLICABLE verdict is still evidence and must
                # be fresh. Previously this branch skipped the reuse/age checks
                # applied to PASS evidence, so a stale N/A record from an older
                # run could qualify a new candidate indefinitely.
                if ev.evidence_id in previous_ids:
                    raise EcoMissingEvidenceError(
                        f"required check {requirement.check_id}: NOT_APPLICABLE "
                        f"evidence {ev.evidence_id} was already recorded; a fresh "
                        "applicability decision is required",
                        verification_report=report,
                    )
                if ev.created_utc < started_utc:
                    raise EcoMissingEvidenceError(
                        f"required check {requirement.check_id}: NOT_APPLICABLE "
                        "evidence predates this experiment (not fresh)",
                        verification_report=report,
                    )
                continue

            if status is not Status.PASS:
                tmp = {
                    Status.FAIL: "FAIL",
                    Status.INCONCLUSIVE: "INCONCLUSIVE",
                    Status.UNSUPPORTED: "INCONCLUSIVE",
                    Status.NOT_RUN: "INCONCLUSIVE",
                }.get(status, "INCONCLUSIVE")
                raise EcoVerificationError(
                    f"required check {requirement.check_id} reported {status.value} "
                    f"(evidence {ev.evidence_id}): {ev.reason}",
                    status=tmp, verification_report=report,
                )
            if ev.evidence_id in previous_ids:
                raise EcoMissingEvidenceError(
                    f"required check {requirement.check_id}: evidence "
                    f"{ev.evidence_id} was already recorded; verification must be fresh",
                    verification_report=report,
                )
            if ev.created_utc < started_utc:
                raise EcoMissingEvidenceError(
                    f"required check {requirement.check_id}: evidence predates this "
                    "experiment (not fresh)", verification_report=report,
                )
            missing = validate_provenance(ev)
            if missing:
                raise EcoMissingEvidenceError(
                    f"required check {requirement.check_id}: incomplete evidence: "
                    + "; ".join(missing), verification_report=report,
                )
            if ev.exit_code != 0:
                raise EcoVerificationError(
                    f"required check {requirement.check_id}: PASS with exit_code="
                    f"{ev.exit_code!r} is not acceptable",
                    verification_report=report,
                )
            if ev.duration_s is not None:
                # TR-03: informational only. A provider-reported duration is
                # never used to enforce a deadline and can be forged/omitted.
                duration = _as_finite_float(ev.duration_s, f"{requirement.check_id}.duration_s")
                if duration < 0:
                    raise EcoVerificationError(
                        f"required check {requirement.check_id}: negative duration",
                        verification_report=report,
                    )
                entry["reported_duration_is_informational"] = True
        # R4/A5-A: surface the managed-execution facts that gated acceptance.
        # Enforcement happens inside VerificationContext.spawn; this record makes
        # the decision auditable (hard-contained vs cooperative-only/unsupported).
        managed = getattr(ctx, "last_managed_result", None)
        if managed is not None:
            report["managed_execution"] = {
                "containment": str(getattr(managed, "containment", "none")),
                "containment_reason": str(getattr(managed, "containment_reason", "")),
                "wsl_uncontained": bool(getattr(managed, "wsl_uncontained", False)),
                "authorization_safe": bool(getattr(managed, "authorization_safe", False)),
                "classification": (
                    "hard-contained-direct-tree"
                    if getattr(managed, "is_host_contained", False)
                    else "unsupported-or-cooperative-or-unconfirmed"
                ),
            }
        return report

    # -- metrics -------------------------------------------------------------
    @staticmethod
    def _compare_metrics(
        protected_metrics: Sequence[ProtectedMetric], values: Mapping[str, object]
    ) -> tuple[dict, list[str]]:
        report: dict = {}
        violations: list[str] = []
        for pm in protected_metrics:
            if pm.name not in values:
                violations.append(f"{pm.name}: missing after change")
                report[pm.name] = {"baseline": pm.baseline, "after": None}
                continue
            try:
                value = _as_finite_float(values[pm.name], pm.name)
            except ValueError as exc:
                violations.append(str(exc))
                report[pm.name] = {"baseline": pm.baseline, "after": values[pm.name]}
                continue
            bad = pm.violated(value)
            report[pm.name] = {
                "baseline": pm.baseline,
                "after": value,
                "tolerance": pm.tolerance,
                "direction": pm.direction,
                "violated": bad,
            }
            if bad:
                violations.append(
                    f"{pm.name}: {value} violates protected baseline {pm.baseline} "
                    f"(tol {pm.tolerance}, {pm.direction})"
                )
        return report, violations

    def _verification_workers(self) -> list:
        """Live registered workers from the ThreadContainment registry (R5)."""
        tc = getattr(self, "_tc", None)
        if tc is None:
            return []
        registry = getattr(tc, "_workers", None)
        if registry is None:
            return []
        values = registry.values() if isinstance(registry, dict) else registry
        out = []
        for w in values:
            thread = getattr(w, "thread", None) or w
            if thread is not None and hasattr(thread, "is_alive"):
                out.append(thread)
        return out

    def _verify_candidate_quiescent(
        self, ckpt: str, before: Mapping[str, str], *, window_s: float = 0.05,
        worker_wait_s: float = 1.0,
    ) -> tuple[bool, str]:
        """Bounded quiesce + rehash, then roll back and verify (A5-C).

        F-04: a fixed sleep is not containment. R4/A5-C: registered workers are
        joined through :class:`ThreadContainment` under ONE shared bounded
        deadline, and the workspace is re-hashed against the POST-ROLLBACK state
        after the wait.  That closes the gap where a worker surviving the 1.0 s
        join wrote after rollback while ``rollback_verified`` stayed true and
        ``late_mutation_detected`` stayed false.

        HONEST CLASSIFICATION: CPython cannot forcibly terminate a thread.  An
        uncooperative worker is DETECTED and reported in
        ``_last_worker_uncontained`` (and ``uncontained_workers`` in the
        quiesce result); it is never claimed as prevented.  Writes after the
        bounded quiesce returns or outside the snapshotted workspace remain
        undetectable by construction (documented residual limitation).
        """
        self._last_worker_uncontained = False
        self._last_quiesce = None
        detail_parts: list[str] = []
        tc = getattr(self, "_tc", None)
        if tc is None:
            # No verification worker was registered (e.g. a pre-verification
            # failure). Fall back to the legacy single worker reference so the
            # previous detection guarantees are not lost.
            tc = ThreadContainment(snapshot_fn=self.snapshot_workspace)
            worker = getattr(self, "_active_verification_worker", None)
            if worker is not None:
                tc.register(worker, name="eco-verifier")

        # Step 1: bounded join of all registered workers. This is a quiesce
        # without a hash baseline; it only establishes liveness. It replaces the
        # old unbounded-in-effect per-worker join with a single shared deadline.
        pre = tc.quiesce(wait_s=worker_wait_s)
        if pre.uncontained_workers:
            self._last_worker_uncontained = True
            detail_parts.append("uncontained_workers=" + repr(pre.uncontained_workers))
        time.sleep(window_s)

        # Step 2: restore the checkpoint and verify the rollback itself.
        restored, detail = self._rollback_and_verify(ckpt, before)

        # Step 3: post-rollback quiesce WITH rehash. ``before`` is the state that
        # must still hold when quiesce returns. Any registered worker still alive
        # after the bound, or any path whose hash changed during the wait, makes
        # the rollback non-qualifying (caller fails closed).
        if restored:
            settled = self.snapshot_workspace()
            qr = tc.quiesce(before=settled, wait_s=worker_wait_s)
            self._last_quiesce = qr
            if qr.uncontained_workers:
                self._last_worker_uncontained = True
            if not qr.contained:
                restored = False
                detail = "managed quiesce failed: " + qr.detail
        if detail_parts:
            detail = "; ".join(detail_parts + [detail])
        return restored, detail

    def _pre_acceptance_revalidation(
        self,
        *,
        ckpt: str,
        declared: Sequence[str],
        baseline: Mapping[str, str],
        revision: CandidateRevision,
        evidence_ids: Sequence[str],
        verification_report: dict,
        deadline: float | None = None,
        dependency_revision: str = "",
        allow_simulated: bool = False,
    ) -> None:
        """Validate the exact candidate immediately before acceptance (TR-02)."""
        self._check_overall(deadline, "before final revision check")
        final = self.snapshot_workspace()
        if dict(final) != dict(revision.snapshot):
            diffs = sorted(
                rel for rel in set(final) | set(revision.snapshot)
                if final.get(rel) != revision.snapshot.get(rel)
            )
            raise EcoRejected(
                "candidate changed after verification (post-verification mutation): "
                + ", ".join(diffs[:20]), status="FAIL",
            )
        self._check_overall(deadline, "after final revision check")
        unlisted = self._unlisted_changes(declared, baseline)
        if unlisted:
            raise EcoRejected(
                "unlisted file modifications detected before acceptance: "
                + ", ".join(unlisted), status="FAIL",
            )
        self._check_overall(deadline, "after unlisted-change check")
        if dependency_revision:
            current_deps = stable_json_hash(self._dependency_snapshot())
            if current_deps != dependency_revision:
                raise EcoRejected(
                    "acceptance-relevant dependency changed since verification "
                    f"(expected {dependency_revision[:12]}, got {current_deps[:12]})",
                    status="FAIL",
                )
        problems: list[str] = []
        all_ev = self.controller.all_evidence()
        by_id = {ev.evidence_id: ev for ev in all_ev}
        for eid in evidence_ids:
            self._check_overall(deadline, f"during evidence revalidation {eid}")
            ev = by_id.get(eid)
            if ev is None:
                problems.append(f"evidence {eid} missing from controller state")
                continue
            if (
                str(getattr(ev, "evidence_class", "TOOL") or "TOOL").upper() != "TOOL"
                and not allow_simulated
            ):
                problems.append(f"{eid}: simulated/internal evidence cannot qualify")
            bound = ev.input_hashes.get("__candidate_revision__")
            if bound and bound != revision.revision_id:
                problems.append(f"{eid}: bound to a different candidate revision")
            problems.extend(f"{eid}: {p}" for p in revalidate_evidence(ev))
            latest = self.controller.latest_evidence(ev.check_id)
            if latest is None or latest.evidence_id != eid:
                problems.append(f"{eid}: no longer the latest evidence for {ev.check_id}")
        if problems:
            raise EcoMissingEvidenceError(
                "evidence revalidation failed: " + "; ".join(problems)
            )
        verification_report["candidate_revision"] = revision.revision_id
        verification_report["accepted_snapshot_sha256"] = stable_json_hash(final)
        verification_report["evidence_revalidated"] = True
        verification_report["downstream_candidate_revision"] = revision.revision_id
        self._check_overall(deadline, "after evidence revalidation")

    # -- experiment lifecycle ------------------------------------------------
    def _blocked(
        self, experiment_id: str, description: str, change_class: str, reason: str
    ) -> ExperimentResult:
        result = ExperimentResult(
            experiment_id=experiment_id,
            change_class=change_class,
            description=description,
            status="BLOCKED",
            reason=reason,
        )
        try:
            self._commit(result)
        except OSError:  # persistence failure must not turn BLOCKED into PASS
            pass
        return result

    def _amend_last(
        self, result: ExperimentResult, transaction_token: str | None = None
    ) -> None:
        """Replace THIS transaction's record without appending a contradictory entry.

        R2-01: never allow a re-entrant/persistence-retry caller to overwrite a
        terminal record with PASS once the active guard is invalidated. R4-A4-B:
        the target is the record carrying this transaction's unique token, not
        ``history[-1]`` - a nested acceptance's record can never be redirected
        or destroyed by its parent's amendment. A missing record is a hard
        error (no silent fail-open that could leave a durable PASS behind).

        Nesting choice (A4-B): a nested ``run_experiment`` is an INDEPENDENT
        transaction with its own token, fresh evidence and its own bound
        candidate revision. When a parent is invalidated we therefore leave the
        nested record intact and amend ONLY the parent's own record - we do not
        cascade an invalidation into a separate acceptance that produced its
        own evidence. The invariant enforced here is per transaction token:
        exactly one terminal record, never a PASS plus an INCONCLUSIVE for the
        same token.
        """
        if result.status == "PASS" and not self._pass_allowed():
            guard = self._active_guard
            reason, kind = guard.chain_reason() if guard is not None else (None, None)
            if kind == "cancellation":
                raise EcoCancellationError(reason or "run cancelled before amend")
            raise EcoTimeoutError(reason or "deadline expired before amend")
        guard = self._active_guard
        token = transaction_token or (guard.transaction_token if guard is not None else "")
        idx = self._record_index(self._history, token)
        if idx < 0:
            raise EcoRejected(
                f"cannot amend: no durable record for transaction {token}",
                status="INCONCLUSIVE",
            )
        self._persist_record(result, token)

    def run_experiment(
        self,
        *,
        experiment_id: str,
        description: str,
        change_class: str,
        changed_files: Sequence[str],
        apply_change: Callable[[], None],
        evaluate: Callable[[], Mapping[str, float]],
        protected_metrics: Sequence[ProtectedMetric],
        verify: Callable[..., Iterable[Evidence]] | None = None,
        verification_tools: Sequence[ManagedToolSpec] | None = None,
        verification_timeout_s: float | None = None,
        downstream_stage: Callable[[ExperimentResult], None] | None = None,
        simulated: str = "",
        authorization_scope: str = "*",
        authorization_reason: str = "",
        authorization_by: str = "",
        change_scope: EcoChangeScope | None = None,
        analysis_mode: bool = False,
    ) -> ExperimentResult:
        """One bounded change -> fresh checks -> compare -> keep or roll back.

        TR-01: applicability of physical checks is derived from ``change_scope``
        (recorded facts), never from ``description`` or the absence of macros.
        TR-02: the accepted candidate is the immutable revision whose evidence
        was validated; post-verification mutation is rejected and rolled back.
        TR-03: verification runs under a real monotonic deadline; partial
        evidence is preserved and late writes are detected after rollback.

        R4/A5-A: ``verification_tools`` is the SUPPORTED production verification
        path. It builds a context-aware provider whose only tool-launch route is
        ``VerificationContext.spawn()`` -> ``runner.run_managed()``; uncontained
        results fail closed instead of qualifying evidence.

        Single-writer boundary: one EcoManager owns the workspace and its log
        for the whole transaction. No cross-process locking is implemented;
        concurrent writers are detected as unlisted changes, not synchronized.
        """
        # R4/A5-A: turn declarative tool specs into the managed provider. This
        # is production code (not a test helper): any caller of run_experiment
        # that supplies tools gets managed containment instead of an inert API.
        # R5 architectural invariant: PRODUCTION qualification requires the
        # managed execution boundary. An unmanaged caller-supplied verify()
        # callback is permitted ONLY for explicitly labelled simulation, where
        # it can never produce a PRODUCTION-qualified result.
        if (
            not simulated.strip()
            and not analysis_mode
            and verification_tools is None
            and verify is not None
        ):
            return self._blocked(
                experiment_id, description, change_class,
                "R5: unmanaged verify() cannot qualify a production candidate; "
                "use verification_tools= (managed execution boundary) or an "
                "explicitly labelled simulated run",
            )
        if verification_tools is not None:
            if verify is not None:
                return self._blocked(
                    experiment_id, description, change_class,
                    "A5-A: verification_tools and verify() are mutually exclusive; "
                    "use the managed provider or a custom provider, not both",
                )
            try:
                verify = managed_tool_verification(
                    verification_tools,
                    log_dir=os.path.join(
                        self.checkpoint_dir, "managed_logs", str(experiment_id)
                    ),
                    cwd=self.workspace,
                )
            except ValueError as exc:
                return self._blocked(
                    experiment_id, description, change_class,
                    f"A5-A: invalid managed verification tools: {exc}",
                )
        if self._consecutive_failures >= self.MAX_FAILURES_BEFORE_REASSESSMENT:
            raise EcoBudgetExceeded(
                f"{self._consecutive_failures} consecutive failed ECO attempts: "
                "reassessment required before another attempt. Do NOT claim the "
                "problem is upstream - re-examine hypotheses, inputs and the "
                "protected metric definitions first."
            )
        try:
            self.require_authorization(change_class, authorization_scope)
        except EcoAuthorizationError:
            return self._blocked(
                experiment_id, description, change_class,
                "restricted change class requires explicit architectural "
                "authorization; no authorization recorded",
            )
        try:
            policy = policy_for(change_class)
        except EcoPolicyError as exc:
            return self._blocked(experiment_id, description, change_class, str(exc))
        if change_scope is None:
            return self._blocked(
                experiment_id, description, change_class,
                "TR-01: no recorded EcoChangeScope; physical check applicability "
                "cannot be decided from caller prose or macro inventory alone",
            )
        if change_scope.change_class != change_class:
            return self._blocked(
                experiment_id, description, change_class,
                "F-06: EcoChangeScope.change_class does not match the submitted "
                "change class; applicability cannot be trusted",
            )
        # F-05: the implementation scope must be backed by a real, identifiable
        # source, not free-form prose. Accept an existing file under the
        # workspace, a registered input label, or an explicit run-id token.
        src = str(change_scope.stage_fact_source).strip()
        # In production, the source must be identifiable (a real file, a
        # registered input label or an explicit run-id token). Labelled
        # simulated tests may use a descriptive source, recorded as such.
        if simulated.strip() or change_class == "no_op":
            src_ok = bool(src) and bool(re.fullmatch(r"[A-Za-z0-9_ .:/()\-]{4,120}", src))
        else:
            # R2-07: production requires a REAL corroborated artifact source.
            src_ok = self._source_corroborated(src, self.controller._inputs)
        if not src_ok:
            return self._blocked(
                experiment_id, description, change_class,
                "R2-07/F-05: EcoChangeScope.stage_fact_source is not a corroborated "
                f"implementation source/path: {src!r}",
            )
        # Corroborate the physical-implementation claim against on-disk artifacts.
        # R4/A6-01: a non-benign assessment includes UNCERTAIN entries; an
        # inconclusive classification is not a licence to grant the N/A rule.
        declared_assessment = self._physical_assessment(self.workspace)
        if change_scope.physical_implementation_present is False and declared_assessment.blocks:
            return self._blocked(
                experiment_id, description, change_class,
                "R2-07/F-05: scope declares netlist-only but physical implementation "
                "artifacts exist or cannot be ruled out: "
                + declared_assessment.summary(),
            )
        try:
            applicability = applicability_for(
                change_class, change_scope, self.controller.design_facts
            )
        except EcoPolicyError as exc:
            return self._blocked(experiment_id, description, change_class, str(exc))
        if change_class == "power_intent" and change_scope.physical_implementation_present:
            return self._blocked(
                experiment_id, description, change_class,
                "power-intent changes on a physical implementation are UNSUPPORTED: "
                "no power-intent verification capability is implemented; "
                "qualification is BLOCKED, not waived",
            )
        if not protected_metrics:
            return self._blocked(
                experiment_id, description, change_class,
                "at least one protected metric is required",
            )
        try:
            declared = self._validate_declared_files(changed_files)
        except ValueError as exc:
            return self._blocked(
                experiment_id, description, change_class,
                f"invalid changed_files path: {exc}",
            )

        self._attempt += 1
        attempt_no = self._attempt
        started_utc = utc_now()
        started_monotonic = time.monotonic()
        previous_ids = {ev.evidence_id for ev in self.controller.all_evidence()}

        # R2-02: establish the complete dependency closure BEFORE the framework
        # verification deadline is armed. Hashing pinned collateral is setup,
        # not verification work; counting the 20 MB ORFS collateral against a
        # short verification budget would spuriously expire the run before any
        # check executes. A closure that cannot be established fails closed.
        try:
            dependency_snapshot = self._dependency_snapshot()
        except EcoDependencyError as exc:
            return self._blocked(experiment_id, description, change_class, str(exc))
        dependency_revision = stable_json_hash(dependency_snapshot)

        # F-03: one authoritative monotonic deadline for the whole transaction.
        overall_deadline: float | None = None
        if verification_timeout_s is not None:
            limit_s = _as_finite_float(verification_timeout_s, "verification_timeout_s")
            if limit_s < 0:
                return self._blocked(experiment_id, description, change_class,
                                     "verification_timeout_s must not be negative")
            overall_deadline = time.monotonic() + limit_s

        # R2-01: one authoritative, single-shot guard for the whole
        # transaction. A re-entrant acceptance (e.g. a callback re-entering
        # run_experiment) must observe and honour an outer invalidation and
        # must never create a fresh PASS in that state.
        previous_guard = self._active_guard
        if previous_guard is not None and previous_guard.chain_invalidated():
            reason, kind = previous_guard.chain_reason()
            if kind == "cancellation":
                raise EcoCancellationError(
                    reason or "nested acceptance refused: run cancelled"
                )
            raise EcoTimeoutError(
                reason or "nested acceptance refused: deadline expired"
            )
        guard = _AcceptanceGuard(
            deadline=overall_deadline,
            cancelled=lambda: self.controller.run_status == "CANCELLED",
            parent=previous_guard,
        )
        # R4: reject a duplicate/re-entrant acceptance for the SAME caller id.
        # A distinct id may nest (it gets its own token and its own record).
        with self._transactions_lock:
            if experiment_id in self._active_transactions:
                self._active_guard = previous_guard
                raise EcoRejected(
                    f"duplicate/re-entrant acceptance for transaction id {experiment_id!r}",
                    status="INCONCLUSIVE",
                )
            self._active_transactions[experiment_id] = guard.transaction_token
        self._active_guard = guard

        try:
            ckpt = self.checkpoint(changed_files, experiment_id)
            manifest = self._read_manifest(ckpt)
        except Exception as exc:  # noqa: BLE001
            self._consecutive_failures += 1
            result = ExperimentResult(
                experiment_id=experiment_id, change_class=change_class,
                description=description, status="FAIL",
                reason=f"checkpoint failed: {type(exc).__name__}: {exc}",
                attempt_number=attempt_no, change_scope=change_scope.to_dict(),
            )
            try:
                self._commit(result)
            except OSError:
                pass
            return result

        baseline = manifest["full"]
        evidence_ids: list[str] = []
        protected_report: dict = {}
        verification_report: dict = {}
        revision: CandidateRevision | None = None
        measured_duration: float | None = None
        deadline_exceeded = False
        late_mutation = False
        # R4 binding: initialized here so the FINAL boundary (which is common
        # to the downstream and no-downstream paths) can reference it safely.
        downstream_error: str | None = None

        try:
            # Facts that decide applicability are registered through the same
            # validated store used for every other N/A decision. This happens
            # inside the transaction so a cancellation here still rolls back.
            try:
                self.controller.register_design_fact(
                    "eco_target_stage", change_scope.target_stage.value,
                    source=change_scope.stage_fact_source,
                )
                self.controller.register_design_fact(
                    "eco_physical_implementation_present",
                    change_scope.physical_implementation_present,
                    source=change_scope.stage_fact_source,
                )
            except Exception as exc:  # noqa: BLE001
                if self.controller.run_status == "CANCELLED":
                    raise EcoCancellationError(
                        f"run was cancelled before verification: {exc}"
                    ) from exc
                raise
            apply_change()

            # R2-07/R4-A6-01: re-derive physical implementation from the ACTUAL
            # changed workspace. A caller can add physical artifacts (or leave
            # content whose classification is uncertain) inside apply_change
            # while still declaring "netlist-only"; that must not exempt DRC.
            post_apply = self._physical_assessment(self.workspace)
            if (
                not simulated.strip()
                and change_scope.physical_implementation_present is False
                and post_apply.blocks
            ):
                raise EcoRejected(
                    "R2-07: apply_change produced physical implementation artifacts "
                    "or left physical collateral that cannot be ruled out, but the "
                    "scope still declares netlist-only; physical verification "
                    "is required: " + post_apply.summary(),
                    status="FAIL",
                )

            # F-02: freeze the candidate before any provider-controlled code
            # runs (also for no_op) and bind all evidence to that revision.
            verified_snapshot = self.snapshot_workspace()
            candidate_revision = stable_json_hash(verified_snapshot)

            if not policy:  # explicit no_op policy: nothing may change
                if self._unlisted_changes((), baseline):
                    raise EcoRejected(
                        "no_op policy declares verification inapplicable but files changed",
                        status="FAIL",
                    )
            else:
                self._check_overall(overall_deadline, "before verification")
                verification_report = self._collect_verification(
                    applicability, verify, change_class=change_class,
                    started_utc=started_utc, previous_ids=previous_ids,
                    timeout_s=verification_timeout_s, evidence_ids=evidence_ids,
                    simulated=simulated,
                    cancellation=lambda: self.controller.run_status == "CANCELLED",
                    candidate_snapshot=verified_snapshot,
                    candidate_revision=candidate_revision,
                    dependency_revision=dependency_revision,
                    overall_deadline=overall_deadline,
                )

            # The authoritative revision is the one frozen before verification.
            revision = CandidateRevision(
                revision_id=candidate_revision,
                snapshot=verified_snapshot, experiment_id=experiment_id,
                evidence_ids=tuple(evidence_ids),
                verification=verification_report,
                measured_duration_s=float(verification_report.get("measured_duration_s", 0.0)),
                created_utc=utc_now(),
                # R4: bind the accepted dependency identity to the immutable
                # revision object that the final integrity check consumes.
                dependency_revision=dependency_revision,
            )

            unlisted = self._unlisted_changes(declared, baseline)
            if unlisted:
                raise EcoRejected(
                    "unlisted file modifications detected: " + ", ".join(unlisted),
                    status="FAIL",
                )

            # F-03: the deadline remains authoritative through evaluation and
            # every acceptance boundary.
            self._check_overall(overall_deadline, "after verification")
            # TR-02: evaluation is read-only w.r.t. the candidate.
            before_eval = self.snapshot_workspace()
            try:
                values = dict(evaluate())
            except Exception as exc:  # noqa: BLE001
                raise EcoRejected(
                    f"metric evaluation failed: {type(exc).__name__}: {exc}",
                    status="INCONCLUSIVE",
                ) from exc
            after_eval = self.snapshot_workspace()
            if after_eval != before_eval:
                changed = sorted(
                    rel for rel in set(after_eval) | set(before_eval)
                    if after_eval.get(rel) != before_eval.get(rel)
                )
                raise EcoRejected(
                    "evaluate() mutated the candidate after verification: "
                    + ", ".join(changed[:20]), status="FAIL",
                )

            self._check_overall(overall_deadline, "after evaluation")
            protected_report, violations = self._compare_metrics(protected_metrics, values)
            if violations:
                raise EcoRejected("; ".join(violations))

            self._pre_acceptance_revalidation(
                ckpt=ckpt, declared=declared, baseline=baseline,
                revision=revision, evidence_ids=evidence_ids,
                verification_report=verification_report,
                deadline=overall_deadline, dependency_revision=dependency_revision,
                allow_simulated=bool(simulated.strip()),
            )
            self._check_overall(overall_deadline, "after final revalidation")
            # R5 worker-drain gate: a surviving verification worker cannot be
            # forcibly terminated in-process. If any registered worker is still
            # alive, FAIL CLOSED instead of accepting while it can still mutate
            # state (R4A-02).
            workers = self._verification_workers()
            if workers and any(w.is_alive() for w in workers):
                raise EcoRejected(
                    "verification worker still alive at acceptance; refusing to "
                    "authorize a candidate that can still be mutated",
                    status="FAIL",
                )
            # F-11: final snapshot immediately before commit; downstream must
            # receive exactly this revision.
            final_snapshot = self.snapshot_workspace()
            if dict(final_snapshot) != dict(revision.snapshot):
                raise EcoRejected(
                    "candidate changed between final revalidation and acceptance",
                    status="FAIL",
                )

            self._consecutive_failures = 0
            accepted = ExperimentResult(
                experiment_id=experiment_id, change_class=change_class,
                description=description, status="PASS",
                reason="fresh verification PASS; candidate revision bound to evidence; "
                       "all protected metrics within tolerance",
                protected=protected_report, rollback_performed=False,
                rollback_verified=False, attempt_number=attempt_no,
                evidence_ids=evidence_ids, verification=verification_report,
                candidate_revision=revision.revision_id,
                candidate_snapshot=dict(revision.snapshot),
                change_scope=change_scope.to_dict(),
                measured_verification_s=revision.measured_duration_s,
                dependency_revision=dependency_revision,
                qualification_class=(
                    "SIMULATED" if (simulated.strip() or analysis_mode) else "PRODUCTION"
                ),
            )
            # R2-04: materialize the accepted candidate as a verified,
            # read-only snapshot BEFORE persistence and hand it to downstream
            # via ``accepted.immutable_snapshot_dir``. Downstream is bound to
            # this exact revision/dependency identity, not the live workspace.
            # R4: keep the authoritative immutable path in a LOCAL. Downstream
            # receives the mutable ``accepted`` object and could rewrite its
            # ``immutable_snapshot_dir``; all integrity checks use this local
            # binding instead, so they always inspect the sealed artifact.
            immutable_dir = self._materialize_immutable_snapshot(revision)
            accepted.immutable_snapshot_dir = immutable_dir
            # R5 consumed-state binding: downstream receives a verifying view.
            # R6 Stage 5: capture immutable VerifiedContent for every declared
            # file. Downstream reads these exact bytes; post-acceptance file
            # mutation (or mutable dict editing) cannot change what is consumed.
            _contents: dict[str, object] = {}
            try:
                from .integrity import VerifiedContent as _VC
                for _rel, _digest in revision.snapshot.items():
                    _safe = str(_rel).replace("\\", "/")
                    _p = os.path.join(immutable_dir, *_safe.split("/"))
                    if os.path.isfile(_p):
                        _vc = _VC.from_path(_p)
                        if _vc.digest == _digest:
                            _contents[_safe] = _vc
            except Exception:  # noqa: BLE001 - fall back to path rehash
                _contents = {}
            accepted.consumed_state = VerifiedCandidate(
                revision_id=revision.revision_id,
                snapshot=dict(revision.snapshot),
                root=immutable_dir,
                contents=_contents,
            )
            # R5: NO durable PASS is written until EVERY finalization check has
            # passed. Previously the PASS was committed here and later amended,
            # which left a durable PASS during the final-check window (R4A-01).
            # Downstream receives the in-memory object directly; persistence is
            # the LAST authoritative act.

            # R2-01: the deadline/cancellation remains authoritative THROUGH
            # persistence. An expiry that happened inside _save_log() is
            # detected here; the single committed record is rewritten to one
            # terminal non-PASS state and downstream is never authorized.
            try:
                guard.check("after persistence")
            except EcoRejected as exc:
                reason, dlex = self._terminal_fields(str(exc), "deadline")
                terminal = self._terminal_from(
                    accepted, reason=reason, deadline_exceeded=dlex
                )
                self._commit(terminal)
                self._consecutive_failures += 1
                return terminal

            if downstream_stage is not None:
                self._check_overall(overall_deadline, "before downstream execution")
                after_snapshot = self.snapshot_workspace()
                if dict(after_snapshot) != dict(revision.snapshot):
                    # Already committed: amend the single PASS record instead of
                    # appending a contradictory FAIL (R2-01 invariant).
                    reason, dlex = self._terminal_fields(
                        "candidate changed between acceptance and downstream execution",
                        "mutation",
                    )
                    terminal = self._mutation_terminal(
                        accepted, reason=reason, deadline_exceeded=dlex
                    )
                    self._commit(terminal)
                    self._consecutive_failures += 1
                    return terminal
                # R6.1 P0-A: a cancelled transaction must never hand a
                # qualified PASS to a downstream consumer. ``_check_overall``
                # above rejects a pre-existing invalidation; this closes the
                # narrow window between that check and the callback. Cancellation
                # landing strictly INSIDE the callback remains post-hoc
                # detection (the callback already holds the object) — the
                # guard rechecks below still prevent any durable authorization.
                _downstream_payload = accepted
                try:
                    if guard.chain_invalidated():
                        _dsn_reason, _dsn_kind = guard.chain_reason()
                        _downstream_payload = self._non_pass_terminal_from_durable(
                            accepted,
                            {
                                "status": "FAIL",
                                "qualification_class": "UNQUALIFIED",
                                "deadline_exceeded": bool(guard.latched_deadline_exceeded()),
                                "cancelled": bool(_dsn_kind == "cancellation"),
                                "reason": _dsn_reason or "commit guard invalidation",
                            },
                            revision, immutable_dir,
                        )
                except Exception:  # noqa: BLE001 - fail closed on a guard failure
                    _dsn_data = accepted.to_dict()
                    _dsn_data.update({
                        "status": "FAIL",
                        "qualification_class": "UNQUALIFIED",
                        "deadline_exceeded": True,
                        "cancelled": True,
                        "reason": "commit guard state unreadable before downstream; failing closed",
                    })
                    _downstream_payload = ExperimentResult(**_dsn_data)
                self._active_accepted_revision = revision.revision_id
                try:
                    self._call_downstream(downstream_stage, _downstream_payload)
                except Exception as exc:  # noqa: BLE001 - no contradictory FAIL record
                    downstream_error = f"{type(exc).__name__}: {exc}"

                # R2-01: recheck AFTER the callback returns (or raises). A
                # downstream callback that advances the deadline or cancels the
                # run cannot leave a durable PASS behind, even if it also
                # raised or tried to re-enter persistence itself.
                try:
                    guard.check("after downstream execution")
                except EcoRejected as exc:
                    reason, dlex = self._terminal_fields(str(exc), "deadline")
                    terminal = self._terminal_from(
                        accepted, reason=reason, deadline_exceeded=dlex
                    )
                    self._bind_terminal_identity(terminal, revision, immutable_dir)
                    if downstream_error is not None:
                        terminal.downstream_failed = True
                        terminal.downstream_error = downstream_error
                    self._commit(terminal)
                    self._consecutive_failures += 1
                    return terminal

                # R2-04: re-hash immediately after the callback. A concurrent
                # or daemon writer (or the callback itself) that mutated the
                # accepted candidate, or tampered with the immutable copy, is
                # detected here and fails closed: containment of the live
                # workspace cannot be guaranteed for arbitrary callbacks, so a
                # stale PASS is never claimed.
                # R4: use the LOCALLY captured immutable path, not the mutable
                # result object downstream may have rewritten.
                problems = self._accepted_integrity_violations(
                    revision, immutable_dir
                )

                # R4-A1-F1: the guard stays authoritative through the FINAL
                # integrity re-hash (the last substantive work item before the
                # return). Previously the last check was at "after downstream
                # execution"; a deadline/cancel crossing during the re-hash
                # left a durable PASS with deadline_exceeded=False and
                # downstream already authorized. The first latched reason wins.
                try:
                    guard.check("after integrity rehash")
                except EcoRejected as exc:
                    reason, dlex = self._terminal_fields(str(exc), "deadline")
                    terminal = self._terminal_from(
                        accepted, reason=reason, deadline_exceeded=dlex
                    )
                    self._bind_terminal_identity(terminal, revision, immutable_dir)
                    if downstream_error is not None:
                        terminal.downstream_failed = True
                        terminal.downstream_error = downstream_error
                    self._commit(terminal)
                    self._consecutive_failures += 1
                    return terminal

                if problems:
                    reason, dlex = self._terminal_fields(
                        "; ".join(problems), "mutation"
                    )
                    terminal = self._mutation_terminal(
                        accepted, reason=reason, deadline_exceeded=dlex
                    )
                    self._bind_terminal_identity(terminal, revision, immutable_dir)
                    if downstream_error is not None:
                        terminal.downstream_failed = True
                        terminal.downstream_error = downstream_error
                    self._commit(terminal)
                    self._consecutive_failures += 1
                    return terminal

                if downstream_error is not None:
                    # R4: the final persist is itself an authorization boundary.
                    try:
                        guard.check("before final acceptance persist")
                    except EcoRejected as exc:
                        reason, dlex = self._terminal_fields(str(exc), "deadline")
                        terminal = self._terminal_from(
                            accepted, reason=reason, deadline_exceeded=dlex
                        )
                        self._bind_terminal_identity(terminal, revision, immutable_dir)
                        terminal.downstream_failed = True
                        terminal.downstream_error = downstream_error
                        self._commit(terminal)
                        self._consecutive_failures += 1
                        return terminal
                    # R5: record the downstream failure on the in-memory result;
                    # the single authoritative commit happens at the end after
                    # all finalization checks (no duplicate PASS record).
                    accepted.downstream_failed = True
                    accepted.downstream_error = downstream_error

            # ---------------------------------------------------------------
            # R4-A4-A / R4-BINDING: FINAL AUTHORIZATION BOUNDARY.
            # This is the LAST substantive work before returning, and it is
            # common to BOTH the downstream and no-downstream paths (the
            # latter previously had no post-commit re-hash at all, A4-D).
            #
            # The PASS was committed BEFORE the first re-hash. A writer can
            # therefore land between that hash and the return (the post-seal
            # TOCTOU of A4-A). Re-running the SAME authoritative binding check
            # here detects such a mutation and amends the durable record to a
            # single non-qualifying terminal before anything is returned.
            #
            # HONEST RESIDUAL: this is still POST-HOC DETECTION, not atomic
            # prevention. Any hash of a mutable workspace has a last-
            # instruction window between this check and ``return accepted``;
            # chmod is only a best-effort seal. A writer landing strictly in
            # that window, or one that can replace the check itself, is not
            # prevented. What is guaranteed is that every mutation detectable
            # up to the last instruction invalidates the durable PASS, and
            # the authoritative artifacts checked here are the frozen
            # revision and the locally captured immutable path - never the
            # mutable result object handed to downstream.
            # ---------------------------------------------------------------
            final_problems = self._accepted_integrity_violations(
                revision, immutable_dir
            )
            try:
                guard.check("after final binding recheck")
            except EcoRejected as exc:
                reason, dlex = self._terminal_fields(str(exc), "deadline")
                terminal = self._terminal_from(
                    accepted, reason=reason, deadline_exceeded=dlex
                )
                self._bind_terminal_identity(terminal, revision, immutable_dir)
                if downstream_error is not None:
                    terminal.downstream_failed = True
                    terminal.downstream_error = downstream_error
                self._commit(terminal)
                self._consecutive_failures += 1
                return terminal

            if final_problems:
                reason, dlex = self._terminal_fields(
                    "; ".join(final_problems), "mutation"
                )
                terminal = self._mutation_terminal(
                    accepted, reason=reason, deadline_exceeded=dlex
                )
                self._bind_terminal_identity(terminal, revision, immutable_dir)
                if downstream_error is not None:
                    terminal.downstream_failed = True
                    terminal.downstream_error = downstream_error
                self._commit(terminal)
                self._consecutive_failures += 1
                return terminal
            # R5: final integrity re-check IMMEDIATELY before persistence. This
            # is the authoritative last read of the consumed bytes; any mutation
            # after the earlier rehash (R4A-01 final window) is caught here.
            last_problems = self._accepted_integrity_violations(revision, immutable_dir)
            if last_problems:
                terminal = self._mutation_terminal(
                    accepted, reason="; ".join(last_problems),
                    deadline_exceeded=guard.expired(),
                )
                self._bind_terminal_identity(terminal, revision, immutable_dir)
                self._commit(terminal)
                self._consecutive_failures += 1
                return terminal
            # R6: the single authoritative PASS is persisted ONLY after all
            # finalization checks pass. There is NO post-commit amendment of a
            # PASS: _save_log stamps the guard state at serialization time, so
            # a deadline/cancellation firing during the atomic write produces a
            # durable non-authorizing FAIL in the same write. Persistence failure
            # leaves only a non-authorizing PENDING/absent record.
            self._check_overall(overall_deadline, "before final acceptance persistence")
            self._commit(accepted)
            # R6.1 P0-A (4): re-check the guard AFTER the atomic write. A
            # cancellation accepted in the window between the commit's own
            # serialization check and this line must reach the caller as a
            # non-PASS terminal result — never as an authorizing PASS.
            try:
                _invalidated = bool(guard.chain_invalidated())
            except Exception:  # noqa: BLE001 - fail closed on a guard failure
                _invalidated = True

            if not _invalidated:
                # No invalidation: return PASS unless _save_log already
                # downgraded the record during serialization (which would have
                # latched the guard, so this branch would not have been taken).
                try:
                    _idx = self._record_index(self._history, guard.transaction_token)
                    if _idx >= 0 and self._history[_idx].get("status") != "PASS":
                        return self._non_pass_terminal_from_durable(
                            accepted, self._history[_idx], revision, immutable_dir,
                            downstream_error,
                        )
                except Exception:  # noqa: BLE001
                    pass
                return accepted

            # Invalidated: ensure the durable record is non-authorizing before
            # returning. Do NOT rely on a post-commit amendment of a PASS to
            # achieve this; the commit-time stamp writes FAIL in the same
            # atomic write. This re-stamp is only a defensive correction for a
            # cancellation that landed after that write. If the durable record
            # cannot be safely corrected, fail closed — never return PASS.
            try:
                _tok = guard.transaction_token
                _idx = self._record_index(self._history, _tok)
                _d = self._history[_idx] if _idx >= 0 else {}
                if not _d or _d.get("status") == "PASS":
                    self._save_log()  # re-stamp with the now-invalidated guard
                    _idx = self._record_index(self._history, _tok)
                    _d = self._history[_idx] if _idx >= 0 else {}
                if not _d or _d.get("status") == "PASS":
                    raise EcoCancellationError(
                        "durable PASS could not be corrected after commit-time "
                        "invalidation; failing closed"
                    )
                self._consecutive_failures += 1
                return self._non_pass_terminal_from_durable(
                    accepted, _d, revision, immutable_dir, downstream_error,
                )
            except EcoRejected:
                raise
            except Exception as exc:  # noqa: BLE001 - fail closed
                raise EcoCancellationError(
                    "post-commit guard invalidation could not be reconciled: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

        except (KeyboardInterrupt, SystemExit):
            restored, detail = self._verify_candidate_quiescent(ckpt, baseline)
            late_mutation = not restored
            self._consecutive_failures += 1
            result = ExperimentResult(
                experiment_id=experiment_id, change_class=change_class,
                description=description, status="FAIL",
                reason=f"experiment cancelled/interrupted; rollback {detail}",
                protected=protected_report, rollback_performed=restored,
                rollback_verified=restored, attempt_number=attempt_no,
                evidence_ids=evidence_ids,
                verification=verification_report or getattr(exc, "verification_report", {}) or {},
                change_scope=change_scope.to_dict(),
                candidate_revision=revision.revision_id if revision else None,
                measured_verification_s=(
                    getattr(exc, "measured_duration_s", None)
                    if getattr(exc, "measured_duration_s", None) is not None
                    else time.monotonic() - started_monotonic
                ),
                deadline_exceeded=(
                    bool(getattr(exc, "verification_report", {}).get("deadline_exceeded"))
                    if getattr(exc, "verification_report", None) else False
                ) or (overall_deadline is not None and time.monotonic() >= overall_deadline),
                late_mutation_detected=late_mutation,
                worker_uncontained=bool(getattr(self, "_last_worker_uncontained", False)),
                dependency_revision=dependency_revision,
            )
            try:
                self._commit(result)
            except OSError:
                pass
            raise
        except Exception as exc:  # noqa: BLE001 - preserve failure evidence
            restored, detail = self._verify_candidate_quiescent(ckpt, baseline)
            late_mutation = not restored
            self._consecutive_failures += 1
            status = exc.status if isinstance(exc, EcoRejected) else "FAIL"
            part = getattr(exc, "verification_report", None) or {}
            result = ExperimentResult(
                experiment_id=experiment_id, change_class=change_class,
                description=description, status=status,
                reason=f"{type(exc).__name__}: {exc}; rollback {detail}",
                protected=protected_report, rollback_performed=restored,
                rollback_verified=restored, attempt_number=attempt_no,
                evidence_ids=evidence_ids,
                verification=verification_report or part,
                change_scope=change_scope.to_dict(),
                candidate_revision=revision.revision_id if revision else None,
                measured_verification_s=(
                    getattr(exc, "measured_duration_s", None)
                    if getattr(exc, "measured_duration_s", None) is not None
                    else time.monotonic() - started_monotonic
                ),
                deadline_exceeded=bool(part.get("deadline_exceeded")) or (
                    overall_deadline is not None and time.monotonic() >= overall_deadline
                ),
                late_mutation_detected=late_mutation,
                worker_uncontained=bool(getattr(self, "_last_worker_uncontained", False)),
                dependency_revision=dependency_revision,
            )
            try:
                self._commit(result)
            except OSError:
                pass
            return result
        finally:
            # R2-01: a completed transaction must not leak its guard into a
            # later (or re-entrant) one.
            self._active_guard = previous_guard
            # R4: release the id reservation only if it still belongs to this
            # transaction (a nested run with the same id is refused upstream).
            with self._transactions_lock:
                if self._active_transactions.get(experiment_id) == guard.transaction_token:
                    del self._active_transactions[experiment_id]

    # -- reporting -----------------------------------------------------------
    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def reassessment_required(self) -> bool:
        return self._consecutive_failures >= self.MAX_FAILURES_BEFORE_REASSESSMENT

    def history(self) -> list[dict]:
        """Persistent history of every attempt (including failed ones)."""
        return [dict(entry) for entry in self._history]
