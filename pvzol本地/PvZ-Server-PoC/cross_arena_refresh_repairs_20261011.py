"""Persistent random cross-server opponents and immediate full battle/count updates.

SuperArenaMainWindow has five opponent tiles. SuperArenaModel.updateOpList
adds the self DTO, so the wire arrays contain four distinct other roles.
Info.topOpponent is a separate five-place podium. Challenge.opponent is a
repeated DTO array, not just the role that fought the match.
"""
from functools import wraps
import copy
import random
import time

OPPONENT_COUNT = 4
TOP_COUNT = 5
STATE_FIELD = 'allserver_rank_arena_data'
TABLE = 'local_cross_arena_slots'
INFO_SC = 'PVZ.Cmd.Cmd_AllServerRankArena_Info_SC'
CHALLENGE_SC = 'PVZ.Cmd.Cmd_AllServerRankArena_Challenge_SC'


def _remember_rank(g, player, rank):
    # Rollover freezes yesterday's last_rank before this day's position changes.
    state = g._asra_state(player)
    state['last_rank'] = int(rank)
    state['max_rank'] = min(int(state.get('max_rank') or rank), int(rank))
    g._save_json_state(player, STATE_FIELD, state)
    return state


def _slot_board(g, player, legacy_leaderboard):
    """One occupied-rank board, independent of shared bot arena scores."""
    # Only migration uses the legacy score order. Subsequent joins append and
    # victories swap occupied slots; neither ordinary nor daily scores sort it.
    initial = legacy_leaderboard(player)
    rows = {int(row[0]): row for row in initial}
    uid = int(player['uin'])
    bot_ids = {int(bot['uin']) for bot in g.bots_local.bots()}
    with g.store._db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS ' + TABLE +
                    '(uin INTEGER PRIMARY KEY,rank INTEGER NOT NULL UNIQUE,is_bot INTEGER NOT NULL)')
        slots = con.execute('SELECT * FROM ' + TABLE + ' ORDER BY rank').fetchall()
        survivors = []
        for slot in slots:
            other = int(slot['uin'])
            exists = (other in bot_ids if slot['is_bot'] else
                      bool(con.execute('SELECT 1 FROM players WHERE uin=?', (other,)).fetchone()))
            if exists:
                survivors.append(slot)
            else:
                con.execute('DELETE FROM ' + TABLE + ' WHERE uin=?', (other,))
        # Deletion may already have removed its slot. Compact in earned order,
        # settling each surviving player's old position before moving it.
        if any(int(slot['rank']) != rank for rank, slot in enumerate(survivors, 1)):
            for rank, slot in enumerate(survivors, 1):
                con.execute('UPDATE ' + TABLE + ' SET rank=? WHERE uin=?', (-rank, slot['uin']))
            for rank, slot in enumerate(survivors, 1):
                con.execute('UPDATE ' + TABLE + ' SET rank=? WHERE uin=?', (rank, slot['uin']))
                if int(slot['rank']) != rank:
                    saved = con.execute('SELECT * FROM players WHERE uin=?', (slot['uin'],)).fetchone()
                    if saved:
                        owner = player if int(slot['uin']) == uid else dict(saved)
                        state = _remember_rank(g, owner, int(slot['rank']))
                        state['last_rank'] = rank
                        state['max_rank'] = min(int(state.get('max_rank') or rank), rank)
                        g._save_json_state(owner, STATE_FIELD, state)
        known = {int(slot['uin']) for slot in survivors}
        if not survivors:
            con.executemany('INSERT INTO ' + TABLE + ' VALUES(?,?,?)',
                            [(int(row[0]), rank, int(int(row[0]) in bot_ids))
                             for rank, row in enumerate(initial, 1)])
        else:
            rank = len(survivors)
            for row in initial:
                other = int(row[0])
                if other not in known:
                    rank += 1
                    con.execute('INSERT INTO ' + TABLE + ' VALUES(?,?,?)',
                                (other, rank, int(other in bot_ids)))
                    known.add(other)
        slots = con.execute('SELECT * FROM ' + TABLE + ' ORDER BY rank').fetchall()
        for slot in slots:
            other = int(slot['uin'])
            saved = con.execute('SELECT * FROM players WHERE uin=?', (other,)).fetchone()
            if other not in rows and saved:
                owner = dict(saved)
                state = g._load_json_state(owner, STATE_FIELD, {})
                rows[other] = (other, owner.get('name') or 'Player', int(owner.get('level') or 1),
                               g._player_formation_power(owner), int(state.get('score') or 100),
                               int(state.get('wins') or 0), int(state.get('defence_wins') or 0))
            elif other in rows and saved:
                state = g._load_json_state(dict(saved), STATE_FIELD, {})
                if state.get('rank_schema'):
                    row = rows[other]
                    rows[other] = row[:5] + (int(state.get('wins') or 0),
                                            int(state.get('defence_wins') or 0))
        ranked = [(rows[int(slot['uin'])], int(slot['rank']))
                  for slot in slots if int(slot['uin']) in rows]
    return ranked, {int(row[0]): rank for row, rank in ranked}


