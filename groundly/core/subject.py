"""Subject lifecycle: create the on-disk layout that everything else assumes."""

from pathlib import Path

from groundly.core import progress, store
from groundly.core.config import Settings, render_config_toml
from groundly.core.manifest import Manifest
from groundly.core.paths import groundly_home, subject_dir


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
            config_path.write_text(render_config_toml(Settings()))
        return True

    def load_manifest(self) -> Manifest:
        return Manifest.load(self.manifest_path)

    def save_manifest(self, manifest: Manifest) -> None:
        manifest.save(self.manifest_path)

    def stored_material(self, filename) -> Path | None:
        """The stored file a material row names, or None. The name is untrusted (an imported
        bundle wrote it), so the resolved path must stay inside the materials dir."""
        root = self.materials_dir.resolve()
        try:
            path = (root / filename).resolve()
            return path if path.is_relative_to(root) and path.is_file() else None
        except (ValueError, OSError, TypeError):  # NUL in a name; a non-text name from a hostile db
            return None

    def display_title(self) -> str:
        """The title, or "" if none is set. For listings: a damaged manifest costs only its label."""
        try:
            return self.load_manifest().title
        except (OSError, ValueError):  # pydantic's ValidationError is a ValueError
            return ""
