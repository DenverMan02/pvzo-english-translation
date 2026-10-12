#!/usr/bin/env python3
"""
Persistent player store (SQLite) for the PvZ Online server.

One row per uin = one save file. On first login for a uin we create a player with
sensible starter defaults; on later logins we load the same row, so state persists
across sessions. This is the foundation the game systems (cards, levels, quests…)
will read from and write back to.

Keyed by `uin` (passed to the client via the ?uin= flashvar), so a new number = a
new account.
"""

import contextlib
import os
import sqlite3
import threading
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "players.db")

# Actor columns and their new-player defaults. Add columns here as systems grow;
# _ensure_columns() migrates existing DBs automatically.
DEFAULTS = {
    "role_id": 0,            # set to uin on create
    "name": "Player",
    "level": 1,
    "exp": 0,
    "coin": 500,
    "sun": 10,
    "energy": 50,
    "friend_point": 0,
    "ticket": 0,            # gold diamond
    "present_ticket": 0,    # gift diamond
    "fame": 0,
    "buy_energy_num": 0,    # diamond energy-buys used today (of the daily limit; escalating cost)
    "buy_energy_day": "",   # YYYYMMDD buy_energy_num belongs to (reset on a new day)
    "energy_time": 0,       # unix ts the `energy` value was last computed (drives time-based regen)
    "foster_ticket": 0,
    "lucky": 0,
    "contrib_val": 0,
    "light": 0,
    "last_pay_time": 0,
    "prestige": 0,
    "zone_id": 0,
    "td_stage": 1,          # latestTDLevel — stageId=1 keeps the town routing happy
    "td_level": 1,
    "td_sub": 1,
    "face_id": 0,
    "avatar_plant_id": 1200,  # peashooter
    "avatar_skin_id": 0,
    "reg_time": 0,          # set to now on create
    "server_open_time": 0,
    "created_at": 0,
    "last_login": 0,
    # --- daily sign-in (Welfare Hall) ---
    "sign_month": 0,        # YYYYMM the current sign_total belongs to (reset on a new month)
    "sign_total": 0,        # days signed this month (drives which calendar cells are claimed)
    "sign_last": 0,         # last sign-in date as YYYYMMDD (dedupe: one sign-in per day)
    "welfare_level_taken": "",  # comma-separated level-chest thresholds already claimed
    # --- zen garden (阳光房) ---
    "garden_exp": 0,        # accumulated garden exp (converts to exp beans via TakeBean)
    "garden_bean": 0,       # exp beans banked in the garden
    "garden_seeds_granted": 0,  # 1 once the one-time starter crop-seed stash has been given
    "garden_goodwill": 0,   # lifetime sunflower exp harvested -> pet-house 好感度 (unlocks pets)
    "garden_pet_data": "",  # JSON: owned/active garden pets and their temporary expiry times
    "garden_autofertilizer": 0,  # selected fertilizer template id for auto-use
    "garden_autouse": 0,          # automatically apply the selected fertilizer to new plants
    "garden_kettlelimit": 10,     # remaining golden-watering-can uses for the current day
    "garden_kettle_day": "",      # YYYYMMDD that garden_kettlelimit belongs to
    "card_market_granted": 0,  # 1 once the one-time card-pack draw-ticket stash has been given
    "mystery_luck": 0,         # Mystery Card Pack pity counter (0..iMysteryCardLuckValueMax)
    "mystery_granted": 0,      # 1 once the one-time MysteriousTicket stash has been given
    "plant_coin_granted": 0,   # 1 once the one-time 植物币/神奇植物币 stash has been given (神奇兑换)
    "plant_shop_page": 0,      # 神奇兑换 (ExchangeStore 5): which page of wares the 道具兑换 window
                               #   shows. The client's panel holds a FIXED six-slot Vector, so the
                               #   server sends six entries at a time and 刷新 advances this page.
    # --- 傀儡 (puppets — the "Dummy" module: tamed zombies that fight beside the plants) ---
    # 傀儡战境 spends 能量 (item 42), 傀儡技能 spends 技能点 (item 41); both regenerate over time, so
    # each keeps its current value plus the timestamp the last regen tick was applied at.
    "puppet_energy": 0,
    "puppet_energy_ts": 0,
    "puppet_skill_val": 0,
    "puppet_skill_val_ts": 0,
    "puppet_lineup": "",        # CSV of puppet baseIds in round order (round 1..5, see the lineup panel)
    "puppet_easy_chapter": 0,   # last FULLY cleared 普通 (mode 1) chapter
    "puppet_hard_chapter": 0,   # last FULLY cleared 精英 (mode 2) chapter
    "puppet_fuben_formation_set": 0,  # 1 once the dedicated plant lineup is explicitly saved, even if empty
    "puppet_fuben_day": "",     # YYYYMMDD the elite-mode daily fight counter belongs to
    "puppet_extract": "",       # CSV "groupId:freeUsedAtUnix:extractCount:pity" per 捕捉 group
    "puppet_granted": 0,        # 1 once the one-time starter stash (捕捉器/能量/培养液) has been given
    "puppet_buy_day": "",       # YYYYMMDD the two market counters below belong to
    "puppet_energy_buy": 0,     # 能量 purchases today (dummyBuyFubenEnemgyInfo: Nth price)
    "puppet_skill_buy": 0,      # 技能点 purchases today (dummyBuySkillPointInfo: Nth price)
    "mystery_board": "",       # persisted board "weekly,daily1,daily2,daily3" (rerolls only on refresh)
    # --- laboratory (实验室) ---
    "lab_bottles": "",         # per-bottle overrides "labId:level:status;..." (only bottles that
                               #   differ from the fresh default: type-1 basics produced@0, rest locked@base)
    "lab_mats_granted": 0,     # 1 once the one-time starter lab-material stash has been given
    # --- world tree (世界树/树屋) ---
    "wt_level": 1,             # tree level (WTTemplateCE row id; 1..999)
    "wt_exp": 0,               # exp toward the next level (auto-levels when >= needExp)
    "wt_free_used": 0,         # normal/free fosters used today (of 5 free/day)
    "wt_adv_used": 0,          # half-price advanced fosters used today (of 5/day)
    "wt_day": "",              # YYYYMMDD the free/adv counters belong to (reset on a new day)
    "wt_granted": 0,           # 1 once the one-time starter fosterTicket stash has been given
    # World Boss is an offline, per-player daily encounter on this local server.
    "worldboss_day": "",       # YYYYMMDD; reset attempts/rewards on the next local day
    "worldboss_state": "",     # JSON: boss HP, damage, attempts, revive count and reward claim
    # --- Trial Road / 试炼之路 (ScrewUp Normal climb; 150 stages, daily material farm) ---
    "tr_max": 0,               # highest stage ever cleared (persistent; drives sweep/chest tiers)
    "tr_current": 0,           # current climb position (Restart resets it to 0 to re-sweep)
    "tr_battles_used": 0,      # NEW-stage climbs used today (of _TR_DAILY_BATTLES)
    "tr_restarts_used": 0,     # restarts used today (of _TR_DAILY_RESTARTS)
    "tr_boxes": "",            # CSV of milestone-chest stageIds already claimed (persistent, one-time)
    "tr_day": "",              # YYYYMMDD the daily counters belong to (reset on a new day)
    # --- Trial Road / 试炼之路 nightmare mode (kept separate from normal progress) ---
    "tr_hard_max": 0,
    "tr_hard_current": 0,
    "tr_hard_battles_used": 0,
    "tr_hard_restarts_used": 0,
    "tr_hard_boxes": "",
    "tr_hard_day": "",
    # --- VIP (会员, 3 tiers; GM-set on this private server) ---
    "vip_level": 0,            # 0=none, 1/2/3 = 阳光/财富/金壶 VIP (drives the privilege bundle)
    "vip_day": "",             # YYYYMMDD the daily VIP gift flags belong to (reset on a new day)
    "vip_energy_taken": 0,     # 1 once today's VIP energy gift (priv 13) is claimed
    "vip_ticket_taken": 0,     # 1 once today's VIP diamond gift (priv 24) is claimed
    "vip_gift_taken": 0,       # 1 once the one-time first-activation gift (giftString) is claimed
    "vip_plant_granted": 0,    # 1 once the VIP3 exclusive plant (Winter Melon 6900) has been unlocked
    # --- nutrition room (营养室 / Building_Nutrition=170) reagents (试剂) ---
    "nutrition_temp": "",        # CSV of temp-bag reagent baseIds awaiting collection (survive re-login)
    "nutrition_gold": 0,         # gold-reagent (60000) pool used by the exchange
    "nutrition_bottle_exp": 0,   # reagent-exp pool (residuals / extract-exp feed it)
    "reagent_seq": 0,            # per-player reagent uid counter (never reused, even after delete)
    # --- low-coupling daily systems migrated from the community build ---
    # JSON is used here because these systems are small, server-owned state machines and
    # older player databases must be migrated without a destructive schema rewrite.
    "online_reward_data": "",
    "question_data": "",
    "daily_quest_data": "",
    "welfare_resource_data": "",
    "daily_td_data": "",
    "noend_td_data": "",
    "plant_test_data": "",
    "arena_data": "",
    "rank_arena_data": "",
    "snowman_data": "",
    # --- gene lab / gene trial ---
    "gene_test_day": "",         # YYYYMMDD for gene-trial daily attempts
    "gene_test_remain": 3,        # failed fight retries available today
    "gene_test_restart_num": 0,   # chapter resets used (reset on daily refresh)
    "gene_test_last_stage": 1,    # last chapter opened in the gene-trial UI
    "gene_test_pass_stage": 0,    # highest normal chapter fully cleared
    "gene_test_pass_elite": 0,    # highest elite chapter fully cleared
    "gene_test_sweep_day": "",   # one sweep per day, YYYYMMDD
    # --- plant baptize / refine (植物洗炼) ---
    "baptize_day": "",           # YYYYMMDD the daily refine counters belong to (reset on a new day)
    "baptize_used": 0,           # refine rolls used today (of iDailyRefineTimes)
    "baptize_granted": 0,        # 1 once the one-time 洗炼 material stash has been given
    # --- plant swap (植物交换) ---
    "swap_granted": 0,           # 1 once the one-time 植物交换券 (item 3207) stash has been given
    # --- plant armour + prestige evolution (装甲 / 威望) ---
    "armor_prestige_granted": 0, # 1 once the testable starter materials have been given
    # --- zombie island (勇闯僵尸岛; town building 160, unlock role lv 24) ---
    # The client ships TWO variants of this feature side by side and each keeps its own climb:
    #   * normal — Cmd_ZombieIsland_*, battle target 9,  Dto_CardBaseInfo.zombieIslandHp
    #   * adv    — Cmd_NewZombieIsland_*, battle target 21, Dto_CardBaseInfo.newZombieIslandHp
    # zi_level = the island (1..12) that mode fights next; zi_max = highest island ever cleared.
    # zi_boxes = CSV of "levelId:boxId" chests already claimed. zi_precards = the island lineup the
    # player arranged in ZombieIslandPrepareView (CSV of card uids, slot order).
    "zi_level": 1,
    "zi_max": 0,
    "zi_adv_level": 1,
    "zi_adv_max": 0,
    "zi_boxes": "",
    "zi_adv_boxes": "",
    "zi_precards": "",
    "zi_adv_precards": "",
    "zi_last_role_level": 1,      # role level at the last pass (Cmd_ZombieIsland_GetIslandInfo_SC field 3)
    "zi_adv_last_role_level": 1,
    "zi_day": "",                 # YYYYMMDD the daily restart counters belong to (normal tab)
    "zi_refresh_used": 0,         # normal: daily 重新开始 restarts used
    "zi_adv_day": "",             # same, for the adv tab (each tab has its own daily allowance)
    "zi_adv_refresh_used": 0,
    "zi_sweep_day": "",           # YYYYMMDD of the last normal-mode sweep (one per day)
    "zi_shop_day": "",            # YYYYMMDD of the last island-shop refresh (daily purchase flags)
    "zi_shop_bought": "",         # CSV of Dto_ExchangeStore_Entry.entryId already exchanged today
    "guild_shop_day": "",         # YYYYMMDD of the last guild-shop (ExchangeStore 4) daily rollover
    "guild_shop_bought": "",      # CSV of guild-shop entryIds already exchanged today
    # --- TD privilege (塔防特权): a time-limited sub that unlocks premium plants in TD levels.
    # GM-set on this private server. td_privilege=1 -> on; td_privilege_endtime = unix ts it expires
    # (we push a far-future ts so it never expires while on). Drives Cmd_TD_PrivilegeInfo_SC +
    # Cmd_TD_OpenPrivilege_SC; the client unlocks the listed `plantId`s for use in TD levels.
    "td_privilege": 0,
    "td_privilege_endtime": 0,
    # --- diamond card (钻石卡 / QQ 蓝钻): the player's OWN Dto_DiamondInfo pushed via
    # Cmd_Vip_SyncVipInfo_SC. diamond_flag = the Dto_DiamondInfo.flag (visibility bitmask; 1 = show
    # the card). diamond_<type>_lvl = the Dto_DiamondLevel.level for that E_DiamondType
    # (1=Blue, 3=Yellow, 4=XinYue). 0 level => that diamond is not granted.
    "diamond_flag": 1,
    "diamond_blue_lvl": 0,
    "diamond_yellow_lvl": 0,
    "diamond_xinyue_lvl": 0,
    # --- New-player guide ---
    # The client owns the presentation and asks the server to retain these values.
    # Keep them per account so an interrupted tutorial resumes after reconnecting.
    "role_created": 0,          # controls CheckOldUser / role-name screen independently
    "tutorial_active": 0,       # set to 1 only for newly-created accounts
    "tutorial_initialized": 0,  # one-time migration marker for saves created before guides returned
    "guide_keys": "",          # newline-separated key/value pairs (key\tvalue)
    "guide_finish_ids": "",    # comma-separated completed guide ids
}

