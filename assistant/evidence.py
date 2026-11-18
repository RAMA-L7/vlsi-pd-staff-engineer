"""Evidence records: every check result bound to exact inputs and provenance.

An evidence record answers, deterministically:

* which exact inputs (RTL/SDC/netlists/...) produced it - by SHA-256
* which configuration / constraint / library / PDK files were in effect
* which tool versions ran
* what command ran, and its exit status
* which analysis view the result belongs to
* where the logs, reports and output artifacts are (hashes included)
* what the gate decision was, and why

Remediation note (SR-04 / SR-05, second review)
-----------------------------------------------
The original module only checked ``input_hashes`` for staleness, read missing
reports from *recorded metadata* instead of the current file, had no unified
dependency model with persistent paths, and used a *total order* for view
compatibility in which ``PHYSICAL_DATABASE`` sorted after ``FILL_EXTRACTED``
and therefore falsely satisfied extracted-timing gates.

This module now provides additive APIs that close those gaps while keeping the
pre-existing public API (``Evidence.from_run``, ``view_compatible``,
``validate_provenance``, ``bind_file``, ``hash_named_files``, ...) intact so the
controller, CLI and existing tests keep working:

* ``Dependency`` / ``DependencySet`` - one model for RTL, SDC, netlists,
  libraries, PDK, config, flow scripts, tool identity and extraction/deck
  collateral, storing *persistent paths* and hashes.  ``DependencySet``
  re-hashes from disk on demand and reports missing/unreadable/modified/
  re-bound dependencies (``validate_current``).
* ``BindingContext`` + ``Evidence.bind_context`` / ``Evidence.binding_errors``
  - bind a report to run, design, stage, tool execution, analysis view, corner
  and library/extraction provenance.  Cross-design and cross-corner reuse is
  rejected explicitly.
* ``Evidence.revalidate_artifacts`` - re-hashes reports/logs/artifacts from
  their persistent paths at evaluation time (no metadata-only checks).
* ``revalidate_evidence`` - the single evaluation-time helper that combines
  dependency re-hashing, artifact re-hashing, binding checks and run-output
  isolation.  Gate controllers/CLI should call this at ``evaluate_gate``
  time; until they do, the old controller path remains functional but
  weaker (documented in the module docstring of ``controller.py`` by its
  owner).
* ``prepare_run_output_dir`` / ``assert_clean_output_dir`` / ``artifact_path``
  - isolate each run's output directory against stale artifact reuse.
* ``ViewContract`` / ``contract_compatible`` / ``post_fill_proof_ok`` -
  explicit, multi-dimensional compatibility contracts.  ``view_compatible``
  is retained (same two-argument signature) but now delegates to the contract
  model instead of a total order:

  CONTRACT CHANGE: ``PHYSICAL_DATABASE`` no longer satisfies extracted-timing
  gates; a GR/place estimate no longer satisfies routed extraction; corner
  and provenance mismatches are rejected; and post-fill gates require a
  filled, extracted, analysed report (not a database).
"""
from __future__ import annotations

import datetime as _dt
import os
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Mapping, Sequence

from .hashing import sha256_file, sha256_bytes
from .statuses import Status, parse_status

#: Analysis views - estimates can never satisfy routed/fill extraction gates.
VIEWS = (
    "RTL",
    "GENERIC_NETLIST",
    "MAPPED_NETLIST",
    "PLACE_ESTIMATE",
    "GR_ESTIMATE",
    "ROUTED_EXTRACTED",
    "FILL_EXTRACTED",
    "PHYSICAL_DATABASE",
    "UNKNOWN",
)

#: Legacy total order kept only as serialized-data reference / documentation.
#: NEVER use this for compatibility decisions - it is unsound (a physical
#: database sorted last and therefore "satisfied" every timing view).
#: ``view_compatible`` and ``contract_compatible`` are the only validators.
VIEW_ORDER = [
    "RTL",
    "GENERIC_NETLIST",
    "MAPPED_NETLIST",
    "PLACE_ESTIMATE",
    "GR_ESTIMATE",
    "ROUTED_EXTRACTED",
    "FILL_EXTRACTED",
    "PHYSICAL_DATABASE",
]


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_entry(path: str) -> dict:
    try:
        return {
            "path": str(path),
            "sha256": sha256_file(path),
            "size": os.path.getsize(path),
        }
    except OSError:
        return {"path": str(path), "sha256": "<missing>", "size": 0}


