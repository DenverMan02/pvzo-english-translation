package com.qq.modules.quest.logic
{
   import PVZ.Cmd.Dto_ItemInfo;
   import PVZ.Cmd.Dto_Quest_Info;
   import com.qq.modules.actFund.command.ActFundCmd;
   import com.qq.modules.bank.command.BankViewCmd;
   import com.qq.modules.brain.command.BrainViewCmd;
   import com.qq.modules.card.CardGlobals;
   import com.qq.modules.card.command.CardViewCmd;
   import com.qq.modules.cardDNA.command.CardDNAViewCmd;
   import com.qq.modules.dailyTD.command.DailyTdViewCmd;
   import com.qq.modules.dailyVow.command.DailyVowViewCmd;
   import com.qq.modules.dummy.command.DummyViewCmd;
   import com.qq.modules.everyDayBuy.command.EveryDayBuyCmd;
   import com.qq.modules.friend.command.FriendViewCmd;
   import com.qq.modules.fuzion.command.FuzionCmd;
   import com.qq.modules.goldExchange.command.GoldExchangeViewCmd;
   import com.qq.modules.main.command.GameFlowCmd;
   import com.qq.modules.nutrition.NutritionGlobal;
   import com.qq.modules.quest.model.templates.ce.QuestInfoTemplateCE;
   import com.qq.modules.roleInfo.command.RoleInfoCmd;
   import com.qq.modules.shop.command.ShopCmd;
   import com.qq.modules.signIn.command.SignInViewCmd;
   import com.qq.modules.treasure.command.TreasureCmd;
   import com.qq.modules.union.command.UnionCmd;
   import com.qq.modules.unionFuben.command.UnionFubenCmd;
   import com.qq.modules.welfareHall.WelfareHallTabType;
   import com.qq.modules.welfareHall.command.WelfareHallCmd;
   import com.qq.modules.worldBoss.command.WorldBossCmd;
   import com.qq.modules.yetiTD.command.YetiTDViewCmd;
   
   public class QuestHelper
   {
      
      public function QuestHelper()
      {
         super();
      }
      
      public static function getCondition(param1:QuestInfoTemplateCE, param2:Dto_Quest_Info) : String
      {
         var _loc3_:String = null;
         var _loc6_:int = 0;
         var _loc4_:Array = param1.finishCondition.split(":");
         if(!param1.isHideProgress)
         {
            _loc6_ = Math.min(param2.currStep,_loc4_[2]);
            _loc3_ = " ".concat(_loc6_,"/",_loc4_[2]);
         }
         else
         {
            _loc3_ = "";
         }
         return param1.finishConditionDesc.concat(_loc3_);
      }
      
      public static function getRewardList(param1:String) : Array
      {
         var _loc5_:String = null;
         var _loc6_:Array = null;
         var _loc7_:Dto_ItemInfo = null;
         var _loc2_:Array = new Array();
         var _loc3_:String = param1;
         var _loc4_:Array = _loc3_.split(";");
         for each(_loc5_ in _loc4_)
         {
            _loc6_ = _loc5_.split(",");
            if(_loc6_.length == 2)
            {
               _loc7_ = new Dto_ItemInfo();
               _loc7_.id = _loc6_[0];
               _loc7_.num = _loc6_[1];
               _loc2_.push(_loc7_);
            }
         }
         return _loc2_;
      }
      
      public static function getDailyQuestRewardList(param1:String) : Array
      {
         var _loc5_:String = null;
         var _loc6_:Array = null;
         var _loc7_:Dto_ItemInfo = null;
         var _loc2_:Array = new Array();
         var _loc3_:String = param1;
         var _loc4_:Array = _loc3_.split(";");
         for each(_loc5_ in _loc4_)
         {
            _loc6_ = _loc5_.split(":");
            if(_loc6_.length == 2)
            {
               _loc7_ = new Dto_ItemInfo();
               _loc7_.id = _loc6_[0];
               _loc7_.num = _loc6_[1];
               _loc2_.push(_loc7_);
            }
         }
         return _loc2_;
      }
      
      public static function questJump(param1:String) : void
      {
         var _loc2_:Array = null;
         var _loc3_:int = 0;
         var _loc4_:int = 0;
         var _loc5_:int = 0;
         var _loc6_:int = 0;
         var _loc7_:int = 0;
         var _loc8_:int = 0;
         if(param1 == null)
         {
            return;
         }
         _loc2_ = param1.split(":");
         switch(int(_loc2_[0]))
         {
            case 1:
               _loc3_ = int(_loc2_[1] / 10000);
               _loc4_ = int(_loc2_[1] % 1000 / 100);
               _loc5_ = int(_loc2_[1] % 10);
               GameFlowCmd.gotoLevelSelectByStateID(_loc3_,_loc4_,_loc5_);
               break;
            case 4:
               GameFlowCmd.gotoPlantTalent();
               break;
            case 9:
            case 11:
               _loc6_ = int(_loc2_[1] / 10000);
               _loc7_ = int(_loc2_[1] % 10);
               GameFlowCmd.gotoFuben(_loc6_,_loc7_);
               break;
            case 12:
               GameFlowCmd.gotoDrunkery();
               break;
            case 23:
               _loc8_ = int(_loc2_[1]);
               if(_loc8_ <= 4)
               {
                  GameFlowCmd.gotoDrunkery();
               }
               else
               {
                  CardViewCmd.showMainWd(CardGlobals.CARD_SCENE_TYPE_UPGRADE);
               }
               break;
            case 14:
            case 15:
               GameFlowCmd.enterLab();
               break;
            case 16:
            case 17:
               CardViewCmd.showMainWd(CardGlobals.CARD_SCENE_TYPE_FORGE);
               break;
            case 18:
               CardViewCmd.showMainWd(CardGlobals.CARD_SCENE_TYPE_UPGRADE);
               break;
            case 20:
            case 21:
               CardViewCmd.showMainWd();
               break;
            case 22:
               GameFlowCmd.gotoFuben();
               break;
            case 25:
            case 61:
               GameFlowCmd.gotoArena();
               break;
            case 26:
               UnionCmd.enterUnion();
               break;
            case 29:
            case 1014:
               GameFlowCmd.gotoMap();
               break;
            case 33:
               CardViewCmd.showMainWd(CardGlobals.CARD_SCENE_TYPE_CHEST);
               break;
            case 34:
               GameFlowCmd.gotoZombieIsland();
               break;
            case 36:
            case 45:
               GameFlowCmd.gotoNutrition(NutritionGlobal.VIEW_TYPE_OBTAIN);
               break;
            case 38:
            case 58:
               GameFlowCmd.gotoNutrition(NutritionGlobal.VIEW_TYPE_USE);
               break;
            case 48:
            case 49:
            case 52:
            case 57:
               GameFlowCmd.gotoGarden();
               break;
            case 55:
               GameFlowCmd.gotoWT();
               break;
            case 59:
            case 68:
               CardViewCmd.showMainWd(CardGlobals.CARD_SCENE_TYPE_EQUIPMENT);
               break;
            case 60:
               GameFlowCmd.gotoZombieIsland();
               break;
            case 62:
               GameFlowCmd.gotoRankArena();
               break;
            case 63:
               GameFlowCmd.gotoTrialRoad();
               break;
            case 65:
            case 69:
               CardViewCmd.showMainWd(CardGlobals.CARD_SCENE_TYPE_PUZZLE);
               break;
            case 1001:
               SignInViewCmd.openSignIn();
               break;
            case 1002:
               BankViewCmd.openBank();
               break;
            case 1003:
               FriendViewCmd.zoomOut();
               break;
            case 1004:
               UnionCmd.enterUnion();
               break;
            case 1005:
               WelfareHallCmd.openWindow(WelfareHallTabType.WELFARE_HALL_TAB_TYPE_DAILY_ACTIVITY);
               break;
            case 1006:
               RoleInfoCmd.buyPhysical();
               break;
            case 1007:
               DailyTdViewCmd.showMainPanel();
               break;
            case 1008:
               YetiTDViewCmd.showMainWd();
               break;
            case 1009:
               WorldBossCmd.onClickWorldBossIcon();
               break;
            case 1010:
               FuzionCmd.enterFuzion();
               break;
            case 1011:
               TreasureCmd.openTreasure();
               break;
            case 1012:
               EveryDayBuyCmd.openEveryDayBuy();
               break;
            case 1013:
               DailyVowViewCmd.openWindow();
               break;
            case 1015:
               GameFlowCmd.gotoEndlessTD();
               break;
            case 1016:
               GoldExchangeViewCmd.showBuyCoinPanel();
               break;
            case 1017:
               ShopCmd.openShop();
               break;
            case 1018:
               GameFlowCmd.gotoPlantTest();
               break;
            case 1019:
               CardViewCmd.showMainWd(CardGlobals.CARD_SCENE_TYPE_BAPTIZE);
               break;
            case 1020:
               ActFundCmd.openFundWindow(2);
               break;
            case 1021:
               CardDNAViewCmd.openDNATestWindow();
               break;
            case 1022:
               BrainViewCmd.openMainView();
               break;
            case 1023:
               GameFlowCmd.gotoHole();
               break;
            case 1024:
               UnionFubenCmd.openUnionFubenWin();
               break;
            case 1025:
               DummyViewCmd.openFubenWindow();
         }
      }
   }
}

