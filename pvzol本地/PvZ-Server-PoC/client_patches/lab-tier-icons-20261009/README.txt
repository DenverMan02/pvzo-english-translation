实验室五档药剂图标修复证据（2026-10-09）

此目录为最终脚本和证据副本，仅供复查，不是独立安装包。
生产配置和40张图标已经修复；勿再次执行 --apply，以免重复备份。

原始素材：D:\植物PVZ素材\tieba\实验室药品、素材
工作脚本、校准脚本和中间素材：
C:\Users\komi\Documents\Codex\2026-10-09\d-pvzol-ol-6\work\lab

repair_lab_assets.py 的 WORK 按脚本所在目录解析。
本证据目录没有复制 native_sources、icons 和 calibrate_icons.ps1。
重建时请使用上述原工作目录的脚本与校准脚本；缺失原图由同属性瓶身、原字牌、原星组件组合。

最终回归测试路径：
C:\Users\komi\Documents\Codex\2026-10-09\d-pvzol-ol-6\work\lab\test_lab_tier_icons.py
测试固定读取此备份，以确认除 icon 之外全部2404行配置字段保留：
D:\pvzol本地最新最新ol\pvzol本地\PvZ-Server-PoC\backups\lab-tier-icons-20261009-092741\clientDB.txt
测试创建并清理临时SQLite数据库，不修改真实玩家存档。

最终测试结果：work\lab\test_result.txt，5项全通过。
最新图标核对图：outputs\实验室药剂复查.png，已逐图核查。
