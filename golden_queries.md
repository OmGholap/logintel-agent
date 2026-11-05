# Golden queries

Hand-written queries with the expected path and answer. They are the regression net for the agent: each one was run several times against the real model and the results are recorded below. The unit tests in `tests/` cover the same paths with a fake LLM.

Data range: `2026-05-26T12:00:00.090` to `2026-05-26T14:02:17.163`.
Two files: `production_incident_01.log` (12:00:00 to 12:04:34) and `auth_rate_limit_noise.log` (14:00:00 to 14:02:17). **There are no logs between 12:04:34 and 14:00:00.**

Status: GQ-1 and GQ-2 recorded below, GQ-1 re-run 8 times after the 1.8 fixes. Time-policy queries T-1 to T-6 and the direct calls were run. T-4 and T-4b (empty catalog) now pass. Selector-path queries S-1 to S-5 were added after steps 1.6 to 1.8, and R-1 after step 1.7.

---

## Recorded runs

### GQ-1: explicit time range
- **Query:** What caused the 503 errors for TENANT-X around 12:02-12:03?
- **Expected extraction:** tenant `TENANT-X`, window `2026-05-26 12:02:00` to `12:03:00`
- **Expected path:** deterministic (rare events found)
- **Expected answer:**
  - root cause = the DB connection timeout line (`log_id=3252`, 12:02:58.378)
  - supporting = the circuit breaker opened line (`log_id=3253`, 12:02:58.408)
  - confidence `high`
- **Must not:** claim a 503 line exists in the evidence. The evidence shows a DB timeout and a breaker opening, not a 503.
- **Recorded run:** matched. Style note: the answer printed the prompt's step headings ("Abnormal entries: ... Chronology & causal chain: ...").
- **After 1.8 (8 real-LLM runs, both "12:02-12:03" and "12 ish" phrasings):** root 3252, supporting [3253], `high` in all 8. No run claims a 503 line exists; 7 of 8 say the evidence has no 503 lines. The "must not" rule above holds. Open: 2 of 8 answers write `log_id=3252` in the prose (see Known bugs).

### GQ-2: vague time
- **Query:** What caused the 503 errors for TENANT-X around 12 ish ?
- **Expected extraction:** tenant `TENANT-X`, window contains `12:02:58` and is at most 15 minutes wide (run recorded `11:55:00` to `12:05:00`)
- **Expected path:** deterministic
- **Expected answer:** same as GQ-1 (root cause 3252, supporting [3253], confidence `high`)
- **Must not:** say the breaker "likely resulted in 503s" as fact. That is not in the evidence.
- **Recorded run:** matched on root cause. The answer did include "likely resulted in 503s", which breaks the rule above.
- **After the 1.8 prompt rule:** the 4 "12 ish" runs above show no unsupported 503 claim. **Pass.**

Note: the window width for vague times used to be chosen by the LLM and varied between runs (before the fix, 3 of 4 runs picked `11:59` to `12:01` and missed the incident, so the query went to the semantic path with `low` confidence).
**Fixed:** for a single time the LLM returns that time as both start and end, and code pads it by `SINGLE_TIME_PAD` (5 minutes each side). "12 ish" is always `11:55:00` to `12:05:00` (5 of 5 runs). Explicit ranges are not padded (GQ-1 stays `12:02:00` to `12:03:00`). Check: window contains 12:02:58 and rare ids are `[3252, 3253]`.

---

## Time-policy queries for step 1.3 (not run yet)

Policy under test: full ISO datetimes only; a window fully outside the data, a reversed range, or an unparseable time asks the user (clarify); partial overlap is fine; no time at all means the tenant's whole history on purpose. None of these may search all history by accident, crash, or move a time to a different day.

### T-1: a date outside the data (the main 1.3 check)
- **Query:** What happened to TENANT-X on 2026-05-27 at 12:02?
- **Today (old code):** `_parse_iso` replaces the 27th with the 26th, so it silently answers from the 26th's data.
- **Expected after 1.3:** clarify path, no synthesis call. The message says there are no logs for that time and gives the range (26 May 12:00 to 14:02). Confidence `low`.
- **Pass:** `path` is clarify, `rare_events` was never computed, and the answer mentions the data range.
- **Recorded runs:**
  - Before 1.3: extracted `2026-05-26 12:01` to `12:03` (the 27th became the 26th), deterministic path, answered GQ-1's root cause with `high` confidence. The model noted in its answer that no logs for the 27th were in the evidence, but the code gave no warning.
  - After 1.3: extracted `2026-05-27 12:01` to `12:03`, clarify path, no synthesis call, `low` confidence, message gave the range "26 May 12:00 to 26 May 14:02". **Pass.**
