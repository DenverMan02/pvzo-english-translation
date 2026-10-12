"""Offline WorldMap/ModuleExped adapter (not the separate ExpeditionFight event).

Protocol IDs/fields come from Assets/src/PVZ/Cmd. NPCs, march durations, daily
limits, cooldowns and rewards below are deliberately local reconstruction rules,
not claims about the retired online game's economy. Each player has an isolated
training map; no other human save is attackable or modified.

Import/registration is side-effect free. Tables are additive and opened lazily
using the CURRENT store.DB_PATH. Mission result + currency + cooldown are committed
in one SQLite transaction; all reports and formations are scoped to the account.
"""
from __future__ import annotations

import contextlib
import copy
import functools
import json
import math
import sqlite3
import time

TEAM_ID = 1001  # E_WARTEAM_ATTACKMIN; do not replace the ordinary home team 1.
DAILY_LIMIT = 20
TARGET_COOLDOWN = 60
AREA_CHANGE_COOLDOWN = 86400  # Local cross-continent relocation rule, one server day.
HISTORY_LIMIT = 50
MAP_SIZES = {1: 2016, 2: 720}  # 56 or 20 repeated 36x36 TMX chunks.
MAP_SIZE = max(MAP_SIZES.values())
SUPPORTED_AREAS = (1, 2)
SPEEDUP_ITEM = 1101
MOVE_ITEM = 3122
HEAL_COST_PER_CARD = 100  # Explicit local coin repair price, not item2005 health bandages.
BUY_COSTS = (10, 20, 30)  # Local diamond prices; hardcoded client text says diamonds.
BUILD_SITES = tuple((x,y) for x in (5,11,17,23,29,35) for y in (5,11,17,23,29,35))
MARCHING, RECALLING, HOME, RETURNING = 1, 3, 4, 5
NPCS = (
    {"role_id": 6000000000001, "name": "本地演练·僵尸营地", "pos": (24, 18),
     "level": 20, "power": 1500, "coin": 500, "stage": 1},
    {"role_id": 6000000000002, "name": "本地演练·僵尸哨站", "pos": (18, 24),
     "level": 30, "power": 15000, "coin": 1500, "stage": 16},
    {"role_id": 6000000000003, "name": "本地演练·僵尸要塞", "pos": (26, 26),
     "level": 45, "power": 100000, "coin": 5000, "stage": 31},
)


def _now():
    """Replaceable clock for isolated, deterministic acceptance tests."""
    return int(time.time())


def _v(field, value):
    def varint(n):
        n = int(n)
        if n < 0 or n > 0xffffffffffffffff:
            raise ValueError("unsigned protobuf integer outside range")
        out = bytearray()
        while n > 127:
            out.append((n & 127) | 128)
            n >>= 7
        return bytes(out + bytes([n]))
    return varint(field << 3) + varint(value)


def _m(field, value):
    return _tagged_bytes(field, value)


def _tagged_bytes(field, value):
    tag = _v(field, 0)[:-1]
    return bytes([tag[0] | 2]) + tag[1:] + _v(1, len(value))[1:] + value


def _s(field, value):
    return _m(field, str(value).encode("utf-8"))


def _fields(data):
    """Strict bounded parser; unlike the legacy helper, never silently truncates."""
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("oversized protobuf message")
    out, at = {}, 0
    def read():
        nonlocal at
        n = 0
        for shift in range(0, 70, 7):
            if at >= len(data):
                raise ValueError("truncated map request")
            b = data[at]
            at += 1
            n |= (b & 127) << shift
            if not b & 128:
                if n > 0xffffffffffffffff:
                    raise ValueError("oversized varint")
                return n
        raise ValueError("overlong varint")
    while at < len(data):
        tag = read()
        field, wire = tag >> 3, tag & 7
        if not field:
            raise ValueError("invalid field")
        if wire == 0:
            value = read()
        elif wire in (1, 2, 5):
            length = read() if wire == 2 else (8 if wire == 1 else 4)
            if at + length > len(data):
                raise ValueError("truncated map field")
            value = data[at:at + length]
            at += length
        else:
            raise ValueError("invalid wire type")
        out.setdefault(field, []).append((wire, value))
    return out


def _one(data, field, wire=0, default=0):
    entries = _fields(data).get(field, [])
    if not entries:
        return default
    if len(entries) != 1 or entries[0][0] != wire:
        raise ValueError("duplicate field or wrong wire type")
    return entries[0][1]


def _pos(data, field):
    value = _one(data, field, 2, None)
    if value is None:
        raise ValueError("missing target position")
    x, y = _one(value, 1, default=-1), _one(value, 2, default=-1)
    if not (0 <= x < MAP_SIZE and 0 <= y < MAP_SIZE):
        raise ValueError("invalid target position")
    return x, y


def _viewport_pos(data, field):
    value = _one(data,field,2,None)
    if value is None:
        raise ValueError("missing viewport position")
    def signed(n):
        n &= 0xffffffff
        return n - 0x100000000 if n & 0x80000000 else n
    return signed(_one(value,1)), signed(_one(value,2))


def _position(pos):
    return _v(1, pos[0]) + _v(2, pos[1])


def _replace(data, field, value):
    out = b""
    for key, entries in _fields(data).items():
        if key == field:
            continue
        for wire, item in entries:
            if wire == 0:
                out += _v(key, item)
            elif wire == 2:
                out += _m(key, item)
            else:
                raise ValueError("unexpected fixed wire in formation")
    return out + value


def _error(code):
    return [("PVZ.Cmd.Cmd_LogicError_SC", _v(1, code))]


def _reply(name, body=b""):
    return [("PVZ.Cmd.Cmd_" + name + "_SC", body)]


