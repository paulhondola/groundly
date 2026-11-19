"""bge-m3 weight conversion: torch runs only in a child process."""

import os
import subprocess
import sys

import numpy as np
import pytest

from groundly.llm import convert
from groundly.llm.embeddings import ModelDownloadError


def _fake_snapshot(tmp_path):  # runs torch in a child, never in the test process
    snap = tmp_path / "snap"
    snap.mkdir()
    code = (
        "import torch,sys; p=sys.argv[1];"
        "torch.save({'embeddings.word_embeddings.weight': torch.ones(4, 2)}, p + '/pytorch_model.bin');"
        "torch.save({'weight': torch.ones(1, 2), 'bias': torch.zeros(1)}, p + '/sparse_linear.pt')"
    )
    subprocess.run([sys.executable, "-c", code, str(snap)], check=True)
    return snap


def test_converts_once_to_f16_safetensors(tmp_path, monkeypatch):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    out = convert.ensure_converted(_fake_snapshot(tmp_path), revision="r1")
    from safetensors.numpy import load_file

    enc, head = load_file(out / "model.safetensors"), load_file(out / "sparse_linear.safetensors")
    assert enc["embeddings.word_embeddings.weight"].dtype == np.float16
    assert set(head) == {"weight", "bias"}
    mtime = (out / "model.safetensors").stat().st_mtime_ns
    assert convert.ensure_converted(tmp_path / "snap", revision="r1") == out
    assert (out / "model.safetensors").stat().st_mtime_ns == mtime  # reused, not reconverted


def test_a_half_finished_conversion_is_never_used(tmp_path, monkeypatch):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    partial = convert.converted_dir("r1").parent / "bge-m3-r1.tmp-dead"
    partial.mkdir(parents=True)
    (partial / "model.safetensors").write_bytes(b"trunc")
    out = convert.ensure_converted(_fake_snapshot(tmp_path), revision="r1")
    assert out == convert.converted_dir("r1") and (out / "sparse_linear.safetensors").exists()


def test_conversion_never_imports_torch_in_the_caller(tmp_path):
    probe = (
        "import sys, groundly.llm.convert as c; from pathlib import Path;"
        f"c.ensure_converted(Path({str(_fake_snapshot(tmp_path))!r}), revision='r2');"
        "print('torch' in sys.modules)"
    )
    r = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "GROUNDLY_HOME": str(tmp_path / "home")},
    )
    assert r.stdout.strip() == "False"


def test_a_failed_conversion_names_the_model(tmp_path, monkeypatch):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "empty").mkdir()
    with pytest.raises(ModelDownloadError, match="bge-m3"):
        convert.ensure_converted(tmp_path / "empty", revision="r3")
