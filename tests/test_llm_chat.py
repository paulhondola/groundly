"""groundly/llm/chat.py: raw httpx POST to {base_url}/chat/completions, via MockTransport."""

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


def _handler(response_json, capture=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["headers"] = request.headers
            capture["body"] = request.content
            capture["url"] = str(request.url)
        return httpx.Response(200, json=response_json)

    return handler


def _completion_json(text="A deadlock is [chunk 1].", prompt_tokens=10, completion_tokens=5):
    return {
        "model": "qwen2.5-7b",
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def test_complete_allows_slow_local_first_token(home):
    """A local runtime JIT-loads the model on first request — minutes, not httpx's
    5 s default. The request must carry a generous read timeout."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions["timeout"]
        return httpx.Response(200, json=_completion_json())

    complete("chat", [{"role": "user", "content": "hi"}], transport=httpx.MockTransport(handler))
    assert seen["timeout"]["read"] >= 120


def test_complete_parses_text_and_tokens(home):
    transport = httpx.MockTransport(_handler(_completion_json()))
    result = complete("chat", [{"role": "user", "content": "hi"}], transport=transport)
    assert result.text == "A deadlock is [chunk 1]."
    assert result.tokens == 15
    assert result.model == "qwen2.5-7b"


def test_complete_computes_cost_when_prices_configured(home):
    transport = httpx.MockTransport(
        _handler(_completion_json(prompt_tokens=1000, completion_tokens=1000))
    )
    result = complete("chat", [{"role": "user", "content": "hi"}], transport=transport)
    # 1000 prompt tok * $1/Mtok + 1000 completion tok * $2/Mtok = 0.001 + 0.002
    assert result.cost_usd == pytest.approx(0.003)


def test_complete_cost_none_without_prices(monkeypatch, tmp_path, home):
    (home / "config.toml").write_text(
        '[providers.chat]\nbase_url = "http://localhost:1234/v1"\nmodel = "m"\n'
    )
    transport = httpx.MockTransport(_handler(_completion_json()))
    result = complete("chat", [{"role": "user", "content": "hi"}], transport=transport)
    assert result.cost_usd is None


def test_complete_sends_api_key_header(home):
    capture = {}
    transport = httpx.MockTransport(_handler(_completion_json(), capture=capture))
    complete("chat", [{"role": "user", "content": "hi"}], transport=transport)
    assert capture["headers"]["authorization"] == "Bearer sk-local"
    assert capture["url"] == "http://localhost:1234/v1/chat/completions"


def test_complete_unreachable_names_cause(home):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(handler)
    with pytest.raises(ChatUnreachableError, match="unreachable"):
        complete("chat", [{"role": "user", "content": "hi"}], transport=transport)
