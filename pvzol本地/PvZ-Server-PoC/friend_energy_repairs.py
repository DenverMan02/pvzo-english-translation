"""Original FRIEND_ENERGY_NOTES: 5 energy, 10 points, daily 20/10 limits."""
import time
import player_store as store
import guild_local as guild
import missing_features_local as mf
from consistency_repairs import v,m,s,integer,atomic,error,state,save


def install(g):
    with store._db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS local_friend_energy '
                    '(id INTEGER PRIMARY KEY AUTOINCREMENT,sender INTEGER NOT NULL,receiver INTEGER NOT NULL,'
                    'created_at INTEGER NOT NULL,claimed INTEGER NOT NULL DEFAULT 0,returned INTEGER NOT NULL DEFAULT 0,'
                    'hidden_sender INTEGER NOT NULL DEFAULT 0,hidden_receiver INTEGER NOT NULL DEFAULT 0)')
    routes={}
    def route(name,fn):routes['PVZ.Cmd.Cmd_Friend_'+name+'_CS']=atomic(fn)
    def counters(p):
        st=state(p,'friend_energy_counters')
        if st.get('day')!=time.strftime('%Y%m%d'):
            st={'day':time.strftime('%Y%m%d'),'present':0,'take':0,'sent':[]}
        return st
    def giftable(a,b):
        if a['uin']==b['uin']:return False
        with store._db() as con:
            if con.execute('SELECT 1 FROM local_friend_blacklist WHERE '
                           '(uin=? AND role_id=?) OR (uin=? AND role_id=?)',
                           (a['uin'],b['role_id'],b['uin'],a['role_id'])).fetchone():return False
        if store.get_friend_by_role_id(a['uin'],b['role_id']):return True
        if any(int(bot['uin'])==int(b['uin']) for bot in g.bots_local.bots()):return True
        with guild._db() as con:
            ma=guild._member(con,a['uin']);mb=guild._member(con,b['uin'])
            return bool(ma and mb and ma['guild_id']==mb['guild_id'])
    def operation_sync(p,role,st):
        # Dto_Friend_OperationStatus roleId/zoneId/status matches original AS.
        return ('PVZ.Cmd.Cmd_Friend_SyncOperationStatus_SC',m(1,v(1,0)+v(2,role)+v(3,3)+v(4,1))+v(3,20-st['present']))
    def send(p,t):
        st=counters(p)
        if not giftable(p,t) or st['present']>=20 or t['role_id'] in st['sent']:return None
        now=int(time.time())
        with store._db() as con:
            # The same sender's latest gift replaces their older pending gift.
            con.execute('UPDATE local_friend_energy SET hidden_receiver=1 WHERE sender=? AND receiver=?',(p['uin'],t['uin']))
            con.execute('INSERT INTO local_friend_energy(sender,receiver,created_at) VALUES(?,?,?)',(p['uin'],t['uin'],now))
            con.execute('UPDATE local_friend_energy SET hidden_receiver=1 WHERE receiver=? AND id NOT IN '
                        '(SELECT id FROM local_friend_energy WHERE receiver=? AND hidden_receiver=0 ORDER BY id DESC LIMIT 50)',(t['uin'],t['uin']))
        st['present']+=1;st['sent'].append(t['role_id']);save(p,'friend_energy_counters',st)
        return g.grant_fuben_reward(p,[(6,10)])+[operation_sync(p,t['role_id'],st)]
    def present(data,p):
        zone=integer(data,1);role=integer(data,2);t=store.get_player_by_role_id(role)
        if zone!=0 or not t:return error()
        messages=send(p,t)
        if messages is None:return error()
        return messages+[('PVZ.Cmd.Cmd_Friend_PresentEnergy_SC',v(1,zone)+v(2,role))]
    route('PresentEnergy',present)
    def row_status(row,p):
        st=counters(p);other=store.get_player_by_role_id(row['role_id'])
        take=16 if not row['claimed'] and st['take']<10 else 0
        ret=64 if not row['returned'] and st['present']<20 and row['role_id'] not in st['sent'] and other and giftable(p,other) else 0
        return take|ret
    def owned(con,ident,p):
        return con.execute('SELECT e.*,p.role_id,p.name FROM local_friend_energy e JOIN players p ON p.uin=e.sender '
                           'WHERE e.id=? AND e.receiver=? AND e.hidden_receiver=0',(ident,p['uin'])).fetchone()
    def take(data,p):
        ident=integer(data,1);st=counters(p)
        with store._db() as con:
            row=owned(con,ident,p)
            if not row or row['claimed'] or st['take']>=10:return error()
            messages=g.grant_fuben_reward(p,[(9,5)])
            con.execute('UPDATE local_friend_energy SET claimed=1 WHERE id=?',(ident,))
            st['take']+=1;save(p,'friend_energy_counters',st)
            row=owned(con,ident,p);status=row_status(row,p)
        return messages+[('PVZ.Cmd.Cmd_Friend_TakeEnergyInfo_SC',v(1,ident)+v(2,status)+v(3,10-st['take']))]
    route('TakeEnergyInfo',take)
    def return_gift(data,p):
        ident=integer(data,1)
        with store._db() as con:
            row=owned(con,ident,p)
            if not row or not row_status(row,p)&64:return error()
            t=store.get_player_by_role_id(row['role_id']);messages=send(p,t)
            if messages is None:return error()
            con.execute('UPDATE local_friend_energy SET returned=1 WHERE id=?',(ident,))
            status=row_status(owned(con,ident,p),p)
        return messages+[('PVZ.Cmd.Cmd_Friend_ReturnEnergyInfo_SC',v(1,ident)+v(2,status))]
    route('ReturnEnergyInfo',return_gift)
    def getlist(data,p):
        page=max(1,integer(data,1,1));size=max(1,min(50,integer(data,2,10)))
        with store._db() as con:
            rows=con.execute('SELECT e.*,p.role_id,p.name,CASE WHEN e.sender=? THEN 1 ELSE 2 END AS kind '
                             'FROM local_friend_energy e JOIN players p ON p.uin=CASE WHEN e.sender=? THEN e.receiver ELSE e.sender END '
                             'WHERE (e.sender=? AND e.hidden_sender=0) OR (e.receiver=? AND e.hidden_receiver=0) '
                             'ORDER BY e.id DESC LIMIT 100',(p['uin'],p['uin'],p['uin'],p['uin'])).fetchall()
        body=v(1,page)+v(2,size)+v(3,len(rows))
        for row in rows[(page-1)*size:page*size]:
            dto=v(1,row['id'])+v(2,row['kind'])+v(3,0)+v(4,row['role_id'])+s(5,row['name'])+v(6,row['created_at'])+v(7,5)+v(8,10)
            body+=m(4,dto+v(9,row_status(row,p) if row['kind']==2 else 0))
        return [('PVZ.Cmd.Cmd_Friend_GetEnergyList_SC',body)]
    route('GetEnergyList',getlist)
    def delete(data,p):
        ids=set(mf._repeated_int64(data,1))
        with store._db() as con:
            for ident in ids:
                con.execute('UPDATE local_friend_energy SET hidden_sender=CASE WHEN sender=? THEN 1 ELSE hidden_sender END,'
                            'hidden_receiver=CASE WHEN receiver=? THEN 1 ELSE hidden_receiver END WHERE id=?',(p['uin'],p['uin'],ident))
        return [('PVZ.Cmd.Cmd_Friend_DeleteEnergyInfo_SC',b''.join(v(1,i) for i in sorted(ids)))]
    route('DeleteEnergyInfo',delete)
    old=g.REQUEST_HANDLERS['PVZ.Cmd.Cmd_Friend_GetList_CS']
    def friend_list(data,p):
        body=old(data,p);st=counters(p);fields=mf._fields(body);out=b''
        for key,values in fields.items():
            if key in (9,12):continue
            for wire,value in values:
                if key==7:
                    dto=b'';role=integer(value,2)
                    for k,vs in mf._fields(value).items():
                        if k==10:continue
                        for w,val in vs:dto+=v(k,val) if w==0 else m(k,val)
                    t=store.get_player_by_role_id(role)
                    status=3|(8 if t and st['present']<20 and role not in st['sent'] and giftable(p,t) else 0)
                    value=dto+v(10,status)
                out+=v(key,value) if wire==0 else m(key,value)
        return out+v(9,20-st['present'])+v(12,10-st['take'])
    route('GetList',friend_list)
    g.REQUEST_HANDLERS.update(routes)
    return routes
