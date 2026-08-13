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
    at_k: Annotated[
        Optional[str],
        typer.Option("--at-k", help="Comma-separated cutoffs for the set-size-matched table."),
    ] = None,
) -> None:
    """Score retrieval arms against a gold set. The vector arm needs no provider."""
    from groundly.eval.gold import GoldSetError
    from groundly.eval.runner import DEFAULT_AT_K, run, write_results
    from groundly.retrieval.arms import validate_arms
    from groundly.retrieval.graph import GraphNotBuiltError

    if at_k is None:
        ks = DEFAULT_AT_K
    else:
        try:
            ks = tuple(int(x) for x in at_k.split(",") if x.strip())
        except ValueError:
            _fail(f"--at-k must be comma-separated integers, got {at_k!r}")
        if not ks or any(k < 1 for k in ks):
            _fail("--at-k needs at least one cutoff, all >= 1")

    subj = _subject_checked(subject)
    store = _store_checked(subj)

    gold_path = gold if gold is not None else Path.cwd() / "evals" / subject / "gold.jsonl"
    arm_list = [a.strip() for a in arms.split(",") if a.strip()]
    # Shared with `eval.runner.run` so the two cannot drift: this screens first, so a
    # message that lived only in the runner would never reach a user.
    try:
        validate_arms(arm_list)
    except ValueError as exc:
        _fail(str(exc))

    # Models load lazily on first retrieve; warn before the wait rather than after it.
    console.print(f"Scoring {len(arm_list)} arm(s) on [bold]{subject}[/bold] from {gold_path}")
    # Long operations name their cost before spending it (.claude/rules/conventions.md),
    # and the two graph arms cost wildly different amounts — collapsing them into one
    # sentence understates global search by more than an order of magnitude, the exact
    # "a believed wrong number is worse than no number" trap of decision 23.
    if "hybrid-local" in arm_list:
        console.print(
            "[dim]'hybrid-local' is zero-key: graphrag's local search is stopped at "
            "`on_context`, before the synthesis Groundly would discard anyway.[/dim]"
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
                at_k=ks,
            )
        except (GoldSetError, GraphNotBuiltError) as exc:
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
            "—" if row["mrr"] is None else f"{row['mrr']:.2f}",
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
            "—" if row["mrr"] is None else f"[bold]{row['mrr']:.2f}[/bold]",
            f"[bold]{row['median_retrieved_n']}[/bold]",
            f"[bold]{row['leakage']:.0%}[/bold]",
            "—" if row["median_latency_ms"] is None else f"[bold]{row['median_latency_ms']}[/bold]",
        )
    console.print(table)

    unranked = sorted({r["slice"]["arm"] for r in results["by_arm"] if r["mrr"] is None})
    if unranked:
        console.print(
            f"[yellow]Note:[/yellow] MRR is '—' for {', '.join(unranked)}: the arm returns "
            "chunks in index order, not relevance order, so a rank metric over it would "
            "measure how the corpus was indexed. Hit rate and recall stay valid."
        )

    # Arms that return wildly different set sizes are not comparable on hit rate or
    # recall — measured on apd, graph-global returns 95% of the corpus every time.
    sizes = {r["slice"]["arm"]: r["median_retrieved_n"] for r in results["by_arm"]}
    if len(sizes) > 1 and max(sizes.values()) > 4 * min(sizes.values()):
        widest = max(sizes, key=sizes.get)
        # Pointing at MRR is only useful advice when the widest arm actually has one.
        remedy = (
            "read MRR instead"
            if widest not in unranked
            else f"and '{widest}' has no rank metric either — it has no comparable quality number"
        )
        console.print(
            f"[yellow]Note:[/yellow] '{widest}' returns {sizes[widest]} chunks per question "
            f"vs {min(sizes.values())} for the narrowest arm. Hit rate and recall are not "
            f"comparable across arms at these set sizes — {remedy}."
        )

    # The set-size-matched table. The one above compares arms at whatever set size each
    # happened to return; this one compares them at the same k, which is the only form in
    # which hit rate and recall mean the same thing across arms.
    base_rate = results["leakage_base_rate"]
    at_k_table = Table(title=f"{subject} — matched cutoffs (leakage base rate {base_rate:.1%})")
    at_k_table.add_column("k", justify="right")
    at_k_table.add_column("Arm")
    at_k_table.add_column("Hit rate", justify="right")
    at_k_table.add_column("Recall", justify="right")
    at_k_table.add_column("MRR", justify="right")
    at_k_table.add_column("Chunks", justify="right")
    at_k_table.add_column("Leak x base", justify="right")
    for row in results["at_k"]:
        enrichment = row["leakage"] / base_rate if base_rate else None
        at_k_table.add_row(
            row["slice"]["k"],
            row["slice"]["arm"],
            f"{row['hit_rate']:.0%}",
            f"{row['recall']:.2f}",
            "—" if row["mrr"] is None else f"{row['mrr']:.2f}",
            str(row["median_retrieved_n"]),
            "—" if enrichment is None else f"{enrichment:.1f}x",
        )
    console.print(at_k_table)
    if results["at_k_excluded_arms"]:
        console.print(
            f"[yellow]Note:[/yellow] {', '.join(results['at_k_excluded_arms'])} "
            "is absent from the table above and from the significance test: it returns "
            "chunks in index order, so its 'top k' would be the k lowest-numbered chunks "
            "in the corpus. A cutoff needs an order that means something."
        )
    console.print(
        "[dim]Leak x base = leakage / the corpus share that is question-source material. "
        "1.0x means the arm retrieves exam text at the rate it appears in the corpus — no "
        "signal. Raw leakage alone makes an arm that returns everything look cleanest.[/dim]"
    )

    # A per-question delta is not a result until it survives a paired test. The published
    # "net -3" was 5 discordant pairs, p = 0.375 — indistinguishable from a tie.
    if results["significance"]:
        sig_table = Table(
            title=f"paired significance vs {results['significance'][0]['baseline']} "
            f"(exact McNemar, n = {results['questions']})"
        )
        sig_table.add_column("k", justify="right")
        sig_table.add_column("Arm")
        sig_table.add_column("Arm only", justify="right")
        sig_table.add_column("Baseline only", justify="right")
        sig_table.add_column("p", justify="right")
        sig_table.add_column("Verdict")
        for sig in results["significance"]:
            sig_table.add_row(
                str(sig["k"]),
                sig["arm"],
                str(sig["arm_only"]),
                str(sig["baseline_only"]),
                f"{sig['p']:.3f}",
                "significant" if sig["p"] < 0.05 else "not resolvable",
            )
        console.print(sig_table)
        console.print(
            "[dim]Computed at each matched cutoff, not at the arms' natural set sizes — "
            "testing a 42-chunk arm against a 20-chunk one would re-introduce the exact "
            "confound the matched table removes.[/dim]"
        )

    if not results["latency_comparable"]:
        console.print(
            "[yellow]Note:[/yellow] latency above is NOT comparable across arms — a "
            "resident local model slows every other arm on the same machine (measured "
            "5.4x on the vector arm). Re-run each arm on its own to compare timings."
        )

    path = write_results(results, out if out is not None else gold_path.parent)
    console.print(f"Wrote {path}")
    # Leakage is the number that decides whether any of the above can be believed.
    console.print(
        "[dim]Leakage = share of retrieved chunks from any exam/quiz file the gold "
        "questions were drawn from. High leakage means an arm matched question text, "
        "not the material that answers it.[/dim]"
    )