# Reagent instance uids live in their own high range so they never collide with card/plant uids.
REAGENT_UID_BASE = 70_000_000

# Normal-mode starter plants a new player unlocks (plantIds from tdPlantInfo/tdPlantTypeInfo):
# Sunflower, Peashooter, Wall-nut, Potato Mine (the "Player's House" set).
STARTER_PLANTS = [1100, 1200, 1300, 1500]


def _connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# Per-thread REUSED connection. Profiling a login showed handle_card_getpackage opening
# ~15k sqlite connections (one per _db() call x 118 cards) and spending 8.5s just in
# connect/close; reusing one connection per thread cut the whole handler from ~27s to
# ~0.2s and speeds up every other handler equally. The old "close every time" contract
# is preserved semantically: success commits, an error rolls the thread's txn back.
# NOTE for tests: a cached connection keeps players.db OPEN, which stops Windows from
# deleting a TemporaryDirectory — tests that swap DB_PATH must call close_thread_db()
# in tearDown (or simply swap via set_db_path, which closes the cached connection).
_THREAD_STATE = threading.local()


def _thread_db():
    con = getattr(_THREAD_STATE, "con", None)
    con_path = getattr(_THREAD_STATE, "path", None)
    if con is not None and con_path != DB_PATH:
        # DB_PATH was repointed: release the stale file handle so the old sqlite file
        # can be deleted/renamed (Windows refuses to delete an open sqlite file).
        _THREAD_STATE.con = None
        try:
            con.close()
        except sqlite3.Error:
            pass
        con = None
    if con is None:
        con = _connect()
        _THREAD_STATE.con = con
        _THREAD_STATE.path = DB_PATH
    return con


def set_db_path(path):
    """Repoint the store at a new players.db and drop this thread's cached connection.
    Tests should use this instead of assigning store.DB_PATH directly."""
    global DB_PATH
    DB_PATH = path
    close_thread_db()


def close_thread_db():
    """Close this thread's cached connection (releases players.db; used by tests)."""
    con = getattr(_THREAD_STATE, "con", None)
    if con is not None:
        _THREAD_STATE.con = None
        try:
            con.close()
        except sqlite3.Error:
            pass


@contextlib.contextmanager
def _db():
    """Yield this thread's shared connection. Commits on success; an exception rolls
    back (matching the old always-close behaviour, where close discarded the txn)."""
    con = _thread_db()
    try:
        yield con
        con.commit()
    except Exception:
        try:
            con.rollback()
        except sqlite3.Error:
            pass
        raise


