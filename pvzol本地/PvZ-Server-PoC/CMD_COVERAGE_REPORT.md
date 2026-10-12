# 客户端/服务端命令覆盖率报告

生成时间：2026-09-27 16:54:54

- 客户端协议类：`Assets/src/PVZ/Cmd/*.as`（共 2075 个 .as 文件）
- 客户端主动请求（`Cmd_*_CS`）：**767** 条
- 服务端已注册 handler（`REQUEST_HANDLERS`）：**434** 条
- 仍无 handler 的请求：**334** 条（43.5%），请求到来时由通用空包 `_SC` 兜底

## 本次补齐的功能（24 条）

实现文件：`PvZ-Server-PoC/missing_features_local.py`，注册处 `pvz_poc_server.py:18590`，测试 `tests/test_missing_features.py`。

| 模块 | 命令 |
|---|---|
| 邮件 | `Cmd_Mail_GetOutline_CS` |
| 邮件 | `Cmd_Mail_GetContent_CS` |
| 邮件 | `Cmd_Mail_Send_CS` |
| 邮件 | `Cmd_Mail_GuildSend_CS` |
| 邮件 | `Cmd_Mail_Delete_CS` |
| 邮件 | `Cmd_Mail_AllDelete_CS` |
| 邮件 | `Cmd_Mail_ObtainAttachment_CS` |
| 邮件 | `Cmd_Mail_ObtainAllAttachment_CS` |
| 无尽副本 | `Cmd_NoEndFuben_Info_CS` |
| 无尽副本 | `Cmd_NoEndFuben_Role_CS` |
| 无尽副本 | `Cmd_NoEndFuben_GetFormation_CS` |
| 无尽副本 | `Cmd_NoEndFuben_SetFormation_CS` |
| 无尽副本 | `Cmd_NoEndFuben_SetPreCard_CS` |
| 无尽副本 | `Cmd_NoEndFuben_Fight_CS` |
| 无尽副本 | `Cmd_NoEndFuben_ConfirmFight_CS` |
| 无尽副本 | `Cmd_NoEndFuben_Sweep_CS` |
| 无尽副本 | `Cmd_NoEndFuben_Reset_CS` |
| 无尽副本 | `Cmd_NoEndFuben_TakeReward_CS` |
| 无尽塔防 | `Cmd_NoEndTD_Vote_CS` |
| 支线任务 | `Cmd_AuxQuest_GetAuxQuest_CS` |
| 支线任务 | `Cmd_AuxQuest_Operate_CS` |
| 支线任务 | `Cmd_AuxQuest_TakeReward_CS` |
| 七日登录 | `Cmd_SevenDayLogin_GetInfo_CS` |
| 七日登录 | `Cmd_SevenDayLogin_TakeReward_CS` |

## 未实现的命令（按模块，共 334 条）

