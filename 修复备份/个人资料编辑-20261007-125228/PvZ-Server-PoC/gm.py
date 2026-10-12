#!/usr/bin/env python3
"""
GM helper — inspect or edit a player's save (players.db).

  python gm.py <uin>                          # show the player's save
  python gm.py <uin> level 20                 # set a field
  python gm.py <uin> level 20 coin 100000 sun 500   # set several
  python gm.py <uin> item 4001 20             # give 20x item 4001 (TD keys)
  python gm.py <uin> item 53100 -5            # remove 5x item 53100
  python gm.py <uin> passto 1 5               # clear stage 1 up through level 5 (all subs)
  python gm.py <uin> passto 1 3 1             # clear stage 1 up through exactly level 3-1
  python gm.py <uin> vip 3                     # set VIP tier (0=off, 1/2/3); relaunch to apply
  python gm.py <uin> tdpriv 1                  # toggle 塔防特权 (0=off, 1=on); relaunch to apply
  python gm.py <uin> addfriend 700000201 Dave 1200 10
                                              # add a friend whose TD friend-card is Peashooter@Lv10
  python gm.py <uin> addfriend 700000201 Dave 1200 10 1 0 0 0
                                              # ...with diamond card: flag=1, blue=0/yellow=0/xinyue=0
  python gm.py <uin> delfriend 700000201      # remove a friend by role id
  python gm.py <uin> diamond 1 7 0 0          # set own 钻石卡: flag=1, blue=Lv7, yellow=0, xinyue=0
  python gm.py <uin> listfriends              # show this save's friends

Changes take effect on the player's next login (or next bag refresh). Bump `level` to
unlock the town, grant item 4001 (塔防钥匙/TD keys) to open the key-gated map gates, etc.

`passto <stage> <level> [sub]` marks every level up to that point cleared (full stars),
unlocks the gates + reward-plants along the way, and sets it as your latest level. Args map
to the in-game "关卡 <level>-<sub>" within chapter <stage>; omit sub to clear the whole level.
Note: reaching a later CHAPTER also needs a role level (adventure ch.3 opens at lvl 8, ch.4
at 12, ...), so pair with e.g. `gm.py <uin> level 12` to actually enter it.
"""
import os
import sys
import player_store as store


def _clientdb_dicts(table_name):
    """Read a clientDB table into a list of {column: value} dicts."""
    if getattr(sys, "frozen", False):
        # 打包成 exe：本模块被装入启动器，基目录 = exe 所在的游戏根目录
        base = os.path.dirname(os.path.abspath(sys.executable))
        path = os.path.abspath(os.path.join(base, "webroot", "clientDB.txt"))
    else:
        path = os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "webroot", "clientDB.txt"))
    if not os.path.isfile(path):
        return []
    data = open(path, "rb").read()
    pos = 0
    while pos + 4 <= len(data):
        nlen = int.from_bytes(data[pos:pos + 4], "big"); pos += 4
        name = data[pos:pos + nlen].decode("utf-8", "replace"); pos += nlen
        dlen = int.from_bytes(data[pos:pos + 4], "big"); pos += 4
        chunk = data[pos:pos + dlen]; pos += dlen
        if name == table_name:
            rows = [r.split("\t") for r in
                    chunk.decode("utf-8", "replace").replace("\r", "").split("\n") if r.strip()]
            return [dict(zip(rows[0], r)) for r in rows[1:]]
    return []


def _i(d, k, default=0):
    try:
        return int(d.get(k, default) or default)
    except ValueError:
        return default


