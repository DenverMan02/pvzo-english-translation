import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g
s=g.store
class NutritionRegression(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.old=s.DB_PATH
  s.set_db_path(str(Path(self.tmp.name)/'test.db'));s.init_db()
  self.p,_=s.get_or_create_player(987123)
  self.p['nutrition_bottle_exp']=1000;s.save_player(self.p['uin'],nutrition_bottle_exp=1000)
 def tearDown(self):
  s.set_db_path(self.old);self.tmp.cleanup()
 def test_smart_quality_and_replay(self):
  values=[60001,60101,60201,60301,60401,60501,60000]
  g._nutrition_save_temp(self.p,values)
  replies=g.handle_nutrition_changeall(b'',self.p)
  self.assertEqual(g._nutrition_temp_list(self.p),values[3:])
  self.assertEqual(self.p['nutrition_bottle_exp'],1065)
  self.assertEqual(g.parse_varint_fields(replies[0][1])[2],1065)
  self.assertEqual(replies[0][1].count(g.pb_varint_field(4,60301)),1)
  g.handle_nutrition_changeall(b'',self.p)
  self.assertEqual(self.p['nutrition_bottle_exp'],1065)
  reloaded,_=s.get_or_create_player(self.p['uin'])
  self.assertEqual(g._nutrition_temp_list(reloaded),values[3:])
 def test_individual_configuration(self):
  for bid,expected in [(60001,5),(60101,20),(60201,40),(60301,70),(60401,100),(60501,150)]:
   before=self.p['nutrition_bottle_exp'];g._nutrition_save_temp(self.p,[bid])
   req=g.pb_varint_field(1,bid)+g.pb_varint_field(2,0)
   g.handle_nutrition_change(req,self.p)
   self.assertEqual(self.p['nutrition_bottle_exp'],before+expected)
   g.handle_nutrition_change(req,self.p)
   self.assertEqual(self.p['nutrition_bottle_exp'],before+expected)
 def test_equipped_protection_and_level_value(self):
  s.add_reagent(self.p['uin'],1,60101,level=2,position=0,slot_id=0)
  s.add_reagent(self.p['uin'],2,60101,level=2,position=3,slot_id=1)
  g.handle_nutrition_extractexpall(b'',self.p)
  self.assertEqual(self.p['nutrition_bottle_exp'],1090)
  self.assertEqual([r['uid'] for r in s.get_reagents(self.p['uin'])],[2])
  g.handle_nutrition_extractexp(g.pb_int64_field(1,2),self.p)
  self.assertEqual(self.p['nutrition_bottle_exp'],1090)
  self.assertEqual(len(s.get_reagents(self.p['uin'])),1)
if __name__=='__main__':unittest.main()
