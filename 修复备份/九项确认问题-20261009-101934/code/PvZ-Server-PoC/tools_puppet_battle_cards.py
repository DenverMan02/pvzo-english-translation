#!/usr/bin/env python3
"""把「傀儡」补进客户端战报要用的卡牌表 (webroot\\clientDB.txt 的 battleCard.txt)。

为什么需要
----------
客户端渲染一场战斗时，会为每个单位走
    BattleServiceCommand.getResourceList -> PlanFactory.getPlanSettingByCardId
      -> SettingsModel.battleCardSettings.getBattleCardTempById(cardId)
而 `Dto_FightSide.puppetList`（field 16，上阵傀儡）里的 `baseId` 只有在 battleCard 表里有行时才能
查到计划；查不到 -> 计划为 null -> 取属性抛 `#1009` -> 战斗模块加载不完（表现：进战斗卡死）。
傀儡的 baseId（1000001/1000002/…）只在客户端 `dummyZombieInfo` 里，**battleCard 里一行都没有**，所以
普通战斗的战报里带傀儡必崩（这就是「进冒险就卡死」的根因）。

补什么
------
`dummyZombieInfo` 里每个「玩家傀儡」(isEnemy=0，共 20 个) 补一行 battleCard：
    id            = baseId（1000001…）
    type          = 1（僵尸阵营）
    name          = 傀儡名（武僧僵尸…）
    icon          = baseId（客户端按 /swf/icons/card/<icon>_0.png 取头像，傀儡面板同源）
    planClassName = dummyZombieInfo.resName（PVP_zombie_kongfu_basic…）
其余数值列抄一份现成的僵尸行当模板（默认 id=111 `zombie_nomal`，可用 --template 换）。

`planClassName` 为什么是 resName：客户端 Game.swf 里那张「战斗动画/计划」XML（battleCartoonSettings）
就是按这个 key 组织的，傀儡的节点本来就有，例如
    <node cardId="PVP_zombie_kongfu_basic" cardName="武僧僵尸">
      <assetsTree><assetUnit assetName="PVP_zombie_kongfu_basic" rightSideMirror="0"/></assetsTree>
      <attackTree><tree><plan planType="SerialPlan">…（MovePlanWarrior / ActorActionPlan /
      InjuredPlanNomalGroup … 全是数据驱动的 plan）
本地 bbone 也在：webroot\\swf\\battle\\animation\\unit\\PVP_zombie_kongfu_basic.bbone。
所以补上这一行后客户端既能查到计划、又能播傀儡的战斗动画 —— 傀儡就能站到植物旁边。

用法
----
    python tools_puppet_battle_cards.py                 # 只检查：哪些傀儡缺卡行
    python tools_puppet_battle_cards.py --apply         # 补行（先备份，可 --revert 还原）
    python tools_puppet_battle_cards.py --revert        # 从备份还原 clientDB.txt
    python tools_puppet_battle_cards.py --template 111  # 换数值模板行（默认 zombie_nomal）

补完 **重启服务器**（服务器会把 battleCard 的 id 集合缓存一次），之后
`pvz_poc_server.py:client_knows_puppet_cards()` 会返回 True，战报就会在任何战斗里带上傀儡。
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENTDB = os.path.abspath(os.path.join(HERE, "..", "webroot", "clientDB.txt"))
BACKUP = os.path.abspath(os.path.join(HERE, "backups", "clientDB.txt.before-puppet-cards"))


def read_archive(path):
    """[(name, data)] — clientDB.txt 是一串 4 字节大端长度 + 名字 + 4 字节大端长度 + 数据。"""
    data = open(path, "rb").read()
    segs, pos = [], 0
    while pos + 4 <= len(data):
        nlen = int.from_bytes(data[pos:pos + 4], "big"); pos += 4
        name = data[pos:pos + nlen].decode("utf-8", "replace"); pos += nlen
        dlen = int.from_bytes(data[pos:pos + 4], "big"); pos += 4
        segs.append((name, data[pos:pos + dlen])); pos += dlen
    return segs


def write_archive(path, segs):
    out = bytearray()
    for name, body in segs:
        nb = name.encode("utf-8")
        out += len(nb).to_bytes(4, "big") + nb + len(body).to_bytes(4, "big") + body
    open(path, "wb").write(bytes(out))


def table_of(segs, name):
    """(index, name, raw bytes) of that table's segment, or (None, name, b"")."""
    for i, (n, body) in enumerate(segs):
        if n in (name, name + ".txt"):
            return i, n, body
    return None, name, b""


def rows_of(body):
    eol = b"\r\n" if b"\r\n" in body else b"\n"
    return eol, [r.split(b"\t") for r in body.split(eol) if r.strip()]


def puppet_rows(segs):
    """[(baseId, name, resName)] for every 玩家傀儡 in dummyZombieInfo (isEnemy = 0)."""
    _, _, dz = table_of(segs, "dummyZombieInfo.txt")
    if not dz:
        raise SystemExit("clientDB has no dummyZombieInfo.txt")
    _, rows = rows_of(dz)
    ci = {c.decode("utf-8", "replace"): i for i, c in enumerate(rows[0])}
    out = []
    for r in rows[1:]:
        if r[ci["isEnemy"]] != b"0":
            continue
        out.append((r[ci["id"]].decode(), r[ci["name"]].decode("utf-8", "replace"),
                    r[ci["resName"]].decode()))
    if not out:
        raise SystemExit("dummyZombieInfo lists no 玩家傀儡 (isEnemy = 0)")
    return out


