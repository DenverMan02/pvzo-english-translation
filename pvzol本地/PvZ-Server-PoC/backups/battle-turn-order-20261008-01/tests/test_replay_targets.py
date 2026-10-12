"""Match Flash PlanFactory's attack/update split for secondary effects."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g


def unit(pos, **kw):
    data = dict(pos=pos, card_id=101, hp=10000, atk=100, def_=0, ability=0,
                skill1=51, skill2=0, is_leader=False, level=1, power=100)
    data.update(kw)
    return data


class ReplayTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.load_battle_cards()
        g.load_skill_info()

    def first_action(self, skill, **kw):
        rounds = g.simulate_battle([unit(3,skill1=skill,**kw),unit(7)], [unit(4)], None, max_rounds=1)
        return rounds[0][0][0]

    def wire(self, action):
        encoded = g.encode_fight_replay([([action],[])])
        round_ = g.parse_repeated_submessages(encoded,4)[0]
        a = g.parse_repeated_submessages(round_,2)[0]
        return g.parse_varint_fields(g.parse_repeated_submessages(a,1)[0]), [g.parse_varint_fields(d) for d in g.parse_repeated_submessages(a,2)]

    def test_lifesteal_heals_self_without_aiming_at_self(self):
        action = self.first_action(3801,initial_hp=5000)
        actor, damage = self.wire(action)
        self.assertEqual(actor[2],4)
        self.assertTrue(any(d[1]==3 and d[2]==50 and d[3]==4 for d in damage))
        self.assertEqual({d[1] for d in damage if d[3]!=4},{4})

    def test_reflection_keeps_real_damage_without_self_attack_animation(self):
        rounds = g.simulate_battle([unit(3,skill1=12455,atk=0)], [unit(4,atk=100,hp=30)],None,max_rounds=1)
        action = next(a for aa,_ in rounds for a in aa if a['actor']==4)
        _, damage = self.wire(action)
        self.assertTrue(any(d[1]==4 and d[3]==4 and d[7]==1 for d in damage))
        self.assertEqual({d[1] for d in damage if d[3]!=4},{3})

    def test_auxiliary_first_does_not_change_primary_target(self):
        action = dict(actor=3,skill=51,damages=[dict(target=3,dmg=-5,dead=False,damage_type=4),dict(target=4,dmg=20,dead=False)])
        actor, damage = self.wire(action)
        self.assertEqual(actor[2],4)
        self.assertEqual(len(damage),2)

    def test_primary_healing_keeps_its_original_animation(self):
        actor, damage = self.wire(self.first_action(61,initial_hp=5000))
        self.assertEqual(actor[2],3)
        self.assertEqual(damage[0][3],1)
        self.assertGreater(damage[0][2],0)

    def test_all_offensive_skill_auxiliaries_are_not_attack_targets(self):
        checked = 0
        for sid, (kind, _) in g._SKILL_INFO.items():
            if kind not in ('attack','aoe') or not g._SKILL_EFFECTS.get(sid):
                continue
            action = self.first_action(sid,initial_hp=5000)
            actor, damage = self.wire(action)
            self.assertEqual(actor[2],4, sid)
            self.assertTrue(all(d[1]==4 for d in damage if d[3]!=4),sid)
            checked += 1
        self.assertGreater(checked,20)

if __name__=='__main__':
    unittest.main()