def init_db():
    cols = ", ".join("%s %s" % (k, "TEXT" if isinstance(v, str) else "INTEGER")
                     for k, v in DEFAULTS.items())
    with _db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS players (uin INTEGER PRIMARY KEY, %s)" % cols)
        con.execute("CREATE TABLE IF NOT EXISTS plants "
                    "(uin INTEGER, plant_id INTEGER, PRIMARY KEY(uin, plant_id))")
        con.execute("CREATE TABLE IF NOT EXISTS td_passed "
                    "(uin INTEGER, stage INTEGER, level INTEGER, sub INTEGER, "
                    "star INTEGER DEFAULT 1, PRIMARY KEY(uin, stage, level, sub))")
        con.execute("CREATE TABLE IF NOT EXISTS td_gates "
                    "(uin INTEGER, stage INTEGER, gate_id INTEGER, "
                    "PRIMARY KEY(uin, stage, gate_id))")
        con.execute("CREATE TABLE IF NOT EXISTS items "
                    "(uin INTEGER, item_id INTEGER, count INTEGER, "
                    "PRIMARY KEY(uin, item_id))")
        # Equipment is stored as individual bag objects (unlike stackable materials).  The
        # equipment template id changes when it is ranked up; uid remains stable so the Flash
        # client's BagProxy can update the same object.
        con.execute("CREATE TABLE IF NOT EXISTS equipments "
                    "(uin INTEGER, uid INTEGER, item_id INTEGER, card_uid INTEGER DEFAULT 0, "
                    "level INTEGER DEFAULT 0, exp INTEGER DEFAULT 0, grade_percent INTEGER DEFAULT 0, "
                    "is_clone INTEGER DEFAULT 0, PRIMARY KEY(uin, uid))")
        con.execute("CREATE TABLE IF NOT EXISTS equipment_sequences "
                    "(uin INTEGER PRIMARY KEY, next_uid INTEGER NOT NULL)")
        # The pool/generator and its attachable part are account-wide, not card-local.
        con.execute("CREATE TABLE IF NOT EXISTS equipment_state "
                    "(uin INTEGER PRIMARY KEY, pool_id INTEGER DEFAULT 1, current_energy INTEGER DEFAULT 0, "
                    "last_energy_at INTEGER DEFAULT 0, current_exp INTEGER DEFAULT 0, part_id INTEGER DEFAULT 1, "
                    "first_claimed INTEGER DEFAULT 0, daily_claim_day TEXT DEFAULT '', "
                    "auto_use_bead INTEGER DEFAULT 0, buy_count INTEGER DEFAULT 0)")
        con.execute("CREATE TABLE IF NOT EXISTS fuben_passed "
                    "(uin INTEGER, chapter INTEGER, mode INTEGER, stage INTEGER, "
                    "star INTEGER DEFAULT 3, PRIMARY KEY(uin, chapter, mode, stage))")
        con.execute("CREATE TABLE IF NOT EXISTS fuben_boxes "
                    "(uin INTEGER, chapter INTEGER, mode INTEGER, "
                    "PRIMARY KEY(uin, chapter, mode))")
        con.execute("CREATE TABLE IF NOT EXISTS cards "
                    "(uin INTEGER, uid INTEGER, card_id INTEGER, level INTEGER DEFAULT 1, "
                    "exp INTEGER DEFAULT 0, fill_quality INTEGER DEFAULT 1, "
                    "evolve_level INTEGER DEFAULT 0, armor_type INTEGER DEFAULT 0, "
                    "PRIMARY KEY(uin, uid))")
        # Card fruit progression: quality is the fruit slot (1..14 in this client DB),
        # level is that slot's catalyst level. Keep it keyed by owned card UID so duplicate
        # plants can grow independently and star-up can carry their fruit along.
        con.execute("CREATE TABLE IF NOT EXISTS card_fruits "
                    "(uin INTEGER, uid INTEGER, quality INTEGER, level INTEGER DEFAULT 0, "
                    "PRIMARY KEY(uin, uid, quality))")
        # Gene progress is card-UID local (never transferable between plants or star grades).
        con.execute("CREATE TABLE IF NOT EXISTS card_genes "
                    "(uin INTEGER, uid INTEGER, gene_id INTEGER, factor_level INTEGER DEFAULT 0, "
                    "factor_exp INTEGER DEFAULT 0, attribute INTEGER DEFAULT 0, level INTEGER DEFAULT 0, "
                    "PRIMARY KEY(uin, uid, gene_id))")
        # Chapter progress is independent for normal/elite mode; reward totals are compact
        # itemId:count pairs so the stage panel can restore its reward history.
        con.execute("CREATE TABLE IF NOT EXISTS gene_test_stages "
                    "(uin INTEGER, mode INTEGER, stage INTEGER, current_sub INTEGER DEFAULT 0, "
                    "max_sub INTEGER DEFAULT 0, rewards TEXT DEFAULT '', "
                    "PRIMARY KEY(uin, mode, stage))")
        card_cols = {row[1] for row in con.execute("PRAGMA table_info(cards)")}
        if "fill_quality" not in card_cols:
            con.execute("ALTER TABLE cards ADD COLUMN fill_quality INTEGER DEFAULT 1")
        if "evolve_level" not in card_cols:
            con.execute("ALTER TABLE cards ADD COLUMN evolve_level INTEGER DEFAULT 0")
        if "armor_type" not in card_cols:
            con.execute("ALTER TABLE cards ADD COLUMN armor_type INTEGER DEFAULT 0")
        con.execute("CREATE TABLE IF NOT EXISTS card_fill "
                "(uin INTEGER, uid INTEGER, piece_index INTEGER, "
                "PRIMARY KEY(uin, uid, piece_index))")
        con.execute("CREATE TABLE IF NOT EXISTS card_forge "
                    "(uin INTEGER, uid INTEGER, forge_level INTEGER DEFAULT 0, "
                    "PRIMARY KEY(uin, uid))")
        # Card wardrobe state is separate from normal card enhancement.  One row per
        # owned card UID and avatar ID keeps duplicate plants independent and lets the
        # wardrobe survive relog/star-up just like the other card-local systems.
        con.execute("CREATE TABLE IF NOT EXISTS card_avatar "
                    "(uin INTEGER, uid INTEGER, avatar_id INTEGER, "
                    "status INTEGER DEFAULT 1, forge_level INTEGER DEFAULT 0, "
                    "PRIMARY KEY(uin, uid, avatar_id))")
        # Plant armour is card-local.  Each plant owns three armour sets
        # (attack/defence/life); crystals are separately socketed into four holes.
        con.execute("CREATE TABLE IF NOT EXISTS card_armor "
                    "(uin INTEGER, uid INTEGER, armor_type INTEGER, star INTEGER DEFAULT 0, "
                    "substar INTEGER DEFAULT 0, level INTEGER DEFAULT 1, exp INTEGER DEFAULT 0, "
                    "PRIMARY KEY(uin, uid, armor_type))")
        con.execute("CREATE TABLE IF NOT EXISTS card_armor_gems "
                    "(uin INTEGER, uid INTEGER, armor_type INTEGER, gem_type INTEGER, "
                    "item_id INTEGER DEFAULT 0, "
                    "PRIMARY KEY(uin, uid, armor_type, gem_type))")
        # Older builds incorrectly made uid the only primary key. The same card uid can exist
        # in different player saves, so migrate that schema before using the composite upsert.
        forge_cols = list(con.execute("PRAGMA table_info(card_forge)"))
        forge_pk = {row[1] for row in forge_cols if row[5]}
        if forge_pk == {"uid"}:
            con.execute("ALTER TABLE card_forge RENAME TO card_forge_legacy")
            con.execute("CREATE TABLE card_forge "
                        "(uin INTEGER, uid INTEGER, forge_level INTEGER DEFAULT 0, "
                        "PRIMARY KEY(uin, uid))")
            con.execute("INSERT OR IGNORE INTO card_forge (uin, uid, forge_level) "
                        "SELECT uin, uid, forge_level FROM card_forge_legacy")
            con.execute("DROP TABLE card_forge_legacy")
        # Plant baptize / refine (植物洗炼): one row per owned card holding its four SAVED 附加属性
        # (攻击/防御/生命/技力 = clientDB baptizeLimit.txt's column order) plus how many rolls it has
        # ever taken. Values are in the battleCard scale and capped per attribute by baptizeLimit.txt
        # (the cap depends on the card's star + awakeLevel: 满值后不能继续增加).
        con.execute("CREATE TABLE IF NOT EXISTS card_baptize "
                    "(uin INTEGER, uid INTEGER, attack INTEGER DEFAULT 0, defense INTEGER DEFAULT 0, "
                    "hp INTEGER DEFAULT 0, ability INTEGER DEFAULT 0, total_times INTEGER DEFAULT 0, "
                    "PRIMARY KEY(uin, uid))")
        # ONE unsaved ("pending") 洗炼 result per player = the client's 保存/放弃 dialog state.
        # The in-game rule is that an unsaved result is dropped when the panel closes OR the player
        # disconnects, so this row is cleared on login (the 洗炼幸运点 refund is still paid out).
        con.execute("CREATE TABLE IF NOT EXISTS card_baptize_pending "
                    "(uin INTEGER PRIMARY KEY, uid INTEGER DEFAULT 0, mode INTEGER DEFAULT 0, "
                    "rolls INTEGER DEFAULT 0, attack INTEGER DEFAULT 0, defense INTEGER DEFAULT 0, "
                    "hp INTEGER DEFAULT 0, ability INTEGER DEFAULT 0)")
        con.execute("CREATE TABLE IF NOT EXISTS formation "
                    "(uin INTEGER, team_id INTEGER, pos_index INTEGER, uid INTEGER, "
                    "owner_role_id INTEGER DEFAULT 0, "
                    "PRIMARY KEY(uin, team_id, pos_index))")
        # Adventure formations can contain one borrowed friend card.  Older saves only
        # stored the card uid; keep that data and add a nullable owner role id in place.
        fm_cols = {r["name"] for r in con.execute("PRAGMA table_info(formation)")}
        if "owner_role_id" not in fm_cols:
            con.execute("ALTER TABLE formation ADD COLUMN owner_role_id INTEGER DEFAULT 0")
        con.execute("CREATE TABLE IF NOT EXISTS formation_leader "
                    "(uin INTEGER, team_id INTEGER, uid INTEGER, PRIMARY KEY(uin, team_id))")
        # zombie island (勇闯僵尸岛): the chests of one island the player has already opened, and the
        # island lineup the prepare view arranged. mode 0 = normal (Cmd_ZombieIsland_*), 1 = adv.
        con.execute("CREATE TABLE IF NOT EXISTS zombie_island_box "
                    "(uin INTEGER, mode INTEGER, level_id INTEGER, box_id INTEGER, "
                    "PRIMARY KEY(uin, mode, level_id, box_id))")
        con.execute("CREATE TABLE IF NOT EXISTS zombie_island_precard "
                    "(uin INTEGER, mode INTEGER, slot INTEGER, uid INTEGER, "
                    "PRIMARY KEY(uin, mode, slot))")
        # 傀儡 (puppets): one row per owned puppet (baseId = clientDB dummyZombieInfo.txt id). The
        # 6 equipment parts live in `spars` as "part:level:quality;..." because they are always sent
        # together and never queried on their own.
        con.execute("CREATE TABLE IF NOT EXISTS puppets "
                    "(uin INTEGER, base_id INTEGER, exp INTEGER DEFAULT 0, level INTEGER DEFAULT 1, "
                    "star INTEGER DEFAULT 1, quality INTEGER DEFAULT 100, active_skill INTEGER DEFAULT 0, "
                    "active_level INTEGER DEFAULT 1, passive_skill INTEGER DEFAULT 0, "
                    "passive_level INTEGER DEFAULT 1, spars TEXT DEFAULT '', "
                    "PRIMARY KEY(uin, base_id))")
        # 傀儡战境 (the puppet dungeon): progress of one stage — how many of its sub-waves are cleared
        # and how many elite-mode attempts it has used today (fightLimit).
        con.execute("CREATE TABLE IF NOT EXISTS puppet_fuben "
                    "(uin INTEGER, mode INTEGER, chapter_id INTEGER, stage_id INTEGER, wave INTEGER DEFAULT 0, "
                    "times INTEGER DEFAULT 0, day TEXT DEFAULT '', "
                    "PRIMARY KEY(uin, mode, chapter_id, stage_id))")
        # The previous schema keyed progress only by (uin, mode, stage_id), but stage IDs restart at 1
        # in every chapter. Preserve those ambiguous legacy rows under chapter 1 so existing progress
        # is not discarded; new progress is isolated by the full chapter/stage key.
        puppet_fuben_columns = list(con.execute("PRAGMA table_info(puppet_fuben)"))
        puppet_fuben_pk = [r[1] for r in sorted(puppet_fuben_columns, key=lambda r: r[5]) if r[5]]
        if puppet_fuben_pk != ["uin", "mode", "chapter_id", "stage_id"]:
            old_columns = {r[1] for r in puppet_fuben_columns}
            # Keep the original table as a read-only audit copy. Its chapterless keys make
            # historical rows ambiguous, but dropping it would erase the only unmodified copy.
            legacy_table = "puppet_fuben_legacy_chapterless"
            suffix = 1
            while con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                              (legacy_table,)).fetchone():
                legacy_table = "puppet_fuben_legacy_chapterless_%d" % suffix
                suffix += 1
            con.execute("ALTER TABLE puppet_fuben RENAME TO %s" % legacy_table)
            con.execute("CREATE TABLE puppet_fuben "
                        "(uin INTEGER, mode INTEGER, chapter_id INTEGER, stage_id INTEGER, wave INTEGER DEFAULT 0, "
                        "times INTEGER DEFAULT 0, day TEXT DEFAULT '', "
                        "PRIMARY KEY(uin, mode, chapter_id, stage_id))")
            legacy_chapter = "chapter_id" if "chapter_id" in old_columns else "1"
            con.execute("INSERT INTO puppet_fuben (uin, mode, chapter_id, stage_id, wave, times, day) "
                        "SELECT uin, mode, %s, stage_id, wave, times, day FROM %s"
                        % (legacy_chapter, legacy_table))
        con.execute("CREATE TABLE IF NOT EXISTS talents "
                    "(uin INTEGER, talent_id INTEGER, PRIMARY KEY(uin, talent_id))")
        # zen garden: one row per UNLOCKED pot. seed_id=0 = empty; plant_time = when the current
        # seed was planted OR last harvested (drives grow/harvest timing).
        con.execute("CREATE TABLE IF NOT EXISTS garden_pots "
                    "(uin INTEGER, field_id INTEGER, seed_id INTEGER DEFAULT 0, "
                    "plant_time INTEGER DEFAULT 0, fertilizer_id INTEGER DEFAULT 0, "
                    "last_fertilizer_time INTEGER DEFAULT 0, level INTEGER DEFAULT 1, "
                    "PRIMARY KEY(uin, field_id))")
        garden_cols = {r["name"] for r in con.execute("PRAGMA table_info(garden_pots)")}
        if "fertilizer_id" not in garden_cols:
            con.execute("ALTER TABLE garden_pots ADD COLUMN fertilizer_id INTEGER DEFAULT 0")
        if "last_fertilizer_time" not in garden_cols:
            con.execute("ALTER TABLE garden_pots ADD COLUMN last_fertilizer_time INTEGER DEFAULT 0")
        if "level" not in garden_cols:
            con.execute("ALTER TABLE garden_pots ADD COLUMN level INTEGER DEFAULT 1")
        # nutrition reagents (试剂): one row per OWNED reagent. base_id -> its buffType/quality
        # (nutritionProperty.txt); level scales the buff. position=0/slot_id=0 = unequipped (in the
        # bag); position in {3,7,9,11,13,15,17} + slot_id 1..8 = equipped onto that plant position.
        con.execute("CREATE TABLE IF NOT EXISTS reagents "
                    "(uin INTEGER, uid INTEGER, base_id INTEGER, level INTEGER DEFAULT 1, "
                    "exp INTEGER DEFAULT 0, position INTEGER DEFAULT 0, slot_id INTEGER DEFAULT 0, "
                    "PRIMARY KEY(uin, uid))")
        reagent_cols = {row["name"] for row in con.execute("PRAGMA table_info(reagents)")}
        if "exp" not in reagent_cols:
            con.execute("ALTER TABLE reagents ADD COLUMN exp INTEGER DEFAULT 0")
        # friends (好友): one row per added friend. help_plant_id is the plantId the friend's
        # "friend card" represents in the TD choose-seeds flow (Dto_FriendInfo.helpPlant =
        # Dto_CardFullInfo whose base.cardId maps to a tdPlantId). help_plant_level = card level.
        # diamond_flag + diamond_<type>_lvl mirror the player's own Dto_DiamondInfo so each friend's
        # diamond card shows on the friend list / world map.
        con.execute("CREATE TABLE IF NOT EXISTS friends "
                    "(uin INTEGER, friend_role_id INTEGER, friend_name TEXT, "
                    "help_plant_id INTEGER, help_plant_level INTEGER, "
                    "diamond_flag INTEGER DEFAULT 1, diamond_blue_lvl INTEGER DEFAULT 0, "
                    "diamond_yellow_lvl INTEGER DEFAULT 0, diamond_xinyue_lvl INTEGER DEFAULT 0, "
                    "PRIMARY KEY(uin, friend_role_id))")
        # Migrate any pre-existing `friends` table that predates this schema (the old PoC
        # shape was (uin, friend_id, add_time) — useless here). Drop+recreate is safe because
        # the old schema was never populated on this private server.
        fr_cols = {r["name"] for r in con.execute("PRAGMA table_info(friends)")}
        if "friend_role_id" not in fr_cols:
            con.execute("DROP TABLE friends")
            con.execute("CREATE TABLE friends "
                        "(uin INTEGER, friend_role_id INTEGER, friend_name TEXT, "
                        "help_plant_id INTEGER, help_plant_level INTEGER, "
                        "diamond_flag INTEGER DEFAULT 1, diamond_blue_lvl INTEGER DEFAULT 0, "
                        "diamond_yellow_lvl INTEGER DEFAULT 0, diamond_xinyue_lvl INTEGER DEFAULT 0, "
                        "PRIMARY KEY(uin, friend_role_id))")
        _ensure_columns(con)
        # td_passed.achievements = csv of the achievement ids actually earned (performance
        # stars). star stays = the count, for fast SUM(star) totals.
        have = {r["name"] for r in con.execute("PRAGMA table_info(td_passed)")}
        if "achievements" not in have:
            con.execute("ALTER TABLE td_passed ADD COLUMN achievements TEXT DEFAULT ''")
    return DB_PATH


