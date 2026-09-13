"""~/.groundly/config.toml: the one place that reads and writes it.

The one provider (`[providers.extraction]`, used only by `index --graph`) is optional:
zero-key operation is first-class, so a missing section is None until a caller needs it.
Interchange-affecting knobs (chunk size, embedding pin) are not config; changing them is a
full re-index. `tomllib` is read-only, so the writer regenerates the whole template.
"""

import logging
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from groundly.core.paths import groundly_home

logger = logging.getLogger(__name__)

CALL_CLASSES = ("extraction",)

_PROVIDER_COMMENTS = {
    "extraction": "graphrag entity extraction + community reports (`index --graph` only)",
}
_PROVIDER_FIELDS = (
    "base_url",
    "model",
    "api_key",
    "input_price_per_mtok",
    "output_price_per_mtok",
    "requests_per_minute",
    "tokens_per_minute",
    "reasoning_effort",
    "temperature",
)


class ProviderConfig(BaseModel):
    base_url: str
    model: str
    # `repr=False` so the key never reaches a log line, traceback frame or `%r` by accident;
    # display paths use `mask_key`.
    api_key: str = Field(default="", repr=False)
    input_price_per_mtok: float | None = None
    output_price_per_mtok: float | None = None
    # Provider/tier limits for graphrag's concurrent client. Unset means no throttling,
    # which is right for a local runtime.
    requests_per_minute: int | None = None
    tokens_per_minute: int | None = None
    # Sent as `extra_body: {"reasoning_effort": ...}`: litellm's drop_params is False, so a
    # flat kwarg raises on every call. A plain str, since accepted values vary by provider.
    reasoning_effort: str | None = None
    # 0.0, not the provider's ~1.0: an unpinned extractor makes builds non-reproducible.
    temperature: float | None = 0.0


class IngestionSettings(BaseModel):
    timeout_seconds: float = 300  # EXTRACT_TIMEOUT_SECONDS — ingestion/extract.py
    max_image_pixels: int = 100_000_000  # MAX_IMAGE_PIXELS — ingestion/extract_worker.py
    max_file_size_mb: float | None = None  # None/0 = no limit (unchanged default behavior)


class LlmSettings(BaseModel):
    timeout_seconds: float = 300  # read timeout — llm/chat.py (connect stays 10s)


class RetrievalSettings(BaseModel):
    context_k: int = 8  # CONTEXT_K — retrieval/vector.py
    rerank: bool = True


# Course-tuned defaults (decision 22) instead of graphrag's news-wire types. `person` stays
# because courses cite Dijkstra and Lamport; a non-CS course retargets via graph.entity_types.
DEFAULT_ENTITY_TYPES = "concept,algorithm,data_structure,theorem,technique,tool,metric,person"


class GraphSettings(BaseModel):
    # The extraction model's usable context; llm/graphrag_adapter.py scales every stage
    # budget to it. 4096 is LM Studio's common default. Floored at 2048: the bundled
    # preamble (~700 tokens) plus a 512-token chunk must fit beside the stage budgets.
    context_window: int = Field(default=4096, ge=2048)

    # Extra extraction passes per chunk, each re-sending prompt, chunk and answer, and each
    # roughly doubling extraction spend (docs/thesis/experiments.md). Runs as 0 below a
    # 16384 window; the fingerprint records the configured value. At exactly 1 the pass is
    # unconditional, since graphrag only asks "any more?" when another round could follow.
    # Capped at 2: beyond that the conversation outgrows any window this project targets.
    gleanings: int = Field(default=0, ge=0, le=2)

    # A custom entity-extraction prompt; unset uses groundly/prompts/extract_graph.txt.
    # Validated when read, and part of the extraction fingerprint, so a change offers a
    # rebuild.
    extraction_prompt: str | None = None

    # Comma-separated, NOT list[str]: _toml_value emits scalars only, so a list would
    # round-trip through `config set` as a Python repr and corrupt the file.
    entity_types: str = DEFAULT_ENTITY_TYPES


class Settings(BaseModel):
    ingestion: IngestionSettings = IngestionSettings()
    llm: LlmSettings = LlmSettings()
    retrieval: RetrievalSettings = RetrievalSettings()
    graph: GraphSettings = GraphSettings()


