# pvz_poc_server 项目记忆

## 服务架构
- 主服务器：`pvzol本地/PvZ-Server-PoC/pvz_poc_server.py`（~19500 行 asyncio，`REQUEST_HANDLERS` 派发 435 个命令）。
- 关键子模块：`hotspot_events.py`（大风吹/许愿活动 id 366）、`treasure_local.py`（太空大战/宝藏活动 id 39001）、`bots_local.py`（人机系统）、`serve_client.py`（静态资源 ThreadingTCPServer，缺失图片返回 1x1 占位图）。

## 物品 id 约定（务必遵守）
- 金币=1，钻石(gift diamond / present_ticket)=4，蓝经验豆=2002，黄经验豆=2003。
- 高稀有植物碎片=5xxxx：53900 火龙草、54900 椰子加农炮、54400 地刺、55800 火爆辣椒、57000 氧气藻。

## 人机系统
- bot uin 范围 900000–999999（`is_bot` 判定）。`_BOTS` 列表、`_GUILD_TEMPLATES`（含"人机联盟"）、`run_ai_tick()` 复用竞技场管线模拟 bot 互殴。
- 真实服务启动：`main()` → `_seed_bots()`（内部 `initialize()` 设 `_DB_PATH` 并建 bot 账号/公会）→ `asyncio.create_task(_bot_ai_loop())` 每 120s 调用 `bots_local.run_ai_tick()`。
- 注意：`run_ai_tick`/`ensure_bots` 在 `_DB_PATH is None` 时静默 return；仅在真实服务器（已 `initialize`）下才真正生效——独立探针未调 `initialize` 会误报。

## 已知历史坑
- 登录握手（step 2）须在 try 内，异常要 `break` 而非静默断开，否则新玩家永久卡"读取玩家信息"。
- `build_friend_info_dto` 必须用 `.get(...) or .get(...) or 0`，禁止直接下标好友字段（缺字段→空包→客户端崩溃）。
- `_puppet_upgrade_refused` 默认 `extra` 必须为 `b""`（bytes），否则空输入 `TypeError: can't concat str to bytes`。
- **好友列表必须分页，且单页条数 ≤ 客户端格子数**：`ModuleFriend.swf` 的 `FriendIMWd`（`com.qq.modules.friend.view.components`）是小镇左侧好友控件，`ITEMS_PER_PAGE = 6`（固定 6 个 FriendIMListCell）。其 `update()` 对响应里每个好友 `view.list.getChildAt(i)`，返回 >6 条就抛 `RangeError #2006`。`handle_friend_getlist` 已改为按 `page/pageSize` 切片并把 `pageSize` 钳到 6（`FRIEND_WIDGET_MAX`）。凡是"固定格子数 + getChildAt 遍历"的客户端列表，服务端都不可全量返回。
- 反汇编客户端：`client_tools/abcdis.py <swf> --classes "<sub>"` / `--methods "<Class>" "<m>"`；常量值需用 monkeypatch `_traits` 捕获 `val_index` 再查 `abc.ints`/`uints`/`strings`（默认解析器会丢掉常量索引）。
- **缺失图片 = 客户端"空白/空气"**：`serve_client.py` 对找不到的图片返回 **1x1 透明 PNG**（不是 404），所以图标缺失时界面显示为空而非报错。排查"抽到空气/图标空白"要查 `client_asset_errors.log` 的 `IMAGE_FALLBACK`。已为 `/image/item/<id>.png` 增加"同 id 段最近真实图标"兜底；卡牌图标早有同类兜底。
- **未注册命令的兜底行为**：`REQUEST_HANDLERS` 未命中 → `response_for(sc)` → `populate_message`（只补非 repeated 的嵌套消息 + int64=0）。因此**扁平消息（如 Cmd_Item_Use_SC）会返回 0 字节**，客户端常报"使用失败"；调试日志 `(unhandled msg: %s)` 是 **DEBUG 级**，INFO 日志里看不到。
- **clientDB.txt 是自定义归档**：`[4字节BE长度][表名][4字节BE长度][TSV正文]`，共 235 张表（ItemInfo.txt / QuestInfo.txt / plantTalentInfo.txt / dummyZombieCallInfo.txt …）。读表用 `_read_clientdb_table`，或按上述格式自行解包。
- **宝箱判定**：`ItemInfo.txt` 中 `type=2` 且 `subType ∈ {200,203,204,217,218,224,226,235}` 即宝箱。僵尸岛宝箱=4071，炼狱宝箱=4091/4092/4093/4094。`Cmd_Item_Use_CS` 必须有 handler（背包"使用"走这条），回复 `Cmd_Item_OpenChest_SC{usedItemId(1),allItems(2),openedItems(3)}` + `Cmd_Item_Use_SC`。
- **主线任务 condition_type=4（天赋）**：`QuestInfo.finishCondition` 格式为 `condType:condArg:target`；天赋任务是 `4:0:1`(id1030) 与 `4:0:2`(id1050)，target 是"已学天赋的最高 stage"。进度必须写入**实际达到的最高 stage**，绝不可硬编码 1（否则 target=2 的 1050 永远 `1>=2` 不成立）。
- **客户端列表条数必须"够"**：repeated 字段若被客户端当 **Vector** 按下标取，空列表会抛
  `RangeError #1125 索引 0 超出范围 0`（如 `RankArenaWdMediator.onGetInfo` 取 `topRole[0..2]`）；
  而条数**过多**走 `getChildAt` 则抛 `#2006`（如 FriendIMWd 的 6 格）。两个方向都要防：
  服务端要么保证 ≥ 客户端固定读取条数，要么 ≤ 控件格子数。
