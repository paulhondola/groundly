"""The arm table (groundly/retrieval/arms.py) — the single inventory of which retrieval
arms exist and what each one is.

`ARMS`, `PRODUCT_ARMS` and `UNRANKED_ARMS` used to be three hand-maintained string
collections next to an if/elif dispatch. These tests exist because the failure mode of
that arrangement was silent: a table and a dispatch disagreeing produces an eval that
scores the wrong arm under the right name.
"""

import pytest
from llama_index.core.retrievers import BaseRetriever

from groundly.retrieval.arms import (
    ARM_TABLE,
    ARMS,
    PRODUCT_ARMS,
    UNRANKED_ARMS,
    ArmContext,
    retrieve_for_arm,
)


def test_the_table_is_the_only_source_of_the_derived_views():
    """Each view is exactly what the table says, so adding an arm cannot leave one of
    them behind."""
    assert ARMS == tuple(n for n, a in ARM_TABLE.items() if a.build is not None)
    assert PRODUCT_ARMS == tuple(n for n, a in ARM_TABLE.items() if a.product)
    assert UNRANKED_ARMS == frozenset(n for n, a in ARM_TABLE.items() if not a.ranked)


def test_every_entry_is_keyed_by_its_own_name():
    """A key and a `name` that disagree would make error messages point at an arm that
    is not the one that ran."""
    assert all(key == arm.name for key, arm in ARM_TABLE.items())


def test_product_arms_is_a_strict_subset_of_arms():
    """`ARMS` is what the eval may score; `PRODUCT_ARMS` is what a user question may
    reach. Strict, because the day they are equal the decision-28 retirement is undone."""
    assert set(PRODUCT_ARMS) < set(ARMS)
    assert set(PRODUCT_ARMS) == {"vector"}


def test_graph_global_is_the_only_unranked_arm():
    """It ends its citation join with `sorted(chunk_ids)` — ascending SQLite rowid. An
    MRR over that measures corpus layout, not retrieval."""
    assert UNRANKED_ARMS == {"graph-global"}


def test_all_four_documented_arms_are_present():
    """docs/architecture/retrieval.md describes four arms. The table names all four,
    including the one with no implementation — a three-entry table would quietly
    contradict the architecture doc."""
    assert set(ARM_TABLE) == {"vector", "hybrid-local", "graph-global", "adaptive"}


def test_adaptive_is_declared_but_not_scoreable():
    """Arm 4 has an interface stub and no implementation. It belongs in the inventory
    and must not be offered to `groundly eval --arms`."""
    assert "adaptive" in ARM_TABLE
    assert ARM_TABLE["adaptive"].build is None
    assert "adaptive" not in ARMS


def test_an_unimplemented_arm_is_refused_as_such_not_as_unknown(retrievable_subject):
    """Two different mistakes deserve two different messages: "unknown arm" sends
    someone hunting a typo they did not make. This also has to raise up front rather
    than per question — the eval's per-question error tolerance would otherwise absorb
    it once per gold item and report a broken run as a flaky one."""
    from groundly.core.paths import subject_dir
    from groundly.core.store import SubjectStore

    store = SubjectStore(subject_dir(retrievable_subject) / "store.db")
    with pytest.raises(ValueError, match="declared but not implemented"):
        retrieve_for_arm(retrievable_subject, "q", "adaptive", store=store)


def test_validate_arms_distinguishes_the_two_mistakes():
    """`retrieve_for_arm`'s message was unreachable from the only surface that takes
    `--arms`: the CLI screened the list against `ARMS` first and said "unknown arm".
    `validate_arms` is the shared screen, so both surfaces say the same thing."""
    from groundly.retrieval.arms import validate_arms

    validate_arms(["vector", "hybrid-local"])  # implemented arms pass
    with pytest.raises(ValueError, match="declared but not implemented"):
        validate_arms(["vector", "adaptive"])
    with pytest.raises(ValueError, match="unknown retrieval arm"):
        validate_arms(["vector", "graph-locul"])


def test_unknown_arm_is_still_refused_as_unknown(retrievable_subject):
    from groundly.core.paths import subject_dir
    from groundly.core.store import SubjectStore

    store = SubjectStore(subject_dir(retrievable_subject) / "store.db")
    with pytest.raises(ValueError, match="unknown retrieval arm"):
        retrieve_for_arm(retrievable_subject, "q", "graph-locul", store=store)


def test_every_implemented_arm_builds_a_retriever(retrievable_subject):
    """The "four arms, one interface" gate, asserted on construction. Arm 3 was an
    `elif` branch in the agents layer until this table existed, so it satisfied the
    claim in the docs and not in the code."""
    from groundly.core.paths import subject_dir
    from groundly.core.store import SubjectStore

    store = SubjectStore(subject_dir(retrievable_subject) / "store.db")
    ctx = ArmContext(
        subject=retrievable_subject, store=store, rerank=False, embedder=None, reranker=None
    )
    for name in ARMS:
        assert isinstance(ARM_TABLE[name].build(ctx), BaseRetriever), name
