from dataclasses import dataclass
from datetime import datetime

from .parser import LogEntry

# Higher = worse. Unknown levels rank lowest.
LEVEL_RANK = {
    "TRACE": 0, "DEBUG": 1, "INFO": 2, "WARN": 3, "WARNING": 3,
    "ERROR": 4, "FATAL": 5, "CRITICAL": 5,
}


def _rank(level):
    return LEVEL_RANK.get(level.upper(), 0)


@dataclass
class CatalogRow:
    row: int  # small number the LLM picks by
    template: str
    use_case: str  # which project's logs this row is from ("" for local files)
    level: str  # worst level seen under this template
    count: int
    first_seen: datetime
    last_seen: datetime
    services: list[str]
    log_ids: list[int]  # internal: not shown to the LLM


def build_catalog(entries: list[LogEntry], tenant_id, time_start=None, time_end=None):
    """One row per template for this tenant and window. None means unbounded
    on that side. Worst level first, then fewest occurrences first."""
    groups = {}  # one row per (use case, template): the same message in two projects is two rows
    for e in entries:
        if e.tenant_id != tenant_id:
            continue
        if time_start is not None and e.timestamp < time_start:
            continue
        if time_end is not None and e.timestamp > time_end:
            continue
        groups.setdefault((e.use_case, e.template), []).append(e)

    rows = []
    for (use_case, template), es in groups.items():
        times = [e.timestamp for e in es]
        rows.append(CatalogRow(
            row=0,
            template=template,
            use_case=use_case,
            level=max((e.log_level for e in es), key=_rank),
            count=len(es),
            first_seen=min(times),
            last_seen=max(times),
            services=sorted({e.service for e in es}),
            log_ids=[e.log_id for e in es],
        ))

    rows.sort(key=lambda r: (-_rank(r.level), r.count))
    for i, r in enumerate(rows, start=1):
        r.row = i
    return rows
