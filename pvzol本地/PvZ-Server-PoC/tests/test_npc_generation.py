"""Shared NPC generator, production-save migration and protocol consistency."""
import collections
import hashlib
import json
import os
from pathlib import Path
import random
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pvz_poc_server as game
import npc_lineups

# The harness must run against a disposable DB supplied by the caller.
DB = os.environ['PVZ_NPC_TEST_DB']
assert Path(DB).name != 'players.db', 'Use a disposable database copy'
game.store.set_db_path(DB)
game._CLIENTDB_PATH = os.environ.get('PVZ_NPC_CLIENTDB', str(ROOT.parent/'webroot'/'clientDB.txt'))
game.store.init_db()
game.guild_local.initialize(DB)
game.load_battle_cards()


def real_rows():
    result = {}
    with sqlite3.connect(DB) as con:
        tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
        for table in tables:
            cols = [r[1] for r in con.execute('pragma table_info("%s")' % table)]
            if 'uin' in cols:
                rows = con.execute('select * from "%s" where uin not between 900001 and 900050' % table).fetchall()
                result[table] = hashlib.sha256(repr(sorted(rows, key=repr)).encode()).hexdigest()
    return result


class NpcGeneratorTest(unittest.TestCase):
    def assertTeam(self, units):
        self.assertEqual(len(units), 6)
        families = [npc_lineups.plant_family(game._BATTLE_CARDS,u['card_id']) for u in units]
        self.assertEqual(len(set(families)), 6)
        self.assertEqual(len({u['pos'] for u in units}), 6)
        self.assertEqual(sum(bool(u['is_leader']) for u in units), 1)
        minimum = {r['awake_id']:r['card_level'] for r in game.load_awake_rules().values()}
        for u in units:
            self.assertGreaterEqual(u['level'], minimum.get(u['card_id'],1))
            self.assertLessEqual(u['level'], game._BATTLE_CARDS[u['card_id']]['maxLv'])

    def test_01_random_teams_and_fallback_never_repeat_species(self):
        captain_positions = set()
        all_ids = set(game._BATTLE_CARDS)
        for seed in range(120):
            units = npc_lineups.generate_units(game, (1000,10000,100000,900000)[seed%4], rng=random.Random(seed),
                avoid_card_ids=all_ids if seed%2 else (),used_card_ids=all_ids if seed%3==0 else ())
            self.assertTeam(units)
            captain_positions.add(next(u['pos'] for u in units if u['is_leader']))
            for u in units:
                self.assertEqual(u['power'],game.card_power(game.card_stats_at_level(u['card_id'],u['level'])))
        self.assertEqual(captain_positions,set(game._FORMATION_POS))

    def test_02_replace_fifty_legacy_teams_without_touching_real_players(self):
        before = real_rows()
        game._seed_bots()
        after = real_rows()
        for table,digest in before.items():
            self.assertEqual(after[table],digest,table)
        with sqlite3.connect(DB) as con:
            self.assertEqual(con.execute('select count(*) from bot_generation where version=1').fetchone()[0],50)
        captain_positions = set()
        teams = set()
        for b in game.bots_local.bots():
            units = game._arena_opponent_units(b['uin'])
            self.assertTeam(units)
            teams.add(tuple(u['card_id'] for u in units))
            captain_positions.add(next(u['pos'] for u in units if u['is_leader']))
            self.assertEqual(sum(u['power'] for u in units), game.bots_local.bot_state(b['uin'])['power'])
            player,_ = game.store.get_or_create_player(b['uin'])
            for u in units:
                needed = max(need for lv,exp,need in game.load_card_level_curve() if lv<=u['level'])
                self.assertGreaterEqual(player['level'],needed)
            with sqlite3.connect(DB) as con:
                member=con.execute('select name,fight_power from local_guild_members where uin=?',(b['uin'],)).fetchone()
                self.assertEqual(member,(b['name'],sum(u['power'] for u in units)))
        self.assertEqual(len(teams),50)
        self.assertGreaterEqual(len(captain_positions),5)

    def test_03_restart_keeps_saved_teams_captains_and_scores(self):
        before = {b['uin']:(game.store.get_cards(b['uin']),game.store.get_formation(b['uin']),game.store.get_formation_leader(b['uin']),game.bots_local.bot_state(b['uin'])) for b in game.bots_local.bots()}
        game.bots_local._BOTS = game.bots_local._make_roster()
        game._seed_bots()
        for uin,state in before.items():
            self.assertEqual((game.store.get_cards(uin),game.store.get_formation(uin),game.store.get_formation_leader(uin),game.bots_local.bot_state(uin)),state)

    def test_04_guard_refresh_preview_and_cached_fight_team_agree(self):
        player,_ = game.store.get_or_create_player(799999999)
        player['coin']=100
        fixture = npc_lineups.generate_units(game,800000,rng=random.Random(123))
        for u in fixture:
            game.store.add_card(player['uin'],u['uid'],u['card_id'],u['level'],0)
        for team in (1,2000):
            game.store.set_formation(player['uin'],team,{u['pos']:u['uid'] for u in fixture})
        handler=game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Guard_RefreshRobTarget_CS']
        old = None
        for repeat in range(4):
            handler(b'',player)
            entry=game.guard_local._ROB_TARGETS[player['uin']]
            fingerprint=[]
            for i,member in enumerate(entry['members']):
                units=[card['unit'] for card in member['cards']]
                self.assertTeam(units)
                self.assertEqual(member['power'],sum(u['power'] for u in units))
                preview=game.guard_local.rob_fight_preview(game,player,i)
                self.assertEqual(preview[2],member['power'])
                decoded=[game.parse_varint_fields(raw) for raw in preview[1]]
                self.assertEqual([(r[1],r[2],bool(r.get(4))) for r in decoded],
                                 [(u['pos'],u['card_id'],u['is_leader']) for u in units])
                fingerprint.append(tuple(u['card_id'] for u in units))
            if old is not None:
                self.assertNotEqual(fingerprint,old)
            old=fingerprint
        expected=[(c['unit']['pos'],c['unit']['card_id'],c['unit']['is_leader']) for c in entry['members'][0]['cards']]
        def combat(left,right,**options):
            self.assertEqual([(u['pos'],u['card_id'],u['is_leader']) for u in right],expected)
            return False,sum(u['power'] for u in left),sum(u['power'] for u in right),[]
        player['light']=5
        with patch.object(game,'run_battle',side_effect=combat) as battle:
            game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Guard_FightRobTarget_CS'](game.pb_varint_field(1,0),player)
            battle.assert_called_once()

    def test_05_preview_leader_and_position_match_saved_team(self):
        for b in game.bots_local.bots()[::5]:
            units=game._arena_opponent_units(b['uin'])
            stored=game.store.get_formation(b['uin'])
            leader=game.store.get_formation_leader(b['uin'])
            for u in units:
                self.assertEqual(stored[u['pos']-1],u['uid'])
                dto=game.parse_varint_fields(game._enemy_card_dto(u['pos'],u['uid'],u['card_id'],u['level'],u,u['is_leader']))
                self.assertEqual(bool(dto.get(4)),u['uid']==leader)
                self.assertEqual(dto[5],u['power'])

    def test_06_each_refresh_generates_new_opponents_and_preserves_encounters(self):
        player,_ = game.store.get_or_create_player(799999999)
        game._refresh_arena_npcs(player,'rank')
        first={b['uin']:game._npc_encounter(player,b['uin'],'rank') for b in game.bots_local.bots()}
        game.bots_local.regenerate([900001])
        self.assertEqual(game._npc_encounter(player,900001,'rank'),first[900001])
        # Battle gets an independent copy; damage cannot corrupt later previews.
        damaged=game._npc_encounter(player,900001,'rank')
        damaged['units'][0]['hp']=0
        self.assertEqual(game._npc_encounter(player,900001,'rank'),first[900001])
        game._refresh_arena_npcs(player,'rank')
        second={b['uin']:game._npc_encounter(player,b['uin'],'rank') for b in game.bots_local.bots()}
        self.assertTrue(all(first[uin]['units']!=second[uin]['units'] for uin in first))
        self.assertTrue(any(first[uin]['name']!=second[uin]['name'] for uin in first))
        for encounter in second.values():
            self.assertTeam(encounter['units'])

    def test_08_status_and_opponent_rotation_preserve_seeded_teams(self):
        player,_=game.store.get_or_create_player(799999999)
        before={b['uin']:game.store.get_cards(b['uin']) for b in game.bots_local.bots()}
        with patch.object(game.bots_local,'regenerate',wraps=game.bots_local.regenerate) as regen:
            for _ in range(2):
                result=game.handle_rankarena_getmyinfo(b'',player)
                self.assertEqual(result[0][0],'PVZ.Cmd.Cmd_RankArena_GetMyInfo_SC')
            game.handle_allserver_rankarena_info(b'',player)
            regen.assert_not_called()
            self.assertEqual(before,{b['uin']:game.store.get_cards(b['uin']) for b in game.bots_local.bots()})
            shown = []
            for _ in range(2):
                result = game.handle_rankarena_getopponentinfo(b'',player)
                self.assertEqual(result[0][0], 'PVZ.Cmd.Cmd_RankArena_GetOpponentInfo_SC')
                rows = game.parse_repeated_submessages(result[0][1], 1)
                shown.append(tuple(game.parse_varint_fields(row)[1] for row in rows))
                self.assertEqual(len(rows), game._RANK_ARENA_OPPONENT_NUM)
            regen.assert_not_called()
        self.assertNotEqual(shown[0], shown[1])
        self.assertEqual(before,{b['uin']:game.store.get_cards(b['uin']) for b in game.bots_local.bots()})

    def test_07_guild_member_refresh_replaces_only_that_guilds_bots(self):
        player,_=game.store.get_or_create_player(799999999)
        with sqlite3.connect(DB) as con:
            gid=con.execute('select guild_id from local_guild_members where uin=900001').fetchone()[0]
            members={r[0] for r in con.execute('select uin from local_guild_members where guild_id=?',(gid,))}
        before={b['uin']:game.store.get_cards(b['uin']) for b in game.bots_local.bots()}
        game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_GuildMember_CS'](game.pb_varint_field(1,gid),player)
        for uin,cards in before.items():
            if uin in members:
                self.assertNotEqual(game.store.get_cards(uin),cards)
            else:
                self.assertEqual(game.store.get_cards(uin),cards)

if __name__=='__main__':
    try:
        unittest.main()
    finally:
        game.store.close_thread_db()

