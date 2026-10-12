import sqlite3

uin = 700000204

plants_to_add = [
    1160, 1170, 1180, 1190,
    1220, 1230, 1240, 1250,
    8800, 8900, 9000, 9100, 9200,
    9400, 9500, 9600, 9700, 9800, 9900,
    1130, 1140
]

conn = sqlite3.connect("players.db")
c = conn.cursor()

# 插入
for pid in plants_to_add:
    try:
        c.execute("INSERT INTO plants (uin, plant_id) VALUES (?, ?)", (uin, pid))
        print(f"✅ 插入 {pid}")
    except sqlite3.IntegrityError:
        print(f"⏭️ {pid} 已存在")

conn.commit()

# 验证
c.execute("SELECT plant_id FROM plants WHERE uin=? ORDER BY plant_id", (uin,))
result = [row[0] for row in c.fetchall()]
print(f"\n📋 当前植物总数: {len(result)}")
print(result)

conn.close()