# ---------------------------------------------------------------------------
# Explicit view compatibility contracts (SR-05)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ViewContract:
    """Multi-dimensional contract for an analysis artifact.

    Dimensions are deliberately independent.  A gate states what it *needs*;
    evidence states what it *provides*; ``contract_compatible`` checks each
    dimension explicitly.  There is no single "later view wins" ordering, so
    a physical database cannot satisfy an extracted-timing requirement and a
    GR estimate cannot satisfy routed extraction.

    Dimensions
    ----------
    artifact_type:
        ``source`` / ``netlist`` / ``timing_report`` / ``database`` /
        ``physical_check`` / ``unknown``.  A database is NOT a timing report.
    implementation_stage:
        ``rtl`` < ``generic`` < ``mapped`` < ``place`` < ``route`` < ``fill``.
    analysis_type:
        ``none`` / ``synthesis`` / ``formal`` / ``sta_setup`` / ``sta_hold`` /
        ``both`` / ``extraction`` / ``drc`` / ``lvs`` / ``ir_drop`` ...
    parasitic_source:
        ``none`` / ``estimate_place`` / ``estimate_gr`` /
        ``extracted_routed`` / ``extracted_filled``.
    fill_status:
        ``unfilled`` or ``filled``.
    timing_mode:
        ``setup`` / ``hold`` / ``both`` / ``""`` (unspecified).
    corner:
        PVT corner identity; ``""`` means unspecified.  A non-empty required
        corner must match the actual corner exactly (no cross-corner reuse).
    library_provenance / extraction_provenance:
        Identity of the Liberty set and extraction deck/ruleset respectively;
        ``""`` means unspecified.  A non-empty required provenance must match.
    """

    artifact_type: str
    implementation_stage: str
    analysis_type: str
    parasitic_source: str
    fill_status: str
    timing_mode: str = ""
    corner: str = ""
    library_provenance: str = ""
    extraction_provenance: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ViewContract":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})  # type: ignore[attr-defined]


#: Implementation-stage ranking (only valid within the same artifact class).
STAGE_RANK: dict[str, int] = {
    "": -1,
    "rtl": 0,
    "generic": 1,
    "mapped": 2,
    "place": 3,
    "route": 4,
    "fill": 5,
}

#: Which parasitic sources satisfy a required source.  Extraction always
#: dominates estimation; a GR estimate never satisfies routed extraction, and
#: neither an estimate nor a database satisfies extracted timing.
PARASITIC_SATISFIES: dict[str, frozenset[str]] = {
    "none": frozenset({"none"}),
    "estimate_place": frozenset(
        {"estimate_place", "estimate_gr", "extracted_routed", "extracted_filled"}
    ),
    "estimate_gr": frozenset({"estimate_gr", "extracted_routed", "extracted_filled"}),
    "extracted_routed": frozenset({"extracted_routed", "extracted_filled"}),
    "extracted_filled": frozenset({"extracted_filled"}),
}

#: Fill status satisfaction: a filled gate needs filled proof; unfilled gates
#: accept either (filled is strictly more complete).
FILL_SATISFIES: dict[str, frozenset[str]] = {
    "unfilled": frozenset({"unfilled", "filled"}),
    "filled": frozenset({"filled"}),
}


def _any(value: str) -> bool:
    return not value or value in ("any", "*", "ANY")


def _analysis_ok(required: str, actual: str) -> bool:
    if _any(required) or required == actual:
        return True
    if actual == "both" and required in ("setup", "hold", "sta_setup", "sta_hold"):
        return True
    if required == "sta" and actual in (
        "setup", "hold", "both", "sta_setup", "sta_hold", "extraction_sta"
    ):
        return True
    return False


def _parasitic_ok(required: str, actual: str) -> bool:
    if _any(required):
        return True
    allowed = PARASITIC_SATISFIES.get(required)
    return actual in allowed if allowed is not None else required == actual


def _fill_ok(required: str, actual: str) -> bool:
    if _any(required):
        return True
    return actual in FILL_SATISFIES.get(required, frozenset({required}))


def _timing_mode_ok(required: str, actual: str) -> bool:
    if _any(required) or required == actual or actual == "both":
        return True
    # A single-mode result must never satisfy a "both modes" requirement.
    return False


def contract_compatible(required: ViewContract, actual: ViewContract) -> bool:
    """True only if *actual* explicitly satisfies every dimension of *required*.

    This is the authoritative compatibility check.  It replaces the old
    single ``VIEW_ORDER`` index comparison, which let ``PHYSICAL_DATABASE``
    (sorted last) satisfy every gate, including extracted timing.
    """
    if not _any(required.artifact_type) and required.artifact_type != actual.artifact_type:
        return False
    if STAGE_RANK.get(required.implementation_stage, -1) > STAGE_RANK.get(
        actual.implementation_stage, -1
    ):
        return False
    if not _analysis_ok(required.analysis_type, actual.analysis_type):
        return False
    if not _parasitic_ok(required.parasitic_source, actual.parasitic_source):
        return False
    if not _fill_ok(required.fill_status, actual.fill_status):
        return False
    if not _timing_mode_ok(required.timing_mode, actual.timing_mode):
        return False
    if not _any(required.corner) and required.corner != actual.corner:
        return False
    if not _any(required.library_provenance) and (
        required.library_provenance != actual.library_provenance
    ):
        return False
    if not _any(required.extraction_provenance) and (
        required.extraction_provenance != actual.extraction_provenance
    ):
        return False
    return True


