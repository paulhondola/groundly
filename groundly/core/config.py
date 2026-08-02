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

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator

from groundly.core.paths import groundly_home

CALL_CLASSES = ("chat", "generation", "extraction", "router")

_PROVIDER_COMMENTS = {
    "chat": "ask pipeline generation",
    "generation": "exam/deck generation (thick path)",
    "extraction": "graphrag entity extraction",
    "router": "cheap query classifier",
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
)


class ProviderConfig(BaseModel):
    base_url: str
    model: str
    api_key: str = ""
    input_price_per_mtok: float | None = None
    output_price_per_mtok: float | None = None
    # Provider/tier rate limits. Unset means no throttling — correct for a local
    # runtime, which has none. Only the graphrag path honours these today (it is the
    # only one that fires hundreds of concurrent calls); see llm/graphrag_adapter.py.
    requests_per_minute: int | None = None
    tokens_per_minute: int | None = None
    # Passed through as `extra_body: {"reasoning_effort": ...}` (never flat — litellm's
    # drop_params is False, so a flat reasoning_effort kwarg raises UnsupportedParamsError
    # on every call instead of degrading). A plain str, not an enum: "none" is what Ollama
    # honours, OpenAI's o-series takes low/medium/high, and providers are not enumerable
    # here (architecture.md: never hardcode a provider).
    reasoning_effort: str | None = None


class IngestionSettings(BaseModel):
    timeout_seconds: float = 300  # EXTRACT_TIMEOUT_SECONDS — ingestion/extract.py
    max_image_pixels: int = 100_000_000  # MAX_IMAGE_PIXELS — ingestion/extract_worker.py
    max_file_size_mb: float | None = None  # None/0 = no limit (unchanged default behavior)


class LlmSettings(BaseModel):
    timeout_seconds: float = 300  # read timeout — llm/chat.py (connect stays 10s)


class RetrievalSettings(BaseModel):
    context_k: int = 8  # CONTEXT_K — retrieval/vector.py
    rerank: bool = True


# Course-tuned defaults (decision 22). graphrag ships `organization,person,geo,event`
# — news-wire types that produced 75 ORGANIZATION and 34 EVENT entities on a
# parallel-algorithms corpus. `person` stays: courses cite Dijkstra and Lamport, and
# those are legitimate nodes. These lean CS-ward, matching the pilot subjects
# (decision 11); a law or history course retargets them via graph.entity_types.
DEFAULT_ENTITY_TYPES = "concept,algorithm,data_structure,theorem,technique,tool,metric,person"


class GraphSettings(BaseModel):
    # The extraction model's usable context. graphrag's own prompt budgets assume
    # ~16k (community reports alone want 8000 in + 2000 out); llm/graphrag_adapter.py
    # scales every stage down to whatever is set here. 4096 is LM Studio's common
    # default, so a graph build works out of the box — raise it to match your model.
    # Floored at 2048: the bundled extraction preamble is ~700 tokens and a chunk can
    # reach CHUNK_MAX_TOKENS (512), so anything smaller cannot fit one call plus the
    # room its stage budgets are carved from.
    context_window: int = Field(default=4096, ge=2048)

    # Path to a custom entity-extraction prompt; unset uses the bundled course-tuned
    # one (groundly/prompts/extract_graph.txt). Two real uses, not speculation: a
    # student outside CS needs different framing, and the thesis's evaluation compares
    # prompts on the gold set — swapping the prompt *is* the experiment. Validated at
    # read time (llm/graphrag_adapter.resolve_extraction_prompt), never as a graphrag
    # internal error. Changing it changes the extraction fingerprint, so the next
    # `groundly index` offers a rebuild.
    extraction_prompt: str | None = None

    # Comma-separated, NOT list[str]: _toml_value emits scalars only, so a list would
    # round-trip through `config set` as a Python repr and corrupt the file.
    entity_types: str = DEFAULT_ENTITY_TYPES

    # Which provider builds community reports. Defaults to "extraction" so an unset
    # value reproduces today's single-model build exactly (one completion model, no
    # second metrics store). A course that wants a stronger/cheaper model for report
    # summarization than for entity extraction points this at another call class —
    # see llm/graphrag_adapter.completion_model_config and ingestion/graph._build_config.
    # Validated against CALL_CLASSES here rather than at the build's read site, so a typo
    # fails immediately instead of surfacing hours in when the community-reports stage
    # finally runs. Note the blast radius is wider than "config time": load_settings() is
    # on the search and index paths too, so a bad value raises there as well — same as
    # context_window's ge=2048 already does.
    report_call_class: str = "extraction"

    @field_validator("report_call_class")
    @classmethod
    def _report_call_class_is_known(cls, v: str) -> str:
        if v not in CALL_CLASSES:
            raise ValueError(f"report_call_class must be one of {CALL_CLASSES}, got {v!r}")
        return v


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

    # `_coerce` only checks the field's *annotation*; whole-model validators (
    # `report_call_class` against CALL_CLASSES, `context_window`'s ge=2048) fire here.
    # Without this they escape as a raw pydantic traceback — worse than the generic
    # error conventions.md already forbids, and on a command whose whole job is to
    # reject bad input. Nothing has been written at this point, so the file is untouched.
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
                    "# input_price_per_mtok  = 0.0   # optional USD/1M input tokens — override for local/unmapped models",
                    "# output_price_per_mtok = 0.0   # optional USD/1M output tokens (litellm's bundled price map costs mapped models automatically)",
                    "# requests_per_minute   = 30    # optional; your provider tier's RPM. Unset = no throttling (right for local runtimes)",
                    "# tokens_per_minute     = 6000  # optional; your provider tier's TPM. Set these on [providers.extraction] before a graph build — it fires hundreds of concurrent calls",
                    '# reasoning_effort      = "none"  # optional; passed as extra_body — "none" for Ollama, low/medium/high for OpenAI-style o-series reasoning models',
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
        f"context_k = {_toml_value(settings.retrieval.context_k)}   # chunks assembled into the answer / prompt",
        f"rerank = {_toml_value(settings.retrieval.rerank)}   # cross-encoder rerank (off is faster on weak hardware)",
        "",
        "[graph]",
        f"context_window = {_toml_value(settings.graph.context_window)}   # usable context of your extraction model; graphrag's per-stage prompt budgets are scaled to fit it",
        f"entity_types = {_toml_value(settings.graph.entity_types)}   # comma-separated types entity extraction looks for; the defaults target course material",
        f'report_call_class = {_toml_value(settings.graph.report_call_class)}   # which call class serves community reports; "extraction" keeps them on the extraction provider',
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
