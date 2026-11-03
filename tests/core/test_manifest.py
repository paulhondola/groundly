"""groundly/core/manifest.py: manifest.json field round-trips (docs/architecture/data-model.md)."""

import json
import os
import subprocess
import sys

from groundly.core.manifest import Manifest


def test_manifest_round_trips(tmp_path):
    manifest = Manifest.new("apd")
    path = tmp_path / "manifest.json"
    manifest.save(path)

    assert Manifest.load(path) == manifest


def test_manifest_from_before_the_graph_was_dropped_still_parses(tmp_path):
    # older manifests carry a graphrag block; it is ignored, not rejected
    old = {
        "format_version": 1,
        "subject": "apd",
        "embedding": {},
        "graphrag": {"version": "3.1.0", "extraction_model": "m", "corpus_hash": "deadbeef"},
        "chunking": {},
        "ocr": {},
        "counts": {},
        "tool_version": "0.1.0",
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(old))

    loaded = Manifest.load(path)
    assert loaded.subject == "apd"
    assert "graphrag" not in loaded.model_dump()


def test_title_round_trips_as_utf8_whatever_the_locale(tmp_path):
    """Bundles parse manifests as UTF-8, so a locale-encoded file would not import elsewhere."""
    path = tmp_path / "manifest.json"
    code = (
        "import sys; from pathlib import Path; from groundly.core.manifest import Manifest;"
        "p = Path(sys.argv[1]); Manifest(subject='s', title='Paraleli și Distribuiți').save(p);"
        "assert Manifest.load(p).title == 'Paraleli și Distribuiți'"
    )
    env = {**os.environ, "LC_ALL": "C", "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"}
    result = subprocess.run(
        [sys.executable, "-c", code, str(path)],
        check=False,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Paraleli și Distribuiți" in path.read_bytes().decode("utf-8")
