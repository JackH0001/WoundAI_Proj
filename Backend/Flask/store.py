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
import hashlib
import json
import os
import shutil
import tempfile
import re
import threading
import time
import uuid
from dataclasses import dataclass

from audit_chain_contract import AUDIT_CHAIN_FIELDS, CHAIN_FIELD_VERSIONS, CHAIN_V, \
    UNVERSIONED_CHAIN_VERSIONS, audit_hash, loads_json_object_strict, validate_current_record


# The final GCS audit epoch is deliberately a single, exact retention period.
# Keeping this policy with the low-level chained-write guard means callers
# cannot bypass api_flywheel.audit() and write a shorter locked chain directly.
AUDIT_RETENTION_SECONDS = 220903200


class Store:
    """飛輪儲存介面。key 一律是相對於飛輪根目錄的 POSIX 風格路徑，例如
    `retrain_queue.jsonl`、`images/70e4e34b.jpg`、`quarantine/70e4e34b.jpg`。"""

    # 稽核鍵清單。**定義在基底類別**，理由同下面的 `_is_audit`：
    # 「稽核不可刪」不該取決於今天跑在哪一種儲存後端。
    PROTECTED_KEYS = ("audit.jsonl", "receipts")
    AUDIT_KEYS = PROTECTED_KEYS  # compatibility for existing callers

    # ⚠ 這個判斷放在基底類別，不放在某一個實作裡。
    #
    # 先前它只定義在 `GcsStore` 上，而 `LocalStore` 沒有——本機模式下任何呼叫
    # `self._is_audit()` 的程式碼都會 AttributeError。加 `delete()` 時就踩到了：
    # 守衛寫好了，卻只在雲端生效，本機是**直接崩潰**。
    # 稽核不可刪這條規則不該取決於今天跑在哪一種儲存後端。
    def _is_audit(self, key: str) -> bool:
        k = (key or "").strip("/")
        return any(k == a.strip("/") or k.startswith(a.strip("/") + "/")
                   for a in self.PROTECTED_KEYS)

    def _is_audit_chain(self, key: str) -> bool:
        """True for the audit chain root and every object below its prefix."""
        k = (key or "").strip("/")
        return k == "audit.jsonl" or k.startswith("audit.jsonl/")

    def _is_audit_chain_root(self, key: str) -> bool:
        """Only this exact key may enter the dedicated chained writer."""
        return (key or "").strip("/") == "audit.jsonl"

    def get_json(self, key: str):
        data = self.get_blob(key)
        return None if data is None else json.loads(data.decode("utf-8"))

    def put_blob_immutable(self, key: str, data: bytes,
                           content_type: str = "image/jpeg") -> bool:
        """Create only; return False only for an identical existing object."""
        raise NotImplementedError

    def put_json_immutable(self, key: str, value: dict) -> bool:
        data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
        created = self.put_blob_immutable(key, data, "application/json")
        if self.get_json(key) != value:
            raise IOError("immutable JSON readback mismatch: " + key)
        return created

    def copy_immutable(self, src: str, dst: str) -> bool:
        """Never delete source, and arbitrate a destination conflict by bytes."""
        # Receipt/audit data must never be copied out of the protected bucket,
        # nor may a generic copy create an unvalidated object under it.
        if self._is_audit(src) or self._is_audit(dst):
            raise PermissionError("protected objects cannot be copied")
        data = self.get_blob(src)
        if data is None:
            raise FileNotFoundError(src)
        return self.put_blob_immutable(dst, data)

    def append_record_once(self, key: str, receipt_id: str, record: dict) -> bool:
        raise NotImplementedError

    # ── 稽核鏈的序列化寫入 ──────────────────────────────────────────────
    #
    # 為什麼需要:`audit()` 原本是「讀尾端 → 算 seq+1 與 prev → append」,讀與寫之間
    # 沒有原子性。多個寫入者讀到同一個尾端,就各自寫出同 prev 的紀錄——雜湊鏈上的 fork。
    # 正式稽核桶 675 筆裡有 26 處(2026-09-01 實查);8 執行緒 tight loop 可重現 81 處。
    #
    # 機制:把「鏈上第 seq 格」本身當成一個只能建立一次的物件。兩個寫入者搶同一格,
    # 只有一個能建立;另一個拿到衝突後重讀尾端、改搶下一格。這與 put_blob_immutable /
    # append_record_once 是同一套 if_generation_match=0 慣用法,不是新機制。
    #
    # 契約(兩個後端都必須滿足):**同一個 seq 至多被寫入一次**。
    def chain_tail(self, key: str):
        """回 (seq, 尾端紀錄 dict);空鏈回 (-1, None)。GCS 後端只 GET 尾端那一個物件。"""
        raise NotImplementedError

    def audit_snapshot_fresh(self, key: str):
        """Return one fresh `(tail_seq, tail, lines)` audit snapshot.

        Backends that can enumerate immutable chain slots should override this
        so validation and tail selection use the same snapshot.  The generic
        fallback preserves local-development behavior; the production GCS
        implementation below is the safety boundary for the locked epoch.
        """
        lines = self.read_lines_fresh(key)
        tail_seq, tail = self.chain_tail(key)
        return tail_seq, tail, lines

    def append_chained(self, key: str, seq: int, line: str) -> bool:
        """把 line 寫成鏈上第 seq 格,且只在該格尚不存在時。
        True=本次建立;False=該格已存在且位元組相同(自己先前「回應遺失」的重試);
        位元組不同 → ChainConflict(別的寫入者搶到了這一格)。"""
        raise NotImplementedError

    def retention_info(self) -> dict:
        return {"verified": False, "locked": False, "reason": "local storage is not WORM"}

    def append_line(self, key: str, line: str) -> None: raise NotImplementedError
    def read_lines(self, key: str): raise NotImplementedError
    def read_lines_fresh(self, key: str):
        """Read a complete current log, bypassing any backend cache if needed."""
        return self.read_lines(key)
    def put_blob(self, key: str, data: bytes) -> None: raise NotImplementedError
    def get_blob(self, key: str): raise NotImplementedError
    def exists(self, key: str) -> bool: raise NotImplementedError
    def move(self, src: str, dst: str) -> bool: raise NotImplementedError
    # ⚠ 刪除是為了**民眾版的撤回**（`DELETE /api/v1/lite/data/<anon_id>`）而加的。
    # 臨床側一律不用它：那邊的規則是「排除／撤回都不刪除，另寫一筆紀錄」，
    # 因為 append-only 的佇列本身就是稽核軌跡。兩者的差別不是潔癖：
    # 民眾提供的是自己的資料且有被遺忘的期待；臨床樣本背後是 IRB 與病歷。
    def delete(self, key: str) -> bool: raise NotImplementedError
    def list_keys(self, prefix: str): raise NotImplementedError
    def describe(self) -> str: raise NotImplementedError


