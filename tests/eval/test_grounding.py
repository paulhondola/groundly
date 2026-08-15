"""groundly/eval/grounding.py: the enforced-vs-host comparison.

The tests that earn their keep here are the ones guarding against the harness flattering
the enforced path — the allowlist that stops path B calling `ask`, the refusal that must
not score as perfect faithfulness, and the matched subset that keeps a retrieval
difference from being read as a composition difference."""

import json
import subprocess
from pathlib import Path

import pytest

from groundly.eval.grounding import (
    ASK,
    HOST,
    AskConfig,
    GroundingScored,
    HostConfig,
    aggregate,
    host_argv,
    run,
    run_host,
    write_results,
)
from groundly.eval.grounding import DIRECTED_CONDITION, HOST_DIRECTED, NEUTRAL_CONDITION

_CFG = HostConfig(model="pinned-model", claude_bin="claude", groundly_bin="groundly")

# The sweep is what these tests exercise, not the cross-encoder — loading a real one
# costs ~9s per test for a ranking no assertion here reads.
_NO_RERANK = AskConfig(rerank=False)


def _row(path, **over):
    base = dict(
        question_id="q1",
        klass="factoid",
        lang="en",
        path=path,
        outcome="answered",
        seen_chunks=[1, 2],
        n_searches=1,
        answer="an answer",
        attributions_n=0,
        attributions_resolvable=0,
        attributions=[],
        claims_total=None,
        claims_supported=None,
        cited_support=None,
        hit=None,
        hit_second_run=None,
        error=None,
        model="m",
        tokens=10,
        cost_usd=0.01,
        latency_ms=100,
    )
    return GroundingScored(**{**base, **over})


# ---------------------------------------------------------------- the host invocation


def test_host_argv_pins_the_tool_allowlist_to_search():
    """The single hard constraint on an otherwise free host. Without it path B can call
    the very pipeline it is the control for, and the experiment measures nothing."""
    argv = host_argv(_CFG, "prompt", "apd")
    assert argv[argv.index("--allowedTools") + 1] == "mcp__groundly__search"


def test_host_argv_denies_the_built_in_tools_by_name():
    """**`--allowedTools` alone does not block `Read`** — measured, a host with only that
    flag read a canary file in its cwd. Without a second guard the host keeps Read/Glob
    and can open `evals/<subject>/gold.jsonl`, the answer key, scoring path B brilliantly
    for reasons that have nothing to do with composing from `search`."""
    argv = host_argv(_CFG, "prompt", "apd")
    denied = argv[argv.index("--disallowedTools") + 1 : argv.index("--allowedTools")]
    assert "Read" in denied and "Bash" in denied and "Glob" in denied


def test_host_argv_never_passes_tools_empty():
    """`--tools ""` looks like the right flag and **disables the MCP tools too**, which
    removes the thing under measurement rather than isolating it. Measured: with it the
    host answers "no such tool is available to me"; without it the same prompt returns 8
    chunks. Pinned because it is the plausible-sounding fix someone will reach for."""
    argv = host_argv(_CFG, "prompt", "apd")
    assert "--tools" not in argv


def test_host_argv_bounds_one_question_when_a_budget_is_set():
    """The host chooses how often to search and `search` has no k ceiling, so an
    unbounded session is an unbounded bill against the student's own key."""
    argv = host_argv(HostConfig(model="m", max_budget_usd=0.5), "p", "apd")
    assert argv[argv.index("--max-budget-usd") + 1] == "0.5"
    assert "--max-budget-usd" not in host_argv(
        HostConfig(model="m", max_budget_usd=None), "p", "apd"
    )


