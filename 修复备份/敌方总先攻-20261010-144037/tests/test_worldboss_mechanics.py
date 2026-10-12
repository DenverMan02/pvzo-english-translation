"""World Boss mechanics and original Flash replay wire-contract regressions."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as game

def signed(value):
    return value - (1 << 64) if value >= (1 << 63) else value

class WorldBossTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        game._CLIENTDB_PATH = str(Path(__file__).resolve().parents[2] / 'webroot' / 'clientDB.txt')
        game.load_battle_cards()
        game.load_skill_info()

    def units(self, hp=1_000_000, atk=1000):
        return [dict(pos=p, card_id=101, hp=hp, atk=atk, def_=0,
                     skill1=51, skill2=0, level=1, power=1000, is_leader=False)
                for p in (3,7,9,11,13,15)]

    def simulate(self, bid, hp=1_000_000, atk=1000, boss_hp=None, count=30):
        left = self.units(hp, atk)
        right = [game._worldboss_enemy(bid, {'hp': boss_hp or game._WORLD_BOSS_HP[bid]})] + game._worldboss_summons(bid)
        state = {}
        replay = game.simulate_battle(left, right, False, max_rounds=count,
                    leader_sides=('left',), stop_on_elimination=True,
                    world_boss_id=bid, result_state=state)
        return left, right, replay, state

    def casts(self, replay):
        return [a for actions,_ in replay for a in actions if a['actor']==10 and a.get('cast')]

    def test_boss_and_both_previews_use_center(self):
        player={'uin':1}
        for bid in (998,999,1000):
            self.assertEqual(game._worldboss_enemy(bid,{'hp':123})['pos'],10)
            with patch.object(game,'_worldboss_state',return_value={'bosses':{}}), patch.object(game,'_worldboss_placements',return_value={}), patch.object(game,'build_formation_info',return_value=b''):
                body=game.handle_worldboss_fightinfo(game.pb_varint_field(1,bid),player)
                self.assertEqual(game.parse_varint_fields(game.parse_repeated_submessages(body,2)[0])[1],10)
                target=game.pb_varint_field(1,8)+game.pb_varint_field(11,bid)
                body=game.handle_battle_getfightinfo(game.pb_bytes(1,target),player)
                self.assertEqual(game.parse_varint_fields(game.parse_repeated_submessages(body,4)[0])[1],10)

    def test_egypt_uses_missiles_for_every_live_target(self):
        left,right,replay,state=self.simulate(999,count=6)
        casts=self.casts(replay)
        self.assertEqual(len(casts),2)
        for action in casts:
            self.assertEqual(action['skill'],1)
            self.assertEqual({d['target'] for d in action['damages']},{u['pos'] for u in left})
            self.assertTrue(all(d['dmg']>0 and not d['dead'] for d in action['damages']))
        self.assertTrue(all(state['hp'][u['pos']]>0 for u in left))

    def test_round_limit_never_forces_survivors_to_die(self):
        for bid in (998,999,1000):
            left,right,replay,state=self.simulate(bid,hp=10_000_000)
            self.assertEqual(len(replay),30)
            self.assertTrue(all(state['hp'][u['pos']]>0 for u in left))
            self.assertFalse(any(d['dead'] and d['target']%2 for aa,bb in replay for d in [d for a in aa for d in a['damages']]+bb))

    def test_qin_summons_four_hidden_distinct_units_and_heals(self):
        left,right,replay,state=self.simulate(998,count=6)
        self.assertEqual(len(right),5)
        self.assertEqual(len({u['pos'] for u in right}),5)
        self.assertEqual(len({u['card_id'] for u in right[1:]}),4)
        cards=[game.parse_varint_fields(c) for c in game.parse_repeated_submessages(game.encode_before_card_list(right),2)]
        for c in cards[1:]:
            self.assertEqual(c[3],0)
            self.assertEqual(c[17],0)
            self.assertGreater(c[6],0)
        self.assertEqual({a['actor'] for a in replay[0][0]}, {u['pos'] for u in left}|{10})
        casts=self.casts(replay)
        self.assertEqual(len(casts),2)
        for a in casts:
            self.assertEqual(a['skill'],2)
            self.assertEqual(len([d for d in a['damages'] if d.get('is_relive')]),4)
            self.assertLessEqual(next(d['dmg'] for d in a['damages'] if d['target']==10),0)
            self.assertTrue(all(d['target']%2==0 for d in a['damages']))
        # Summoned units can act starting on the following round, not before reveal.
        self.assertTrue(any(a['actor'] in (4,8,12,16) for a in replay[2][0]))
        wire=game.encode_fight_replay(replay)
        summons=[]
        for r in game.parse_repeated_submessages(wire,4):
            for a in game.parse_repeated_submessages(r,2):
                summons += [game.parse_varint_fields(d) for d in game.parse_repeated_submessages(a,2) if game.parse_varint_fields(d).get(10)]
        self.assertEqual(len(summons),8)

    def test_qin_healing_and_all_replay_deltas_match_simulation(self):
        left,right,replay,state=self.simulate(998)
        hp={u['pos']:u.get('initial_hp',u['hp']) for u in left+right}
        for aa,bb in replay:
            for d in [d for a in aa for d in a['damages']]+bb:
                hp[d['target']]-=d['dmg']
                self.assertGreaterEqual(hp[d['target']],0)
        self.assertEqual(hp,state['hp'])
        self.assertLessEqual(hp[10],game._WORLD_BOSS_HP[998])

    def test_swordsman_keeps_own_single_target_skill(self):
        _,right,replay,_=self.simulate(1000,count=6)
        self.assertEqual(len(right),1)
        for a in self.casts(replay):
            self.assertEqual(a['skill'],3)
            self.assertEqual(len(a['damages']),1)
            self.assertEqual(a['damages'][0]['target']%2,1)

    def test_real_deaths_stop_the_battle_without_forced_actions(self):
        left,right,replay,state=self.simulate(999,hp=100,count=30)
        self.assertTrue(all(state['hp'][u['pos']]==0 for u in left))
        self.assertLess(len(replay),30)
        left,right,replay,state=self.simulate(998,boss_hp=100,atk=1000)
        self.assertEqual(state['hp'][10],0)
        self.assertEqual(len(replay),1)
        self.assertEqual(len(replay[0][0]),1)
        self.assertFalse(self.casts(replay))

    def test_final_damage_and_saved_hp_come_from_same_replay(self):
        player={'uin':1,'role_id':1,'level':100,'name':'test','face_id':1}
        for bid in (998,999,1000):
            state={'bosses':{},'inspire_time':0,'claimed':False}
            with patch.object(game,'_worldboss_state',return_value=state), patch.object(game,'_worldboss_save'), patch.object(game,'_worldboss_placements',return_value={}), patch.object(game,'build_player_units',side_effect=lambda *a:self.units()), patch.object(game,'player_face_url',return_value=''), patch.object(game,'_worldboss_bossinfo_body',return_value=b''), patch.object(game,'_worldboss_roleinfo_body',return_value=b''):
                messages=game._worldboss_fight(player,bid)
            body=dict(messages)['PVZ.Cmd.Cmd_WorldBoss_StartFight_SC']
            report=game.parse_repeated_submessages(body,1)[0]
            gross=0;remaining=game._WORLD_BOSS_HP[bid]
            for r in game.parse_repeated_submessages(report,4):
                damages=game.parse_repeated_submessages(r,3)
                for a in game.parse_repeated_submessages(r,2):
                    damages+=game.parse_repeated_submessages(a,2)
                for d in damages:
                    fields=game.parse_varint_fields(d)
                    if fields[1]==10:
                        delta=signed(fields.get(2,0));remaining+=delta;gross+=max(0,-delta)
            self.assertEqual(game.parse_varint_fields(body)[2],gross)
            self.assertEqual(state['bosses'][str(bid)]['hp'],remaining)
            self.assertEqual(state['bosses'][str(bid)]['damage'],gross)

if __name__=='__main__':
    unittest.main()

