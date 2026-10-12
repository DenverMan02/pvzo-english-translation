"""Account-isolated mine and temple reconstruction for the offline WorldMap.

DTOs are from Assets/src/PVZ/Cmd; temple coordinates/reward items and mine-cart
limits are loaded from clientDB. LOCAL RULES, not the retired online economy:
mine attacks persist a marching task and fight on arrival with the existing
battle engine; temple combat still resolves immediately. Mining
makes 10 coins/second, capped at 36,000 uncollected coins, with no offline multiplier.
The reference video's eight-hour harvest interval is supplied to both the Flash
client and collect_ready by the server iMineCollectCDTime configuration;
capturing a temple gives no immediate currency and its template chest is claimable
once per server-local calendar day while still in the original qualifying guild.
The AS exposes the template's timeLimit through an unused getter; no client
auto-expiry contract was found, so this reconstruction uses the explicitly local
uncollected cap and the client collect cooldown. No other player's map, guild
membership or save is changed.

Import/registration never opens SQLite. All state, item grants and claim receipts
use the adapter's CURRENT store.DB_PATH and one BEGIN IMMEDIATE transaction.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import time

import worldmap_local as wm

# Legacy fixture positions remain valid; the authored TMX mine sites repeat on
# the 2 mod 6 lattice in every 36x36 chunk, not only around the map origin.
MINE_POSITIONS = ((20, 20), (26, 20), (20, 26), (14, 20))
MINING_RATE = 10
MINING_CAP = 36000
REPORT_BASE = 2_000_000_000
# Action IDs are uint32 task identities, independent of battle-report IDs.
MINE_ACTION_BASE = 3_000_000_000
MINE_MISSION_BASE = 4_000_000_000
GUARDING = 2  # Original E_TeamState_Guarding, distinct from Returning (5).
RETRY_SECONDS = 60


class ResourceGameplay:
    def __init__(self, adapter):
        self.a = adapter
        self.api = adapter.api
        self._templates = None
        self._mission_locks = {}
        self._mission_locks_guard = threading.Lock()

    def templates(self):
        if self._templates is None:
            def table(name):
                rows = self.api._read_clientdb_table(name)
                return [dict(zip(rows[0], row)) for row in rows[1:]] if rows else []
            union_levels = {int(r['id']): int(r['unionLevel']) for r in table('MapTempleTypeInfoTemplate.txt')}
            temples = []
            for row in table('MapTempleTemplate.txt'):
                if row.get('isValid') != '1' or int(row['area']) not in wm.SUPPORTED_AREAS:
                    continue
                pos = tuple(int(v.strip()) for v in row['pos'].split(','))
                if len(pos) != 2 or not all(0 <= n < wm.MAP_SIZES[int(row['area'])] for n in pos):
                    continue
                kind = int(row['type'])
                temples.append(dict(id=int(row['id']), type=kind, area=int(row['area']), pos=pos,
                    name='本地演练·' + row['name'], power=1500 if kind == 2 else 15000,
                    stage=1 if kind == 2 else 16, coin=0, union_level=union_levels[kind]))
            rewards = {(int(r['type']), int(r['count'])): tuple(map(int, r['originalReward'].split(':')))
                       for r in table('MapTempleReward.txt')}
            limits = {int(r['id']): (int(r['carCount']), int(r['timeLimit']))
                      for r in table('MapMineInfoTemplate.txt')}
            if any(level < 1 or count < 0 or limit < 0 or bool(count) != bool(limit)
                   for level, (count, limit) in limits.items()):
                raise ValueError('invalid mine cart template')
            self._templates = temples, rewards, limits
        return self._templates

    def ensure(self, con):
        con.execute('''CREATE TABLE IF NOT EXISTS local_worldmap_mines (
            uin INTEGER NOT NULL, area INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL,
            held INTEGER NOT NULL DEFAULT 0, team_id INTEGER NOT NULL DEFAULT 0,
            formation BLOB NOT NULL DEFAULT X'', power INTEGER NOT NULL DEFAULT 0,
            started INTEGER NOT NULL DEFAULT 0, paid INTEGER NOT NULL DEFAULT 0,
            cooldown INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (uin,area,x,y))''')
        columns = {r[1] for r in con.execute('PRAGMA table_info(local_worldmap_mines)')}
        for name, default in (('guard_at', 0), ('guard_x', -1), ('guard_y', -1)):
            if name not in columns:
                con.execute('ALTER TABLE local_worldmap_mines ADD COLUMN '
                            + name + ' INTEGER NOT NULL DEFAULT ' + str(default))
        con.execute('''CREATE TABLE IF NOT EXISTS local_worldmap_temples (
            uin INTEGER NOT NULL, temple_id INTEGER NOT NULL, captured_at INTEGER NOT NULL DEFAULT 0,
            cooldown INTEGER NOT NULL DEFAULT 0, guild_id INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (uin,temple_id))''')
        if 'guild_id' not in {r[1] for r in con.execute('PRAGMA table_info(local_worldmap_temples)')}:
            con.execute('ALTER TABLE local_worldmap_temples ADD COLUMN guild_id INTEGER NOT NULL DEFAULT 0')
        con.execute('''CREATE TABLE IF NOT EXISTS local_worldmap_resource_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT, uin INTEGER NOT NULL, kind INTEGER NOT NULL,
            target INTEGER NOT NULL, report BLOB NOT NULL, created_at INTEGER NOT NULL)''')
        con.execute('''CREATE TABLE IF NOT EXISTS local_worldmap_mine_missions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, uin INTEGER NOT NULL,
            area INTEGER NOT NULL, x INTEGER NOT NULL, y INTEGER NOT NULL,
            team_id INTEGER NOT NULL, origin_x INTEGER NOT NULL, origin_y INTEGER NOT NULL,
            state INTEGER NOT NULL, begin_at INTEGER NOT NULL, arrive_at INTEGER NOT NULL,
            home_at INTEGER NOT NULL, period INTEGER NOT NULL, settled INTEGER NOT NULL DEFAULT 0,
            cancelled INTEGER NOT NULL DEFAULT 0, won INTEGER NOT NULL DEFAULT 0,
            report_id INTEGER NOT NULL DEFAULT 0, formation BLOB NOT NULL,
            placements TEXT NOT NULL, leader INTEGER NOT NULL DEFAULT 0,
            held_before INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)''')
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS local_mine_team_active '
                    'ON local_worldmap_mine_missions(uin,team_id) WHERE state<>4')
        con.execute('CREATE UNIQUE INDEX IF NOT EXISTS local_mine_site_active '
                    'ON local_worldmap_mine_missions(uin,area,x,y) WHERE state<>4')
        con.execute('''CREATE TABLE IF NOT EXISTS local_worldmap_temple_claims (
            uin INTEGER NOT NULL, day TEXT NOT NULL, rewards TEXT NOT NULL,
            PRIMARY KEY (uin,day))''')

    @contextlib.contextmanager
    def db(self, player):
        with self.a.db(player) as con:
            self.ensure(con)
            yield con

    def busy(self, player, con=None):
        if con is None:
            with self.db(player) as db:
                return self.busy(player, db)
        self.ensure(con)
        return (con.execute('SELECT 1 FROM local_worldmap_mines WHERE uin=? AND team_id<>0 LIMIT 1',
                            (player['uin'],)).fetchone() is not None or
                con.execute('SELECT 1 FROM local_worldmap_mine_missions WHERE uin=? AND state<>? LIMIT 1',
                            (player['uin'], wm.HOME)).fetchone() is not None)

    def busy_cards(self, player, con=None):
        """Actual plant UIDs frozen in garrisons, without calling adapter locks."""
        if con is None:
            with self.db(player) as db:
                return self.busy_cards(player, db)
        self.ensure(con)
        rows = con.execute('SELECT formation FROM local_worldmap_mines WHERE uin=? AND team_id<>0',
                           (player['uin'],)).fetchall()
        rows += con.execute('SELECT formation FROM local_worldmap_mine_missions WHERE uin=? AND state<>?',
                            (player['uin'], wm.HOME)).fetchall()
        return {wm._one(card, 2) for row in rows
                for wire, card in wm._fields(bytes(row[0])).get(1, []) if wire == 2}

    def team_busy(self, player, team_id, con=None):
        if con is None:
            with self.db(player) as db:
                return self.team_busy(player, team_id, db)
        self.ensure(con)
        return (con.execute('SELECT 1 FROM local_worldmap_mines WHERE uin=? AND team_id=? LIMIT 1',
                            (player['uin'], team_id)).fetchone() is not None or
                con.execute('SELECT 1 FROM local_worldmap_mine_missions WHERE uin=? AND team_id=? AND state<>? LIMIT 1',
                            (player['uin'], team_id, wm.HOME)).fetchone() is not None)

    def frozen_formation(self, player, team_id):
        with self.db(player) as con:
            row = con.execute('SELECT formation FROM local_worldmap_mine_missions WHERE uin=? AND team_id=? AND state<>?',
                              (player['uin'], team_id, wm.HOME)).fetchone()
            row = row or con.execute('SELECT formation FROM local_worldmap_mines WHERE uin=? AND team_id=?',
                                    (player['uin'], team_id)).fetchone()
        return wm._replace(bytes(row[0]), 6, wm._v(6, 1)) if row else None

    def mine(self, con, player, pos, area=None):
        area = self.a.state(con, player)['area'] if area is None else area
        if not self.valid_mine_position(pos, area):
            raise ValueError('unknown local mine')
        con.execute('INSERT OR IGNORE INTO local_worldmap_mines(uin,area,x,y) VALUES(?,?,?,?)',
                    (player['uin'], area, *pos))
        return dict(con.execute('SELECT * FROM local_worldmap_mines WHERE uin=? AND area=? AND x=? AND y=?',
                                (player['uin'], area, *pos)).fetchone())

    def valid_mine_position(self, pos, area):
        return (area in wm.SUPPORTED_AREAS and len(pos) == 2
                and all(0 <= n < wm.MAP_SIZES[area] and n % 6 == 2 for n in pos)
                and self.a.npc(pos=pos) is None
                and not any(t['area'] == area and t['pos'] == tuple(pos) for t in self.templates()[0]))

    def mine_cars(self, player, st):
        """Persisted city carts, including unguarded carts, without save writes.

        LocalCityDisplay builds city DTOs inside its own write transaction and
        also displays other accounts. Opening the gameplay transaction here
        would deadlock and initialize those remote accounts while viewing them.
        """
        with contextlib.closing(sqlite3.connect(self.api.store.DB_PATH, timeout=10)) as con:
            con.row_factory = sqlite3.Row
            exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_worldmap_mines'").fetchone()
            rows = con.execute('SELECT * FROM local_worldmap_mines WHERE uin=? AND area=? AND started>0',
                               (player['uin'], st['area'])).fetchall() if exists else []
        return b''.join(wm._m(10, wm._m(1, wm._position((r['x'], r['y'])))
            + wm._v(2, max(0, self.produced(r)-r['paid'])) + wm._v(3, MINING_CAP)) for r in rows)

    def mine_history_dto(self, player, row, origin, state=2):
        """A real stationary guard; zero times never claim a travel/return ETA."""
        own = int(player.get('role_id') or player['uin'])
        name = player.get('name') or 'Player'
        side = self.a.target_dto(own, name, origin)
        target = wm._replace(self.a.target_dto(own, name, (row['x'], row['y'])),
                             1, wm._v(1, 3))  # E_WarTargetType_Mine
        span = wm.MAP_SIZES[1]
        action = MINE_ACTION_BASE + row['area']*span*span + row['x']*span + row['y']
        return (wm._v(1, 2) + wm._m(2, side) + wm._m(3, target)
                + wm._v(4, row['team_id']) + wm._v(5, action)
                + wm._v(6, row.get('guard_at', 0)) + wm._v(7, 0)
                + wm._v(8, state) + wm._v(9, 0) + wm._v(10, 0)
                + wm._v(11, 0) + wm._v(12, row['area'])
                + wm._v(13, 1) + wm._v(14, row.get('guard_at', 0)))

    def mine_histories(self, player, name=None):
        """Read-only active guards for private, map and simple history queries.

        A cart without a defending team is not an army task. Legacy saves have
        no guard timestamp/origin; retain zero time and their current hometown.
        Pure guard tasks have reportId zero, so action IDs cannot route a town
        replay or pretend to be one of the separately saved resource reports.
        """
        with contextlib.closing(sqlite3.connect(self.api.store.DB_PATH, timeout=10)) as con:
            con.row_factory = sqlite3.Row
            exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_worldmap_mines'").fetchone()
            if not exists:
                return []
            rows = con.execute('SELECT * FROM local_worldmap_mines WHERE uin=? AND held=1 AND team_id<>0 '
                               'ORDER BY area,x,y', (player['uin'],)).fetchall()
            st = con.execute('SELECT x,y FROM local_worldmap_state WHERE uin=?', (player['uin'],)).fetchone()
            jobs_exist = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_worldmap_mine_missions'").fetchone()
            jobs = con.execute('SELECT * FROM local_worldmap_mine_missions WHERE uin=? '
                               + ('' if name == 'War_GetHistory' else 'AND state<>4 ')
                               + 'ORDER BY id DESC LIMIT ?',
                               (player['uin'], wm.HISTORY_LIMIT)).fetchall() if jobs_exist else []
            active = {(r['area'],r['x'],r['y'],r['team_id']) for r in jobs if r['state'] != wm.HOME}
        result = []
        for saved in rows:
            row = dict(saved)
            if (row['area'],row['x'],row['y'],row['team_id']) in active:
                continue
            origin = (row.get('guard_x', -1), row.get('guard_y', -1))
            if min(origin) < 0:
                origin = (int(st[0]), int(st[1])) if st else (0, 0)
            result.append(self.mine_history_dto(player, row, origin))
        return [self.mission_history_dto(player,dict(job)) for job in jobs] + result

    def mission_history_dto(self, player, row):
        own = int(player.get('role_id') or player['uin'])
        origin = self.a.target_dto(own, player.get('name') or 'Player', (row['origin_x'],row['origin_y']))
        # Missing NPC role information renders the original caption "僵尸".
        target = wm._v(1,3) + wm._m(3,wm._position((row['x'],row['y'])))
        if row['state'] == wm.MARCHING:
            begin,end = row['begin_at'],row['arrive_at']
        elif row['state'] == GUARDING:
            begin,end = row['arrive_at'],0
        else:
            begin,end = row['arrive_at'],row['home_at']
        return (wm._v(1,2) + wm._m(2,origin) + wm._m(3,target) + wm._v(4,row['team_id'])
            + wm._v(5,MINE_MISSION_BASE+row['id']) + wm._v(6,begin) + wm._v(7,end)
            + wm._v(8,row['state']) + wm._v(9,(2 if row['won'] else 1) if row['settled'] else 0)
            + wm._v(10,row['report_id']) + wm._v(11,max(0,end-begin))
            + wm._v(12,row['area']) + wm._v(13,1)
            + wm._v(14,(row['begin_at'] if row['state'] == wm.MARCHING else
                        row['home_at'] if row['state'] == wm.HOME else row['arrive_at'])))

    def has_active_missions(self, player, con=None):
        """Only moving tasks require server polling; a stationary guard does not."""
        if con is None:
            with contextlib.closing(sqlite3.connect(self.api.store.DB_PATH, timeout=10)) as db:
                return self.has_active_missions(player, db)
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_worldmap_mine_missions'").fetchone()
        return bool(exists and con.execute('SELECT 1 FROM local_worldmap_mine_missions '
            'WHERE uin=? AND state IN (?,?,?) LIMIT 1',
            (player['uin'],wm.MARCHING,wm.RECALLING,wm.RETURNING)).fetchone())

    def map_sync(self, player, pos=None, change=None):
        """The client replaces its viewport; never send a mine-only snapshot."""
        with self.db(player) as con:
            st = self.a.state(con, player)
        result = []
        if pos is not None and change is not None:
            result += wm._reply('Mine_StateChange', wm._v(1, change)
                + wm._m(2, wm._position((st['x'], st['y'])))
                + wm._s(3, player.get('name') or 'Player') + wm._m(4, wm._position(pos)))
        request = player.get('_worldmap_viewport', b'')
        if wm._one(request, 5, default=1) != st['area']:
            request = wm._v(5, st['area'])
        # This is an internal snapshot, not a new socket request. Calling the
        # guarded route here recursively advances missions and can deadlock a
        # simultaneous recall while an arrival battle is being calculated.
        display = getattr(self.a,'local_city_display',None)
        return result + (display.get_info(request,player) if display else self.a.get_info(request,player))

    @staticmethod
    def mine_key(row):
        return row['uin'], row['area'], row['x'], row['y']

    def cart_limit(self, player):
        limits = self.templates()[2]
        level = max(1, int(player.get('level') or 1))
        key = max((k for k in limits if k <= level), default=0)
        return limits.get(key, (0, 0))

    def produced(self, row, now=None):
        return min(MINING_CAP, max(0, (wm._now() if now is None else now) - row['started']) * MINING_RATE) if row['started'] else 0

    def guild(self, con, player):
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_guild_members'").fetchone()
        if not exists:
            return 0, ''
        row = con.execute('SELECT g.guild_id,g.name FROM local_guild_members m '
                          'JOIN local_guilds g ON m.guild_id=g.guild_id WHERE m.uin=?', (player['uin'],)).fetchone()
        return (int(row[0]), str(row[1])) if row else (0, '')

    def temple_guild_allowed(self, con, player, temple):
        gid, _name = self.guild(con, player)
        if not gid:
            return False
        row = con.execute('SELECT level FROM local_guilds WHERE guild_id=?', (gid,)).fetchone()
        return bool(row and int(row[0]) >= temple['union_level'])

    def mine_info_body(self, con, player, row):
        pos = (row['x'], row['y'])
        count, limit = self.cart_limit(player)
        guild_id, guild_name = self.guild(con, player) if row['held'] else (0, '')
        body = wm._m(1, wm._position(pos)) + wm._v(2, 2 if row['held'] else 1)
        body += wm._v(3, guild_id) + wm._s(4, guild_name)
        if row['held']:
            produced = self.produced(row)
            holder = (wm._v(1, int(player.get('role_id') or player['uin']))
                + wm._s(2, player.get('name') or 'Player') + wm._v(3, row['power'])
                + wm._v(4, row['started'])
                + wm._v(5, max(0, produced-row['paid'])) + wm._v(6, bool(row['started']))
                + wm._v(7, bool(row['team_id'])) + wm._v(8, row['paid']) + wm._v(9, MINING_CAP))
            body += wm._m(5, holder)
        mining = con.execute('SELECT COUNT(*) FROM local_worldmap_mines WHERE uin=? AND started>0',
                             (player['uin'],)).fetchone()[0]
        return body + wm._v(6, max(0, count-mining)) + wm._v(7, count)

    def mine_info(self, data, player):
        with self.db(player) as con:
            row = self.mine(con, player, wm._pos(data, 1))
            body = self.mine_info_body(con, player, row)
        return wm._reply('Mine_GetInfo', body)

    def collect_ready(self, row, player):
        cooldown = max(1, int(self.api._cfg_value('iMineCollectCDTime', player) or 1))
        return bool(row['started'] and wm._now()-row['started'] >= cooldown)

    def mining_info(self, data, player):
        with self.db(player) as con:
            rows = con.execute('SELECT * FROM local_worldmap_mines WHERE uin=? AND started>0',
                               (player['uin'],)).fetchall()
            produced = sum(self.produced(r) for r in rows)
            available = sum(max(0, self.produced(r)-r['paid']) for r in rows if self.collect_ready(r,player))
        return wm._reply('Mine_GetMiningInfo', wm._v(1, len(rows)) + wm._v(2, produced)
                         + wm._v(3, available))

    def get_defend(self, data, player):
        pos = wm._pos(data, 1)
        with self.db(player) as con:
            row = self.mine(con, player, pos)
        return wm._reply('Mine_GetDefendTeam', wm._m(1, wm._position(pos)) + wm._v(2, row['team_id']))

    def _available(self, con, player, team, placements, requires_attempt=True):
        if team not in wm.ATTACK_TEAMS:
            return 4701
        if (self.a.mission(con, player, team) or self.team_busy(player, team, con)
                or set(placements.values()) & self.a.busy_cards(con, player)):
            return 4702
        st = self.a.state(con, player)
        if st.get('defend_team', 0) == team:
            return 4721
        if requires_attempt and st['used'] >= wm.DAILY_LIMIT+st['bonus']:
            return 4708
        if self.a.repair_cost(st, placements) > int(con.execute(
                'SELECT coin FROM players WHERE uin=?', (player['uin'],)).fetchone()[0]):
            return 202
        return 0

    def valid_placements(self, placements):
        return (bool(placements) and len(placements) <= len(self.api._FORMATION_POS)
                and set(placements) <= set(self.api._FORMATION_POS)
                and len(set(placements.values())) == len(placements))

    def _owned(self, con, player, placements, team):
        owned = {int(r[0]) for r in con.execute('SELECT uid FROM cards WHERE uin=?', (player['uin'],))}
        if not self.valid_placements(placements) or not set(placements.values()) <= owned:
            return False
        return self.a.current_placements(con, player, team) == placements

    def health_snapshot(self, con, player, placements):
        # A different team's battle may update its own plants while this battle
        # is calculated. Only these plants' health is part of this admission.
        health = json.loads(self.a.state(con, player)['health'])
        return {str(uid): int(health.get(str(uid), 10000)) for uid in placements.values()}

    def _save_report(self, con, player, kind, target, report):
        cur = con.execute('INSERT INTO local_worldmap_resource_reports(uin,kind,target,report,created_at) VALUES(?,?,?,?,?)',
                          (player['uin'], kind, target, report, wm._now()))
        con.execute('DELETE FROM local_worldmap_resource_reports WHERE uin=? AND id NOT IN '
                    '(SELECT id FROM local_worldmap_resource_reports WHERE uin=? ORDER BY id DESC LIMIT 50)',
                    (player['uin'], player['uin']))
        return REPORT_BASE + cur.lastrowid

    def _report(self, player, target, placements, kind, team_id=wm.TEAM_ID, leader=None):
        options = {'team_id': team_id}
        if leader is not None:
            options['leader_uid_override'] = leader
        won, report, after = self.a.make_report_details(player, target, placements, **options)
        # Capture pays through mining/daily claims, never a zero-valued loot box.
        left = wm._one(report, 2, 2, None)
        if left is not None:
            report = wm._replace(report, 2, wm._m(2, wm._replace(left, 3, b'')))
        return won, wm._replace(report, 5, wm._v(5, kind)), after

    def apply_health(self, formation, health_after):
        cards, injured = b'', 0
        for wire, card in wm._fields(formation).get(1, []):
            uid, maximum = wm._one(card, 2), wm._one(card, 5)
            ratio = health_after.get(str(uid))
            if ratio is not None:
                card = wm._replace(card, 4, wm._v(4, maximum*ratio//10000))
            injured += int(wm._one(card, 4) < maximum)
            cards += wm._m(1, card)
        formation = wm._replace(formation, 1, cards)
        return wm._replace(formation, 7, wm._v(7, injured*wm.HEAL_COST_PER_CARD))

    def commit_health(self, con, player, after):
        health = json.loads(self.a.state(con, player)['health'])
        health.update(after)
        con.execute('UPDATE local_worldmap_state SET health=? WHERE uin=?',
                    (json.dumps(health), player['uin']))

    def occupy(self, data, player):
        pos, team = wm._pos(data, 1), wm._one(data, 2)
        placements = self.a.placements(player, team) if team in wm.ATTACK_TEAMS else {}
        if not self.valid_placements(placements):
            return wm._error(4704)
        formation = self.a.restored_formation(self.a.formation(player, team))
        repair_paid = 0
        with self.db(player) as con:
            row = self.mine(con, player, pos)
            existing = con.execute('SELECT * FROM local_worldmap_mine_missions '
                'WHERE uin=? AND area=? AND x=? AND y=? AND state<>?',
                (*self.mine_key(row),wm.HOME)).fetchone()
            if existing:
                if existing['team_id'] != team:
                    return wm._error(4702)
                job = dict(existing)
            elif row['team_id'] == team:
                # Existing offline saves keep their stationary guard unchanged.
                return wm._reply('Mine_SetOutTeam', wm._m(1, wm._position(pos)) + wm._v(2, team))
            else:
                if row['team_id']:
                    return wm._error(4702)
                code = self._available(con, player, team, placements, not row['held'])
                if code:
                    return wm._error(code)
                if self.cart_limit(player)[0] == 0:
                    return wm._error(200)
                if row['cooldown'] > wm._now():
                    return wm._error(4709)
                if not self._owned(con, player, placements, team):
                    return wm._error(2001)
                leader = self.a.current_leader(con, player, team)
                if leader != wm._one(formation,3):
                    return wm._error(4705)
                repair_paid = self.a.repair_in_transaction(con, player, placements)
                if repair_paid is None:
                    return wm._error(202)
                st, now = self.a.state(con,player),wm._now()
                period = self.a.period((st['x'],st['y']),pos)
                cur = con.execute('INSERT INTO local_worldmap_mine_missions '
                    '(uin,area,x,y,team_id,origin_x,origin_y,state,begin_at,arrive_at,home_at,period,'
                    'formation,placements,leader,held_before,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (player['uin'],row['area'],*pos,team,st['x'],st['y'],wm.MARCHING,
                     now,now+period,now+period*2,period,formation,json.dumps(placements),leader,row['held'],now))
                if MINE_MISSION_BASE+cur.lastrowid > 0xffffffff:
                    raise ValueError('mine task identity exceeds original uint32 contract')
                if not row['held']:
                    con.execute('UPDATE local_worldmap_state SET used=used+1 WHERE uin=?', (player['uin'],))
                job = dict(con.execute('SELECT * FROM local_worldmap_mine_missions WHERE id=?',
                                       (cur.lastrowid,)).fetchone())
        self.a.active_users.add(int(player['uin']))
        self.a.seen_users.add(int(player['uin']))
        result = wm._reply('Mine_SetOutTeam', wm._m(1, wm._position(pos)) + wm._v(2, team))
        if repair_paid:
            result += wm._reply('SyncCoin', wm._v(1, player['coin']))
        result += wm._reply('War_SyncHistory',wm._m(1,self.mission_history_dto(player,job)))
        result += self.mine_info(data, player) + self.a.get_formation(b'', player) + self.map_sync(player)
        self.a.publish(player,result)
        return result

    def advance_missions(self, player):
        """Resolve due mine battles outside a write lock; commit each task once."""
        # Multiple socket sessions share one resource adapter. Serialize this
        # account's transition calculation so a costly battle is not simulated
        # twice, while unrelated players remain independent. SQL rechecks still
        # reject stale results if a recall or another process changed the task.
        with self._mission_locks_guard:
            lock = self._mission_locks.setdefault(int(player['uin']),threading.RLock())
        with lock:
            return self._advance_missions_locked(player)

    def _advance_missions_locked(self, player):
        with contextlib.closing(sqlite3.connect(self.api.store.DB_PATH, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_worldmap_mine_missions'").fetchone()
            jobs = [dict(r) for r in db.execute('SELECT * FROM local_worldmap_mine_missions '
                'WHERE uin=? AND ((state=? AND arrive_at<=?) OR (state IN (?,?) AND home_at<=?)) ORDER BY id',
                (player['uin'],wm.MARCHING,wm._now(),wm.RECALLING,wm.RETURNING,wm._now()))] if exists else []
        changed, reports = [], []
        for job in jobs:
            report, after, won = None, {}, bool(job['held_before'])
            if job['state'] == wm.MARCHING:
                placements = {int(k):int(v) for k,v in json.loads(job['placements']).items()}
                if not job['held_before']:
                    npc = dict(name='僵尸',power=1500,stage=1,coin=0)
                    won,report,after = self._report(player,npc,placements,7,job['team_id'],leader=job['leader'])
                formation = self.apply_health(bytes(job['formation']),after)
                units = self.api.build_player_units(player,placements,leader_team_id=job['team_id'],
                                                    leader_uid_override=job['leader'])
                power = sum(int(u.get('power') or 0) for u in units)
            with self.db(player) as con:
                saved = con.execute('SELECT * FROM local_worldmap_mine_missions WHERE id=? AND uin=?',
                                    (job['id'],player['uin'])).fetchone()
                if saved is None:
                    continue
                current = dict(saved)
                if (current['state'],current['arrive_at'],current['home_at']) != (job['state'],job['arrive_at'],job['home_at']):
                    continue
                if job['state'] == wm.MARCHING:
                    if current['settled']:
                        continue
                    row = self.mine(con,player,(job['x'],job['y']),area=job['area'])
                    if report:
                        report_id = self._save_report(con,player,7,job['area']*1000000+job['x']*1000+job['y'],report)
                        self.commit_health(con,player,after)
                    else:
                        report_id = 0
                    state = GUARDING if won else wm.RETURNING
                    con.execute('UPDATE local_worldmap_mine_missions SET state=?,settled=1,won=?,report_id=?,formation=? '
                                'WHERE id=? AND uin=?', (state,int(won),report_id,formation,job['id'],player['uin']))
                    if won:
                        con.execute('UPDATE local_worldmap_mines SET held=1,team_id=?,formation=?,power=?,cooldown=0,'
                            'guard_at=?,guard_x=?,guard_y=? WHERE uin=? AND area=? AND x=? AND y=?',
                            (job['team_id'],formation,power,job['arrive_at'],job['origin_x'],job['origin_y'],*self.mine_key(row)))
                    else:
                        con.execute('UPDATE local_worldmap_mines SET cooldown=? WHERE uin=? AND area=? AND x=? AND y=?',
                                    (job['arrive_at']+RETRY_SECONDS,*self.mine_key(row)))
                    if report:
                        reports.append(report)
                # If the server was off throughout the entire failed attack,
                # it settles the loss and return home in this same account lock.
                settled = dict(con.execute('SELECT * FROM local_worldmap_mine_missions WHERE id=?', (job['id'],)).fetchone())
                if settled['state'] in (wm.RETURNING,wm.RECALLING) and settled['home_at'] <= wm._now():
                    con.execute('UPDATE local_worldmap_mine_missions SET state=? WHERE id=?', (wm.HOME,job['id']))
                changed.append(dict(con.execute('SELECT * FROM local_worldmap_mine_missions WHERE id=?', (job['id'],)).fetchone()))
                con.execute('DELETE FROM local_worldmap_mine_missions WHERE uin=? AND state=? AND id NOT IN '
                    '(SELECT id FROM local_worldmap_mine_missions WHERE uin=? ORDER BY id DESC LIMIT ?)',
                    (player['uin'],wm.HOME,player['uin'],wm.HISTORY_LIMIT))
        if not changed:
            return []
        result = [msg for report in reports for msg in wm._reply('Battle_QueryReport',wm._m(1,report))]
        result += [msg for job in changed for msg in wm._reply('War_SyncHistory',wm._m(1,self.mission_history_dto(player,job)))]
        return result + self.a.get_formation(b'',player) + self.map_sync(player)

    def start_mining(self, data, player):
        pos = wm._pos(data, 1)
        started = False
        with self.db(player) as con:
            row = self.mine(con, player, pos)
            if not row['held']:
                return wm._error(4756)
            if not row['started']:
                count = con.execute('SELECT COUNT(*) FROM local_worldmap_mines WHERE uin=? AND started>0',
                                    (player['uin'],)).fetchone()[0]
                if count >= self.cart_limit(player)[0]:
                    return wm._error(4755)
                con.execute('UPDATE local_worldmap_mines SET started=?,paid=0 WHERE uin=? AND area=? AND x=? AND y=?',
                            (wm._now(), *self.mine_key(row)))
                started = True
        return (wm._reply('Mine_Mining', wm._m(1, wm._position(pos))) + self.mine_info(data, player)
                + self.mining_info(b'', player) + self.map_sync(player, pos, 2 if started else None))

    def _collect(self, con, player, row, cancel=False):
        produced = self.produced(row)
        requested = max(0, produced-row['paid'])
        coin = int(con.execute('SELECT coin FROM players WHERE uin=?', (player['uin'],)).fetchone()[0])
        paid = min(requested, max(0, 0xffffffff-coin))
        if paid:
            con.execute('UPDATE players SET coin=coin+? WHERE uin=?', (paid, player['uin']))
        # The AS CollectProduct callback keeps mining but resets its timestamp
        # and production to zero. Commit that same new epoch with the coin grant.
        # Replayed collection at this epoch pays zero; cancellation stops it.
        restarted = wm._now() if row['started'] and not cancel else 0
        con.execute('UPDATE local_worldmap_mines SET started=?,paid=0 WHERE uin=? AND area=? AND x=? AND y=?',
                    (restarted, *self.mine_key(row)))
        return paid

    def collect(self, data, player, cancel=False):
        pos = wm._pos(data, 1)
        with self.db(player) as con:
            row = self.mine(con, player, pos)
            if not row['held']:
                return wm._error(4756)
            if not cancel and not row['started']:
                return wm._error(4753)
            if not cancel and row['started'] and not self.collect_ready(row, player):
                return wm._error(4760)
            paid = self._collect(con, player, row, cancel)
        name = 'Mine_CancelMining' if cancel else 'Mine_CollectProduct'
        return (wm._reply(name, wm._m(1, wm._position(pos)) + wm._v(2, paid))
                + wm._reply('SyncCoin', wm._v(1, max(0, min(0xffffffff, player['coin']))))
                + self.mine_info(data, player) + self.mining_info(b'', player)
                + self.map_sync(player, pos, 3 if cancel else 4))

    def collect_all(self, data, player):
        with self.db(player) as con:
            rows = con.execute('SELECT * FROM local_worldmap_mines WHERE uin=? AND started>0',
                               (player['uin'],)).fetchall()
            total = sum(self._collect(con, player, row) for row in rows if self.collect_ready(row, player))
        return (wm._reply('Mine_CollectAllMine', wm._v(1, total))
                + wm._reply('SyncCoin', wm._v(1, max(0, min(0xffffffff, player['coin']))))
                + self.mining_info(b'', player) + self.map_sync(player))

    def recall(self, data, player):
        pos = wm._pos(data, 1)
        with self.db(player) as con:
            st = self.a.state(con,player)
            job = con.execute('SELECT * FROM local_worldmap_mine_missions WHERE uin=? AND area=? '
                              'AND x=? AND y=? AND state<>?',
                              (player['uin'],st['area'],*pos,wm.HOME)).fetchone()
        if job:
            return self._recall_job(player,dict(job),'Mine_RecallTeam')
        ended = None
        with self.db(player) as con:
            row = self.mine(con, player, pos)
            if row['team_id']:
                st = self.a.state(con, player)
                origin = (row['guard_x'], row['guard_y'])
                if min(origin) < 0:
                    origin = (st['x'], st['y'])
                ended = self.mine_history_dto(player, row, origin, state=4)
            # The client has separate guard and cart controls. Returning the
            # team frees its plants; only CancelMining ends the independent cart.
            con.execute("UPDATE local_worldmap_mines SET team_id=0,formation=X'',power=0 "
                        'WHERE uin=? AND area=? AND x=? AND y=?', self.mine_key(row))
        sync = wm._reply('War_SyncHistory', wm._m(1, ended)) if ended else []
        return (wm._reply('Mine_RecallTeam', wm._m(1, wm._position(pos))) + sync
                + self.mine_info(data, player) + self.a.get_formation(b'', player) + self.map_sync(player))

    def recall_mission(self, data, player):
        team = wm._one(data,1)
        if team not in wm.ATTACK_TEAMS or wm._one(data,2,default=1) != 1:
            return None
        with self.db(player) as con:
            row = con.execute('SELECT * FROM local_worldmap_mine_missions WHERE uin=? AND team_id=? AND state<>?',
                              (player['uin'],team,wm.HOME)).fetchone()
        return self._recall_job(player,dict(row),'War_RecallTeam') if row else None

    def _recall_job(self, player, job, reply):
        with self.db(player) as con:
            saved = con.execute('SELECT * FROM local_worldmap_mine_missions WHERE id=? AND uin=?',
                                (job['id'],player['uin'])).fetchone()
            if saved is None:
                return wm._error(4705)
            current = dict(saved)
            if current['state'] not in (wm.MARCHING,GUARDING,wm.RECALLING):
                return wm._error(4705)
            if current['state'] != wm.RECALLING:
                now = wm._now()
                duration = (max(1,min(current['period'],now-current['begin_at']))
                            if current['state'] == wm.MARCHING else current['period'])
                con.execute('UPDATE local_worldmap_mine_missions SET state=?,cancelled=1,arrive_at=?,home_at=? WHERE id=?',
                            (wm.RECALLING,now,now+duration,current['id']))
                con.execute("UPDATE local_worldmap_mines SET team_id=0,formation=X'',power=0 "
                            'WHERE uin=? AND area=? AND x=? AND y=? AND team_id=?',
                            (player['uin'],current['area'],current['x'],current['y'],current['team_id']))
            current = dict(con.execute('SELECT * FROM local_worldmap_mine_missions WHERE id=?', (current['id'],)).fetchone())
        self.a.active_users.add(int(player['uin']))
        self.a.seen_users.add(int(player['uin']))
        body = wm._m(1,wm._position((job['x'],job['y']))) if reply == 'Mine_RecallTeam' else b''
        result = wm._reply(reply,body) + wm._reply('War_SyncHistory',wm._m(1,self.mission_history_dto(player,current)))
        request = wm._m(1,wm._position((job['x'],job['y'])))
        result += self.mine_info(request,player) + self.a.get_formation(b'',player) + self.map_sync(player)
        self.a.publish(player,result)
        return result

    def speedup_mission(self, data, player):
        team = wm._one(data,1)
        if team not in wm.ATTACK_TEAMS:
            return None
        with self.db(player) as con:
            row = con.execute('SELECT * FROM local_worldmap_mine_missions WHERE uin=? AND team_id=? AND state<>?',
                              (player['uin'],team,wm.HOME)).fetchone()
            if row is None:
                return None
            if wm._one(data,2) != wm.SPEEDUP_ITEM:
                return wm._error(204)
            if row['state'] not in (wm.MARCHING,wm.RETURNING,wm.RECALLING):
                return wm._error(4705)
            column = 'arrive_at' if row['state'] == wm.MARCHING else 'home_at'
            remaining = row[column]-wm._now()
            if remaining < 15:
                return wm._error(4722)
            if not con.execute('UPDATE items SET count=count-1 WHERE uin=? AND item_id=? AND count>=1',
                               (player['uin'],wm.SPEEDUP_ITEM)).rowcount:
                return wm._error(204)
            cut = max(1,(remaining+9)//10)
            if column == 'arrive_at':
                con.execute('UPDATE local_worldmap_mine_missions SET arrive_at=arrive_at-?,home_at=home_at-? WHERE id=?',
                            (cut,cut,row['id']))
            else:
                con.execute('UPDATE local_worldmap_mine_missions SET home_at=home_at-? WHERE id=?', (cut,row['id']))
            count = con.execute('SELECT count FROM items WHERE uin=? AND item_id=?',
                                (player['uin'],wm.SPEEDUP_ITEM)).fetchone()[0]
            job = dict(con.execute('SELECT * FROM local_worldmap_mine_missions WHERE id=?',(row['id'],)).fetchone())
        return (wm._reply('War_SpeedUpTeam')
                + wm._reply('War_SyncHistory',wm._m(1,self.mission_history_dto(player,job)))
                + wm._reply('Item_Update',wm._m(1,self.api.build_item_dto(wm.SPEEDUP_ITEM,count))))

    def temple(self, pos=None, area=2, temple_id=None):
        result = next((t for t in self.templates()[0] if t['id'] == temple_id), None) if temple_id is not None else next(
            (t for t in self.templates()[0] if t['pos'] == pos and t['area'] == area), None)
        if result is None:
            raise ValueError('unknown local temple')
        return result

    def temple_row(self, con, player, temple):
        con.execute('INSERT OR IGNORE INTO local_worldmap_temples(uin,temple_id) VALUES(?,?)',
                    (player['uin'], temple['id']))
        return dict(con.execute('SELECT * FROM local_worldmap_temples WHERE uin=? AND temple_id=?',
                                (player['uin'], temple['id'])).fetchone())

    def temple_owner(self, con, row):
        if not row['captured_at']:
            return 0, '本地演练·僵尸守卫'
        gid = int(row['guild_id'])
        owner = con.execute('SELECT name FROM local_guilds WHERE guild_id=?', (gid,)).fetchone() if gid else None
        return gid, str(owner[0]) if owner else '本地演练·历史占领'

    def temple_info(self, data, player):
        temple = self.temple(wm._pos(data, 1), wm._one(data, 2, default=2))
        with self.db(player) as con:
            row = self.temple_row(con, player, temple)
            gid, gname = self.temple_owner(con, row)
        guild = (wm._v(1, gid) + wm._s(2, gname or ('本地演练·已占领' if row['captured_at'] else '本地演练·僵尸守卫'))
                 + wm._v(3, 0 if row['captured_at'] else 1) + wm._v(4, 1) + wm._v(5, max(0, wm._now()-row['captured_at']) if row['captured_at'] else 0)
                 + wm._v(6, 0) + wm._v(7, 0) + wm._v(8, 0))
        return wm._reply('Temple_GetTempleInfo', wm._m(1, wm._position(temple['pos'])) + wm._v(2, temple['area'])
                         + wm._v(3, not bool(row['captured_at'])) + wm._m(4, guild) + wm._v(5, gid))

    def temple_guards(self, data, player):
        temple = self.temple(wm._pos(data, 1), wm._one(data, 2, default=2))
        with self.db(player) as con:
            row = self.temple_row(con, player, temple)
        body = b'' if row['captured_at'] else wm._m(1, wm._s(1, temple['name']) + wm._v(2, temple['power'])
            + wm._v(3, 0) + wm._v(4, 6100000000000+temple['id']) + wm._v(5, 1))
        return wm._reply('Temple_GetGuarderInfo', body)

    def temple_detect(self, data, player):
        temple = self.temple(temple_id=wm._one(data, 2))
        with self.db(player) as con:
            row = self.temple_row(con, player, temple)
        body = b'' if row['captured_at'] else wm._m(1, wm._v(1, 6100000000000+temple['id'])
            + wm._s(2, temple['name']) + wm._v(3, 0) + wm._s(4, '') + wm._v(5, 1) + wm._v(6, 4))
        return wm._reply('Temple_Detect', body)

    def capture_temple(self, data, player):
        temple = self.temple(wm._pos(data, 1), wm._one(data, 3, default=2))
        team = wm._one(data, 2)
        placements = self.a.placements(player, team) if team in wm.ATTACK_TEAMS else {}
        if not self.valid_placements(placements):
            return wm._error(4704)
        with self.db(player) as con:
            row = self.temple_row(con, player, temple)
            st = self.a.state(con, player)
            if st['area'] != temple['area'] or int(player.get('level') or 1) < 30:
                return wm._error(4703)
            if not self.temple_guild_allowed(con, player, temple):
                return wm._error(4703)
            if row['captured_at']:
                return wm._reply('Temple_SetOut') if row['guild_id'] == self.guild(con,player)[0] else wm._error(4703)
            code = self._available(con, player, team, placements)
            if code:
                return wm._error(code)
            if row['cooldown'] > wm._now():
                return wm._error(4709)
            health_before = self.health_snapshot(con, player, placements)
        try:
            won, report, health_after = self._report(player, temple, placements, 6, team)
        except ValueError:
            return wm._error(4704)
        with self.db(player) as con:
            row = self.temple_row(con, player, temple)
            code = self._available(con, player, team, placements)
            if code:
                return wm._error(code)
            if (row['captured_at'] or row['cooldown'] > wm._now()
                    or self.a.state(con, player)['area'] != temple['area']
                    or self.health_snapshot(con, player, placements) != health_before):
                return wm._error(4705)
            if not self._owned(con, player, placements, team):
                return wm._error(2001)
            if not self.temple_guild_allowed(con, player, temple):
                return wm._error(4703)
            repair_paid = self.a.repair_in_transaction(con, player, placements)
            if repair_paid is None:
                return wm._error(202)
            con.execute('UPDATE local_worldmap_temples SET captured_at=?,cooldown=?,guild_id=? WHERE uin=? AND temple_id=?',
                        (wm._now() if won else 0, wm._now()+RETRY_SECONDS, self.guild(con,player)[0] if won else 0,
                         player['uin'], temple['id']))
            con.execute('UPDATE local_worldmap_state SET used=used+1 WHERE uin=?', (player['uin'],))
            self.commit_health(con, player, health_after)
            self._save_report(con, player, 6, temple['id'], report)
        info = wm._m(1, wm._position(temple['pos'])) + wm._v(2, temple['area'])
        result = wm._reply('Temple_SetOut') + wm._reply('Battle_QueryReport', wm._m(1, report))
        if repair_paid:
            result += wm._reply('SyncCoin', wm._v(1, player['coin']))
        return (result + self.temple_info(info, player) + self.lords(b'', player)
                + self.show_reward(b'', player) + self.a.get_formation(b'', player))

    def lords(self, data, player):
        with self.db(player) as con:
            body = b''
            for temple in self.templates()[0]:
                row = self.temple_row(con, player, temple)
                gid, name = self.temple_owner(con, row)
                body += wm._m(1, wm._v(1, temple['id']) + wm._v(2, gid) + wm._s(3, name))
        return wm._reply('Temple_GetTempleLordInfo', body)

    def temple_list(self, data, player):
        temples = self.templates()[0]
        start, end = wm._one(data, 2), wm._one(data, 3, default=len(temples))
        if start > end or end-start > 1000:
            return wm._error(4703)
        with self.db(player) as con:
            body = b''
            for temple in temples[start:end]:
                row = self.temple_row(con, player, temple)
                gid, name = self.temple_owner(con, row)
                # No undocumented city enemyState values are reused as templeState.
                body += wm._m(1, wm._v(1, temple['id']) + wm._s(2, name)
                    + wm._v(3, 0 if row['captured_at'] else 1) + wm._v(4, 0) + wm._v(5, 0))
        return wm._reply('Setout_GetTempleList', body + wm._v(2, len(temples)))

    def reward_state(self, con, player):
        day = time.strftime('%Y%m%d', time.localtime(wm._now()))
        claimed = con.execute('SELECT 1 FROM local_worldmap_temple_claims WHERE uin=? AND day=?',
                              (player['uin'], day)).fetchone() is not None
        guild_id, guild_name = self.guild(con, player)
        captured = {int(r[0]) for r in con.execute('SELECT temple_id FROM local_worldmap_temples '
            'WHERE uin=? AND captured_at>0 AND guild_id=?', (player['uin'],guild_id))} if guild_id else set()
        counts = {}
        for temple in self.templates()[0]:
            if temple['id'] in captured and self.temple_guild_allowed(con, player, temple):
                counts[temple['type']] = counts.get(temple['type'], 0)+1
        rewards = [self.templates()[1][(kind, min(3, count))] for kind, count in sorted(counts.items())]
        return day, claimed, counts, rewards

    def show_reward(self, data, player):
        with self.db(player) as con:
            day, claimed, counts, rewards = self.reward_state(con, player)
        body = wm._v(1, 1 if claimed else (0 if rewards else 4))
        body += b''.join(wm._m(3, wm._v(1, item) + wm._v(2, count)) for item, count in rewards)
        body += b''.join(wm._m(4, wm._v(1, kind) + wm._v(2, count) + wm._v(3, 3)) for kind, count in sorted(counts.items()))
        return wm._reply('Temple_ShowDailyReward', body + wm._v(5, int(claimed)) + wm._v(6, 1) + wm._v(7, len(rewards)))

    def take_reward(self, data, player):
        updates = []
        with self.db(player) as con:
            day, claimed, counts, rewards = self.reward_state(con, player)
            if claimed or not rewards:
                return wm._error(4705)
            con.execute('INSERT INTO local_worldmap_temple_claims(uin,day,rewards) VALUES(?,?,?)',
                        (player['uin'], day, json.dumps(rewards)))
            for item, count in rewards:
                con.execute('INSERT INTO items(uin,item_id,count) VALUES(?,?,?) ON CONFLICT(uin,item_id) '
                    'DO UPDATE SET count=count+MIN(excluded.count,MAX(0,4294967295-count))', (player['uin'], item, count))
                total = con.execute('SELECT count FROM items WHERE uin=? AND item_id=?', (player['uin'], item)).fetchone()[0]
                updates.append(wm._m(1, self.api.build_item_dto(item, total)))
        result = wm._reply('Temple_TakeDailyReward', b''.join(wm._m(1, wm._v(1, i)+wm._v(2, n)) for i,n in rewards))
        return result + wm._reply('Item_Update', b''.join(updates)) + self.show_reward(b'', player)

    def query_report(self, report_id, player):
        if report_id <= REPORT_BASE:
            return None
        with self.db(player) as con:
            row = con.execute('SELECT report FROM local_worldmap_resource_reports WHERE id=? AND uin=?',
                              (report_id-REPORT_BASE, player['uin'])).fetchone()
        return bytes(row[0]) if row else None

    def preview(self, data, player):
        target = wm._one(data, 1, 2, b'')
        if wm._one(target, 1) not in (6, 7):
            return None
        # These target DTOs carry no position; only dedicated resource commands
        # authenticate the cell. Reject guessed IDs instead of falling into fuben.
        return wm._error(4703)

    def fight(self, data, player):
        target = wm._one(data, 1, 2, b'')
        return wm._error(4705) if wm._one(target, 1) in (6, 7) else None

    def mine_positions(self, player, st, bounds=None, saved=()):
        """Generate only requested TMX sites; viewing does not persist empty mines."""
        size = wm.MAP_SIZES[st['area']]
        if bounds:
            rectangles = [(max(0, min(p[0] for p in bounds)), max(0, min(p[1] for p in bounds)),
                           min(size-1, max(p[0] for p in bounds)), min(size-1, max(p[1] for p in bounds)))]
        else:
            # Internal/unbounded queries stay finite even on the 2016x2016 map.
            # Include the home chunk, initial chunk and all actually touched sites.
            chunk_x, chunk_y = st['x']//36*36, st['y']//36*36
            rectangles = [(0, 0, 35, 35), (chunk_x, chunk_y, min(size-1, chunk_x+35), min(size-1, chunk_y+35))]
        positions = set()
        for left, top, right, bottom in rectangles:
            first_x, first_y = left+(2-left)%6, top+(2-top)%6
            positions.update((x, y) for x in range(first_x, right+1, 6)
                             for y in range(first_y, bottom+1, 6))
        if not bounds:
            positions.update(saved)
        return sorted(p for p in positions if self.valid_mine_position(p, st['area']))

    def cells(self, player, st, bounds=None):
        cells = []
        with self.db(player) as con:
            gid, gname = self.guild(con, player)
            base_cell = wm._replace(self.a.cell(player, st), 10, b'')
            saved = {(r['x'], r['y']): dict(r) for r in con.execute(
                'SELECT * FROM local_worldmap_mines WHERE uin=? AND area=?', (player['uin'], st['area']))}
            for pos in self.mine_positions(player, st, bounds, saved):
                row = saved.get(pos, {'held': 0})
                dto = base_cell
                for field, value in ((1, wm._v(1, 2)), (2, wm._m(2, wm._position(pos))),
                    (3, wm._v(3, 6200000000000+st['area']*1000000+pos[0]*1000+pos[1])), (4, wm._s(4, '本地演练·矿场')), (11, wm._v(11, gid if row['held'] else 0)),
                    (12, wm._s(12, gname if row['held'] else '')), (30, wm._v(30, 2 if row['held'] else 1))):
                    dto = wm._replace(dto, field, value)
                cells.append((dto, pos))
            for temple in self.templates()[0]:
                if temple['area'] != st['area']:
                    continue
                row = self.temple_row(con, player, temple)
                owner_id, owner_name = self.temple_owner(con, row)
                dto = base_cell
                for field, value in ((1, wm._v(1, 3)), (2, wm._m(2, wm._position(temple['pos']))),
                    (3, wm._v(3, 6300000000000+temple['id'])), (4, wm._s(4, temple['name'])),
                    (7, wm._v(7, 12 if temple['type'] == 1 else 11)), (11, wm._v(11, owner_id)),
                    (12, wm._s(12, owner_name if row['captured_at'] else '')), (50, wm._v(50, temple['id']))):
                    dto = wm._replace(dto, field, value)
                cells.append((dto, temple['pos']))
        return cells

    def resource_cells(self, player, st, bounds=None):
        return self.cells(player, st, bounds)

    def handlers(self):
        routes = {'Mine_GetInfo': self.mine_info, 'Mine_GetMiningInfo': self.mining_info,
            'Mine_GetDefendTeam': self.get_defend, 'Mine_SetOutTeam': self.occupy,
            'Mine_Mining': self.start_mining, 'Mine_CollectProduct': self.collect,
            'Mine_CancelMining': lambda d,p: self.collect(d,p,True), 'Mine_RecallTeam': self.recall,
            'Mine_CollectAllMine': self.collect_all, 'Temple_GetTempleInfo': self.temple_info,
            'Temple_GetGuarderInfo': self.temple_guards, 'Temple_Detect': self.temple_detect,
            'Temple_SetOut': self.capture_temple, 'Temple_GetTempleLordInfo': self.lords,
            'Temple_ShowDailyReward': self.show_reward, 'Temple_TakeDailyReward': self.take_reward,
            'Setout_GetTempleList': self.temple_list}
        return {'PVZ.Cmd.Cmd_'+name+'_CS': self.a.guard(fn) for name,fn in routes.items()}


def make_handlers(adapter):
    adapter.resources = ResourceGameplay(adapter)
    return adapter.resources.handlers()


def resource_cells(adapter, player, st):
    return adapter.resources.cells(player, st)
