"""Source-backed exchange, backpack, ladder and town fixes for 2026-10-09.

ItemInfo/giftPackage/fourFrameBag remain the reward authorities. Feature state
uses the existing local_feature_state table and joins player_store transactions.
Install once after the server has defined all handlers and make_init_sc.
"""
from functools import wraps
import json
import re
import time

import chest_reward_repairs as chests

PLANT_STORE = 5
TOWN_REBUILD_ITEM = 2051
TOWN_MAX_DURABILITY = 100
TOWN_REBUILD_COOLDOWN = 600
BATCH_LIMIT = 999


def _state(g, player, feature):
    with g.store._db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS local_feature_state '
                    '(uin INTEGER NOT NULL,feature TEXT NOT NULL,data TEXT NOT NULL,'
                    'PRIMARY KEY(uin,feature))')
        row = con.execute('SELECT data FROM local_feature_state WHERE uin=? AND feature=?',
                          (player['uin'], feature)).fetchone()
    if row:
        try:
            result = json.loads(row[0])
            if isinstance(result, dict):
                return result
        except (TypeError, ValueError):
            pass
    return {}


def _save(g, player, feature, data):
    with g.store._db() as con:
        con.execute('INSERT INTO local_feature_state(uin,feature,data) VALUES(?,?,?) '
                    'ON CONFLICT(uin,feature) DO UPDATE SET data=excluded.data',
                    (player['uin'], feature, json.dumps(data, ensure_ascii=False)))


def plant_shop_bought(g, player):
    data = _state(g, player, 'plant_ticket_shop')
    today = g._zi_today()
    if data.get('day') != today:
        data = {'day': today, 'bought': []}
        _save(g, player, 'plant_ticket_shop', data)
    return {int(value) for value in data.get('bought', ())}


def _shop_list(g, player):
    return g._exchange_itemlist_body(g.plant_coin_shop_page(player), plant_shop_bought(g, player),
                                    refresh_max=g._PLANT_COIN_SHOP_REFRESH_MAX,
                                    store_id=PLANT_STORE)


def exchange_plant_fragment(g, cmd_data, player):
    entry_id = int(g.parse_varint_fields(cmd_data).get(1, 0))
    entry = next((e for e in g.exchange_store_entries(PLANT_STORE) if e[0] == entry_id), None)
    if entry is None:
        return [('PVZ.Cmd.Cmd_ExchangeStore_Exchange_SC', b'')]
    _eid, _pos, item_id, item_num, cost_id, cost_num = entry
    with g.store.atomic_player(player):
        bought = plant_shop_bought(g, player)
        messages = [('PVZ.Cmd.Cmd_ExchangeStore_Exchange_SC', b'')]
        if entry_id not in bought and g.store.get_item_count(player['uin'], cost_id) >= cost_num:
            update = g.grant_items(player, [(cost_id, -cost_num), (item_id, item_num)])
            bought.add(entry_id)
            _save(g, player, 'plant_ticket_shop', {'day': g._zi_today(), 'bought': sorted(bought)})
            if update:
                messages.append(update)
        messages.append(('PVZ.Cmd.Cmd_ExchangeStore_ItemList_SC', _shop_list(g, player)))
        return messages


def clientdb_item_updates(g):
    """Explicit ItemInfo edits for the parent's single clientDB archive update.

    Only usable choices and fixed chests whose complete source rewards are
    modeled are enabled. Unknown or conflicting boxes are retained unchanged.
    """
    result = {}
    data = chests.config(g)
    for item_id, row in data['items'].items():
        if not chests._chest(row):
            continue
        kind, _reason = chests.contract(g, item_id)
        if kind == 'select' and row.get('isUse') == '1':
            result[item_id] = {'isMulUse': '1'}
        elif kind == 'fixed' and (row.get('isUse') == '1' or re.fullmatch(
                r'试炼之路第\d+关宝箱', row.get('name', ''))):
            loot = chests.roll(g, item_id)
            if loot and chests._validate(g, loot):
                result[item_id] = {'isUse': '1', 'isMulUse': '1'}
    return result


def _ensure_chest_flags(g):
    data = chests.config(g)
    if data.get('_rules_20261009'):
        return
    for item_id, changes in clientdb_item_updates(g).items():
        data['items'][item_id].update(changes)
    data['_rules_20261009'] = True


