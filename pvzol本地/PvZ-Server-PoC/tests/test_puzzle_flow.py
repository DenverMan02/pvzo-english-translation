import importlib.util, os, sys, tempfile, unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('puzzle_flow_server',os.environ.get('PVZ_PUZZLE_SOURCE',str(ROOT/'pvz_poc_server.py')))
srv=importlib.util.module_from_spec(spec);spec.loader.exec_module(srv)
srv._CLIENTDB_PATH=os.environ.get('PVZ_CLIENTDB',r'D:\pvzol本地最新最新ol\pvzol本地\webroot\clientDB.txt')
store=srv.store

class PuzzleFlowTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  old=store.DB_PATH;store.set_db_path(str(Path(self.tmp.name)/'test.db'))
  self.addCleanup(lambda:store.set_db_path(old));store.init_db()
  self.player=store.get_or_create_player(1)[0];self.player['level']=200;store.save_player(1,level=200)
  self.uid=10006504;store.add_card(1,self.uid,6504,300,0)
  observer=patch.object(srv,'_quest_observe_card_progress',return_value=[]);observer.start();self.addCleanup(observer.stop)
 def stage(self,n):
  store.set_card_fill_quality(1,self.uid,n);store.clear_card_filled_indices(1,self.uid)
  return srv.load_card_puzzle()[n]
 def batch(self,indexes):
  return srv.handle_card_batchfill(srv.pb_int64_field(1,self.uid)+b''.join(srv.pb_varint_field(2,i) for i in indexes),self.player)
 def buy(self,index,amount):
  return srv.handle_card_exchangepiece(srv.pb_varint_field(1,index)+srv.pb_varint_field(2,amount)+srv.pb_int64_field(3,self.uid),self.player)
 def finish(self):return srv.handle_card_finishfill(srv.pb_int64_field(1,self.uid),self.player)
 def full(self,board):
  for index in range(len(board['cost'])):store.add_card_filled_index(1,self.uid,index)
 def rows(self,messages,name):
  body=dict(messages)[name]
  return [srv.parse_varint_fields(b) for b in srv.parse_repeated_submessages(body,1)]
 def test_gold_and_red_share_original_material_not_25_invented_items(self):
  for stage in range(25,45):
   b=srv.load_card_puzzle()[stage]
   self.assertEqual(len(b['cost']),25)
   self.assertEqual(len(set(i for i,n in b['cost'])),1)
  self.assertEqual(srv.load_card_puzzle()[41]['cost'][0],(67601,49))
 def test_shared_inventory_precedes_fill_event_and_no_false_usable_cells(self):
  for stage in (25,41):
   b=self.stage(stage);item,cost=b['cost'][0];store.add_item(1,item,cost*2+3)
   old_items=dict(store.get_items(1));messages=self.batch(range(25));bag=dict(old_items);filled=set()
   for name,body in messages:
    if name=='PVZ.Cmd.Cmd_Item_Update_SC':
     for r in srv.parse_repeated_submessages(body,1):
      f=srv.parse_varint_fields(r);bag[f[2]]=f[3]
    elif name=='PVZ.Cmd.Cmd_Card_BatchFill_SC':
     filled.update(srv.parse_repeated_varint(body,2))
     # The original updateGroup reserves each shared stack in board order.
     available=bag.get(item,0);can_fill=[]
     for index in range(25):
      if index not in filled and available>=cost:
       can_fill.append(index);available-=cost
     self.assertEqual(can_fill,[], 'client must see final stock at the fill event')
   self.assertEqual(filled,{0,1});self.assertEqual(store.get_item_count(1,item),3)
   self.assertEqual(len(self.rows(messages,'PVZ.Cmd.Cmd_Item_Update_SC')),1)
 def test_buy_before_batch_uses_same_shared_stock_without_double_charge(self):
  b=self.stage(41);item,cost=b['cost'][0];store.add_item(1,item,cost);store.add_item(1,2020,10000)
  self.assertTrue(self.buy(24,0));self.batch([24])
  self.assertEqual(store.get_item_count(1,2020),10000)
  price=srv.load_puzzle_prices()[item]
  self.assertTrue(self.buy(0,cost));self.batch([0])
  self.assertEqual(store.get_item_count(1,2020),10000-cost*price)
  self.assertEqual(store.get_card_filled_indices(1,self.uid),[0,24])
 def test_stale_batch_repairs_view_without_success(self):
  self.stage(25);messages=self.batch([0,1])
  self.assertIn('PVZ.Cmd.Cmd_Card_GetFillInfo_SC',dict(messages))
  self.assertNotIn('PVZ.Cmd.Cmd_Card_BatchFill_SC',dict(messages))
 def test_piece_bonus_matches_config_and_panel_battle_and_power(self):
  self.stage(1)
  before_stats=srv.effective_card_stats(self.player,6504,300,srv._ZERO_STAT_BONUS,uid=self.uid)
  before_info=srv.build_card_sync_sc(1,self.uid,6504,78)[1]
  store.add_item(1,61001,1);messages=self.batch([0])
  after_stats=srv.effective_card_stats(self.player,6504,300,srv._ZERO_STAT_BONUS,uid=self.uid)
  self.assertEqual(after_stats['atk']-before_stats['atk'],84)
  self.assertEqual(after_stats['def_'],before_stats['def_'])
  before=srv.parse_varint_fields(srv.parse_repeated_submessages(before_info,1)[0])
  after=srv.parse_varint_fields(self.rows_body(messages,'PVZ.Cmd.Cmd_Card_SyncFullInfo_SC'))
  self.assertEqual(after[3]-before[3],84)
  sources=srv._card_power_source_elements(self.player,self.uid,6504,300)
  self.assertGreater(dict((e,v) for e,v,sp in sources).get(17,0),0)
 def rows_body(self,messages,name):return srv.parse_repeated_submessages(dict(messages)[name],1)[0]
 def test_completion_retains_piece_bonus_and_adds_only_finish_reward(self):
  b=self.stage(1);self.full(b);before=srv.card_puzzle_bonus(1,self.uid)
  messages=self.finish();after=srv.card_puzzle_bonus(1,self.uid)
  self.assertEqual(store.get_card_fill_quality(1,self.uid),2)
  self.assertEqual(store.get_card_filled_indices(1,self.uid),[])
  for k in ('atk','def_','hp','ability'):self.assertEqual(after[k]-before[k],b['finish'][k])
  self.assertLess([m[0] for m in messages].index('PVZ.Cmd.Cmd_Card_SyncFullInfo_SC'),[m[0] for m in messages].index('PVZ.Cmd.Cmd_Card_FinishFill_SC'))
  self.assertFalse(self.finish());self.assertEqual(srv.card_puzzle_bonus(1,self.uid),after)
 def test_invalid_indices_cannot_fake_completion(self):
  b=self.stage(1)
  for index in range(1,10):store.add_card_filled_index(1,self.uid,index)
  self.assertFalse(self.finish());self.assertEqual(store.get_card_fill_quality(1,self.uid),1)
 def test_level_rules_prevent_buy_fill_and_finish(self):
  b=self.stage(4);store.set_card_level(1,self.uid,28,0);item,cost=b['cost'][0]
  store.add_item(1,item,cost);store.add_item(1,2020,10000)
  self.assertFalse(self.buy(1,b['cost'][1][1]))
  self.batch([0]);self.assertEqual(store.get_item_count(1,item),cost)
  self.full(b);self.assertFalse(self.finish());self.assertEqual(store.get_card_fill_quality(1,self.uid),4)
 def test_final_board_cannot_advance_past_original_hasnext_rule(self):
  b=self.stage(44);self.full(b);before=srv.card_puzzle_bonus(1,self.uid)
  self.assertFalse(self.finish());self.assertEqual(store.get_card_fill_quality(1,self.uid),44)
  self.assertEqual(store.get_card_filled_indices(1,self.uid),list(range(25)))
  self.assertEqual(srv.card_puzzle_bonus(1,self.uid),before)
 def test_batch_rolls_back_material_and_slots_if_sync_fails(self):
  b=self.stage(25);item,cost=b['cost'][0];store.add_item(1,item,cost*2)
  with patch.object(srv,'build_card_sync_sc',side_effect=RuntimeError('sync failure')):
   with self.assertRaises(RuntimeError):self.batch([0,1])
  self.assertEqual(store.get_item_count(1,item),cost*2);self.assertEqual(store.get_card_filled_indices(1,self.uid),[])
 def test_finish_rolls_back_stage_and_cells_if_clear_or_sync_fails(self):
  for where in ('clear_card_filled_indices','build_card_sync_sc'):
   b=self.stage(1);self.full(b)
   target=store if where=='clear_card_filled_indices' else srv
   with patch.object(target,where,side_effect=RuntimeError('finish failure')):
    with self.assertRaises(RuntimeError):self.finish()
   self.assertEqual(store.get_card_fill_quality(1,self.uid),1)
   self.assertEqual(store.get_card_filled_indices(1,self.uid),list(range(9)))
 def test_concurrent_finishes_advance_only_once(self):
  b=self.stage(1);self.full(b)
  def run(_):
   try:return srv.handle_card_finishfill(srv.pb_int64_field(1,self.uid),dict(self.player))
   finally:store.close_thread_db()
  with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
  self.assertEqual(sum(bool(r) for r in results),1)
  self.assertEqual(store.get_card_fill_quality(1,self.uid),2)
 def test_duplicate_batch_indices_consume_once(self):
  b=self.stage(25);item,cost=b['cost'][0];store.add_item(1,item,cost*2)
  self.batch([0,0,0,1,1]);self.assertEqual(store.get_item_count(1,item),0)
  self.assertEqual(store.get_card_filled_indices(1,self.uid),[0,1])
if __name__=='__main__':unittest.main()