def post_fill_proof_ok(
    contract: ViewContract, *, require_extraction_provenance: bool = False
) -> bool:
    """True only for a *filled, extracted, analysed* proof artifact.

    Post-fill signoff must not be satisfied by a physical database or by a
    routed-but-unfilled extraction.  Set ``require_extraction_provenance`` for
    real signoff gates so an extraction deck identity is mandatory.
    """
    return (
        contract.artifact_type in ("timing_report", "extracted_timing")
        and contract.analysis_type
        in ("setup", "hold", "both", "sta_setup", "sta_hold", "sta", "extraction_sta")
        and contract.parasitic_source == "extracted_filled"
        and contract.fill_status == "filled"
        and (
            not require_extraction_provenance or bool(contract.extraction_provenance)
        )
    )


def _vc(
    artifact_type: str,
    stage: str,
    analysis: str,
    parasitic: str,
    fill: str,
    mode: str = "",
) -> ViewContract:
    return ViewContract(artifact_type, stage, analysis, parasitic, fill, mode)


#: Canonical contracts for the legacy view names, used by ``view_compatible``
#: so the two-argument API keeps working with sound semantics.  Corner and
#: provenance are intentionally unspecified here; real gates should build an
#: explicit ViewContract (or rely on ``BindingContext``) to pin them.
VIEW_CONTRACTS: dict[str, ViewContract] = {
    "RTL": _vc("source", "rtl", "none", "none", "unfilled"),
    "GENERIC_NETLIST": _vc("netlist", "generic", "synthesis", "none", "unfilled"),
    "MAPPED_NETLIST": _vc("netlist", "mapped", "synthesis", "none", "unfilled"),
    "PLACE_ESTIMATE": _vc(
        "timing_report", "place", "sta_setup", "estimate_place", "unfilled", "setup"
    ),
    "GR_ESTIMATE": _vc(
        "timing_report", "route", "sta_setup", "estimate_gr", "unfilled", "setup"
    ),
    "ROUTED_EXTRACTED": _vc(
        "timing_report", "route", "sta_setup", "extracted_routed", "unfilled", "setup"
    ),
    "FILL_EXTRACTED": _vc(
        "timing_report", "fill", "sta_setup", "extracted_filled", "filled", "setup"
    ),
    # A database is an artifact, not timing analysis: it has no parasitics and
    # cannot satisfy an extracted-timing / post-fill gate.
    "PHYSICAL_DATABASE": _vc("database", "fill", "none", "none", "unfilled"),
    "UNKNOWN": _vc("unknown", "unknown", "unknown", "unknown", "unknown"),
}


def view_compatible(required: str | ViewContract | None, actual: str | ViewContract) -> bool:
    """Whether evidence for *actual* satisfies a requirement for *required*.

    CONTRACT CHANGE (SR-05): this used to be a total order over ``VIEW_ORDER``;
    ``PHYSICAL_DATABASE`` therefore satisfied every extracted-timing gate.  It
    now delegates to explicit contracts.  The two-string signature is retained
    for the controller/CLI/tests:

    * ``PHYSICAL_DATABASE`` never satisfies extracted-timing gates,
    * ``GR_ESTIMATE`` never satisfies routed extraction,
    * a routed result does not satisfy a fill gate,
    * unknown names fall back to strict equality.

    Callers needing corner / library / extraction-deck provenance checks should
    pass :class:`ViewContract` objects to :func:`contract_compatible` directly
    (and use :func:`post_fill_proof_ok` for post-fill signoff).
    """
    if required is None:
        return True
    req = required if isinstance(required, ViewContract) else VIEW_CONTRACTS.get(required)
    act = actual if isinstance(actual, ViewContract) else VIEW_CONTRACTS.get(actual)
    if req is None or act is None:
        return required == actual
    return contract_compatible(req, act)


# ---------------------------------------------------------------------------
# Error / provenance helpers (existing API, extended below)
# ---------------------------------------------------------------------------
class ProvenanceError(ValueError):
    """Raised when CLI-supplied evidence provenance is invalid/incomplete."""


class StaleOutputError(ProvenanceError):
    """Raised when a run output directory may contain stale artifacts."""