def _trial_boxes(g):
    data = chests.config(g)
    result = {}
    for item_id, item in data['items'].items():
        match = re.fullmatch(r'试炼之路第(\d+)关宝箱', item.get('name', ''))
        if match and item_id in data['fixed']:
            result[int(match[1])] = list(data['fixed'][item_id])
    return result


def take_trial_chest(g, cmd_data, player, hard=False):
    stage_id = int(g.parse_varint_fields(cmd_data).get(1, 0))
    key, max_key = ('tr_hard_boxes', 'tr_hard_max') if hard else ('tr_boxes', 'tr_max')
    prefix = 'NewScrewUp' if hard else 'ScrewUp'
    with g.store.atomic_player(player):
        lvl = g.load_trial_road(hard).get(stage_id)
        taken = {int(x) for x in str(player.get(key) or '').split(',') if x.strip().isdigit()}
        if not lvl or not lvl['chest'] or stage_id > int(player.get(max_key) or 0) or stage_id in taken:
            return []
        rewards = lvl['chest'] if hard else _trial_boxes(g).get(stage_id, lvl['chest'])
        if not chests._validate(g, rewards) or not chests._can_receive(g, player, rewards, 0, 0):
            return [g._logic_error(902)]
        messages = g.grant_fuben_reward(player, rewards)
        taken.add(stage_id)
        player[key] = ','.join(str(s) for s in sorted(taken))
        g.store.save_player(player['uin'], **{key: player[key]})
        messages.insert(0, ('PVZ.Cmd.Cmd_%s_TakeReward_SC' % prefix,
                           g.pb_varint_field(1, stage_id) + g._tr_reward_bytes(2, rewards)))
        return messages


def town_state(g, player):
    data = _state(g, player, 'town_durability')
    return {'durability': max(0, min(TOWN_MAX_DURABILITY, int(data.get('durability', TOWN_MAX_DURABILITY)))),
            'rebuild_at': max(0, int(data.get('rebuild_at', 0)))}


def town_durability_message(g, player, now=None):
    now = int(time.time()) if now is None else int(now)
    data = town_state(g, player)
    cooldown = max(0, data['rebuild_at'] + TOWN_REBUILD_COOLDOWN - now) if data['rebuild_at'] else 0
    return ('PVZ.Cmd.Cmd_City_GetTownDurability_SC',
            g.pb_varint_field(1, data['durability']) + g.pb_varint_field(2, TOWN_MAX_DURABILITY)
            + g.pb_varint_field(3, cooldown))


def use_town_rebuild(g, cmd_data, player):
    fields = g.parse_varint_fields(cmd_data)
    if int(fields.get(2, 1)) != 1:
        return [g._logic_error(902)]
    with g.store.atomic_player(player):
        now = int(time.time())
        data = town_state(g, player)
        # ItemInfo 2051: restore to 100, one use every ten minutes. Original
        # FontHKHB refuses a full town or a cooldown before spending the token.
        if data['durability'] >= TOWN_MAX_DURABILITY or (data['rebuild_at'] and
                now < data['rebuild_at'] + TOWN_REBUILD_COOLDOWN):
            return [g._logic_error(902), town_durability_message(g, player, now)]
        if g.store.get_item_count(player['uin'], TOWN_REBUILD_ITEM) < 1:
            return [g._logic_error(204)]
        update = g.grant_items(player, [(TOWN_REBUILD_ITEM, -1)])
        _save(g, player, 'town_durability', {'durability': TOWN_MAX_DURABILITY, 'rebuild_at': now})
        ack = ('PVZ.Cmd.Cmd_Item_Use_SC', g.pb_varint_field(1, TOWN_REBUILD_ITEM) + g.pb_varint_field(2, 1))
        return ([update] if update else []) + [town_durability_message(g, player, now), ack]


def _replace_init_durability(g, body, durability):
    """Preserve every unrelated encoded field of the original Init_SC."""
    def varint(pos):
        value = shift = 0
        while True:
            byte = body[pos]
            pos += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value, pos
            shift += 7
    pos, chunks = 0, []
    while pos < len(body):
        start = pos
        tag, pos = varint(pos)
        field, wire = tag >> 3, tag & 7
        if wire == 0:
            _value, pos = varint(pos)
        elif wire == 2:
            size, pos = varint(pos)
            pos += size
        elif wire in (1, 5):
            pos += 8 if wire == 1 else 4
        else:
            raise ValueError('unsupported Init_SC wire type')
        chunks.append(g.pb_varint_field(field, durability if field == 20 else TOWN_MAX_DURABILITY)
                      if field in (20, 21) and wire == 0 else body[start:pos])
    return b''.join(chunks)


