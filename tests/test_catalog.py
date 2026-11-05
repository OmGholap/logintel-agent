from datetime import datetime

from logintel.catalog import build_catalog
from logintel.parser import parse_lines
from conftest import line


def entries_for(rows, use_case=""):
    out, _ = parse_lines([line(*r) for r in rows], "f")
    for e in out:
        e.use_case = use_case
    return out


ROWS = [
    ("12:00:01", "TENANT-X", "INFO", "s", "ok 1"), ("12:00:02", "TENANT-X", "INFO", "s", "ok 2"),
    ("12:00:03", "TENANT-X", "INFO", "s", "ok 3"), ("12:00:04", "TENANT-X", "WARN", "s", "slow"),
    ("12:00:05", "TENANT-X", "ERROR", "s", "boom 1"), ("12:00:06", "TENANT-X", "ERROR", "s", "boom 2"),
    ("12:00:07", "TENANT-X", "ERROR", "s", "other thing"), ("12:00:08", "TENANT-Z", "INFO", "s", "ok 9"),
]


def test_rows_are_worst_level_first_then_rarest_and_numbered_from_one():
    rows = build_catalog(entries_for(ROWS), "TENANT-X")
    assert [(r.level, r.count) for r in rows] == [("ERROR", 1), ("ERROR", 2), ("WARN", 1), ("INFO", 3)]
    assert [r.row for r in rows] == [1, 2, 3, 4]
    assert len(rows[3].log_ids) == 3  # the ids stay internal, one per line


def test_filters_by_tenant_and_window():
    es = entries_for(ROWS)
    assert build_catalog(es, "TENANT-NOPE") == []
    rows = build_catalog(es, "TENANT-X", datetime(2026, 5, 26, 12, 0, 5), datetime(2026, 5, 26, 12, 0, 6))
    assert sum(r.count for r in rows) == 2


def test_the_same_message_in_two_use_cases_is_two_rows():
    es = entries_for(ROWS[:1], "A") + entries_for(ROWS[:1], "B")
    assert sorted(r.use_case for r in build_catalog(es, "TENANT-X")) == ["A", "B"]
