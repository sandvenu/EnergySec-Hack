import re, sys
p = "work/ppt/presentation.xml"
x = open(p, encoding="utf-8").read()
ids = {1: ("2147483646", "rId2"), 2: ("272", "rId3"), 3: ("2147482752", "rId4"), 4: ("2147483647", "rId5"),
       5: ("271", "rId6"), 6: ("273", "rId7"), 7: ("2147482753", "rId8"), 8: ("274", "rId9"), 9: ("275", "rId10"),
       11: ("276", "rId12"), 12: ("277", "rId13"), 13: ("278", "rId14"), 14: ("279", "rId15"), 15: ("280", "rId16"),
       16: ("256", "rId24")}
order = [1, 2, 12, 5, 4, 11, 8, 14, 6, 9, 7, 13, 3, 16, 15]
new = "<p:sldIdLst>" + "".join(f'<p:sldId id="{ids[n][0]}" r:id="{ids[n][1]}"/>' for n in order) + "</p:sldIdLst>"
x2, k = re.subn(r"<p:sldIdLst>.*?</p:sldIdLst>", new, x, flags=re.S)
assert k == 1
open(p, "w", encoding="utf-8").write(x2)
print("order set:", order)
