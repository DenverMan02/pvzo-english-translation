"""Regression coverage for the October 4 gameplay issue report."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g
from test_live_combat import unit

class SkillDescriptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.load_skill_info();g.load_battle_cards()
    def actions(self, sid, allies=None, enemies=None, rounds=1, casting=False):
        allies=allies or [unit(p,initial_hp=1000+i*700) for i,p in enumerate((3,7,9,11,13,15))]
        allies[0].update(skill1=61 if casting else sid, skill2=sid if casting else 0)
        enemies=enemies or [unit(p,atk=1) for p in (4,8,10,12,14,16,18)]
        replay=g.simulate_battle(allies,enemies,False,max_rounds=rounds)
        return [a for aa,bb in replay for a in aa if a['actor']==allies[0]['pos'] and a['skill']==sid]
    def test_oxygen_all_five_tiers_heal_three_and_regenerate(self):
        for sid in range(9301,9306):
            a=self.actions(sid,rounds=2,casting=True)[0]
            self.assertTrue(a['cast'])
            self.assertEqual(len(a['damages']),3)
            self.assertTrue(all(d['dmg']<0 and d.get('buff_left_round')==2 for d in a['damages']))
            self.assertEqual({d['target'] for d in a['damages']},{3,7,9})
    def test_moonflower_all_five_tiers_heal_every_ally_with_damage_buff(self):
        for sid in range(12461,12466):
            a=self.actions(sid,rounds=2,casting=True)[0]
            self.assertEqual({d['target'] for d in a['damages']},{3,7,9,11,13,15})
            self.assertTrue(all(d['dmg']<0 and d.get('buff_id') for d in a['damages']))
            self.assertTrue(g._SKILL_EFFECTS[sid]['cleanse'])
    def test_prioritize_captain(self):
        for sid in (8601,9901):
            a=self.actions(sid,enemies=[unit(4),unit(18,is_leader=True)])[0]
            self.assertEqual(next(d['target'] for d in a['damages'] if d['dmg']>0),18)
    def test_lowest_hp_is_guaranteed_critical_below_half(self):
        for sid in (8501,8811):
            a=self.actions(sid,enemies=[unit(4),unit(18,initial_hp=1000)])[0]
            d=next(d for d in a['damages'] if d['dmg']>0)
            self.assertEqual((d['target'],d['damage_type']),(18,2))
    def test_flat_dodge_bonus_is_configured_with_exact_value(self):
        for sid in range(901,906):
            expected=int(__import__('re').search(r"提高(\d+)闪避",g._SKILL_DESCS[sid])[1])
            self.assertTrue(any(e.get('param')==(1,14,0,expected) for e in g._SKILL_EFFECTS[sid]['effects']))
    def test_self_heal_targets_only_caster(self):
        a=self.actions(121)[0]
        self.assertEqual([d['target'] for d in a['damages']],[3])
        self.assertLess(a['damages'][0]['dmg'],0)
    def test_repeated_random_projectiles_keep_hit_count(self):
        for sid,count in ((4701,3),(5601,3),(12101,2)):
            a=self.actions(sid,enemies=[unit(10)])[0]
            self.assertEqual(len([d for d in a['damages'] if d['dmg']>0]),count)
    def test_one_hit_buff_does_not_persist_after_attack(self):
        self.assertEqual(g._SKILL_EFFECTS[12481]['effects'],[])
    def test_splash_does_half_damage(self):
        a=self.actions(7701,enemies=[unit(4),unit(10),unit(12)])[0]
        hits=[d['dmg'] for d in a['damages'] if d['dmg']>0]
        self.assertGreater(len(hits),1)
        self.assertEqual(hits[1],hits[0]//2)

class StorageAndWireTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        old=g.store.DB_PATH;g.store.set_db_path(str(Path(self.tmp.name)/'test.db'))
        self.addCleanup(lambda:g.store.set_db_path(old));g.store.init_db()
        self.player,_=g.store.get_or_create_player(88001)
        self.uid=10001001;g.store.add_card(88001,self.uid,1001,300,0)
    def test_visual_fields_survive_both_sides_and_result(self):
        visuals=dict(gene_id=5,armor_type=2,armor_star=4,forge_level=9,quality=6,rank=3)
        u=unit(3,**visuals)
        before,after=g.encode_left_card_lists(self.player,[u],0)
        for body,field in ((before,2),(after,4),(g.encode_before_card_list([u]),2)):
            f=g.parse_varint_fields(g.parse_repeated_submessages(body,field)[0])
            self.assertEqual([f[x] for x in (21,22,23,13,19,20)],[5,2,4,9,6,3])
    def test_owned_visuals_are_read_from_saved_card(self):
        g.store.set_card_gene(88001,self.uid,5,1,0,1,1)
        g.store.set_card_armor(88001,self.uid,2,4,0,1,0)
        g.store.set_card_armor_type(88001,self.uid,2)
        u=g.build_player_units(self.player,{3:self.uid})[0]
        self.assertEqual((u['gene_id'],u['armor_type'],u['armor_star']),(5,2,4))
    def test_last_puzzle_cell_and_duplicate_batch(self):
        g.store.set_card_fill_quality(88001,self.uid,3)
        board=g.load_card_puzzle()[3];last=len(board['cost'])-1
        for i in range(last):g.store.add_card_filled_index(88001,self.uid,i)
        iid,n=board['cost'][last];g.store.add_item(88001,iid,n)
        req=g.pb_int64_field(1,self.uid)+g.pb_varint_field(2,last)*2
        msg=dict(g.handle_card_batchfill(req,self.player))
        self.assertEqual(g.parse_repeated_varint(msg['PVZ.Cmd.Cmd_Card_BatchFill_SC'],2),[last])
        self.assertEqual(g.store.get_item_count(88001,iid),0)
        self.assertTrue(g.handle_card_finishfill(g.pb_int64_field(1,self.uid),self.player))
        self.assertEqual(g.store.get_card_fill_quality(88001,self.uid),4)
        self.assertFalse(g.handle_card_finishfill(g.pb_int64_field(1,self.uid),self.player))
    def test_missing_piece_cannot_fill(self):
        req=g.pb_int64_field(1,self.uid)+g.pb_varint_field(2,0)
        self.assertFalse(g.handle_card_batchfill(req,self.player))
        self.assertEqual(g.store.get_card_filled_indices(88001,self.uid),[])
    def test_kill_rewards_and_join_rewards_are_separate_and_once_only(self):
        for bid in g._WORLD_BOSS_IDS:
            with patch.object(g,'build_player_units',return_value=[unit(3,atk=10**10)]),patch.object(g,'_worldboss_placements',return_value={3:self.uid}):
                messages=g._worldboss_fight(self.player,bid)
                self.assertTrue(messages)
                self.assertFalse(g._worldboss_fight(self.player,bid))
            expected=g.parse_bonus_items(g._worldboss_config(bid)['killAward'])
            report=g.parse_repeated_submessages(dict(messages)['PVZ.Cmd.Cmd_WorldBoss_StartFight_SC'],1)[0]
            side=g.parse_repeated_submessages(report,2)[0]
            reward=g.parse_repeated_submessages(side,3)[0]
            self.assertEqual(len(g.parse_repeated_submessages(reward,4)),len(expected))
        self.assertEqual(g.store.get_item_count(88001,3447),2)
        before=self.player['coin']
        self.assertTrue(g.handle_worldboss_getjoinreward(b'',self.player))
        self.assertEqual(self.player['coin'],before+30000)
        self.assertEqual(g.store.get_item_count(88001,3447),3)
        self.assertFalse(g.handle_worldboss_getjoinreward(b'',self.player))
        self.assertFalse(g.store.claim_worldboss_reward(88001,self.player['worldboss_day'],'kill:999:1',[(3447,1)],self.player['worldboss_state']))
        self.assertEqual(g.store.get_item_count(88001,3447),3)
    def test_all_twenty_puppet_mappings_are_present(self):
        rows=g._read_clientdb_table('dummyZombieInfo.txt');ci={v:i for i,v in enumerate(rows[0])}
        own=[r for r in rows[1:] if r[ci['isEnemy']]=='0']
        self.assertEqual(len(own),20)
        self.assertTrue(all(r[ci['id']] in g.battlecard_ids() for r in own))

if __name__=='__main__':unittest.main()
