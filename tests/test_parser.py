from logintel.parser import (
    build_frequency_counts, build_template, find_rare_events, parse_lines,
)
from conftest import line

HEADER = "2026-05-26T12:00:00.090Z [TENANT-X] INFO  [svc.web] hello 42"


def test_parses_an_entry():
    entries, report = parse_lines([HEADER], "f.log")
    e = entries[0]
    assert (e.tenant_id, e.log_level, e.service, e.message) == ("TENANT-X", "INFO", "svc.web", "hello 42")
    assert (e.timestamp.minute, e.timestamp.microsecond) == (0, 90000)
    assert e.source_ref == "f.log:1" and report["entries"] == 1


def test_fractions_of_any_length_and_none():
    lines = [f"2026-05-26T12:00:00{frac}Z [T] INFO [s] m" for frac in ("", ".5", ".123456789")]
    entries, report = parse_lines(lines, "f")
    assert report["entries"] == 3 and entries[2].timestamp.microsecond == 123456


def test_bad_timestamp_is_skipped_and_counted_not_fatal():
    bad = "2026-13-26T12:00:00.000Z [T] INFO [s] boom"
    entries, report = parse_lines([bad, "    at com.x.Y(Y.java:1)", HEADER], "f")
    assert len(entries) == 1 and report["bad_timestamp"] == 1 and report["orphan"] == 1


def test_stack_frames_attach_and_garbage_does_not():
    entries, report = parse_lines([HEADER, "    at com.x.Y(Y.java:1)", "Caused by: boom", "THIS IS GARBAGE"], "f")
    assert entries[0].stack_trace.count("\n") == 2 and "GARBAGE" not in entries[0].stack_trace
    assert report["continuation"] == 2 and report["malformed"] == 1


def test_inline_first_frame_gives_the_same_template_as_the_normal_layout():
    inline = "2026-05-26T12:00:00.000Z [T] ERROR [s] timeout after 3000ms. at com.x.Y(Y.java:82)"
    normal = "2026-05-26T12:00:01.000Z [T] ERROR [s] timeout after 3000ms."
    (a,), _ = parse_lines([inline], "f")
    (b,), _ = parse_lines([normal], "f")
    assert a.message == b.message and a.template == b.template
    assert a.stack_trace == "at com.x.Y(Y.java:82)\n"


def test_templates_mask_noise_but_keep_status_codes_apart():
    assert build_template("took 250ms from 10.0.0.1 id 9f8e7d6c5b4a3921") == "took <DUR> from <IP> id <ID>"
    assert build_template("req 17 ok") == build_template("req 99 ok")
    assert build_template("Method=GET Status=200") != build_template("Method=GET Status=500")


def test_rare_events_are_counted_per_use_case_tenant_and_template():
    def mk(use_case, n):
        entries, _ = parse_lines([line(f"12:00:{i:02d}", "TENANT-X", "ERROR", "s", "Out of memory") for i in range(n)], use_case)
        for e in entries:
            e.use_case = use_case
        return entries

    entries = mk("A", 1) + mk("B", 25)  # one in A, a routine message in B
    freq = build_frequency_counts(entries)
    rare = find_rare_events(entries, freq, "TENANT-X", None, None)
    assert [e.use_case for e in rare] == ["A"]
