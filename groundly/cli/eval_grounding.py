"""Eval verb: `groundly eval-grounding SUBJECT` — the enforced-vs-host comparison
(docs/architecture/retrieval.md). A research verb like `eval`, not a student-facing one.

Its own verb rather than a flag on `eval` because the two sweeps share almost nothing:
different paths, different metrics, different provider requirements, different output
document. `eval` is retrieval-only and mostly zero-key; this one spends real money on
every question through two providers and a subprocess host.
"""

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.markup import escape
from rich.table import Table

from groundly.cli.app import _fail, _store_checked, _subject_checked, app, console


@app.command("eval-grounding")
def eval_grounding(
    subject: Annotated[str, typer.Argument(help="Subject to evaluate.")],
    gold: Annotated[
        Optional[Path],
        typer.Option(
            "--gold", "-g", help="Gold set JSONL (default: ./evals/<SUBJECT>/gold.jsonl)."
        ),
    ] = None,
    host_model: Annotated[
        str, typer.Option("--host-model", help="Model the MCP host runs on. Pinned, and recorded.")
    ] = "sonnet",
    claude_bin: Annotated[
        str, typer.Option("--claude-bin", help="Path to the `claude` CLI that drives path B.")
    ] = "claude",
    groundly_bin: Annotated[
        str, typer.Option("--groundly-bin", help="Path the host's MCP config spawns for `mcp`.")
    ] = "groundly",
    arm: Annotated[
        str, typer.Option("--arm", help="Retrieval arm for the enforced path.")
    ] = "vector",
    judge_runs: Annotated[
        int, typer.Option("--judge-runs", help="Judge passes per answer; 2 reports self-agreement.")
    ] = 2,
    out: Annotated[
        Optional[Path],
        typer.Option("--out", "-o", help="Results directory (default: the gold set's directory)."),
    ] = None,
    rerank: Annotated[
        bool, typer.Option("--rerank/--no-rerank", help="Cross-encoder rerank (default: on).")
    ] = True,
    timeout: Annotated[
        float, typer.Option("--host-timeout", help="Seconds before a host session is abandoned.")
    ] = 300.0,
    budget: Annotated[
        Optional[float],
        typer.Option(
            "--host-budget",
            help="Max USD per host session (enforced by the host). 0 disables the cap.",
        ),
    ] = 1.0,
    chat_model: Annotated[
        Optional[str],
        typer.Option(
            "--chat-model",
            help="Override [providers.chat]'s model for the enforced path. The sensitivity "
            "run: path A and the host use different models, so a host win cannot be told "
            "from a model-strength difference until A is re-run on a comparable one.",
        ),
    ] = None,
    no_host: Annotated[
        bool,
        typer.Option(
            "--no-host",
            help="Run the enforced path only, no host sessions. Makes the sensitivity run "
            "cost cents instead of re-buying every host session.",
        ),
    ] = False,
    conditions_spec: Annotated[
        str,
        typer.Option(
            "--conditions",
            help="Path-B conditions, comma-separated. `host`: neutral prompt, `search` "
            "only — the control for enforced `ask`. `host-directed`: prompt tells it to "
            "search, separating 'will not retrieve' from 'retrieves and then drifts'. "
            "`host-product`: neutral prompt over the full MCP surface — what a student "
            "actually runs. Each is a whole extra host session per question.",
        ),
    ] = "host",
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the cost confirmation.")] = False,
) -> None:
    """Compare enforced `ask` grounding against a real MCP host composing from `search`."""
    from groundly.core.config import load_provider
    from groundly.eval.gold import GoldSetError
    from groundly.eval.grounding import (
        AskConfig,
        HostConfig,
        resolve_conditions,
        run,
        write_results,
    )
    from groundly.retrieval.arms import validate_arms

    subj = _subject_checked(subject)
    store = _store_checked(subj)
    gold_path = gold if gold is not None else Path.cwd() / "evals" / subject / "gold.jsonl"

    try:
        validate_arms([arm], ranked_only=True)
    except ValueError as exc:
        _fail(str(exc))

    # Both providers are preflighted before any question runs. A sweep that dies at
    # question 30 because the judge was never configured has spent real money on 29
    # host sessions to learn a fact that was knowable up front.
    for call_class, why in (("chat", "the enforced path"), ("judge", "faithfulness scoring")):
        if load_provider(call_class) is None:
            _fail(
                f"no [providers.{call_class}] configured — {why} cannot run without it. "
                f"Set it with: groundly config set {call_class}.model <model>"
            )

    total = _gold_count(gold_path)
    if total == 0:
        _fail(f"no questions found at {gold_path}")

    # `--host-budget 0` is how the cap is turned off; None and 0.0 mean the same thing to
    # everything downstream, so they are collapsed here rather than in two places.
    budget = budget if budget else None
    try:
        conditions = resolve_conditions(conditions_spec)
    except ValueError as exc:
        _fail(str(exc))
    if no_host:
        # Path A alone. The comparison block comes out empty, which is honest: there is
        # nothing to compare against in this run, and the numbers are read against a
        # previous full sweep rather than within this file.
        conditions = ()

    # Long operations name their cost before spending it (.claude/rules/conventions.md).
    # This one is unusually easy to under-estimate: it is not 48 calls, it is 48 enforced
    # answers plus 48 whole agent sessions plus 2 judge passes over both paths' answers.
    console.print(f"Grounding-fidelity comparison on [bold]{subject}[/bold] from {gold_path}")
    on_model = f" using [bold]{chat_model}[/bold]" if chat_model else ""
    host_line = (
        f"  path B: {total * len(conditions)} cold [bold]{host_model}[/bold] host sessions "
        f"({len(conditions)} condition(s)), each free to search as often as it likes\n"
        if conditions
        else "  path B: [bold]skipped[/bold] (--no-host)\n"
    )
    console.print(
        f"  [bold]{total}[/bold] questions x {1 + len(conditions)} path(s)\n"
        f"  path A: {total} enforced `ask` calls on the [bold]{arm}[/bold] arm{on_model}\n"
        f"{host_line}"
        f"  judge:  up to {total * (1 + len(conditions)) * judge_runs} calls "
        f"({judge_runs} run(s) over every path's answers)"
    )
    # A ceiling, not a forecast. Path B is the one leg that can be bounded exactly, since
    # `--max-budget-usd` is enforced by the host itself; A and the judge are metered after
    # the fact. Printing a made-up total for all three would claim a precision this has no
    # way to have — the trap decision 28 recorded when a build was projected at $0.15-0.70
    # and cost $0.92.
    if budget is not None:
        console.print(
            f"  [bold]path B is capped at ${budget:.2f} per session[/bold] "
            f"— at most ${budget * total * len(conditions):,.2f} for the sweep."
        )
    else:
        console.print(
            "  [yellow]path B is uncapped[/yellow] — the host chooses how often to search "
            "and `search` has no k ceiling. Pass --host-budget to bound it."
        )
    console.print(
        "  [dim]paths A and judge are metered, not pre-priced: token counts are not "
        "knowable before the answers exist. Actual spend is written to the results "
        "file, split three ways.[/dim]"
    )
    console.print(
        "[yellow]Note:[/yellow] path B is a real MCP host. Its own system prompt is "
        "Anthropic's, is not publishable, and drifts between CLI versions — the run is "
        "re-runnable, not frozen. The results file records the CLI version and model id."
    )
    if not yes and not typer.confirm("Proceed?", default=False):
        raise typer.Exit(code=1)

    done = 0

    def _progress(question, path):
        nonlocal done
        done += 1
        label = f"[{done}] {question.id} · {path}"
        if console.is_terminal:
            status.update(f"[bold]{label}[/bold]")
        else:
            # The sweep runs for hours and is usually redirected to a file, where a
            # spinner writes nothing and a live run is indistinguishable from a hung one.
            console.print(label)

    with console.status("[bold]Running…", spinner="dots") as status:
        try:
            results = run(
                subject,
                gold_path,
                store,
                host=HostConfig(
                    model=host_model,
                    claude_bin=claude_bin,
                    groundly_bin=groundly_bin,
                    timeout_seconds=timeout,
                    max_budget_usd=budget,
                ),
                ask_config=AskConfig(arm=arm, rerank=rerank, model=chat_model),
                conditions=conditions,
                judge_runs=judge_runs,
                on_question=_progress,
            )
        except (GoldSetError, RuntimeError) as exc:
            _fail(str(exc))

    _report(results)
    path = write_results(results, out if out is not None else gold_path.parent)
    console.print(f"\nResults written to [bold]{path}[/bold]")


