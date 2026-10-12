无尽炼狱 VerifyError #1024 修复记录（2026-10-09）

用户现象：进入无尽炼狱，加载20%时弹出“堆栈下溢”，调用栈指向EndlessFubenView构造。

根因
上次使用FFDec整类编译客户端重置功能时，没有完整带入外部基类依赖，生成了错误的类声明脚本。
EndlessFubenViewMediator的声明漏掉Object、MediatorBase、Mediator、BasicScreenMediator继承链的作用域初始化。
错误脚本先newclass消耗唯一的接收对象，后续initproperty需要接收对象和类两项，实际只有一项，因此Flash校验报#1024。
EndlessFubenView自身构造器原本无变化；它引用损坏的Mediator，所以错误栈显示这个入口。
上次源码导出和功能检查未覆盖这一类初始化校验，未发现该编译错误。

本次修复
以改动前的原始ModuleEndlessFuben.swf为基础，恢复完整类声明、继承链、构造器和作用域元数据。
直接替换onClickBtnRefresh、getLeftResetTimes两个方法体，更新重置提示字符串。
其他381个方法体与作用域头逐字节保持原值，原常量索引、类布局和方法编号均保留。
免费重置剩余数不小于0；免费次数耗尽时仍可购买；每日3次免费和额外20钻/次的服务端配置保持生效。
已更新实际webroot中的ModuleEndlessFuben.swf及对应加载别名，共2个文件。

验证
39项相关检查通过：5项新增字节码回归、3项客户端检查、31项无尽服务端/傀儡回归。
新增回归明确复现旧声明脚本的堆栈下溢，并确认恢复后的声明脚本有效。
检查两个新方法所有控制流分支的堆栈、作用域上限和跳转边界。
执行实际方法字节码验证27组免费/付费/次数耗尽场景。
核对实际游戏SWF及加载别名与已验证的修复文件完全一致。
测试使用临时数据库，没有修改真实玩家存档。
尚未在用户的实际Flash窗口中重新点击进入无尽炼狱。

生效方式
完全退出Flash播放器，再重新打开游戏。仅点击报错框“继续”不会卸载已缓存的错误类。
本次只更新客户端模块，已启动的服务端无需因本次修复重启。

备份
D:\pvzol本地最新最新ol\pvzol本地\PvZ-Server-PoC\backups\endless-verifyerror-20261009-110111

留存补丁、原出错文件、原模块对照、回归脚本和部署清单
D:\pvzol本地最新最新ol\pvzol本地\PvZ-Server-PoC\client_patches\endless-verifyerror-20261009

修复后的SWF SHA256
f90140d8b9b1f6f4bfdfc3262c217904a555c9948f8ff824b047ccd093f89580