def test_host_argv_isolates_the_host_from_the_machine_that_ran_it():
    """No setting sources (so no CLAUDE.md, hooks, plugins or output style), no skills,
    and no MCP server but ours. Measured on the development machine: 46,555 tokens of
    inherited context without these flags against 8,935 with them — local configuration
    that is unpublishable and changes the answer.

    Deliberately not `--bare`, which strips the same things but forces auth to
    `ANTHROPIC_API_KEY`, refusing the subscription login most people have. Isolation must
    not cost the ability to run the experiment at all."""
    argv = host_argv(_CFG, "prompt", "apd")
    assert argv[argv.index("--setting-sources") + 1] == ""
    assert "--disable-slash-commands" in argv
    assert "--strict-mcp-config" in argv
    assert "--bare" not in argv


def test_host_argv_is_a_list_never_a_shell_string():
    argv = host_argv(_CFG, "prompt; rm -rf /", "apd")
    assert isinstance(argv, list)
    assert "prompt; rm -rf /" in argv


def test_host_argv_points_the_mcp_server_at_groundly_mcp():
    config = json.loads(
        host_argv(_CFG, "p", "apd")[host_argv(_CFG, "p", "apd").index("--mcp-config") + 1]
    )
    assert config["mcpServers"]["groundly"] == {"command": "groundly", "args": ["mcp"]}


def _fake_run(payload, *, returncode=0, stdout=None):
    def _run(argv, **kwargs):
        return subprocess.CompletedProcess(
            argv, returncode, stdout if stdout is not None else json.dumps(payload), ""
        )

    return _run


def test_run_host_parses_the_answer_and_its_cost(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run(
            {
                "is_error": False,
                "result": "Deadlocks need circular wait.",
                "total_cost_usd": 0.042,
                "duration_ms": 1234,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            }
        ),
    )
    run_result = run_host("why?", "apd", _CFG)
    assert run_result.answer == "Deadlocks need circular wait."
    assert run_result.cost_usd == 0.042
    assert run_result.tokens == 120
    assert run_result.latency_ms == 1234
    assert run_result.error is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"returncode": 1},
        {"stdout": "not json at all"},
    ],
)
def test_run_host_records_failures_rather_than_raising(monkeypatch, kwargs):
    """One question must not destroy a 96-session sweep."""
    monkeypatch.setattr(subprocess, "run", _fake_run({"result": "x"}, **kwargs))
    result = run_host("why?", "apd", _CFG)
    assert result.answer is None
    assert result.error


def test_run_host_records_a_host_reported_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run({"is_error": True, "result": "no credit"}))
    assert run_host("why?", "apd", _CFG).error


def test_a_missing_host_binary_raises_instead_of_failing_48_times(monkeypatch):
    """A configuration fact holds for every remaining question, so it is raised rather
    than recorded once per question into a results file full of identical errors."""

    def _boom(*a, **k):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(subprocess, "run", _boom)
    with pytest.raises(RuntimeError, match="host binary not found"):
        run_host("why?", "apd", _CFG)


# ---------------------------------------------------------------------- the scoring


def test_a_refusal_does_not_score_as_perfect_faithfulness():
    """**The most likely way these numbers could lie in Groundly's favour.** The enforced
    path refuses by design; if a refusal counted as 1.0 it would win by declining."""
    agg = aggregate([_row(ASK, outcome="refused", claims_total=0, claims_supported=0)])
    assert agg.faithfulness is None
    assert agg.refusal_rate == 1.0
    assert agg.refusals == 1


def test_refusal_rate_sits_beside_faithfulness_not_under_it():
    rows = [
        _row(ASK, outcome="refused", claims_total=0, claims_supported=0),
        _row(ASK, claims_total=4, claims_supported=2, hit=False),
    ]
    agg = aggregate(rows)
    assert agg.faithfulness == pytest.approx(0.5)  # the answered row only
    assert agg.refusal_rate == pytest.approx(0.5)


def test_errored_rows_are_excluded_from_quality_but_counted():
    agg = aggregate(
        [
            _row(HOST, outcome="error", error="host timed out"),
            _row(HOST, claims_total=2, claims_supported=2, hit=True),
        ]
    )
    assert agg.errors == 1
    assert agg.n == 1
    assert agg.fully_supported_rate == 1.0


