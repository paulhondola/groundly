"""groundly/llm/graphrag_adapter.py: the one place translating Groundly's provider
config into graphrag's config primitives (.claude/rules/architecture.md)."""

import pytest
from graphrag_llm.config import ModelConfig
from graphrag_llm.embedding.embedding_factory import embedding_factory

from groundly.core.config import ProviderConfig, ProviderNotConfiguredError
from groundly.llm.graphrag_adapter import (
    BGE_M3_EMBEDDING_TYPE,
    Bgem3GraphEmbedding,
    completion_model_config,
    concurrent_requests,
    register_bge_m3_embedding,
)


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


class StubEmbedder:
    """Records the texts it was asked to encode; returns deterministic dense vectors
    and (unused) sparse weights, matching Embedder.encode's shape."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def encode(self, texts):
        self.calls.append(texts)
        dense = [[float(len(t)), 0.5] for t in texts]
        sparse = [{1: 0.9} for _ in texts]
        return dense, sparse


# --- completion_model_config --------------------------------------------------------


def test_completion_model_config_maps_extraction_provider(home):
    (home / "config.toml").write_text(
        "[providers.extraction]\n"
        'base_url = "http://localhost:1234/v1"\n'
        'model = "gpt-4o-mini"\n'
        'api_key = "sk-secret"\n'
    )
    cfg = completion_model_config()
    assert isinstance(cfg, ModelConfig)
    assert cfg.model_provider == "openai"
    assert cfg.model == "gpt-4o-mini"
    assert cfg.api_base == "http://localhost:1234/v1"
    assert cfg.api_key == "sk-secret"


def test_completion_model_config_fails_fast_when_unconfigured(home):
    with pytest.raises(ProviderNotConfiguredError):
        completion_model_config()


def test_completion_model_config_local_provider_gets_placeholder_key(home):
    # LM Studio/Ollama: no api_key configured, but graphrag's ModelConfig rejects an
    # empty one outright — a local provider still needs *some* truthy placeholder.
    (home / "config.toml").write_text(
        "[providers.extraction]\n"
        'base_url = "http://localhost:1234/v1"\n'
        'model = "gemma-4-12b-qat"\n'
        'api_key = ""\n'
    )
    cfg = completion_model_config()  # must not raise ModelConfig's own validation error
    assert cfg.api_key


def test_completion_model_config_nests_reasoning_effort_under_extra_body(home):
    """litellm's drop_params is False, so a flat call_args={"reasoning_effort": ...}
    makes litellm raise UnsupportedParamsError on *every* call instead of degrading —
    measured: nesting the same value under extra_body is what actually reaches the
    provider (93 -> 2 completion tokens on a local reasoning model). Assert the nesting
    specifically, not just that the value ends up somewhere in call_args."""
    (home / "config.toml").write_text(
        "[providers.extraction]\n"
        'base_url = "http://localhost:1234/v1"\n'
        'model = "gpt-4o-mini"\n'
        'api_key = "sk-secret"\n'
        'reasoning_effort = "none"\n'
    )
    cfg = completion_model_config()
    assert cfg.call_args == {"extra_body": {"reasoning_effort": "none"}}


def test_completion_model_config_omits_call_args_when_reasoning_effort_unset(home):
    """The default path: no reasoning_effort configured must reproduce today's
    ModelConfig exactly — call_args left at its own `{}` default, not an explicitly-set
    empty dict standing in for one."""
    (home / "config.toml").write_text(
        "[providers.extraction]\n"
        'base_url = "http://localhost:1234/v1"\n'
        'model = "gpt-4o-mini"\n'
        'api_key = "sk-secret"\n'
    )
    assert completion_model_config().call_args == {}


# --- Bgem3GraphEmbedding -------------------------------------------------------------


def test_embedding_delegates_to_embedder_and_returns_dense_only():
    stub = StubEmbedder()
    adapter = Bgem3GraphEmbedding(model_id="bge_m3/bge-m3", embedder=stub)

    response = adapter.embedding(input=["hello", "world!"])

    assert stub.calls == [["hello", "world!"]]
    assert response.embeddings == [[5.0, 0.5], [6.0, 0.5]]
    assert response.model == "bge_m3/bge-m3"


async def test_embedding_async_delegates_the_same_way():
    stub = StubEmbedder()
    adapter = Bgem3GraphEmbedding(embedder=stub)

    response = await adapter.embedding_async(input=["hi"])

    assert response.embeddings == [[2.0, 0.5]]


def test_embedder_property_lazily_constructs_bge_m3_embedder(monkeypatch):
    import groundly.llm.graphrag_adapter as adapter_module

    class FakeBgeM3Embedder:
        def __init__(self):
            self.constructed = True

    monkeypatch.setattr("groundly.llm.embeddings.BgeM3Embedder", FakeBgeM3Embedder, raising=True)
    adapter = adapter_module.Bgem3GraphEmbedding()
    assert adapter._embedder is None  # not constructed yet
    embedder = adapter.embedder
    assert isinstance(embedder, FakeBgeM3Embedder)
    assert adapter.embedder is embedder  # constructed once, cached


def test_metrics_store_and_tokenizer_are_passthrough():
    adapter = Bgem3GraphEmbedding(tokenizer="tok", metrics_store="metrics")
    assert adapter.tokenizer == "tok"
    assert adapter.metrics_store == "metrics"


# --- register_bge_m3_embedding -------------------------------------------------------


def test_register_bge_m3_embedding_is_idempotent():
    register_bge_m3_embedding()
    register_bge_m3_embedding()  # must not raise
    assert BGE_M3_EMBEDDING_TYPE in embedding_factory


# --- prompt budgets ---------------------------------------------------------------------


@pytest.mark.parametrize("window", [4096, 8192, 16384, 32768, 131072])
def test_prompt_budgets_always_fit_the_window(window):
    """Every stage's input + output reserve has to fit the model's actual context —
    graphrag's own defaults want ~10k for community reports alone, which is what
    made every extraction call 400 on a 4k local model."""
    from groundly.llm.graphrag_adapter import prompt_budgets

    b = prompt_budgets(window)
    assert b.summarize_max_input_tokens + b.summarize_max_length <= window
    assert b.community_max_input_length + b.community_max_length <= window


@pytest.mark.parametrize("window", [4096, 8192, 16384, 131072])
def test_prompt_budgets_never_exceed_graphrag_defaults(window):
    """This only ever scales down: a big context reproduces stock graphrag."""
    from groundly.llm.graphrag_adapter import prompt_budgets

    b = prompt_budgets(window)
    assert b.summarize_max_input_tokens <= 4000
    assert b.summarize_max_length <= 500
    assert b.community_max_input_length <= 8000
    assert b.community_max_length <= 2000


def test_gleanings_default_to_off_at_every_window():
    """The window must not *enable* gleaning on its own. It used to: `1 if
    context_window >= 16384 else 0` meant raising the window for capacity reasons
    silently doubled extraction calls and changed the shape of the graph, with nothing
    recording that the two builds differed in anything but the model."""
    from groundly.llm.graphrag_adapter import prompt_budgets

    for window in (4096, 8192, 12288, 16384, 131072):
        assert prompt_budgets(window).max_gleanings == 0


def test_the_window_clamps_gleanings_but_never_grants_them():
    """A gleaning round replays prompt + chunk + the model's whole first answer, so it
    roughly doubles peak context and cannot fit below 16384 beside the stage budgets.
    The window is therefore a ceiling on the student's choice, never the choice itself."""
    from groundly.llm.graphrag_adapter import prompt_budgets

    assert prompt_budgets(8192, 2).max_gleanings == 0  # asked for 2, window forbids it
    assert prompt_budgets(12288, 1).max_gleanings == 0
    assert prompt_budgets(16384, 0).max_gleanings == 0  # window allows, student declined
    assert prompt_budgets(16384, 1).max_gleanings == 1
    assert prompt_budgets(131072, 2).max_gleanings == 2