- **protobuf 编码陷阱：repeated 消息字段 = 每个元素一个独立子消息**。
  `Dto_FlexibleActivityReward.items(1)` 这类 `RepeatedFieldDescriptor$TYPE_MESSAGE` 必须
  `每个元素 → pb_tag(field,2)+len+payload`；把多个元素先拼成一个 blob 再整体包一层，
  会让客户端在同一个子消息里读到重复字段号，抛
  `Bad data format: <Dto>.<field> cannot be set twice`。
  同时注意**用对 DTO 类型**：`Dto_IdNum{id(1),num(2)}` 与 `build_item_dto` 产出的
  `Dto_ItemInfo`（背包 `Cmd_Item_Update_SC` 用）不是一回事，混用必崩。
  参考正确写法：`town_login_rewards.get_stat`、`jar_local._pair`。
- **竞技场（RankArena）战斗链路**：挑战 = 开 `CommEmbattle` 布阵窗（`Dto_BattleTarget{type=12,
  roleId=对手}`）→ 布阵窗发 `Cmd_Battle_GetFightInfo_CS` 取敌方预览 → `Cmd_RankArena_Challenge_CS`
  → 客户端用 `recorder.reportUid` 调 `GameFlowCmd.queryReport` 取 `Cmd_Battle_QueryReport_SC{report}`
  回放对局。所以：**预览要按 type=12 给真实敌方阵容；挑战要发真战报并把 reportUid 换成真实 id**，
  否则要么退回"unknown"默认阵容、要么直接跳结果没战斗。bots 是有 cards/formation 的真实行，
  敌方阵容直接用 `build_player_units(bot_player, placements)`。
  普通竞技场 `Cmd_Arena_*` 无战斗场景（BattleTargetType 里没有 Arena），直接出结果是设计。
- **植物战力绝不能只靠等级**：等级上限就是 **300**（cardLevel.txt 300 级曲线），其余战力来自
  觉醒(威望,200级)/果实(14槽×20级)/洗练(baptizeLimit)/拼图(45段)/锻造(10级)/护甲/基因/时装。
  算真实战力的唯一正确入口是 `effective_card_stats(player, cid, level, uid=uid)` + `card_power`。
  机器人战力 therefore 用 `bot_develop_card(uin, uid, cid, fraction)` 按同一进度推所有系统。
  q5 六卡全拉满的天花板 ≈ **184万**——300万 达不到，除非破坏等级上限（会渲染异常）。
  护甲 `star_prop` 键 = `fruitType*1000+armorType*100+star*10+substar`，star 0..5、substar 0..4。
