from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import SecretStr

from .parser import LogEntry, build_frequency_counts, parse_multiple_log_files


@dataclass
class Context:
    """Everything the graph needs from the outside world. Built by the caller and
    passed in, so importing this package does no work and nothing is global."""
    entries: list[LogEntry]
    freq: dict
    llm: Any
    data_min: Any  # earliest log timestamp (naive, the log's own clock)
    data_max: Any  # latest log timestamp
    use_cases: frozenset = frozenset()  # the projects present in these entries


def build_context(entries: list[LogEntry], llm) -> Context:
    if not entries:
        raise ValueError("No log entries to analyse")
    times = [e.timestamp for e in entries]
    return Context(
        entries=entries,
        freq=build_frequency_counts(entries),
        llm=llm,
        data_min=min(times),
        data_max=max(times),
        use_cases=frozenset(e.use_case for e in entries if e.use_case),
    )


def load_file_context(paths, llm) -> Context:
    entries, _reports = parse_multiple_log_files(paths)
    return build_context(entries, llm)


def load_loki_context(client, selector, llm, start=None, end=None,
                      lookback=timedelta(days=365)) -> Context:
    """Fetch the logs matching a LogQL selector once and build a Context from them.
    By default that is everything in the last `lookback`. It is a snapshot: logs
    pushed afterwards are not seen until a new context is built."""
    end = end or datetime.now(timezone.utc).replace(tzinfo=None)
    start = start or end - lookback
    entries = client.query_range(selector, start, end)
    if not entries:
        raise ValueError(f"Loki returned no logs for {selector} between {start:%Y-%m-%d} "
                         f"and {end:%Y-%m-%d}. Load some first (python -m logintel.loader).")
    return build_context(entries, llm)


def make_llm(config=None):
    """The chat model, built from a Config (or the environment). Created on call,
    never at import."""
    from langchain_openai import ChatOpenAI

    from .config import Config

    config = config or Config.from_env()
    if not config.openai_api_key:
        raise ValueError("OPENAI_API_KEY is not set")
    # gpt-5-mini only accepts temperature=1 (it rejects 0)
    return ChatOpenAI(
        api_key=SecretStr(config.openai_api_key),
        model=config.model,
        temperature=1,
    )