def bind_file(path: str) -> dict:
    """Return ``{path, sha256}`` for an *existing* file.

    Unlike the internal ``_file_entry`` helper used by ``from_run`` (which
    tolerates missing files as ``<missing>``), this refuses missing or
    unreadable files so the CLI cannot silently record unbound artefacts.
    """
    try:
        sha = sha256_file(path)
    except OSError as exc:
        raise ProvenanceError(
            f"file does not exist or is unreadable: {path!r}"
        ) from exc
    return {"path": str(path), "sha256": sha}


def _parse_pairs(items: Iterable[str], *, kind: str) -> dict[str, str]:
    """Parse repeatable ``NAME=VALUE`` CLI tokens into a dict."""
    out: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ProvenanceError(
                f"expected NAME={kind} token, got {item!r}"
            )
        name, value = item.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name:
            raise ProvenanceError(f"empty name in {item!r}")
        if not value:
            raise ProvenanceError(f"empty {kind} in {item!r}")
        out[name] = value
    return out


def parse_tool_versions(items: Iterable[str]) -> dict[str, str]:
    """Parse repeatable ``NAME=VERSION`` tokens (non-empty name + version)."""
    return _parse_pairs(items, kind="version")


def hash_named_files(items: Iterable[str]) -> dict[str, str]:
    """Parse repeatable ``NAME=PATH`` tokens into ``{name: sha256}``.

    Files must exist; missing/unreadable files raise ``ProvenanceError``.

    NOTE (SR-04): this legacy helper stores only hashes, not persistent paths,
    so it cannot be re-hashed at evaluation time.  New callers should use
    :class:`DependencySet` (``from_paths`` / ``add_file``), which records both
    the path and the hash for every dependency category.
    """
    pairs = _parse_pairs(items, kind="path")
    hashed: dict[str, str] = {}
    for name, path in pairs.items():
        try:
            hashed[name] = sha256_file(path)
        except OSError as exc:
            raise ProvenanceError(
                f"file does not exist or is unreadable: {path!r}"
            ) from exc
    return hashed


# ---------------------------------------------------------------------------
# Unified dependency model (SR-04, hypotheses 3-5)
# ---------------------------------------------------------------------------
#: Every dependency class the flow must be able to pin.  ``tool`` is an
#: identity (version string), not a file; ``extraction_deck`` and
#: ``collateral`` cover extraction rules, DRC/LVS decks and other helper data.
DEPENDENCY_CATEGORIES: tuple[str, ...] = (
    "rtl",
    "sdc",
    "netlist",
    "library",
    "pdk",
    "config",
    "script",
    "tool",
    "extraction_deck",
    "collateral",
)


@dataclass(frozen=True)
class Dependency:
    """One pinned dependency with a persistent path and a content hash.

    ``value`` is used only by ``tool`` dependencies: it stores the tool version
    string whose SHA-256 is recorded in ``sha256`` so tool identity can be
    revalidated without a file on disk.
    """

    category: str
    label: str
    path: str
    sha256: str
    size: int = 0
    value: str = ""

    @property
    def key(self) -> str:
        return f"{self.category}:{self.label}"