- **`Dto_FormationInfo`/`Dto_CardPosInfo` 只能装玩家自己的卡牌 uid**：客户端悬浮卡牌 tooltip
  （`PlantCardToolTipWithAllMediator.changeHandler`）按载荷类型分四路，`Dto_CardPosInfo` 走
  `updateSelfBattleCardByPosInfo`（按 uid 查**玩家自己**的集合，查不到 → #1009）；
  展示别人的阵容/敌人必须走 `Dto_EnemyCardInfo`（enemyCard 字段 → `updateEnemyBattleCard`）。
  所以任何"查看对方阵容"的功能都不能用 `build_formation_info(别人)` 下发。
- **同一场战斗里 posIndex 必须互不相同**（boss + 护卫/召唤物都算）：客户端 UnitFactory 按
  posIndex 建单位，重复会互相覆盖显示（"boss 变成僵尸"）并抛
  `UnitBase.addControl: 相同类型控制器只能添加一次!`。给 boss 加护卫时要从
  `_ENEMY_POS` 里**剔除 boss 自己的 pos**。另：`battleCard` 里 999=邪恶博士、998=秦始皇、
  1000=霸刀将王；`_WORLD_BOSS_IDS=(998,999,1000)` 已把秦始皇放首位。
- **定位客户端崩溃的标准手法**：`PvZ-Server-PoC/client_tools/abcdis.py <swf> --methods "<Class>" "<method>"`
  看字节码。判读要点：`pushbyte N` + `getproperty <runtime>` = 按常量下标取 Vector/数组；
  `--classes "<关键字>"` 先列类名；数据模型 `*.loadMsg` 可确认 SC 字段→客户端字段的改名映射。
  常用 SWF：`webroot/ModuleRankArena.swf`、`ModuleFriend.swf`。
- **排行榜**：`_RANK_TYPES{ranktype: (list_field, self_field, sort_key, builder)}` 覆盖
  1(战力)/2(成就)/3(联盟战力)/4(天梯)/5(竞技场)/6(植物星)/12(新天梯)/101/102/104/105。
  每页 10 条但**必须补齐到 `_RANK_MIN_ROWS=40` 行**——客户端 `RankWindowMediator.updateView`
  对每个 `i < RankGlobal.PAGE_BOUNDS` 取 `rankList[i]` 且无边界检查，短页 = `RangeError #1125`。
  `ranknum(3)` 必须是**真实条目数**（客户端用来算 `m_totalPage`），不是页大小。
  `RankGlobal.PAGE_BOUNDS` 读不到（`Game.swf` 的 ABC tail drift 太大），别浪费时间。
  联盟名/联盟战力经 `guild_local.member_index()` / `power_board()`；入盟/退盟/踢人会回调
  `guild_changed` 清缓存（`_rank_guild_index` 5s、`_rank_bot_rows` 10s）。
- **冒险好友卡（借卡）**：好友行只有 `help_plant_id + help_plant_level`，所以借卡属性若用
  `card_stats_at_level` 就会"太弱且普通"。正确做法：`borrowed_friend(uin, owner_role_id)` 解析好友行
  （**人机没有 friends 行，必须合成一行**，`bots_local.is_bot` 判 900000–999999），
  `friend_best_card(role)` 借出该好友**阵容队长**那张主卡（带真实等级），
  `friend_card_stats(owner_uin, cid, level)` 用 **全零 deployed bonus** 调 `effective_card_stats`
  —— 借用方的 Lab/世界树加成由 `build_player_units` / `build_formation_info_fuben` 再加，
  否则双倍。借用 UID 必须始终由 `_friend_help_uid(role, cardId)` 派生，**不可**复用好友自己的卡 uid。
  `store.get_friend_by_role_id` 只能当"取行"，**绝不可当"是不是好友"的判据**（会静默丢格子）。
- **阳光房宠物**：`GardenPet.txt` 的 `priceForever == unlockExp`（2=1000/3=2000/4=10000/5=20000），
  这就是"1钻石=1好感度、达到阈值解锁"的实现基础；`priceTrial` 是 1 天卡价格（10/20/100/200）。
  永久购买必须**用自己的钻石直接解锁**（不能先要求好感度达标，否则死锁）。
  规则 3：每只已永久宠物给其他宠物永久价打 9 折，上限 5 折，且要同时反映到下发商店的
  `Dto_Pet` price(8)。好感度的唯一来源是花钻石，收获只加经验池。
