"""Original client market rules, with bilingual English/Chinese clientDB support.
BattleCardMarketSetting.txt supplies plant probabilities and pack guarantees.
It does not ship server-only miscellaneous weights/amounts. Valid ItemInfo
templates below use uniform local weights, one item each, or 10,000 coins.
"""
import re

SMALL_WEIGHTS = ((4, 1), (3, 1), (2, 3), (1, 15), (None, 80))
SMALL_PITY_WEIGHTS = ((4, 8), (3, 25), (2, 35), (1, 32))
BIG_WEIGHTS = ((4, 10), (3, 25), (2, 35), (1, 30))
LOCAL_MISC_ITEMS = ((1, 10000), (2002, 1), (2003, 1), (2004, 1),
                    (2015, 1), (2016, 1), (2017, 1), (2130, 1),
                    (2131, 1), (2101, 1), (2102, 1), (2103, 1), (210, 1))

def quality(g, weights):
    roll = g.random.randint(1, sum(weight for _, weight in weights))
    for rarity, weight in weights:
        roll -= weight
        if roll <= 0:
            return rarity
    raise RuntimeError("Invalid market probability table")

def reward_data(g):
    cached = getattr(g, '_CARD_MARKET_REWARD_DATA', None)
    if cached is not None:
        return cached
    rows = g._read_clientdb_table('ItemInfo.txt')
    if not rows:
        raise RuntimeError('Missing ItemInfo market reward templates')
    ci = {name: index for index, name in enumerate(rows[0])}
    items = {int(row[ci['id']]): dict(zip(rows[0], row)) for row in rows[1:]}
    settings = g._read_clientdb_table('BattleCardMarketSetting.txt')

    # Match both Chinese '(3星)' and English '(3-Star)'
    rare_names = {name.strip() for row in settings[1:] for value in row
                  for name in re.findall(r'([^<>]+?)\s*\((?:3星|3-Star)\)', value)}

    plants = [(bid, cid) for bid, cid in g.card_market_pool()
              if 1 <= g._BATTLE_CARDS[cid]['rarity'] <= 4
              and 1 <= g._BATTLE_CARDS[cid]['star'] <= 3
              and (g._BATTLE_CARDS[cid]['rarity'] != 4
                   or g._BATTLE_CARDS[cid]['star'] != 3
                   or not rare_names
                   or g._BATTLE_CARDS[cid]['name'] in rare_names)]

    fragments = [(iid, 1) for iid, row in items.items()
                 if int(row['type']) == 5 and int(row['subType']) == 501
                 and 1 <= int(row['quality']) <= 4]

    # Match both Chinese '酒吧' and English 'Tavern' / 'Bar'
    rare_fragments = [(iid, 1) for iid, row in items.items()
                      if int(row['type']) == 5 and int(row['subType']) == 501
                      and int(row['quality']) == 4
                      and row.get('gainWay', '') in ('酒吧', 'Tavern', 'Bar')]
    if not rare_fragments:
        rare_fragments = [(iid, 1) for iid, row in items.items()
                          if int(row['type']) == 5 and int(row['subType']) == 501
                          and int(row['quality']) == 4]

    misc = [entry for entry in LOCAL_MISC_ITEMS if entry[0] in items] + rare_fragments

    # Match both Chinese '杨桃' and English 'Starfruit' (or card ID 8503 / 58503)
    first = [(bid, 1) for bid, cid in plants
             if (g._BATTLE_CARDS[cid]['name'] in ('杨桃', 'Starfruit') or cid in (8503, 58503, 6203, 56203))
             and g._BATTLE_CARDS[cid]['star'] == 3]
    if not first:
        first = [(bid, 1) for bid, cid in plants if g._BATTLE_CARDS[cid]['star'] == 3]

    if not all((plants, fragments, rare_fragments, misc, first)):
        raise RuntimeError('Incomplete original market reward templates')
    cached = dict(plants=plants, fragments=fragments, rare_fragments=rare_fragments,
                  misc=misc, first=first)
    g._CARD_MARKET_REWARD_DATA = cached
    return cached

def plant(g, rarity=None, star=None):
    pool = [(bid, 1) for bid, cid in reward_data(g)['plants']
            if (rarity is None or g._BATTLE_CARDS[cid]['rarity'] == rarity)
            and (star is None or g._BATTLE_CARDS[cid]['star'] == star)]
    if not pool:
        pool = [(bid, 1) for bid, cid in reward_data(g)['plants']
                if (star is None or g._BATTLE_CARDS[cid]['star'] == star)]
    if not pool:
        pool = reward_data(g)['plants']
    return g.random.choice(pool)

def roll_rewards(g, gid, player):
    data = reward_data(g)
    if gid == 1:
        count = int(player.get('card_market_single_draws') or 0)
        if count == 0:
            return [g.random.choice(data['first'])]
        pity = (count + 1) % 10 == 0
        rarity = quality(g, SMALL_PITY_WEIGHTS if pity else SMALL_WEIGHTS)
        if rarity is None:
            return [g.random.choice(data['misc'])]
        return [plant(g, rarity, 3 if pity or rarity == 4 else None)]
    if gid == 2:
        rarity = 4 if int(player.get('card_market_lucky_flower_num') or 0) >= 9 else quality(g, BIG_WEIGHTS)
        return ([plant(g, rarity, 3), plant(g, star=2), plant(g, star=1),
                 g.random.choice(data['rare_fragments']), (2103, 1), (210, 1)]
                + [g.random.choice(data['misc']) for _ in range(4)])
    if gid == 3:
        _, used, next_at = g._card_market_friend_state(player, int(g.time.time()))
        first = plant(g) if used == 0 and next_at <= int(g.time.time()) else g.random.choice(data['fragments'])
        return [first] + [g.random.choice(data['misc']) for _ in range(4)]
    raise ValueError('Unknown card market group')
