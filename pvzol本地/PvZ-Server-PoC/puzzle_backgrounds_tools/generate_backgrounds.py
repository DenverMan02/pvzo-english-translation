#!/usr/bin/env python3
"""Design and render the missing colored card-puzzle backing images.

Default behavior is intentionally preview-only. The Flash client requests
PUZZLE_BG_<quality>_<rank>.png for quality 1..7 and rank suffix 0..6; after the
user approves the palette, pass --all-assets to install all 49 runtime PNGs.
"""
from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "webroot" / "image" / "puzzle"
TOOL_DIR = Path(__file__).resolve().parent
SOURCE_DIR = TOOL_DIR / "sources"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
WIDTH, HEIGHT = 405, 338
# label, light rim, panel base, darker ray, lighter ray, muted outline
PALETTES = {
    1: ("white",  "#f5f7f3", "#f0f2ee", "#bfc8c6", "#d7dedd", "#8a9d9b"),
    2: ("green",  "#edfff0", "#a9e9bd", "#6cbf8e", "#83d0a1", "#79ac8d"),
    3: ("blue",   "#e4f5f4", "#a0d4d8", "#65abb3", "#b4e0e0", "#557f82"),
    4: ("purple", "#fbf2ff", "#d9c4ed", "#a17bcb", "#b695d9", "#a28bb2"),
    5: ("orange", "#fff8ee", "#edc699", "#ce8e52", "#dfa76f", "#aa8b6e"),
    6: ("gold",   "#fffceb", "#eddf8c", "#c6ad3d", "#dac45b", "#a89b63"),
    7: ("red",    "#fff4f0", "#eab0b1", "#c86d79", "#d98890", "#a77d80"),
}


def ray_endpoint(angle: float, bounds: tuple[float, float, float, float], center: tuple[float, float]) -> tuple[float, float]:
    left, top, right, bottom = bounds
    cx, cy = center
    dx, dy = math.cos(angle), math.sin(angle)
    ts = []
    if dx < -1e-8:
        ts.append((left - cx) / dx)
    elif dx > 1e-8:
        ts.append((right - cx) / dx)
    if dy < -1e-8:
        ts.append((top - cy) / dy)
    elif dy > 1e-8:
        ts.append((bottom - cy) / dy)
    t = min(value for value in ts if value > 0)
    return cx + t * dx, cy + t * dy


def make_svg(quality: int) -> str:
    label, rim, base, ray_dark, ray_light, outline = PALETTES[quality]
    bounds = (18.0, 18.0, 387.0, 320.0)
    center = (202.5, 320.0)
    # Eight broad fan sections converge just above the lower edge.
    angles = [-153, -135, -117, -99, -81, -63, -45, -27, -9]
    endpoints = [ray_endpoint(math.radians(a), bounds, center) for a in angles]
    wedges = []
    for index, (a, b) in enumerate(zip(endpoints, endpoints[1:])):
        color = ray_dark if index % 2 == 0 else ray_light
        opacity = "0.76" if index % 2 == 0 else "0.60"
        wedges.append(
            f'<path d="M{center[0]:.2f},{center[1]:.2f} '
            f'L{a[0]:.2f},{a[1]:.2f} L{b[0]:.2f},{b[1]:.2f} Z" '
            f'fill="{color}" opacity="{opacity}"/>'
        )
    ray_markup = "\n    ".join(wedges)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
  <defs>
    <clipPath id="panel"><rect x="17" y="17" width="371" height="304" rx="8"/></clipPath>
    <linearGradient id="rim" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#ffffff" stop-opacity="0.95"/>
      <stop offset="0.56" stop-color="{rim}" stop-opacity="0.88"/>
      <stop offset="1" stop-color="{outline}" stop-opacity="0.20"/>
    </linearGradient>
    <linearGradient id="panelSheen" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff" stop-opacity="0.18"/>
      <stop offset="0.55" stop-color="#ffffff" stop-opacity="0"/>
      <stop offset="1" stop-color="{outline}" stop-opacity="0.10"/>
    </linearGradient>
  </defs>
  <!-- A clearly visible pale color rim with a thin muted keyline, not a wooden frame. -->
  <rect x="0.75" y="0.75" width="403.5" height="336.5" rx="11" fill="{rim}" stroke="{outline}" stroke-width="1.5"/>
  <rect x="4.5" y="4.5" width="396" height="329" rx="9" fill="url(#rim)" stroke="#ffffff" stroke-opacity="0.88" stroke-width="1.2"/>
  <g clip-path="url(#panel)">
    <rect x="17" y="17" width="371" height="304" fill="{base}"/>
    {ray_markup}
    <rect x="17" y="17" width="371" height="304" fill="url(#panelSheen)"/>
  </g>
  <rect x="17.5" y="17.5" width="370" height="303" rx="8" fill="none" stroke="{rim}" stroke-opacity="0.9" stroke-width="1.2"/>
</svg>
'''


def render(svg_path: Path, png_path: Path) -> None:
    if not CHROME.is_file():
        raise SystemExit(f"Chrome renderer not found: {CHROME}")
    result = subprocess.run([
        str(CHROME), "--headless", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
        f"--window-size={WIDTH},{HEIGHT}", f"--screenshot={png_path}", svg_path.resolve().as_uri(),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if result.returncode != 0 or not png_path.is_file():
        raise SystemExit(f"Could not render {svg_path.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-assets", action="store_true", help="install all quality/rank PNGs after the palette is approved")
    parser.add_argument("--force", action="store_true", help="replace existing runtime PNGs when using --all-assets")
    args = parser.parse_args()
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    blue_svg = SOURCE_DIR / "PUZZLE_BG_3_blue-prototype.svg"
    blue_png = TOOL_DIR / "blue_preview.png"
    blue_svg.write_text(make_svg(3), encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="pvz-puzzle-blue-") as temp:
        blue_temp = Path(temp) / blue_png.name
        render(blue_svg, blue_temp)
        shutil.copyfile(blue_temp, blue_png)
        print(f"Blue preview only: {blue_png} ({WIDTH}x{HEIGHT})")
        if args.all_assets:
            written = 0
            for quality in PALETTES:
                svg_path = SOURCE_DIR / f"PUZZLE_BG_{quality}.svg"
                svg_path.write_text(make_svg(quality), encoding="utf-8")
                rendered = Path(temp) / f"quality-{quality}.png"
                render(svg_path, rendered)
                for rank in range(7):
                    target = OUT_DIR / f"PUZZLE_BG_{quality}_{rank}.png"
                    if target.exists() and not args.force:
                        continue
                    shutil.copyfile(rendered, target)
                    written += 1
            print(f"Installed {written} runtime puzzle backplates into {OUT_DIR}")

if __name__ == "__main__":
    main()
