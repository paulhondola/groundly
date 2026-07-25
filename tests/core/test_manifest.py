"""groundly/core/manifest.py: manifest.json field round-trips (docs/architecture/data-model.md)."""

import json

from groundly.core.manifest import Graphrag, Manifest


def test_graphrag_corpus_hash_defaults_to_none():
    assert Graphrag().corpus_hash is None


def test_manifest_round_trips_corpus_hash(tmp_path):
    manifest = Manifest.new("apd")
    manifest.graphrag.corpus_hash = "deadbeef"
    manifest.graphrag.version = "3.1.0"
    manifest.graphrag.extraction_model = "gpt-4o-mini"
    path = tmp_path / "manifest.json"
    manifest.save(path)

    loaded = Manifest.load(path)
    assert loaded.graphrag.corpus_hash == "deadbeef"
    assert loaded.graphrag.version == "3.1.0"
    assert loaded.graphrag.extraction_model == "gpt-4o-mini"


def test_old_manifest_without_corpus_hash_still_parses(tmp_path):
    # pre-P5 manifests never wrote corpus_hash — additive field, no format_version bump
    old = {
        "format_version": 1,
        "subject": "apd",
        "embedding": {},
        "graphrag": {"version": None, "extraction_model": None},
        "chunking": {},
        "ocr": {},
        "counts": {},
        "tool_version": "0.1.0",
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(old))

    loaded = Manifest.load(path)
    assert loaded.graphrag.corpus_hash is None
