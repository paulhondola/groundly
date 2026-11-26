"""One measured embed-bench run: bge-m3 and the reranker on one engine, over one subject.

`--engine torch` makes `mlx_available` false before any model loads, so the product's own
classes build FlagEmbedding models (a bench-side patch; Groundly has no engine setting).
Embedding is `encode_stream` over every chunk, as indexing calls it. Reranking scores each
query's BM25 pool, which no engine influences, so both arms score the same pairs. `search`
latency is `vector.search` per query after one warm-up search.

Writes facts (JSON) and outputs (npz) for the parent; course text goes in neither.
"""

import argparse
import json
import sqlite3
import time
from pathlib import Path

import numpy as np

SPARSE_TOP = 10


def _force_torch() -> None:
    from groundly.llm import embeddings, mlx_xlmr, rerank

    for module in (mlx_xlmr, embeddings, rerank):
        module.mlx_available = lambda: False  # pyrefly: ignore[bad-assignment]


def _device(engine: str) -> str:
    if engine == "mlx":
        return "mlx"
    from FlagEmbedding.abc.inference import AbsEmbedder

    # None asks FlagEmbedding to pick, as the product's models do
    return AbsEmbedder.get_target_devices(None)[0]  # pyrefly: ignore[bad-argument-type]


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m bench.embed.child")
    ap.add_argument("--engine", choices=["mlx", "torch"], required=True)
    ap.add_argument("--home", type=Path, required=True)
    ap.add_argument("--subject", required=True)
    ap.add_argument("--queries", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--facts", type=Path, required=True)
    args = ap.parse_args()
    if args.engine == "torch":
        _force_torch()

    from groundly.core.store import SubjectStore
    from groundly.core.subject import Subject
    from groundly.llm.embeddings import shared_embedder
    from groundly.llm.rerank import shared_reranker
    from groundly.retrieval import vector

    labelled = json.loads(args.queries.read_text())
    db = Subject(args.subject).store_db_path
    con = sqlite3.connect(db)
    chunks = con.execute("select id, text from chunks order by id").fetchall()
    con.close()
    embedder, reranker = shared_embedder(), shared_reranker()

    t = time.perf_counter()
    embedder.encode(["warm-up"])
    reranker.compute_score([("warm-up", "warm-up")])
    load_s = time.perf_counter() - t

    t = time.perf_counter()
    dense, sparse_ids = [], []
    for vec, weights in embedder.encode_stream([text for _, text in chunks]):
        dense.append(np.asarray(vec, dtype=np.float32))
        top = sorted(weights, key=weights.__getitem__, reverse=True)[:SPARSE_TOP]
        sparse_ids.append(top + [-1] * (SPARSE_TOP - len(top)))
    embed_s = time.perf_counter() - t

    store = SubjectStore(db)
    qids, cids, scores = [], [], []
    rerank_s = 0.0
    for i, lab in enumerate(labelled):
        pool = store.bm25_search(lab["query"], vector.RERANK_POOL)
        texts = {r["chunk_id"]: r["text"] for r in store.chunk_details(pool)}
        live = [c for c in pool if c in texts]
        t = time.perf_counter()
        got = reranker.compute_score([(lab["query"], texts[c]) for c in live]) if live else []
        rerank_s += time.perf_counter() - t
        qid = str(lab.get("id", i))
        qids += [qid] * len(live)
        cids += live
        scores += got

    vector.search(args.subject, labelled[0]["query"], k=8)  # warm the retriever path
    hits, latencies = [], []
    for lab in labelled:
        t = time.perf_counter()
        found = vector.search(args.subject, lab["query"], k=8)
        latencies.append((time.perf_counter() - t) * 1000)
        hits.append([(h.filename, h.page, h.score) for h in found])

    np.savez(
        args.out,
        chunk_ids=np.array([c for c, _ in chunks], dtype=np.int64),
        dense=np.stack(dense) if dense else np.zeros((0, 1024), np.float32),
        sparse_ids=np.array(sparse_ids, dtype=np.int64).reshape(-1, SPARSE_TOP),
        rerank_qids=np.array(qids, dtype=str),
        rerank_cids=np.array(cids, dtype=np.int64),
        rerank_scores=np.array(scores, dtype=np.float64),
    )
    args.facts.write_text(
        json.dumps(
            {
                "device": _device(args.engine),
                "load_s": load_s,
                "embed_s": embed_s,
                "rerank_s": rerank_s,
                "chunks": len(chunks),
                "pairs": len(cids),
                "latencies_ms": latencies,
                "hits": hits,
            }
        )
    )


if __name__ == "__main__":
    main()
