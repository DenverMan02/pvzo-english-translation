import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pvz_poc_server as srv
rows = srv._read_clientdb_table("ItemInfo.txt")
hdr = rows[0]; ci = {c: i for i, c in enumerate(hdr)}
def col(r, k):
    i = ci.get(k); return r[i] if i is not None and i < len(r) else ""
want = sys.argv[1] if len(sys.argv) > 1 else "金色试剂"
print("--- by name:", want)
for r in rows[1:]:
    if want in col(r, "name"):
        print(col(r,"id"), "|", col(r,"name"), "| type", col(r,"type"), "| quality", col(r,"quality"), "| gainWay", col(r,"gainWay")[:60])
print("--- ids 59990..60010")
for r in rows[1:]:
    try: iid = int(col(r, "id"))
    except ValueError: continue
    if 59990 <= iid <= 60010:
        print(iid, "|", col(r,"name"), "| type", col(r,"type"), "| quality", col(r,"quality"))