| 模块 | 数量 | 说明 |
|---|---:|---|
| Friend | 23 | 好友系统：纯单机可部分实现（列表/赠送体力/黑名单） |
| ExpeditionFight | 20 | 远征：跨服地图玩法，需要世界地图数据 |
| TD | 18 | 塔防：需确认具体子命令（可能为已下线子模式） |
| Guard | 17 | 守护：需玩法配置，暂不建议 |
| GuildFight | 17 | 公会战：可基于现有 guild_local 扩展，需战斗/报名/布阵 |
| FlexibleActivity | 15 | 弹性活动：随版本变动的小活动入口 |
| AllServerRankArena | 14 | 全服排位竞技：需全服排行榜 |
| TopFight | 13 | 巅峰对决：需对手匹配池 |
| Melee | 12 | 乱斗：需匹配池 |
| Battle | 10 | 战斗通用：_report/录像类，可补齐 |
| War | 10 | 战争玩法：需地图数据 |
| Mine | 9 | 矿洞：可单机实现 |
| PayShop | 8 | 商城/支付：离线环境无意义，建议保留空回包 |
| City | 7 | 城镇：多为区间切换展示 |
| Farm | 7 | 家园/农场：部分被 garden 覆盖 |
| SecondPwd | 7 | 二级密码：可单机实现 |
| Temple | 7 | 神殿/探险：需  |
| CastleAdventure | 6 | 城堡冒险：掷骰子玩法，可单机实现 |
| Halloween | 6 |  |
| FlexibleActivity27 | 5 |  |
| Item | 5 | 道具：需核对是否已有同名 handler |
| Shelling | 5 |  |
| Adventure | 4 |  |
| DailyPay | 4 | 每日充值：支付相关，离线无意义 |
| Hoodle | 4 |  |
| MergeServerWar | 4 |  |
| Setout | 4 |  |
| LuckyStar | 3 |  |
| OfflineReward | 3 |  |
| Outpost | 3 |  |
| SlotMachine | 3 |  |
| Active | 2 |  |
| BalancePk | 2 |  |
| Card | 2 |  |
| DailyVow | 2 |  |
| Flag | 2 |  |
| Garden | 2 |  |
| GoldExchange | 2 |  |
| Market | 2 |  |
| QQBrowser | 2 | QQ 浏览器平台接口 |
| Qzone | 2 | QQ 空间平台接口 |
| Scratch | 2 |  |
| Spring | 2 |  |
| SuperFlopCard | 2 |  |
| ThanksGiving | 2 |  |
| Buff | 1 |  |
| ChangeRoleName | 1 | 改名：可单机实现 |
| CheckOldUser | 1 | 老玩家校验：可单机实现 |
| ClientDownLog | 1 | 客户端崩溃日志上报 |
| CrossGame | 1 | 跨游戏领奖：外链活动 |
| DaveLabor | 1 |  |
| DoGm | 1 | GM 指令（无 CIA 权限） |
| DoIdipGm | 1 | iDIP GM 指令（需腾讯后台） |
| GetLevelRewardInfo | 1 |  |
| Guide | 1 |  |
| GuildBid | 1 |  |
| GuildCancerLeaderDeliver | 1 |  |
| GuildDelateLeader | 1 |  |
| GuildFertilizer | 1 |  |
| GuildFertilizerLog | 1 |  |
| GuildLeaderDeliver | 1 |  |
| GuildLevelUp | 1 |  |
| Init | 1 |  |
| LogicOpenInfo | 1 |  |
| Nutrition | 1 |  |
| OpenPrivilege | 1 |  |
| Plant | 1 |  |
| PlayZoneTask | 1 |  |
| Promotion | 1 |  |
| SetRoleName | 1 |  |
| SetSignature | 1 |  |
| SignIn | 1 |  |
| StrengthenEntry | 1 |  |
| TakeLevelReward | 1 |  |
| Treasure | 1 | 探宝：部分已被 treasure_local 覆盖，需核对 |
| Vip | 1 |  |
| VipBuy | 1 |  |
| VipGetRenewGift | 1 |  |

### 明细

**Active（2）**

```
Cmd_Active_GetInfo_CS  Cmd_Active_TakeReward_CS
```

**Adventure（4）**

```
Cmd_Adventure_Auto_Do_CS  Cmd_Adventure_Do_CS  Cmd_Adventure_GetInfo_CS  Cmd_Adventure_Refresh_CS
```

**AllServerRankArena（14）**

```
Cmd_AllServerRankArena_Challenge_CS  Cmd_AllServerRankArena_GetAttackFormation_CS  Cmd_AllServerRankArena_GetDefendFormation_CS  Cmd_AllServerRankArena_GetOpponentFormation_CS
Cmd_AllServerRankArena_GetOpponent_CS  Cmd_AllServerRankArena_GetRecorder_CS  Cmd_AllServerRankArena_Info_CS  Cmd_AllServerRankArena_Rank_CS
Cmd_AllServerRankArena_SaveDefendFormation_CS  Cmd_AllServerRankArena_SetAttackFormation_CS  Cmd_AllServerRankArena_SetDefendFormation_CS  Cmd_AllServerRankArena_TakeDailyRankReward_CS
Cmd_AllServerRankArena_TakeMaxRankReward_CS  Cmd_AllServerRankArena_TakeScoreReward_CS
```

