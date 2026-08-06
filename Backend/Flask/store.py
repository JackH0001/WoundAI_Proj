# -*- coding: utf-8 -*-
"""飛輪執行期資料的儲存抽象：本機檔案 ↔ 雲端物件儲存。

## 為什麼需要這一層

`api_flywheel.py` 原本直接對本機檔案系統做 `open(..., "a")` 與 `shutil.move`。
在單機上完全沒問題，但要放上 **Cloud Run** 就會壞得很安靜：

- Cloud Run 的容器檔案系統是**暫時的**。實例回收（閒置縮到零、部署新版、當機重啟）
  之後，`flywheel/images/` 與 `retrain_queue.jsonl` 全部消失——而且不會有任何錯誤，
  只是統計數字某天突然歸零。醫師會以為是自己送錯了。
- 多實例時每個容器看到不同的檔案系統：A 實例收的標註，B 實例查不到影像。

所以在上雲之前必須先有這一層。**先抽介面，不要先搬家**——本機開發與 CI 完全不受影響，
上雲只是換一個實作。反過來先搬到雲上再抽介面，本機就從此跑不起來了。

## 兩種實作

| | `LocalStore` | `GcsStore` |
|---|---|---|
| 用在 | 本機開發、CI、未來的 Mac mini | Cloud Run |
| jsonl 附加 | 直接 `open(..., "a")` | **一筆一個物件**（見下） |
| 影像 | 檔案 | 物件 |

### 為什麼 GCS 是「一筆一個物件」而不是附加同一個檔

物件儲存的物件是**不可變**的，沒有 append。要模擬附加只有兩條路：

1. 讀出整個檔、加一行、整個寫回 —— 這是 read-modify-write，兩個實例同時送標註就會有一筆
   靜默消失。在**稽核軌跡**上遺失紀錄是不可接受的。
2. 每筆寫成一個獨立物件，鍵名前綴是時間戳 —— 沒有競態，讀取時把前綴底下的物件依名稱
   排序串起來即可。

選 2。鍵名用 `<20位奈秒時間戳>_<8碼隨機>.jsonl`，**字典序＝時間序**，這正是
`withdrawn_keys()` 依賴的「依檔案順序重播 withdraw/restore」語意。

⚠ **誠實邊界**：跨實例的時間戳來自各自的系統時鐘（NTP 同步，偏差通常在毫秒級）。
同一奈秒的兩筆會由隨機碼決定順序。對「撤回同意後又重新簽署」這種相隔數分鐘以上的操作
綽綽有餘；若日後出現亞毫秒級的順序相依，要改用資料庫的序號而不是時間戳。

## 設定

    WOUNDAI_STORE=local            # 預設
    WOUNDAI_STORE=gcs
    WOUNDAI_GCS_BUCKET=my-bucket   # gcs 時必填
    WOUNDAI_GCS_PREFIX=flywheel    # 選填，預設 flywheel
    WOUNDAI_AUDIT_BUCKET=my-audit  # 選填。設了的話 audit.jsonl 改寫入這個桶
                                   # （該桶應設保留政策/WORM，見 harden_bucket.ps1）
"""
import io
import json
import os
import shutil
import time
import uuid


class Store:
    """飛輪儲存介面。key 一律是相對於飛輪根目錄的 POSIX 風格路徑，例如
    `retrain_queue.jsonl`、`images/70e4e34b.jpg`、`quarantine/70e4e34b.jpg`。"""

    def append_line(self, key: str, line: str) -> None: raise NotImplementedError
    def read_lines(self, key: str): raise NotImplementedError
    def put_blob(self, key: str, data: bytes) -> None: raise NotImplementedError
    def get_blob(self, key: str): raise NotImplementedError
    def exists(self, key: str) -> bool: raise NotImplementedError
    def move(self, src: str, dst: str) -> bool: raise NotImplementedError
    def list_keys(self, prefix: str): raise NotImplementedError
    def describe(self) -> str: raise NotImplementedError


def _record_name() -> str:
    """時間戳前綴的物件名。20 位零填充，確保字典序＝時間序（不補零的話 '9' > '10'）。"""
    return "%020d_%s.jsonl" % (time.time_ns(), uuid.uuid4().hex[:8])


