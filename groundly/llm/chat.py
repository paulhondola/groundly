"""Chat completion client: litellm.completion() against any OpenAI-compatible
endpoint (LM Studio, Ollama, cloud providers) — see .claude/rules/architecture.md:
LLM clients constructed only in llm/. Every call resolves its provider from
groundly.llm.config, so callers only ever name a call class.

litellm import stays lazy (inside complete()): cold import costs ~2.5s and must
never happen at MCP spawn time. LITELLM_LOCAL_MODEL_COST_MAP must be set before
litellm is imported anywhere in the process — otherwise litellm's own __init__
fetches its price map from GitHub, violating the privacy rule
(.claude/rules/grounding-and-privacy.md): nothing may leave the machine except
calls to the student's own configured provider."""

import os
from dataclasses import dataclass
from typing import Protocol

import httpx

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from groundly.llm.config import load_settings, require_provider

_LOCAL_PLACEHOLDER_KEY = "not-needed"  # LM Studio/Ollama ignore the Authorization header


@dataclass
class ChatResult:
    text: str
    tokens: int
    cost_usd: float | None
    model: str


class ChatFn(Protocol):
    def __call__(self, call_class: str, messages: list[dict]) -> ChatResult: ...


class ChatUnreachableError(Exception):
    """The configured chat provider could not be reached (network/HTTP error)."""


def complete(call_class: str, messages: list[dict]) -> ChatResult:
    import litellm
    import openai

    litellm.telemetry = False  # privacy: no phone-home (grounding-and-privacy.md)
    # litellm print()s a "Give Feedback / Get Help" banner to *stdout* on any provider
    # exception. `groundly mcp` speaks the MCP protocol over stdout and calls this
    # in-process, so an unreachable provider would corrupt the JSON-RPC stream.
    litellm.suppress_debug_info = True

    cfg = require_provider(call_class)
    try:
        response = litellm.completion(
            model=f"openai/{cfg.model}",
            messages=messages,
            api_base=cfg.base_url,
            api_key=cfg.api_key or _LOCAL_PLACEHOLDER_KEY,
            # Local runtimes (LM Studio, Ollama) JIT-load the model on first request
            # and can take minutes to first token; a dead host should still fail fast —
            # 10s connect, configurable read (litellm passes httpx.Timeout through).
            timeout=httpx.Timeout(10.0, read=load_settings().llm.timeout_seconds),
        )
    except openai.APIError as exc:
        # Every litellm exception raised by completion() (connection failures,
        # timeouts, HTTP status errors) subclasses openai.APIError — the tightest
        # common base covering this call's whole failure surface.
        raise ChatUnreachableError(
            f"[providers.{call_class}] at {cfg.base_url} is unreachable: {exc}"
        ) from exc

    text = response.choices[0].message.content
    usage = response.usage
    prompt_tokens = usage.prompt_tokens
    completion_tokens = usage.completion_tokens
    tokens = usage.total_tokens

    if cfg.input_price_per_mtok is not None and cfg.output_price_per_mtok is not None:
        cost_usd = (
            prompt_tokens * cfg.input_price_per_mtok + completion_tokens * cfg.output_price_per_mtok
        ) / 1_000_000
    else:
        try:
            cost_usd = litellm.completion_cost(completion_response=response)
        except Exception:
            # Unmapped/local model — litellm can't price it, not an error condition.
            cost_usd = None

    return ChatResult(
        text=text, tokens=tokens, cost_usd=cost_usd, model=response.model or cfg.model
    )
