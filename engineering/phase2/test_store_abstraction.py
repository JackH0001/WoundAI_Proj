# -*- coding: utf-8 -*-
"""儲存抽象層契約測試：換掉底層實作，飛輪的行為必須完全一樣。

## 為什麼需要這支測試

上 Cloud Run 之前把檔案 I/O 抽成 `store.Store` 介面。抽象化最常見的失敗模式是
「本機還會動、雲端悄悄壞掉」——而雲端壞掉時的症狀是**統計數字某天歸零**，
沒有例外、沒有紅字，等到有人發現時資料已經沒了。

所以這裡用一個**純記憶體的假實作**跑一遍完整的飛輪語意：附加、讀取、影像存在性、
隔離與回復。假實作刻意模擬物件儲存的三個關鍵差異：

1. **物件不可變、沒有 append** —— 一筆一個物件，讀取時靠鍵名排序還原順序
2. **沒有 rename** —— move 是「複製再刪除」
3. **沒有目錄** —— 只有前綴

只要這支過了，`GcsStore` 出錯的機率就只剩在「呼叫 Google API 的那幾行」，
那是連線設定問題，不是語意問題。

    python engineering/phase2/test_store_abstraction.py
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))
sys.path.insert(0, BACKEND)

FAILED = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


from store import Store


class FakeObjectStore(Store):
    """模擬物件儲存：不可變物件、無 append、無 rename、無目錄。"""

    def __init__(self):
        self.objects = {}   # name -> bytes
        self._seq = 0

    def append_line(self, key, line):
        # 一筆一個物件，鍵名前綴保證字典序＝時間序
        self._seq += 1
        self.objects["%s/%020d.jsonl" % (key, self._seq)] = (line.rstrip("\n") + "\n").encode()

    def read_lines(self, key):
        base = key + "/"
        out = []
        for name in sorted(k for k in self.objects if k.startswith(base)):
            out.extend([ln for ln in self.objects[name].decode().split("\n") if ln.strip()])
        return out

    def put_blob(self, key, data):
        self.objects[key] = data

    def get_blob(self, key):
        return self.objects.get(key)

    def exists(self, key):
        return key in self.objects

    def move(self, src, dst):
        if src not in self.objects:
            return False
        self.objects[dst] = self.objects.pop(src)   # 複製再刪除
        return True

    def list_keys(self, prefix):
        base = prefix.rstrip("/") + "/"
        return sorted(k for k in self.objects if k.startswith(base))

    def describe(self):
        return "fake-object-store"

    # 稽核鏈序列化原語。假物件儲存要忠實模擬 GCS 的「只在物件不存在時建立」語意——
    # 這正是序列化依賴的那條性質,所以不能用 append_line 的舊路徑退回去。
    def chain_tail(self, key):
        from store import _CHAIN_NAME_RE
        base = key + "/"
        names = sorted(k for k in self.objects if k.startswith(base))
        if not names:
            return (-1, None)
        leaf = names[-1][len(base):]
        m = _CHAIN_NAME_RE.match(leaf)
        if not m:
            raise RuntimeError("legacy time-named records in " + base)
        rec = json.loads(self.objects[names[-1]].decode())
        return (int(m.group(1)), rec)

    def append_chained(self, key, seq, line):
        from store import ChainConflict, _chain_name
        import threading
        lock = self.__dict__.setdefault("_chain_lock", threading.Lock())
        name = key + "/" + _chain_name(seq)
        data = (line.rstrip("\n") + "\n").encode()
        with lock:                                  # 模擬伺服器端的原子條件建立
            if name in self.objects:
                if self.objects[name] == data:
                    return False
                raise ChainConflict(name)
            self.objects[name] = data
            return True


def main():
    import store as st
    fake = FakeObjectStore()
    st.reset_store(fake)

    import importlib
    import api_flywheel as fw
    importlib.reload(fw)          # 讓 FLYWHEEL_DIR 等常數重新綁定
    st.reset_store(fake)          # reload 可能清掉，重設

    POLY = [[10, 10], [200, 10], [200, 200], [10, 200]]
    POLY2 = [[12, 12], [205, 10], [200, 205], [10, 200], [8, 100]]
    IID = "aaaabbbbccccdddd"
    BASE = {"code": "WD-UT9001", "gt_polygon": POLY, "exudate": 2,
            "doctor_verified": True, "deidentified": True, "consent_train": True,
            "image_id": IID, "image_w": 640, "image_h": 480, "source": "clinical"}

    # 影像先進儲存（模擬 classify）
    fake.put_blob("images/%s.jpg" % IID, b"\xff\xd8fake-jpeg\xff\xd9")
    fake.put_blob("receipts/legacy_ratification.json", json.dumps({
        "schema": "woundai.legacy-ratification/1", "image_ids": [IID],
        "approved_by": "SYNTHETIC TEST FIXTURE - NOT AN OWNER APPROVAL"}).encode())

    # 1 附加與讀回
    fw.append_jsonl(fw.QUEUE, {**BASE, "received_at": "2026-08-03T10:00:00Z"})
    recs = fw.read_jsonl(fw.QUEUE)
    check("1  附加後讀得回來", len(recs) == 1 and recs[0]["code"] == "WD-UT9001", len(recs))

    # 2 順序：物件儲存沒有 append，順序必須靠鍵名還原
    for i in range(2, 6):
        fw.append_jsonl(fw.QUEUE, {**BASE, "code": "WD-UT900%d" % i,
                                   "received_at": "2026-08-03T10:0%d:00Z" % i})
    codes = [r["code"] for r in fw.read_jsonl(fw.QUEUE)]
    check("2  多筆附加維持時間順序", codes == ["WD-UT900%d" % i for i in range(1, 6)], codes)

    # 3 重複偵測（跨物件讀取）
    exact, same = fw.find_duplicate(fw.QUEUE, IID, POLY)
    check("3  同影像同遮罩判為重複", exact is not None and len(same) == 5)
    exact2, _ = fw.find_duplicate(fw.QUEUE, IID, POLY2)
    check("3b 同影像不同遮罩不算重複（是修訂）", exact2 is None)

    # 4 影像存在性走儲存層
    recs, stats = fw.effective_queue()
    check("4  可訓練樣本計得出來", stats["trainable"] == 1 and stats["superseded"] == 4, stats)

    # 5 缺影像 → image_file_missing（而不是靜默算成可訓練）
    fw.append_jsonl(fw.QUEUE, {**BASE, "code": "WD-NOIMG", "image_id": "ffffffffffffffff",
                               "received_at": "2026-08-03T11:00:00Z"})
    _, stats = fw.effective_queue()
    check("5  影像不存在被算進 image_file_missing", stats["image_file_missing"] == 1, stats)

    # 6 隔離：物件儲存沒有 rename，move 必須是複製再刪除
    moved = fw.quarantine_image(IID)
    check("6  隔離影像成功", moved)
    check("6b 影像已離開 images/", not fake.exists("images/%s.jpg" % IID))
    check("6c 影像在 quarantine/", fake.exists("quarantine/%s.jpg" % IID))
    check("6d is_quarantined 認得出來", fw.is_quarantined(IID))

    # 7 撤回墓碑重播（withdraw → restore 的順序語意）
    fw.append_jsonl(fw.WITHDRAWN, {"code": "WD-UT9001", "image_ids": [IID], "action": "withdraw"})
    codes_w, imgs_w = fw.withdrawn_keys()
    check("7  撤回後 code 與 image 都被排除", "WD-UT9001" in codes_w and IID in imgs_w)
    fw.append_jsonl(fw.WITHDRAWN, {"code": "WD-UT9001", "image_ids": [IID], "action": "restore"})
    codes_r, imgs_r = fw.withdrawn_keys()
    check("7b restore 後排除解除（順序重播正確）",
          "WD-UT9001" not in codes_r and IID not in imgs_r)

    # 8 稽核軌跡
    fw.audit("test:tester", "unit_test", "WD-UT9001", "ok", "test", "test")
    ad = fw.read_jsonl(fw.AUDIT)
    check("8  稽核軌跡寫得進、讀得出", len(ad) == 1 and ad[0]["action"] == "unit_test")

    # 9 壞行不靜默消失
    fake.append_line("retrain_queue.jsonl", "{ 這不是合法 JSON")
    _, bad = fw.read_jsonl(fw.QUEUE, with_bad=True)
    check("9  格式損壞的紀錄被計數而非略過", bad == 1, bad)

    st.reset_store(None)
    print()
    if FAILED:
        print("FAILED %d 項：%s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("全部通過：換成物件儲存語意後，飛輪行為與本機檔案版一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
