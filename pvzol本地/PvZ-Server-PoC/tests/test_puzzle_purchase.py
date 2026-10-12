import importlib.util, os, sys, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
source=Path(os.environ.get('PVZ_PUZZLE_SOURCE',ROOT/'pvz_poc_server.py'))
spec=importlib.util.spec_from_file_location('puzzle_test_server',source)
srv=importlib.util.module_from_spec(spec);spec.loader.exec_module(srv)
srv._CLIENTDB_PATH=str(Path(os.environ.get('PVZ_CLIENTDB',r'D:\pvzol本地最新最新ol\pvzol本地\webroot\clientDB.txt')))
store=srv.store
class PuzzlePurchaseTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  old=store.DB_PATH;store.set_db_path(str(Path(self.tmp.name)/'test.db'))
  self.addCleanup(lambda:store.set_db_path(old));store.init_db()
  self.player={'uin':1};self.uid=10001001;store.add_card(1,self.uid,1001,300,0)
 def buy(self,index,amount,uid=None):
  return srv.handle_card_exchangepiece(srv.pb_varint_field(1,index)+srv.pb_varint_field(2,amount)+srv.pb_int64_field(3,uid or self.uid),self.player)
 def fill(self,index):
  return srv.handle_card_fill(srv.pb_int64_field(1,self.uid)+srv.pb_varint_field(2,index),self.player)
 def test_every_configured_stage_and_cell(self):
  store.add_item(1,2020,1000000000);count=0
  for stage,board in srv.load_card_puzzle().items():
   store.set_card_fill_quality(1,self.uid,stage);store.clear_card_filled_indices(1,self.uid)
   self.assertEqual(len(board['cost']),board['width']*board['height'])
   for index,(item,required) in enumerate(board['cost']):
    with self.subTest(stage=stage,index=index):
     price=srv.load_puzzle_prices()[item];before=store.get_item_count(1,2020)
     msgs=self.buy(index,required)
     self.assertEqual([m[0] for m in msgs],['PVZ.Cmd.Cmd_Item_Update_SC','PVZ.Cmd.Cmd_Card_ExchangePiece_SC'])
     self.assertEqual(store.get_item_count(1,2020),before-required*price)
     self.assertEqual(store.get_item_count(1,item),required)
     self.assertTrue(self.fill(index));self.assertEqual(store.get_item_count(1,item),0)
     self.assertFalse(self.fill(index));self.assertFalse(self.buy(index,required));count+=1
   self.assertEqual(len(store.get_card_filled_indices(1,self.uid)),len(board['cost']))
  print('verified stage/cell combinations:',count)
 def test_partial_stock_only_buys_missing_amount(self):
  store.set_card_fill_quality(1,self.uid,3);item,required=srv.load_card_puzzle()[3]['cost'][0]
  store.add_item(1,item,1);store.add_item(1,2020,10000)
  self.assertTrue(self.buy(0,required-1));self.assertEqual(store.get_item_count(1,item),required)
  self.assertEqual(store.get_item_count(1,2020),10000-srv.load_puzzle_prices()[item]*(required-1))
 def test_zero_purchase_when_already_has_material(self):
  store.add_item(1,61001,1);self.assertTrue(self.buy(0,0));self.assertTrue(self.fill(0))
  self.assertEqual(store.get_item_count(1,2020),0)
 def test_insufficient_points_no_change(self):
  store.add_item(1,2020,9);self.assertFalse(self.buy(0,1));self.assertEqual(store.get_items(1),[(2020,9)])
 def test_bad_requests_no_change(self):
  store.add_item(1,2020,10000)
  for index,amount,uid in [(99,1,self.uid),(0,100,self.uid),(0,0,self.uid),(0,1,999)]:self.assertFalse(self.buy(index,amount,uid))
  self.assertEqual(store.get_items(1),[(2020,10000)])
 def test_repeat_purchase_not_double_charged(self):
  store.add_item(1,2020,100);self.assertTrue(self.buy(0,1));self.assertFalse(self.buy(0,1))
  self.assertEqual(store.get_item_count(1,2020),90);self.assertEqual(store.get_item_count(1,61001),1)
 def test_open_panel_does_not_give_free_pieces(self):
  srv.handle_card_getfillinfo(srv.pb_int64_field(1,self.uid),self.player);self.assertEqual(store.get_items(1),[])
 def test_batchfill_consumes_configured_quantities(self):
  store.set_card_fill_quality(1,self.uid,3)
  costs=srv.load_card_puzzle()[3]['cost']
  for item,amount in costs:store.add_item(1,item,amount)
  req=srv.pb_int64_field(1,self.uid)+b''.join(srv.pb_varint_field(2,i) for i in range(len(costs)))
  self.assertTrue(srv.handle_card_batchfill(req,self.player));self.assertEqual(store.get_items(1),[])
  self.assertEqual(store.get_card_filled_indices(1,self.uid),list(range(len(costs))))
 def test_recycle_refunds_actual_high_stage_piece_counts(self):
  store.set_card_fill_quality(1,self.uid,4);store.add_card_filled_index(1,self.uid,0)
  items,coins,notes=srv._card_recycle_reward(self.player,self.uid,(self.uid,1001,300,0))
  for item,amount in srv.load_card_puzzle()[3]['cost']:self.assertEqual(items[item],amount)
  self.assertEqual(items[62201],3)
if __name__=='__main__': unittest.main()
