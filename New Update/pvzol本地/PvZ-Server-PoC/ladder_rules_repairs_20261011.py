"""RankArena contracts from ModuleRankArena and the original Chinese rule text.

Ranks are occupied slots, independent of ordinary/cross-server arena scores.
The opponent batch survives the proxy's automatic GetMyInfo after a refresh.
"""
from datetime import datetime, timedelta
from functools import wraps
from itertools import permutations
import copy
import random
import time

TABLE = 'local_rank_arena_slots'
FREE = 5
PAID = 5
COOLDOWN = 300
# EP49 shows 10000 coins on two wins; EP81 shows one tiny experience bean.
# The user selected a random one-of-two reward, with equal local probability.
# No original rank threshold or probability is established by these videos.
VICTORY_REWARDS = ((1, 10000), (2002, 1))


def _board(g, player):
    roster = g._bot_roster_with_state()
    rows = {int(b['uin']): dict(uin=int(b['uin']), name=b['name'],
            level=int(b.get('level') or 1), power=int(st.get('power') or 0),
            score=int(st.get('score') or 0), wins=0, defence_wins=0)
            for b, st in roster}
    data = g._arena_state(player, 'rank_arena_data')
    uid = int(player['uin'])
    rows[uid] = dict(uin=uid, name=player.get('name') or 'Player',
                     level=int(player.get('level') or 1), power=g._player_formation_power(player),
                     score=int(data.get('score') or 0), wins=int(data.get('wins') or 0),
                     defence_wins=int(data.get('defence_wins') or 0))
    with g.store._db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS ' + TABLE +
                    '(uin INTEGER PRIMARY KEY, rank INTEGER NOT NULL UNIQUE, is_bot INTEGER NOT NULL)')
        saved = con.execute('SELECT * FROM ' + TABLE + ' ORDER BY rank').fetchall()
        if saved:
            survivors = []
            for slot in saved:
                other = int(slot['uin'])
                exists = other in rows if slot['is_bot'] else bool(con.execute('SELECT 1 FROM players WHERE uin=?', (other,)).fetchone())
                if exists:
                    survivors.append(slot)
                else:
                    con.execute('DELETE FROM ' + TABLE + ' WHERE uin=?', (other,))
            # Account deletion already removes its occupied slot. Close that
            # gap without changing the order earned through previous matches.
            if any(int(s['rank']) != i for i, s in enumerate(survivors, 1)):
                for i, slot in enumerate(survivors, 1):
                    con.execute('UPDATE ' + TABLE + ' SET rank=? WHERE uin=?', (-i, slot['uin']))
                for i, slot in enumerate(survivors, 1):
                    con.execute('UPDATE ' + TABLE + ' SET rank=? WHERE uin=?', (i, slot['uin']))
                    if not slot['is_bot']:
                        p = dict(con.execute('SELECT * FROM players WHERE uin=?', (slot['uin'],)).fetchone())
                        st = settlement(g, p, int(slot['rank']))[0]
                        st['last_rank'] = i
                        g._save_json_state(p, 'rank_arena_data', st)
                        if int(slot['uin']) == uid:
                            player['rank_arena_data'] = p['rank_arena_data']
            saved = con.execute('SELECT * FROM ' + TABLE + ' ORDER BY rank').fetchall()
        for p in con.execute('SELECT uin,rank_arena_data FROM players').fetchall():
            if int(p['uin']) in rows:
                counters = g._load_json_state(dict(p), 'rank_arena_data', {})
                rows[int(p['uin'])].update(wins=int(counters.get('wins') or 0),
                                          defence_wins=int(counters.get('defence_wins') or 0))
        if not saved:
            initial = sorted(rows.values(), key=lambda r: (-r['score'], r['uin']))
            con.executemany('INSERT INTO ' + TABLE + ' VALUES(?,?,?)',
                            [(r['uin'], i, int(r['uin'] != uid)) for i, r in enumerate(initial, 1)])
        else:
            known = {int(r['uin']) for r in saved}
            rank = max(int(r['rank']) for r in saved)
            for uin in rows:
                if uin not in known:
                    rank += 1
                    con.execute('INSERT INTO ' + TABLE + ' VALUES(?,?,?)', (uin, rank, int(uin != uid)))
        saved = con.execute('SELECT * FROM ' + TABLE + ' ORDER BY rank').fetchall()
        for slot in saved:
            other = int(slot['uin'])
            if other in rows or slot['is_bot']:
                continue
            p = con.execute('SELECT * FROM players WHERE uin=?', (other,)).fetchone()
            if p:
                p = dict(p)
                st = g._load_json_state(p, 'rank_arena_data', {})
                rows[other] = dict(uin=other, name=p['name'], level=p['level'],
                                   power=g._player_formation_power(p), score=int(st.get('score') or 0),
                                   wins=int(st.get('wins') or 0), defence_wins=int(st.get('defence_wins') or 0))
        return [dict(rows[int(s['uin'])], rank=int(s['rank'])) for s in saved if int(s['uin']) in rows]


