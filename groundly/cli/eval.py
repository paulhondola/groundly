"""Eval verb: `groundly eval SUBJECT --gold PATH [--arms ...]` — scores the retrieval
arms against a labelled gold set and prints the per-arm table. The `vector` arm runs
fully offline; graph arms need the `extraction` provider (graphrag synthesises inside
its own search call). The harness is `groundly/eval/`."""

from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.markup import escape
from rich.table import Table

from groundly.cli.app import _fail, _store_checked, _subject_checked, app, console

_DEFAULT_ARMS = "vector,hybrid-local,graph-global"


@app.command("eval")
def eval_(
    subject: Annotated[str, typer.Argument(help="Subject to evaluate.")],
    gold: Annotated[
        Optional[Path],
        typer.Option(
            "--gold", "-g", help="Gold set JSONL (default: ./evals/<SUBJECT>/gold.jsonl)."
        ),
    ] = None,
    arms: Annotated[
        str, typer.Option("--arms", help="Comma-separated retrieval arms to score.")
    ] = _DEFAULT_ARMS,
    out: Annotated[
        Optional[Path],
        typer.Option("--out", "-o", help="Results directory (default: the gold set's directory)."),
    ] = None,
    rerank: Annotated[
        bool, typer.Option("--rerank/--no-rerank", help="Cross-encoder rerank (default: on).")
    ] = True,
) -> None:
    """Score retrieval arms against a gold set. The vector arm needs no provider."""
    from groundly.agents.ask import ARMS
    from groundly.eval.gold import GoldSetError
    from groundly.eval.runner import ArmDegradedError, run, write_results

    subj = _subject_checked(subject)
    store = _store_checked(subj)

    gold_path = gold if gold is not None else Path.cwd() / "evals" / subject / "gold.jsonl"
    arm_list = [a.strip() for a in arms.split(",") if a.strip()]
    unknown = [a for a in arm_list if a not in ARMS]
    if unknown:
        _fail(f"unknown arm(s): {', '.join(unknown)} — expected from: {', '.join(ARMS)}")

    # Models load lazily on first retrieve; warn before the wait rather than after it.
    console.print(f"Scoring {len(arm_list)} arm(s) on [bold]{subject}[/bold] from {gold_path}")
    # Long operations name their cost before spending it (.claude/rules/conventions.md),
    # and the two graph arms cost wildly different amounts — collapsing them into one
    # sentence understates global search by more than an order of magnitude, the exact
    # "a believed wrong number is worse than no number" trap of decision 23.
    if "hybrid-local" in arm_list:
        console.print(
            "[yellow]Note:[/yellow] 'hybrid-local' calls the extraction provider about "
            "once per question, inside graphrag. That spend reaches no trace row."
        )
    if "graph-global" in arm_list:
        console.print(
            "[yellow]Note:[/yellow] 'graph-global' is map-reduce over community "
            "summaries — [bold]tens of extraction calls per question[/bold], not one "
            "(measured ~33 on a 555-report graph). None of it reaches a trace row."
        )

    # Cheap line count for the progress denominator — `run()` does the real parse and
    # validation moments later, so a wrong guess here costs nothing but a label.
    total = 0
    if gold_path.exists():
        lines = gold_path.read_text(encoding="utf-8").splitlines()
        total = sum(1 for ln in lines if ln.strip() and not ln.startswith("#")) * len(arm_list)
    done = 0

    with console.status("[bold]Running…", spinner="dots") as status:

        def _progress(question, arm):
            nonlocal done
            done += 1
            label = f"[{done}/{total}] {question.id} · {arm}" if total else f"{question.id} · {arm}"
            if console.is_terminal:
                status.update(f"[bold]{label}[/bold]")
            else:
                # A graph sweep runs for hours, usually redirected to a file. A spinner
                # writes nothing there, leaving no way to tell a live run from a hung one.
                console.print(label)

        try:
            results = run(
                subject,
                gold_path,
                store,
                arms=arm_list,
                rerank=rerank,
                on_question=_progress,
            )
        except (GoldSetError, ArmDegradedError) as exc:
            _fail(str(exc))

    for warning in results["warnings"]:
        console.print(f"[yellow]Warning:[/yellow] {warning}")

    if results["partial"]:
        console.print(
            "[yellow]Interrupted[/yellow] — scores below cover only the questions that "
            "ran. The results file is marked partial; do not report it as a full run."
        )

    if results["errors"]:
        # Errored questions are excluded from the quality columns, so the table below
        # would otherwise look healthy while resting on a fraction of the gold set.
        failed = {r["error"] for r in results["rows"] if r["error"]}
        console.print(
            f"[red]{results['errors']} of {results['questions'] * len(arm_list)} "
            "question-arm runs failed[/red] and are excluded from the scores below:"
        )
        for message in sorted(failed)[:3]:
            console.print(f"  [dim]{escape(message[:300])}[/dim]")

    table = Table(title=f"{subject} — retrieval, {results['questions']} questions")
    table.add_column("Arm")
    table.add_column("Class")
    table.add_column("n", justify="right")
    table.add_column("Err", justify="right")
    table.add_column("Hit rate", justify="right")
    table.add_column("Recall", justify="right")
    table.add_column("MRR", justify="right")
    table.add_column("Chunks", justify="right")
    table.add_column("Leakage", justify="right")
    table.add_column("Median ms", justify="right")
    for row in results["by_arm_class"]:
        table.add_row(
            row["slice"]["arm"],
            row["slice"]["klass"],
            str(row["n"]),
            str(row["errors"]) if row["errors"] else "—",
            f"{row['hit_rate']:.0%}",
            f"{row['recall']:.2f}",
            f"{row['mrr']:.2f}",
            str(row["median_retrieved_n"]),
            f"{row['leakage']:.0%}",
            "—" if row["median_latency_ms"] is None else str(row["median_latency_ms"]),
        )
    for row in results["by_arm"]:
        table.add_row(
            f"[bold]{row['slice']['arm']}[/bold]",
            "[bold]all[/bold]",
            f"[bold]{row['n']}[/bold]",
            f"[bold]{row['errors']}[/bold]" if row["errors"] else "—",
            f"[bold]{row['hit_rate']:.0%}[/bold]",
            f"[bold]{row['recall']:.2f}[/bold]",
            f"[bold]{row['mrr']:.2f}[/bold]",
            f"[bold]{row['median_retrieved_n']}[/bold]",
            f"[bold]{row['leakage']:.0%}[/bold]",
            "—" if row["median_latency_ms"] is None else f"[bold]{row['median_latency_ms']}[/bold]",
        )
    console.print(table)

    # Arms that return wildly different set sizes are not comparable on hit rate or
    # recall — measured on apd, graph-global returns 95% of the corpus every time.
    sizes = {r["slice"]["arm"]: r["median_retrieved_n"] for r in results["by_arm"]}
    if len(sizes) > 1 and max(sizes.values()) > 4 * min(sizes.values()):
        widest = max(sizes, key=sizes.get)
        console.print(
            f"[yellow]Note:[/yellow] '{widest}' returns {sizes[widest]} chunks per question "
            f"vs {min(sizes.values())} for the narrowest arm. Hit rate and recall are not "
            "comparable across arms at these set sizes — read MRR instead."
        )

    path = write_results(results, out if out is not None else gold_path.parent)
    console.print(f"Wrote {path}")
    # Leakage is the number that decides whether any of the above can be believed.
    console.print(
        "[dim]Leakage = share of retrieved chunks from the question's own exam/quiz file. "
        "High leakage means an arm matched the question text, not the material.[/dim]"
    )
