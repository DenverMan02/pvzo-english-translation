"""Persistent 48-hour leader transfer; impeachment cancels on leader login."""
import time
import player_store as store
import guild_local as guild
import missing_features_local as mf
from consistency_repairs import v,m,s,integer,atomic,error,state,save


def install(g):
    old_info=guild._guild_info
    old_role=guild._role_guild_info
    def pending(con,gid):
        p={'uin':-gid};st=state(p,'guild_leader');changed=False
        if st.get('target'):
            leader=con.execute('SELECT m.*,p.last_login FROM local_guild_members m LEFT JOIN players p ON p.uin=m.uin '
                               'WHERE m.guild_id=? AND m.position=1',(gid,)).fetchone()
            target=con.execute('SELECT * FROM local_guild_members WHERE guild_id=? AND role_id=?',(gid,st['target'])).fetchone()
            valid=bool(leader and target and leader['role_id']==st['leader'] and target['position'] in (3,4))
            if st['kind']=='delate' and leader and int(leader['last_login'] or 0)>st['started']:valid=False
            if not valid:
                st={};changed=True
            elif time.time()>=st['deadline']:
                con.execute('UPDATE local_guild_members SET position=3 WHERE guild_id=? AND position=1',(gid,))
                con.execute('UPDATE local_guild_members SET position=1 WHERE guild_id=? AND role_id=?',(gid,st['target']))
                st={};changed=True;guild._notify_changed()
            if changed:save(p,'guild_leader',st)
        return st
    def info(con,row):
        if not row:return old_info(con,row)
        gid=row['guild_id'];st=pending(con,gid);fields=mf._fields(old_info(con,guild._guild(con,gid)));body=b''
        for key,vals in fields.items():
            if key in (6,12,13,14,15,16,17):continue
            for wire,val in vals:body+=v(key,val) if wire==0 else m(key,val)
        last=state({'uin':-gid},'guild_level').get('time',row['created_at']);body+=v(6,last)
        for kind,flag,deadline,dto in (('deliver',12,13,17),('delate',14,15,16)):
            active=st.get('kind')==kind and bool(st.get('target'));body+=v(flag,int(active))+v(deadline,st.get('deadline',0) if active else 0)
            if active:
                target=con.execute('SELECT * FROM local_guild_members WHERE guild_id=? AND role_id=?',(gid,st['target'])).fetchone()
                # Dto_Guild_User is roleId/name only; the full member roster
                # DTO has additional fields and a different name field.
                body+=m(dto,v(1,target['role_id'])+s(2,target['name']))
        return body
    guild._guild_info=info
    def role(con,p):
        member=guild._member(con,p['uin'])
        if member:pending(con,member['guild_id'])
        body=old_role(con,p)
        if member:
            midnight=int(time.mktime(time.strptime(time.strftime('%Y%m%d'),'%Y%m%d')))
            count=con.execute('SELECT COUNT(*) FROM local_guild_fertilizer WHERE uin=? AND time>=?',(p['uin'],midnight)).fetchone()[0]
            body+=v(10,max(0,1-count))
        return body
    guild._role_guild_info=role
    def refresh(p):
        with guild._db() as con:
            member=guild._member(con,p['uin']);row=guild._guild(con,member['guild_id']) if member else None
            return [('PVZ.Cmd.Cmd_GuildInfoRefresh_SC',m(1,info(con,row))),('PVZ.Cmd.Cmd_RoleGuildInfo_SC',m(1,role(con,p)))]
    def start(data,p,delate=False,cancel=False):
        with guild._db() as con:
            member=guild._member(con,p['uin'])
            if not member:return error(5007)
            gid=member['guild_id'];st=pending(con,gid);member=guild._member(con,p['uin'])
            leader=con.execute('SELECT m.*,p.last_login FROM local_guild_members m LEFT JOIN players p ON p.uin=m.uin '
                               'WHERE m.guild_id=? AND m.position=1',(gid,)).fetchone()
            if cancel:
                if member['position']!=1 or st.get('kind')!='deliver':return error(5007)
                save({'uin':-gid},'guild_leader',{})
            else:
                if delate:
                    if member['position'] not in (3,4) or not leader:return error(5007)
                    threshold=max(1,int(g._cfg_value('iGuildLearderDelateLoginDay')))*86400
                    if time.time()-int(leader['last_login'] or 0)<threshold or st:return error()
                    target=member
                else:
                    if member['position']!=1:return error(5007)
                    target=con.execute('SELECT * FROM local_guild_members WHERE guild_id=? AND role_id=?',(gid,integer(data,1))).fetchone()
                    if not target or target['position'] not in (3,4) or st:return error()
                now=int(time.time());save({'uin':-gid},'guild_leader',{'kind':'delate' if delate else 'deliver',
                    'target':target['role_id'],'leader':leader['role_id'],'started':now,'deadline':now+48*3600})
        name='GuildCancerLeaderDeliver' if cancel else 'GuildDelateLeader' if delate else 'GuildLeaderDeliver'
        return [('PVZ.Cmd.Cmd_'+name+'_SC',b'')]+refresh(p)
    g.REQUEST_HANDLERS.update({'PVZ.Cmd.Cmd_GuildLeaderDeliver_CS':atomic(start),
        'PVZ.Cmd.Cmd_GuildCancerLeaderDeliver_CS':atomic(lambda d,p:start(d,p,cancel=True)),
        'PVZ.Cmd.Cmd_GuildDelateLeader_CS':atomic(lambda d,p:start(d,p,delate=True))})
