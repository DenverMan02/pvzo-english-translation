from pathlib import Path
import sys,re,shutil
sys.path.insert(0,str(Path('work/icons').resolve()))
from abcdis2 import load_swf
base=Path('work/rank-loading')
root=Path(r'D:\pvzol本地最新最新ol\pvzol本地')
source=root/'webroot/ModuleRankArena.swf'
shutil.copy2(source,base/'ModuleRankArena.original.swf')
shutil.copy2(root/'PvZ-Server-PoC/pvz_poc_server.py',base/'server.original.py')
abc=load_swf(str(source))[0]
cls=next(c for c in abc.classes if c['name'].endswith('RankArenaWdMediator'))
s=(base/'pcode/scripts/com/qq/modules/rankArena/view/components/RankArenaWdMediator.pcode').read_text(encoding='utf8')
q=lambda n:'QName(PackageNamespace(""),"'+n+'")'
def prop(n): return 'getproperty '+q(n)+'\n'
view='getlocal0\n'+prop('view')
def buttons(enabled):
    flag='pushtrue\n' if enabled else 'pushfalse\n'
    code=view+prop('btnChange')+flag+'setproperty '+q('enabled')+'\n'
    for i in range(3):
        code+=view+prop('opponentArr')+'pushbyte '+str(i)+'\ngetproperty MultinameL([PackageNamespace("")])\n'+prop('btnChallenge')+flag+'setproperty '+q('enabled')+'\n'
    return code
gate=view+prop('btnChange')+prop('enabled')+'iftrue npc_ready\nreturnvoid\nnpc_ready:\n'
args=[]
for name in ['init','onClick','clickTopRole','btnChanllenge_clickHandler','onChangeOpponent']:
    a=s.index('function '+name+'(');start=s.index('\n',s.index('dispid ',a))+1
    end=s.index('end ; method',start)+len('end ; method');part=s[start:end]
    part=re.sub(r'maxstack (\d+)',lambda m:'maxstack '+str(max(4,int(m[1]))),part)
    if name=='init':
        needle='getlex QName(PackageNamespace("com.qq.modules.rankArena.command"),"RankArenaProxyCmd")'
        part=part.replace(needle,buttons(False)+needle,1)
    elif name=='onChangeOpponent':
        part=part.replace('returnvoid',buttons(True)+'returnvoid')
    else:
        part=part.replace('pushscope','pushscope\n'+gate,1)
        if name=='onClick':
            needle='callpropvoid '+q('changeOpponent')+', 0'
            # Receiver is already on stack; inserted balanced code preserves it.
            part=part.replace(needle,buttons(False)+needle,1)
    mid=next(m for n,m in abc.trait_methods(cls,name) if n.endswith(name))
    index=next(i for i,b in enumerate(abc.bodies) if b['method']==mid)
    p=base/(name+'.pcode');p.write_text(part,encoding='utf8')
    args.extend(['com.qq.modules.rankArena.view.components.RankArenaWdMediator',str(p.resolve()),str(index)])
    print(name,index)
(base/'replace_args.txt').write_text('\n'.join(args),encoding='utf8')
p=Path('work/npc/server/pvz_poc_server.py');src=(base/'server.original.py').read_text(encoding='utf8')
src=src.replace('    bots_local.regenerate(uins)\n    roster = bots_local.bots()', '    bots_local.regenerate(uins)\n    _capture_arena_npcs(player, mode, uins)\n\n\ndef _capture_arena_npcs(player, mode, uins=None):\n    """Capture the displayed roster without replacing accounts on status reads."""\n    roster = bots_local.bots()',1)
src=src.replace('    _refresh_arena_npcs(player, "rank")\n    data = _arena_state', '    _capture_arena_npcs(player, "rank")\n    data = _arena_state',1)
src=src.replace('def handle_allserver_rankarena_info(cmd_data, player):\n    _refresh_arena_npcs', 'def handle_allserver_rankarena_info(cmd_data, player):\n    _capture_arena_npcs',1)
p.write_text(src,encoding='utf8')
