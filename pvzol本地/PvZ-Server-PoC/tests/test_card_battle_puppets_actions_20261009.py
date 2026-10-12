"""Card battle puppets and World Boss positional-attack regressions."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g

def unit(pos, card=101, skill1=51, skill2=0):
    return dict(pos=pos, uid=0, card_id=card, hp=10000000, atk=100,
                def_=0, ability=100, skill1=skill1, skill2=skill2,
                is_leader=False, level=1, power=100, speed=0)

class CardBattlePuppetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old=g.store.DB_PATH
        self.addCleanup(lambda: g.store.set_db_path(old))
        g.store.set_db_path(str(Path(self.tmp.name)/'test.db'))
        g.store.init_db()
        self.addCleanup(g.store.close_thread_db)
        self.player,_=g.store.get_or_create_player(88009103)
        g._CLIENTDB_PATH=str(Path(__file__).resolve().parents[2]/'webroot'/'clientDB.txt')
        g.load_battle_cards()
        info=g.load_dummy_config()['zombies'][1000001]
        g.store.save_puppet(self.player['uin'],1000001,level=100,star=1,quality=100,
            active_skill=info['active'],active_level=1,passive_skill=info['passive'],passive_level=1)
        g.save_puppet_lineup(self.player,[1000001])

    def assert_puppet_report(self, report, enemy_type=1):
        left=g.parse_repeated_submessages(report,2)[0]
        right=g.parse_repeated_submessages(report,3)[0]
        self.assertEqual(g.parse_varint_fields(left)[1],38)
        self.assertEqual(g.parse_varint_fields(right)[1],enemy_type)
        self.assertEqual(len(g.parse_repeated_submessages(left,16)),1)
        self.assertFalse(g.parse_repeated_submessages(right,16))
        rounds=g.parse_repeated_submessages(report,4)
        self.assertEqual(g.parse_varint_fields(rounds[0])[1],0)
        acts=[a for r in rounds for a in g.parse_repeated_submessages(r,2)
              if g.parse_repeated_submessages(a,6)]
        self.assertTrue(acts)
        self.assertTrue(all(not g.parse_repeated_submessages(a,1) for a in acts))

    def test_shared_player_battle_and_report_include_real_puppet(self):
        left=[unit(3)];right=[unit(10,111)]
        won,_,_,rounds=g.run_battle(left,right,max_rounds=6,puppets=g._auto_puppets(self.player))
        self.assertTrue(any(a.get('puppet') for aa,_ in rounds for a in aa))
        self.assert_puppet_report(g.build_fight_report(self.player,0,0,0,won,
            left_units=left,right_units=right,replay_bytes=g.encode_fight_replay(rounds)))

    def test_worldboss_entry_includes_puppet_and_preserves_enemy_type(self):
        for bid in (998,999,1000):
            with self.subTest(boss=bid), patch.object(g,'build_player_units',return_value=[unit(7)]):
                messages=g._worldboss_fight(self.player,bid)
                response=next(body for name,body in messages if name.endswith('StartFight_SC'))
                report=g.parse_repeated_submessages(response,1)[0]
                self.assert_puppet_report(report,8)

    def test_activity_entry_includes_puppet(self):
        with patch.object(g,'build_player_units',return_value=[unit(3)]), \
             patch.object(g,'_activity_enemy_units',return_value=[unit(10,111)]), \
             patch.object(g,'apply_previewed_enemy_types',side_effect=lambda player,right,*args:right), \
             patch.object(g,'_activity_fuben_rewards',return_value=[]):
            messages=g.handle_activity_startfight(b'',self.player)
        response=next(body for name,body in messages if name.endswith('StartActivityFubenFight_SC'))
        self.assert_puppet_report(g.parse_repeated_submessages(response,1)[0])

    def test_unknown_plan_does_not_emit_phantom_puppet(self):
        with patch.object(g,'_BATTLECARD_IDS',{'101','111'}):
            left=[unit(3)];right=[unit(10,111)]
            won,_,_,rounds=g.run_battle(left,right,max_rounds=1,puppets=g._auto_puppets(self.player))
            self.assertFalse(any(a.get('puppet') for aa,_ in rounds for a in aa))
            report=g.build_fight_report(self.player,0,0,0,won,left_units=left,
                right_units=right,replay_bytes=g.encode_fight_replay(rounds))
            self.assertFalse(g.parse_repeated_submessages(g.parse_repeated_submessages(report,2)[0],16))

class WorldBossPlantActions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g._CLIENTDB_PATH=str(Path(__file__).resolve().parents[2]/'webroot'/'clientDB.txt')
        g.load_battle_cards();g.load_skill_info()

    def simulate(self, card, pos, bid, count=6):
        row=g._BATTLE_CARDS[card]
        left=[unit(p) for p in [q for q in g._FORMATION_POS if q!=pos][:count-1]]
        left.append(unit(pos,card,row['skill1'],row['skill2']))
        boss=g._worldboss_enemy(bid,{'hp':100000000})
        boss.update(atk=1,def_=0,skill1=51,skill2=0)
        rounds=g.simulate_battle(left,[boss],None,max_rounds=3,
             world_boss_id=bid,leader_sides=())
        return [a for aa,_ in rounds for a in aa if a['actor']==pos]

    def test_redstinger_every_slot_boss_and_team_size(self):
        for bid in (998,999,1000):
            for count in (2,6):
                for pos in g._FORMATION_POS:
                    with self.subTest(boss=bid,count=count,pos=pos):
                        actions=self.simulate(1181,pos,bid,count)
                        self.assertEqual(len(actions),3)
                        self.assertEqual(actions[0]['skill'],12371)
                        self.assertTrue(any(d['target']==10 for d in actions[0]['damages']))
                        self.assertEqual(actions[1]['skill'],12381)

    def test_all_plant_variants_have_turns_in_every_worldboss_slot(self):
        plants={cid:r for cid,r in g._BATTLE_CARDS.items() if r.get('type')==2 and r.get('tdId')}
        missing=[]
        for cid,row in plants.items():
            for pos in g._FORMATION_POS:
                for bid in (998,999,1000):
                    for count in (2,6):
                        actions=self.simulate(cid,pos,bid,count)
                        if not actions or actions[0]['skill']!=row['skill1'] or (len(actions)!=3 and '流放' not in g._SKILL_DESCS.get(row['skill2'],'')):
                            missing.append((cid,row['name'],pos,bid,count,len(actions)))
        print('Audited plant variants:',len(plants),'slots:',len(g._FORMATION_POS),'bosses: 3, team sizes: 2/6, missing:',len(missing))
        self.assertEqual(missing,[])

    def test_ordinary_row_attack_keeps_its_lane_rule(self):
        left=[unit(7,1181,12371,12381)]
        rounds=g.simulate_battle(left,[unit(10,111)],None,max_rounds=1,leader_sides=())
        self.assertFalse(any(a['actor']==7 for aa,_ in rounds for a in aa))

if __name__=='__main__':unittest.main()

