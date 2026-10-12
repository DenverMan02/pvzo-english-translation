"""NoEndFuben repairs backed by the original Flash model and clientDB tables.

Install after missing_features_local.register(). No player save is touched by
install(); handlers update only the requesting player's normal run state.
"""
from __future__ import annotations

from collections import Counter
import json
import time

import missing_features_local as mf

PREFIX = 'PVZ.Cmd.Cmd_NoEndFuben_'
STAGE_TABLE = 'endlessFubenStageTemplate.txt'
RANK_TABLE = 'endlessFubenRankRewardTemplate.txt'


def _rows(g, table):
    data = g._read_clientdb_table(table)
    return [dict(zip(data[0], r)) for r in data[1:]] if data else []


def _pairs(text):
    return mf._parse_items(text)


def mode_rules(g, mode):
    """Use the authored restrictions of the current difficulty, not the floor."""
    row = next((r for r in _rows(g, STAGE_TABLE) if int(r['id']) == int(mode)), {})
    desc = row.get('desc', '')
    healing = '有治疗' in desc
    puppets = '有傀儡' in desc
    return {'healing': healing, 'puppets': puppets, 'pass_reward': _pairs(row.get('passReward', ''))}


def alive_positions(row, positions):
    ratios = mf._hp_ratios(row)
    return {pos: uid for pos, uid in positions.items() if ratios.get(uid, 1.0) > 0}


def ranking_prize(g, con, row):
    """Rank within this round and difficulty; use authored chest IDs/amounts."""
    if int(row['best_stage_id']) <= 0:
        return []
    ranked = con.execute(
        'SELECT uin FROM local_noend_fuben WHERE mode_id=? AND begin_time=? '
        'AND end_time=? AND best_stage_id>0 ORDER BY best_stage_id DESC,round_best_power DESC,uin ASC',
        (int(row['mode_id']), int(row['begin_time']), int(row['end_time']))).fetchall()
    rank = next((n for n, r in enumerate(ranked, 1) if int(r['uin']) == int(row['uin'])), 0)
    for template in _rows(g, RANK_TABLE):
        if int(template['id']) == int(row['mode_id']) and int(template['minRank']) <= rank <= int(template['maxRank']):
            return _pairs(template['reward'])
    return []


