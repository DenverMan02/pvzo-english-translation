"""Compatibility checks using a disposable SQLite save, without a live import."""
from pathlib import Path
from contextlib import contextmanager
import concurrent.futures
import logging
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import worldmap_local as wm
import worldmap_repairs as repairs


class Resources:
    def __init__(self,adapter):
        self.a = adapter
    def busy_cards(self,player,con=None): return set()
    def busy(self,player,con=None): return False
    def templates(self): return ([],{}, {})
    def resource_cells(self,player,st,bounds=None):
        result = []
        for kind,pos,role in ((2,(20,20),6200000000001),(3,(100,100),6300000000001)):
            dto = self.a.cell(player,st)
            dto = wm._replace(dto,1,wm._v(1,kind))
            dto = wm._replace(dto,2,wm._m(2,wm._position(pos)))
            dto = wm._replace(dto,3,wm._v(3,role))
            result.append((dto,pos))
        return result
    def handlers(self):
        return {'PVZ.Cmd.Cmd_Temple_GetTempleInfo_CS':lambda d,p:wm._reply('Temple_GetTempleInfo',b'new-resource')}


class LocalTownCompatibilityTests(unittest.TestCase):
    @contextmanager
    def db(self):
        con = sqlite3.connect(self.path)
        try:
            with con:
                yield con
        finally:
            con.close()

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='pvzol-city-compat-')
        self.addCleanup(self.scratch.cleanup)
        self.path = str(Path(self.scratch.name)/'synthetic.db')
        with self.db() as con:
            con.execute('CREATE TABLE players(uin INTEGER PRIMARY KEY,role_id INTEGER UNIQUE,name TEXT,'
                        'level INTEGER,face_id INTEGER,coin INTEGER,present_ticket INTEGER,ticket INTEGER)')
            con.execute('CREATE TABLE items(uin INTEGER,item_id INTEGER,count INTEGER,PRIMARY KEY(uin,item_id))')
            con.executemany('INSERT INTO players VALUES(?,?,?,?,?,?,?,?)',
                            [(11,10001,'Local A',100,1205,10000,100,100),(22,10002,'Local B',50,1999,2345,10,20)])
        self.api = SimpleNamespace(store=SimpleNamespace(DB_PATH=self.path),REQUEST_HANDLERS={},
                                   build_player_diamond=lambda p:b'',build_item_dto=lambda i,n:wm._v(1,i)+wm._v(2,n),
                                   log=logging.getLogger('city-compat-test'))
        self.a = self.api._worldmap_adapter = wm.WorldMap(self.api)
        self.a.resources = Resources(self.a)
        repairs.install(self.api)
        self.clock = patch.object(wm,'_now',return_value=1800000000)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.p = self.player(11)

    def player(self,uin):
        with self.db() as con:
            con.row_factory = sqlite3.Row
            return dict(con.execute('SELECT * FROM players WHERE uin=?',(uin,)).fetchone())
    def call(self,name,data=b'',player=None):
        return self.api.REQUEST_HANDLERS['PVZ.Cmd.Cmd_'+name+'_CS'](data,player or self.p)
    def body(self,result,name):
        return next(body for kind,body in result if kind == 'PVZ.Cmd.Cmd_'+name+'_SC')
    def cells(self,data=b''):
        body = self.body(self.call('WorldMap_GetInfo',data),'WorldMap_GetInfo')
        return [value for wire,value in wm._fields(body).get(1,[]) if wire == 2]
    def position(self,cell):
        raw = wm._one(cell,2,2)
        return wm._one(raw,1),wm._one(raw,2)
    def error(self,result):
        return wm._one(self.body(result,'LogicError'),1)

    def test_registration_is_save_free_and_force_restores_original_adapter_routes(self):
        self.api.REQUEST_HANDLERS['PVZ.Cmd.Cmd_War_GetHistory_CS'] = lambda d,p:wm._reply('War_GetHistory',b'old')
        self.api.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Temple_GetTempleInfo_CS'] = lambda d,p:wm._reply('Temple_GetTempleInfo',b'old')
        with patch('worldmap_local.sqlite3.connect',side_effect=AssertionError('registration opened save')):
            repairs.install(self.api,force=True)
        self.assertIs(self.api._worldmap_adapter,self.a)
        self.assertEqual(self.body(self.call('Temple_GetTempleInfo'),'Temple_GetTempleInfo'),b'new-resource')
        self.assertNotEqual(self.body(self.call('War_GetHistory'),'War_GetHistory'),b'old')
        self.assertIn('PVZ.Cmd.Cmd_Temple_GetTempleInfo_CS',self.api._WORLD_MAP_REQUESTS)

    def test_city_migration_retains_saved_id_uses_grid_and_is_idempotent(self):
        with self.db() as con:
            repairs.LocalCityDisplay.ensure(con)
            con.execute('INSERT INTO local_worldmap_cities VALUES(11,2)')
        initial = self.body(self.call('City_Init'),'City_Init')
        self.assertEqual(wm._pos(initial,1),(11,11))
        self.call('City_Init')
        with self.db() as con:
            self.assertEqual(con.execute('SELECT plot FROM local_worldmap_cities WHERE uin=11').fetchone()[0],2)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM local_worldmap_city_migrations WHERE uin=11').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT coin FROM players WHERE uin=11').fetchone()[0],10000)

    def test_viewport_contains_real_accounts_and_unmodified_npc_resource_types(self):
        cells = self.cells()
        humans = {wm._one(c,3):c for c in cells if wm._one(c,3) in (10001,10002)}
        self.assertEqual(set(humans),{10001,10002})
        self.assertEqual(self.position(humans[10001]),(5,5))
        self.assertEqual(self.position(humans[10002]),(11,5))
        self.assertEqual({wm._one(c,1) for c in cells},{1,2,3})
        self.assertEqual(len([c for c in cells if wm._one(c,3) in {n['role_id'] for n in wm.NPCS}]),3)
        self.assertEqual((wm._one(humans[10001],1),wm._one(humans[10001],7),wm._one(humans[10001],15)),(1,0,1))
        with self.db() as con:
            self.assertIsNone(con.execute('SELECT 1 FROM local_worldmap_state WHERE uin=22').fetchone())
            self.assertEqual(con.execute('SELECT coin,face_id FROM players WHERE uin=22').fetchone(),(2345,1999))

    def test_signed_viewport_applies_equally_to_real_and_training_cells(self):
        bounds = wm._m(1,wm._v(1,0xffffffffffffffff)+wm._v(2,0xffffffffffffffff))
        bounds += wm._m(4,wm._position((8,8)))
        cells = self.cells(bounds)
        self.assertEqual([wm._one(c,3) for c in cells],[10001])

    def test_detail_queries_match_viewport_and_unknown_targets_are_errors(self):
        humans = {wm._one(c,3):c for c in self.cells() if wm._one(c,3) in (10001,10002)}
        for role in humans:
            detail = wm._one(self.body(self.call('WorldMap_GetCellInfo',wm._v(1,role)),'WorldMap_GetCellInfo'),1,2)
            self.assertEqual(detail,humans[role])
        self.assertEqual(self.error(self.call('WorldMap_GetCellInfo',wm._v(1,99999))),108)
        self.assertEqual(self.error(self.call('WorldMap_GetCityInfo',wm._v(1,99999))),108)
        city = self.body(self.call('WorldMap_GetCityInfo',wm._v(1,10002)),'WorldMap_GetCityInfo')
        self.assertEqual((wm._one(city,1),wm._one(city,4)),(10002,1999))
        npc = wm.NPCS[0]
        detail = wm._one(self.body(self.call('WorldMap_GetCellInfo',wm._v(1,npc['role_id'])),'WorldMap_GetCellInfo'),1,2)
        self.assertEqual((wm._one(detail,3),wm._one(detail,5)),(npc['role_id'],3))

    def test_local_guild_and_flags_are_preserved_without_changing_membership(self):
        with self.db() as con:
            con.execute('CREATE TABLE local_guilds(guild_id INTEGER PRIMARY KEY,name TEXT,color_id INTEGER,'
                        'color2_id INTEGER,surface_id INTEGER,sign_id INTEGER)')
            con.execute('CREATE TABLE local_guild_members(uin INTEGER PRIMARY KEY,guild_id INTEGER,role_id INTEGER)')
            con.execute('INSERT INTO local_guilds VALUES(7,"Local Guild",2,3,4,5)')
            con.executemany('INSERT INTO local_guild_members VALUES(?,7,?)',[(11,10001),(22,10002)])
        cells = self.cells()
        for cell in cells[:2]:
            self.assertEqual(wm._one(cell,11),7)
            self.assertEqual(wm._one(cell,12,2),b'Local Guild')
            flags = wm._one(cell,13,2)
            self.assertEqual([wm._one(flags,i) for i in range(1,5)],[2,3,4,5])
        members = self.body(self.call('WorldMap_GetGuildMemberList'),'WorldMap_GetGuildMemberList')
        self.assertEqual(len(wm._fields(members)[1]),2)
        remote = self.body(self.call('WorldMap_GetCityInfo',wm._v(1,10002)),'WorldMap_GetCityInfo')
        self.assertEqual(wm._one(remote,5),7)

    def test_real_accounts_cannot_be_declared_against_or_dispatched_to(self):
        human = next(c for c in self.cells() if wm._one(c,3) == 10002)
        self.assertEqual(wm._one(human,5),0)
        self.assertEqual(self.error(self.call('War_DeclareWar',wm._v(1,10002))),4706)
        request = wm._v(1,2)+wm._m(2,wm._position(self.position(human)))+wm._v(3,1)
        self.assertEqual(self.error(self.call('War_SetOut',request)),4703)
        with self.db() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM local_worldmap_missions').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT coin FROM players WHERE uin=22').fetchone()[0],2345)

    def test_migrated_town_position_is_not_reset_on_repeated_queries(self):
        self.call('City_Init')
        with self.db() as con:
            con.execute('UPDATE local_worldmap_state SET x=41,y=47 WHERE uin=11')
        initial = self.body(self.call('City_Init'),'City_Init')
        self.assertEqual(wm._pos(initial,1),(41,47))
        own = next(c for c in self.cells() if wm._one(c,3) == 10001)
        self.assertEqual(self.position(own),(41,47))

    def test_preexisting_changed_state_is_preserved_and_other_areas_are_not_duplicated(self):
        with self.a.db(self.p) as con:
            con.execute('UPDATE local_worldmap_state SET area=2,x=41,y=47 WHERE uin=11')
        self.call('City_Init')
        self.assertFalse(any(wm._one(c,3) == 10001 for c in self.cells()))
        own = next(c for c in self.cells(wm._v(5,2)) if wm._one(c,3) == 10001)
        self.assertEqual((self.position(own),wm._one(own,15)),((41,47),2))

    def test_legacy_mine_marker_assignment_is_moved_to_safe_city_site_without_reassigning_id(self):
        with self.db() as con:
            repairs.LocalCityDisplay.ensure(con)
            con.execute('INSERT INTO local_worldmap_cities VALUES(11,36)')
        initial = self.body(self.call('City_Init'),'City_Init')
        self.assertEqual(wm._pos(initial,1),(41,5))
        with self.db() as con:
            self.assertEqual(con.execute('SELECT plot FROM local_worldmap_cities WHERE uin=11').fetchone()[0],36)

    def test_fixed_move_rejects_other_real_city_without_spending_item(self):
        self.cells()
        with self.db() as con:
            con.execute('INSERT INTO items VALUES(11,?,2)',(wm.MOVE_ITEM,))
        request = wm._v(1,1)+wm._m(2,wm._position((11,5)))
        self.assertEqual(self.error(self.call('City_FixedMove',request)),4703)
        with self.db() as con:
            self.assertEqual(con.execute('SELECT count FROM items WHERE uin=11').fetchone()[0],2)
            self.assertEqual(con.execute('SELECT x,y FROM local_worldmap_state WHERE uin=11').fetchone(),(5,5))

    def test_parallel_moves_to_same_empty_site_commit_only_one_item_charge(self):
        other = self.player(22)
        self.call('City_Init')
        self.call('City_Init',player=other)
        with self.db() as con:
            con.executemany('INSERT INTO items VALUES(?,?,2)',[(11,wm.MOVE_ITEM),(22,wm.MOVE_ITEM)])
        request = wm._v(1,1)+wm._m(2,wm._position((41,41)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda p:self.call('City_FixedMove',request,p),(self.p,other)))
        self.assertEqual(sum(any(name.endswith('City_FixedMove_SC') for name,_ in result) for result in results),1)
        self.assertEqual(sum(any(name.endswith('LogicError_SC') for name,_ in result) for result in results),1)
        with self.db() as con:
            self.assertEqual(con.execute('SELECT SUM(count) FROM items WHERE item_id=?',(wm.MOVE_ITEM,)).fetchone()[0],3)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM local_worldmap_state WHERE x=41 AND y=41').fetchone()[0],1)

    def test_cross_area_relocation_avoids_overlap_and_preserves_return_positions(self):
        other = self.player(22)
        for p in (self.p,other):
            self.call('City_Init',player=p)
            self.body(self.call('City_ChangeArea',wm._v(1,2),p),'City_ChangeArea')
        with self.db() as con:
            rows = con.execute('SELECT x,y FROM local_worldmap_state ORDER BY uin').fetchall()
            self.assertNotEqual(rows[0],rows[1])
            self.assertTrue(all(x % 6 == 5 and y % 6 == 5 for x,y in rows))
            self.assertTrue(all(0 <= x < wm.MAP_SIZES[2]-1 and 0 <= y < wm.MAP_SIZES[2]-1 for x,y in rows))
            con.execute('UPDATE local_worldmap_state SET change_cd=0')
        for p in (self.p,other):
            self.call('City_ChangeArea',wm._v(1,1),p)
        with self.db() as con:
            self.assertEqual(con.execute('SELECT x,y FROM local_worldmap_state ORDER BY uin').fetchall(),[(5,5),(11,5)])

    def test_first_home_migration_avoids_another_previously_moved_town_and_keeps_plot_id(self):
        other = self.player(22)
        with self.a.db(other) as con:
            con.execute('UPDATE local_worldmap_state SET x=5,y=5 WHERE uin=22')
        with self.db() as con:
            repairs.LocalCityDisplay.ensure(con)
            con.executemany('INSERT INTO local_worldmap_cities VALUES(?,?)',[(11,0),(22,1)])
        initial = self.body(self.call('City_Init'),'City_Init')
        self.assertEqual(wm._pos(initial,1),(11,5))
        with self.db() as con:
            self.assertEqual(con.execute('SELECT plot FROM local_worldmap_cities WHERE uin=11').fetchone()[0],0)
            self.assertEqual(con.execute('SELECT x,y FROM local_worldmap_state WHERE uin=22').fetchone(),(5,5))


if __name__ == '__main__':
    unittest.main()