**BalancePk（2）**

```
Cmd_BalancePk_Fight_CS  Cmd_BalancePk_SetFormation_CS
```

**Battle（10）**

```
Cmd_Battle_CancelDefendTeam_CS  Cmd_Battle_GetAutoRestoreDefenceTeamHealthHp_CS  Cmd_Battle_HasNewFormationPos_CS  Cmd_Battle_QueryRankReport_CS
Cmd_Battle_QueryReport_CS  Cmd_Battle_QuerySpecialReport_CS  Cmd_Battle_SeeNewFormationPos_CS  Cmd_Battle_SetAutoRestoreDefenceTeamHealthHp_CS
Cmd_Battle_SetDefaultTeam_CS  Cmd_Battle_SetDefendTeam_CS
```

**Buff（1）**

```
Cmd_Buff_GetList_CS
```

**Card（2）**

```
Cmd_Card_RecoverHealth_CS  Cmd_Card_SwapGeneItem_CS
```

**CastleAdventure（6）**

```
Cmd_CastleAdventure_Dice_CS  Cmd_CastleAdventure_GetInfo_CS  Cmd_CastleAdventure_GiveUp_CS  Cmd_CastleAdventure_Move_CS
Cmd_CastleAdventure_Reset_CS  Cmd_CastleAdventure_TakeCompletionReward_CS
```

**ChangeRoleName（1）**

```
Cmd_ChangeRoleName_CS
```

**CheckOldUser（1）**

```
Cmd_CheckOldUser_CS
```

**City（7）**

```
Cmd_City_ChangeArea_CS  Cmd_City_FixedMove_CS  Cmd_City_GetBuildingEvent_CS  Cmd_City_GetTownDurability_CS
Cmd_City_Init_CS  Cmd_City_MergeServerFlag_CS  Cmd_City_SetNullShow_CS
```

**ClientDownLog（1）**

```
Cmd_ClientDownLog_CS
```

**CrossGame（1）**

```
Cmd_CrossGame_TakeReward_CS
```

**DailyPay（4）**

```
Cmd_DailyPay_Exchange_CS  Cmd_DailyPay_GetItem_CS  Cmd_DailyPay_GetPayInfo_CS  Cmd_DailyPay_Pay_CS
```

**DailyVow（2）**

```
Cmd_DailyVow_DoVow_CS  Cmd_DailyVow_GetInfo_CS
```

**DaveLabor（1）**

```
Cmd_DaveLabor_GetInfo_CS
```

**DoGm（1）**

```
Cmd_DoGm_CS
```

**DoIdipGm（1）**

```
Cmd_DoIdipGm_CS
```

**ExpeditionFight（20）**

```
Cmd_ExpeditionFight_AttackNpc_CS  Cmd_ExpeditionFight_Attack_CS  Cmd_ExpeditionFight_ChangeCity_CS  Cmd_ExpeditionFight_CityRole_CS
Cmd_ExpeditionFight_DetailInfo_CS  Cmd_ExpeditionFight_GetFormation_CS  Cmd_ExpeditionFight_Heartbeat_CS  Cmd_ExpeditionFight_Info_CS
Cmd_ExpeditionFight_MoveToFront_CS  Cmd_ExpeditionFight_QueryReport_CS  Cmd_ExpeditionFight_RefreshRankInfo_CS  Cmd_ExpeditionFight_RefreshSumRankInfo_CS
Cmd_ExpeditionFight_SetAttackCity_CS  Cmd_ExpeditionFight_SetDefendCity_CS  Cmd_ExpeditionFight_SetFormation_CS  Cmd_ExpeditionFight_SetTarget_CS
Cmd_ExpeditionFight_SignUpList_CS  Cmd_ExpeditionFight_SignUp_CS  Cmd_ExpeditionFight_SpyCityRole_CS  Cmd_ExpeditionFight_TakeReward_CS
```

