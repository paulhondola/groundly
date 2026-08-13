"""`groundly eval-grounding`: the CLI wrapper over groundly/eval/grounding.py.

The sweep spends real money on two providers and a subprocess host per question, so the
failure paths that matter are the ones that must fire *before* anything is spent: a
missing provider, an unaskable arm, an absent gold set. Every one prints a named cause
through `_fail` and exits 1 — never a bare traceback."""

import json

from typer.testing import CliRunner

from groundly.cli import app

runner = CliRunner()

_GOLD = {
    "id": "q1",
    "query": "what causes a deadlock?",
    "lang": "en",
    "class": "factoid",
    "expected": [{"file": "lec.pdf", "page": 1}],
}


def _gold_file(tmp_path):
    path = tmp_path / "gold.jsonl"
    path.write_text(json.dumps(_GOLD))
    return path


def _provider(model="m"):
    from groundly.core.config import ProviderConfig

    return ProviderConfig(base_url="http://localhost:1234/v1", model=model)


def _configured(monkeypatch, *, judge=True, chat=True):
    def _load(call_class):
        if call_class == "judge":
            return _provider("judge-model") if judge else None
        if call_class == "chat":
            return _provider("chat-model") if chat else None
        return _provider()

    monkeypatch.setattr("groundly.core.config.load_provider", _load)


def test_missing_judge_provider_fails_before_spending_anything(
    retrievable_subject, tmp_path, monkeypatch
):
    """A sweep that dies at question 30 because the judge was never configured has spent
    real money on 29 host sessions to learn a fact that was knowable up front."""
    _configured(monkeypatch, judge=False)

    def _must_not_run(*a, **k):
        raise AssertionError("the sweep started without a judge provider")

    monkeypatch.setattr("groundly.eval.grounding.run", _must_not_run)
    result = runner.invoke(
        app,
        ["eval-grounding", retrievable_subject, "--gold", str(_gold_file(tmp_path)), "-y"],
    )
    assert result.exit_code == 1
    assert "providers.judge" in result.output
    assert "faithfulness scoring" in result.output


def test_missing_chat_provider_fails_before_spending_anything(
    retrievable_subject, tmp_path, monkeypatch
):
    _configured(monkeypatch, chat=False)
    result = runner.invoke(
        app,
        ["eval-grounding", retrievable_subject, "--gold", str(_gold_file(tmp_path)), "-y"],
    )
    assert result.exit_code == 1
    assert "providers.chat" in result.output


def test_an_unranked_arm_is_refused_by_name(retrievable_subject, tmp_path, monkeypatch):
    """`graph-global` returns no relevance order, so `ask` cannot ground an answer in its
    top results. The message names the arm rather than saying 'invalid'."""
    _configured(monkeypatch)
    result = runner.invoke(
        app,
        [
            "eval-grounding",
            retrievable_subject,
            "--gold",
            str(_gold_file(tmp_path)),
            "--arm",
            "graph-global",
            "-y",
        ],
    )
    assert result.exit_code == 1
    assert "graph-global" in result.output


def test_unknown_arm_is_refused(retrievable_subject, tmp_path, monkeypatch):
    _configured(monkeypatch)
    result = runner.invoke(
        app,
        [
            "eval-grounding",
            retrievable_subject,
            "--gold",
            str(_gold_file(tmp_path)),
            "--arm",
            "vecotr",
            "-y",
        ],
    )
    assert result.exit_code == 1
    assert "unknown retrieval arm" in result.output


def test_missing_gold_set_fails_with_the_path(retrievable_subject, tmp_path, monkeypatch):
    _configured(monkeypatch)
    result = runner.invoke(
        app,
        ["eval-grounding", retrievable_subject, "--gold", str(tmp_path / "nope.jsonl"), "-y"],
    )
    assert result.exit_code == 1
    assert "nope.jsonl" in result.output


def test_the_cost_estimate_names_host_sessions_not_just_calls(
    retrievable_subject, tmp_path, monkeypatch
):
    """Path B is 48 whole agent sessions, each free to search repeatedly. Reporting it as
    '48 calls' understates it by however many times the host chooses to query."""
    _configured(monkeypatch)
    result = runner.invoke(
        app,
        ["eval-grounding", retrievable_subject, "--gold", str(_gold_file(tmp_path))],
        input="n\n",
    )
    assert result.exit_code == 1  # declined at the prompt
    assert "host sessions" in result.output
    assert "search as often as it likes" in result.output


def test_the_unpublishable_system_prompt_caveat_is_printed(
    retrievable_subject, tmp_path, monkeypatch
):
    """The one limitation of choosing a real host over a scripted prompt. It belongs in
    front of the person about to spend money on the run, not only in the thesis."""
    _configured(monkeypatch)
    result = runner.invoke(
        app,
        ["eval-grounding", retrievable_subject, "--gold", str(_gold_file(tmp_path))],
        input="n\n",
    )
    assert "not publishable" in result.output
    assert "re-runnable, not frozen" in result.output
