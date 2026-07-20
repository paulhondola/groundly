"""Subject lifecycle: create the on-disk layout that everything else assumes."""

from pathlib import Path

from groundly.core import store
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
        store.create_progress(self.progress_db_path)
        Manifest.new(self.name).save(self.manifest_path)

        config_path = groundly_home() / "config.toml"
        if not config_path.exists():
            config_path.write_text(render_config_toml({}, Settings()))
        return True

    def load_manifest(self) -> Manifest:
        return Manifest.load(self.manifest_path)

    def save_manifest(self, manifest: Manifest) -> None:
        manifest.save(self.manifest_path)


def init_subject(name: str) -> tuple[Path, bool]:
    """Create ~/.groundly/<name>/ (manifest, materials/, store.db, progress.db).

    Returns (subject_dir, created); created=False if already initialized (idempotent).
    Also writes the top-level config.toml template on first ever init.
    """
    subject = Subject(name)
    created = subject.initialize()
    return subject.root_dir, created
