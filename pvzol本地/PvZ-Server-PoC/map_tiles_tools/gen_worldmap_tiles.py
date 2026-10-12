# -*- coding: utf-8 -*-
"""Generate the world-map tileset sheets the Flash client requests from
/assets/map/maoxian_2/ (see client_asset_errors.log IMAGE_FALLBACK entries).

The embedded TMX resources in ModuleMap.swf (WorldMapRes1/WorldMapRes2, tag id
1/2 as DefineBinaryData) declare the exact sheet geometry:

    res1: cd 1024x128 | sl 1024x256 | cp 1024x128 | td 1024x128
          st 1024x128 | gm 1024x128 | xz 1024x256 (tile 128x64 / 128x128)
    res2: ts nt ks kcp nt2 gm2 nt3 1024x128 | ys sl2 xz 1024x256
          js 1024x128 (tile 128x128, single row)

Every ground tile is a full 128x64 isometric diamond with the four corners
transparent; 128x128 tiles put the diamond in the bottom half and the object
(trees / rocks / plot markers) in the top half.  Sheet sizes MUST match the
TMX declarations exactly, otherwise TiledGraphicData.UpdateTiledSetData throws
ArgumentError #2015 (copyPixels out of bounds) and the map stays black.

Art is redrawn from scratch in the bright cartoon style of
map_tiles_tools/wiki_worldmap_reference.png (green grass, blue ponds, brown
dirt, dead trees for the res2 wasteland sheet, red diamond plot markers).
"""

import os
import random

from PIL import Image, ImageDraw

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       os.pardir, os.pardir, "webroot", "assets", "map", "maoxian_2")

TW, TH = 128, 64          # isometric ground diamond
COLS = 8                  # every sheet is 8 tiles wide (1024 px)

# --- palettes ----------------------------------------------------------------
GRASS = {"base": (108, 191, 74), "light": (126, 209, 90), "dark": (86, 165, 60),
         "edge": (74, 148, 52)}
TURF = {"base": (138, 211, 95), "light": (154, 224, 112), "dark": (114, 188, 78),
        "edge": (98, 170, 66)}
DIRT = {"base": (185, 138, 86), "light": (200, 154, 102), "dark": (156, 111, 68),
        "edge": (132, 92, 56)}
WATER = {"base": (79, 168, 224), "light": (124, 196, 236), "deep": (58, 140, 200),
         "edge": (188, 230, 250)}
BUSH = {"base": (62, 143, 62), "light": (87, 169, 75)}
STONE = {"base": (168, 152, 128), "light": (186, 172, 148), "dark": (140, 124, 100),
         "edge": (112, 98, 78)}
MUD = {"base": (110, 90, 62), "light": (126, 106, 76), "dark": (90, 72, 48),
       "edge": (70, 56, 38)}
DARKDIRT = {"base": (122, 92, 58), "light": (140, 108, 70), "dark": (100, 74, 46),
            "edge": (80, 58, 36)}
DRY = {"base": (183, 168, 92), "light": (199, 184, 110), "dark": (158, 142, 74),
       "edge": (132, 118, 60)}
DEADLEAF = (128, 124, 60)
TRUNK = (139, 90, 43)
TRUNK_DARK = (110, 70, 32)
ROCK = (148, 146, 142)
ROCK_LIGHT = (176, 174, 170)
ROCK_DARK = (112, 110, 106)
RED = (214, 58, 47)
RED_LIGHT = (240, 108, 92)


