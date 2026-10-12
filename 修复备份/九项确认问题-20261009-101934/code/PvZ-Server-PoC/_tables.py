import struct, sys
p = r"../webroot/clientDB.txt"
data = open(p, "rb").read()
i = 0
names = []
while i + 8 <= len(data):
    n = struct.unpack(">I", data[i:i+4])[0]; i += 4
    if n <= 0 or n > 200 or i + n > len(data):
        break
    name = data[i:i+n].decode("utf-8", "replace"); i += n
    m = struct.unpack(">I", data[i:i+4])[0]; i += 4
    names.append(name)
    if m < 0 or i + m > len(data):
        break
    i += m
print("tables:", len(names))
for nm in names:
    if "utrition" in nm or "eagent" in nm or "rade" in nm or "abb" in nm:
        print("  ", nm)
