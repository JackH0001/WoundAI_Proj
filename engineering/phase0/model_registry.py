"""ModelRegistry：以 manifest 管理模型；缺檔即回 None，呼叫端須 graceful degrade，禁止偽造結果。"""
import json, os
class ModelRegistry:
    def __init__(self, manifest_path, base_dir=None):
        self.base = base_dir or os.path.dirname(os.path.abspath(manifest_path))
        self.m = {k:v for k,v in json.load(open(manifest_path, encoding="utf-8")).items() if not k.startswith("_")}
    def path(self, model_id):
        d = self.m.get(model_id)
        return os.path.join(self.base, d["artifact"]) if d and d.get("artifact") else None
    def is_available(self, model_id):
        p = self.path(model_id)
        return bool(p and os.path.exists(p))
    def require(self, model_id):
        return self.path(model_id) if self.is_available(model_id) else None
    def report(self):
        return {k: {"task": d.get("task"), "stage": d.get("stage"), "available": self.is_available(k)} for k, d in self.m.items()}
if __name__ == "__main__":
    import sys
    r = ModelRegistry(sys.argv[1] if len(sys.argv) > 1 else "model_registry.json")
    for k, i in r.report().items():
        print(("OK     " if i["available"] else "MISSING"), k, f"[{i['task']}/{i['stage']}]")