def test_attribution_resolvable_rate_averages_only_over_answers_that_cited():
    """An answer citing nothing has no resolvability to average — folding it in as 0.0
    would conflate 'cited badly' with 'did not cite', which are the two separate layers
    this experiment reports."""
    rows = [_row(HOST, attributions_n=0), _row(HOST, attributions_n=4, attributions_resolvable=3)]
    agg = aggregate(rows)
    assert agg.attribution_present_rate == pytest.approx(0.5)
    assert agg.attribution_resolvable_rate == pytest.approx(0.75)


# ------------------------------------------------------------------------- the sweep


@pytest.fixture
def gold(tmp_path):
    path = tmp_path / "gold.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "q1",
                "query": "what causes deadlock?",
                "lang": "en",
                "class": "factoid",
                "expected": [{"file": "lec.pdf", "page": 1}],
            }
        )
    )
    return path


def _stub_everything(monkeypatch, subject, host_answer, judge_reply, searched=None):
    """Path A through the real `ask` pipeline with a stubbed chat provider, path B through
    a stubbed subprocess, and a stubbed judge. Everything between is the real code."""
    from groundly.llm.chat import ChatResult

    def _chat(call_class, messages, **kwargs):
        return ChatResult(
            text="Deadlock needs circular wait [chunk 1].",
            tokens=5,
            cost_usd=0.001,
            model="stub-chat",
        )

    monkeypatch.setattr("groundly.agents.ask.complete", _chat)
    monkeypatch.setattr("groundly.agents.ask.require_provider", lambda _c: None)

    payload = {
        "is_error": False,
        "result": host_answer,
        "total_cost_usd": 0.01,
        "duration_ms": 500,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }

    def _run(argv, **kwargs):
        # What the real MCP server does from its own process (retrieval/vector.py traces
        # every `search`). Writing it here is what makes this exercise the mechanism path B
        # rests on — bracket with `max_trace_id`, run the subprocess, read back what it
        # wrote — instead of trusting that it works.
        if searched is not None:
            from groundly.core.progress import connect_progress, record_trace
            from groundly.core.subject import Subject

            conn = connect_progress(Subject(subject).progress_db_path)
            try:
                record_trace(
                    conn,
                    kind="search",
                    query="what causes deadlock?",
                    arm="vector",
                    chunk_ids=list(searched),
                    outcome="results",
                )
            finally:
                conn.close()
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    monkeypatch.setattr(subprocess, "run", _run)
    monkeypatch.setattr(
        "groundly.eval.judge.complete",
        lambda call_class, messages, **kw: ChatResult(
            text=judge_reply, tokens=5, cost_usd=0.001, model="stub-judge"
        ),
    )


def test_run_produces_a_row_per_path_and_records_provenance(
    retrievable_subject, gold, monkeypatch, tmp_path
):
    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject

    _stub_everything(
        monkeypatch,
        retrievable_subject,
        host_answer="Deadlock needs circular wait (lec.pdf p. 1).",
        judge_reply=json.dumps(
            [{"claim": "deadlock needs circular wait", "supported": True, "supporting_chunk": 1}]
        ),
        searched=[1, 2],
    )
    store = SubjectStore(Subject(retrievable_subject).store_db_path)
    doc = run(retrievable_subject, gold, store, host=_CFG, ask_config=_NO_RERANK, judge_runs=2)

    assert {r["path"] for r in doc["rows"]} == {ASK, HOST}
    assert doc["provenance"]["host"]["model"] == "pinned-model"
    assert doc["provenance"]["judge"]["runs"] == 2
    assert doc["provenance"]["host"]["conditions"][HOST]["task_prompt_sha256"]
    assert doc["provenance"]["judge"]["prompt_sha256"]
    # The graph block is recorded even when the arm needs no graph, so the file is always
    # self-describing — four indistinguishable results files once cost a full misdiagnosis.
    assert "graph" in doc["provenance"]


