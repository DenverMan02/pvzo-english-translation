"""Normalize native tower mouse points at their callers only.

The native SystemManager owns the window scale/centering transform. MixLayer
globalToLocal instead delegates to its Starling display, whose coordinates are
used internally elsewhere. Preserve that API and the existing internal tower
scale; remove the native window transform before passing external mouse input.
"""
from pathlib import Path
import hashlib
import json
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'tower'))
import build_tower_patch as tower

SOURCE = tower.PROJECT / 'webroot/ModuleTD_p.swf'
DEST = HERE / 'ModuleTD_p.pointer.swf'
MOUSE = 'com.qq.modules.td.logic.mouseAction.'


def insert_and_relocate(code, inserts):
    """Insert at fall-through boundaries, retaining original branch targets.

    Targets skip the insertion at that exact boundary. This is required for
    ReleaseAreaSkillAction: the valid-tile branch must skip normalization of
    the fallback raw stage coordinates, which only the else branch rereads.
    """
    original = list(tower.instructions(code).values())
    boundaries = {ins.offset for ins in original}
    assert set(inserts) <= boundaries
    new_at = lambda offset: offset + sum(len(v) for k, v in inserts.items() if k <= offset)
    result = bytearray()
    for ins in original:
        if ins.offset in inserts:
            result.extend(inserts[ins.offset])
        assert len(result) == new_at(ins.offset)
        piece = bytearray(code[ins.offset:ins.next_offset])
        if ins.targets:
            assert len(ins.targets) == 1 and ins.opcode != 0x1b
            delta = new_at(ins.targets[0]) - (len(result) + len(piece))
            assert -(1 << 23) <= delta < (1 << 23)
            piece[1:4] = (delta & 0xffffff).to_bytes(3, 'little')
        result.extend(piece)
    tower.instructions(bytes(result))
    return bytes(result)


