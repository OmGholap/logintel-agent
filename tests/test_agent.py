import pytest

from logintel.agent import Agent
from conftest import FakeLLM, line, make_agent


def test_rare_events_answer_is_high_confidence_and_cites_the_real_line(rare_rows):
    llm = FakeLLM()  # cites the first line it was shown: the only rare event
    ans = make_agent(rare_rows, llm).ask("What went wrong for TENANT-X?")
    assert (ans.path, ans.confidence, ans.tenant_id) == ("rare_events", "high", "TENANT-X")
    assert ans.citations == ["2026-05-26T12:00:30.000 ERROR svc.db Disk quota exceeded for volume vol-7"]
    assert llm.calls == ["ExtractedParams", "FinalAnswer"]  # the selector never ran


def test_selector_path_picks_rows_and_confidence_is_capped_at_medium(busy_rows):
    llm = FakeLLM(tenant="TENANT-Y", selected=[1], confidence="high")  # the model over-claims
    ans = make_agent(busy_rows, llm).ask("Any retries for TENANT-Y?")
    assert ans.path == "selector" and ans.confidence == "medium"
    assert llm.calls == ["ExtractedParams", "SelectorResult", "FinalAnswer"]


def test_unknown_tenant_answers_no_logs_without_calling_the_model_again(rare_rows):
    llm = FakeLLM(tenant="TENANT-NOPE")
    ans = make_agent(rare_rows, llm).ask("What happened to TENANT-NOPE?")
    assert (ans.path, ans.confidence) == ("no_logs", "low") and ans.answer.startswith("No logs for TENANT-NOPE")
    assert llm.calls == ["ExtractedParams"]


def test_no_tenant_asks_for_one(rare_rows):
    llm = FakeLLM(tenant=None)
    ans = make_agent(rare_rows, llm).ask("What happened?")
    assert ans.path == "clarify" and "Which tenant" in ans.answer and llm.calls == ["ExtractedParams"]


def test_a_time_outside_the_data_asks_instead_of_searching(rare_rows):
    llm = FakeLLM(start="2026-05-27T12:00:00", end="2026-05-27T12:05:00")
    ans = make_agent(rare_rows, llm).ask("What happened to TENANT-X on the 27th?")
    assert ans.path == "clarify" and "no logs for that time" in ans.answer


def test_nothing_matching_gives_the_no_match_answer_not_a_made_up_one(busy_rows):
    llm = FakeLLM(tenant="TENANT-Y", selected=[], open_ended=False)
    ans = make_agent(busy_rows, llm).ask("Why did the disk fill up for TENANT-Y?")
    assert ans.path == "no_match" and "none matched" in ans.answer
    assert "FinalAnswer" not in llm.calls and llm.calls.count("SelectorResult") == 2  # asked once, then confirmed


def test_an_open_ended_question_falls_back_to_the_top_rows(busy_rows):
    llm = FakeLLM(tenant="TENANT-Y", selected=[], open_ended=True)
    ans = make_agent(busy_rows, llm).ask("What happened to TENANT-Y?")
    assert ans.path == "selector" and "FinalAnswer" in llm.calls


def test_ids_the_model_invents_are_dropped_and_the_gap_is_stated(rare_rows):
    ans = make_agent(rare_rows, FakeLLM(cite=[999999])).ask("What went wrong for TENANT-X?")
    assert ans.supporting_log_ids == [] and ans.citations == []
    assert "No log lines were cited" in ans.answer


def test_a_model_failure_becomes_a_low_confidence_error_answer(rare_rows):
    ans = make_agent(rare_rows, FakeLLM(fail=RuntimeError("boom"))).ask("q")
    assert (ans.path, ans.confidence) == ("error", "low") and "could not be completed" in ans.answer


def two_projects():
    a = [("A", line(f"12:00:{i:02d}", "TENANT-X", "INFO", "s", f"ok {i}")) for i in range(10)]
    a.append(("A", line("12:00:40", "TENANT-X", "ERROR", "s", "Out of memory")))
    b = [("B", line(f"12:00:{i:02d}", "TENANT-X", "ERROR", "s", "Out of memory")) for i in range(10, 40)]
    return a + b


def test_use_case_limits_the_search_to_one_project():
    agent = make_agent(two_projects(), FakeLLM())
    assert agent.ask("q", use_case="A").path == "rare_events"   # one Out of memory in A is rare
    assert agent.ask("q", use_case="B").path == "selector"      # thirty in B is routine
    with pytest.raises(ValueError, match="no logs"):
        agent.ask("q", use_case="C")


def test_searching_all_projects_keeps_their_counts_apart_and_names_the_project():
    llm = FakeLLM()
    ans = make_agent(two_projects(), llm).ask("What happened to TENANT-X?")
    assert ans.path == "rare_events" and ans.citations[0].startswith("[A] ")
    assert "use_case=A" in llm.prompts[-1]


def test_a_use_case_cannot_inject_logql(rare_rows):
    with pytest.raises(ValueError, match="Invalid use case"):
        make_agent(rare_rows, FakeLLM()).ask("q", use_case='a"} or {x=~".+')


def test_logs_are_read_once_per_ttl_per_use_case(rare_rows):
    requests, now = [], [0.0]
    agent = make_agent(rare_rows, FakeLLM(), ttl=60, clock=lambda: now[0], requests=requests)
    agent.ask("q", use_case="svc")
    first = len(requests)
    agent.ask("q again", use_case="svc")
    assert len(requests) == first                        # cached
    now[0] = 61
    agent.ask("after the ttl", use_case="svc")
    assert len(requests) == 2 * first                    # read again
    agent.refresh()
    agent.ask("after refresh", use_case="svc")
    assert len(requests) == 3 * first