- **星魄 2121**：`ItemInfo.txt` 写"第 12 章起精英难度掉落"，`fuben.txt` 用 `bonusOptional=2121:2:2000`
  表达（副本路径自动生效）。**主线冒险（`Cmd_TD_ReportLevelResult_CS`）没有任何 bonusItems**，
  所以主线必须另给 `td_star_soul_reward()`（4%/章递增，第 5 章起 20%）。
- **给借卡/外部卡加"成员校验"要小心**：`build_formation_info_fuben` 里对未映射的借卡必须
  "照发空属性也要占住格子"，不能 `continue` 丢掉——`tests/test_adventure_formation.py` 会抓。

## 占位 PNG 与 repeated 字段兜底（2026-10-04）
- **`serve_client.py` 的 `PLACEHOLDER_PNG` 是全局单点故障**：所有缺失图片都返回它，
  一处坏字节 = 任意面板抛 `ArgumentError #2015`（栈里只有 `BitmapData/copyPixels` /
  `BlitData/SetContent`，完全看不出是图片问题）。历史故障：IDAT 长度声明 11、真实 13 字节。
  **改它之前必须跑 `tests/test_placeholder_png.py`**（重解析签名/块长/每个 CRC32/zlib/alpha）。
- **`populate_message` 的 repeated 处理是个刻意取舍**：`_MSG_FIELD_RE` 的 `(?<!Repeated)`
  负向前瞻是为了避免预填 repeated 撞 `RangeError #2006`；代价是"唯一字段是 repeated"的 SC
  兜底产出 0 字节。现已用 `_REP_MSG_FIELD_RE` 补上：每个 repeated 消息字段发**恰好一个**空元素。
  **这类 SC 仍应写显式 handler**（兜底只能保证"不崩"，给不出真实列表）。
- **客户端命令注册表**：新增 handler 必须同时加进 `REQUEST_HANDLERS`；
  未注册的命令走 `response_for(sc)` → `populate_message`，`(unhandled msg: %s)` 是 **DEBUG 级**，
  INFO 日志里看不到。
- **超级配药是两轴独立的**：左列金币档（costType 1/2/3 = 20/50/100 杯）决定**品质分布**，
  右列分解档（changeType 3/4/5）只做**分解判定**。曾把 changeType 当 dietician 传，
  导致最便宜的分解设置产出最差试剂，且 changeType 3/4 产出完全相同。
- **跑测试必须用 `_run_tests.py`**（每文件独立进程）。`unittest discover` 会因各测试在
  模块级改写 `store.DB_PATH` 而互相污染报大量假 ERROR。
- **Windows 下部分测试退出码非 0 是环境噪声**：sqlite 句柄未释放 → `tempfile` 清理
  `PermissionError: [WinError 32]`，用例本身 0 失败。判断依据是 `^(FAIL|ERROR):` 计数为 0。
- **阳光房宠物不是战斗属性**：`GardenPet.txt` 的 `effect`/`addExp` 作用于**阳光房内自动拾取经验球**，
  由客户端 `GardenUtils.getPetExpBuff`（`addExp * 3600 / time`）本地算，服务端只管拥有/上阵状态。
  玩家看不到属性变化是正常的，别当 bug 查。
- **看 `server_runtime.log` 判归属**：`local guild adapter ready` + `loaded N templates` 前奏
  是每个测试进程启动时打的，`lvl=100 coin=600` 这类恒定值也是测试账号。
  **测试进程会污染日志**，别拿它的 `LEVEL PASSED` 当真实玩家通关证据。

## 世界地图（ModuleMap）约定
- 贴图：客户端从 `/assets/map/maoxian_2/` 加载 17 张 tileset（尺寸由 ModuleMap.swf 内嵌
  WorldMapRes1/2.tmx 声明，必须分毫不差否则 #2015）。生成器 `map_tiles_tools/gen_worldmap_tiles.py`。
- TMX 72 个城池点 = 交错等距像素坐标（x=col*128+(row%2)*64, y=row*64）；
  Dto_WorldMap_Pos 用像素坐标，玩家城 (1152,1152)，常量在 worldmap_local.CITY_PLOT_XY。
- ServerConfig `sTDInheritNeedItem` 格式 "itemId:num"，为空会让挑战窗口 #1009。
