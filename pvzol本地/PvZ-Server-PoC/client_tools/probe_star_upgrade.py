# -*- coding: utf-8 -*-
"""诊断升星失败: 条件满足却升不了星.
1. dump CardUpgradeTemplate.txt 原始表头+行数
2. load_card_upgrade_recipes() 解析结果: 数量+样例
3. 交叉核对: 每条配方的 newCardId 是否在 _BATTLE_CARDS (L4384 静默拒绝点)
4. 反向: _BATTLE_CARDS 里有哪些卡有配方/没配方
5. forge 数据源: get_card_forge_level 语义
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pvz_poc_server as srv

rows = srv._read_clientdb_table("CardUpgradeTemplate.txt")
print("=== raw CardUpgradeTemplate.txt ===")
print("rows:", len(rows))
if rows:
    print("header:", rows[0])
    for r in rows[1:6]:
        print("row:", r)

recipes = srv.load_card_upgrade_recipes()
print("\n=== parsed recipes ===")
print("count:", len(recipes))
sample = sorted(recipes.items())[:8]
for src, (need_level, need_forge, item_id, item_num, coin_num, new_id) in sample:
    print("  src=%d -> lvl>=%d forge>=%d item(%d)x%d coin=%d new=%d"
          % (src, need_level, need_forge, item_id, item_num, coin_num, new_id))

srv.load_battle_cards()
missing_new = [(src, new_id) for src, rec in recipes.items()
               for new_id in [rec[5]] if new_id and new_id not in srv._BATTLE_CARDS]
print("\n=== newCardId not in _BATTLE_CARDS (silent reject) ===")
print("missing:", len(missing_new), "of", len(recipes))
for src, new_id in missing_new[:20]:
    rec = recipes[src]
    print("  src=%d lvl>=%d forge>=%d item(%d)x%d coin=%d -> new=%d MISSING"
          % (src, rec[0], rec[1], rec[2], rec[3], rec[4], new_id))

no_recipe_cards = [cid for cid in sorted(srv._BATTLE_CARDS)
                   if cid in recipes]
star_cards = [cid for cid in sorted(srv._BATTLE_CARDS)
              if (srv._BATTLE_CARDS[cid].get("star") or 0) >= 2]
print("\n=== battle cards with star>=2 but no recipe keyed on them ===")
n = [cid for cid in star_cards if cid not in recipes]
print("star>=2 cards:", len(star_cards), " lacking recipe:", len(n), n[:15])

# forge 数据源
print("\n=== forge source ===")
import inspect
src = inspect.getsource(srv.store.get_card_forge_level)
print(src)