def install(g):
    if getattr(g, '_ENDLESS_RULES_REPAIRS_INSTALLED', False):
        return
    g._ENDLESS_RULES_REPAIRS_INSTALLED = True
    routes = g.REQUEST_HANDLERS
    original_positions = mf._noend_positions
    original_refresh = mf._noend_refresh
    original_fight = routes[PREFIX + 'Fight_CS']
    original_confirm = routes[PREFIX + 'ConfirmFight_CS']
    original_pre_card = routes[PREFIX + 'SetPreCard_CS']
    original_set_formation = routes[PREFIX + 'SetFormation_CS']
    original_reset = routes[PREFIX + 'Reset_CS']
    original_preview = g.handle_battle_getfightinfo

    def positions(row):
        return alive_positions(row, original_positions(row))

    def ensure_pending_column(con):
        columns = {r[1] for r in con.execute('PRAGMA table_info(local_noend_fuben)')}
        for name, declaration in (('pending_hp_ratios', "TEXT NOT NULL DEFAULT ''"),
                                  ('round_best_power', 'INTEGER NOT NULL DEFAULT 0')):
            if name not in columns:
                con.execute('ALTER TABLE local_noend_fuben ADD COLUMN ' + name + ' ' + declaration)
        con.execute('CREATE TABLE IF NOT EXISTS local_noend_round_rewards '
                    '(uin INTEGER NOT NULL,round_begin INTEGER NOT NULL,round_end INTEGER NOT NULL,'
                    'mode INTEGER NOT NULL,rewards TEXT NOT NULL,claimed INTEGER NOT NULL DEFAULT 0,'
                    'PRIMARY KEY(uin,round_begin,round_end,mode))')

    def settle_round(con, row):
        """Freeze the whole completed cohort before refreshing anyone's run."""
        if int(row['end_time']) <= 0 or int(row['end_time']) > int(time.time()):
            return
        entrants = con.execute('SELECT * FROM local_noend_fuben WHERE begin_time=? AND end_time=? '
                               'AND best_stage_id>0', (int(row['begin_time']), int(row['end_time']))).fetchall()
        for entrant in entrants:
            key = (int(entrant['uin']), int(entrant['begin_time']), int(entrant['end_time']), int(entrant['mode_id']))
            if con.execute('SELECT 1 FROM local_noend_round_rewards WHERE uin=? AND round_begin=? '
                           'AND round_end=? AND mode=?', key).fetchone():
                continue
            prizes = ranking_prize(g, con, entrant)
            con.execute('INSERT INTO local_noend_round_rewards VALUES(?,?,?,?,?,?)',
                        (*key, json.dumps(prizes), int(bool(entrant['has_take_reward']))))

    def pending_prizes(con, uin):
        pending = con.execute('SELECT * FROM local_noend_round_rewards WHERE uin=? AND claimed=0 '
                              'AND round_end<=? ORDER BY round_begin,round_end,mode',
                              (int(uin), int(time.time()))).fetchall()
        prizes = Counter()
        for reward in pending:
            for item_id, amount in json.loads(reward['rewards']):
                prizes[int(item_id)] += int(amount)
        return pending, [(i, n) for i, n in sorted(prizes.items()) if n > 0]

    def refresh_row(con, row):
        ensure_pending_column(con)
        # Re-read after additive schema migration so every indexed key exists.
        row = mf._noend_row(con, int(row['uin']))
        old_window = (int(row['begin_time']), int(row['end_time']))
        settle_round(con, row)
        refreshed = original_refresh(con, row)
        if (int(refreshed['begin_time']), int(refreshed['end_time'])) != old_window:
            # best_stage_id is THIS round's ranking result; history is permanent.
            con.execute("UPDATE local_noend_fuben SET best_stage_id=0,round_best_power=0,pending_hp_ratios='' WHERE uin=?", (int(row['uin']),))
            refreshed = mf._noend_row(con, int(row['uin']))
        return refreshed

    def state(player, refresh=True):
        with g.store.atomic_player(player), mf._db() as con:
            ensure_pending_column(con)
            row = mf._noend_row(con, int(player['uin']))
            return refresh_row(con, row) if refresh else row

    def restricted_player(player, mode):
        return player if mode_rules(g, mode)['puppets'] else dict(player, puppet_lineup='')

    def placement_for_uin(uin):
        with mf._db() as con:
            row = mf._noend_row(con, uin)
            owned = {int(c[0]) for c in g.store.get_cards(uin)}
            return {p: u for p, u in positions(row).items() if u in owned}

    g._noend_fuben_placements = placement_for_uin

    def formation(player):
        row = state(player, False)
        placed = placement_for_uin(int(player['uin']))
        return g.build_formation_info(restricted_player(player, int(row['mode_id'])), 1,
                                      placed, leader_uid_override=int(row['leader_uid']))

    def sync(player):
        return (PREFIX + 'SyncFormation_SC', mf._m(1, formation(player)))

    def get_formation(data, player):
        with g.store.atomic_player(player):
            state(player)
            return [sync(player)]

    def get_info(data, player):
        with g.store.atomic_player(player), mf._db() as con:
            row = refresh_row(con, mf._noend_row(con, int(player['uin'])))
            _, prizes = pending_prizes(con, int(player['uin']))
        # Info.reward is field 10: the real Flash button refuses to send the
        # claim request when it is absent, regardless of the claim handler.
        info_row = dict(row)
        info_row['has_take_reward'] = int(not bool(prizes))
        return mf._noend_info_sc(info_row) + b''.join(mf._m(10, mf._v(1, i) + mf._v(2, n)) for i, n in prizes)

    def role(data, player):
        with g.store.atomic_player(player), mf._db() as con:
            row = refresh_row(con, mf._noend_row(con, int(player['uin'])))
            ranked = con.execute(
                "SELECT n.uin,n.best_stage_id AS history_max_stage_id,COALESCE(p.name,'') AS role_name "
                'FROM local_noend_fuben n LEFT JOIN players p ON p.uin=n.uin '
                'WHERE n.mode_id=? AND n.begin_time=? AND n.end_time=? AND n.best_stage_id>0 '
                'ORDER BY n.best_stage_id DESC,n.round_best_power DESC,n.uin ASC',
                (int(row['mode_id']), int(row['begin_time']), int(row['end_time']))).fetchall()
            rank = next((n for n, r in enumerate(ranked, 1) if int(r['uin']) == int(player['uin'])), 0)
            first = mf._noend_role_dto(1, ranked[0]) if ranked else b''
            previous = mf._noend_role_dto(rank - 1, ranked[rank - 2]) if rank > 1 else b''
            self_dto = mf._noend_role_dto(rank, ranked[rank - 1]) if rank else b''
        return ((mf._m(1, first) if first else b'') + (mf._m(2, previous) if previous else b'')
                + (mf._m(3, self_dto) if self_dto else b''))

    def pre_card(data, player):
        row = state(player)
        cards = mf._repeated_int64(data, 1)
        current = mf._pre_cards(row)
        # Sixteen plants are chosen once for a run. Reissuing selection must
        # neither replace dead plants with fresh ones nor refill their HP.
        if current and cards != current:
            return mf._logic_error(5006)
        return original_pre_card(data, player)

    def set_formation(data, player):
        row = state(player)
        ratios = mf._hp_ratios(row)
        requested = mf._wire2(data, 1)
        if any(ratios.get(mf._wire0(c, 2), 1.0) <= 0 for c in mf._repeated_messages(requested, 1)):
            return [sync(player)]
        original_set_formation(data, player)
        return [sync(player)]

    def fight(data, player):
        with g.store.atomic_player(player):
            row = state(player)
            requested = max(1, mf._wire0(data, 1, 1))
            if (int(row['lose_times']) >= int(g._cfg_value('iNoEndFubenLoseTimes', player))
                    or requested > int(row['stage_id']) + 1 or not positions(row)):
                return mf._logic_error(5006)
            starting_hp = row['hp_ratios']
            previous_best = int(row['best_stage_id'])
            result = original_fight(data, player)
            if not isinstance(result, bytes):
                return result
            row = state(player, False)
            with mf._db() as con:
                if mf._wire0(result, 3):
                    # A winning replay is provisional until ConfirmFight. The
                    # retry button must begin from this floor's original HP.
                    con.execute('UPDATE local_noend_fuben SET pending_hp_ratios=?,hp_ratios=? WHERE uin=?',
                                (row['hp_ratios'], starting_hp, int(player['uin'])))
                    if requested >= previous_best:
                        power = mf._wire0(mf._wire2(mf._wire2(result, 6), 2), 7)
                        if requested == previous_best:
                            power = max(int(row['round_best_power']), power)
                        con.execute('UPDATE local_noend_fuben SET round_best_power=? WHERE uin=?',
                                    (power, int(player['uin'])))
                else:
                    con.execute("UPDATE local_noend_fuben SET pending_hp_ratios='' WHERE uin=?", (int(player['uin']),))
            prizes = []
            if mf._wire0(result, 3) and requested > int(row['reward_stage_id']):
                prizes = mode_rules(g, int(row['mode_id']))['pass_reward']
                messages = g.grant_fuben_reward(player, prizes) if prizes else []
                with mf._db() as con:
                    con.execute('UPDATE local_noend_fuben SET reward_stage_id=? WHERE uin=?',
                                (requested, int(player['uin'])))
            else:
                messages = []
            # Both Fight.reward and Sweep.reward are singular Dto_IdNum. The
            # shipped passReward has exactly one authored loot chest per floor.
            if prizes:
                if len(prizes) != 1:
                    raise ValueError('NoEndFuben passReward must fit singular reward DTO')
                i, n = prizes[0]
                result += mf._m(7, mf._v(1, i) + mf._v(2, n))
            return [(PREFIX + 'Fight_SC', result)] + messages + [sync(player)]

    def confirm(data, player):
        with g.store.atomic_player(player):
            row = state(player)
            requested = max(1, mf._wire0(data, 1, 1))
            if (int(row['has_pass_next_stage']) and requested == int(row['stage_id']) + 1
                    and row['pending_hp_ratios']):
                with mf._db() as con:
                    con.execute("UPDATE local_noend_fuben SET hp_ratios=?,pending_hp_ratios='' WHERE uin=?",
                                (row['pending_hp_ratios'], int(player['uin'])))
            result = original_confirm(data, player)
            return [(PREFIX + 'ConfirmFight_SC', result), sync(player)]

    def reset(data, player):
        with g.store.atomic_player(player):
            state(player)
            result = original_reset(data, player)
            if not (isinstance(result, list) and any(n == 'PVZ.Cmd.Cmd_LogicError_SC' for n, _ in result)):
                with mf._db() as con:
                    con.execute("UPDATE local_noend_fuben SET pending_hp_ratios='' WHERE uin=?", (int(player['uin']),))
            return result

    def sweep(data, player):
        with g.store.atomic_player(player), mf._db() as con:
            row = refresh_row(con, mf._noend_row(con, int(player['uin'])))
            target = max(0, int(row['history_max_stage_id']) - 20)
            current = int(row['stage_id']) + int(bool(row['has_pass_next_stage']))
            if target <= current or not positions(row):
                return [(PREFIX + 'Sweep_SC', mf._v(1, current))]
            reward_count = max(0, target - int(row['reward_stage_id']))
            prizes = [(i, n * reward_count) for i, n in mode_rules(g, int(row['mode_id']))['pass_reward'] if reward_count]
            messages = g.grant_fuben_reward(player, prizes) if prizes else []
            if target >= int(row['best_stage_id']):
                units = g.build_player_units(restricted_player(player, int(row['mode_id'])),
                                             placements=positions(row),
                                             leader_uid_override=int(row['leader_uid']))
                power = sum(int(u['power']) for u in units)
                if target == int(row['best_stage_id']):
                    power = max(int(row['round_best_power']), power)
                con.execute('UPDATE local_noend_fuben SET round_best_power=? WHERE uin=?',
                            (power, int(player['uin'])))
            con.execute('UPDATE local_noend_fuben SET stage_id=?,has_pass_next_stage=0,'
                        'best_stage_id=MAX(best_stage_id,?),reward_stage_id=MAX(reward_stage_id,?),'
                        "last_sweep_stage=MAX(last_sweep_stage,?),pending_hp_ratios='' WHERE uin=?",
                        (target, target, target, target, int(player['uin'])))
            body = mf._v(1, target)
            if prizes:
                if len(prizes) != 1:
                    raise ValueError('NoEndFuben passReward must fit singular reward DTO')
                i, n = prizes[0]
                body += mf._m(2, mf._v(1, i) + mf._v(2, n))
            return [(PREFIX + 'Sweep_SC', body)] + messages

    def take_reward(data, player):
        with g.store.atomic_player(player), mf._db() as con:
            refresh_row(con, mf._noend_row(con, int(player['uin'])))
            pending, prizes = pending_prizes(con, int(player['uin']))
            if not prizes:
                # No active-round ranking payout is advertised or granted.
                return [(PREFIX + 'TakeReward_SC', b'')]
            messages = g.grant_fuben_reward(player, prizes)
            for reward in pending:
                con.execute('UPDATE local_noend_round_rewards SET claimed=1 WHERE uin=? '
                            'AND round_begin=? AND round_end=? AND mode=?',
                            (int(reward['uin']), int(reward['round_begin']), int(reward['round_end']), int(reward['mode'])))
            body = b''.join(mf._m(1, mf._v(1, i) + mf._v(2, n)) for i, n in prizes)
            return [(PREFIX + 'TakeReward_SC', body)] + messages

    def run_fight(player, mode, stage):
        row = state(player)
        mode = int(row['mode_id'])  # The request cannot opt into another layer's rules.
        rules = mode_rules(g, mode)
        left = g.build_player_units(restricted_player(player, mode),
                                    placements=placement_for_uin(int(player['uin'])),
                                    leader_uid_override=int(row['leader_uid']))
        if not left:
            return [g._logic_error(5006)]
        ratios = mf._hp_ratios(row)
        for unit in left:
            unit['initial_hp'] = min(unit['hp'], max(0, round(unit['hp'] * ratios.get(int(unit['uid']), 1.0))))
            unit['is_live'] = unit['initial_hp'] > 0
        right = g._noend_fuben_squad(stage)
        right = g.apply_previewed_enemy_types(player, right, 'noend', 'noend/%d' % stage)
        puppets = g._auto_puppets(player) if rules['puppets'] else None
        puppet_bytes = g._auto_puppet_list(player) if rules['puppets'] else b''
        won, _, _, rounds = g.run_battle(left, right, puppets=puppets,
                                        allow_healing=rules['healing'], leader_sides=('left',),
                                        stop_on_elimination=True)
        report = g.build_fight_report(player, min(15, max(1, (stage + 4) // 5)), 0,
                                     stage, won, [], False, left, right, g.encode_fight_replay(rounds),
                                     fight_type=g._NOEND_FUBEN_TARGET,
                                     puppet_bytes=puppet_bytes, side_types=(43, 44))
        return [('PVZ.Cmd.Cmd_Battle_Fight_SC', g.pb_bytes(1, report))]

    g._noend_fuben_run_fight = run_fight

    def preview(data, player):
        targets = g.parse_repeated_submessages(data, 1)
        if not targets or g.parse_varint_fields(targets[0]).get(1) != g._NOEND_FUBEN_TARGET:
            return original_preview(data, player)
        row = state(player)
        result = original_preview(data, restricted_player(player, int(row['mode_id'])))
        return result

    g.handle_battle_getfightinfo = preview
    routes['PVZ.Cmd.Cmd_Battle_GetFightInfo_CS'] = preview
    routes.update({PREFIX + 'Info_CS': get_info,
                   PREFIX + 'Role_CS': role,
                   PREFIX + 'GetFormation_CS': get_formation,
                   PREFIX + 'SetFormation_CS': set_formation,
                   PREFIX + 'SetPreCard_CS': pre_card,
                   PREFIX + 'Fight_CS': fight,
                   PREFIX + 'ConfirmFight_CS': confirm,
                   PREFIX + 'Reset_CS': reset,
                   PREFIX + 'Sweep_CS': sweep,
                   PREFIX + 'TakeReward_CS': take_reward})
