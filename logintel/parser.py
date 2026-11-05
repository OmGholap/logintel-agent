import logging
import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class LogEntry:
    log_id: int
    timestamp: datetime
    tenant_id: str
    log_level: str
    service: str
    message: str
    stack_trace: str = ""
    template: str = ""
    source_ref: str = ""  # where this entry came from: "file:line" or the Loki stream + time
    use_case: str = ""    # the Loki use_case label (which project); empty for local files


# Matches the start of a real log entry, e.g.
# 2026-05-26T12:00:00.090Z [TENANT-X] INFO  [com.service.gateway.Ingress] message...
LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\s+"
    r"\[(?P<tenant>[^\]]+)\]\s+"
    r"(?P<level>\w+)\s+"
    r"\[(?P<service>[^\]]+)\]\s+"
    r"(?P<msg>.*)$"
)


# A first stack frame written on the header line: "... after 3000ms. at com.x.Y(Y.java:82)"
INLINE_FRAME_RE = re.compile(r"\s+(at [\w$.<>]+\([^)]*\))\s*$")


def _parse_ts(ts):
    # Fractions of any length are cut to microseconds; no fraction is fine too.
    base, _, frac = ts.rstrip("Z").partition(".")
    dt = datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
    return dt.replace(microsecond=int(frac[:6].ljust(6, "0")) if frac else 0)


def _is_frame(line):
    # A non-matching line is a stack frame only if it looks like one.
    return line[0] in " \t" or line.lstrip().startswith(("at ", "Caused by:", "..."))


def parse_log_file(path):
    """Return (entries, report). Bad lines are skipped and counted in the report.
    Blank lines are ignored; every other line lands in exactly one count."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
    except FileNotFoundError:
        raise FileNotFoundError(f"Log file not found: {path}") from None
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"Cannot read log file {path}: {e}") from None
    return parse_lines(lines, path)


def parse_lines(lines, source):
    """Same as parse_log_file but for lines already in memory (a file, a Loki entry).
    `source` names where they came from, for the report and each entry's source_ref."""
    entries = []
    report = {"path": source, "lines_read": 0, "entries": 0, "continuation": 0,
              "orphan": 0, "malformed": 0, "bad_timestamp": 0, "samples": []}

    def skip(kind, line):
        report[kind] += 1
        if len(report["samples"]) < 3:
            report["samples"].append(line.strip()[:100])

    current = None  # the entry that stack-trace lines attach to
    for lineno, line in enumerate(lines, start=1):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        report["lines_read"] += 1

        match = LINE_RE.match(line)
        if match:
            try:
                ts = _parse_ts(match["ts"])
            except ValueError:  # e.g. month 13
                skip("bad_timestamp", line)
                current = None
                continue
            msg, frame = match["msg"], ""
            inline = INLINE_FRAME_RE.search(msg)
            if inline:
                msg, frame = msg[:inline.start()], inline[1] + "\n"
            current = LogEntry(
                log_id=len(entries) + 1,
                timestamp=ts,
                tenant_id=match["tenant"],
                log_level=match["level"],
                service=match["service"],
                message=msg,
                stack_trace=frame,
                source_ref=f"{source}:{lineno}",
            )
            entries.append(current)
            report["entries"] += 1
        elif _is_frame(line) and current:
            current.stack_trace += line.strip() + "\n"
            report["continuation"] += 1
        elif _is_frame(line):
            skip("orphan", line)  # a frame with no entry above it
        else:
            skip("malformed", line)  # never attached to an entry

    skipped = report["orphan"] + report["malformed"] + report["bad_timestamp"]
    if skipped:
        logging.warning(
            "%s: skipped %d line(s) (%d orphan, %d malformed, %d bad timestamp), e.g. %s",
            source, skipped, report["orphan"], report["malformed"],
            report["bad_timestamp"], report["samples"])
    for entry in entries:
        entry.template = build_template(entry.message)
    return entries, report


def parse_multiple_log_files(paths):
    """Parse several files into one combined stream. Real queries don't know
    which file they belong to - there's just one multi-tenant log. IDs are
    renumbered across the whole list so they stay unique after concatenation.
    Returns (entries, one report per file)."""
    entries, reports = [], []
    for path in paths:
        file_entries, report = parse_log_file(path)
        entries.extend(file_entries)
        reports.append(report)

    entries.sort(key=lambda e: e.timestamp)
    for i, entry in enumerate(entries, start=1):
        entry.log_id = i
    return entries, reports


