package com.qq.modules.endlessFuben
{
   import com.qq.templates.font.FontHKHB;
   
   public class EndlessFubenConst
   {
      
      public static const Event_Request_Info_Success:String = "Event_Request_Info_Success";
      
      public static const Event_Request_Refresh_Reward:String = "Event_Request_Refresh_Reward";
      
      public static const Event_Request_Fight_Result:String = "Event_Request_Fight_Result";
      
      public static const EVENT_SIMPLE_RANK_SELF_UPDATE:String = "EVENT_SIMPLE_RANK_SELF_UPDATE";
      
      public static const EVENT_RANK_LIST_UPDATE:String = "EVENT_RANK_LIST_UPDATE";
      
      public static const EVENT_RANK_SELF_UPDATE:String = "EVENT_RANK_SELF_UPDATE";
      
      public static const EVENT_Request_Reset_Success:String = "EVENT_Request_Reset_Success";
      
      public static const Max_Challenge_Num:int = 50;
      
      public static const TxT_Rule:String = "1. 无尽炼狱在小镇{0}级时开启，共有7层难度，由全服镇长共同参与<br>2. 无尽炼狱每周进行2轮，每周{1}、周{2}0点准时开启并持续{3}小时,开启后首次通过任意关卡可获得该难度首通奖励，重复挑战无奖励<br>3. 每轮挑战可以出战至多16个植物，当任一植物阵亡后不可继续使用<br>4. 每次挑战胜利后保留植物剩余血量到下一关卡。如果对战斗结果不满意，可以选择重新挑战来获得更好的成绩<br>5. 每次挑战失败后扣除1次失败次数。如果失败次数用完，可以选择重新开始或者结束本轮挑战<br>6. 每轮挑战结束后，根据各镇长所在炼狱难度的通关数量决定排名和奖励,通关数量相同时按照通关战力高低进行排序<br>7. 每轮挑战结束后，根据各层炼狱内的排名决定挑战者到下轮挑战的晋级、保级和降级结果<br>8. 炼狱关卡内容将随游戏更新进行实时调整，以确保关卡体验";
      
      public static const Txt_AutoAttackTip:String = "本难度历史最高成绩为第{0}关，可以使用一键扫荡到第{1}关";
      
      public static const Txt_RefreshLeftTimes:String = "今日免费重置：<font color=\'{3}\'>{0}</font>/{1}<br>每日0点恢复免费次数；用完后每次{2}钻购买重置";
      
      public static const Last_Result_None:int = 0;
      
      public static const Last_Result_Win:int = 1;
      
      public static const Last_Result_Fail:int = 2;
      
      public static const PROPERTY_INDEX:uint = 0;
      
      public static const PROPERTY_ROLE:uint = 1;
      
      public static const PROPERTY_ZONEID:uint = 2;
      
      public static const PROPERTY_TRIAL:uint = 3;
      
      public static const PROPERTY_REACH_TIME:uint = 5;
      
      public static const PROPERTY_VIEW:uint = 6;
      
      public static const PROPERTY_RESULT:uint = 7;
      
      public static const PROPERTY_PASS_FP:uint = 8;
      
      public static const RESULT_PROMOTION:int = 1;
      
      public static const RESULT_DOWNGRADE:int = 2;
      
      public static const RESULT_MAINTAIN:int = 0;
      
      public static var rankWidthArr:Array = [60,130,110,110,110,110];
      
      public static var rankTitleArr:Array = [FontHKHB.RANK_TITLE_INDEX,FontHKHB.RANK_TITLE_ROLE,FontHKHB.RANK_TITLE_ROLE_ZONEID,FontHKHB.RANK_TITLE_TRIAL,FontHKHB.RANK_TITLE_RANK_PASS_FP,FontHKHB.RANK_TITLE_RANK_RESULT];
      
      public static var rankInfoTypeArr:Array = [EndlessFubenConst.PROPERTY_INDEX,EndlessFubenConst.PROPERTY_ROLE,EndlessFubenConst.PROPERTY_ZONEID,EndlessFubenConst.PROPERTY_TRIAL,EndlessFubenConst.PROPERTY_PASS_FP,EndlessFubenConst.PROPERTY_RESULT];
      
      public static const PRE_CARD_BOUNDS:int = 16;
      
      public static const Challenge_State_Close:int = 0;
      
      public static const Challenge_State_Never:int = 1;
      
      public static const Challenge_State_No_Times_Never:int = 2;
      
      public static const Challenge_State_No_Times_Batch:int = 3;
      
      public static const Challenge_State_Success_Not_SaveHP:int = 4;
      
      public static const Challenge_State_Success_SaveHP:int = 5;
      
      public static const Challenge_State_No_Times:int = 6;
      
      public static const Challenge_State_Batch:int = 7;
      
      public static const PAGE_BOUNDS:int = 10;
      
      public function EndlessFubenConst()
      {
         super();
      }
   }
}

