# Adopt litellm as the LLM client in `groundly/llm/chat.py`

## Context

`groundly/llm/chat.py` currently does a raw httpx POST to `{base_url}/chat/completions`
and computes cost manually from two user-supplied config fields
(`input_price_per_mtok`/`output_price_per_mtok`). That was the right lazy call when it
avoided a dependency — but `litellm==1.86.2` is now already installed (transitive via
`graphrag==3.1.0`, which uses it for all its own LLM I/O). Adopting it adds **zero new
installs**, consolidates Groundly and graphrag onto one client, and brings a bundled
price map (~2,700 models) so cost tracing works automatically for known cloud models
without the user configuring per-token prices.

Verified against the installed package (litellm 1.86.2):
- `litellm.completion(model="openai/<name>", messages=..., api_base=..., api_key=..., timeout=<float>)` targets any OpenAI-compatible endpoint; response is OpenAI-shaped (`.choices[0].message.content`, `.usage.prompt_tokens/completion_tokens/total_tokens`).
- `litellm.completion_cost(completion_response=...)` prices mapped models; **raises** (plain `Exception("This model isn't mapped yet…")`) for unmapped/local models.
- Exceptions subclass the `openai` equivalents (`litellm.exceptions.APIConnectionError`, `Timeout`, `APIError`, …).
- **Two privacy/perf hazards:** (1) importing litellm by default fetches the remote price map from GitHub (`__init__.py:489` + `model_cost_map_url`) — must set `LITELLM_LOCAL_MODEL_COST_MAP=True` before import to force the bundled local map (privacy rule: nothing leaves the machine except provider calls); (2) cold import ≈ 2.5 s — must stay lazy (inside `complete()`), never at module/MCP-spawn time.
- Empty `api_key` passes through litellm to the OpenAI client which errors — pass a placeholder for keyless local providers (same trick as `graphrag_adapter._LOCAL_PLACEHOLDER_KEY`).

The swap is surgical because the contract is already narrow: every production caller
(`agents/ask.py`, `agents/router.py`, `agents/study_modes.py`) and every `stub_chat`
test consumes only `ChatResult(text, tokens, cost_usd, model)` + `ChatUnreachableError`;
only `tests/llm/test_llm_chat.py` stubs at the httpx layer (`transport=` MockTransport).

## Changes

### 1. `groundly/llm/chat.py` — the swap
- Module top: `os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")` (with a comment naming the privacy rule it enforces). litellm import stays lazy inside `complete()`.
- `complete(call_class, messages)` body:
  - `cfg = require_provider(call_class)` (unchanged).
  - `litellm.completion(model=f"openai/{cfg.model}", messages=messages, api_base=cfg.base_url, api_key=cfg.api_key or "not-needed", timeout=load_settings().llm.timeout_seconds)`.
  - Also verify/disable any litellm telemetry flag in the installed version (`litellm.telemetry`) — privacy invariant.
  - Usage from `response.usage`; text from `response.choices[0].message.content`.
  - **Cost precedence:** manual `input_price_per_mtok`/`output_price_per_mtok` if configured (existing formula — keeps local/unmapped models priceable); else try `litellm.completion_cost(completion_response=response)`; on its unmapped-model exception → `cost_usd=None`.
  - litellm exceptions → `ChatUnreachableError` with the same message shape (`[providers.{call_class}] at {base_url} is unreachable: …`).
- Drop the `transport=` param from `complete()` and the `ChatFn` Protocol (litellm has no transport injection); `tests/conftest.py`'s `StubChat.__call__` signature updated to match.
- Keep `ChatResult`/`ChatUnreachableError` exactly as-is — zero changes to any caller in `agents/`, `mcp/`, `cli/`.

### 2. `tests/llm/test_llm_chat.py` — rewrite the seam
Replace httpx MockTransport with monkeypatching `litellm.completion` at chat.py's import site (or litellm's `mock_response=` kwarg where cleaner). Coverage: success + usage mapping; manual-price cost calc still wins when configured; auto-cost for a litellm-mapped model; unmapped model → `cost_usd=None` (no raise); connection error → `ChatUnreachableError`; keyless provider gets placeholder; timeout kwarg passed from `llm.timeout_seconds`.

### 3. `groundly/llm/graphrag_adapter.py` — `estimate_cost()` litellm fallback
If `input_price_per_mtok` is unset, fall back to litellm's local price map (`litellm.model_cost` lookup for `cfg.model`, lazy import, same env var) → `tokens * input_cost_per_token`; still `(tokens, None)` when the model isn't mapped either. Manual field remains the override. Tests for both fallback paths.

### 4. `pyproject.toml`
Add `litellm` as a direct pinned dependency at the already-resolved version (no new install; records the pin per the project's exact-pin convention for load-bearing deps).

### 5. Docs — decision change, same change set (project rule)
- `docs/groundly-spec.md` §7: new decision entry — litellm adopted as the LLM client inside `llm/` (already installed transitively via graphrag; zero new installs; automatic cost tracing for mapped models; `LITELLM_LOCAL_MODEL_COST_MAP=True` is load-bearing for privacy; manual price fields become an override for local/unmapped models). Reverses chat.py's "raw httpx, no SDK" rationale.
- `.claude/rules/architecture.md` + `docs/tech-stack/tech-stack.md`: amend the "exactly three frameworks" / "no provider SDK" wording — litellm is the provider *client* inside `llm/` (graphrag's own transitive dep), not a fourth orchestration framework; the OpenAI-compatible `base_url`+`model`+`key` config shape is unchanged.
- `chat.py` module docstring rewritten to match reality.
- `groundly/core/config.py` template comments + `llm.timeout_seconds` comment (no longer "httpx read timeout"; note the single flat timeout, losing the separate 10 s connect timeout).
- `docs/guides/lm-studio.md` + `docs/guides/graphrag-provider.md`: price fields now optional for litellm-mapped cloud models (auto-priced); still needed for local/unmapped models if you want cost figures.

## Non-changes (explicitly out of scope)
- graphrag's own internal LLM calls stay untraced (documented framework-boundary gap, unchanged).
- No streaming, no retries/rate-limiting via litellm — not requested, keep the call minimal.
- `record_trace`/traces schema unchanged — cost still flows only through `ChatResult.cost_usd`.

## Verification
- `uv run pytest tests/ -q` — all green (294 incl. the one pre-existing unrelated bundle-test flake).
- `uv run ruff check . && uv run ruff format --check .`
- Manual: `groundly ask apd "<question>"` against LM Studio still answers (keyless path); a cloud-provider config with no price fields shows a non-None `cost_usd` in the trace row for a mapped model.
- Confirm no network fetch at import: run with network observation or check litellm's local-map env var path is hit (`LITELLM_LOCAL_MODEL_COST_MAP` set before import).

## Critical files
- `groundly/llm/chat.py`
- `tests/llm/test_llm_chat.py`, `tests/conftest.py` (StubChat signature)
- `groundly/llm/graphrag_adapter.py` + its tests
- `pyproject.toml`
- `docs/groundly-spec.md`, `.claude/rules/architecture.md`, `docs/tech-stack/tech-stack.md`, `groundly/core/config.py`, `docs/guides/lm-studio.md`, `docs/guides/graphrag-provider.md`
