# Vendored assets

Third-party files checked into this directory, and why they are here rather than fetched.

## vis-network.min.js

| | |
| --- | --- |
| Package | [vis-network](https://visjs.github.io/vis-network/) 9.1.6 (2023-03-23) |
| Source | `https://unpkg.com/vis-network@9.1.6/standalone/umd/vis-network.min.js` |
| Size | 702,611 bytes |
| SHA-384 (SRI) | `Ux6phic9PEHJ38YtrijhkzyJ8yQlH8i/+buBR8s3mAZOJrP1gwyvAcIYl3GWtpX1` |
| License | Apache-2.0 **OR** MIT (dual; header retained verbatim in the file) |

Verify the checked-in copy has not drifted:

```bash
python3 -c "import base64,hashlib;print(base64.b64encode(hashlib.sha384(open('groundly/assets/vis-network.min.js','rb').read()).digest()).decode())"
```

**Do not let an editor format this file.** It is minified on purpose and pinned by hash; reformatting is not
cosmetic here, it breaks the pin. This is not hypothetical — on 2026-08-02 a format-on-save expanded it from
702,611 to 1,160,728 bytes, and `tests/core/test_graph_html.py::test_vendored_vis_network_matches_its_recorded_hash`
is what caught it. `.prettierignore` at the repo root exists to prevent the repeat; the test is the backstop for
whichever formatter it does not cover.

**Why vendored and not loaded from a CDN.** `groundly export-graph` inlines this file into the HTML it
generates. Referencing unpkg instead would make every *view* of a generated page a request to a third party —
disclosing that the student is looking at a knowledge graph, plus their IP and the time. That is not one of the
three egress paths `.claude/rules/grounding-and-privacy.md` permits (the student's own configured provider, HF
model downloads, and sha256-pinned RapidOCR models), and an exported page is meant to survive being opened
offline, months later, on a machine that has never heard of unpkg.

Upgrading is a deliberate event, not a refresh: re-fetch, re-verify the SHA-384 against the version being
pinned, update this table, and re-run `tests/core/test_graph_html.py` — `test_page_references_no_external_url`
is what stops a future edit from quietly reintroducing a CDN reference.
