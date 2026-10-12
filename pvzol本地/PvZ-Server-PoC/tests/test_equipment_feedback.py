import sys,tempfile,unittest,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g
s=g.store
class EquipmentFeedbackTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old=s.DB_PATH;s.set_db_path(str(Path(self.tmp.name)/'test.db'));s.init_db();self.p,_=s.get_or_create_player(889994);s.add_card(self.p['uin'],90001,101,1);s.add_equipment(self.p['uin'],20000001,30001,card_uid=90001,level=1)
  s.update_equipment_state(self.p['uin'],current_energy=0,last_energy_at=int(time.time()),auto_use_bead=1)
 def tearDown(self):s.set_db_path(self.old);self.tmp.cleanup()
 def test_pine_level_and_equipped_stats(self):
  before=g.effective_card_stats(self.p,101,1,uid=90001);s.add_item(self.p['uin'],2046,2)
  self.assertTrue(g.handle_equip_add_all_exp(g.pb_int64_field(1,20000001),self.p))
  e=s.get_equipment(self.p['uin'],20000001);self.assertGreater(e['level'],1)
  self.assertLess(s.get_item_count(self.p['uin'],2046),2)
  after=g.effective_card_stats(self.p,101,1,uid=90001);self.assertGreater(after['atk'],before['atk'])
  powers={k:v for k,v,_ in g._card_power_source_elements(self.p,90001,101,1,include_deployed=True)}
  self.assertEqual(powers[0],g.card_power(after))
 def test_rank_upgrade_consumes_bead_when_enabled(self):
  s.add_item(self.p['uin'],2037,18);s.add_item(self.p['uin'],2046,1)
  self.assertTrue(g.handle_equip_upgrade(g.pb_int64_field(1,20000001),self.p))
  self.assertEqual(s.get_item_count(self.p['uin'],2046),0)
  self.assertEqual(s.get_equipment(self.p['uin'],20000001)['grade_percent'],20)
 def test_disabled_never_consumes(self):
  s.update_equipment_state(self.p['uin'],auto_use_bead=0);s.add_item(self.p['uin'],2046,1);s.add_item(self.p['uin'],2037,18)
  self.assertEqual(g.handle_equip_upgrade(g.pb_int64_field(1,20000001),self.p),[])
  self.assertEqual(s.get_item_count(self.p['uin'],2046),1)
if __name__=='__main__':unittest.main()
