"""Verify the installed avatar and NoEnd fixes in actual Flash bytecode."""
from pathlib import Path
import sys
import unittest
import zlib
SERVER=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SERVER/'client_patches/card-exp-bean-repeat-20260926'))
from patch_module_card_clickthrough import find_abc,parse_abc,encode_u30
WEB=SERVER.parent/'webroot'

def abc_info(path):
    packed=path.read_bytes()
    raw=packed[:8]+zlib.decompress(packed[8:]) if packed[:3]==b'CWS' else packed
    if packed[:3] not in (b'CWS',b'FWS') or len(raw)!=int.from_bytes(packed[4:8],'little'):
        raise AssertionError('Invalid SWF header/length')
    a,b,*_=find_abc(raw)
    abc=raw[a:b]
    return abc,parse_abc(abc)

def method(abc,info,classname,name):
    mid=info.class_methods[(classname,name)]
    _,start,size=info.method_bodies[mid]
    return abc[start:start+size]

def names(info,name):
    indexes=[]
    for i,n in enumerate(info.multinames):
        if not n:continue
        index=n[2] if n[0] in (7,13) else n[1] if n[0] in (9,14) else -1
        if 0<=index<len(info.strings) and info.strings[index]==name:indexes.append(i)
    return indexes

class FlashUIFixTests(unittest.TestCase):
    def test_avatar_initialization_sets_native_circular_mask(self):
        for filename in ('Game_p.swf','Game_n.swf','Game.swf'):
            with self.subTest(file=filename):
                abc,info=abc_info(WEB/filename)
                code=method(abc,info,'com.qq.modules.mapinfo.view.components.MapInfoRoleWnd','InitializeComponent')
                self.assertTrue(any(b'\x4f'+encode_u30(i)+b'\x03' in code for i in names(info,'drawCircle')))
                self.assertTrue(any(b'\x61'+encode_u30(i) in code for i in names(info,'mask')))

    def test_all_loader_aliases_have_current_ui_fix(self):
        for pattern,canonical in [('Game_p*.swf','Game_p.swf'),('Game_n*.swf','Game_n.swf'),('ModuleEndlessFuben*.swf','ModuleEndlessFuben.swf')]:
            expected=(WEB/canonical).read_bytes()
            for path in WEB.glob(pattern):
                with self.subTest(file=path.name):self.assertEqual(path.read_bytes(),expected)

    def test_endless_free_and_paid_paths_exist_in_real_button(self):
        abc,info=abc_info(WEB/'ModuleEndlessFuben.swf')
        code=method(abc,info,'com.qq.modules.endlessFuben.view.EndlessFubenViewMediator','onClickBtnRefresh')
        self.assertIn('购买重置进度',info.strings)
        self.assertIn('免费重置进度',info.strings)
        self.assertTrue(any(b'\x46'+encode_u30(i) in code or b'\x4f'+encode_u30(i) in code for i in names(info,'confirmUseDiamond')))
        model=method(abc,info,'com.qq.modules.endlessFuben.model.EndlessFubenModel','getLeftResetTimes')
        self.assertIn(b'\x2a\x24\x00\x18',model)  # Clamp depleted free count with an AVM2 branch.

if __name__=='__main__':unittest.main()
