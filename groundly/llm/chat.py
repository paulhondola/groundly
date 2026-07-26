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


def complete(call_class: str, messages: list[dict], *, json_object: bool = False) -> ChatResult:
    import litellm
    import openai

    litellm.telemetry = False  # privacy: no phone-home (grounding-and-privacy.md)
    # litellm print()s a "Give Feedback / Get Help" banner to *stdout* on any provider
    # exception. `groundly mcp` speaks the MCP protocol over stdout and calls this
    # in-process, so an unreachable provider would corrupt the JSON-RPC stream.
    litellm.suppress_debug_info = True

    cfg = require_provider(call_class)
    # json_object is a provider *capability*, not universally available on
    # OpenAI-compatible endpoints — graphrag's community-report stage requires it, so
    # the graph build probes for it up front rather than discovering it mid-run.
    extra = {"response_format": {"type": "json_object"}} if json_object else {}
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
