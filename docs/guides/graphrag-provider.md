# Configuring the GraphRAG provider

`groundly index --graph` builds the graph retrieval arm: entity/relation
extraction, Leiden community detection, and hierarchical summarization over
your indexed materials. Extraction is a real LLM call per chunk — this is the
one call class that needs a specific kind of provider, not just any
configured endpoint.

## Why extraction shouldn't be a small local model

LM Studio/Ollama work mechanically for `extraction` — it's the same
OpenAI-compatible `base_url`+`model`+`key` shape as every other call class,
and an unset key is fine (Groundly passes a placeholder graphrag's own
config validation requires, same as any local provider). But a weak
extraction model produces a bad graph — sparse or wrong entities, garbled
relationships — and a bad graph silently invalidates the whole point of
having one. The rule
is: extraction needs a mid-tier cloud model, never a small local model. If
you don't want to spend on this, skip `--graph` entirely — the vector arm
works with zero API key, and `groundly index` (without `--graph`) is
completely unaffected.

## Configure it

```sh
groundly config set extraction.base_url https://api.<provider>.com/v1
groundly config set extraction.model <mid-tier-model>
groundly config set extraction.key <your-api-key>
```

If `<mid-tier-model>` is in litellm's bundled price map (most mainstream cloud
models are), cost tracing works automatically — no extra config. For a
local/unmapped model, set the optional per-token override fields instead:
without them, `groundly index --graph` still prompts for confirmation before
building, but can't show you a dollar estimate first:

```sh
groundly config set extraction.input_price_per_mtok <price per 1M input tokens>
groundly config set extraction.output_price_per_mtok <price per 1M output tokens>
```

`groundly config` shows the effective value (key masked) for every call
class, including `extraction`.

## Build the graph

```sh
groundly index <SUBJECT> --graph
```

On first build, this prints a rough cost estimate (chunk text length ÷ 4,
priced against `extraction.input_price_per_mtok` if set, else litellm's
bundled price map for `extraction.model`) and asks for confirmation —
`--yes`/`-y` skips the prompt. If the model is priced by neither, you'll see
"no cost estimate available" instead of a dollar figure, but you're still
asked to confirm before anything is sent anywhere.

Once a subject has a graph, `--graph` is no longer needed: every later
`groundly index` run checks whether the corpus changed (a material was
added, removed, or re-extracted) and rebuilds automatically if so — same
confirmation gate, same `--yes` skip.

## What else needs configuring

Extraction only covers the build. Two other call classes matter once the
graph exists:

- **`chat`** — every `ask` (multi-hop/global routed), `drill_down`, and
  `overview` call still needs a configured chat provider for Groundly's own
  answer synthesis, same as the vector arm. This can be local (LM Studio) —
  see [using-lm-studio.md](lm-studio.md).
- **`router`** — the query classifier that decides whether `ask` actually
  routes to a graph arm at all. Unconfigured or unreachable, every query
  degrades to `factoid` → vector-only, so a built graph never gets used by
  `ask` (though `drill_down`/`overview` still work directly, since they
  don't go through the router).

Mixing providers is normal — e.g. a cheap cloud model for `extraction`, a
local model for `chat`, nothing for `router` if you don't need `ask` to
auto-route.

## What leaves your machine

This is the one path in Groundly where your course materials' text gets
sent to a provider beyond what `ask` already sends per-question: extraction
reads every indexed chunk once, in full, to build the graph. It goes only to
whichever provider you configured for `extraction` — nothing else changes
about Groundly's privacy model (`progress.db` still never exports, `graph/`
travels with the rest of the subject on export like `store.db` does).

## Troubleshooting

- `[providers.extraction] is not configured` — the section above is missing
  from `~/.groundly/config.toml`, or a stale-graph auto-rebuild fired with no
  extraction provider set. Configure it (or delete `<subject>/graph/` if you
  no longer want a graph for this subject).
- `graph not built for this subject — run \`groundly index --graph\` first`
  — from `drill_down`, `overview`, or a multi-hop/global `ask` on a subject
  that's never had `--graph` run. Vector-only `ask` still works.
- Import dropped an imported bundle's graph and printed a note — the
  bundle's `graph/` didn't match its own `store.db` (tampered, stale export,
  or an embedding-pin change triggered a re-embed). Rebuild locally with
  `groundly index --graph` if you want one.
- `graph build failed: workflow(s) <names> failed` — graphrag ran but one or
  more of its workflows errored. The manifest is deliberately *not* stamped in
  this case, so the graph stays marked stale and the next `groundly index`
  retries it. Re-run with `--debug` for graphrag's own log lines naming the
  cause (commonly the extraction provider rate-limiting or returning
  unparseable output).
- `graphrag config is invalid` — the pinned graphrag version rejected the
  generated config. The details are withheld on purpose: they would include
  your `extraction` api_key.
- **The build looks stuck.** It isn't silent any more — `groundly index
  --graph` shows a bar advancing through graphrag's workflows. For the detail
  behind it, add `--debug` (or set `GROUNDLY_LOG_LEVEL=DEBUG`) to stream
  graphrag's own log lines to stderr; the bar is suppressed while logging is
  on so the two don't fight. graphrag also writes every build to
  `<subject>/graph/logs/indexing-engine.log`, which never leaves your machine
  (it's excluded from bundle export).