# --- provider response compatibility ----------------------------------------------------


def test_allow_nonstandard_service_tier_accepts_groqs_value():
    """graphrag_llm types service_tier with OpenAI's exact literal set and builds its
    response as LLMCompletionResponse(**response.model_dump()). Groq returns
    'on_demand', so pydantic rejected *every* response — HTTP 200, tokens spent,
    result discarded. Observed as 258 requests / 258 failures / no entities."""
    from graphrag_llm.types.types import LLMCompletionResponse

    from groundly.llm.graphrag_adapter import allow_nonstandard_service_tier

    allow_nonstandard_service_tier()
    payload = dict(id="x", created=1, model="m", object="chat.completion", choices=[])

    assert LLMCompletionResponse(**payload, service_tier="on_demand").service_tier == "on_demand"
    # must not break the providers that do follow OpenAI's enum
    assert LLMCompletionResponse(**payload, service_tier="default").service_tier == "default"
    assert LLMCompletionResponse(**payload, service_tier=None).service_tier is None


def test_allow_nonstandard_service_tier_is_idempotent():
    """Called on every build and every graph query, so it must be cheap to repeat."""
    from graphrag_llm.types.types import LLMCompletionResponse

    from groundly.llm.graphrag_adapter import allow_nonstandard_service_tier

    allow_nonstandard_service_tier()
    allow_nonstandard_service_tier()
    payload = dict(id="x", created=1, model="m", object="chat.completion", choices=[])
    assert LLMCompletionResponse(**payload, service_tier="on_demand").service_tier == "on_demand"


