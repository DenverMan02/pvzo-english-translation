"""Regression for the actual bad class initializer and patched reset bytecode."""
from pathlib import Path
import json
import unittest
from abc_tools import find_abc,parse_abc,u30
from build_minimal_patch import unpack,SOURCE

HERE=Path(__file__).resolve().parent
WEB=Path('D:/pvzol本地最新最新ol/pvzol本地/webroot')
PATCH=HERE/'ModuleEndlessFuben.minimal.swf'
BROKEN=HERE/'ModuleEndlessFuben.broken.swf'

def read(path):
    raw=unpack(path);a,b,*_=find_abc(raw);abc=raw[a:b]
    return abc,parse_abc(abc)

def code(abc,info,mid):
    _,a,n=info.method_bodies[mid]
    return abc[a:a+n]

def name(info,mn):
    n=info.multinames[mn]
    return info.strings[n[2] if n[0] in (7,13) else n[1]]

def instructions(body):
    p=0;out={}
    single={0x2c,0x60,0x66,0x5d,0x58,0x68,0x61,0x62,0x63}
    while p<len(body):
        start=p;op=body[p];p+=1;args=()
        if op in single:
            a,p=u30(body,p);args=(a,)
        elif op in (0x46,0x4f):
            a,p=u30(body,p);b,p=u30(body,p);args=(a,b)
        elif op==0x24:
            args=(int.from_bytes(body[p:p+1],'little',signed=True),);p+=1
        elif op in (0x10,0x17,0x18):
            delta=int.from_bytes(body[p:p+3],'little',signed=True);p+=3;args=(p+delta,)
        elif op not in (0xd0,0xd1,0xd2,0xd3,0xd6,0x30,0x1d,0x20,0x26,0x73,0xa1,0x2a,0x29,0x47,0x48):
            raise AssertionError(f'Unsupported opcode {op:02x} at {start}')
        out[start]=(op,args,p)
    return out

def verify(body,header):
    _,max_stack,local_count,initial,max_scope=header
    ins=instructions(body);queue=[(0,0,initial)];seen={};highest=0
    while queue:
        pc,depth,scope=queue.pop()
        if pc in seen:
            assert seen[pc]==(depth,scope),f'Inconsistent merge at {pc}'
            continue
        seen[pc]=(depth,scope)
        assert pc in ins,f'Invalid branch {pc}'
        op,args,next_pc=ins[pc];take=0;give=0
        if op in (0xd0,0xd1,0xd2,0xd3,0x60,0x5d,0x24,0x2c,0x20,0x26):give=1
        elif op in (0x66,0x58,0x73):take=1;give=1
        elif op in (0x68,0x61,0x17,0x18,0xa1):take=2;give=int(op==0xa1)
        elif op in (0x30,0x29,0xd6,0x48):take=1
        elif op==0x2a:take=1;give=2
        elif op in (0x46,0x4f):take=args[1]+1;give=int(op==0x46)
        if 0xd0<=op<=0xd3:assert op-0xd0<local_count
        if op==0xd6:assert local_count>2
        assert depth>=take,f'Stack underflow at {pc}: opcode {op:02x}, depth {depth}, requires {take}'
        depth+=give-take;highest=max(highest,depth)
        if op==0x30:scope+=1
        if op==0x1d:scope-=1
        assert initial<=scope<=max_scope,f'Scope out of bounds {scope}'
        assert depth<=max_stack,f'max_stack too small at {pc}'
        if op in (0x47,0x48):assert depth==0;continue
        if op==0x10:queue.append((args[0],depth,scope))
        elif op in (0x17,0x18):queue.extend(((args[0],depth,scope),(next_pc,depth,scope)))
        else:queue.append((next_pc,depth,scope))
    return highest

