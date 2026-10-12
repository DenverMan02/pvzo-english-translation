package com.qq.modules.endlessFuben.model
{
   import PVZ.Cmd.Dto_AllServerNoEndFuben;
   import PVZ.Cmd.Dto_CardHpRatio;
   import PVZ.Cmd.Dto_NoEndFuben_Role;
   import com.qq.GameGloble;
   import com.qq.modules.basic.model.templates.ServerConfigTemplateFactory;
   import com.qq.modules.embattle.constant.EmbattleConst;
   import com.qq.modules.endlessFuben.EndlessFubenConst;
   import com.qq.modules.endlessFuben.EndlessFubenDataFactory;
   import com.qq.modules.rank.RankGlobal;
   import com.qq.utils.CommandDispatcher;
   import com.qq.utils.DateUtils;
   import com.tencent.protobuf.Int64;
   
   public class EndlessFubenModel
   {
      
      public var stageID:int;
      
      private var _saveLevelID:int;
      
      public var bestLevelID:int;
      
      public var leftTryNum:int;
      
      public var totalTryNum:int;
      
      public var stageTemplate:EndlessFubenStageTemplate;
      
      public var endTimeSecond:Number;
      
      public var hasPassNextStage:Boolean;
      
      public var rewardList:Array;
      
      private var _cardHpRatioList:Array;
      
      public var opened:Boolean;
      
      public var reportId:Int64;
      
      private var _rewardStageId:int;
      
      public var hasTakeReward:Boolean;
      
      public var totalPage:int;
      
      public var m_selfRecord:EndlessFubenRankRecordVO;
      
      public var m_vecAllRecord:Vector.<EndlessFubenRankRecordVO>;
      
      public var simpleRankFirst_dto:Dto_NoEndFuben_Role;
      
      public var simpleRankPrev_dto:Dto_NoEndFuben_Role;
      
      public var simpleRankSelf_dto:Dto_NoEndFuben_Role;
      
      public var lastResult:int;
      
      public var resetTimes:int;
      
      public function EndlessFubenModel()
      {
         super();
         this.totalTryNum = ServerConfigTemplateFactory.instance.iNoEndFubenLoseTimes;
         this.m_selfRecord = new EndlessFubenRankRecordVO();
         this.m_vecAllRecord = new Vector.<EndlessFubenRankRecordVO>();
         var _loc1_:int = 0;
         while(_loc1_ < EndlessFubenConst.PAGE_BOUNDS)
         {
            this.m_vecAllRecord.push(new EndlessFubenRankRecordVO());
            _loc1_++;
         }
      }
      
      public function isTakenReward(param1:int) : Boolean
      {
         if(this._rewardStageId == 0)
         {
            return false;
         }
         return param1 <= this._rewardStageId;
      }
      
      public function setRewardStageID(param1:int, param2:Boolean) : void
      {
         if(param1 > this._rewardStageId || param2)
         {
            this._rewardStageId = param1;
         }
      }
      
      public function setPrepareCardList(param1:Array) : void
      {
         var _loc5_:Dto_CardHpRatio = null;
         if(param1 == null)
         {
            return;
         }
         var _loc2_:Array = new Array();
         var _loc3_:int = int(param1.length);
         var _loc4_:int = 0;
         while(_loc4_ < _loc3_)
         {
            _loc5_ = param1[_loc4_];
            _loc2_.push(_loc5_.uid);
            _loc4_++;
         }
         GameGloble.actorModel.battleCardModel.resetCardPrepareList(_loc2_);
      }
      
      public function resetCardHpRatioList(param1:Array) : void
      {
         this._cardHpRatioList = param1;
         GameGloble.setCommEmbattleCardLeftHP(EmbattleConst.MODE_NOENDFUBEN,this._cardHpRatioList);
      }
      
      public function updateCardHpRatioList(param1:Array) : void
      {
         var _loc5_:Dto_CardHpRatio = null;
         var _loc6_:int = 0;
         var _loc7_:Dto_CardHpRatio = null;
         var _loc2_:int = int(this._cardHpRatioList.length);
         var _loc3_:int = int(param1.length);
         var _loc4_:int = 0;
         while(_loc4_ < _loc3_)
         {
            _loc5_ = param1[_loc4_];
            _loc6_ = 0;
            while(_loc6_ < _loc2_)
            {
               _loc7_ = this._cardHpRatioList[_loc6_];
               if(_loc7_.uid.compare(_loc5_.uid))
               {
                  this._cardHpRatioList[_loc6_] = _loc5_;
                  break;
               }
               _loc6_++;
            }
            _loc4_++;
         }
         GameGloble.setCommEmbattleCardLeftHP(EmbattleConst.MODE_NOENDFUBEN,this._cardHpRatioList);
      }
      
      public function isNeedSetPreCard() : Boolean
      {
         return (this._cardHpRatioList == null || this._cardHpRatioList.length == 0) && this.isOpen();
      }
      
      public function setRankDataList(param1:Array) : void
      {
         var _loc3_:EndlessFubenRankRecordVO = null;
         var _loc2_:int = int(param1.length);
         var _loc4_:int = 0;
         while(_loc4_ < EndlessFubenConst.PAGE_BOUNDS)
         {
            _loc3_ = this.m_vecAllRecord[_loc4_];
            _loc3_.reset();
            if(_loc4_ < _loc2_)
            {
               _loc3_.dto = param1[_loc4_];
            }
            _loc4_++;
         }
         CommandDispatcher.send(EndlessFubenConst.EVENT_RANK_LIST_UPDATE);
      }
      
      public function setSelfData(param1:Dto_AllServerNoEndFuben) : void
      {
         if(param1)
         {
            this.m_selfRecord.dto = param1;
         }
         else
         {
            this.m_selfRecord.reset();
            this.m_selfRecord.setData(RankGlobal.PROPERTY_INDEX,-1);
         }
         CommandDispatcher.send(EndlessFubenConst.EVENT_RANK_SELF_UPDATE);
      }
      
      public function getLeftResetTimes() : int
      {
         var _temp_1:* = ServerConfigTemplateFactory.instance.iNoEndFubenResetTimes - this.resetTimes;
         §§push(ServerConfigTemplateFactory.instance.iNoEndFubenResetTimes - this.resetTimes);
         if(_temp_1 < 0)
         {
            §§pop();
            §§push(0);
         }
         return int(§§pop());
      }
      
      public function reset(param1:int, param2:int, param3:int, param4:int, param5:Boolean, param6:Number, param7:Array, param8:Array, param9:Boolean, param10:Boolean, param11:int) : void
      {
         this.stageID = param1;
         this.setLevelInfo(param5,param2);
         this.bestLevelID = param3;
         this.setLeftTryNum(param4);
         this.stageTemplate = EndlessFubenDataFactory.getInstance().getStageTemplate(param1);
         this.endTimeSecond = param6;
         this.rewardList = param7;
         this.setPrepareCardList(param8);
         this.resetCardHpRatioList(param8);
         this.opened = param9;
         this.hasTakeReward = param10;
         this.resetTimes = param11;
      }
      
      public function setLeftTryNum(param1:int) : void
      {
         this.leftTryNum = this.totalTryNum - param1;
      }
      
      public function isOpen() : Boolean
      {
         if(!this.opened)
         {
            return false;
         }
         if(this.endTimeSecond == 0)
         {
            return false;
         }
         var _loc1_:Number = Number(DateUtils.getInstance().getInterval(this.endTimeSecond));
         if(_loc1_ <= 0)
         {
            return false;
         }
         return true;
      }
      
      public function setBestLevelID(param1:int) : void
      {
         if(param1 > this.bestLevelID)
         {
            this.bestLevelID = param1;
         }
      }
      
      public function reduceTryTimes() : void
      {
         if(this.leftTryNum > 0)
         {
            --this.leftTryNum;
         }
      }
      
      public function setLevelInfo(param1:Boolean, param2:int) : void
      {
         this.hasPassNextStage = param1;
         this._saveLevelID = param2;
      }
      
      public function getSaveLevelID() : int
      {
         return this._saveLevelID;
      }
      
      public function getCurLevelID() : int
      {
         return this.hasPassNextStage ? int(this._saveLevelID + 1) : this._saveLevelID;
      }
      
      public function getNextLevelID() : int
      {
         var _loc1_:int = this.getCurLevelID();
         return this.hasPassNextStage ? int(_loc1_ + 1) : int(_loc1_ + 1);
      }
      
      public function getAutoPassLevelID() : int
      {
         return int(Math.max(0,this.bestLevelID - 20));
      }
   }
}

