"""Local friendship, character and guild operations with persistent validation."""
from __future__ import annotations
import time
import json
import re
import player_store as store
import guild_local as guild
from consistency_repairs import v,m,s,integer,blob,atomic,error,records,items,available,item_dtos,state,save
import missing_features_local as mf


def install(g):
    routes = {}
    def route(name, fn):
        routes['PVZ.Cmd.'+name+'_CS'] = atomic(fn)
    with store._db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS local_friend_applications '
                    '(sender INTEGER NOT NULL,receiver INTEGER NOT NULL,created_at INTEGER NOT NULL,PRIMARY KEY(sender,receiver))')
        con.execute('CREATE TABLE IF NOT EXISTS local_friend_blacklist '
                    '(uin INTEGER NOT NULL,role_id INTEGER NOT NULL,PRIMARY KEY(uin,role_id))')
        con.execute('CREATE TABLE IF NOT EXISTS local_friend_hidden '
                    '(uin INTEGER NOT NULL,role_id INTEGER NOT NULL,PRIMARY KEY(uin,role_id))')
        con.execute('CREATE TABLE IF NOT EXISTS local_guild_fertilizer '
                    '(id INTEGER PRIMARY KEY AUTOINCREMENT,guild_id INTEGER NOT NULL,uin INTEGER NOT NULL,role_id INTEGER NOT NULL,'
                    'role_name TEXT NOT NULL,fertilizer INTEGER NOT NULL,exp INTEGER NOT NULL,contrib INTEGER NOT NULL,time INTEGER NOT NULL)')
    def target(role):
        return store.get_player_by_role_id(role)
    def blocked(con, a, b):
        return bool(con.execute('SELECT 1 FROM local_friend_blacklist WHERE '
                   '(uin=? AND role_id=?) OR (uin=? AND role_id=?)', (a['uin'],b['role_id'],b['uin'],a['role_id'])).fetchone())
    def add(a,b):
        best=g.friend_best_card(b['role_id']); plant=1200;level=1
        if best:
            plant=int(g._BATTLE_CARDS.get(best[0],{}).get('tdId') or 1200);level=best[1]
        store.add_friend(a['uin'],b['role_id'],b['name'],plant,level,0,0,0,0)
        with store._db() as con:
            con.execute('DELETE FROM local_friend_hidden WHERE uin=? AND role_id=?',(a['uin'],b['role_id']))
    def apply(data,player):
        role=integer(data,1); t=target(role)
        if not t or t['uin']==player['uin']:
            return error()
        with store._db() as con:
            if blocked(con,player,t) or store.get_friend_by_role_id(player['uin'],role):
                return error()
            con.execute('INSERT OR IGNORE INTO local_friend_applications VALUES(?,?,?)',(player['uin'],t['uin'],int(time.time())))
        return [('PVZ.Cmd.Cmd_Friend_Apply_SC',v(1,role))]
    route('Cmd_Friend_Apply',apply)
    def agree(data,player,accept=True):
        ids=mf._repeated_int64(data,1)
        with store._db() as con:
            for role in set(ids):
                t=target(role)
                if not t:
                    continue
                pending=con.execute('SELECT 1 FROM local_friend_applications WHERE sender=? AND receiver=?',(t['uin'],player['uin'])).fetchone()
                if not pending:
                    continue
                if accept and not blocked(con,player,t):
                    add(player,t);add(t,player)
                con.execute('DELETE FROM local_friend_applications WHERE sender=? AND receiver=?',(t['uin'],player['uin']))
        return [('PVZ.Cmd.Cmd_Friend_Apply'+('Agree' if accept else 'Ignore')+'_SC',b'')]
    route('Cmd_Friend_ApplyAgree',agree)
    route('Cmd_Friend_ApplyIgnore',lambda d,p: agree(d,p,False))
    def apply_recommend(data,player):
        for role in set(mf._repeated_int64(data,1)):
            apply(v(1,role),player)
        return [('PVZ.Cmd.Cmd_Friend_ApplyRecommend_SC',b'')]
    route('Cmd_Friend_ApplyRecommend',apply_recommend)
    def delete(data,player):
        role=integer(data,1);store.remove_friend(player['uin'],role)
        t=target(role)
        if t:
            store.remove_friend(t['uin'],player['role_id'])
        with store._db() as con:
            con.execute('INSERT OR IGNORE INTO local_friend_hidden VALUES(?,?)',(player['uin'],role))
        return [('PVZ.Cmd.Cmd_Friend_Delete_SC',v(1,role))]
    route('Cmd_Friend_Delete',delete)
    def blacklist(data,player,remove=False):
        role=integer(data,1)
        if role<=0 or role==int(player['role_id']):return error()
        with store._db() as con:
            if remove:
                con.execute('DELETE FROM local_friend_blacklist WHERE uin=? AND role_id=?',(player['uin'],role))
            else:
                if con.execute('SELECT COUNT(*) FROM local_friend_blacklist WHERE uin=?',(player['uin'],)).fetchone()[0]>=int(g._cfg_value('iBlackListMaxNum')):
                    return error()
                con.execute('INSERT OR IGNORE INTO local_friend_blacklist VALUES(?,?)',(player['uin'],role))
                t=target(role)
                if t:con.execute('DELETE FROM local_friend_applications WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?)',(player['uin'],t['uin'],t['uin'],player['uin']))
        return [('PVZ.Cmd.Cmd_Friend_'+('Del' if remove else 'Add')+'BlackList_SC',b'')]
    route('Cmd_Friend_AddBlackList',blacklist)
    route('Cmd_Friend_DelBlackList',lambda d,p: blacklist(d,p,True))
    def apply_dto(t):
        return v(1,t['role_id'])+s(2,t['name'])+v(3,t['level'])+s(4,g.player_face_url(t))
    def get_apply(data,player):
        page=max(1,integer(data,1,1));size=max(1,min(50,integer(data,2,10)))
        with store._db() as con:
            rows=con.execute('SELECT p.* FROM local_friend_applications a JOIN players p ON p.uin=a.sender WHERE a.receiver=? ORDER BY a.created_at DESC,a.sender',(player['uin'],)).fetchall()
        body=v(1,page)+v(2,size)+v(3,len(rows))+b''.join(m(4,apply_dto(dict(r))) for r in rows[(page-1)*size:page*size])
        return [('PVZ.Cmd.Cmd_Friend_GetApplyList_SC',body)]
    route('Cmd_Friend_GetApplyList',get_apply)
    def get_black(data,player):
        with store._db() as con:
            rows=con.execute('SELECT p.* FROM local_friend_blacklist b JOIN players p ON p.role_id=b.role_id WHERE b.uin=? ORDER BY b.role_id',(player['uin'],)).fetchall()
        # Dto_BlackList_Info field names are roleId/name, not Dto_FriendApply.
        body=b''.join(m(1,v(1,r['role_id'])+s(2,r['name'])) for r in rows)
        return [('PVZ.Cmd.Cmd_Friend_GetBlackList_SC',body)]
    route('Cmd_Friend_GetBlackList',get_black)
    def recommend(data,player):
        with store._db() as con:
            rows=con.execute('SELECT * FROM players WHERE uin<>? AND role_id NOT IN '
                             '(SELECT friend_role_id FROM friends WHERE uin=?) AND role_id NOT IN '
                             '(SELECT role_id FROM local_friend_blacklist WHERE uin=?) ORDER BY last_login DESC,uin LIMIT 6',
                             (player['uin'],player['uin'],player['uin'])).fetchall()
        return [('PVZ.Cmd.Cmd_Friend_GetRecommendList_SC',b''.join(m(1,apply_dto(dict(r))) for r in rows))]
    route('Cmd_Friend_GetRecommendList',recommend)
    def myplant(data,player):
        body=b''.join(m(1,g._build_plant_info_dto(pid)) for pid in store.get_player_plants(player['uin']))
        return [('PVZ.Cmd.Cmd_Friend_GetMyPlant_SC',body)]
    route('Cmd_Friend_GetMyPlant',myplant)
    def get_list(data,player):
        """Fetch the player's actual friend list from the friends table."""
        friends_list=visible_friends(player['uin'],store.get_friends(player['uin']))
        # Build Dto_FriendInfo DTOs: field 1=friend_role_id, field 2=friend_name, field 3=help_plant_id, 
        # field 4=help_plant_level, field 5=diamond_flag, field 6=diamond_blue_lvl, field 7=diamond_yellow_lvl, field 8=diamond_xinyue_lvl
        friend_dtos=b''.join(m(7,v(1,int(f['friend_role_id']))+s(2,f['friend_name'])+v(3,int(f['help_plant_id']))+
                          v(4,int(f['help_plant_level']))+v(5,int(f['diamond_flag']))+v(6,int(f['diamond_blue_lvl']))+
                          v(7,int(f['diamond_yellow_lvl']))+v(8,int(f['diamond_xinyue_lvl']))) for f in friends_list)
        body=friend_dtos
        return [('PVZ.Cmd.Cmd_Friend_GetList_SC',body)]
    route('Cmd_Friend_GetList',get_list)
    # Logs are backed by actual recorded operations, never invented battles.
    def log_info(data,player,fight=False):
        page=max(1,integer(data,1,1));size=max(1,min(50,integer(data,2,10)))
        rows=state(player,'friend_fight_log' if fight else 'friend_energy_log').get('entries',[])
        body=v(1,page)+v(2,size)+v(3,len(rows))
        for row in rows[(page-1)*size:page*size]:body+=m(4,base64_decode(row['body']))
        return [('PVZ.Cmd.Cmd_Friend_Get'+('Fight' if fight else 'Energy')+'List_SC',body)]
    route('Cmd_Friend_GetFightList',lambda d,p:log_info(d,p,True))
    route('Cmd_Friend_GetEnergyList',log_info)
    def delete_log(data,player,fight=False):
        ids=set(mf._repeated_int64(data,1));key='friend_fight_log' if fight else 'friend_energy_log';st=state(player,key)
        st['entries']=[r for r in st.get('entries',[]) if r['id'] not in ids];save(player,key,st)
        return [('PVZ.Cmd.Cmd_Friend_Delete'+('Fight' if fight else 'Energy')+'Info_SC',b''.join(v(1,i) for i in sorted(ids))) ]
    route('Cmd_Friend_DeleteFightInfo',lambda d,p:delete_log(d,p,True))
    route('Cmd_Friend_DeleteEnergyInfo',delete_log)

    def rename(data,player):
        name=blob(data,1).decode('utf-8').strip();sex=integer(data,2)
        # Original RenameFromItemWd.txtName.maxChars=7; AS counts UTF-16.
        length=len(name.encode('utf-16-le'))//2
        if not 2<=length<=7 or sex not in (1,2) or any(ord(c)<32 for c in name):return error()
        with store._db() as con:
            if con.execute('SELECT 1 FROM players WHERE name=? COLLATE NOCASE AND uin<>?',(name,player['uin'])).fetchone():return error()
        if store.get_item_count(player['uin'],2024)<1:return error(5005)
        messages=g.grant_fuben_reward(player,[(2024,-1)])
        player.update(name=name,sex=sex);store.save_player(player['uin'],name=name,sex=sex)
        with store._db() as con:
            con.execute('UPDATE friends SET friend_name=? WHERE friend_role_id=?',(name,player['role_id']))
            con.execute('UPDATE local_guild_members SET name=? WHERE uin=?',(name,player['uin']))
        guild._notify_changed()
        return messages+[('PVZ.Cmd.Cmd_ChangeRoleName_SC',s(1,name)+v(2,sex))]
    route('Cmd_ChangeRoleName',rename)

    def guild_sync(player):
        with guild._db() as con:
            member=guild._member(con,player['uin']); row=guild._guild(con,member['guild_id']) if member else None
            result=[('PVZ.Cmd.Cmd_GuildInfoRefresh_SC',m(1,guild._guild_info(con,row))),
                    ('PVZ.Cmd.Cmd_RoleGuildInfo_SC',m(1,guild._role_guild_info(con,player)))]
        guild._notify_changed();return result
    levels={int(r['id']):r for r in records(g,'UnionLevelInfo.txt')}
    def guild_level(data,player):
        with guild._db() as con:
            member=guild._member(con,player['uin'])
            if not member or member['position'] not in (1,3):return error(5007)
            row=guild._guild(con,member['guild_id']);tpl=levels.get(row['level'])
            if not tpl or row['level']+1 not in levels or row['exp']<int(tpl['needExp']):return error()
            st=state({'uin':-row['guild_id']},'guild_level')
            if int(st.get('time',0))+int(tpl['levelupCD'])>int(time.time()):return error()
            con.execute('UPDATE local_guilds SET level=level+1,exp=exp-? WHERE guild_id=?',(int(tpl['needExp']),row['guild_id']))
            save({'uin':-row['guild_id']},'guild_level',{'time':int(time.time())})
        return [('PVZ.Cmd.Cmd_GuildLevelUp_SC',b'')]+guild_sync(player)
    route('Cmd_GuildLevelUp',guild_level)
    muck={int(r['id']):r for r in records(g,'UnionMuckInfo.txt')}
    def fertilize(data,player):
        fid=integer(data,1);tpl=muck.get(fid)
        with guild._db() as con:
            member=guild._member(con,player['uin'])
            if not tpl or not member:return error(5007)
            row=guild._guild(con,member['guild_id'])
            if min(row['level'],max(int(t['needUnionLv']) for t in muck.values()))!=int(tpl['needUnionLv']):return error()
            start=int(time.mktime(time.strptime(time.strftime('%Y%m%d'),'%Y%m%d')))
            if con.execute('SELECT 1 FROM local_guild_fertilizer WHERE uin=? AND time>=?',(player['uin'],start)).fetchone():return error()
            cost=items(tpl['needRes'])
            if not available(g,player,cost):return error(5005)
            messages=g.grant_fuben_reward(player,[(i,-n) for i,n in cost])
            messages+=g.grant_fuben_reward(player,[(9,int(tpl['rewardPower']))])
            contribution=int(tpl['donate']);player['contrib_val']=int(player.get('contrib_val') or 0)+contribution
            store.save_player(player['uin'],contrib_val=player['contrib_val'])
            con.execute('UPDATE local_guilds SET exp=exp+? WHERE guild_id=?',(int(tpl['rewardExp']),row['guild_id']))
            prior=member['day_contrib'] if member['contrib_day']==time.strftime('%Y%m%d') else 0
            con.execute('UPDATE local_guild_members SET contrib=contrib+?,day_contrib=?,contrib_day=? WHERE uin=?',
                        (contribution,prior+contribution,time.strftime('%Y%m%d'),player['uin']))
            con.execute('INSERT INTO local_guild_fertilizer(guild_id,uin,role_id,role_name,fertilizer,exp,contrib,time) VALUES(?,?,?,?,?,?,?,?)',
                        (row['guild_id'],player['uin'],player['role_id'],player['name'],fid,int(tpl['rewardExp']),contribution,int(time.time())))
        return messages+[('PVZ.Cmd.Cmd_SyncContribVal_SC',v(1,player['contrib_val'])),('PVZ.Cmd.Cmd_GuildFertilizer_SC',v(1,int(tpl['rewardPower'])))]+guild_sync(player)
    route('Cmd_GuildFertilizer',fertilize)
    def fert_log(data,player):
        with guild._db() as con:
            member=guild._member(con,player['uin'])
            rows=con.execute('SELECT * FROM local_guild_fertilizer WHERE guild_id=? ORDER BY time DESC,id DESC LIMIT 100',(member['guild_id'],)).fetchall() if member else []
        body=b''.join(m(1,v(1,r['role_id'])+s(2,r['role_name'])+v(3,r['fertilizer'])+v(4,r['exp'])+v(5,r['time'])+v(6,r['contrib'])) for r in rows)
        return [('PVZ.Cmd.Cmd_GuildFertilizerLog_SC',body)]
    route('Cmd_GuildFertilizerLog',fert_log)
    g.REQUEST_HANDLERS.update(routes)
    import friend_energy_repairs
    friend_energy_repairs.install(g)
    import guild_leader_repairs
    guild_leader_repairs.install(g)
    import guild_cohesion_repairs
    guild_cohesion_repairs.install(g)
    import worldmap_repairs
    worldmap_repairs.install(g)
    import guild_damage_repairs
    guild_damage_repairs.install(g)
    return routes


def base64_decode(value):
    import base64
    return base64.b64decode(value)


def visible_friends(uin, friends):
    with store._db() as con:
        hidden={int(r[0]) for r in con.execute('SELECT role_id FROM local_friend_blacklist WHERE uin=? UNION SELECT role_id FROM local_friend_hidden WHERE uin=?',(uin,uin))}
    seen=set();result=[]
    for fr in friends:
        role=int(fr['friend_role_id'])
        if role not in hidden and role not in seen:
            seen.add(role);result.append(fr)
    return result
