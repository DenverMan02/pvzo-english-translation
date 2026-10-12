from pathlib import Path
import hashlib,shutil,datetime,sys
base=Path(__file__).resolve().parent
sys.path.insert(0,str(base.parent/'icons'))
from abcdis2 import load_swf
original=load_swf(str(base/'ModuleRankArena.original.swf'))[0]
patched=load_swf(str(base/'ModuleRankArena.swf'))[0]
assert len(original.bodies)==len(patched.bodies)
changed=[i for i,(a,b) in enumerate(zip(original.bodies,patched.bodies)) if a['code']!=b['code']]
assert changed==[185,187,188,189,197],changed
as3=(base/'verify-final/scripts/com/qq/modules/rankArena/view/components/RankArenaWdMediator.as').read_text(encoding='utf8')
assert as3.count('if(!this.view.btnChange.enabled)')==3
for i in range(3):
    assert as3.count(f'this.view.opponentArr[{i}].btnChallenge.enabled = false;')==2
    assert as3.count(f'this.view.opponentArr[{i}].btnChallenge.enabled = true;')==1
root=Path(r'D:\pvzol本地最新最新ol\pvzol本地')
server=root/'PvZ-Server-PoC'
assert (server/'pvz_poc_server.py').read_bytes()==(base/'server.original.py').read_bytes(),'Server changed during repair'
targets=[root/'webroot/ModuleRankArena.swf',root/'webroot/ModuleRankArena_8f55d5063a8ad07783ed31d34a5a40eb.swf']
for p in targets:
    assert p.read_bytes()==(base/'ModuleRankArena.original.swf').read_bytes(),'Client changed during repair'
stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
backup=server/'backups'/('rankarena-loading-'+stamp)
backup.mkdir(parents=True)
pairs=[(base.parent/'npc/server/pvz_poc_server.py',server/'pvz_poc_server.py'),
       (base.parent/'npc/server/tests/test_npc_generation.py',server/'tests/test_npc_generation.py')]
pairs += [(base/'ModuleRankArena.swf',p) for p in targets]
for source,target in pairs:
    shutil.copy2(target,backup/target.name)
    shutil.copy2(source,target)
    assert source.read_bytes()==target.read_bytes()
    print(target,hashlib.sha256(target.read_bytes()).hexdigest())
for name in ['prepare.py','deploy.py']:
    shutil.copy2(base/name,backup/name)
print('BACKUP',backup)