def _swap_slots(g, left, right):
    with g.store._db() as con:
        own = int(con.execute('SELECT rank FROM ' + TABLE + ' WHERE uin=?', (left,)).fetchone()[0])
        enemy = int(con.execute('SELECT rank FROM ' + TABLE + ' WHERE uin=?', (right,)).fetchone()[0])
        if enemy < own:
            con.execute('UPDATE ' + TABLE + ' SET rank=-1 WHERE uin=?', (left,))
            con.execute('UPDATE ' + TABLE + ' SET rank=? WHERE uin=?', (own, right))
            con.execute('UPDATE ' + TABLE + ' SET rank=? WHERE uin=?', (enemy, left))
            return enemy, own
        return own, enemy


def _replace_fields(g, body, replacements):
    """Replace complete top-level fields, preserving the battle report bytes."""
    output, pos = [], 0
    while pos < len(body):
        start = pos
        key, pos = g.read_uvarint(body, pos)
        field, wire = key >> 3, key & 7
        if wire == 0:
            _, pos = g.read_uvarint(body, pos)
        elif wire == 2:
            size, pos = g.read_uvarint(body, pos)
            pos += size
        elif wire == 1:
            pos += 8
        elif wire == 5:
            pos += 4
        else:
            raise ValueError('unsupported cross-arena protobuf wire type')
        if pos > len(body):
            raise ValueError('truncated cross-arena protobuf field')
        if field not in replacements:
            output.append(body[start:pos])
    output.extend(replacements.values())
    return b''.join(output)


def _saved_batch(state):
    result = []
    for value in state.get('opponent_batch') or []:
        try:
            # Accept a short-lived dict format if an existing local save used it.
            result.append(int(value.get('uin') if isinstance(value, dict) else value))
        except (ValueError, TypeError):
            return []
    return result


def choose_batch(g, player, ranked, state, refresh=False):
    """Choose on explicit refresh/battle, reuse on repeated Info or reconnect."""
    own = int(player['uin'])
    by_uin = {int(row[0]): (row, rank) for row, rank in ranked if int(row[0]) != own}
    count = min(OPPONENT_COUNT, len(by_uin))
    own_rank = next(rank for row, rank in ranked if int(row[0]) == own)
    better = [uin for uin, (_, rank) in by_uin.items() if rank < own_rank]
    # EP81 offers four higher ranks with a broad random spread. Near the top
    # of the small offline roster, keep higher ranks and randomly fill from
    # ALL lower ranks. Restricting fillers to 2..5 freezes rank one's refresh.
    lower = [uin for uin, (_, rank) in by_uin.items() if rank > own_rank]
    previous = _saved_batch(state)
    valid = (len(previous) == count and len(set(previous)) == count
             and all(uin in by_uin for uin in previous))
    if refresh or not valid:
        if len(better) > count or (len(better) == count and not lower):
            selected = random.sample(better, count)
            alternatives, replace_index = better, -1
        elif len(better) == count:
            # Exactly four higher roles also form a permanently fixed set.
            # Keep three, add a random lower role; all four higher roles remain
            # accessible through the independent top-five podium. This is a
            # small-roster fallback, not an inferred original match interval.
            selected = random.sample(better, count - 1) + random.sample(lower, 1)
            alternatives, replace_index = lower, -1
            if len(lower) == 1:
                alternatives, replace_index = better, 0
        else:
            selected = better + random.sample(lower, count - len(better))
            alternatives, replace_index = lower, -1
        # Random sampling can repeat an entire set. When alternatives exist,
        # make an explicit click visibly change the batch without fixed ranks.
        choices = [uin for uin in alternatives if uin not in selected]
        if count and choices and set(selected) == set(previous):
            selected[replace_index] = random.choice(choices)
        state['opponent_batch'] = selected
        g._capture_arena_npcs(player, 'allserver', selected)
    else:
        selected = previous
    return sorted((by_uin[uin] for uin in selected), key=lambda pair: pair[1])


