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
    "1. 无尽炼狱在小镇{0}级时开启，共有7层难度，由全服镇长共同参与<br>2. 无尽炼狱每周进行2轮，每周{1}、周{2}0点准时开启并持续{3}小时,开启后首次通过任意关卡可获得该难度首通奖励，重复挑战无奖励<br>3. 每轮挑战可以出战至多16个植物，当任一植物阵亡后不可继续使用<br>4. 每次挑战胜利后保留植物剩余血量到下一关卡。如果对战斗结果不满意，可以选择重新挑战来获得更好的成绩<br>5. 每次挑战失败后扣除1次失败次数。如果失败次数用完，可以选择重新开始或者结束本轮挑战<br>6. 每轮挑战结束后，根据各镇长所在炼狱难度的通关数量决定排名和奖励,通关数量相同时按照通关战力高低进行排序<br>7. 每轮挑战结束后，根据各层炼狱内的排名决定挑战者到下轮挑战的晋级、保级和降级结果<br>8. 炼狱关卡内容将随游戏更新进行实时调整，以确保关卡体验": "1. Endless Purgatory unlocks at Town level {0}. There are 7 difficulty tiers, and players across the server can participate.<br>2. Endless Purgatory runs for 2 rounds each week, opening at 00:00 on {1} and {2} and lasting {3} hours. The first clear of any stage after opening earns that difficulty's first-clear reward; repeat clears do not award rewards.<br>3. You may deploy up to 16 plants each round. A plant that is defeated cannot be used again.<br>4. After each victory, your plants' remaining health carries over to the next stage. If you are unhappy with the result, retry to aim for a better score.<br>5. Each defeat consumes one failure attempt. Once all failure attempts are used, you may restart or end the current round.<br>6. At the end of each round, rankings and rewards depend on the number of stages cleared at each player's highest difficulty. Ties are resolved by battle power.<br>7. At the end of each round, rankings within each difficulty determine promotion, retention, or demotion for the next round.<br>8. Endless Purgatory stage content may be adjusted with game updates to keep the challenge experience fresh.",
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
    "暂无奖励": "No rewards available",
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
    "装备植物": "Equip Plant",
    "装备道具": "Equip Item",
    "强化装备": "Enhance Equipment",
    "强化植物": "Enhance Plant",
    "洗炼": "Refinement",
    "装甲": "Armor",
    "装扮": "Cosmetics",
    "技能": "Skill",
    "今日探险次数已达到上限": "You have reached the daily exploration limit.",
    "前两章主线没有全部通关": "Clear the first two main-story chapters first.",
    "剩余挑战次数不足": "Not enough challenge attempts remaining.",
    "已经继承过上周分数": "Last week's score has already been carried over.",
    "签到次数不足": "Not enough check-in attempts.",
    "宝箱已经打开了": "This chest has already been opened.",
    "宝箱Index错误": "Invalid chest index.",
    "密码长度不正确": "Invalid password length.",
    "密码格式不正确": "Invalid password format.",
    "密码重置中": "Password reset in progress.",
    "道具批量使用数量超过上限": "Bulk item use exceeds the limit.",
    "你的背包快满了，请先清理背包{bag}": "Your inventory is almost full. Clear some space first: {bag}",
    "当前植物存在未选择进化方向的基因，请先选择进化方向": "This plant has genes with no evolution path selected. Choose an evolution path first.",
    "植物稀有度不同，不能交换基因": "Plants of different rarities cannot exchange genes.",
    "该装饰未解锁": "This decoration is locked.",
    "强化失败！试剂在背包中": "Enhancement failed! The reagent is still in your inventory.",
    "试剂吞噬失败": "Reagent consumption failed.",
    "不满足金币限制": "Does not meet the coin requirement.",
    "错误的金额类型": "Invalid currency type.",
    "错误的凝聚类型": "Invalid synthesis type.",
    "未满足等级要求": "Level requirement not met.",
    "等级不够": "Level too low.",
    "剩余签到次数不足": "Not enough check-in attempts remaining.",
    "签到次数超过剩余补签次数": "Check-in attempts exceed the remaining make-up allowance.",
    "今日剩余次数": "Attempts Remaining Today",
    "今日剩余次数:": "Attempts Remaining Today: ",
    "今日剩余次数：": "Attempts Remaining Today: ",
    "邮件已发送": "Mail sent",
    "邮件发送失败": "Failed to send mail",
    "邮件不能为空": "Mail cannot be empty",
    "查看邮件": "View Mail",
    "写邮件": "Compose Mail",
    "今日剩余次数不足": "No attempts remaining today.",
    "今日次数已用完": "Today's attempts are used up.",
    "每日免费次数": "Daily free attempts",
    "已达到每日上限": "Daily limit reached",
    "未开启": "Not open yet",
    "暂未开启": "Not available yet",
    "开启中": "Opening",
    "关闭中": "Closing",
    "已关闭": "Closed",
    "服务器维护": "Server maintenance",
    "更新完成": "Update complete",
    "正在更新": "Updating",
    "更新失败": "Update failed",
    "连接中": "Connecting",
    "连接成功": "Connected",
    "连接超时": "Connection timed out",
    "网络连接失败": "Network connection failed",
    "服务器繁忙": "Server busy",
    "请稍后重试": "Please try again later",
    "参数错误": "Invalid parameters",
    "无效操作": "Invalid operation",
    "数据异常": "Data error",
    "配置错误": "Configuration error",
    "操作已取消": "Operation cancelled",
    "购买确认": "Confirm purchase",
    "消耗数量": "Quantity used",
    "所需等级": "Required level",
    "货币不足": "Insufficient currency",
    "材料不足": "Not enough materials",
    "剩余容量": "Remaining capacity",
    "背包已满": "Inventory full",
    "背包空间不足": "Not enough inventory space",
    "账号不存在": "Account not found",
    "角色不存在": "Character not found",
    "卡牌不存在": "Card not found",
    "植物不存在": "Plant not found",
    "请选择植物": "Select a plant",
    "请选择卡牌": "Select a card",
    "请选择角色": "Select a character",
    "没有可用奖励": "No rewards available",
    "没有可领取奖励": "No rewards to claim",
    "暂无可用道具": "No items available",
    "当前没有好友": "No friends yet",
    "暂无任务": "No quests",
    "暂无活动": "No events",
    "正在加载": "Loading",
    "加载成功": "Loaded successfully",
    "数据加载失败": "Failed to load data",
    "保存成功": "Saved successfully",
    "保存失败": "Failed to save",
    "请输入账号": "Enter account ID",
    "请输入密码": "Enter password",
    "请输入验证码": "Enter verification code",
    "密码错误": "Incorrect password",
    "账号错误": "Invalid account",
    "登录中": "Logging in",
    "注册成功": "Registration successful",
    "注册失败": "Registration failed",
    "已解锁": "Unlocked",
    "未解锁": "Locked",
    "待领取": "Ready to claim",
    "登录成功": "Login successful",
    "登录失败": "Login failed",
    "连接失败": "Connection failed",
    "网络错误": "Network error",
    "服务器错误": "Server error",
    "数据错误": "Data error",
    "兑换成功": "Exchange successful",
    "兑换失败": "Exchange failed",
    "购买成功": "Purchase successful",
    "购买失败": "Purchase failed",
    "领取失败": "Claim failed",
    "升级成功": "Level up successful",
    "升级失败": "Level up failed",
    "强化成功": "Enhancement successful",
    "强化失败": "Enhancement failed",
    "请稍候": "Please wait",
    "请输入": "Please enter",
    "请选择": "Please select",
    "是否确认": "Are you sure",
    "确认退出": "Confirm Exit",
    "退出游戏": "Exit Game",
    "返回首页": "Return Home",
    "重新登录": "Log In Again",
    "版本更新": "Version Update",
    "更新内容": "Patch Notes",
    "活动时间": "Event Duration",
    "活动奖励": "Event Rewards",
    "每日奖励": "Daily Rewards",
    "在线奖励": "Login Rewards",
    "累计奖励": "Milestone Rewards",
    "任务奖励": "Quest Rewards",
    "通关奖励": "Stage Clear Rewards",
    "首通奖励": "First Clear Rewards",
    "已完成": "Completed",
    "未完成": "Incomplete",
    "已过期": "Expired",
    "剩余时间": "Time Remaining",
    "剩余数量": "Remaining Quantity",
    "剩余次数": "Attempts Remaining",
    "次数不足": "Not enough attempts",
    "道具不足": "Not enough items",
    "金币不足": "Not enough coins",
    "钻石不足": "Not enough diamonds",
    "等级不足": "Level too low",
    "条件不足": "Requirements not met",
    "尚未开放": "Not available yet",
    "暂未开放": "Not available yet",
    "功能未开放": "Feature not available yet",
    "已达到上限": "Limit reached",
    "已达上限": "Limit reached",
    "无法重复领取": "Already claimed",
    "不能重复领取": "Already claimed",
    "活动结束": "Event ended",
    "活动未开始": "Event has not started",
    "维护中": "Under maintenance",
    "系统提示": "System Notice",
    "温馨提示": "Notice",
    "注意": "Attention",
    "公告": "Announcements",
    "说明": "Description",
    "详细信息": "Details",
    "查看详情": "View Details",
    "点击查看": "Click to View",
    "点击领取": "Click to Claim",
    "点击进入": "Click to Enter",
    "自动战斗": "Auto Battle",
    "快速扫荡": "Quick Sweep",
    "一键扫荡": "Quick Sweep",
    "扫荡中": "Sweeping",
    "扫荡完成": "Sweep Complete",
    "跳过战斗": "Skip Battle",
    "战斗胜利": "Victory",
    "战斗失败": "Defeat",
    "平局": "Draw",
    "伤害总计": "Total Damage",
    "治疗总量": "Total Healing",
    "造成伤害": "Damage Dealt",
    "受到伤害": "Damage Taken",
    "剩余血量": "Health Remaining",
    "战斗时长": "Battle Duration",
    "推荐战力": "Recommended Power",
    "战力不足": "Insufficient Power",
    "关卡进度": "Stage Progress",
    "当前关卡": "Current Stage",
    "下一关": "Next Stage",
    "上一关": "Previous Stage",
    "选择难度": "Select Difficulty",
    "普通难度": "Normal Difficulty",
    "困难难度": "Hard Difficulty",
    "精英难度": "Elite Difficulty",
    "当前等级": "Current Level",
    "目标等级": "Target Level",
    "经验不足": "Not enough XP",
    "金币": "Coins",
    "阳光": "Sun",
    "蓝钻": "Blue Diamonds",
    "金钻": "Gold Diamonds",
    "钻石": "Diamonds",
    "体力": "Stamina",
    "声望": "Prestige",
    "经验": "XP",
    "等级": "Level",
    "数量": "Quantity",
    "名称": "Name",
    "描述": "Description",
    "消耗": "Cost",
    "所需": "Required",
    "获得": "Obtained",
    "使用": "Use",
    "购买": "Buy",
    "出售": "Sell",
    "兑换": "Exchange",
    "进阶": "Advance",
    "合成": "Synthesize",
    "分解": "Dismantle",
    "卸下": "Unequip",
    "当前": "Current",
    "最大": "Maximum",
    "属性": "Stats",
    "生命": "Health",
    "攻击": "Attack",
    "防御": "Defense",
    "速度": "Speed",
    "治疗": "Healing",
    "伤害": "Damage",
    "成功": "Success",
    "失败": "Failed",
    "完成": "Complete",
    "帮助": "Help",
    "规则": "Rules",
    "预览": "Preview",
    "商店": "Shop",
    "邮件": "Mail",
    "好友": "Friends",
    "排名": "Rank",
    "名次": "Place",
    "第一名": "1st Place",
    "第二名": "2nd Place",
    "第三名": "3rd Place",
    "暂无排名": "No Ranking",
    "暂无记录": "No Records",
    "没有记录": "No Records",
    "无记录": "No Records",
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
EXCLUDED_MODULES = {"ModuleClientDBRes.swf", "ModuleFont.swf"}