def board(g, player):
    # Generic Rank_GetRankList is also a caller and is not itself transactional.
    # Initialization/normalization must never commit temporary negative slots.
    with g.store.atomic_player(player):
        return _board(g, player)


def swap(g, left, right):
    with g.store._db() as con:
        a = con.execute('SELECT rank FROM ' + TABLE + ' WHERE uin=?', (left,)).fetchone()[0]
        b = con.execute('SELECT rank FROM ' + TABLE + ' WHERE uin=?', (right,)).fetchone()[0]
        if a > b:
            con.execute('UPDATE ' + TABLE + ' SET rank=-1 WHERE uin=?', (left,))
            con.execute('UPDATE ' + TABLE + ' SET rank=? WHERE uin=?', (a, right))
            con.execute('UPDATE ' + TABLE + ' SET rank=? WHERE uin=?', (b, left))
            return b
        return a


def opponents(g, player, refresh=False, current=None):
    current = board(g, player) if current is None else current
    uid = int(player['uin'])
    mine = next(r['rank'] for r in current if r['uin'] == uid)
    others = [r for r in current if r['uin'] != uid]
    count = min(3, len(others))
    better = [r for r in others if r['rank'] < mine]
    # Both reference videos offer higher-ranked challengers. The local small
    # roster needs random lower-ranked fillers when fewer than three remain.
    # Restricting those to the nearest slots would freeze rank one's refresh.
    lower = [r for r in others if r['rank'] > mine]
    top_fill = len(better) <= count and bool(lower)
    pool = others if top_fill else better
    by = {r['uin']: r for r in pool}
    st = g._arena_state(player, 'rank_arena_data')
    previous = st.get('opponent_batch') or []
    selected = [by[int(r['uin'])] for r in previous if int(r['uin']) in by]
    if refresh or len(selected) != min(3, len(pool)):
        count = min(3, len(pool))
        old_ids = {int(r['uin']) for r in previous}
        fresh = [r for r in pool if r['uin'] not in old_ids]
        # The original footage changes the offered ranks irregularly. Sample
        # actual opponents instead of paging a rank-sorted list. When enough
        # alternatives exist, make every card new on an explicit refresh.
        if top_fill:
            # At rank four, choosing only its three higher slots would still
            # freeze the offered ranks. Keep promotion targets and randomly
            # fill one or more cells from the rest of the local roster.
            higher_count = min(len(better), count - 1)
            fresh_lower = [r for r in lower if r['uin'] not in old_ids]
            missing = count - higher_count
            candidates = fresh_lower if refresh and len(fresh_lower) >= missing else lower
            selected = random.sample(random.sample(better, higher_count) +
                                     random.sample(candidates, missing), count)
        else:
            candidates = fresh if refresh and len(fresh) >= count else pool
            selected = random.sample(candidates, count)
        if refresh and previous and len(pool) > 1:
            for _ in range(count):
                if all(i >= len(previous) or r['uin'] != int(previous[i]['uin'])
                       for i, r in enumerate(selected)):
                    break
                selected = selected[1:] + selected[:1]
            if any(i < len(previous) and r['uin'] == int(previous[i]['uin'])
                   for i, r in enumerate(selected)):
                # Permute only the three drawn cards, keeping a required higher
                # rank in the batch without enumerating the entire board.
                choices = [list(rows) for rows in permutations(selected, count)
                           if all(i >= len(previous) or r['uin'] != int(previous[i]['uin'])
                                  for i, r in enumerate(rows))]
                if choices:
                    selected = random.choice(choices)
        g._capture_arena_npcs(player, 'rank', [r['uin'] for r in selected])
    st['opponent_batch'] = [dict(uin=r['uin'], rank=r['rank']) for r in selected]
    g._save_json_state(player, 'rank_arena_data', st)
    # Flash unconditionally indexes exactly three cells. Only genuinely missing
    # slots use inert padding; Challenge validates membership in the real board.
    while len(selected) < 3:
        selected.append(dict(uin=900000 + len(selected) + 1, name='训练对手', level=1, power=0, rank=len(pool) + len(selected) + 1))
    return selected


