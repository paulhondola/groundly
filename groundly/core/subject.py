"""Subject lifecycle: create the on-disk layout that everything else assumes."""

from pathlib import Path

from groundly.core import progress, store
from groundly.core.config import Settings, render_config_toml
from groundly.core.manifest import Manifest
from groundly.core.paths import subject_dir, groundly_home


class Subject:
    """Represents a Groundly subject workspace with its directories, database files, and manifest."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._root_dir = subject_dir(name)

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    @property
    def materials_dir(self) -> Path:
        return self._root_dir / "materials"

    @property
    def store_db_path(self) -> Path:
        return self._root_dir / "store.db"

    @property
    def progress_db_path(self) -> Path:
        return self._root_dir / "progress.db"

    @property
    def manifest_path(self) -> Path:
        return self._root_dir / "manifest.json"

    def exists(self) -> bool:
        return self.manifest_path.exists()

    def initialize(self) -> bool:
        """Create subject layout (~/.groundly/<name>/).

        Returns True if created, False if already initialized.
        """
        if self.exists():
            return False

        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.materials_dir.mkdir(exist_ok=True)
        store.create_store(self.store_db_path)
        progress.create_progress(self.progress_db_path)
        Manifest.new(self.name).save(self.manifest_path)

        config_path = groundly_home() / "config.toml"
        if not config_path.exists():
            config_path.write_text(render_config_toml({}, Settings()))
        return True

    def graph_is_built(self) -> bool:
        """A *recorded* graph, not merely a directory. A refused or interrupted build
        deliberately leaves partial parquet behind so the retry keeps graphrag's paid-for
        cache (decision 21); only `corpus_hash` is written by a build that passed every
        gate, so it is the honest record of "there is a graph here".

        The `and` order is load-bearing: it short-circuits before `load_manifest()`, so a
        subject that was never initialized answers False instead of raising
        FileNotFoundError from inside the manifest read. `core/graph_html.py` orders its
        own check that way for the same reason.

        Narrower checks deliberately do *not* call this. `ingestion/graph.py`'s build
        gates and `cli/subjects.py` ask whether a hash is *recorded*, ignoring the
        directory — `graph_is_stale` is what reports a directory that went missing, and
        folding the directory term in here would make that branch unreachable.
        """
        return (
            self.root_dir / "graph"
        ).exists() and self.load_manifest().graphrag.corpus_hash is not None

    def load_manifest(self) -> Manifest:
        return Manifest.load(self.manifest_path)

    def save_manifest(self, manifest: Manifest) -> None:
        manifest.save(self.manifest_path)
