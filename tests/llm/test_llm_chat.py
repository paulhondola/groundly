"""groundly/llm/chat.py: litellm.completion() against any OpenAI-compatible endpoint,
stubbed by monkeypatching litellm.completion/completion_cost at the module attribute
litellm's own object (the seam chat.py's lazy `import litellm` resolves against)."""

from types import SimpleNamespace

import pytest

from groundly.llm.chat import ChatUnreachableError, complete


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "config.toml").write_text(
        '[providers.chat]\nbase_url = "http://localhost:1234/v1"\nmodel = "qwen2.5-7b"\n'
        'api_key = "sk-local"\ninput_price_per_mtok = 1.0\noutput_price_per_mtok = 2.0\n'
    )
    return tmp_path / "home"


def _response(
    text="A deadlock is [chunk 1].", prompt_tokens=10, completion_tokens=5, model="qwen2.5-7b"
):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
        model=model,
    )


def _stub_completion(monkeypatch, response=None, exc=None, capture=None):
    import litellm

    def fake_completion(**kwargs):
        if capture is not None:
            capture.update(kwargs)
        if exc is not None:
            raise exc
        return response or _response()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    return capture


def test_complete_parses_text_and_tokens(monkeypatch, home):
    _stub_completion(monkeypatch, _response())
    result = complete("chat", [{"role": "user", "content": "hi"}])
    assert result.text == "A deadlock is [chunk 1]."
    assert result.tokens == 15
    assert result.model == "qwen2.5-7b"


def test_complete_computes_cost_when_prices_configured(monkeypatch, home):
    _stub_completion(monkeypatch, _response(prompt_tokens=1000, completion_tokens=1000))
    result = complete("chat", [{"role": "user", "content": "hi"}])
    # 1000 prompt tok * $1/Mtok + 1000 completion tok * $2/Mtok = 0.001 + 0.002
    assert result.cost_usd == pytest.approx(0.003)


def test_manual_price_wins_over_litellm_auto_cost(monkeypatch, home):
    """Both a manual price and a mapped model are available — manual formula wins."""
    import litellm

    _stub_completion(monkeypatch, _response(prompt_tokens=1000, completion_tokens=1000))
    monkeypatch.setattr(
        litellm,
        "completion_cost",
        lambda **kw: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    result = complete("chat", [{"role": "user", "content": "hi"}])
    assert result.cost_usd == pytest.approx(0.003)


def test_complete_auto_cost_for_mapped_model_without_manual_prices(monkeypatch, tmp_path, home):
    (home / "config.toml").write_text(
        '[providers.chat]\nbase_url = "http://localhost:1234/v1"\nmodel = "gpt-4o-mini"\n'
    )
    import litellm

    _stub_completion(monkeypatch, _response(model="gpt-4o-mini"))
    monkeypatch.setattr(litellm, "completion_cost", lambda **kw: 0.0042)
    result = complete("chat", [{"role": "user", "content": "hi"}])
    assert result.cost_usd == pytest.approx(0.0042)


def test_complete_cost_none_for_unmapped_model(monkeypatch, tmp_path, home):
    (home / "config.toml").write_text(
        '[providers.chat]\nbase_url = "http://localhost:1234/v1"\nmodel = "m"\n'
    )
    import litellm

    _stub_completion(monkeypatch, _response())

    def raise_unmapped(**kw):
        raise Exception("This model isn't mapped yet")

    monkeypatch.setattr(litellm, "completion_cost", raise_unmapped)
    result = complete("chat", [{"role": "user", "content": "hi"}])
    assert result.cost_usd is None


def test_complete_sends_api_key_and_model_and_base_url(monkeypatch, home):
    capture = {}
    _stub_completion(monkeypatch, _response(), capture=capture)
    complete("chat", [{"role": "user", "content": "hi"}])
    assert capture["api_key"] == "sk-local"
    assert capture["api_base"] == "http://localhost:1234/v1"
    assert capture["model"] == "openai/qwen2.5-7b"


def test_complete_keyless_provider_gets_placeholder_key(monkeypatch, tmp_path, home):
    (home / "config.toml").write_text(
        '[providers.chat]\nbase_url = "http://localhost:1234/v1"\nmodel = "m"\n'
    )
    capture = {}
    _stub_completion(monkeypatch, _response(), capture=capture)
    complete("chat", [{"role": "user", "content": "hi"}])
    assert capture["api_key"] == "not-needed"


def test_complete_passes_timeout_from_settings(monkeypatch, tmp_path, home):
    (home / "config.toml").write_text(
        '[providers.chat]\nbase_url = "http://localhost:1234/v1"\nmodel = "m"\n'
        "\n[llm]\ntimeout_seconds = 123.0\n"
    )
    capture = {}
    _stub_completion(monkeypatch, _response(), capture=capture)
    complete("chat", [{"role": "user", "content": "hi"}])
    assert capture["timeout"] == 123.0


def test_complete_unreachable_names_cause(monkeypatch, home):
    import openai

    _stub_completion(
        monkeypatch, exc=openai.APIConnectionError(request=None, message="connection refused")
    )
    with pytest.raises(ChatUnreachableError, match="unreachable"):
        complete("chat", [{"role": "user", "content": "hi"}])
