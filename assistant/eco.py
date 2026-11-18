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

import inspect
import json
import math
import os
import re
import shutil
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Callable, Iterable, Mapping, Sequence

from .controller import GateController, RESTRICTED_CHANGE_CLASSES, GateBypassError
from .evidence import Evidence, utc_now, validate_provenance
from .hashing import sha256_file
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

    def __init__(self, reason: str, *, status: str = "FAIL") -> None:
        super().__init__(reason, status=status)


class EcoMissingEvidenceError(EcoVerificationError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason, status="INCONCLUSIVE")


class EcoTimeoutError(EcoVerificationError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason, status="INCONCLUSIVE")


class EcoCancellationError(EcoVerificationError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason, status="INCONCLUSIVE")


@dataclass(frozen=True)
class CheckRequirement:
    """One check in a registered change-class policy.

    ``na_reason`` is ``None`` for a mandatory check (only fresh PASS evidence
    satisfies it).  When set, the check is *explicitly inapplicable* for this
    change class and verify() must return a NOT_APPLICABLE evidence record
    carrying exactly this validated reason.  There is no implicit skip.
    """

    check_id: str
    na_reason: str | None = None


# The reason must be accepted by controller.validate_applicability_reason().
_DRC_NA_REASON = (
    "NA-RULE:NO-MACROS no physical macro geometry is modified by a logical "
    "or timing ECO; routed DRC is explicitly not applicable"
)

#: Registered policy: every non-trivial change class maps to required checks.
#: Unknown classes are rejected (EcoPolicyError -> BLOCKED); nothing is
#: inferred from a reason string and nothing is implicitly optional.
CHANGE_CLASS_POLICY: dict[str, tuple[CheckRequirement, ...]] = {
    "logic_optimization": (
        CheckRequirement("eco_lec"),
        CheckRequirement("eco_sta"),
        CheckRequirement("eco_physical_drc", na_reason=_DRC_NA_REASON),
    ),
    "rtl_behavior": (
        CheckRequirement("eco_lec"),
        CheckRequirement("eco_sta"),
        CheckRequirement("eco_physical_drc", na_reason=_DRC_NA_REASON),
    ),
    "interface_latency": (
        CheckRequirement("eco_lec"),
        CheckRequirement("eco_sta"),
        CheckRequirement("eco_physical_drc", na_reason=_DRC_NA_REASON),
    ),
    "clock_definition": (
        CheckRequirement("eco_lec"),
        CheckRequirement("eco_sta"),
        CheckRequirement("eco_physical_drc", na_reason=_DRC_NA_REASON),
    ),
    "timing_exception": (
        CheckRequirement("eco_lec"),
        CheckRequirement("eco_sta"),
        CheckRequirement("eco_physical_drc", na_reason=_DRC_NA_REASON),
    ),
    "power_intent": (
        CheckRequirement("eco_lec"),
        CheckRequirement("eco_sta"),
        CheckRequirement("eco_physical_drc", na_reason=_DRC_NA_REASON),
    ),
    "verification_exclusion": (
        CheckRequirement("eco_lec"),
        CheckRequirement("eco_sta"),
        CheckRequirement("eco_physical_drc", na_reason=_DRC_NA_REASON),
    ),
    # Explicit inapplicable policy: the only class for which verification may
    # be omitted, and only if the workspace is byte-identical afterwards.
    "no_op": (),
}


