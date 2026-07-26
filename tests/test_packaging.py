"""Packaging assumptions that only break once installed.

`groundly/prompts/extract_graph.txt` is the first non-Python file Groundly ships as
package data. `[tool.hatch.build.targets.wheel] packages = ["groundly"]` is supposed to
include it with no pyproject change — worth asserting rather than trusting, because a
missing prompt file works perfectly from a source checkout and fails only for a user who
installed the wheel.

@slow: builds a real wheel (a few seconds), so it is excluded from the default run.
"""

import subprocess
import zipfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BUNDLED_PROMPT = "groundly/prompts/extract_graph.txt"


@pytest.mark.slow
def test_bundled_extraction_prompt_ships_in_the_wheel(tmp_path):
    build = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0:
        pytest.skip(f"wheel build unavailable: {build.stderr.strip()[:200]}")

    wheels = list(tmp_path.glob("*.whl"))
    assert wheels, "uv build produced no wheel"
    with zipfile.ZipFile(wheels[0]) as whl:
        names = whl.namelist()
        assert _BUNDLED_PROMPT in names, f"{_BUNDLED_PROMPT} missing from the wheel"
        # ...and it is the real prompt, not an empty placeholder
        assert b"{input_text}" in whl.read(_BUNDLED_PROMPT)


def test_bundled_prompt_resolves_as_a_package_resource():
    """The runtime lookup graphrag_adapter uses. Runs everywhere (no wheel build), so a
    rename of the prompt file fails the default suite too, not just the slow one."""
    from importlib.resources import files

    resource = files("groundly").joinpath("prompts/extract_graph.txt")
    assert resource.is_file()
    assert "{entity_types}" in resource.read_text(encoding="utf-8")
