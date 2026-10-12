"""Throwaway: dump a clientDB.txt table as TSV (first N rows)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pvz_poc_server as srv

name = sys.argv[1]
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 40
rows = srv._read_clientdb_table(name)
if not rows:
    print("TABLE MISSING", name); raise SystemExit(1)
print("ROWS", len(rows))
for r in rows[:limit]:
    print("\t".join(str(c) for c in r))
