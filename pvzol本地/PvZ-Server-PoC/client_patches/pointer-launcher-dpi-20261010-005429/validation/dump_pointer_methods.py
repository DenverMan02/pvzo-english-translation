from actual_vm import VM
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parent.parent/'plant-alignment'
vm=VM(ROOT/'ModuleTD_p.current.swf')
targets=json.loads((ROOT/'ModuleTD_p.pointer.json').read_text(encoding='utf-8'))['methods']
texts=[]
for target in targets:
    abc=vm.abcs[0]
    body=abc.body_of_method[target['method_id']]
    texts.append(str(target)+'\n'+'\n'.join(abc.disassemble(body['code'])))
for abc in vm.abcs:
    for entry in abc.classes:
        for trait in entry['traits']+entry['inst_traits']:
            if VM.short(trait['name']) in ('getRoadTileByScreenPos','getRoadTileByIndex','getRoadTileByPos'):
                body=abc.body_of_method[trait['method']]
                texts.append(entry['name']+' '+str(trait)+'\n'+'\n'.join(abc.disassemble(body['code'])))
(ROOT/'pointer_method_dump.txt').write_text('\n\n'.join(texts),encoding='utf-8')
print(len(texts),'method dumps')
