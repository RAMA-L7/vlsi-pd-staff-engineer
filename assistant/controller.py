"""Deterministic gate controller: stage states, evidence binding, blocking.

Design rules enforced here (see docs/reviews/remediation-tracker.md, finding A,
and docs/reviews/second-review.md SR-03):

* Gate decisions are computed ONLY from recorded evidence records.
* LLM/human summaries are stored as advisory text and are never consulted by
  ``evaluate_gate``; ``force_pass`` raises ``GateBypassError``.
* Evidence whose registered inputs changed since recording is STALE and no
  longer counts (the check falls back to NOT_RUN until re-executed).
* A required check that is not PASS (or explicitly waived / validated
  NOT_APPLICABLE) blocks the gate, and a blocked gate blocks downstream
  stages.
* NOT_APPLICABLE requires an ``NA-RULE:`` reason PLUS registered design facts
  that corroborate the rule (never a free-text reason alone). Non-waivable
  functional-proof checks can never be NOT_APPLICABLE or waived (U-A).
* Waivers are explicit, scoped, identity-bound, revision-bound, expiring and
  surfaced in every gate report. Functional-proof failures are non-waivable.
* Clean PASS, WAIVED_PASS and NOT_APPLICABLE are distinct dispositions; a
  validated N/A gate is never reported as CLEAN_PASS (U-A).
* Strict provenance validation is the DEFAULT for substantive statuses; only
  explicitly labelled simulated checks may opt out with ``strict=False`` (U-B).
* Registered gate specifications define the authoritative required-check set,
  inputs, view and upstream dependencies: callers cannot shorten that set to
  obtain a PASS, and empty definitions/cycles are rejected.
* ALL recording boundaries (direct API, CLI, runner, ECO) pass through the
  single validation boundary ``validate_gate_submission``.
* State is persisted atomically so a cancelled run can be restarted without
  losing evidence; cancellation blocks all new work until a valid restart.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field, asdict
from typing import Callable, Iterable, Mapping, Sequence

from .evidence import (
    BindingContext,
    DependencySet,
    Evidence,
    VIEW_CONTRACTS,
    ViewContract,
    artifact_path,
    contract_compatible,
    post_fill_proof_ok,
    prepare_run_output_dir,
    revalidate_evidence,
    utc_now,
    validate_provenance,
    view_compatible,
)
from .hashing import sha256_file
from .statuses import Status, blocks, parse_status


class GateBypassError(RuntimeError):
    """Raised when something tries to authorize a gate without evidence."""


class ApplicabilityError(ValueError):
    """Raised when a NOT_APPLICABLE reason fails validation."""


class StateError(RuntimeError):
    """Raised for illegal state transitions (e.g. work while cancelled)."""


class GateDefinitionError(ValueError):
    """Raised for empty/invalid gate specs, unknown checks or dependency cycles."""


class GateValidationError(ValueError):
    """Raised when a submitted check fails the authoritative validation boundary."""


#: Applicability reasons must start with an explicit rule token and explain
#: why the check does not apply.  Rule ids live in APPLICABILITY_RULES.
APPLICABILITY_RULES: dict[str, str] = {
    "NA-RULE:NO-UPF": "design has no UPF/CPF power-intent file in scope",
    "NA-RULE:NO-MACROS": "design contains no hard macros",
    "NA-RULE:NO-CLOCK-GATES": "design contains no integrated clock gates",
    "NA-RULE:NO-ANALOG": "design contains no analog/hard IP",
    "NA-RULE:COMBO-ONLY": "design is purely combinational (no sequential elements)",
}

#: Each applicability rule is corroborated by a validated design fact of the
#: shape ``{name, expected_value}``.  A reason alone is never sufficient once
#: design facts are available to the controller (SR-03 sub-finding 13).
APPLICABILITY_RULE_FACTS: dict[str, tuple[str, object]] = {
    "NA-RULE:NO-UPF": ("has_upf", False),
    "NA-RULE:NO-MACROS": ("has_macros", False),
    "NA-RULE:NO-CLOCK-GATES": ("has_clock_gates", False),
    "NA-RULE:NO-ANALOG": ("has_analog", False),
    "NA-RULE:COMBO-ONLY": ("is_combinational", True),
}

MIN_REASON_LEN = 20
MIN_WAIVER_REASON_LEN = 20

#: Substantive statuses must carry full provenance when recorded. NOT_APPLICABLE
#: is not substantive: it is governed by validated design facts instead (U-A).
SUBSTANTIVE_STATUSES: frozenset[Status] = frozenset(
    {
        Status.PASS,
        Status.FAIL,
        Status.INCONCLUSIVE,
        Status.UNSUPPORTED,
        Status.NOT_RUN,
    }
)

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

#: Functional-proof checks can never be made PASS by a waiver. An unresolved
#: formal/equivalence failure is a hard blocker (SR-03 sub-finding 11).
NON_WAIVABLE_CHECKS: frozenset[str] = frozenset(
    {"formal_lec", "lec_formal", "functional_equiv", "formal_proof"}
)

#: Recognised waiver policies. "unspecified" is the legacy default and only
#: honoured for gates that do not carry a registered specification.
WAIVER_POLICIES: frozenset[str] = frozenset(
    {"temporary_risk_acceptance", "tool_gap", "unspecified"}
)


def _parse_utc(value: str) -> _dt.datetime:
    """Parse an ISO UTC timestamp (``...Z`` or ``+00:00``) into an aware dt."""
    text = (value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = _dt.datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


def validate_applicability_reason(reason: str, design_facts: Mapping | None = None) -> str:
    """Validate + normalize a NOT_APPLICABLE reason (U-A).

    Applicability is NEVER decided by a free-text reason string alone: validated
    design facts matching the rule (``APPLICABILITY_RULE_FACTS``) are mandatory.
    This is the single validator invoked from ``record_evidence`` so every
    recording boundary (direct API, CLI, ``mark_not_applicable``) is covered.
    A missing/empty ``design_facts`` mapping is rejected outright.
    """
    if not design_facts:
        raise ApplicabilityError(
            "NOT_APPLICABLE requires registered, validated design facts for rule "
            "corroboration; reason-only applicability is not accepted"
        )
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
    contract = APPLICABILITY_RULE_FACTS.get(rule_token)
    if contract is None:
        raise ApplicabilityError(
            f"applicability rule {rule_token} has no validated design-fact contract"
        )
    fact_name, expected = contract
    if fact_name not in design_facts:
        raise ApplicabilityError(
            f"missing validated design fact {fact_name!r} required by {rule_token}"
        )
    raw = design_facts[fact_name]
    actual = raw.get("value") if isinstance(raw, Mapping) else raw
    if actual != expected:
        raise ApplicabilityError(
            f"validated design fact {fact_name}={actual!r} contradicts {rule_token}"
        )
    return text


def validate_applicability_for_check(
    reason: str,
    check_id: str,
    design_facts: Mapping,
) -> str:
    """Authoritative applicability helper for CLI/runner callers (U-A).

    Validates a NOT_APPLICABLE decision for a *target check id* against the
    registered design facts. Non-waivable functional-proof checks can never be
    declared NOT_APPLICABLE. Returns the normalized reason; raises
    ``ApplicabilityError`` on any violation.
    """
    if not check_id:
        raise ApplicabilityError("a target check_id is required for applicability")
    if check_id in NON_WAIVABLE_CHECKS:
        raise ApplicabilityError(
            f"non-waivable check {check_id!r} cannot be NOT_APPLICABLE; "
            "genuine PASS evidence is required"
        )
    return validate_applicability_reason(reason, design_facts)


# Backwards-compatible private alias (kept so existing callers do not break).
_validate_applicability_reason = validate_applicability_reason


def evidence_view_contract(ev: Evidence) -> ViewContract:
    """Build the explicit contract an evidence record actually provides.

    The base dimensions come from the recorded ``analysis_view`` (sound contract
    model, NOT the legacy total order); the binding dimensions (corner, library
    and extraction provenance) come from the evidence's recorded binding context.
    """
    base = VIEW_CONTRACTS.get(ev.analysis_view, VIEW_CONTRACTS["UNKNOWN"])
    return ViewContract(
        artifact_type=base.artifact_type,
        implementation_stage=base.implementation_stage,
        analysis_type=base.analysis_type,
        parasitic_source=base.parasitic_source,
        fill_status=base.fill_status,
        timing_mode=base.timing_mode,
        corner=ev.corner or base.corner,
        library_provenance=ev.library_provenance or base.library_provenance,
        extraction_provenance=ev.extraction_provenance or base.extraction_provenance,
    )


# ---------------------------------------------------------------------------
# Registered gate / check specifications (SR-03 sub-findings 3, 6, 7, 11)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CheckSpec:
    """Contract for a single required check.

    ``execution_kind`` is ``"tool"`` (an external process ran) or ``"internal"``
    (a deterministic in-process validator ran).  Tool checks must present an
    explicit successful process result (``exit_code == 0``) *and* a parsed
    engineering outcome validated by ``engineering_validator``.  Internal
    checks must declare an explicit ``validator_type`` and ``evidence_contract``.
    """

    check_id: str
    execution_kind: str = "tool"  # "tool" | "internal"
    required_view: str | None = None
    engineering_validator: str = ""  # tool: name in OUTCOME_VALIDATORS
    validator_type: str = ""  # internal: explicit validator kind
    evidence_contract: str = ""  # internal: what evidence must be present
    waivable: bool = True
    required_inputs: tuple[str, ...] = ()
    description: str = ""

    # -- SR-04/SR-05 evaluation-time expectations (serialized forms) ---------
    #: Serialized :class:`DependencySet` (persistent paths + hashes) that must
    #: still match at gate evaluation time.
    expected_dependencies: Mapping[str, object] = field(default_factory=dict)
    #: Serialized :class:`BindingContext` (run/design/stage/tool/view/corner).
    expected_context: Mapping[str, object] = field(default_factory=dict)
    #: Artifacts must live under this isolated run output directory.
    expected_output_dir: str = ""
    #: Serialized :class:`ViewContract` for explicit, multi-dimensional checks.
    view_contract: Mapping[str, object] = field(default_factory=dict)
    require_post_fill_proof: bool = False
    require_extraction_provenance: bool = False

    def dependency_expectation(self) -> DependencySet | None:
        return DependencySet.from_dict(self.expected_dependencies) if self.expected_dependencies else None

    def context_expectation(self) -> BindingContext | None:
        return BindingContext.from_dict(self.expected_context) if self.expected_context else None

    def contract_expectation(self) -> ViewContract | None:
        return ViewContract.from_dict(self.view_contract) if self.view_contract else None

    def validate(self) -> None:
        if not self.check_id:
            raise GateDefinitionError("CheckSpec requires a non-empty check_id")
        if self.execution_kind not in ("tool", "internal"):
            raise GateDefinitionError(
                f"unknown execution_kind {self.execution_kind!r} for {self.check_id}"
            )
        if self.execution_kind == "tool" and not self.engineering_validator:
            raise GateDefinitionError(
                f"tool check {self.check_id} requires an engineering_validator"
            )
        if self.execution_kind == "internal":
            if not self.validator_type:
                raise GateDefinitionError(
                    f"internal check {self.check_id} requires a validator_type"
                )
            if not self.evidence_contract:
                raise GateDefinitionError(
                    f"internal check {self.check_id} requires an evidence_contract"
                )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["required_inputs"] = list(self.required_inputs)
        return d

    @classmethod
    def from_dict(cls, d: Mapping) -> "CheckSpec":
        data = dict(d)
        data["required_inputs"] = tuple(data.get("required_inputs") or ())
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class GateSpec:
    """Authoritative definition of a gate.

    ``required_checks`` is the full set a caller must satisfy; a caller may not
    pass a shorter list to ``evaluate_gate`` and obtain a PASS. ``required_inputs``
    must be registered and bound into evidence; ``required_view`` constrains the
    analysis view; ``upstream`` names gates that must be *re-evaluated* first.
    """

    gate_id: str
    required_checks: tuple[str, ...]
    required_inputs: tuple[str, ...] = ()
    required_view: str | None = None
    upstream: tuple[str, ...] = ()
    checks: Mapping[str, CheckSpec] = field(default_factory=dict)
    description: str = ""

    # -- SR-04/SR-05 evaluation-time expectations (serialized forms) ---------
    #: Serialized :class:`DependencySet` required for every check in this gate.
    expected_dependencies: Mapping[str, object] = field(default_factory=dict)
    #: Serialized :class:`BindingContext` (design/corner binding) for the gate.
    expected_context: Mapping[str, object] = field(default_factory=dict)
    #: Isolated run output directory all evidence artifacts must live under.
    expected_output_dir: str = ""
    #: Serialized :class:`ViewContract` pinning every compatibility dimension.
    view_contract: Mapping[str, object] = field(default_factory=dict)
    require_post_fill_proof: bool = False
    require_extraction_provenance: bool = False

    def dependency_expectation(self) -> DependencySet | None:
        return DependencySet.from_dict(self.expected_dependencies) if self.expected_dependencies else None

    def context_expectation(self) -> BindingContext | None:
        return BindingContext.from_dict(self.expected_context) if self.expected_context else None

    def contract_expectation(self) -> ViewContract | None:
        return ViewContract.from_dict(self.view_contract) if self.view_contract else None

    def validate(self) -> None:
        if not self.gate_id:
            raise GateDefinitionError("GateSpec requires a non-empty gate_id")
        if not self.required_checks:
            raise GateDefinitionError(
                f"gate {self.gate_id!r} has an empty required-check list"
            )
        if len(set(self.required_checks)) != len(self.required_checks):
            raise GateDefinitionError(
                f"gate {self.gate_id!r} has duplicate required checks"
            )
        for cid in self.required_checks:
            if cid not in self.checks:
                raise GateDefinitionError(
                    f"gate {self.gate_id!r}: no CheckSpec for required check {cid!r}"
                )
            self.checks[cid].validate()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["required_checks"] = list(self.required_checks)
        d["required_inputs"] = list(self.required_inputs)
        d["upstream"] = list(self.upstream)
        d["checks"] = {k: v.to_dict() for k, v in self.checks.items()}
        return d

    @classmethod
    def from_dict(cls, d: Mapping) -> "GateSpec":
        data = dict(d)
        data["required_checks"] = tuple(data.get("required_checks") or ())
        data["required_inputs"] = tuple(data.get("required_inputs") or ())
        data["upstream"] = tuple(data.get("upstream") or ())
        data["checks"] = {
            k: CheckSpec.from_dict(v) for k, v in (data.get("checks") or {}).items()
        }
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


#: Registered outcome validators (engineering verdict parsers / internal
#: validators).  A validator returns True only when the parsed outcome is a
#: valid engineering result consistent with the evidence.
OUTCOME_VALIDATORS: dict[str, Callable[[Evidence, Mapping], bool]] = {}


def register_outcome_validator(
    name: str, fn: Callable[[Evidence, Mapping], bool]
) -> None:
    """Register a named parsed-outcome validator used by check specs."""
    if not name or not callable(fn):
        raise GateDefinitionError("outcome validator needs a name and a callable")
    OUTCOME_VALIDATORS[name] = fn


def default_gate_specs() -> dict[str, GateSpec]:
    """Canonical example specifications (not auto-registered).

    Callers/CLI/runner/ECO can register these via ``GateController.register_gate``
    to obtain authoritative, non-shortenable gate evaluation.
    """
    specs = {
        "STA_SIGNOFF": GateSpec(
            gate_id="STA_SIGNOFF",
            required_checks=("sta", "parasitics"),
            required_inputs=("rtl", "sdc"),
            required_view="ROUTED_EXTRACTED",
            checks={
                "sta": CheckSpec(
                    check_id="sta", execution_kind="tool",
                    required_view="ROUTED_EXTRACTED",
                    engineering_validator="sta_verdict",
                ),
                "parasitics": CheckSpec(
                    check_id="parasitics", execution_kind="tool",
                    required_view="ROUTED_EXTRACTED",
                    engineering_validator="parasitic_extraction",
                ),
            },
        ),
        "LEC_SIGNOFF": GateSpec(
            gate_id="LEC_SIGNOFF",
            required_checks=("formal_lec",),
            required_inputs=("rtl", "netlist"),
            checks={
                "formal_lec": CheckSpec(
                    check_id="formal_lec", execution_kind="tool",
                    engineering_validator="equivalence_verdict",
                    waivable=False,  # unresolved functional proof is non-waivable
                ),
            },
        ),
    }
    for spec in specs.values():
        spec.validate()
    return specs


# ---------------------------------------------------------------------------
# Authoritative validation boundary (SR-03 sub-finding 1)
# ---------------------------------------------------------------------------
def validate_gate_submission(
    ev: Evidence,
    *,
    spec: CheckSpec | None = None,
    design_facts: Mapping | None = None,
    engineering_outcome: Mapping | None = None,
    strict_provenance: bool = False,
    required_inputs: Sequence[str] = (),
) -> list[str]:
    """Validate any evidence submission before it is recorded.

    This is the ONE authoritative boundary shared by the direct API, CLI,
    runner and ECO.  CLI callers should invoke it with ``strict_provenance=True``
    (which additionally enforces ``assistant.evidence.validate_provenance``);
    the controller invokes it on every ``record_evidence`` call.  It returns a
    list of validation errors (empty means valid) rather than mutating state.

    NOT_APPLICABLE evidence is validated through ``validate_applicability_reason``
    (which raises ``ApplicabilityError`` on a bad/mis-corroborated reason) and is
    exempt from tool/internal substantive requirements.
    """
    errors: list[str] = []
    if ev.status is Status.NOT_APPLICABLE:
        # U-A: non-waivable functional-proof checks can never be N/A.
        if ev.check_id in NON_WAIVABLE_CHECKS or (spec is not None and not spec.waivable):
            errors.append(
                f"non-waivable check {ev.check_id!r} cannot be NOT_APPLICABLE; "
                "genuine PASS evidence is required"
            )
        # Raises ApplicabilityError when facts are missing/mismatched (reason-only
        # N/A is rejected in this authoritative boundary).
        validate_applicability_reason(ev.applicability_reason or ev.reason, design_facts)
        return errors

    if not (ev.reason or "").strip():
        errors.append("a substantive reason is required")
    if strict_provenance:
        errors.extend(validate_provenance(ev))

    kind = spec.execution_kind if spec is not None else "tool"
    if kind == "tool":
        if spec is not None:
            if ev.exit_code != 0:
                errors.append(
                    f"tool-executed check {spec.check_id} requires an explicit "
                    f"successful process result (exit_code 0), got {ev.exit_code!r}"
                )
            if engineering_outcome is None:
                errors.append(
                    f"tool-executed check {spec.check_id} requires a parsed "
                    "engineering outcome"
                )
            else:
                validator = OUTCOME_VALIDATORS.get(spec.engineering_validator)
                if validator is None:
                    errors.append(
                        f"unknown engineering validator {spec.engineering_validator!r}"
                    )
                elif not validator(ev, engineering_outcome):
                    errors.append(
                        f"engineering outcome rejected for {spec.check_id}"
                    )
        # Legacy/no-spec checks are validated for PASS consistency in
        # evaluate_gate (exit_code=None never yields a clean PASS).
    else:  # internal
        assert spec is not None
        if not spec.validator_type:
            errors.append(f"internal check {spec.check_id} requires a validator_type")
        if not spec.evidence_contract:
            errors.append(f"internal check {spec.check_id} requires an evidence_contract")
        if engineering_outcome is None:
            errors.append(f"internal check {spec.check_id} requires a parsed engineering outcome")
        validator = OUTCOME_VALIDATORS.get(spec.validator_type)
        if spec.validator_type and validator is None:
            errors.append(f"unknown internal validator {spec.validator_type!r}")
        elif validator is not None and engineering_outcome is not None:
            if not validator(ev, engineering_outcome):
                errors.append(f"internal validation rejected for {spec.check_id}")

    for label in required_inputs:
        if label not in ev.input_hashes:
            errors.append(f"evidence is not bound to required input {label!r}")
    return errors


def assert_valid_gate_submission(ev: Evidence, **kwargs) -> None:
    """Raise ``GateValidationError`` if the submission fails validation."""
    errors = validate_gate_submission(ev, **kwargs)
    if errors:
        raise GateValidationError("; ".join(errors))


def evaluate_gate_from_state(
    state_path: str | os.PathLike[str], gate_id: str, required_checks: Sequence[str],
    **kwargs,
) -> "GateDecision":
    """Convenience entry point for CLI/runner/ECO callers."""
    return GateController(state_path).evaluate_gate(gate_id, required_checks, **kwargs)


# ---------------------------------------------------------------------------
# Waiver / decision records
# ---------------------------------------------------------------------------
@dataclass
class Waiver:
    check_id: str
    scope: str
    reason: str
    approved_by: str
    created_utc: str = field(default_factory=utc_now)
    expires_utc: str | None = None
    waiver_id: str = field(
        default_factory=lambda: f"W{utc_now()}".replace(":", "") + uuid.uuid4().hex[:8]
    )
    #: Input hashes captured when the waiver was granted. A later input change
    #: invalidates the waiver (it applies only to the revision it was granted for).
    input_revision: dict[str, str] = field(default_factory=dict)
    policy: str = "unspecified"
    design_scope: str = ""

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
    #: CLEAN_PASS | WAIVED_PASS | NOT_APPLICABLE | BLOCKED -- clean PASS is
    #: distinct from waived and from validated-N/A dispositions (U-A).
    disposition: str = "CLEAN_PASS"
    waived_checks: list[str] = field(default_factory=list)
    na_checks: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.decision == "PASS"

    @property
    def human_summary(self) -> str:
        return f"[{self.disposition}] {self.gate_id}: {self.reason}"

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------
class GateController:
    """Owns evidence records, waivers and gate evaluation for one workspace."""

    def __init__(self, state_path: str | os.PathLike[str]):
        self.state_path = str(state_path)
        self._state: dict = {
            "version": 2,
            "run_status": "ACTIVE",
            "cancel_reason": "",
            "restart_history": [],
            "evidence": {},       # evidence_id -> record
            "check_index": {},    # check_id -> [evidence_id, ...] (ordered)
            "waivers": [],
            "summaries": [],      # advisory only, never read by evaluate_gate
            "gate_history": [],
            "registered_inputs": {},  # label -> path (persisted across restart)
            "design_facts": {},       # validated design facts
            "submissions": {},        # evidence_id -> parsed outcome / spec
            "gate_specs": {},         # persisted registered gate specs
            "check_specs": {},        # persisted registered check specs
            "created_utc": utc_now(),
            "updated_utc": utc_now(),
        }
        if os.path.exists(self.state_path):
            with open(self.state_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self._state.update(loaded)

        # Input registry and design facts live *in state* so they survive a
        # restart (SR-03 sub-finding 9). ``self._inputs`` is a live view.
        self._inputs: dict[str, str] = self._state.setdefault("registered_inputs", {})
        self._state.setdefault("design_facts", {})
        self._state.setdefault("submissions", {})
        self._state.setdefault("gate_specs", {})
        self._state.setdefault("check_specs", {})
        self._state.setdefault("restart_history", [])

        self.gate_specs: dict[str, GateSpec] = {}
        self.check_specs: dict[str, CheckSpec] = {}
        for gid, data in self._state["gate_specs"].items():
            try:
                self.gate_specs[gid] = GateSpec.from_dict(data)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                continue
        for cid, data in self._state["check_specs"].items():
            try:
                self.check_specs[cid] = CheckSpec.from_dict(data)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                continue

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

    def _require_active(self) -> None:
        if self._state["run_status"] == "CANCELLED":
            raise StateError(
                f"run is CANCELLED ({self._state['cancel_reason']}); restart() "
                "before starting new work"
            )

    # -- spec registration ----------------------------------------------------
    def register_check_spec(self, spec: CheckSpec) -> CheckSpec:
        self._require_active()
        spec.validate()
        self.check_specs[spec.check_id] = spec
        self._state["check_specs"][spec.check_id] = spec.to_dict()
        self._save()
        return spec

    def register_gate(self, spec: GateSpec) -> GateSpec:
        """Register an authoritative gate specification (persisted across restart)."""
        self._require_active()
        spec.validate()
        if spec.upstream and set(spec.upstream) == {spec.gate_id}:
            raise GateDefinitionError(
                f"self-dependency detected in gate {spec.gate_id!r}"
            )
        for cid in spec.required_checks:
            self.check_specs[cid] = spec.checks[cid]
            self._state["check_specs"][cid] = spec.checks[cid].to_dict()
        self.gate_specs[spec.gate_id] = spec
        self._state["gate_specs"][spec.gate_id] = spec.to_dict()
        self._save()
        return spec

    # -- input registry -------------------------------------------------------
    def register_input(self, label: str, path: str) -> str:
        """Register a file whose change invalidates dependent evidence."""
        self._require_active()
        self._inputs[label] = str(path)
        self._save()  # persisted so a restarted controller still detects staleness
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

    # -- run output isolation (SR-04) -----------------------------------------
    def prepare_output_dir(
        self, run_id: str, base: str | os.PathLike[str] | None = None, *,
        exist_ok: bool = False,
    ) -> str:
        """Create/validate an isolated output directory for one run.

        Wraps ``evidence.prepare_run_output_dir`` so artifacts from a previous
        run cannot be silently reused. ``artifact_path`` should be used for every
        file written into the returned directory.
        """
        self._require_active()
        root = str(base) if base is not None else os.path.dirname(
            os.path.abspath(self.state_path)
        )
        return prepare_run_output_dir(root, run_id, exist_ok=exist_ok)

    def safe_artifact_path(self, output_dir: str, name: str) -> str:
        """Return a path guaranteed to stay under *output_dir*."""
        return artifact_path(output_dir, name)

    # -- validated design facts ----------------------------------------------
    @property
    def design_facts(self) -> dict:
        return self._state["design_facts"]

    def register_design_fact(
        self, name: str, value: object, *, source: str = "", evidence_ref: str = ""
    ) -> dict:
        """Register a validated design fact used to corroborate N/A decisions."""
        self._require_active()
        if not name:
            raise ValueError("design fact needs a name")
        entry = {"value": value, "source": source, "evidence_ref": evidence_ref}
        self._state["design_facts"][name] = entry
        self._save()
        return entry

    # -- evidence -------------------------------------------------------------
    def record_evidence(
        self,
        ev: Evidence,
        *,
        allow_while_cancelled: bool = False,
        engineering_outcome: Mapping | None = None,
        check_spec: CheckSpec | None = None,
        strict: bool = True,
        simulated: str = "",
    ) -> Evidence:
        """Record evidence through the single authoritative validation boundary.

        Strict provenance validation is the DEFAULT for every substantive status
        (PASS/FAIL/INCONCLUSIVE/UNSUPPORTED/NOT_RUN). The only way to opt out is
        an explicit, labelled ``strict=False, simulated="<why>"`` pair for
        internal/simulated checks; an unlabelled ``strict=False`` is refused so
        no caller can silently drop provenance by accident (U-B).

        NOT_APPLICABLE is never exempt from design-fact corroboration and is
        forbidden outright for non-waivable checks (U-A).

        ``allow_while_cancelled`` is retained for signature compatibility but
        is deliberately ignored: cancellation always blocks new work (SR-03
        sub-finding 14).
        """
        self._require_active()
        if not strict and not simulated.strip():
            raise GateValidationError(
                "strict=False requires an explicit 'simulated' label describing "
                "why the evidence is internal/simulated (no real tool provenance)"
            )
        spec = check_spec or self.check_specs.get(ev.check_id)

        # Central NOT_APPLICABLE validation: a target check id + registered,
        # matching design facts are mandatory; non-waivable checks are refused.
        # Validate BEFORE mutating state so a bad decision is never persisted.
        if ev.status is Status.NOT_APPLICABLE:
            ev.applicability_reason = validate_applicability_for_check(
                ev.applicability_reason or ev.reason, ev.check_id, self.design_facts
            )
            ev.reason = ev.applicability_reason

        errors = validate_gate_submission(
            ev,
            spec=spec,
            design_facts=self.design_facts or None,
            engineering_outcome=engineering_outcome,
            strict_provenance=strict,
        )
        if errors:
            raise GateValidationError("; ".join(errors))

        # Bind evidence to the currently registered inputs (only labels that
        # were registered at record time become staleness anchors).
        if not ev.input_hashes:
            ev.input_hashes = self.current_input_hashes()
        self._state["evidence"][ev.evidence_id] = ev.to_dict()
        idx = self._state["check_index"].setdefault(ev.check_id, [])
        idx.append(ev.evidence_id)
        if spec is not None or engineering_outcome is not None:
            self._state["submissions"][ev.evidence_id] = {
                "check_id": ev.check_id,
                "spec": spec.to_dict() if spec is not None else None,
                "engineering_outcome": dict(engineering_outcome or {}),
            }
        self._save()
        return ev

    def record_check_result(
        self,
        check_id: str,
        status: Status | str,
        reason: str,
        *,
        engineering_outcome: Mapping | None = None,
        exit_code: int | None = None,
        analysis_view: str = "UNKNOWN",
        command: Iterable[str] | str | None = None,
        tool_versions: Mapping[str, str] | None = None,
        reports: Iterable[str] = (),
        logs: Iterable[str] = (),
        artifacts: Iterable[str] = (),
        applicability_reason: str = "",
        spec: CheckSpec | None = None,
        dependencies: DependencySet | None = None,
        binding_context: BindingContext | None = None,
        run_id: str = "",
        design: str = "",
        stage: str = "",
        tool_execution_id: str = "",
        corner: str = "",
        library_provenance: str = "",
        extraction_provenance: str = "",
        strict: bool = True,
        simulated: str = "",
    ) -> Evidence:
        """Convenience API: build + validate + record a check result in one call.

        ``dependencies`` (persistent paths + hashes) and ``binding_context``
        (run/design/stage/tool/view/corner) are attached via the evidence module's
        ``set_dependencies`` / ``bind_context`` so they can be re-validated at
        every gate evaluation. ``strict`` / ``simulated`` are forwarded to
        :meth:`record_evidence` (strict provenance is the default).
        """
        ev = Evidence.from_run(
            check_id=check_id, status=status, reason=reason, command=command,
            exit_code=exit_code, analysis_view=analysis_view,
            tool_versions=tool_versions, reports=reports, logs=logs,
            artifacts=artifacts, applicability_reason=applicability_reason,
            dependencies=dependencies, binding_context=binding_context,
            run_id=run_id, design=design, stage=stage,
            tool_execution_id=tool_execution_id, corner=corner,
            library_provenance=library_provenance,
            extraction_provenance=extraction_provenance,
        )
        return self.record_evidence(
            ev, engineering_outcome=engineering_outcome, check_spec=spec,
            strict=strict, simulated=simulated,
        )

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
        self._require_active()
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

    def mark_not_applicable_for_check(
        self,
        check_id: str,
        reason: str,
        *,
        design_facts: Mapping | None = None,
    ) -> Evidence:
        """CLI-facing helper: record a validated N/A for a target check (U-A).

        ``design_facts`` may be supplied explicitly (e.g. from a CLI inventory)
        or defaults to the controller's registered facts. Non-waivable checks
        and reason-only decisions are refused.
        """
        facts = design_facts if design_facts is not None else self.design_facts
        normalized = validate_applicability_for_check(reason, check_id, facts)
        ev = Evidence(
            check_id=check_id,
            status=Status.NOT_APPLICABLE,
            reason=normalized,
            applicability_reason=normalized,
        )
        return self.record_evidence(ev)

    def add_waiver(self, waiver: Waiver) -> Waiver:
        """Record a waiver with enforced identity, scope, policy and revision."""
        self._require_active()
        if not waiver.check_id.strip():
            raise ValueError("waiver must name a check_id (identity)")
        if len(waiver.reason.strip()) < MIN_WAIVER_REASON_LEN:
            raise ValueError(
                f"waiver reason must be at least {MIN_WAIVER_REASON_LEN} characters"
            )
        if not waiver.scope.strip():
            raise ValueError("waiver must declare an explicit scope")
        if not waiver.approved_by.strip():
            raise ValueError("waiver must record who approved it")
        if waiver.policy not in WAIVER_POLICIES:
            raise ValueError(
                f"unknown waiver policy {waiver.policy!r}; "
                f"expected one of {sorted(WAIVER_POLICIES)}"
            )
        if any(w.get("waiver_id") == waiver.waiver_id for w in self._state["waivers"]):
            raise ValueError(f"duplicate waiver identity {waiver.waiver_id!r}")
        if waiver.expires_utc:
            try:
                _parse_utc(waiver.expires_utc)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid waiver expiry {waiver.expires_utc!r}") from exc
        if not waiver.input_revision and self.current_input_hashes():
            waiver.input_revision = self.current_input_hashes()
        self._state["waivers"].append(waiver.to_dict())
        self._save()
        return waiver

    def _waiver_expired(self, waiver: Waiver) -> bool:
        if not waiver.expires_utc:
            return False
        try:
            return _parse_utc(waiver.expires_utc) <= _dt.datetime.now(_dt.timezone.utc)
        except (TypeError, ValueError):
            return True  # malformed expiry is treated as expired, never active

    def waivers_for(self, gate_id: str, check_id: str) -> list[Waiver]:
        """Only identity/scope/expiry/revision-valid waivers are returned."""
        current = self.current_input_hashes()
        out: list[Waiver] = []
        for w in self._state["waivers"]:
            obj = Waiver.from_dict(w)
            if not obj.covers(gate_id, check_id):
                continue
            if self._waiver_expired(obj):
                continue
            if obj.input_revision and obj.input_revision != current:
                continue  # waiver applies only to the input revision it was granted for
            out.append(obj)
        return out

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
        self._require_active()
        self._assert_no_cycles()  # authoritative dependency graph is cycle-free
        return self._evaluate_gate(
            gate_id, required_checks, stage=stage, required_view=required_view,
            upstream_gates=upstream_gates, visiting=frozenset(),
        )

    def _assert_no_cycles(self) -> None:
        """Detect cycles in the registered gate dependency graph (SR-03 #8)."""
        state: dict[str, int] = {}

        def visit(gid: str, stack: list[str]) -> None:
            mark = state.get(gid, 0)
            if mark == 1:
                raise GateDefinitionError(
                    "dependency cycle detected: " + " -> ".join(stack + [gid])
                )
            if mark == 2:
                return
            state[gid] = 1
            spec = self.gate_specs.get(gid)
            if spec:
                for up in spec.upstream:
                    visit(up, stack + [gid])
            state[gid] = 2

        for gid in sorted(self.gate_specs):
            visit(gid, [])

    def _evaluate_gate(
        self,
        gate_id: str,
        required_checks: Sequence[str],
        *,
        stage: str,
        required_view: str | None,
        upstream_gates: Iterable[str],
        visiting: frozenset[str],
    ) -> GateDecision:
        self._require_active()
        if gate_id in visiting:
            chain = " -> ".join(list(visiting) + [gate_id])
            raise GateDefinitionError(f"dependency cycle detected: {chain}")

        spec = self.gate_specs.get(gate_id)
        # The registered spec is authoritative: callers cannot omit checks.
        effective = list(spec.required_checks) if spec else list(required_checks)
        if not effective:
            raise GateDefinitionError(
                f"gate {gate_id!r} has an empty required-check list"
            )
        if spec and set(effective) != set(required_checks):
            # Record that the caller's list was overridden by the spec, but do
            # NOT let a shorter caller list reduce the enforced set.
            pass

        here = visiting | {gate_id}
        view = required_view or (spec.required_view if spec else None)
        upstream: list[str] = list(upstream_gates)
        if spec:
            for up in spec.upstream:
                if up not in upstream:
                    upstream.append(up)

        current = self.current_input_hashes()
        check_report: dict[str, dict] = {}
        blocking: list[str] = []
        waived_visible: list[dict] = []
        na_visible: list[dict] = []
        waived_checks: list[str] = []
        na_checks: list[str] = []  # valid N/A checks -> disposition NOT_APPLICABLE

        # Registered input requirements must be registered and bound.
        if spec:
            missing_registered = [lbl for lbl in spec.required_inputs if lbl not in self._inputs]
            if missing_registered:
                blocking.append(
                    f"gate requires unregistered inputs: {', '.join(missing_registered)}"
                )
                check_report["inputs:registered"] = {
                    "status": Status.NOT_RUN.value,
                    "reason": "required inputs not registered: "
                    + ", ".join(missing_registered),
                }

        # Upstream gates must be RE-EVALUATED from current evidence, not trusted
        # from a historical PASS (SR-03 sub-findings 4 and 8).
        for up in upstream:
            hist = self._last_decision(up)
            if hist is None:
                blocking.append(f"upstream gate {up} not PASS")
                check_report[f"upstream:{up}"] = {
                    "status": Status.NOT_RUN.value,
                    "reason": "upstream gate has no PASS decision",
                }
                continue
            hist_checks = hist.get("required_checks") or [
                k for k in hist.get("checks", {}) if not k.startswith("upstream:")
            ]
            hist_view = hist.get("required_view")
            re_dec = self._evaluate_gate(
                up, hist_checks, stage=hist.get("stage", ""),
                required_view=hist_view,
                upstream_gates=hist.get("upstream_gates", ()),
                visiting=here,
            )
            if not re_dec.passed:
                blocking.append(f"upstream gate {up} fails re-evaluation: {re_dec.reason}")
                check_report[f"upstream:{up}"] = {
                    "status": "BLOCKED",
                    "reason": re_dec.reason,
                    "disposition": re_dec.disposition,
                }

        for check_id in effective:
            cspec = (spec.checks.get(check_id) if spec else None) or self.check_specs.get(check_id)
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
            submission = self._state.get("submissions", {}).get(ev.evidence_id)
            outcome = submission.get("engineering_outcome") if submission else None

            # Consistency guards: an exit code alone never proves correctness.
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
            else:
                entry["reason"] = ev.reason

            # PASS without an explicit successful process result is never a
            # clean PASS, except for a fully contracted internal check.
            internal_ok = bool(
                cspec and cspec.execution_kind == "internal"
                and cspec.validator_type and cspec.evidence_contract
                and outcome is not None
                and cspec.validator_type in OUTCOME_VALIDATORS
                and OUTCOME_VALIDATORS[cspec.validator_type](ev, outcome)
            )
            if status is Status.PASS and ev.exit_code is None and not internal_ok:
                status = Status.FAIL
                entry["reason"] = (
                    "PASS recorded without an explicit successful process result "
                    "(exit_code=None)"
                )

            # Tool / internal execution contracts are re-checked at evaluation.
            if status is Status.PASS and cspec is not None:
                if cspec.execution_kind == "tool":
                    validator = OUTCOME_VALIDATORS.get(cspec.engineering_validator)
                    if outcome is None or validator is None or not validator(ev, outcome):
                        status = Status.FAIL
                        entry["reason"] = (
                            f"tool check {check_id} lacks a valid parsed engineering outcome"
                        )
                elif not internal_ok:
                    status = Status.FAIL
                    entry["reason"] = (
                        f"internal check {check_id} lacks a valid validator/contract"
                    )

            # Registered view / input binding constraints.
            check_view = (cspec.required_view if cspec and cspec.required_view else view)
            if status is Status.PASS and check_view and not view_compatible(
                check_view, ev.analysis_view
            ):
                status = Status.FAIL
                entry["reason"] = (
                    f"analysis view mismatch: gate requires {check_view}, "
                    f"evidence is {ev.analysis_view}"
                )
            if status is Status.PASS and spec:
                unbound = [lbl for lbl in spec.required_inputs if lbl not in ev.input_hashes]
                if unbound:
                    status = Status.FAIL
                    entry["reason"] = (
                        "evidence not bound to required inputs: " + ", ".join(unbound)
                    )

            # ---- SR-04/SR-05 evaluation-time revalidation -------------------
            # Re-hash dependencies AND artifacts from their persistent paths and
            # check run/design/stage/tool/view/corner binding. Recorded metadata
            # is never trusted: any problem makes the evidence STALE/invalid and
            # hard-blocks the check (it can never become a clean PASS, nor be
            # waived away). With no expectations configured this is still called
            # so that modified/missing artifacts are caught for every gate.
            rv_problems: list[str] = []
            if status is not Status.NOT_APPLICABLE:
                exp_deps = (
                    (cspec.dependency_expectation() if cspec and cspec.expected_dependencies else None)
                    or (spec.dependency_expectation() if spec else None)
                )
                exp_ctx = (
                    (cspec.context_expectation() if cspec and cspec.expected_context else None)
                    or (spec.context_expectation() if spec else None)
                )
                exp_dir = (
                    (cspec.expected_output_dir if cspec and cspec.expected_output_dir else "")
                    or (spec.expected_output_dir if spec else "")
                    or None
                )
                rv_problems = revalidate_evidence(
                    ev,
                    expected_dependencies=exp_deps,
                    expected_context=exp_ctx,
                    expected_output_dir=exp_dir,
                )
            rv_non_waivable = bool(rv_problems)
            if rv_problems:
                entry["revalidation_problems"] = rv_problems
                blocking.append(
                    f"{check_id}: STALE/invalid evidence: " + "; ".join(rv_problems)
                )
                if status is Status.PASS:
                    status = Status.NOT_RUN
                    entry["reason"] = "STALE/invalid evidence: " + "; ".join(rv_problems)

            # Explicit, multi-dimensional view contracts. The legacy total
            # order is never used here: a physical database cannot satisfy an
            # extracted-timing gate, a GR estimate cannot satisfy routed
            # extraction, and post-fill needs filled+extracted+analysed proof.
            if status is Status.PASS:
                req_contract = (
                    (cspec.contract_expectation() if cspec and cspec.view_contract else None)
                    or (spec.contract_expectation() if spec else None)
                )
                need_fill = bool(
                    (cspec and cspec.require_post_fill_proof)
                    or (spec and spec.require_post_fill_proof)
                )
                if req_contract is not None:
                    actual_contract = evidence_view_contract(ev)
                    if not contract_compatible(req_contract, actual_contract):
                        status = Status.FAIL
                        entry["reason"] = (
                            "view contract mismatch: required "
                            + str(req_contract.to_dict())
                            + ", evidence "
                            + str(actual_contract.to_dict())
                        )
                if need_fill and status is Status.PASS:
                    need_prov = bool(
                        (cspec and cspec.require_extraction_provenance)
                        or (spec and spec.require_extraction_provenance)
                    )
                    if not post_fill_proof_ok(
                        evidence_view_contract(ev),
                        require_extraction_provenance=need_prov,
                    ):
                        status = Status.FAIL
                        entry["reason"] = (
                            "post-fill proof required: need a filled, extracted, "
                            "analysed report"
                            + (" with extraction provenance" if need_prov else "")
                        )

            non_waivable = bool(
                check_id in NON_WAIVABLE_CHECKS or (cspec and not cspec.waivable)
            )

            # U-A: non-waivable checks can NEVER be NOT_APPLICABLE. A persisted
            # legacy N/A record must be converted to a hard-blocking FAIL so no
            # gate can pass on an applicability claim for a functional proof.
            if non_waivable and status is Status.NOT_APPLICABLE:
                status = Status.FAIL
                entry["non_waivable"] = True
                entry["reason"] = (
                    "non-waivable check cannot be NOT_APPLICABLE; "
                    "genuine PASS evidence is required"
                )
                blocking.append(
                    f"{check_id}: non-waivable check declared NOT_APPLICABLE"
                )
                check_report[check_id] = {
                    **entry,
                    "status": status.value,
                    "evidence_id": ev.evidence_id,
                    "analysis_view": ev.analysis_view,
                    "exit_code": ev.exit_code,
                }
                continue

            if status is Status.NOT_APPLICABLE:
                # validated at record time against registered design facts
                na_visible.append(
                    {"check": check_id, "reason": ev.applicability_reason}
                )
                na_checks.append(check_id)

            applicable_waivers = [
                w for w in waivers
                if not non_waivable
                and not rv_non_waivable
                and (spec is None or w.policy != "unspecified")
            ]
            waived = bool(applicable_waivers) and blocks(status)
            if applicable_waivers:
                waived_visible.extend(w.to_dict() for w in applicable_waivers)
            if non_waivable and blocks(status) and waivers:
                entry["non_waivable"] = True
                blocking.append(
                    f"{check_id}: non-waivable functional proof failure "
                    f"({status.value})"
                )
                check_report[check_id] = {
                    **entry,
                    "status": status.value,
                    "evidence_id": ev.evidence_id,
                    "analysis_view": ev.analysis_view,
                    "exit_code": ev.exit_code,
                }
                continue

            entry.update(
                status=status.value,
                evidence_id=ev.evidence_id,
                analysis_view=ev.analysis_view,
                exit_code=ev.exit_code,
            )
            check_report[check_id] = entry

            if blocks(status, waived=waived):
                blocking.append(f"{check_id}: {status.value}")
            if waived:
                waived_checks.append(check_id)

        decision = "PASS" if not blocking else "BLOCKED"
        if blocking:
            disposition = "BLOCKED"
            reason = "; ".join(blocking)
        elif waived_checks:
            disposition = "WAIVED_PASS"
            reason = "PASS with waived checks: " + ", ".join(waived_checks)
        elif na_checks:
            # U-A: a gate satisfied partly/wholly by validated N/A is NOT a clean PASS.
            disposition = "NOT_APPLICABLE"
            reason = "PASS with validated NOT_APPLICABLE checks: " + ", ".join(na_checks)
        else:
            disposition = "CLEAN_PASS"
            reason = "all required checks PASS"

        gd = GateDecision(
            gate_id=gate_id,
            decision=decision,
            reason=reason,
            checks=check_report,
            waivers=waived_visible,
            advisory_summaries=len(self._state["summaries"]),
            disposition=disposition,
            waived_checks=waived_checks,
            na_checks=na_checks,
        )
        self._state["gate_history"].append(
            {
                "stage": stage,
                "required_checks": effective,
                "required_view": view,
                "upstream_gates": list(upstream),
                **gd.to_dict(),
            }
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

    def restart(self, reason: str = "", *, operator: str = "") -> str:
        """Reopen a cancelled run, preserving all recorded evidence.

        A *valid* restart records who/why; this is surfaced in the report and
        gate history. The no-argument form is retained for compatibility.
        """
        prev = self._state["run_status"]
        self._state["run_status"] = "ACTIVE"
        self._state["cancel_reason"] = ""
        self._state["restart_history"].append(
            {"from": prev, "reason": reason, "operator": operator,
             "created_utc": utc_now()}
        )
        self._save()
        return f"run restarted from {prev}; evidence preserved"

    @property
    def run_status(self) -> str:
        return self._state["run_status"]

    def report(self) -> dict:
        """Full visible state: evidence, waivers (visible), summaries (advisory)."""
        history = list(self._state["gate_history"])
        last = history[-1] if history else {}
        return {
            "run_status": self._state["run_status"],
            "cancel_reason": self._state["cancel_reason"],
            "inputs": dict(self._inputs),
            "current_input_hashes": self.current_input_hashes(),
            "design_facts": dict(self._state["design_facts"]),
            "evidence_count": len(self._state["evidence"]),
            "waivers": list(self._state["waivers"]),
            "advisory_summaries": list(self._state["summaries"]),
            "gate_history": history,
            "restart_history": list(self._state["restart_history"]),
            "last_gate_disposition": last.get("disposition", "NONE"),
        }