def _context(g, player, refresh=False):
    ranked, _ = g._asra_ranked(player)
    own = next((row, rank) for row, rank in ranked if int(row[0]) == int(player['uin']))
    state = g._asra_state(player)
    state['last_rank'] = own[1]
    if own[1]:
        state['max_rank'] = min(int(state.get('max_rank') or own[1]), own[1])
    batch = choose_batch(g, player, ranked, state, refresh)
    g._save_json_state(player, STATE_FIELD, state)
    return ranked, own, state, batch


def _opponent_fields(g, batch, field):
    return b''.join(g.pb_bytes(field, g._asra_dto_from_row(row, rank)) for row, rank in batch)


def _info_body(g, context):
    ranked, own, state, batch = context
    # Match consistency_repairs' reward cursors and yesterday's fixed snapshot.
    body = (g.pb_bytes(1, g._asra_dto_from_row(*own)) +
            g.pb_varint_field(2, int(state.get('challenges') or 0)) +
            g.pb_varint_field(3, int(state.get('max_rank') or own[1])) +
            g.pb_varint_field(4, int(state.get('claimed_max_rank') or 0)) +
            g.pb_varint_field(5, int(state.get('daily_score') or 0)) +
            g.pb_varint_field(6, int(state.get('claimed_score') or 0)) +
            g.pb_varint_field(7, 0 if state.get('taken_daily') else int(state.get('daily_reward_rank') or 0)))
    return body + _opponent_fields(g, ranked[:TOP_COUNT], 8) + _opponent_fields(g, batch, 9)


