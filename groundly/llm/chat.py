"""Chat completion client: raw httpx POST to {base_url}/chat/completions — no openai
SDK (avoids a new dependency), no OpenAILike (not installed). LLM HTTP happens only
here (.claude/rules/architecture.md); every call resolves its provider from
groundly.llm.config, so callers only ever name a call class."""

from dataclasses import dataclass
from typing import Protocol

import httpx

from groundly.llm.config import require_provider

# Local runtimes (LM Studio, Ollama) JIT-load the model on first request and can
# take minutes to first token on weak hardware; httpx's 5 s default aborts them.
_TIMEOUT = httpx.Timeout(10.0, read=300.0)


@dataclass
class ChatResult:
    text: str
    tokens: int
    cost_usd: float | None
    model: str


class ChatFn(Protocol):
    def __call__(
        self,
        call_class: str,
        messages: list[dict],
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> ChatResult: ...


class ChatUnreachableError(Exception):
    """The configured chat provider could not be reached (network/HTTP error)."""


def complete(
    call_class: str,
    messages: list[dict],
    *,
    transport: httpx.BaseTransport | None = None,
) -> ChatResult:
    cfg = require_provider(call_class)
    headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
    url = f"{cfg.base_url.rstrip('/')}/chat/completions"
    try:
        with httpx.Client(transport=transport, timeout=_TIMEOUT) as client:
            response = client.post(
                url, json={"model": cfg.model, "messages": messages}, headers=headers
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as exc:
        raise ChatUnreachableError(
            f"[providers.{call_class}] at {cfg.base_url} is unreachable: {exc}"
        ) from exc

    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)
    tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

    cost_usd = None
    if cfg.input_price_per_mtok is not None and cfg.output_price_per_mtok is not None:
        cost_usd = (
            prompt_tokens * cfg.input_price_per_mtok + completion_tokens * cfg.output_price_per_mtok
        ) / 1_000_000

    return ChatResult(
        text=text, tokens=tokens, cost_usd=cost_usd, model=data.get("model", cfg.model)
    )
