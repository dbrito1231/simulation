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

# Tiny Farm idiom palette (outline RGB ≈ 63,38,49)
OUT = (63, 38, 49, 255)
CLR = {
    ".": (0, 0, 0, 0),
    "k": OUT,
    "w": (255, 255, 255, 255),
    "l": (192, 203, 220, 255),
    "m": (139, 155, 180, 255),
    "d": (82, 96, 124, 255),
    "h": (90, 105, 136, 255),
    "b": (192, 203, 220, 255),  # bird/gull body
    "j": (160, 120, 90, 255),  # deer tan
    "J": (200, 160, 120, 255),  # deer light belly
    "K": (120, 85, 60, 255),  # deer dark legs
    "S": (180, 120, 70, 255),  # squirrel fur
    "T": (180, 100, 50, 255),  # fox bushy tail
    "F": (100, 130, 160, 255),  # seal flipper
    "W": (139, 115, 85, 255),  # owl brown
    "U": (180, 160, 130, 255),  # owl face cream
    "n": (62, 78, 110, 255),  # nose dark
    "o": (225, 154, 101, 255),  # fox orange
    "p": (247, 194, 130, 255),  # fox light belly
    "r": (195, 75, 53, 255),  # red comb / crab
    "y": (227, 134, 40, 255),  # beak / feet
    "g": (120, 145, 85, 255),  # turtle shell
    "s": (85, 115, 60, 255),  # turtle dark shell
    "e": (140, 175, 95, 255),  # turtle head
    "f": (100, 180, 220, 255),  # fish blue
    "t": (60, 130, 180, 255),  # fish tail
    "c": (220, 90, 80, 255),  # crab red
    "u": (180, 60, 55, 255),  # crab dark
    "a": (170, 100, 210, 255),  # butterfly wing A
    "v": (130, 70, 180, 255),  # butterfly wing B
    "i": (255, 112, 109, 255),  # pink nose / accents
    "q": (38, 43, 68, 255),  # deep shadow
    "x": (110, 85, 65, 255),  # boar dark brown
    "z": (240, 230, 210, 255),  # tusk / highlight
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
    "deer": [
        "................",
        "....k.kk........",
        "...k.k.k.k......",
        "....k.k.k.......",
        ".....k.k........",
        "....kjjjjjjjk...",
        "...kjjJjjjJjjk..",
        "..kjjjjjjjjjjjk.",
        ".kjjjjjjjjjjjjjk",
        "kjjjjjjjjjjjjjjk",
        "kjjjjjhhnjjjjjjk",
        ".kjjjjjjjjjjjjk.",
        "..kK......Kk....",
        "...K......K.....",
        "...K......K.....",
        "................",
    ],
    "boar": [
        "................",
        "....k.k......k.k",
        "...k.k.k....k.k.",
        "....k.k......k.k",
        "....kxxxxxxxxk..",
        "...kxxxxxxxyxxk.",
        "..kxxxxxxxxxxxk.",
        ".kxxxxxxxxxxxxxk",
        "kxxxxxxxxxxxxxxk",
        "kxxxxxxhnxxxxxxk",
        ".kxxxxxxxxxxxxk.",
        "..kxx......xxk..",
        "...xx......xx...",
        "................",
        "................",
        "................",
    ],
    "seal": [
        "................",
        ".....kkkkkkk....",
        "....klllllllk...",
        "...klllllllllk..",
        "..klllllllllllk.",
        ".klllllllllllllk",
        "kllllllllllllllk",
        "klllllllnllllllk",
        "kllllllFlllllllk",
        ".kllllllllllllk.",
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
        ".kooooooooopk...",
        "koooppppppnnk...",
        "koooppppppnnk...",
        "kooooooooooook..",
        "kooooooooooTTk..",
        ".kooooooddddok..",
        "..kdddd..ddddk..",
        "...ddd....ddd...",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
    "owl_stand": [
        "................",
        ".....kkkk.......",
        "....kWUUUWk.....",
        "...kWyiiiyWk....",
        "..kWUUUUUUUWk...",
        "..kWWWWWWWWk....",
        ".kWWWWWWWWWWk...",
        ".kWWWWWWWWWWk...",
        "..kWWWWWWWWk....",
        "...kWWWWWWk.....",
        "....kdd.ddk.....",
        "................",
        "................",
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
        "..kWWWWWWWWk....",
        ".kWWWWWWWWWWk...",
        ".kWWWWWWWWWWk...",
        "..kWWWWWWWWk....",
        "...kWWWWWWk.....",
        "....kdd.ddk.....",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
    "turtle": [
        "................",
        "....kkkkkkk.....",
        "...kgssssssgk...",
        "..kgshhhhhsgk...",
        "..kgggggggggk...",
        ".kgggggggggggk..",
        ".kggggggggeek...",
        "..kggggggggk....",
        "...kd....dk.....",
        "...kd....dk.....",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
    "rabbit_stand": [
        "................",
        "....k.....k.....",
        "....w.....w.....",
        "....w.....w.....",
        "...kwwwwwwwwk...",
        "..kwwwniwwwwk...",
        ".kwwwwwwwwwwwk..",
        "kwwwwwwwwwwwwwk.",
        "kwwwwwwwwwwwwwk.",
        ".kwwwwwwwwwwwk..",
        "..kdd.....ddk...",
        "...dd.....dd....",
        "................",
        "................",
        "................",
        "................",
    ],
    "rabbit_alt": [
        "................",
        "....k.....k.....",
        ".....w...w......",
        "....w.w.........",
        "...kwwwwwwwwk...",
        "..kwwwniwwwwk...",
        ".kwwwwwwwwwwwk..",
        "kwwwwwwwwwwwwwk.",
        "kwwwwwwwwwwwwwk.",
        ".kwwwwwwwwwwwk..",
        "..kdd.....ddk...",
        "...dd.....dd....",
        "................",
        "................",
        "................",
        "................",
    ],
    "gull_stand": [
        "................",
        "......kkkkkk....",
        ".....kwllllk....",
        "....kllmmbbek...",
        "...kllllllllk...",
        "..klllllllllk...",
        ".klllllllllllk..",
        ".klllllllllllk..",
        "..klllllllllk...",
        "...kllllllk.....",
        "....kdd.ddk.....",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
    "gull_alt": [
        "................",
        "......kkkkkk....",
        ".....kmmlllk....",
        "....kllwwbbek...",
        "...kllllllllk...",
        "..klllllllllk...",
        ".klllllllllllk..",
        ".klllllllllllk..",
        "..klllllllllk...",
        "...kllllllk.....",
        "....kdd.ddk.....",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
    "bird_stand": [
        "................",
        "......kkkkkk....",
        ".....klllllk....",
        "....kllmmbbek...",
        "...kllllllllk...",
        "..klllllllllk...",
        ".klllllllllllk..",
        ".klllllllllllk..",
        "..klllllllllk...",
        "...kllllllk.....",
        "....kdd.ddk.....",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
    "bird_alt": [
        "................",
        "......kkkkkk....",
        ".....kmmlllk....",
        "....kllwwbbek...",
        "...kllllllllk...",
        "..klllllllllk...",
        ".klllllllllllk..",
        ".klllllllllllk..",
        "..klllllllllk...",
        "...kllllllk.....",
        "....kdd.ddk.....",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
    "mouse": [
        "................",
        "....kk...kk.....",
        "....ll...ll.....",
        "...kllllllllk...",
        "..tkllllnlllk...",
        "..klllllllllk...",
        ".kllllllllllk...",
        "..klllllllllk...",
        "...kllllllllk...",
        "....kdd...ddk...",
        ".....dd...dd....",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
    "squirrel_stand": [
        "................",
        "kkk.............",
        "kSSS............",
        ".kSSS...........",
        "..kSSSSSSSSSk...",
        "...kSSSSSSSSSk..",
        "....kSSSSSSSSk..",
        ".....kSSSSSSSSk.",
        "......kSSSSSSSk.",
        ".......kSSSSSSk.",
        "........kdd..ddk",
        ".........dd..dd.",
        "................",
        "................",
        "................",
        "................",
    ],
    "squirrel_alt": [
        "................",
        "kkk.............",
        "kSSS.t..........",
        ".kSSS...........",
        "..kSSSSSSSSSk...",
        "...kSSSSSSSSSk..",
        "....kSSSSSSSSk..",
        ".....kSSSSSSSSk.",
        "......kSSSSSSSk.",
        ".......kSSSSSSk.",
        "........kdd..ddk",
        ".........dd..dd.",
        "................",
        "................",
        "................",
        "................",
    ],
    "fish_stand": [
        "................",
        "...kkkkkk.......",
        "..kffffffffk....",
        ".tkfffffffffnk..",
        "kfffffffffffffk.",
        "kfffffffffffffk.",
        ".kfffffffffffk..",
        "..kfffffffffk...",
        "...kffffffffk...",
        "....kkkkkk......",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
    "fish_alt": [
        "................",
        "...kkkkkk.......",
        "..kffffffffk....",
        "..tkfffffffffnk.",
        ".kfffffffffffffk",
        ".kfffffffffffffk",
        "..kfffffffffffk.",
        "...kfffffffffk..",
        "....kffffffffk..",
        ".....kkkkkk.....",
        "................",
        "................",
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
        ".ckccccccccccck.",
        "..kcccccccccck..",
        "...kuuuuuuuuk...",
        "....uuuuuuuuu...",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
    ],
    "butterfly_stand": [
        "................",
        ".aaa....k....bbb",
        "aaaak...k...bbbb",
        "aaaak...k...bbbb",
        "aaaak...k...bbbb",
        "aaaak...k...bbbb",
        ".aaa....k....bbb",
        "......k.k.......",
        "......k.k.......",
        "................",
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
        ".....kbbbk......",
        "....kbbbbk......",
        "....kbbbbk......",
        ".....kbbbk......",
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
MID = {"destW": 24, "destH": 24}
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


def main() -> None:
    sheet = Image.new("RGBA", (COLS * CELL, ROWS * CELL), (0, 0, 0, 0))

    # Hand-authored
    for name, rows in HAND_SPRITES.items():
        col, row = PLACEMENTS[name]
        paste_tile(sheet, col, row, bake_grid(grid_from_strings(rows)))

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

    frames = build_frames_js()
    print("\n--- WILDLIFE_SHEET_FRAMES (paste into sprites.js) ---\n")
    print(frames_to_js(frames))
    print(f"\nKinds: {len(frames)} (expect 16)")


if __name__ == "__main__":
    main()