def _gold_count(gold_path: Path) -> int:
    """Cheap line count for the cost estimate. `run()` does the real parse moments later,
    so a wrong guess here costs nothing but a label."""
    if not gold_path.exists():
        return 0
    lines = gold_path.read_text(encoding="utf-8").splitlines()
    return sum(1 for ln in lines if ln.strip() and not ln.startswith("#"))


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def _report(results: dict) -> None:
    if results["partial"]:
        console.print(
            "[yellow]Interrupted[/yellow] — the results file is marked partial; do not "
            "report it as a full run."
        )
    if results["errors"]:
        failed = {r["error"] for r in results["rows"] if r["error"]}
        console.print(f"[red]{results['errors']} question-path runs failed[/red] and are excluded:")
        for message in sorted(failed)[:3]:
            console.print(f"  [dim]{escape(message[:300])}[/dim]")

    table = Table(
        title=f"{results['subject']} — grounding fidelity, {results['questions']} questions"
    )
    table.add_column("Path")
    table.add_column("n", justify="right")
    table.add_column("Err", justify="right")
    # Refusal sits immediately beside faithfulness, never under it: the enforced path can
    # win faithfulness purely by refusing more, and the two numbers are only readable
    # together.
    table.add_column("Faithful", justify="right")
    table.add_column("Refused", justify="right")
    # The two failure modes that used to be filed as harness errors and dropped. They are
    # each path's characteristic way of not answering, and faithfulness is not readable
    # without them: it is conditional on having produced a gradeable answer at all.
    table.add_column("Ungrounded", justify="right")
    table.add_column("No cite", justify="right")
    table.add_column("Supported", justify="right")
    table.add_column("Cites", justify="right")
    table.add_column("Resolvable", justify="right")
    table.add_column("Cited support", justify="right")
    table.add_column("Median ms", justify="right")
    for row in results["by_path"]:
        table.add_row(
            f"[bold]{row['slice']['path']}[/bold]",
            str(row["n"]),
            str(row["errors"]) if row["errors"] else "—",
            _pct(row["faithfulness"]),
            _pct(row["refusal_rate"]),
            _pct(row["ungrounded_rate"]),
            _pct(row["no_citation_rate"]),
            _pct(row["supported_rate"]),
            _pct(row["attribution_present_rate"]),
            _pct(row["attribution_resolvable_rate"]),
            _pct(row["cited_support_rate"]),
            "—" if row["median_latency_ms"] is None else str(row["median_latency_ms"]),
        )
    console.print(table)
    console.print(
        "[dim]Faithful is conditional on producing a gradeable answer — read it with "
        "Refused, Ungrounded and No cite beside it. Ungrounded = the host answered "
        "without retrieving anything; No cite = `ask` produced text whose citations "
        "resolved to nothing, so the pipeline refused it. Both count as losses in Full "
        "support.[/dim]"
    )

    agreement = results["judge_agreement"]
    if agreement is not None and agreement < 0.9:
        # Loud, because every faithfulness number above rests on this. Decision 28's
        # router figure was retracted for precisely this class of instability.
        console.print(
            f"[red]Judge self-agreement is {agreement:.0%}[/red] — it disagreed with "
            "itself on identical input. Treat the faithfulness columns as indicative "
            "only and check the judge's temperature and reasoning_effort."
        )
    elif agreement is not None:
        console.print(f"[dim]Judge self-agreement: {agreement:.0%}[/dim]")

    thin = False
    for label, comp in results["comparisons"].items():
        matched = comp["significance_matched"]
        console.print(
            f"\n[bold]ask vs {label}[/bold] — matched subset: {comp['matched_n']} of "
            f"{results['questions']} questions where the host saw everything `ask` saw."
        )
        console.print(
            f"  McNemar on supported: ask-only {matched['ask_only']}, "
            f"host-only {matched['host_only']}, p = {matched['p']:.3f} "
            f"({matched['n_pairs']} pairs)"
        )
        thin = thin or matched["n_pairs"] < 20
    if thin:
        console.print(
            "\n[yellow]Note:[/yellow] this gold set cannot resolve a difference much under "
            "10 questions. Report a consistent direction descriptively, not as a result."
        )