def _ensure_columns(con):
    have = {r["name"] for r in con.execute("PRAGMA table_info(players)")}
    for k, v in DEFAULTS.items():
        if k not in have:
            # New column WITH a DEFAULT so existing rows backfill to the default
            # (a bare ALTER ADD COLUMN leaves existing rows NULL -> int(None) crashes).
            typ = "TEXT" if isinstance(v, str) else "INTEGER"
            default = "''" if isinstance(v, str) else str(int(v))
            con.execute("ALTER TABLE players ADD COLUMN %s %s DEFAULT %s" % (k, typ, default))
        elif not isinstance(v, str):
            # Heal any NULLs left by an older ALTER that ran before this DEFAULT logic.
            con.execute("UPDATE players SET %s=? WHERE %s IS NULL" % (k, k), (v,))


def get_or_create_player(uin):
    """Return (player_dict, is_new). Creates a starter player if uin is unseen."""
    now = int(time.time())
    with _db() as con:
        row = con.execute("SELECT * FROM players WHERE uin=?", (uin,)).fetchone()
        if row is not None:
            con.execute("UPDATE players SET last_login=? WHERE uin=?", (now, uin))
            vals = dict(row)
            has_td_progress = con.execute(
                "SELECT 1 FROM td_passed WHERE uin=? AND stage>0 LIMIT 1", (uin,)
            ).fetchone() is not None
            # One-time upgrade for saves made while the tutorial was disabled:
            # accounts with no ordinary TD progress are still at the new-player
            # starting point, so allow them to enter the restored tutorial once.
            if not vals.get("tutorial_initialized"):
                vals["tutorial_initialized"] = 1
                vals["tutorial_active"] = 0 if has_td_progress else 1
            # Any ordinary TD clear proves both role creation and the tutorial
            # were completed. Repair stale flags on every login.
            if has_td_progress:
                vals["role_created"] = 1
                vals["tutorial_active"] = 0
            con.execute("UPDATE players SET tutorial_initialized=?, tutorial_active=?, "
                        "role_created=? WHERE uin=?",
                        (vals["tutorial_initialized"], vals["tutorial_active"],
                         vals["role_created"], uin))
            return vals, False
        vals = dict(DEFAULTS)
        vals["role_id"] = uin
        vals["name"] = "Player%d" % (uin % 100000)
        vals["reg_time"] = now
        vals["server_open_time"] = now
        vals["created_at"] = now
        vals["last_login"] = now
        vals["tutorial_active"] = 1
        vals["tutorial_initialized"] = 1
        vals["role_created"] = 0
        keys = ["uin"] + list(vals.keys())
        con.execute(
            "INSERT INTO players (%s) VALUES (%s)" % (
                ", ".join(keys), ", ".join("?" * len(keys))),
            [uin] + list(vals.values()))
        con.executemany("INSERT OR IGNORE INTO plants (uin, plant_id) VALUES (?, ?)",
                        [(uin, p) for p in STARTER_PLANTS])
        vals["uin"] = uin
        return vals, True


def get_player_by_role_id(role_id):
    """按 roleId 查玩家存档 dict；没有则返回 None。
    依赖 players 表的 role_id 列（创建时默认 = uin）。"""
    if not role_id:
        return None
    con = _connect()
    try:
        row = con.execute("SELECT uin FROM players WHERE role_id=?",
                          (int(role_id),)).fetchone()
    finally:
        con.close()
    if not row:
        return None
    player, _ = get_or_create_player(int(row[0]))
    player["plants"] = get_player_plants(player["uin"])
    return player
    
    
def get_player_plants(uin):
    """List of unlocked plantIds for a player (empty if none)."""
    with _db() as con:
        return [r["plant_id"] for r in con.execute(
            "SELECT plant_id FROM plants WHERE uin=? ORDER BY plant_id", (uin,))]


def unlock_plant(uin, plant_id):
    with _db() as con:
        con.execute("INSERT OR IGNORE INTO plants (uin, plant_id) VALUES (?, ?)",
                    (uin, plant_id))


def remove_plant(uin, plant_id):
    """Remove a plant from a player's TD roster (used to clean up plants that were never valid TD
    plants). The battle-card collection (cards table) is separate and untouched."""
    with _db() as con:
        con.execute("DELETE FROM plants WHERE uin=? AND plant_id=?", (uin, plant_id))


def pass_level(uin, stage, level, sub, achievements):
    """Record a passed TD level with the achievement ids actually earned (performance
    stars). `achievements` is an iterable of ids; star = how many. Keeps the BEST run
    (a replay only overwrites if it earned strictly more stars)."""
    ach = sorted({int(a) for a in achievements})
    star = len(ach)
    csv = ",".join(str(a) for a in ach)
    with _db() as con:
        con.execute(
            "INSERT INTO td_passed (uin, stage, level, sub, star, achievements) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(uin, stage, level, sub) DO UPDATE SET "
            "star=excluded.star, achievements=excluded.achievements "
            "WHERE excluded.star > td_passed.star",
            (uin, stage, level, sub, star, csv))


def get_passed_levels(uin, stage):
    """[(level, sub, star, [achievementIds])] the player has passed in a stage. Legacy
    rows with no stored ids fall back to synthesized ids [1..star] so their star COUNT
    still displays (the exact ids only matter for the per-level achievement tooltip)."""
    out = []
    with _db() as con:
        for r in con.execute(
                "SELECT level, sub, star, achievements FROM td_passed "
                "WHERE uin=? AND stage=? ORDER BY level, sub", (uin, stage)):
            ach = [int(x) for x in (r["achievements"] or "").split(",") if x != ""]
            if not ach and r["star"] > 0:
                ach = list(range(1, r["star"] + 1))
            out.append((r["level"], r["sub"], r["star"], ach))
    return out


def is_level_passed(uin, stage, level, sub):
    with _db() as con:
        return con.execute("SELECT 1 FROM td_passed WHERE uin=? AND stage=? AND level=? "
                           "AND sub=?", (uin, stage, level, sub)).fetchone() is not None


def get_all_passed_levels(uin):
    """[(stage, level, sub)] for every TD level the player has passed (all stages). Used to rebuild the
    legitimately-earned TD plant roster (starters + each level's bonusPlant)."""
    with _db() as con:
        return [(r["stage"], r["level"], r["sub"]) for r in con.execute(
            "SELECT stage, level, sub FROM td_passed WHERE uin=? ORDER BY stage, level, sub", (uin,))]


def total_stars(uin, stage):
    """Sum of stars the player has earned in a stage (drives the map HUD star count
    and the gate needStars thresholds)."""
    with _db() as con:
        row = con.execute("SELECT COALESCE(SUM(star), 0) AS s FROM td_passed "
                          "WHERE uin=? AND stage=?", (uin, stage)).fetchone()
        return int(row["s"])


def unlock_gate(uin, stage, gate_id):
    """Record a level gate the player has unlocked (idempotent)."""
    with _db() as con:
        con.execute("INSERT OR IGNORE INTO td_gates (uin, stage, gate_id) VALUES (?,?,?)",
                    (uin, stage, gate_id))


def get_unlocked_gates(uin, stage):
    """List of gateIds the player has unlocked in a stage."""
    with _db() as con:
        return [r["gate_id"] for r in con.execute(
            "SELECT gate_id FROM td_gates WHERE uin=? AND stage=? ORDER BY gate_id",
            (uin, stage))]


def get_items(uin):
    """[(item_id, count)] the player holds (count > 0), ordered by item_id."""
    with _db() as con:
        return [(r["item_id"], r["count"]) for r in con.execute(
            "SELECT item_id, count FROM items WHERE uin=? AND count>0 ORDER BY item_id", (uin,))]


def get_item_count(uin, item_id):
    with _db() as con:
        row = con.execute("SELECT count FROM items WHERE uin=? AND item_id=?",
                          (uin, item_id)).fetchone()
        return int(row["count"]) if row else 0


def add_item(uin, item_id, delta):
    """Add `delta` (may be negative) to an item stack; clamp at 0 and delete empty stacks.
    Returns the new count."""
    with _db() as con:
        row = con.execute("SELECT count FROM items WHERE uin=? AND item_id=?",
                          (uin, item_id)).fetchone()
        new = max(0, (int(row["count"]) if row else 0) + int(delta))
        if new <= 0:
            con.execute("DELETE FROM items WHERE uin=? AND item_id=?", (uin, item_id))
        else:
            con.execute("INSERT INTO items (uin, item_id, count) VALUES (?,?,?) "
                        "ON CONFLICT(uin, item_id) DO UPDATE SET count=excluded.count",
                        (uin, item_id, new))
        return new


def get_equipments(uin):
    """Return the player's individual equipment objects as dictionaries."""
    with _db() as con:
        return [dict(r) for r in con.execute(
            "SELECT uid, item_id, card_uid, level, exp, grade_percent, is_clone "
            "FROM equipments WHERE uin=? ORDER BY uid", (uin,))]


def get_equipment(uin, uid):
    with _db() as con:
        row = con.execute(
            "SELECT uid, item_id, card_uid, level, exp, grade_percent, is_clone "
            "FROM equipments WHERE uin=? AND uid=?", (uin, int(uid))).fetchone()
        return dict(row) if row else None


def next_equipment_uid(uin):
    """Allocate a stable bag uid, separate from stackable item ids and card uids."""
    with _db() as con:
        con.execute("INSERT OR IGNORE INTO equipment_sequences (uin, next_uid) VALUES (?,20000001)",
                    (uin,))
        row = con.execute("SELECT next_uid FROM equipment_sequences WHERE uin=?", (uin,)).fetchone()
        uid = int(row["next_uid"])
        con.execute("UPDATE equipment_sequences SET next_uid=? WHERE uin=?", (uid + 1, uin))
        return uid


def add_equipment(uin, uid, item_id, card_uid=0, level=0, exp=0,
                  grade_percent=0, is_clone=0):
    with _db() as con:
        con.execute("INSERT OR REPLACE INTO equipments "
                    "(uin, uid, item_id, card_uid, level, exp, grade_percent, is_clone) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (uin, int(uid), int(item_id), int(card_uid), int(level), int(exp),
                     int(grade_percent), int(is_clone)))


def update_equipment(uin, uid, **fields):
    allowed = {"item_id", "card_uid", "level", "exp", "grade_percent", "is_clone"}
    values = [(key, int(value)) for key, value in fields.items() if key in allowed]
    if not values:
        return
    with _db() as con:
        con.execute("UPDATE equipments SET " + ",".join(key + "=?" for key, _ in values) +
                    " WHERE uin=? AND uid=?", [value for _, value in values] + [uin, int(uid)])


def remove_equipment(uin, uid):
    with _db() as con:
        con.execute("DELETE FROM equipments WHERE uin=? AND uid=?", (uin, int(uid)))


def get_equipment_state(uin):
    """Create a usable starter glass pool once, then return its persisted state."""
    now = int(time.time())
    with _db() as con:
        con.execute("INSERT OR IGNORE INTO equipment_state "
                    "(uin, pool_id, current_energy, last_energy_at, current_exp, part_id) "
                    "VALUES (?,1,80000,?,0,1)", (uin, now))
        return dict(con.execute("SELECT * FROM equipment_state WHERE uin=?", (uin,)).fetchone())