def test_both_paths_are_scored_by_the_same_attribution_extractor(
    retrievable_subject, gold, monkeypatch
):
    """Path A cites `[chunk 1]`, the host cites `lec.pdf p. 1`. Both must register as a
    present, resolvable attribution — scoring the host with a `[chunk N]` regex would
    report 0.0 and measure the regex rather than the host."""
    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject

    _stub_everything(
        monkeypatch,
        retrievable_subject,
        host_answer="Deadlock needs circular wait (lec.pdf p. 1).",
        judge_reply=json.dumps([{"claim": "c", "supported": True, "supporting_chunk": 1}]),
        searched=[1, 2],
    )
    store = SubjectStore(Subject(retrievable_subject).store_db_path)
    doc = run(retrievable_subject, gold, store, host=_CFG, ask_config=_NO_RERANK, judge_runs=1)

    by_path = {r["path"]: r for r in doc["rows"]}
    assert by_path[ASK]["attributions_n"] == 1
    assert by_path[HOST]["attributions_n"] == 1
    assert by_path[ASK]["attributions_resolvable"] == 1
    assert by_path[HOST]["attributions_resolvable"] == 1


def test_the_host_search_count_comes_from_its_own_trace_rows(
    retrievable_subject, gold, monkeypatch
):
    """No product change was needed for path B: `retrieval/vector.py` has always traced
    every `search`, so how many times the host queried and what it saw are read back out
    of the trace table rather than assumed. The host here searches twice, and the chunks
    it saw are the union of both calls."""
    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject

    _stub_everything(
        monkeypatch,
        retrievable_subject,
        host_answer="An answer.",
        judge_reply="[]",
        searched=[1, 2],
    )
    store = SubjectStore(Subject(retrievable_subject).store_db_path)
    doc = run(retrievable_subject, gold, store, host=_CFG, ask_config=_NO_RERANK, judge_runs=1)
    host_row = next(r for r in doc["rows"] if r["path"] == HOST)
    assert host_row["n_searches"] == 1
    assert host_row["seen_chunks"] == [1, 2]


def test_a_host_that_never_searched_cites_unresolvably(retrievable_subject, gold, monkeypatch):
    """The resolvability layer's whole job. An answer naming a real page it never
    retrieved has made a present attribution that resolves to nothing retrieved — which
    is a different failure from citing nothing at all, and the two are counted apart."""
    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject

    _stub_everything(
        monkeypatch,
        retrievable_subject,
        host_answer="Deadlock needs circular wait (lec.pdf p. 1).",
        judge_reply="[]",
    )
    store = SubjectStore(Subject(retrievable_subject).store_db_path)
    doc = run(retrievable_subject, gold, store, host=_CFG, ask_config=_NO_RERANK, judge_runs=1)
    host_row = next(r for r in doc["rows"] if r["path"] == HOST)
    assert host_row["n_searches"] == 0
    assert host_row["attributions_n"] == 1
    assert host_row["attributions_resolvable"] == 0


def test_write_results_uses_the_gitignored_prefix(tmp_path):
    """`.gitignore` carries an unanchored `results-*.json`, so a file full of course
    content and chunk ids cannot reach the repo by being forgotten."""
    path = write_results({"ts": "2026-08-13T10:00:00+00:00"}, tmp_path)
    assert path.name.startswith("results-grounding-")
    assert path.name.endswith(".json")


