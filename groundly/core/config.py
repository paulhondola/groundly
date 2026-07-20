"""Groundly config: ~/.groundly/config.toml — the one place that reads and writes it.

Two kinds of config live here:

- **Providers**: one OpenAI-compatible endpoint per call class (chat/generation/
  extraction/router). Read lazily and per-section (a half-edited section never
  breaks unrelated calls); zero-key operation is first-class — a missing/unfilled
  section is simply None, never an error, until a caller actually needs it.
- **Settings**: user-tunable operational knobs (ingestion/llm/retrieval) whose
  defaults are the constant values that used to be hardcoded. All defaulted, so a
  missing file yields working defaults and no providers.

Config *parsing* lives here (a foundation both llm/ and ingestion/ may import).
The LLM-provider boundary is about *client construction* — that still happens only
in llm/. Interchange-affecting knobs (chunk size, embedding pin) are deliberately
NOT here: changing them is a full re-index migration, not a config tweak.

`tomllib` is read-only by design, so the writer regenerates the whole documented
template from the effective config — always valid TOML, always self-documenting.
"""

import tomllib
from pathlib import Path

from pydantic import BaseModel, TypeAdapter, ValidationError

from groundly.core.paths import groundly_home

CALL_CLASSES = ("chat", "generation", "extraction", "router")

_PROVIDER_COMMENTS = {
    "chat": "ask pipeline generation",
    "generation": "exam/deck generation (thick path)",
    "extraction": "graphrag entity extraction",
    "router": "cheap query classifier",
}
_PROVIDER_FIELDS = ("base_url", "model", "api_key", "input_price_per_mtok", "output_price_per_mtok")


class ProviderConfig(BaseModel):
    base_url: str
    model: str
    api_key: str = ""
    input_price_per_mtok: float | None = None
    output_price_per_mtok: float | None = None


class IngestionSettings(BaseModel):
    timeout_seconds: float = 300  # EXTRACT_TIMEOUT_SECONDS — ingestion/extract.py
    max_image_pixels: int = 100_000_000  # MAX_IMAGE_PIXELS — ingestion/extract_worker.py
    max_file_size_mb: float | None = None  # None/0 = no limit (unchanged default behavior)


class LlmSettings(BaseModel):
    timeout_seconds: float = 300  # httpx read timeout — llm/chat.py (connect stays 10s)


class RetrievalSettings(BaseModel):
    context_k: int = 8  # CONTEXT_K — retrieval/vector.py
    rerank: bool = True


class Settings(BaseModel):
    ingestion: IngestionSettings = IngestionSettings()
    llm: LlmSettings = LlmSettings()
    retrieval: RetrievalSettings = RetrievalSettings()


_SETTINGS_SECTIONS: dict[str, type[BaseModel]] = {
    "ingestion": IngestionSettings,
    "llm": LlmSettings,
    "retrieval": RetrievalSettings,
}


class ProviderNotConfiguredError(Exception):
    """Raised by require_provider when a call class has no usable config section."""


class ConfigKeyError(Exception):
    """`config set` given an unknown key/section or an unparseable value. Message names
    the valid keys (typo protection) — the CLI surfaces it verbatim."""


def config_path() -> Path:
    return groundly_home() / "config.toml"


def _load_raw() -> dict:
    path = config_path()
    return tomllib.loads(path.read_text()) if path.exists() else {}


def providers_raw() -> dict:
    """Raw (unvalidated) providers table — for `config` display, which must tolerate
    half-edited sections that would fail ProviderConfig validation."""
    return _load_raw().get("providers", {})


def load_provider(call_class: str) -> ProviderConfig | None:
    section = _load_raw().get("providers", {}).get(call_class)
    return ProviderConfig(**section) if section else None


def require_provider(call_class: str) -> ProviderConfig:
    cfg = load_provider(call_class)
    if cfg is None:
        raise ProviderNotConfiguredError(
            f"[providers.{call_class}] is not configured in {config_path()} — "
            "add base_url and model (see the commented template written by `groundly init`)"
        )
    return cfg


def load_settings() -> Settings:
    data = _load_raw()
    return Settings(
        ingestion=IngestionSettings(**data.get("ingestion", {})),
        llm=LlmSettings(**data.get("llm", {})),
        retrieval=RetrievalSettings(**data.get("retrieval", {})),
    )


def mask_key(api_key: str) -> str:
    return f"***{api_key[-3:]}" if api_key else "(none)"


def _valid_fields(model: type[BaseModel]) -> str:
    return ", ".join("key" if f == "api_key" else f for f in model.model_fields)


