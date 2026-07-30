#!/usr/bin/env python3
"""Build simulation/wildlife.png — 16-kind wildlife spritesheet (Phase 4).

Atlas layout (8 columns × 4 rows, cell = 16×16 px, sheet = 128×64 px):

  Row 0 (sy=0):   deer | boar | grazer* | seal | fox | owl-stand | owl-alt | turtle
  Row 1 (sy=16):  rabbit-stand | rabbit-alt | chicken* | chicken*-alt | gull-stand | gull-alt | bird-stand | bird-alt
  Row 2 (sy=32):  mouse | squirrel-stand | squirrel-alt | fish-stand | fish-alt | crab | butterfly-stand | butterfly-alt
  Row 3 (sy=48):  cow* (unused atlas slot — not mapped to any live kind)

  * = sourced from Kenney Tiny Farm CC0 tiles (tile_0120 sheep, tile_0122 chicken, tile_0121 cow unused).

Run: uv run python scripts/build_wildlife_sheet.py
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "simulation" / "_vendor" / "tiny-farm" / "Tiles"
OUT_PATH = ROOT / "simulation" / "wildlife.png"

COLS = 8
ROWS = 4
CELL = 16

# Tiny Farm idiom palette (outline k = 63,38,49 — always)
OUT = (63, 38, 49, 255)
CLR = {
    ".": (0, 0, 0, 0),
    "k": OUT,
    "w": (255, 255, 255, 255),
    "l": (192, 203, 220, 255),
    "m": (139, 155, 180, 255),
    "d": (82, 96, 124, 255),
    "h": (90, 105, 136, 255),
    "b": (160, 120, 90, 255),  # bird brown body
    "j": (196, 154, 108, 255),  # deer tan body
    "A": (170, 120, 80, 255),  # deer shade
    "J": (220, 190, 150, 255),  # deer/belly light
    "H": (90, 70, 55, 255),  # deer/boar dark face
    "B": (220, 200, 170, 255),  # antler bone
    "S": (180, 120, 70, 255),  # squirrel fur
    "T": (180, 100, 50, 255),  # fox bushy tail
    "F": (100, 130, 160, 255),  # seal flipper
    "W": (139, 115, 85, 255),  # owl brown
    "U": (180, 160, 130, 255),  # owl face cream
    "R": (180, 180, 180, 255),  # mouse grey body
    "L": (150, 150, 150, 255),  # mouse tail
    "n": (62, 78, 110, 255),  # nose dark
    "o": (225, 154, 101, 255),  # fox orange
    "p": (247, 194, 130, 255),  # fox light belly
    "y": (227, 134, 40, 255),  # beak / feet
    "g": (120, 145, 85, 255),  # turtle shell
    "s": (85, 115, 60, 255),  # turtle dark shell
    "e": (140, 175, 95, 255),  # turtle head / fish eye accent
    "f": (100, 180, 220, 255),  # fish blue
    "t": (60, 130, 180, 255),  # fish tail fin
    "c": (220, 90, 80, 255),  # crab red
    "u": (180, 60, 55, 255),  # crab dark
    "E": (40, 40, 40, 255),  # crab eye dots
    "a": (170, 100, 210, 255),  # butterfly wing A
    "v": (130, 70, 180, 255),  # butterfly wing B
    "i": (255, 180, 180, 255),  # pink ears / nose
    "q": (38, 43, 68, 255),  # deep wool shadow (sheep idiom)
    "x": (110, 85, 65, 255),  # boar body
    "Q": (90, 70, 50, 255),  # boar dark shade
    "z": (240, 230, 210, 255),  # tusks / highlights
}


def grid_from_strings(rows: list[str]) -> list[list[tuple[int, int, int, int]]]:
    grid: list[list[tuple[int, int, int, int]]] = []
    for row in rows:
        line: list[tuple[int, int, int, int]] = []
        for ch in row:
            if ch not in CLR:
                raise ValueError(f"Unknown palette key {ch!r}")
            line.append(CLR[ch])
        grid.append(line)
    return grid


def bake_grid(grid: list[list[tuple[int, int, int, int]]]) -> Image.Image:
    h = len(grid)
    w = max(len(r) for r in grid)
    img = Image.new("RGBA", (CELL, CELL), (0, 0, 0, 0))
    ox = (CELL - w) // 2
    oy = (CELL - h) // 2
    px = img.load()
    assert px is not None
    for y, row in enumerate(grid):
        for x, rgba in enumerate(row):
            if rgba[3]:
                px[ox + x, oy + y] = rgba
    return img


def paste_tile(sheet: Image.Image, col: int, row: int, img: Image.Image) -> None:
    sheet.paste(img.convert("RGBA"), (col * CELL, row * CELL))


def load_vendor(name: str) -> Image.Image:
    return Image.open(VENDOR / name).convert("RGBA")


def make_chicken_peck_alt(stand: Image.Image) -> Image.Image:
    """Shift head/comb/beak down 1px for subtle peck frame."""
    alt = stand.copy()
    src = stand.load()
    dst = alt.load()
    assert src is not None and dst is not None
    # Clear original head band (rows 2-6, right half)
    for y in range(2, 8):
        for x in range(7, 16):
            dst[x, y] = (0, 0, 0, 0)
    # Copy head pixels shifted down by 1
    for y in range(2, 7):
        for x in range(7, 16):
            c = src[x, y]
            if c[3] and c != OUT:
                ny = y + 1
                if ny < CELL:
                    dst[x, ny] = c
    # Restore outline around shifted head
    for y in range(2, 9):
        for x in range(6, 16):
            c = src[x, y]
            if c == OUT:
                ny = y + 1 if y >= 2 and x >= 7 else y
                if ny < CELL:
                    dst[x, ny] = OUT
    # Extend beak down-right one pixel
    for x, y in ((13, 7), (14, 8), (15, 8)):
        if 0 <= x < CELL and 0 <= y < CELL:
            dst[x, y] = CLR["y"]
    return alt


# ---------------------------------------------------------------------------
# Hand-authored frames (Tiny Farm idiom: heavy OUT outline, 3–4 shade steps)
# ---------------------------------------------------------------------------

HAND_SPRITES: dict[str, list[str]] = {
    # Sheep skeleton: side view facing RIGHT, dark head on right, 4 short legs.
    "deer": [
        "................",
        "....k..B..k.....",
        "...k.Bk.k.Bk....",
        "..k..B...B..k...",
        "........kkkkkk..",
        "......kkkkkkkkk.",
        "..kkkkkkkjjjjkkk",
        ".kkkkkkHHHHHHkkk",
        "kkkjjjjHHHHHHkkk",
        "kkjJJjjjHHHHHkkk",
        "kkjAAjjjHHHHkkk.",
        "kkjJJjjjjjjjkkk.",
        "kkjAAjjjAAjjkkk.",
        "kkkjjjjjjjjjkk..",
        ".kkHkHkkHkHkkk..",
        ".kkHkHkkHkHkkk..",
    ],
    "boar": [
        "................",
        "....k.k......k.k",
        "...k.k.k....k.k.",
        "....k.k......k.k",
        "........kkkkkk..",
        "......kkkkkkkkk.",
        "..kkkkkkkxxxxkkk",
        ".kkkkkkxxxxxxyxk",
        "kkkxxxxxxxxxxyxk",
        "kkxxxxxxxxxHHzkk",
        "kkxxxxxxxxxHHzkk",
        "kkxxxxxxxxxxkkk.",
        "kkkxxxxxxxxkkk..",
        ".kxxkxxkxxkk....",
        "...xx...xx......",
        "................",
    ],
    "seal": [
        "................",
        "................",
        "....kkkkkkkkk...",
        "...kmmmmmmmmmk..",
        "..kmmmmmmmmmmmk.",
        ".kmmmmmmmmmmmmmk",
        "kmmmmmmmmmmmmmmk",
        "kmmmmmmmmmnmmmmk",
        "kmmmmmmmmFmmmmmk",
        ".kmmmmmmmmmmmmk.",
        "..kkkkkkkkkkkk..",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
    "fox": [
        "................",
        "..k.........k...",
        ".kok.......kok..",
        "kTTT..kkkkkkkk..",
        "kTTT.kkkkkkkkkk.",
        "kTTTkkkkkkoooooo",
        "kTT.kkkkkooooooe",
        "kTTkkkkooooooooo",
        "kkoooooooooooooo",
        "kkooooppppppooTT",
        "kkoooooooddddook",
        ".kooooooddddook.",
        "..kdddd..ddddk..",
        "...kddd...dddk..",
        "....dd.....dd...",
        "................",
    ],
    "owl_stand": [
        "................",
        ".....kkkk.......",
        "....kWUUUWk.....",
        "...kWyiiiyWk....",
        "..kWUUUUUUUWk...",
        ".kWWWWWWWWWWk...",
        "kWWWWWWWWWWWWk..",
        "kWWWWWWWWWWWWk..",
        "kWWWWWWWWWWWWk..",
        ".kWWWWWWWWWWk...",
        "..kWWWWWWWWk....",
        "...kWWWWWWk.....",
        "....kdd.ddk.....",
        "................",
        "................",
        "................",
    ],
    "owl_alt": [
        "................",
        ".....kkkk.......",
        "....kWUUUWk.....",
        "...kWykkkyWk....",
        "..kWUUUUUUUWk...",
        ".kWWWWWWWWWWk...",
        "kWWWWWWWWWWWWk..",
        "kWWWWWWWWWWWWk..",
        "kWWWWWWWWWWWWk..",
        ".kWWWWWWWWWWk...",
        "..kWWWWWWWWk....",
        "...kWWWWWWk.....",
        "....kdd.ddk.....",
        "................",
        "................",
        "................",
    ],
    "turtle": [
        "................",
        "....kkkkkkk.....",
        "...kgssssssgk...",
        "..kgsheeehsgk...",
        ".kgggggggggggk..",
        "kggggggggggggggk",
        "kggggggggggeekk.",
        "kgggggggggggkk..",
        ".kggggggggggk...",
        "..kd....d..dk...",
        "..kd....d..dk...",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
    "rabbit_stand": [
        "................",
        "....k.....k.....",
        "....i.....i.....",
        "....w.....w.....",
        "....w.....w.....",
        "........kkkkkk..",
        "......kkkkkkkkk.",
        "..kkkkkkkwwwwkkk",
        ".kkkkkkwwniwwwek",
        "kkkwwwwwwwwwwwkk",
        "kkwwwwwwwqwwqkkk",
        "kkwwwwwwwwwwwkkk",
        "kkwllwwwwwwllkk.",
        ".kwwwwwwwwwwwk..",
        "..kdd.....ddk...",
        "...dd.....dd....",
    ],
    "rabbit_alt": [
        "................",
        "....k.....k.....",
        ".....i...i......",
        "....w.w.........",
        "....w.....w.....",
        "........kkkkkk..",
        "......kkkkkkkkk.",
        "..kkkkkkkwwwwkkk",
        ".kkkkkkwwniwwwek",
        "kkkwwwwwwwwwwwkk",
        "kkwwwwwwwqwwqkkk",
        "kkwwwwwwwwwwwkkk",
        "kkwllwwwwwwllkk.",
        ".kwwwwwwwwwwwk..",
        "..kdd.....ddk...",
        "...dd.....dd....",
    ],
    "gull_stand": [
        "................",
        "........kkkkkk..",
        "......kkkkkkkkk.",
        "..kkkkkkkwwwwwkk",
        ".kkkkkkhhhhhyek.",
        "kkkwwwwhhhhhhhk.",
        "kkwwwwwwwqwwqkkk",
        "kkwwwwwwwhhhhkkk",
        "kkwllwwwwwnhhkk.",
        "kkwllwwwwwwkkkk.",
        ".kwwwwwwwwwwkk..",
        "..kwwwwwwwwk....",
        "...kdd...ddk....",
        "................",
        "................",
        "................",
    ],
    "gull_alt": [
        "................",
        "........kkkkkk..",
        "......kkkkkkkkk.",
        "..kkkkkkkmwwwwkk",
        ".kkkkkkhhhhhyek.",
        "kkkmwwwwhhhhhhhk",
        "kkwwwwwwwqwwqkkk",
        "kkwwwwwwwhhhhkkk",
        "kkwllwwwwwnhhkk.",
        "kkwllwwwwwwkkkk.",
        ".kwwwwwwwwwwkk..",
        "..kwwwwwwwwk....",
        "...kdd...ddk....",
        "................",
        "................",
        "................",
    ],
    "bird_stand": [
        "................",
        "........kkkkkk..",
        "......kkkkkkkkk.",
        "..kkkkkkkbbbbbkk",
        ".kkkkkkhhhhhyek.",
        "kkkbbbbhhhhhhhk.",
        "kkbbbbbbbfbbfkkk",
        "kkbbbbbbbhhhkkk.",
        "kkbllbbbbbnhhkk.",
        "kkbllbbbbbbkkkk.",
        ".kbbbbbbbbbbkk..",
        "..kbbbbbbbbk....",
        "...kdd...ddk....",
        "................",
        "................",
        "................",
    ],
    "bird_alt": [
        "................",
        "........kkkkkk..",
        "......kkkkkkkkk.",
        "..kkkkkkkmbbbbkk",
        ".kkkkkkhhhhhyek.",
        "kkkmbbbbhhhhhhhk",
        "kkbbbbbbbfbbfkkk",
        "kkbbbbbbbhhhkkk.",
        "kkbllbbbbbnhhkk.",
        "kkbllbbbbbbkkkk.",
        ".kbbbbbbbbbbkk..",
        "..kbbbbbbbbk....",
        "...kdd...ddk....",
        "................",
        "................",
        "................",
    ],
    "mouse": [
        "................",
        "................",
        "...kk.....kk....",
        "...Ri.....iR....",
        "..kRRRRRRRRRk...",
        ".tkRRRRnRRRRRk..",
        "kRRRRRRRRRRRRRk.",
        "kRRRRRRRRRRRRRk.",
        ".kRRRRRRRRRRRk..",
        "..kRRRRRRRRRk...",
        "...kdd.....ddk..",
        "....LL.....dd...",
        "....kLL.........",
        ".....LL.........",
        "................",
        "................",
    ],
    "squirrel_stand": [
        "................",
        "....TTTT........",
        "...kTTTTTTk.....",
        "..kTTTTTTTTk....",
        ".kTTTSSSSSSSk...",
        "kTTTTkkSSSSSSSk.",
        "kTTTTkSSSSSSSSSk",
        "kTTTkSSSSSSSSSSk",
        ".kTTkSSSSSSSSSk.",
        "..kkSSSSSSSSSSk.",
        "...kSSSSSSSSSSk.",
        "....kSSSSSSSSk..",
        ".....kdd...ddk..",
        "......dd...dd...",
        "................",
        "................",
    ],
    "squirrel_alt": [
        "................",
        "....TTTT.t......",
        "...kTTTTTTk.....",
        "..kTTTTTTTTk....",
        ".kTTTSSSSSSSk...",
        "kTTTTkkSSSSSSSk.",
        "kTTTTkSSSSSSSSSk",
        "kTTTkSSSSSSSSSSk",
        ".kTTkSSSSSSSSSk.",
        "..kkSSSSSSSSSSk.",
        "...kSSSSSSSSSSk.",
        "....kSSSSSSSSk..",
        ".....kdd...ddk..",
        "......dd...dd...",
        "................",
        "................",
    ],
    "fish_stand": [
        "................",
        "................",
        "...kkkkkk.......",
        "..kffffffffk....",
        ".tkfffffffffnk..",
        "kfffffffffffffk.",
        "kfffffffffffffk.",
        "kfffffffffffffk.",
        ".kfffffffffffk..",
        "..kfffffffffk...",
        "...kffffffffk...",
        "....kkkkkkkk....",
        "................",
        "................",
        "................",
        "................",
    ],
    "fish_alt": [
        "................",
        "................",
        "....kkkkkk......",
        "...kffffffffk...",
        "..tkfffffffffnk.",
        ".kfffffffffffffk",
        ".kfffffffffffffk",
        "..kffffffffffffk",
        "...kfffffffffffk",
        "....kfffffffffk.",
        ".....kffffffffk.",
        "......kkkkkkkk..",
        "................",
        "................",
        "................",
        "................",
    ],
    "crab": [
        "................",
        ".c.....kkkk....c",
        "c..kcccccccck..c",
        "..ckcccccccccck.",
        ".ckccccccccccck.",
        "kcccccccccccccck",
        "kcccccccccccccck",
        ".ckcccccccccEck.",
        "..kcccccccccck..",
        "...kuuuuuuuuk...",
        "....kuuuuuuk....",
        ".....kuuuk......",
        "................",
        "................",
        "................",
        "................",
    ],
    "butterfly_stand": [
        "................",
        "aaa.......bbb...",
        "aaaak.....k.bbbb",
        "vaaak.....k.bbbb",
        "aaaak.....k.vbbb",
        "aaaak.....k.bbbb",
        "aaaak.....k.bbbb",
        ".aaa.......bbb..",
        "......k.k.......",
        "......k.k.......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
    "butterfly_alt": [
        "................",
        "......k.k.......",
        ".....kvvvvk.....",
        "....kvvvvvvk....",
        "....kvvvvvvk....",
        ".....kvvvvk.....",
        "......k.k.......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
}

# Atlas cell assignments: name -> (col, row)
PLACEMENTS: dict[str, tuple[int, int]] = {
    "deer": (0, 0),
    "boar": (1, 0),
    "grazer": (2, 0),
    "seal": (3, 0),
    "fox": (4, 0),
    "owl_stand": (5, 0),
    "owl_alt": (6, 0),
    "turtle": (7, 0),
    "rabbit_stand": (0, 1),
    "rabbit_alt": (1, 1),
    "chicken_stand": (2, 1),
    "chicken_alt": (3, 1),
    "gull_stand": (4, 1),
    "gull_alt": (5, 1),
    "bird_stand": (6, 1),
    "bird_alt": (7, 1),
    "mouse": (0, 2),
    "squirrel_stand": (1, 2),
    "squirrel_alt": (2, 2),
    "fish_stand": (3, 2),
    "fish_alt": (4, 2),
    "crab": (5, 2),
    "butterfly_stand": (6, 2),
    "butterfly_alt": (7, 2),
    "cow_unused": (0, 3),
}

LARGE = {"destW": 32, "destH": 32}
MID = {"destW": 28, "destH": 28}
SMALL = {"destW": 14, "destH": 14}


def frame_at(name: str) -> dict:
    col, row = PLACEMENTS[name]
    base = {"sx": col * CELL, "sy": row * CELL, "sw": CELL, "sh": CELL}
    return base


def build_frames_js() -> dict:
    """Build WILDLIFE_SHEET_FRAMES object for sprites.js."""
    def f(name: str, tier: dict) -> dict:
        return {**frame_at(name), **tier}

    return {
        "deer": f("deer", LARGE),
        "boar": f("boar", LARGE),
        "grazer": f("grazer", LARGE),
        "seal": f("seal", LARGE),
        "fox": f("fox", MID),
        "owl": {
            "stand": f("owl_stand", MID),
            "alt": f("owl_alt", MID),
        },
        "turtle": f("turtle", MID),
        "rabbit": {
            "stand": f("rabbit_stand", MID),
            "alt": f("rabbit_alt", MID),
        },
        "chicken": {
            "stand": f("chicken_stand", MID),
            "alt": f("chicken_alt", MID),
        },
        "gull": {
            "stand": f("gull_stand", MID),
            "alt": f("gull_alt", MID),
        },
        "bird": {
            "stand": f("bird_stand", MID),
            "alt": f("bird_alt", MID),
        },
        "mouse": f("mouse", SMALL),
        "squirrel": {
            "stand": f("squirrel_stand", SMALL),
            "alt": f("squirrel_alt", SMALL),
        },
        "fish": {
            "stand": f("fish_stand", SMALL),
            "alt": f("fish_alt", SMALL),
        },
        "crab": f("crab", SMALL),
        "butterfly": {
            "stand": f("butterfly_stand", SMALL),
            "alt": f("butterfly_alt", SMALL),
        },
    }


def frames_to_js(frames: dict) -> str:
    """Emit JS object literal (compact, paste-ready)."""

    def fmt_entry(key: str, val: dict, indent: int = 2) -> str:
        sp = " " * indent
        if "stand" in val:
            stand = json.dumps(val["stand"], separators=(",", ":"))
            alt = json.dumps(val["alt"], separators=(",", ":"))
            return f"{sp}{key}: {{ stand: {stand}, alt: {alt} }},"
        inner = json.dumps(val, separators=(",", ":"))
        return f"{sp}{key}: {inner},"

    lines = ["const WILDLIFE_SHEET_FRAMES = {"]
    for key in sorted(frames.keys(), key=lambda k: list(frames.keys()).index(k)):
        lines.append(fmt_entry(key, frames[key]))
    lines.append("};")
    return "\n".join(lines)


def count_opaque(rows: list[str]) -> int:
    return sum(1 for row in rows for ch in row if ch != ".")


def write_preview(sheet: Image.Image, path: Path, scale: int = 8) -> None:
    """Upscaled atlas preview for silhouette QA."""
    preview = sheet.resize(
        (sheet.width * scale, sheet.height * scale), Image.Resampling.NEAREST
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(path, optimize=True)


def main() -> None:
    sheet = Image.new("RGBA", (COLS * CELL, ROWS * CELL), (0, 0, 0, 0))

    # Hand-authored
    print("Opaque pixel counts (hand-authored):")
    for name, rows in HAND_SPRITES.items():
        col, row = PLACEMENTS[name]
        img = bake_grid(grid_from_strings(rows))
        paste_tile(sheet, col, row, img)
        print(f"  {name:18s} {count_opaque(rows):3d}")

    # Tiny Farm tiles
    paste_tile(sheet, *PLACEMENTS["grazer"], load_vendor("tile_0120.png"))
    chicken = load_vendor("tile_0122.png")
    paste_tile(sheet, *PLACEMENTS["chicken_stand"], chicken)
    paste_tile(sheet, *PLACEMENTS["chicken_alt"], make_chicken_peck_alt(chicken))
    paste_tile(sheet, *PLACEMENTS["cow_unused"], load_vendor("tile_0121.png"))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(OUT_PATH, optimize=True)
    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"Wrote {OUT_PATH} ({sheet.width}x{sheet.height}, {size_kb:.1f} KB)")

    preview_path = ROOT / "simulation" / "_vendor" / "wildlife-preview-8x.png"
    write_preview(sheet, preview_path, scale=8)
    print(f"Wrote {preview_path} ({preview_path.stat().st_size / 1024:.1f} KB)")

    frames = build_frames_js()
    print("\n--- WILDLIFE_SHEET_FRAMES (paste into sprites.js) ---\n")
    print(frames_to_js(frames))
    print(f"\nKinds: {len(frames)} (expect 16)")


if __name__ == "__main__":
    main()