# --- retry / rate limiting ---------------------------------------------------------------


def _write_extraction(home, extra: str = "") -> None:
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "https://api.groq.com/openai/v1"\n'
        'model = "llama-3.3-70b-versatile"\napi_key = "k"\n' + extra
    )


def test_completion_model_config_always_retries_with_jittered_backoff(home):
    """graphrag swallows a 429 per text unit like any other failure, so without a retry
    a rate-limited provider silently drops chunks (283 of 304 against Groq's free tier).
    Jitter matters as much as backoff: without it every concurrent worker retries in
    lockstep and rebuilds the burst that caused the 429."""
    _write_extraction(home)
    retry = completion_model_config().retry

    assert retry is not None
    assert retry.max_retries == 5
    assert retry.jitter is True
    assert retry.base_delay > 1.0  # graphrag rejects <= 1.0 for exponential backoff


def test_completion_model_config_leaves_rate_limit_unset_by_default(home):
    """Unset means unthrottled — correct for a local runtime, which has no limits, and
    preserves the previous behavior for anyone who hasn't declared their tier."""
    _write_extraction(home)
    assert completion_model_config().rate_limit is None


def test_completion_model_config_builds_a_per_minute_rate_limit(home):
    _write_extraction(home, "requests_per_minute = 30\ntokens_per_minute = 6000\n")
    rl = completion_model_config().rate_limit

    assert rl is not None
    assert rl.period_in_seconds == 60
    assert rl.requests_per_period == 30
    assert rl.tokens_per_period == 6000


def test_retry_config_retries_a_capacity_400(home):
    """A local runtime reports capacity exhaustion as a 400 ("Context size has been
    exceeded" when concurrent slots overrun the shared KV cache), and litellm maps it to
    the same BadRequestError as a malformed request — which graphrag never retries.
    Measured 2026-08-01: 8 report calls failed, `"retries": 0`, and the wave that ran
    with fewer in flight succeeded unaided."""
    _write_extraction(home)
    retry = completion_model_config().retry

    assert retry is not None
    skip = retry.model_dump()["exceptions_to_skip"]
    assert "BadRequestError" not in skip
    # only that one is lifted — an auth failure must still fail immediately
    assert "AuthenticationError" in skip
    assert "ContentPolicyViolationError" in skip


def test_retry_config_reaches_the_retrier_that_graphrag_actually_builds(home):
    """model_dump() -> ExponentialRetry(**init_args) only works because RetryConfig is
    `extra="allow"`; asserting on the config alone would pass even if the extra field
    were silently dropped on the way to the object that consults it."""
    from graphrag_llm.retry.retry_factory import create_retry

    _write_extraction(home)
    retrier = create_retry(completion_model_config().retry)

    assert "BadRequestError" not in retrier._exceptions_to_skip
    assert "AuthenticationError" in retrier._exceptions_to_skip


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:1234/v1",
        "http://127.0.0.1:1234/v1",
        "http://[::1]:1234/v1",
        "http://0.0.0.0:11434/v1",
        "http://box.localhost:1234/v1",
    ],
)
def test_concurrent_requests_serializes_a_local_runtime(base_url):
    """graphrag's default of 25 assumes each call owns the context window. A llama.cpp
    -family server serves several from ONE shared KV cache — measured 2026-08-01, 4 slots
    x ~2,300-token report prompts against an 8192 cache, which llama.cpp answers with
    `decode: Context size has been exceeded`."""
    assert concurrent_requests(ProviderConfig(base_url=base_url, model="m")) == 1


@pytest.mark.parametrize(
    "base_url",
    ["https://api.openai.com/v1", "http://192.168.1.50:1234/v1", "https://api.groq.com/openai/v1"],
)
def test_concurrent_requests_leaves_a_remote_provider_at_graphrags_default(base_url):
    """The cloud path must not slow down 25x. The LAN case is a known gap — a local
    runtime reached over the network has the same shared cache and still needs its slots
    reduced by hand (docs/guides/graphrag-provider.md says so)."""
    assert concurrent_requests(ProviderConfig(base_url=base_url, model="m")) == 25


