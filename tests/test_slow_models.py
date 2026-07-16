"""Real-model checks — excluded by default (pyproject addopts); run: pytest -m slow.
First run downloads bge-m3 (~2.3 GB) to the HF cache."""

import pytest

pytestmark = pytest.mark.slow


def test_bge_m3_dense_and_sparse_contract():
    from unilearn.core.manifest import EMBEDDING_DIM
    from unilearn.llm.embeddings import BgeM3Embedder

    dense, sparse = BgeM3Embedder().encode(["mutual exclusion in distributed systems"])
    assert len(dense) == len(sparse) == 1
    assert len(dense[0]) == EMBEDDING_DIM
    norm_sq = sum(x * x for x in dense[0])
    assert abs(norm_sq - 1.0) < 1e-3  # manifest contract: normalized
    assert sparse[0], "learned sparse weights must be non-empty"
    assert all(isinstance(t, int) and w > 0 for t, w in sparse[0].items())
