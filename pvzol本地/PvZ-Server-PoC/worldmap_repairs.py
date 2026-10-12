"""Real local towns displayed alongside each account's offline training map.

Keep the old persistent plot IDs, convert their TMX pixels to grid sites, and
preserve actual guild flags. All saved towns can be challenged in the same area;
combat reads their existing defenders without changing their save. Registration
never opens a save.
"""
from __future__ import annotations

import worldmap_local as wm

# First 36 authored objects are city sites: TMX (x,y)/64 - (1,1).
# The former remaining 36 objects are mine markers, so those old assignment
# IDs now receive valid city sites in the next repeated map chunk.
CITY_SITES = (
    (5,5), (11,5), (11,11), (17,11), (5,11), (11,17), (5,17), (5,23),
    (5,29), (11,29), (11,35), (17,29), (11,23), (17,35), (23,35),
    (23,29), (29,35), (23,23), (17,23), (17,17), (23,17), (29,23),
    (29,29), (35,35), (35,29), (35,23), (5,35), (17,5), (23,11),
    (29,17), (23,5), (29,11), (29,5), (35,11), (35,17), (35,5),
)


def plot_position(plot,area=1):
    plot = int(plot)
    if plot < 0:
        raise ValueError('invalid saved city plot')
    block,index = divmod(plot,len(CITY_SITES))
    x,y = CITY_SITES[index]
    chunks = wm.MAP_SIZES[area] // 36
    x += (block % chunks)*36
    y += (block // chunks)*36
    if x >= wm.MAP_SIZES[area]-1 or y >= wm.MAP_SIZES[area]-1:
        raise ValueError('saved city plot outside buildable map')
    return x,y


class LocalCityDisplay:
    def __init__(self,adapter):
        self.a = adapter

    @staticmethod
    def ensure(con):
        con.execute('CREATE TABLE IF NOT EXISTS local_worldmap_cities '
                    '(uin INTEGER PRIMARY KEY,plot INTEGER NOT NULL UNIQUE)')
        con.execute('CREATE TABLE IF NOT EXISTS local_worldmap_city_migrations '
                    '(uin INTEGER PRIMARY KEY)')

    def plot(self,con,player):
        row = con.execute('SELECT plot FROM local_worldmap_cities WHERE uin=?',
                          (player['uin'],)).fetchone()
        if row:
            return int(row[0])
        used = {int(r[0]) for r in con.execute('SELECT plot FROM local_worldmap_cities')}
        for index in range(len(CITY_SITES)*(wm.MAP_SIZES[1]//36)**2):
            if index in used:
                continue
            try:
                plot_position(index)
            except ValueError:
                continue
            con.execute('INSERT INTO local_worldmap_cities(uin,plot) VALUES(?,?)',
                        (player['uin'],index))
            return index
        raise ValueError('no available local city site')

    @staticmethod
    def pristine(st):
        return (int(st['area']) == 1 and (int(st['x']),int(st['y'])) == (18,18)
                and st['area_positions'] == '{}' and not int(st['change_cd']))

    def home(self,player):
        # Only migrate the authenticated account, once. Preserve an already
        # moved town or a persisted mission's origin, and every old plot ID.
        with self.a.db(player) as con:
            self.ensure(con)
            plot = self.plot(con,player)
            receipt = con.execute('SELECT 1 FROM local_worldmap_city_migrations WHERE uin=?',
                                  (player['uin'],)).fetchone()
            if not receipt:
                st = self.a.state(con,player)
                existing_mission = con.execute('SELECT 1 FROM local_worldmap_missions WHERE uin=? LIMIT 1',
                                              (player['uin'],)).fetchone()
                resource_busy = self.a.resources and self.a.resources.busy(player,con=con)
                if self.pristine(st) and not existing_mission and not resource_busy:
                    x,y = self.destination(con,player,1,plot_position(plot))
                    con.execute('UPDATE local_worldmap_state SET x=?,y=? WHERE uin=?',
                                (x,y,player['uin']))
                con.execute('INSERT INTO local_worldmap_city_migrations(uin) VALUES(?)',
                            (player['uin'],))

    def town_state(self,con,player):
        plot = self.plot(con,player)
        row = con.execute('SELECT * FROM local_worldmap_state WHERE uin=?',
                          (player['uin'],)).fetchone()
        migrated = con.execute('SELECT 1 FROM local_worldmap_city_migrations WHERE uin=?',
                               (player['uin'],)).fetchone()
        if row and (migrated or not self.pristine(row)):
            return dict(row)
        x,y = plot_position(plot)
        # A remote account's gameplay state is not initialized by viewing it.
        return dict(area=1,x=x,y=y,cooldowns='{}')

    def occupied(self,con,player,area,pos):
        """Check all real towns under the same relocation write transaction."""
        self.ensure(con)
        for row in con.execute('SELECT * FROM players WHERE uin<>? ORDER BY uin',(player['uin'],)).fetchall():
            state = self.town_state(con,dict(row))
            if state['area'] == area and (state['x'],state['y']) == tuple(pos):
                return True
        return False

    def destination(self,con,player,area,preferred):
        """Cross-area relocation must also select an unoccupied legal site."""
        self.ensure(con)
        temples = {t['pos'] for t in self.a.resources.templates()[0] if t['area'] == area} if self.a.resources else set()
        preferred = tuple(preferred)
        if (all(0 <= n < wm.MAP_SIZES[area]-1 and n % 6 == 5 for n in preferred)
                and preferred not in temples and self.a.npc(pos=preferred) is None
                and not self.occupied(con,player,area,preferred)):
            return preferred
        preferred_plot = self.plot(con,player)
        candidates = [preferred_plot]
        candidates.extend(range(len(CITY_SITES)*(wm.MAP_SIZES[area]//36)**2))
        for index in candidates:
            try:
                pos = plot_position(index,area)
            except ValueError:
                continue
            if pos not in temples and self.a.npc(pos=pos) is None and not self.occupied(con,player,area,pos):
                return pos
        raise ValueError('no available city site in destination area')

    @staticmethod
    def guild(con,uin):
        tables = {row[0] for row in con.execute('SELECT name FROM sqlite_master WHERE type="table"')}
        if not {'local_guilds','local_guild_members'} <= tables:
            return None
        return con.execute('SELECT g.* FROM local_guild_members m JOIN local_guilds g '
                           'ON g.guild_id=m.guild_id WHERE m.uin=?',(uin,)).fetchone()

    def cell(self,con,player,viewer=None):
        st = self.town_state(con,player)
        dto = self.a.cell(player,st)
        guild = self.guild(con,player['uin'])
        if guild:
            flags = b''.join(wm._v(i,int(guild[name])) for i,name in enumerate(
                ('color_id','color2_id','surface_id','sign_id'),1))
            for field,value in ((11,wm._v(11,guild['guild_id'])),
                                (12,wm._s(12,guild['name'])),(13,wm._m(13,flags))):
                dto = wm._replace(dto,field,value)
        # Existing towns can be declared upon; only a real relationship changes
        # the city label to declaring / declared upon / in battle.
        viewer = viewer or player
        state = self.a.state(con,viewer)
        attackable = int(player['uin']) != int(viewer['uin']) and st['area'] == state['area']
        war,deadline,free,free_end = self.a.war_view(con,viewer,int(player.get('role_id') or player['uin']),st['area']) if attackable else (0,0,1,0)
        for field,value in ((5,war),(6,deadline),(8,free),(9,free_end)):
            dto = wm._replace(dto,field,wm._v(field,value))
        return dto,(st['x'],st['y']),st['area']

    def city_init(self,data,player):
        self.home(player)
        return self.a.city_init(data,player)

    def get_info(self,data,player):
        self.home(player)
        area = wm._one(data,5,default=1) or 1
        result = self.a.get_info(data,player)
        if any(name.endswith('LogicError_SC') for name,_body in result):
            return result
        # Replace only the own city, preserving the candidate NPC/resources.
        own_role = int(player.get('role_id') or player['uin'])
        body = result[0][1]
        cells = [value for wire,value in wm._fields(body).get(1,[])
                 if wire == 2 and not (wm._one(value,1) == 1 and wm._one(value,3) == own_role)]
        bounds = [wm._viewport_pos(data,i) for i in range(1,5) if i in wm._fields(data)]
        with self.a.db(player) as con:
            self.ensure(con)
            towns = con.execute('SELECT * FROM players ORDER BY CASE WHEN uin=? THEN 0 ELSE 1 END,uin',
                                (player['uin'],)).fetchall()
            visible = []
            for row in towns:
                dto,pos,town_area = self.cell(con,dict(row),viewer=player)
                if town_area != area:
                    continue
                if bounds and not (min(p[0] for p in bounds) <= pos[0] <= max(p[0] for p in bounds)
                                   and min(p[1] for p in bounds) <= pos[1] <= max(p[1] for p in bounds)):
                    continue
                visible.append(dto)
        return wm._reply('WorldMap_GetInfo',b''.join(wm._m(1,c) for c in visible+cells))

    def get_cell(self,data,player):
        self.home(player)
        role = wm._one(data,1) or int(player.get('role_id') or player['uin'])
        if self.a.npc(role_id=role):
            return self.a.get_cell(data,player)
        with self.a.db(player) as con:
            row = con.execute('SELECT * FROM players WHERE role_id=?',(role,)).fetchone()
            if not row:
                return wm._error(108)
            self.ensure(con)
            dto,_pos,_area = self.cell(con,dict(row),viewer=player)
        return wm._reply('WorldMap_GetCellInfo',wm._m(1,dto))

    def get_city(self,data,player):
        role = wm._one(data,1) or int(player.get('role_id') or player['uin'])
        if self.a.npc(role_id=role):
            return self.a.get_city(data,player)
        with self.a.db(player) as con:
            target = con.execute('SELECT * FROM players WHERE role_id=?',(role,)).fetchone()
            if not target:
                return wm._error(108)
            target = dict(target)
            guild = self.guild(con,target['uin'])
            target_info = self.a.target(player,role_id=role,con=con)
            body = (wm._v(1,role)+wm._s(2,target['name'])+wm._v(3,int(target.get('level') or 1))
                    +wm._v(4,int(target.get('face_id') or 0))
                    +wm._v(5,guild['guild_id'] if guild else 0)+wm._s(6,guild['name'] if guild else '')
                    +wm._s(8,'')+wm._m(9,self.a.api.build_player_diamond(target))
                    +wm._v(10,0)+wm._s(11,'本地城镇，可出征对战其已保存的防守阵容。' if target_info
                                      else '自己的城镇或其他大陆的城镇。'))
        return wm._reply('WorldMap_GetCityInfo',body)

    def guild_members(self,data,player):
        self.home(player)
        with self.a.db(player) as con:
            self.ensure(con)
            guild = self.guild(con,player['uin'])
            towns = con.execute('SELECT p.* FROM players p JOIN local_guild_members m ON p.uin=m.uin '
                                'WHERE m.guild_id=? ORDER BY m.role_id',(guild['guild_id'],)).fetchall() if guild else []
            cells = [self.cell(con,dict(row),viewer=player)[0] for row in towns]
        return wm._reply('WorldMap_GetGuildMemberList',b''.join(wm._m(1,c) for c in cells))

    def handlers(self):
        return {'PVZ.Cmd.Cmd_'+name+'_CS':self.a.guard(fn,name) for name,fn in {
            'City_Init':self.city_init,'WorldMap_GetInfo':self.get_info,
            'WorldMap_GetCellInfo':self.get_cell,'WorldMap_GetCityInfo':self.get_city,
            'WorldMap_GetGuildMemberList':self.guild_members}.items()}


def install(g,force=False):
    adapter = getattr(g,'_worldmap_adapter',None)
    if adapter is None:
        raise RuntimeError('register worldmap_local.WorldMap before local city display')
    display = getattr(adapter,'local_city_display',None)
    if display is None:
        display = adapter.local_city_display = LocalCityDisplay(adapter)
    if getattr(g,'_local_city_display_installed',False) and not force:
        return
    # Restore every route after legacy installers that also replace histories
    # and temples. Keep the original adapter and its ordinary battle delegates.
    routes = adapter.handlers()
    if adapter.resources:
        routes.update(adapter.resources.handlers())
    routes.update(display.handlers())
    g.REQUEST_HANDLERS.update(routes)
    g._WORLD_MAP_REQUESTS = frozenset(routes)
    g._local_city_display_installed = True