def passto(uin, stage, level, sub):
    """Mark every main level up to (stage, level, sub) cleared (full stars), unlock the
    gates and reward-plants along the way, and set the latest-level pointer."""
    target = (stage, level, sub)
    levels = [d for d in _clientdb_dicts("levelselectInfo.txt")
              if (d.get("challengeLevelId", "0") or "0") in ("0", "")]
    npass = nplant = 0
    for d in levels:
        key = (_i(d, "stageId"), _i(d, "levelId"), _i(d, "subLevelId"))
        if key <= target:  # lexicographic = progression order (stage, then level, then sub)
            info = d.get("achievementInfo", "") or ""
            ach = [int(p.split(":")[0]) for p in info.split("|") if p]
            store.pass_level(uin, key[0], key[1], key[2], ach)
            npass += 1
            bp = _i(d, "bonusPlant")
            if bp:
                store.unlock_plant(uin, bp)
                nplant += 1
    ngate = 0
    for d in _clientdb_dicts("levelselectGateInfo.txt"):
        key = (_i(d, "stageId"), _i(d, "levelId"), _i(d, "subLevelId"))
        if key <= target:
            store.unlock_gate(uin, key[0], _i(d, "gateId"))
            ngate += 1
    store.save_player(uin, td_stage=stage, td_level=level, td_sub=sub)
    print("uin=%d: cleared %d levels, unlocked %d gates + %d reward-plants; latest = %d-%d (sub %d)"
          % (uin, npass, ngate, nplant, stage, level, sub))
    print("  plants now:", store.get_player_plants(uin))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    store.init_db()
    uin = int(sys.argv[1])
    # Ensure the row exists BEFORE updating (UPDATE on a missing row is a no-op).
    player, is_new = store.get_or_create_player(uin)
    args = sys.argv[2:]
    if args and args[0] == "item":
        item_id, delta = int(args[1]), int(args[2])
        new = store.add_item(uin, item_id, delta)
        print("uin=%d item %d -> count now %d" % (uin, item_id, new))
        print("bag:", store.get_items(uin))
        return
    if args and args[0] == "passto":
        stage, level = int(args[1]), int(args[2])
        sub = int(args[3]) if len(args) > 3 else 99   # 99 = all subs of that level
        passto(uin, stage, level, sub)
        return
    if args and args[0] == "vip":
        # vip <tier>  -> set the account's VIP tier (0=off, 1 阳光/2 财富/3 金壶). Drives the whole
        # privilege bundle (speed, sweeps, gifts, stat boosts, Winter Melon@VIP3) on next login.
        import pvz_poc_server as srv
        tier = int(args[1]) if len(args) > 1 else 0
        if not 0 <= tier <= 3:
            print("VIP tier must be 0 (off), 1, 2, or 3")
            return
        store.save_player(uin, vip_level=tier)
        srv.load_vip_tiers()
        t = srv._VIP_TIERS.get(tier)
        if tier == 0:
            print("uin=%d: VIP turned OFF" % uin)
        else:
            print("uin=%d: VIP tier %d (%s) — %d privileges granted"
                  % (uin, tier, t["name"], len(t["privileges"])))
            if srv._VIP_PLANT_PRIV in t["privileges"]:
                print("  includes the exclusive Winter Melon plant (6900) + deployed stat boosts")
        print("  -> RELAUNCH the client (not just reconnect) so the VIP config + plant apply.")
        return
    if args and args[0] == "tdpriv":
        # tdpriv <0|1>  -> toggle 塔防特权 (TD privilege). On=1 unlocks the privilege-plant list in
        # TD levels (Cmd_TD_PrivilegeInfo_SC.hasPrivilege=true + Cmd_TD_OpenPrivilege_SC push at
        # login). Off=0 returns to the default no-privilege state.
        on = int(args[1]) if len(args) > 1 else 0
        if on:
            # far-future expiry so it stays on until toggled off
            import time as _t
            store.save_player(uin, td_privilege=1, td_privilege_endtime=int(_t.time()) + 86400 * 365)
        else:
            store.save_player(uin, td_privilege=0, td_privilege_endtime=0)
        print("uin=%d: TD privilege %s" % (uin, "ON" if on else "OFF"))
        print("  -> RELAUNCH the client so the privilege push applies.")
        return
    if args and args[0] == "addfriend":
        # addfriend <role_id> <name> <plant_id> <plant_level> [diamond_flag] [blue_lvl]
        #           [yellow_lvl] [xinyue_lvl]
        # Adds (or replaces) a friend whose TD friend-card is the given plant at the given level,
        # and whose diamond card (钻石卡) shows the configured diamond levels. plant_id is a
        # tdPlantId (e.g. 1100 sunflower, 1200 peashooter).
        if len(args) < 5:
            print("usage: addfriend <role_id> <name> <plant_id> <plant_level> "
                  "[diamond_flag] [blue_lvl] [yellow_lvl] [xinyue_lvl]")
            return
        role_id = int(args[1])
        name = args[2]
        plant_id = int(args[3])
        plant_level = int(args[4])
        d_flag = int(args[5]) if len(args) > 5 else 1
        d_blue = int(args[6]) if len(args) > 6 else 0
        d_yellow = int(args[7]) if len(args) > 7 else 0
        d_xinyue = int(args[8]) if len(args) > 8 else 0
        store.add_friend(uin, role_id, name, plant_id, plant_level,
                         d_flag, d_blue, d_yellow, d_xinyue)
        print("uin=%d: friend %d (%s) -> card plant %d @ Lv%d, diamond flag=%d blue=%d yellow=%d xinyue=%d"
              % (uin, role_id, name, plant_id, plant_level, d_flag, d_blue, d_yellow, d_xinyue))
        print("  -> RELAUNCH the client so the friend list + friend-card chooser reload.")
        return
    if args and args[0] == "delfriend":
        if len(args) < 2:
            print("usage: delfriend <role_id>")
            return
        store.remove_friend(uin, int(args[1]))
        print("uin=%d: removed friend %d" % (uin, int(args[1])))
        return
    if args and args[0] == "listfriends":
        friends = store.get_friends(uin)
        if not friends:
            print("uin=%d: no friends" % uin)
            return
        print("uin=%d: %d friend(s):" % (uin, len(friends)))
        for f in friends:
            print("  role=%d  name=%r  card=plant %d @ Lv%d  diamond flag=%d blue=%d yellow=%d xinyue=%d"
                  % (f["friend_role_id"], f["friend_name"], f["help_plant_id"],
                     f["help_plant_level"], f["diamond_flag"], f["diamond_blue_lvl"],
                     f["diamond_yellow_lvl"], f["diamond_xinyue_lvl"]))
        return
    if args and args[0] == "diamond":
        # diamond <flag> <blue_lvl> <yellow_lvl> <xinyue_lvl>
        # Sets the player's OWN Dto_DiamondInfo (pushed at login via Cmd_Vip_SyncVipInfo_SC) so the
        # 钻石卡 / ModuleBlueDiamond window displays correctly. flag=1 => show the card; 0 => hide.
        # blue_lvl/yellow_lvl/xinyue_lvl = the Dto_DiamondLevel.level for E_DiamondType
        # (1=Blue, 3=Yellow, 4=XinYue). 0 => that diamond isn't granted.
        # Purchase cost: 20 diamonds (gold ticket first, then gift present_ticket).
        if len(args) < 5:
            print("usage: diamond <flag> <blue_lvl> <yellow_lvl> <xinyue_lvl>")
            return
        flag = int(args[1]); blue = int(args[2]); yellow = int(args[3]); xinyue = int(args[4])
        if flag:   # purchasing / activating the card — deduct 20 diamonds
            COST = 20
            player = store.get_or_create_player(uin)[0]
            total = int(player.get("ticket", 0) or 0) + int(player.get("present_ticket", 0) or 0)
            if total < COST:
                print("uin=%d: need %d diamonds to purchase, have %d — abort." % (uin, COST, total))
                return
            # deduct from gold ticket first, overflow from gift present_ticket
            if int(player.get("ticket", 0) or 0) >= COST:
                store.save_player(uin, ticket=int(player.get("ticket", 0)) - COST)
            else:
                remain = COST - int(player.get("ticket", 0) or 0)
                store.save_player(uin, ticket=0, present_ticket=int(player.get("present_ticket", 0) or 0) - remain)
        store.save_player(uin, diamond_flag=flag, diamond_blue_lvl=blue,
                          diamond_yellow_lvl=yellow, diamond_xinyue_lvl=xinyue)
        print("uin=%d: diamond card -> flag=%d blue=Lv%d yellow=Lv%d xinyue=Lv%d"
              % (uin, flag, blue, yellow, xinyue))
        print("  -> RELAUNCH the client so Cmd_Vip_SyncVipInfo_SC re-pushes.")
        return
    if args and args[0] == "cardlevel":
        # cardlevel <level> [cardId]  -> set the exp of the player's card(s) so they reach <level>.
        # Omit cardId to level EVERY owned card. Uses the real cardLevel.txt totalExp curve. Test-only
        # shortcut that bypasses the bean UI; the card grows its stats just like a normal level-up.
        import pvz_poc_server as srv
        level = int(args[1])
        only_cid = int(args[2]) if len(args) > 2 else None
        srv.load_card_level_curve()
        total = next((t for lv, t, _n in srv._CARD_LEVEL_CURVE if lv == level), 0)
        acct = int(player.get("level", 1))
        done = []
        for cuid, cid, _lv, _exp in store.get_cards(uin):
            if only_cid and cid != only_cid:
                continue
            new_lv, kept = srv.card_level_from_exp(total, cid, acct)
            store.set_card_level(uin, cuid, new_lv, kept)
            done.append((cid, new_lv))
        print("uin=%d set card level %d (acct %d caps it): %s" % (uin, level, acct, done))
        print("NOTE: account level gates the card cap — raise it first (e.g. `gm.py %d level 50`) for higher card levels." % uin)
        return
    if args:
        fields = {}
        for i in range(0, len(args) - 1, 2):
            k, v = args[i], args[i + 1]
            fields[k] = int(v) if v.lstrip("-").isdigit() else v
        store.save_player(uin, **fields)
        print("updated uin=%d: %s\n" % (uin, fields))
        player, _ = store.get_or_create_player(uin)  # reload to show new values
    print("%s uin=%d:" % ("CREATED" if is_new else "player", uin))
    for k, v in player.items():
        print("  %-18s = %r" % (k, v))


if __name__ == "__main__":
    main()
