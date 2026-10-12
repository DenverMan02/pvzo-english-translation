package com.qq.modules.card.view.bean
{
   import PVZ.Cmd.E_EquipQuality;
   import asgui.resources.AssetManager;
   import com.qq.display.QCanvas;
   import com.qq.modules.bag.model.BagItemTemplate;
   import com.qq.modules.bag.view.components.BagItemIcon;
   import flash.display.MovieClip;
   import flash.text.TextField;
   
   public class CardBeanItem extends QCanvas
   {
      
      private var icon:BagItemIcon;
      
      private var txt:TextField;
      
      private var flashMc:MovieClip;
      
      public function CardBeanItem(param1:MovieClip)
      {
         this.txt = param1["txt"] as TextField;
         this.txt.mouseEnabled = false;
         var _loc2_:MovieClip = param1["frame"] as MovieClip;
         _loc2_.mouseEnabled = _loc2_.mouseChildren = false;
         super();
      }
      
      override public function InitializeComponent() : void
      {
         super.InitializeComponent();
         this.width = 62;
         this.height = 70;
         this.icon = new BagItemIcon();
         this.addChild(this.icon);
         this.flashMc = AssetManager.GetNewClass("ModuleCard_Bean_Flash") as MovieClip;
         this.flashMc.x = this.width / 2;
         this.flashMc.y = 56;
         this.flashMc.visible = false;
         // The overlay is visual only; disable hit testing on it and its children.
         this.flashMc.mouseEnabled = false;
         this.flashMc.mouseChildren = false;
         this.addChild(this.flashMc);
      }
      
      public function setData(param1:BagItemTemplate, param2:int) : void
      {
         this.toolTip = param1.name + "\n增加经验：" + param1.getUseEffect(1);
         this.txt.text = param2.toString();
         this.icon.setTemplate(param1);
         this.icon.attrQuality = E_EquipQuality.EquipQuality_White;
      }
      
      public function showFlash() : void
      {
         this.flashMc.visible = true;
         this.flashMc.gotoAndPlay(1);
      }
   }
}

