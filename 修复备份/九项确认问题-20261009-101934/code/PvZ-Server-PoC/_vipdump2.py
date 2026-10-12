import pvz_poc_server as s
s._CLIENTDB_PATH = r"C:/Users/huawei/Downloads/pvzol本地最新最新ol/pvzol本地/webroot/clientDB.txt"
for tbl in ("VipPrivilegeInfo.txt",):
    rows = s._read_clientdb_table(tbl)
    if not rows:
        print(tbl, "NOT FOUND"); continue
    hdr = rows[0]; ci = {c:i for i,c in enumerate(hdr)}
    print("==",tbl, hdr)
    for r in rows[1:]:
        def g(k):
            i = ci.get(k)
            return r[i] if i is not None and i < len(r) else ""
        pid = g("id") or g("privilegeId")
        print("  ", pid, "|", g("name"), "|", g("description") or g("desc"))
