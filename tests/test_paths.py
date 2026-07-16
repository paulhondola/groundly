from pathlib import Path

import pytest

from groundly.core import paths
from groundly.core.manifest import Manifest


def test_home_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path))
    assert paths.groundly_home() == tmp_path


def test_home_default(monkeypatch):
    monkeypatch.delenv("GROUNDLY_HOME", raising=False)
    assert paths.groundly_home() == Path.home() / ".groundly"


@pytest.mark.parametrize("name", ["PDSS", "ml-course", "algo_2", "a"])
def test_valid_subject_names(name):
    paths.validate_subject_name(name)


@pytest.mark.parametrize("name", ["", "-lead", "a b", "a/b", "../x", "a.b", "PDSS\n"])
def test_invalid_subject_names(name):
    with pytest.raises(ValueError, match="invalid subject name"):
        paths.validate_subject_name(name)


def test_discover_subjects_scans_manifests(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path))
    for name in ["PDSS", "ML"]:
        (tmp_path / name).mkdir()
        Manifest.new(name).save(tmp_path / name / "manifest.json")
    (tmp_path / "not-a-subject").mkdir()  # no manifest.json
    assert paths.discover_subjects() == ["ML", "PDSS"]