def test_concurrent_requests_is_pessimistic_across_providers():
    """graphrag has ONE global concurrency setting covering every stage, so a local
    extraction provider binds the whole build even when graph.report_call_class points
    community reports at a cloud model."""
    local = ProviderConfig(base_url="http://localhost:1234/v1", model="m")
    remote = ProviderConfig(base_url="https://api.openai.com/v1", model="m")

    assert concurrent_requests(remote, local) == 1
    assert concurrent_requests(local, remote) == 1
    assert concurrent_requests(remote, remote) == 25


def test_rate_limit_honours_either_limit_alone(home):
    """Providers publish RPM and TPM independently; declaring one must not require
    inventing the other."""
    _write_extraction(home, "tokens_per_minute = 6000\n")
    rl = completion_model_config().rate_limit
    assert rl is not None and rl.tokens_per_period == 6000 and rl.requests_per_period is None

    _write_extraction(home, "requests_per_minute = 30\n")
    rl = completion_model_config().rate_limit
    assert rl is not None and rl.requests_per_period == 30 and rl.tokens_per_period is None


def test_allow_nonstandard_service_tier_rebuilds_the_model_only_once(monkeypatch):
    """Called on every build *and every graph query*. `str | None` builds a fresh
    types.UnionType each evaluation, so a guard of `field.annotation is not (str | None)`
    is always true and re-ran model_rebuild(force=True) on a shared third-party class
    from concurrent query handlers — pydantic promises nothing about that."""
    from graphrag_llm.types.types import LLMCompletionResponse

    from groundly.llm import graphrag_adapter

    monkeypatch.setattr(graphrag_adapter, "_service_tier_widened", False)
    rebuilds = []
    real = LLMCompletionResponse.model_rebuild
    monkeypatch.setattr(
        LLMCompletionResponse,
        "model_rebuild",
        classmethod(lambda cls, **kw: rebuilds.append(kw) or real(**kw)),
    )

    for _ in range(5):
        graphrag_adapter.allow_nonstandard_service_tier()

    assert len(rebuilds) == 1


# --- the bundled extraction prompt (decision 22) ----------------------------------------


def test_bundled_prompt_keeps_the_placeholders_graphrag_substitutes():
    """graph_extractor._process_document formats with exactly these two keys. A prompt
    missing one sends every chunk the same literal text."""
    from groundly.llm.graphrag_adapter import _bundled_prompt_text

    text = _bundled_prompt_text()
    assert "{entity_types}" in text
    assert "{input_text}" in text


def test_bundled_prompt_has_no_delimiter_placeholders():
    """graphrag 3.1.0 writes the delimiters into the prompt literally and parses with
    hardcoded constants — it never substitutes these. One in the prompt raises KeyError
    inside graph_extractor's per-chunk except, i.e. every chunk fails silently."""
    from groundly.llm.graphrag_adapter import _bundled_prompt_text

    text = _bundled_prompt_text()
    for placeholder in ("{tuple_delimiter}", "{record_delimiter}", "{completion_delimiter}"):
        assert placeholder not in text
    # ...and the literal delimiters the parser does split on are present
    assert "<|>" in text and "##" in text and "<|COMPLETE|>" in text


def test_bundled_prompt_stays_within_its_token_budget():
    """The whole point of decision 22: this preamble is sent once per chunk, so its size
    *is* the build's bill. 700 tokens keeps a 1194-chunk apd build near 1.0M (from
    2.12M). Growing the worked example past this silently re-inflates every build."""
    from groundly.llm.graphrag_adapter import _bundled_prompt_text

    assert len(_bundled_prompt_text()) // 4 <= 700


def test_bundled_prompt_reuses_graphrags_instruction_block_verbatim():
    """Only the worked examples are ours. The instruction block defines the record
    format the downstream parser depends on, so it is copied verbatim — this fails
    if a graphrag upgrade changes it and the bundled prompt is not re-derived.

    Compared modulo *trailing* whitespace: graphrag's block has five lines that are a lone
    space and no final newline, and this repo strips both (73023cd did exactly that in
    passing, while renaming something else — `git grep -l ' $'` finds no other tracked
    file). Trailing whitespace cannot reach the delimited record format, so tolerating it
    loses no detection and stops the guard firing on ordinary repo hygiene."""
    from graphrag.prompts.index.extract_graph import GRAPH_EXTRACTION_PROMPT

    from groundly.llm.graphrag_adapter import _bundled_prompt_text

    def norm(s: str) -> str:
        return "\n".join(line.rstrip() for line in s.split("\n")).rstrip("\n")

    instructions, _, rest = GRAPH_EXTRACTION_PROMPT.partition("######################\n-Examples-")
    _, _, real_data = rest.partition("######################\n-Real Data-")

    text = norm(_bundled_prompt_text())
    assert text.startswith(norm(instructions))
    assert text.endswith(norm(real_data))


