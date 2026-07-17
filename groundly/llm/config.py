"""Provider config: ~/.groundly/config.toml, one OpenAI-compatible endpoint per call
class (chat/generation/extraction/router). Read-only here; `groundly config set`
writes it (later phase). Zero-key operation is first-class: a missing/unfilled
section is simply None, never an error, until a caller actually needs it."""

import tomllib
from pathlib import Path

from pydantic import BaseModel

from groundly.core.paths import groundly_home


class ProviderConfig(BaseModel):
    base_url: str
    model: str
    api_key: str = ""
    input_price_per_mtok: float | None = None
    output_price_per_mtok: float | None = None


class ProviderNotConfiguredError(Exception):
    """Raised by require_provider when a call class has no usable config section."""


def _config_path() -> Path:
    return groundly_home() / "config.toml"


def load_provider(call_class: str) -> ProviderConfig | None:
    path = _config_path()
    if not path.exists():
        return None
    data = tomllib.loads(path.read_text())
    section = data.get("providers", {}).get(call_class)
    if not section:
        return None
    return ProviderConfig(**section)


def require_provider(call_class: str) -> ProviderConfig:
    cfg = load_provider(call_class)
    if cfg is None:
        raise ProviderNotConfiguredError(
            f"[providers.{call_class}] is not configured in {_config_path()} — "
            "add base_url and model (see the commented template written by `groundly init`)"
        )
    return cfg
