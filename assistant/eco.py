"""Controlled ECO experiments: checkpoints, bounded change, rollback, gates.

Rules enforced (mission requirements, findings I/M):

* Every experiment starts from an isolated checkpoint of the working tree
  files it may touch.
* Exactly ONE bounded change per experiment.
* Changes in ``RESTRICTED_CHANGE_CLASSES`` (RTL behavior, interface latency,
  clock definition, timing exception, power intent, verification exclusion)
  require explicit architectural authorization recorded *before* the change.
* Protected metrics are compared against the baseline; a regression rolls the
  experiment back automatically and preserves the failed evidence.
* Three consecutive failed attempts trigger a mandatory reassessment - never
  an automatic "it must be upstream" claim.
* Constraints may never be silently relaxed to improve numbers: relaxing any
  registered constraint/protected file is itself a restricted change class.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Iterable, Mapping, Sequence

from .controller import GateController, RESTRICTED_CHANGE_CLASSES, GateBypassError
from .evidence import Evidence, utc_now
from .hashing import sha256_file
from .statuses import Status


class EcoAuthorizationError(RuntimeError):
    """Raised when a restricted change is attempted without authorization."""


class EcoBudgetExceeded(RuntimeError):
    """Raised when the failure budget forces reassessment."""


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

    def violated(self, value: float) -> bool:
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
    attempt_number: int = 1
    evidence_ids: list[str] = field(default_factory=list)
    created_utc: str = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return asdict(self)


class EcoManager:
    """Runs bounded ECO experiments with checkpoint/rollback discipline."""

    MAX_FAILURES_BEFORE_REASSESSMENT = 3

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
        self.checkpoint_dir = checkpoint_dir or os.path.join(
            workspace, ".eco_checkpoints"
        )
        self.experiment_log = experiment_log or os.path.join(
            workspace, ".eco_experiments.json"
        )
        os.makedirs(self.checkpoint_dir, exist_ok=True)
        self._authorizations: list[Authorization] = []
        self._consecutive_failures = 0
        self._attempt = 0
        self._load_log()

    # -- persistence ---------------------------------------------------------
    def _load_log(self) -> None:
        if os.path.isfile(self.experiment_log):
            try:
                with open(self.experiment_log, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._authorizations = [
                    Authorization(**a) for a in data.get("authorizations", [])
                ]
                self._consecutive_failures = int(data.get("consecutive_failures", 0))
                self._attempt = int(data.get("attempt", 0))
            except (OSError, ValueError, TypeError):
                pass

    def _save_log(self) -> None:
        data = {
            "authorizations": [a.to_dict() for a in self._authorizations],
            "consecutive_failures": self._consecutive_failures,
            "attempt": self._attempt,
            "updated_utc": utc_now(),
        }
        tmp = self.experiment_log + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, self.experiment_log)

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
        """Snapshot the exact files an experiment may modify."""
        ckpt = os.path.join(self.checkpoint_dir, f"{int(time.time()*1000)}_{label}")
        os.makedirs(ckpt, exist_ok=True)
        manifest = {}
        for rel in files:
            src = os.path.join(self.workspace, rel)
            dst = os.path.join(ckpt, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isfile(src):
                shutil.copy2(src, dst)
                manifest[rel] = sha256_file(src)
            else:
                manifest[rel] = "<absent>"
        with open(os.path.join(ckpt, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)
        return ckpt

    def rollback(self, ckpt: str) -> list[str]:
        """Restore checkpointed files; returns list of restored relative paths."""
        with open(os.path.join(ckpt, "manifest.json"), "r", encoding="utf-8") as f:
            manifest = json.load(f)
        restored: list[str] = []
        for rel, digest in manifest.items():
            dst = os.path.join(self.workspace, rel)
            src = os.path.join(ckpt, rel)
            if digest == "<absent>":
                if os.path.isfile(dst):
                    os.remove(dst)
                    restored.append(rel + " (removed)")
            elif os.path.isfile(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                restored.append(rel)
        return restored

    # -- experiment lifecycle ------------------------------------------------
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
        authorization_scope: str = "*",
        authorization_reason: str = "",
        authorization_by: str = "",
    ) -> ExperimentResult:
        """One bounded change -> run checks -> compare -> keep or roll back.

        ``evaluate()`` returns current metric values for the protected set.
        ``verify()`` (optional) returns fresh Evidence records for every check
        affected by the change; each is recorded in the gate controller.
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
            return ExperimentResult(
                experiment_id=experiment_id,
                change_class=change_class,
                description=description,
                status="BLOCKED",
                reason=(
                    "restricted change class requires explicit architectural "
                    "authorization; no authorization recorded"
                ),
            )

        self._attempt += 1
        attempt_no = self._attempt
        ckpt = self.checkpoint(changed_files, experiment_id)

        # Apply the single bounded change.
        try:
            apply_change()
        except Exception as exc:  # noqa: BLE001 - preserve failure evidence
            restored = self.rollback(ckpt)
            self._consecutive_failures += 1
            self._save_log()
            return ExperimentResult(
                experiment_id=experiment_id,
                change_class=change_class,
                description=description,
                status="FAIL",
                reason=f"change application failed: {exc}; rolled back {restored}",
                rollback_performed=True,
                attempt_number=attempt_no,
            )

        # Run affected checks and record evidence (fresh, hash-bound).
        evidence_ids: list[str] = []
        if verify is not None:
            for ev in verify():
                recorded = self.controller.record_evidence(ev)
                evidence_ids.append(recorded.evidence_id)

        # Compare protected metrics against baseline.
        try:
            values = dict(evaluate())
        except Exception as exc:  # noqa: BLE001
            restored = self.rollback(ckpt)
            self._consecutive_failures += 1
            self._save_log()
            return ExperimentResult(
                experiment_id=experiment_id,
                change_class=change_class,
                description=description,
                status="INCONCLUSIVE",
                reason=f"metric evaluation failed: {exc}; rolled back {restored}",
                rollback_performed=True,
                attempt_number=attempt_no,
                evidence_ids=evidence_ids,
            )

        violations = []
        protected_report = {}
        for pm in protected_metrics:
            if pm.name not in values:
                violations.append(f"{pm.name}: missing after change")
                protected_report[pm.name] = {"baseline": pm.baseline, "after": None}
                continue
            v = values[pm.name]
            bad = pm.violated(v)
            protected_report[pm.name] = {
                "baseline": pm.baseline,
                "after": v,
                "tolerance": pm.tolerance,
                "direction": pm.direction,
                "violated": bad,
            }
            if bad:
                violations.append(
                    f"{pm.name}: {v} violates protected baseline {pm.baseline} "
                    f"(tol {pm.tolerance}, {pm.direction})"
                )

        if violations:
            restored = self.rollback(ckpt)
            self._consecutive_failures += 1
            self._save_log()
            # Preserve failed evidence too (already recorded above).
            return ExperimentResult(
                experiment_id=experiment_id,
                change_class=change_class,
                description=description,
                status="ROLLED_BACK",
                reason="; ".join(violations),
                protected=protected_report,
                rollback_performed=True,
                attempt_number=attempt_no,
                evidence_ids=evidence_ids,
            )

        self._consecutive_failures = 0
        self._save_log()
        return ExperimentResult(
            experiment_id=experiment_id,
            change_class=change_class,
            description=description,
            status="PASS",
            reason="all protected metrics within tolerance; experiment kept",
            protected=protected_report,
            rollback_performed=False,
            attempt_number=attempt_no,
            evidence_ids=evidence_ids,
        )

    # -- reporting -----------------------------------------------------------
    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def reassessment_required(self) -> bool:
        return self._consecutive_failures >= self.MAX_FAILURES_BEFORE_REASSESSMENT

    def history(self) -> list[dict]:
        if not os.path.isfile(self.experiment_log):
            return []
        with open(self.experiment_log, "r", encoding="utf-8") as f:
            return json.load(f).get("experiments", [])
