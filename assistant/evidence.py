"""Evidence records: every check result bound to exact inputs and provenance.

An evidence record answers, deterministically:

* which exact inputs (RTL/SDC/netlists/...) produced it - by SHA-256
* which configuration / constraint / library / PDK files were in effect
* which tool versions ran
* what command ran, and its exit status
* which analysis view the result belongs to
* where the logs, reports and output artifacts are (hashes included)
* what the gate decision was, and why
"""
from __future__ import annotations

import datetime as _dt
import os
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Mapping

from .hashing import sha256_file
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

#: Ordered view compatibility: a gate requiring view V accepts evidence whose
#: view is V or a *later* (more complete) view in this ordering.
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


def view_compatible(required: str | None, actual: str) -> bool:
    """Estimates cannot satisfy a routed-extraction requirement."""
    if required is None:
        return True
    if required not in VIEW_ORDER or actual not in VIEW_ORDER:
        return required == actual
    return VIEW_ORDER.index(actual) >= VIEW_ORDER.index(required)


class ProvenanceError(ValueError):
    """Raised when CLI-supplied evidence provenance is invalid/incomplete."""


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
    ) -> "Evidence":
        if isinstance(command, str):
            cmd = [command]
        else:
            cmd = list(command or [])
        return cls(
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
        )

    # -- staleness ------------------------------------------------------------
    def stale_inputs(self, current: Mapping[str, str]) -> list[str]:
        """Labels whose current hash differs from the recorded evidence hash.

        A label present in *current* but absent from the record is NOT stale
        (it was not an input at the time); a label recorded but now changed or
        missing IS stale.
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
        return [r["path"] for r in self.reports if r["sha256"] == "<missing>"]

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
