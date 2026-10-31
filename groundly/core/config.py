"""~/.groundly/config.toml: the one place that reads and writes it.

Operational settings only: Groundly runs no LLM, so there is no provider or key to
configure. Interchange-affecting knobs (chunk size, embedding pin) are not config;
changing them is a full re-index. `tomllib` is read-only, so the writer regenerates the
whole template.
"""

import logging
import tomllib
from pathlib import Path
from typing import get_args

from pydantic import BaseModel, TypeAdapter, ValidationError

from groundly.core.paths import groundly_home

logger = logging.getLogger(__name__)


class IngestionSettings(BaseModel):
    timeout_seconds: float = 300  # EXTRACT_TIMEOUT_SECONDS — ingestion/extract.py
    max_image_pixels: int = 100_000_000  # MAX_IMAGE_PIXELS — ingestion/extract_worker.py
    max_file_size_mb: float | None = None  # None/0 = no limit (unchanged default behavior)


class RetrievalSettings(BaseModel):
    top_k: int = 8  # TOP_K — retrieval/vector.py
    rerank: bool = True


class Settings(BaseModel):
    ingestion: IngestionSettings = IngestionSettings()
    retrieval: RetrievalSettings = RetrievalSettings()


_SETTINGS_SECTIONS: dict[str, type[BaseModel]] = {
    "ingestion": IngestionSettings,
    "retrieval": RetrievalSettings,
}


class ConfigKeyError(Exception):
    """`config set` given an unknown key/section or an unparseable value. Message names
    the valid keys (typo protection) — the CLI surfaces it verbatim."""


def config_path() -> Path:
    return groundly_home() / "config.toml"


def _load_raw() -> dict:
    path = config_path()
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    # Retired sections are ignored, never rejected: this file is the student's own
    # deployed state, and may still carry the provider/graph settings of older versions.
    retired = sorted(set(data) - set(_SETTINGS_SECTIONS))
    if retired:
        logger.debug("config.toml: ignoring retired sections %s", retired)
    return data


def _settings_from_raw(data: dict) -> Settings:
    """One construction site for both readers (load_settings and set_key's rewrite) —
    a section added to only one of them would be silently dropped on the next write."""
    return Settings(
        **{name: model(**data.get(name, {})) for name, model in _SETTINGS_SECTIONS.items()}
    )


def load_settings() -> Settings:
    return _settings_from_raw(_load_raw())


def _valid_fields(model: type[BaseModel]) -> str:
    return ", ".join(model.model_fields)


# plain-language names for `config set` errors, keyed by field type
_EXPECTED = {int: "an integer", float: "a number", bool: "true or false", str: "text"}


def _coerce(model: type[BaseModel], field: str, value: str, section: str):
    if field not in model.model_fields:
        raise ConfigKeyError(
            f"unknown field '{field}' for '{section}' — valid: {_valid_fields(model)}"
        )
    annotation = model.model_fields[field].annotation
    nullable = type(None) in get_args(annotation)
    if nullable and value.lower() in ("none", "null"):
        return None
    try:
        return TypeAdapter(annotation).validate_python(value)
    except ValidationError:
        base = next(a for a in get_args(annotation) or (annotation,) if a is not type(None))
        expected = _EXPECTED[base] + (" or 'none'" if nullable else "")
        raise ConfigKeyError(
            f"invalid value for {section}.{field}: expected {expected}, got {value!r}"
        ) from None


def set_key(dotted_key: str, value: str) -> None:
    """Set one dotted key (`ingestion.timeout_seconds`, `retrieval.rerank`, ...), coerced
    and validated against its field type, then rewrite the documented file."""
    section, _, field = dotted_key.partition(".")
    if not field:
        raise ConfigKeyError(
            f"key must be dotted, e.g. ingestion.timeout_seconds (got {dotted_key!r})"
        )
    if section not in _SETTINGS_SECTIONS:
        valid = ", ".join(_SETTINGS_SECTIONS)
        raise ConfigKeyError(f"unknown config section '{section}' — valid: {valid}")
    data = _load_raw()
    data.setdefault(section, {})[field] = _coerce(
        _SETTINGS_SECTIONS[section], field, value, section
    )
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_config_toml(_settings_from_raw(data)))


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


def render_config_toml(settings: Settings) -> str:
    """Regenerate the whole config file with every setting shown at its value. Used by
    both first-run `init` (defaults) and `config set`."""
    ing = settings.ingestion
    lines = [
        "# Groundly config — operational settings. Groundly never asks for an API key.",
        "# Set values with e.g.:  groundly config set retrieval.top_k 12",
        "",
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
        "[retrieval]",
        f"top_k = {_toml_value(settings.retrieval.top_k)}   # chunks returned per search",
        f"rerank = {_toml_value(settings.retrieval.rerank)}   # cross-encoder rerank (off is faster on weak hardware)",
        "",
    ]
    return "\n".join(lines)