class LocalStore(Store):
    """本機檔案。維持與抽象化之前**完全相同**的磁碟版面，既有資料不需要遷移。"""

    def __init__(self, root: str):
        self.root = root

    def _p(self, key: str) -> str:
        # 絕對路徑原樣使用：測試與匯出腳本會直接指定暫存目錄，
        # 硬把它拼到 root 底下會讓它們讀到產線資料。
        if os.path.isabs(key):
            return key
        return os.path.join(self.root, *key.split("/"))

    def append_line(self, key: str, line: str) -> None:
        p = self._p(key)
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(line.rstrip("\n") + "\n")

    def read_lines(self, key: str):
        p = self._p(key)
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as f:
            return [ln.rstrip("\n") for ln in f]

    def put_blob(self, key: str, data: bytes) -> None:
        p = self._p(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)

    def get_blob(self, key: str):
        p = self._p(key)
        if not os.path.exists(p):
            return None
        with open(p, "rb") as f:
            return f.read()

    def exists(self, key: str) -> bool:
        return os.path.exists(self._p(key))

    def move(self, src: str, dst: str) -> bool:
        s, d = self._p(src), self._p(dst)
        if not os.path.exists(s):
            return False
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.move(s, d)
        return True

    def list_keys(self, prefix: str):
        p = self._p(prefix)
        if not os.path.isdir(p):
            return []
        return ["%s/%s" % (prefix.rstrip("/"), n) for n in sorted(os.listdir(p))]

    def describe(self) -> str:
        return "local:%s" % self.root


class GcsStore(Store):
    """Google Cloud Storage。給 Cloud Run 用——容器重啟不掉資料，多實例看到同一份。

    刻意**延後 import** `google.cloud.storage`：本機開發不必安裝這個套件，
    也不會因為缺它而讓整個模組 import 失敗。
    """

    # 走**獨立稽核桶**的鍵。
    #
    # 為什麼要分桶：GCS 的保留政策（WORM）是桶層級，無法只套在某個前綴上。
    # 套在主桶會連影像一起鎖住——而影像必須刪得掉（撤回同意、保存期限）。
    # 稽核軌跡則相反，它必須刪不掉。兩種需求互斥，只能分桶。
    #
    # 沒有這段路由的話，稽核桶就只是一個空的、有保留政策的桶——看起來合規，
    # 實際上稽核紀錄還是寫在刪得掉的主桶裡。
    AUDIT_KEYS = ("audit.jsonl",)

    def __init__(self, bucket: str, prefix: str = "flywheel", audit_bucket: str = None):
        from google.cloud import storage  # noqa: 延後 import，見 docstring
        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket)
        self._bucket_name = bucket
        self.prefix = prefix.strip("/")
        self._audit_bucket_name = audit_bucket or None
        self._audit_bucket = self._client.bucket(audit_bucket) if audit_bucket else None
        # append-only 鍵的增量快取：{(bucket, base): {"names": [...], "lines": [...]}}
        #
        # 為什麼需要：每一次 append 是一個獨立物件（見 append_line 的理由），
        # 所以讀一份一萬筆的稽核軌跡＝**一萬次 GET**。主控台每開一次頁就付這個代價。
        # 而這些鍵是 append-only 的，舊物件的內容不會變 —— 已經讀過的就不必再讀。
        #
        # 快取只在同一個 Cloud Run 實例內有效；列舉仍然每次都做，
        # 所以其他實例寫入的新紀錄一定看得到（正確性不依賴快取）。
        self._line_cache = {}
        self._CACHE_MAX_LINES = 200_000

    def _is_audit(self, key: str) -> bool:
        k = key.strip("/")
        return any(k == a or k.startswith(a + "/") for a in self.AUDIT_KEYS)

    def _target(self, key: str):
        """(bucket 物件, bucket 名稱)。稽核鍵在有設定稽核桶時走那一個。"""
        if self._audit_bucket is not None and self._is_audit(key):
            return self._audit_bucket, self._audit_bucket_name
        return self._bucket, self._bucket_name

    def _k(self, key: str) -> str:
        return "%s/%s" % (self.prefix, key.strip("/")) if self.prefix else key.strip("/")

    def append_line(self, key: str, line: str) -> None:
        # 一筆一個物件。不做 read-modify-write：那會在多實例下靜默吃掉紀錄，
        # 而這條路徑同時承載稽核軌跡，遺失是不可接受的。
        bucket, _ = self._target(key)
        name = "%s/%s" % (self._k(key), _record_name())
        bucket.blob(name).upload_from_string(
            line.rstrip("\n") + "\n", content_type="application/json")

    def read_lines(self, key: str):
        # 物件名以零填充的奈秒時間戳開頭 → 字典序＝時間序，
        # 這正是 withdraw/restore 重播所依賴的順序。
        _, bname = self._target(key)
        base = self._k(key) + "/"
        blobs = sorted(self._client.list_blobs(bname, prefix=base),
                       key=lambda b: b.name)
        names = [b.name for b in blobs]

        # 增量讀取：只下載沒讀過的物件。
        #
        # ⚠ 只有在快取的名稱清單是目前清單的**前綴**時才成立。
        # 不是前綴就代表有物件被刪除或改名 —— append-only 的前提被打破了，
        # 這時寧可整份重讀，也不要拿一份可能已經對不上的紀錄去驗雜湊鏈。
        ck = (bname, base)
        c = self._line_cache.get(ck)
        start, out = 0, []
        if c and c["names"] and len(c["names"]) <= len(names) \
                and names[:len(c["names"])] == c["names"]:
            start, out = len(c["names"]), list(c["lines"])

        for b in blobs[start:]:
            try:
                text = b.download_as_bytes().decode("utf-8")
            except Exception:
                # 讀不到就整份放棄快取：快取裡少一筆會讓之後每次都算在對的位置上，
                # 而稽核鏈對「少一筆」的表現是後面全部 broken_link —— 假警報比慢更糟。
                self._line_cache.pop(ck, None)
                continue
            out.extend([ln for ln in text.split("\n") if ln.strip()])

        if len(out) <= self._CACHE_MAX_LINES:
            self._line_cache[ck] = {"names": names, "lines": list(out)}
        else:
            self._line_cache.pop(ck, None)
        return out

    def put_blob(self, key: str, data: bytes) -> None:
        bucket, _ = self._target(key)
        # content_type 依副檔名決定。寫死 image/jpeg 的話，組織遮罩 PNG 在 GCS 上
        # 會被標成 JPEG——`gcloud storage cp` 下載沒問題，但瀏覽器預覽與任何依
        # metadata 分派的工具都會解錯，而檔案本身是好的，症狀會很難歸因。
        ct = "image/png" if self._k(key).lower().endswith(".png") else "image/jpeg"
        bucket.blob(self._k(key)).upload_from_file(io.BytesIO(data), content_type=ct)

    def get_blob(self, key: str):
        bucket, _ = self._target(key)
        b = bucket.blob(self._k(key))
        if not b.exists():
            return None
        return b.download_as_bytes()

    def exists(self, key: str) -> bool:
        bucket, _ = self._target(key)
        return bucket.blob(self._k(key)).exists()

    def move(self, src: str, dst: str) -> bool:
        # 稽核鍵不該被 move；真的發生就是有人想搬走軌跡，讓它明確失敗而不是靜默照做。
        if self._is_audit(src) or self._is_audit(dst):
            raise PermissionError("稽核軌跡不可搬移")
        s = self._bucket.blob(self._k(src))
        if not s.exists():
            return False
        # 物件儲存沒有 rename：先複製再刪除。**順序不可顛倒**——
        # 先刪再複製的話，中途失敗就是影像永久消失。
        self._bucket.copy_blob(s, self._bucket, self._k(dst))
        s.delete()
        return True

    def list_keys(self, prefix: str):
        _, bname = self._target(prefix)
        base = self._k(prefix).rstrip("/") + "/"
        n = len(self.prefix) + 1 if self.prefix else 0
        return sorted(b.name[n:] for b in self._client.list_blobs(bname, prefix=base))

    def describe(self) -> str:
        d = "gcs://%s/%s" % (self._bucket_name, self.prefix)
        if self._audit_bucket_name:
            d += " (稽核→gcs://%s，WORM)" % self._audit_bucket_name
        return d


