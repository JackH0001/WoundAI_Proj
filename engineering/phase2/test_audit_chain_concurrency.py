# -*- coding: utf-8 -*-
"""稽核鏈寫入序列化的測試。

背景:`audit()` 原本是「讀全部 → 算 seq+1 → append」,讀寫之間沒有原子性。正式稽核桶
675 筆裡有 26 處 fork(2026-09-01 實查);8 執行緒 tight loop 可重現 81 處。
修法:鏈上第 seq 格本身是一個只能建立一次的物件(GCS `if_generation_match=0`;
本機為行程內鎖 + tail 檢查),搶輸的重讀尾端改搶下一格。

這組測試同時釘住兩件事:
  A. 並行寫入**不再**產生 fork(正向控制:修法前同一測試會產生數十處 fork——
     設計文件 §1 表格保留了那組數字);
  B. 序列化沒有把正確性換掉——衝突路徑、位元組仲裁、舊命名守門、用盡重試,
     每一條都要以**正確的方式**失敗或成功,不得靜默。

    python engineering/phase2/test_audit_chain_concurrency.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))
sys.path.insert(0, BACKEND)

FAILED = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


# ── 一個忠實模擬 GCS 條件建立語意的假件 ──────────────────────────────────
class _PreconditionFailed(Exception):
    pass


class _FakeBlob:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def upload_from_string(self, data, content_type=None, if_generation_match=None):
        if isinstance(data, str):
            data = data.encode("utf-8")
        with self.store.lock:
            self.store.calls.append(("upload", self.name, if_generation_match))
            if if_generation_match == 0 and self.name in self.store.objects:
                raise self.store.PreconditionFailed("exists: " + self.name)
            self.store.objects[self.name] = data

    def download_as_bytes(self):
        with self.store.lock:
            self.store.calls.append(("download", self.name, None))
            if self.name not in self.store.objects:
                raise KeyError(self.name)
            return self.store.objects[self.name]


class _FakeBucket:
    def __init__(self, store):
        self.store = store

    def blob(self, name):
        return _FakeBlob(self.store, name)


class _FakeGcs:
    """objects: name -> bytes。list_blobs 回依名稱排序的物件(與 GCS 一致)。"""

    def __init__(self, PreconditionFailed):
        self.objects, self.calls = {}, []
        self.lock = threading.Lock()
        self.PreconditionFailed = PreconditionFailed

    def list_blobs(self, bucket_name, prefix=""):
        with self.lock:
            names = sorted(k for k in self.objects if k.startswith(prefix))
        return [_FakeBlob(self, n) for n in names]

    def bucket(self, name):
        return _FakeBucket(self)


def make_gcs_store(fake):
    """比照 test_p0_4_staging 的做法:繞過 __init__ 直接組一個 GcsStore。"""
    import store as st
    g = st.GcsStore.__new__(st.GcsStore)
    g._client = fake
    g._bucket, g._audit_bucket = fake.bucket("main"), fake.bucket("audit")
    g._bucket_name, g._audit_bucket_name = "synthetic-main", "synthetic-audit"
    g.prefix = "flywheel"
    g._line_cache, g._CACHE_MAX_LINES = {}, 200_000
    return g


def main():
    from google.api_core.exceptions import PreconditionFailed, ServiceUnavailable
    import store as st

    # ══ A. 本機後端:並行寫入不再 fork ═══════════════════════════════════
    tmp = tempfile.mkdtemp(prefix="woundai_chaincc_")
    os.environ["WOUNDAI_FLYWHEEL_DIR"] = tmp
    os.environ.pop("WOUNDAI_STORE", None)
    for d in ("images", "quarantine"):
        os.makedirs(os.path.join(tmp, d), exist_ok=True)
    for f in ("retrain_queue.jsonl", "withdrawn.jsonl", "audit.jsonl"):
        open(os.path.join(tmp, f), "w").close()
    st.reset_store(None)
    import importlib
    import api_flywheel as fw
    importlib.reload(fw)
    st.reset_store(None)

    N_THREADS, N_EACH = 8, 25          # 8 = Cloud Run --threads 8
    barrier = threading.Barrier(N_THREADS)
    errors = []

    def worker(t):
        try:
            barrier.wait()
            for i in range(N_EACH):
                fw.audit("default:t%d" % t, "record_preview", "WD-%02d%03d" % (t, i),
                         "ok", "admin", "default")
        except Exception as e:      # 任何例外都要浮上來,不能被執行緒吞掉
            errors.append(repr(e))

    ths = [threading.Thread(target=worker, args=(t,)) for t in range(N_THREADS)]
    [x.start() for x in ths]
    [x.join() for x in ths]

    recs = fw.read_jsonl(fw.AUDIT)
    seqs = [r["seq"] for r in recs]
    ok, issues, stats = fw.verify_audit_chain()
    total = N_THREADS * N_EACH
    check("A1 執行緒無例外", not errors, errors[:3])
    check("A2 筆數 = %d(沒有遺失)" % total, len(recs) == total, len(recs))
    check("A3 seq 無重複、連續 0..%d" % (total - 1),
          sorted(seqs) == list(range(total)), (min(seqs), max(seqs), len(set(seqs))))
    check("A4 fork = 0", stats["kinds"].get("fork", 0) == 0, stats["kinds"])
    check("A5 broken_link = 0", stats["kinds"].get("broken_link", 0) == 0)
    check("A6 鏈驗證整體通過(real_issues = 0)", ok and stats["real_issues"] == 0)
    check("A7 每筆帶 chain_v=%d 與 32-hex nonce" % fw.CHAIN_V,
          all(r.get("chain_v") == fw.CHAIN_V and len(r.get("nonce", "")) == 32 for r in recs))
    check("A8 nonce 全部相異", len({r["nonce"] for r in recs}) == total)

    # ══ B. GCS 後端(假件):條件建立、仲裁、守門、用盡重試 ══════════════════
    fake = _FakeGcs(PreconditionFailed)
    g = make_gcs_store(fake)
    st.reset_store(g)

    # B0 不可變物件建立前先驗 slot/content。Bucket Lock 後才發現就已無法修復。
    for bad_line in (
            json.dumps({"seq": 9, "hash": "a" * 64, "prev": "GENESIS"}),
            json.dumps({"seq": 0, "hash": "a" * 64, "prev": "GENESIS"}) + "\n{}"):
        try:
            g.append_chained("audit.jsonl", 0, bad_line)
            check("B0 壞 slot 內容在寫入前被拒", False, "沒有拋例外")
        except ValueError:
            check("B0 壞 slot 內容在寫入前被拒", True)
    check("B0b 拒絕後桶仍為空", not fake.objects, sorted(fake.objects))

    # B1 首筆:空前綴 → seq 0、prev GENESIS,物件名為 20 位零填充
    r0 = fw.audit("default:admin", "record_preview", "WD-G0", "ok", "admin", "default")
    names = sorted(fake.objects)
    check("B1 首筆落在 flywheel/audit.jsonl/00000000000000000000.jsonl",
          names == ["flywheel/audit.jsonl/00000000000000000000.jsonl"], names)
    check("B1b seq=0, prev=GENESIS", r0["seq"] == 0 and r0["prev"] == "GENESIS")
    check("B1c 以 if_generation_match=0 建立",
          any(c == ("upload", names[0], 0) for c in fake.calls))

    # B2 第二筆:prev 必須等於首筆的 hash;寫入前完整驗過既有鏈。
    fake.calls.clear()
    r1 = fw.audit("default:admin", "record_preview", "WD-G1", "ok", "admin", "default")
    downloads_before_upload = [c for c in fake.calls if c[0] == "download"
                               and fake.calls.index(c) < next(i for i, c2 in enumerate(fake.calls) if c2[0] == "upload")]
    check("B2 prev 鏈到首筆 hash", r1["prev"] == r0["hash"] and r1["seq"] == 1)
    check("B2b 寫入前驗過既有鏈與尾端(不接受孤立的有效尾端)",
          len(downloads_before_upload) >= 2, len(downloads_before_upload))

    # B3 別人搶到這一格:第一次 upload 撞 412 且內容不同 → 必須重讀並以 seq+1 落地
    fake.calls.clear()
    intruder_name = "flywheel/audit.jsonl/00000000000000000002.jsonl"
    original_upload = _FakeBlob.upload_from_string
    state = {"injected": False}

    def racing_upload(self, data, content_type=None, if_generation_match=None):
        # 在第一次嘗試建立 slot 2 之前,讓「別的實例」先把 slot 2 寫掉
        if not state["injected"] and self.name == intruder_name:
            state["injected"] = True
            intruder = dict(json.loads(fake.objects[names[0]].decode()))
            intruder.update({"seq": 2, "nonce": "f" * 32, "prev": r1["hash"], "code": "WD-INTRUDER"})
            intruder["hash"] = fw._audit_hash(intruder)
            with fake.lock:
                fake.objects[intruder_name] = (json.dumps(intruder) + "\n").encode()
        return original_upload(self, data, content_type, if_generation_match)

    _FakeBlob.upload_from_string = racing_upload
    try:
        r3 = fw.audit("default:admin", "record_preview", "WD-G3", "ok", "admin", "default")
    finally:
        _FakeBlob.upload_from_string = original_upload
    check("B3 撞 412 後改搶下一格:落在 seq 3", r3["seq"] == 3, r3["seq"])
    check("B3b prev 鏈到闖入者(slot 2)的 hash",
          r3["prev"] == json.loads(fake.objects[intruder_name].decode())["hash"])
    check("B3c 沒有覆蓋 slot 2", json.loads(fake.objects[intruder_name].decode())["code"] == "WD-INTRUDER")
    ok, issues, stats = fw.verify_audit_chain()
    check("B3d 整條鏈(含闖入者)仍然無 fork、無 broken_link", ok, stats["kinds"])

    # B4 自己的重試:412 但位元組相同 → append_chained 回 False,不重複寫、不拋
    line = fake.objects[names[0]].decode().rstrip("\n")
    before = dict(fake.objects)
    ret = g.append_chained("audit.jsonl", 0, line)
    check("B4 位元組相同的重試 → False(視為已落地)", ret is False)
    check("B4b 物件集合未變", fake.objects == before)

    # B4c 已接受但回應遺失：不能把同一事件再寫成下一個 seq。
    response_lost = _FakeGcs(PreconditionFailed)
    st.reset_store(make_gcs_store(response_lost))
    lost_original_upload = _FakeBlob.upload_from_string
    lost_state = {"raised": False}

    def accepted_then_response_lost(self, data, content_type=None, if_generation_match=None):
        result = lost_original_upload(self, data, content_type, if_generation_match)
        if not lost_state["raised"]:
            lost_state["raised"] = True
            raise ServiceUnavailable("synthetic response lost after commit")
        return result

    _FakeBlob.upload_from_string = accepted_then_response_lost
    try:
        lost_rec = fw.audit("default:admin", "record_preview", "WD-LOST", "ok", "admin", "default")
    finally:
        _FakeBlob.upload_from_string = original_upload
    check("B4c 已接受但回應遺失 → 同一筆被確認為成功", lost_rec["seq"] == 0)
    check("B4d 回應遺失後不新增重複 slot", len(response_lost.objects) == 1,
          sorted(response_lost.objects))

    # B5 位元組不同的衝突 → ChainConflict(不是靜默覆蓋)
    try:
        g.append_chained("audit.jsonl", 0, line.replace("WD-G0", "WD-EVIL"))
        check("B5 位元組不同 → ChainConflict", False, "沒有拋例外")
    except st.ChainConflict:
        check("B5 位元組不同 → ChainConflict", True)
    check("B5b slot 0 內容未被改動", fake.objects[names[0]] == before[names[0]])

    # B6 舊命名守門:前綴裡出現時間戳命名的物件 → 拒寫、不留下新物件
    legacy = _FakeGcs(PreconditionFailed)
    legacy.objects["flywheel/audit.jsonl/01785812442826839294_e9234ffe.jsonl"] = b'{"seq":0}\n'
    st.reset_store(make_gcs_store(legacy))
    try:
        fw.audit("default:admin", "record_preview", "WD-L", "ok", "admin", "default")
        check("B6 舊命名前綴 → RuntimeError 拒寫", False, "沒有拋例外")
    except RuntimeError as e:
        check("B6 舊命名前綴 → RuntimeError 拒寫", "legacy" in str(e), str(e)[:60])
    check("B6b 守門後沒有留下任何新物件", len(legacy.objects) == 1, len(legacy.objects))

    # B7 用盡重試:每一次都被搶 → AuditWriteConflict,且沒有留下半成品
    contended = _FakeGcs(PreconditionFailed)
    st.reset_store(make_gcs_store(contended))
    fw.AUDIT_BACKOFF_BASE, fw.AUDIT_BACKOFF_CAP = 0.0, 0.0     # 測試不等退避

    lose_count = {"n": 0}

    def always_lose(self, data, content_type=None, if_generation_match=None):
        lose_count["n"] += 1          # 替身取代了原方法,原方法裡的 calls 記錄不會跑,自己計數
        # 「別人永遠先一步」:在我們嘗試建立 slot n 之前,先塞一筆**格式正確**的紀錄進去
        # (seq 與槽位一致——否則會被 chain_tail 的名稱/內容一致性守門擋住,那是 B8 在測的事)。
        n = int(self.name.rsplit("/", 1)[1].split(".")[0])
        with contended.lock:
            if self.name not in contended.objects:
                intruder = json.loads(data)
                intruder["nonce"] = ("%032x" % n)[-32:]
                intruder["code"] = "WD-INTRUDER-%d" % n
                intruder["hash"] = fw._audit_hash(intruder)
                contended.objects[self.name] = (json.dumps(intruder) + "\n").encode()
        raise PreconditionFailed("always lose")

    _FakeBlob.upload_from_string = always_lose
    try:
        # 仲裁會下載既有物件並發現位元組不同 → ChainConflict → 重試 … 直到用盡
        try:
            fw.audit("default:admin", "record_preview", "WD-X", "ok", "admin", "default")
            check("B7 持續衝突 → AuditWriteConflict", False, "沒有拋例外")
        except fw.AuditWriteConflict:
            check("B7 持續衝突 → AuditWriteConflict", True)
        except Exception as e:
            check("B7 持續衝突 → AuditWriteConflict", False, repr(e))
    finally:
        _FakeBlob.upload_from_string = original_upload
    check("B7b 嘗試次數 = AUDIT_MAX_RETRY(%d),沒有多也沒有少" % fw.AUDIT_MAX_RETRY,
          lose_count["n"] == fw.AUDIT_MAX_RETRY, lose_count["n"])
    check("B7c 每次都搶了不同的格(每次重試都有重讀尾端)",
          len(contended.objects) == fw.AUDIT_MAX_RETRY, len(contended.objects))

    # B7d 尾端本身驗不過時，不得在壞鏈後面繼續接新紀錄。
    poisoned = _FakeGcs(PreconditionFailed)
    poisoned_rec = dict(r0, hash="0" * 64)
    poisoned.objects["flywheel/audit.jsonl/00000000000000000000.jsonl"] = (
        json.dumps(poisoned_rec) + "\n").encode()
    st.reset_store(make_gcs_store(poisoned))
    try:
        fw.audit("default:admin", "record_preview", "WD-POISON", "ok", "admin", "default")
        check("B7d 壞尾端 → AuditChainCorrupt", False, "沒有拋例外")
    except fw.AuditChainCorrupt:
        check("B7d 壞尾端 → AuditChainCorrupt", True)
    check("B7e 壞尾端後沒有建立新 slot", len(poisoned.objects) == 1)

    # B7e2 尾端本身可自驗但 prev 沒接到前格時，同樣不可往後接。
    broken_link = _FakeGcs(PreconditionFailed)
    good0 = dict(r0)
    wrong1 = dict(r1, prev="GENESIS")
    wrong1["hash"] = fw._audit_hash(wrong1)
    broken_link.objects["flywheel/audit.jsonl/00000000000000000000.jsonl"] = (
        json.dumps(good0) + "\n").encode()
    broken_link.objects["flywheel/audit.jsonl/00000000000000000001.jsonl"] = (
        json.dumps(wrong1) + "\n").encode()
    st.reset_store(make_gcs_store(broken_link))
    try:
        fw.audit("default:admin", "record_preview", "WD-BROKEN-LINK", "ok", "admin", "default")
        check("B7e2 自驗尾端但 prev 斷鏈 → AuditChainCorrupt", False, "沒有拋例外")
    except fw.AuditChainCorrupt:
        check("B7e2 自驗尾端但 prev 斷鏈 → AuditChainCorrupt", True)
    check("B7e3 斷鏈後沒有建立新 slot", len(broken_link.objects) == 2)

    # B7e4 不能只驗尾端：中段 payload 被改、尾端仍有正確 self-hash/prev
    # 時，寫入前的 fresh full verifier 必須拒絕延伸。
    middle_tamper = _FakeGcs(PreconditionFailed)
    valid2 = dict(r1, seq=2, nonce="e" * 32, prev=r1["hash"], code="WD-G2")
    valid2["hash"] = fw._audit_hash(valid2)
    tampered1 = dict(r1, code="WD-MIDDLE-TAMPERED")  # deliberately keep stale hash
    middle_tamper.objects["flywheel/audit.jsonl/00000000000000000000.jsonl"] = (
        json.dumps(r0) + "\n").encode()
    middle_tamper.objects["flywheel/audit.jsonl/00000000000000000001.jsonl"] = (
        json.dumps(tampered1) + "\n").encode()
    middle_tamper.objects["flywheel/audit.jsonl/00000000000000000002.jsonl"] = (
        json.dumps(valid2) + "\n").encode()
    st.reset_store(make_gcs_store(middle_tamper))
    try:
        fw.audit("default:admin", "record_preview", "WD-MIDDLE", "ok", "admin", "default")
        check("B7e4 中段 hash mismatch → AuditChainCorrupt", False, "沒有拋例外")
    except fw.AuditChainCorrupt:
        check("B7e4 中段 hash mismatch → AuditChainCorrupt", True)
    check("B7e5 中段竄改後沒有建立新 slot", len(middle_tamper.objects) == 3)

    # B7e6 同一種竄改若發生在名稱沒有變的 cache 命中後，也不能被
    # incremental read cache 蓋掉；audit admission 必須使用 read_lines_fresh。
    cached_tamper = _FakeGcs(PreconditionFailed)
    cached_tamper.objects["flywheel/audit.jsonl/00000000000000000000.jsonl"] = (
        json.dumps(r0) + "\n").encode()
    cached_tamper.objects["flywheel/audit.jsonl/00000000000000000001.jsonl"] = (
        json.dumps(r1) + "\n").encode()
    cached_tamper.objects["flywheel/audit.jsonl/00000000000000000002.jsonl"] = (
        json.dumps(valid2) + "\n").encode()
    cached_store = make_gcs_store(cached_tamper)
    cached_store.read_lines("audit.jsonl")  # deliberately populate normal cache
    cached_tamper.objects["flywheel/audit.jsonl/00000000000000000001.jsonl"] = (
        json.dumps(tampered1) + "\n").encode()
    st.reset_store(cached_store)
    try:
        fw.audit("default:admin", "record_preview", "WD-CACHED-TAMPER", "ok", "admin", "default")
        check("B7e6 快取後中段竄改仍被 fresh verifier 拒絕", False, "沒有拋例外")
    except fw.AuditChainCorrupt:
        check("B7e6 快取後中段竄改仍被 fresh verifier 拒絕", True)
    check("B7e7 快取竄改後沒有建立新 slot", len(cached_tamper.objects) == 3)

    # B7f Cloud Run 最壞正常並行：3 instances × concurrency 4 同時讀到空尾端。
    # 第一輪強制同時通過 tail read，之後的衝突必須靠條件建立 + 重讀收斂。
    twelve = _FakeGcs(PreconditionFailed)
    twelve_store = make_gcs_store(twelve)
    st.reset_store(twelve_store)
    initial_tail = twelve_store.chain_tail
    all_read = threading.Barrier(12)
    local_state = threading.local()

    def synchronized_first_tail(key):
        value = initial_tail(key)
        if not getattr(local_state, "first_tail", False):
            local_state.first_tail = True
            all_read.wait(timeout=5)
        return value

    twelve_store.chain_tail = synchronized_first_tail
    twelve_errors = []

    def twelve_writer(i):
        try:
            fw.audit("default:w%d" % i, "record_preview", "WD-12-%d" % i,
                     "ok", "admin", "default")
        except Exception as exc:
            twelve_errors.append(repr(exc))

    twelve_threads = [threading.Thread(target=twelve_writer, args=(i,)) for i in range(12)]
    [t.start() for t in twelve_threads]
    [t.join() for t in twelve_threads]
    twelve_recs = [json.loads(twelve.objects[name].decode()) for name in sorted(twelve.objects)]
    _, _, twelve_stats = fw.verify_audit_chain(recs=twelve_recs)
    check("B7f 12 位同時寫入無例外", not twelve_errors, twelve_errors[:3])
    check("B7g 12 位同時寫入 seq 連續且無遺失",
          sorted(r["seq"] for r in twelve_recs) == list(range(12)),
          [r["seq"] for r in twelve_recs])
    check("B7h 12 位同時寫入沒有 fork 或 broken_link",
          not twelve_stats["kinds"].get("fork") and not twelve_stats["kinds"].get("broken_link"),
          twelve_stats["kinds"])

    # B8 名稱/內容 seq 不一致的物件 → chain_tail 明確失敗,不把壞尾端當基準
    broken = _FakeGcs(PreconditionFailed)
    broken.objects["flywheel/audit.jsonl/00000000000000000005.jsonl"] = b'{"seq":9,"hash":"h"}\n'
    try:
        make_gcs_store(broken).chain_tail("audit.jsonl")
        check("B8 名稱 seq≠內容 seq → IOError", False, "沒有拋例外")
    except IOError:
        check("B8 名稱 seq≠內容 seq → IOError", True)

    st.reset_store(None)
    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILED:
        print("FAILED %d 項:%s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("全部通過:並行寫入不再 fork;衝突、仲裁、守門、用盡重試皆以正確方式收場。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