def test_the_host_runs_in_a_temp_directory_not_the_repo(monkeypatch, tmp_path):
    """Second of two independent guards. `--tools ""` removes the filesystem tools; this
    removes what they would have reached. Inheriting the sweep's cwd put the agent in the
    repo, one directory from the gold set's answer key and beside previous results files
    — `ingestion/extract.py` runs its far less capable subprocess in a tempdir already."""
    seen = {}

    def _run(argv, **kwargs):
        seen["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(argv, 0, json.dumps({"result": "x"}), "")

    monkeypatch.setattr(subprocess, "run", _run)
    run_host("why?", "apd", _CFG)
    assert seen["cwd"] is not None
    assert "groundly-host-" in str(seen["cwd"])
    assert Path(seen["cwd"]).resolve() != Path.cwd().resolve()


def test_a_host_that_reached_ask_voids_that_question(retrievable_subject, gold, monkeypatch):
    """The isolation claim, verified rather than trusted: the mechanism lives inside a
    third-party CLI's permission handling, so the harness checks the trace table for an
    `ask` row in the host's window. A comparison whose control called the pipeline under
    test is void, and saying so beats publishing it."""
    from groundly.core.progress import connect_progress, record_trace
    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject

    _stub_everything(monkeypatch, retrievable_subject, host_answer="An answer.", judge_reply="[]")
    real_run = subprocess.run

    def _run_and_cheat(argv, **kwargs):
        conn = connect_progress(Subject(retrievable_subject).progress_db_path)
        try:
            record_trace(
                conn,
                kind="ask",
                query="what causes deadlock?",
                arm="vector",
                chunk_ids=[1],
                outcome="answered",
                answer="smuggled",
            )
        finally:
            conn.close()
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", _run_and_cheat)
    store = SubjectStore(Subject(retrievable_subject).store_db_path)
    doc = run(retrievable_subject, gold, store, host=_CFG, ask_config=_NO_RERANK, judge_runs=1)

    host_row = next(r for r in doc["rows"] if r["path"] == HOST)
    assert "reached the `ask` pipeline" in host_row["error"]
    assert host_row["answer"] is None


def test_the_human_review_sample_is_actually_blind(retrievable_subject, gold, monkeypatch):
    """An earlier version carried `path` inline with each answer and called itself blind.
    It was not — the reviewer read the label on the same line as the text it was meant to
    bias them about, and the shuffle blinded nothing."""
    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject

    _stub_everything(
        monkeypatch, retrievable_subject, host_answer="An answer.", judge_reply="[]", searched=[1]
    )
    store = SubjectStore(Subject(retrievable_subject).store_db_path)
    doc = run(retrievable_subject, gold, store, host=_CFG, ask_config=_NO_RERANK, judge_runs=1)

    for item in doc["human_review_sample"]:
        assert set(item) == {"sample_id", "answer"}
    assert set(doc["human_review_key"]) == {i["sample_id"] for i in doc["human_review_sample"]}


def test_judge_spend_is_recorded_separately_from_answer_spend(
    retrievable_subject, gold, monkeypatch
):
    """The instrument's bill is not the experiment's bill. Folding them together would
    overstate what an answer costs; leaving the judge unrecorded would let ~192 LLM calls
    cost nothing on paper."""
    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject

    _stub_everything(
        monkeypatch,
        retrievable_subject,
        host_answer="Deadlock needs circular wait (lec.pdf p. 1).",
        judge_reply=json.dumps([{"claim": "c", "supported": True, "supporting_chunk": 1}]),
        searched=[1],
    )
    store = SubjectStore(Subject(retrievable_subject).store_db_path)
    doc = run(retrievable_subject, gold, store, host=_CFG, ask_config=_NO_RERANK, judge_runs=2)

    assert set(doc["spend_usd"]) == {ASK, HOST, "judge"}
    assert doc["spend_usd"]["judge"] is not None
    judged = [r for r in doc["rows"] if r["judge_cost_usd"] is not None]
    assert judged, "no row recorded what judging it cost"


def test_a_host_that_declines_in_prose_counts_as_a_refusal(retrievable_subject, gold, monkeypatch):
    """A host has no mandated refusal sentence, so its refusals are only visible as the
    judge's empty verdict. Without this the host's refusal rate is 0% by construction
    while the enforced path's is real — and refusal is the column that stops faithfulness
    being won by declining to answer."""
    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject

    _stub_everything(
        monkeypatch,
        retrievable_subject,
        host_answer="I could not find anything about that in the materials.",
        judge_reply="[]",
        searched=[1],
    )
    store = SubjectStore(Subject(retrievable_subject).store_db_path)
    doc = run(retrievable_subject, gold, store, host=_CFG, ask_config=_NO_RERANK, judge_runs=1)
    assert next(r for r in doc["rows"] if r["path"] == HOST)["outcome"] == "refused"


def test_a_host_that_never_searched_is_counted_not_dropped(
    retrievable_subject, gold, monkeypatch
):
    """**The bug the first partial run exposed.** 9 of 12 host sessions answered without
    calling `search` at all, and the harness filed every one as "no source text for the
    chunks this answer saw" — a harness error, excluded from every column. That deleted
    agent-mediated grounding's worst failure from the averages meant to measure it.

    An answer built on nothing retrieved is `ungrounded`: no claim can rest on a chunk
    nobody read, so it is a loss in the paired test and gets its own rate beside
    faithfulness."""
    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject

    _stub_everything(
        monkeypatch,
        retrievable_subject,
        host_answer="Deadlocks require a circular wait, four conditions in total.",
        judge_reply="[]",
    )
    store = SubjectStore(Subject(retrievable_subject).store_db_path)
    doc = run(retrievable_subject, gold, store, host=_CFG, ask_config=_NO_RERANK, judge_runs=1)

    host_row = next(r for r in doc["rows"] if r["path"] == HOST)
    assert host_row["n_searches"] == 0
    assert host_row["seen_chunks"] == []
    assert host_row["outcome"] == "ungrounded"
    assert host_row["error"] is None, "an ungrounded answer is a result, not a harness error"
    assert host_row["hit"] is False, "it must count as a loss in the paired test"

    host_agg = next(a for a in doc["by_path"] if a["slice"]["path"] == HOST)
    assert host_agg["errors"] == 0
    assert host_agg["n"] == 1
    assert host_agg["ungrounded_rate"] == 1.0


def test_ask_without_a_resolvable_citation_is_counted_not_dropped(
    retrievable_subject, gold, monkeypatch
):
    """The mirror-image bug, biasing the other way. `NoCitationsError` is the enforced
    pipeline working exactly as designed — refusing an answer whose citations resolve to
    nothing — and the student still gets no answer. Filing it as a provider outage dropped
    7 of 13 `ask` rows on the first run: the enforced path's own worst cases, removed from
    the average judging it."""
    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject
    from groundly.llm.chat import ChatResult

    _stub_everything(monkeypatch, retrievable_subject, host_answer="x", judge_reply="[]")
    # An answer citing a chunk that was never retrieved: resolve_citations drops it and
    # raises, exactly as it does in production.
    monkeypatch.setattr(
        "groundly.agents.ask.complete",
        lambda *a, **k: ChatResult(
            text="Deadlocks need mutual exclusion [chunk 9999].", tokens=5, cost_usd=0.001,
            model="stub-chat",
        ),
    )
    store = SubjectStore(Subject(retrievable_subject).store_db_path)
    doc = run(retrievable_subject, gold, store, host=_CFG, ask_config=_NO_RERANK, judge_runs=1)

    ask_row = next(r for r in doc["rows"] if r["path"] == ASK)
    assert ask_row["outcome"] == "no_citations"
    assert ask_row["error"] is None, "the pipeline refusing an ungrounded answer is a result"

    ask_agg = next(a for a in doc["by_path"] if a["slice"]["path"] == ASK)
    assert ask_agg["errors"] == 0
    assert ask_agg["no_citation_rate"] == 1.0


def test_genuine_failures_are_still_errors(retrievable_subject, gold, monkeypatch):
    """The counterpart guard: widening what counts as a result must not swallow a real
    provider outage, which would read as a path answering badly rather than not at all."""
    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject

    _stub_everything(monkeypatch, retrievable_subject, host_answer="x", judge_reply="[]")
    monkeypatch.setattr(subprocess, "run", _fake_run({"result": "x"}, returncode=1))
    store = SubjectStore(Subject(retrievable_subject).store_db_path)
    doc = run(retrievable_subject, gold, store, host=_CFG, ask_config=_NO_RERANK, judge_runs=1)

    host_row = next(r for r in doc["rows"] if r["path"] == HOST)
    assert host_row["outcome"] == "error"
    assert host_row["error"]
    assert next(a for a in doc["by_path"] if a["slice"]["path"] == HOST)["errors"] == 1


def test_the_directed_condition_tells_the_host_to_search(retrievable_subject, gold, monkeypatch):
    """The second path-B condition exists to answer the objection the neutral prompt
    invites: *you never told it to search*. Running both separates "will not retrieve"
    from "retrieves and then drifts" — two different product problems that the neutral
    prompt alone reports as one number."""
    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject

    prompts = []
    real = subprocess.run

    def _capture(argv, **kwargs):
        # `_document` also shells out for `git rev-parse` and `claude --version`; only the
        # host invocations carry `-p`.
        if "-p" not in argv:
            return real(argv, **kwargs)
        prompts.append(argv[argv.index("-p") + 1])
        return subprocess.CompletedProcess(argv, 0, json.dumps({"result": "an answer"}), "")

    _stub_everything(monkeypatch, retrievable_subject, host_answer="x", judge_reply="[]")
    monkeypatch.setattr(subprocess, "run", _capture)
    store = SubjectStore(Subject(retrievable_subject).store_db_path)
    doc = run(
        retrievable_subject,
        gold,
        store,
        host=_CFG,
        ask_config=_NO_RERANK,
        conditions=(NEUTRAL_CONDITION, DIRECTED_CONDITION),
        judge_runs=1,
    )

    assert {r["path"] for r in doc["rows"]} == {ASK, HOST, HOST_DIRECTED}
    assert len(prompts) == 2, "one host session per condition"
    neutral, directed = prompts
    assert "search" not in neutral.lower(), "the neutral condition must not mention the tool"
    assert "`search` tool" in directed
    # Neither condition mentions citing: attribution stays unprompted in both, so the
    # attribution layers remain comparable across them.
    assert "cite" not in neutral.lower() and "cite" not in directed.lower()


def test_each_condition_gets_its_own_comparison_and_provenance(
    retrievable_subject, gold, monkeypatch
):
    """Every comparison is `ask` against one condition, so matched-subset and significance
    are per condition. Both prompts are recorded verbatim and by hash — the two differ by
    a single clause, and a condition is only interpretable next to its exact wording."""
    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject

    _stub_everything(
        monkeypatch, retrievable_subject, host_answer="an answer", judge_reply="[]", searched=[1]
    )
    store = SubjectStore(Subject(retrievable_subject).store_db_path)
    doc = run(
        retrievable_subject,
        gold,
        store,
        host=_CFG,
        ask_config=_NO_RERANK,
        conditions=(NEUTRAL_CONDITION, DIRECTED_CONDITION),
        judge_runs=1,
    )

    assert set(doc["comparisons"]) == {HOST, HOST_DIRECTED}
    for comp in doc["comparisons"].values():
        assert {"matched_n", "matched_question_ids", "significance_matched", "significance_all"} <= set(comp)

    recorded = doc["provenance"]["host"]["conditions"]
    assert set(recorded) == {HOST, HOST_DIRECTED}
    assert recorded[HOST]["task_prompt"] != recorded[HOST_DIRECTED]["task_prompt"]
    assert recorded[HOST]["task_prompt_sha256"] != recorded[HOST_DIRECTED]["task_prompt_sha256"]
    assert set(doc["spend_usd"]) == {ASK, HOST, HOST_DIRECTED, "judge"}


def test_the_neutral_condition_is_the_default(retrievable_subject, gold, monkeypatch):
    """The directed condition doubles path B, so it is opt-in."""
    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject

    _stub_everything(monkeypatch, retrievable_subject, host_answer="x", judge_reply="[]")
    store = SubjectStore(Subject(retrievable_subject).store_db_path)
    doc = run(retrievable_subject, gold, store, host=_CFG, ask_config=_NO_RERANK, judge_runs=1)
    assert {r["path"] for r in doc["rows"]} == {ASK, HOST}
    assert set(doc["comparisons"]) == {HOST}
