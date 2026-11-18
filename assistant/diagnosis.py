"""Read-only diagnosis: evidence-linked analysis with strict epistemic labels.

Three categories are kept apart everywhere in this module:

* **observation** - directly parsed from a recorded artifact (deterministic).
* **hypothesis** - a candidate explanation; requires a discriminating test.
* **heuristic** - a rule-of-thumb prioritisation aid, NOT a conclusion.

No credentials, no network, no live inference are used.  ``summarize_for_llm``
produces a clearly-labelled *advisory* text block that the gate controller
stores but never consults for decisions (finding A).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Mapping, Sequence

from .evidence import Evidence
from .statuses import Status

# ---------------------------------------------------------------------------
# Parsers (deterministic)
# ---------------------------------------------------------------------------

# OpenSTA / OpenROAD worst-slack markers.  Real 12_sta.sh reports emit
# "setup_wns <num>" / "hold_wns <num>" (via `puts`) and "<num> slack (MET)"
# (via report_checks).  Legacy reports used "worst_slack: <num>" and
# "slack (MET) <num>".  Both spellings are accepted (SR-07 U3).
WNS_RE = re.compile(
    r"\b(?:worst_slack|setup_wns|hold_wns|wns)\b\s*[:=]?\s*(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
TNS_RE = re.compile(
    r"\b(?:total_negative_slack|setup_tns|hold_tns|tns)\b\s*[:=]?\s*(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
SLACK_RE = re.compile(
    r"(?:(-?\d+(?:\.\d+)?)\s+slack\s*\((?:MET|VIOLATED)\)"
    r"|slack\s*\((?:MET|VIOLATED)\)\s+(-?\d+(?:\.\d+)?))",
    re.IGNORECASE,
)
CLOCK_RE = re.compile(r"\bcreate_clock\b|\bcreate_generated_clock\b", re.IGNORECASE)
# OpenSTA `check_setup` (its check_timing equivalent) output plus explicit
# markers emitted by 12_sta.sh.  Any constraint-sanity warning is a structural
# rejection, never a PASS.
UNCONSTRAINED_RE = re.compile(
    r"\bwarning\s*:|unconstrained|not constrained|no paths found|"
    r"missing\s+set_(?:input|output)_delay|(?:input|output) ports? missing|"
    r"is not a valid startpoint|has no clock|no\s+(?:related\s+)?clock|"
    r"missing clock|not clocked|unmapped|cannot find port|"
    r"check_timing_verdict:\s*unconstrained|"
    r"check_timing_verdict:\s*probe_failed",
    re.IGNORECASE,
)
UNRESOLVED_RE = re.compile(
    r"unresolved reference|no such cell|cell .* not found|"
    r"cannot find cell|link failed",
    re.IGNORECASE,
)
ERROR_RE = re.compile(r"^ERROR:|\berror:\b", re.IGNORECASE | re.MULTILINE)


@dataclass
class ParsedSlack:
    wns: float | None = None
    tns: float | None = None
    path_slacks: list[float] = field(default_factory=list)
    has_clock: bool = False
    unconstrained_hits: list[str] = field(default_factory=list)
    unresolved_hits: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    malformed: bool = False

    @property
    def has_any_slack(self) -> bool:
        return self.wns is not None or self.tns is not None or bool(self.path_slacks)


def parse_sta_report(text: str) -> ParsedSlack:
    """Parse an STA report, flagging malformed/absent information.

    Distinguishes *analysis ran* (text present, parseable) from *meets
    targets* (caller compares numbers to targets).
    """
    p = ParsedSlack()
    if not text or not text.strip():
        p.malformed = True
        p.errors.append("empty report")
        return p

    m = WNS_RE.search(text)
    if m:
        try:
            p.wns = float(m.group(1))
        except ValueError:
            p.malformed = True
            p.errors.append(f"unparseable WNS token: {m.group(0)!r}")
    elif re.search(r"\b(?:worst_slack|WNS)\s*[:=]", text):
        # the marker is present but carries no parseable number -> malformed
        p.malformed = True
        p.errors.append("WNS marker present but no numeric value follows")
    m = TNS_RE.search(text)
    if m:
        try:
            p.tns = float(m.group(1))
        except ValueError:
            p.malformed = True
            p.errors.append(f"unparseable TNS token: {m.group(0)!r}")
    elif re.search(r"\b(?:tns|TNS)\s*[:=]", text):
        p.malformed = True
        p.errors.append("TNS marker present but no numeric value follows")

    for m in SLACK_RE.finditer(text):
        value = m.group(1) if m.group(1) is not None else m.group(2)
        try:
            p.path_slacks.append(float(value))
        except (TypeError, ValueError):
            p.malformed = True

    p.has_clock = bool(CLOCK_RE.search(text)) or bool(
        re.search(r"\bclk\b", text) and re.search(r"clock", text, re.IGNORECASE)
    )

    p.unconstrained_hits = sorted({h.strip() for h in UNCONSTRAINED_RE.findall(text)})
    p.unresolved_hits = sorted({h.strip() for h in UNRESOLVED_RE.findall(text)})
    p.errors.extend(m.group(0) for m in ERROR_RE.finditer(text))
    return p


def sta_verdict(
    text: str,
    *,
    target_wns_ns: float = 0.0,
    expect_paths: bool = True,
    require_clock: bool = True,
) -> dict:
    """Deterministic STA verdict separating 'ran' from 'meets targets'.

    Returns status + reasons per the canonical statuses.
    """
    p = parse_sta_report(text)
    reasons: list[str] = []

    if p.malformed:
        return {
            "status": Status.FAIL.value,
            "ran": False,
            "meets_targets": False,
            "reasons": p.errors or ["malformed/empty STA report"],
            "parsed": asdict(p),
        }

    # Structural rejections - the analysis cannot be trusted.
    if require_clock and not p.has_clock:
        reasons.append("no clock definition found in report")
    if p.unresolved_hits:
        reasons.append("unresolved cells/ports: " + ", ".join(p.unresolved_hits[:5]))
    if not p.has_any_slack:
        reasons.append("report contains no slack values (analysis may not have run)")
    if expect_paths and not p.path_slacks and p.wns is None:
        reasons.append("expected timing paths but none reported")

    # Any unconstrained-path indicator is a structural rejection.  In
    # particular it must never fall through to the "worst slack meets target"
    # branch and yield PASS (SR-07 U3).
    if p.unconstrained_hits:
        reasons.append("unconstrained-path indicators: " + ", ".join(p.unconstrained_hits[:3]))

    if reasons:
        status = Status.FAIL.value
        meets = False
    else:
        # Ran successfully; now does it meet targets?
        worst = p.wns if p.wns is not None else (
            min(p.path_slacks) if p.path_slacks else None
        )
        if worst is None:
            status = Status.INCONCLUSIVE.value
            meets = False
            reasons.append("analysis ran but no numeric slack available")
        elif worst >= target_wns_ns - 1e-9:
            status = Status.PASS.value
            meets = True
            reasons.append(f"worst slack {worst} ns meets target {target_wns_ns} ns")
        else:
            status = Status.FAIL.value
            meets = False
            reasons.append(
                f"analysis ran but worst slack {worst} ns misses target "
                f"{target_wns_ns} ns"
            )

    return {
        "status": status,
        "ran": not p.malformed and p.has_any_slack,
        "meets_targets": meets,
        "reasons": reasons,
        "parsed": asdict(p),
    }


# ---------------------------------------------------------------------------
# Diagnosis structures
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    kind: str  # observation | hypothesis | heuristic
    text: str
    evidence_refs: list[str] = field(default_factory=list)
    confidence: str = "n/a"          # only meaningful for hypotheses
    discriminating_test: str = ""    # how to confirm/refute a hypothesis

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DiagnosisReport:
    subject: str
    observations: list[Finding] = field(default_factory=list)
    hypotheses: list[Finding] = field(default_factory=list)
    heuristics: list[Finding] = field(default_factory=list)
    proposed_ecos: list[dict] = field(default_factory=list)
    required_verifications: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "observations": [f.to_dict() for f in self.observations],
            "hypotheses": [f.to_dict() for f in self.hypotheses],
            "heuristics": [f.to_dict() for f in self.heuristics],
            "proposed_ecos": list(self.proposed_ecos),
            "required_verifications": list(self.required_verifications),
            "disclaimer": (
                "Read-only diagnosis. Observations are parsed from artifacts; "
                "hypotheses are unconfirmed; heuristics are rules of thumb. "
                "No gate decision is derived from this report, and no summary "
                "generated from it can authorize any gate transition."
            ),
        }


def diagnose_timing(
    report_text: str,
    *,
    check_id: str = "sta",
    target_wns_ns: float = 0.0,
    previous_wns_ns: float | None = None,
) -> DiagnosisReport:
    """Evidence-linked timing diagnosis (read-only)."""
    verdict = sta_verdict(report_text, target_wns_ns=target_wns_ns)
    p = parse_sta_report(report_text)
    rep = DiagnosisReport(subject=f"timing:{check_id}")

    # --- observations (parsed facts) ---------------------------------------
    if p.wns is not None:
        rep.observations.append(
            Finding("observation", f"reported WNS = {p.wns} ns", [check_id])
        )
    if p.tns is not None:
        rep.observations.append(
            Finding("observation", f"reported TNS = {p.tns} ns", [check_id])
        )
    if p.path_slacks:
        worst_i = min(range(len(p.path_slacks)), key=lambda i: p.path_slacks[i])
        rep.observations.append(
            Finding(
                "observation",
                f"{len(p.path_slacks)} path slacks parsed; worst = "
                f"{p.path_slacks[worst_i]} ns",
                [check_id],
            )
        )
    if not p.has_clock:
        rep.observations.append(
            Finding("observation", "no clock definition found in report", [check_id])
        )
    for hit in p.unresolved_hits[:5]:
        rep.observations.append(
            Finding("observation", f"unresolved reference indicator: {hit}", [check_id])
        )
    rep.observations.append(
        Finding(
            "observation",
            f"verdict={verdict['status']} ran={verdict['ran']} "
            f"meets_targets={verdict['meets_targets']}",
            [check_id],
        )
    )

    # --- hypotheses (each with a discriminating test) ----------------------
    if verdict["status"] == Status.FAIL.value and not verdict["meets_targets"]:
        if p.wns is not None and p.wns < target_wns_ns:
            rep.hypotheses.append(
                Finding(
                    "hypothesis",
                    f"real setup/hold violation (WNS {p.wns} < target {target_wns_ns})",
                    [check_id],
                    confidence="medium",
                    discriminating_test=(
                        "inspect worst path startpoint/endpoint and check "
                        "constraint (clock period, uncertainty) values"
                    ),
                )
            )
        if previous_wns_ns is not None and p.wns is not None and p.wns < previous_wns_ns:
            rep.hypotheses.append(
                Finding(
                    "hypothesis",
                    f"regression vs previous run (WNS {previous_wns_ns} -> {p.wns})",
                    [check_id],
                    confidence="medium",
                    discriminating_test=(
                        "diff inputs/SDC/lib hashes between the two runs; "
                        "if unchanged, diff netlist and floorplan"
                    ),
                )
            )
        if not p.has_clock:
            rep.hypotheses.append(
                Finding(
                    "hypothesis",
                    "missing or ineffective clock constraint (analysis structurally invalid)",
                    [check_id],
                    confidence="high",
                    discriminating_test="confirm create_clock present in SDC and read by STA",
                )
            )
        if p.unresolved_hits:
            rep.hypotheses.append(
                Finding(
                    "hypothesis",
                    "unresolved cells mean link failures - numbers are not trustworthy",
                    [check_id],
                    confidence="high",
                    discriminating_test="check read_liberty/link reports for the named cells",
                )
            )

    # --- heuristics (explicitly labelled) -----------------------------------
    if p.path_slacks:
        rep.heuristics.append(
            Finding(
                "heuristic",
                "RULE OF THUMB: if >50% of reported paths cluster within 100 ps "
                "of the worst slack, the bottleneck is likely a common stage "
                "rather than one path - prioritise shared logic review",
                [check_id],
            )
        )
    if previous_wns_ns is None:
        rep.heuristics.append(
            Finding(
                "heuristic",
                "RULE OF THUMB: without a previous-run WNS, regression detection "
                "is unavailable; treat single-run numbers as baseline only",
                [check_id],
            )
        )

    rep.proposed_ecos.append(
        {
            "change": "none (read-only)",
            "rationale": "diagnosis proposes bounded experiments only after a hypothesis is confirmed",
            "requires_authorization": False,
        }
    )
    rep.required_verifications = [
        "re-run STA after any change and compare against protected metrics",
        "verify inputs/config/constraint/lib hashes before attributing a delta",
    ]
    return rep


def diagnose_stage_delta(
    before: Mapping[str, float | None],
    after: Mapping[str, float | None],
    *,
    worse_is: str = "lower",
) -> DiagnosisReport:
    """Compare stage metrics (e.g. WNS/area/cells) between two runs."""
    rep = DiagnosisReport(subject="stage-delta")
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        if b is None or a is None:
            rep.observations.append(
                Finding(
                    "observation",
                    f"{key}: before={b} after={a} (missing value - not comparable)",
                )
            )
            continue
        delta = a - b
        direction = "improved" if (delta > 0) == (worse_is == "higher") else "worsened"
        if abs(delta) < 1e-12:
            direction = "unchanged"
        rep.observations.append(
            Finding("observation", f"{key}: {b} -> {a} (delta {delta:+g}, {direction})")
        )
        if direction == "worsened":
            rep.hypotheses.append(
                Finding(
                    "hypothesis",
                    f"{key} worsened; candidate sources: input change, constraint "
                    f"change, library/tool change, physical change",
                    confidence="low",
                    discriminating_test=(
                        "compare recorded input/config/constraint/library hashes "
                        "of the two runs; the first differing hash is the prime suspect"
                    ),
                )
            )
    rep.heuristics.append(
        Finding(
            "heuristic",
            "RULE OF THUMB: attribute a metric delta to the FIRST differing hash "
            "in input -> constraint -> library -> tool order; verify before believing",
        )
    )
    return rep


def likely_regression_sources(evidence_seq: Sequence[Evidence]) -> list[dict]:
    """Order candidate regression sources by hash-difference recency."""
    if len(evidence_seq) < 2:
        return []
    prev, cur = evidence_seq[-2], evidence_seq[-1]
    sources: list[dict] = []
    for field_name, label in (
        ("input_hashes", "input"),
        ("constraint_hashes", "constraint"),
        ("config_hashes", "config"),
        ("library_hashes", "library"),
        ("pdk_hashes", "pdk"),
    ):
        pmap = getattr(prev, field_name) or {}
        cmap = getattr(cur, field_name) or {}
        diffs = sorted(
            k for k in set(pmap) | set(cmap) if pmap.get(k) != cmap.get(k)
        )
        if diffs:
            sources.append({"category": label, "changed": diffs})
    return sources


def summarize_for_llm(report: DiagnosisReport) -> str:
    """Render an ADVISORY summary. Never used for gate decisions."""
    lines = [f"# ADVISORY diagnosis summary (subject: {report.subject})",
             "This text is advisory only and cannot authorize any gate transition.", ""]
    for title, items in (
        ("Observations", report.observations),
        ("Hypotheses (unconfirmed)", report.hypotheses),
        ("Heuristics (rules of thumb)", report.heuristics),
    ):
        lines.append(f"## {title}")
        for f in items:
            suffix = f" [confidence: {f.confidence}]" if f.confidence != "n/a" else ""
            lines.append(f"- {f.text}{suffix}")
            if f.discriminating_test:
                lines.append(f"  - test: {f.discriminating_test}")
        lines.append("")
    if report.proposed_ecos:
        lines.append("## Proposed bounded ECOs")
        for e in report.proposed_ecos:
            lines.append(f"- {e.get('change')}: {e.get('rationale')}")
    return "\n".join(lines)
