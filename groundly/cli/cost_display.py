from rich.markup import escape

from groundly.cli.app import console


def _usd(amount: float) -> str:
    """Two decimals reads as money; below a cent it reads as zero, which is worse than
    verbose. No four-decimal figures — this is a heuristic, and printing it to a
    hundredth of a cent claims a precision it does not have."""
    return f"${amount:,.2f}" if amount >= 1 else f"${amount:.3f}"


def _print_cost_estimate(est) -> None:
    """The spend gate (conventions.md: print cost estimates before spending the
    student's tokens). A range, and every assumption behind it named — the previous
    single figure priced input tokens for the extraction pass only and said so nowhere,
    which presented a build as costing a fraction of what it did."""
    console.print(
        f"Estimated graph build: ~{est.input_tokens:,} input tokens, "
        f"up to ~{est.max_output_tokens:,} output"
    )
    if est.low_usd is None:
        console.print(
            "[dim]  no cost estimate available — set input_price_per_mtok and "
            f"output_price_per_mtok for {escape('[providers.extraction]')} in "
            "config.toml to see one[/dim]"
        )
    else:
        console.print(f"  [bold]{_usd(est.low_usd)} to {_usd(est.high_usd)}[/bold]")
        console.print(f"[dim]  prices: {escape(est.price_source)}[/dim]")
    console.print(
        "[dim]  extraction pass only — community reports and description summaries are "
        "billed on top, and cannot be sized before the graph exists[/dim]"
    )
    if est.report_call_class:
        # With one provider, "billed on top" is a caveat. With two it is a hole: the
        # split exists so extraction can run somewhere cheap or free, which means the
        # money is all on the *other* provider and none of it is in the range above.
        # Saying "billed on top" without saying "on a provider this figure never
        # priced" would be technically true and practically a lie.
        console.print(
            f"[yellow]  ⚠ community reports run on {escape(f'[providers.{est.report_call_class}]')}"
            "[/yellow] — a different provider, whose cost is not included above at all."
        )
    if est.moving_alias:
        console.print(
            f"[yellow]  ⚠ {escape(est.moving_alias)} is a moving alias[/yellow] — it may now "
            "point at a differently-priced model than the one priced above."
        )


def _print_actual_spend(result) -> None:
    """What the build actually cost, metered by graphrag's own usage aggregates rather
    than re-derived from the estimate. Absent when nothing was metered — a missing
    number is not worth a warning."""
    if result.prompt_tokens is None or result.completion_tokens is None:
        return
    line = f"[dim]  metered: {result.prompt_tokens:,} in / {result.completion_tokens:,} out"
    if result.cost_usd is not None:
        line += f" — {_usd(result.cost_usd)}"
    console.print(f"{line}[/dim]")
