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
