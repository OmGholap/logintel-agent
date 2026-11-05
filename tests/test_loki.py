import json
from datetime import datetime

import httpx
import pytest

from logintel.loki import LokiClient
from conftest import line, loki_transport

D0, D1 = datetime(2026, 5, 26, 12, 0, 0), datetime(2026, 5, 26, 13, 0, 0)


def rows_with_ties():
    # three entries share 12:00:02, two share 12:00:03
    secs = [1, 2, 2, 2, 3, 3, 4]
    return [("svc", line(f"12:00:{s:02d}", "TENANT-X", "INFO", "s", f"msg {chr(97 + i)}")) for i, s in enumerate(secs)]


@pytest.mark.parametrize("page_size", [3, 4, 5, 100])
def test_paging_returns_every_entry_once_even_when_timestamps_tie(page_size):
    client = LokiClient("http://x", transport=loki_transport(rows_with_ties()))
    entries = client.query_range('{use_case=~".+"}', D0, D1, page_size=page_size)
    assert [e.message for e in entries] == [f"msg {c}" for c in "abcdefg"]
    assert [e.log_id for e in entries] == list(range(1, 8))
    assert len({e.source_ref for e in entries}) == 7 and entries[0].use_case == "svc"


def test_long_ranges_are_split_into_30_day_pieces():
    requests = []
    client = LokiClient("http://x", transport=loki_transport([], requests))
    client.query_range("{x}", datetime(2026, 1, 1), datetime(2026, 3, 12))  # 70 days
    assert len(requests) == 3
    assert all(int(r.url.params["end"]) - int(r.url.params["start"]) <= 30 * 86400 * 10**9 for r in requests)


def test_a_multi_line_entry_keeps_its_stack_trace():
    text = line("12:00:01", "TENANT-X", "ERROR", "s", "boom") + "\n    at com.x.Y(Y.java:1)"
    client = LokiClient("http://x", transport=loki_transport([("svc", text)]))
    (entry,) = client.query_range("{x}", D0, D1)
    assert entry.message == "boom" and "com.x.Y" in entry.stack_trace


def test_loki_errors_are_reported_clearly():
    bad = LokiClient("http://x", transport=httpx.MockTransport(lambda r: httpx.Response(400, text="parse error")))
    with pytest.raises(RuntimeError, match="400"):
        bad.query_range("{", D0, D1)

    def refuse(request):
        raise httpx.ConnectError("refused")

    down = LokiClient("http://x", transport=httpx.MockTransport(refuse))
    with pytest.raises(ConnectionError, match="Cannot reach Loki"):
        down.query_range("{x}", D0, D1)
