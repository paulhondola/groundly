"""groundly/core/config.py: settings + the config-set writer (providers covered by
tests/llm/test_llm_config.py via the re-export shim)."""

import pytest

from groundly.core.config import (
    ConfigKeyError,
    Settings,
    config_path,
    load_provider,
    load_settings,
    mask_key,
    providers_raw,
    render_config_toml,
    set_key,
)


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def test_settings_default_when_no_file():
    s = load_settings()
    assert s.ingestion.timeout_seconds == 300
    assert s.ingestion.max_image_pixels == 100_000_000
    assert s.ingestion.max_file_size_mb is None  # unlimited by default
    assert s.llm.timeout_seconds == 300
    assert s.retrieval.context_k == 8
    assert s.retrieval.rerank is True
    assert s.graph.context_window == 4096  # LM Studio's common default
    assert s.graph.extraction_prompt is None  # unset = the bundled course-tuned prompt
    assert s.graph.entity_types.startswith("concept,algorithm")


def test_graph_prompt_settings_round_trip_through_set_and_rewrite(home, tmp_path):
    """entity_types is a comma-separated *string*, not list[str], precisely so it
    survives this: the writer emits scalars only, so a list would come back as a Python
    repr and every later read would fail."""
    custom = tmp_path / "custom.txt"
    set_key("graph.entity_types", "concept,proof")
    set_key("graph.extraction_prompt", str(custom))

    s = load_settings()
    assert s.graph.entity_types == "concept,proof"
    assert s.graph.extraction_prompt == str(custom)

    set_key("retrieval.context_k", "12")  # rewrite triggered by an unrelated section
    s = load_settings()
    assert s.graph.entity_types == "concept,proof"
    assert s.graph.extraction_prompt == str(custom)


def test_graph_context_window_round_trips_through_set_and_rewrite(home):
    """The rewrite regenerates the whole file from the effective config, so a section
    missing from the writer is silently dropped on the next `config set`."""
    set_key("graph.context_window", "16384")
    assert load_settings().graph.context_window == 16384

    set_key("retrieval.context_k", "12")  # rewrite triggered by an unrelated section
    assert load_settings().graph.context_window == 16384


def test_graph_report_call_class_round_trips_through_set_and_rewrite(home):
    """`config set graph.report_call_class chat` printed success and wrote nothing: the
    key was missing from render_config_toml's [graph] block, and set_key regenerates the
    whole file from that renderer, so the value was dropped on the way to disk and read
    back as the default. The documented command was a silent no-op — and every other test
    for this setting missed it by writing config.toml by hand instead of going through
    set_key, which is the only path a user actually has."""
    set_key("graph.report_call_class", "chat")
    assert load_settings().graph.report_call_class == "chat"

    set_key("retrieval.context_k", "12")  # rewrite triggered by an unrelated section
    assert load_settings().graph.report_call_class == "chat"


def test_render_then_load_round_trips(home):
    (home / "config.toml").write_text(render_config_toml({}, Settings()))
    s = load_settings()
    assert s.ingestion.timeout_seconds == 300
    assert s.retrieval.rerank is True
    assert load_provider("chat") is None  # all provider sections commented


def test_set_settings_int(home):
    set_key("ingestion.timeout_seconds", "600")
    assert load_settings().ingestion.timeout_seconds == 600


def test_set_settings_bool(home):
    set_key("retrieval.rerank", "false")
    assert load_settings().retrieval.rerank is False


def test_set_max_file_size(home):
    assert load_settings().ingestion.max_file_size_mb is None
    set_key("ingestion.max_file_size_mb", "50")
    assert load_settings().ingestion.max_file_size_mb == 50


def test_set_provider_and_key_alias(home):
    set_key("chat.base_url", "http://localhost:1234/v1")
    set_key("chat.model", "qwen2.5-7b")
    set_key("chat.key", "sk-secret")
    cfg = load_provider("chat")
    assert cfg.base_url == "http://localhost:1234/v1"
    assert cfg.model == "qwen2.5-7b"
    assert cfg.api_key == "sk-secret"


def test_set_preserves_other_sections(home):
    set_key("chat.base_url", "http://x")
    set_key("chat.model", "m")
    set_key("ingestion.timeout_seconds", "700")
    # setting a setting must not wipe the configured provider
    assert load_provider("chat").model == "m"
    assert load_settings().ingestion.timeout_seconds == 700


def test_unknown_section_lists_valid(home):
    with pytest.raises(ConfigKeyError) as exc:
        set_key("nope.field", "x")
    assert "chat" in str(exc.value) and "ingestion" in str(exc.value)


def test_unknown_field_rejected(home):
    with pytest.raises(ConfigKeyError):
        set_key("ingestion.nope", "1")


def test_bad_type_rejected(home):
    with pytest.raises(ConfigKeyError):
        set_key("ingestion.timeout_seconds", "abc")


def test_model_validator_failures_are_named_not_raw_tracebacks(home):
    """`_coerce` checks the field's annotation only; whole-model validators fire later,
    in `_settings_from_raw`. They used to escape as a raw pydantic traceback — on the one
    command whose entire job is rejecting bad input (conventions.md: name the cause,
    never a generic error, and a stack dump is worse than generic). Both surfaces:
    report_call_class's CALL_CLASSES check and context_window's pre-existing ge=2048."""
    with pytest.raises(ConfigKeyError) as exc:
        set_key("graph.report_call_class", "bogus")
    assert "report_call_class" in str(exc.value) and "extraction" in str(exc.value)

    with pytest.raises(ConfigKeyError) as exc:
        set_key("graph.context_window", "512")
    assert "2048" in str(exc.value)


def test_non_dotted_key_rejected(home):
    with pytest.raises(ConfigKeyError):
        set_key("chat", "x")


def test_mask_key():
    assert mask_key("sk-local") == "***cal"
    assert mask_key("") == "(none)"


def test_providers_raw_tolerates_partial_section(home):
    # a half-edited section (base_url only, no model) would fail ProviderConfig,
    # but display reads raw and must not crash
    set_key("chat.base_url", "http://x")
    raw = providers_raw()
    assert raw["chat"]["base_url"] == "http://x"


def test_config_path_under_home(home):
    assert config_path() == home / "config.toml"


def test_set_value_with_control_chars_stays_valid_toml(home):
    # a pasted value with a newline must not corrupt the file for every later read
    set_key("chat.base_url", "http://x\n[providers.router]\nbase_url=evil")
    set_key("chat.model", "m")
    assert load_provider("router") is None  # no injected section
    assert "evil" in load_provider("chat").base_url  # round-trips as one string


def test_provider_rate_limits_round_trip(home):
    """Rate limits are provider/tier properties, so they live on the provider section
    and must survive the whole-file rewrite like every other provider field."""
    set_key("extraction.base_url", "https://api.groq.com/openai/v1")
    set_key("extraction.model", "llama-3.3-70b-versatile")
    set_key("extraction.tokens_per_minute", "6000")
    set_key("extraction.requests_per_minute", "30")

    cfg = load_provider("extraction")
    assert (cfg.tokens_per_minute, cfg.requests_per_minute) == (6000, 30)

    set_key("graph.context_window", "16384")  # unrelated rewrite must not drop them
    cfg = load_provider("extraction")
    assert (cfg.tokens_per_minute, cfg.requests_per_minute) == (6000, 30)


def test_provider_rate_limits_default_to_unset(home):
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://x"\nmodel = "m"\n'
    )
    cfg = load_provider("extraction")
    assert cfg.tokens_per_minute is None and cfg.requests_per_minute is None
