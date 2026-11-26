import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_bench_dependencies_are_a_group_not_a_published_extra():
    p = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "bench" not in p["project"]["optional-dependencies"]
    group = p["dependency-groups"]["bench"]
    assert "mineru-vl-utils[mlx]==2.0.9" in group
    assert all("==" in d for d in group), "bench dependencies are pinned exactly"
    assert p["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["groundly"]
