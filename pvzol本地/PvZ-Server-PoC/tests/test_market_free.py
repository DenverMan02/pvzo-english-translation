import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g
s=g.store
class MarketTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.old=s.DB_PATH;s.set_db_path(str(Path(self.tmp.name)/'test.db'));s.init_db();self.p,_=s.get_or_create_player(899999)
  self.p.update(present_ticket=1000,ticket=0);s.save_player(self.p['uin'],present_ticket=1000,ticket=0)
 def tearDown(self):s.set_db_path(self.old);self.tmp.cleanup()
 def test_free_consumed_and_persists(self):
  with patch.object(g.time,'time',return_value=100000):
   self.assertEqual(g._card_market_pay(self.p,1),[])
   stale=dict(self.p);stale['card_market_next_free_at']=0
   self.assertTrue(g._card_market_pay(stale,1));self.assertEqual(stale['present_ticket'],760)
   reloaded,_=s.get_or_create_player(self.p['uin']);self.assertGreater(reloaded['card_market_next_free_at'],100000)
   self.assertEqual(g.parse_varint_fields(g._card_market_freeinfo_dto(1,reloaded))[3],g._CARD_MARKET_FREE_SECONDS)
  with patch.object(g.time,'time',return_value=100000+g._CARD_MARKET_FREE_SECONDS):
   self.assertEqual(g._card_market_pay(reloaded,1),[]);self.assertEqual(reloaded['present_ticket'],760)
 def test_ticket_priority_and_insufficient_balance(self):
  g._card_market_pay(self.p,1);s.add_item(self.p['uin'],2025,1)
  self.assertTrue(g._card_market_pay(self.p,1));self.assertEqual(s.get_item_count(self.p['uin'],2025),0);self.assertEqual(self.p['present_ticket'],1000)
  s.save_player(self.p['uin'],present_ticket=0,ticket=0)
  self.assertIsNone(g._card_market_pay(self.p,1));self.assertIsNone(g._card_market_pay(self.p,2))
if __name__=='__main__':unittest.main()