def update_equipment_state(uin, **fields):
    allowed = {"pool_id", "current_energy", "last_energy_at", "current_exp", "part_id",
               "first_claimed", "daily_claim_day", "auto_use_bead", "buy_count"}
    values = [(key, value) for key, value in fields.items() if key in allowed]
    if not values:
        return
    with _db() as con:
        con.execute("INSERT OR IGNORE INTO equipment_state (uin) VALUES (?)", (uin,))
        con.execute("UPDATE equipment_state SET " + ",".join(key + "=?" for key, _ in values) +
                    " WHERE uin=?", [value for _, value in values] + [uin])


def pass_fuben_stage(uin, chapter, mode, stage, star=3):
    """Record a cleared adventure (fuben) stage, keeping the best star."""
    with _db() as con:
        con.execute(
            "INSERT INTO fuben_passed (uin, chapter, mode, stage, star) VALUES (?,?,?,?,?) "
            "ON CONFLICT(uin, chapter, mode, stage) DO UPDATE SET star=MAX(star, excluded.star)",
            (uin, chapter, mode, stage, star))


def get_fuben_passed(uin, chapter, mode):
    """{stageId: star} the player has cleared in a chapter+mode."""
    with _db() as con:
        return {r["stage"]: r["star"] for r in con.execute(
            "SELECT stage, star FROM fuben_passed WHERE uin=? AND chapter=? AND mode=?",
            (uin, chapter, mode))}


def take_fuben_box(uin, chapter, mode):
    """Mark a chapter's star-reward chest (for a mode) as claimed (idempotent)."""
    with _db() as con:
        con.execute("INSERT OR IGNORE INTO fuben_boxes (uin, chapter, mode) VALUES (?,?,?)",
                    (uin, chapter, mode))


def is_fuben_box_taken(uin, chapter, mode):
    """True if the player has already claimed a chapter+mode star-reward chest."""
    with _db() as con:
        return con.execute("SELECT 1 FROM fuben_boxes WHERE uin=? AND chapter=? AND mode=?",
                           (uin, chapter, mode)).fetchone() is not None


def get_cards(uin):
    """[(uid, card_id, level, exp)] battle cards the player owns (their collection)."""
    with _db() as con:
        return [(r["uid"], r["card_id"], r["level"], r["exp"]) for r in con.execute(
            "SELECT uid, card_id, level, exp FROM cards WHERE uin=? ORDER BY card_id", (uin,))]


def get_card_fill_quality(uin, uid):
    """Return the persistent plant quality reached through completed puzzle boards."""
    with _db() as con:
        row = con.execute("SELECT fill_quality FROM cards WHERE uin=? AND uid=?",
                          (uin, int(uid))).fetchone()
        return max(1, int(row["fill_quality"])) if row else 1


def set_card_fill_quality(uin, uid, quality):
    """Persist a plant's puzzle quality (1=white, 2=green, ...)."""
    with _db() as con:
        con.execute("UPDATE cards SET fill_quality=? WHERE uin=? AND uid=?",
                    (max(1, int(quality)), uin, int(uid)))


def get_card_evolve_level(uin, uid):
    """Return the persistent 威望/plant-evolution level (0..200)."""
    with _db() as con:
        row = con.execute("SELECT evolve_level FROM cards WHERE uin=? AND uid=?",
                          (uin, int(uid))).fetchone()
        return max(0, int(row["evolve_level"])) if row else 0


def set_card_evolve_level(uin, uid, level):
    with _db() as con:
        con.execute("UPDATE cards SET evolve_level=? WHERE uin=? AND uid=?",
                    (max(0, int(level)), uin, int(uid)))


def get_card_armor_type(uin, uid):
    """The armour set whose special passive is active (0 means not selected yet)."""
    with _db() as con:
        row = con.execute("SELECT armor_type FROM cards WHERE uin=? AND uid=?",
                          (uin, int(uid))).fetchone()
        return int(row["armor_type"]) if row else 0


def set_card_armor_type(uin, uid, armor_type):
    with _db() as con:
        con.execute("UPDATE cards SET armor_type=? WHERE uin=? AND uid=?",
                    (int(armor_type), uin, int(uid)))


def get_card_armors(uin, uid):
    """Return all three armour records, supplying pristine defaults when absent."""
    result = {armor_type: {"armor_type": armor_type, "star": 0, "substar": 0,
                           "level": 1, "exp": 0, "gems": {}}
              for armor_type in (1, 2, 3)}
    with _db() as con:
        for row in con.execute(
                "SELECT armor_type,star,substar,level,exp FROM card_armor "
                "WHERE uin=? AND uid=?", (uin, int(uid))):
            armor_type = int(row["armor_type"])
            if armor_type in result:
                result[armor_type].update(star=int(row["star"]),
                                          substar=int(row["substar"]),
                                          level=max(1, int(row["level"])),
                                          exp=max(0, int(row["exp"])))
        for row in con.execute(
                "SELECT armor_type,gem_type,item_id FROM card_armor_gems "
                "WHERE uin=? AND uid=?", (uin, int(uid))):
            armor_type = int(row["armor_type"])
            if armor_type in result and int(row["item_id"]) > 0:
                result[armor_type]["gems"][int(row["gem_type"])] = int(row["item_id"])
    return result


def set_card_armor(uin, uid, armor_type, star, substar, level, exp):
    with _db() as con:
        con.execute(
            "INSERT INTO card_armor (uin,uid,armor_type,star,substar,level,exp) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(uin,uid,armor_type) DO UPDATE SET "
            "star=excluded.star,substar=excluded.substar,level=excluded.level,exp=excluded.exp",
            (uin, int(uid), int(armor_type), max(0, int(star)), max(0, int(substar)),
             max(1, int(level)), max(0, int(exp))))


def set_card_armor_gem(uin, uid, armor_type, gem_type, item_id):
    with _db() as con:
        if int(item_id) <= 0:
            con.execute("DELETE FROM card_armor_gems WHERE uin=? AND uid=? AND armor_type=? "
                        "AND gem_type=?", (uin, int(uid), int(armor_type), int(gem_type)))
        else:
            con.execute(
                "INSERT INTO card_armor_gems (uin,uid,armor_type,gem_type,item_id) "
                "VALUES (?,?,?,?,?) ON CONFLICT(uin,uid,armor_type,gem_type) DO UPDATE SET "
                "item_id=excluded.item_id",
                (uin, int(uid), int(armor_type), int(gem_type), int(item_id)))


def get_all_card_armor_gems(uin):
    """All socketed crystals for account-wide armour amplification counting."""
    with _db() as con:
        return [int(r["item_id"]) for r in con.execute(
            "SELECT item_id FROM card_armor_gems WHERE uin=? AND item_id>0", (uin,))]


def has_card(uin, uid):
    with _db() as con:
        return con.execute("SELECT 1 FROM cards WHERE uin=? AND uid=?",
                           (uin, uid)).fetchone() is not None


def add_card(uin, uid, card_id, level=1, exp=0):
    """Add a battle card to the collection (idempotent on uid). Returns True if newly added."""
    with _db() as con:
        cur = con.execute("INSERT OR IGNORE INTO cards (uin, uid, card_id, level, exp) "
                          "VALUES (?,?,?,?,?)", (uin, uid, card_id, level, exp))
        return cur.rowcount > 0


def set_card_level(uin, uid, level, exp=0):
    with _db() as con:
        con.execute("UPDATE cards SET level=?, exp=? WHERE uin=? AND uid=?",
                    (level, exp, uin, uid))


def upgrade_card(uin, uid, card_id):
    """Star-up a card IN PLACE: swap its cardId (which encodes the star) but keep the same uid so
    saved formations that reference it stay valid. Level/exp are preserved."""
    with _db() as con:
        con.execute("UPDATE cards SET card_id=? WHERE uin=? AND uid=?", (card_id, uin, uid))


def replace_card(uin, old_uid, new_uid, card_id):
    """Replace a card during star-up while preserving its level/exp and references.

    The client treats star-up as an old-card -> new-card operation.  Moving the
    UID makes its card model discard the stale forge-level object; formations,
    puzzle progress and enhancement state are moved along with it.
    """
    old_uid, new_uid = int(old_uid), int(new_uid)
    with _db() as con:
        if old_uid == new_uid:
            con.execute("UPDATE cards SET card_id=? WHERE uin=? AND uid=?",
                        (int(card_id), uin, old_uid))
            return
        con.execute("DELETE FROM card_forge WHERE uin=? AND uid=?", (uin, new_uid))
        con.execute("DELETE FROM card_avatar WHERE uin=? AND uid=?", (uin, new_uid))
        con.execute("DELETE FROM card_fill WHERE uin=? AND uid=?", (uin, new_uid))
        con.execute("DELETE FROM card_baptize WHERE uin=? AND uid=?", (uin, new_uid))
        con.execute("DELETE FROM card_armor WHERE uin=? AND uid=?", (uin, new_uid))
        con.execute("DELETE FROM card_armor_gems WHERE uin=? AND uid=?", (uin, new_uid))
        con.execute("UPDATE formation SET uid=? WHERE uin=? AND uid=?",
                    (new_uid, uin, old_uid))
        con.execute("UPDATE card_forge SET uid=? WHERE uin=? AND uid=?",
                    (new_uid, uin, old_uid))
        con.execute("UPDATE card_avatar SET uid=? WHERE uin=? AND uid=?",
                    (new_uid, uin, old_uid))
        con.execute("UPDATE card_fill SET uid=? WHERE uin=? AND uid=?",
                    (new_uid, uin, old_uid))
        con.execute("UPDATE card_baptize SET uid=? WHERE uin=? AND uid=?",
                    (new_uid, uin, old_uid))
        con.execute("UPDATE card_armor SET uid=? WHERE uin=? AND uid=?",
                    (new_uid, uin, old_uid))
        con.execute("UPDATE card_armor_gems SET uid=? WHERE uin=? AND uid=?",
                    (new_uid, uin, old_uid))
        con.execute("UPDATE card_baptize_pending SET uid=? WHERE uin=? AND uid=?",
                    (new_uid, uin, old_uid))
        con.execute("DELETE FROM card_fruits WHERE uin=? AND uid=?", (uin, new_uid))
        con.execute("UPDATE card_fruits SET uid=? WHERE uin=? AND uid=?",
                    (new_uid, uin, old_uid))
        con.execute("UPDATE cards SET uid=?, card_id=? WHERE uin=? AND uid=?",
                    (new_uid, int(card_id), uin, old_uid))


def remove_card(uin, uid, replacement_uid=0):
    """Remove one duplicate card and clean its dependent rows."""
    uid, replacement_uid = int(uid), int(replacement_uid or 0)
    with _db() as con:
        if replacement_uid:
            con.execute("UPDATE formation SET uid=? WHERE uin=? AND uid=?",
                        (replacement_uid, uin, uid))
        else:
            con.execute("DELETE FROM formation WHERE uin=? AND uid=?", (uin, uid))
        con.execute("DELETE FROM card_forge WHERE uin=? AND uid=?", (uin, uid))
        con.execute("DELETE FROM card_avatar WHERE uin=? AND uid=?", (uin, uid))
        con.execute("DELETE FROM card_fill WHERE uin=? AND uid=?", (uin, uid))
        con.execute("DELETE FROM card_fruits WHERE uin=? AND uid=?", (uin, uid))
        con.execute("DELETE FROM card_genes WHERE uin=? AND uid=?", (uin, uid))
        con.execute("DELETE FROM card_baptize WHERE uin=? AND uid=?", (uin, uid))
        con.execute("DELETE FROM card_baptize_pending WHERE uin=? AND uid=?", (uin, uid))
        con.execute("DELETE FROM card_armor WHERE uin=? AND uid=?", (uin, uid))
        con.execute("DELETE FROM card_armor_gems WHERE uin=? AND uid=?", (uin, uid))
        con.execute("DELETE FROM cards WHERE uin=? AND uid=?", (uin, uid))