def _coerce(model: type[BaseModel], field: str, value: str, section: str):
    if field not in model.model_fields:
        raise ConfigKeyError(
            f"unknown field '{field}' for '{section}' — valid: {_valid_fields(model)}"
        )
    annotation = model.model_fields[field].annotation
    try:
        return TypeAdapter(annotation).validate_python(value)
    except ValidationError:
        raise ConfigKeyError(
            f"invalid value for {section}.{field}: expected {annotation}, got {value!r}"
        ) from None


def set_key(dotted_key: str, value: str) -> None:
    """Set one dotted key (`chat.model`, `chat.key`, `ingestion.timeout_seconds`, ...),
    coerced+validated against its field type, then rewrite the documented file."""
    section, _, field = dotted_key.partition(".")
    if not field:
        raise ConfigKeyError(
            f"key must be dotted, e.g. chat.model or ingestion.timeout_seconds (got {dotted_key!r})"
        )
    data = _load_raw()
    if section in CALL_CLASSES:
        field = "api_key" if field == "key" else field
        coerced = _coerce(ProviderConfig, field, value, section)
        data.setdefault("providers", {}).setdefault(section, {})[field] = coerced
    elif section in _SETTINGS_SECTIONS:
        coerced = _coerce(_SETTINGS_SECTIONS[section], field, value, section)
        data.setdefault(section, {})[field] = coerced
    else:
        valid = ", ".join(CALL_CLASSES + tuple(_SETTINGS_SECTIONS))
        raise ConfigKeyError(f"unknown config section '{section}' — valid: {valid}")

    settings = Settings(
        ingestion=IngestionSettings(**data.get("ingestion", {})),
        llm=LlmSettings(**data.get("llm", {})),
        retrieval=RetrievalSettings(**data.get("retrieval", {})),
    )
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_config_toml(data.get("providers", {}), settings))


def _toml_value(v) -> str:
    if isinstance(v, bool):  # bool before int: bool is an int subclass
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    # Escape TOML basic-string specials incl. newlines/tabs — a stray control char
    # (e.g. a pasted value with a newline) would otherwise emit invalid TOML that
    # breaks every later read, not just this write.
    escaped = (
        str(v)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def render_config_toml(providers: dict, settings: Settings) -> str:
    """Regenerate the whole config file: configured provider sections filled in,
    unconfigured ones shown as commented examples, all settings shown with values.
    Used by both first-run `init` (empty providers) and `config set`."""
    lines = [
        "# Groundly config — providers + operational settings.",
        "# Providers: one OpenAI-compatible endpoint per call class; all optional",
        "# (indexing and search work with no provider at all). Set values with e.g.:",
        "#   groundly config set chat.base_url http://localhost:1234/v1",
        "#   groundly config set chat.model <model>",
        "",
    ]
    for cls in CALL_CLASSES:
        section = providers.get(cls) or {}
        comment = _PROVIDER_COMMENTS[cls]
        if section:
            lines.append(f"[providers.{cls}]  # {comment}")
            for field in _PROVIDER_FIELDS:
                if section.get(field) is not None:
                    lines.append(f"{field} = {_toml_value(section[field])}")
        else:
            lines.append(f"# [providers.{cls}]  # {comment}")
            if cls == "chat":
                lines += [
                    '# base_url = "http://localhost:1234/v1"',
                    '# model    = "..."',
                    '# api_key  = "..."',
                    "# input_price_per_mtok  = 0.0   # optional USD/1M input tokens — enables cost tracing",
                    "# output_price_per_mtok = 0.0   # optional USD/1M output tokens",
                ]
        lines.append("")

    ing = settings.ingestion
    lines += [
        "[ingestion]",
        f"timeout_seconds = {_toml_value(ing.timeout_seconds)}   # per-file extraction wall-clock; raise for large PDFs / heavy OCR",
        f"max_image_pixels = {_toml_value(ing.max_image_pixels)}   # decompression-bomb cap before an image is rasterized",
    ]
    if ing.max_file_size_mb:
        lines.append(
            f"max_file_size_mb = {_toml_value(ing.max_file_size_mb)}   # reject input files larger than this (MB)"
        )
    else:
        lines.append(
            "# max_file_size_mb =        # optional MB cap on input files; unset = no limit"
        )
    lines += [
        "",
        "[llm]",
        f"timeout_seconds = {_toml_value(settings.llm.timeout_seconds)}   # HTTP read timeout for provider calls; local models can be slow to first token",
        "",
        "[retrieval]",
        f"context_k = {_toml_value(settings.retrieval.context_k)}   # chunks assembled into the answer / prompt",
        f"rerank = {_toml_value(settings.retrieval.rerank)}   # cross-encoder rerank (off is faster on weak hardware)",
        "",
    ]
    return "\n".join(lines)