_ACTIVE = None


def get_store(root: str = None) -> Store:
    """依環境變數挑實作。root 只給 LocalStore 用（相容既有的 WOUNDAI_FLYWHEEL_DIR）。"""
    global _ACTIVE
    if _ACTIVE is not None:
        return _ACTIVE
    kind = (os.environ.get("WOUNDAI_STORE") or "local").lower()
    if kind == "gcs":
        bucket = os.environ.get("WOUNDAI_GCS_BUCKET")
        if not bucket:
            raise RuntimeError("WOUNDAI_STORE=gcs 但缺 WOUNDAI_GCS_BUCKET")
        # 稽核桶為選填。沒設就退回主桶——功能正常，但稽核軌跡是刪得掉的，
        # describe() 會誠實反映這件事（不會顯示 WORM 字樣）。
        _ACTIVE = GcsStore(bucket, os.environ.get("WOUNDAI_GCS_PREFIX", "flywheel"),
                           os.environ.get("WOUNDAI_AUDIT_BUCKET") or None)
    else:
        _ACTIVE = LocalStore(root or os.environ.get("WOUNDAI_FLYWHEEL_DIR") or "flywheel")
    return _ACTIVE


def reset_store(store: Store = None):
    """測試用：換掉或清掉目前的實作。"""
    global _ACTIVE
    _ACTIVE = store