def test_default_entity_types_target_course_material():
    """graphrag's defaults are organization/person/geo/event, which produced 75
    ORGANIZATION and 34 EVENT entities on a parallel-algorithms corpus."""
    from groundly.llm.graphrag_adapter import extraction_entity_types

    types = extraction_entity_types()
    assert "concept" in types and "algorithm" in types
    assert "person" in types  # courses cite Dijkstra and Lamport
    for news_type in ("organization", "geo", "event"):
        assert news_type not in types


def test_entity_types_are_split_and_stripped(home):
    from groundly.llm.graphrag_adapter import extraction_entity_types

    (home / "config.toml").write_text('[graph]\nentity_types = "concept, algorithm ,, theorem"\n')
    assert extraction_entity_types() == ["concept", "algorithm", "theorem"]


def test_custom_prompt_missing_file_names_the_cause(home, tmp_path):
    from groundly.llm.graphrag_adapter import ExtractionPromptError, resolve_extraction_prompt

    missing = tmp_path / "nope.txt"
    (home / "config.toml").write_text(f'[graph]\nextraction_prompt = "{missing}"\n')
    with pytest.raises(ExtractionPromptError) as exc:
        with resolve_extraction_prompt():
            pass
    assert str(missing) in str(exc.value)


def test_custom_prompt_missing_a_placeholder_names_it(home, tmp_path):
    from groundly.llm.graphrag_adapter import ExtractionPromptError, resolve_extraction_prompt

    custom = tmp_path / "custom.txt"
    custom.write_text("Types: [{entity_types}]\nOutput:")  # no {input_text}
    (home / "config.toml").write_text(f'[graph]\nextraction_prompt = "{custom}"\n')
    with pytest.raises(ExtractionPromptError, match=r"\{input_text\}"):
        with resolve_extraction_prompt():
            pass


def test_custom_prompt_with_a_delimiter_placeholder_is_refused(home, tmp_path):
    """The silent-failure case: graphrag does not substitute these, so .format() raises
    KeyError per chunk and swallows it. Refuse up front instead."""
    from groundly.llm.graphrag_adapter import ExtractionPromptError, resolve_extraction_prompt

    custom = tmp_path / "custom.txt"
    custom.write_text("Types [{entity_types}] Text {input_text} Sep {tuple_delimiter} Output:")
    (home / "config.toml").write_text(f'[graph]\nextraction_prompt = "{custom}"\n')
    with pytest.raises(ExtractionPromptError, match=r"tuple_delimiter"):
        with resolve_extraction_prompt():
            pass


def test_resolved_prompt_is_a_real_readable_file(home):
    """ExtractGraphConfig.prompt is a path graphrag re-reads at extraction time, so the
    resolved file has to exist on disk for the duration of the build."""
    from groundly.llm.graphrag_adapter import _bundled_prompt_text, resolve_extraction_prompt

    with resolve_extraction_prompt() as (path, text):
        assert path.is_file()
        assert path.read_text(encoding="utf-8") == text == _bundled_prompt_text()


def test_fingerprint_changes_with_prompt_and_with_types():
    from groundly.llm.graphrag_adapter import extraction_fingerprint

    base = extraction_fingerprint("prompt", ["concept", "algorithm"])
    assert base == extraction_fingerprint("prompt", ["concept", "algorithm"])
    assert base != extraction_fingerprint("other prompt", ["concept", "algorithm"])
    assert base != extraction_fingerprint("prompt", ["concept"])
    # order counts: graphrag interpolates the list as given, so a reorder is a change
    assert base != extraction_fingerprint("prompt", ["algorithm", "concept"])


def test_fingerprint_changes_with_gleanings():
    """A gleaning round is a second extraction call per chunk, so two builds differing
    only in it are genuinely different builds — apd produced 2,685 entities at 0 and
    6,184 at 1. Folding it into the fingerprint is what makes `graph_is_stale` say so
    and offer a rebuild, without adding a manifest field."""
    from groundly.llm.graphrag_adapter import extraction_fingerprint

    types = ["concept", "algorithm"]
    assert extraction_fingerprint("p", types, 0) != extraction_fingerprint("p", types, 1)
    assert extraction_fingerprint("p", types, 1) != extraction_fingerprint("p", types, 2)
    assert extraction_fingerprint("p", types, 0) == extraction_fingerprint("p", types)