def clear_cards_and_formation(uin):
    """Wipe a player's card collection + saved formation (they regenerate from owned plants on next
    login). Used to migrate the uid scheme: the client only treats uid>10000 as a REAL owned card."""
    with _db() as con:
        con.execute("DELETE FROM cards WHERE uin=?", (uin,))
        con.execute("DELETE FROM formation WHERE uin=?", (uin,))
        con.execute("DELETE FROM card_fruits WHERE uin=?", (uin,))
        con.execute("DELETE FROM card_avatar WHERE uin=?", (uin,))
        con.execute("DELETE FROM card_baptize WHERE uin=?", (uin,))
        con.execute("DELETE FROM card_baptize_pending WHERE uin=?", (uin,))
        con.execute("DELETE FROM card_armor WHERE uin=?", (uin,))
        con.execute("DELETE FROM card_armor_gems WHERE uin=?", (uin,))


def get_card_fruits(uin, uid):
    """Return {fruit_slot: level} for one owned card, omitting unupgraded slots."""
    with _db() as con:
        return {int(r["quality"]): int(r["level"]) for r in con.execute(
            "SELECT quality, level FROM card_fruits WHERE uin=? AND uid=? AND level>0 "
            "ORDER BY quality", (uin, int(uid)))}


def set_card_fruit_level(uin, uid, quality, level):
    """Persist the level of one fruit slot (quality is the client-side slot index)."""
    with _db() as con:
        con.execute("INSERT INTO card_fruits (uin, uid, quality, level) VALUES (?,?,?,?) "
                    "ON CONFLICT(uin, uid, quality) DO UPDATE SET level=excluded.level",
                    (uin, int(uid), int(quality), max(0, int(level))))


def get_card_genes(uin, uid):
    """{gene_id: {factor_level, factor_exp, attribute, level}} for one owned plant card."""
    with _db() as con:
        return {int(r["gene_id"]): {"factor_level": int(r["factor_level"]),
                                    "factor_exp": int(r["factor_exp"]),
                                    "attribute": int(r["attribute"]),
                                    "level": int(r["level"])}
                for r in con.execute(
                    "SELECT gene_id,factor_level,factor_exp,attribute,level FROM card_genes "
                    "WHERE uin=? AND uid=? ORDER BY gene_id", (uin, int(uid)))}


def set_card_gene(uin, uid, gene_id, factor_level, factor_exp, attribute, level):
    with _db() as con:
        con.execute("INSERT INTO card_genes "
                    "(uin,uid,gene_id,factor_level,factor_exp,attribute,level) VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(uin,uid,gene_id) DO UPDATE SET "
                    "factor_level=excluded.factor_level,factor_exp=excluded.factor_exp,"
                    "attribute=excluded.attribute,level=excluded.level",
                    (uin, int(uid), int(gene_id), int(factor_level), int(factor_exp),
                     int(attribute), int(level)))


def delete_card_genes(uin, uid):
    with _db() as con:
        con.execute("DELETE FROM card_genes WHERE uin=? AND uid=?", (uin, int(uid)))


def get_gene_test_stage(uin, mode, stage):
    with _db() as con:
        row = con.execute("SELECT current_sub,max_sub,rewards FROM gene_test_stages "
                          "WHERE uin=? AND mode=? AND stage=?", (uin, mode, stage)).fetchone()
    if not row:
        return {"current_sub": 0, "max_sub": 0, "rewards": {}}
    rewards = {}
    for pair in (row["rewards"] or "").split(","):
        if ":" in pair:
            try:
                iid, count = pair.split(":", 1)
                rewards[int(iid)] = int(count)
            except ValueError:
                pass
    return {"current_sub": int(row["current_sub"]), "max_sub": int(row["max_sub"]),
            "rewards": rewards}


def set_gene_test_stage(uin, mode, stage, current_sub, max_sub, rewards):
    reward_text = ",".join("%d:%d" % (int(i), int(n))
                          for i, n in sorted(rewards.items()) if n > 0)
    with _db() as con:
        con.execute("INSERT INTO gene_test_stages "
                    "(uin,mode,stage,current_sub,max_sub,rewards) VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(uin,mode,stage) DO UPDATE SET "
                    "current_sub=excluded.current_sub,max_sub=excluded.max_sub,rewards=excluded.rewards",
                    (uin, mode, stage, current_sub, max_sub, reward_text))


def get_card_filled_indices(uin, uid):
    """Return enhancement slots already filled for one owned card."""
    with _db() as con:
        return [r["piece_index"] for r in con.execute(
            "SELECT piece_index FROM card_fill WHERE uin=? AND uid=? ORDER BY piece_index",
            (uin, int(uid)))]


def add_card_filled_index(uin, uid, piece_index):
    """Mark one enhancement slot filled; return True only for a new slot."""
    with _db() as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO card_fill (uin, uid, piece_index) VALUES (?,?,?)",
            (uin, int(uid), int(piece_index)))
        return cur.rowcount > 0


def clear_card_filled_indices(uin, uid):
    """Clear enhancement slots after the client completes filling."""
    with _db() as con:
        con.execute("DELETE FROM card_fill WHERE uin=? AND uid=?", (uin, int(uid)))


def get_card_forge_level(uin, uid):
    """Return a card's persistent enhancement level."""
    with _db() as con:
        row = con.execute("SELECT forge_level FROM card_forge WHERE uin=? AND uid=?",
                          (uin, int(uid))).fetchone()
        return int(row["forge_level"]) if row else 0


def set_card_forge_level(uin, uid, forge_level):
    """Persist a card's enhancement level."""
    with _db() as con:
        con.execute("INSERT INTO card_forge (uin, uid, forge_level) VALUES (?,?,?) "
                    "ON CONFLICT(uin, uid) DO UPDATE SET forge_level=excluded.forge_level",
                    (uin, int(uid), int(forge_level)))


# --- card wardrobe / avatar skins ---------------------------------------------
def get_card_avatars(uin, uid):
    """Return persisted wardrobe rows keyed by avatar id.

    Missing rows are intentional: the server derives the initial default avatar
    from clientDB and only writes a row when the player buys, uses, or forges a
    skin.  Keeping this state independent from ``card_forge`` prevents normal
    card enhancement and wardrobe enhancement from overwriting one another.
    """
    with _db() as con:
        return {
            int(row["avatar_id"]): {
                "status": int(row["status"]),
                "forge_level": int(row["forge_level"]),
            }
            for row in con.execute(
                "SELECT avatar_id, status, forge_level FROM card_avatar "
                "WHERE uin=? AND uid=? ORDER BY avatar_id", (uin, int(uid)))
        }


def set_card_avatar(uin, uid, avatar_id, status=None, forge_level=None):
    """Upsert one avatar's status/forge level without resetting omitted fields."""
    uid, avatar_id = int(uid), int(avatar_id)
    with _db() as con:
        row = con.execute(
            "SELECT status, forge_level FROM card_avatar WHERE uin=? AND uid=? AND avatar_id=?",
            (uin, uid, avatar_id)).fetchone()
        old_status = int(row["status"]) if row else 1
        old_level = int(row["forge_level"]) if row else 0
        con.execute(
            "INSERT INTO card_avatar (uin, uid, avatar_id, status, forge_level) VALUES (?,?,?,?,?) "
            "ON CONFLICT(uin, uid, avatar_id) DO UPDATE SET status=excluded.status, "
            "forge_level=excluded.forge_level",
            (uin, uid, avatar_id, old_status if status is None else int(status),
             old_level if forge_level is None else int(forge_level)))


def delete_card_avatar(uin, uid, avatar_id):
    with _db() as con:
        con.execute("DELETE FROM card_avatar WHERE uin=? AND uid=? AND avatar_id=?",
                    (uin, int(uid), int(avatar_id)))


# --- plant baptize / refine (植物洗炼) ---------------------------------------------
# The four refineable attributes, in clientDB baptizeLimit.txt's column order (attack/defense/
# hp/ability). The DTO's `type` field is 1-based (= index+1) like every other attribute-type id
# in this client's data (cardDeckDetail "1:200", plantArmorAttributeInfo type 1..3).
BAPTIZE_ATTRS = ("attack", "defense", "hp", "ability")


def get_card_baptize(uin, uid):
    """A card's SAVED refine state: {'attack','defense','hp','ability','total_times'} (zeros if never
    refined). Values are in the battleCard scale; caps come from clientDB baptizeLimit.txt."""
    with _db() as con:
        row = con.execute("SELECT attack,defense,hp,ability,total_times FROM card_baptize "
                          "WHERE uin=? AND uid=?", (uin, int(uid))).fetchone()
    if not row:
        return {"attack": 0, "defense": 0, "hp": 0, "ability": 0, "total_times": 0}
    out = {attr: int(row[attr]) for attr in BAPTIZE_ATTRS}
    out["total_times"] = int(row["total_times"])
    return out


def set_card_baptize(uin, uid, values, total_times=None):
    """Persist a card's four refine attributes; total_times (lifetime rolls) is kept unless given."""
    with _db() as con:
        row = con.execute("SELECT total_times FROM card_baptize WHERE uin=? AND uid=?",
                          (uin, int(uid))).fetchone()
        total = int(row["total_times"]) if row else 0
        if total_times is not None:
            total = int(total_times)
        con.execute(
            "INSERT INTO card_baptize (uin, uid, attack, defense, hp, ability, total_times) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(uin, uid) DO UPDATE SET "
            "attack=excluded.attack, defense=excluded.defense, hp=excluded.hp, "
            "ability=excluded.ability, total_times=excluded.total_times",
            (uin, int(uid), int(values.get("attack", 0)), int(values.get("defense", 0)),
             int(values.get("hp", 0)), int(values.get("ability", 0)), total))


def get_baptize_pending(uin):
    """The player's ONE unsaved refine result as {uid, mode, rolls, <attrs>}, or None."""
    with _db() as con:
        row = con.execute("SELECT uid,mode,rolls,attack,defense,hp,ability "
                          "FROM card_baptize_pending WHERE uin=?", (uin,)).fetchone()
    if not row:
        return None
    out = {attr: int(row[attr]) for attr in BAPTIZE_ATTRS}
    out.update(uid=int(row["uid"]), mode=int(row["mode"]), rolls=int(row["rolls"]))
    return out


def set_baptize_pending(uin, uid, mode, rolls, values):
    """Store the unsaved refine result the client's 保存/放弃 dialog is looking at."""
    with _db() as con:
        con.execute(
            "INSERT INTO card_baptize_pending (uin, uid, mode, rolls, attack, defense, hp, ability) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(uin) DO UPDATE SET uid=excluded.uid, "
            "mode=excluded.mode, rolls=excluded.rolls, attack=excluded.attack, "
            "defense=excluded.defense, hp=excluded.hp, ability=excluded.ability",
            (uin, int(uid), int(mode), int(rolls), int(values.get("attack", 0)),
             int(values.get("defense", 0)), int(values.get("hp", 0)),
             int(values.get("ability", 0))))


def clear_baptize_pending(uin):
    """Delete + return the unsaved refine result (None if there is none). The caller decides whether
    the 洗炼幸运点 refund is paid out."""
    pending = get_baptize_pending(uin)
    if pending is None:
        return None
    with _db() as con:
        con.execute("DELETE FROM card_baptize_pending WHERE uin=?", (uin,))
    return pending


