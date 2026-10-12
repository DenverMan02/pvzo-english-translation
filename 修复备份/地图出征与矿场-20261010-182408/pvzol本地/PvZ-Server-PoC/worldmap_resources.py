"""Account-isolated mine and temple reconstruction for the offline WorldMap.

DTOs are from Assets/src/PVZ/Cmd; temple coordinates/reward items and mine-cart
limits are loaded from clientDB. LOCAL RULES, not the retired online economy:
resource combat resolves immediately with the existing battle engine; mining
makes 10 coins/second, capped at 36,000 uncollected coins, with no offline multiplier;
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
import time

import worldmap_local as wm

# Legacy fixture positions remain valid; the authored TMX mine sites repeat on
# the 2 mod 6 lattice in every 36x36 chunk, not only around the map origin.
MINE_POSITIONS = ((20, 20), (26, 20), (20, 26), (14, 20))
MINING_RATE = 10
MINING_CAP = 36000
REPORT_BASE = 2_000_000_000
RETRY_SECONDS = 60


class ResourceGameplay:
    def __init__(self, adapter):
        self.a = adapter
        self.api = adapter.api
        self._templates = None

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
        con.execute('''CREATE TABLE IF NOT EXISTS local_worldmap_temples (
            uin INTEGER NOT NULL, temple_id INTEGER NOT NULL, captured_at INTEGER NOT NULL DEFAULT 0,
            cooldown INTEGER NOT NULL DEFAULT 0, guild_id INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (uin,temple_id))''')
        if 'guild_id' not in {r[1] for r in con.execute('PRAGMA table_info(local_worldmap_temples)')}:
            con.execute('ALTER TABLE local_worldmap_temples ADD COLUMN guild_id INTEGER NOT NULL DEFAULT 0')
        con.execute('''CREATE TABLE IF NOT EXISTS local_worldmap_resource_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT, uin INTEGER NOT NULL, kind INTEGER NOT NULL,
            target INTEGER NOT NULL, report BLOB NOT NULL, created_at INTEGER NOT NULL)''')
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
        return con.execute('SELECT 1 FROM local_worldmap_mines WHERE uin=? AND team_id<>0 LIMIT 1',
                           (player['uin'],)).fetchone() is not None

    def busy_cards(self, player, con=None):
        """Actual plant UIDs frozen in garrisons, without calling adapter locks."""
        if con is None:
            with self.db(player) as db:
                return self.busy_cards(player, db)
        self.ensure(con)
        rows = con.execute('SELECT formation FROM local_worldmap_mines WHERE uin=? AND team_id<>0',
                           (player['uin'],)).fetchall()
        return {wm._one(card, 2) for row in rows
                for wire, card in wm._fields(bytes(row[0])).get(1, []) if wire == 2}

    def team_busy(self, player, team_id, con=None):
        if con is None:
            with self.db(player) as db:
                return self.team_busy(player, team_id, db)
        self.ensure(con)
        return con.execute('SELECT 1 FROM local_worldmap_mines WHERE uin=? AND team_id=? LIMIT 1',
                           (player['uin'], team_id)).fetchone() is not None

    def frozen_formation(self, player, team_id):
        with self.db(player) as con:
            row = con.execute('SELECT formation FROM local_worldmap_mines WHERE uin=? AND team_id=?',
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
        handler = self.api.REQUEST_HANDLERS.get('PVZ.Cmd.Cmd_WorldMap_GetInfo_CS')
        return result + (handler(request, player) if handler else self.a.get_info(request, player))

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
        if team not in (1, wm.TEAM_ID):
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
        if team == wm.TEAM_ID:
            raw = self.a.state(con, player)['formation']
            current = {int(k): int(v) for k, v in json.loads(raw or '{}').items()}
        else:
            current = {int(r[0]): int(r[1]) for r in con.execute('SELECT pos_index,uid FROM formation '
                'WHERE uin=? AND team_id=1 AND COALESCE(owner_role_id,0)=0', (player['uin'],))}
        return current == placements

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

    def _report(self, player, target, placements, kind, team_id=wm.TEAM_ID):
        won, report, after = self.a.make_report_details(player, target, placements, team_id=team_id)
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
        placements = self.a.placements(player, team) if team in (1, wm.TEAM_ID) else {}
        if not self.valid_placements(placements):
            return wm._error(4704)
        with self.db(player) as con:
            row = self.mine(con, player, pos)
            if row['team_id'] == team:
                return wm._reply('Mine_SetOutTeam', wm._m(1, wm._position(pos)) + wm._v(2, team))
            if row['team_id']:
                return wm._error(4702)
            code = self._available(con, player, team, placements, not row['held'])
            if code:
                return wm._error(code)
            if self.cart_limit(player)[0] == 0:
                return wm._error(200)
            if row['cooldown'] > wm._now():
                return wm._error(4709)
            area, held = row['area'], row['held']
            health_before = self.health_snapshot(con, player, placements)
        formation = self.a.restored_formation(self.a.formation(player, team))
        npc = dict(name='本地演练·矿场守卫', power=1500, stage=1, coin=0)
        try:
            won, report, health_after = (True, None, {}) if held else self._report(player, npc, placements, 7, team)
        except ValueError:
            return wm._error(4704)
        formation = self.apply_health(formation, health_after)
        power = sum(int(u.get('power') or 0) for u in self.api.build_player_units(player, placements))
        with self.db(player) as con:
            row = self.mine(con, player, pos)
            code = self._available(con, player, team, placements, not row['held'])
            if code:
                return wm._error(code)
            if (row['area'] != area or row['held'] != held or row['team_id'] or row['cooldown'] > wm._now()
                    or self.health_snapshot(con, player, placements) != health_before):
                return wm._error(4705)
            if not self._owned(con, player, placements, team):
                return wm._error(2001)
            repair_paid = self.a.repair_in_transaction(con, player, placements)
            if repair_paid is None:
                return wm._error(202)
            if report:
                self.commit_health(con, player, health_after)
                self._save_report(con, player, 7, area*1000000+pos[0]*1000+pos[1], report)
                con.execute('UPDATE local_worldmap_state SET used=used+1 WHERE uin=?', (player['uin'],))
            if won:
                con.execute('UPDATE local_worldmap_mines SET held=1,team_id=?,formation=?,power=?,cooldown=0 '
                    'WHERE uin=? AND area=? AND x=? AND y=?', (team, formation, power, *self.mine_key(row)))
            else:
                con.execute('UPDATE local_worldmap_mines SET cooldown=? WHERE uin=? AND area=? AND x=? AND y=?',
                            (wm._now()+RETRY_SECONDS, *self.mine_key(row)))
        result = wm._reply('Mine_SetOutTeam', wm._m(1, wm._position(pos)) + wm._v(2, team))
        if report:
            result += wm._reply('Battle_QueryReport', wm._m(1, report))
        if repair_paid:
            result += wm._reply('SyncCoin', wm._v(1, player['coin']))
        return (result + self.mine_info(data, player) + self.a.get_formation(b'', player)
                + self.map_sync(player))

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
            row = self.mine(con, player, pos)
            # The client has separate guard and cart controls. Returning the
            # team frees its plants; only CancelMining ends the independent cart.
            con.execute("UPDATE local_worldmap_mines SET team_id=0,formation=X'',power=0 "
                        'WHERE uin=? AND area=? AND x=? AND y=?', self.mine_key(row))
        return (wm._reply('Mine_RecallTeam', wm._m(1, wm._position(pos)))
                + self.mine_info(data, player) + self.a.get_formation(b'', player) + self.map_sync(player))

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
        placements = self.a.placements(player, team) if team in (1, wm.TEAM_ID) else {}
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