- **Known cosmetic issue:** the `__main__` printout labels every run that stops at clarify as `clarify (no tenant)`, even when the cause was the time. The label is wrong, not the behaviour; fix with the single entry point (1.10) or observability.

### T-2: a time before the data starts
- **Query:** What happened to TENANT-X at 09:00?
- **Expected after 1.3:** clarify with the data range. The search must not widen to all history.
- **Recorded run (real LLM):** extracted `2026-05-26 08:59:30` to `09:01:30`, clarify path, `low` confidence, message gave the range "26 May 12:00 to 26 May 14:02". **Pass.**

### T-3: reversed range
- **Query:** What happened to TENANT-X between 12:03 and 12:01?
- **Expected after 1.3:** either the extractor returns the range in order (then the answer matches GQ-1), or `start > end` is caught and clarify says so. Never a reversed search.
- **Reliable check:** call `_check_times` directly with `start > end` (the LLM may silently reorder, which hides the code path).
- **Recorded run (real LLM):** the model reordered the range to `12:01` to `12:03`, deterministic path, root cause 3252, `high`. **Pass**, but this only tests the model. The `start > end` code path was covered by the direct call: `_check_times` returned "The start time is after the end time. Please check the range."

### T-4: a gap inside the data range
- **Query:** What happened to TENANT-X at 13:00?
- **Why it matters:** 13:00 is inside 12:00 to 14:02, so `_check_times` lets it through, but there are no logs between 12:04:34 and 14:00:00. Today this goes to the semantic path and the model is called with "(no evidence found)".
- **Expected:** a "no logs in that window" answer without calling the LLM, e.g. "No logs for TENANT-X between 12:58 and 13:02. TENANT-X has logs from 12:00 to 12:04."
- **Decision: belongs to 1.6, not 1.3.** `_check_times` only knows the global data bounds. "Does this tenant have logs in this window" depends on the actual entries, so it is the empty-catalog rule in 1.6a/1.6d. This query is a 1.6 check; it is expected to fail until then.

### T-4b: tenant silent in the window, no global gap
- **Query:** What happened to TENANT-Z around 12:02?
- **Why it matters:** 12:02 is inside the data range and other tenants are logging then, so there is no global gap. But TENANT-Z only appears in `auth_rate_limit_noise.log` (around 14:00; 0 lines in the incident file). A range check passes it; only the per-tenant catalog sees zero rows.
- **Expected:** the same empty-catalog answer, no LLM call after extraction: "No logs for TENANT-Z between ... and ...". Also a 1.6 check.

### T-5: partial overlap
- **Query:** What happened to TENANT-X between 11:50 and 12:01?
- **Expected:** a normal answer. The window is clipped by the data (12:00 to 12:01), not refused.
- **Recorded run (real LLM):** window stayed `11:50` to `12:01`, not refused. Deterministic found no rare events, so the semantic path answered ("only log_id=1, a routine POST"), `low` confidence. **Pass.**

### T-6: no time at all (the deliberate widening)
- **Query:** What's going on with TENANT-X?
- **Expected:** tenant extracted, both times null, the tenant's whole history searched. This is the one case where "all history" is correct.
- **Recorded run (real LLM):** tenant `TENANT-X`, both times null, deterministic path, root cause 3252, `high`. **Pass.**

### Direct-call checks (no LLM)
Run against `_parse_iso` and `_check_times` once step 1.3 is written:
- a garbage string such as `"banana"` must not raise
  - **Note (recorded run):** `_parse_iso("banana")` does raise `ValueError`, by design. `extract_params` catches it and the graph goes to clarify, so the graph does not crash. The crash path was run through the graph with a fake LLM returning `"09:00"` and `"around noon"` (both clarify, "I couldn't read that time. Please include the date, e.g. 2026-05-26T12:02."). The real model never returned an unparseable time.
- a bare time such as `"12:02"` is rejected, not anchored to 26 May
  - **Recorded run:** `_parse_iso("12:02")` raised `ValueError`. **Pass.**
