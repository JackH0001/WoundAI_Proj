"""驗證 iOS Swift DTO 與 OpenAPI 契約對齊（無 Swift 編譯器時的契約防漂移；納入 CI）。
檢查：欄位名一致、完整覆蓋、required 欄位在 Swift 為非 Optional。"""
import os, re, sys, yaml
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
SWIFT = os.path.join(ROOT, "iOS", "WoundMeasurementApp", "Services", "AnnotationFlywheelService.swift")
SPEC = os.path.join(ROOT, "openapi", "annotation_segmentation.yaml")
src = open(SWIFT, encoding="utf-8").read()
schemas = yaml.safe_load(open(SPEC, encoding="utf-8"))["components"]["schemas"]
def parse_struct(name):
    m = re.search(r"struct %s: Codable \{(.*?)\n\}" % name, src, re.S)
    if not m: return None
    body = m.group(1)
    optional = {}
    for pm in re.finditer(r"let (\w+):\s*([\w\[\]]+)(\??)", body):
        optional[pm.group(1)] = (pm.group(3) == "?")
    cm = re.search(r"enum CodingKeys.*?\{(.*?)\}", body, re.S)
    keys, prop_opt = {}, {}
    for line in cm.group(1).splitlines():
        c = re.search(r"case (\w+)(?:\s*=\s*\"([^\"]+)\")?", line)
        if not c: continue
        swift_name = c.group(1); json_key = c.group(2) or swift_name
        keys[json_key] = swift_name; prop_opt[json_key] = optional.get(swift_name, True)
    return keys, prop_opt
r = []
def ck(n, c): r.append(bool(c)); print(("PASS " if c else "FAIL "), n)
for sname in ("SegmentationResult", "AnnotationSubmit", "AnnotationRecord"):
    parsed = parse_struct(sname)
    ck(f"{sname}: Swift DTO 存在", parsed is not None)
    if not parsed: continue
    keys, prop_opt = parsed
    props = set(schemas[sname]["properties"].keys()); req = set(schemas[sname].get("required", []))
    ck(f"{sname}: 欄位名與 schema 完全一致", set(keys.keys()) == props)
    ck(f"{sname}: required ⊆ Swift 欄位", req <= set(keys.keys()))
    ck(f"{sname}: required 欄位皆為非 Optional", all(prop_opt.get(k, True) is False for k in req))
ok = sum(r); print(f"\n{ok}/{len(r)} PASS"); sys.exit(0 if ok == len(r) else 1)
