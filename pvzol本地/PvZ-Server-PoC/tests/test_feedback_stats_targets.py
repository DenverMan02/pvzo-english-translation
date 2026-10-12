import sys,unittest,tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g
class FeedbackChecks(unittest.TestCase):
 def test_armor_matches_client_centistat_display(self):
  g.load_battle_cards();table=g.load_plant_armor();cid=1101
  fruit=g._BATTLE_CARDS[cid]['fruitType'];key=fruit*1000+100
  row=table['star_prop'][key]
  with patch.object(g.store,'get_card_armors',return_value={1:dict(star=0,substar=0,level=300,gems={})}):
   bonus=g._armor_bonus(42,90001,cid,growth={})
  for field in ('atk','def_','hp','ability'):
   self.assertEqual(bonus[field],(row[field]+row['grow_'+field]*300)//100)
 def test_giant_all_tiers_both_sides_only_attack_opponents(self):
  g.load_battle_cards();g.load_skill_info()
  def unit(p):return dict(pos=p,card_id=101,hp=10000000,atk=30,skill1=51,skill2=0)
  for sid in range(6801,6806):
   for actor in (3,4):
    left=[unit(p) for p in (3,7,9,11,13,15,17)]
    right=[unit(p) for p in (4,8,10,12,14,16,18)]
    team=left if actor==3 else right;team[0].update(card_id=580,skill2=sid)
    opponent={u['pos'] for u in (right if actor==3 else left)}
    rounds=g.simulate_battle(left,right,None,max_rounds=4)
    actions=[a for aa,_ in rounds for a in aa if a['actor']==actor and a['skill']==sid]
    self.assertTrue(actions,(sid,actor))
    for a in actions:
     self.assertTrue({d['target'] for d in a['damages'] if d['dmg']>0}<=opponent)
     replay=g.encode_fight_replay([([a],[])])
     rnd=g.parse_repeated_submessages(replay,4)[0]
     wa=g.parse_repeated_submessages(rnd,2)[0]
     target=g.parse_varint_fields(g.parse_repeated_submessages(wa,1)[0])[2]
     self.assertIn(target,opponent)
if __name__=='__main__':unittest.main()
