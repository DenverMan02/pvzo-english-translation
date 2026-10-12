Generate the missing card-puzzle colored backplates. The approved design uses
the blue fan-ray pattern and the wider pale rim from `blue_preview.png`.

The 49 runtime assets are installed under `webroot/image/puzzle/` at 405x338:
seven card qualities, each with the seven rank suffixes requested by the client.
The first set made before the rim was widened is preserved under
`PvZ-Server-PoC/backups/puzzle-background-before-border-adjustment-20260926/`.

The default command recreates only `blue_preview.png`; use the following to
regenerate/install all 49 assets after changing the design:

  python PvZ-Server-PoC/puzzle_backgrounds_tools/generate_backgrounds.py --all-assets

The client requests `image/puzzle/PUZZLE_BG_<quality>_<rank>.png`; qualities map
from cardPuzzle.txt as 1 white, 2 green, 3 blue, 4 purple, 5 orange, 6 gold,
7 red. Rank URL suffixes are 0..6 (ranks above 6 are clamped to 6).
