from pathlib import Path
import json

HERE = Path(__file__).resolve().parent
OUT = HERE.parent/'outputs'

def read(relative):
    return json.loads((HERE/relative).read_text('utf-8'))

def main():
    installed = read('pointer_dpi_installed.json')
    structure = read('validation/pointer_structure_summary.json')
    helper = read('dpi-tests/dpi_helper_summary.json')
    geometry = read('dpi-tests/ui_geometry_summary.json')
    native = read('dpi-tests/candidate-frozen-ui/summary.json')
    fractional = read('dpi-tests/candidate-fractional115/summary.json')
    deployed = read('dpi-tests/installed-frozen-ui/summary.json')
    behavior = read('validation/pointer_behavior_summary.json')
    controller = read('dpi-fix/controller_test_summary.json')
    assert structure['passed'] and helper['passed'] and geometry['passed']
    assert native['ok'] and fractional['ok'] and deployed['ok']
    assert behavior['passed'] and controller['passed']
    result = dict(installed=installed,pointer_structure=structure,dpi_api_tests=helper,
        dpi_geometry_tests=geometry,native_candidate=native,
        fractional_120_percent_simulation=fractional,native_installed=deployed,
        pointer_behavior=behavior,controller_diagnostics=controller,
        limitations=['120% 原反馈机器的无法启动故障未在实际系统缩放下复现；115/96仅为隔离分数布局模拟。',
                     '种植行为检查执行实际AVM2方法字节码并使用合成原生接口；未进行真实Flash画面/GPU人工验收。'])
    for relative,key in [('validation/pointer_behavior_summary.json','pointer_behavior'),
                         ('dpi-fix/controller_test_summary.json','controller_diagnostics')]:
        file = HERE/relative
        if file.exists():
            value = read(relative)
            assert value['passed']
            result[key] = value
    OUT.mkdir(exist_ok=True)
    (OUT/'种植与显示缩放验证结果.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    text = f'''已安装到 D:\\pvzol本地最新最新ol。

种植坐标：已确认原生鼠标点未去除外层窗口缩放和居中偏移。补丁在原生输入入口先转换到应用坐标，再沿用原有塔防内部转换和格子查询。覆盖种植预览/点击、铲子、区域技能、能量格、特定植物技能、宝石小游戏以及两个锤子光标，共13个方法。保留植物贴图锚点、地图格子和原种植位置规则；截图本身不足以证明资源贴图需要重做。

启动器缩放：正式PvZLauncherUI.exe已重新打包，包含每显示器DPI声明；窗口创建和CLR导入前初始化DPI，老系统自动回退，系统已设定DPI时正常继续。保留按工作区限制窗口尺寸的逻辑，显示器DPI变化后约束超大窗口。支持系统提供的自定义DPI比例，没有固定100%或120%的白名单。补充启动器错误详情和子进程失败记录，并过滤会话token。

检查结果：
- 13方法前后共26次方法控制流/栈检查通过，7988个其他方法体及常量/类结构保持。
- 上一轮塔防视口修复保留，实际方法字节码10组窗口/调试组合通过。
- 825个选格回调与825个原始窗口参考回调一致，另有50个锤子光标及10个区域技能分支检查通过；13个旧入口的错位可复现。
- DPI API回退与错误诊断测试通过，52组初始尺寸/工作区模拟通过，18组DPI变化约束模拟通过。
- 新正式exe和安装后的exe在当前Windows150%缩放下实际打开、居中显示、托盘及退出清理通过。
- 120%附近的115/96分数布局在真实.NET/Edge隔离窗口中模拟通过；没有修改Windows系统缩放，也没有登录账号。

检查边界：120%原机器的无法启动现象未在实际系统120%下复现，需在反馈电脑重新尝试；不能将模拟检查当成全部显示器实测。塔防坐标检查执行实际方法字节码配合合成接口，尚未完成真实Flash画面/GPU验收。

安装文件数：{installed['count']}。所有写入文件已校验SHA-256，SWF规范文件、哈希别名与fileVer.xml同步。
备份：{installed['backup']}
补丁及脚本记录：{installed['patch']}

使用：完全退出启动器和Flash游戏后，重新打开一键启动游戏.exe。可在120%显示缩放下重新尝试。若仍无法打开，新错误记录位于游戏内launcher_cache/ui-shell.log及portable_launcher.log。
恢复：退出游戏和启动器后，将备份内pvzol本地目录的文件复制回游戏同名目录。
账号、存档、数据库和Windows显示设置未修改。
'''
    (OUT/'种植与显示缩放修复记录.txt').write_text(text,encoding='utf-8')
    print(json.dumps(dict(report=str(OUT/'种植与显示缩放修复记录.txt'),results=str(OUT/'种植与显示缩放验证结果.json')),ensure_ascii=False))

if __name__ == '__main__': main()
