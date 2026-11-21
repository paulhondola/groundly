import json
import sys
import types
from pathlib import Path

import pytest

from bench.stacks._worker import handler
from groundly.ingestion import extract_worker


def fake_stack(result=None, **attrs) -> types.ModuleType:
    stack = types.ModuleType("fake")
    stack.SUFFIXES = attrs.get("SUFFIXES", frozenset({".pdf"}))
    stack.calls = 0

    def extract(path, ocr_lang):
        stack.calls += 1
        return result or {"pages": 1, "chunks": [{"text": "hi"}]}

    stack.extract = extract
    return stack


def touch(path: Path) -> Path:
    path.write_bytes(b"x")
    return path


def test_claimed_suffix_goes_to_the_stack(tmp_path):
    stack = fake_stack(result={"pages": 1, "chunks": [{"text": "hi"}]})
    handler(stack)(touch(tmp_path / "a.pdf"), tmp_path / "out.json", None)
    assert json.loads((tmp_path / "out.json").read_text())["pages"] == 1
    assert stack.calls == 1


def test_other_suffixes_use_groundly_default(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(extract_worker, "_handle", lambda *a: seen.append(a))
    stack = fake_stack()
    handler(stack)(tmp_path / "n.txt", tmp_path / "out.json", "ro")
    assert stack.calls == 0
    assert seen == [(tmp_path / "n.txt", tmp_path / "out.json", "ro")]


def test_blank_stack_output_exits_no_text(tmp_path):
    stack = fake_stack(result={"pages": 1, "chunks": [{"text": "  "}]})
    with pytest.raises(SystemExit) as e:
        handler(stack)(touch(tmp_path / "a.pdf"), tmp_path / "out.json", None)
    assert e.value.code == extract_worker.EXIT_NO_TEXT
    assert not (tmp_path / "out.json").exists()


def test_page_mode_writes_one_line_per_page_and_isolates_file_errors(tmp_path, monkeypatch):
    from bench.stacks import _worker

    good, bad = tmp_path / "good.pdf", tmp_path / "bad.pdf"
    good.write_bytes(b"g")
    bad.write_bytes(b"b")
    manifest = tmp_path / "m.json"
    pages = [
        {"sha256": "g", "page": 1, "path_hint": str(good), "ref": False},
        {"sha256": "g", "page": 2, "path_hint": str(good), "ref": True},
        {"sha256": "b", "page": 1, "path_hint": str(bad), "ref": False},
    ]
    manifest.write_text(json.dumps({"version": 1, "pages": pages}))
    stack = fake_stack()

    def page_texts(path, wanted, ocr_lang):
        if path == bad:
            raise ValueError("boom")
        return {n: f"t{n}" for n in wanted}

    stack.page_texts = page_texts
    out = tmp_path / "o.jsonl"
    monkeypatch.setattr(
        sys, "argv", ["x", "--pages", str(manifest), "--out", str(out), "--only-ref"]
    )
    _worker.main(stack)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [(r["sha256"], r["page"], r["text"]) for r in rows] == [
        ("g", 2, "t2"),
    ]
    monkeypatch.setattr(sys, "argv", ["x", "--pages", str(manifest), "--out", str(out)])
    _worker.main(stack)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [(r["sha256"], r["page"], r["error"]) for r in rows] == [
        ("g", 1, None),
        ("g", 2, None),
        ("b", 1, "ValueError: boom"),
    ]
