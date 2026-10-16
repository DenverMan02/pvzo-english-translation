"""Translate hardcoded player-facing strings in the live SWF modules.

Scans canonical UI modules from fileVer.xml and the main Game_n/Game_p builds.
Client database resources and font assets are excluded. Only display strings
are edited; ActionScript namespace/property identifiers are protected.
The asset server runs this before serving the game, and updates version hashes
and hashed SWF aliases when a module changes.
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
    "我的名字我的名字": "My Name My Name",
    "加入黑名单": "Add to Blocklist",
    "移除黑名单": "Remove from Blocklist",
    "个性签名：": "Signature: ",
    "好友资料": "Friend Profile",
    "修改": "Edit",
    "发送邮件": "Send Mail",
    "邮件标题": "Mail Subject",
    "邮件内容": "Mail Message",
    "收件人": "Recipient",
    "附件": "Attachment",
    "领取邮件附件": "Claim Mail Attachments",
    "邮件已过期": "Mail Expired",
    "没有可领取的邮件": "No mail available to claim",
    "暂无邮件": "No Mail",
    "没有邮件": "No Mail",
    "赠送好友体力": "Send Stamina to a Friend",
    "好友切磋": "Friendly Match",
    "每日签到获得奖励": "Claim daily check-in rewards",
    "参与时光之旅获得时光币": "Participate in Time Journey to earn Time Coins",
    "参与捉小偷获得大量金币": "Catch thieves to earn lots of coins",
    "参与护农田获得大量经验豆": "Protect the farm to earn lots of XP Beans",
    "挑战结束": "Challenge Ended",
    "本轮挑战结束": "Round Complete",
    "本轮挑战": "Current Round",
    "重复挑战无奖励": "No rewards for repeat clears",
    "失败次数用完": "No failure attempts remaining",
    "重新开始本轮挑战": "Restart This Round",
    "结束本轮挑战": "End This Round",
    "保留植物剩余血量到下一关卡": "Plants retain their remaining health for the next stage",
    "可以选择重新挑战": "You can retry the challenge",
    "通关数量相同时按照通关战力高低进行排序": "Ties are ranked by battle power",
    "晋级、保级和降级": "Promotion, retention, and demotion",
    "触发概率": "Trigger Chance",
    "冷却缩短": "Cooldown Reduction",
    "冷却时间缩短": "Cooldown Reduction",
    "攻击速度提升": "Attack Speed Increase",
    "攻击力提升": "Attack Increase",
    "防御力提升": "Defense Increase",
    "生命值提升": "Max Health Increase",
    "伤害提升": "Damage Increase",
    "攻击力下降": "Attack Reduction",
    "防御力下降": "Defense Reduction",
    "虚弱": "Weakened",
    "单次攻击": "Single Attack",
    "附带效果": "Additional Effect",
    "范围内": "Within Range",
    "优先攻击": "Prioritize Attacking",
    "优先目标": "Priority Target",
    "增加攻击力": "Increase Attack",
    "减少攻击力": "Reduce Attack",
    "提升防御": "Increase Defense",
    "提升生命": "Increase Health",
    "恢复量": "Healing Amount",
    "治疗量": "Healing Amount",
    "免疫控制": "Crowd Control Immunity",
    "控制效果": "Control Effect",
    "异常抗性": "Status Resistance",
    "前排": "Front Row",
    "后排": "Back Row",
    "地面": "Ground",
    "水面": "Water Surface",
    "空中": "Airborne",
    "目标": "Target",
    "当前目标": "Current Target",
    "最近目标": "Nearest Target",
    "随机目标": "Random Target",
    "所有目标": "All Targets",
    "敌方全体": "All Enemies",
    "友方全体": "All Allies",
    "周围": "Nearby",
    "全屏": "Full Screen",
    "直线攻击": "Line Attack",
    "范围攻击": "Area Attack",
    "群体攻击": "AoE Attack",
    "单体攻击": "Single-target Attack",
    "弹射": "Ricochet",
    "溅射": "Splash",
    "耗时": "Time Required",
    "种植冷却": "Plant Cooldown",
    "射程": "Range",
    "每秒攻击": "Attacks per Second",
    "魅惑": "Charmed",
    "沉默": "Silence",
    "增益效果": "Buff Effects",
    "减益效果": "Debuff Effects",
    "增伤": "Damage Bonus",
    "无敌": "Invulnerable",
    "护盾值": "Shield",
    "护盾上限": "Max Shield",
    "治疗效果": "Healing Effect",
    "回血": "HP Recovery",
    "生命恢复": "Health Recovery",
    "恢复生命": "Restore Health",
    "自动恢复": "Auto Recovery",
    "最终伤害": "Final Damage",
    "最终攻击": "Final Attack",
    "基础攻击": "Base Attack",
    "基础防御": "Base Defense",
    "基础生命": "Base Health",
    "攻击属性": "Attack Stats",
    "防御属性": "Defense Stats",
    "技能效果": "Skill Effect",
    "效果持续时间": "Effect Duration",
    "持续时间": "Duration",
    "冷却结束": "Cooldown Complete",
    "冷却中": "On Cooldown",
    "能量": "Energy",
    "怒气": "Rage",
    "法力": "Mana",
    "防御姿态": "Defensive Stance",
    "水属性": "Water Element",
    "火属性": "Fire Element",
    "冰属性": "Ice Element",
    "雷属性": "Lightning Element",
    "土属性": "Earth Element",
    "风属性": "Wind Element",
    "光属性": "Light Element",
    "暗属性": "Dark Element",
    "元素抗性": "Elemental Resistance",
    "伤害减免": "Damage Reduction",
    "伤害加深": "Damage Amplification",
    "攻击间隔": "Attack Interval",
    "范围伤害": "Area Damage",
    "溅射伤害": "Splash Damage",
    "穿透伤害": "Piercing Damage",
    "反击": "Counterattack",
    "格挡率": "Block Chance",
    "暴击抵抗": "Critical Resistance",
    "异常状态": "Status Effect",
    "免疫": "Immune",
    "中毒": "Poisoned",
    "灼烧": "Burn",
    "麻痹": "Paralysis",
    "击退": "Knockback",
    "竞技场": "Arena",
    "天梯": "Ladder",
    "无尽炼狱": "Endless Purgatory",
    "首通": "First Clear",
    "首胜奖励": "First Victory Rewards",
    "重置进度": "Reset Progress",
    "全服排行": "Server-wide Rankings",
    "全服排名": "Server-wide Rank",
    "排名详情": "Ranking Details",
    "赛季奖励": "Season Rewards",
    "每日签到": "Daily Check-In",
    "签到奖励": "Check-In Rewards",
    "累计登录": "Cumulative Login",
    "连续登录": "Consecutive Login",
    "在线时长": "Online Time",
    "新手引导": "Tutorial",
    "新手奖励": "New Player Rewards",
    "主线任务": "Main Quests",
    "支线任务": "Side Quests",
    "章节": "World",
    "小镇": "Town",
    "城镇": "Town",
    "建筑": "Building",
    "繁荣度": "Prosperity",
    "贡献度": "Contribution",
    "公会战": "Guild Battle",
    "守卫": "Guard",
    "塔防": "Tower Defense",
    "塔防特权": "Tower Defense Perk",
    "竞技积分": "Arena Points",
    "段位": "Tier",
    "赛季结算": "Season Results",
    "已挑战": "Attempted",
    "未挑战": "Not Attempted",
    "可挑战": "Available to Challenge",
    "不可挑战": "Unavailable",
    "奖励内容": "Reward Contents",
    "击败敌人": "Defeat Enemies",
    "我的排名": "My Rank",
    "邮件箱": "Mailbox",
    "邮箱": "Mailbox",
    "好友列表": "Friend List",
    "植物列表": "Plant List",
    "卡牌列表": "Card List",
    "挑战记录": "Challenge Records",
    "本周": "This Week",
    "本日": "Today",
    "今日": "Today",
    "昨天": "Yesterday",
    "明天": "Tomorrow",
    "明日": "Tomorrow",
    "本轮": "This Round",
    "下轮": "Next Round",
    "晋级": "Promoted",
    "保级": "Retain Rank",
    "降级": "Demoted",
    "通关数量": "Stages Cleared",
    "失败次数": "Failure Attempts",
    "出战": "Deploy",
    "出战植物": "Deployed Plants",
    "战斗结果": "Battle Result",
    "排名奖励": "Ranking Rewards",
    "福利": "Welfare",
    "福利大厅": "Welfare Hall",
    "每日路线": "Daily Routes",
    "活跃度奖励": "Activity Rewards",
    "累计活跃度": "Total Activity",
    "免费次数": "Free Attempts",
    "剩余免费次数": "Free Attempts Remaining",
    "刷新时间": "Refresh Time",
    "倒计时": "Countdown",
    "领取时间": "Claim Time",
    "活动规则": "Event Rules",
    "挑战规则": "Challenge Rules",
    "攻击加成": "Attack Bonus",
    "防御加成": "Defense Bonus",
    "血量": "Health",
    "护甲": "Armor",
    "攻速": "Attack Speed",
    "技能伤害加成": "Skill Damage Bonus",
    "反伤": "Reflect Damage",
    "闪避率": "Dodge Chance",
    "命中率": "Hit Chance",
    "格挡": "Block",
    "穿透": "Penetration",
    "元素": "Element",
    "火焰": "Fire",
    "冰冻": "Freeze",
    "电击": "Shock",
    "眩晕": "Stun",
    "减速": "Slow",
    "持续伤害": "Damage over Time",
    "持续治疗": "Heal over Time",
    "复活": "Revive",
    "阵亡": "Defeated",
    "击败": "Defeat",
    "敌人": "Enemy",
    "友方": "Ally",
    "敌方": "Enemy",
    "卡组": "Deck",
    "上阵": "Deploy",
    "下阵": "Remove from Formation",
    "阵型": "Formation",
    "队长": "Leader",
    "战绩": "Battle Record",
    "通关": "Clear Stage",
    "掉落": "Drops",
    "概率": "Chance",
    "几率": "Chance",
    "使用次数": "Uses Remaining",
    "重置次数": "Reset Count",
    "次数": "Attempts",
    "花费": "Cost",
    "技能点": "Skill Points",
    "进化材料": "Evolution Materials",
    "训练": "Train",
    "培养": "Cultivate",
    "营养液": "Nutrient Solution",
    "配方": "Recipe",
    "联盟": "Alliance",
    "战队": "Team",
    "排行": "Ranking",
    "赛季": "Season",
    "跨服": "Cross-Server",
    "体力恢复": "Stamina Recovery",
    "未激活": "Inactive",
    "已激活": "Active",
    "激活": "Activate",
    "改造": "Modify",
    "转生": "Rebirth",
    "觉醒": "Awakening",
    "突破": "Breakthrough",
    "技能升级": "Skill Upgrade",
    "提升": "Increase",
    "附加属性": "Bonus Stats",
    "稀有属性": "Rare Stats",
    "详情": "Details",
    "更多": "More",
    "上一页": "Previous Page",
    "下一页": "Next Page",
    "洗练": "Refinement",
    "升级材料": "Upgrade Materials",
    "进阶材料": "Ascension Materials",
    "强化材料": "Enhancement Materials",
    "合成材料": "Synthesis Materials",
    "分解获得": "Obtained from Dismantling",
    "奖励预览": "Reward Preview",
    "预览奖励": "Preview Rewards",
    "礼包": "Bundle",
    "礼包内容": "Bundle Contents",
    "领取全部": "Claim All",
    "全部领取": "Claim All",
    "红点提示": "Notification",
    "未读邮件": "Unread Mail",
    "新邮件": "New Mail",
    "邮件已读": "Mail Read",
    "邮件未读": "Unread Mail",
    "发送": "Send",
    "回复": "Reply",
    "删除": "Delete",
    "举报": "Report",
    "屏蔽": "Block",
    "切换": "Switch",
    "退出登录": "Log Out",
    "换区": "Change Server",
    "服务器列表": "Server List",
    "登录": "Log In",
    "注册": "Register",
    "界面": "Interface",
    "刷新中": "Refreshing",
    "重试连接": "Retry Connection",
    "未开放": "Not Available",
    "即将开放": "Coming Soon",
    "剩余": "Remaining",
    "体力不足": "Not enough stamina",
    "挑战次数": "Challenge Attempts",
    "每日次数": "Daily Attempts",
    "扫荡次数": "Sweep Attempts",
    "战斗次数": "Battle Attempts",
    "请先通关": "Clear the previous stage first",
    "等待开始": "Waiting to Start",
    "正在战斗": "In Battle",
    "植物进化": "Plant Evolution",
    "进化": "Evolve",
    "技能树": "Skill Tree",
    "被动": "Passive",
    "主动": "Active",
    "品质": "Quality",
    "稀有": "Rare",
    "史诗": "Epic",
    "传说": "Legendary",
    "单体": "Single Target",
    "群体": "Area",
    "范围": "Area",
    "冷却": "Cooldown",
    "冷却时间": "Cooldown",
    "抽奖": "Draw",
    "招募": "Recruit",
    "抽卡": "Draw Cards",
    "卡牌": "Cards",
    "转盘": "Wheel",
    "胜利": "Victory",
    "点我": "Click Here",
    "使用说明": "Instructions",
    "玩法说明": "How to Play",
    "玩法规则": "Game Rules",
    "邀请好友": "Invite Friends",
    "添加好友": "Add Friend",
    "私聊": "Private Chat",
    "世界聊天": "World Chat",
    "好友聊天": "Friend Chat",
    "系统公告": "System Announcements",
    "保存设置": "Save Settings",
    "下一步": "Next",
    "上一步": "Previous",
    "确认选择": "Confirm Selection",
    "选择": "Select",
    "清空": "Clear",
    "排序": "Sort",
    "筛选": "Filter",
    "提示": "Notice",
    "阵容": "Formation",
    "队伍": "Team",
    "公会": "Guild",
    "远征": "Expedition",
    "探险": "Exploration",
    "副本": "Dungeon",
    "炼狱": "Purgatory",
    "每日": "Daily",
    "全部": "All",
    "开战": "Begin Battle",
    "自动": "Auto",
    "手动": "Manual",
    "重试": "Retry",
    "重新开始": "Restart",
    "放弃": "Abandon",
    "会员": "Membership",
    "充值": "Top Up",
    "已拥有": "Owned",
    "未拥有": "Not Owned",
    "植物卡": "Plant Card",
    "植物卡牌": "Plant Cards",
    "成就": "Achievements",
    "签到": "Check-In",
    "活跃度": "Activity",
    "商城": "Store",
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
    "今日剩余次数:": "Attempts Remaining Today:",
    "今日剩余次数：": "Attempts Remaining Today:",
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
SUBSTRING_TRANSLATIONS = {
    # These are concatenated around dynamic stage numbers in ActionScript.
    "挑战第",
    "关成功！",
    "挑战失败！当前成绩为第",
}
# Only translate most phrases when they are a complete label or a delimited
# token. This prevents a label such as "失败" from being inserted into the
# middle of an otherwise-Chinese sentence.
CONTEXTUAL_TERMS = set(TRANSLATIONS).difference(SUBSTRING_TRANSLATIONS)
TOKEN_BOUNDARY_RE = r"[\s\[\]【】()（）:：+/\-·<>{}]"


def _translate_display_string(value: str) -> str:
    # A few dynamic strings are assembled from separate ActionScript literals,
    # so these fragments must be replaced even when they are not the whole value.
    for old, new in ORDERED_TRANSLATIONS:
        if old in SUBSTRING_TRANSLATIONS or value == old:
            value = value.replace(old, new)

    # Other labels are translated only when standalone or separated from
    # surrounding text by UI punctuation, whitespace, numbers, or HTML markup.
    for old, new in ORDERED_TRANSLATIONS:
        if old not in CONTEXTUAL_TERMS:
            continue
        pattern = (
            r"(^|" + TOKEN_BOUNDARY_RE + r")"
            + re.escape(old)
            + r"(?=$|" + TOKEN_BOUNDARY_RE + r"|\d|[%％])"
        )
        value = re.sub(
            pattern,
            lambda match, replacement=new: match.group(1) + replacement,
            value,
        )
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
    scanned_count = skipped_count = unchanged_count = 0
    print("[battle-ui-translation] Checking %d live SWF modules for untranslated labels." % len(target_modules))
    for module_name in target_modules:
        scanned_count += 1
        print("[battle-ui-translation] Scanning %s..." % module_name)
        source = WEBROOT / module_name
        if not source.is_file():
            skipped_count += 1
            print("[battle-ui-translation] Skipped missing module: %s" % module_name)
            continue

        original = source.read_bytes()
        try:
            patched, changed_strings, hits, blocked, block_count, skipped_blocks = _patch_swf(original)
        except Exception as exc:
            skipped_count += 1
            print("[battle-ui-translation] Skipped %s: %s" % (module_name, exc))
            continue
        if changed_strings == 0:
            unchanged_count += 1
            if block_count and skipped_blocks >= block_count:
                print("[battle-ui-translation] %s: all %d ABC blocks were unsupported; no strings could be checked." % (module_name, block_count))
            elif blocked:
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
            skipped_count += 1
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
        print(
            "[battle-ui-translation] Complete: scanned %d module(s), %d unchanged, %d skipped; no display strings were changed."
            % (scanned_count, unchanged_count, skipped_count)
        )
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
        "[battle-ui-translation] Complete: scanned %d module(s), updated %d, unchanged %d, skipped %d; %d string entries changed, %d label replacement(s)."
        % (scanned_count, len(prepared), unchanged_count, skipped_count, changed_entries, replacement_hits)
    )
    return changed_entries > 0

if __name__ == "__main__":
    apply_translation()
