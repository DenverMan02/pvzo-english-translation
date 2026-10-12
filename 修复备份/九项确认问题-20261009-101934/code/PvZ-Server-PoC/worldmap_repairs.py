"""Display saved local accounts and guilds at persistent authored map plots."""
import player_store as store
import guild_local as guild
import worldmap_local as wm
from consistency_repairs import v,m,s,integer,atomic,error


def install(g):
    with store._db() as con:
        con.execute('CREATE TABLE IF NOT EXISTS local_worldmap_cities '
                    '(uin INTEGER PRIMARY KEY,plot INTEGER NOT NULL UNIQUE)')
    def plot(p):
        with store._db() as con:
            row=con.execute('SELECT plot FROM local_worldmap_cities WHERE uin=?',(p['uin'],)).fetchone()
            if row:return row[0]
            used={r[0] for r in con.execute('SELECT plot FROM local_worldmap_cities')}
            available=next((i for i in range(len(wm.CITY_PLOT_XY)) if i not in used),None)
            if available is None:return None
            con.execute('INSERT INTO local_worldmap_cities VALUES(?,?)',(p['uin'],available));return available
    def cell(p):
        index=plot(p)
        if index is None:return None
        x,y=wm.CITY_PLOT_XY[index];info=guild.member_index().get(p['uin'],{})
        flags=b''
        if info:
            with guild._db() as con:
                row=guild._guild(con,info['guild_id']);flags=v(1,row['color_id'])+v(2,row['color2_id'])+v(3,row['surface_id'])+v(4,row['sign_id'])
        return (v(1,0)+m(2,v(1,x)+v(2,y))+v(3,p['role_id'])+s(4,p['name'])+v(5,0)+v(6,0)
                +v(7,p['face_id'])+v(8,1)+v(9,0)+v(11,info.get('guild_id',0))+s(12,info.get('guild_name',''))
                +m(13,flags)+m(14,b'')+v(15,1)+m(16,g.build_player_diamond(p))+v(30,0)+v(50,0))
    def info(data,p):
        area=integer(data,5,1)
        # No fabricated mine/temple objects on other area maps.
        if area!=1:return [('PVZ.Cmd.Cmd_WorldMap_GetInfo_SC',b'')]
        own=cell(p)
        with store._db() as con:players=con.execute('SELECT * FROM players WHERE uin<>? ORDER BY created_at,uin',(p['uin'],)).fetchall()
        cells=[own] if own else []
        for row in players:
            dto=cell(dict(row))
            if dto:cells.append(dto)
        return [('PVZ.Cmd.Cmd_WorldMap_GetInfo_SC',b''.join(m(1,d) for d in cells))]
    def getcell(data,p):
        role=integer(data,1,int(p['role_id']));target=store.get_player_by_role_id(role)
        if not target:return error()
        dto=cell(target)
        return [('PVZ.Cmd.Cmd_WorldMap_GetCellInfo_SC',m(1,dto))] if dto else error()
    def members(data,p):
        with guild._db() as con:
            member=guild._member(con,p['uin'])
            rows=con.execute('SELECT p.* FROM local_guild_members m JOIN players p ON p.uin=m.uin '
                             'WHERE m.guild_id=? ORDER BY m.role_id',(member['guild_id'],)).fetchall() if member else []
        cells=[cell(dict(row)) for row in rows]
        return [('PVZ.Cmd.Cmd_WorldMap_GetGuildMemberList_SC',b''.join(m(1,d) for d in cells if d))]
    g.REQUEST_HANDLERS.update({'PVZ.Cmd.Cmd_WorldMap_GetInfo_CS':atomic(info),
        'PVZ.Cmd.Cmd_WorldMap_GetCellInfo_CS':atomic(getcell),
        'PVZ.Cmd.Cmd_WorldMap_GetGuildMemberList_CS':atomic(members)})
