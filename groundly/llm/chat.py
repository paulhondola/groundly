"""Chat completion via litellm against any OpenAI-compatible endpoint; callers name a call
class and the provider resolves from config. litellm is imported inside complete(): its
multi-second cold import must never run at MCP spawn. The env vars it reads at import are
set in groundly/__init__.py."""

from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

import httpx

from groundly.llm.config import load_settings, require_provider

_LOCAL_PLACEHOLDER_KEY = "not-needed"  # LM Studio/Ollama ignore the Authorization header


@dataclass
class ChatResult:
    text: str
    tokens: int
    cost_usd: float | None
    model: str


class ChatUnreachableError(Exception):
    """The configured provider could not be reached, or refused the request."""


def loaded_context_length(call_class: str) -> int | None:
    """The context length the provider actually loaded its model with, or None if it does
    not say. Never raises: a build must not fail over an unrelated HTTP hiccup.

    `graph.context_window` is asserted, not measured, so a model loaded smaller silently
    invalidates every prompt budget. Only LM Studio's REST API (`GET /api/v0/models`)
    reports it; nothing here reaches an LLM, and any other endpoint yields None."""
    cfg = require_provider(call_class)
    # base_url is the OpenAI surface (…/v1); the REST API is a sibling at the origin.
    origin = urlunparse(urlparse(cfg.base_url)._replace(path="", query="", fragment=""))
    try:
        response = httpx.get(f"{origin}/api/v0/models", timeout=2.0)
        response.raise_for_status()
        for entry in response.json()["data"]:
            if entry["id"] == cfg.model:
                length = entry.get("loaded_context_length")
                return int(length) if length is not None else None
    except Exception:
        return None
    return None


def complete(
    call_class: str,
    messages: list[dict],
    *,
    response_format: object | None = None,
) -> ChatResult:
    """One completion through `[providers.<call_class>]`. `response_format` requests
    structured output — the graph build's probe passes graphrag's own response model, so
    the probe can never test a shape the build does not send."""
    import litellm
    import openai

    litellm.telemetry = False  # privacy: no phone-home (grounding-and-privacy.md)
    # litellm print()s a "Give Feedback / Get Help" banner to *stdout* on any provider
    # exception. `groundly mcp` speaks the MCP protocol over stdout and calls this
    # in-process, so an unreachable provider would corrupt the JSON-RPC stream.
    litellm.suppress_debug_info = True

    cfg = require_provider(call_class)
    # The caller's own response_format, not a JSON-mode flag: endpoints accept different
    # shapes. Client-side schema validation (turned on globally by graphrag_llm's import) is
    # off, because this asks whether the provider accepts the request, not how well the
    # model fills the schema.
    extra = (
        {"response_format": response_format, "enable_json_schema_validation": False}
        if response_format is not None
        else {}
    )
    # Under extra_body, never flat: litellm's drop_params is False, so a flat kwarg raises
    # UnsupportedParamsError on every call.
    if cfg.reasoning_effort:
        extra["extra_body"] = {"reasoning_effort": cfg.reasoning_effort}
    # Flat, unlike reasoning_effort: temperature is a first-class OpenAI parameter every
    # compatible endpoint accepts, so litellm maps it rather than rejecting it.
    if cfg.temperature is not None:
        extra["temperature"] = cfg.temperature
    try:
        response = litellm.completion(
            model=f"openai/{cfg.model}",
            messages=messages,
            api_base=cfg.base_url,
            api_key=cfg.api_key or _LOCAL_PLACEHOLDER_KEY,
            **extra,
            # Local runtimes (LM Studio, Ollama) JIT-load the model on first request
            # and can take minutes to first token; a dead host should still fail fast —
            # 10s connect, configurable read (litellm passes httpx.Timeout through).
            timeout=httpx.Timeout(10.0, read=load_settings().llm.timeout_seconds),
        )
    except openai.APIStatusError as exc:
        # The server answered and refused (400, 401, 429). Checked before its base class
        # APIError, so a rejection is never reported as a network problem.
        raise ChatUnreachableError(
            f"[providers.{call_class}] at {cfg.base_url} rejected the request "
            f"(HTTP {getattr(exc, 'status_code', '?')}): {exc}"
        ) from exc
    except openai.APIError as exc:
        # Every remaining completion() failure (connection, timeout) subclasses APIError.
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