def build():
    ctx = tower.context(SOURCE)
    info = ctx['info']
    sm = tower.mn(info, 'SystemManager', 'asgui.managers')
    instance = tower.mn(info, 'instance', '')
    global_to_local = tower.mn(info, 'globalToLocal', '')
    point = tower.mn(info, 'Point', 'flash.geom')
    enc = tower.encode_u30
    manager = bytes([0x60]) + enc(sm) + bytes([0x66]) + enc(instance)
    convert = bytes([0x46]) + enc(global_to_local) + bytes([1, 0x80]) + enc(point)

    def local(register):
        load = bytes([0xd0 + register]) if register <= 3 else bytes([0x62]) + enc(register)
        store = bytes([0xd4 + register]) if register <= 3 else bytes([0x63]) + enc(register)
        return manager + load + convert + store

    def field(field_id):
        return bytes([0xd0]) + manager + bytes([0xd0, 0x66]) + enc(field_id) + convert + bytes([0x68]) + enc(field_id)

    # Explicit boundaries and expected original instructions keep this patch
    # tied to the currently installed SWF, including the preceding viewport fix.
    specs = [
        (MOUSE+'TDCropPlantAction', 'onMouseMove', {41:local(2)}, None),
        (MOUSE+'TDCropPlantAction', 'onMouseClick', {102:field(712)}, None),
        (MOUSE+'TDUseShovelAction', 'onMouseMove', {67:field(927)}, None),
        (MOUSE+'TDUseShovelAction', 'onMouseClick', {57:local(2)}, None),
        (MOUSE+'TDReleaseAreaSkillAction', 'onMouseMove', {28:local(2),216:local(2)}, None),
        (MOUSE+'TDReleaseAreaSkillAction', 'onMouseClick', {18:local(2)}, None),
        (MOUSE+'TDLayEnergyTileAction', 'onMouseMove', {28:local(2)}, None),
        (MOUSE+'TDLayEnergyTileAction', 'onMouseClick', {28:local(2)}, None),
        ('com.qq.modules.td.logic.TDSpecialSkillTrigger', 'updateState', {117:field(1542)}, None),
        # Existing local4 is declared Point and is about to receive the final
        # effectLayer result; use it transiently so max_stack remains 4.
        ('com.qq.modules.td.logic.bt_ai.aiNode.aiCode.plant.AI_plant_OxygenAlgae',
         'onMouseMove', {82:bytes([0x63,4]) + manager + bytes([0x62,4]) + convert}, None),
        ('com.qq.modules.td.logic.miniGame.jewels.TDMiniGameJewelsMouseCtr',
         'getJewel', {36:local(1)}, None),
    ]
    patches = {}
    for cls, method, inserts, limits in specs:
        mid, code = tower.body(ctx, cls, method)
        decoded = tower.instructions(code)
        for offset in inserts:
            if cls.endswith('AI_plant_OxygenAlgae'):
                assert decoded[offset].opcode == 0x46
                assert tower.qname(info, decoded[offset].args[0])[1] == 'globalToLocal'
            else:
                before = [ins for ins in decoded.values() if ins.next_offset == offset][0]
                assert before.opcode in (0xd6, 0x61)
                if before.opcode == 0x61:
                    assert tower.qname(info, before.args[0])[1] == 'y'
        patched = insert_and_relocate(code, inserts)
        patches[(cls,method)] = (patched, limits, dict(
            insertion_offsets=list(inserts),
            insertion_hex={str(k):v.hex() for k,v in inserts.items()},
            native_window_point_normalized=True))
    # Native hammer sprites are children of the scaled native TD top-effect
    # layer. Their two raw stage reads need one normalized Point, with no
    # Starling conversion. Rebuild only these tiny, branch-free method bodies.
    for cls, cursor_id in [
        ('com.qq.modules.td.logic.action.object.effect.TDSkillSummonZombieAction',4387),
        ('com.qq.modules.td.logic.action.zombie.terracotta.ZombieTerracottaCottaAction',4183),
    ]:
        mid, code = tower.body(ctx,cls,'onMouseMove')
        assert len(code) == 35
        assert not any(ins.targets for ins in tower.instructions(code).values())
        assert code[:2] == bytes([0xd0,0x30]) and code[-1:] == bytes([0x47])
        raw_mouse = lambda name: (bytes([0x60]) + enc(tower.mn(info,'GameGloble','com.qq'))
                                 + bytes([0x66]) + enc(tower.mn(info,'stage',''))
                                 + bytes([0x66]) + enc(tower.mn(info,name,'')))
        px,py = tower.mn(info,'x',''),tower.mn(info,'y','')
        expected = (bytes([0xd0,0x30,0xd0,0x66]) + enc(cursor_id) + raw_mouse('mouseX')
                    + bytes([0x61]) + enc(px) + bytes([0xd0,0x66]) + enc(cursor_id)
                    + raw_mouse('mouseY') + bytes([0x61]) + enc(py) + bytes([0x47]))
        assert code == expected
        normalized = (bytes([0xd0,0x30]) + manager + bytes([0x5d]) + enc(point)
                      + raw_mouse('mouseX') + raw_mouse('mouseY')
                      + bytes([0x4a]) + enc(point) + bytes([2]) + convert + bytes([0xd6]))
        assignments = b''.join(bytes([0xd0,0x66]) + enc(cursor_id) + bytes([0xd2,0x66])
                               + enc(axis) + bytes([0x61]) + enc(axis) for axis in (px,py))
        patches[(cls,'onMouseMove')] = (normalized + assignments + bytes([0x47]),(4,3),
            dict(native_window_point_normalized=True,native_cursor_only=True,
                 original_code_hex=code.hex(),point_register=2))
    # A stable baseline enables meaningful old/new regression tests even after
    # a later authorized installation by the parent agent.
    (HERE/'ModuleTD_p.current.swf').write_bytes(ctx['packed'])
    manifest = tower.write_candidate(ctx, patches, DEST)
    packed = DEST.read_bytes()
    install = [dict(file=SOURCE.name,input_sha256=manifest['source_sha256'],
                    output_sha256=manifest['output_sha256'],output_md5=hashlib.md5(packed).hexdigest(),
                    size=len(packed),candidate=str(DEST))]
    (HERE/'install_manifest.json').write_text(json.dumps(install,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    build()
