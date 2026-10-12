经验豆连续使用修复

故障复盘：上一版通过 FFDec 重编译 ModuleCard.swf。虽然源码表面正常，重编译改变了 CardBeanItem 的 ABC 类作用域信息，游戏在创建经验豆升级面板时报告 VerifyError #1024（堆栈下溢）。已保留上一版在 PvZ-Server-PoC/backups/experience-bean-repeat-20260926/，但不应继续部署或重新导入那版源码。

修复：patch_module_card_clickthrough.py 直接修改原始 ModuleCard.swf 的 AVM2 方法体，不经过 ActionScript 重编译。把 CardBeanItem.InitializeComponent 中的 `flashMc.stop()` 调用换成 `flashMc.mouseEnabled = false` 和 `flashMc.mouseChildren = false`。动画本身和子元件都不再截获鼠标，点击事件会落回经验豆控件；showFlash() 仍会在播放前跳回第 1 帧。补丁同步更新方法长度、DoABC 长度和 SWF 长度字段，保持 maxstack 和类作用域不变。

构建：

```powershell
python patch_module_card_clickthrough.py <原始ModuleCard.swf> <输出ModuleCard.swf>
```

验证：FFDec 可重新导出全部 169 个脚本；P-code 对比显示只有 CardBeanItem.InitializeComponent 改动；新指令段先复制 MovieClip 引用，再设置两个鼠标属性，净堆栈效果与原 stop 调用一致，并且 maxstack 与类作用域表保持不变。实际 Flash 客户端回归仍需用户启动客户端验证。

部署文件：webroot/ModuleCard.swf，以及对应文件版本别名。最终产物大小为 117022 字节，MD5 为 `27687cfe6dac3cda5156ebdaf76684cb`；webroot/fileVer.xml 的 ModuleCard 记录与之匹配。请完整重启 Flash 客户端后验证连续点豆。