# --- 植物交换 (plant swap) --------------------------------------------------------
# The 植物交换 dialog moves a group of per-card state between two owned cards. All of it lives in
# per-uid tables, so an exchange is a delete+reinsert of the rows with the two uids swapped — done in
# ONE transaction per group so a crash can never duplicate or lose progress.
def swap_card_level(uin, uid_a, uid_b):
    """Exchange two cards' level + exp (植物交换's 等级完全互换). Returns {uid: (level, exp)} as it was
    BEFORE the swap, or {} if the player does not own both cards."""
    uid_a, uid_b = int(uid_a), int(uid_b)
    with _db() as con:
        rows = {int(r["uid"]): (int(r["level"]), int(r["exp"])) for r in con.execute(
            "SELECT uid, level, exp FROM cards WHERE uin=? AND uid IN (?,?)", (uin, uid_a, uid_b))}
        if len(rows) < 2:
            return {}
        con.execute("UPDATE cards SET level=?, exp=? WHERE uin=? AND uid=?",
                    (rows[uid_b][0], rows[uid_b][1], uin, uid_a))
        con.execute("UPDATE cards SET level=?, exp=? WHERE uin=? AND uid=?",
                    (rows[uid_a][0], rows[uid_a][1], uin, uid_b))
        return rows


def swap_card_fill(uin, uid_a, uid_b):
    """Exchange two cards' 拼图 progress: the board stage (cards.fill_quality) AND the piece indexes
    already placed on the current board. Returns the two fill_quality values as they were before the
    swap ({uid: quality}), or {} if both cards are not owned."""
    uid_a, uid_b = int(uid_a), int(uid_b)
    with _db() as con:
        stages = {int(r["uid"]): int(r["fill_quality"]) for r in con.execute(
            "SELECT uid, fill_quality FROM cards WHERE uin=? AND uid IN (?,?)", (uin, uid_a, uid_b))}
        if len(stages) < 2:
            return {}
        pieces = {uid_a: [], uid_b: []}
        for r in con.execute("SELECT uid, piece_index FROM card_fill WHERE uin=? AND uid IN (?,?)",
                             (uin, uid_a, uid_b)):
            pieces[int(r["uid"])].append(int(r["piece_index"]))
        con.execute("DELETE FROM card_fill WHERE uin=? AND uid IN (?,?)", (uin, uid_a, uid_b))
        con.executemany("INSERT INTO card_fill (uin, uid, piece_index) VALUES (?,?,?)",
                        [(uin, uid_b, p) for p in pieces[uid_a]]
                        + [(uin, uid_a, p) for p in pieces[uid_b]])
        con.execute("UPDATE cards SET fill_quality=? WHERE uin=? AND uid=?",
                    (stages[uid_b], uin, uid_a))
        con.execute("UPDATE cards SET fill_quality=? WHERE uin=? AND uid=?",
                    (stages[uid_a], uin, uid_b))
        return stages


def swap_card_fruits(uin, uid_a, uid_b):
    """Exchange two cards' 果实 (fruit slot) rows. Returns the number of rows moved."""
    uid_a, uid_b = int(uid_a), int(uid_b)
    with _db() as con:
        rows = [(int(r["uid"]), int(r["quality"]), int(r["level"])) for r in con.execute(
            "SELECT uid, quality, level FROM card_fruits WHERE uin=? AND uid IN (?,?)",
            (uin, uid_a, uid_b))]
        con.execute("DELETE FROM card_fruits WHERE uin=? AND uid IN (?,?)", (uin, uid_a, uid_b))
        con.executemany("INSERT INTO card_fruits (uin, uid, quality, level) VALUES (?,?,?,?)",
                        [(uin, uid_b if uid == uid_a else uid_a, quality, level)
                         for uid, quality, level in rows])
        return len(rows)


def swap_card_genes(uin, uid_a, uid_b):
    """Exchange two cards' 基因 rows. Returns the number of rows moved."""
    uid_a, uid_b = int(uid_a), int(uid_b)
    with _db() as con:
        rows = [(int(r["uid"]), int(r["gene_id"]), int(r["factor_level"]), int(r["factor_exp"]),
                 int(r["attribute"]), int(r["level"]))
                for r in con.execute("SELECT uid, gene_id, factor_level, factor_exp, attribute, "
                                     "level FROM card_genes WHERE uin=? AND uid IN (?,?)",
                                     (uin, uid_a, uid_b))]
        con.execute("DELETE FROM card_genes WHERE uin=? AND uid IN (?,?)", (uin, uid_a, uid_b))
        con.executemany("INSERT INTO card_genes (uin, uid, gene_id, factor_level, factor_exp, "
                        "attribute, level) VALUES (?,?,?,?,?,?,?)",
                        [(uin, uid_b if uid == uid_a else uid_a, gene_id, factor_level, factor_exp,
                          attribute, level)
                         for uid, gene_id, factor_level, factor_exp, attribute, level in rows])
        return len(rows)


def get_formation(uin, team_id=1):
    """{pos_index: uid} the player's saved battle team (empty if never set)."""
    with _db() as con:
        return {r["pos_index"]: r["uid"] for r in con.execute(
            "SELECT pos_index, uid FROM formation WHERE uin=? AND team_id=? ORDER BY pos_index",
            (uin, team_id))}


def get_formation_leader(uin, team_id=1):
    with _db() as con:
        row = con.execute("SELECT uid FROM formation_leader WHERE uin=? AND team_id=?",
                          (uin, team_id)).fetchone()
        return int(row["uid"]) if row else 0


def set_formation_leader(uin, team_id, uid):
    with _db() as con:
        con.execute("INSERT OR REPLACE INTO formation_leader (uin,team_id,uid) VALUES (?,?,?)",
                    (uin, team_id, int(uid)))


def get_formation_with_owner(uin, team_id=1):
    """Return ``{pos_index: (uid, owner_role_id)}`` for adventure lineups.

    ``owner_role_id == 0`` means the card is owned by this player.  This is an
    additive API: callers that only need the old ``{pos: uid}`` view can keep using
    :func:`get_formation` unchanged.
    """
    with _db() as con:
        return {r["pos_index"]: (r["uid"], int(r["owner_role_id"] or 0)) for r in con.execute(
            "SELECT pos_index, uid, owner_role_id FROM formation WHERE uin=? AND team_id=? ORDER BY pos_index",
            (uin, team_id))}


def set_formation(uin, team_id, placements, owner_roles=None):
    """Replace a team's layout.

    ``placements`` = ``{pos_index: uid}`` (or an iterable of pairs).  The optional
    ``owner_roles`` map stores a borrowed friend's role id per position while
    remaining fully backward compatible with all existing formation callers.
    """
    items = placements.items() if isinstance(placements, dict) else placements
    owner_roles = owner_roles or {}
    with _db() as con:
        con.execute("DELETE FROM formation WHERE uin=? AND team_id=?", (uin, team_id))
        con.executemany("INSERT INTO formation (uin, team_id, pos_index, uid, owner_role_id) VALUES (?,?,?,?,?)",
                        [(uin, team_id, int(p), int(u), int(owner_roles.get(p, 0) or 0))
                         for p, u in items])


def get_garden_pots(uin):
    """{field_id: (seed_id, plant_time)} for every UNLOCKED pot the player has."""
    with _db() as con:
        return {r["field_id"]: (r["seed_id"], r["plant_time"]) for r in con.execute(
            "SELECT field_id, seed_id, plant_time FROM garden_pots WHERE uin=?", (uin,))}


def unlock_garden_field(uin, field_id):
    """Ensure a pot row exists (unlocked, empty). Idempotent."""
    with _db() as con:
        con.execute("INSERT OR IGNORE INTO garden_pots (uin, field_id, seed_id, plant_time) "
                    "VALUES (?,?,0,0)", (uin, int(field_id)))


def set_garden_pot(uin, field_id, seed_id, plant_time):
    """Set a pot's planted seed + time (seed_id=0 clears it).

    Fertilizer metadata is optional so old callers retain their behavior.  Garden
    handlers that intentionally plant/clear/fertilize a pot pass it explicitly.
    """
    with _db() as con:
        con.execute("INSERT INTO garden_pots (uin, field_id, seed_id, plant_time, fertilizer_id, "
                    "last_fertilizer_time, level) VALUES (?,?,?,?,0,0,1) "
                    "ON CONFLICT(uin, field_id) DO UPDATE SET seed_id=excluded.seed_id, "
                    "plant_time=excluded.plant_time", (uin, int(field_id), int(seed_id), int(plant_time)))


def get_garden_pot_details(uin):
    """Return per-pot state, including the persistent upgrade level."""
    with _db() as con:
        return {r["field_id"]: {
            "seed_id": int(r["seed_id"] or 0),
            "plant_time": int(r["plant_time"] or 0),
            "fertilizer_id": int(r["fertilizer_id"] or 0),
            "last_fertilizer_time": int(r["last_fertilizer_time"] or 0),
            "level": max(1, int(r["level"] or 1)),
        } for r in con.execute(
            "SELECT field_id, seed_id, plant_time, fertilizer_id, last_fertilizer_time, level "
            "FROM garden_pots WHERE uin=?", (uin,))}


def update_garden_pot_meta(uin, field_id, fertilizer_id=None, last_fertilizer_time=None):
    """Update only fertilizer metadata for an already-unlocked pot."""
    assignments, values = [], []
    if fertilizer_id is not None:
        assignments.append("fertilizer_id=?")
        values.append(int(fertilizer_id))
    if last_fertilizer_time is not None:
        assignments.append("last_fertilizer_time=?")
        values.append(int(last_fertilizer_time))
    if not assignments:
        return
    values.extend([int(uin), int(field_id)])
    with _db() as con:
        con.execute("UPDATE garden_pots SET %s WHERE uin=? AND field_id=?" % ", ".join(assignments), values)


def update_garden_pot_level(uin, field_id, level):
    """Persist a pot's upgrade level without disturbing its crop/fertilizer state."""
    with _db() as con:
        con.execute("UPDATE garden_pots SET level=? WHERE uin=? AND field_id=?",
                    (max(1, int(level)), int(uin), int(field_id)))


def get_talents(uin):
    """List of plant-talent ids the player has studied (Greenhouse / 植物研究所)."""
    with _db() as con:
        return [r["talent_id"] for r in con.execute(
            "SELECT talent_id FROM talents WHERE uin=?", (uin,))]


def study_talent(uin, talent_id):
    """Record a studied talent (idempotent)."""
    with _db() as con:
        con.execute("INSERT OR IGNORE INTO talents (uin, talent_id) VALUES (?,?)",
                    (uin, int(talent_id)))


def has_talent(uin, talent_id):
    with _db() as con:
        return con.execute("SELECT 1 FROM talents WHERE uin=? AND talent_id=?",
                           (uin, int(talent_id))).fetchone() is not None


# --- nutrition reagents (试剂) ---------------------------------------------------
def get_reagents(uin):
    """Every owned reagent as {uid, base_id, level, position, slot_id} (list of dicts)."""
    with _db() as con:
        return [{"uid": r["uid"], "base_id": r["base_id"], "level": r["level"],
                 "exp": r["exp"], "position": r["position"], "slot_id": r["slot_id"]}
                for r in con.execute(
                    "SELECT uid, base_id, level, exp, position, slot_id FROM reagents WHERE uin=?", (uin,))]


def next_reagent_uid(uin):
    """A fresh, never-reused reagent uid (REAGENT_UID_BASE + a per-player incrementing seq)."""
    with _db() as con:
        row = con.execute("SELECT reagent_seq FROM players WHERE uin=?", (uin,)).fetchone()
        seq = (int(row["reagent_seq"]) if row and row["reagent_seq"] is not None else 0) + 1
        con.execute("UPDATE players SET reagent_seq=? WHERE uin=?", (seq, uin))
        return REAGENT_UID_BASE + seq


