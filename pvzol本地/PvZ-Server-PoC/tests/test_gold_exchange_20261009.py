from pathlib import Path
import sys, tempfile, unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pvz_poc_server as g
import gold_exchange_local as gold

class GoldExchangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        before=g.store.DB_PATH
        self.addCleanup(lambda:g.store.set_db_path(before))
        g.store.set_db_path(str(Path(self.tmp.name)/'gold.db'))
        g.store.init_db()
        self.player,_=g.store.get_or_create_player(88101300)
        self.save(level=20,coin=100,ticket=100,present_ticket=0)
    def save(self,**fields):
        g.store.save_player(self.player['uin'],**fields)
        self.player.update(fields)
    def info(self,body):
        return g.parse_varint_fields(g.parse_repeated_submessages(body,1)[0])
    def buy(self):return g.handle_goldexchange_exchange(b'',self.player)
    def wallet(self):
        player,_=g.store.get_or_create_player(self.player['uin'])
        return {k:player[k] for k in ['coin','ticket','present_ticket','gold_exchange_num']}
    def test_empty_getinfo_has_complete_original_message(self):
        data=g.handle_goldexchange_getinfo(b'',self.player)
        self.assertEqual(self.info(data),{1:gold.PRICE,2:gold.PRICE*10,3:gold.GOLD,4:1,5:1,6:0})
        self.assertIs(g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_GoldExchange_GetInfo_CS'],g.handle_goldexchange_getinfo)
    def test_buy_gold_diamonds_updates_all_balances_and_record(self):
        reply=dict(self.buy())
        self.assertEqual(self.wallet(),{'coin':10100,'ticket':90,'present_ticket':0,'gold_exchange_num':1})
        self.assertEqual(self.info(reply['PVZ.Cmd.Cmd_GoldExchange_Exchange_SC'])[4],0)
        info=g.parse_repeated_submessages(reply['PVZ.Cmd.Cmd_GoldExchange_Exchange_SC'],1)[0]
        record=g.parse_varint_fields(g.parse_repeated_submessages(info,7)[0])
        self.assertEqual(record,{1:10,2:10000,3:1,4:0,5:37,6:0})
        self.assertIn('PVZ.Cmd.Cmd_SyncTicket_SC',reply)
        self.assertIn('PVZ.Cmd.Cmd_SyncCoin_SC',reply)
    def test_gift_diamonds_are_used_first(self):
        self.save(present_ticket=30,ticket=0)
        reply=dict(self.buy())
        self.assertEqual(self.wallet(),{'coin':10100,'ticket':0,'present_ticket':20,'gold_exchange_num':1})
        self.assertIn('PVZ.Cmd.Cmd_SyncPresentTicket_SC',reply)
        self.assertNotIn('PVZ.Cmd.Cmd_SyncTicket_SC',reply)
    def test_mixed_payment_counts_one_purchase_and_records_exact_cost(self):
        self.save(present_ticket=3,ticket=7)
        reply=dict(self.buy())
        self.assertEqual(self.wallet(),{'coin':10100,'ticket':0,'present_ticket':0,'gold_exchange_num':1})
        dto=g.parse_repeated_submessages(reply['PVZ.Cmd.Cmd_GoldExchange_Exchange_SC'],1)[0]
        records=[g.parse_varint_fields(r) for r in g.parse_repeated_submessages(dto,7)]
        self.assertEqual([(r[5],r[1],r[2]) for r in records],[(4,3,3000),(37,7,7000)])
    def test_not_enough_diamonds_changes_nothing(self):
        self.save(present_ticket=3,ticket=6)
        reply=self.buy()
        self.assertEqual(g.parse_varint_fields(reply[0][1])[1],208)
        self.assertEqual(self.wallet(),{'coin':100,'ticket':6,'present_ticket':3,'gold_exchange_num':0})
    def test_daily_limit_survives_reload(self):
        self.buy()
        before=self.wallet()
        self.player,_=g.store.get_or_create_player(self.player['uin'])
        reply=self.buy()
        self.assertEqual(g.parse_varint_fields(reply[0][1])[1],5900)
        self.assertEqual(self.wallet(),before)
    def test_rollover_resets_count_and_records(self):
        self.buy()
        self.save(gold_exchange_day='20000101')
        self.assertEqual(self.info(g.handle_goldexchange_getinfo(b'',self.player))[4],1)
        self.assertEqual(self.player['gold_exchange_records'],'[]')
    def test_original_vip_privilege_46_increases_allowance(self):
        with patch.object(g,'vip_privileges',return_value={46:20}):
            self.assertEqual(self.info(g.handle_goldexchange_getinfo(b'',self.player))[4],21)
            self.buy()
            self.assertEqual(self.info(g.handle_goldexchange_getinfo(b'',self.player))[4],20)
    def test_getinfo_restores_history_but_exchange_returns_only_new_records(self):
        with patch.object(g,'vip_privileges',return_value={46:20}):
            self.save(present_ticket=10)
            self.buy()
            reply=dict(self.buy())
            dto=g.parse_repeated_submessages(reply['PVZ.Cmd.Cmd_GoldExchange_Exchange_SC'],1)[0]
            self.assertEqual(len(g.parse_repeated_submessages(dto,7)),1)
            dto=g.parse_repeated_submessages(g.handle_goldexchange_getinfo(b'',self.player),1)[0]
            self.assertEqual([g.parse_varint_fields(r)[5] for r in g.parse_repeated_submessages(dto,7)],[37,4])
    def test_locked_level_does_not_charge(self):
        self.save(level=9)
        self.assertEqual(g.parse_varint_fields(self.buy()[0][1])[1],200)
        self.assertEqual(self.wallet()['ticket'],100)
    def test_uint32_overflow_does_not_charge(self):
        self.save(coin=gold.MAX_BALANCE-5)
        self.assertEqual(g.parse_varint_fields(self.buy()[0][1])[1],5901)
        self.assertEqual(self.wallet()['ticket'],100)
    def test_failure_after_wallet_write_rolls_back_in_memory_and_save(self):
        g.handle_goldexchange_getinfo(b'',self.player)
        before=self.wallet()
        with patch.object(gold,'_body',side_effect=RuntimeError('forced reply failure after wallet save')):
            with self.assertRaises(RuntimeError):self.buy()
        self.assertEqual(self.wallet(),before)
        self.assertEqual(self.player['coin'],100)
        self.assertEqual(self.player['ticket'],100)

if __name__=='__main__':unittest.main()
