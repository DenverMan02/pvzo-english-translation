import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g
s=g.store
class TalentRegression(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old=s.DB_PATH
  s.set_db_path(str(Path(self.tmp.name)/'test.db'));s.init_db()
  self.p,_=s.get_or_create_player(989444);self.p['level']=10;s.save_player(self.p['uin'],level=10)
  s.unlock_plant(self.p['uin'],1100)
 def tearDown(self):s.set_db_path(self.old);self.tmp.cleanup()
 def test_power_boundary(self):
  info=g.load_plant_talents()['talent'][1101]
  for power,expected in [(info['power']-1,False),(info['power'],True)]:
   with patch.object(g,'plant_card_power',return_value=power),patch.object(g,'_quest_observe_talent_progress',return_value=[]):
    g.handle_talent_study(g.pb_varint_field(1,1101),self.p)
    self.assertEqual(1101 in s.get_talents(self.p['uin']),expected)
 def test_locked_plant_and_task_do_not_bypass(self):
  s.remove_plant(self.p['uin'],1100)
  with patch.object(g,'plant_card_power',return_value=999999):
   g.handle_talent_study(g.pb_varint_field(1,1101),self.p)
   self.assertNotIn(1101,s.get_talents(self.p['uin']))
   s.unlock_plant(self.p['uin'],1100);self.p['level']=1;s.save_player(self.p['uin'],level=1)
   g.handle_talent_study(g.pb_varint_field(1,1101),self.p)
   self.assertNotIn(1101,s.get_talents(self.p['uin']))
 def test_parent_cannot_be_skipped(self):
  with patch.object(g,'plant_card_power',return_value=999999):
   g.handle_talent_study(g.pb_varint_field(1,1102),self.p)
  self.assertNotIn(1102,s.get_talents(self.p['uin']))
 def test_unknown_condition_not_auto_approved(self):
  self.assertFalse(g._eval_talent_condition(self.p,'999::1'))
  self.assertFalse(g._eval_talent_condition(self.p,'broken'))
if __name__=='__main__':unittest.main()