**Farm（7）**

```
Cmd_Farm_CollectProduct_CS  Cmd_Farm_Cultivate_CS  Cmd_Farm_DigUpPlant_CS  Cmd_Farm_SowMyBlock_CS
Cmd_Farm_SowOtherBlock_CS  Cmd_Farm_StealProduct_CS  Cmd_Farm_UnlockBlock_CS
```

**Flag（2）**

```
Cmd_Flag_Close_CS  Cmd_Flag_GetList_CS
```

**FlexibleActivity（15）**

```
Cmd_FlexibleActivity_ConsumeFundBuy_CS  Cmd_FlexibleActivity_DaveRedPacketGet_CS  Cmd_FlexibleActivity_GrowFundBuy_CS  Cmd_FlexibleActivity_GrowFundGetReturn_CS
Cmd_FlexibleActivity_GrowFundReurnList_CS  Cmd_FlexibleActivity_Operate_CS  Cmd_FlexibleActivity_RandomReward_CS  Cmd_FlexibleActivity_RedPacketList_CS
Cmd_FlexibleActivity_SeckillList_CS  Cmd_FlexibleActivity_Sign_CS  Cmd_FlexibleActivity_TakePayReward_CS  Cmd_FlexibleActivity_TryLuck_CS
Cmd_FlexibleActivity_WatchVideo_CS  Cmd_FlexibleActivity_WealthGodRedPacketGet_CS  Cmd_FlexibleActivity_WealthGodRedPacketSend_CS
```

**FlexibleActivity27（5）**

```
Cmd_FlexibleActivity27_Exchange_CS  Cmd_FlexibleActivity27_History_CS  Cmd_FlexibleActivity27_Random_CS  Cmd_FlexibleActivity27_RecycleReward_CS
Cmd_FlexibleActivity27_TakeReward_CS
```

**Friend（23）**

```
Cmd_Friend_AddBlackList_CS  Cmd_Friend_ApplyAgree_CS  Cmd_Friend_ApplyIgnore_CS  Cmd_Friend_ApplyRecommend_CS
Cmd_Friend_Apply_CS  Cmd_Friend_DelBlackList_CS  Cmd_Friend_DeleteEnergyInfo_CS  Cmd_Friend_DeleteFightInfo_CS
Cmd_Friend_Delete_CS  Cmd_Friend_GetApplyList_CS  Cmd_Friend_GetBlackList_CS  Cmd_Friend_GetEnergyList_CS
Cmd_Friend_GetFightList_CS  Cmd_Friend_GetInviteGrowReward_CS  Cmd_Friend_GetMyPlant_CS  Cmd_Friend_GetRecommendList_CS
Cmd_Friend_InviteCountReward_CS  Cmd_Friend_InviteCount_CS  Cmd_Friend_InviteFriend_CS  Cmd_Friend_PresentEnergy_CS
Cmd_Friend_QueryInviteGrowReward_CS  Cmd_Friend_ReturnEnergyInfo_CS  Cmd_Friend_TakeEnergyInfo_CS
```

**Garden（2）**

```
Cmd_Garden_BuyPot_CS  Cmd_Garden_Unlock_CS
```

**GetLevelRewardInfo（1）**

```
Cmd_GetLevelRewardInfo_CS
```

**GoldExchange（2）**

```
Cmd_GoldExchange_Exchange_CS  Cmd_GoldExchange_GetInfo_CS
```

**Guard（17）**

