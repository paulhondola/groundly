"""Chat completion client: litellm.completion() against any OpenAI-compatible
endpoint (LM Studio, Ollama, cloud providers) — see .claude/rules/architecture.md:
LLM clients constructed only in llm/. Every call resolves its provider from
groundly.llm.config, so callers only ever name a call class.

litellm import stays lazy (inside complete()): cold import costs ~2.5s and must
never happen at MCP spawn time. The env vars litellm reads at *its* import —
LITELLM_LOCAL_MODEL_COST_MAP (unset, its __init__ fetches the price map from GitHub,
violating the privacy rule in .claude/rules/grounding-and-privacy.md) and LITELLM_LOG
— are set in groundly/__init__.py, not here: callers reach litellm transitively via
graphrag before this module's body ever runs, so setting them here was a no-op on
exactly those paths."""

from dataclasses import dataclass
from typing import Protocol
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


class ChatFn(Protocol):
    def __call__(self, call_class: str, messages: list[dict]) -> ChatResult: ...


class ChatUnreachableError(Exception):
    """The configured chat provider could not be reached (network/HTTP error)."""


def loaded_context_length(call_class: str) -> int | None:
    """The context length the provider *actually* loaded its model with, or None if it
    does not say. Best-effort and never raises.

    `graph.context_window` is a number Groundly asserts, not one it measures: every
    prompt budget is carved from it and nothing checks it against the endpoint, so a
    model reloaded at a smaller window silently invalidates the whole sizing. Observed
    2026-08-01 — config said 12288, LM Studio was serving 8192 after a reload.

    LM Studio is the only endpoint asked, via its own REST API (`GET /api/v0/models`,
    `loaded_context_length`); the OpenAI-compatible surface has no equivalent field and
    Ollama's context is server-side only. That makes this an unusually literal reading of
    "never hardcode a provider" (.claude/rules/architecture.md): the rule protects the
    *call* path, where a hardcoded provider would make some endpoints unusable. Nothing
    here reaches an LLM or gates a build — a server that 404s, times out, or answers a
    shape this does not recognise returns None and the build proceeds exactly as before.

    Deliberately not raising on *anything*: this is a diagnostic, and the one failure mode
    worse than missing the drift is refusing a build over an unrelated HTTP hiccup."""
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
    model: str | None = None,
) -> ChatResult:
    """`model` overrides the call class's configured model for this call only, against the
    same `base_url` and key. It exists for one measurement: the grounding-fidelity
    experiment compares an enforced `ask` against a host running a different model, and
    without a way to re-run the enforced path on a *comparable* model the result cannot be
    told apart from "the host's model is stronger".

    It does not weaken the provider boundary — the client is still constructed here, from
    the same configured section, and the model that actually ran is recorded in the trace
    rather than assumed."""
    import litellm
    import openai

    litellm.telemetry = False  # privacy: no phone-home (grounding-and-privacy.md)
    # litellm print()s a "Give Feedback / Get Help" banner to *stdout* on any provider
    # exception. `groundly mcp` speaks the MCP protocol over stdout and calls this
    # in-process, so an unreachable provider would corrupt the JSON-RPC stream.
    litellm.suppress_debug_info = True

    cfg = require_provider(call_class)
    # Structured output is a provider *capability*, not universally available on
    # OpenAI-compatible endpoints — and the accepted shape differs per endpoint, in both
    # directions: DeepSeek takes `{"type": "json_object"}` and refuses `json_schema`,
    # LM Studio refuses `json_object` and demands `json_schema`. So this takes the
    # response_format the caller actually needs rather than a bool naming one shape: the
    # graph build's probe hands over graphrag's own response model, which litellm converts
    # into the same wire request the build sends, so the probe can never test a shape the
    # build never sends (ingestion/graph.py's _probe_extraction).
    #
    # enable_json_schema_validation is graphrag_llm's global — lite_llm_completion.py sets
    # it True at import, and graphrag is imported well before the probe runs — and it makes
    # litellm validate the *response* against the schema client-side and raise. Off here:
    # this call asks whether the provider accepts the request, and how well a model fills
    # the schema is the build's problem, not a reason to refuse to start it.
    extra = (
        {"response_format": response_format, "enable_json_schema_validation": False}
        if response_format is not None
        else {}
    )
    # Nested under extra_body, never passed flat: litellm's drop_params is False, so a
    # flat reasoning_effort kwarg raises UnsupportedParamsError on every call instead of
    # degrading (measured — see llm/graphrag_adapter.completion_model_config, which nests
    # the same way so the setting means the same thing on every call class).
    if cfg.reasoning_effort:
        extra["extra_body"] = {"reasoning_effort": cfg.reasoning_effort}
    # Flat, unlike reasoning_effort: temperature is a first-class OpenAI parameter every
    # compatible endpoint accepts, so litellm maps it rather than rejecting it.
    if cfg.temperature is not None:
        extra["temperature"] = cfg.temperature
    try:
        response = litellm.completion(
            model=f"openai/{model or cfg.model}",
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
        # The server answered and refused (400 context overflow, 401 bad key, 429).
        # Checked before APIError below, which it subclasses: calling a rejected
        # request "unreachable" sends people to debug their network instead of the
        # actual cause (conventions.md — name the cause specifically).
        raise ChatUnreachableError(
            f"[providers.{call_class}] at {cfg.base_url} rejected the request "
            f"(HTTP {getattr(exc, 'status_code', '?')}): {exc}"
        ) from exc
    except openai.APIError as exc:
        # Every remaining litellm exception raised by completion() (connection
        # failures, timeouts) subclasses openai.APIError — the tightest common base
        # covering the rest of this call's failure surface.
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
