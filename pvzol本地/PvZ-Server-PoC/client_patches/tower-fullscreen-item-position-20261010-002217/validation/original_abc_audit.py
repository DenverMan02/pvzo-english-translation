from pathlib import Path

SOURCE = Path(__file__).resolve().with_name('abcdis.py')
source = SOURCE.read_text(encoding='utf-8')
source = source.replace('for i in range(1, n):\n            pc, ret', 'for i in range(n):\n            pc, ret')
source = source.replace('                    r2.u30()\n                    pos = r2.pos\n                    args.append(f"-> {start + s24 if False else pos + s24}")', '                    pos += 3\n                    args.append(f"-> {pos + s24}")')
source = source.replace('''                    r2 = Reader(code, pos)
                    r2.u30()
                    cnt = r2.u30()
                    for _ in range(cnt + 1):
                        r2.u30()
                    pos = r2.pos
                    args.append(f"{cnt} cases")''', '''                    default = int.from_bytes(code[pos:pos+3], "little", signed=True)
                    pos += 3
                    r2 = Reader(code, pos)
                    cnt = r2.u30()
                    pos = r2.pos
                    targets = []
                    for _ in range(cnt + 1):
                        targets.append(start + int.from_bytes(code[pos:pos+3], "little", signed=True))
                        pos += 3
                    args.append(f"default -> {start + default}; cases {targets}")''')
ns = {'__name__': 'abc_scratch'}
exec(source, ns)
Abc = ns['Abc']
Reader = ns['Reader']
abc_blobs = ns['abc_blobs']

def traits(self, r):
    out = []
    for _ in range(r.u30()):
        name = r.u30()
        k = r.u8()
        tag = k & 15
        e = {'name': self.mn(name), 'kind': tag}
        if tag in (0, 6):
            e['slot'], e['type'] = r.u30(), r.u30()
            v = r.u30()
            e['value_index'] = v
            if v:
                e['vkind'] = r.u8()
        elif tag in (1, 2, 3):
            e['disp'], e['method'] = r.u30(), r.u30()
        elif tag == 4:
            e['slot'], e['class'] = r.u30(), r.u30()
        elif tag == 5:
            e['slot'], e['method'] = r.u30(), r.u30()
        else:
            raise ValueError('trait kind ' + str(tag))
        if k & 64:
            for _ in range(r.u30()):
                r.u30()
        out.append(e)
    return out

def mn(self, i):
    if i == 0 or i >= len(self.multinames):
        return '*'
    kind, data = self.multinames[i]
    if kind in (7, 13):
        ni, name = data
        nsp = self.namespaces[ni]
        return (self.s(nsp[1]) + '::' if nsp and nsp[1] else '::') + self.s(name)
    if kind in (9, 14):
        return '::' + self.s(data[0])
    if kind == 29:
        return self.mn(data[0]) + '<...>'
    return '<runtime>'

Abc._traits = traits
Abc.mn = mn
BASE = Path(r'D:\pvzol本地最新最新ol\pvzol本地\webroot')

def load(filename):
    return [Abc(b).load(tries=10) for b in abc_blobs(str(BASE / filename))]

def dump(abc, c, q, m):
    head = c['name'] + '.' + q + ' method ' + str(m)
    b = abc.body_of_method.get(m)
    return '\n'.join([head] + (abc.disassemble(b['code']) if b else ['<no body>']))
