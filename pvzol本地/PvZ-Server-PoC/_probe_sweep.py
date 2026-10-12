import pvz_poc_server as s
import player_store as store
s._CLIENTDB_PATH = r"C:/Users/huawei/Downloads/pvzol本地最新最新ol/pvzol本地/webroot/clientDB.txt"
store.set_db_path(r"C:/Users/huawei/Downloads/pvzol本地最新最新ol/pvzol本地/PvZ-Server-PoC/players.db")
player, created = store.get_or_create_player(700000204)
print("player:", player["uin"], player["name"], "level", player["level"], "vip", player.get("vip_level"))
privs = s.vip_privileges(player)
print("privilege ids:", sorted(privs))
print("has14:", 14 in privs, "has15:", 15 in privs)
for stars in (0,1,2,3):
    print("  stars=%d -> %s" % (stars, s._fuben_sweep_access(player, stars)))
print("priv SC bytes:", len(s.build_pvzprivilege_sc(player)))
# simulate the sweep
import struct
def varint(n):
    out=b""
    while True:
        b=n&0x7f; n>>=7
        out+=bytes([b|(0x80 if n else 0)])
        if not n: return out
req = varint(1<<3|0)+varint(1)+varint(2<<3|0)+varint(1)+varint(3<<3|0)+varint(1)+varint(4<<3|0)+varint(10)+varint(5<<3|0)+varint(1)
print("sweep 10x reply:", [n for n,_ in s.handle_fuben_sweep(req, player)])

print("passed ch1 m1:", store.get_fuben_passed(700000204,1,1))
print("energy:", player.get("energy"), srv_energy := None)
print("fuben_energy_cost:", s.fuben_energy_cost(1,1,1))
