# Brand icons on the page — design

Small change. Approved in conversation 2026-11-27.

## Intent

The page still shows the borrowed Claude Code logo (`groundly/web/static/claude-code.svg`) in the browser tab and the header. Replace it with Groundly's own icons from `docs/design/icon/`, and give the page a proper home-screen icon on iPhone and Android, opening full-screen (standalone) where the platform allows.

## Goals

- Browser tab: Groundly favicon, SVG for modern browsers plus a `.ico` (16 + 32 px) fallback.
- Header logo (`Brand.svelte`): the Groundly mark, light/dark variant following the page theme toggle.
- iPhone home screen: 180 px `apple-touch-icon.png`; opens standalone.
- Android home screen: `manifest.webmanifest` with 192 and 512 px icons, `display: standalone`.
- `claude-code.svg` removed (no other users).

## Non-goals / anti-goals

- No service worker, no offline mode, no install prompt UI.
- No new runtime or Node dependency; nothing generated at install or runtime.
- No change to pairing or session lifetime.

## Constraints

- `/static/` is the only path served without a session on the Tailscale socket, and Chrome fetches the manifest without cookies — so every icon and the manifest live in `/static/`.
- CSP is `default-src 'none'`: add `manifest-src 'self'`.
- iOS ignores SVG touch icons and rounds corners itself → full-bleed square PNG (no `rx`), else black corners.
- The 16/32 favicon SVGs are hand-tuned per size (fewer dots) → build the `.ico` from those, not a scaled 1024 px icon.
- The design SVGs embed ~8 KB of C2PA provenance each. Served copies are stripped of the `<metadata>` block; originals in `docs/design/icon/` stay untouched.
- Vite builds into `groundly/web/static/` without emptying it (the old `claude-code.svg` survived builds), so committed icon files persist.

## Approaches

1. **Committed files + static manifest (chosen).** Generate PNG/ICO once with a throwaway `uvx` rasterizer, commit them, link with plain `<link>` tags. Zero runtime code.
2. FastAPI route that emits the manifest — code for a file that never changes.
3. `vite-plugin-pwa` — adds a Node dependency and a service worker nobody asked for.

## Decided

- Home-screen tile: **dark** (`#101318`, matches favicon) on both platforms. *(Claude's call; user did not pick.)*
- Standalone mode: on (`display: standalone` in the manifest, `apple-mobile-web-app-capable` + `apple-mobile-web-app-title` meta), `theme_color`/`background_color` `#101318`.
- Android also gets `icon-maskable-512.png` (`purpose: maskable`): full-bleed tile, mark scaled to 80% so it stays inside the 40% safe-zone circle.
- Status bar: `<meta name="theme-color">` starts at the dark page background `#0b0d10`; the theme store sets it to the resolved page background (`#fbfcfd` light, `#0b0d10` dark). `mobile-web-app-capable` sits beside the iOS meta so Chrome doesn't warn.
- Header mark: `groundly-mark-dark.svg` (light strokes) in dark theme, `groundly-mark-light.svg` in light theme, swapped with Tailwind `dark:` classes on two `<img>` tags.

## Known limits

- **Android over plain HTTP:** Chrome only honours `standalone` for an installed app, which needs HTTPS. Over the Tailscale HTTP address the home-screen shortcut opens in a normal tab; it should still use the manifest icon. Needs checking on a real Android phone.
- **iPhone sign-in:** a home-screen app keeps its own cookie jar, copied from Safari when the icon is added. Sessions last 30 days (`SESSION_TTL_S`, fixed). When one expires, re-pair in Safari and re-add the icon; the pairing QR never opens inside the standalone app.
- **iPhone source links:** a source PDF opens with `target="_blank"`, which in standalone mode leaves the app's cookie jar. If that view has no valid session it shows `401 unpaired` instead of the PDF. Unverified; check on a real iPhone.

## Left to the builder

- Exact rasterizer (`uvx resvg`, `cairosvg`, …) and the ICO packing (Pillow is in the venv).
- File names under `/static/`; keep `web/index.html` (dev) and `groundly/web/page.html` (served) in step.
- A test asserting the served HTML links the icons and the CSP has `manifest-src 'self'`, and that the manifest's icon paths resolve under `/static/`.
