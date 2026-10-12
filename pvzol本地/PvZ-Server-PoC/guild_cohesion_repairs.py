"""UNION_FUBEN_RULE: energy -> cohesion (1000/day), 10 cohesion -> medal."""
import time
import player_store as store
import guild_local as guild
from consistency_repairs import v,m,atomic,error,state,save


def install(g):
    g.SERVER_CONFIG_OVERRIDES['iGuildFubenNewDayRewardFactor']='10'
    old=g.consume_energy
    def consume(p,amount):
        with store.atomic_player(p):
            paid=old(p,amount)
            if not paid or amount<=0:return paid
            with guild._db() as con:
                member=guild._member(con,p['uin'])
                if not member or time.strftime('%Y%m%d',time.localtime(member['joined_at']))>=time.strftime('%Y%m%d'):
                    return paid
                gid=member['guild_id'];key='guild_cohesion_'+str(gid);st=state(p,key);days=st.setdefault('days',{})
                day=days.setdefault(time.strftime('%Y%m%d'),{'cohesion':0,'taken':0})
                gain=min(int(amount),max(0,1000-int(day['cohesion'])))
                day['cohesion']+=gain
                con.execute('UPDATE local_guilds SET cohesion=cohesion+? WHERE guild_id=?',(gain,gid))
                save(p,key,st)
            return paid
    g.consume_energy=consume
    def counts(p):
        with guild._db() as con:member=guild._member(con,p['uin'])
        if not member:return None
        key='guild_cohesion_'+str(member['guild_id']);st=state(p,key);days=st.setdefault('days',{})
        today=time.strftime('%Y%m%d');current=days.setdefault(today,{'cohesion':0,'taken':0})
        history=sum(max(0,int(r['cohesion'])//10-int(r['taken'])) for d,r in days.items() if d<today)
        return key,st,current,history
    def info(d,p):
        c=counts(p)
        if not c:return error(5007)
        key,st,day,history=c;save(p,key,st)
        return [('PVZ.Cmd.Cmd_GuildFuben_NewDayReward_GetInfo_SC',v(1,2028)+v(2,history)+v(3,day['taken'])+v(4,day['cohesion']//10)+v(5,100))]
    def claim(d,p):
        c=counts(p)
        if not c:return error(5007)
        key,st,day,history=c;amount=history+max(0,day['cohesion']//10-day['taken'])
        if amount<=0:return error()
        messages=g.grant_fuben_reward(p,[(2028,amount)])
        for r in st['days'].values():r['taken']=r['cohesion']//10
        save(p,key,st)
        return messages+[('PVZ.Cmd.Cmd_GuildFuben_NewDayReward_Get_SC',m(1,v(1,2028)+v(2,amount)))]+info(b'',p)
    g.REQUEST_HANDLERS.update({'PVZ.Cmd.Cmd_GuildFuben_NewDayReward_GetInfo_CS':atomic(info),
        'PVZ.Cmd.Cmd_GuildFuben_NewDayReward_Get_CS':atomic(claim)})