# A status code is the signal, not noise: a lone Status=500 must not collapse
# into the Status=200 template, so numbers right after "Status" stay as they are.
NOT_STATUS = r"(?<!Status=)(?<!Status:)(?<!Status: )"


def build_template(message):
    """Replace the variable parts of a message with placeholders so that
    structurally identical lines collapse to one template."""
    t = message
    t = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<IP>", t)          # IPv4 addresses
    t = re.sub(r"\b\d+(?:\.\d+)?\s?(?:ms|s|m|h)\b", "<DUR>", t)  # 3000ms, 1.5s, 250 ms
    t = re.sub(r"\b\d+/\d+\b", "<RATIO>", t)                        # ratios like 140/100
    t = re.sub(r"\b[0-9a-fA-F-]{16,}\b", "<ID>", t)                # long hex/uuid-ish ids
    t = re.sub(r"\b(?:us|eu|ap|sa|ca|me|af)-[a-z]+-\d+[a-z]\b", "<ZONE>", t)  # us-east-1a
    # ids where digits are glued to a prefix (cl_1594, session:91410) - \b\d+\b
    # below won't catch these because there's no boundary inside the token.
    t = re.sub(NOT_STATUS + r"(?<=[_:])\d+", "<NUM>", t, flags=re.I)
    t = re.sub(NOT_STATUS + r"\b\d+\b", "<NUM>", t, flags=re.I)                 # any remaining number
    return t


def build_frequency_counts(entries):
    """Count how often each (use_case, tenant_id, template) appears in the whole stream.
    High counts are repetitive noise; low counts are the rare/anomalous events.
    Counting per use case keeps one project's noise from hiding another's anomaly."""
    counts = {}
    for entry in entries:
        key = (entry.use_case, entry.tenant_id, entry.template)
        counts[key] = counts.get(key, 0) + 1
    return counts


def find_rare_events(entries, freq_counts, tenant_id, time_start, time_end, max_frequency=5):
    # time_start/time_end are optional: None means unbounded on that side, so
    # a query can span a tenant's whole activity across the combined stream.
    result = [
        e for e in entries
        if e.tenant_id == tenant_id
        and (time_start is None or e.timestamp >= time_start)
        and (time_end is None or e.timestamp <= time_end)
        and freq_counts[(e.use_case, e.tenant_id, e.template)] <= max_frequency
    ]
    result.sort(key=lambda e: e.timestamp)
    return result


def print_results(rare):
    print(f"Rare entries found: {len(rare)}\n")
    for i, e in enumerate(rare, start=1):
        ts = e.timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        print(f"[{i}] timestamp: {ts}")
        print(f"    tenant_id: {e.tenant_id}")
        print(f"    log_level: {e.log_level}")
        print(f"    service: {e.service}")
        print(f"    message: {e.message}")
        if e.stack_trace:
            print("    stack_trace:")
            for tl in e.stack_trace.strip().split("\n"):
                print(f"        {tl}")
        else:
            print("    stack_trace: (none)")
        print()


if __name__ == "__main__":
    # Scenario 1 — TENANT-X, incident log
    # entries = parse_log_file("../production_incident_01.log")
    entries, _ = parse_multiple_log_files(["production_incident_01.log", "auth_rate_limit_noise.log"])
    freq_counts = build_frequency_counts(entries)
    print("=== Scenario 1 ===")
    print_results(find_rare_events(
        entries, freq_counts,
        tenant_id="TENANT-X",
        time_start=datetime(2026, 5, 26, 12, 0, 00),
        time_end=datetime(2026, 5, 26, 12, 4, 59),
    ))

    # Scenario 2 — TENANT-Z, noise log
    # entries = parse_log_file("../auth_rate_limit_noise.log")
    # freq_counts = build_frequency_counts(entries)
    # print("=== Scenario 2")
    # print_results(find_rare_events(
    #     entries, freq_counts,
    #     tenant_id="TENANT-Z",
    #     time_start=datetime(2026, 5, 26, 14, 1, 0),
    #     time_end=datetime(2026, 5, 26, 14, 2, 0),
    # ))

