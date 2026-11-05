"""Fakes shared by the tests: no network and no API key needed."""
import json
import re
from datetime import datetime

import httpx
import pytest

from logintel.agent import Agent
from logintel.config import Config
from logintel.graph import ExtractedParams, FinalAnswer, SelectorResult
from logintel.loki import LokiClient, _to_ns


def line(ts, tenant, level, service, msg):
    """One log line in the format the parser reads. ts like '12:00:05'."""
    return f"2026-05-26T{ts}.000Z [{tenant}] {level:<5} [{service}] {msg}"


class FakeLLM:
    """Stands in for the chat model. Records which structured outputs were asked for."""

    def __init__(self, tenant="TENANT-X", start=None, end=None, selected=(1,),
                 open_ended=False, confidence="high", answer="ok", cite=None, fail=None):
        self.tenant, self.start, self.end = tenant, start, end
        self.selected, self.open_ended = list(selected), open_ended
        self.confidence, self.answer, self.cite, self.fail = confidence, answer, cite, fail
        self.calls, self.prompts = [], []

    def with_structured_output(self, cls):
        fake = self

        class Runner:
            def invoke(_, messages):
                fake.calls.append(cls.__name__)
                fake.prompts.append(messages[-1][1])
                if fake.fail:
                    raise fake.fail
                if cls is ExtractedParams:
                    return ExtractedParams(tenant_id=fake.tenant, time_start=fake.start, time_end=fake.end)
                if cls is SelectorResult:
                    return SelectorResult(open_ended=fake.open_ended, selected_rows=fake.selected, reason="")
                shown = [int(i) for i in re.findall(r"log_id=(\d+)", messages[-1][1])]
                cited = fake.cite if fake.cite is not None else shown[:1]
                return FinalAnswer(answer=fake.answer, supporting_log_ids=cited, confidence=fake.confidence)

        return Runner()


def loki_transport(rows, requests=None):
    """A fake Loki. rows = [(use_case, log line)]. Serves query_range like the real one:
    label matcher, [start, end), oldest first, capped at `limit`."""
    stored = sorted(
        ((_to_ns(datetime.fromisoformat(text.split()[0].rstrip("Z"))), text, uc) for uc, text in rows),
        key=lambda r: r[0],
    )

    def handler(request):
        if requests is not None:
            requests.append(request)
        p = request.url.params
        want = re.search(r'use_case="([^"]+)"', p["query"])
        start, end, limit = int(p["start"]), int(p["end"]), int(p["limit"])
        hits = [r for r in stored if start <= r[0] < end and (not want or r[2] == want[1])][:limit]
        streams = {}
        for ts, text, uc in hits:
            streams.setdefault(uc, []).append([str(ts), text])
        result = [{"stream": {"use_case": uc}, "values": v} for uc, v in streams.items()]
        return httpx.Response(200, json={"status": "success", "data": {"result": result}})

    return httpx.MockTransport(handler)


def make_agent(rows, llm, ttl=60, clock=None, requests=None):
    kwargs = {"clock": clock} if clock else {}
    return Agent(
        config=Config(snapshot_ttl_seconds=ttl),
        llm=llm,
        client=LokiClient("http://loki", transport=loki_transport(rows, requests)),
        **kwargs,
    )


@pytest.fixture
def rare_rows():
    """TENANT-X: 20 routine lines and one lone error, so the rare-event path runs."""
    rows = [("svc", line(f"12:00:{i:02d}", "TENANT-X", "INFO", "svc.web", f"Request ok id={i}")) for i in range(20)]
    rows.append(("svc", line("12:00:30", "TENANT-X", "ERROR", "svc.db", "Disk quota exceeded for volume vol-7")))
    return rows


@pytest.fixture
def busy_rows():
    """TENANT-Y: every message repeats more than 5 times, so nothing is rare and the selector runs."""
    rows = [("svc", line(f"12:01:{i:02d}", "TENANT-Y", "INFO", "svc.cache", f"Cache hit key={i}")) for i in range(10)]
    rows += [("svc", line(f"12:02:{i:02d}", "TENANT-Y", "WARN", "svc.net", f"Retry attempt {i} failed")) for i in range(8)]
    return rows
