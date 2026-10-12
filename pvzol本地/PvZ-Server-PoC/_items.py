import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pvz_poc_server as srv
rows = srv._read_clientdb_table("ItemInfo.txt")
hdr = rows[0]; ci = {c: i for i, c in enumerate(hdr)}
def col(r, k):
    i = ci.get(k); return r[i] if i is not None and i < len(r) else ""
print("cols:", ",".join(hdr[:24]))
for r in rows[1:]:
    try: iid = int(col(r, "id"))
    except ValueError: continue
    if 60000 <= iid < 60700 and iid % 100 in range(1, 10) and (iid % 100) != 0:
        print(iid, "|", col(r,"name"), "| type", col(r,"type"), "| subType", col(r,"subType"), "| quality", col(r,"quality"), "| icon", col(r,"icon"), "| desc", col(r,"description")[:40])
