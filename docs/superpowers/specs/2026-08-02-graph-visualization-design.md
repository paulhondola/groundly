# Graph visualization: a self-contained HTML cluster explorer

**Status:** implemented 2026-08-02 · branch `graph-visualization` · `groundly export-graph`

## Why

The graph build produces `entities` / `relationships` / `communities` / `community_reports` parquet, and until
now only the query path ever read them. A student could not see the clusters, the thesis had no picture of the
knowledge graph, and — the operational cost — **a build that quietly extracted nonsense looked identical to a
good one**. The failure mode that motivated this was concrete: the 2026-08-01 build lost 8 of 11 community
reports and still reported success.

`groundly export-graph <subject>` writes one HTML file that opens offline, colours entities by Leiden community,
and resolves any node back to the documents it came from.

## What it is not

**The `.lance` files are not involved.** They hold embeddings (`entity_description`, `community_full_content`,
`text_unit_text`). A cluster view is entirely a parquet job — nodes from `entities`, edges from `relationships`,
colour from `communities`. Vectors would only matter for a *semantic* projection (UMAP/t-SNE), where proximity
means "similar meaning" rather than "connected". That is a different picture and is not built here.

This is also **not** the P7 mastery dashboard. That remains a separate deliverable served by `groundly serve`;
this is a one-shot CLI export, which is why `serve` needed no FastAPI wiring.

## Shape

| | |
| --- | --- |
| Generator | `groundly/core/graph_html.py` — `export_graph_html(subject_name, out_path, *, level=None) -> GraphHtmlResult` |
| CLI | `groundly/cli/graph.py` — `export-graph SUBJECT [-o PATH] [-l LEVEL]` |
| Renderer | vis-network 9.1.6, vendored and inlined |
| Tests | `tests/core/test_graph_html.py` (7), `tests/cli/test_cli_graph.py` (2) |

`core/` is the layer by precedent, not exception: `core/anki.py` (`.apkg`) and `core/bundle.py` (`.zip`) are the
existing "render an artifact to a file" modules. `cli/` holds the verb only.

Citations reuse `SubjectStore.chunk_details()` — the existing *"resolve chunk ids to citation targets: document
+ page + heading path"* helper — rather than hand-rolled SQL.

## The three findings that shaped it

### 1. No CDN, therefore vendored

graphify's `graph.html` loads vis-network from unpkg. That makes every *view* of the page a third-party request,
disclosing that the student is looking at a knowledge graph plus their IP and the time — not one of the three
egress paths `.claude/rules/grounding-and-privacy.md` permits. An exported page must also survive being opened
offline months later.

So `groundly/assets/vis-network.min.js` is checked in, SHA-384-verified against the SRI hash graphify
itself pins, and inlined at generation time. Provenance and a re-verification one-liner live in
`groundly/assets/VENDORED.md`. `test_page_references_no_external_url` is what stops a future edit from
quietly reintroducing a CDN reference; it is scoped to `src=`/`href=` attributes so the library's own license
comment (which contains bare URLs) does not false-positive.

### 2. Escaping `<` is sufficient, and `>` must stay

Entity titles and descriptions come from course PDFs — layer 4, and `groundly import` is an explicit trust
boundary, so a hostile bundle can carry an entity named `</script><img onerror=…>`.

All graph data is embedded as **one `json.dumps` blob** with `<` → `<`, and the JS reaches the DOM only via
`textContent`/`createElement`, never `innerHTML`. Escaping `<` alone is provably sufficient: per the HTML spec
the script-data end-tag-open state requires `</`, so with no literal `<` in the blob the parser can never begin
tag parsing inside the script element. Verified on a real export: 3 `</script` occurrences in the file, all of
them the genuine closing tags, zero inside any script body.

`>` is deliberately **not** escaped. It buys nothing — a bare `>` cannot start a tag. (For the record, escaping
it would have been *safe*, just pointless: `>` round-trips through `JSON.parse`. An early claim that it
would corrupt heading-path citations was wrong; it would only have broken a test that greps raw file text.)

The test therefore asserts the **structural property** — no `</script` inside any script body — rather than one
spelling of the escape, so a future equally-correct escaping does not fail it.

### 3. Default to the coarsest Leiden level, not the finest

Leiden only subdivides communities large enough to split, so every level below the root covers strictly fewer
entities, and an entity in no community at the chosen level renders grey. Measured 2026-08-02:

| subject | level 0 | level 1 | level 2 |
| --- | --- | --- | --- |
| `test_graph` (114 entities) | **81 (71%)** | 41 (36%) | — |
| `gm-validate` (385 entities) | **188 (49%)** | 145 (38%) | 12 (**3%**) |

