# Using LM Studio as Groundly's provider

Groundly talks to any OpenAI-compatible endpoint, configured per call class in
`~/.groundly/config.toml`. LM Studio is the zero-cost, fully local option: no
API key, nothing leaves your machine. Indexing and `search` never need a
provider at all — this is only for `ask` (and later generation phases).

## 1. Serve a model

### Desktop app

1. Install LM Studio from [lmstudio.ai](https://lmstudio.ai), open it, and
   download a model from the search tab (e.g. `qwen2.5-7b-instruct` — any
   instruct-tuned chat model works).
2. Go to the **Developer** tab and start the server. Default address:
   `http://localhost:1234`.
3. Load the model (or enable **just-in-time model loading** in the server
   settings, which loads it on the first request).
4. Copy the model identifier shown in the server view — you need its exact
   string for the config below.

### CLI (`lms`)

The desktop app ships the CLI; expose it once with:

```sh
~/.lmstudio/bin/lms bootstrap
```

Then:

```sh
lms get qwen2.5-7b-instruct     # download
lms load qwen2.5-7b-instruct    # load into memory
lms server start                # serve on http://localhost:1234
lms ls                          # exact model identifiers (for config.toml)
lms ps                          # what's currently loaded
```

## 2. Point Groundly at it

`groundly init <SUBJECT>` writes a commented template to
`~/.groundly/config.toml`. Uncomment/fill the chat section:

```toml
[providers.chat]
base_url = "http://localhost:1234/v1"
model    = "qwen2.5-7b-instruct"   # exact id from `lms ls` / the server view
```

No `api_key` — local runtimes don't need one. The optional
`input_price_per_mtok` / `output_price_per_mtok` fields stay unset (cost
traces record 0 for local models).

The other call classes (`[providers.generation]`, `[providers.extraction]`,
`[providers.router]`) accept the same keys when later phases need them — LM
Studio and a cloud key are the same code path, so you can mix (e.g. local
chat, cloud extraction).

## 3. Verify

```sh
groundly ask <SUBJECT> "what does lecture 1 cover?"
```

A cited answer means the loop works. Failure modes:

- `[providers.chat] is not configured` — the section above is missing or
  still commented out.
- Connection refused — the LM Studio server isn't running (`lms server
  status`).
- Model not found — the `model` string doesn't match a downloaded model
  identifier (`lms ls`).

Note: Groundly serializes generation jobs when the provider is a local
runtime, so a long-running generation and an interactive `ask` won't fight
over your GPU.
