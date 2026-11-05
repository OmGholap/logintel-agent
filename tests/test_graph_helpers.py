from datetime import datetime

import pytest

from logintel.catalog import CatalogRow
from logintel.context import build_context
from logintel.graph import (
    MAX_RARE, _check_times, _parse_iso, format_catalog, rare_budget, strip_id_refs,
)
from logintel.parser import parse_lines
from conftest import line


def events(n_info, n_error):
    rows = [line(f"12:{i // 60:02d}:{i % 60:02d}", "T", "INFO", "s", f"i{i}") for i in range(n_info)]
    rows += [line(f"13:00:{i:02d}", "T", "ERROR", "s", f"e{i}") for i in range(n_error)]
    return parse_lines(rows, "f")[0]


def test_rare_budget_leaves_a_small_set_alone():
    es = events(5, 2)
    assert rare_budget(es) == (es, "")


def test_rare_budget_keeps_the_worst_levels_in_time_order_and_says_it_cut():
    es = events(MAX_RARE + 10, 5)
    shown, note = rare_budget(es)
    assert len(shown) == MAX_RARE
    assert sum(e.log_level == "ERROR" for e in shown) == 5  # every error survives the cut
    assert shown == sorted(shown, key=lambda e: e.timestamp)
    assert f"{len(es)} rare events matched, showing {MAX_RARE}" in note


@pytest.mark.parametrize("text, expected", [
    ("The timeout (log_id=3252) is the trigger.", "The timeout is the trigger."),
    ("A (root_cause_log_id 3252) then B (supporting_log_ids 3252, 3253).", "A then B."),
    ("timeout, log_id 3252, then open.", "timeout, then open."),
    ("No ids here.", "No ids here."),
])
def test_ids_are_stripped_from_the_answer_text(text, expected):
    assert strip_id_refs(text) == expected


def test_times_must_be_full_iso_datetimes():
    assert _parse_iso("2026-05-27T12:00:00") == datetime(2026, 5, 27, 12, 0)
    assert _parse_iso("2026-05-26T12:00:00+05:30") == datetime(2026, 5, 26, 12, 0)  # offset dropped
    assert _parse_iso(None) is None
    for bad in ("12:02", "banana"):
        with pytest.raises(ValueError):
            _parse_iso(bad)


def test_time_windows_outside_or_reversed_are_refused():
    ctx = build_context(events(3, 0), llm=None)  # data spans 12:00:00 to 12:00:02 on 26 May
    d = lambda h, m: datetime(2026, 5, 26, h, m)
    assert _check_times(d(12, 5), d(12, 1), ctx).startswith("The start time is after")
    assert "no logs for that time" in _check_times(d(9, 0), d(9, 1), ctx)
    assert _check_times(d(11, 0), d(12, 0), ctx) is None  # partial overlap is fine
    assert _check_times(None, None, ctx) is None          # no time means the whole history


def row(use_case, n):
    t = datetime(2026, 5, 26, 12, 0, 0)
    return CatalogRow(n, f"tmpl {n}", use_case, "INFO", 1, t, t, ["svc"], [n])


def test_the_catalog_table_shows_a_use_case_column_only_when_projects_are_mixed():
    one = format_catalog([row("A", 1), row("A", 2)]).split("\n")
    assert one[0] == "row|level|count|first_seen|last_seen|services|template"
    two = format_catalog([row("A", 1), row("B", 2)]).split("\n")
    assert two[0].startswith("row|use_case|level") and two[2].startswith("2|B|INFO")