def reward_period(now):
    moment = datetime.fromtimestamp(now)
    settlement = moment.replace(hour=21, minute=0, second=0, microsecond=0)
    if moment < settlement:
        settlement -= timedelta(days=1)
    return settlement.strftime('%Y%m%d'), int(settlement.timestamp()), now >= settlement.timestamp() + 600


def reward(g, rank):
    # RankArena has no authored reward table in clientDB: the original server
    # supplied DTO fields 12/15. EP81 at 02:56 proves rank 101 pays 60 blue
    # diamonds. Other positions retain the existing offline amounts instead
    # of inventing an original tier schedule or borrowing the cross-server one.
    return [(4, 60), (2002, 50), (2014, 120)] if rank == 101 else [(1, 5000), (4, 10)]


def settlement(g, player, rank):
    legacy = g._load_json_state(player, 'rank_arena_data', {})
    st = g._arena_state(player, 'rank_arena_data')
    now = int(time.time())
    period, when, available = reward_period(now)
    if st.get('reward_period') != period:
        # First migration has no historical settlement to invent. Existing rank
        # becomes the baseline, and subsequent periods settle the saved position.
        legacy_day = str(legacy.get('legacy_rank_reward_claim_day') or (legacy.get('day') if legacy.get('taken') else '') or '')
        claimed = (legacy_day >= period
                   if 'reward_period' not in st else False)
        st.update(reward_period=period, reward_rank=int(st.get('last_rank') or rank),
                  reward_taken=claimed, reward_settled_at=when)
    st['last_rank'] = rank
    g._save_json_state(player, 'rank_arena_data', st)
    return st, reward(g, int(st.get('reward_rank') or rank)), available and not st.get('reward_taken')


def recorder(g, rec):
    return (g.pb_int64_field(1, int(rec.get('report_uid') or 0)) +
            g.pb_int64_field(2, int(rec.get('opponent') or 0)) + g.pb_string(3, rec.get('name') or '训练对手') +
            g.pb_varint_field(4, int(rec.get('face') or 0)) + g.pb_varint_field(5, int(rec.get('level') or 1)) +
            g.pb_bool(7, bool(rec.get('win'))) + g.pb_bool(8, bool(rec.get('attack', True))) +
            g.pb_varint_field(9, int(rec.get('ts') or 0)))


def opponent_dto(g, row):
    body = (g.pb_int64_field(1, row['uin']) + g.pb_string(2, row['name']) +
            g.pb_varint_field(3, 1) + g.pb_varint_field(4, row['level']) +
            g.pb_string(5, 'http://127.0.0.1:8080/image/head/%d.png' % (row['uin'] % 6 + 1)) +
            g.pb_varint_field(6, int(row.get('wins') or 0)) +
            g.pb_varint_field(7, int(row.get('defence_wins') or 0)) +
            g.pb_varint_field(8, row['rank']) + g.pb_varint_field(14, row['power']))
    for item, count in reward(g, row['rank']):
        body += g.pb_bytes(15, g.pb_varint_field(1, item) + g.pb_varint_field(2, count))
    return body


