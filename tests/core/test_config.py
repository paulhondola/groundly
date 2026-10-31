"""groundly/core/config.py: settings + the config-set writer."""

import tomllib

import pytest

from groundly.core.config import (
    ConfigKeyError,
    Settings,
    _toml_value,
    config_path,
    load_settings,
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
    assert s.retrieval.context_k == 8
    assert s.retrieval.rerank is True


def test_render_then_load_round_trips(home):
    (home / "config.toml").write_text(render_config_toml(Settings()))
    s = load_settings()
    assert s.ingestion.timeout_seconds == 300
    assert s.retrieval.rerank is True


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


def test_set_preserves_other_sections(home):
    set_key("retrieval.context_k", "12")
    set_key("ingestion.timeout_seconds", "700")
    # the whole file is regenerated, so a section the writer forgets is lost on the next set
    assert load_settings().retrieval.context_k == 12
    assert load_settings().ingestion.timeout_seconds == 700


def test_unknown_section_lists_valid(home):
    with pytest.raises(ConfigKeyError) as exc:
        set_key("nope.field", "x")
    assert "ingestion" in str(exc.value) and "retrieval" in str(exc.value)


def test_retired_provider_section_is_unknown(home):
    with pytest.raises(ConfigKeyError, match="unknown config section 'extraction'"):
        set_key("extraction.model", "m")


def test_unknown_field_rejected(home):
    with pytest.raises(ConfigKeyError):
        set_key("ingestion.nope", "1")


def test_bad_type_rejected(home):
    with pytest.raises(ConfigKeyError):
        set_key("ingestion.timeout_seconds", "abc")


def test_non_dotted_key_rejected(home):
    with pytest.raises(ConfigKeyError):
        set_key("chat", "x")


def test_config_path_under_home(home):
    assert config_path() == home / "config.toml"


def test_toml_strings_with_control_chars_stay_valid_toml():
    # a pasted value with a newline must not corrupt the file for every later read
    value = 'a"b\n[injected]\nx=1\t\\'
    assert tomllib.loads(f"v = {_toml_value(value)}") == {"v": value}


def test_config_with_retired_sections_still_loads(home):
    """A config.toml written before the cut carries provider, [llm] and [graph] sections.
    Loading must ignore them, never reject them — the student's own file is the deployed
    state, and there is no server to migrate."""
    (home / "config.toml").write_text(
        '[providers.extraction]\nbase_url = "http://e"\nmodel = "e"\napi_key = "sk-secret"\n'
        "[llm]\ntimeout_seconds = 60\n"
        "[graph]\ncontext_window = 8192\n"
        "[retrieval]\ncontext_k = 5\n"
    )
    assert load_settings().retrieval.context_k == 5

    set_key("ingestion.timeout_seconds", "700")  # whole-file rewrite sheds them, key included
    text = (home / "config.toml").read_text()
    assert "providers" not in text and "[graph]" not in text and "sk-secret" not in text
    assert load_settings().retrieval.context_k == 5