class DependencySet:
    """A manifest of :class:`Dependency` entries for one evidence record.

    Files are hashed when added and can be re-hashed from their persisted
    paths at evaluation time via :meth:`validate_current`.  Missing,
    unreadable, modified, re-path'd or newly-required dependencies are all
    reported as problems (an empty list means the manifest is still valid).
    """

    def __init__(self, dependencies: Iterable[Dependency] = ()) -> None:
        self._deps: dict[str, Dependency] = {}
        for dep in dependencies:
            if dep.key in self._deps:
                raise ProvenanceError(f"duplicate dependency key: {dep.key}")
            self._deps[dep.key] = dep

    # -- construction ---------------------------------------------------------
    @classmethod
    def from_paths(cls, by_category: Mapping[str, Mapping[str, str]]) -> "DependencySet":
        """Hash ``{category: {label: path}}`` into a manifest.

        Missing/unreadable files are rejected up front (unlike
        ``hash_mapping``'s ``<missing>`` sentinel) so nothing partial is bound.
        """
        deps: list[Dependency] = []
        for category, items in by_category.items():
            for label, path in items.items():
                p = str(path)
                try:
                    sha = sha256_file(p)
                    size = os.path.getsize(p)
                except OSError as exc:
                    raise ProvenanceError(
                        f"dependency file does not exist or is unreadable: {p!r}"
                    ) from exc
                deps.append(
                    Dependency(category, label, os.path.abspath(p), sha, size)
                )
        return cls(deps)

    def add_file(self, category: str, label: str, path: str) -> Dependency:
        """Hash and add a file dependency at its absolute persistent path."""
        p = os.path.abspath(str(path))
        try:
            sha = sha256_file(p)
            size = os.path.getsize(p)
        except OSError as exc:
            raise ProvenanceError(
                f"dependency file does not exist or is unreadable: {p!r}"
            ) from exc
        dep = Dependency(category, label, p, sha, size)
        self._deps[dep.key] = dep
        return dep

    def add_files(self, category: str, mapping: Mapping[str, str]) -> None:
        for label, path in mapping.items():
            self.add_file(category, label, path)

    def add_tool(self, label: str, version: str) -> Dependency:
        """Pin a tool identity by hashing its version string."""
        version = str(version)
        if not version:
            raise ProvenanceError("tool version must be non-empty")
        dep = Dependency(
            "tool", label, "<identity>", sha256_bytes(version.encode("utf-8")), 0, version
        )
        self._deps[dep.key] = dep
        return dep

    # -- serialization --------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "version": 1,
            "dependencies": [
                asdict(self._deps[k]) for k in sorted(self._deps)
            ],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "DependencySet":
        deps: list[Dependency] = []
        for item in d.get("dependencies", []):
            deps.append(
                Dependency(
                    category=str(item.get("category", "")),
                    label=str(item.get("label", "")),
                    path=str(item.get("path", "")),
                    sha256=str(item.get("sha256", "")),
                    size=int(item.get("size", 0) or 0),
                    value=str(item.get("value", "") or ""),
                )
            )
        return cls(deps)

    # -- inspection -----------------------------------------------------------
    def __len__(self) -> int:
        return len(self._deps)

    def __iter__(self):
        return iter(self._deps.values())

    def keys(self) -> set[str]:
        return set(self._deps)

    def hashes(self) -> dict[str, str]:
        return {k: d.sha256 for k, d in self._deps.items()}

    def manifest(self) -> dict[str, tuple[str, str, str]]:
        return {
            k: (d.category, d.label, d.path) for k, d in self._deps.items()
        }

    # -- revalidation ---------------------------------------------------------
    def rehash_current(self) -> dict[str, Dependency]:
        """Re-read every dependency from disk and return current entries."""
        current: dict[str, Dependency] = {}
        for key, dep in self._deps.items():
            if dep.category == "tool":
                sha = (
                    sha256_bytes(dep.value.encode("utf-8"))
                    if dep.value
                    else "<missing>"
                )
                current[key] = Dependency(
                    dep.category, dep.label, dep.path, sha, 0, dep.value
                )
                continue
            try:
                sha = sha256_file(dep.path)
                size = os.path.getsize(dep.path)
            except OSError:
                sha, size = "<missing>", 0
            current[key] = Dependency(dep.category, dep.label, dep.path, sha, size, dep.value)
        return current

    def validate_current(self, expected: "DependencySet | None" = None) -> list[str]:
        """Re-hash dependencies and report every invalidation.

        Detects missing, unreadable and modified files/tool identities.  When
        *expected* is supplied, the recorded manifest must match it exactly:
        a dependency that was not bound at recording time, a re-pointed path,
        a changed hash, or an old report re-bound to new input hashes are all
        rejected (SR-04 hypotheses 1, 5, 6).
        """
        problems: list[str] = []
        current = self.rehash_current()
        for key, dep in self._deps.items():
            now = current[key]
            if now.sha256 == "<missing>":
                if dep.category == "tool" and not dep.value:
                    kind = "unreadable"
                else:
                    kind = "missing" if not os.path.exists(dep.path) else "unreadable"
                problems.append(f"{key}: dependency {kind}: {dep.path!r}")
            elif now.sha256 != dep.sha256:
                problems.append(
                    f"{key}: dependency modified "
                    f"(recorded {dep.sha256[:12]}, current {now.sha256[:12]})"
                )
            elif dep.size and now.size and now.size != dep.size:
                problems.append(f"{key}: dependency size changed: {dep.path!r}")

        if expected is not None:
            recorded_keys = set(self._deps)
            wanted_keys = set(expected._deps)
            for key in sorted(wanted_keys - recorded_keys):
                problems.append(f"{key}: required dependency was not bound to evidence")
            for key in sorted(recorded_keys - wanted_keys):
                problems.append(f"{key}: evidence binds a dependency not required now")
            for key in sorted(wanted_keys & recorded_keys):
                want = expected._deps[key]
                have = self._deps[key]
                if (want.category, want.label, want.path) != (
                    have.category, have.label, have.path
                ):
                    problems.append(f"{key}: dependency path/category rebind detected")
                elif want.sha256 != have.sha256:
                    problems.append(
                        f"{key}: input hash changed since recording (rebind refused)"
                    )
        return problems