def install(g):
    if getattr(g, '_CROSS_ARENA_REFRESH_20261011_INSTALLED', False):
        return
    g._CROSS_ARENA_REFRESH_20261011_INSTALLED = True

    previous_leaderboard = g._asra_leaderboard
    @wraps(previous_leaderboard)
    def leaderboard(player):
        # The legacy builder hardcodes every bot to level 70. The roster uses
        # the saved generated account's actual role level, like its preview.
        # Replace only that metadata; preserve the existing score/rank order.
        levels = {int(bot['uin']): int(bot.get('level') or 1)
                  for bot in g.bots_local.bots()}
        return [row[:2] + (levels.get(int(row[0]), row[2]),) + row[3:]
                for row in previous_leaderboard(player)]
    @wraps(g._asra_ranked)
    def ranked(player):
        with g.store.atomic_player(player):
            return _slot_board(g, player, leaderboard)
    g._asra_ranked = ranked
    g._asra_leaderboard = lambda player: [row for row, _ in ranked(player)[0]]

    previous_encounter = g._npc_encounter
    @wraps(previous_encounter)
    def encounter(player, role_id, mode):
        if mode != 'allserver' or g.bots_local.is_bot(role_id):
            return previous_encounter(player, role_id, mode)
        saved = g._NPC_ENCOUNTERS.setdefault((int(player['uin']), mode), {})
        role_id = int(role_id or 0)
        if not saved.get(role_id, {}).get('units'):
            enemy = g.store.get_player_by_role_id(role_id)
            if enemy is None:
                return dict(name='训练对手', level=1, units=[])
            placements = g.store.get_formation(enemy['uin'], 3) or g.store.get_formation(enemy['uin'], 1)
            units = g.build_player_units(enemy, placements or None, leader_team_id=3)
            for unit in units:
                unit['pos'] = int(unit['pos']) + 1
            saved[role_id] = dict(name=enemy.get('name') or 'Player',
                                  level=int(enemy.get('level') or 1), units=units)
        return copy.deepcopy(saved[role_id])
    g._npc_encounter = encounter

    def atomic(handler):
        @wraps(handler)
        def wrapped(data, player):
            with g.store.atomic_player(player):
                return handler(data, player)
        return wrapped

    # These apparent reads call _asra_state, which also writes rollover JSON.
    # Reload a socket's stale player dictionary before that write, or opening
    # the replay/preview on another connection can erase a settled battle.
    for command, helper in (
            ('GetRecorder', 'handle_allserver_rankarena_getrecorder'),):
        route = 'PVZ.Cmd.Cmd_AllServerRankArena_' + command + '_CS'
        handler = atomic(g.REQUEST_HANDLERS[route])
        g.REQUEST_HANDLERS[route] = handler
        setattr(g, helper, handler)

    previous_board = g._rank_board
    @wraps(previous_board)
    def rank_board(ranktype, player):
        if int(ranktype) != 105:
            return previous_board(ranktype, player)
        with g.store.atomic_player(player):
            return previous_board(ranktype, player)
    g._rank_board = rank_board

    @atomic
    def info(data, player):
        # Rebuilding through the old Info route would clear every captured
        # encounter and emit the whole sorted leaderboard on each status read.
        return [(INFO_SC, _info_body(g, _context(g, player)))]

    @atomic
    def refresh(data, player):
        _, own, _, batch = _context(g, player, refresh=True)
        body = g.pb_bytes(1, g._asra_dto_from_row(*own)) + _opponent_fields(g, batch, 2)
        return [('PVZ.Cmd.Cmd_AllServerRankArena_GetOpponent_SC', body)]

    @atomic
    def formation(data, player):
        target = int(g.parse_varint_fields(data).get(1) or 0)
        _, by_uin = g._asra_ranked(player)
        name = 'PVZ.Cmd.Cmd_AllServerRankArena_GetOpponentFormation_SC'
        if target == int(player['uin']) or target not in by_uin:
            return [(name, b'')]
        units = g._npc_encounter(player, target, 'allserver')['units']
        g.load_battle_cards()
        body = g.pb_int64_field(1, target) + g.pb_varint_field(2, by_uin[target])
        for unit in units:
            body += g.pb_bytes(3, g._enemy_card_dto(unit['pos']-1, unit['uid'], unit['card_id'],
                                                   unit['level'], unit, unit['is_leader']))
        body += g.pb_varint_field(4, sum(unit['power'] for unit in units))
        return [(name, body)]

    @atomic
    def challenge(data, player):
        fields = g.parse_varint_fields(data)
        state = g._asra_state(player)
        used = int(state.get('challenges') or 0)
        free = max(0, int(g._cfg_value('iAllServerRankArenaRankFreeChallengeTimes', player)))
        remaining = free + 10 - used
        if remaining <= 0:
            return [g._logic_error(7705)]
        if 3 in fields and int(fields[3]) != used + 1:
            return [g._logic_error(5006)]
        before, by_uin = g._asra_ranked(player)
        uid = int(player['uin'])
        target = int(fields.get(1) or 0)
        if not target:
            pick = choose_batch(g, player, before, state)
            target = int(pick[0][0][0]) if pick else 0
        if target == uid or target not in by_uin:
            return [g._logic_error(5006)]
        if 2 in fields and int(fields[2]) != by_uin[target]:
            # A different challenger may have moved the tile's opponent since
            # it was displayed. Reject before charging or starting a match.
            return [g._logic_error(5006)]
        left = g.build_player_units(player)
        encounter = g._npc_encounter(player, target, 'allserver')
        right = encounter['units']
        if not left or not right:
            return [g._logic_error(5006)]
        charge = []
        if used >= free:
            prices = g._cfg_value('sAllServerRankArenaRankPayChallengeFee', player).split(',')
            price = int(prices[remaining-1]) if 0 <= remaining-1 < len(prices) and prices[remaining-1] else 50
            charge = g._spend_diamonds(player, price)
            if charge is None:
                return [g._logic_error(5005)]
        # Settle the attacker and offline defender before changing either slot.
        state = _remember_rank(g, player, by_uin[uid])
        enemy = g.store.get_player_by_role_id(target)
        enemy_state = _remember_rank(g, enemy, by_uin[target]) if enemy else None
        won, _, _, rounds = g.run_battle(left, right, puppets=g._auto_puppets(player),
                                        stop_on_elimination=True)
        # run_battle only returns won when the attacker survives and eliminates
        # the defender. Timeout or mutual elimination therefore counts as loss.
        puppets = g._auto_puppet_list(player)
        delta = 2 if won else 1
        report = g.build_fight_report(player, 0, 0, 0, won, None, False, left, right,
                                      g.encode_fight_replay(rounds),
                                      fight_type=g._ALLSERVER_RANK_ARENA_TARGET,
                                      puppet_bytes=puppets,
                                      side_types=(38, 46) if puppets else None,
                                      score=delta)
        report_id = g._store_battle_report(report)
        own_rank, enemy_rank = (_swap_slots(g, uid, target) if won else
                                (by_uin[uid], by_uin[target]))
        state['rank_schema'] = 1
        state['last_rank'] = own_rank
        state['max_rank'] = min(int(state.get('max_rank') or own_rank), own_rank)
        state['challenges'] = used + 1
        state['score'] = max(0, int(state.get('score') or 0) + (15 if won else -8))
        state['daily_score'] = int(state.get('daily_score') or 0) + delta
        counter = 'wins' if won else 'losses'
        state[counter] = int(state.get(counter) or 0) + 1
        record = dict(report_uid=report_id, enemy_role=target, enemy_name=encounter['name'],
                      enemy_uin=target, enemy_level=encounter['level'], win=bool(won), attack=True,
                      ts=int(time.time()), before_rank=by_uin[uid], after_rank=own_rank)
        state.setdefault('records', []).insert(0, record)
        state['records'] = state['records'][:20]
        g._save_json_state(player, STATE_FIELD, state)
        g._save_arena_report(player, report_id, report)
        if enemy_state is not None:
            enemy_state['rank_schema'] = 1
            enemy_state['last_rank'] = enemy_rank
            enemy_state['max_rank'] = min(int(enemy_state.get('max_rank') or enemy_rank), enemy_rank)
            counter = 'defence_losses' if won else 'defence_wins'
            enemy_state[counter] = int(enemy_state.get(counter) or 0) + 1
            reverse = dict(record, enemy_role=uid, enemy_uin=uid,
                           enemy_name=player.get('name') or 'Player',
                           enemy_level=int(player.get('level') or 1), win=not won, attack=False,
                           before_rank=by_uin[target], after_rank=enemy_rank)
            enemy_state.setdefault('records', []).insert(0, reverse)
            enemy_state['records'] = enemy_state['records'][:20]
            g._save_json_state(enemy, STATE_FIELD, enemy_state)
            g._save_arena_report(enemy, report_id, report)
        if g.bots_local.is_bot(target):
            # Preserve existing local bot statistics, without using that shared
            # score to order the independent cross-server occupied ranks.
            g.bots_local.apply_match_result(target, won)
        context = _context(g, player, refresh=True)
        _, own, state, batch = context
        body = (g.pb_bool(1, won) + g.pb_varint_field(2, own[1]) +
                g.pb_varint_field(3, int(state.get('wins') or 0)) +
                g.pb_varint_field(4, int(state.get('challenges') or 0)) +
                g.pb_varint_field(5, int(state.get('daily_score') or 0)) +
                _opponent_fields(g, batch, 6) + g.pb_bytes(7, report))
        # Info refreshes the displayed counts before Challenge opens its report.
        # Both arrays are identical, including after the proxy's automatic Info.
        return [(INFO_SC, _info_body(g, context)), (CHALLENGE_SC, body)] + charge

    for command, helper, handler in (
            ('Info', 'handle_allserver_rankarena_info', info),
            ('GetOpponent', 'handle_allserver_rankarena_getopponent', refresh),
            ('GetOpponentFormation', 'handle_allserver_rankarena_getoppformation', formation),
            ('Challenge', 'handle_allserver_rankarena_challenge', challenge)):
        g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_AllServerRankArena_' + command + '_CS'] = handler
        setattr(g, helper, handler)

