已确认的错误是塔防原生鼠标输入没有消除窗口缩放和居中偏移。截图显示预览与正式植物不在预期位置，但截图本身不能证明植物资源锚点错误。此次候选修复坐标转换，不修改资源、植物落点规则或地图格子。

当前生产 ModuleTD_p.swf 作为基线，SHA-256 为 a13f2bbc3751a9beb6b9de87691ae51f2eb0da62a41ffbf19fe4f5c7fa9de3c8，已包含上轮 TDBGViewMediator.checkSize 视口修复。基线保存为 ModuleTD_p.current.swf；最终候选 ModuleTD_p.pointer.swf 的 SHA-256 为 8b80432e9997467fea10ffeb12827fb9a653c335919363ac5118f5098fd8ed8c。仅修改 13 个 AVM2 方法体，7988 个其他方法体、所有常量池、类继承和 traits 保持。10 方法阶段候选另存为 ModuleTD_p.pointer-10.swf/json 及 install_manifest-10.json，以便追溯。

源码证据：

- MainContext.onWindowResize 在原生 SystemManager 上设置 windowScale 和窗口居中 x/y，同时设置对应物理 Starling viewport。
- TDCropPlantAction.onMouseMove 读取原生 stage.mouseX/Y，onMouseClick 读取原生 MouseEvent.stageX/Y；原逻辑直接把这些点交给 effectLayer.globalToLocal。
- MixLayer.globalToLocal 实际委托 Starling display。该内部层转换不会消除原生 SystemManager 的窗口变换，而且其他调用会传入内部 Starling 坐标，因此不能在公共 MixLayer 方法中无条件加入窗口反变换。
- getRoadTileByScreenPos 继续处理塔防内部 scaleCurrent 和 xForScene。直接用 Starling viewport/stage 比例转换原生点，然后再次走原方法，会重复处理内部缩放。
- 正确入口流程是原生 stage 点 → SystemManager.instance.globalToLocal → 原来的 effectLayer.globalToLocal → 原来的 getRoadTileByScreenPos。种植点击转换放在 cardSlotRect 判断之前，使卡槽判断也采用原来的应用坐标。
- 跟鼠标 cropPlant 使用原生 Flash frontLayer；吸附格子的 tipPlant 和正式植物使用 Starling。两个分支在窗口放大后更容易暴露原始鼠标点与应用点混用。
- tipPlant 与正式 TDGameObjectCommand.addPlant 都调用 getPlantPos(tile.index, plantID)。effectLayer 与 unitLayer 的 Starling display 同属于 TDScene；不存在另一个独立窗口偏移。动态图格绘制也使用 TDMapTileData.rect。没有源码证据需要重写资源锚点。

最终修改的方法：

- TDCropPlantAction.onMouseMove、onMouseClick。
- TDUseShovelAction.onMouseMove、onMouseClick。
- TDReleaseAreaSkillAction.onMouseMove、onMouseClick；移动方法 else 分支重新读取原生鼠标点，也执行归一化；正常格子分支跳过该额外归一化。
- TDLayEnergyTileAction.onMouseMove、onMouseClick。
- TDSpecialSkillTrigger.updateState。
- AI_plant_OxygenAlgae.onMouseMove。
- TDMiniGameJewelsMouseCtr.getJewel。
- TDSkillSummonZombieAction.onMouseMove、ZombieTerracottaCottaAction.onMouseMove。锤子位于原生 topEffectLayer.dragSp，仅把原生 stage 点转为 SystemManager 本地点后设置 cursor，不经过 Starling 转换。

两个锤子方法新增 Point 局部变量，将 max_stack 从 2 调整为 4，local_count 从 2 调整为 3，scope 保持。其他 11 个方法的 stack/local/scope 头保持原值。均复用既有常量，不重新编译整类。TDSampleMower 工具提示直接添加到原生 stage，raw stage 点正确，因此保持该路径。原生 GameObject 分支使用原生 frontLayer.parent.globalToLocal，也保持。

最终审查：FFDec 独立反编译 9 个相关类均成功，13 个方法及区域技能特殊 else 分支符合预期。test_pointer_structure.py 的 3 个测试通过：26 次旧/新方法控制流与栈/scope 校验通过；未修改完整方法体及类结构逐字节保持；现有 checkSize 修复在 5 种窗口尺寸 × debug 开关的 10 种场景中保持物理 viewport。真实 AVM2 方法字节码的合成原生接口行为测试由 verification 代理保存于 work/validation；这不是实际 Flash 图形窗口操作，最终报告应区分二者。

另一个独立旧问题已记录但不加入本次修复：TDCropPlantAction.onMouseMove 在 getRoadTileByScreenPos 返回 null 时没有隐藏旧 tipPlant/tipCropPlant_LevelUp。鼠标离开棋盘后可能仍显示上一格预览。该行为与已确认的外层缩放输入混用不是同一修改点；按本轮范围决定保持最终 13 方法候选稳定。