_SETTINGS_SECTIONS: dict[str, type[BaseModel]] = {
    "ingestion": IngestionSettings,
    "llm": LlmSettings,
    "retrieval": RetrievalSettings,
    "graph": GraphSettings,
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
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    # Retired provider sections and keys are ignored, never rejected: this file is the
    # student's own deployed state.
    retired = sorted(set(data.get("providers", {})) - set(CALL_CLASSES))
    if retired:
        logger.debug("config.toml: ignoring retired provider sections %s", retired)
    if "report_call_class" in data.get("graph", {}):
        logger.debug("config.toml: ignoring retired key graph.report_call_class")
    return data


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


def _settings_from_raw(data: dict) -> Settings:
    """One construction site for both readers (load_settings and set_key's rewrite) —
    a section added to only one of them would be silently dropped on the next write."""
    return Settings(
        **{name: model(**data.get(name, {})) for name, model in _SETTINGS_SECTIONS.items()}
    )


def load_settings() -> Settings:
    return _settings_from_raw(_load_raw())


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
    """Set one dotted key (`extraction.model`, `extraction.key`,
    `ingestion.timeout_seconds`, ...), coerced+validated against its field type, then
    rewrite the documented file."""
    section, _, field = dotted_key.partition(".")
    if not field:
        raise ConfigKeyError(
            f"key must be dotted, e.g. extraction.model or ingestion.timeout_seconds (got {dotted_key!r})"
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

    # `_coerce` checks only the annotation; field constraints (`context_window`'s ge=2048)
    # fire here and must surface as a named ConfigKeyError. Nothing is written yet.
    try:
        settings = _settings_from_raw(data)
    except ValidationError as exc:
        reasons = "; ".join(e.get("msg", "invalid") for e in exc.errors())
        raise ConfigKeyError(f"{dotted_key} rejected: {reasons}") from exc
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
        "#   groundly config set extraction.base_url http://localhost:1234/v1",
        "#   groundly config set extraction.model <model>",
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
            if cls == "extraction":
                lines += [
                    '# base_url = "http://localhost:1234/v1"',
                    '# model    = "..."',
                    '# api_key  = "..."',
                    "# input_price_per_mtok  = 0.0   # optional USD/1M input tokens — override for local/unmapped models",
                    "# output_price_per_mtok = 0.0   # optional USD/1M output tokens (litellm's bundled price map costs mapped models automatically)",
                    "# requests_per_minute   = 30    # optional; your provider tier's RPM. Unset = no throttling (right for local runtimes)",
                    "# tokens_per_minute     = 6000  # optional; your provider tier's TPM. Set these on [providers.extraction] before a graph build — it fires hundreds of concurrent calls",
                    '# reasoning_effort      = "none"  # optional; passed as extra_body — "none" for Ollama, low/medium/high for OpenAI-style o-series reasoning models. Some hosted models ignore it (measured: gpt-oss-120b on DeepInfra still emits reasoning_content)',
                    "# temperature           = 0.0   # defaults to 0.0, NOT the provider's ~1.0 — an unpinned extractor makes every measurement a draw from a distribution. Raise it only where variety is the point",
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
        f"timeout_seconds = {_toml_value(settings.llm.timeout_seconds)}   # read timeout for provider calls; local models can be slow to first token",
        "",
        "[retrieval]",
        f"context_k = {_toml_value(settings.retrieval.context_k)}   # chunks returned per search",
        f"rerank = {_toml_value(settings.retrieval.rerank)}   # cross-encoder rerank (off is faster on weak hardware)",
        "",
        "[graph]",
        f"context_window = {_toml_value(settings.graph.context_window)}   # usable context of your extraction model; graphrag's per-stage prompt budgets are scaled to fit it",
        f"gleanings = {_toml_value(settings.graph.gleanings)}   # extra entity-extraction passes per chunk (0-2); each one doubles extraction cost and mostly adds unconnected entities",
        f"entity_types = {_toml_value(settings.graph.entity_types)}   # comma-separated types entity extraction looks for; the defaults target course material",
    ]
    if settings.graph.extraction_prompt:
        lines.append(
            f"extraction_prompt = {_toml_value(settings.graph.extraction_prompt)}   # custom extraction prompt; must keep {{entity_types}} and {{input_text}}"
        )
    else:
        lines.append(
            "# extraction_prompt =        # path to a custom extraction prompt; unset = the bundled course-tuned one"
        )
    lines.append("")
    return "\n".join(lines)
