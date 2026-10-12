"""Protocol-level time journey regression. Always uses an isolated SQLite DB."""
import json, os, sys, tempfile
from pathlib import Path

SERVER = Path(os.environ.get('PVZ_TEST_SERVER', Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(SERVER))
import player_store as store
temp = tempfile.TemporaryDirectory(prefix='pvz_time_journey_')
store.DB_PATH = str(Path(temp.name) / 'isolated.db')
store.init_db()
import pvz_poc_server as s


def state(p):
    return json.loads(p['daily_td_data'])


def field(messages, name, num):
    body = next(body for cmd, body in messages if cmd.endswith(name))
    return s.parse_varint_fields(body).get(num, 0)


def main():
    p, _ = store.get_or_create_player(889020001)
    store.save_player(p['uin'], ticket=1000)
    p['ticket'] = 1000
    p['plants'] = [1100, 1200, 1300, 1500, 1600, 1700]
    messages = s.handle_dailytd_getinfo(b'', p)
    d = state(p)
    assert d['seq'] > 0 and len(d['route']) == 7
    assert d['cars'] == [1] * 5 and len(d['offers']) == 3
    assert all(x in p['plants'] for x in d['offers'])
    route = list(d['route'])
    # Seven wins must be possible with a two-loss budget.
    for stage in range(1, 8):
        assert state(p)['stage'] == stage and state(p)['seq'] == route[stage - 1]
        offered = state(p)['offers'][0]
        messages = s.handle_dailytd_choosecard(s.pb_varint_field(1, 1), p)
        assert offered in state(p)['cards']
        assert messages[0][0].endswith('GetInfo_SC') and messages[-1][0].endswith('ChooseCard_SC')
        start = s.pb_varint_field(1, stage)
        assert field(s.handle_dailytd_starttdgame(start, p), 'StartTDGame_SC', 1) == 1
        run = state(p)['run']
        s.handle_dailytd_starttdgame(start, p)
        assert state(p)['run'] == run
        if stage == 2:
            # Flash sends stageId=1 for purchases in every daily checkpoint.
            ticket_before = p['ticket']
            purchase = s.pb_varint_field(1, 1)
            s.handle_dailytd_buysun(purchase, p)
            assert p['ticket'] == ticket_before - 2
            s.handle_dailytd_buysun(purchase, p)
            assert p['ticket'] == ticket_before - 5
            p['td_privilege'] = 1
            p['td_privilege_endtime'] = 0
            bean_reply = s.handle_dailytd_buyenergybean(purchase, p)
            assert field(bean_reply, 'BuyEnergyBean_SC', 1) == 9
            assert p['ticket'] == ticket_before - 10
            beans_before = state(p)['beans']
            skill = purchase + s.pb_varint_field(2, 5700)
            s.handle_dailytd_useskill(skill, p)
            assert p['ticket'] == ticket_before - 30
            assert state(p)['beans'] == beans_before
        report = start + s.pb_varint_field(2, 1)
        if stage == 3:
            # The first report can arrive as a resend after a network break.
            report += s.pb_bool(5, True)
        s.handle_dailytd_reportresult(report, p)
        assert len(state(p)['lottery']) == 3
        assert state(p)['losses'] == 0
        snapshot = p['daily_td_data']
        s.handle_dailytd_reportresult(report, p)
        assert snapshot == p['daily_td_data']
        for _ in range(3):
            s.handle_dailytd_dolottery(b'', p)
        snapshot = p['daily_td_data']
        s.handle_dailytd_dolottery(b'', p)
        assert snapshot == p['daily_td_data']
        # A stale success after advancing cannot issue another reward.
        s.handle_dailytd_reportresult(report, p)
        assert snapshot == p['daily_td_data']
    assert state(p)['stage'] == 7 and state(p)['process'] == 2
    assert store.get_item_count(p['uin'], 2010) == 140
    reloaded, _ = store.get_or_create_player(p['uin'])
    assert state(reloaded) == state(p)
    # Shop must return six config IDs and charge bag time coins, not a fake token.
    shop = s.handle_dailytd_gettokenshop(b'', p)
    body = next(body for cmd, body in shop if cmd.endswith('GetTokenShop_SC'))
    ids = s.parse_repeated_varint(body, 1)
    assert len(ids) == 6
    trade = ids[0]; row = s._dailytd_tables()['trades'][trade]
    count_before = store.get_item_count(p['uin'], int(row['itemID']))
    s.handle_dailytd_dotrade(s.pb_varint_field(1, trade), p)
    assert store.get_item_count(p['uin'], 2010) == 140 - int(row['price'])
    assert store.get_item_count(p['uin'], int(row['itemID'])) == count_before + int(row['itemNum'])
    coin_before = store.get_item_count(p['uin'], 2010)
    s.handle_dailytd_dotrade(s.pb_varint_field(1, trade), p)
    assert store.get_item_count(p['uin'], 2010) == coin_before
    # Cross-day resets progression but not real currency.
    d = state(p); d['day'] = '19990101'; s._dailytd_commit(p, d)
    s.handle_dailytd_getinfo(b'', p)
    assert state(p)['stage'] == 1 and state(p)['losses'] == 0
    assert store.get_item_count(p['uin'], 2010) == coin_before
    # Two failures close the daily retry budget; duplicates don't cost attempts.
    s.handle_dailytd_choosecard(s.pb_varint_field(1, 1), p)
    for losses in (1, 2):
        s.handle_dailytd_starttdgame(s.pb_varint_field(1, 1), p)
        req = s.pb_varint_field(1, 1) + s.pb_varint_field(2, 0)
        s.handle_dailytd_reportresult(req, p)
        s.handle_dailytd_reportresult(req, p)
        assert state(p)['losses'] == losses
    assert field(s.handle_dailytd_starttdgame(s.pb_varint_field(1, 1), p), 'StartTDGame_SC', 1) == 0
    # Legacy token conversion is once-only and repairs invalid map seq=0.
    q, _ = store.get_or_create_player(889020002)
    q['daily_td_data'] = json.dumps({'day':s.time.strftime('%Y%m%d'),'stage':1,'seq':0,'token':37})
    s.handle_dailytd_getinfo(b'', q); s.handle_dailytd_getinfo(b'', q)
    assert state(q)['seq'] > 0 and store.get_item_count(q['uin'], 2010) == 37
    print('PASS: seven stages, correct wire ordering, duplicate reports, persistence, shop, rollover, loss limit, migration')


if __name__ == '__main__':
    main()
