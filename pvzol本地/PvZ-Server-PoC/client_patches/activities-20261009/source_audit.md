# 活动与植物大作战修复源码核对（2026-10-09）

## 植物大作战入口

clientDB `DailyActInfo.txt` 第 3 项显示名为“植物大作战”，原 `finishCondition=1014:0:0`。`ModuleQuest.swf` 的 `QuestHelper.questJump` case 1014 调用 `GameFlowCmd.gotoMap()`，入口属于大地图。`Game` 中 `MapInfo` 战报页也使用这一名称，因此同时保留了空战报修复，但不能把战报修复当作地图入口修复。

旧 `worldmap_local.py` 和实际由 `consistency_repairs` 间接安装的 `worldmap_repairs.py` 均将城市 `Dto_WorldMap_CellInfo.type` 写为 0，并把玩家 faceId 当地图 avatar。原 `MapGlobals` 定义 CITY=1、MINE=2、TEMPLE=3。`MapProxy.updateCellInArea` 会由 type 获取模板，0 没有模板，最终产生已在日志出现的 #1009；植物头像 ID 也不属于 `MapCellSkinTemplate`。修复包裹最终实际启用的 GetInfo、GetCellInfo、GetGuildMemberList 路由，城市使用 type=1、原地图默认城市 skin=1；保留持久城市坐标、玩家、联盟、旗帜及会员信息。不会把多账号城市改到同一坐标。旧空坐标仅回退到既有中心地块。

区域 2 按 `MapTempleTemplate.txt` 加载且只公开 isValid=1、type 为 1/2 的 17 座原有建筑（5 游乐场、12 马戏团）。客户端地图单元使用 type=3，skin 为游乐场 12、马戏团 11。保留其原位置、templeId、名称和区域；中立建筑不指定玩家 roleId，从而使用 `MapBasicCellVO` 按坐标区分建筑。原 `Int64.compare(null)` 返回 false，此做法不会触发空引用。

`ModuleTemple.swf` 中 `TempleProxy` 对 `GetTempleInfo_SC.pos` 直接解引用。补齐 GetTempleInfo（原请求位置、区域、isTempleWarStart=false、显式 Int64 零领主、真实空联盟列表）、GetTempleLordInfo（17 个有效模板及零领主）、GetGuarderInfo（真实空驻军列表）。现存本地模型没有占领/神殿战争记录，本次只修复建筑读取和入口，不声称已实现联网占领战，也不制造已开战、虚假联盟或虚假驻军。

## 每日活动入口

旧 WelfareHall `onEnterClick` 只识别四个活动副本 type，其他条目落到无处理的 generic `daily`。服务端保持四个副本入口，clientDB `dailyActivityInfo.txt` 其余 25 个活动采用 `localQuestJump`，最小修改原 WelfareHall 方法让其调用已存在的 `QuestHelper.questJump`。所有条件数值都能在当前编译客户端的 case 中找到，并逐项核对具体调用目标。普通冒险/精英冒险参数为 9:1:1 / 9:2:1，原 QuestHelper 取 arg1%10 选择难度。clientParams 保留原中文名称与顺序。此处“植物大乱斗”原数据对应 Fuzion，不应和“植物大作战”大地图混淆。

原方法其余字节、继承 scope、exceptions 等均保留；修改源码与原方法反编译结果保存在 client_patches/activities-20261009。后续每日点卡按钮补丁由 root 基于同一已改 WelfareHall 实施，最终资源 hash 和 fileVer 由 root 统一更新，本脚本不覆盖最终模块。

## 植物测验

原 `_plant_test_enemies` 已有 6 种真实植物，章节预览恒取相同的第一个植物导致全为豌豆射手。修复每关确定性轮换队伍顺序，统一预览、实际战斗、Battle_GetFightInfo 所调用的敌人生成函数，并重新给位置/队长标记。

原 clientDB 只有 plantTestChapter、plantTestCondition、plantTestReset 三张测验表；关表包含 150 关的 monsterLevel、singlePower、totalPower、originalReward，没有敌人 plantId/cardId。保留这些原关卡数值及奖励规则，使用现有本地六种真实植物轮换。未取得原运营服的逐关敌队配置，因此本次不能称为恢复了完整原运营敌队。

## 回归验证

tests/test_activities_repairs_20261009.py 先将 player_store.DB_PATH 指向自动创建的临时目录，再导入服务端，从未用实际玩家数据库测试。覆盖所有 25 活动对应客户端 case/调用方法、普通与精英参数、4 活动副本保持不变、150 测验关等级/血量、30 关预览与战斗一致、有效地图类型和 skin、持久城市位置一致与多账号不重叠、17 座原有建筑的位置及模板、17 个建筑查询回复、真实空领主和驻军、地图首次原有地块登记之后整库读取不变、三种战报不产生零值虚假记录及重复安装不叠加。

接入：由主服务端在 legacy adapters 后调用 activities_repairs_20261009.install(_activity_server_api())。需要重新启动游戏服务以加载 Python 模块；每日入口客户端需要完整关闭 Flash 并重新打开游戏。其他客户端模块无需本次重编译。无尽、背包、每日点卡及其他并行补丁由各负责人接入。