def add_reagent(uin, uid, base_id, level=1, position=0, slot_id=0):
    with _db() as con:
        con.execute("INSERT OR REPLACE INTO reagents (uin, uid, base_id, level, position, slot_id) "
                    "VALUES (?,?,?,?,?,?)",
                    (uin, int(uid), int(base_id), int(level), int(position), int(slot_id)))


def set_reagent_pos(uin, uid, position, slot_id):
    """Move a reagent to an equip slot (position>0, slot_id 1..8) or back to the bag (0, 0)."""
    with _db() as con:
        con.execute("UPDATE reagents SET position=?, slot_id=? WHERE uin=? AND uid=?",
                    (int(position), int(slot_id), uin, int(uid)))


def set_reagent_level(uin, uid, level):
    with _db() as con:
        con.execute("UPDATE reagents SET level=? WHERE uin=? AND uid=?", (int(level), uin, int(uid)))


def set_reagent_progress(uin, uid, level, exp):
    """Persist both the level and partial experience gained by merging reagents."""
    with _db() as con:
        con.execute("UPDATE reagents SET level=?, exp=? WHERE uin=? AND uid=?",
                    (int(level), max(0, int(exp)), uin, int(uid)))


def remove_reagent(uin, uid):
    with _db() as con:
        con.execute("DELETE FROM reagents WHERE uin=? AND uid=?", (uin, int(uid)))


# --- friends (好友) -----------------------------------------------------------
def get_friends(uin):
    """Every added friend as a dict {friend_role_id, friend_name, help_plant_id,
    help_plant_level, diamond_flag, diamond_blue_lvl, diamond_yellow_lvl,
    diamond_xinyue_lvl}, ordered by friend_role_id."""
    with _db() as con:
        return [dict(r) for r in con.execute(
            "SELECT friend_role_id, friend_name, help_plant_id, help_plant_level, "
            "diamond_flag, diamond_blue_lvl, diamond_yellow_lvl, diamond_xinyue_lvl "
            "FROM friends WHERE uin=? ORDER BY friend_role_id", (uin,))]


def get_friend_by_role_id(uin, friend_role_id):
    """Return one saved friend row, or ``None`` when it is not in the list."""
    with _db() as con:
        row = con.execute(
            "SELECT friend_role_id, friend_name, help_plant_id, help_plant_level, "
            "diamond_flag, diamond_blue_lvl, diamond_yellow_lvl, diamond_xinyue_lvl "
            "FROM friends WHERE uin=? AND friend_role_id=?",
            (int(uin), int(friend_role_id))).fetchone()
        return dict(row) if row else None


def add_friend(uin, friend_role_id, friend_name, help_plant_id, help_plant_level,
               diamond_flag=1, diamond_blue_lvl=0, diamond_yellow_lvl=0, diamond_xinyue_lvl=0):
    """Insert or replace a friend row."""
    with _db() as con:
        con.execute(
            "INSERT OR REPLACE INTO friends (uin, friend_role_id, friend_name, help_plant_id, "
            "help_plant_level, diamond_flag, diamond_blue_lvl, diamond_yellow_lvl, "
            "diamond_xinyue_lvl) VALUES (?,?,?,?,?,?,?,?,?)",
            (uin, int(friend_role_id), str(friend_name), int(help_plant_id),
             int(help_plant_level), int(diamond_flag), int(diamond_blue_lvl),
             int(diamond_yellow_lvl), int(diamond_xinyue_lvl)))


def remove_friend(uin, friend_role_id):
    with _db() as con:
        con.execute("DELETE FROM friends WHERE uin=? AND friend_role_id=?",
                    (uin, int(friend_role_id)))


def save_player(uin, **fields):
    """Update specific columns for a player."""
    if not fields:
        return
    sets = ", ".join("%s=?" % k for k in fields)
    with _db() as con:
        con.execute("UPDATE players SET %s WHERE uin=?" % sets,
                    list(fields.values()) + [uin])


# --- zombie island (勇闯僵尸岛) ----------------------------------------------------
# One climb per mode (0 = normal / Cmd_ZombieIsland_*, 1 = adv / Cmd_NewZombieIsland_*). The scalar
# progress lives in the players row (zi_* / zi_adv_*); the per-island chests and the arranged island
# lineup get their own tables because they are 1..N rows.
_ZI_PREFIX = {0: "zi_", 1: "zi_adv_"}


def island_progress(uin, mode):
    """(next_level, max_level, refresh_used, day, boxes_csv, precards_csv, last_role_level)."""
    pre = _ZI_PREFIX[int(mode)]
    with _db() as con:
        row = con.execute(
            "SELECT %slevel AS lvl, %smax AS mx, %srefresh_used AS used, %sday AS day, "
            "%sboxes AS boxes, %sprecards AS pre, %slast_role_level AS rl "
            "FROM players WHERE uin=?" % (pre, pre, pre, pre, pre, pre, pre), (uin,)).fetchone()
    if not row:
        return 1, 0, 0, "", "", "", 1
    return (int(row["lvl"] or 1), int(row["mx"] or 0), int(row["used"] or 0),
            row["day"] or "", row["boxes"] or "", row["pre"] or "", int(row["rl"] or 1))


def save_island_progress(uin, mode, **kw):
    """Persist island scalar fields. Keys are given WITHOUT the mode prefix (level/max/refresh_used/
    day/boxes/precards/last_role_level)."""
    pre = _ZI_PREFIX[int(mode)]
    fields = {}
    for key, value in kw.items():
        if key not in ("level", "max", "refresh_used", "day", "boxes", "precards",
                       "last_role_level"):
            raise KeyError("unknown zombie-island field %r" % key)
        fields[pre + key] = value
    save_player(uin, **fields)


def island_boxes(uin, mode):
    """{(level_id, box_id)} chests of this mode already opened."""
    with _db() as con:
        return {(r["level_id"], r["box_id"]) for r in con.execute(
            "SELECT level_id, box_id FROM zombie_island_box WHERE uin=? AND mode=?",
            (uin, int(mode)))}


def take_island_box(uin, mode, level_id, box_id):
    """Record a chest as opened. Returns True the first time only."""
    with _db() as con:
        cur = con.execute("INSERT OR IGNORE INTO zombie_island_box (uin, mode, level_id, box_id) "
                          "VALUES (?,?,?,?)", (uin, int(mode), int(level_id), int(box_id)))
        return cur.rowcount > 0


def island_precards(uin, mode):
    """[uid,...] the island lineup the prepare view arranged (slot order; [] = never set)."""
    with _db() as con:
        return [r["uid"] for r in con.execute(
            "SELECT uid FROM zombie_island_precard WHERE uin=? AND mode=? ORDER BY slot",
            (uin, int(mode)))]


def set_island_precards(uin, mode, uids):
    """Replace the island lineup with `uids` (slot order)."""
    with _db() as con:
        con.execute("DELETE FROM zombie_island_precard WHERE uin=? AND mode=?", (uin, int(mode)))
        con.executemany("INSERT INTO zombie_island_precard (uin, mode, slot, uid) VALUES (?,?,?,?)",
                        [(uin, int(mode), i, int(u)) for i, u in enumerate(uids)])


# --- 傀儡 (puppets) ---------------------------------------------------------------
# One row per owned puppet; `spars` holds the six equipment parts as "part:level:quality;...".
PUPPET_FIELDS = ("exp", "level", "star", "quality", "active_skill", "active_level",
                 "passive_skill", "passive_level", "spars")


def get_puppets(uin):
    """{base_id: {field: value}} for every puppet the player owns (empty when none yet)."""
    with _db() as con:
        rows = con.execute("SELECT * FROM puppets WHERE uin=?", (uin,)).fetchall()
    return {int(r["base_id"]): {k: r[k] for k in r.keys() if k not in ("uin", "base_id")}
            for r in rows}


def get_puppet(uin, base_id):
    """One puppet's row as a dict, or None."""
    return get_puppets(uin).get(int(base_id))


def save_puppet(uin, base_id, **fields):
    """Insert or update one puppet. Unknown keys raise (typos must not silently do nothing)."""
    for key in fields:
        if key not in PUPPET_FIELDS:
            raise KeyError("unknown puppet field %r" % key)
    with _db() as con:
        have = con.execute("SELECT 1 FROM puppets WHERE uin=? AND base_id=?",
                           (uin, int(base_id))).fetchone()
        if not have:
            con.execute("INSERT INTO puppets (uin, base_id) VALUES (?,?)", (uin, int(base_id)))
        if fields:
            sets = ", ".join("%s=?" % k for k in fields)
            con.execute("UPDATE puppets SET %s WHERE uin=? AND base_id=?" % sets,
                        list(fields.values()) + [uin, int(base_id)])


def puppet_fuben_progress(uin, mode, chapter_id):
    """{stage_id: (wave, times, day)} for one mode and chapter."""
    with _db() as con:
        rows = con.execute("SELECT stage_id, wave, times, day FROM puppet_fuben "
                           "WHERE uin=? AND mode=? AND chapter_id=?",
                           (uin, int(mode), int(chapter_id))).fetchall()
    return {int(r["stage_id"]): (int(r["wave"] or 0), int(r["times"] or 0), r["day"] or "")
            for r in rows}


def save_puppet_stage(uin, mode, chapter_id, stage_id, **fields):
    """Insert or update one 傀儡战境 chapter/stage's progress (wave/times/day)."""
    for key in fields:
        if key not in ("wave", "times", "day"):
            raise KeyError("unknown puppet stage field %r" % key)
    with _db() as con:
        have = con.execute("SELECT 1 FROM puppet_fuben WHERE uin=? AND mode=? AND chapter_id=? AND stage_id=?",
                           (uin, int(mode), int(chapter_id), int(stage_id))).fetchone()
        if not have:
            con.execute("INSERT INTO puppet_fuben (uin, mode, chapter_id, stage_id) VALUES (?,?,?,?)",
                        (uin, int(mode), int(chapter_id), int(stage_id)))
        if fields:
            sets = ", ".join("%s=?" % k for k in fields)
            con.execute("UPDATE puppet_fuben SET %s WHERE uin=? AND mode=? AND chapter_id=? AND stage_id=?" % sets,
                        list(fields.values()) + [uin, int(mode), int(chapter_id), int(stage_id)])


if __name__ == "__main__":
    init_db()
    p, new = get_or_create_player(700000123)
    print("created" if new else "loaded", "player:", dict(p))


def awaken_card_atomic(uin, uid, source_id, target_id, min_level, min_forge, cost):
    """Validate and consume materials with the UID update in one transaction."""
    costs = {}
    for iid, amount in cost:
        costs[iid] = costs.get(iid, 0) + amount
    with _db() as con:
        con.execute("BEGIN IMMEDIATE")
        card = con.execute("SELECT card_id,level FROM cards WHERE uin=? AND uid=?", (uin,uid)).fetchone()
        forge = con.execute("SELECT forge_level FROM card_forge WHERE uin=? AND uid=?", (uin,uid)).fetchone()
        if not card or card["card_id"] != source_id or card["level"] < min_level or (forge[0] if forge else 0) < min_forge:
            return False
        for iid, amount in costs.items():
            item = con.execute("SELECT count FROM items WHERE uin=? AND item_id=?", (uin,iid)).fetchone()
            if not item or item[0] < amount:
                return False
        for iid, amount in costs.items():
            con.execute("UPDATE items SET count=count-? WHERE uin=? AND item_id=?", (amount,uin,iid))
        con.execute("UPDATE cards SET card_id=? WHERE uin=? AND uid=?", (target_id,uin,uid))
    return True