def _module_targets(manifest: bytes) -> list[str]:
    """Return canonical UI SWF modules listed in the active asset manifest.

    Hashed files are aliases of canonical modules and are updated alongside
    their source. Client DB resources and fonts are deliberately excluded.
    """
    declared = []
    pattern = rb'<file\s+name="([^"]+\.swf)"\s+size="[^"]+"\s+ver="[^"]+"\s*/>'
    for match in re.finditer(pattern, manifest):
        try:
            name = match.group(1).decode("ascii")
        except UnicodeDecodeError:
            continue
        if not name.startswith("Module") or not name.endswith(".swf"):
            continue
        if re.fullmatch(r"Module.*_[0-9a-f]{32}\.swf", name):
            continue
        if name not in EXCLUDED_MODULES:
            declared.append(name)
    ordered = list(dict.fromkeys([*MODULE_NAMES, *declared]))
    return [name for name in ordered if name not in EXCLUDED_MODULES]


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
    "金币", "阳光", "蓝钻", "金钻", "钻石", "体力", "声望", "经验", "等级",
    "今日剩余次数", "已解锁", "未解锁", "开启", "启用", "停用", "禁用",
    "连接中", "连接成功", "加载成功", "登录中", "注册成功", "注册失败",
    "数量", "名称", "描述", "消耗", "所需", "获得", "使用", "购买", "出售",
    "兑换", "进阶", "合成", "分解", "装备", "卸下", "当前", "最大", "属性",
    "生命", "攻击", "防御", "速度", "治疗", "伤害", "成功", "失败", "完成",
    "帮助", "规则", "预览", "排名", "名次", "第一名", "第二名", "第三名",
}
TOKEN_BOUNDARY_RE = r"[\s\[\]【】()（）:：+/\-·]"


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
    target_modules = _module_targets(version_out)
    prepared = []
    print("[battle-ui-translation] Checking %d live SWF modules for untranslated labels." % len(target_modules))
    for module_name in target_modules:
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