def diamond_points(x0, y0):
    """Full-tile iso diamond inside the 128x64 rect at (x0, y0)."""
    return [(x0 + TW // 2, y0), (x0 + TW, y0 + TH // 2),
            (x0 + TW // 2, y0 + TH), (x0, y0 + TH // 2)]


def draw_ground(draw, x0, y0, pal, seed):
    rng = random.Random(seed)
    pts = diamond_points(x0, y0)
    draw.polygon(pts, fill=pal["base"])
    # soft north-light shading: lighter top-left facet, darker bottom-right
    draw.polygon([pts[0], pts[1], pts[2], (x0 + TW // 2, y0 + TH)],
                 fill=pal["base"])
    draw.polygon([pts[0], pts[1], (x0 + TW // 2 + TW // 4, y0 + TH // 2 + TH // 4),
                  (x0 + TW // 2, y0 + TH // 2)], fill=pal["light"])
    draw.polygon([pts[2], pts[3], (x0 + TW // 2 - TW // 4, y0 + TH // 2 + TH // 4),
                  (x0 + TW // 2, y0 + TH // 2)], fill=pal["dark"])
    # rim outline (slightly darker, keeps tiles readable but seamless)
    draw.line([pts[0], pts[1]], fill=pal["edge"])
    draw.line([pts[1], pts[2]], fill=pal["edge"])
    draw.line([pts[2], pts[3]], fill=pal["edge"])
    draw.line([pts[3], pts[0]], fill=pal["edge"])
    # speckle texture
    for _ in range(14):
        u = rng.uniform(0.18, 0.82)
        v = rng.uniform(0.18, 0.82)
        cx = x0 + TW // 2 + int((u - v) * TW // 2 * 0.9)
        cy = y0 + TH // 2 + int((u + v - 1) * TH // 2 * 0.9)
        r = rng.choice((1, 1, 2))
        col = pal["light"] if rng.random() < 0.5 else pal["dark"]
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)


def draw_grass_tufts(draw, x0, y0, seed):
    rng = random.Random(seed)
    for _ in range(5):
        u = rng.uniform(0.25, 0.75)
        v = rng.uniform(0.3, 0.7)
        cx = x0 + TW // 2 + int((u - v) * TW // 2 * 0.8)
        cy = y0 + TH // 2 + int((u + v - 1) * TH // 2 * 0.8)
        draw.line([cx - 2, cy + 2, cx - 3, cy - 3], fill=TURF["dark"])
        draw.line([cx, cy + 2, cx, cy - 4], fill=TURF["dark"])
        draw.line([cx + 2, cy + 2, cx + 3, cy - 3], fill=TURF["dark"])


def draw_small_stones(draw, x0, y0, seed):
    rng = random.Random(seed)
    for _ in range(3):
        u = rng.uniform(0.3, 0.7)
        v = rng.uniform(0.3, 0.7)
        cx = x0 + TW // 2 + int((u - v) * TW // 2 * 0.7)
        cy = y0 + TH // 2 + int((u + v - 1) * TH // 2 * 0.7)
        draw.ellipse([cx - 3, cy - 2, cx + 3, cy + 2], fill=ROCK)
        draw.ellipse([cx - 2, cy - 2, cx + 1, cy], fill=ROCK_LIGHT)


def draw_tree(draw, cx, base_y, scale, dead=False, seed=0):
    """Cartoon tree: trunk + layered canopy circles. base_y = ground line."""
    rng = random.Random(seed)
    h = int(34 * scale)
    tw_ = int(6 * scale)
    draw.polygon([(cx - tw_, base_y), (cx + tw_, base_y),
                  (cx + tw_ // 2, base_y - h), (cx - tw_ // 2, base_y - h)],
                 fill=TRUNK if not dead else ROCK_DARK)
    if dead:
        # bare branches
        draw.line([cx, base_y - h + 4, cx - int(12 * scale), base_y - h - int(10 * scale)],
                  fill=ROCK_DARK, width=max(2, int(3 * scale)))
        draw.line([cx, base_y - h + 8, cx + int(11 * scale), base_y - h - int(6 * scale)],
                  fill=ROCK_DARK, width=max(2, int(3 * scale)))
        return
    leaf_dark = (52, 118, 52)
    leaf = BUSH["base"]
    leaf_light = BUSH["light"]
    cy = base_y - h - int(6 * scale)
    r = int(15 * scale)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=leaf_dark)
    r2 = int(12 * scale)
    draw.ellipse([cx - r2 - int(8 * scale), cy + int(4 * scale) - r2,
                  cx - int(8 * scale) + r2, cy + int(4 * scale) + r2], fill=leaf)
    draw.ellipse([cx - r2 + int(9 * scale), cy + int(3 * scale) - r2,
                  cx + int(9 * scale) + r2, cy + int(3 * scale) + r2], fill=leaf)
    r3 = int(8 * scale)
    draw.ellipse([cx - r3, cy - r3 - int(7 * scale), cx + r3, cy + r3 - int(7 * scale)],
                 fill=leaf_light)


def draw_bush_patch(draw, cx, cy, seed):
    rng = random.Random(seed)
    for _ in range(3):
        dx = rng.randint(-12, 12)
        dy = rng.randint(-5, 5)
        r = rng.randint(5, 8)
        draw.ellipse([cx + dx - r, cy + dy - r, cx + dx + r, cy + dy + r],
                     fill=BUSH["base"])
        r2 = max(2, r - 3)
        draw.ellipse([cx + dx - 2 - r2, cy + dy - 2 - r2,
                      cx + dx - 2 + r2, cy + dy - 2 + r2], fill=BUSH["light"])


def draw_water(draw, x0, y0, seed):
    rng = random.Random(seed)
    pts = diamond_points(x0, y0)
    draw.polygon(pts, fill=WATER["base"])
    draw.polygon([pts[0], pts[1], (x0 + TW // 2 + TW // 4, y0 + TH // 2 + TH // 4),
                  (x0 + TW // 2, y0 + TH // 2)], fill=WATER["light"])
    # darker centre to suggest depth
    draw.polygon([(x0 + 34, y0 + 26), (x0 + 94, y0 + 26), (x0 + 64, y0 + 44)],
                 fill=WATER["deep"])
    # ripple highlights
    for _ in range(4):
        u = rng.uniform(0.2, 0.8)
        v = rng.uniform(0.2, 0.8)
        cx = x0 + TW // 2 + int((u - v) * TW // 2 * 0.7)
        cy = y0 + TH // 2 + int((u + v - 1) * TH // 2 * 0.7)
        draw.line([cx - 5, cy, cx + 5, cy], fill=WATER["edge"], width=2)
    draw.line([pts[0], pts[1]], fill=WATER["edge"], width=2)
    draw.line([pts[1], pts[2]], fill=WATER["edge"], width=2)
    draw.line([pts[2], pts[3]], fill=WATER["edge"], width=2)
    draw.line([pts[3], pts[0]], fill=WATER["edge"], width=2)


def build_sheet(name, width, height, tile_h, painter):
    """width/height MUST equal the TMX <image> declaration."""
    rows = height // tile_h
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for slot in range(COLS * rows):
        col, row = slot % COLS, slot // COLS
        painter(img, ImageDraw.Draw(img), col * TW, row * tile_h, slot, tile_h)
    path = os.path.join(OUT_DIR, name)
    img.save(path)
    print("wrote", os.path.normpath(path), img.size)


def ground_sheet(name, pal, decor=None):
    """1024x128 sheet, 128x64 tiles. Row 0 plain, row 1 decorated."""
    def painter(img, draw, x0, y0, slot, tile_h):
        draw_ground(draw, x0, y0, pal, seed=slot * 977 + 13)
        if decor and slot >= COLS:
            decor(draw, x0, y0, seed=slot * 131 + 7)
    build_sheet(name, 1024, 128, 64, painter)


def forest_sheet(name, pal, dead=False):
    """1024x256 sheet, 128x128 tiles: ground diamond bottom + trees above."""
    def painter(img, draw, x0, y0, slot, tile_h):
        gy = y0 + tile_h - TH          # ground diamond occupies the bottom half
        draw_ground(draw, x0, gy, pal, seed=slot * 811 + 5)
        rng = random.Random(slot * 613 + 29)
        n = rng.choice((2, 3))
        for i in range(n):
            cx = x0 + 28 + rng.randint(0, 70)
            base_y = gy + 30 + rng.randint(0, 16)
            scale = rng.uniform(0.8, 1.15)
            draw_tree(draw, cx, base_y, scale, dead=dead, seed=slot * 100 + i)
    build_sheet(name, 1024, 256, 128, painter)


def rock_sheet(name, pal, tall, forced_h=None):
    """ys (1024x256) / js (1024x128): rocks on stone ground.
    forced_h: the TMX <image> height overrides the natural 2-row layout;
    js.png is 1024x128 with 128x128 tiles (single row, tile fills the height)."""
    height = forced_h if forced_h else (256 if tall else 128)
    tile_h = 128 if tall else 64

    def painter(img, draw, x0, y0, slot, tile_h):
        gy = y0 + tile_h - TH
        draw_ground(draw, x0, gy, pal, seed=slot * 733 + 11)
        rng = random.Random(slot * 419 + 3)
        for i in range(rng.choice((1, 2))):
            cx = x0 + 34 + rng.randint(0, 60)
            base_y = gy + 34 + rng.randint(0, 12)
            s = rng.uniform(0.8, 1.2) * (1.25 if tall else 0.8)
            w, h = int(18 * s), int(14 * s)
            draw.ellipse([cx - w, base_y - h * 2, cx + w, base_y], fill=ROCK)
            draw.ellipse([cx - w // 2, base_y - int(h * 1.6), cx + w // 2,
                          base_y - h // 2], fill=ROCK_LIGHT)
            draw.ellipse([cx - w, base_y - h, cx + w, base_y + h // 3],
                         fill=ROCK_DARK)
    build_sheet(name, 1024, height, tile_h, painter)


def xz_sheet():
    """1024x256, 128x128 tiles. City-plot markers:
    row 0 = occupied-city brown platform; row 1 = red diamond plot outlines
    (slot 8 has type=2, slot 9 has type=1 in the TMX)."""
    def painter(img, draw, x0, y0, slot, tile_h):
        gy = y0 + tile_h - TH
        if slot < COLS:
            # platform base for an existing city
            draw_ground(draw, x0, gy, DIRT, seed=slot * 311 + 17)
            inner = [(x0 + TW // 2, gy + 8), (x0 + TW - 14, gy + TH // 2),
                     (x0 + TW // 2, gy + TH - 8), (x0 + 14, gy + TH // 2)]
            draw.polygon(inner, outline=(240, 208, 160), width=2)
        else:
            # empty plot: red iso outline floating over transparent ground
            m = 6
            pts = [(x0 + TW // 2, gy + m), (x0 + TW - m, gy + TH // 2),
                   (x0 + TW // 2, gy + TH - m), (x0 + m, gy + TH // 2)]
            if slot == 8:
                draw.polygon(pts, fill=(196, 148, 96))
            draw.line(pts + [pts[0]], fill=RED, width=4)
            m2 = 12
            pts2 = [(x0 + TW // 2, gy + m2), (x0 + TW - m2, gy + TH // 2),
                    (x0 + TW // 2, gy + TH - m2), (x0 + m2, gy + TH // 2)]
            draw.line(pts2 + [pts2[0]], fill=RED_LIGHT, width=2)
    build_sheet("xz.png", 1024, 256, 128, painter)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    # --- res1 (primary bright world) ---
    ground_sheet("cd.png", GRASS, decor=draw_grass_tufts)     # 草地
    forest_sheet("sl.png", GRASS)                              # 森林
    ground_sheet("cp.png", TURF, decor=draw_grass_tufts)       # 草皮
    ground_sheet("td.png", DIRT, decor=draw_small_stones)      # 土地
    build_sheet("st.png", 1024, 128, 64,
                lambda i, d, x, y, s, t: draw_water(d, x, y, s))  # 水塘
    def bush_painter(img, draw, x0, y0, slot, tile_h):
        draw_ground(draw, x0, y0, GRASS, seed=slot * 977 + 13)
        draw_bush_patch(draw, x0 + TW // 2, y0 + TH // 2, seed=slot * 251 + 9)
    build_sheet("gm.png", 1024, 128, 64, bush_painter)
    xz_sheet()                                                 # 城池位

    # --- res2 (wasteland / dark world) ---
    ground_sheet("ts.png", STONE, decor=draw_small_stones)     # 土石
    ground_sheet("nt.png", MUD, decor=draw_small_stones)       # 泥塘
    rock_sheet("ys.png", MUD, tall=True)                       # 岩石
    def ore_painter(img, draw, x0, y0, slot, tile_h):
        draw_ground(draw, x0, y0, DIRT, seed=slot * 353 + 19)
        rng = random.Random(slot * 87 + 1)
        for _ in range(4):
            u, v = rng.uniform(0.3, 0.7), rng.uniform(0.3, 0.7)
            cx = x0 + TW // 2 + int((u - v) * TW // 2 * 0.7)
            cy = y0 + TH // 2 + int((u + v - 1) * TH // 2 * 0.7)
            col = (86, 196, 220) if rng.random() < 0.5 else (232, 200, 92)
            draw.polygon([(cx, cy - 4), (cx + 3, cy), (cx, cy + 4), (cx - 3, cy)],
                         fill=col)
    build_sheet("ks.png", 1024, 128, 64, ore_painter)          # 矿石
    forest_sheet("sl2.png", MUD, dead=True)                    # 枯森林
    ground_sheet("kcp.png", DRY, decor=draw_grass_tufts)       # 枯草皮
    ground_sheet("nt2.png", DARKDIRT, decor=draw_small_stones) # 泥土
    def bush2_painter(img, draw, x0, y0, slot, tile_h):
        draw_ground(draw, x0, y0, DARKDIRT, seed=slot * 977 + 13)
        draw_bush_patch(draw, x0 + TW // 2, y0 + TH // 2, seed=slot * 251 + 9)
    build_sheet("gm2.png", 1024, 128, 64, bush2_painter)       # 灌木(暗)
    rock_sheet("js.png", STONE, tall=True, forced_h=128)       # 巨石
    ground_sheet("nt3.png", DARKDIRT, decor=draw_small_stones) # 泥土1

    print("all tiles written to", os.path.normpath(OUT_DIR))


if __name__ == "__main__":
    main()
