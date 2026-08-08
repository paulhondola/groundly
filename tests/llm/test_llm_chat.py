"""groundly/llm/chat.py: litellm.completion() against any OpenAI-compatible endpoint,
stubbed by monkeypatching litellm.completion/completion_cost at the module attribute
litellm's own object (the seam chat.py's lazy `import litellm` resolves against)."""

from types import SimpleNamespace

import httpx
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


def test_complete_nests_reasoning_effort_under_extra_body(monkeypatch, home):
    """Same nesting as llm/graphrag_adapter.completion_model_config, for the same
    reason: litellm's drop_params is False, so a flat reasoning_effort kwarg raises
    UnsupportedParamsError on every call instead of degrading — nesting under
    extra_body is what actually reaches the provider."""
    (home / "config.toml").write_text(
        '[providers.chat]\nbase_url = "http://localhost:1234/v1"\nmodel = "qwen2.5-7b"\n'
        'api_key = "sk-local"\nreasoning_effort = "low"\n'
    )
    capture = {}
    _stub_completion(monkeypatch, _response(), capture=capture)
    complete("chat", [{"role": "user", "content": "hi"}])
    assert capture["extra_body"] == {"reasoning_effort": "low"}


def test_complete_omits_extra_body_when_reasoning_effort_unset(monkeypatch, home):
    capture = {}
    _stub_completion(monkeypatch, _response(), capture=capture)
    complete("chat", [{"role": "user", "content": "hi"}])
    assert "extra_body" not in capture


def test_complete_keyless_provider_gets_placeholder_key(monkeypatch, tmp_path, home):
    (home / "config.toml").write_text(
        '[providers.chat]\nbase_url = "http://localhost:1234/v1"\nmodel = "m"\n'
    )
    capture = {}
    _stub_completion(monkeypatch, _response(), capture=capture)
    complete("chat", [{"role": "user", "content": "hi"}])
    assert capture["api_key"] == "not-needed"


def test_complete_passes_split_timeout_from_settings(monkeypatch, tmp_path, home):
    # 10s connect (a dead host fails fast) + configurable read (local models are
    # slow to first token) — litellm passes httpx.Timeout through unchanged.
    (home / "config.toml").write_text(
        '[providers.chat]\nbase_url = "http://localhost:1234/v1"\nmodel = "m"\n'
        "\n[llm]\ntimeout_seconds = 123.0\n"
    )
    capture = {}
    _stub_completion(monkeypatch, _response(), capture=capture)
    complete("chat", [{"role": "user", "content": "hi"}])
    timeout = capture["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.read == 123.0
    assert timeout.connect == 10.0


def test_complete_unreachable_names_cause(monkeypatch, home):
    import openai

    _stub_completion(
        monkeypatch, exc=openai.APIConnectionError(request=None, message="connection refused")
    )
    with pytest.raises(ChatUnreachableError, match="unreachable"):
        complete("chat", [{"role": "user", "content": "hi"}])


def test_temperature_is_pinned_to_zero_by_default(monkeypatch, home):
    """Unset temperature means the provider's default (~1.0). Measured on gpt-oss-120b:
    one unchanged router question returned three different labels across 10 calls, and
    whole-gold-set router accuracy swung 39.6% -> 58.3% between two identical runs. Any
    published number that passes through a model is a draw from a distribution until this
    is pinned, so the default is 0.0 and sampling must be opted into per call class."""
    capture = _stub_completion(monkeypatch, capture={})
    complete("chat", [{"role": "user", "content": "hi"}])
    assert capture["temperature"] == 0.0


def test_temperature_can_be_opted_out_per_call_class(monkeypatch, home):
    """Deck generation may legitimately want variety; a classifier never does."""
    (home / "config.toml").write_text(
        '[providers.chat]\nbase_url = "http://localhost:1234/v1"\nmodel = "qwen2.5-7b"\n'
        'api_key = "sk-local"\ntemperature = 0.8\n'
    )
    capture = _stub_completion(monkeypatch, capture={})
    complete("chat", [{"role": "user", "content": "hi"}])
    assert capture["temperature"] == 0.8
