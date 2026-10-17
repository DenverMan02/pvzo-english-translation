"""Original GoldExchange wire contract, with explicit offline economy settings.

The shipped client contains the protocol/UI but no official backend price or
yield table. These two values are local defaults, not recovered official data.
"""
import json
import time

PRICE = 10
GOLD = 10_000
MIN_LEVEL = 10
MAX_BALANCE = 4_294_967_295
HISTORY_LIMIT = 10


def _reset(g, player):
    day = time.strftime('%Y%m%d')
    if player.get('gold_exchange_day') != day:
        player.update(gold_exchange_day=day, gold_exchange_num=0, gold_exchange_records='[]')
        g.store.save_player(player['uin'], gold_exchange_day=day, gold_exchange_num=0,
                            gold_exchange_records='[]')


def _limit(g, player):
    return max(0, int(g._cfg_value('iGoldExchangeBaseCount', player))) + max(
        0, int(g.vip_privileges(player).get(46, 0)))


def _records(player):
    try:
        rows = json.loads(player.get('gold_exchange_records') or '[]')
    except (TypeError, ValueError):
        return []
    return rows[:HISTORY_LIMIT] if isinstance(rows, list) else []


def _body(g, player, records):
    limit = _limit(g, player)
    dto = (g.pb_varint_field(1, PRICE) + g.pb_varint_field(2, PRICE * 10)
           + g.pb_varint_field(3, GOLD)
           + g.pb_varint_field(4, max(0, limit - int(player.get('gold_exchange_num') or 0)))
           + g.pb_varint_field(5, limit) + g.pb_varint_field(6, 0))
    for row in records:
        recorder = b''.join(g.pb_varint_field(i, int(row.get(str(i), 0))) for i in range(1, 7))
        dto += g.pb_bytes(7, recorder)
    return g.pb_bytes(1, dto)


def get_info(g, cmd_data, player):
    with g.store.atomic_player(player):
        _reset(g, player)
        return _body(g, player, _records(player))


def exchange(g, cmd_data, player):
    with g.store.atomic_player(player):
        _reset(g, player)
        if int(player.get('level') or 1) < MIN_LEVEL:
            return [g._logic_error(200)]
        if int(player.get('gold_exchange_num') or 0) >= _limit(g, player):
            return [g._logic_error(5900)]
        gift = min(PRICE, max(0, int(player.get('present_ticket') or 0)))
        paid = PRICE - gift
        if int(player.get('ticket') or 0) < paid:
            return [g._logic_error(208)]
        if int(player.get('coin') or 0) + GOLD > MAX_BALANCE:
            return [g._logic_error(5901)]
        rows = []
        allocated = 0
        for item_id, price in [(4, gift), (37, paid)]:
            if not price:
                continue
            amount = GOLD * price // PRICE
            if item_id == 37 or not paid:
                amount = GOLD - allocated
            allocated += amount
            rows.append({'1': price, '2': amount, '3': 1, '4': 0, '5': item_id, '6': 0})
        player['present_ticket'] = int(player.get('present_ticket') or 0) - gift
        player['ticket'] = int(player.get('ticket') or 0) - paid
        player['coin'] = int(player.get('coin') or 0) + GOLD
        player['gold_exchange_num'] = int(player.get('gold_exchange_num') or 0) + 1
        player['gold_exchange_records'] = json.dumps((rows + _records(player))[:HISTORY_LIMIT],
                                                     separators=(',', ':'))
        g.store.save_player(player['uin'], present_ticket=player['present_ticket'],
                            ticket=player['ticket'], coin=player['coin'],
                            gold_exchange_num=player['gold_exchange_num'],
                            gold_exchange_records=player['gold_exchange_records'])
        reply = [('PVZ.Cmd.Cmd_GoldExchange_Exchange_SC', _body(g, player, rows)),
                 ('PVZ.Cmd.Cmd_SyncCoin_SC', g.pb_varint_field(1, player['coin']))]
        if gift:
            reply.append(('PVZ.Cmd.Cmd_SyncPresentTicket_SC', g.pb_varint_field(1, player['present_ticket'])))
        if paid:
            reply.append(('PVZ.Cmd.Cmd_SyncTicket_SC', g.pb_varint_field(1, player['ticket'])))
        return reply
