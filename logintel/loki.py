"""Read logs from Loki (LogQL query_range) and turn them into LogEntry objects."""
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from .parser import LogEntry, parse_lines

NS = 1_000_000_000
PAGE_SIZE = 5000            # entries asked for per request
MAX_LIMIT = 5000            # Loki's default cap on entries per request
SPAN = timedelta(days=30)   # Loki refuses a single query longer than about 30 days

LOG = logging.getLogger(__name__)


def _to_ns(dt: datetime) -> int:
    # Log timestamps carry no zone and are written with a "Z", so read them as UTC.
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    return int(dt.timestamp()) * NS + dt.microsecond * 1000


class LokiClient:
    def __init__(self, url, user=None, password=None, timeout=30.0, transport=None):
        self.url = url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.url, timeout=timeout, transport=transport,
            auth=(user, password) if user else None,
        )

    @classmethod
    def from_env(cls):
        return cls(
            os.environ.get("LOKI_URL", "http://localhost:3100"),
            os.environ.get("LOKI_USER"), os.environ.get("LOKI_PASSWORD"),
        )

    def _get_page(self, selector, start_ns, end_ns, limit):
        try:
            resp = self._http.get("/loki/api/v1/query_range", params={
                "query": selector, "start": start_ns, "end": end_ns,
                "limit": limit, "direction": "forward",
            })
        except httpx.ConnectError:
            raise ConnectionError(f"Cannot reach Loki at {self.url}. Is it running?") from None
        if resp.status_code >= 300:
            raise RuntimeError(f"Loki query failed ({resp.status_code}): {resp.text[:300]}")
        page = []
        for stream in resp.json()["data"]["result"]:
            labels = ",".join(f'{k}="{v}"' for k, v in sorted(stream["stream"].items()))
            use_case = stream["stream"].get("use_case", "")
            for ts, line in stream["values"]:
                page.append((int(ts), line, labels, use_case))
        page.sort(key=lambda p: p[0])
        return page

    def _fetch_window(self, selector, start_ns, end_ns, page_size):
        """All raw (ts_ns, line, labels, use_case) in [start, end), paging forward in time."""
        out, cursor, limit = [], start_ns, page_size
        while True:
            page = self._get_page(selector, cursor, end_ns, limit)
            if not page:
                return out
            # a page starts at `cursor` inclusive, so drop what the last page already gave us
            already = {p for p in out[-limit:] if p[0] == cursor}
            new = [p for p in page if p not in already]
            out.extend(new)
            if len(page) < limit:
                return out
            last_ts = page[-1][0]
            if last_ts == cursor and not new:
                # a full page of entries we already have: more may share this timestamp
                if limit >= MAX_LIMIT:
                    raise RuntimeError(f"More than {limit} Loki entries share one timestamp")
                limit = min(limit * 2, MAX_LIMIT)
                continue
            limit = page_size
            cursor = last_ts

    def query_range(self, selector, start: datetime, end: datetime, page_size=PAGE_SIZE) -> list[LogEntry]:
        """Every log entry matching `selector` from start (inclusive) to end (exclusive),
        oldest first, as LogEntry objects numbered 1..n. A range longer than SPAN is
        fetched in pieces."""
        raw, lo, end_ns = [], _to_ns(start), _to_ns(end)
        while lo < end_ns:
            hi = min(lo + int(SPAN.total_seconds()) * NS, end_ns)
            raw.extend(self._fetch_window(selector, lo, hi, page_size))
            lo = hi
        entries = []
        for ts, text, labels, use_case in raw:
            parsed, _ = parse_lines(text.split("\n"), f"loki:{labels}")
            ref = f"loki:{{{labels}}}@{ts}#{hashlib.sha1(text.encode()).hexdigest()[:8]}"
            for k, entry in enumerate(parsed):
                entry.source_ref = ref if k == 0 else f"{ref}/{k}"
                entry.use_case = use_case
                entries.append(entry)
        entries.sort(key=lambda e: e.timestamp)  # stable: ties keep the order Loki gave
        for i, entry in enumerate(entries, start=1):
            entry.log_id = i
        return entries
