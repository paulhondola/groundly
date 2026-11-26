# In-repo OCR bench — short design

**Intent.** Run the OCR benchmarks from Groundly's own checkout on `main`, not from a long-lived `bench-suite` branch in a second worktree. Why (Paul, 2026-11-26): the branch drifts from `main` (36 ahead, 14 behind when this was written) and every product fix has to be carried across by merge. The thesis figures also need the bench to keep running against current product code.

**Goals**
- `bench/` and `tests/bench/` live on `main`.
- `uv sync --extra dev --group bench` sets the bench up; `uv run python -m bench.{extract,index,retrieval}.run` runs it from the main checkout.
- The fast bench tests run in the default `pytest` and in CI; slow ones stay behind `-m slow`.

**Non-goals**
- No product flag and no reader switch in `groundly/`. The bench already swaps the extraction worker by handing `SubprocessExtractor` its own argv (`bench/index/run.py:worker_argv`).
- No change to decision 49.
- No results or reference text in git.

**Anti-goals** (failure even if it works)
- MinerU or mlx-vlm installable through `groundly[...]`, or present in a plain install.
- CI needing the bench group.

**Constraints**
- Bench-only dependencies sit in a PEP 735 `[dependency-groups]` entry, which is never published. Today `bench` is an optional extra, so it appears in the wheel's metadata as `groundly[bench]`. Moving it is part of this change.
- The `opencv-python` override stays in the root `[tool.uv]`. uv has one lock per project, and RapidOCR's `opencv-python-headless` must not be overwritten.
- The wheel keeps `packages = ["groundly"]`.
- `bench/results/` and `bench/reference/` stay gitignored: both hold course text (trust layer 3).

**Approaches**
- **A. Dependency group at the root** (picked by Paul). `bench/` stays a top-level package and its dependencies go in a `bench` group. This costs almost nothing, because it is how `bench-suite` is already built.
- **B. uv workspace member.** `bench/pyproject.toml` depending on `groundly`. It gives a cleaner boundary on paper, but the lock and the opencv override stay shared at the root, so it adds ceremony without adding isolation.
- **C. Dev flag in the product.** Rejected: it would put a VLM reader path into `groundly/`, against decision 49 and `llm/`'s embedder-and-reranker-only rule, for something the worker argv already gives the bench.

**Decided**
- This lands as the end of Plan 2, after the verdict and the final review, so the runs in flight aren't disturbed.
- Merge `main` into `bench-suite`, move `bench` from `[project.optional-dependencies]` to `[dependency-groups]`, and open a PR from `bench-suite` to `main`. Paul merges it.
- The docs already say where bench code and results live: CLAUDE.md and `.claude/rules/conventions.md` on `bench-suite`.
- After the merge, move `bench/results/` and `bench/reference/` from `../groundly-bench` into the main checkout's `bench/`. They keep the same gitignored paths, so the thesis data survives. Then remove the `../groundly-bench` worktree.

**Left to the builder**
- Confirm that `tests/bench` passes with only `--extra dev` installed, as CI has it. Use `pytest.importorskip` where a test needs the bench group.
- The setup line in `bench/README.md`.
- Check the built wheel's file list and metadata: no `bench/` and no `bench` extra.
