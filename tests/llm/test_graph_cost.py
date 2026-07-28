import pytest

from groundly.llm.graph_cost import estimate_cost


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


# --- estimate_cost -------------------------------------------------------------------


def _priced(**extra: str) -> str:
    """An extraction provider with both manual prices set. Both are required for the
    override (matching llm/chat.py and agents/decks.py), so tests that want a priced
    estimate have to say so in full."""
    body = (
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "m"\n'
        "input_price_per_mtok = 5.0\noutput_price_per_mtok = 10.0\n"
    )
    return body + "".join(f"{k} = {v}\n" for k, v in extra.items())


def test_estimate_cost_unconfigured_provider_returns_none_cost(home):
    est = estimate_cost(4000, 0)
    assert est.input_tokens == 1000
    assert est.low_usd is None and est.high_usd is None
    assert est.price_source is None


def test_estimate_cost_unpriced_provider_returns_none_cost(home):
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "m"\n'
    )
    est = estimate_cost(4000, 0)
    assert est.input_tokens == 1000
    assert est.low_usd is None


def test_estimate_cost_priced_provider_computes_cost(home):
    (home / "config.toml").write_text(_priced())
    est = estimate_cost(4_000_000, 0)  # 1,000,000 input tokens, no chunks
    assert est.input_tokens == 1_000_000
    assert est.low_usd == pytest.approx(5.0)
    assert est.price_source == "config.toml"


def test_estimate_cost_half_set_manual_prices_fall_through_to_litellm(monkeypatch, home):
    """A half-set override would produce a range whose upper bound silently omits
    output — the exact defect the range exists to fix. Both fields or neither."""
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "gpt-4o-mini"\n'
        "input_price_per_mtok = 5.0\n"
    )
    import litellm

    monkeypatch.setattr(
        litellm,
        "model_cost",
        {"gpt-4o-mini": {"input_cost_per_token": 1.5e-07, "output_cost_per_token": 6e-07}},
    )
    est = estimate_cost(4_000_000, 0)
    assert est.low_usd == pytest.approx(0.15)  # litellm's, not the manual 5.0
    assert est.price_source.startswith("litellm ")


def test_estimate_cost_prices_output_tokens_too(home):
    """The whole point: the previous estimate priced input only, understating a build
    several-fold on its own (measured completion:prompt ran from 0.87:1 to 4.06:1)."""
    (home / "config.toml").write_text(_priced())
    est = estimate_cost(4_000_000, 10)
    assert est.max_output_tokens > 0
    assert est.high_usd == pytest.approx(est.low_usd + est.max_output_tokens * 10.0 / 1_000_000)
    assert est.high_usd > est.low_usd


def test_estimate_cost_output_ceiling_scales_with_the_context_window(home):
    """Derived from the room a call has left to answer in, not fitted to one provider's
    measured output — so it tracks graph.context_window instead of going stale."""
    from groundly.llm.graph_cost import _preamble_tokens

    (home / "config.toml").write_text(_priced() + "\n[graph]\ncontext_window = 32768\n")
    wide = estimate_cost(0, 1)
    assert wide.max_output_tokens == 32768 - _preamble_tokens() - 512

    (home / "config.toml").write_text(_priced() + "\n[graph]\ncontext_window = 4096\n")
    assert estimate_cost(0, 1).max_output_tokens < wide.max_output_tokens


def test_estimate_cost_output_ceiling_never_goes_negative(home, tmp_path):
    """Reachable via graph.extraction_prompt: a custom preamble larger than the window
    leaves no room to answer in. Zero, not a negative estimate that would price the
    high end *below* the low one. (build_graph refuses this config separately.)"""
    custom = tmp_path / "huge.txt"
    custom.write_text("Types [{entity_types}] Text {input_text}" + "x" * 40_000)
    (home / "config.toml").write_text(
        _priced() + f'\n[graph]\ncontext_window = 2048\nextraction_prompt = "{custom}"\n'
    )
    est = estimate_cost(0, 100)
    assert est.max_output_tokens == 0
    assert est.high_usd == est.low_usd


def test_estimate_cost_flags_a_moving_alias(home):
    """litellm 1.86.2 prices mistral/mistral-small-latest at $0.06/$0.18 per Mtok; the
    alias resolves today to Mistral Small 4 at $0.15/$0.60. Drift is certain here, not
    merely possible, so the CLI gets something specific to warn about."""
    (home / "config.toml").write_text(
        _priced().replace('model = "m"', 'model = "mistral-small-latest"')
    )
    assert estimate_cost(4000, 0).moving_alias == "mistral-small-latest"


def test_estimate_cost_pinned_model_is_not_flagged(home):
    (home / "config.toml").write_text(
        _priced().replace('model = "m"', 'model = "mistral-small-2603"')
    )
    assert estimate_cost(4000, 0).moving_alias is None


def test_estimate_cost_falls_back_to_litellm_map_for_mapped_model(monkeypatch, home):
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "gpt-4o-mini"\n'
    )
    import litellm

    monkeypatch.setattr(
        litellm,
        "model_cost",
        {"gpt-4o-mini": {"input_cost_per_token": 1.5e-07, "output_cost_per_token": 6e-07}},
    )
    est = estimate_cost(4_000_000, 0)  # 1,000,000 tokens
    assert est.input_tokens == 1_000_000
    assert est.low_usd == pytest.approx(0.15)
    assert "gpt-4o-mini" in est.price_source


def test_estimate_cost_half_priced_litellm_entry_is_refused(monkeypatch, home):
    """An entry with no output price would produce an upper bound identical to the
    lower one — a range that silently claims output is free."""
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "gpt-4o-mini"\n'
    )
    import litellm

    monkeypatch.setattr(litellm, "model_cost", {"gpt-4o-mini": {"input_cost_per_token": 1.5e-07}})
    assert estimate_cost(4_000_000, 0).low_usd is None


