"""Plant-test retry/reset protocol and saved opponent formations.

The stage's decorative showCard uses exp for the encounter's actual total power
and level for its stage id. ModuleAwake stores exp in a dedicated preview-power
slot; neither field is used to construct a battle card.
"""
import copy
import json
import random
from functools import wraps


def install(g):
    if getattr(g, '_plant_test_repairs_20261011', False):
        return
    g._plant_test_repairs_20261011 = True
    g.SERVER_CONFIG_OVERRIDES['iPlantTestDailyChallengeTimes'] = '3'
    original_state = g._plant_test_state

    def load_state(player):
        previous = g._load_json_state(player, 'plant_test_data', {})
        data = original_state(player)
        changed = False
        if data.get('rules_version') != 2:
            # Retain losses already spent under the erroneous one-attempt rule.
            if previous.get('day') == data.get('day'):
                for progress in data['stages'].values():
                    progress['remain'] = max(0, 3 - max(0, 1 - int(progress.get('remain', 1))))
            data['rules_version'] = 2
            changed = True
        if previous.get('day') != data.get('day') or previous.get('week') != data.get('week'):
            data.pop('opponents', None)
            changed = True
        if changed:
            g._save_json_state(player, 'plant_test_data', data)
        return data

    def state(player):
        # Even read/preview requests normalize and save this JSON. Refresh it
        # under the same transaction used by fights, resets and formation edits.
        with g.store.atomic_player(player):
            return load_state(player)

    def atomic(handler):
        @wraps(handler)
        def wrapped(cmd_data, player):
            with g.store.atomic_player(player):
                return handler(cmd_data, player)
        return wrapped

    def copied_roster(player, bots):
        """Read saved, owned team-one cards without logging the source account in."""
        with g.store._db() as con:
            rows = con.execute(
                'SELECT p.* FROM players p WHERE p.uin != ? AND '
                'EXISTS(SELECT 1 FROM cards c WHERE c.uin=p.uin) ORDER BY p.uin',
                (player['uin'],)).fetchall()
        roster = []
        for row in rows:
            owner = dict(row)
            if g.bots_local.is_bot(owner['uin']) != bots:
                continue
            owned = {card[0] for card in g.store.get_cards(owner['uin'])}
            placements = {pos: uid for pos, uid in g.store.get_formation(owner['uin'], 1).items()
                          if pos in g._FORMATION_POS and uid in owned}
            if not placements:
                continue
            units = g.build_player_units(owner, placements)
            if not units:
                continue
            units = copy.deepcopy(units)
            for unit in units:
                unit['pos'] += 1
            roster.append({'source': 'bot' if bots else 'player',
                           'role_id': int(owner.get('role_id') or owner['uin']),
                           'name': owner.get('name') or '植物测验守关者',
                           'units': units,
                           'power': sum(int(unit['power']) for unit in units)})
        return roster

    def prepare_stage(player, data, sid):
        saved = data.setdefault('opponents', {})
        key = str(sid)
        versions = data.setdefault('opponent_rules', {})
        if key in saved and (versions.get(key) == 1
                             or data['stages'][key].get('current', 0) > 0):
            # Finish already-started climbs with their previewed snapshots.
            return saved[key]
        # Prefer real players, but never reuse the same identity throughout one
        # grade. Stored bots fill the remaining chapters before local generation.
        roster = copied_roster(player, False) + copied_roster(player, True)
        used_names = set()
        encounters = {}
        for row in g._plant_test_rows(sid):
            sub = int(row['chapterID'])
            desired = max(1, int(row['totalPower']))
            candidates = [entry for entry in roster if entry['name'] not in used_names]
            if candidates:
                chosen = min(candidates, key=lambda entry: (entry['source'] != 'player',
                             abs(entry['power'] - desired), entry['role_id']))
                encounter = copy.deepcopy(chosen)
                roster = [entry for entry in roster if entry['role_id'] != chosen['role_id']]
            else:
                # This also makes a newly-created, isolated local world playable
                # before the usual arena bot seed has run.
                seed = '%s/%s/%s/%s/%s' % (player['uin'], data['day'], data.get('resets', 0), sid, sub)
                units = g.npc_lineups.generate_units(
                    g, desired, rng=random.Random(seed), positions=g._ENEMY_POS,
                    uid_base=70000000 + sid * 10000 + sub * 100, count=6)
                encounter = {'source': 'generated_bot', 'role_id': 0,
                             'name': '植物测验守关者·%s-%s' % (sid, sub), 'units': units,
                             'power': sum(int(unit['power']) for unit in units)}
            used_names.add(encounter['name'])
            encounters[str(sub)] = encounter
        saved[key] = encounters
        versions[key] = 1
        g._save_json_state(player, 'plant_test_data', data)
        return encounters

    def enemies(player, sid, sub):
        if sid * 1000 + sub not in g._plant_test_tables()['chapters']:
            return []
        with g.store.atomic_player(player):
            data = state(player)
            entry = prepare_stage(player, data, sid).get(str(sub), {})
            return copy.deepcopy(entry.get('units', []))

    def stage_info(cmd_data, player):
        sid = g.parse_varint_fields(cmd_data).get(1, 1)
        data = state(player)
        if sid not in g._plant_test_tables()['conditions']:
            return []
        progress = data['stages'][str(sid)]
        body = (g.pb_varint_field(1, sid) + g.pb_varint_field(2, progress['current'])
                + g.pb_varint_field(3, progress['best']) + g.pb_varint_field(4, progress['remain']))
        body += g._plant_test_idnums(5, progress['rewards'].items())
        encounters = prepare_stage(player, data, sid)
        for row in g._plant_test_rows(sid):
            sub = int(row['chapterID'])
            entry = encounters[str(sub)]
            leader = next((unit for unit in entry['units'] if unit.get('is_leader')), entry['units'][0])
            card = (g.pb_int64_field(1, sid * 1000 + sub) + g.pb_varint_field(2, leader['card_id'])
                    + g.pb_varint_field(3, entry['power']) + g.pb_varint_field(4, sid)
                    + g.pb_varint_field(9, int(leader.get('avatar_id') or 0)))
            body += g.pb_bytes(6, card) + g.pb_string(7, entry['name'])
        return body

    def commit(player, data, rewards=(), diamond_cost=0):
        totals = {}
        for iid, amount in rewards:
            totals[int(iid)] = totals.get(int(iid), 0) + int(amount)
        with g.store._db() as con:
            g.store.begin_immediate(con)
            wallet = con.execute('SELECT coin, ticket FROM players WHERE uin=?', (player['uin'],)).fetchone()
            if not wallet or wallet['ticket'] < diamond_cost:
                return None
            con.execute('UPDATE players SET plant_test_data=?, coin=coin+?, ticket=ticket-? WHERE uin=?',
                        (json.dumps(data, ensure_ascii=False), totals.get(1, 0), diamond_cost, player['uin']))
            for iid, amount in totals.items():
                if iid != 1:
                    con.execute('INSERT INTO items(uin,item_id,count) VALUES(?,?,?) '
                                'ON CONFLICT(uin,item_id) DO UPDATE SET count=count+excluded.count',
                                (player['uin'], iid, amount))
        player['plant_test_data'] = json.dumps(data, ensure_ascii=False)
        player['coin'] = wallet['coin'] + totals.get(1, 0)
        player['ticket'] = wallet['ticket'] - diamond_cost
        messages = []
        if 1 in totals:
            messages.append(('PVZ.Cmd.Cmd_SyncCoin_SC', g.pb_varint_field(1, player['coin'])))
        if diamond_cost:
            messages.append(('PVZ.Cmd.Cmd_SyncTicket_SC', g.pb_varint_field(1, player['ticket'])))
        items = b''.join(g.pb_bytes(1, g.build_item_dto(iid, g.store.get_item_count(player['uin'], iid)))
                         for iid in totals if iid != 1)
        if items:
            messages.append(('PVZ.Cmd.Cmd_Item_Update_SC', items))
        return messages

    def restart(cmd_data, player):
        sid = g.parse_varint_fields(cmd_data).get(1, 0)
        data = state(player)
        row = g._plant_test_tables()['resets'].get(data.get('resets', 0) + 1)
        if not row or sid < 1 or sid > g._plant_test_opened(player, data):
            return [g.puppet_error(11106 if not row else 11101)]
        currency, cost = map(int, row['originalCost'].split(':'))
        if currency != 37:
            return [g.puppet_error(11106)]
        progress = data['stages'][str(sid)]
        if progress['current'] == 0 and progress['remain'] >= 3:
            return [g.puppet_error(11106)]
        progress.update(current=0, remain=3, rewards={})
        data.get('opponents', {}).pop(str(sid), None)
        data['resets'] = data.get('resets', 0) + 1
        updates = commit(player, data, diamond_cost=cost)
        if updates is None:
            return [g.puppet_error(11107)]
        # RestartStage_SC increments resetCount in the Flash proxy. The full
        # stage update then resets its daily chapter so it returns to chapter 1.
        return [('PVZ.Cmd.Cmd_PlantTest_RestartStage_SC',
                 g.pb_varint_field(1, sid) + g.pb_varint_field(2, 3))] + updates + [
                    ('PVZ.Cmd.Cmd_PlantTest_GetStageInfo_SC', stage_info(g.pb_varint_field(1, sid), player))]

    original_fight = g.handle_plant_test_fight
    original_sweep = g.handle_plant_test_sweep

    def fight(cmd_data, player):
        fields = g.parse_varint_fields(cmd_data)
        sid, sub = fields.get(1, 0), fields.get(2, 0)
        data = state(player)
        if sid * 1000 + sub in g._plant_test_tables()['chapters']:
            # Prepare before the original handler loads its progress object so
            # its reward commit retains the exact previewed enemy snapshot.
            prepare_stage(player, data, sid)
        return original_fight(cmd_data, player)

    def sweep(cmd_data, player):
        sid = g.parse_varint_fields(cmd_data).get(1, 0)
        messages = original_sweep(cmd_data, player)
        if any(name == 'PVZ.Cmd.Cmd_PlantTest_Sweep_SC' for name, _ in messages):
            data = state(player)
            progress = data['stages'][str(sid)]
            if progress['current'] == len(g._plant_test_rows(sid)):
                progress['completed'] = True
                g._save_json_state(player, 'plant_test_data', data)
        return messages

    g._plant_test_state = state
    g._plant_test_daily_times = lambda: 3
    g._plant_test_enemies = enemies
    g._plant_test_commit = commit
    for action, helper, handler in (
            ('GetInfo', 'handle_plant_test_getinfo', g.handle_plant_test_getinfo),
            ('GetStageInfo', 'handle_plant_test_getstageinfo', stage_info),
            ('GetFormation', 'handle_plant_test_getformation', g.handle_plant_test_getformation),
            ('SetFormation', 'handle_plant_test_setformation', g.handle_plant_test_setformation),
            ('RestartStage', 'handle_plant_test_restart', restart),
            ('Fight', 'handle_plant_test_fight', fight),
            ('Sweep', 'handle_plant_test_sweep', sweep)):
        wrapped = atomic(handler)
        setattr(g, helper, wrapped)
        g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_PlantTest_' + action + '_CS'] = wrapped
