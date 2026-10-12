import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g


def unit(pos, **kw):
    result = dict(pos=pos, card_id=101, hp=10000, atk=100, def_=0, ability=0,
                  skill1=51, skill2=0, is_leader=False, level=1, power=100)
    result.update(kw)
    return result


class LiveCombatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g._CLIENTDB_PATH = str(Path(__file__).resolve().parents[2] / 'webroot/clientDB.txt')
        g.load_battle_cards()
        g.load_skill_info()

    def fight(self, left, right, **kw):
        state = {}
        replay = g.simulate_battle(left, right, kw.pop('won', True), result_state=state,
                                  max_rounds=kw.pop('max_rounds', 6), **kw)
        return replay, state

    def events(self, replay):
        return [d for aa, bb in replay for d in [d for a in aa for d in a['damages']] + bb]

    def test_power_and_requested_outcome_cannot_protect_or_kill(self):
        left=[unit(3, hp=10, atk=1, power=10**12)]
        right=[unit(4, hp=100, atk=20, power=1)]
        a, sa = self.fight(copy.deepcopy(left), copy.deepcopy(right), won=True)
        b, sb = self.fight(copy.deepcopy(left), copy.deepcopy(right), won=False)
        self.assertEqual(a, b)
        self.assertEqual(sa, sb)
        self.assertFalse(sa['won'])
        self.assertEqual(sa['hp'][3], 0)
        self.assertFalse(g.run_battle(left, right)[0])

    def test_timeout_preserves_survivors(self):
        replay,state=self.fight([unit(3)],[unit(4)],max_rounds=2)
        self.assertEqual(len(replay),2)
        self.assertEqual(state['hp'],{3:9800,4:9800})
        self.assertTrue(state['timed_out'])
        self.assertFalse(state['won'])

    def test_shield_absorbs_then_spills_exactly(self):
        replay,state=self.fight([unit(3,shield=150)],[unit(4)],max_rounds=2)
        hits=[a['damages'][0] for aa,_ in replay for a in aa if a['actor']==4]
        self.assertEqual([(d['dmg'],d['change_shield']) for d in hits],[(0,-100),(50,-50)])
        self.assertEqual(state['hp'][3],9950)

    def test_defense_and_ability_affect_damage(self):
        _, a=self.fight([unit(3)],[unit(4)],max_rounds=1)
        _, b=self.fight([unit(3)],[unit(4,def_=100)],max_rounds=1)
        self.assertGreater(b['hp'][4],a['hp'][4])
        _, c=self.fight([unit(3,skill1=12431,ability=200)],[unit(4)],max_rounds=1)
        self.assertLess(c['hp'][4],a['hp'][4])

    def test_healing_is_capped_and_scales_with_healer(self):
        low,_=self.fight([unit(3,skill1=61,initial_hp=1000)],[unit(4)],max_rounds=1)
        high,state=self.fight([unit(3,skill1=61,atk=200,ability=50,initial_hp=1000)],[unit(4)],max_rounds=1)
        self.assertEqual(low[0][0][0]['damages'][0]['dmg'],-100)
        self.assertEqual(high[0][0][0]['damages'][0]['dmg'],-250)
        full,_=self.fight([unit(3,skill1=61)],[unit(4)],max_rounds=1)
        self.assertEqual(full[0][0][0]['damages'][0]['dmg'],0)

    def test_late_freeze_consumes_next_target_turn(self):
        replay,_=self.fight([unit(3)],[unit(4,skill1=3401)],max_rounds=3)
        self.assertTrue(any(a['actor']==3 for a in replay[0][0]))
        self.assertFalse(any(a['actor']==3 and not a.get('update') for a in replay[1][0]))

    def test_silence_prevents_cast_for_full_two_turns(self):
        replay,_=self.fight([unit(3,skill1=7201)],[unit(4,skill2=1)],max_rounds=3)
        self.assertFalse(any(a['cast'] for aa,_ in replay for a in aa if a['actor']==4))
        effect=g._SKILL_EFFECTS[7201]
        self.assertEqual(effect['turns'],2)
        self.assertEqual(g._SKILL_EFFECTS[12501]['chance'],10)

    def test_bleed_and_regeneration_change_hp(self):
        replay,state=self.fight([unit(3,skill1=2201)],[unit(4)],max_rounds=1)
        self.assertTrue(any(d['dmg']==1000 and d['target']==4 for _,bb in replay for d in bb))
        self.assertEqual(state['hp'][4],8900)
        replay,_=self.fight([unit(3,skill1=9301,initial_hp=1000)],[unit(4)],max_rounds=1)
        self.assertTrue(any(d['dmg']<0 for _,bb in replay for d in bb))

    def test_bleed_cannot_desync_flash_shield(self):
        replay,state=self.fight([unit(3,skill1=2201)],[unit(4,shield=10000)],max_rounds=1)
        self.assertEqual(state['hp'][4],10000)
        self.assertEqual(state['shield'][4],8900)

    def test_shield_skill_is_support_and_self_shield_targets_self(self):
        self.assertEqual(g._SKILL_INFO[12331],('support',2))
        replay,_=self.fight([unit(3),unit(9,skill1=4001)],[unit(4)],max_rounds=1)
        action=next(a for a in replay[0][0] if a['actor']==9)
        self.assertEqual(action['damages'][0]['target'],9)
        self.assertEqual(action['damages'][0]['change_shield'],5000)
        replay,_=self.fight([unit(3,skill1=12331),unit(9)],[unit(4)],max_rounds=1)
        self.assertTrue(any(d.get('change_shield',0)>0 for d in self.events(replay)))
        self.assertTrue(any(g.load_battle_states().get(d.get('buff_id'),(0,))[0]==9 for d in self.events(replay)))

    def test_multi_effect_skill_applies_bleed_and_self_damage_bonus(self):
        replay,_=self.fight([unit(3,skill1=12381)],[unit(4)],max_rounds=2)
        hits=[a['damages'][0]['dmg'] for aa,_ in replay for a in aa if a['actor']==3]
        self.assertGreater(hits[1],hits[0])
        events=self.events(replay)
        self.assertTrue(any(d.get('buff_id') and d['target']==3 for d in events))
        self.assertTrue(any(d.get('buff_id') and d['target']==4 for d in events))

    def test_puppet_damage_respects_shields(self):
        replay,state=self.fight([unit(3)],[unit(4,shield=10000)],max_rounds=1,
                               puppets={1:{'actor':101,'skill':0}})
        self.assertEqual(state['hp'][4],10000)
        self.assertTrue(any(d.get('change_shield',0)<0 for d in self.events(replay)))

    def test_initiative_changes_first_actor(self):
        replay,_=self.fight([unit(3)],[unit(4,speed=10)],max_rounds=1)
        self.assertEqual(replay[0][0][0]['actor'],4)

    def test_healing_buff_and_weakness_change_actual_heal(self):
        replay,_=self.fight([unit(3,skill1=8401),unit(9,skill1=61,initial_hp=5000)],
                           [unit(4)],max_rounds=1)
        heal=next(a for a in replay[0][0] if a['actor']==9)['damages'][0]['dmg']
        self.assertEqual(heal,-110)
        replay,_=self.fight([unit(3,skill1=61,initial_hp=5000)],
                           [unit(4,skill1=12201,speed=10)],max_rounds=1)
        heal=next(a for a in replay[0][0] if a['actor']==3)['damages'][0]['dmg']
        self.assertEqual(heal,-90)

    def test_lifesteal_works_on_the_applying_hit(self):
        replay,_=self.fight([unit(3,skill1=3801,initial_hp=5000)],
                           [unit(4)],max_rounds=1)
        self.assertTrue(any(d['target']==3 and d['dmg']==-50 for d in replay[0][0][0]['damages']))

    def test_max_hp_wire_does_not_double_native_hp_update(self):
        for cap, dmg, expected in ((1000,-1100,100),(-1000,800,0),(0,-100,100)):
            event=dict(target=3,dmg=dmg,dead=False,change_max_hp=cap)
            wire=g.encode_fight_replay([([dict(actor=3,skill=51,damages=[event])],[])])
            rr=g.parse_repeated_submessages(wire,4)[0]
            aa=g.parse_repeated_submessages(rr,2)[0]
            dd=g.parse_varint_fields(g.parse_repeated_submessages(aa,2)[0])
            self.assertEqual(dd[2],expected)

    def test_shield_and_healing_change_match_result(self):
        self.assertFalse(g.run_battle([unit(3,hp=250,atk=100)],[unit(4,hp=300,atk=150)])[0])
        self.assertTrue(g.run_battle([unit(3,hp=250,atk=100,shield=300)],[unit(4,hp=300,atk=150)])[0])

    def test_reflect_can_kill_actor(self):
        replay,state=self.fight([unit(3,skill1=12455,atk=0,hp=10000)],
                               [unit(4,atk=100,hp=30)],max_rounds=1)
        self.assertEqual(state['hp'][4],0)
        self.assertTrue(state['won'])

    def test_expired_report_is_not_an_unrelated_fabricated_battle(self):
        with patch.object(g,'_BATTLE_REPORTS',{}),patch.object(g,'run_battle') as run:
            messages=g.handle_battle_queryreport(g.pb_varint_field(1,123),{'uin':1})
            run.assert_not_called()
            self.assertNotEqual(messages[0][0],'PVZ.Cmd.Cmd_Battle_QueryReport_SC')

    def test_arena_score_and_report_follow_same_actual_match(self):
        from contextlib import ExitStack, nullcontext
        data=dict(challenges=0,score=100,wins=0,losses=0)
        left=[unit(3,hp=10,atk=1,power=10**9)]
        right=[unit(4,hp=100,atk=20,power=1)]
        with ExitStack() as stack:
            stack.enter_context(patch.object(g.store, 'atomic_player', return_value=nullcontext()))
            stack.enter_context(patch.object(g, '_daily_quest_touch'))
            stack.enter_context(patch.object(g,'_arena_state',return_value=data))
            stack.enter_context(patch.object(g.bots_local,'pick_opponent',return_value={'uin':900001,'power':1}))
            stack.enter_context(patch.object(g.bots_local,'bots',return_value=[{'uin':900001,'name':'test'}]))
            stack.enter_context(patch.object(g,'build_player_units',return_value=left))
            stack.enter_context(patch.object(g,'_arena_opponent_units',return_value=right))
            regenerate=stack.enter_context(patch.object(g.bots_local,'regenerate'))
            save=stack.enter_context(patch.object(g,'_save_json_state'))
            result=stack.enter_context(patch.object(g.bots_local,'apply_match_result'))
            report=stack.enter_context(patch.object(g,'build_fight_report',return_value=b'report'))
            stack.enter_context(patch.object(g,'_store_battle_report',return_value=123))
            stack.enter_context(patch.object(g,'_arena_recorder',return_value=b'record'))
            stack.enter_context(patch.object(g,'_arena_rank',return_value=1))
            g._arena_challenge({'uin':1},'arena_data','test_SC')
            regenerate.assert_called_once_with([900001])
            self.assertEqual(data['score'],92)
            self.assertEqual(data['losses'],1)
            result.assert_called_once_with(900001,False)
            self.assertFalse(report.call_args.args[4])
            self.assertEqual(report.call_args.args[7],left)
            reported_enemy=report.call_args.args[8]
            self.assertEqual(len(reported_enemy),len(right))
            self.assertTrue(all(all(enemy[k]==value for k,value in original.items())
                                for enemy,original in zip(reported_enemy,right)))
            self.assertEqual(reported_enemy[0]['final_hp'],99)
            self.assertNotIn('final_hp',right[0])  # encounter snapshots must remain pristine

    def test_all_report_builders_use_live_engine(self):
        import ast
        tree=ast.parse(Path(g.__file__).read_text(encoding='utf-8'))
        checked=[]
        for f in tree.body:
            if not isinstance(f,ast.FunctionDef):continue
            names=[n.func.id for n in ast.walk(f) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
            if 'build_fight_report' in names:
                self.assertTrue('run_battle' in names or 'simulate_battle' in names,f.name)
                self.assertNotIn('resolve_battle',names)
                checked.append(f.name)
        self.assertGreaterEqual(len(checked),13)

    def test_all_762_skill_replays_conserve_hp_and_shield(self):
        for sid in g._SKILL_INFO:
            with self.subTest(skill=sid):
                left=[unit(3,skill1=sid,initial_hp=6000,shield=400,ability=50),unit(9,initial_hp=8000)]
                right=[unit(4,shield=700),unit(10)]
                hp={u['pos']:u.get('initial_hp',u['hp']) for u in left+right}
                sh={u['pos']:u.get('shield',0) for u in left+right}
                replay,state=self.fight(left,right,max_rounds=5)
                for d in self.events(replay):
                    t=d['target']; hp[t]-=d['dmg'];sh[t]+=d.get('change_shield',0)
                    self.assertGreaterEqual(hp[t],0)
                    self.assertGreaterEqual(sh[t],0)
                    # Original Flash clears all shield whenever HP is damaged.
                    if d['dmg']>0:
                        self.assertEqual(sh[t],0)
                self.assertEqual(hp,state['hp'])
                self.assertEqual(sh,state['shield'])
                wire=g.encode_fight_replay(replay)
                native_hp={u['pos']:u.get('initial_hp',u['hp']) for u in left+right}
                native_max={u['pos']:u['hp'] for u in left+right}
                native_sh={u['pos']:u.get('shield',0) for u in left+right}
                def signed(n): return n-(1<<64) if n >= 1<<63 else n
                for rr in g.parse_repeated_submessages(wire,4):
                    dd=[d for aa in g.parse_repeated_submessages(rr,2)
                        for d in g.parse_repeated_submessages(aa,2)]
                    dd+=g.parse_repeated_submessages(rr,3)
                    for body in dd:
                        e=g.parse_varint_fields(body); t=e[1]
                        cap=signed(e.get(11,0));delta=signed(e.get(2,0))
                        native_max[t]+=cap
                        native_hp[t]=min(native_max[t],native_hp[t]+max(0,cap))
                        native_hp[t]=min(native_max[t],max(0,native_hp[t]+delta))
                        native_sh[t]=0 if delta<0 else max(0,native_sh[t]+signed(e.get(6,0)))
                self.assertEqual(native_hp,state['hp'])
                self.assertEqual(native_sh,state['shield'])


if __name__=='__main__':
    unittest.main()