def install(g):
    if getattr(g, '_REWARDS_RULES_20261009_INSTALLED', False):
        return
    g._REWARDS_RULES_20261009_INSTALLED = True
    g.SERVER_CONFIG_OVERRIDES['iBatchOpenChestGiftLimit'] = str(BATCH_LIMIT)

    old_list = g.exchange_store_itemlist
    @wraps(old_list)
    def shop_list(store_id, player):
        return _shop_list(g, player) if int(store_id or 0) == PLANT_STORE else old_list(store_id, player)
    g.exchange_store_itemlist = shop_list

    old_exchange = g.handle_exchangestore_exchange
    @wraps(old_exchange)
    def exchange(cmd_data, player):
        entry_id = int(g.parse_varint_fields(cmd_data).get(1, 0))
        if g.exchange_store_of_entry(entry_id) == PLANT_STORE:
            return exchange_plant_fragment(g, cmd_data, player)
        return old_exchange(cmd_data, player)

    old_use = g.handle_item_use
    @wraps(old_use)
    def use(cmd_data, player):
        if int(g.parse_varint_fields(cmd_data).get(1, 0)) == TOWN_REBUILD_ITEM:
            return use_town_rebuild(g, cmd_data, player)
        _ensure_chest_flags(g)
        return old_use(cmd_data, player)

    old_rank = g.handle_rankarena_challenge
    @wraps(old_rank)
    def challenge(cmd_data, player):
        with g.store.atomic_player(player):
            messages = old_rank(cmd_data, player)
            for name, body in messages:
                if name == 'PVZ.Cmd.Cmd_RankArena_Challenge_SC' and g.parse_varint_fields(body).get(2) == 1:
                    messages.extend(g.grant_fuben_reward(player, [(6, 2)]))
                    break
            return messages

    old_buy = g.handle_shop_buyitem
    @wraps(old_buy)
    def buy(cmd_data, player):
        with g.store.atomic_player(player):
            # Source StoreService uses shopId 113. Also accept the itemId used
            # by older local clients, mapping it to the real authored price.
            fields = g.parse_varint_fields(cmd_data)
            requested_id = int(fields.get(1, 0))
            if requested_id == TOWN_REBUILD_ITEM:
                shop_id = next((sid for sid, row in g.mall_catalog().items()
                                if row[0] == TOWN_REBUILD_ITEM), 0)
                if not shop_id:
                    return []
                cmd_data = g.pb_varint_field(1, shop_id) + g.pb_varint_field(2, int(fields.get(2, 1)))
            messages = old_buy(cmd_data, player)
            # Inventory/wallet must be processed before StoreService's Buy_SC
            # invokes the immediate use callback, otherwise it sees no token.
            if requested_id in (TOWN_REBUILD_ITEM, 113):
                messages.sort(key=lambda msg: msg[0] == 'PVZ.Cmd.Cmd_Shop_BuyItem_SC')
            return messages

    def normal_chest(cmd_data, player):
        return take_trial_chest(g, cmd_data, player)
    def hard_chest(cmd_data, player):
        return take_trial_chest(g, cmd_data, player, True)
    def durability(cmd_data, player):
        return [town_durability_message(g, player)]

    bindings = {
        'handle_exchangestore_exchange': ('ExchangeStore_Exchange', exchange),
        'handle_item_use': ('Item_Use', use),
        'handle_rankarena_challenge': ('RankArena_Challenge', challenge),
        'handle_shop_buyitem': ('Shop_BuyItem', buy),
        'handle_screwup_takereward': ('ScrewUp_TakeReward', normal_chest),
        'handle_newscrewup_takereward': ('NewScrewUp_TakeReward', hard_chest),
        'handle_city_gettown_durability': ('City_GetTownDurability', durability),
    }
    for helper, (command, handler) in bindings.items():
        setattr(g, helper, handler)
        g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_%s_CS' % command] = handler

    if hasattr(g, 'make_init_sc'):
        old_init = g.make_init_sc
        @wraps(old_init)
        def init(player):
            return _replace_init_durability(g, old_init(player), town_state(g, player)['durability'])
        g.make_init_sc = init
