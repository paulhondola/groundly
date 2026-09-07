"""groundly/agents/probe.py: the citation-compliance probe.

The pipeline's `[chunk N]` mandate is a model capability, not a given — measured
2026-08-16, `gpt-oss-120b` produced no resolvable citation on 28% of apd questions
and 47% of passc, where `Qwen3-235B-A22B-Instruct-2507` produced 0% on both. The
probe turns that from a refusal rate the student reads as a broken product into one
named answer about their model."""

import pytest

from groundly.agents.probe import PROBE_CHUNK_ID, probe_citation_compliance
from groundly.llm.config import ProviderNotConfiguredError


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def _configure_chat(home):
    (home / "config.toml").write_text(
        '[providers.chat]\nbase_url = "http://x"\nmodel = "m"\napi_key = "sk"\n'
    )


def test_probe_passes_when_the_model_cites_the_synthetic_chunk(home, monkeypatch, stub_chat):
    _configure_chat(home)
    chat = stub_chat(f"The escape velocity is 42 m/s [chunk {PROBE_CHUNK_ID}].")
    monkeypatch.setattr("groundly.agents.probe.complete", chat)

    result = probe_citation_compliance()

    assert result.compliant is True


def test_probe_fails_when_the_model_answers_without_citing(home, monkeypatch, stub_chat):
    _configure_chat(home)
    chat = stub_chat("The escape velocity is 42 m/s.")
    monkeypatch.setattr("groundly.agents.probe.complete", chat)

    result = probe_citation_compliance()

    assert result.compliant is False


def test_probe_fails_when_the_model_cites_a_chunk_it_was_never_given(home, monkeypatch, stub_chat):
    """A marker alone is not compliance. `resolve_citations` drops ids outside the
    retrieved set, so a model that emits plausible-looking markers of its own invention
    fails the pipeline exactly as one that emits none."""
    _configure_chat(home)
    chat = stub_chat("The escape velocity is 42 m/s [chunk 999].")
    monkeypatch.setattr("groundly.agents.probe.complete", chat)

    result = probe_citation_compliance()

    assert result.compliant is False


def test_probe_sends_the_real_ask_prompt_shape(home, monkeypatch, stub_chat):
    """Object for object, the discipline `_probe_extraction` documents: a probe that
    approximates the pipeline tests a capability nobody exercises."""
    from groundly.agents.prompts import SYSTEM_RULES

    _configure_chat(home)
    chat = stub_chat(f"[chunk {PROBE_CHUNK_ID}]")
    monkeypatch.setattr("groundly.agents.probe.complete", chat)

    probe_citation_compliance()

    call_class, messages = chat.calls[0]
    assert call_class == "chat"
    assert messages[0]["content"] == SYSTEM_RULES
    assert f'<chunk id="{PROBE_CHUNK_ID}"' in messages[1]["content"]
    assert "<course-materials>" in messages[1]["content"]


def test_probe_reports_the_model_and_what_the_call_cost(home, monkeypatch, stub_chat):
    """`config check` prints these instead of writing a trace row: the probe is a global
    verb and traces live in a per-subject progress.db (see the module docstring)."""
    _configure_chat(home)
    chat = stub_chat(f"[chunk {PROBE_CHUNK_ID}]", model="qwen-3-235b", tokens=37, cost_usd=0.0004)
    monkeypatch.setattr("groundly.agents.probe.complete", chat)

    result = probe_citation_compliance()

    assert result.model == "qwen-3-235b"
    assert result.tokens == 37
    assert result.cost_usd == 0.0004


def test_probe_refuses_without_a_configured_chat_provider(home, monkeypatch):
    def must_not_call(*a, **k):
        raise AssertionError("must not reach a provider that is not configured")

    monkeypatch.setattr("groundly.agents.probe.complete", must_not_call)

    with pytest.raises(ProviderNotConfiguredError):
        probe_citation_compliance()