def build_new_rows(segs, template_id, missing):
    """The battleCard rows to append: a copy of the template row with the 傀儡 fields swapped in."""
    _, _, bc = table_of(segs, "battleCard.txt")
    _, rows = rows_of(bc)
    ci = {c.decode("utf-8", "replace"): i for i, c in enumerate(rows[0])}
    template = None
    for r in rows[1:]:
        if r[ci["id"]].decode() == template_id:
            template = r
            break
    if template is None:                                     # fall back to the first plain zombie row
        for r in rows[1:]:
            if r[ci["type"]] == b"1" and not r[ci["planClassName"]].startswith(b"PVP_"):
                template = r
                break
    if template is None:
        raise SystemExit("battleCard has no zombie row to use as a template")
    eol, _ = rows_of(bc)
    added = []
    for base_id, name, res_name in missing:
        row = list(template)
        row[ci["id"]] = base_id.encode()
        row[ci["tdId"]] = b"0"                               # no TD template for a 傀儡
        row[ci["name"]] = name.encode("utf-8")
        row[ci["type"]] = b"1"                               # zombie side
        row[ci["subType"]] = b"0"
        row[ci["icon"]] = base_id.encode()                   # /swf/icons/card/<icon>_0.png
        row[ci["des"]] = ("傀儡：%s（%s）" % (name, base_id)).encode("utf-8")
        row[ci["planClassName"]] = res_name.encode()         # the Game.swf cartoon/plan cardId
        row[ci["hideBone"]] = b""
        if len(row) != len(ci):
            raise SystemExit("template row has %d cells, header has %d" % (len(row), len(ci)))
        added.append((base_id, res_name, b"\t".join(row)))
    return eol, added


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = sys.argv[1:]
    if not os.path.isfile(CLIENTDB):
        raise SystemExit("clientDB.txt not found: %s" % CLIENTDB)
    if "--revert" in args:
        if not os.path.isfile(BACKUP):
            raise SystemExit("no backup at %s" % BACKUP)
        shutil.copyfile(BACKUP, CLIENTDB)
        print("reverted %s from %s" % (CLIENTDB, BACKUP))
        return 0
    segs = read_archive(CLIENTDB)
    idx, name, body = table_of(segs, "battleCard.txt")
    _, rows = rows_of(body)
    ci = {c.decode("utf-8", "replace"): i for i, c in enumerate(rows[0])}
    have = {r[ci["id"]].decode() for r in rows[1:]}
    puppets = puppet_rows(segs)
    missing = [p for p in puppets if p[0] not in have]
    print("傀儡 (dummyZombieInfo isEnemy=0): %d" % len(puppets))
    print("battleCard rows: %d, 缺傀儡卡行: %d" % (len(rows) - 1, len(missing)))
    widths = sorted({len(r) for r in rows})
    if len(widths) != 1:                                     # a split row must never ship
        print("WARNING: battleCard is MALFORMED, row widths %s — run --revert!" % widths)
    for base_id, pname, res in puppets:
        print("   %-9s %-12s %-28s %s" % (base_id, pname, res,
                                          "OK" if base_id in have else "MISSING"))
    if "--apply" not in args:
        print("\n(--apply to add the missing rows, --revert to restore the backup)")
        return 0
    if not missing:
        print("\nnothing to do — every 傀儡 already has a battleCard row")
        return 0
    if not os.path.isfile(BACKUP):                           # keep the pristine copy once
        os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
        shutil.copyfile(CLIENTDB, BACKUP)
        print("\nbacked up -> %s" % BACKUP)
    template_id = args[args.index("--template") + 1] if "--template" in args else "111"
    eol, added = build_new_rows(segs, template_id, missing)
    extra = eol.join(rows_bytes for _bid, _res, rows_bytes in added)
    if not body.endswith(eol):
        extra = eol + extra
    segs[idx] = (name, body + extra + eol)
    write_archive(CLIENTDB, segs)
    print("\nadded %d battleCard row(s) to %s" % (len(added), CLIENTDB))
    for base_id, res, _ in added:
        print("   id=%s planClassName=%s" % (base_id, res))
    # Self-check: the table must still be RECTANGULAR. A row split across lines turns every cell into
    # its own 1-cell "row", and the loader then reads junk rows — e.g. a cell "1301" would shadow the
    # real card 1301 (that is exactly what a `\t`/EOL mix-up here caused once). See tests/test_recycle_swap.
    _, _, body2 = table_of(read_archive(CLIENTDB), "battleCard.txt")
    _eol2, rows2 = rows_of(body2)
    widths = sorted({len(r) for r in rows2})
    print("battleCard now %d rows, row widths %s" % (len(rows2) - 1, widths))
    if len(widths) != 1:
        raise SystemExit("MALFORMED table (row widths %s) — run --revert now!" % widths)
    print("restart the server so client_knows_puppet_cards() re-reads the table.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

