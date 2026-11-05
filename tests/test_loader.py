import json

import httpx
import pytest

from logintel import loader
from conftest import line


class Clock:
    """A fake clock: sleeping just moves time forward."""
    def __init__(self):
        self.now = 0.0
    def time(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_it_waits_while_loki_starts_then_carries_on():
    answers = iter([httpx.ConnectError("refused"), httpx.Response(503, text="Ingester not ready"), httpx.Response(200, text="ready")])

    def handler(request):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    clock = Clock()
    assert loader.wait_for_loki(client_for(handler), "http://loki", sleep=clock.sleep, clock=clock.time)
    assert clock.now == 4  # two short waits, then ready


def test_it_gives_up_after_the_timeout_and_a_server_without_ready_counts_as_up():
    def refuse(request):
        raise httpx.ConnectError("refused")

    clock = Clock()
    assert not loader.wait_for_loki(client_for(refuse), "http://loki", timeout=10, sleep=clock.sleep, clock=clock.time)
    assert clock.now >= 10
    assert loader.wait_for_loki(client_for(lambda r: httpx.Response(404)), "http://loki")  # no /ready endpoint


def test_read_entries_keeps_stack_traces_and_skips_unreadable_lines(tmp_path):
    f = tmp_path / "a.log"
    f.write_text("\n".join([
        line("12:00:02", "T", "ERROR", "s", "boom"), "    at com.x.Y(Y.java:1)",
        "THIS IS GARBAGE", line("12:00:01", "T", "INFO", "s", "ok"),
        "2026-13-01T00:00:00.000Z [T] INFO [s] bad month",
    ]))
    entries, skipped = loader.read_entries(str(f))
    assert [e[0] for e in entries] == sorted(e[0] for e in entries)  # oldest first
    assert "at com.x.Y" in entries[1][1] and skipped == 2


def test_run_pushes_each_file_under_its_use_case_and_flushes(tmp_path, monkeypatch, capsys):
    f = tmp_path / "a.log"
    f.write_text("\n".join(line(f"12:00:{i:02d}", "T", "INFO", "s", f"m{i}") for i in range(3)))
    monkeypatch.setenv("LOKI_URL", "http://loki")
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.content))
        return httpx.Response(200 if request.url.path == "/ready" else 204)

    loader.run([f"incident={f}"], transport=httpx.MockTransport(handler))
    pushes = [json.loads(c) for m, p, c in seen if p == "/loki/api/v1/push"]
    assert pushes[0]["streams"][0]["stream"] == {"use_case": "incident"}
    assert len(pushes[0]["streams"][0]["values"]) == 3
    assert ("POST", "/flush", b"") in seen
    assert "pushed 3 entries" in capsys.readouterr().out


def test_an_unreachable_loki_exits_with_the_port_hint(monkeypatch):
    monkeypatch.setenv("LOKI_URL", "http://loki")
    monkeypatch.setattr(loader, "wait_for_loki", lambda *a, **k: False)
    with pytest.raises(SystemExit) as e:
        loader.run(["x=y.log"], transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert "force-recreate loki" in str(e.value)