def save_report(g, owner, uid, report):
    with g.store._db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS local_rank_arena_reports'
                    '(report_id INTEGER, owner INTEGER, payload BLOB, PRIMARY KEY(report_id,owner))')
        con.execute('INSERT OR REPLACE INTO local_rank_arena_reports VALUES(?,?,?)', (uid, owner, report))
        # Each of the three history windows can still reference 20 reports.
        # A busy ordinary arena must not evict an older ladder/cross-arena
        # report that remains visible. Retain those references as well as a
        # small recent buffer (ordinary settlement saves its report first).
        keep = {int(uid)}
        row = con.execute('SELECT arena_data,rank_arena_data,allserver_rank_arena_data '
                          'FROM players WHERE uin=?', (owner,)).fetchone()
        if row:
            for field in ('arena_data', 'rank_arena_data', 'allserver_rank_arena_data'):
                records = g._load_json_state(dict(row), field, {}).get('records') or []
                visible = records[:20] if field == 'allserver_rank_arena_data' else records[-20:]
                keep.update(int(rec.get('report_uid') or 0) for rec in visible)
        keep.update(int(row[0]) for row in con.execute(
            'SELECT report_id FROM local_rank_arena_reports WHERE owner=? '
            'ORDER BY report_id DESC LIMIT 40', (owner,)))
        placeholders = ','.join('?' for _ in keep)
        con.execute('DELETE FROM local_rank_arena_reports WHERE owner=? AND report_id NOT IN ('
                    + placeholders + ')', (owner, *sorted(keep)))


