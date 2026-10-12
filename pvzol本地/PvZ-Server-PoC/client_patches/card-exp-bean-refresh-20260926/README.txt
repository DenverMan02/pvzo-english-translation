经验豆连续点击修复（单颗豆子升级，非“一键升级”）

客户端调用路径：CardAddExpSelectCtMediator.tryCardLevelup ->
Cmd_Card_LevelUp_CS -> Cmd_Card_SyncFullInfo_SC -> CARD_PROP_UPDATED ->
CardAddExpSelectCtMediator.update。原版 update 每次都对三张豆子调用
CardBeanItem.setData；此前补丁只处理 flashMc 的鼠标命中，未阻止三张
CardIcon 在属性更新时重复设置相同资源。本补丁在 CardBeanItem.setData 中
仅当物品 ID 变化时才调用 icon.setTemplate，数量和提示仍会逐次刷新。

后续排查确认了共用底层原因：此前 Game_p 的 BagItemIcon.resetIcon
额外写入 width/height = NaN，而相同 URL 不会再次加载并恢复尺寸。
shared-item-icon-repeat-20260926 已在 Game_p 共用组件中撤掉这两句，
同时修复其他购买、领取、兑换界面。此处 CardBeanItem 的跳过重复模板
仍保留，作为经验豆面板的局部保护。

服务端 handle_card_levelup 现在先送 Cmd_Item_Update_SC，再送卡牌属性
更新。面板收到 CARD_PROP_UPDATED 时，背包里已是扣掉一颗豆子的数量。

从原始 ModuleCard.swf.original 构建；不使用 FFDec 的脚本重编译。
CardBeanItem.InitializeComponent 同时保留原 flashMc.stop() 并禁用
动画鼠标命中，避免旧补丁删掉 stop 的副作用。FFDec 可以导出全部
169 个脚本；和原始版相比仅 CardBeanItem 的 P-code 不同。

构建命令：
python patch_module_card_refresh.py <ModuleCard.swf.original> <输出.swf>

验证：python PvZ-Server-PoC/tests/test_exp_bean_repeat.py 通过，模拟连续
使用两颗豆子，检查两次扣豆、经验和响应顺序。实际 Flash 画面与点击
仍需在完整重启客户端后验证。

部署 MD5: 024f83990713bde2d3b9e40ae9d0cf0c
部署大小: 117037 字节
部署路径: webroot/ModuleCard.swf、同 MD5 后缀文件、webroot/fileVer.xml。
回滚备份: PvZ-Server-PoC/backups/experience-bean-icon-refresh-20260926/。