Defaulting to the finest level — the intuitive choice — opens `gm-validate` as a 97%-grey graph. `level=None`
therefore resolves to the coarsest level; the page's own toggle still reaches every level, and the finer levels
produce genuinely better labels for drilling in ("BGE-M3 and Vector Retrieval Pipeline" at level 1 vs "Groundly
Indexing System and BGE-M3 Embedding Workflow" at level 0).

## Two data facts that are easy to get wrong

- **`communities.entity_ids` holds entity UUIDs; `relationships.source`/`.target` hold entity TITLES.** Two
  different join keys onto the same table. Assuming one form yields a graph with nodes but no edges.
- **Community labels come from `community_reports.title`.** `communities.title` is the literal string
  `"Community 0"` — present, and useless.

## Scale guard

Above `_MAX_NODES = 5000` entities the generator renders the **community meta-graph** (one node per community at
the chosen level, edge weight = relationships crossing each pair) and sets `aggregated=True`; the CLI says so
explicitly rather than silently rendering a different graph. No real subject reaches the cap yet, so the path is
covered by a synthetic fixture. Note the extrapolation is a range, not a number: extraction density varies with
material — 4.3 entities/chunk on `gm-validate` (PDF slides) against 8.1 on `test_graph` (markdown prose) — so a
1,194-chunk subject lands somewhere between ~5,200 and ~9,700, over the cap either way.

`GraphHtmlResult.communities` counts communities **at the rendered level**, not every level summed. Summing gave
20 for `test_graph` (9 + 11) — a number the user never sees at once, contradicting the page's own footer — and
made the aggregated view's `nodes == communities` invariant accidental rather than true.

## What the reviews changed

`spec-guardian` and `security-reviewer` were run against the finished branch. The XSS defence and the privacy
boundary both survived — the reviewer's independent instrumentation of `open`/`sqlite3.connect` confirmed
`progress.db` is never touched, and a purpose-built hostile subject failed to break out of the script element.
Four real defects surfaced, all fixed:

- **Citations were silently dropped on duplicate text-unit ids.** `text_units.id` is a content hash, so two
  chunks with byte-identical text collide — and they are still different chunks on different pages.
  `set_index("id")` made `.get()` return a Series for those, `int()` raised `TypeError`, and an
  `except (TypeError, ValueError): continue` swallowed it. On `gm-validate`, **10 entities lost every citation**
  (`VECTOR`, `SIMD`, `SPMD`, `NVIDIA GPU ARCHITECTURE`…) and 6 lost some, while the page still claimed to resolve
  them. Now a list of document ids per text-unit id; 385/385 nodes carry citations.
- **A partial build raised a bare `FileNotFoundError`.** Only `entities.parquet` was existence-checked, so an
  interrupted build tracebacked past the CLI's `except GraphHtmlError` — on precisely the case this feature
  exists to diagnose. All five artifacts are now checked together and named.
- **Resource bounds were absent.** `_MAX_NODES` capped how many things are drawn, not how big each is: 100
  entities (50× *under* the cap) with 1 MiB descriptions produced a **300 MiB** page. `_MAX_FIELD_CHARS` fixes
  the output side (300 MiB → 1.9 MiB); `_read_bounded` fixes the read side by streaming row-group batches
  against a 512 MB ceiling — measured, a 5 GiB expansion is now refused in 0.11 s at 847 MiB peak.
  A first attempt used the parquet footer's `total_byte_size` and was **wrong**: it reports the *encoded* size,
  so 600 identical 1 MiB strings dictionary-encode to 1.02 MiB against 600 MiB actual. That trap is recorded in
  `_read_bounded`'s docstring, because a check that passes the attack it exists to catch is worse than none.
- **Relationship descriptions were shipped but never rendered.** Dead payload in a file meant for sharing, and
  the larger half of the amplifier. Removed.

Two smaller changes came out of it: the vendored blob moved from `groundly/web/static/` to `groundly/assets/`
(`web/` is a named client layer, so a foundation module reading it was an invisible coupling that no import
graph would surface — `prompts/` is the real precedent), and the CLI now states what the page contains, the way
`groundly export` states what a bundle contains.

**The hash test earned itself immediately.** An IDE format-on-save expanded `vis-network.min.js` from 702,611 to
1,160,728 bytes; `test_vendored_vis_network_matches_its_recorded_hash` caught it. `.prettierignore` now prevents
the repeat, and the test remains the backstop for whichever formatter it does not cover.

## Verification performed

Full suite green. Both real subjects export (`test_graph` 114/110/9, `gm-validate` 385/341/14 at level 0). The
page was loaded in a browser and driven: node click → sidebar with type, community, degree, description,
citations, neighbours; level toggle re-colours and rebuilds the legend; legend labels are LLM titles with zero
`"Community N"` survivors. Citations render in both forms — `lecture_1.pdf, p.1` for a paged chunk and
`knowledge-base.md › Use Cases… > UC-01` for a page-less markdown chunk, never `pNone`.

**The privacy property was verified live, not just statically**: loading the page issued exactly one network
request — the document itself — and nothing else.