def test_estimate_cost_unmapped_model_in_litellm_map_returns_none(monkeypatch, home):
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "totally-unmapped-local-model"\n'
    )
    import litellm

    monkeypatch.setattr(
        litellm,
        "model_cost",
        {"gpt-4o-mini": {"input_cost_per_token": 1.5e-07, "output_cost_per_token": 6e-07}},
    )
    est = estimate_cost(4_000_000, 0)
    assert est.input_tokens == 1_000_000
    assert est.low_usd is None


def test_estimate_cost_manual_price_overrides_litellm_map(home):
    # No litellm stubbing needed: a configured manual price must short-circuit
    # before litellm's map is ever consulted.
    (home / "config.toml").write_text(_priced().replace('model = "m"', 'model = "gpt-4o-mini"'))
    est = estimate_cost(4_000_000, 0)
    assert est.input_tokens == 1_000_000
    assert est.low_usd == pytest.approx(5.0)
    assert est.price_source == "config.toml"


def test_estimate_cost_counts_the_per_chunk_extraction_preamble(home):
    """The whole few-shot preamble is sent with every chunk, which at Groundly's chunk
    size dominates the input — counting chunk text alone understated a real 1194-chunk
    build by 11.4x."""
    from groundly.llm.graph_cost import _preamble_tokens

    (home / "config.toml").write_text(_priced())
    chunk_tokens = 512
    est = estimate_cost(chunk_tokens * 4, 1)  # one chunk of 512 tokens

    assert est.input_tokens == chunk_tokens + _preamble_tokens()


def test_estimate_cost_prices_the_bundled_prompt_not_graphrags(home):
    """The saving only reaches the student if the confirmation gate quotes it. Pricing
    graphrag's 1620-token preamble here would over-quote every build by ~2x."""
    from graphrag.prompts.index.extract_graph import GRAPH_EXTRACTION_PROMPT

    from groundly.llm.graph_cost import _preamble_tokens
    from groundly.llm.graphrag_adapter import _bundled_prompt_text

    assert _preamble_tokens() == len(_bundled_prompt_text()) // 4
    assert _preamble_tokens() < len(GRAPH_EXTRACTION_PROMPT) // 4 / 2


def test_estimate_cost_measures_a_custom_prompt(home, tmp_path):
    custom = tmp_path / "custom.txt"
    custom.write_text("Types [{entity_types}] Text {input_text} Output:" + "x" * 4000)
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "m"\n'
        f'\n[graph]\nextraction_prompt = "{custom}"\n'
    )
    from groundly.llm.graph_cost import _preamble_tokens

    assert _preamble_tokens() == len(custom.read_text()) // 4


def test_estimate_cost_falls_back_when_a_custom_prompt_is_unreadable(home, tmp_path):
    """estimate_cost feeds the cost line, not the build. It must degrade to a number
    rather than raise — build_graph still refuses, with a named cause, before any call."""
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "m"\n'
        f'\n[graph]\nextraction_prompt = "{tmp_path / "nope.txt"}"\n'
    )
    from groundly.llm.graph_cost import _preamble_tokens
    from groundly.llm.graphrag_adapter import _bundled_prompt_text

    assert _preamble_tokens() == len(_bundled_prompt_text()) // 4
    assert estimate_cost(2048, 1).input_tokens > 0


def test_estimate_cost_prices_a_provider_prefixed_model_by_suffix(monkeypatch, home):
    """litellm keys OpenAI bare but everything else provider-prefixed. Groundly only
    knows the bare name (the provider is a base_url), so a plain .get() missed all
    2199 prefixed entries — including every Groq model."""
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "https://api.groq.com/openai/v1"\n'
        'model = "llama-3.3-70b-versatile"\n'
    )
    import litellm

    monkeypatch.setattr(
        litellm,
        "model_cost",
        {
            "gpt-4o-mini": {"input_cost_per_token": 1.5e-07, "output_cost_per_token": 6e-07},
            "groq/llama-3.3-70b-versatile": {
                "input_cost_per_token": 5.9e-07,
                "output_cost_per_token": 7.9e-07,
            },
        },
    )
    est = estimate_cost(4_000_000, 0)
    assert est.input_tokens == 1_000_000
    assert est.low_usd == pytest.approx(0.59)
    assert "groq/llama-3.3-70b-versatile" in est.price_source


def test_estimate_cost_refuses_an_ambiguous_suffix_match(monkeypatch, home):
    """Two providers shipping the same bare name must not silently bill against
    whichever happens to come first in the map."""
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "llama-3.3-70b"\n'
    )
    import litellm

    monkeypatch.setattr(
        litellm,
        "model_cost",
        {
            "groq/llama-3.3-70b": {
                "input_cost_per_token": 5.9e-07,
                "output_cost_per_token": 7.9e-07,
            },
            "together_ai/llama-3.3-70b": {
                "input_cost_per_token": 8.8e-07,
                "output_cost_per_token": 8.8e-07,
            },
        },
    )
    est = estimate_cost(4_000_000, 0)
    assert est.input_tokens == 1_000_000
    assert est.low_usd is None


def test_estimate_cost_prefers_an_exact_bare_key_over_a_suffix_match(monkeypatch, home):
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "some-model"\n'
    )
    import litellm

    monkeypatch.setattr(
        litellm,
        "model_cost",
        {
            "some-model": {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06},
            "vendor/some-model": {"input_cost_per_token": 9e-06, "output_cost_per_token": 9e-06},
        },
    )
    est = estimate_cost(4_000_000, 0)
    assert est.low_usd == pytest.approx(1.0)  # the exact key, not the prefixed one
