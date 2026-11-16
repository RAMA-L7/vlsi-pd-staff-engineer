"""Canonical result statuses for checks and gates.

PASS / FAIL            - the check ran and produced a verdict
INCONCLUSIVE           - ran, but the evidence does not support a verdict
UNSUPPORTED            - the capability is not implemented in this flow
NOT_RUN                - no (valid) evidence exists
NOT_APPLICABLE         - explicitly not applicable, requires a validated reason
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    UNSUPPORTED = "UNSUPPORTED"
    NOT_RUN = "NOT_RUN"
    NOT_APPLICABLE = "NOT_APPLICABLE"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: Statuses that block a required check (and therefore block the gate).
BLOCKING_STATUSES: frozenset[Status] = frozenset(
    {
        Status.FAIL,
        Status.INCONCLUSIVE,
        Status.UNSUPPORTED,
        Status.NOT_RUN,
    }
)

#: Statuses that are terminal verdicts (evidence exists and is final).
TERMINAL_STATUSES: frozenset[Status] = frozenset(
    {Status.PASS, Status.FAIL, Status.INCONCLUSIVE, Status.UNSUPPORTED}
)


def parse_status(value: object) -> Status:
    """Parse a status from a string (case-insensitive) or a Status."""
    if isinstance(value, Status):
        return value
    if isinstance(value, str):
        for st in Status:
            if value.strip().upper() == st.value:
                return st
    raise ValueError(f"unknown status: {value!r}")


def blocks(status: Status, waived: bool = False) -> bool:
    """True when *status* prevents a required check from passing.

    A validated NOT_APPLICABLE never blocks.  A waiver makes a blocking
    status non-blocking but must remain visible to the gate report.
    """
    status = parse_status(status)
    if status is Status.NOT_APPLICABLE:
        return False
    if waived:
        return False
    return status in BLOCKING_STATUSES


def summarize(statuses: Iterable[Status]) -> Status:
    """Roll a set of check statuses up into a single verdict.

    Precedence: FAIL > INCONCLUSIVE > UNSUPPORTED > NOT_RUN >
    NOT_APPLICABLE > PASS.  An empty set is NOT_RUN.
    """
    vals = [parse_status(s) for s in statuses]
    if not vals:
        return Status.NOT_RUN
    for st in (
        Status.FAIL,
        Status.INCONCLUSIVE,
        Status.UNSUPPORTED,
        Status.NOT_RUN,
        Status.NOT_APPLICABLE,
    ):
        if st in vals:
            return st
    return Status.PASS
