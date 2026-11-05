import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .config import Config
from .context import load_loki_context, make_llm
from .graph import build_graph, run_graph
from .loki import LokiClient

USE_CASE_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass
class Answer:
    question: str
    answer: str
    confidence: str                  # "high" | "medium" | "low"
    path: str                        # rare_events | selector | no_match | no_logs | clarify | error
    citations: list[str] = field(default_factory=list)  # the real log lines the answer rests on
    root_cause_log_id: Optional[int] = None
    supporting_log_ids: list[int] = field(default_factory=list)
    tenant_id: Optional[str] = None
    time_start: Optional[datetime] = None
    time_end: Optional[datetime] = None


def _path(state) -> str:
    if "tenant_id" not in state:
        return "error"          # the run failed before extraction finished
    if "rare_events" not in state:
        return "clarify"
    if state["rare_events"]:
        return "rare_events"
    if not state["catalog"]:
        return "no_logs"
    return "selector" if state["selected_rows"] else "no_match"


class Agent:
    """Ask questions about the logs in Loki.

        agent = Agent()
        answer = agent.ask("What caused the 503s for TENANT-X around 12:02?", use_case="incident")
        print(answer.answer, answer.confidence, *answer.citations, sep="\\n")

    Logs are read from Loki and kept in memory for `snapshot_ttl_seconds`, per use case."""

    def __init__(self, config: Optional[Config] = None, llm=None, client=None, clock=time.monotonic):
        self.config = config or Config.from_env()
        self._llm = llm or make_llm(self.config)
        self._client = client or LokiClient(
            self.config.loki_url, self.config.loki_user, self.config.loki_password)
        self._clock = clock
        self._graphs = {}  # LogQL selector -> (time it was read, compiled graph)

    @staticmethod
    def selector_for(use_case: Optional[str]) -> str:
        if use_case is None:
            return '{use_case=~".+"}'
        if not USE_CASE_RE.match(use_case):
            raise ValueError(f"Invalid use case {use_case!r}: use letters, digits, '_', '.' or '-'")
        return f'{{use_case="{use_case}"}}'

    def refresh(self):
        """Forget the cached logs; the next ask reads Loki again."""
        self._graphs.clear()

    def _graph(self, use_case):
        selector = self.selector_for(use_case)
        cached = self._graphs.get(selector)
        if cached and self._clock() - cached[0] < self.config.snapshot_ttl_seconds:
            return cached[1]
        ctx = load_loki_context(self._client, selector, self._llm,
                                lookback=timedelta(days=self.config.lookback_days))
        graph = build_graph(ctx)
        self._graphs[selector] = (self._clock(), graph)
        return graph

    def ask(self, question: str, use_case: Optional[str] = None) -> Answer:
        """Answer a question. `use_case` limits the search to one Loki use case;
        None searches all of them. Raises ConnectionError if Loki is unreachable and
        ValueError if it holds no logs for the use case; an LLM failure comes back
        as a low-confidence Answer with path "error"."""
        state = run_graph(self._graph(use_case), question)
        final = state["final_answer"]
        return Answer(
            question=question,
            answer=final.answer,
            confidence=final.confidence,
            path=_path(state),
            citations=state.get("citations", []),
            root_cause_log_id=final.root_cause_log_id,
            supporting_log_ids=final.supporting_log_ids,
            tenant_id=state.get("tenant_id"),
            time_start=state.get("time_start"),
            time_end=state.get("time_end"),
        )