def policy_for(change_class: str) -> tuple[CheckRequirement, ...]:
    """Return the registered policy for *change_class* (no implicit default)."""
    try:
        return CHANGE_CLASS_POLICY[change_class]
    except (KeyError, TypeError) as exc:
        raise EcoPolicyError(
            f"no registered verification policy for change class {change_class!r}; "
            "known classes: " + ", ".join(sorted(CHANGE_CLASS_POLICY))
        ) from exc


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

    def to_dict(self) -> dict:
        return asdict(self)


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
        data = {
            "authorizations": [a.to_dict() for a in self._authorizations],
            "experiments": self._history,
            "consecutive_failures": self._consecutive_failures,
            "attempt": self._attempt,
            "updated_utc": utc_now(),
        }
        tmp = self.experiment_log + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, self.experiment_log)

    def _commit(self, result: ExperimentResult) -> None:
        """Persist an attempt (including failures) before it can be returned.

        The in-memory history is only updated after the atomic file replace
        succeeds, so a failed persistence can never leave a phantom PASS in
        ``history()``.
        """
        previous = self._history
        self._history = previous + [result.to_dict()]
        try:
            self._save_log()
        except Exception:
            self._history = previous
            raise

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
    def _collect_verification(
        self,
        policy: tuple[CheckRequirement, ...],
        verify: Callable[[], Iterable[Evidence]] | None,
        *,
        change_class: str,
        started_utc: str,
        previous_ids: set[str],
        timeout_s: float | None,
        evidence_ids: list[str],
        simulated: str = "",
    ) -> dict:
        if not policy:  # explicit no_op policy
            return {"policy": "explicitly_inapplicable", "checks": {}}
        if verify is None:
            raise EcoMissingEvidenceError(
                f"verification is required for change class {change_class!r}: "
                "verify() was not provided"
            )
        if self.controller.run_status == "CANCELLED":
            raise EcoCancellationError(
                f"run is CANCELLED ({self.controller.report()['cancel_reason']}); "
                "verification refused"
            )
        try:
            supplied = list(verify())
        except Exception as exc:  # noqa: BLE001 - all verify failures reject
            raise EcoVerificationError(
                f"verify() raised {type(exc).__name__}: {exc}", status="INCONCLUSIVE"
            ) from exc

        seen: dict[str, list[Evidence]] = defaultdict(list)
        for ev in supplied:
            if not isinstance(ev, Evidence):
                raise EcoVerificationError(
                    f"verify() returned a non-Evidence object: {ev!r}"
                )
            seen[ev.check_id].append(ev)
            try:
                if simulated.strip():
                    # Explicit, labelled simulated mode. No silent opt-out:
                    # an unlabelled strict=False is refused by the controller.
                    recorded = self.controller.record_evidence(
                        ev, strict=False, simulated=simulated
                    )
                else:
                    # Production default: authoritative strict provenance.
                    recorded = self.controller.record_evidence(ev)
            except Exception as exc:  # noqa: BLE001
                if isinstance(exc, EcoCancellationError):
                    raise
                status = "INCONCLUSIVE" if self.controller.run_status == "CANCELLED" else "FAIL"
                raise EcoVerificationError(
                    f"recording verification evidence failed: {type(exc).__name__}: {exc}",
                    status=status,
                ) from exc
            evidence_ids.append(recorded.evidence_id)

        if self.controller.run_status == "CANCELLED":
            raise EcoCancellationError("run was cancelled during verification")

        report: dict = {"policy": "required_checks", "checks": {}}
        for requirement in policy:
            matches = seen.get(requirement.check_id, [])
            if not matches:
                raise EcoMissingEvidenceError(
                    f"required check {requirement.check_id}: no fresh evidence returned "
                    "by verify()"
                )
            if len(matches) != 1:
                raise EcoVerificationError(
                    f"required check {requirement.check_id}: ambiguous evidence "
                    f"({len(matches)} records for one check)"
                )
            ev = matches[0]
            status = parse_status(ev.status)
            entry = {
                "check_id": requirement.check_id,
                "status": status.value,
                "evidence_id": ev.evidence_id,
                "reason": ev.reason,
            }
            report["checks"][requirement.check_id] = entry

            if requirement.na_reason is not None:
                if status is not Status.NOT_APPLICABLE:
                    raise EcoVerificationError(
                        f"required check {requirement.check_id} is explicitly "
                        f"inapplicable for {change_class!r} but reported "
                        f"{status.value}: {ev.reason}"
                    )
                if ev.applicability_reason != requirement.na_reason:
                    raise EcoVerificationError(
                        f"required check {requirement.check_id}: NOT_APPLICABLE reason "
                        f"does not match the registered policy: {ev.applicability_reason!r}"
                    )
                continue

            if status is not Status.PASS:
                # FAIL / INCONCLUSIVE / UNSUPPORTED / NOT_RUN / NOT_APPLICABLE
                # are all non-accepting; the temporary status class keeps the
                # result meaningful while still guaranteeing rejection.
                tmp = {
                    Status.FAIL: "FAIL",
                    Status.INCONCLUSIVE: "INCONCLUSIVE",
                    Status.UNSUPPORTED: "INCONCLUSIVE",
                    Status.NOT_RUN: "INCONCLUSIVE",
                }.get(status, "INCONCLUSIVE")
                raise EcoVerificationError(
                    f"required check {requirement.check_id} reported {status.value} "
                    f"(evidence {ev.evidence_id}): {ev.reason}",
                    status=tmp,
                )

            if ev.evidence_id in previous_ids:
                raise EcoMissingEvidenceError(
                    f"required check {requirement.check_id}: evidence {ev.evidence_id} "
                    "was already recorded; verification must be fresh"
                )
            if ev.created_utc < started_utc:
                raise EcoMissingEvidenceError(
                    f"required check {requirement.check_id}: evidence predates this "
                    "experiment (not fresh)"
                )
            missing = validate_provenance(ev)
            if missing:
                raise EcoMissingEvidenceError(
                    f"required check {requirement.check_id}: incomplete evidence: "
                    + "; ".join(missing)
                )
            if ev.exit_code != 0:
                raise EcoVerificationError(
                    f"required check {requirement.check_id}: PASS with exit_code="
                    f"{ev.exit_code!r} is not acceptable"
                )
            if ev.duration_s is not None:
                duration = _as_finite_float(ev.duration_s, f"{requirement.check_id}.duration_s")
                if duration < 0:
                    raise EcoVerificationError(
                        f"required check {requirement.check_id}: negative duration"
                    )
                if timeout_s is not None:
                    limit = _as_finite_float(timeout_s, "verification_timeout_s")
                    if duration > limit:
                        raise EcoTimeoutError(
                            f"required check {requirement.check_id} timed out: "
                            f"{duration}s > {limit}s"
                        )
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
        verify: Callable[[], Iterable[Evidence]] | None = None,
        verification_timeout_s: float | None = None,
        downstream_stage: Callable[[ExperimentResult], None] | None = None,
        simulated: str = "",
        authorization_scope: str = "*",
        authorization_reason: str = "",
        authorization_by: str = "",
    ) -> ExperimentResult:
        """One bounded change -> fresh checks -> compare -> keep or roll back.

        ``verify()`` must return fresh Evidence for every check registered for
        *change_class* (see ``CHANGE_CLASS_POLICY``).  Verification is not
        optional for substantive changes.  ``downstream_stage`` (if supplied)
        is invoked only after the experiment has been accepted and persisted.

        ``simulated`` must be left empty in production: verification evidence
        is then recorded through the controller's strict provenance boundary.
        Unit tests with synthetic providers pass an explicit label, which is
        forwarded as ``strict=False, simulated=<label>`` only for the evidence
        recorded by this experiment.  The change/acceptance gates are not
        relaxed: statuses, freshness and rollback checks are unchanged.
        """
        # Reassessment gate: after MAX_FAILURES, stop and force reassessment.
        if self._consecutive_failures >= self.MAX_FAILURES_BEFORE_REASSESSMENT:
            raise EcoBudgetExceeded(
                f"{self._consecutive_failures} consecutive failed ECO attempts: "
                "reassessment required before another attempt. Do NOT claim the "
                "problem is upstream - re-examine hypotheses, inputs and the "
                "protected metric definitions first."
            )

        # Authorization must exist BEFORE the change is applied.
        try:
            self.require_authorization(change_class, authorization_scope)
        except EcoAuthorizationError:
            return self._blocked(
                experiment_id, description, change_class,
                "restricted change class requires explicit architectural "
                "authorization; no authorization recorded",
            )

        # Registered policy + metric definitions + paths are validated before
        # anything is touched.
        try:
            policy = policy_for(change_class)
        except EcoPolicyError as exc:
            return self._blocked(experiment_id, description, change_class, str(exc))
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
        previous_ids = {ev.evidence_id for ev in self.controller.all_evidence()}

        try:
            ckpt = self.checkpoint(changed_files, experiment_id)
        except Exception as exc:  # noqa: BLE001
            self._consecutive_failures += 1
            result = ExperimentResult(
                experiment_id=experiment_id, change_class=change_class,
                description=description, status="FAIL",
                reason=f"checkpoint failed: {type(exc).__name__}: {exc}",
                attempt_number=attempt_no,
            )
            try:
                self._commit(result)
            except OSError:
                pass
            return result

        try:
            manifest = self._read_manifest(ckpt)
        except Exception as exc:  # noqa: BLE001
            self._consecutive_failures += 1
            result = ExperimentResult(
                experiment_id=experiment_id, change_class=change_class,
                description=description, status="FAIL",
                reason=f"checkpoint manifest unreadable: {type(exc).__name__}: {exc}",
                attempt_number=attempt_no,
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

        # ---- the transaction: apply -> verify -> evaluate -> policy -> commit
        try:
            apply_change()

            if not policy:  # explicit inapplicable policy: nothing may change
                unlisted = self._unlisted_changes((), baseline)
                changed_declared = [
                    rel for rel in declared
                    if self.snapshot_workspace().get(rel, self._ABSENT)
                    != baseline.get(rel, self._ABSENT)
                ]
                if unlisted or changed_declared:
                    raise EcoRejected(
                        "no_op policy declares verification inapplicable but files "
                        "changed: " + ", ".join(sorted(set(unlisted + changed_declared))),
                        status="FAIL",
                    )
            else:
                verification_report = self._collect_verification(
                    policy, verify, change_class=change_class,
                    started_utc=started_utc, previous_ids=previous_ids,
                    timeout_s=verification_timeout_s, evidence_ids=evidence_ids,
                    simulated=simulated,
                )

            unlisted = self._unlisted_changes(declared, baseline)
            if unlisted:
                raise EcoRejected(
                    "unlisted file modifications detected: " + ", ".join(unlisted),
                    status="FAIL",
                )

            try:
                values = dict(evaluate())
            except Exception as exc:  # noqa: BLE001
                raise EcoRejected(
                    f"metric evaluation failed: {type(exc).__name__}: {exc}",
                    status="INCONCLUSIVE",
                ) from exc

            protected_report, violations = self._compare_metrics(protected_metrics, values)
            if violations:
                raise EcoRejected("; ".join(violations))

            if self.controller.run_status == "CANCELLED":
                raise EcoCancellationError("run was cancelled before acceptance")

            self._consecutive_failures = 0
            accepted = ExperimentResult(
                experiment_id=experiment_id, change_class=change_class,
                description=description, status="PASS",
                reason="fresh verification PASS; all protected metrics within tolerance",
                protected=protected_report, rollback_performed=False,
                rollback_verified=False, attempt_number=attempt_no,
                evidence_ids=evidence_ids, verification=verification_report,
            )
            # Persist before any downstream work; a persistence failure rolls
            # the candidate back and turns the result into FAIL.
            self._commit(accepted)
            if downstream_stage is not None:
                self._call_downstream(downstream_stage, accepted)
            return accepted

        except (KeyboardInterrupt, SystemExit):
            restored, detail = self._rollback_and_verify(ckpt, baseline)
            self._consecutive_failures += 1
            result = ExperimentResult(
                experiment_id=experiment_id, change_class=change_class,
                description=description, status="FAIL",
                reason=f"experiment cancelled/interrupted; rollback {detail}",
                protected=protected_report, rollback_performed=restored,
                rollback_verified=restored, attempt_number=attempt_no,
                evidence_ids=evidence_ids, verification=verification_report,
            )
            try:
                self._commit(result)
            except OSError:
                pass
            raise
        except Exception as exc:  # noqa: BLE001 - preserve failure evidence
            restored, detail = self._rollback_and_verify(ckpt, baseline)
            self._consecutive_failures += 1
            status = exc.status if isinstance(exc, EcoRejected) else "FAIL"
            result = ExperimentResult(
                experiment_id=experiment_id, change_class=change_class,
                description=description, status=status,
                reason=f"{type(exc).__name__}: {exc}; rollback {detail}",
                protected=protected_report, rollback_performed=restored,
                rollback_verified=restored, attempt_number=attempt_no,
                evidence_ids=evidence_ids, verification=verification_report,
            )
            try:
                self._commit(result)
            except OSError:
                pass
            return result

    # -- reporting -----------------------------------------------------------
    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def reassessment_required(self) -> bool:
        return self._consecutive_failures >= self.MAX_FAILURES_BEFORE_REASSESSMENT

    def history(self) -> list[dict]:
        """Persistent history of every attempt (including failed ones)."""
        return [dict(entry) for entry in self._history]
