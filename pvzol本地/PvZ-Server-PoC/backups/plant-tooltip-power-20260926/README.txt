植物悬浮战力来源调查和修复记录（2026-09-26）

修改内容：PvZ-Server-PoC/pvz_poc_server.py 增加分项 Dto_CardPower 构造，并接入卡包、卡片同步、普通/冒险阵型和战斗报告。分项来自服务端已有卡牌属性计算（基础/强化、拼图、果实、基因、装备、洗炼、威望进化、装甲、装扮，以及已部署的实验室/营养室/VIP/世界树）；不支持或无实际值的来源不伪造。ele=14 保留 CardItemVO 既有洗炼战力/先攻口径；若该原始值高于本地总战力可容纳的剩余值，改用已计算的战斗属性增量避免负数基础战力。Dto_FightCardInfo 的盾字段支持显式序列化，但未造动态盾值。

客户端证据：PlantCardToolTip.updatePower 按 Dto_CardPower.ele 和 PROP_SHOW_ORDER 渲染分项。updateWithFightCard 只在 shield > 0 时显示盾条；CardFullInfo 的普通卡片悬停 update() 明确将盾条高度设为 0 并隐藏，因此当前服务端不能单靠 DTO 令卡包悬停显示盾量。

备份和回滚：pvz_poc_server.py.before 是本轮修改前的服务端源文件。恢复时用它覆盖 PvZ-Server-PoC/pvz_poc_server.py；用 test_battle_preview.py.before-tooltip 恢复本轮适配过的既有测试；删除新增的 tests/test_plant_tooltip_power.py。未修改 SWF/贴图资源。

验证：python -m py_compile；tests/test_plant_tooltip_power.py；tests/test_battle_preview.py；tests/test_baptize.py；tests/test_card_avatar.py 均通过。Flash 客户端未改，因此没有 SWF 编译/游戏内视觉验证。
