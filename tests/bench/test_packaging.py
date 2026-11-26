import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_bench_dependencies_are_a_group_not_a_published_extra():
    p = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "bench" not in p["project"]["optional-dependencies"]
    assert p["dependency-groups"]["bench"] == ["mineru-vl-utils[mlx]==2.0.9"]
    assert p["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["groundly"]
