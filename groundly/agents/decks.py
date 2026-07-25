"""Deck building: the two doors through the one verifier gate (P6 slice 1 design
doc, docs/architecture/agents.md §2). `submit_cards` is that gate made literal —
the thin door (MCP `submit_cards`, host-generated, zero-key) and the thick door
(`generate_deck`'s loop) both call it; nothing else writes cards. Rejected cards
store nothing; every verdict is recorded in progress.db for the
rejection-rate-by-source measurement."""

import json
import time
from dataclasses import dataclass

from groundly.agents.prompts import assemble_cards, assemble_cards_retry
from groundly.agents.verifier import CardCandidate, Rejection, verify_card
from groundly.core.store import (
    SQLiteSubjectStore,
    connect_progress,
    record_trace,
    record_verification,
)
from groundly.core.subject import Subject
from groundly.retrieval.vector import VectorRetriever

GEN_CONTEXT_K = 20  # generating a batch needs more breadth than ask's context_k=8
MAX_RETRIES = 2  # per design: 1 + MAX_RETRIES chat calls per job, then drop
MAX_COUNT = 50  # cards per generate_deck call


@dataclass
class CardOutcome:
    index: int
    accepted: bool
    question_id: int | None = None
    rejection: Rejection | None = None


def submit_cards(
    subject: str,
    deck: str,
    cards: list[CardCandidate],
    *,
    generation_source: str,
    embedder=None,
) -> list[CardOutcome]:
    """Verify every card and store the ones that pass into `deck`. Zero-key: the
    verifier touches only local bge-m3 (lazily), never a provider."""
    subj = Subject(subject)
    store = SQLiteSubjectStore(subj.store_db_path)
    deck_id = store.get_or_create_deck(deck)
    progress_conn = connect_progress(subj.progress_db_path)

    outcomes: list[CardOutcome] = []
    try:
        for i, card in enumerate(cards):
            rejection = verify_card(card, store, embedder=embedder)
            if rejection is None:
                question_id = store.add_verified_card(
                    deck_id, card.front, card.back, card.chunk_ids, generation_source
                )
                outcomes.append(CardOutcome(index=i, accepted=True, question_id=question_id))
            else:
                outcomes.append(CardOutcome(index=i, accepted=False, rejection=rejection))
            record_verification(
                progress_conn,
                generation_source=generation_source,
                reason=None if rejection is None else rejection.reason,
            )
    finally:
        progress_conn.close()
    return outcomes


def estimate_generation(count: int) -> dict:
    """Constants-only cost heuristic — no retrieval, no model load, nothing spent.
    The MCP reading of "print cost estimates before spending" (conventions.md):
    generate_deck(confirm=false) returns this; confirm=true starts the job."""
    from groundly.core.config import load_provider

    prompt_tokens = GEN_CONTEXT_K * 512 + 400  # context chunks (≤512 tok each) + rules
    output_tokens = count * 80  # ~80 tokens per generated card
    cfg = load_provider("generation")
    if cfg and cfg.input_price_per_mtok is not None and cfg.output_price_per_mtok is not None:
        cost = (
            prompt_tokens * cfg.input_price_per_mtok + output_tokens * cfg.output_price_per_mtok
        ) / 1_000_000
        note = "call again with confirm=true to start the generation job"
    else:
        cost = None
        note = (
            "no cost estimate available — the generation provider has no price "
            "configured and isn't in the local price map; call again with "
            "confirm=true to proceed anyway"
        )
    return {
        "estimated_tokens": prompt_tokens + output_tokens,
        "estimated_cost_usd": cost,
        "note": note,
    }


def _parse_cards(text: str) -> list[CardCandidate] | None:
    """Model reply -> card candidates. Tolerant of code fences/prose around the JSON
    array (first '[' to last ']'); None = unparseable (burns one loop round)."""
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    cards: list[CardCandidate] = []
    for item in data:
        if not isinstance(item, dict):
            return None
        front, back, chunk_ids = item.get("front"), item.get("back"), item.get("chunk_ids")
        if (
            not isinstance(front, str)
            or not isinstance(back, str)
            or not isinstance(chunk_ids, list)
            or not all(isinstance(cid, int) for cid in chunk_ids)
        ):
            return None
        cards.append(CardCandidate(front=front, back=back, chunk_ids=chunk_ids))
    return cards


def generate_deck_job(
    subject: str,
    topic: str,
    deck: str,
    count: int,
    *,
    chat=None,
    embedder=None,
) -> dict:
    """The thick door's job body (runs on a jobs.py thread): retrieve topic context
    once, generate the batch, gate every card through submit_cards(source='server'),
    feed rejections back for regeneration — max MAX_RETRIES retry rounds, then drop
    with a batch-report note. Bounded: at most 1 + MAX_RETRIES chat calls."""
    if chat is None:
        from groundly.llm.chat import complete as chat  # noqa: PLW0127 — lazy, tests inject

    subj = Subject(subject)
    store = SQLiteSubjectStore(subj.store_db_path)

    start = time.monotonic()
    tokens_total = 0
    cost_total: float | None = 0.0
    model: str | None = None
    context_ids: list[int] = []
    outcome = "error"
    error: str | None = None
    accepted_total = 0
    dropped: list[dict] = []

    try:
        retriever = VectorRetriever(store, embedder=embedder, rerank=False, context_k=GEN_CONTEXT_K)
        nodes = retriever.retrieve(topic)
        context_ids = [n.node.metadata["chunk_id"] for n in nodes]
        if not nodes:
            raise RuntimeError(
                f"no course material found for topic {topic!r} — nothing to generate from"
            )

        messages = assemble_cards(topic, count, nodes)
        rejected: list[tuple[CardCandidate, Rejection]] = []
        parsed_any = False
        rounds = 0
        while rounds < 1 + MAX_RETRIES:
            rounds += 1
            result = chat("generation", messages)
            tokens_total += result.tokens
            model = result.model
            if cost_total is not None:
                cost_total = None if result.cost_usd is None else cost_total + result.cost_usd

            candidates = _parse_cards(result.text)
            if candidates is None:
                continue  # unparseable burns this round; same messages go again
            parsed_any = True

            outcomes = submit_cards(
                subject, deck, candidates, generation_source="server", embedder=embedder
            )
            accepted_total += sum(1 for o in outcomes if o.accepted)
            rejected = [(candidates[o.index], o.rejection) for o in outcomes if not o.accepted]
            if not rejected:
                break
            messages = assemble_cards_retry(topic, rejected, nodes)

        if not parsed_any:
            raise RuntimeError(
                f"the generation model returned unparseable output in all {rounds} "
                "rounds — no cards were stored; try a different generation model"
            )

        dropped = [
            {"front": card.front, "reason": rej.reason, "detail": rej.detail, "attempts": rounds}
            for card, rej in rejected
        ]
        outcome = "answered"
        return {
            "deck": deck,
            "requested": count,
            "accepted": accepted_total,
            "dropped": dropped,
            "tokens": tokens_total,
            "cost_usd": cost_total,
        }
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        latency_ms = int((time.monotonic() - start) * 1000)
        progress_conn = connect_progress(subj.progress_db_path)
        try:
            record_trace(
                progress_conn,
                kind="ask",  # P5 precedent: existing progress.dbs' CHECK can't grow cheaply
                query=topic,
                arm="generate_deck",
                chunk_ids=context_ids or None,
                outcome=outcome,
                model=model,
                tokens=tokens_total or None,
                cost_usd=cost_total,
                latency_ms=latency_ms,
                error=error,
            )
        finally:
            progress_conn.close()
