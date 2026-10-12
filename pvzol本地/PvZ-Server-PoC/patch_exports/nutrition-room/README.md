# Nutrition room asset and SuperTrade patch

This patch only covers Nutrition room reagent icons and SuperTrade failure handling.

## Reagent icon mapping

The PNGs in `webroot/image/item` are 50×50 versions cropped from the user-provided atlas `fab1a93aee43661dc5f46a669fbe1df2.png`. The atlas cards were 64×64 at x positions `8 + 73×column` and y positions `1363 + 68×qualityRow`; icons were resized to the existing 50×50 item-icon convention.

The atlas column order maps to reagent ID suffixes as follows:

| Atlas column | Label | ID suffix |
|---:|---|---:|
| 1 | 力量试剂 | 01 |
| 2 | 生命试剂 | 03 |
| 3 | 精准试剂 | 05 |
| 4 | 狂暴试剂 | 07 |
| 5 | 钢铁试剂 | 02 |
| 6 | 智慧试剂 | 04 |
| 7 | 敏捷试剂 | 06 |
| 8 | 坚韧试剂 | 08 |

The five rows are tiers 1–5 (green, blue, purple, orange, gold). IDs use `60<tier><suffix>`, e.g. `60101.png` is tier 1 力量 and `60508.png` is tier 5 坚韧. The two currency icons are `60001.png` (试剂残渣, first tile) and `60000.png` (黄金原液, second tile).

The source atlas has no art in the orange 敏捷 tile (`60406`). On 2026-09-27, the player supplied the missing 64×64 icon. Its original is saved as `60406_user_source_64x64.png`; `webroot/image/item/60406.png` is the corresponding 50×50 game icon. The former transparent placeholder is backed up under `PvZ-Server-PoC/backups/nutrition-agility-icon-20260927/`. The other previous item icons remain under `PvZ-Server-PoC/backups/nutrition-icons-before-20260926/`.

## SuperTrade handling

`Cmd_Nutrition_SuperTrade_CS` defines `costType` field 1 and `changeType` field 2. Its response defines `oldExp`, `newExp`, `goldReagentNum`, `cost`, repeated `qualityNum`, `stopReason`, `activateList`, and `freeList`. The handler previously returned no message when funds or bag capacity failed, and it reported `stopReason=0` after a partially completed trade stopped at a full bag.

The server now returns existing `Cmd_LogicError_SC` messages for localized error IDs 202 (金币不足), 6301 (试剂背包已满), and 6316 (invalid reagent/trade configuration). SuperTrade can continue dissolving selected low-quality results while the reagent bag is full; it stops before charging for the first higher-quality result that cannot fit, returns `SuperTrade_SC` so the client opens its result panel, sets `stopReason=6301`, refreshes the reagent bag model, and sends the localized full-bag message after the result. If no result can be processed, only the localized error is returned.

## Verification

Run from `PvZ-Server-PoC`:

```powershell
python -m py_compile pvz_poc_server.py tests\test_nutrition_supertrade_errors.py
python tests\test_nutrition_supertrade_errors.py
```

The test checks insufficient funds, an already-full bag, and a partial trade stopping exactly when the final reagent slot is filled.