# ---------------------------------------------------------------------------
# Run/artifact binding (SR-04, hypotheses 2, 7, 8, 9)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BindingContext:
    """Identity of the execution a report belongs to.

    A report is only reusable when *all* of these agree with the gate being
    evaluated.  In particular ``design`` and ``corner`` prevent cross-design
    and cross-corner evidence reuse, and ``tool_execution_id`` prevents an old
    tool run's report from being reused for a new invocation.
    """

    run_id: str
    design: str
    stage: str = ""
    tool_execution_id: str = ""
    analysis_view: str = "UNKNOWN"
    corner: str = ""
    library_provenance: str = ""
    extraction_provenance: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "BindingContext":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})  # type: ignore[attr-defined]


def validate_provenance(ev: "Evidence") -> list[str]:
    """Return completeness errors for a CLI-recorded evidence record.

    Substantive statuses (everything except ``NOT_APPLICABLE``) must carry a
    command, an explicit exit code, a concrete analysis view, at least one
    bound (existing) report/log/artifact, and at least one tool version.
    ``NOT_APPLICABLE`` evidence needs only a validated reason (checked by the
    controller), so it is exempt here.

    This is intentionally opt-in for the CLI path only; direct API callers
    using ``Evidence.from_run`` remain usable for tests and internal flows.
    """
    if ev.status is Status.NOT_APPLICABLE:
        return []
    errs: list[str] = []
    if not ev.command or not any(str(c).strip() for c in ev.command):
        errs.append("a non-empty command is required")
    if ev.exit_code is None:
        errs.append("exit-code must be provided explicitly")
    if not ev.analysis_view or ev.analysis_view == "UNKNOWN":
        errs.append("a concrete analysis view is required")
    artefacts = list(ev.reports or []) + list(ev.logs or []) + list(ev.artifacts or [])
    bound = [
        a for a in artefacts
        if isinstance(a, dict) and a.get("sha256") not in (None, "", "<missing>")
    ]
    if not bound:
        errs.append("at least one existing report, log or artifact is required")
    if not ev.tool_versions:
        errs.append("at least one tool version is required")
    return errs


def _same_path(root: str, path: str) -> bool:
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:  # different drives on Windows
        return False


