"""Restore the game's existing laboratory icon atlas and its table references.

Existing artwork is reused verbatim where available. Four absent atlas entries
are assembled from same-purpose native bottle, label and LabStar components.
Combat bonuses, costs, level ranges and saved bottle IDs are never rewritten.
"""
from pathlib import Path
from PIL import Image, ImageFilter, ImageDraw
import argparse
import hashlib
import json
import shutil
from datetime import datetime

ROOT = Path('D:/pvzol本地最新最新ol/pvzol本地')
SOURCE = Path('D:/植物PVZ素材/tieba/实验室药品、素材')
WORK = Path(__file__).resolve().parent
FULL = WORK / 'native_sources'
OUT = WORK / 'icons'
MAPPING = {
    1:'floor32_01', 2:'floor32_02', 3:'floor32_03', 4:'floor32_04',
    5:'floor32_05', 6:'floor32_06', 7:'floor32_07', 8:'floor32_08',
    10:'floor32_09', 12:'floor33_01', 13:'floor33_02', 14:'floor33_03',
    15:'floor33_04', 16:'floor33_05', 17:'floor33_06',
    20:'floor33_07', 21:'floor33_08', 22:'floor33_09',
    24:'floor34_01', 25:'floor34_02', 26:'floor34_03', 27:'floor34_04',
    30:'floor34_05', 31:'floor34_06', 32:'floor34_07', 33:'floor34_08',
    34:'floor34_09', 36:'floor35_01', 37:'floor35_02',
    40:'floor35_03', 41:'floor35_04', 42:'floor35_05', 43:'floor35_06',
    44:'floor35_07', 45:'floor35_08', 46:'floor35_09',
}
MISSING = {
    # bottle body, grade plaque prefix, attribute plaque suffix, star count
    11: ('floor32_02', 'floor33_01', 'floor32_02', 2),
    23: ('floor32_04', 'floor33_09', 'floor34_08', 3),
    35: ('floor32_07', 'floor35_01', 'floor35_07', 4),
    47: ('floor35_09', 'floor35_09', 'floor34_09', 5),
}

def source(prefix):
    paths = list(SOURCE.glob(prefix+'_*.jpg'))
    assert len(paths)==1, prefix
    return paths[0]

def normalized(prefix):
    im=Image.open(source(prefix)).convert('RGBA')
    box=im.getchannel('A').point(lambda x:255 if x>8 else 0).getbbox()
    # Original source plaques have the same 38-pixel height, with a few y offsets.
    offset=126-(box[3]-1-38)
    out=Image.new('RGBA',(200,200)); out.alpha_composite(im,(0,offset))
    return out

def make_missing(bid):
    body,prefix,suffix,stars=MISSING[bid]
    im=normalized(body)
    if bid in (11,23,35):
        # Native stars cover the basic bottle's one-star mark without repainting
        # its artwork. The source remains the correct orange/blue attribute family.
        star=normalized('floor32_04').crop((88,103,113,129))
        pp=star.load()
        for y in range(star.height):
            for x in range(star.width):
                r,g,b,a=pp[x,y]
                if not (r>b*1.2 and g>b*1.1 and r>35):
                    pp[x,y]=(r,g,b,0)
        if bid in (11,35):
            # An even number of overlapping stars cover the old star except its
            # tiny central tip. Restore that tip from the native glass fill.
            pp=im.load()
            for y in range(103,114):
                left,right=pp[86,y],pp[114,y]
                for x in range(97,105):
                    t=(x-86)/28
                    pp[x,y]=tuple(round(a+(b-a)*t) for a,b in zip(left,right))
        for i in range(stars):
            im.alpha_composite(star,(int(88+10*(i-(stars-1)/2)),103))
    # Copy the whole native plaque to avoid leaving any old grade lettering.
    prefix_im=normalized(prefix);suffix_im=normalized(suffix)
    im.paste(prefix_im.crop((40,126,170,168)),(40,126))
    def text_top(pic):
        pp=pic.load()
        return next(y for y in range(129,152) if sum(1 for x in range(68,145) if min(pp[x,y][:3])>210 and pp[x,y][3]>100)>3)
    sy,dy=text_top(suffix_im)-2,text_top(prefix_im)-2
    # Keep the prefix's native characters and append native attribute glyphs.
    im.paste(suffix_im.crop((113,sy,145,sy+19)),(113,dy))
    return im

