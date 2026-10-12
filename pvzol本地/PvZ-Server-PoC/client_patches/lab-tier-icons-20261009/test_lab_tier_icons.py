"""Laboratory native icon grades, source values and real upgrade/combat wiring."""
import sys
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

GAME = Path('D:/pvzol本地最新最新ol/pvzol本地')
SERVER=GAME/'PvZ-Server-PoC'
sys.path.insert(0,str(SERVER))
import player_store as store
TEMP=tempfile.TemporaryDirectory(prefix='lab-test-',dir=Path(__file__).resolve().parent)
store.DB_PATH=str(Path(TEMP.name)/'players.db')
store.init_db()
import pvz_poc_server as srv
logging.getLogger('PIL').setLevel(logging.WARNING)
from repair_lab_assets import tables

GRADES={1:'初级',2:'中级',3:'高级',4:'专家',5:'宗师'}
STAT_NAMES={6:'命中',7:'闪避',8:'暴击',9:'韧性'}

def read_rows(path):
    text=dict((n,b) for n,b,_ in tables(path.read_bytes()))['Lab.txt'].decode()
    lines=text.splitlines();h=lines[0].split('\t')
    return [dict(zip(h,line.split('\t'))) for line in lines[1:] if line.strip()]

ROWS=read_rows(GAME/'webroot/clientDB.txt')
SECONDARY=[r for r in ROWS if r['upgradeResult'] and int(r['upgradeResult'].split(',')[0]) in STAT_NAMES]

class LabTierTests(unittest.TestCase):
    def test_all_twenty_secondary_bottles_have_their_own_correct_grade_icons(self):
        self.assertEqual(len({int(r['bottleId']) for r in SECONDARY}),20)
        for r in SECONDARY:
            with self.subTest(bottle=r['bottleId'],level=r['level']):
                st=int(r['upgradeResult'].split(',')[0]);grade=int(r['starLv']);bid=int(r['bottleId'])
                self.assertEqual(r['name'],f'[{GRADES[grade]}]{STAT_NAMES[st]}')
                self.assertEqual(int(r['maxLevel']),grade*20)
                self.assertEqual(r['icon'],str(bid*10000))
                self.assertIn(f'+{int(r["level"])*10}</font>',r['resultDescRemark'])
        for bid in {int(r['bottleId']) for r in ROWS}:
            with Image.open(GAME/'webroot/image/lab'/f'{bid*10000}.png') as image:
                self.assertEqual(image.size,(128,170));self.assertEqual(image.mode,'RGBA')
                self.assertIsNotNone(image.getbbox())

    def test_every_original_field_except_icon_is_preserved(self):
        before=read_rows(SERVER/'backups/lab-tier-icons-20261009-092741/clientDB.txt')
        self.assertEqual(len(before),len(ROWS))
        for old,new in zip(before,ROWS):
            with self.subTest(id=new['id']):
                self.assertEqual({k:v for k,v in old.items() if k!='icon'},
                                 {k:v for k,v in new.items() if k!='icon'})

    def test_all_secondary_levels_apply_exactly_the_displayed_actual_bonus(self):
        player={'uin':919001}
        for r in SECONDARY:
            bid=int(r['bottleId']);level=int(r['level']);st=int(r['upgradeResult'].split(',')[0])
            player['lab_bottles']=f'{bid}:{level}:2'
            with self.subTest(bottle=bid,level=level):
                bonus=srv.lab_stat_bonus(player)
                self.assertEqual(bonus[srv._LAB_STAT_FIELDS[st]],10*level)
                self.assertEqual(sum(bonus.values()),10*level)
        player['lab_bottles']=';'.join(f'{bid}:{srv.load_lab()[bid]["maxLevel"]}:2' for bid in sorted({int(r['bottleId']) for r in SECONDARY}))
        bonus=srv.lab_stat_bonus(player)
        self.assertEqual({k:bonus[k] for k in ('critical','hit','tough','dodge')},
                         {k:3000 for k in ('critical','hit','tough','dodge')})

    def test_actual_card_combat_stats_receive_each_bottle_bonus(self):
        player,_=store.get_or_create_player(919002)
        card_id=next(iter(srv.load_battle_cards()))
        store.add_card(player['uin'],1,card_id,1)
        cards=store.get_cards(player['uin']);self.assertTrue(cards)
        uid,cid,level,_=cards[0]
        player['lab_bottles']=''
        base=srv.effective_card_stats(player,cid,level,bonus=srv.lab_stat_bonus(player),uid=uid)
        for r in SECONDARY:
            if int(r['level']) not in (1,int(r['maxLevel'])):continue
            bid=int(r['bottleId']);lv=int(r['level']);st=int(r['upgradeResult'].split(',')[0])
            player['lab_bottles']=f'{bid}:{lv}:2'
            effective=srv.effective_card_stats(player,cid,level,bonus=srv.lab_stat_bonus(player),uid=uid)
            field=srv._LAB_STAT_FIELDS[st]
            self.assertEqual(effective[field]-base[field],lv*10,(bid,lv))
            for other in ('atk','def_','hp','ability','critical','hit','tough','dodge'):
                if other!=field:self.assertEqual(effective[other],base[other],(bid,lv,other))

    def test_produce_upgrade_and_max_level_guard_use_real_handlers(self):
        for bid in sorted({int(r['bottleId']) for r in SECONDARY}):
            player,_=store.get_or_create_player(920000+bid)
            player['level']=300;player['coin']=100000000
            store.save_player(player['uin'],level=300,coin=100000000)
            bottle=srv.load_lab()[bid]
            for item,count in bottle['levels'][bottle['minLevel']]['unlockCost']:
                store.add_item(player['uin'],item,count)
            req=srv.pb_varint_field(1,bid)
            with patch.object(srv,'_quest_record_condition',return_value=[]):
                messages=srv.handle_lab_produce(req,player)
                self.assertTrue(any(n=='PVZ.Cmd.Cmd_Lab_ProduceLab_SC' for n,_ in messages),bid)
                self.assertEqual(srv._lab_load_state(player)[bid],(bottle['minLevel'],2))
                coin=player['coin']
                messages=srv.handle_lab_upgrade(req,player)
                self.assertTrue(any(n=='PVZ.Cmd.Cmd_Lab_UpgradeLab_SC' for n,_ in messages),bid)
                self.assertEqual(srv._lab_load_state(player)[bid],(bottle['minLevel']+1,2))
                self.assertLess(player['coin'],coin)
                srv._lab_save_bottle(player,bid,bottle['maxLevel']-1,2)
                srv.handle_lab_upgrade(req,player)
                self.assertEqual(srv._lab_load_state(player)[bid],(bottle['maxLevel'],2))
                coin=player['coin'];state=player['lab_bottles']
                self.assertEqual(srv.handle_lab_upgrade(req,player),[])
                self.assertEqual(player['coin'],coin);self.assertEqual(player['lab_bottles'],state)

if __name__=='__main__':
    try:unittest.main(verbosity=2)
    finally:
        store.close_thread_db()
        TEMP.cleanup()
