"""groundly/core/subject.py — the subject workspace, and the one predicate its callers
must agree on.

`graph_is_built()` replaced a two-term check that was written out in several places in
slightly different shapes. These tests pin the two terms and the states between them,
because the failure they guard against is silent: a graph arm answering from a build
that never finished, under that arm's name.
"""

from groundly.core.subject import Subject


def test_graph_is_built_needs_both_the_directory_and_the_recorded_hash(subject):
    """Neither term alone. `corpus_hash` is written only by a build that passed every
    gate, and a refused or interrupted build deliberately leaves `graph/` behind so the
    retry keeps graphrag's paid-for cache — so the directory is not evidence."""
    subj = Subject(subject)
    assert subj.graph_is_built() is False  # fresh subject: neither

    (subj.root_dir / "graph").mkdir()
    assert subj.graph_is_built() is False, "a partial build is not a graph"

    manifest = subj.load_manifest()
    manifest.graphrag.corpus_hash = "deadbeef"
    subj.save_manifest(manifest)
    assert subj.graph_is_built() is True


def test_graph_is_built_survives_a_hash_with_no_directory(subject):
    """The other half of the partial state. `graph_is_stale` is what names a directory
    that went missing; this predicate just answers no."""
    subj = Subject(subject)
    manifest = subj.load_manifest()
    manifest.graphrag.corpus_hash = "deadbeef"
    subj.save_manifest(manifest)

    assert subj.graph_is_built() is False


def test_graph_is_built_on_a_subject_that_was_never_initialized(subject):
    """The `and` short-circuits before `load_manifest()`, which would otherwise raise
    FileNotFoundError. The eval preflight depends on this: it is handed a subject name,
    not a checked workspace."""
    assert Subject("never-created").graph_is_built() is False