def execute(body,info,this,globals_):
    ins=instructions(body);pc=0;stack=[];locals_={0:this,1:None};steps=0
    while True:
        steps+=1;assert steps<200
        op,args,next_pc=ins[pc];pc=next_pc
        if op in (0xd0,0xd1,0xd2,0xd3):stack.append(locals_[op-0xd0])
        elif op==0xd6:locals_[2]=stack.pop()
        elif op==0x30:stack.pop()
        elif op==0x60:stack.append(globals_[name(info,args[0])])
        elif op==0x66:stack.append(stack.pop()[name(info,args[0])])
        elif op==0x24:stack.append(args[0])
        elif op==0x2c:stack.append(info.strings[args[0]])
        elif op==0x20:stack.append(None)
        elif op==0x26:stack.append(True)
        elif op==0x73:stack.append(int(stack.pop()))
        elif op==0xa1:
            b=stack.pop();a=stack.pop();stack.append(a-b)
        elif op==0x2a:stack.append(stack[-1])
        elif op==0x29:stack.pop()
        elif op in (0x17,0x18):
            b=stack.pop();a=stack.pop()
            if (a>b if op==0x17 else a>=b):pc=args[0]
        elif op==0x10:pc=args[0]
        elif op in (0x46,0x4f):
            argc=args[1];params=stack[-argc:] if argc else []
            if argc:del stack[-argc:]
            obj=stack.pop();value=obj[name(info,args[0])](*params)
            if op==0x46:stack.append(value)
        elif op==0x48:return stack.pop()
        elif op==0x47:assert not stack;return
        else:raise AssertionError(op)

class VerifyErrorRegression(unittest.TestCase):
    def test_installed_module_and_all_aliases_match_verified_candidate(self):
        expected=PATCH.read_bytes()
        installed=list(WEB.glob('ModuleEndlessFuben*.swf'))
        self.assertGreaterEqual(len(installed),2)
        for path in installed:
            with self.subTest(file=path.name):
                self.assertEqual(path.read_bytes(),expected)
                read(path)

    def test_old_initializer_reproduces_underflow_and_original_is_valid(self):
        abc,info=read(BROKEN)
        with self.assertRaisesRegex(AssertionError,'Stack underflow'):
            verify(code(abc,info,346),info.method_headers[346])
        abc,info=read(PATCH)
        verify(code(abc,info,186),info.method_headers[186])

    def test_all_other_bodies_scopes_and_class_traits_are_preserved(self):
        original,oi=read(SOURCE);patched,pi=read(PATCH)
        targets={75,177}
        self.assertEqual(oi.class_methods,pi.class_methods)
        self.assertEqual(oi.multinames,pi.multinames)
        self.assertEqual(oi.namespaces,pi.namespaces)
        self.assertEqual(oi.method_bodies.keys(),pi.method_bodies.keys())
        for mid in oi.method_bodies:
            if mid in targets:continue
            self.assertEqual(code(original,oi,mid),code(patched,pi,mid),mid)
            self.assertEqual(oi.method_headers[mid][1:],pi.method_headers[mid][1:],mid)

    def test_changed_methods_have_valid_stacks_and_scope_headers(self):
        abc,info=read(PATCH)
        self.assertEqual(verify(code(abc,info,177),info.method_headers[177]),7)
        self.assertEqual(verify(code(abc,info,75),info.method_headers[75]),3)

    def test_actual_bytecode_free_paid_and_negative_remaining_behavior(self):
        abc,info=read(PATCH);left=code(abc,info,75);click=code(abc,info,177)
        for used in range(9):
            for price in (0,20,45):
                with self.subTest(used=used,price=price):
                    calls=[];model={'resetTimes':used}
                    callback=object()
                    globals_={'ServerConfigTemplateFactory':{'instance':{'iNoEndFubenResetTimes':3,'iNoEndFubenResetGoldTicket':price}},
                              'CommonCmd':{'confirmUseDiamond':lambda *args:calls.append(args)},
                              'EndlessFubenProxyCmd':{'requestReset':callback}}
                    model['getLeftResetTimes']=lambda:execute(left,info,model,globals_)
                    self.assertEqual(model['getLeftResetTimes'](),max(0,3-used))
                    execute(click,info,{'proxy':{'model':model}},globals_)
                    expected=0 if used<3 else price
                    self.assertEqual(calls,[(('免费重置进度' if expected==0 else '购买重置进度'),expected,callback,None,0,True)])

if __name__=='__main__':unittest.main(verbosity=2)
