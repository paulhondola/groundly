# Brand Icons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the borrowed Claude Code logo with Groundly's icons in the browser tab, the page header and the iOS/Android home screen (standalone).

**Architecture:** Icon files are generated once from `docs/design/icon/*.svg` and committed to `groundly/web/static/` (served by the existing `StaticFiles` mount, reachable unpaired on the Tailscale socket). The served HTML links them; a static `manifest.webmanifest` covers Android; the CSP gains `manifest-src 'self'`.

**Tech Stack:** FastAPI `StaticFiles`, Svelte 5 + Tailwind v4 (`dark:` variant on `.dark`), one-off `uvx` + cairosvg + Pillow for rasterizing (not a project dependency).

**Spec:** `docs/superpowers/specs/2026-11-27-brand-icons-design.md`

## Global Constraints

- No new runtime, install-time or Node dependency; nothing generated at install or runtime.
- Every icon and the manifest live under `/static/` (the only unauthenticated path on the Tailscale socket).
- Home-screen tile colour `#101318`; manifest `theme_color` and `background_color` `#101318`.
- iOS touch icon: 180×180 PNG, full-bleed square (no rounded corners, no transparency).
- `.ico` holds 16 px (from `groundly-favicon-16.svg`) and 32 px (from `groundly-favicon-32.svg`).
- Served SVGs carry no `<metadata>` / C2PA block; `docs/design/icon/` originals are never modified.
- `web/index.html` (dev) and `groundly/web/page.html` (served) carry the same `<head>` icon tags.
- No service worker, no install-prompt UI, no pairing or session change.

## Review Focus

- An **unpaired phone** on the Tailscale socket fetches the manifest and every icon → 200 (Chrome fetches the manifest without cookies). Test in Task 2.
- **iOS black corners**: the touch icon's corner pixel must be opaque `#101318`. Test in Task 1.
- **Theme toggle incl. "system"**: the header shows the light-stroke mark only under `.dark`, the dark-stroke mark otherwise. Test in Task 2.
- **Home-screen launch from a deep page** opens `/`, not the page it was added from: manifest `start_url` and `scope` are `/`. Test in Task 1.
- **Wrong MIME** for `.webmanifest` / `.ico` makes browsers ignore them: served as `application/manifest+json` and `image/x-icon` (`vnd.microsoft.icon` also accepted). Test in Task 2.

---

### Task 1: Generate and commit the static icon set

**Files:**
- Create: `groundly/web/static/favicon.svg` (stripped copy of `groundly-favicon-32.svg`)
- Create: `groundly/web/static/favicon.ico`
- Create: `groundly/web/static/groundly-mark-light.svg`, `groundly/web/static/groundly-mark-dark.svg` (stripped copies)
- Create: `groundly/web/static/apple-touch-icon.png` (180, full-bleed)
- Create: `groundly/web/static/icon-192.png`, `groundly/web/static/icon-512.png` (rounded tile as designed, transparent corners, from `groundly-icon-dark.svg`)
- Create: `groundly/web/static/manifest.webmanifest`
- Test: `tests/web/test_icons.py`

**Interfaces:**
- Produces: the eight file paths above under `/static/`, consumed by Task 2.

- [ ] **Step 1: Write the failing test** `tests/web/test_icons.py`

```python
import json
from pathlib import Path
from PIL import Image

STATIC = Path(__file__).parents[2] / "groundly" / "web" / "static"


def test_manifest_is_standalone_dark_and_its_icons_exist():
    m = json.loads((STATIC / "manifest.webmanifest").read_text())
    assert m["name"] == m["short_name"] == "Groundly"
    assert m["start_url"] == m["scope"] == "/"
    assert m["display"] == "standalone"
    assert m["theme_color"] == m["background_color"] == "#101318"
    sizes = {}
    for icon in m["icons"]:
        assert icon["src"].startswith("/static/") and icon["type"] == "image/png"
        sizes[icon["sizes"]] = Image.open(STATIC / icon["src"].removeprefix("/static/")).size
    assert sizes == {"192x192": (192, 192), "512x512": (512, 512)}


def test_touch_icon_is_an_opaque_full_bleed_square():
    img = Image.open(STATIC / "apple-touch-icon.png").convert("RGBA")
    assert img.size == (180, 180)
    for xy in [(0, 0), (179, 0), (0, 179), (179, 179)]:
        assert img.getpixel(xy) == (0x10, 0x13, 0x18, 255)


def test_ico_holds_16_and_32():
    assert Image.open(STATIC / "favicon.ico").info["sizes"] == {(16, 16), (32, 32)}


def test_served_svgs_carry_no_provenance_blob():
    for name in ("favicon.svg", "groundly-mark-light.svg", "groundly-mark-dark.svg"):
        text = (STATIC / name).read_text()
        assert text.startswith("<svg") and "c2pa" not in text and "<metadata" not in text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/web/test_icons.py -v`
Expected: 4 FAIL (`FileNotFoundError`).

- [ ] **Step 3: Generate the files**

