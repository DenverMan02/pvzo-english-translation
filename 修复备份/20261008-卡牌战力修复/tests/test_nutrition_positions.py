import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g
s=g.store
class NutritionPositionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old=s.DB_PATH;s.set_db_path(str(Path(self.tmp.name)/'test.db'));s.init_db();self.p,_=s.get_or_create_player(989991);self.p['level']=70;s.add_card(self.p['uin'],90001,101,1);self.uid,self.cid,self.lv,_=s.get_cards(self.p['uin'])[0]
 def tearDown(self):s.set_db_path(self.old);self.tmp.cleanup()
 def test_position_and_display(self):
  s.add_reagent(self.p['uin'],1,60101,position=3,slot_id=1)
  s.add_reagent(self.p['uin'],2,60201,position=7,slot_id=1)
  baseline=g.effective_card_stats(self.p,self.cid,self.lv,uid=self.uid)
  at3=g.effective_card_stats(self.p,self.cid,self.lv,uid=self.uid,position=3)
  at7=g.effective_card_stats(self.p,self.cid,self.lv,uid=self.uid,position=7)
  a=g._reagent_props(60101,1)[1]//100;b=g._reagent_props(60201,1)[1]//100
  self.assertEqual(at3['atk']-baseline['atk'],a);self.assertEqual(at7['atk']-baseline['atk'],b)
  r=s.get_reagents(self.p['uin'])[0];wire=g.parse_varint_fields(g._encode_reagent_dto(r));self.assertEqual(wire[7],a);self.assertEqual(wire[10],a)
  elements={k:v for k,v,_ in g._card_power_source_elements(self.p,self.uid,self.cid,self.lv,include_deployed=True,position=3)}
  self.assertEqual(elements[0],g.card_power(at3));self.assertEqual(elements[5],a)
  s.set_reagent_pos(self.p['uin'],1,9,1)
  self.assertEqual(g.effective_card_stats(self.p,self.cid,self.lv,uid=self.uid,position=3)['atk'],baseline['atk'])
 def test_hp_and_rate_power_match_stats(self):
  for bid in (60101,60102,60103,60104,60105,60106,60107,60108):
   bt,bv,_=g._reagent_props(bid,1);field=g._NUTRITION_STAT_FIELDS[bt]
   self.assertEqual(g._reagent_fight_power(bid,1),g.card_power({field:bv if bt>5 else bv//100}))
if __name__=='__main__':unittest.main()

