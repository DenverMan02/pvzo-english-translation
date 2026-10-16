"""Translate hardcoded battle-card tooltip labels in the live SWF client.

This patches live battle/formation tooltip and HUD modules plus Endless Fuben messages; it does not edit clientDB files.
Only display-string constants are edited: strings used as ABC namespace or
multiname identifiers are excluded to avoid renaming ActionScript properties.
The launcher calls apply_translation() before the web server starts.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import struct
import sys
import zlib
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
WEBROOT = SERVER.parent / "webroot"
PATCH_HELPERS = SERVER / "client_patches" / "card-exp-bean-repeat-20260926"
if str(PATCH_HELPERS) not in sys.path:
    sys.path.insert(0, str(PATCH_HELPERS))

from patch_module_card_clickthrough import encode_u30, find_abc, parse_abc, s32, u30

# Longest phrases first. These are player-facing terms visible in battle mode.
TRANSLATIONS = {
    "基础战力": "Base Power",
    "免费重置进度": "Reset Progress for Free",
    "购买重置进度": "Purchase Progress Reset",
    "本难度历史最高成绩为第{0}关，可以使用一键扫荡到第{1}关": "Best result in this difficulty: stage {0}. Use Quick Sweep to clear up to stage {1}.",
    "每日0点恢复免费次数；用完后每次{2}钻购买重置": "Free resets replenish daily at midnight; after they run out, each reset costs {2} diamonds.",
    "领取奖励": "Claim Rewards",
    "今日免费重置": "Free Resets Today",
    "基础属性": "Base Stats",
    "角色属性": "Character Stats",
    "植物属性": "Plant Stats",
    "攻击速度": "Attack Speed",
    "攻击范围": "Attack Range",
    "当前生命": "Current Health",
    "最大生命": "Max Health",
    "经验值": "Experience",
    "角色等级": "Character Level",
    "植物等级": "Plant Level",
    "等级上限": "Level Cap",
    "升级所需": "Required to Level Up",
    "主动技能": "Active Skill",
    "被动技能": "Passive Skill",
    "技能说明": "Skill Description",
    "属性加成": "Stat Bonus",
    "当前战力": "Current Power",
    "总生命": "Total Health",
    "每日任务": "Daily Quests",
    "领取成功": "Claimed Successfully",
    "挑战成功": "Challenge Complete",
    "挑战失败": "Challenge Failed",
    "战斗记录": "Battle Records",
    "战报": "Battle Report",
    "战斗奖励": "Battle Rewards",
    "返回战斗": "Return to Battle",
    "开始战斗": "Start Battle",
    "结束战斗": "End Battle",
    "继续挑战": "Continue Challenge",
    "已解锁": "Unlocked",
    "未解锁": "Locked",
    "暂无奖励": "No Rewards Available",
    "暂无战报": "No Battle Reports",
    "暂无数据": "No Data Available",
    "加载失败": "Failed to Load",
    "加载中…": "Loading…",
    "请求失败": "Request Failed",
    "操作成功": "Operation Successful",
    "操作失败": "Operation Failed",
    "确定": "Confirm",
    "取消": "Cancel",
    "返回": "Back",
    "关闭": "Close",
    "开始": "Start",
    "升级": "Level Up",
    "更换": "Change",
    "重置": "Reset",
    "刷新": "Refresh",
    "挑战": "Challenge",
    "奖励": "Rewards",
    "排行榜": "Leaderboard",
    "好友": "Friends",
    "商店": "Shop",
    "设置": "Settings",
    "背包": "Inventory",
    "邮件": "Mail",
    "任务": "Quests",
    "活动": "Events",
    "关卡": "Stage",
    "战斗": "Battle",
    "角色": "Character",
    "领取": "Claim",
    "进行中": "In Progress",
    "等待中": "Pending",
    "攻击力": "Attack",
    "防御力": "Defense",
    "技能伤害": "Skill Damage",
    "暴击伤害": "Critical Damage",
    "暴击率": "Critical Chance",
    "暴击": "Critical",
    "抗暴": "Crit Resistance",
    "命中": "Accuracy",
    "闪避": "Dodge",
    "韧性": "Tenacity",
    "攻击": "Attack",
    "防御": "Defense",
    "技力": "Skill Power",
    "护盾": "Shield",
    "减伤": "Damage Reduction",
    "吸血": "Lifesteal",
    "速度": "Speed",
    "强化": "Enhancement",
    "星级": "Star Level",
    "生命上限": "Max Health",
    "部队总先攻": "Team Initiative",
    "上阵植物": "Plants Deployed",
    "返回小镇": "Return to Town",
    "查看星性": "View Attributes",
    "队伍总战力": "Total Team Power",
    "战斗力": "Combat Power",
    "今日免费重置：": "Free resets today: ",
    "剩余次数：": "Remaining resets: ",
    "金钻消耗：": "Diamond cost: ",
    "暂无新的战报！": "No new battle reports!",
    "暂无奖励！": "No rewards available!",
    "战场关闭中！": "Battlefield is closing!",
    "挑战失败！当前成绩为第": "Challenge failed! Current best: stage ",
    "关成功！": " cleared!",
    "挑战第": "Challenge stage ",
    "重新挑战": "Retry Challenge",
    "重置次数已用完": "No resets remaining",
    "每日0点恢复": "Resets daily at midnight",
    "总战力": "Total Power",
    "植物拼图": "Plant Puzzle",
    "威望进化": "Prestige Evolution",
    "队长技能": "Leader Skill",
    "普通攻击": "Basic Attack",
    "营养室": "Nutrition Room",
    "未领取": "Unclaimed",
    "已领取": "Claimed",
    "稀有度": "Rarity",
    "世界树": "World Tree",
    "实验室": "Laboratory",
    "普攻": "Basic Attack",
    "健康：": "Health: ",
    "生命值": "Health",
    "生命：": "Health: ",
    "健康:": "Health: ",
    "生命:": "Health: ",
    "健康": "Health",
    "生命": "Health",
    "战力": "Power",
    "先攻": "Initiative",
    "总和": "Total",
    "果实": "Fruit",
    "基因": "Genes",
    "装备": "Equipment",
    "洗炼": "Refinement",
    "装甲": "Armor",
    "装扮": "Cosmetics",
    "技能": "Skill",
}
ORDERED_TRANSLATIONS = sorted(TRANSLATIONS.items(), key=lambda item: len(item[0]), reverse=True)
MODULE_NAMES = (
    "Game_n.swf", "Game_p.swf", "ModuleCard.swf", "ModuleCardBar.swf",
    "ModuleBattleLineup.swf", "ModuleBattle.swf", "ModuleEmbattle.swf", "ModuleEndlessFuben.swf",
    "ModuleCardFruit.swf", "ModulePlantArmor.swf", "ModulePlantEvolution.swf",
    "ModulePlantTalent.swf", "ModulePlantExplore.swf", "ModuleAwake.swf",
    "ModuleNutrition.swf", "ModuleNutritionExchange.swf", "ModuleGarden.swf",
    "ModuleWorldTree.swf", "ModuleLab.swf", "ModuleStrengthen.swf",
    "ModuleTreasure.swf", "ModuleUnion.swf", "ModuleRoleInfo.swf",
    "ModuleCardDNA.swf", "ModuleVip.swf", "ModulePrivilege.swf",
    "ModulePrivilegeBar.swf", "ModuleDailyTD.swf", "ModuleTD.swf", "ModuleTD_p.swf",
)
VERSION_NAME = "fileVer.xml"


# Short stat names should only be translated when they are standalone labels or
# delimited stat tokens. This avoids turning ordinary Chinese sentences into a
# Chinese/English mixture just because they mention health, attacks, or skills.
CONTEXTUAL_TERMS = {
    "攻击力", "防御力", "生命上限", "生命值", "健康", "生命",
    "技能伤害", "暴击伤害", "暴击率", "总和", "战力", "先攻",
    "果实", "基因", "装备", "洗炼", "装甲", "装扮", "技能",
    "普攻", "技力", "攻击", "防御", "命中", "闪避", "韧性",
    "暴击", "抗暴", "护盾", "减伤", "吸血", "速度", "强化", "星级",
    "确定", "取消", "返回", "关闭", "开始", "升级", "更换", "重置", "刷新",
    "挑战", "奖励", "排行榜", "好友", "商店", "设置", "背包", "邮件", "任务", "活动",
    "关卡", "战斗", "角色", "领取", "进行中", "等待中",
}
TOKEN_BOUNDARY_RE = r"[\s\[\]【】()（）:：+/\-·,，]"


def _translate_display_string(value: str) -> str:
    # First translate explicit UI labels, multi-word labels, and whole phrases.
    for old, new in ORDERED_TRANSLATIONS:
        if old not in CONTEXTUAL_TERMS:
            value = value.replace(old, new)

    # Then translate short stat terms only at clear label/token boundaries.
    for old, new in ORDERED_TRANSLATIONS:
        if old not in CONTEXTUAL_TERMS:
            continue
        pattern = r"(^|" + TOKEN_BOUNDARY_RE + r")" + re.escape(old) + r"(?=$|" + TOKEN_BOUNDARY_RE + r"|\d|[%％])"
        value = re.sub(pattern, lambda match, replacement=new: match.group(1) + replacement, value)
    return value


def _patch_abc_strings(abc: bytes) -> tuple[bytes, int, int, int]:
    """Patch eligible display strings while preserving the ABC pool structure."""
    info = parse_abc(abc)
    protected = set()
    # Namespace names are structural; never translate them.
    for _kind, name_index in info.namespaces:
        protected.add(name_index)
    # A QName/Multiname's string is a property or class identifier, not UI text.
    for item in info.multinames:
        if not item:
            continue
        kind = item[0]
        if kind in (0x07, 0x0D):
            protected.add(item[2])
        elif kind in (0x09, 0x0E):
            protected.add(item[1])

    replacements = {}
    hits = 0
    blocked = 0
    p = 4  # minor_version, major_version
    for _ in range(2):  # int and uint pools
        count, p = u30(abc, p)
        for _ in range(max(0, count - 1)):
            p = s32(abc, p)
    count, p = u30(abc, p)  # double pool
    p += max(0, count - 1) * 8
    string_count, p = u30(abc, p)
    entries_start = p
    chunks = []
    for index in range(1, string_count):
        start = p
        size, p = u30(abc, p)
        original = abc[p:p + size].decode("utf-8", "replace")
        p += size
        proposed = _translate_display_string(original)
        translated = original if index in protected else proposed
        if index in protected and proposed != original:
            blocked += sum(original.count(old) for old, _new in ORDERED_TRANSLATIONS if old in original)
        if translated != original:
            replacements[index] = translated
            hits += sum(original.count(old) for old, _new in ORDERED_TRANSLATIONS if old in original)
            encoded = translated.encode("utf-8")
            chunks.append(encode_u30(len(encoded)) + encoded)
        else:
            chunks.append(abc[start:p])
    patched = abc[:entries_start] + b"".join(chunks) + abc[p:]
    return patched, len(replacements), hits, blocked


def _iter_abc_tags(raw: bytes):
    """Yield (abc_start, abc_end, tag_start, header_len, tag_len) for every DoABC2 tag."""
    nbits = raw[8] >> 3
    rect_length = (5 + 4 * nbits + 7) // 8
    pos = 8 + rect_length + 4  # RECT, frame rate, frame count
    while pos + 2 <= len(raw):
        header = struct.unpack_from("<H", raw, pos)[0]
        tag_code, tag_len = header >> 6, header & 0x3F
        header_len = 2
        if tag_len == 0x3F:
            if pos + 6 > len(raw):
                raise ValueError("Truncated SWF tag header")
            tag_len = struct.unpack_from("<I", raw, pos + 2)[0]
            header_len = 6
        payload = pos + header_len
        tag_end = payload + tag_len
        if tag_end > len(raw):
            raise ValueError("SWF tag extends beyond file length")
        if tag_code == 82:  # DoABC2: flags, NUL-terminated name, ABC
            if header_len != 6:
                raise ValueError("DoABC2 tag is not long-form; refusing unsafe rewrite")
            name_start = payload + 4
            name_end = raw.find(b"\x00", name_start, tag_end)
            if name_end < 0:
                raise ValueError("DoABC2 tag has no terminating ABC name")
            yield name_end + 1, tag_end, pos + 2, header_len, tag_len
        if tag_code == 0:
            break
        pos = tag_end


def _patch_swf(packed: bytes) -> tuple[bytes, int, int, int, int, int]:
    if packed[:3] == b"CWS":
        raw = bytearray(packed[:8] + zlib.decompress(packed[8:]))
    elif packed[:3] == b"FWS":
        raw = bytearray(packed)
    else:
        raise ValueError("Expected an unencrypted FWS/CWS SWF")
    if len(raw) != int.from_bytes(raw[4:8], "little"):
        raise ValueError("SWF file-length header does not match its contents")

    tags = list(_iter_abc_tags(raw))
    if not tags:
        raise ValueError("No DoABC2 bytecode blocks found in SWF")
    changed_strings = replacement_hits = blocked_strings = skipped_blocks = 0
    patched_tag_indexes = set()
    # Process tags from last to first so changing one ABC length cannot shift
    # the offsets of ABC blocks that have not been processed yet.
    for tag_index in range(len(tags) - 1, -1, -1):
        abc_start, abc_end, tag_length_offset, tag_header_length, tag_length = tags[tag_index]
        abc = bytes(raw[abc_start:abc_end])
        try:
            patched_abc, changed, hits, blocked = _patch_abc_strings(abc)
        except Exception as exc:
            # Some game SWFs use ASC bytecode variants unsupported by the small
            # helper parser. Skip only that block, not every other block/module.
            skipped_blocks += 1
            print("[battle-ui-translation] Skipping unsupported ABC block %d: %s" % (tag_index + 1, exc))
            continue
        blocked_strings += blocked
        if changed == 0:
            continue
        changed_strings += changed
        replacement_hits += hits
        patched_tag_indexes.add(tag_index)
        raw[abc_start:abc_end] = patched_abc
        struct.pack_into("<I", raw, tag_length_offset, tag_length + len(patched_abc) - len(abc))

    if changed_strings == 0:
        return packed, 0, 0, blocked_strings, len(tags), skipped_blocks

    struct.pack_into("<I", raw, 4, len(raw))
    raw[:3] = b"CWS"
    output = bytes(raw[:8]) + zlib.compress(bytes(raw[8:]), 9)
    # Re-open and parse every DoABC2 block so malformed output never gets served.
    verified = bytearray(output[:8] + zlib.decompress(output[8:]))
    if len(verified) != int.from_bytes(verified[4:8], "little"):
        raise ValueError("Patched SWF failed its file-length check")
    verify_tags = list(_iter_abc_tags(verified))
    if len(verify_tags) != len(tags):
        raise ValueError("Patched SWF changed the number of DoABC2 blocks")
    for index, (a, b, _t, _h, _l) in enumerate(verify_tags):
        if index in patched_tag_indexes:
            parse_abc(bytes(verified[a:b]))
    return output, changed_strings, replacement_hits, blocked_strings, len(tags), skipped_blocks
def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".translation-tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def apply_translation() -> bool:
    """Patch and deploy hardcoded labels from the live game modules."""
    version_file = WEBROOT / VERSION_NAME
    if not version_file.is_file():
        print("[battle-ui-translation] Skipped: fileVer.xml is missing.")
        return False

    version_out = version_file.read_bytes()
    prepared = []
    print("[battle-ui-translation] Checking %d live SWF modules for untranslated labels." % len(MODULE_NAMES))
    for module_name in MODULE_NAMES:
        print("[battle-ui-translation] Scanning %s..." % module_name)
        source = WEBROOT / module_name
        if not source.is_file():
            print("[battle-ui-translation] Skipped missing module: %s" % module_name)
            continue

        original = source.read_bytes()
        try:
            patched, changed_strings, hits, blocked, block_count, skipped_blocks = _patch_swf(original)
        except Exception as exc:
            print("[battle-ui-translation] Skipped %s: %s" % (module_name, exc))
            continue
        if changed_strings == 0:
            if blocked:
                print("[battle-ui-translation] %s: checked %d ABC block(s), skipped %d unsupported; %d label occurrences are structural identifiers, so they were left untouched." % (module_name, block_count, skipped_blocks, blocked))
            else:
                print("[battle-ui-translation] %s: checked %d ABC block(s), skipped %d unsupported; no untranslated matching display strings found." % (module_name, block_count, skipped_blocks))
            continue

        md5 = hashlib.md5(patched).hexdigest()
        pattern = (
            rb'(<file\s+name="'
            + re.escape(module_name.encode("ascii"))
            + rb'"\s+size=")[^"]*("\s+ver=")[^"]*("\s*/>)'
        )
        new_entry = lambda match: (
            match.group(1) + str(len(patched)).encode("ascii")
            + match.group(2) + md5.encode("ascii") + match.group(3)
        )
        candidate_version, count = re.subn(pattern, new_entry, version_out)
        if count != 1:
            print("[battle-ui-translation] Skipped %s: expected one fileVer entry, found %d." % (module_name, count))
            continue
        version_out = candidate_version

        stem = module_name[:-4]
        aliases = [
            path for path in WEBROOT.glob(stem + "_*.swf")
            if re.fullmatch(re.escape(stem) + r"_[0-9a-f]{32}\.swf", path.name)
        ]
        # The generic Game.swf and its older hash aliases are byte-identical
        # to Game_n in this client pack. Keep those legacy entry points in sync.
        if module_name == "Game_n.swf":
            aliases.extend(
                path for path in [WEBROOT / "Game.swf", *WEBROOT.glob("Game_*.swf")]
                if path.is_file()
                and (re.fullmatch(r"Game_[0-9a-f]{32}\.swf", path.name) or path.name == "Game.swf")
            )
        aliases = list(dict.fromkeys(aliases))
        expected_alias = WEBROOT / (stem + "_" + md5 + ".swf")
        prepared.append({
            "source": source,
            "patched": patched,
            "aliases": aliases,
            "expected_alias": expected_alias,
            "module_name": module_name,
            "changed_strings": changed_strings,
            "hits": hits,
        })

    if not prepared:
        return False

    # Keep generated binary backups outside the Git working tree so a sync/push cannot accidentally commit them.
    backup_dir = Path.home() / ".pvzol-battle-ui-backups" / "battle-ui-english-20261015"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for item in prepared:
        for path in [item["source"], *item["aliases"]]:
            backup = backup_dir / path.name
            if path.is_file() and not backup.exists():
                shutil.copy2(path, backup)
    backup_version = backup_dir / VERSION_NAME
    if version_file.is_file() and not backup_version.exists():
        shutil.copy2(version_file, backup_version)
    print("[battle-ui-translation] Original assets backed up to %s." % backup_dir)

    for item in prepared:
        _atomic_write(item["source"], item["patched"])
        for alias in item["aliases"]:
            _atomic_write(alias, item["patched"])
        _atomic_write(item["expected_alias"], item["patched"])
        print(
            "[battle-ui-translation] Updated %s: %d string entries (%d label replacements); "
            "deployed %s (%d bytes)."
            % (
                item["module_name"], item["changed_strings"], item["hits"],
                item["expected_alias"].name, len(item["patched"]),
            )
        )
    _atomic_write(version_file, version_out)
    changed_entries = sum(item["changed_strings"] for item in prepared)
    replacement_hits = sum(item["hits"] for item in prepared)
    print("[battle-ui-translation] Refreshed fileVer.xml.")
    print(
        "[battle-ui-translation] Complete: %d module(s) updated, %d string entries changed, %d label replacement(s)."
        % (len(prepared), changed_entries, replacement_hits)
    )
    return changed_entries > 0

if __name__ == "__main__":
    apply_translation()