def install(g):
    # All arena-family history windows query the original report by its ID.
    g._save_arena_report = lambda player, uid, report: save_report(g, int(player['uin']), uid, report)
    # Original config keys. No paid prices were archived; retain the local
    # diamond price and expose it accurately, with five free/five paid tries.
    g.SERVER_CONFIG_OVERRIDES.update(iRankArenaChallengeNumLimit='5',
                                    sRankArenaPayChallenge='5,5,5,5,5',
                                    iRankArenaClearCoolDownCost='5')
    old_state = g._arena_state
    def arena_state(player, field):
        raw = g._load_json_state(player, field, {}) if field == 'rank_arena_data' else {}
        st = old_state(player, field)
        if field == 'rank_arena_data' and raw.get('taken') and 'reward_period' not in raw:
            st['legacy_rank_reward_claim_day'] = raw.get('day') or ''
            g._save_json_state(player, field, st)
        return st
    g._arena_state = arena_state
    old_encounter = g._npc_encounter
    def encounter(player, role_id, mode):
        if mode != 'rank' or g.bots_local.is_bot(role_id):
            return old_encounter(player, role_id, mode)
        saved = g._NPC_ENCOUNTERS.setdefault((int(player['uin']), mode), {})
        if not saved.get(int(role_id), {}).get('units'):
            enemy = g.store.get_player_by_role_id(int(role_id))
            if enemy is None:
                return dict(name='训练对手', level=1, units=[])
            units = g.build_player_units(enemy, leader_team_id=3)
            for unit in units:
                unit['pos'] = int(unit['pos']) + 1
            saved[int(role_id)] = dict(name=enemy['name'], level=enemy['level'], units=units)
        return copy.deepcopy(saved[int(role_id)])
    g._npc_encounter = encounter

    # Preserve earlier adapters on the shared query route (notably the
    # persistent world-map reports installed before the arena repairs).
    old_query = g.REQUEST_HANDLERS.get(
        'PVZ.Cmd.Cmd_Battle_QueryReport_CS', g.handle_battle_queryreport)
    def query(data, player):
        uid = int(g.parse_varint_fields(data).get(1) or 0)
        with g.store._db() as con:
            exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_rank_arena_reports'").fetchone()
            row = con.execute('SELECT payload FROM local_rank_arena_reports WHERE report_id=?', (uid,)).fetchone() if exists else None
        if row:
            return [('PVZ.Cmd.Cmd_Battle_QueryReport_SC', g.pb_bytes(1, bytes(row[0])))]
        return old_query(data, player)
    g.handle_battle_queryreport = query
    g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Battle_QueryReport_CS'] = query
    def atomic(fn):
        @wraps(fn)
        def run(data, player):
            with g.store.atomic_player(player):
                return fn(data, player)
        return run

    def myinfo(cmd, player):
        rows = board(g, player)
        me = next(r for r in rows if r['uin'] == int(player['uin']))
        st, items, can_take = settlement(g, player, me['rank'])
        used = int(st.get('challenges') or 0)
        body = (g.pb_varint_field(2, me['rank']) + g.pb_varint_field(3, int(st.get('wins') or 0)) +
                g.pb_varint_field(4, int(st.get('defence_wins') or 0)) + g.pb_varint_field(5, max(0, FREE - used)) +
                g.pb_varint_field(6, max(0, PAID - max(0, used - FREE))) +
                g.pb_varint_field(7, max(0, int(st.get('cooldown') or 0) - int(time.time()))) +
                g.pb_bool(8, True) + g.pb_varint_field(9, me['power']) + g.pb_bool(13, bool(items) and can_take))
        for rec in (st.get('records') or [])[-20:]:
            body += g.pb_bytes(1, recorder(g, rec))
        for r in opponents(g, player, current=rows):
            body += g.pb_bytes(10, opponent_dto(g, r))
        for r in rows[:3]:
            body += g.pb_bytes(11, opponent_dto(g, r))
        for item, count in items:
            body += g.pb_bytes(12, g.pb_varint_field(1, item) + g.pb_varint_field(2, count))
        return [('PVZ.Cmd.Cmd_RankArena_GetMyInfo_SC', body)]

    def refresh(cmd, player):
        body = b''.join(g.pb_bytes(1, opponent_dto(g, r)) for r in opponents(g, player, True))
        return [('PVZ.Cmd.Cmd_RankArena_GetOpponentInfo_SC', body)]

    def rankings(cmd, player):
        body = b''.join(g.pb_bytes(1, opponent_dto(g, r)) for r in board(g, player))
        return [('PVZ.Cmd.Cmd_RankArena_GetRank_SC', body)]

    def challenge(cmd, player):
        if int(player.get('level') or 1) < 12:
            return [g._logic_error(5006)]
        fields = g.parse_varint_fields(cmd)
        st = g._arena_state(player, 'rank_arena_data')
        used = int(st.get('challenges') or 0)
        if used >= FREE + PAID:
            return [g._logic_error(7705)]
        if int(st.get('cooldown') or 0) > int(time.time()):
            return [g._logic_error(8010)]
        rows = board(g, player)
        by = {r['uin']: r for r in rows}
        uid = int(player['uin'])
        target = int(fields.get(1) or 0)
        if not target:
            target = opponents(g, player)[0]['uin']
        if target == uid or target not in by:
            return [g._logic_error(5006)]
        st, _, _ = settlement(g, player, by[uid]['rank'])
        batch = st.get('opponent_batch') or []
        index = int(fields.get(2) or 0)
        if batch and 0 <= index < len(batch) and int(batch[index]['uin']) == target and int(batch[index]['rank']) != by[target]['rank']:
            return [g._logic_error(8003)]
        encounter = g._npc_encounter(player, target, 'rank')
        right = encounter['units']
        left = g.build_player_units(player, leader_team_id=3)
        if not left or not right:
            return [g._logic_error(5006)]
        fees = [int(n) for n in g._cfg_value('sRankArenaPayChallenge').split(',')]
        charge = g._spend_diamonds(player, fees[used - FREE]) if used >= FREE else []
        if charge is None:
            return [g._logic_error(5005)]
        won, lp, rp, rounds = g.run_battle(left, right, puppets=g._auto_puppets(player),
                                         stop_on_elimination=True, attacker_first=True)
        puppets = g._auto_puppet_list(player)
        # Draw once at settlement. The stored report and wallet grant share the
        # same reward; viewing or replaying the report must never draw again.
        win_items = [random.choice(VICTORY_REWARDS)] if won else []
        if won:
            win_items.append((6, 2))
        report = g.build_fight_report(player, 0, 0, 0, won, win_items, False, left, right,
                                     g.encode_fight_replay(rounds), fight_type=g._RANK_ARENA_TARGET,
                                     puppet_bytes=puppets, side_types=(38, 20) if puppets else None)
        report_id = g._store_battle_report(report)
        before = by[uid]['rank']
        enemy = g.store.get_player_by_role_id(target)
        # Settle BOTH positions before a swap, including an offline defender
        # crossing 21:00, then remember each side's actual new position.
        enemy_st = settlement(g, enemy, by[target]['rank'])[0] if enemy else None
        rank = swap(g, uid, target) if won else before
        st.update(challenges=used + 1, cooldown=int(time.time()) + COOLDOWN,
                  last_opponent=target, last_rank=rank, opponent_batch=[])
        counter = 'wins' if won else 'losses'
        st[counter] = int(st.get(counter) or 0) + 1
        rec = dict(opponent=target, name=by[target]['name'], level=by[target]['level'], win=won,
                   attack=True, change=before-rank, report_uid=report_id, ts=int(time.time()),
                   before_rank=before, after_rank=rank)
        st.setdefault('records', []).append(rec)
        st['records'] = st['records'][-20:]
        g._save_json_state(player, 'rank_arena_data', st)
        save_report(g, uid, report_id, report)
        if enemy_st is not None:
            enemy_st['last_rank'] = before if rank != before else by[target]['rank']
            count = 'defence_losses' if won else 'defence_wins'
            enemy_st[count] = int(enemy_st.get(count) or 0) + 1
            reverse = dict(rec, opponent=uid, name=player.get('name') or 'Player',
                           level=int(player.get('level') or 1), win=not won, attack=False,
                           before_rank=by[target]['rank'], after_rank=enemy_st['last_rank'])
            enemy_st.setdefault('records', []).append(reverse)
            enemy_st['records'] = enemy_st['records'][-20:]
            g._save_json_state(enemy, 'rank_arena_data', enemy_st)
            save_report(g, target, report_id, report)
        g._daily_quest_touch(player, 'arena', 1)
        messages = myinfo(b'', player) + [('PVZ.Cmd.Cmd_RankArena_Challenge_SC', g.pb_varint_field(1, rank) + g.pb_bool(2, won) + g.pb_bytes(3, recorder(g, rec)))] + charge
        if won:
            messages += g.grant_fuben_reward(player, win_items)
        return messages

    def take(cmd, player):
        me = next(r for r in board(g, player) if r['uin'] == int(player['uin']))
        st, items, available = settlement(g, player, me['rank'])
        if not available or not items:
            return [g._logic_error(5006)]
        messages = g.grant_fuben_reward(player, items)
        st['reward_taken'] = True
        g._save_json_state(player, 'rank_arena_data', st)
        body = b''.join(g.pb_bytes(1, g.pb_varint_field(1, i) + g.pb_varint_field(2, n)) for i, n in items)
        return messages + [('PVZ.Cmd.Cmd_RankArena_TakeDailyReward_SC', body)] + myinfo(b'', player)

    def clear(cmd, player):
        st = g._arena_state(player, 'rank_arena_data')
        if int(st.get('cooldown') or 0) <= int(time.time()):
            return [('PVZ.Cmd.Cmd_RankArena_ClearCoolDown_SC', b'')] + myinfo(b'', player)
        messages = g._spend_diamonds(player, int(g._cfg_value('iRankArenaClearCoolDownCost')))
        if messages is None:
            return [g._logic_error(5005)]
        st['cooldown'] = 0
        g._save_json_state(player, 'rank_arena_data', st)
        return messages + [('PVZ.Cmd.Cmd_RankArena_ClearCoolDown_SC', b'')] + myinfo(b'', player)

    # The global ranking window must use the same occupied slots as RankArena.
    old_rank_board = g._rank_board
    def rank_board(kind, player):
        if kind != 5:
            return old_rank_board(kind, player)
        return [(r['rank'], g._rank_row(uin=r['uin'], role_id=r['uin'], name=r['name'],
                  level=r['level'], power=r['power'], wins=r.get('wins', 0), losses=r.get('defence_wins', 0))) for r in board(g, player)]
    g._rank_board = rank_board
    g._rank_arena_board = lambda p: board(g, p)
    g._rank_arena_opponent_rows = lambda p: opponents(g, p)
    handlers = {'GetMyInfo': ('getmyinfo', myinfo), 'GetOpponentInfo': ('getopponentinfo', refresh),
                'GetRank': ('getrank', rankings), 'Challenge': ('challenge', challenge),
                'TakeDailyReward': ('takedailyreward', take), 'ClearCoolDown': ('clearcooldown', clear)}
    for command, (helper, fn) in handlers.items():
        run = atomic(fn)
        setattr(g, 'handle_rankarena_' + helper, run)
        g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_RankArena_' + command + '_CS'] = run