```
Cmd_Guard_BuyLight_CS  Cmd_Guard_CancelRob_CS  Cmd_Guard_Create_CS  Cmd_Guard_DefenceHistory_CS
Cmd_Guard_FightRobTarget_CS  Cmd_Guard_GetFormation_CS  Cmd_Guard_GetGuardFormation_CS  Cmd_Guard_Info_By_ID_CS
Cmd_Guard_Info_CS  Cmd_Guard_InviteList_CS  Cmd_Guard_Invite_CS  Cmd_Guard_Join_CS
Cmd_Guard_RefreshRobTarget_CS  Cmd_Guard_RobHistory_CS  Cmd_Guard_SetFormation_CS  Cmd_Guard_SetGuardFormation_CS
Cmd_Guard_TakeReward_CS
```

**Guide（1）**

```
Cmd_Guide_SkipSome_CS
```

**GuildBid（1）**

```
Cmd_GuildBid_CS
```

**GuildCancerLeaderDeliver（1）**

```
Cmd_GuildCancerLeaderDeliver_CS
```

**GuildDelateLeader（1）**

```
Cmd_GuildDelateLeader_CS
```

**GuildFertilizer（1）**

```
Cmd_GuildFertilizer_CS
```

**GuildFertilizerLog（1）**

```
Cmd_GuildFertilizerLog_CS
```

**GuildFight（17）**

```
Cmd_GuildFight_Allot_CS  Cmd_GuildFight_ApplyKnockout_CS  Cmd_GuildFight_Bet_CS  Cmd_GuildFight_FightQueue_CS
Cmd_GuildFight_GetAllotInfo_CS  Cmd_GuildFight_GetBetNumReward_CS  Cmd_GuildFight_GetBetResult_CS  Cmd_GuildFight_GetBetReturnReward_CS
Cmd_GuildFight_GetClashRecord_CS  Cmd_GuildFight_GetKnockout_CS  Cmd_GuildFight_GetReward_CS  Cmd_GuildFight_GuildClash_CS
Cmd_GuildFight_SetCycles_CS  Cmd_GuildFight_SetKnockout_CS  Cmd_GuildFight_SwapWay_CS  Cmd_GuildFight_SyncKnockoutFight_CS
Cmd_GuildFight_WayRoleInfo_CS
```

**GuildLeaderDeliver（1）**

```
Cmd_GuildLeaderDeliver_CS
```

**GuildLevelUp（1）**

```
Cmd_GuildLevelUp_CS
```

**Halloween（6）**

```
Cmd_Halloween_HitPumpkin_CS  Cmd_Halloween_Reset_CS  Cmd_Halloween_TakeFinalReward_CS  Cmd_Halloween_TakeFullServerReward_CS
Cmd_Halloween_TakeNutHammer_CS  Cmd_Halloween_TakeReward_CS
```

**Hoodle（4）**

```
Cmd_Hoodle_ChangeNut_CS  Cmd_Hoodle_Fire_CS  Cmd_Hoodle_GetInfo_CS  Cmd_Hoodle_TakeReward_CS
```

**Init（1）**

```
Cmd_Init_CS
```

**Item（5）**

```
Cmd_Item_Exchange_CS  Cmd_Item_GetCardPieceBagInfo_CS  Cmd_Item_MergeChlorophyll_CS  Cmd_Item_SeeNewItems_CS
Cmd_Item_Use_CS
```

**LogicOpenInfo（1）**

```
Cmd_LogicOpenInfo_CS
```

**LuckyStar（3）**

```
Cmd_LuckyStar_Buy_CS  Cmd_LuckyStar_Info_CS  Cmd_LuckyStar_Take_DailyPack_CS
```

**Market（2）**

```
Cmd_Market_DoTrade_CS  Cmd_Market_GetInfo_CS
```

**Melee（12）**