def _record_name() -> str:
    """時間戳前綴的物件名。20 位零填充，確保字典序＝時間序（不補零的話 '9' > '10'）。"""
    return "%020d_%s.jsonl" % (time.time_ns(), uuid.uuid4().hex[:8])


class ImmutableConflict(ValueError):
    """The key exists with different content; callers must not overwrite it."""


class ChainConflict(ValueError):
    """鏈上這一格已被別的寫入者建立(內容不同)。呼叫端應重讀尾端後改搶下一格。"""


@dataclass(frozen=True)
class _VerifiedAuditPrefix:
    """Process-local proof that an immutable GCS prefix was fully verified."""
    bucket: str
    base: str
    count: int
    head_hash: str
    manifest_sha256: str


# 鏈格物件名:20 位零填充的 seq。字典序 = 數值序,且**不含底線**——
# 舊的時間戳命名 `<ns>_<rand>.jsonl` 含底線,兩者藉此區分。
_CHAIN_NAME_RE = re.compile(r"^(\d{20})\.jsonl$")


def _chain_name(seq: int) -> str:
    # `_CHAIN_NAME_RE` admits exactly 20 decimal digits.  Letting a direct
    # caller create a 21-digit slot would permanently poison a locked prefix:
    # subsequent readers correctly reject it as noncanonical.  bool is also
    # excluded even though it subclasses int in Python.
    if type(seq) is not int or not 0 <= seq < 10 ** 20:
        raise ValueError("chain seq must be an int in [0, 10**20)")
    return "%020d.jsonl" % seq


