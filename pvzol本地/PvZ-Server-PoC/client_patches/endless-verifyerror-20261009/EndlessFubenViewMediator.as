package com.qq.modules.endlessFuben.view
{
   import PVZ.Cmd.Dto_IdNum;
   import asgui.managers.PopUpManager;
   import com.qq.modules.bag.model.ItemTemplateFactory;
   import com.qq.modules.basic.model.templates.ServerConfigTemplateFactory;
   import com.qq.modules.basic.model.templates.ce.EntryTemplateCE;
   import com.qq.modules.basic.view.BasicScreenMediator;
   import com.qq.modules.battle.constant.BattleConst;
   import com.qq.modules.endlessFuben.EndlessFubenConst;
   import com.qq.modules.endlessFuben.command.EndlessFubenProxyCmd;
   import com.qq.modules.endlessFuben.command.EndlessFubenViewCmd;
   import com.qq.modules.endlessFuben.model.EndlessFubenProxy;
   import com.qq.modules.main.command.CommonCmd;
   import com.qq.modules.main.command.GameFlowCmd;
   import com.qq.utils.UtilsManager;
   import flash.events.Event;
   import flash.events.MouseEvent;
   import tencent.base.utils.StringUtil;
   
   public class EndlessFubenViewMediator extends BasicScreenMediator
   {
      
      [Inject]
      public var proxy:EndlessFubenProxy;
      
      [Inject]
      public var view:EndlessFubenView;
      
      public function EndlessFubenViewMediator()
      {
         super();
      }
      
      override protected function initListener() : void
      {
         super.initListener();
         addContextListener(EndlessFubenConst.Event_Request_Info_Success,this.onRequestInfoSuccess);
         addContextListener(EndlessFubenConst.Event_Request_Refresh_Reward,this.onRequestInfoSuccess);
         addComponentListener(this.view.btnExit,MouseEvent.CLICK,this.onClickBtnExit);
         addComponentListener(this.view.btnRank,MouseEvent.CLICK,this.onClickBtnRank);
         addComponentListener(this.view.btnReward,MouseEvent.CLICK,this.onClickBtnReward);
         addComponentListener(this.view.btnBattleReport,MouseEvent.CLICK,this.onClickBtnReport);
         addComponentListener(this.view.btnRefresh,MouseEvent.CLICK,this.onClickBtnRefresh);
         addContextListener(BattleConst.BATTLE_END,this.onBattleEnd);
         addContextListener(EndlessFubenConst.EVENT_Request_Reset_Success,this.onRequestResetSuccess);
      }
      
      override protected function initialized() : void
      {
         var _loc3_:int = 0;
         super.initialized();
         var _loc1_:EntryTemplateCE = ServerConfigTemplateFactory.instance.getEntryTemplateEndlessFuben();
         var _loc2_:Array = ServerConfigTemplateFactory.instance.sNoEndFubenOpenWeek;
         if(_loc2_ != null && _loc2_.length == 2)
         {
            _loc3_ = int(ServerConfigTemplateFactory.instance.iNoEndFubenEndTime / 3600);
            this.view.btnIntr.toolTip = StringUtil.substitute(EndlessFubenConst.TxT_Rule,_loc1_.needLevel,UtilsManager.num2Chinese(_loc2_[0],true),UtilsManager.num2Chinese(_loc2_[1],true),_loc3_);
         }
         EndlessFubenProxyCmd.requestEndlessFubenInfo();
      }
      
      private function onBattleEnd(param1:Event) : void
      {
         switch(this.proxy.model.lastResult)
         {
            case EndlessFubenConst.Last_Result_Win:
               CommonCmd.txtFly("挑战第" + this.proxy.model.getCurLevelID() + "关成功！");
               break;
            case EndlessFubenConst.Last_Result_Fail:
               CommonCmd.txtFly("挑战失败！当前成绩为第" + this.proxy.model.getCurLevelID() + "关");
         }
         this.proxy.model.lastResult = EndlessFubenConst.Last_Result_None;
      }
      
      private function onClickBtnRefresh(param1:Event) : void
      {
         if(this.proxy.model.getLeftResetTimes() <= 0)
         {
            var _loc2_:int = int(ServerConfigTemplateFactory.instance.iNoEndFubenResetGoldTicket);
         }
         else
         {
            _loc2_ = 0;
         }
         CommonCmd.confirmUseDiamond(_loc2_ <= 0 ? "免费重置进度" : "购买重置进度",_loc2_,EndlessFubenProxyCmd.requestReset,null,0,true);
      }
      
      private function onClickBtnRank(param1:Event) : void
      {
         EndlessFubenViewCmd.openRankWindow(this.proxy.model.stageID);
      }
      
      private function onClickBtnReward(param1:Event) : void
      {
         if(!this.proxy.model.hasTakeReward && this.proxy.model.rewardList.length > 0)
         {
            EndlessFubenProxyCmd.requestTakeReward();
         }
         else
         {
            CommonCmd.txtFly("暂无奖励！",NaN,NaN,0,null,true);
         }
      }
      
      private function onClickBtnReport(param1:Event) : void
      {
         if(this.proxy.model.reportId != null)
         {
            if(this.proxy.model.isOpen())
            {
               GameFlowCmd.queryReport(this.proxy.model.reportId);
            }
            else
            {
               CommonCmd.txtFly("战场关闭中！",NaN,NaN,0,null,true);
            }
         }
         else
         {
            CommonCmd.txtFly("暂无新的战报！",NaN,NaN,0,null,true);
         }
      }
      
      private function onRequestResetSuccess(param1:Event) : void
      {
         this.refreshRefreshTimes();
      }
      
      private function onRequestInfoSuccess(param1:Event) : void
      {
         this.refreshRefreshTimes();
         this.refreshReward();
      }
      
      private function refreshRefreshTimes() : void
      {
         var _loc1_:String = "#FF0000";
         var _loc2_:int = this.proxy.model.getLeftResetTimes();
         if(_loc2_ > 0)
         {
            _loc1_ = "#00FF00";
         }
         this.view.btnRefresh.toolTip = StringUtil.substitute(EndlessFubenConst.Txt_RefreshLeftTimes,_loc2_,ServerConfigTemplateFactory.instance.iNoEndFubenResetTimes,ServerConfigTemplateFactory.instance.iNoEndFubenResetGoldTicket,_loc1_);
      }
      
      private function refreshReward() : void
      {
         var _loc4_:Dto_IdNum = null;
         var _loc1_:int = int(this.proxy.model.rewardList.length);
         var _loc2_:String = "";
         var _loc3_:int = 0;
         while(_loc3_ < _loc1_)
         {
            _loc4_ = this.proxy.model.rewardList[_loc3_];
            _loc2_ = _loc2_.concat(ItemTemplateFactory.instance.getBagItemName(_loc4_.id),"x",_loc4_.num,"<br>");
            _loc3_++;
         }
         if(_loc2_.length == 0)
         {
            _loc2_ = "暂无奖励";
         }
         else if(!this.proxy.model.hasTakeReward)
         {
            _loc2_ = _loc2_.concat("<font color=\'#00FF00\'>(未领取)</font>");
         }
         else
         {
            _loc2_ = _loc2_.concat("<font color=\'#FF0000\'>(已领取)</font>");
         }
         this.view.btnReward.toolTip = _loc2_;
      }
      
      private function onClickBtnExit(param1:Event) : void
      {
         PopUpManager.RemovePopUp(this.view);
         this.view.Dispose();
         GameFlowCmd.gotoCity();
      }
   }
}

