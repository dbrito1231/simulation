#!/usr/bin/env python3
"""Pack user-provided wildlife PNGs into simulation/wildlife.png.

Loads simulation/assets/wildlife/<kind>.png for each mapped kind, trims
transparent padding (and mint-green backdrop on bee.png), shelf-packs into
one RGBA atlas, writes simulation/wildlife.png, and prints WILDLIFE_SHEET_FRAMES
for sprites.js.

Kinds without a source PNG (bird, owl, squirrel) are omitted — the viewer falls
back to canvas helpers / procedural grids.

Run: uv run python scripts/build_wildlife_sheet.py
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = ROOT / "simulation" / "assets" / "wildlife"
OUT_PATH = ROOT / "simulation" / "wildlife.png"
PREVIEW_PATH = ROOT / "simulation" / "_vendor" / "wildlife-preview-4x.png"

PADDING = 2

# Kinds with user PNGs (pack order: large → mid → small for tighter rows).
SHEET_KINDS = [
    "deer", "boar", "grazer", "seal",
    "fox", "turtle", "rabbit", "chicken", "gull",
    "mouse", "fish", "crab", "bee",
]

TIER_MAX_SIDE = {
    "large": 44,
    "mid": 34,
    "small": 26,
}

KIND_TIER = {
    "deer": "large", "boar": "large", "grazer": "large", "seal": "large",
    "fox": "mid", "turtle": "mid", "rabbit": "mid", "chicken": "mid", "gull": "mid",
    "mouse": "small", "fish": "small", "crab": "small", "bee": "small",
}


def _corner_colors(img: Image.Image) -> list[tuple[int, int, int, int]]:
    px = img.load()
    assert px is not None
    w, h = img.size
    pts = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]
    return [px[x, y] for x, y in pts]


def _is_mint_green(r: int, g: int, b: int) -> bool:
    """Near #c8e6c9 and other light mint-greens used as bee backdrop."""
    if g < 170 or r < 150 or b < 150:
        return False
    if g >= r and g >= b and (g - min(r, b)) >= 15:
        return True
    # explicit #c8e6c9 neighborhood
    return abs(r - 200) <= 40 and abs(g - 230) <= 40 and abs(b - 201) <= 40


def _pixel_empty(r: int, g: int, b: int, a: int, bg: tuple[int, int, int] | None) -> bool:
    if a < 16:
        return True
    if bg is not None:
        dr, dg, db = abs(r - bg[0]), abs(g - bg[1]), abs(b - bg[2])
        if dr <= 35 and dg <= 35 and db <= 35:
            return True
    if _is_mint_green(r, g, b):
        return True
    return False


def trim_image(img: Image.Image, *, treat_corners_as_bg: bool = False) -> Image.Image:
    """Crop to opaque bounding box; optional corner-chroma key (bee)."""
    img = img.convert("RGBA")
    px = img.load()
    assert px is not None
    w, h = img.size

    bg: tuple[int, int, int] | None = None
    if treat_corners_as_bg:
        corners = _corner_colors(img)
        bg = (
            sum(c[0] for c in corners) // 4,
            sum(c[1] for c in corners) // 4,
            sum(c[2] for c in corners) // 4,
        )

    min_x, min_y = w, h
    max_x, max_y = -1, -1
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if not _pixel_empty(r, g, b, a, bg):
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)

    if max_x < min_x:
        return img

    return img.crop((min_x, min_y, max_x + 1, max_y + 1))


def dest_size(sw: int, sh: int, max_side: int) -> tuple[int, int]:
    """Fit source rect inside max_side box, preserve aspect ratio."""
    scale = min(max_side / sw, max_side / sh)
    return max(1, round(sw * scale)), max(1, round(sh * scale))


def shelf_pack(images: dict[str, Image.Image]) -> tuple[Image.Image, dict[str, dict]]:
    """Row/shelf pack trimmed sprites; return atlas + {kind: {sx,sy,sw,sh}}."""
    items = [(kind, images[kind]) for kind in SHEET_KINDS if kind in images]
    if not items:
        raise SystemExit("No wildlife PNGs found to pack")

    max_row_w = max(img.width for _, img in items) * 4
    x = PADDING
    y = PADDING
    row_h = 0
    atlas_w = PADDING
    atlas_h = PADDING
    placements: dict[str, dict] = {}

    for kind, img in items:
        iw, ih = img.size
        if x + iw + PADDING > max_row_w and x > PADDING:
            x = PADDING
            y += row_h + PADDING
            row_h = 0
        placements[kind] = {"sx": x, "sy": y, "sw": iw, "sh": ih}
        x += iw + PADDING
        row_h = max(row_h, ih)
        atlas_w = max(atlas_w, x)
        atlas_h = max(atlas_h, y + row_h + PADDING)

    sheet = Image.new("RGBA", (atlas_w, atlas_h), (0, 0, 0, 0))
    for kind, img in items:
        p = placements[kind]
        sheet.paste(img, (p["sx"], p["sy"]))
    return sheet, placements


def build_frames_js(placements: dict[str, dict]) -> dict:
    frames: dict = {}
    for kind in SHEET_KINDS:
        if kind not in placements:
            continue
        p = placements[kind]
        tier = KIND_TIER[kind]
        dw, dh = dest_size(p["sw"], p["sh"], TIER_MAX_SIDE[tier])
        frames[kind] = {**p, "destW": dw, "destH": dh}
    return frames


def frames_to_js(frames: dict) -> str:
    lines = ["const WILDLIFE_SHEET_FRAMES = {"]
    for kind in SHEET_KINDS:
        if kind not in frames:
            continue
        inner = json.dumps(frames[kind], separators=(",", ":"))
        lines.append(f"  {kind}: {inner},")
    lines.append("};")
    return "\n".join(lines)


def write_preview(sheet: Image.Image, path: Path, scale: int = 4) -> None:
    preview = sheet.resize(
        (sheet.width * scale, sheet.height * scale), Image.Resampling.NEAREST
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(path, optimize=True)


def main() -> None:
    trimmed: dict[str, Image.Image] = {}
    print("Loading and trimming assets:")
    for kind in SHEET_KINDS:
        src = ASSETS_DIR / f"{kind}.png"
        if not src.is_file():
            print(f"  SKIP {kind}: missing {src.name}")
            continue
        raw = Image.open(src).convert("RGBA")
        img = trim_image(raw, treat_corners_as_bg=(kind == "bee"))
        trimmed[kind] = img
        print(f"  {kind:8s} {raw.size[0]:4d}x{raw.size[1]:<4d} -> {img.size[0]:4d}x{img.size[1]}")

    sheet, placements = shelf_pack(trimmed)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(OUT_PATH, optimize=True)
    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"\nWrote {OUT_PATH} ({sheet.width}x{sheet.height}, {size_kb:.1f} KB)")

    write_preview(sheet, PREVIEW_PATH, scale=4)
    print(f"Wrote {PREVIEW_PATH}")

    frames = build_frames_js(placements)
    print(f"\nKinds on sheet: {len(frames)}")
    print("\n--- WILDLIFE_SHEET_FRAMES (paste into sprites.js) ---\n")
    print(frames_to_js(frames))


if __name__ == "__main__":
    main()
