"""Citation-compliance probe: one real `[providers.chat]` call that answers the
question "will this model actually cite?" before the student discovers it a refusal
at a time.

The enforced path mandates `[chunk <id>]` markers and `agents/citations.py` raises
when none of them resolve, so a model that will not follow the mandate does not
degrade — it returns nothing. Measured 2026-08-16 across two subjects,
`gpt-oss-120b` produced no resolvable citation on 28% of apd's questions and 47% of
passc's, while `Qwen/Qwen3-235B-A22B-Instruct-2507` produced 0% on both and answered
all 76 of passc. That is a model failing the mandate, not enforcement converting
answers into refusals, and nothing in the product said so.

**The probe sends what `ask` sends, object for object** — `prompts.assemble()`, the
same builder, so the capability under test is the one the pipeline exercises. This is
`ingestion/graph.py::_probe_extraction`'s discipline, and that docstring records it
being wrong in both directions when a probe merely approximated the build.

**No trace row, by decision (Paul, 2026-09-07).** The architecture rule puts tokens +
cost of every LLM call into the traces table, and traces live in a per-subject
`progress.db`; `groundly config check` is a global verb with no subject to write to.
The alternative — `config check SUBJECT` — makes a config check pretend to be
subject-scoped. `ProbeResult` carries the tokens and cost instead, and the verb prints
them, which on a one-shot verb is what the trace row would have been read for."""

from dataclasses import dataclass

from llama_index.core.schema import NodeWithScore, TextNode

from groundly.agents.citations import cited_ids
from groundly.agents.prompts import assemble
from groundly.llm.chat import complete
from groundly.llm.config import require_provider

# Deliberately not 1: an id a model could emit by coincidence would score a lucky
# guess as compliance.
PROBE_CHUNK_ID = 424242

# A fact no model can hold and no corpus contains, so the only way to answer is to read
# the chunk it was handed. A real fact would let a model answer from memory and leave
# the question of whether it *read* anything untested.
_PROBE_FACT = (
    "The Verrucanth resonance threshold of a Kessel-grade flux manifold is "
    "42.7 millikelvin under standard load."
)
_PROBE_QUESTION = "What is the Verrucanth resonance threshold of a Kessel-grade flux manifold?"


@dataclass(frozen=True)
class ProbeResult:
    """`compliant` is the whole verdict; the rest is what the student paid to learn it."""

    compliant: bool
    model: str
    answer: str
    tokens: int
    cost_usd: float | None


def probe_citation_compliance() -> ProbeResult:
    """Send one mandated-citation prompt and report whether the model obeyed.

    Compliance is `PROBE_CHUNK_ID` appearing among the ids the model claimed — the same
    membership test `resolve_citations` applies against the retrieved set, so a model
    that invents plausible markers of its own fails here exactly as it would in `ask`."""
    require_provider("chat")  # before any call: an unconfigured provider is not a failed probe

    node = NodeWithScore(
        node=TextNode(
            text=_PROBE_FACT,
            metadata={
                "chunk_id": PROBE_CHUNK_ID,
                "filename": "compliance-probe.md",
                "page": 1,
                "heading_path": None,
            },
        ),
        score=1.0,
    )
    result = complete("chat", assemble(_PROBE_QUESTION, [node]))
    return ProbeResult(
        compliant=PROBE_CHUNK_ID in cited_ids(result.text),
        model=result.model,
        answer=result.text,
        tokens=result.tokens,
        cost_usd=result.cost_usd,
    )
