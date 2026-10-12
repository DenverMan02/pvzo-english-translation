"""Protocol/config-backed repairs (2026-10-07); no platform/tournament adapters.

Read the actual Flash model before choosing response fields. Rewards and claim
state use player_store.atomic_player, including costs and in-memory rollback.
Unknown original operational data is not replaced with invented prize pools.
"""
from __future__ import annotations

import base64
import functools
import json
import random
import re
import struct
import time
import zlib
from datetime import datetime, timedelta
from collections import Counter
import player_store as store
import missing_features_local as mf
import guild_local as guild

v, m, s, integer, blob = mf._v, mf._m, mf._s, mf._wire0, mf._wire2


def records(g, name):
    rows = g._read_clientdb_table(name)
    return [dict(zip(rows[0], row)) for row in rows[1:]] if rows else []


def items(spec):
    result = Counter()
    for bit in re.split(r'[;|]', str(spec or '')):
        if not bit.strip():
            continue
        a, b = bit.split(':')
        if int(a) > 0 and int(b) >= 0:
            result[int(a)] += int(b)
        else:
            raise ValueError('invalid configured item')
    return list(result.items())


def item_dtos(pairs, field):
    return b''.join(m(field, v(1, i) + v(2, n)) for i, n in pairs if n > 0)


def state(player, key):
    with store._db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS local_feature_state '
                    '(uin INTEGER NOT NULL,feature TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(uin,feature))')
        row = con.execute('SELECT data FROM local_feature_state WHERE uin=? AND feature=?',
                          (player['uin'], key)).fetchone()
    if row:
        try:
            value = json.loads(row[0])
            if isinstance(value, dict):
                return value
        except (ValueError, TypeError):
            pass
    return {}


def save(player, key, data):
    with store._db() as con:
        con.execute('INSERT INTO local_feature_state VALUES(?,?,?) ON CONFLICT(uin,feature) '
                    'DO UPDATE SET data=excluded.data',
                    (player['uin'], key, json.dumps(data, ensure_ascii=False)))


def atomic(handler):
    @functools.wraps(handler)
    def wrapped(data, player):
        with store.atomic_player(player):
            return handler(data, player)
    return wrapped


def error(code=5006):
    return [('PVZ.Cmd.Cmd_LogicError_SC', v(1, code))]


def available(g, player, pairs):
    totals = Counter()
    for i, n in pairs:
        totals[i] += n
    for i, n in totals.items():
        if n < 0:
            return False
        currency = g._CURRENCY_SYNC.get(i)
        have = int(player.get(currency[0]) or 0) if currency else store.get_item_count(player['uin'], i)
        if have < n:
            return False
    return True


def week_key(now):
    d = datetime.fromtimestamp(now)
    return (d - timedelta(days=d.weekday())).strftime('%Y%m%d')


