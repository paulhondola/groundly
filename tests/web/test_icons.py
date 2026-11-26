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
