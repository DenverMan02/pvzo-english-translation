import re, struct
p = r"C:/Users/huawei/Downloads/pvzol本地最新最新ol/pvzol本地/webroot/clientDB.txt"
data = open(p,"rb").read()
pos = 0
names = []
while pos + 4 <= len(data):
    (n,) = struct.unpack_from(">I", data, pos); pos += 4
    name = data[pos:pos+n].decode("utf-8","replace"); pos += n
    (m,) = struct.unpack_from(">I", data, pos); pos += 4
    body = data[pos:pos+m]; pos += m
    names.append((name, body))
print("total tables:", len(names))
for name, body in names:
    txt = body.decode("utf-8","replace")
    hdr = txt.split("\n",1)[0]
    if "privilege" in hdr.lower() or "vip" in name.lower():
        print("==", name, "|", hdr[:200])