@dataclass
class Evidence:
    check_id: str
    status: Status
    reason: str
    command: list[str] = field(default_factory=list)
    exit_code: int | None = None
    analysis_view: str = "UNKNOWN"
    tool_versions: dict[str, str] = field(default_factory=dict)
    input_hashes: dict[str, str] = field(default_factory=dict)
    config_hashes: dict[str, str] = field(default_factory=dict)
    constraint_hashes: dict[str, str] = field(default_factory=dict)
    library_hashes: dict[str, str] = field(default_factory=dict)
    pdk_hashes: dict[str, str] = field(default_factory=dict)
    logs: list[dict] = field(default_factory=list)
    reports: list[dict] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    gate_decision: str = ""
    gate_reason: str = ""
    waivers: list[dict] = field(default_factory=list)
    applicability_reason: str = ""
    duration_s: float | None = None
    created_utc: str = field(default_factory=utc_now)
    evidence_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    # -- SR-04 additions (all optional => backward compatible) ---------------
    #: Serialized :class:`DependencySet` (persistent paths + hashes).
    dependencies: dict[str, Any] = field(default_factory=dict)
    #: Run / design / stage / tool-execution / corner binding identity.
    run_id: str = ""
    design: str = ""
    stage: str = ""
    tool_execution_id: str = ""
    corner: str = ""
    library_provenance: str = ""
    extraction_provenance: str = ""

    # -- construction helpers -------------------------------------------------
    @classmethod
    def from_run(
        cls,
        check_id: str,
        status: Status | str,
        reason: str,
        *,
        command: Iterable[str] | str | None = None,
        exit_code: int | None = None,
        analysis_view: str = "UNKNOWN",
        tool_versions: Mapping[str, str] | None = None,
        input_hashes: Mapping[str, str] | None = None,
        config_hashes: Mapping[str, str] | None = None,
        constraint_hashes: Mapping[str, str] | None = None,
        library_hashes: Mapping[str, str] | None = None,
        pdk_hashes: Mapping[str, str] | None = None,
        logs: Iterable[str] = (),
        reports: Iterable[str] = (),
        artifacts: Iterable[str] = (),
        duration_s: float | None = None,
        applicability_reason: str = "",
        dependencies: DependencySet | Mapping[str, Any] | None = None,
        binding_context: BindingContext | None = None,
        run_id: str = "",
        design: str = "",
        stage: str = "",
        tool_execution_id: str = "",
        corner: str = "",
        library_provenance: str = "",
        extraction_provenance: str = "",
    ) -> "Evidence":
        if isinstance(command, str):
            cmd = [command]
        else:
            cmd = list(command or [])
        if isinstance(dependencies, DependencySet):
            dep_dict = dependencies.to_dict()
        elif dependencies is not None:
            dep_dict = dict(dependencies)
        else:
            dep_dict = {}
        ev = cls(
            check_id=check_id,
            status=parse_status(status),
            reason=reason,
            command=cmd,
            exit_code=exit_code,
            analysis_view=analysis_view,
            tool_versions=dict(tool_versions or {}),
            input_hashes=dict(input_hashes or {}),
            config_hashes=dict(config_hashes or {}),
            constraint_hashes=dict(constraint_hashes or {}),
            library_hashes=dict(library_hashes or {}),
            pdk_hashes=dict(pdk_hashes or {}),
            logs=[_file_entry(p) for p in logs],
            reports=[_file_entry(p) for p in reports],
            artifacts=[_file_entry(p) for p in artifacts],
            duration_s=duration_s,
            applicability_reason=applicability_reason,
            dependencies=dep_dict,
            run_id=run_id,
            design=design,
            stage=stage,
            tool_execution_id=tool_execution_id,
            corner=corner,
            library_provenance=library_provenance,
            extraction_provenance=extraction_provenance,
        )
        if binding_context is not None:
            ev.bind_context(binding_context)
        return ev

    # -- dependency / binding helpers ----------------------------------------
    def set_dependencies(self, deps: DependencySet) -> "Evidence":
        """Attach a dependency manifest (persistent paths + hashes)."""
        self.dependencies = deps.to_dict()
        return self

    def dependency_set(self) -> DependencySet:
        return (
            DependencySet.from_dict(self.dependencies)
            if self.dependencies
            else DependencySet()
        )

    def bind_context(self, ctx: BindingContext) -> "Evidence":
        """Bind this evidence to a run/design/stage/tool execution/view/corner."""
        self.run_id = ctx.run_id
        self.design = ctx.design
        self.stage = ctx.stage
        self.tool_execution_id = ctx.tool_execution_id
        self.analysis_view = ctx.analysis_view or self.analysis_view
        self.corner = ctx.corner
        self.library_provenance = ctx.library_provenance
        self.extraction_provenance = ctx.extraction_provenance
        return self

    def binding_context(self) -> BindingContext:
        return BindingContext(
            run_id=self.run_id,
            design=self.design,
            stage=self.stage,
            tool_execution_id=self.tool_execution_id,
            analysis_view=self.analysis_view,
            corner=self.corner,
            library_provenance=self.library_provenance,
            extraction_provenance=self.extraction_provenance,
        )

    def binding_errors(self, expected: BindingContext) -> list[str]:
        """Return mismatches against *expected* (cross-design/corner reuse fails)."""
        errs: list[str] = []

        def check(name: str, have: str, want: str) -> None:
            if _any(want):
                return
            if not have:
                errs.append(f"{name}: evidence is not bound to a value (expected {want!r})")
            elif have != want:
                errs.append(f"{name}: mismatch (evidence {have!r}, expected {want!r})")

        check("run_id", self.run_id, expected.run_id)
        check("design", self.design, expected.design)
        check("stage", self.stage, expected.stage)
        check("tool_execution_id", self.tool_execution_id, expected.tool_execution_id)
        check("analysis_view", self.analysis_view, expected.analysis_view)
        check("corner", self.corner, expected.corner)
        check("library_provenance", self.library_provenance, expected.library_provenance)
        check("extraction_provenance", self.extraction_provenance, expected.extraction_provenance)
        return errs

    # -- staleness ------------------------------------------------------------
    def stale_inputs(self, current: Mapping[str, str]) -> list[str]:
        """Labels whose current hash differs from the recorded evidence hash.

        A label present in *current* but absent from the record is NOT stale
        (it was not an input at the time); a label recorded but now changed or
        missing IS stale.

        NOTE (SR-04): this legacy check covers ``input_hashes`` only.  Use
        :meth:`dependency_set` / :func:`revalidate_evidence` to rehash every
        dependency category and the evidence artifacts as well.
        """
        stale: list[str] = []
        for label, recorded in self.input_hashes.items():
            now = current.get(label)
            if now is None or now != recorded:
                stale.append(label)
        return stale

    def is_stale(self, current: Mapping[str, str]) -> bool:
        return bool(self.stale_inputs(current))

    def missing_report_artifacts(self) -> list[str]:
        """Legacy metadata-only check (kept for the existing controller)."""
        return [r["path"] for r in self.reports if r["sha256"] == "<missing>"]

    def revalidate_artifacts(self) -> list[str]:
        """Re-hash reports/logs/artifacts from their persisted paths *now*.

        Fixes SR-04 hypothesis 2: a report file that was present at recording
        but has since been deleted, replaced or made unreadable is reported
        here instead of relying on the stale ``sha256`` metadata field.
        """
        problems: list[str] = []
        for kind in ("reports", "logs", "artifacts"):
            for entry in getattr(self, kind) or []:
                path = str(entry.get("path", ""))
                recorded = entry.get("sha256")
                if not path:
                    problems.append(f"{kind}: artifact has no persistent path")
                    continue
                try:
                    sha = sha256_file(path)
                except OSError:
                    why = "missing" if not os.path.exists(path) else "unreadable"
                    problems.append(f"{kind}: artifact {why}: {path!r}")
                    continue
                if recorded in (None, "", "<missing>"):
                    problems.append(f"{kind}: artifact was unbound at recording: {path!r}")
                elif sha != recorded:
                    problems.append(f"{kind}: artifact modified: {path!r}")
        return problems

    def artifacts_outside_dir(self, output_dir: str) -> list[str]:
        """Flag artifacts pointing outside *output_dir* (stale-reuse guard)."""
        root = os.path.realpath(str(output_dir))
        problems: list[str] = []
        for kind in ("reports", "logs", "artifacts"):
            for entry in getattr(self, kind) or []:
                path = str(entry.get("path", ""))
                if not path:
                    problems.append(f"{kind}: artifact has no persistent path")
                    continue
                if not _same_path(root, os.path.realpath(path)):
                    problems.append(
                        f"{kind}: artifact outside run output directory: {path!r}"
                    )
        return problems

    # -- serialization --------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Evidence":
        data = dict(d)
        data["status"] = parse_status(data.get("status"))
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        data = {k: v for k, v in data.items() if k in known}
        return cls(**data)


