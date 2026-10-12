package com.qq.modules.temple.model
{
   import PVZ.Cmd.Cmd_Temple_Detect_SC;
   import PVZ.Cmd.Cmd_Temple_GetGuarderInfo_SC;
   import PVZ.Cmd.Cmd_Temple_GetTempleInfo_SC;
   import PVZ.Cmd.Cmd_Temple_SetOut_SC;
   import PVZ.Cmd.Dto_GuarderInfo;
   import PVZ.Cmd.Dto_TempleGuildInfo;
   import PVZ.Cmd.Dto_WorldMap_Pos;
   import com.qq.constant.temple.TempleConstant;
   import com.qq.modules.basic.event.CommonEvent;
   import com.qq.modules.basic.model.templates.ServerConfigTemplateFactory;
   import com.qq.modules.basic.modle.BasicProxy;
   import com.qq.modules.main.command.CommonCmd;
   import com.qq.modules.temple.TempleGlobal;
   import com.qq.modules.temple.model.vo.TempleArmyVO;
   import com.qq.modules.temple.model.vo.TempleUnionVO;
   import com.qq.utils.DateUtils;
   import com.tencent.protobuf.Int64;
   import tencent.io.service.events.SocketServiceEvent;
   
   public class TempleProxy extends BasicProxy
   {
      
      private var m_isTempleWarStart:Boolean;
      
      private var m_posX:int;
      
      private var m_posY:int;
      
      private var m_areaID:int;
      
      private var m_startDate:Date;
      
      private var m_endDate:Date;
      
      private var m_preDate:Date;
      
      private var m_vecTempleUnion:Vector.<TempleUnionVO>;
      
      private var m_currentLordUnionID:Int64;
      
      private var m_warTime:int;
      
      private var m_totalCount:int;
      
      private var m_timeInterval:int;
      
      public function TempleProxy()
      {
         super();
      }
      
      override public function init(param1:Array = null) : void
      {
         this.m_vecTempleUnion = new Vector.<TempleUnionVO>();
         this.m_startDate = this.getDateByString(ServerConfigTemplateFactory.instance.sTempleWarStartTime);
         this.m_endDate = this.getDateByString(ServerConfigTemplateFactory.instance.sTempleWarEndTime);
         this.m_preDate = this.getDateByString(ServerConfigTemplateFactory.instance.sTempleWarPreStartTime);
         this.m_warTime = (this.m_endDate.time - this.m_startDate.time) / 1000;
         this.m_timeInterval = ServerConfigTemplateFactory.instance.iTimerRewardInterval;
         this.m_totalCount = this.m_warTime / this.m_timeInterval;
         super.init(param1);
      }
      
      public function get warTime() : int
      {
         return this.m_warTime;
      }
      
      public function get totalCount() : int
      {
         return this.m_totalCount;
      }
      
      public function get timeInterval() : int
      {
         return this.m_timeInterval;
      }
      
      private function getDateByString(param1:String) : Date
      {
         var _loc2_:Array = param1.split(":");
         var _loc3_:int = int(_loc2_[0]);
         var _loc4_:int = int(_loc2_[1]);
         var _loc5_:int = int(_loc2_[2]);
         var _loc6_:Date = DateUtils.getInstance().getServerDate();
         _loc6_.setHours(_loc3_,_loc4_,_loc5_);
         return _loc6_;
      }
      
      public function get startDate() : Date
      {
         return this.m_startDate;
      }
      
      public function get endDate() : Date
      {
         return this.m_endDate;
      }
      
      public function get preDate() : Date
      {
         return this.m_preDate;
      }
      
      override public function listMsgInterests() : Array
      {
         return [[Cmd_Temple_SetOut_SC.$MessageID,this.onCmd_Temple_SetOut_SC],[Cmd_Temple_GetGuarderInfo_SC.$MessageID,this.onCmd_Temple_GetGuarderInfo_SC],[Cmd_Temple_GetTempleInfo_SC.$MessageID,this.onCmd_Temple_GetTmepleInfo_SC],[Cmd_Temple_Detect_SC.$MessageID,this.onCmd_Temple_Detect_SC]];
      }
      
      private function onCmd_Temple_GetGuarderInfo_SC(param1:SocketServiceEvent) : void
      {
         var _loc4_:Dto_GuarderInfo = null;
         var _loc2_:Cmd_Temple_GetGuarderInfo_SC = param1.socketData as Cmd_Temple_GetGuarderInfo_SC;
         var _loc3_:Vector.<TempleArmyVO> = new Vector.<TempleArmyVO>();
         if(_loc2_.guarder)
         {
            for each(_loc4_ in _loc2_.guarder)
            {
               _loc3_.push(this.parseFromDto(_loc4_));
            }
         }
         this.dispatch(new CommonEvent(TempleGlobal.EVENT_ARMY_INFO,_loc3_));
      }
      
      private function onCmd_Temple_Detect_SC(param1:SocketServiceEvent) : void
      {
         var _loc2_:Cmd_Temple_Detect_SC = param1.socketData as Cmd_Temple_Detect_SC;
         this.dispatch(new CommonEvent(TempleGlobal.EVENT_GET_UNION_INFO,_loc2_.detectInfo));
      }
      
      private function onCmd_Temple_GetTmepleInfo_SC(param1:SocketServiceEvent) : void
      {
         var _loc3_:Dto_TempleGuildInfo = null;
         var _loc2_:Cmd_Temple_GetTempleInfo_SC = param1.socketData as Cmd_Temple_GetTempleInfo_SC;
         this.m_isTempleWarStart = _loc2_.isTempleWarStart;
         this.updatePos(_loc2_.pos,_loc2_.area);
         this.m_vecTempleUnion.length = 0;
         for each(_loc3_ in _loc2_.guildInfo)
         {
            this.m_vecTempleUnion.push(new TempleUnionVO(_loc3_));
         }
         this.m_vecTempleUnion.sort(TempleUnionVO.sortByCaptureTime);
         this.m_currentLordUnionID = _loc2_.currLordGuildId;
         this.dispatch(new CommonEvent(TempleGlobal.EVENT_TEMPLE_INFO));
      }
      
      public function getUnionData(param1:Int64) : TempleUnionVO
      {
         var _loc2_:TempleUnionVO = null;
         for each(_loc2_ in this.m_vecTempleUnion)
         {
            if(_loc2_.unionID.compare(param1))
            {
               return _loc2_;
            }
         }
         return null;
      }
      
      private function updatePos(param1:Dto_WorldMap_Pos, param2:int) : void
      {
         this.m_posX = param1.posX;
         this.m_posY = param1.posY;
         this.m_areaID = param2;
      }
      
      public function get isTempleWarStart() : Boolean
      {
         return this.m_isTempleWarStart;
      }
      
      public function get posX() : int
      {
         return this.m_posX;
      }
      
      public function get posY() : int
      {
         return this.m_posY;
      }
      
      public function get areaID() : int
      {
         return this.m_areaID;
      }
      
      public function get currentLordUnionID() : Int64
      {
         return this.m_currentLordUnionID;
      }
      
      public function get allUnion() : Vector.<TempleUnionVO>
      {
         return this.m_vecTempleUnion;
      }
      
      private function onCmd_Temple_SetOut_SC(param1:SocketServiceEvent) : void
      {
         var _loc2_:Cmd_Temple_SetOut_SC = param1.socketData as Cmd_Temple_SetOut_SC;
         CommonCmd.txtFly(TempleConstant.TEMPLE_SETOUT_SUCCESS);
         this.dispatch(new CommonEvent(TempleGlobal.EVENT_TEMPLE_SETOUT_SUCCESS));
      }
      
      private function parseFromDto(param1:Dto_GuarderInfo) : TempleArmyVO
      {
         var _loc2_:TempleArmyVO = new TempleArmyVO();
         _loc2_.roleID = param1.roleId;
         _loc2_.roleName = param1.name;
         _loc2_.reachTime = param1.guardTime;
         _loc2_.power = param1.power;
         _loc2_.teamID = param1.teamId;
         return _loc2_;
      }
   }
}

