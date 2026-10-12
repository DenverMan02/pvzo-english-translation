import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pvz_poc_server as srv
rows = srv._read_clientdb_table("nutritionProperty.txt")
hdr = rows[0]; ci = {c: i for i, c in enumerate(hdr)}
def col(r, k):
    i = ci.get(k); return r[i] if i is not None and i < len(r) else "0"
seen = {}
for r in rows[1:]:
    try: bid = int(col(r,"itemID")); lv = int(col(r,"level"))
    except ValueError: continue
    if lv != 1: continue
    seen[bid] = (int(col(r,"quality")), int(col(r,"propertyType")), int(col(r,"propertyValue")), int(col(r,"exp")), int(col(r,"needExp")))
for bid in sorted(seen):
    q, pt, pv, ex, ne = seen[bid]
    print(bid, "quality=", q, "type=", pt, "value=", pv, "exp=", ex, "needExp=", ne)
