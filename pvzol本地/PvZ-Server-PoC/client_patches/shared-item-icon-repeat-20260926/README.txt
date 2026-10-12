物品图片在购买、领取或兑换一次后消失的共用组件修复

已核对复现路径：
- 潘妮小店：ActPennyShopListCellMediator.refresh 在背包更新后重新设置
  三个 BagItemIcon.itemData。
- 宝罐迷阵：ActHitJarSelectRewardViewMediator.reset 等选择刷新方法重设
  BagItemIcon 的物品模板。
- 集字活动珍珠兑换：CollectWordExchangeItem.item 在刷新页面时重设
  BagItemIcon 的物品模板。
商店和在线奖励界面也使用同一 BagItemIcon.setTemplate 路径。

根因：当前发布的 Game_p 中，BagItemIcon.resetIcon 比原版多了
_itemIcon.width = NaN 和 _itemIcon.height = NaN。setTemplate 会先调用
resetIcon，而 asgui.controls.SWFLoader.source 对相同图片 URL 不重新加载。
首次绘制时资源加载能重新赋予尺寸；在同一图片第二次刷新时，NaN
尺寸留在已有图标上，造成图片消失。图片自身又承担某些格子的鼠标
命中，因而后续无法点击。

本补丁只把 resetIcon 中这两条宽高写入替换为 AVM2 NOP，保留
setEquipTemplate 的 50×50 装备图标设置，以及项目中其他修复。
从此前发布的 Game_p_e90cc556a5e9f63b73ffa23b6fe21c34.swf 构建。

验证：解压后 SWF 长度不变，只有连续 24 字节范围内的 20 字节变化；
FFDec 成功导出全部 3891 个脚本，BagItemIcon 的源码差异恰好是
resetIcon 中这两句消失。Game_n 中原本没有这两句，因此无需修改。
实际游戏内连续点击仍需完整重启 Flash 客户端后验证。

输出 MD5: 04d85414d9f7ef6add82a525a0475506
输出大小: 2401902 字节
部署: webroot/Game_p.swf、对应 MD5 后缀文件和 webroot/fileVer.xml
回滚: PvZ-Server-PoC/backups/shared-item-icon-repeat-20260926/