def build():
    FULL.mkdir(exist_ok=True); OUT.mkdir(exist_ok=True)
    records=[]
    for bid,prefix in MAPPING.items():
        dst=FULL/f'{bid*10000}.png'; shutil.copyfile(source(prefix),dst)
        records.append({'bottle':bid,'icon':bid*10000,'source':str(source(prefix)),'native':True})
    for bid in MISSING:
        make_missing(bid).save(FULL/f'{bid*10000}.png')
        records.append({'bottle':bid,'icon':bid*10000,'native_components':MISSING[bid],'native':False})
    (WORK/'icon_manifest.json').write_text(json.dumps(sorted(records,key=lambda x:x['bottle']),ensure_ascii=False,indent=2),encoding='utf-8')
    # Source sheet is for visual inspection before calibrated game exports.
    sheet=Image.new('RGBA',(1200,1750),'#aaa'); draw=ImageDraw.Draw(sheet)
    for i,p in enumerate(sorted(FULL.glob('*.png'),key=lambda p:int(p.stem))):
        x,y=(i%6)*200,(i//6)*250
        sheet.alpha_composite(Image.open(p).convert('RGBA'),(x,y+25))
        draw.text((x+5,y+5),p.stem,fill='black')
    sheet.convert('RGB').save(WORK/'all_sources.png')
    print('native sources:',len(MAPPING),'native component assemblies:',len(MISSING))

def tables(data):
    p=0; out=[]
    while p<len(data):
        start=p
        n=int.from_bytes(data[p:p+4],'big');p+=4
        name=data[p:p+n].decode('utf-8');p+=n
        n=int.from_bytes(data[p:p+4],'big');p+=4
        chunk=data[p:p+n];p+=n
        out.append((name,chunk,data[start:p]))
    assert p==len(data)
    return out

def apply():
    db=ROOT/'webroot/clientDB.txt'
    all_tables=tables(db.read_bytes())
    rows=dict((name,body) for name,body,_raw in all_tables)['Lab.txt'].decode('utf-8').splitlines()
    header=rows[0].split('\t'); ix=header.index('icon'); bidx=header.index('bottleId')
    updated=[rows[0]]
    changed=0
    for row in rows[1:]:
        cols=row.split('\t'); bid=int(cols[bidx]);icon=str(bid*10000)
        assert (OUT/f'{icon}.png').is_file(),icon
        if cols[ix]!=icon: changed+=1
        cols[ix]=icon;updated.append('\t'.join(cols))
    body=('\r\n'.join(updated)+'\r\n').encode('utf-8')
    replacement=b''.join((len(name.encode()).to_bytes(4,'big')+name.encode()+len(body).to_bytes(4,'big')+body) if name=='Lab.txt' else raw for name,_body,raw in all_tables)
    backup=ROOT/'PvZ-Server-PoC/backups'/('lab-tier-icons-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True)
    shutil.copy2(db,backup/'clientDB.txt')
    shutil.copytree(ROOT/'webroot/image/lab',backup/'lab')
    db.write_bytes(replacement)
    for p in OUT.glob('*.png'):
        shutil.copy2(p,ROOT/'webroot/image/lab'/p.name)
    after=tables(db.read_bytes())
    assert len(after)==len(all_tables)
    for (name,before,_),(aname,abody,_) in zip(all_tables,after):
        assert name==aname
        if name!='Lab.txt': assert before==abody,name
    print('backup:',backup,'updated rows:',changed)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');args=p.parse_args()
    apply() if args.apply else build()
