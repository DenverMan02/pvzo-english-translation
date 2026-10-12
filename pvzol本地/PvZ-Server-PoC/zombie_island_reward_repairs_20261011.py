"""Use the existing 4071 chest contract for island guard-chest loot.

ItemInfo calls 4071 a zombie-island clear reward and says it produces ONE
random item. The island handler expanded the same local candidate pool into
all four rewards, disagreeing with the repaired backpack handler. Both paths
now use chest_reward_repairs.roll, while the island's separate medal bonus and
its other caches retain their existing local rules. The shipped client has no
4071 online drop weights; this does not invent a new official drop table.
"""
from collections import Counter
from functools import wraps

import chest_reward_repairs as chests


def guard_box_rewards(g, mode, level):
    level = g._zi_clamp_level(level)
    boost = 2 if int(mode) == g._ZI_ADV else 1
    loot = chests.roll(g, g._ZI_CHEST_ITEM)
    if not loot or not chests._validate(g, loot):
        raise ValueError('zombie-island chest has no valid ItemInfo reward contract')
    total = Counter({g._ZI_MEDAL_ITEM: (20 + 5 * level) * boost})
    for item_id, amount in loot:
        total[item_id] += amount
    return list(total.items())


def install(g):
    if getattr(g, '_ZOMBIE_ISLAND_REWARD_REPAIRS_20261011_INSTALLED', False):
        return
    g._ZOMBIE_ISLAND_REWARD_REPAIRS_20261011_INSTALLED = True
    previous = g._zi_box_rewards

    @wraps(previous)
    def box_rewards(mode, level, box_id):
        if int(box_id) == 1:
            return guard_box_rewards(g, mode, level)
        return previous(mode, level, box_id)

    g._zi_box_rewards = box_rewards