class WorldMap:
    def __init__(self, api):
        self.api = api
        self.original_get = api.REQUEST_HANDLERS.get("PVZ.Cmd.Cmd_Battle_GetFormation_CS")
        self.original_set = api.REQUEST_HANDLERS.get("PVZ.Cmd.Cmd_Battle_SetFormation_CS")
        self.original_preview = api.REQUEST_HANDLERS.get("PVZ.Cmd.Cmd_Battle_GetFightInfo_CS")
        self.original_fight = api.REQUEST_HANDLERS.get("PVZ.Cmd.Cmd_Battle_Fight_CS")
        self.resources = None
        self.active_users = set()
        self.seen_users = set()
        self.notifications = {}

    @contextlib.contextmanager
    def db(self, player):
        uin = int(player.get("uin", 0))
        con = sqlite3.connect(self.api.store.DB_PATH, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM players WHERE uin=?", (uin,)).fetchone() is None:
                raise ValueError("unknown authenticated player")
            con.execute("""CREATE TABLE IF NOT EXISTS local_worldmap_state (
                uin INTEGER PRIMARY KEY, area INTEGER NOT NULL DEFAULT 1,
                x INTEGER NOT NULL DEFAULT 18, y INTEGER NOT NULL DEFAULT 18,
                day TEXT NOT NULL DEFAULT '', used INTEGER NOT NULL DEFAULT 0,
                show_null INTEGER NOT NULL DEFAULT 0,
                formation TEXT NOT NULL DEFAULT '', leader INTEGER NOT NULL DEFAULT 0,
                cooldowns TEXT NOT NULL DEFAULT '{}', wins INTEGER NOT NULL DEFAULT 0,
                default_team INTEGER NOT NULL DEFAULT 1,
                bonus INTEGER NOT NULL DEFAULT 0, bought INTEGER NOT NULL DEFAULT 0,
                health TEXT NOT NULL DEFAULT '{}',
                area_positions TEXT NOT NULL DEFAULT '{}', change_cd INTEGER NOT NULL DEFAULT 0,
                home_initialized INTEGER NOT NULL DEFAULT 0, defend_team INTEGER NOT NULL DEFAULT 0)""")
            con.execute("""CREATE TABLE IF NOT EXISTS local_worldmap_missions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, uin INTEGER NOT NULL,
                npc INTEGER NOT NULL, area INTEGER NOT NULL, team_id INTEGER NOT NULL,
                origin_x INTEGER NOT NULL, origin_y INTEGER NOT NULL,
                state INTEGER NOT NULL, begin_at INTEGER NOT NULL,
                arrive_at INTEGER NOT NULL, home_at INTEGER NOT NULL,
                period INTEGER NOT NULL, settled INTEGER NOT NULL DEFAULT 0,
                cancelled INTEGER NOT NULL DEFAULT 0, won INTEGER NOT NULL,
                reward INTEGER NOT NULL, report BLOB NOT NULL,
                formation BLOB NOT NULL, created_at INTEGER NOT NULL,
                health_after TEXT NOT NULL DEFAULT '{}')""")
            con.execute("CREATE INDEX IF NOT EXISTS local_worldmap_missions_owner "
                        "ON local_worldmap_missions(uin,id)")
            con.execute("INSERT OR IGNORE INTO local_worldmap_state(uin) VALUES (?)", (uin,))
            today = time.strftime("%Y%m%d", time.localtime(_now()))
            con.execute("UPDATE local_worldmap_state SET day=?,used=0,bonus=0,bought=0 WHERE uin=? AND day<>?",
                        (today, uin, today))
            yield con
            con.commit()
            row = con.execute("SELECT coin,present_ticket,ticket FROM players WHERE uin=?", (uin,)).fetchone()
            if row is not None:
                player["coin"],player["present_ticket"],player["ticket"] = map(int,row)
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def state(self, con, player):
        return dict(con.execute("SELECT * FROM local_worldmap_state WHERE uin=?",
                                (int(player["uin"]),)).fetchone())

    def mission(self, con, player, team_id=None):
        sql = "SELECT * FROM local_worldmap_missions WHERE uin=? AND state<>?"
        args = [int(player["uin"]),HOME]
        if team_id is not None:
            sql += " AND team_id=?"
            args.append(int(team_id))
        return con.execute(sql+" ORDER BY id DESC LIMIT 1",args).fetchone()

    def city_busy_cards(self,con,player):
        cards = set()
        for row in con.execute("SELECT formation FROM local_worldmap_missions WHERE uin=? AND state<>?",
                               (player["uin"],HOME)):
            cards.update(_one(card,2) for wire,card in _fields(bytes(row[0])).get(1,[]) if wire == 2)
        return cards

    def busy_cards(self,con,player):
        cards = self.city_busy_cards(con,player)
        if self.resources:
            cards.update(self.resources.busy_cards(player,con=con))
        return cards

    def team_busy(self,con,player,team_id):
        return bool(self.mission(con,player,team_id) or
                    (self.resources and self.resources.team_busy(player,team_id,con=con)))

    def current_placements(self,con,player,team_id):
        if team_id == TEAM_ID:
            return {int(k):int(v) for k,v in json.loads(self.state(con,player)["formation"] or '{}').items()}
        return {int(row[0]):int(row[1]) for row in con.execute(
            "SELECT pos_index,uid FROM formation WHERE uin=? AND team_id=? AND COALESCE(owner_role_id,0)=0",
            (player["uin"],team_id))}

    def health_snapshot(self,state,placements):
        health = json.loads(state["health"])
        return {str(uid):int(health.get(str(uid),10000)) for uid in placements.values()}

    def repair_cost(self,state,placements):
        return sum(value < 10000 for value in self.health_snapshot(state,placements).values())*HEAL_COST_PER_CARD

    def repair_in_transaction(self,con,player,placements):
        """Return charged coins, or None; no partial heal on insufficient money."""
        state = self.state(con,player)
        cost = self.repair_cost(state,placements)
        if cost and not con.execute("UPDATE players SET coin=coin-? WHERE uin=? AND coin>=?",
                                    (cost,player["uin"],cost)).rowcount:
            return None
        if cost:
            health = json.loads(state["health"])
            health.update({str(uid):10000 for uid in placements.values()})
            con.execute("UPDATE local_worldmap_state SET health=? WHERE uin=?",(json.dumps(health),player["uin"]))
        return cost

    def restored_formation(self,dto):
        cards = b"".join(_m(1,_replace(card,4,_v(4,_one(card,5))))
                         for wire,card in _fields(dto).get(1,[]) if wire == 2)
        return _replace(_replace(dto,1,cards),7,_v(7,0))

    def npc(self, role_id=None, pos=None):
        return next((n for n in NPCS if (n["role_id"] == role_id if role_id is not None
                                       else n["pos"] == pos)), None)

    def advance(self, player):
        """Settle every due independent team, once, in one account transaction."""
        now, changed = _now(), []
        with self.db(player) as con:
            rows = con.execute("SELECT * FROM local_worldmap_missions WHERE uin=? AND state<>? ORDER BY id",
                               (player["uin"],HOME)).fetchall()
            self.seen_users.add(int(player["uin"]))
            if not rows:
                self.active_users.discard(int(player["uin"]))
                return []
            self.active_users.add(int(player["uin"]))
            for record in rows:
                row = dict(record)
                mutated = False
                if row["state"] == MARCHING and now >= row["arrive_at"]:
                    if not row["settled"]:
                        reward = row["reward"] if row["won"] else 0
                        if reward:
                            con.execute("UPDATE players SET coin=MIN(4294967295,coin+?) WHERE uin=?",
                                        (reward,player["uin"]))
                        state = self.state(con,player)
                        cooldowns = json.loads(state["cooldowns"])
                        key = str(row["npc"])
                        cooldowns[key] = max(cooldowns.get(key,0),row["arrive_at"]+TARGET_COOLDOWN)
                        health = json.loads(state["health"])
                        health.update(json.loads(row["health_after"]))
                        con.execute("UPDATE local_worldmap_state SET cooldowns=?,health=?,wins=wins+? WHERE uin=?",
                                    (json.dumps(cooldowns),json.dumps(health),int(bool(row["won"])),player["uin"]))
                        after_hp = json.loads(row["health_after"])
                        cards = b""
                        injured = 0
                        for wire,card in _fields(bytes(row["formation"])).get(1,[]):
                            if wire != 2:
                                continue
                            ratio = int(after_hp.get(str(_one(card,2)),10000))
                            injured += int(ratio < 10000)
                            cards += _m(1,_replace(card,4,_v(4,_one(card,5)*ratio//10000)))
                        row["formation"] = _replace(_replace(bytes(row["formation"]),1,cards),7,
                                                    _v(7,injured*HEAL_COST_PER_CARD))
                        row["settled"] = 1
                    row["state"],mutated = RETURNING,True
                if row["state"] in (RETURNING,RECALLING) and now >= row["home_at"]:
                    row["state"],mutated = HOME,True
                if mutated:
                    con.execute("UPDATE local_worldmap_missions SET state=?,settled=?,formation=? WHERE id=? AND uin=?",
                                (row["state"],row["settled"],row["formation"],row["id"],player["uin"]))
                    changed.append(row)
            if self.mission(con,player) is None:
                self.active_users.discard(int(player["uin"]))
            con.execute("DELETE FROM local_worldmap_missions WHERE uin=? AND state=? AND id NOT IN "
                        "(SELECT id FROM local_worldmap_missions WHERE uin=? ORDER BY id DESC LIMIT ?)",
                        (player["uin"],HOME,player["uin"],HISTORY_LIMIT))
        if not changed:
            return []
        messages = _reply("SyncCoin",_v(1,player["coin"]))
        messages += [("PVZ.Cmd.Cmd_War_SyncHistory_SC",_m(1,self.history_dto(player,row))) for row in changed]
        messages += self.get_formation(b"",player)
        self.publish(player,messages)
        return messages

    def publish(self,player,messages):
        uin = int(player["uin"])
        version = self.notifications.get(uin,(0,[]))[0] + 1
        self.notifications[uin] = (version,list(messages))
        player["_worldmap_seen_revision"] = version

    def pending_notifications(self,player):
        event = self.notifications.get(int(player["uin"]))
        if not event or player.get("_worldmap_seen_revision") == event[0]:
            return []
        player["_worldmap_seen_revision"] = event[0]
        # Currency in a cached transition is never authoritative after another
        # connection performs a subsequent action. Read the current total.
        with contextlib.closing(sqlite3.connect(self.api.store.DB_PATH)) as con:
            row = con.execute("SELECT coin FROM players WHERE uin=?",(player["uin"],)).fetchone()
        if row:
            player["coin"] = int(row[0])
        current_formation = self.get_formation(b"",player)[0][1]
        return [(name,(_v(1,player["coin"]) if name.endswith("SyncCoin_SC") else
                      current_formation if name.endswith("Battle_GetFormation_SC") else body))
                for name,body in event[1]]

    def city_init(self, data, player):
        with self.db(player) as con:
            st = self.state(con, player)
        return _reply("City_Init", _m(1, _position((st["x"], st["y"]))) + _v(2, st["area"])
                      + _v(3, st["change_cd"]) + _v(4, st["show_null"]))

    def cell(self, player, st, npc=None):
        role = int(player.get("role_id") or player["uin"])
        name, pos, face = player.get("name") or "Player", (st["x"], st["y"]), 0
        war, until = 0, 0
        if npc:
            role, name, pos, face = npc["role_id"], npc["name"], npc["pos"], 0
            until = int(json.loads(st["cooldowns"]).get(str(role), 0))
            war = 3 if until <= _now() else 0
        return (_v(1, 1) + _m(2, _position(pos)) + _v(3, role) + _s(4, name)
                + _v(5, war) + _v(6, until if until > _now() else 0) + _v(7, face)
                + _v(8, 2 if until > _now() else 1) + _v(9, until if until > _now() else 0)
                + _v(11, 0) + _s(12, "") + _m(13, b"") + _m(14, b"")
                + _v(15, st["area"]) + _m(16, self.api.build_player_diamond(player if not npc else {}))
                + _v(30, 0) + _v(50, 0))

    def get_info(self, data, player):
        area = _one(data, 5, default=1) or 1
        if area not in SUPPORTED_AREAS:
            return _error(4703)
        with self.db(player) as con:
            st = self.state(con, player)
        # NPC fields are local to the viewed area; the player exists only in its
        # persisted area. Do not duplicate the player marker on every map.
        shown = dict(st, area=area)
        cells = [(self.cell(player, shown, n), n["pos"]) for n in NPCS]
        if self.resources:
            cells += self.resources.resource_cells(player, shown)
        # Building-site VOs (type11) are generated locally from TMX objects.
        # The network factory accepts only types1/2/3, so never transmit11.
        if area == st["area"]:
            cells.insert(0, (self.cell(player, st), (st["x"], st["y"])))
        bounds = [_viewport_pos(data, i) for i in range(1, 5) if i in _fields(data)]
        if bounds:
            lo_x, hi_x = min(p[0] for p in bounds), max(p[0] for p in bounds)
            lo_y, hi_y = min(p[1] for p in bounds), max(p[1] for p in bounds)
            cells = [(cell, pos) for cell, pos in cells if lo_x <= pos[0] <= hi_x and lo_y <= pos[1] <= hi_y]
        return _reply("WorldMap_GetInfo", b"".join(_m(1, c) for c, _ in cells))

    def get_cell(self, data, player):
        role = _one(data, 1)
        own = int(player.get("role_id") or player["uin"])
        npc = self.npc(role_id=role)
        if role not in (0, own) and npc is None:
            return _error(108)
        with self.db(player) as con:
            st = self.state(con, player)
        return _reply("WorldMap_GetCellInfo", _m(1, self.cell(player, st, npc)))

    def get_city(self, data, player):
        role = _one(data, 1)
        own = int(player.get("role_id") or player["uin"])
        npc = self.npc(role_id=role)
        if role not in (0, own) and npc is None:
            return _error(108)
        body = (_v(1, npc["role_id"] if npc else own) + _s(2, npc["name"] if npc else player.get("name", "Player"))
                + _v(3, npc["level"] if npc else int(player.get("level") or 1))
                + _v(4, 0 if npc else int(player.get("face_id") or 0)) + _v(5, 0) + _s(6, "")
                + _s(8, "") + _m(9, self.api.build_player_diamond(player if not npc else {}))
                + _v(10, 0) + _s(11, "离线演练目标，战胜后获得金币；不影响其他账号。" if npc else ""))
        return _reply("WorldMap_GetCityInfo", body)

    def enemy_list(self, data, player):
        with self.db(player) as con:
            st = self.state(con, player)
        return _reply("WorldMap_GetEnemyList", b"".join(_m(1, self.cell(player, st, n)) for n in NPCS))

    def period(self, origin, target):
        return max(20, min(300, int(math.ceil(math.hypot(target[0]-origin[0], target[1]-origin[1])*3))))

    def city_list(self, data, player):
        kind = _one(data, 1, default=1)
        if kind not in (1,2,3,4):
            return _error(107)
        start, end = _one(data, 2), _one(data, 3, default=len(NPCS))
        if start > end or end-start > 1000:
            return _error(4703)
        with self.db(player) as con:
            st = self.state(con, player)
        cds = json.loads(st["cooldowns"])
        # The local training camps fill the enemy tab. There are no fabricated
        # PvP humiliations or guild-flag relationships in the other tabs.
        targets = list(NPCS) if kind == 1 else []
        body = b""
        for n in targets[start:end]:
            enemy_state = 2 if int(cds.get(str(n["role_id"]), 0)) > _now() else 4
            dto = (_v(1, n["role_id"]) + _s(2, n["name"]) + _v(3, n["level"])
                   + _v(4, st["area"]) + _m(5, _position(n["pos"])) + _v(6, enemy_state)
                   + _v(7, self.period((st["x"],st["y"]), n["pos"])))
            body += _m(1, dto)
        return _reply("Setout_GetCityList", body + _v(2, len(targets)))

    def times(self, data, player):
        with self.db(player) as con:
            st = self.state(con, player)
        return _reply("Setout_GetSetoutTimes", _v(1, _one(data, 1, default=2))
                      + _v(2, DAILY_LIMIT) + _v(3, max(0, DAILY_LIMIT+st["bonus"]-st["used"]))
                      + _v(4, len(BUY_COSTS)) + _v(5, max(0,len(BUY_COSTS)-st["bought"])))

    def declare(self, data, player):
        npc = self.npc(role_id=_one(data, 1))
        if npc is None:
            return _error(4706)
        # Training camps are already attackable. The idempotent declaration does
        # not spend currency, reset a cooldown or create a duplicate enemy.
        with self.db(player) as con:
            st = self.state(con, player)
        if json.loads(st["cooldowns"]).get(str(npc["role_id"]), 0) > _now():
            return _error(4709)
        return _reply("War_DeclareWar", _v(1, npc["role_id"]) + _v(2, _now())) + self.enemy_list(b"", player)

    def pre_setout(self, data, player):
        war_type = _one(data,1)
        if war_type not in (2,4):
            return _error(2007)
        pos = _pos(data,2)
        with self.db(player) as con:
            st = self.state(con,player)
        target = self.npc(pos=pos)
        if target and war_type == 2:
            return _reply("War_PreSetOut",_v(1,self.period((st["x"],st["y"]),pos)))
        # All three Embattle map child modes call preWar before their dedicated
        # Mine_SetOutTeam/Temple_SetOut request. Recognize resource cells here too,
        # or the real UI fails before it can reach those correctly tested routes.
        if self.resources:
            for dto,cell_pos in self.resources.resource_cells(player,st):
                if cell_pos == pos and _one(dto,1) in (2,3):
                    return _reply("War_PreSetOut",_v(1,1))
        return _error(4703)

    def placements(self, player, team_id=TEAM_ID):
        self.api.ensure_player_cards(player)
        with self.db(player) as con:
            st = self.state(con,player)
        reserved = {int(k):int(v) for k,v in json.loads(st["formation"] or '{}').items()}
        if team_id == TEAM_ID:
            placements = reserved  # New teams start empty, never clone another team's UIDs.
        elif team_id == 1:
            if not st["home_initialized"]:
                self.api.default_formation_placements(player)
                with self.db(player) as con:
                    con.execute("UPDATE local_worldmap_state SET home_initialized=1 WHERE uin=?",(player["uin"],))
            saved = self.api.store.get_formation_with_owner(player["uin"],1)
            placements = {int(pos):int(uid) for pos,(uid,owner) in saved.items()
                          if not int(owner or 0) and int(uid) not in reserved.values()}
            # Older shared adventure code may auto-fill the home formation.
            # Remove only duplicated owned entries, preserving borrowed-friend slots.
            overlap = [int(uid) for _pos,(uid,owner) in saved.items()
                       if not int(owner or 0) and int(uid) in reserved.values()]
            if overlap:
                with self.db(player) as con:
                    con.executemany("DELETE FROM formation WHERE uin=? AND team_id=1 AND uid=? AND COALESCE(owner_role_id,0)=0",
                                    [(player["uin"],uid) for uid in overlap])
        else:
            return {}
        owned = {int(r[0]) for r in self.api.store.get_cards(player["uin"])}
        cleaned = {p:uid for p,uid in placements.items() if p in self.api._FORMATION_POS and uid in owned}
        return cleaned if self.valid_placements(cleaned) else {}

    def valid_placements(self,placements):
        return (len(placements) <= len(self.api._FORMATION_POS)
                and set(placements) <= set(self.api._FORMATION_POS)
                and len(set(placements.values())) == len(placements)
                and all(int(uid) > 0 for uid in placements.values()))

    def formation(self, player, team_id=TEAM_ID):
        with self.db(player) as con:
            mission = self.mission(con, player,team_id)
            st = self.state(con, player)
        if mission:
            return _replace(bytes(mission["formation"]), 6, _v(6, 1))
        if self.resources:
            frozen = self.resources.frozen_formation(player,team_id)
            if frozen is not None:
                return _replace(frozen,6,_v(6,1))
        placements = self.placements(player, team_id)
        dto = self.api.build_formation_info(player, team_id, placements)
        dto = _replace(dto, 6, _v(6, 0))
        dto = _replace(dto, 8, _v(8,int(st["defend_team"] == team_id)))
        if team_id == TEAM_ID and st["leader"] in placements.values():
            dto = _replace(dto, 3, _v(3, st["leader"]))
        health = json.loads(st["health"])
        cards,injured = b"",0
        for wire,card in _fields(dto).get(1,[]):
            uid,maxhp = _one(card,2),_one(card,5)
            ratio = max(0,min(10000,int(health.get(str(uid),10000))))
            injured += int(ratio < 10000)
            cards += _m(1,_replace(card,4,_v(4,maxhp*ratio//10000)))
        dto = _replace(dto,1,cards)
        return _replace(dto,7,_v(7,injured*HEAL_COST_PER_CARD))

    def get_formation(self, data, player):
        request_team = _one(data,1,default=0)
        home,map_team = self.formation(player,1),self.formation(player,TEAM_ID)
        with self.db(player) as con:
            st = self.state(con,player)
        # Every UID occurs in at most one network team. AS getCardStatus scans
        # the first matching team, so duplicated home/out copies hide busy cards.
        body = (_m(1,map_team if request_team == TEAM_ID else home)
                + _m(2,home)+_m(2,map_team)+_v(3,2)+_v(4,st["default_team"]))
        return _reply("Battle_GetFormation",body)

    def set_formation(self, data, player):
        formation = _one(data, 1, 2, b"")
        team = _one(formation, 5, default=1) or 1
        if team not in (1,TEAM_ID):
            return _error(4701)
        placements, owned = {}, {int(r[0]) for r in self.api.store.get_cards(player["uin"])}
        for wire, card in _fields(formation).get(1, []):
            if wire != 2:
                return _error(2001)
            pos, uid = _one(card, 1), _one(card, 2)
            if (_one(card, 3) not in (0, int(player.get("role_id") or player["uin"]))
                    or pos not in self.api._FORMATION_POS or uid not in owned
                    or pos in placements or uid in placements.values()):
                return _error(2001)
            placements[pos] = uid
        if len(placements) > len(self.api._FORMATION_POS):
            return _error(2001)
        leader = _one(formation, 3)
        if leader and leader not in placements.values():
            return _error(2001)
        with self.db(player) as con:
            if self.team_busy(con,player,team) or set(placements.values()) & self.busy_cards(con,player):
                return _error(4702)
            live_owned = {int(r[0]) for r in con.execute("SELECT uid FROM cards WHERE uin=?",(player["uin"],))}
            if not set(placements.values()) <= live_owned:
                return _error(2001)
            st = self.state(con,player)
            other_map = {int(k):int(v) for k,v in json.loads(st["formation"] or '{}').items()}
            if st["defend_team"] == team and not placements:
                return _error(5202)
            if team == 1:
                other_map = {p:uid for p,uid in other_map.items() if uid not in placements.values()}
                if st["defend_team"] == TEAM_ID and not other_map:
                    return _error(5202)
                if not placements and not other_map:
                    return _error(4715)
                con.execute("UPDATE local_worldmap_state SET formation=?,leader=?,home_initialized=1 WHERE uin=?",
                            (json.dumps(other_map),st["leader"] if st["leader"] in other_map.values() else 0,player["uin"]))
                con.execute("DELETE FROM formation WHERE uin=? AND team_id=1",(player["uin"],))
                con.executemany("INSERT INTO formation(uin,team_id,pos_index,uid,owner_role_id) VALUES(?,1,?,?,0)",
                                [(player["uin"],pos,uid) for pos,uid in placements.items()])
                if leader:
                    con.execute("INSERT INTO formation_leader(uin,team_id,uid) VALUES(?,1,?) "
                                "ON CONFLICT(uin,team_id) DO UPDATE SET uid=excluded.uid",(player["uin"],leader))
            else:
                home_uids = {int(r[0]) for r in con.execute("SELECT uid FROM formation WHERE uin=? AND team_id=1 "
                            "AND COALESCE(owner_role_id,0)=0",(player["uin"],))}
                if st["defend_team"] == 1 and not (home_uids-set(placements.values())):
                    return _error(5202)
                if not placements and not home_uids:
                    return _error(4715)
                con.executemany("DELETE FROM formation WHERE uin=? AND team_id=1 AND uid=? AND COALESCE(owner_role_id,0)=0",
                                [(player["uin"],uid) for uid in placements.values()])
                con.execute("UPDATE local_worldmap_state SET formation=?,leader=?,home_initialized=1 WHERE uin=?",
                            (json.dumps(placements),leader,player["uin"]))
        return self.get_formation(_v(1,team),player)

    def enemies(self, npc):
        self.api.load_battle_cards()
        return self.api._tr_enemy_squad(npc["power"], npc["stage"])

    def make_report_details(self, player, npc, placements):
        if not placements or not self.valid_placements(placements):
            raise ValueError("invalid or duplicated map formation")
        left = self.api.build_player_units(player, placements)
        with self.db(player) as con:
            selected_leader = self.state(con,player)["leader"]
        if selected_leader in placements.values():
            for unit in left:
                unit["is_leader"] = unit["uid"] == selected_leader
        # Actual Embattle checkPlantHP confirmation invokes SetOut directly.
        # All dispatches therefore fight restored units; their caller atomically
        # pays the quoted repair cost when committing the operation.
        right = self.enemies(npc)
        if not left or not right:
            raise ValueError("empty or fully injured combat formation")
        # Current combat resolves outcome and replay in the same simulation.
        # Re-running simulate_battle would also lose the authoritative final
        # health after healing, shields and temporary maximum-HP changes.
        won, _lp, _rp, rounds = self.api.run_battle(
            left, right, leader_sides=("left",), stop_on_elimination=True)
        after = {str(u["uid"]):max(0,min(10000,int(math.ceil(
                    int(u.get("final_hp",u["hp"]))*10000 /
                    max(1,int(u.get("final_max_hp",u.get("max_hp",u["hp"]))))))))
                 for u in left}
        report = self.api.build_fight_report(player, 0, 0, 0, won,
                    [(1,npc["coin"])] if won else [], False, left, right,
                    self.api.encode_fight_replay(rounds), fight_type=3, side_types=(8,9))
        right_dto = _one(report, 3, 2, b"")
        right_dto = _replace(right_dto, 6, _s(6, npc["name"]))
        return bool(won), _replace(report, 3, _m(3, right_dto)), after

    def make_report(self,player,npc,placements):
        won,report,_health = self.make_report_details(player,npc,placements)
        return won,report

    def setout(self, data, player):
        if _one(data, 1) != 2:
            return _error(2007)
        team_id = _one(data, 3)
        if team_id not in (1, TEAM_ID):
            return _error(4701)
        npc = self.npc(pos=_pos(data, 2))
        if not npc:
            return _error(4703)
        placements = self.placements(player, team_id)
        if not placements:
            return _error(4704)
        with self.db(player) as con:
            st = self.state(con, player)
            if self.team_busy(con,player,team_id) or set(placements.values()) & self.busy_cards(con,player):
                return _error(4702)
            if st["defend_team"] == team_id:
                return _error(4721)
            if st["used"] >= DAILY_LIMIT+st["bonus"]:
                return _error(4708)
            if json.loads(st["cooldowns"]).get(str(npc["role_id"]),0) > _now():
                return _error(4709)
            fingerprint = st["formation"] if team_id == TEAM_ID else json.dumps(placements,sort_keys=True)
            repair_cost = self.repair_cost(st,placements)
            if int(con.execute("SELECT coin FROM players WHERE uin=?",(player["uin"],)).fetchone()[0]) < repair_cost:
                return _error(202)
            health_before = self.health_snapshot(st,placements)
            origin_before = (st["area"],st["x"],st["y"])
            leader_before = st["leader"] if team_id == TEAM_ID else (con.execute(
                "SELECT uid FROM formation_leader WHERE uin=? AND team_id=1",(player["uin"],)).fetchone() or [0])[0]
        # No database write lock is held while the existing battle engine runs.
        try:
            won, report, health_after = self.make_report_details(player, npc, placements)
        except ValueError:
            return _error(4704)
        formation = self.restored_formation(self.formation(player,team_id))
        now = _now()
        with self.db(player) as con:
            st = self.state(con, player)
            if self.team_busy(con,player,team_id) or set(placements.values()) & self.busy_cards(con,player):
                return _error(4702)
            if st["defend_team"] == team_id:
                return _error(4721)
            if st["used"] >= DAILY_LIMIT+st["bonus"]:
                return _error(4708)
            if json.loads(st["cooldowns"]).get(str(npc["role_id"]),0) > now:
                return _error(4709)
            if self.health_snapshot(st,placements) != health_before or (st["area"],st["x"],st["y"]) != origin_before:
                return _error(4705)
            leader_now = st["leader"] if team_id == TEAM_ID else (con.execute(
                "SELECT uid FROM formation_leader WHERE uin=? AND team_id=1",(player["uin"],)).fetchone() or [0])[0]
            if leader_now != leader_before:
                return _error(4705)
            if team_id == TEAM_ID and st["formation"] != fingerprint:
                return _error(4705)
            if team_id == 1:
                current = {int(r[0]):int(r[1]) for r in con.execute(
                    "SELECT pos_index,uid FROM formation WHERE uin=? AND team_id=1 AND COALESCE(owner_role_id,0)=0",
                    (player["uin"],))}
                if json.dumps(current,sort_keys=True) != fingerprint:
                    return _error(4705)
            # Recheck ownership against SQL under the dispatch lock (a recycled
            # card must not be submitted through a stale formation/session).
            owned = {int(r[0]) for r in con.execute("SELECT uid FROM cards WHERE uin=?", (player["uin"],))}
            if not set(placements.values()) <= owned:
                return _error(2001)
            paid_repair = self.repair_in_transaction(con,player,placements)
            if paid_repair is None:
                return _error(202)
            period = self.period((st["x"], st["y"]), npc["pos"])
            cur = con.execute("INSERT INTO local_worldmap_missions "
                "(uin,npc,area,team_id,origin_x,origin_y,state,begin_at,arrive_at,home_at,period,won,reward,report,formation,created_at,health_after) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (player["uin"],npc["role_id"],st["area"],team_id,st["x"],st["y"],MARCHING,now,now+period,
                 now+2*period,period,int(won),npc["coin"] if won else 0,report,formation,now,json.dumps(health_after)))
            con.execute("UPDATE local_worldmap_state SET used=used+1 WHERE uin=?", (player["uin"],))
            mission = dict(con.execute("SELECT * FROM local_worldmap_missions WHERE id=?", (cur.lastrowid,)).fetchone())
        self.active_users.add(int(player["uin"]))
        self.seen_users.add(int(player["uin"]))
        return (_reply("War_SetOut", _v(1, team_id))
                + (_reply("SyncCoin",_v(1,player["coin"])) if paid_repair else [])
                + _reply("War_SyncHistory", _m(1, self.history_dto(player, mission)))
                + self.get_formation(b"", player) + self.times(_v(1,2), player))

    def target_dto(self, role, name, pos):
        role_info = _v(1,0) + _s(2,"") + _v(3,role) + _s(4,name) + _m(5,_position(pos))
        return _v(1,1) + _v(2,0) + _m(3,_position(pos)) + _m(4,role_info)

    def history_dto(self, player, row):
        n = self.npc(role_id=row["npc"])
        own = int(player.get("role_id") or player["uin"])
        origin = self.target_dto(own,player.get("name") or "Player",(row["origin_x"],row["origin_y"]))
        target = self.target_dto(n["role_id"],n["name"],n["pos"])
        begin = row["begin_at"] if row["state"] == MARCHING else row["arrive_at"]
        end = row["arrive_at"] if row["state"] == MARCHING else row["home_at"]
        return (_v(1,2) + _m(2,origin) + _m(3,target) + _v(4,row["team_id"]) + _v(5,row["id"])
                + _v(6,begin) + _v(7,end) + _v(8,row["state"])
                + _v(9,(2 if row["won"] else 1) if row["settled"] else 0)
                + _v(10,row["id"] if row["settled"] and not row["cancelled"] else 0)
                + _v(11,max(1,end-begin)) + _v(12,row["area"]) + _v(13,1) + _v(14,_now()))

    def history(self, name, data, player):
        if name == "War_GetHistory":
            history_type = _one(data,1,default=1)
            if history_type not in (1,2,3):
                return _error(107)
            if history_type != 1:
                # Guild/Plant-War tabs must not mislabel private NPC training
                # missions as real shared guild attacks.
                return _reply(name)
        with self.db(player) as con:
            rows = con.execute("SELECT * FROM local_worldmap_missions WHERE uin=? ORDER BY id DESC LIMIT ?",
                               (player["uin"], HISTORY_LIMIT)).fetchall()
        if name in ("War_GetMapHistory","War_GetSimpleHistory"):
            rows = [r for r in rows if r["state"] != HOME]
        return _reply(name,b"".join(_m(1,self.history_dto(player,r)) for r in rows))

    def query_report(self, data, player):
        report_id = _one(data,1)
        with self.db(player) as con:
            row = con.execute("SELECT report FROM local_worldmap_missions WHERE id=? AND uin=? "
                              "AND settled=1 AND cancelled=0", (report_id,player["uin"])).fetchone()
        if row:
            return _reply("Battle_QueryReport",_m(1,bytes(row[0])))
        resource_report = self.resources.query_report(report_id,player) if self.resources else None
        return _reply("Battle_QueryReport",_m(1,resource_report)) if resource_report else _error(2005)

    def recall(self, data, player):
        team_id = _one(data,1)
        if team_id not in (1,TEAM_ID) or _one(data,2,default=1) != 1:
            return _error(4701)
        now = _now()
        with self.db(player) as con:
            row = self.mission(con,player,team_id)
            if row is None or row["team_id"] != team_id or row["state"] != MARCHING:
                return _error(4705)
            duration = max(1,min(row["period"],now-row["begin_at"]))
            con.execute("UPDATE local_worldmap_missions SET state=?,cancelled=1,reward=0,arrive_at=?,home_at=? WHERE id=?",
                        (RECALLING,now,now+duration,row["id"]))
            row = dict(con.execute("SELECT * FROM local_worldmap_missions WHERE id=?",(row["id"],)).fetchone())
        return _reply("War_RecallTeam") + _reply("War_SyncHistory",_m(1,self.history_dto(player,row)))

    def speedup(self, data, player):
        team_id = _one(data,1)
        if team_id not in (1,TEAM_ID):
            return _error(4701)
        if _one(data,2) != SPEEDUP_ITEM:
            return _error(204)
        with self.db(player) as con:
            row = self.mission(con,player,team_id)
            if row is None or row["team_id"] != team_id or row["state"] not in (MARCHING,RETURNING,RECALLING):
                return _error(4705)
            column = "arrive_at" if row["state"] == MARCHING else "home_at"
            remaining = row[column]-_now()
            if remaining < 15:
                return _error(4722)
            spent = con.execute("UPDATE items SET count=count-1 WHERE uin=? AND item_id=? AND count>=1",
                                (player["uin"],SPEEDUP_ITEM)).rowcount
            if not spent:
                return _error(204)
            cut = max(1,int(math.ceil(remaining*0.1)))
            if column == "arrive_at":
                con.execute("UPDATE local_worldmap_missions SET arrive_at=arrive_at-?,home_at=home_at-? WHERE id=?",
                            (cut,cut,row["id"]))
            else:
                con.execute("UPDATE local_worldmap_missions SET home_at=home_at-? WHERE id=?",(cut,row["id"]))
            count = con.execute("SELECT count FROM items WHERE uin=? AND item_id=?",(player["uin"],SPEEDUP_ITEM)).fetchone()[0]
            row = dict(con.execute("SELECT * FROM local_worldmap_missions WHERE id=?",(row["id"],)).fetchone())
        return (_reply("War_SpeedUpTeam") + _reply("War_SyncHistory",_m(1,self.history_dto(player,row)))
                + _reply("Item_Update",_m(1,self.api.build_item_dto(SPEEDUP_ITEM,count))))

    def preview(self, data, player):
        if self.resources:
            result = self.resources.preview(data,player)
            if result is not None:
                return result
        target = _one(data,1,2,b"")
        if _one(target,1) != 3:
            return self.original_preview(data,player)
        npc = self.npc(role_id=_one(target,8))
        if npc is None:
            return _error(4703)
        enemies = self.enemies(npc)
        body = _m(1,self.formation(player))
        for u in enemies:
            body += _m(4,self.api.build_enemy_card(u["pos"],u["card_id"],u["hp"],u["level"],u["is_leader"],u["power"]))
        return _reply("Battle_GetFightInfo",body+_v(5,sum(u["power"] for u in enemies)))

    def fight(self, data, player):
        if self.resources:
            result = self.resources.fight(data,player)
            if result is not None:
                return result
        target = _one(data,1,2,b"")
        # World map attacks must travel and settle via War_SetOut. Never let an
        # arbitrary generic Battle_Fight request become a free fuben fight.
        if _one(target,1) == 3:
            return _error(4705)
        return self.original_fight(data,player)

    def buy_times(self,data,player):
        with self.db(player) as con:
            st = self.state(con,player)
            # Match the real UI: refill whenever remaining is below the fixed
            # capacity, including19/20. At20/20 a repeated request cannot spend.
            remaining = DAILY_LIMIT+st["bonus"]-st["used"]
            if remaining >= DAILY_LIMIT or st["bought"] >= len(BUY_COSTS):
                return _error(4708)
            cost = BUY_COSTS[st["bought"]]
            wallet = con.execute("SELECT present_ticket,ticket FROM players WHERE uin=?",(player["uin"],)).fetchone()
            gift,gold = max(0,int(wallet[0])),max(0,int(wallet[1]))
            if gift+gold < cost:
                return _error(208)
            gift_cost = min(gift,cost)
            con.execute("UPDATE players SET present_ticket=present_ticket-?,ticket=ticket-? WHERE uin=?",
                        (gift_cost,cost-gift_cost,player["uin"]))
            con.execute("UPDATE local_worldmap_state SET bought=bought+1,bonus=bonus+1 WHERE uin=?",
                        (player["uin"],))
            left = len(BUY_COSTS)-st["bought"]-1
        return (_reply("Setout_BuyMapWarSetoutTimes",_v(1,len(BUY_COSTS))+_v(2,left))
                + _reply("SyncPresentTicket",_v(1,player["present_ticket"]))
                + _reply("SyncTicket",_v(1,player["ticket"])))

    def fixed_move(self,data,player):
        area,pos = _one(data,1),_pos(data,2)
        if (area not in SUPPORTED_AREAS or pos[0] % 6 != 5 or pos[1] % 6 != 5
                or not (0 <= pos[0] < MAP_SIZES[area]-1 and 0 <= pos[1] < MAP_SIZES[area]-1)
                or self.npc(pos=pos) is not None):
            return _error(4703)
        if self.resources and any(t["pos"] == pos and t["area"] == area for t in self.resources.templates()[0]):
            return _error(4703)
        if int(player.get("level") or 1) < (30 if area == 2 else 20):
            return _error(200)
        with self.db(player) as con:
            st = self.state(con,player)
            if self.mission(con,player) or (self.resources and self.resources.busy(player,con=con)):
                return _error(4702)
            if area == st["area"] and pos == (st["x"],st["y"]):
                # Repeated network delivery is an idempotent acknowledgement.
                return _reply("City_FixedMove",_v(1,area)+_m(2,_position(pos)))
            towns = getattr(self,"local_city_display",None)
            if towns and towns.occupied(con,player,area,pos):
                return _error(4703)
            if not con.execute("UPDATE items SET count=count-1 WHERE uin=? AND item_id=? AND count>0",
                               (player["uin"],MOVE_ITEM)).rowcount:
                return _error(204)
            positions = json.loads(st["area_positions"])
            positions[str(st["area"])] = [st["x"],st["y"]]
            positions[str(area)] = list(pos)
            con.execute("UPDATE local_worldmap_state SET area=?,x=?,y=?,area_positions=? WHERE uin=?",
                        (area,pos[0],pos[1],json.dumps(positions),player["uin"]))
            count = con.execute("SELECT count FROM items WHERE uin=? AND item_id=?",(player["uin"],MOVE_ITEM)).fetchone()[0]
        return (_reply("City_FixedMove",_v(1,area)+_m(2,_position(pos)))
                + _reply("Item_Update",_m(1,self.api.build_item_dto(MOVE_ITEM,count)))
                + self.city_init(b"",player))

    def recover_hp(self,data,player):
        team = _one(data,1)
        if team not in (1,TEAM_ID):
            return _error(4701)
        placements = self.placements(player,team)
        with self.db(player) as con:
            if self.team_busy(con,player,team) or set(placements.values()) & self.busy_cards(con,player):
                return _error(4705)
            if self.current_placements(con,player,team) != placements:
                return _error(4705)
            st = self.state(con,player)
            health = json.loads(st["health"])
            injured = [str(uid) for uid in placements.values() if int(health.get(str(uid),10000)) < 10000]
            if not injured:
                return _error(4711)
            cost = len(injured)*HEAL_COST_PER_CARD
            if not con.execute("UPDATE players SET coin=coin-? WHERE uin=? AND coin>=?",
                               (cost,player["uin"],cost)).rowcount:
                return _error(202)
            for uid in injured:
                health[uid] = 10000
            con.execute("UPDATE local_worldmap_state SET health=? WHERE uin=?",(json.dumps(health),player["uin"]))
        return (_reply("War_RecoverHp") + _reply("SyncCoin",_v(1,player["coin"]))
                + self.get_formation(_v(1,team),player))

    def change_area(self, data, player):
        area = _one(data,1)
        if area not in SUPPORTED_AREAS:
            return _error(4703)
        if area == 2 and int(player.get("level") or 1) < 30:
            return _error(602)
        with self.db(player) as con:
            if self.mission(con,player) or (self.resources and self.resources.busy(player,con=con)):
                return _error(4702)
            st = self.state(con,player)
            if area == st["area"]:
                return _reply("City_ChangeArea",_v(1,st["change_cd"]))
            if st["change_cd"] > _now():
                return _error(603)
            positions = json.loads(st["area_positions"])
            positions[str(st["area"])] = [st["x"],st["y"]]
            pos = positions.get(str(area),[18,18])
            towns = getattr(self,"local_city_display",None)
            if towns:
                pos = towns.destination(con,player,area,pos)
            end = _now()+AREA_CHANGE_COOLDOWN
            con.execute("UPDATE local_worldmap_state SET area=?,x=?,y=?,area_positions=?,change_cd=? WHERE uin=?",
                        (area,pos[0],pos[1],json.dumps(positions),end,player["uin"]))
        # ChangeArea_SC changes only the button's cooldown. The native random-
        # relocation message is the route that updates myPos/myAreaId and view.
        return (_reply("City_ChangeArea",_v(1,end))
                + _reply("City_RandomMove",_m(1,_position(pos))+_v(2,area)+_v(3,0))
                + self.city_init(b"",player))

    def set_defend(self,data,player,cancel=False):
        team = _one(data,1)
        if team not in (1,TEAM_ID):
            return _error(4701)
        placements = self.placements(player,team)
        with self.db(player) as con:
            state = self.state(con,player)
            if cancel:
                if state["defend_team"] not in (0,team):
                    return _error(4701)
                con.execute("UPDATE local_worldmap_state SET defend_team=0 WHERE uin=?",(player["uin"],))
            else:
                if not placements:
                    return _error(5202)
                if self.current_placements(con,player,team) != placements:
                    return _error(4705)
                if self.team_busy(con,player,team) or set(placements.values()) & self.busy_cards(con,player):
                    return _error(4702)
                con.execute("UPDATE local_worldmap_state SET defend_team=? WHERE uin=?",(team,player["uin"]))
        # These commands define no _SC classes; synchronize the authoritative
        # formation list instead of inventing an unreadable response.
        return self.get_formation(b"",player)

    def set_default(self, data, player):
        team_id = _one(data,1)
        if team_id not in (1,TEAM_ID):
            return _error(4701)
        with self.db(player) as con:
            con.execute("UPDATE local_worldmap_state SET default_team=? WHERE uin=?",(team_id,player["uin"]))
        return _reply("Battle_SetDefaultTeam",_v(1,team_id))

    def set_null(self, data, player):
        show = _one(data,1)
        if show not in (0,1):
            raise ValueError("invalid boolean")
        with self.db(player) as con:
            con.execute("UPDATE local_worldmap_state SET show_null=? WHERE uin=?",(show,player["uin"]))
        return []  # No Cmd_City_SetNullShow_SC exists.

    def guard(self, fn, response_name=None):
        @functools.wraps(fn)
        def wrapped(data,player):
            try:
                if len(data) > 65536:
                    raise ValueError("oversized map request")
                _fields(data)
                updates = self.advance(player)
                result = fn(data,player)
                if isinstance(result, bytes):
                    # Only delegated ordinary handlers may return raw bytes.
                    return result if not updates else updates + [("PVZ.Cmd.Cmd_" + response_name + "_SC", result)]
                if (response_name in {"War_SetOut","War_RecallTeam","War_SpeedUpTeam",
                        "Battle_SetFormation","Battle_SetDefaultTeam","Battle_SetDefendTeam",
                        "Battle_CancelDefendTeam","City_FixedMove",
                        "City_ChangeArea","War_RecoverHp"}
                        and not any(name.endswith("LogicError_SC") for name,_body in result)):
                    self.publish(player,result)
                return updates + result
            except (ValueError,OverflowError):
                return _error(4703)
            except sqlite3.Error:
                self.api.log.exception("worldmap database operation rolled back")
                return _error(3001)
        return wrapped

    def handlers(self):
        routes = {
            "City_Init": self.city_init, "City_SetNullShow": self.set_null,
            "City_ChangeArea": self.change_area,
            "City_FixedMove": self.fixed_move,
            "WorldMap_GetInfo": self.get_info, "WorldMap_GetCellInfo": self.get_cell,
            "WorldMap_GetCityInfo": self.get_city, "WorldMap_GetEnemyList": self.enemy_list,
            "WorldMap_GetGuildMemberList": lambda d,p: _reply("WorldMap_GetGuildMemberList"),
            "WorldMap_OperateStatInfo": lambda d,p: [],
            "Setout_GetCityList": self.city_list, "Setout_GetSetoutTimes": self.times,
            "Setout_GetTempleList": lambda d,p: _reply("Setout_GetTempleList",_v(2,0)),
            "Setout_BuyMapWarSetoutTimes": self.buy_times,
            "War_DeclareWar": self.declare, "War_PreSetOut": self.pre_setout,
            "War_SetOut": self.setout, "War_RecallTeam": self.recall,
            "War_SpeedUpTeam": self.speedup, "War_RecoverHp": self.recover_hp,
            "War_GetDeclareWarStat": lambda d,p: _reply("War_GetDeclareWarStat",_v(1,DAILY_LIMIT)+_v(2,DAILY_LIMIT)),
            "Battle_GetFormation": self.get_formation, "Battle_SetFormation": self.set_formation,
            "Battle_GetFightInfo": self.preview, "Battle_Fight": self.fight,
            "Battle_QueryReport": self.query_report,
            "Battle_SetDefaultTeam": self.set_default,
            "Battle_SetDefendTeam": self.set_defend,
            "Battle_CancelDefendTeam": lambda d,p: self.set_defend(d,p,True),
            "City_GetTownDurability": lambda d,p: _reply("City_GetTownDurability",_v(1,100)+_v(2,100)+_v(3,0)),
            # Online shared-world services are deliberately not fabricated. Their
            # read-only lists may be empty, but mutations must not fake success.
            "Outpost_GetAllyTeam": lambda d,p: _reply("Outpost_GetAllyTeam"),
            "Outpost_ReturnAllyTeam": lambda d,p: _error(12600),
            "Outpost_SetFormation": lambda d,p: _error(12600),
            "Flag_GetList": lambda d,p: _reply("Flag_GetList"),
            "Flag_Close": lambda d,p: _error(12600),
            "Battle_SetAutoRestoreDefenceTeamHealthHp": lambda d,p: _error(12600),
        }
        for name in ("War_GetHistory","War_GetMapHistory","War_GetSimpleHistory"):
            routes[name] = functools.partial(self.history,name)
        return {"PVZ.Cmd.Cmd_"+name+"_CS": self.guard(fn,name) for name,fn in routes.items()}


def make_handlers(api):
    adapter = WorldMap(api)
    api._worldmap_adapter = adapter
    return adapter.handlers()


def poll(api,player):
    adapter = getattr(api,"_worldmap_adapter",None)
    if not adapter or not player:
        return []
    uin = int(player["uin"])
    if uin not in adapter.seen_users:
        # First connection poll is read-only if this account never used the map.
        # Opening the game must not create map tables or rewrite an idle save.
        with contextlib.closing(sqlite3.connect(api.store.DB_PATH)) as con:
            exists = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='local_worldmap_missions'").fetchone()
            if exists and con.execute("SELECT 1 FROM local_worldmap_missions WHERE uin=? AND state<>? LIMIT 1",(uin,HOME)).fetchone():
                adapter.active_users.add(uin)
        adapter.seen_users.add(uin)
    updates = adapter.advance(player) if uin in adapter.active_users else []
    return updates or adapter.pending_notifications(player)
