"""groundly/llm/config.py: provider config reading from ~/.groundly/config.toml."""

import pytest

from groundly.llm.config import (
    ProviderConfig,
    ProviderNotConfiguredError,
    load_provider,
    require_provider,
)


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def test_load_provider_no_config_file_returns_none(home):
    assert load_provider("chat") is None


def test_load_provider_template_only_returns_none(home):
    # the template written by Subject.initialize() — every [providers.*] section commented out
    (home / "config.toml").write_text(
        "# Groundly provider config\n"
        "# [providers.chat]\n"
        '# base_url = "http://localhost:1234/v1"\n'
        '# model    = "..."\n'
    )
    assert load_provider("chat") is None


def test_load_provider_filled_section_returns_values(home):
    (home / "config.toml").write_text(
        "[providers.chat]\n"
        'base_url = "http://localhost:1234/v1"\n'
        'model = "qwen2.5-7b"\n'
        'api_key = "sk-local"\n'
        "input_price_per_mtok = 0.5\n"
        "output_price_per_mtok = 1.5\n"
    )
    cfg = load_provider("chat")
    assert cfg == ProviderConfig(
        base_url="http://localhost:1234/v1",
        model="qwen2.5-7b",
        api_key="sk-local",
        input_price_per_mtok=0.5,
        output_price_per_mtok=1.5,
    )


def test_load_provider_missing_section_returns_none(home):
    (home / "config.toml").write_text('[providers.chat]\nbase_url = "http://x"\nmodel = "m"\n')
    assert load_provider("router") is None


def test_load_provider_defaults_api_key_and_prices(home):
    (home / "config.toml").write_text('[providers.chat]\nbase_url = "http://x"\nmodel = "m"\n')
    cfg = load_provider("chat")
    assert cfg.api_key == ""
    assert cfg.input_price_per_mtok is None
    assert cfg.output_price_per_mtok is None


def test_require_provider_raises_naming_section_and_path(home):
    with pytest.raises(ProviderNotConfiguredError) as exc:
        require_provider("chat")
    assert "[providers.chat]" in str(exc.value)
    assert str(home / "config.toml") in str(exc.value)


def test_require_provider_returns_config_when_present(home):
    (home / "config.toml").write_text('[providers.chat]\nbase_url = "http://x"\nmodel = "m"\n')
    cfg = require_provider("chat")
    assert cfg.base_url == "http://x"
