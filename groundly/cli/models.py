"""Embedding model management verbs, plus the still-stubbed config verbs (small;
not worth a module of their own)."""

from typing import Annotated

import typer

from groundly.cli.app import _fail, config_app, console, models_app


@config_app.callback(invoke_without_command=True)
def config(ctx: typer.Context) -> None:
    """Show the config file path and effective values per call class (keys masked)."""
    if ctx.invoked_subcommand is not None:
        return
    from groundly.core.config import (
        CALL_CLASSES,
        config_path,
        load_settings,
        mask_key,
        providers_raw,
    )

    console.print(f"Config: {config_path()}", soft_wrap=True)
    providers = providers_raw()
    console.print("\n[bold]Providers[/bold]")
    for call_class in CALL_CLASSES:
        section = providers.get(call_class) or {}
        if section.get("base_url") and section.get("model"):
            key = mask_key(section.get("api_key", ""))
            console.print(
                f"  {call_class}: model={section['model']}  "
                f"base_url={section['base_url']}  key={key}"
            )
        else:
            console.print(f"  {call_class}: [dim](not configured)[/dim]")

    s = load_settings()
    console.print("\n[bold]Settings[/bold]")
    console.print(f"  ingestion.timeout_seconds  = {s.ingestion.timeout_seconds}")
    console.print(f"  ingestion.max_image_pixels = {s.ingestion.max_image_pixels}")
    console.print(
        f"  ingestion.max_file_size_mb = "
        f"{s.ingestion.max_file_size_mb if s.ingestion.max_file_size_mb else '(unlimited)'}"
    )
    console.print(f"  llm.timeout_seconds        = {s.llm.timeout_seconds}")
    console.print(f"  retrieval.context_k        = {s.retrieval.context_k}")
    console.print(f"  retrieval.rerank           = {s.retrieval.rerank}")


@config_app.command(name="set")
def config_set(
    key: Annotated[
        str,
        typer.Argument(help="Dotted key, e.g. chat.model, chat.key, ingestion.timeout_seconds."),
    ],
    value: Annotated[str, typer.Argument(help="Value to set.")],
) -> None:
    """Set a provider or settings value in ~/.groundly/config.toml."""
    from groundly.core.config import ConfigKeyError, set_key

    try:
        set_key(key, value)
    except ConfigKeyError as exc:
        _fail(str(exc))
    console.print(f"set {key}")


@models_app.command()
def install(
    force: Annotated[
        bool, typer.Option("--force", help="Re-download and re-verify even if already cached.")
    ] = False,
) -> None:
    """Download the bge-m3 embedding model and the bge-reranker-v2-m3 cross-encoder
    into the local Hugging Face cache."""
    from groundly.core.manifest import EMBEDDING_MODEL
    from groundly.llm import embeddings
    from groundly.llm.rerank import RERANKER_HF_REVISION, RERANKER_MODEL

    embed_cached = embeddings.cached_snapshot() is not None
    rerank_cached = embeddings.cached_snapshot(RERANKER_MODEL, RERANKER_HF_REVISION) is not None
    if not force and embed_cached and rerank_cached:
        console.print(
            f"{EMBEDDING_MODEL} and {RERANKER_MODEL} already cached — "
            "Nothing to do (use --force to re-verify)"
        )
        return

    try:
        embeddings.ensure_downloaded(force=force)
        embeddings.ensure_downloaded(RERANKER_MODEL, RERANKER_HF_REVISION, force=force)
    except embeddings.ModelDownloadError as exc:
        _fail(str(exc))
    console.print(f"{EMBEDDING_MODEL} and {RERANKER_MODEL} ready")


@models_app.command()
def uninstall(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Remove the bge-m3 embedding model from the local Hugging Face cache."""
    from groundly.core.manifest import EMBEDDING_MODEL
    from groundly.llm import embeddings

    if embeddings.cached_snapshot() is None:
        console.print(f"{EMBEDDING_MODEL} is not cached — nothing to do")
        return

    if not yes:
        typer.confirm(f"remove {EMBEDDING_MODEL} from the local Hugging Face cache?", abort=True)

    embeddings.remove_cached()
    console.print(f"removed {EMBEDDING_MODEL} from the cache")
