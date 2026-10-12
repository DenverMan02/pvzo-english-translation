import pvz_poc_server as s
s._CLIENTDB_PATH = r"C:/Users/huawei/Downloads/pvzol本地最新最新ol/pvzol本地/webroot/clientDB.txt"
rows = s._read_clientdb_table("VipInfo.txt")
hdr = rows[0]
print(hdr)
ci = {c:i for i,c in enumerate(hdr)}
for r in rows[1:]:
    def g(k):
        i = ci.get(k)
        return r[i] if i is not None and i < len(r) else ""
    print(g("vipType"), g("level"), g("name"), "|", g("privilegeString"))
