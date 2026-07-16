"""Subject lifecycle: create the on-disk layout that everything else assumes."""

from pathlib import Path

from unilearn.core import store
from unilearn.core.manifest import Manifest
from unilearn.core.paths import subject_dir, unilearn_home

_CONFIG_TEMPLATE = """\
# UniLearn provider config — one OpenAI-compatible endpoint per call class.
# All classes are optional: indexing and search work with no provider at all.
#
# [providers.chat]        # ask pipeline generation
# base_url = "http://localhost:1234/v1"
# model    = "..."
# api_key  = "..."
#
# [providers.generation]  # exam/deck generation (thick path)
# [providers.extraction]  # graphrag entity extraction
# [providers.router]      # cheap query classifier
"""


def init_subject(name: str) -> tuple[Path, bool]:
    """Create ~/.unilearn/<name>/ (manifest, materials/, store.db, progress.db).

    Returns (subject_dir, created); created=False if already initialized (idempotent).
    Also writes the top-level config.toml template on first ever init.
    """
    sdir = subject_dir(name)
    manifest_path = sdir / "manifest.json"
    if manifest_path.exists():
        return sdir, False

    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "materials").mkdir(exist_ok=True)
    store.create_store(sdir / "store.db")
    store.create_progress(sdir / "progress.db")
    Manifest.new(name).save(manifest_path)

    config_path = unilearn_home() / "config.toml"
    if not config_path.exists():
        config_path.write_text(_CONFIG_TEMPLATE)
    return sdir, True