```
Cmd_Melee_AutoJoinFight_CS  Cmd_Melee_GetFightRecorder_CS  Cmd_Melee_GetFormation_CS  Cmd_Melee_GetMyInfo_CS
Cmd_Melee_GetRankList_CS  Cmd_Melee_GetSignUpStat_CS  Cmd_Melee_Inspire_CS  Cmd_Melee_JoinFight_CS
Cmd_Melee_SetFormation_CS  Cmd_Melee_ShowReward_CS  Cmd_Melee_SignUp_CS  Cmd_Melee_TakeReward_CS
```

**MergeServerWar（4）**

```
Cmd_MergeServerWar_GetDailyInfo_CS  Cmd_MergeServerWar_GetTotalInfo_CS  Cmd_MergeServerWar_TakeDailyReward_CS  Cmd_MergeServerWar_TakeTotalReward_CS
```

**Mine（9）**

```
Cmd_Mine_CancelMining_CS  Cmd_Mine_CollectAllMine_CS  Cmd_Mine_CollectProduct_CS  Cmd_Mine_GetDefendTeam_CS
Cmd_Mine_GetInfo_CS  Cmd_Mine_GetMiningInfo_CS  Cmd_Mine_Mining_CS  Cmd_Mine_RecallTeam_CS
Cmd_Mine_SetOutTeam_CS
```

**Nutrition（1）**

```
Cmd_Nutrition_GetReagentSlots_CS
```

**OfflineReward（3）**

```
Cmd_OfflineReward_ActiveTakeReward_CS  Cmd_OfflineReward_GetInfo_CS  Cmd_OfflineReward_TakeReward_CS
```

**OpenPrivilege（1）**

```
Cmd_OpenPrivilege_CS
```

**Outpost（3）**

```
Cmd_Outpost_GetAllyTeam_CS  Cmd_Outpost_ReturnAllyTeam_CS  Cmd_Outpost_SetFormation_CS
```

**PayShop（8）**

```
Cmd_PayShop_BuyGoods_CS  Cmd_PayShop_GetDayTicketList_CS  Cmd_PayShop_GetDayTicket_CS  Cmd_PayShop_GetFlagList_CS
Cmd_PayShop_GetGiftTicketList_CS  Cmd_PayShop_GetGiftTicket_CS  Cmd_PayShop_GetList_CS  Cmd_PayShop_SaveFlag_CS
```

**Plant（1）**

```
Cmd_Plant_Unlock_CS
```

**PlayZoneTask（1）**

```
Cmd_PlayZoneTask_CS
```

**Promotion（1）**

```
Cmd_Promotion_Buy_CS
```

**QQBrowser（2）**

```
Cmd_QQBrowser_TakeDailyLoginGift_CS  Cmd_QQBrowser_TakeExclusiveGift_CS
```

**Qzone（2）**

```
Cmd_Qzone_QueryShareList_CS  Cmd_Qzone_SendShareMsg_CS
```

**Scratch（2）**

```
Cmd_Scratch_GetInfo_CS  Cmd_Scratch_Shave_CS
```

**SecondPwd（7）**

```
Cmd_SecondPwd_Conditon_CS  Cmd_SecondPwd_Info_CS  Cmd_SecondPwd_InputPwd_CS  Cmd_SecondPwd_Modify_CS
Cmd_SecondPwd_NeedPwd_CS  Cmd_SecondPwd_Reset_CS  Cmd_SecondPwd_SetPwd_CS
```

**SetRoleName（1）**

```
Cmd_SetRoleName_CS
```

**SetSignature（1）**

```
Cmd_SetSignature_CS
```

**Setout（4）**

```
Cmd_Setout_BuyMapWarSetoutTimes_CS  Cmd_Setout_GetCityList_CS  Cmd_Setout_GetSetoutTimes_CS  Cmd_Setout_GetTempleList_CS
```

**Shelling（5）**