One-off script in the scratchpad (not committed), run with
`DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uvx --with cairosvg --with pillow python <script>`:
- Strip: `re.sub(r"\s*<metadata>.*?</metadata>", "", text, flags=re.S)` and remove the ` xmlns:c2pa="http://c2pa.org/manifest"` attribute; write the three stripped SVGs.
- Touch icon: from the stripped `groundly-icon-dark.svg`, replace `rx="22.5"` with `rx="0"`, `cairosvg.svg2png(..., output_width=180, output_height=180)`.
- `icon-192.png` / `icon-512.png`: stripped `groundly-icon-dark.svg` as is, at 192 and 512.
- ICO: render favicon-16 at 16 and favicon-32 at 32, then `img32.save(ico, format="ICO", sizes=[(16, 16), (32, 32)], append_images=[img16])`.

Write `manifest.webmanifest` by hand with exactly the fields the test asserts plus `"icons"` for 192 and 512 (`"purpose"` omitted = `any`).

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/web/test_icons.py -v`
Expected: 4 PASS. Also open `apple-touch-icon.png` and `icon-512.png` and look at them (Read tool) — the mark must be centred and crisp.

- [ ] **Step 5: Commit**

```bash
git add groundly/web/static/{favicon.svg,favicon.ico,groundly-mark-light.svg,groundly-mark-dark.svg,apple-touch-icon.png,icon-192.png,icon-512.png,manifest.webmanifest} tests/web/test_icons.py
git commit -m "feat(web): Groundly icon set and web manifest"
```
Put the generation recipe (the bullets above) in the commit body.

---

### Task 2: Wire the icons into the page

**Files:**
- Modify: `groundly/web/page.html` (head), `web/index.html` (head)
- Modify: `groundly/web/app.py:85-88` (`_CSP`)
- Modify: `web/src/lib/components/Brand.svelte:18`
- Delete: `groundly/web/static/claude-code.svg`
- Rebuild: `groundly/web/static/page.js`, `page.css` (`cd web && vp build`)
- Test: `tests/web/test_app.py`, `tests/web/test_remote.py`, `web/src/lib/components/__tests__/brand.test.ts` (new)

**Interfaces:**
- Consumes: Task 1's `/static/` paths.

- [ ] **Step 1: Write the failing tests**

In `tests/web/test_app.py::test_page_routes_serve_html_under_a_self_only_csp` add:
```python
        assert directives["manifest-src"] == "'self'"
        for tag in (
            '<link rel="icon" href="/static/favicon.ico" sizes="32x32">',
            '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">',
            '<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">',
            '<link rel="manifest" href="/static/manifest.webmanifest">',
            '<meta name="apple-mobile-web-app-capable" content="yes">',
            '<meta name="apple-mobile-web-app-title" content="Groundly">',
        ):
            assert tag in r.text
        assert "claude-code.svg" not in r.text
```
In `test_page_assets_are_served_with_their_own_types` add:
```python
assert (
    client.get("/static/manifest.webmanifest")
    .headers["content-type"]
    .startswith("application/manifest+json")
)
assert client.get("/static/favicon.ico").headers["content-type"] in (
    "image/x-icon",
    "image/vnd.microsoft.icon",
)
```
In `tests/web/test_remote.py::test_perimeter_restricts_head_to_static_only`, inside the `phone` block (unpaired), add:
```python
for name in (
    "manifest.webmanifest",
    "icon-192.png",
    "icon-512.png",
    "apple-touch-icon.png",
    "favicon.ico",
    "favicon.svg",
):
    assert phone.get(f"/static/{name}").status_code == 200
```
New `web/src/lib/components/__tests__/brand.test.ts` (mount pattern as in `source-item.test.ts`): mount `Brand`, assert two `img` elements — `src` ending `/static/groundly-mark-light.svg` with class `dark:hidden`, and `/static/groundly-mark-dark.svg` with classes `hidden` and `dark:block`; both `alt=""`; no `claude-code.svg` in `container.innerHTML`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/web/test_app.py tests/web/test_remote.py -k "csp or own_types or head_to_static" -v` and `cd web && vp test run brand`
Expected: FAIL on the missing `manifest-src`, tags and `img` elements.

- [ ] **Step 3: Implement**

- `_CSP`: append `manifest-src 'self'; ` (keep every other directive unchanged).
- `page.html` and `web/index.html`: replace the `claude-code.svg` link with the six tags from the test, in that order (`index.html` keeps its own ` />` self-closing style).
- `Brand.svelte`: replace the single `<img>` with
  `<img src="/static/groundly-mark-light.svg" alt="" class="h-6 w-6 shrink-0 dark:hidden" />` and
  `<img src="/static/groundly-mark-dark.svg" alt="" class="hidden h-6 w-6 shrink-0 dark:block" />`.
- `git rm groundly/web/static/claude-code.svg`; `cd web && vp build`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/web -q` and `cd web && vp test run && pnpm check`
Expected: all PASS, `svelte-check` 0 errors. `grep -r claude-code groundly/web web/src web/index.html` prints nothing.

- [ ] **Step 5: Check it in the browser**

`uv run groundly serve`, open the page in the browser pane: tab shows the Groundly favicon; header mark swaps when the theme toggle cycles light → dark → system; no CSP errors in the console.

- [ ] **Step 6: Commit**

```bash
git add -A groundly/web web/index.html web/src tests/web
git commit -m "feat(web): Groundly favicon, header mark and home-screen icons"
```
Then run `graphify update .`.

Not verifiable here (tell the user): adding to the home screen on a real iPhone and Android phone over the Tailscale address.