def install(g):
    routes = {}
    def route(name, handler, transactional=True):
        routes['PVZ.Cmd.' + name + '_CS'] = atomic(handler) if transactional else handler

    # Resource IDs from ItemType/ItemID and the existing puppet wallet. These
    # used to be put in the bag by the generic reward pipeline instead of HUD.
    g._CURRENCY_SYNC.update({37: ('ticket', 'PVZ.Cmd.Cmd_SyncTicket_SC'),
                             9: ('energy', 'PVZ.Cmd.Cmd_SyncEnergy_SC')})
    store.DEFAULTS.update({'signature': '', 'sex': 0})
    with store._db() as con:
        # First startup creates players later, using DEFAULTS. Existing saves
        # need migration now; do not ALTER a table that does not exist yet.
        has_players = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='players'").fetchone()
        for name, ddl in (('signature', "TEXT NOT NULL DEFAULT ''"), ('sex', 'INTEGER NOT NULL DEFAULT 0')):
            if has_players and name not in {r[1] for r in con.execute('PRAGMA table_info(players)')}:
                con.execute('ALTER TABLE players ADD COLUMN ' + name + ' ' + ddl)

    # DailyVow type 6 is the big wish: it invokes the five unlocked wish types.
    # There is deliberately no type-6 dailyVowInfo row in the original table.
    vow_types = {int(r['id']): r for r in records(g, 'dailyVowType.txt')}
    vow_rows = {(int(r['type']), int(r['count'])): r for r in records(g, 'dailyVowInfo.txt')}
    wish_rows = {int(r['id']): r for r in records(g, 'dailyVowWish.txt')}
    g.SERVER_CONFIG_OVERRIDES['iDailyVowTotalTimesLimit'] = str(sum(int(r['dailyLimit']) for i, r in vow_types.items() if i != 6))
    # The operational discount is unavailable. Full configured cost keeps
    # advertised and charged prices consistent; no unverified bonus is created.
    g.SERVER_CONFIG_OVERRIDES['iDailyBigVowDiscount'] = '100'

    def vow_state(player):
        st = state(player, 'daily_vow')
        if st.get('day') != time.strftime('%Y%m%d'):
            st.update(day=time.strftime('%Y%m%d'), counts={}, total=0, big=0)
        st.setdefault('level', 1); st.setdefault('exp', 0)
        return st

    def eligible(player, typ):
        row = vow_types.get(typ, {})
        return row and int(player.get('level') or 1) >= int(row['needLevel']) and g.active_vip_level(player) >= int(row['needVIP'])

    def vow_info(data, player):
        st = vow_state(player)
        body = b''
        for typ, row in sorted(vow_types.items()):
            count = int(st['counts'].get(str(typ), 0)) if typ != 6 else int(st.get('big', 0))
            if typ == 6:
                free = max(0,int(row['freeCount'])-count)
            else:
                free = max(0, int(row['freeCount']) - count)
            body += m(1, v(1, typ) + v(2, count) + v(3, free))
        save(player, 'daily_vow', st)
        return [('PVZ.Cmd.Cmd_DailyVow_GetInfo_SC', body + v(2, st['total']) + v(3, st['level']) + v(4, st['exp']))]

    def do_vow(data, player):
        typ = integer(data, 1); st = vow_state(player)
        if not eligible(player, typ):
            return error()
        if typ==6 and int(st.get('big',0))>=int(vow_types[6]['dailyLimit']):return error()
        selected = [typ] if typ != 6 else [i for i in range(1, 6) if eligible(player, i)
                   and int(st['counts'].get(str(i), 0)) < int(vow_types[i]['dailyLimit'])]
        if not selected or st['total'] + len(selected) > int(g._cfg_value('iDailyVowTotalTimesLimit')):
            return error()
        reward, cost = Counter(), Counter(); exp = 0
        for i in selected:
            count = int(st['counts'].get(str(i), 0)) + 1
            row = vow_rows.get((i, count))
            if not row or count > int(vow_types[i]['dailyLimit']):
                return error()
            big_free=typ==6 and int(st.get('big',0))<int(vow_types[6]['freeCount'])
            if not big_free and count > int(vow_types[i]['freeCount']):
                cost.update(dict(items(row['originalCost'])))
            reward.update(dict(items(row['originalReward'])))
            exp += int(row['wish'])
        if not available(g, player, cost.items()):
            return error(5005)
        crits = items(wish_rows[st['level']]['originalCrit'])
        crit = random.choices([a for a, b in crits], weights=[b for a, b in crits])[0]
        messages = g.grant_fuben_reward(player, [(i, -n) for i, n in cost.items() if n])
        paid = [(i, n * crit) for i, n in reward.items()]
        messages += g.grant_fuben_reward(player, paid)
        for i in selected:
            st['counts'][str(i)] = int(st['counts'].get(str(i), 0)) + 1
        st['total'] += len(selected)
        if typ == 6:
            st['big'] += 1
        st['exp'] += exp
        while st['level'] < max(wish_rows) and st['exp'] >= int(wish_rows[st['level']]['needExp']):
            st['exp'] -= int(wish_rows[st['level']]['needExp']); st['level'] += 1
        st['exp'] = min(st['exp'], int(wish_rows[st['level']]['needExp']))
        save(player, 'daily_vow', st)
        messages.append(('PVZ.Cmd.Cmd_DailyVow_DoVow_SC', v(1, typ) + v(2, crit) + item_dtos(paid, 3) + v(4, exp)))
        return messages + vow_info(b'', player)
    route('Cmd_DailyVow_GetInfo', vow_info)
    route('Cmd_DailyVow_DoVow', do_vow)

    # A rank threshold is a lifetime cursor (smallest claimed rank), score is
    # a daily increasing cursor. The Flash client explicitly enforces ordering.
    old_asra_state = g._asra_state
    def asra_state(player):
        before = g._load_json_state(player, 'allserver_rank_arena_data', {})
        st = old_asra_state(player)
        st.setdefault('claimed_max_rank', int(before.get('claimed_max_rank') or 0))
        if not st.get('claim_schema'):
            st['claimed_max_rank'] = int(before.get('claimed_max_rank') or 0)
            st['claimed_score'] = 0
            st['daily_score'] = 0
            st['claim_schema'] = 1
        if before.get('day') != st.get('day'):
            st['claimed_score'] = 0
            st['daily_score'] = 0
            st['daily_reward_rank'] = int(before.get('last_rank') or 0)
        st.setdefault('daily_reward_rank', 0)
        # Keep existing leaderboard scores; the UI's reward rank is a snapshot
        # of yesterday, not today's current position.
        g._save_json_state(player, 'allserver_rank_arena_data', st)
        return st
    g._asra_state = asra_state
    old_info = g.handle_allserver_rankarena_info
    def asra_info(data, player):
        result = old_info(data, player)
        st = asra_state(player)
        ranked, by = g._asra_ranked(player)
        st['last_rank'] = int(by.get(int(player['uin']), 0))
        if st['last_rank']:
            st['max_rank'] = min(int(st.get('max_rank') or st['last_rank']), st['last_rank'])
        g._save_json_state(player, 'allserver_rank_arena_data', st)
        replacements = {3: int(st.get('max_rank') or 0), 4: int(st.get('claimed_max_rank') or 0), 5: int(st.get('daily_score') or 0),
                        6: int(st.get('claimed_score') or 0),
                        7: 0 if st.get('taken_daily') else int(st.get('daily_reward_rank') or 0)}
        fields = mf._fields(result[0][1]); body = b''
        for key, vals in fields.items():
            if key not in replacements:
                for wire, val in vals:
                    body += v(key, val) if wire == 0 else m(key, val)
        body += b''.join(v(k, val) for k, val in replacements.items())
        return [(result[0][0], body)]
    route('Cmd_AllServerRankArena_Info', asra_info)
    old_challenge = g.handle_allserver_rankarena_challenge
    def challenge(data,player):
        before=asra_state(player);used=int(before.get('challenges') or 0)
        result=old_challenge(data,player);st=asra_state(player)
        if int(st.get('challenges') or 0)>used:
            for n,body in result:
                if n=='PVZ.Cmd.Cmd_AllServerRankArena_Challenge_SC':
                    st['daily_score']=int(st.get('daily_score') or 0)+(2 if integer(body,1) else 1)
                    fields=mf._fields(body);out=b''
                    for k,values in fields.items():
                        if k==5:continue
                        for w,val in values:out+=v(k,val) if w==0 else m(k,val)
                    result=[(name,out+v(5,st['daily_score']) if name==n else payload) for name,payload in result]
                    g._save_json_state(player,'allserver_rank_arena_data',st)
                    break
        return result
    route('Cmd_AllServerRankArena_Challenge',challenge)
    for kind in ('DailyRank', 'MaxRank', 'Score'):
        def take(data, player, kind=kind):
            st = asra_state(player); requested = integer(data, 1)
            if kind == 'DailyRank':
                rank = int(st.get('daily_reward_rank') or 0)
                row = next((r for r in records(g, 'superArenaDailyRankRewardTemplate.txt')
                            if int(r['rankMax']) <= rank <= int(r['rankMin'])), None)
                if st.get('taken_daily') or not rank or requested != rank or not row:
                    return error()
                st['taken_daily'] = True
            elif kind == 'MaxRank':
                rows = sorted(records(g, 'superArenaMaxRankRewardTemplate.txt'), key=lambda r: -int(r['id']))
                cursor = int(st.get('claimed_max_rank') or 0)
                row = next((r for r in rows if not cursor or int(r['id']) < cursor), None)
                if not row or requested != int(row['id']) or not int(st.get('max_rank') or 0) or int(st['max_rank']) > requested:
                    return error()
                st['claimed_max_rank'] = requested
            else:
                cursor = int(st.get('claimed_score') or 0)
                row = next((r for r in sorted(records(g, 'superArenaScoreRewardTemplate.txt'), key=lambda r: int(r['id'])) if int(r['id']) > cursor), None)
                if not row or requested != int(row['id']) or requested > int(st.get('daily_score') or 0):
                    return error()
                st['claimed_score'] = requested
            reward = items(row['rewardStr'])
            messages = g.grant_fuben_reward(player, reward)
            g._save_json_state(player, 'allserver_rank_arena_data', st)
            messages.append(('PVZ.Cmd.Cmd_AllServerRankArena_Take' + kind + 'Reward_SC', v(1, requested) + item_dtos(reward, 2)))
            return messages
        route('Cmd_AllServerRankArena_Take' + kind + 'Reward', take)

    old_touch = g._online_reward_touch
    week_rows = records(g, 'onlineWeekReward.txt')
    def online_touch(player, now=None):
        now = int(time.time() if now is None else now)
        st = g._online_reward_data(player); wk = week_key(now)
        last = int(st.get('last_touch') or 0)
        delta = max(0, min(now - last, 3600)) if last else 0
        monday = int(datetime.strptime(wk, '%Y%m%d').timestamp())
        if not st.get('week'):
            # Only migrate time from the week containing the recorded touch.
            st['week'] = week_key(int(st.get('last_touch') or now))
        if st['week'] != wk:
            previous = datetime.strptime(wk, '%Y%m%d') - timedelta(days=7)
            carry = min(delta,max(0,monday-(now-delta)))
            st['last_week_time'] = int(st.get('weekly_time') or 0)+carry if st['week'] == previous.strftime('%Y%m%d') else 0
            st.update(week=wk, weekly_time=0, last_week_taken=False)
        # Split a connected interval at midnight/Monday; do not charge yesterday
        # to today's daily tally. Existing local time accrual is kept bounded.
        midnight = int(datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        if st.get('day') != time.strftime('%Y%m%d', time.localtime(now)):
            st.update(day=time.strftime('%Y%m%d', time.localtime(now)), daily_time=0, last_reward_time=0, claimed_ids=[])
        elif 'claimed_ids' not in st:
            st['claimed_ids']=[1] if int(st.get('last_reward_time') or 0)>0 else []
            if st['claimed_ids']:
                st['daily_time']=min(86400,int(st.get('daily_time') or 0)+g.ONLINE_REWARD_INTERVAL)
        if not isinstance(st.get('claimed_ids'),list):st['claimed_ids']=[]
        st['daily_time'] = min(86400, int(st.get('daily_time') or 0) + min(delta, max(0, now-midnight)))
        st['weekly_time'] = min(604800, int(st.get('weekly_time') or 0) + min(delta, max(0, now-monday)))
        st['last_touch'] = now
        g._save_json_state(player, 'online_reward_data', st)
        return st
    g._online_reward_touch = online_touch
    old_online_info = g.handle_online_reward_getinfo
    def online_info(data, player):
        messages = old_online_info(data, player); st = g._online_reward_data(player)
        rs = [r for r in week_rows if int(r['time']) <= int(st.get('last_week_time') or 0)]
        row = max(rs, key=lambda r: int(r['time'])) if rs else None
        fields = mf._fields(messages[0][1]); body = b''
        for key, vals in fields.items():
            if key not in (6, 7):
                for wire, val in vals:
                    body += v(key, val) if wire == 0 else m(key, val)
        reward = items(row['rewardItem']) if row else []
        body += item_dtos(reward, 6) + v(7, 2 if st.get('last_week_taken') else 1 if reward else 3)
        return [(messages[0][0], body)]
    g.handle_online_reward_getinfo = online_info
    route('Cmd_OnlineReward_GetInfo', online_info)
    def weekly_take(data, player):
        st = online_touch(player)
        rs = [r for r in week_rows if int(r['time']) <= int(st.get('last_week_time') or 0)]
        if not rs or st.get('last_week_taken'):
            return online_info(b'', player)
        row = max(rs, key=lambda r: int(r['time'])); reward = items(row['rewardItem'])
        messages = g.grant_fuben_reward(player, reward)
        st['last_week_taken'] = True
        g._save_json_state(player, 'online_reward_data', st)
        return messages + [('PVZ.Cmd.Cmd_OnlineReward_TakeRewardWeekly_SC', item_dtos(reward, 1))] + online_info(b'', player)
    route('Cmd_OnlineReward_TakeRewardWeekly', weekly_take)
    route('Cmd_OnlineReward_TakeReward', g.handle_online_reward_takereward)

    # SaveRecord stores compressed lawn state and settled progress. ReadRecord
    # resumes that map sequence; it is not a replay (bRepeat is a network retry).
    def noend_save(data, player):
        raw = blob(data, 1)
        if not raw or len(raw) > 262144:
            return error()
        try:
            decoder = zlib.decompressobj(); clear = decoder.decompress(raw, 1048576)
            if not decoder.eof or decoder.unused_data or len(clear) < 11 or struct.unpack_from('>I', clear)[0] != 1:
                return error()
            # Each plant/tile is an ObjectRecord: byte value-count followed by
            # that many unsigned ints. Validate both arrays before Flash loads
            # them, otherwise a truncated saved lawn raises EOFError in client.
            offset = 9
            for _ in range(2):
                count = clear[offset]; offset += 1
                for _ in range(count):
                    width = clear[offset]; offset += 1
                    if width > 127:
                        return error()
                    offset += width * 4
                    if offset > len(clear):
                        return error()
            if offset != len(clear):
                return error()
        except (zlib.error, struct.error, IndexError):
            return error()
        st = g._noendtd_state(player)
        if not int(st.get('level') or 0):
            return error()
        record = {k: st[k] for k in ('mode','level','score','rain','wave')}
        record['info'] = base64.b64encode(raw).decode('ascii')
        save(player, 'noend_record', record)
        return [('PVZ.Cmd.Cmd_NoEndTD_SaveRecord_SC', b'')]
    route('Cmd_NoEndTD_SaveRecord', noend_save)
    old_noend_info = g.handle_noendtd_getinfo
    def noend_info(data, player):
        messages = old_noend_info(data, player); record = state(player, 'noend_record')
        if record:
            body = (v(1, record['level']) + v(2, record['rain']) + v(3, record['score'])
                    + m(4, base64.b64decode(record['info'])))
            messages[0] = (messages[0][0], messages[0][1] + m(10, body))
        return messages
    route('Cmd_NoEndTD_GetInfo', noend_info)
    def noend_read(data, player):
        record = state(player, 'noend_record')
        if not record:
            return error()
        st = g._noendtd_state(player)
        # The client immediately starts record.level + 1. Restore the server's
        # corresponding cursor too, including when today's run was reset or
        # advanced since saving. Entering this resumed run still consumes the
        # normal daily attempt; never restore rain already spent since saving.
        if not 0 < int(record['level']) < 12:
            return error()
        st.update({k: record[k] for k in ('mode','level','score','wave')})
        st.update(rain=min(int(st['rain']), int(record['rain'])), active_level=0,
                  active_run_id=0, continuing=False)
        g._save_json_state(player, 'noend_td_data', st)
        return [('PVZ.Cmd.Cmd_NoEndTD_ReadRecord_SC', m(1, g._noendtd_mode_dto(st)))]
    route('Cmd_NoEndTD_ReadRecord', noend_read)
    old_result = g.handle_noendtd_reportresult
    def noend_result(data, player):
        before = g._noendtd_state(player)
        valid = bool(before.get('active_level') and before.get('active_run_id') != before.get('settled_run_id'))
        messages = old_result(data, player)
        if valid:
            st = state(player, 'noend_rank'); wk = week_key(time.time())
            if st.get('week') != wk:
                st.update(week=wk, weekly=0)
            score = integer(data, 3)
            st['best'] = max(int(st.get('best') or 0), score)
            st['weekly'] = max(int(st.get('weekly') or 0), score)
            save(player, 'noend_rank', st)
        return messages
    route('Cmd_NoEndTD_ReportResult', noend_result)
    def rankboard(data, player, weekly=False):
        mode = integer(data, 1, 1); page = max(1, integer(data, 2, 1)); size = max(1, min(50, integer(data, 3, 10)))
        state(player, 'noend_rank')
        with store._db() as con:
            rows = con.execute('SELECT p.*,f.data FROM local_feature_state f JOIN players p ON p.uin=f.uin WHERE f.feature=?', ('noend_rank',)).fetchall()
        with mf._db() as con:
            votes={r['target_role_id']:(r['up'],r['down']) for r in con.execute(
                'SELECT target_role_id,SUM(up_down) AS up,SUM(1-up_down) AS down FROM local_noend_votes '
                'WHERE mode=? AND board_type=? GROUP BY target_role_id',(mode,2 if weekly else 1))}
        ranked = []
        for row in rows:
            st = json.loads(row['data'])
            score = int(st.get('weekly' if weekly else 'best') or 0)
            if weekly and st.get('week') != week_key(time.time()):
                continue
            if score > 0:
                ranked.append((score, row))
        ranked.sort(key=lambda r: (-r[0], r[1]['role_id']))
        pages = max(1, (len(ranked) + size - 1) // size); page = min(page, pages)
        body = v(1, mode) + v(2, page) + v(3, pages); self_rank = 0
        for i, (score, row) in enumerate(ranked, 1):
            up,down=votes.get(row['role_id'],(0,0))
            dto = v(1, row['role_id']) + v(2, score) + s(3, row['qq_face_url']) + v(4, up) + v(5, down) + v(6, row['face_id']) + s(7, row['name'])
            if row['uin'] == player['uin']:
                self_rank = i; body += m(5, dto)
            if (page-1)*size < i <= page*size:
                body += m(4, dto)
        return [('PVZ.Cmd.Cmd_NoEndTD_Get' + ('Week' if weekly else '') + 'RankBoard_SC', body + v(6, self_rank))]
    route('Cmd_NoEndTD_GetRankBoard', rankboard)
    route('Cmd_NoEndTD_GetWeekRankBoard', lambda d,p: rankboard(d,p,True))

    def signature(data, player):
        text = blob(data, 1).decode('utf-8')
        if len(text.encode('utf-16-le')) // 2 > int(g._cfg_value('iSignatureMaxLen')) or any(ord(c) < 32 for c in text):
            return error()
        player['signature'] = text
        store.save_player(player['uin'], signature=text)
        return [('PVZ.Cmd.Cmd_SetSignature_SC', s(1, text))]
    route('Cmd_SetSignature', signature)

    g.REQUEST_HANDLERS.update(routes)
    return routes
