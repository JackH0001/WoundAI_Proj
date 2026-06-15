"""FeatureFlags：未知或關閉之功能一律視為 False（保守，避免啟用未完成功能）。"""
import json
class FeatureFlags:
    def __init__(self, path):
        self.f = {k: v for k, v in json.load(open(path, encoding="utf-8")).items() if not k.startswith("_")}
    def is_enabled(self, name):
        return bool(self.f.get(name, False))
if __name__ == "__main__":
    import sys
    ff = FeatureFlags(sys.argv[1] if len(sys.argv) > 1 else "feature_flags.json")
    for k in ff.f:
        print("ON " if ff.is_enabled(k) else "off", k)
