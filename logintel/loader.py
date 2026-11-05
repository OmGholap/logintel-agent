"""Push log files into Loki so the agent has something to query.

    python -m logintel.loader                      # the synthetic demo logs in demo_data/
    python -m logintel.loader incident=a.log noise=b.log

Each NAME=FILE pair becomes the stream {use_case="NAME"}. A stack trace stays with
its log line as one multi-line entry. Afterwards it asks Loki to flush (see flush()). Pushing the same file twice is harmless:
Loki drops an entry whose stream, timestamp and text it already has.

Env: LOKI_URL (default http://localhost:3100), LOKI_USER and LOKI_PASSWORD (optional)."""
import argparse
import os
import sys
import time
from datetime import timezone

import httpx

from .parser import LINE_RE, _is_frame, _parse_ts

BATCH = 1000  # entries per push request
READY_TIMEOUT = 60  # seconds to wait for a freshly started Loki

CANNOT_REACH = (
    "Cannot reach Loki at {url}. Start it with `docker compose up -d`.\n"
    "If `docker compose ps` shows loki as just `3100/tcp` (the port is not published to your machine),\n"
    "run `docker compose up -d --force-recreate loki`."
)
DEFAULT_PAIRS = ["incident=demo_data/production_incident_01.log", "noise=demo_data/auth_rate_limit_noise.log"]


def read_entries(path):
    """Return [(timestamp_ns, text)] for one file. Lines that are neither an entry
    nor a stack frame are skipped (the parser reports them the same way)."""
    entries, skipped = [], 0
    with open(path, encoding="utf-8") as f:
        for line in f.read().split("\n"):
            line = line.rstrip("\r")
            if not line.strip():
                continue
            match = LINE_RE.match(line)
            if match:
                try:
                    ts = _parse_ts(match["ts"])
                except ValueError:
                    skipped += 1
                    entries.append(None)  # a bad entry: its frames must not attach to the one before
                    continue
                ns = int(ts.replace(tzinfo=timezone.utc).timestamp()) * 1_000_000_000 + ts.microsecond * 1000
                entries.append([ns, line])
            elif _is_frame(line) and entries and entries[-1] is not None:
                entries[-1][1] += "\n" + line
            else:
                skipped += 1
    entries = [(ns, text) for ns, text in (e for e in entries if e is not None)]
    entries.sort(key=lambda e: e[0])  # Loki wants each stream in time order
    return entries, skipped


def push(client, url, labels, entries):
    for i in range(0, len(entries), BATCH):
        batch = entries[i:i + BATCH]
        body = {"streams": [{"stream": labels, "values": [[str(ns), text] for ns, text in batch]}]}
        resp = client.post(f"{url}/loki/api/v1/push", json=body)
        if resp.status_code >= 300:
            raise RuntimeError(f"Loki rejected the push ({resp.status_code}): {resp.text[:300]}")


def flush(client, url):
    """Ask Loki to write what it holds in memory to storage. Without this, logs dated
    more than ~3 hours ago stay invisible to queries until Loki flushes on its own
    (about half an hour). Only a self-hosted Loki has this endpoint, so a failure is fine."""
    try:
        return client.post(f"{url}/flush").status_code < 300
    except httpx.HTTPError:
        return False


def wait_for_loki(client, url, timeout=READY_TIMEOUT, sleep=time.sleep, clock=time.monotonic):
    """Wait until Loki accepts requests. A just-started Loki needs a few seconds, and
    refuses connections or answers 503 until then. A server with no /ready endpoint
    (404) counts as ready. Returns False if it is still not up after `timeout` seconds."""
    deadline, told = clock() + timeout, False
    while True:
        try:
            if client.get(f"{url}/ready").status_code != 503:
                return True
        except httpx.TransportError:  # connection refused, timeout
            pass
        if clock() >= deadline:
            return False
        if not told:
            print("Waiting for Loki to start...")
            told = True
        sleep(2)


def run(pairs, transport=None):
    """Push NAME=FILE pairs into Loki (the address comes from LOKI_URL)."""
    url = os.environ.get("LOKI_URL", "http://localhost:3100").rstrip("/")
    user, password = os.environ.get("LOKI_USER"), os.environ.get("LOKI_PASSWORD")
    auth = (user, password) if user else None
    try:
        with httpx.Client(auth=auth, timeout=30, transport=transport) as client:
            if not wait_for_loki(client, url):
                sys.exit(CANNOT_REACH.format(url=url))
            for pair in pairs:
                name, _, path = pair.partition("=")
                if not name or not path:
                    sys.exit(f"Expected NAME=FILE, got {pair!r}")
                entries, skipped = read_entries(path)
                push(client, url, {"use_case": name}, entries)
                note = f" ({skipped} unreadable lines skipped)" if skipped else ""
                print(f"{name}: pushed {len(entries)} entries from {path}{note}")
            if flush(client, url):
                print("flushed to storage: old-dated logs are queryable now")
    except httpx.ConnectError:
        sys.exit(CANNOT_REACH.format(url=url))
    except FileNotFoundError as e:
        sys.exit(f"Log file not found: {e.filename}")


def main():
    parser = argparse.ArgumentParser(prog="python -m logintel.loader")
    parser.add_argument("pairs", nargs="*", metavar="NAME=FILE", default=DEFAULT_PAIRS)
    run(parser.parse_args().pairs)


if __name__ == "__main__":
    main()