- a full ISO value on another day keeps its date
  - **Recorded run:** `_parse_iso("2026-05-27T12:00:00")` returned `2026-05-27 12:00`. **Pass.**

---

## Selector-path queries (steps 1.6 to 1.8, real `gpt-5-mini`)

These go through catalog, selector, evidence and synthesis. Dataset: TENANT-X and TENANT-A, 26 May 12:00 to 12:04.

### S-1: a named symptom, answerable from one row
- **Query:** Were there any pool connection warnings for TENANT-X between 12:02:00 and 12:02:56?
- **Expected:** catalog of 3 rows; selector picks row 1 only (5 WARN lines); all 5 lines shown (no cut); answer says yes with 5 warnings; `root_cause_log_id` null; supporting [3202, 3204, 3206, 3208, 3210].
- **Recorded runs:** 4 full runs. Selected `[1]` or `[1, 3]`; all 5 warnings listed (the earlier undercount of 4 is fixed by `KEEP_ALL = 6`); root cause null in the last 3 runs after the `asks_cause` flag.

### S-2: many identical lines, sampled
- **Query:** What caused the 503 errors for TENANT-X between 12:03:10 and 12:03:40?
- **Expected:** one row with 135 lines; 4 shown, spread across 12:03:10 to 12:03:39; the answer says 135 occurrences; root 3461.
- **Recorded runs:** matched in every run (supporting `[3461, 3641, 3817, 3997]`). Confidence `low` until 1.9.

### S-3: open-ended question
- **Query:** What happened to TENANT-A around 12:02?
- **Expected:** a short answer from the first catalog rows (routine traffic, no errors). Never "no logs".
- **Recorded runs:** 24 runs over 3 phrasings ("what happened", "what is happening", "any problems"): 0 empty. Picked `[1, 2, 3]` in 19, all 6 rows in 5. Needed the `open_ended` flag plus one confirmation retry (before, 2 of 12 came back empty).

### S-4: a symptom nothing matches
- **Query:** Why did the disk fill up for TENANT-A around 12:02?
- **Expected:** `selected_rows` is `[]`; fixed message "TENANT-A has 6 kinds of log lines in this window, but none matched your question."; no synthesis call; no citations.
- **Recorded runs:** 8 of 8 empty, message correct. Before the selector prompt fix it picked all 6 rows and cited 24 lines.

### S-5: a symptom one row matches
- **Query:** Were there any Kafka consumer lag alerts for TENANT-A around 12:02?
- **Expected:** selector picks only the Kafka-related row.
- **Recorded runs:** `[6]` in 8 of 8.

### R-1: evidence budget on the rare path (step 1.7)
- **Setup:** `MAX_RARE = 30`. Over the cap, keep the worst levels first and the earliest within a level, put the kept events back in time order, add a note, and cap confidence at `medium`. No real query reaches the cap (GQ-1 has 2 rare events), so this is checked with a fake tenant (`TENANT-F`, no LLM) and one real call.
- **No LLM, `rare_budget` only:** 20 events show 20, no note; 30 show 30, no note; 31 show 30 with the note "31 rare events matched, showing 30 (worst levels first, earliest within a level)"; 10,000 show 30 (all ERROR), same note. The shown events are always in time order.
- **Real LLM, 100 rare events (INFO, WARN, ERROR mixed):** confidence `medium`; the answer says "100 rare events matched (only 30 are shown)"; 10 cited ids, all among the 30 shown.
- **Grounding fix made at the same time:** `ground` now checks citations against the events shown to the model, not all rare events.
- **Regression:** GQ-1, T-6 and "12 ish" stay `high`, root 3252, supporting [3252, 3253] (see B-8 for one `medium` run).

### T-4 / T-4b results (step 1.6)
- **T-4** (TENANT-X at 13:00): "No logs for TENANT-X between 12:55 and 13:05. TENANT-X has logs from 26 May 12:00 to 26 May 12:04." No LLM call after extraction. **Pass.**
- **T-4b** (TENANT-Z around 12:02): "No logs for TENANT-Z between 11:57 and 12:07. TENANT-Z has logs from 26 May 14:00 to 26 May 14:02." **Pass.**

---