```
Cmd_Shelling_Do_CS  Cmd_Shelling_Exchange_CS  Cmd_Shelling_GetExchangeInfo_CS  Cmd_Shelling_GetInfo_CS
Cmd_Shelling_GetTop_CS
```

**SignIn（1）**

```
Cmd_SignIn_SetFillInfo_CS
```

**SlotMachine（3）**

```
Cmd_SlotMachine_Exchange_CS  Cmd_SlotMachine_Spin_CS  Cmd_SlotMachine_TakeCoin_CS
```

**Spring（2）**

```
Cmd_Spring_GetCredits_CS  Cmd_Spring_OpenBox_CS
```

**StrengthenEntry（1）**

```
Cmd_StrengthenEntry_GetInfo_CS
```

**SuperFlopCard（2）**

```
Cmd_SuperFlopCard_BuyTimes_CS  Cmd_SuperFlopCard_FlopCard_CS
```

**TD（18）**

```
Cmd_TD_AfterReportResult_CS  Cmd_TD_BeforeReportResult_CS  Cmd_TD_BuyEnergyBean_CS  Cmd_TD_BuySun_CS
Cmd_TD_ChallengeInheritLastWeekScore_CS  Cmd_TD_CollectProduct_CS  Cmd_TD_GetChallengeLevelInfo_CS  Cmd_TD_GetOldPromotionRewardInfo_CS
Cmd_TD_GetProductInfo_CS  Cmd_TD_GetPromotionRewardInfo_CS  Cmd_TD_GetPromotionReward_CS  Cmd_TD_GetRankRewardInfo_CS
Cmd_TD_GetRankReward_CS  Cmd_TD_GetRank_CS  Cmd_TD_StartTDGame_CS  Cmd_TD_TakeOldPromotionReward_CS
Cmd_TD_UnlockPrivilegePlant_CS  Cmd_TD_UseSkill_CS
```

**TakeLevelReward（1）**

```
Cmd_TakeLevelReward_CS
```

**Temple（7）**

```
Cmd_Temple_Detect_CS  Cmd_Temple_GetGuarderInfo_CS  Cmd_Temple_GetTempleInfo_CS  Cmd_Temple_GetTempleLordInfo_CS
Cmd_Temple_SetOut_CS  Cmd_Temple_ShowDailyReward_CS  Cmd_Temple_TakeDailyReward_CS
```

**ThanksGiving（2）**

```
Cmd_ThanksGiving_GetInfo_CS  Cmd_ThanksGiving_Lottery_CS
```

**TopFight（13）**

```
Cmd_TopFight_Bet_CS  Cmd_TopFight_GetBetInfo_CS  Cmd_TopFight_GetFormation_CS  Cmd_TopFight_HistoryRoleInfo_CS
Cmd_TopFight_PairInfo_CS  Cmd_TopFight_RecordInfo_CS  Cmd_TopFight_RoleInfo_CS  Cmd_TopFight_RoleList_CS
Cmd_TopFight_SaveFormation_CS  Cmd_TopFight_SetFormation_CS  Cmd_TopFight_TakeBetReward_CS  Cmd_TopFight_TakeReward_CS
Cmd_TopFight_TopN_CS
```

**Treasure（1）**

```
Cmd_Treasure_Seize_CS
```

**Vip（1）**

```
Cmd_Vip_Renewal_CS
```

**VipBuy（1）**

```
Cmd_VipBuy_CS
```

**VipGetRenewGift（1）**

```
Cmd_VipGetRenewGift_CS
```

**War（10）**

```
Cmd_War_DeclareWar_CS  Cmd_War_GetDeclareWarStat_CS  Cmd_War_GetHistory_CS  Cmd_War_GetMapHistory_CS
Cmd_War_GetSimpleHistory_CS  Cmd_War_PreSetOut_CS  Cmd_War_RecallTeam_CS  Cmd_War_RecoverHp_CS
Cmd_War_SetOut_CS  Cmd_War_SpeedUpTeam_CS
```
