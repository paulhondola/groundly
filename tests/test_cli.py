"""CLI skeleton: the P1 command surface exists with the designed grammar.

Bodies are stubs (exit 1) until their phase lands; these tests pin the surface,
not behavior — see docs/superpowers/specs/2026-07-16-p1-cli-surface-design.md.
"""

import pytest
from typer.testing import CliRunner

from unilearn.cli import app

runner = CliRunner()


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Usage" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["init", "PDSS"],
        ["index", "PDSS", "lecture1.pdf"],
        ["index", "PDSS", "a.pdf", "b.pdf"],  # PATHS is variadic
        ["list"],  # subject optional
        ["list", "PDSS"],
        ["remove", "PDSS", "lecture1.pdf", "--yes"],
        ["remove", "PDSS", "lecture1.pdf", "-y"],
        ["config"],  # bare config = show
        ["config", "set", "chat.model", "llama-3.3-70b"],
    ],
)
def test_verb_registered_and_stubbed(args: list[str]) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 1, result.output
    assert "not implemented yet" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["init"],  # subject required
        ["index", "PDSS"],  # paths required
        ["remove", "PDSS"],  # material required
        ["config", "set", "chat.model"],  # value required
        ["ask", "PDSS", "q"],  # P3 verb must NOT exist yet
    ],
)
def test_bad_usage_is_usage_error(args: list[str]) -> None:
    result = runner.invoke(app, args)
    assert result.exit_code == 2, result.output