## Step 1.11: full golden run (real `gpt-5-mini`, 26 May data, run after step 1.9 and 1.7)

Every query above was run 3 times (GQ-2 and T-3 were re-run 6 more times after the first pass looked off). A run passes when the path, root cause, supporting ids, confidence and "no unsupported claims" rule all hold (no "would cause", "resulted in" or `log_id` in the answer text).

| Query | Runs passed | Notes |
|---|---|---|
| GQ-1 | 3/3 | root 3252, supporting [3253], `high` |
| GQ-2 | 6/9 `high` (8/9 at `medium` or better) | right root cause every time; confidence varied (B-8) |
| T-1 | 3/3 | clarify, gives the 26 May range |
| T-2 | 3/3 | clarify, gives the range |
| T-3 | 9/9 | reversed range is answered in order. Root cause is null or 3252 depending on whether the model reads "what happened" as a cause question; both are accepted. My first check demanded null and wrongly failed 5 runs. |
| T-4 | 3/3 | "No logs for TENANT-X ..." with no model call after extraction |
| T-4b | 3/3 | same for TENANT-Z |
| T-5 | 3/3 | window clipped by the data, answered normally |
| T-6 | 3/3 | root 3252, `high` |
| S-1 | 3/3 | 5 warnings, root null, `medium` |
| S-2 | 3/3 | root 3461, "135", `medium` |
| S-3 | 3/3 | non-empty selection, `medium` |
| S-4 | 3/3 | `[]` and the fixed "none matched" message |
| S-5 | 3/3 | `[6]` |

Totals: 38 of 42 first-pass runs; the 4 misses were wrong expectations for T-3 (3 runs) and one `low` on GQ-2. After correcting the T-3 check, no run was wrong on path, ids or claims. The only open wobble is confidence on the rare path (B-8).

---

## Known bugs / to fix

| # | Bug | Seen in | Plan |
|---|---|---|---|
| B-1 | ~~`log_id=3252` written in the answer text.~~ **Fixed:** `strip_id_refs` in the `ground` node removes id references from the prose (checked on 5 sample sentences and 3 real queries, no `log_id` left). | GQ-1 | Done. |
| B-2 | ~~Selector path can never be `medium`.~~ **Fixed in 1.9:** rare path `high`, selector path `medium` at most, no-match / empty / clarify `low`; code sets the ceiling. Checked on 8 query types, 3 runs each on the selector paths. | S-1, S-2 | Done. |
| B-3 | The open-ended selector sometimes picks all 6 rows instead of 3 (5 of 24 runs). Harmless on this dataset; with a large catalog it hits the 10-row cap. | S-3 | Cap or prefilter before the selector (Phase 2). |
| B-4 | `SelectorResult.reason` is returned but dropped. | all selector runs | Keep it for debugging output (1.9). |
| B-5 | ~~Unguarded debug print in `make_catalog`.~~ **Fixed:** now `LOG.debug`; set `RCA_DEBUG=1` to see the catalog. Silent by default. | every selector run | Done. |
| B-6 | ~~"Databaseconnection" in the template.~~ **Not a bug:** the template and the log file both have the space; it was a terminal line-wrap in the pasted output. | S-1 | Closed. |
| B-7 | `STATUS= 404` (equals plus space) and `HTTP 502` are still masked by `build_template`; `5 m` / `3 h` are masked as durations. | step 1.5 | Add if real data needs it. |
| B-8 | On the rare path the model sometimes lowers confidence on its own: GQ-2 ("12 ish") gave `high` 6, `medium` 2, `low` 1 over 9 runs; GQ-1 gave `high` 3 of 3. The answers were correct each time and said plainly that no 503 line is in the evidence, so this is defensible, but it makes the headline query look timid. The code only caps confidence from above. | GQ-2 | Decide after Phase 2: force `high` when the rare path is uncut and the model found a root cause, or keep it honest. |
| B-9 | The use case (project) comes only from the `use_case` argument of `Agent.ask`, not from the question. With `use_case=None` and the same tenant in several projects, one answer can mix evidence from them. **Partly fixed:** `LogEntry.use_case`, per-project rare-event counts and catalog rows, `[project]` in citations and evidence when more than one project is present (checked with two projects sharing TENANT-X). **Still open, post-interview:** read the use case from the question and ask "which project?" when ambiguous. | multi-project data | In README roadmap. |
