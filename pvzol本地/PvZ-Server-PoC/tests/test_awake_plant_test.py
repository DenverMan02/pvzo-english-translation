import tempfile
from contextlib import contextmanager
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import player_store as db
_BOOT = tempfile.TemporaryDirectory(prefix='pvz_awake_boot_')
db.set_db_path(str(Path(_BOOT.name)/'test.db'))
db.init_db()
import pvz_poc_server as s
import plant_test_repairs_20261011
plant_test_repairs_20261011.install(s)

@contextmanager
def isolated_database():
    with tempfile.TemporaryDirectory() as td:
        try:
            yield td
        finally:
            db.close_thread_db()

def main():
    with isolated_database() as td:
        db.set_db_path(str(Path(td)/'test.db'))
        db.init_db()
        p,_=db.get_or_create_player(998877)
        p['uin']=998877
        uid=123456
        db.add_card(p['uin'],uid,1205,60)
        db.set_card_forge_level(p['uin'],uid,5)
        for iid, count in s.load_awake_rules()[1205]['cost']:
            db.add_item(p['uin'],iid,count)
        msgs=s.handle_card_usecardawaken(s.pb_int64_field(1,uid),p)
        assert msgs and db.get_cards(p['uin'])[0][1]==101205
        assert db.get_card_forge_level(p['uin'],uid)==5
        assert s.handle_card_usecardawaken(s.pb_int64_field(1,uid),p)[0][0]=='PVZ.Cmd.Cmd_LogicError_SC'
        db.set_card_level(p['uin'],uid,70,123)
        for iid, count in s.load_awake_rules()[101205]['cost']:
            db.add_item(p['uin'],iid,count)
        assert s.handle_card_usecardawaken(s.pb_int64_field(1,uid),p)
        assert db.get_cards(p['uin'])[0]==(uid,201205,70,123)
        p['plants']=[1200]
        s.ensure_player_cards(p)
        assert not any(cid==1205 for _,cid,_,_ in db.get_cards(p['uin'])), 'awakening must not generate a new base card'
        info=s.parse_varint_fields(s.handle_plant_test_getinfo(b'',p))
        assert info[1]==1, info
        stage=s.handle_plant_test_getstageinfo(s.pb_varint_field(1,1),p)
        assert len(s.parse_repeated_submessages(stage,6))==30
        assert len(s.parse_repeated_submessages(stage,7))==30
        state=s._plant_test_state(p)
        state['formation']={'3':uid}
        s._save_json_state(p,'plant_test_data',state)
        target=s.pb_bytes(1,s.pb_varint_field(1,16)+s.pb_varint_field(12,1)+s.pb_varint_field(13,1))
        preview=s.handle_battle_getfightinfo(target,p)
        assert len(s.parse_repeated_submessages(preview,4))==6
        request=s.pb_varint_field(1,1)+s.pb_varint_field(2,1)
        # Force outcomes to exercise both progression branches independently of team strength.
        original=s.run_battle
        s.run_battle=lambda left,right,**kwargs:(True,1,0,[])
        first=s.handle_plant_test_fight(request,p)
        assert first and s._plant_test_state(p)['stages']['1']['current']==1
        assert s.handle_plant_test_fight(request,p)[0][0]=='PVZ.Cmd.Cmd_LogicError_SC'
        s.run_battle=lambda left,right,**kwargs:(False,0,1,[])
        second=s.handle_plant_test_fight(s.pb_varint_field(1,1)+s.pb_varint_field(2,2),p)
        assert second and s._plant_test_state(p)['stages']['1']['remain']==2
        assert s.handle_plant_test_restart(s.pb_varint_field(1,1),p)
        assert s._plant_test_state(p)['stages']['1']['current']==0
        assert s._plant_test_state(p)['stages']['1']['remain']==3
        assert s.handle_plant_test_restart(s.pb_varint_field(1,1),p)[0][0]=='PVZ.Cmd.Cmd_LogicError_SC'
        data=s._plant_test_state(p)
        data['day']='20000101'
        s._save_json_state(p,'plant_test_data',data)
        assert s._plant_test_state(p)['stages']['1']['current']==0
        assert s.handle_plant_test_sweep(s.pb_varint_field(1,1),p)
        assert s.handle_plant_test_sweep(s.pb_varint_field(1,1),p)[0][0]=='PVZ.Cmd.Cmd_LogicError_SC'
        s.run_battle=original
        reloaded,_=db.get_or_create_player(p['uin'])
        reloaded['uin']=p['uin']
        assert s._plant_test_state(reloaded)['stages']['1']['current']==1
        # A second physical card cannot lose its own identity/progression during awakening.
        other=uid+1
        db.add_card(p['uin'],other,1205,60)
        db.set_card_forge_level(p['uin'],other,4)
        before=list(db.get_cards(p['uin']))
        assert not db.awaken_card_atomic(p['uin'],other,1205,101205,60,5,[(2111,1000)])
        assert db.get_cards(p['uin'])==before
        # Previous implementation produced awakened cards at forge=0: normal login repairs them.
        db.set_card_forge_level(p['uin'],uid,0)
        s.ensure_player_cards(p)
        assert db.get_card_forge_level(p['uin'],uid)==5
        assert any(card[0]==other for card in db.get_cards(p['uin']))
        # Paid reset must debit gold diamonds and restart chapter progress; insufficient funds changes nothing.
        data=s._plant_test_state(p)
        data['resets']=1
        data['stages']['1']['remain']=0
        s._save_json_state(p,'plant_test_data',data)
        db.save_player(p['uin'],ticket=0)
        p['ticket']=0
        assert s.handle_plant_test_restart(s.pb_varint_field(1,1),p)[0][0]=='PVZ.Cmd.Cmd_LogicError_SC'
        assert s._plant_test_state(p)['resets']==1
        db.save_player(p['uin'],ticket=150)
        p['ticket']=150
        assert s.handle_plant_test_restart(s.pb_varint_field(1,1),p)[0][0]=='PVZ.Cmd.Cmd_PlantTest_RestartStage_SC'
        assert p['ticket']==50
        assert s._plant_test_state(p)['resets']==2
        print('PASS: two awakenings/forge retention, stage DTOs, preview, win/loss, duplicate, free reset, daily reset, sweep, persistence')
        db.close_thread_db()

if __name__=='__main__':
    try:
        main()
    finally:
        db.close_thread_db()
        _BOOT.cleanup()

