"""Embedding model management verbs, plus the config verbs (small; not worth a module
of their own)."""

import shutil
from typing import Annotated

import typer

from groundly.cli.app import _fail, config_app, console, models_app


@config_app.callback(invoke_without_command=True)
def config(ctx: typer.Context) -> None:
    """Show the config file path and effective settings."""
    if ctx.invoked_subcommand is not None:
        return
    from groundly.core.config import config_path, load_settings

    console.print(f"Config: {config_path()}", soft_wrap=True)

    for section, values in load_settings():
        for key, value in values:
            if key == "max_file_size_mb" and not value:
                value = "(unlimited)"
            console.print(f"  {section + '.' + key:<26} = {value}")


@config_app.command(name="set")
def config_set(
    key: Annotated[
        str,
        typer.Argument(help="Dotted key, e.g. ingestion.timeout_seconds or retrieval.top_k."),
    ],
    value: Annotated[str, typer.Argument(help="Value to set.")],
) -> None:
    """Set a settings value in ~/.groundly/config.toml."""
    from groundly.core.config import ConfigKeyError, set_key

    try:
        set_key(key, value)
    except ConfigKeyError as exc:
        _fail(str(exc))
    console.print(f"set {key}")


def _models() -> list[tuple[str, str]]:
    """(model, pinned revision) for everything `models` manages."""
    from groundly.core.manifest import EMBEDDING_MODEL
    from groundly.llm.embeddings import HF_REVISION
    from groundly.llm.rerank import RERANKER_HF_REVISION, RERANKER_MODEL

    return [(EMBEDDING_MODEL, HF_REVISION), (RERANKER_MODEL, RERANKER_HF_REVISION)]


@models_app.callback(invoke_without_command=True)
def models(ctx: typer.Context) -> None:
    """Show whether each model is in the local Hugging Face cache (loads no weights)."""
    if ctx.invoked_subcommand is not None:
        return
    from groundly.llm import embeddings

    for model, revision in _models():
        status = "cached" if embeddings.cached_snapshot(model, revision) else "not cached"
        console.print(f"  {model}  {status}")


@models_app.command()
def install(
    force: Annotated[
        bool, typer.Option("--force", help="Re-download and re-verify even if already cached.")
    ] = False,
) -> None:
    """Download the bge-m3 embedding model and the bge-reranker-v2-m3 cross-encoder
    into the local Hugging Face cache."""
    from groundly.llm import embeddings

    names = " and ".join(model for model, _ in _models())
    if not force and all(embeddings.cached_snapshot(m, r) for m, r in _models()):
        console.print(f"{names} already cached — Nothing to do (use --force to re-verify)")
        return

    try:
        for model, revision in _models():
            embeddings.ensure_downloaded(model, revision, force=force)
    except embeddings.ModelDownloadError as exc:
        _fail(str(exc))
    console.print(f"{names} ready")


@models_app.command()
def uninstall(
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Remove bge-m3 and bge-reranker-v2-m3 from the local Hugging Face cache."""
    from groundly.llm import embeddings
    from groundly.llm.convert import converted_dir

    cached = [model for model, revision in _models() if embeddings.cached_snapshot(model, revision)]
    converted = converted_dir()
    if not cached and not converted.exists():
        console.print("no models cached — nothing to do")
        return

    if not yes:
        targets = [f"{m} from the local Hugging Face cache" for m in cached]
        if converted.exists():
            targets.append("the converted bge-m3 weights")
        typer.confirm(f"remove {' and '.join(targets)}?", abort=True)

    for model in cached:
        embeddings.remove_cached(model)
        console.print(f"removed {model} from the cache")
    if converted.exists():
        shutil.rmtree(converted)
        console.print("removed the converted bge-m3 weights")
