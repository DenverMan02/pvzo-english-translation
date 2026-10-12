"""Persist shared HP/shield from the actual local guild dungeon replay."""
import threading
import player_store as store
import guild_local as guild
import guild_modes_local as modes
from consistency_repairs import atomic,state,save

_LOCAL=threading.local()
_GAME=None


def key(gid,chapter,stage,reset):return {'uin':-gid},'guild_hp_%d_%d_%d'%(chapter,stage,reset)


def stage_hp(gid,chapter,stage,reset):
    if _GAME is None:return None
    p,k=key(gid,chapter,stage,reset);st=state(p,k)
    if not st:
        units=_GAME._build_wiki_enemies(chapter,stage) or _GAME._build_fallback_enemies(chapter,stage)
        st={'units':{str(u['pos']):{'max':u['hp'],'hp':u['hp'],'shield':u.get('shield',0),'max_shield':u.get('shield',0)} for u in units}}
        save(p,k,st)
    return sum(r['max']+r['max_shield'] for r in st['units'].values()),sum(r['hp']+r['shield'] for r in st['units'].values())


def install(g):
    global _GAME
    _GAME=g;old_units=g.build_battle_units;old_fight=modes._API['run_fight']
    def build(p,chapter,mode,stage):
        left,right=old_units(p,chapter,mode,stage)
        if mode!=3:return left,right
        with guild._db() as con:
            member=guild._member(con,p['uin'])
            run=con.execute('SELECT * FROM local_guild_dungeon WHERE guild_id=? AND chapter_id=?',(member['guild_id'],chapter)).fetchone() if member else None
        if not run:return left,right
        owner,k=key(member['guild_id'],chapter,stage,run['reset_count']);st=state(owner,k)
        if not st:
            st={'units':{str(u['pos']):{'max':u['hp'],'hp':u['hp'],'shield':u.get('shield',0),'max_shield':u.get('shield',0)} for u in right}}
            save(owner,k,st)
        survivors=[]
        for u in right:
            r=st['units'].get(str(u['pos']))
            if r:
                u.update(max_hp=r['max'],hp=r['max'],initial_hp=r['hp'],shield=r['shield'])
                if r['hp']<=0:continue
            survivors.append(u)
        capture=getattr(_LOCAL,'capture',None)
        if capture is not None:capture.update(owner=owner,key=k,state=st,right=survivors)
        return left,survivors
    g.build_battle_units=build
    def fight(d,p):
        capture={};_LOCAL.capture=capture
        try:result=old_fight(d,p)
        finally:_LOCAL.capture=None
        if capture and isinstance(result,list) and result and result[0][0]=='PVZ.Cmd.Cmd_Battle_Fight_SC':
            st=capture['state'];damage=0
            for u in capture['right']:
                r=st['units'][str(u['pos'])];hp=max(0,int(u.get('final_hp',r['hp'])));shield=max(0,int(u.get('final_shield',r['shield'])))
                damage+=max(0,r['hp']+r['shield']-hp-shield);r.update(hp=hp,shield=shield)
            save(capture['owner'],capture['key'],st)
            score=state(capture['owner'],capture['key']+'_damage');score[str(p['role_id'])]=int(score.get(str(p['role_id'])) or 0)+damage
            save(capture['owner'],capture['key']+'_damage',score)
        return result
    modes._API['run_fight']=fight
    # Attempt, shared HP, progress and reward all commit together.
    route='PVZ.Cmd.Cmd_GuildFuben_StartFight_CS'
    g.REQUEST_HANDLERS[route]=atomic(g.REQUEST_HANDLERS[route])