def revalidate_evidence(
    ev: Evidence,
    *,
    expected_dependencies: DependencySet | None = None,
    expected_context: BindingContext | None = None,
    expected_output_dir: str | None = None,
) -> list[str]:
    """Evaluate-time validation of one evidence record.

    Returns a list of problems; an empty list means the record is still valid
    and may be used for a gate decision.  This helper is what the controller
    and CLI should call from ``evaluate_gate`` (they currently call only the
    weaker ``stale_inputs`` / metadata checks).  It combines:

    * artifact re-hashing (``Evidence.revalidate_artifacts``),
    * dependency re-hashing and required-manifest matching
      (``DependencySet.validate_current``),
    * run/design/stage/tool/view/corner binding checks,
    * run-output-directory isolation.

    It never *rewrites* recorded hashes: a changed input is always reported as
    a problem, so an old report cannot be silently re-bound to new hashes.
    """
    problems: list[str] = []
    problems.extend(ev.revalidate_artifacts())

    recorded = ev.dependency_set()
    if len(recorded):
        problems.extend(recorded.validate_current(expected_dependencies))
    elif expected_dependencies is not None and len(expected_dependencies):
        problems.append("evidence has no dependency manifest but dependencies are required")

    if expected_context is not None:
        problems.extend(ev.binding_errors(expected_context))

    if expected_output_dir is not None:
        problems.extend(ev.artifacts_outside_dir(expected_output_dir))
    return problems


# ---------------------------------------------------------------------------
# Run output directory isolation (SR-04, hypothesis 8)
# ---------------------------------------------------------------------------
def assert_clean_output_dir(path: str | os.PathLike[str]) -> str:
    """Ensure *path* exists as a directory and contains nothing (no stale reuse)."""
    p = str(path)
    if os.path.exists(p):
        if not os.path.isdir(p):
            raise StaleOutputError(f"run output path is not a directory: {p!r}")
        if os.listdir(p):
            raise StaleOutputError(f"run output directory is not empty: {p!r}")
    else:
        os.makedirs(p, exist_ok=True)
    return p


def prepare_run_output_dir(
    base: str | os.PathLike[str], run_id: str, *, exist_ok: bool = False
) -> str:
    """Create an isolated ``base/run_id`` output directory for one run.

    By default a non-empty directory is rejected so evidence from a previous
    run cannot be reused.  Pass ``exist_ok=True`` only when intentionally
    resuming a run whose directory was already validated.
    """
    path = os.path.join(str(base), str(run_id))
    if os.path.exists(path) and not exist_ok:
        return assert_clean_output_dir(path)
    os.makedirs(path, exist_ok=True)
    return path


def artifact_path(output_dir: str | os.PathLike[str], name: str) -> str:
    """Return a path for *name* guaranteed to stay under *output_dir*."""
    root = os.path.realpath(str(output_dir))
    path = os.path.realpath(os.path.join(root, str(name)))
    if not _same_path(root, path):
        raise StaleOutputError(f"artifact path escapes output directory: {name!r}")
    return path
