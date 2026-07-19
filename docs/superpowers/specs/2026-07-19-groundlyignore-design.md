# .groundlyignore — prune junk directories during indexing

**Date:** 2026-07-19 · **Status:** approved · **Phase:** P1-surface polish

## Problem

Indexing a course repo walked `dist/` and `.venv/` and spent most of its time there. Root cause: `_iter_files` (`groundly/ingestion/pipeline.py:19-26`) does a bare `p.rglob("*")` — no directory pruning, no dotfile skipping — and since `.py`/`.js`/etc. are in `SUPPORTED_SUFFIXES`, every file inside `.venv/` is enumerated, stat'd, and hashed before the pipeline's per-file filters run. Filtering must move to walk time: directory-level pruning is the only fix that saves the walk itself.

## Decisions

- **Semantics:** built-in default deny-list + optional `.groundlyignore` with one fnmatch pattern per line. Stdlib only — no `pathspec` dependency; full gitignore semantics (negation, anchoring, `**`) are out of scope until someone needs them.
- **Location:** `.groundlyignore` at the root of each indexed directory — it travels with the materials, like `.gitignore` travels with a repo. No global ignore file.
- **No CLI changes:** `groundly index` args stay the same; the file is picked up if present.

## Design

All changes live in `groundly/ingestion/pipeline.py`: `_iter_files`, one module-level constant, one small loader.

### Walk with pruning

Replace `p.rglob("*")` with `os.walk(p)` so directories can be pruned in place (`dirnames[:] = [...]`) — `rglob` cannot prune. `os.walk` defaults to `followlinks=False`, matching the existing "symlink — not followed" policy. Sort `dirnames` and `filenames` in the loop to keep output deterministic.

### Default deny-list

```python
DEFAULT_IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__", "target", ".idea", ".vscode"}
```

A directory is pruned if its name is in `DEFAULT_IGNORED_DIRS` **or** starts with `.` (hidden). Hidden files (`.DS_Store`, `.groundlyignore` itself) are skipped too.

### `.groundlyignore` format

Read `<indexed-dir>/.groundlyignore` if present — only at the root of each directory the user passes, not per-subdirectory.

- one pattern per line, `fnmatch` syntax (`*`, `?`, `[...]`)
- blank lines and `#` comments ignored
- a pattern matches if it fnmatches the entry's **name**, or — when the pattern contains `/` — the entry's **path relative to the indexed root** (posix-style)
- matching a directory prunes the whole subtree; matching a file skips it
- ignored entries are pruned silently (git-style, no per-file report)

Loader: `_load_ignore_patterns(root: Path) -> list[str]` next to `_iter_files`.

### Explicit paths win

A file passed directly on the CLI (`groundly index subj notes.pdf`) is never ignore-filtered — ignores apply only when walking a directory. This is already the shape of `_iter_files` (non-dir paths are appended as-is).

## Testing

New tests in `tests/test_pipeline.py` beside `test_symlink_not_followed`, using the existing `subject`/`course`/stub fixtures:

- default deny: `.venv/lib/foo.py` and `dist/bundle.js` under the course dir are not indexed; sibling real materials are
- hidden dir/file skipped by default
- `.groundlyignore` with a name pattern and a glob pattern prunes matching dir and skips matching file
- comments/blank lines in `.groundlyignore` are inert
- explicitly passed file is indexed even if a pattern matches it

## Doc updates (same change set)

- `docs/groundly-spec.md` §7 decision register: one-line entry — ignore semantics (defaults + fnmatch `.groundlyignore` at indexed root, no pathspec dep).
- Brief mention of the behavior where the P1 `index` surface is described.
