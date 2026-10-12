"""Real client table/protocol regression, using only a disposable player database."""
from pathlib import Path
import json
import re
import sqlite3
import sys
import tempfile
import unittest

SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
import player_store as store

# Redirect the storage path before server imports register their adapters.
_scratch = tempfile.TemporaryDirectory(prefix='pvz-activities-test-')
store.DB_PATH = str(Path(_scratch.name)/'players.db')
store.init_db()
import pvz_poc_server as game
import activities_repairs_20261009 as repair
repair.install(game)


class ActivitiesRepairsTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        store.close_thread_db()
        _scratch.cleanup()

    def setUp(self):
        self.player, _ = store.get_or_create_player(98769001)
        self.player['uin'] = 98769001
        self.player['name'] = '活动协议测试'
        store.save_player(self.player['uin'], name=self.player['name'])

    def _daily_params(self):
        rows = game.parse_repeated_submessages(game.handle_welfare_dailyactivity(b'', self.player)[0][1], 1)
        return {game.parse_varint_fields(row)[1]: json.loads(
            game.parse_repeated_submessages(row, 11)[0].decode('utf-8')) for row in rows}

    def test_all_daily_routes_match_the_compiled_original_quest_helper(self):
        source = (SERVER/'client_patches/activities-20261009/reference/QuestHelper.as').read_text('utf-8')
        targets = {1:'SignInViewCmd.openSignIn',2:'FriendViewCmd.zoomOut',3:'FriendViewCmd.zoomOut',
                   4:'RoleInfoCmd.buyPhysical',7:'GameFlowCmd.gotoFuben',8:'GameFlowCmd.gotoFuben',
                   9:'GameFlowCmd.gotoDrunkery',10:'DailyTdViewCmd.showMainPanel',12:'UnionCmd.enterUnion',
                   13:'GameFlowCmd.gotoArena',14:'WorldBossCmd.onClickWorldBossIcon',15:'BankViewCmd.openBank',
                   16:'GameFlowCmd.gotoZombieIsland',17:'TreasureCmd.openTreasure',18:'TreasureCmd.openTreasure',
                   19:'SignInViewCmd.openSignIn',20:'EveryDayBuyCmd.openEveryDayBuy',22:'GameFlowCmd.gotoTrialRoad',
                   23:'YetiTDViewCmd.showMainWd',24:'GameFlowCmd.gotoRankArena',25:'FuzionCmd.enterFuzion',
                   26:'GameFlowCmd.gotoGarden',27:'GameFlowCmd.gotoWT',28:'CardViewCmd.showMainWd'}
        params = self._daily_params()
        self.assertEqual(set(targets), set(repair.DAILY_JUMPS))
        for aid, target in targets.items():
            with self.subTest(activity=aid, target=target):
                route = params[aid]['localQuestJump'].split(':')
                self.assertEqual(len(route), 3)
                self.assertTrue(all(value.isdigit() for value in route))
                match = re.search(r'case\s+' + route[0] + r'\s*:(.*?break;)', source, re.S)
                self.assertIsNotNone(match)
                self.assertIn(target, match.group(1))

    def test_adventure_daily_routes_select_authored_normal_and_elite_difficulties(self):
        params = self._daily_params()
        for aid, difficulty in ((7, 1), (8, 2)):
            route = list(map(int, params[aid]['localQuestJump'].split(':')))
            self.assertEqual(route[0], 9)
            self.assertEqual(route[1]//10000, 0)
            self.assertEqual(route[1]%10, difficulty)

    def test_plant_war_shortcut_is_the_map_entry_in_the_original_table(self):
        daily = repair.client_table(game._CLIENTDB_PATH, 'DailyActInfo.txt')
        row = next(row for row in daily[1:] if row[1] == '植物大作战')
        self.assertEqual(row[4], '1014:0:0')
        source = (SERVER/'client_patches/activities-20261009/reference/QuestHelper.as').read_text('utf-8')
        branch = re.search(r'case 1014:(.*?break;)', source, re.S).group(1)
        self.assertIn('GameFlowCmd.gotoMap()', branch)

    def test_worldmap_city_has_valid_type_position_area_and_map_skin(self):
        self.player['face_id'] = 1205
        store.save_player(self.player['uin'], face_id=1205)
        handler = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_WorldMap_GetInfo_CS']
        reply = handler(game.pb_varint_field(5, 1), self.player)[0][1]
        city = game.parse_repeated_submessages(reply, 1)
        own = next(c for c in city if game.parse_varint_fields(c).get(3) == self.player['role_id'])
        fields = game.parse_varint_fields(own)
        self.assertEqual((fields[1], fields[7], fields[15]), (1, 0, 1))
        pos = game.parse_varint_fields(game.parse_repeated_submessages(own, 2)[0])
        self.assertEqual((pos[1] % 6, pos[2] % 6), (5, 5))
        initialized = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_City_Init_CS'](b'', self.player)[0][1]
        init_pos = game.parse_varint_fields(game.parse_repeated_submessages(initialized, 1)[0])
        self.assertEqual((pos[1], pos[2]), (init_pos[1], init_pos[2]))
        self.assertEqual(self.player['face_id'], 1205)

    def test_worldmap_single_city_info_matches_viewport(self):
        viewport = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_WorldMap_GetInfo_CS'](b'', self.player)[0][1]
        detail = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_WorldMap_GetCellInfo_CS'](b'', self.player)
        if isinstance(detail, list):
            detail = detail[0][1]
        positions = []
        for response in (viewport, detail):
            cell = next(c for c in game.parse_repeated_submessages(response, 1)
                        if game.parse_varint_fields(c).get(3) == self.player['role_id'])
            fields = game.parse_varint_fields(cell)
            self.assertEqual((fields[1], fields[7], fields[15]), (1, 0, 1))
            pos = game.parse_varint_fields(game.parse_repeated_submessages(cell, 2)[0])
            positions.append((pos[1], pos[2]))
        self.assertEqual(positions[0], positions[1])

    def test_worldmap_war_area_contains_only_authored_valid_landmarks(self):
        handler = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_WorldMap_GetInfo_CS']
        cells = game.parse_repeated_submessages(handler(game.pb_varint_field(5, 2), self.player)[0][1], 1)
        rows = repair.client_table(game._CLIENTDB_PATH, 'MapTempleTemplate.txt')
        templates = {int(row[0]):row for row in rows[1:] if int(row[7]) == 1}
        # The new area also includes its explicitly named training NPCs/mines;
        # validate authored temples without requiring those gameplay cells to vanish.
        cells = [cell for cell in cells if game.parse_varint_fields(cell).get(1) == 3]
        self.assertEqual(len(cells), 17)
        found = set()
        positions = set()
        for cell in cells:
            fields = game.parse_varint_fields(cell)
            row = templates[fields[50]]
            found.add(fields[50])
            self.assertEqual(fields[1], 3)
            self.assertEqual(fields[15], int(row[2]))
            self.assertEqual(fields[7], 12 if int(row[1]) == 1 else 11)
            self.assertGreater(fields[3], 0)
            pos = game.parse_varint_fields(game.parse_repeated_submessages(cell, 2)[0])
            xy = (pos[1], pos[2])
            self.assertEqual(xy, tuple(map(int, row[3].split(','))))
            positions.add(xy)
        self.assertEqual(found, set(templates))
        self.assertEqual(len(positions), 17)

    def test_empty_unconfigured_map_areas_do_not_copy_players_city(self):
        handler = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_WorldMap_GetInfo_CS']
        for area in (3, 4, 999):
            result = handler(game.pb_varint_field(5, area), self.player)
            self.assertEqual(result[0][0], 'PVZ.Cmd.Cmd_LogicError_SC')
            self.assertEqual(game.parse_varint_fields(result[0][1])[1], 4703)

    def test_all_temple_info_responses_echo_valid_clicked_positions_without_fake_guilds(self):
        handler = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Temple_GetTempleInfo_CS']
        rows = repair.client_table(game._CLIENTDB_PATH, 'MapTempleTemplate.txt')
        for row in rows[1:]:
            if int(row[7]) != 1:
                continue
            x, y = map(int, row[3].split(','))
            point = game.pb_varint_field(1, x) + game.pb_varint_field(2, y)
            request = game.pb_bytes(1, point) + game.pb_varint_field(2, int(row[2]))
            result = handler(request, self.player)[0][1]
            self.assertEqual(game.parse_repeated_submessages(result, 1), [point])
            fields = game.parse_varint_fields(result)
            self.assertEqual(fields[2], int(row[2]))
            self.assertEqual(fields[3], 1)  # The uncaptured local target has real NPC guards.
            self.assertIn(5, fields)
            self.assertEqual(fields[5], 0)
            guild = game.parse_repeated_submessages(result, 4)
            self.assertEqual(len(guild), 1)
            self.assertEqual(game.parse_varint_fields(guild[0])[1], 0)

    def test_temple_owner_and_guarder_lists_have_no_unknown_template_or_phantom_team(self):
        result = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Temple_GetTempleLordInfo_CS'](b'', self.player)[0][1]
        owners = game.parse_repeated_submessages(result, 1)
        self.assertEqual(len(owners), 17)
        self.assertEqual({game.parse_varint_fields(dto)[1] for dto in owners}, set(range(1, 18)))
        for dto in owners:
            self.assertEqual(game.parse_varint_fields(dto)[2], 0)
        rows = repair.client_table(game._CLIENTDB_PATH, 'MapTempleTemplate.txt')
        target = next(row for row in rows[1:] if int(row[7]) == 1)
        x,y = map(int,target[3].split(','))
        request = game.pb_bytes(1,game.pb_varint_field(1,x)+game.pb_varint_field(2,y))
        request += game.pb_varint_field(2,int(target[2]))
        guards = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Temple_GetGuarderInfo_CS'](request, self.player)
        guard = game.parse_repeated_submessages(guards[0][1],1)
        self.assertEqual(len(guard),1)
        fields = game.parse_varint_fields(guard[0])
        self.assertGreater(fields[2],0)
        self.assertGreater(fields[4],0)
        unknown = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Temple_GetGuarderInfo_CS'](b'', self.player)
        self.assertEqual(unknown[0][0], 'PVZ.Cmd.Cmd_LogicError_SC')
        self.assertEqual(game.parse_varint_fields(unknown[0][1])[1],4703)

    def test_map_and_temple_reads_do_not_mutate_player_or_disposable_database(self):
        def snapshot():
            connection = sqlite3.connect(store.DB_PATH)
            try:
                return list(connection.iterdump())
            finally:
                connection.close()
        # New additive map/resource state is initialized lazily. Warm both
        # supported areas and the lord list; repeated reads must then preserve
        # the complete save and all player resources.
        game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_WorldMap_GetInfo_CS'](b'', self.player)
        game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_WorldMap_GetInfo_CS'](game.pb_varint_field(5,2), self.player)
        game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Temple_GetTempleLordInfo_CS'](b'', self.player)
        before = snapshot(); player = dict(self.player)
        for command in ('WorldMap_GetInfo', 'WorldMap_GetCellInfo', 'Temple_GetTempleInfo',
                        'Temple_GetTempleLordInfo', 'Temple_GetGuarderInfo'):
            game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_' + command + '_CS'](b'', self.player)
        self.assertEqual(self.player, player)
        self.assertEqual(snapshot(), before)

    def test_every_original_daily_activity_has_real_original_quest_jump(self):
        result = game.handle_welfare_dailyactivity(b'', self.player)
        rows = game.parse_repeated_submessages(result[0][1], 1)
        actual = {}
        fuben = []
        for row in rows:
            aid = game.parse_varint_fields(row)[1]
            params = json.loads(game.parse_repeated_submessages(row, 11)[0].decode('utf-8'))
            if aid >= 58000:
                fuben.append(params['type'])
            else:
                actual[aid] = params
        authored = repair.client_table(game._CLIENTDB_PATH, 'dailyActivityInfo.txt')[1:]
        self.assertEqual(set(actual), {int(row[0]) for row in authored if int(row[0]) != 11})
        self.assertEqual(len(fuben), 4)
        self.assertEqual(set(fuben), {'catch_thief','protect_farm','normal_fruit','advance_fruit'})
        for aid, params in actual.items():
            self.assertEqual(params['type'], 'local_daily')
            self.assertTrue(params['localQuestJump'])
            self.assertTrue(params['title'])
        self.assertEqual(actual[4]['localQuestJump'], '1006:0:1')
        self.assertEqual(actual[13]['localQuestJump'], '61:0:1')
        self.assertEqual(actual[24]['localQuestJump'], '62:0:1')
        self.assertEqual(actual[25]['localQuestJump'], '1010:0:1')
        self.assertEqual(actual[26]['localQuestJump'], '49:0:1')

    def test_plant_chapter_previews_vary_and_match_actual_encounters(self):
        stage = game.handle_plant_test_getstageinfo(game.pb_varint_field(1, 1), self.player)
        previews = game.parse_repeated_submessages(stage, 6)
        self.assertEqual(len(previews), 30)
        cards = []
        for chapter, preview in enumerate(previews, 1):
            fields = game.parse_varint_fields(preview)
            enemies = game._plant_test_enemies(self.player, 1, chapter)
            cards.append(fields[2])
            self.assertEqual(fields[2], enemies[0]['card_id'])
            self.assertEqual(fields[4], enemies[0]['level'])
            self.assertEqual(len(enemies), 6)
            self.assertEqual([e['pos'] for e in enemies], game._ENEMY_POS[:6])
            self.assertEqual([e['is_leader'] for e in enemies], [True,False,False,False,False,False])
            self.assertEqual(enemies, game._plant_test_enemies(self.player, 1, chapter))
        self.assertEqual(len(set(cards)), 6)
        self.assertNotEqual(cards[0], cards[1])

    def test_plant_battle_preview_shares_varying_enemy_generator(self):
        for chapter in (1, 2, 6, 7):
            target = game.pb_bytes(1, game.pb_varint_field(1, 16)
                                    + game.pb_varint_field(12, 1)
                                    + game.pb_varint_field(13, chapter))
            preview = game.handle_battle_getfightinfo(target, self.player)
            expected = game._plant_test_enemies(self.player, 1, chapter)
            self.assertEqual(len(game.parse_repeated_submessages(preview, 4)), len(expected))

    def test_all_150_authored_plant_test_chapters_keep_real_table_numbers(self):
        rows = repair.client_table(game._CLIENTDB_PATH, 'plantTestChapter.txt')
        self.assertEqual(len(rows[1:]), 150)
        self.assertNotIn('cardId', rows[0])
        self.assertNotIn('plantId', rows[0])
        for row in rows[1:]:
            stage, chapter = int(row[1]), int(row[2])
            enemies = game._plant_test_enemies(self.player, stage, chapter)
            self.assertEqual(len(enemies), 6)
            self.assertEqual(enemies[0]['level'], int(row[3]))
            self.assertGreater(enemies[0]['hp'], 0)

    def test_war_tabs_do_not_receive_invented_empty_record(self):
        old = game.response_for('PVZ.Cmd.Cmd_War_GetHistory_SC')
        self.assertEqual(len(game.parse_repeated_submessages(old, 1)), 1)
        for name in ('GetHistory','GetMapHistory','GetSimpleHistory'):
            handler = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_War_' + name + '_CS']
            for tab in (1, 2, 3):
                result = handler(game.pb_varint_field(1, tab), self.player)
                self.assertEqual(result[0][0], 'PVZ.Cmd.Cmd_War_' + name + '_SC')
                self.assertFalse(game.parse_repeated_submessages(result[0][1], 1))
        # An actual NPC dispatch creates an accountable history rather than
        # the former synthetic all-zero record. Shared guild tabs stay empty.
        import worldmap_local as wm
        game.ensure_player_cards(self.player)
        game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Battle_GetFormation_CS'](b'', self.player)
        npc = wm.NPCS[0]
        request = (game.pb_varint_field(1,2)
                   +game.pb_bytes(2,game.pb_varint_field(1,npc['pos'][0])+game.pb_varint_field(2,npc['pos'][1]))
                   +game.pb_varint_field(3,1))
        dispatch = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_War_SetOut_CS'](request,self.player)
        self.assertTrue(any(name == 'PVZ.Cmd.Cmd_War_SetOut_SC' for name,_ in dispatch),dispatch)
        history = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_War_GetHistory_CS'](game.pb_varint_field(1,1),self.player)
        records = game.parse_repeated_submessages(history[0][1],1)
        self.assertEqual(len(records),1)
        fields = game.parse_varint_fields(records[0])
        self.assertGreater(fields[5],0)
        self.assertEqual((fields[4],fields[8],fields[10]),(1,wm.MARCHING,0))
        target = game.parse_repeated_submessages(records[0],3)[0]
        role = game.parse_repeated_submessages(target,4)[0]
        self.assertEqual(game.parse_varint_fields(role)[3],npc['role_id'])
        hidden = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Battle_QueryReport_CS'](game.pb_varint_field(1,fields[5]),self.player)
        self.assertEqual(hidden[0][0],'PVZ.Cmd.Cmd_LogicError_SC')
        self.assertEqual(game.parse_varint_fields(hidden[0][1])[1],2005)
        guild_tab = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_War_GetHistory_CS'](game.pb_varint_field(1,2),self.player)
        self.assertFalse(game.parse_repeated_submessages(guild_tab[0][1],1))

    def test_install_is_idempotent(self):
        before = game._plant_test_enemies(self.player, 1, 2)
        repair.install(game)
        self.assertEqual(before, game._plant_test_enemies(self.player, 1, 2))

    def test_z_multiple_accounts_retain_distinct_saved_city_plots(self):
        other, _ = store.get_or_create_player(98769002)
        handler = game.REQUEST_HANDLERS['PVZ.Cmd.Cmd_WorldMap_GetInfo_CS']
        request = game.pb_varint_field(5, 1)
        before = handler(request, self.player)[0][1]
        after = handler(request, self.player)[0][1]
        self.assertEqual(before, after)
        cells = game.parse_repeated_submessages(before, 1)
        cells = [cell for cell in cells if game.parse_varint_fields(cell).get(3)
                 in {self.player['role_id'],other['role_id']}]
        self.assertEqual(len(cells), 2)
        roles, positions = set(), set()
        for cell in cells:
            fields = game.parse_varint_fields(cell)
            roles.add(fields[3])
            point = game.parse_varint_fields(game.parse_repeated_submessages(cell, 2)[0])
            positions.add((point[1], point[2]))
            self.assertEqual((fields[1], fields[7], fields[15]), (1, 0, 1))
        self.assertEqual(roles, {self.player['role_id'], other['role_id']})
        self.assertEqual(len(positions), 2)


if __name__ == '__main__':
    unittest.main()
