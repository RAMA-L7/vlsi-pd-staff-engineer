"""Status semantics: blocking, precedence, parsing."""
import pytest

from assistant.statuses import (
    BLOCKING_STATUSES,
    Status,
    blocks,
    parse_status,
    summarize,
)


def test_all_six_statuses_exist():
    assert {s.value for s in Status} == {
        "PASS",
        "FAIL",
        "INCONCLUSIVE",
        "UNSUPPORTED",
        "NOT_RUN",
        "NOT_APPLICABLE",
    }


@pytest.mark.parametrize(
    "value,expected",
    [
        ("pass", Status.PASS),
        ("FAIL", Status.FAIL),
        (" inconclusive ", Status.INCONCLUSIVE),
        ("UNSUPPORTED", Status.UNSUPPORTED),
        ("not_run", Status.NOT_RUN),
        ("NOT_APPLICABLE", Status.NOT_APPLICABLE),
        (Status.PASS, Status.PASS),
    ],
)
def test_parse_status(value, expected):
    assert parse_status(value) is expected


def test_parse_status_rejects_unknown():
    with pytest.raises(ValueError):
        parse_status("GREEN")


def test_blocking_matrix():
    # NOT_APPLICABLE never blocks
    assert not blocks(Status.NOT_APPLICABLE)
    # PASS never blocks
    assert not blocks(Status.PASS)
    # everything else blocks when required
    for st in (Status.FAIL, Status.INCONCLUSIVE, Status.UNSUPPORTED, Status.NOT_RUN):
        assert blocks(st)
        assert st in BLOCKING_STATUSES


def test_waiver_makes_nonblocking_but_is_separate():
    # a waived blocking status stops blocking; NOT_APPLICABLE with waiver too
    assert not blocks(Status.FAIL, waived=True)
    assert not blocks(Status.NOT_APPLICABLE, waived=True)


def test_summarize_precedence():
    assert summarize([]) is Status.NOT_RUN
    assert summarize([Status.PASS, Status.PASS]) is Status.PASS
    assert summarize([Status.PASS, Status.NOT_APPLICABLE]) is Status.NOT_APPLICABLE
    assert summarize([Status.PASS, Status.NOT_RUN]) is Status.NOT_RUN
    assert summarize([Status.NOT_RUN, Status.UNSUPPORTED]) is Status.UNSUPPORTED
    assert summarize([Status.UNSUPPORTED, Status.INCONCLUSIVE]) is Status.INCONCLUSIVE
    assert summarize([Status.INCONCLUSIVE, Status.FAIL]) is Status.FAIL
