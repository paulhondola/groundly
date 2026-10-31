"""groundly/core/manifest.py: manifest.json field round-trips (docs/architecture/data-model.md)."""

import json

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