# LocalStore 的鏈鎖:以絕對路徑為鍵、行程內共用。本機模式只承諾**同一行程內**的序列化
# (開發伺服器與測試都是單行程多執行緒);跨行程的本機寫入不是支援情境,正式路徑是 GCS。
_CHAIN_LOCKS = {}
_CHAIN_LOCKS_GUARD = threading.Lock()


def _chain_lock_for(path: str):
    with _CHAIN_LOCKS_GUARD:
        # chain_tail() also takes this lock.  RLock lets append_chained() re-read
        # the tail while holding the same per-file lock, so readers never see a
        # partially appended JSON line.
        return _CHAIN_LOCKS.setdefault(path, threading.RLock())


def _chain_record_bytes(seq: int, line: str):
    """Validate one immutable chain-slot record before it can be created.

    A bad slot cannot be repaired after Bucket Lock.  Validate the slot/content
    identity and basic link/hash shape *before* the conditional create, instead
    of discovering the poison object on the next write.
    """
    _chain_name(seq)  # validates type and range
    if not isinstance(line, str):
        raise TypeError("chain record must be text")
    text = line[:-1] if line.endswith("\n") else line
    if "\n" in text or "\r" in text:
        raise ValueError("chain slot must contain exactly one JSON record")
    try:
        rec = loads_json_object_strict(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("chain slot must contain valid JSON") from exc
    validate_current_record(rec, seq)
    return rec, (text + "\n").encode("utf-8")


def _expected_chain_prev(tail):
    """Return the only predecessor hash a newly admitted slot may reference."""
    if isinstance(tail, dict) and isinstance(tail.get("hash"), str):
        return tail["hash"]
    return "GENESIS"


# Fail at startup if protected-prefix dispatch is accidentally weakened.
if not all(k and k == k.strip("/") for k in Store.PROTECTED_KEYS):
    raise RuntimeError("protected key names must be non-empty and slash-normalized")
if not Store()._is_audit("receipts/promotion/test.json"):
    raise RuntimeError("protected receipts prefix self-check failed")
if Store()._is_audit("receiptsX/test.json"):
    raise RuntimeError("protected receipts prefix boundary self-check failed")


class LocalStore(Store):
    """本機檔案。維持與抽象化之前**完全相同**的磁碟版面，既有資料不需要遷移。"""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)

    def _protected_identity(self, key: str) -> str:
        """Canonical LocalStore key for protection checks.

        Windows and common macOS volumes are case-insensitive.  The storage
        guard must therefore treat ``AUDIT.JSONL`` and ``audit.jsonl`` as the
        same destination even when tests happen to run on a case-sensitive
        filesystem.  GCS deliberately remains case-sensitive.
        """
        p = os.path.normcase(os.path.abspath(self._p(key)))
        root = os.path.normcase(self.root)
        return os.path.relpath(p, root).replace(os.sep, "/").casefold()

    def _is_audit(self, key: str) -> bool:
        # Absolute aliases inside root must not bypass the protected-key guard.
        return super()._is_audit(self._protected_identity(key))

    def _is_audit_chain(self, key: str) -> bool:
        return super()._is_audit_chain(self._protected_identity(key))

    def _is_audit_chain_root(self, key: str) -> bool:
        return super()._is_audit_chain_root(self._protected_identity(key))

    def _p(self, key: str) -> str:
        # 絕對路徑原樣使用：測試與匯出腳本會直接指定暫存目錄，
        # 硬把它拼到 root 底下會讓它們讀到產線資料。
        if os.path.isabs(key):
            return key
        return os.path.join(self.root, *key.split("/"))

    def append_line(self, key: str, line: str) -> None:
        # The audit chain has one writer: append_chained().  Letting the public
        # JSONL helper append a raw line bypasses seq/prev/hash validation.
        if self._is_audit(key):
            raise PermissionError("protected keys require their dedicated immutable writer")
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

    def chain_tail(self, key: str):
        p = os.path.abspath(self._p(key))
        with _chain_lock_for(p):
            lines = [ln for ln in self.read_lines(key) if ln.strip()]
            if not lines:
                return (-1, None)
            rec = json.loads(lines[-1])
            if not isinstance(rec, dict):
                raise IOError("audit tail must be a JSON object")
            # 舊紀錄可能沒有 seq(雜湊鏈導入前);退回以行序為 seq,與舊 audit() 一致。
            return (int(rec.get("seq", len(lines) - 1)), rec)

    def _current_chain_snapshot(self, key: str):
        """Return a fully validated local current-v4 chain under its file lock.

        The public primitive is used by development tools as well as API code.
        Checking only the last record would let a direct caller extend a
        self-hash-corrupt earlier history.  Local audit admission therefore uses
        the same strict v4 record and predecessor rules as the final GCS epoch.
        Historical local logs are deliberately not extended through this public
        primitive; the API verifier already fails closed for unauthenticated
        legacy records and a migration must be explicit.
        """
        previous = None
        records, data_by_seq = [], {}
        for seq, raw_line in enumerate(line for line in self.read_lines(key) if line.strip()):
            try:
                record, data = _chain_record_bytes(seq, raw_line)
            except (TypeError, ValueError, UnicodeDecodeError) as exc:
                raise IOError("invalid local audit chain slot %d: %s" % (seq, exc)) from exc
            if record["prev"] != _expected_chain_prev(previous):
                raise IOError("local audit chain broken link at seq %d" % seq)
            records.append(record)
            data_by_seq[seq] = data
            previous = record
        return (len(records) - 1, previous, data_by_seq)

    def append_chained(self, key: str, seq: int, line: str) -> bool:
        # 單一檔案、行程內鎖。鎖內完整驗證既有 current-v4 鏈，再要求 seq 恰為
        # tail+1——這就是本機版的「只在該格不存在時建立」:別的執行緒先寫了這一格,
        # tail 已推進,本次拋衝突。直接 primitive 不可在損壞歷史後繼續寫入。
        if not self._is_audit_chain_root(key):
            raise PermissionError("append_chained is reserved for audit.jsonl")
        p = os.path.abspath(self._p(key))
        rec, data = _chain_record_bytes(seq, line)
        with _chain_lock_for(p):
            tail_seq, tail, old_data_by_seq = self._current_chain_snapshot(key)
            if seq <= tail_seq:
                # A caller retrying an accepted write must be idempotent, but it
                # may never turn an old slot into a different event.
                if old_data_by_seq.get(seq) == data:
                    return False
                raise ChainConflict("%s: slot %d already differs" % (key, seq))
            if seq != tail_seq + 1:
                raise ChainConflict("%s: slot %d is not next (tail=%d)" % (key, seq, tail_seq))
            if rec["prev"] != _expected_chain_prev(tail):
                raise ChainConflict("%s: slot %d has wrong predecessor" % (key, seq))
            # Do not route this private chain write back through append_line():
            # append_line intentionally rejects every protected key.
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(data.decode("utf-8"))
            return True

    def put_blob(self, key: str, data: bytes) -> None:
        if self._is_audit(key):
            raise PermissionError("protected objects require immutable writes")
        p = self._p(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)

    def put_blob_immutable(self, key: str, data: bytes,
                           content_type: str = "image/jpeg") -> bool:
        if self._is_audit_chain(key):
            raise PermissionError("audit.jsonl must be written through append_chained")
        p = self._p(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        # Publish a fully flushed file with an exclusive hard link. No reader
        # can observe a partially written receipt, and a racing writer cannot
        # replace the winner (works on NTFS and the Linux deployment filesystem).
        fd, tmp = tempfile.mkstemp(prefix=".immutable-", dir=os.path.dirname(p))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, p)
            except FileExistsError:
                if self.get_blob(key) != data:
                    raise ImmutableConflict(key)
                return False
            return True
        finally:
            os.unlink(tmp)

    def append_record_once(self, key: str, receipt_id: str, record: dict) -> bool:
        if self._is_audit(key):
            raise PermissionError("protected keys cannot use queue receipt append")
        if record.get("annotation_receipt_id") != receipt_id:
            raise ValueError("queue receipt mismatch")
        p = self._p(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        # The lock file is separate so existing JSONL readers see only records.
        with open(p + ".lock", "a+b") as lock:
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                for line in self.read_lines(key):
                    row = json.loads(line)
                    if row.get("annotation_receipt_id") == receipt_id:
                        return False
                with open(p, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                return True
            finally:
                lock.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def get_blob(self, key: str):
        p = self._p(key)
        if not os.path.exists(p):
            return None
        with open(p, "rb") as f:
            return f.read()

    def exists(self, key: str) -> bool:
        return os.path.exists(self._p(key))

    def delete(self, key: str) -> bool:
        if self._is_audit(key):
            raise PermissionError("稽核軌跡不可刪除")
        p = self._p(key)
        if not os.path.exists(p):
            return False
        os.remove(p)
        return True

    def move(self, src: str, dst: str) -> bool:
        if self._is_audit(src) or self._is_audit(dst):
            raise PermissionError("protected objects cannot be moved")
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
    # （`AUDIT_KEYS` 已移到基底 `Store`，兩種後端共用同一份清單。）

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
        # Audit admission cache is separate from the generic line cache.  It is
        # populated only after an exact-v4 full verification in a locked
        # seven-year epoch.  Each later append still lists every slot name and
        # generation, but downloads only the suffix not yet verified by this
        # process.  Offline verification never uses this cache.
        self._audit_prefix_cache = {}
        self._audit_append_lock = threading.RLock()

    def _target(self, key: str):
        """(bucket 物件, bucket 名稱)。稽核鍵在有設定稽核桶時走那一個。"""
        if self._is_audit(key):
            if self._audit_bucket is None:
                raise RuntimeError("protected storage requires WOUNDAI_AUDIT_BUCKET")
            return self._audit_bucket, self._audit_bucket_name
        return self._bucket, self._bucket_name

    def _k(self, key: str) -> str:
        return "%s/%s" % (self.prefix, key.strip("/")) if self.prefix else key.strip("/")

    def append_line(self, key: str, line: str) -> None:
        # 一筆一個物件。不做 read-modify-write：那會在多實例下靜默吃掉紀錄，
        # 而這條路徑同時承載稽核軌跡，遺失是不可接受的。
        if self._is_audit(key):
            raise PermissionError("protected keys require their dedicated immutable writer")
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
                raise IOError("cannot read complete append-only log: " + key)
            out.extend([ln for ln in text.split("\n") if ln.strip()])

        if len(out) <= self._CACHE_MAX_LINES:
            self._line_cache[ck] = {"names": names, "lines": list(out)}
        else:
            self._line_cache.pop(ck, None)
        return out

    def read_lines_fresh(self, key: str):
        """Force a new list and download pass before an audit write.

        The normal incremental cache is safe only under an already-locked WORM
        epoch.  Audit admission must not use it: before Lock, a changed old
        object's name can remain a cache prefix while its bytes no longer match.
        """
        _, bname = self._target(key)
        base = self._k(key) + "/"
        self._line_cache.pop((bname, base), None)
        return self.read_lines(key)

    def audit_snapshot_fresh(self, key: str):
        """Read and validate all immutable audit slots from one GCS snapshot.

        `audit()` validates each record's versioned hash using the returned
        lines, then derives its next slot from the *same* list/download pass.
        That avoids a second, independently observed tail between verification
        and conditional creation.  Production admission separately requires a
        locked retention epoch, so a prior slot cannot change after this read.
        """
        if self._is_audit(key) and key.strip("/") != "audit.jsonl":
            raise PermissionError("receipts require immutable writes")
        bucket, bname = self._target(key)
        base = self._k(key) + "/"
        slots = {}
        for blob in self._client.list_blobs(bname, prefix=base):
            leaf = blob.name[len(base):]
            match = _CHAIN_NAME_RE.fullmatch(leaf)
            if not match:
                raise RuntimeError(
                    "audit prefix gs://%s/%s holds a legacy or non-slot object (%s); "
                    "point WOUNDAI_AUDIT_BUCKET at a clean bucket" % (bname, base, leaf))
            slot = int(match.group(1))
            if slot in slots:
                raise IOError("duplicate audit chain slot: %d" % slot)
            slots[slot] = blob

        if not slots:
            return (-1, None, [])
        max_seq = max(slots)
        if len(slots) != max_seq + 1:
            raise IOError("audit chain slots are not contiguous: count=%d max_seq=%d" %
                          (len(slots), max_seq))

        previous = None
        tail = None
        lines = []
        for slot in range(max_seq + 1):
            blob = slots[slot]
            try:
                record, data = _chain_record_bytes(slot, blob.download_as_bytes().decode("utf-8"))
            except (TypeError, ValueError, UnicodeDecodeError) as exc:
                raise IOError("invalid audit chain slot %s: %s" % (blob.name, exc)) from exc
            expected_prev = "GENESIS" if previous is None else previous["hash"]
            if record["prev"] != expected_prev:
                raise IOError("audit chain broken link at seq %d" % slot)
            lines.append(data.decode("utf-8").rstrip("\n"))
            previous = record
            tail = record
        return (max_seq, tail, lines)

    @staticmethod
    def _audit_manifest_sha256(blobs) -> str:
        digest = hashlib.sha256()
        for blob in blobs:
            generation = getattr(blob, "generation", None)
            if generation is None or str(generation) == "":
                raise IOError("audit slot listing omitted object generation: " + blob.name)
            digest.update(blob.name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(generation).encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()

    def _list_audit_slots_for_admission(self, key: str):
        """List the complete canonical slot manifest for one locked epoch."""
        if not self._is_audit_chain_root(key):
            raise PermissionError("audit admission is reserved for audit.jsonl")
        self.require_locked_audit_epoch()
        bucket, bname = self._target(key)
        base = self._k(key) + "/"
        # Materialise the iterator: a pagination/network failure must raise,
        # never look like a valid short prefix.
        blobs = sorted(list(self._client.list_blobs(bname, prefix=base)),
                       key=lambda blob: blob.name)
        for seq, blob in enumerate(blobs):
            expected = base + _chain_name(seq)
            if blob.name != expected:
                raise IOError("noncanonical or noncontiguous audit slot: expected %s got %s" %
                              (expected, blob.name))
        # Require generations even for an empty suffix comparison.  A locked
        # prefix changing generation is an integrity event, not a cache miss.
        self._audit_manifest_sha256(blobs)
        return bucket, bname, base, blobs

    def _verified_audit_prefix(self, key: str) -> _VerifiedAuditPrefix:
        """Cold-verify all slots; thereafter verify immutable manifest + suffix.

        A cached-prefix mismatch is never healed by silently doing a fresh full
        scan in the same call.  Existing generations cannot change in a locked
        WORM epoch; observing that change is itself a fail-closed event.
        """
        with self._audit_append_lock:
            cache_key = None
            try:
                # Establish the key before the network listing so *listing*
                # failures (pagination, malformed names, missing generations)
                # evict an earlier proof too.  Keeping it would let a later
                # transiently successful call reuse evidence across an
                # unobserved integrity event.
                _, expected_bname = self._target(key)
                expected_base = self._k(key) + "/"
                cache_key = (expected_bname, expected_base)
                bucket, bname, base, blobs = self._list_audit_slots_for_admission(key)
                if (bname, base) != cache_key:
                    raise IOError("audit target changed during admission")
                cached = self._audit_prefix_cache.get(cache_key)
                if cached is not None:
                    if len(blobs) < cached.count:
                        raise IOError("locked audit prefix shrank after verification")
                    observed_prefix = self._audit_manifest_sha256(blobs[:cached.count])
                    if observed_prefix != cached.manifest_sha256:
                        raise IOError("locked audit prefix generation/name manifest changed")
                    start = cached.count
                    previous_hash = cached.head_hash
                else:
                    start = 0
                    previous_hash = "GENESIS"

                for seq in range(start, len(blobs)):
                    blob = blobs[seq]
                    try:
                        raw = blob.download_as_bytes().decode("utf-8")
                        record, _ = _chain_record_bytes(seq, raw)
                    except (TypeError, ValueError, UnicodeDecodeError) as exc:
                        raise IOError("invalid audit chain slot %s: %s" %
                                      (blob.name, exc)) from exc
                    if record["prev"] != previous_hash:
                        raise IOError("audit chain broken link at seq %d" % seq)
                    previous_hash = record["hash"]

                state = _VerifiedAuditPrefix(
                    bucket=bname,
                    base=base,
                    count=len(blobs),
                    head_hash=previous_hash,
                    manifest_sha256=self._audit_manifest_sha256(blobs),
                )
                self._audit_prefix_cache[cache_key] = state
                return state
            except Exception:
                if cache_key is not None:
                    self._audit_prefix_cache.pop(cache_key, None)
                raise

    def _create_chained_slot(self, key: str, seq: int, line: str) -> bool:
        """Conditional-create one already-admitted slot; never list the chain."""
        from google.api_core.exceptions import (
            BadGateway, DeadlineExceeded, GatewayTimeout, InternalServerError,
            NotFound, PreconditionFailed, RetryError, ServiceUnavailable,
            TooManyRequests,
        )
        from requests.exceptions import ConnectionError as RequestsConnectionError
        from requests.exceptions import Timeout as RequestsTimeout
        response_may_be_lost = (
            BadGateway, DeadlineExceeded, GatewayTimeout, InternalServerError,
            RetryError, ServiceUnavailable, TooManyRequests,
            RequestsConnectionError, RequestsTimeout,
        )
        bucket, _ = self._target(key)
        name = self._k(key) + "/" + _chain_name(seq)
        _, data = _chain_record_bytes(seq, line)
        blob = bucket.blob(name)
        try:
            blob.upload_from_string(data, content_type="application/json",
                                    if_generation_match=0)
        except PreconditionFailed:
            if bucket.blob(name).download_as_bytes() == data:
                return False
            raise ChainConflict(name)
        except response_may_be_lost as upload_error:
            # The create may be durable even when its response was lost.
            try:
                existing = bucket.blob(name).download_as_bytes()
            except NotFound:
                # No durable object exists to arbitrate.  Preserve the original
                # transport/API failure rather than misreporting a readback 404.
                raise upload_error
            if existing == data:
                return False
            raise ChainConflict(name)
        if bucket.blob(name).download_as_bytes() != data:
            raise IOError("chain slot readback mismatch: " + name)
        return True

    def append_next_chained(self, key: str, record_builder):
        """Build and append the one record following a verified GCS prefix."""
        with self._audit_append_lock:
            state = self._verified_audit_prefix(key)
            record = record_builder(state.count, state.head_hash)
            if not isinstance(record, dict):
                raise TypeError("audit record builder must return an object")
            line = json.dumps(record, ensure_ascii=False, allow_nan=False)
            admitted, _ = _chain_record_bytes(state.count, line)
            if admitted["prev"] != state.head_hash:
                raise ChainConflict("new audit record has wrong predecessor")
            self._create_chained_slot(key, state.count, line)
            # Do not advance the cache optimistically: the next admission must
            # observe the service-returned generation and verify this slot as a
            # suffix.  This keeps response-lost and external-writer paths equal.
            return record

    def put_blob(self, key: str, data: bytes) -> None:
        if self._is_audit(key):
            raise PermissionError("protected objects require immutable writes")
        bucket, _ = self._target(key)
        # content_type 依副檔名決定。寫死 image/jpeg 的話，組織遮罩 PNG 在 GCS 上
        # 會被標成 JPEG——`gcloud storage cp` 下載沒問題，但瀏覽器預覽與任何依
        # metadata 分派的工具都會解錯，而檔案本身是好的，症狀會很難歸因。
        ct = "image/png" if self._k(key).lower().endswith(".png") else "image/jpeg"
        bucket.blob(self._k(key)).upload_from_file(io.BytesIO(data), content_type=ct)

    def put_blob_immutable(self, key: str, data: bytes,
                           content_type: str = "image/jpeg") -> bool:
        from google.api_core.exceptions import PreconditionFailed
        if self._is_audit_chain(key):
            raise PermissionError("audit.jsonl must be written through append_chained")
        if self._is_audit(key):
            # Promotion/care receipts are audit evidence too.  They must not
            # be admitted into an unlocked bucket merely because they are not
            # chain slots.
            self.require_locked_audit_epoch()
        bucket, _ = self._target(key)
        blob = bucket.blob(self._k(key))
        try:
            blob.upload_from_string(data, content_type=content_type,
                                    if_generation_match=0)
        except PreconditionFailed:
            if self.get_blob(key) != data:
                raise ImmutableConflict(key)
            return False
        if self.get_blob(key) != data:
            raise IOError("immutable blob readback mismatch: " + key)
        return True

    def append_record_once(self, key: str, receipt_id: str, record: dict) -> bool:
        import re
        from google.api_core.exceptions import PreconditionFailed
        if self._is_audit(key):
            raise PermissionError("protected keys cannot use queue receipt append")
        if not re.fullmatch(r"[0-9a-f]{16}", receipt_id):
            raise ValueError("invalid annotation receipt id")
        if record.get("annotation_receipt_id") != receipt_id:
            raise ValueError("queue receipt mismatch")
        bucket, _ = self._target(key)
        blob = bucket.blob(self._k(key) + "/receipt_" + receipt_id + ".jsonl")
        data = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
        try:
            blob.upload_from_string(data, content_type="application/json",
                                    if_generation_match=0)
            return True
        except PreconditionFailed:
            old = json.loads(blob.download_as_bytes())
            if old.get("annotation_receipt_id") != receipt_id:
                raise ImmutableConflict(key)
            return False

    def chain_tail(self, key: str):
        tail_seq, tail, _ = self.audit_snapshot_fresh(key)
        return (tail_seq, tail)

    def append_chained(self, key: str, seq: int, line: str) -> bool:
        """Public direct admission; one verified-prefix pass, never caller tail."""
        from google.api_core.exceptions import NotFound
        if not self._is_audit_chain_root(key):
            raise PermissionError("append_chained is reserved for audit.jsonl")
        with self._audit_append_lock:
            state = self._verified_audit_prefix(key)
            rec, data = _chain_record_bytes(seq, line)
            name = self._k(key) + "/" + _chain_name(seq)
            bucket, _ = self._target(key)
            if seq < state.count:
                try:
                    existing = bucket.blob(name).download_as_bytes()
                except NotFound:
                    raise ChainConflict("%s: verified prefix lacks slot %d" % (key, seq))
                if existing == data:
                    return False
                raise ChainConflict(name)
            if seq != state.count:
                raise ChainConflict("%s: slot %d is not next (count=%d)" %
                                    (key, seq, state.count))
            if rec["prev"] != state.head_hash:
                raise ChainConflict("%s: slot %d has wrong predecessor" % (key, seq))
            return self._create_chained_slot(key, seq, line)

    def retention_info(self) -> dict:
        if self._audit_bucket is None:
            return {"verified": False, "locked": False, "reason": "audit bucket missing"}
        try:
            self._audit_bucket.reload()
            policy = self._audit_bucket._properties.get("retentionPolicy", {})
            return {"verified": True, "bucket": self._audit_bucket_name,
                    "retention_seconds": int(policy.get("retentionPeriod", 0)),
                    "locked": policy.get("isLocked") is True}
        except Exception:
            return {"verified": False, "locked": False, "reason": "readback failed"}

    def require_locked_audit_epoch(self) -> None:
        """Fail closed for every direct GCS audit-chain append path."""
        info = self.retention_info()
        retention = int(info.get("retention_seconds") or 0)
        if (info.get("verified") is True and info.get("locked") is True
                and retention == AUDIT_RETENTION_SECONDS):
            return
        raise PermissionError(
            "GCS audit writes require a verified, locked 7-year retention epoch")

    def get_blob(self, key: str):
        bucket, _ = self._target(key)
        b = bucket.blob(self._k(key))
        if not b.exists():
            return None
        return b.download_as_bytes()

    def exists(self, key: str) -> bool:
        bucket, _ = self._target(key)
        return bucket.blob(self._k(key)).exists()

    def delete(self, key: str) -> bool:
        # 與 move 同一條守則：稽核鍵不可刪，而且要**明確失敗**而不是靜默照做。
        if self._is_audit(key):
            raise PermissionError("稽核軌跡不可刪除")
        bucket, _ = self._target(key)
        b = bucket.blob(self._k(key))
        if not b.exists():
            return False
        b.delete()
        return True

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
            d += " (稽核→gcs://%s; retention must be read back)" % self._audit_bucket_name
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
