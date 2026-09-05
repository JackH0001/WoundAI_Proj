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
            self.store._write_locked(self.name, data)

    def download_as_bytes(self):
        with self.store.lock:
            self.store.calls.append(("download", self.name, None))
            if self.name not in self.store.objects:
                from google.api_core.exceptions import NotFound
                raise NotFound(self.name)
            return self.store.objects[self.name]

    @property
    def generation(self):
        # GCS generations are object metadata, not content hashes.  Keep an
        # explicit monotonic surrogate so a same-byte replacement still
        # invalidates a cached manifest exactly as production GCS would.
        with self.store.lock:
            return self.store.generations.get(self.name)


class _FakeBucket:
    def __init__(self, store):
        self.store = store
        # Default to the same locked, seven-year epoch required for real GCS
        # admission.  Individual tests explicitly weaken this to prove the
        # production gate rejects it.
        self._properties = {"retentionPolicy": {
            "retentionPeriod": "220903200", "isLocked": True}}

    def blob(self, name):
        return _FakeBlob(self.store, name)

    def reload(self):
        return self


class _FakeGcs:
    """objects: name -> bytes。list_blobs 回依名稱排序的物件(與 GCS 一致)。"""

    def __init__(self, PreconditionFailed):
        self.objects, self.generations, self.calls = {}, {}, []
        self._next_generation = 0
        self.lock = threading.Lock()
        self.PreconditionFailed = PreconditionFailed

    def _write_locked(self, name, data):
        """Replace one fake object while the caller holds ``self.lock``."""
        self._next_generation += 1
        self.objects[name] = data
        self.generations[name] = self._next_generation

    def put_object(self, name, data):
        with self.lock:
            self._write_locked(name, data)

    def list_blobs(self, bucket_name, prefix=""):
        with self.lock:
            self.calls.append(("list", prefix, None))
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
    g._audit_prefix_cache = {}
    g._audit_append_lock = threading.RLock()
    return g


def main():
    from google.api_core.exceptions import (
        BadGateway, GatewayTimeout, PreconditionFailed, RetryError,
        ServiceUnavailable,
    )
    from requests.exceptions import ConnectionError as RequestsConnectionError
    from requests.exceptions import Timeout as RequestsTimeout
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

    # The LocalStore primitive is also a public admission boundary during local
    # development.  A self-hash-valid record must not extend the current tail
    # with an arbitrary predecessor.
    local_store = st.get_store()
    local_bad = {
        "chain_v": fw.CHAIN_V, "nonce": "e" * 32, "seq": total,
        "ts": "2026-09-03T00:00:00Z", "actor": "direct:local", "role": "admin",
        "org": "default", "action": "record_preview", "code": "WD-LOCAL-BAD",
        "result": "ok", "prev": "a" * 64,
    }
    local_bad["hash"] = fw._audit_hash(local_bad)
    before_local = list(fw.read_jsonl(fw.AUDIT))
    try:
        local_store.append_chained(fw.AUDIT, total, json.dumps(local_bad))
        check("A9 Local direct wrong-prev 被拒", False, "沒有拋例外")
    except st.ChainConflict:
        check("A9 Local direct wrong-prev 被拒", True)
    check("A9b Local direct wrong-prev 不增加紀錄", fw.read_jsonl(fw.AUDIT) == before_local)

    # Public LocalStore admission must refuse to extend an already-corrupt
    # current-v4 history, even when the caller makes its new prev match the
    # corrupt tail.  This is intentionally a direct primitive test, not API
    # audit(), so a development-side bypass cannot hide behind API verification.
    poisoned_local_root = tempfile.mkdtemp(prefix="woundai_chainpoison_")
    poisoned_local = st.LocalStore(poisoned_local_root)
    os.makedirs(poisoned_local_root, exist_ok=True)
    poisoned_record = dict(recs[0], hash="0" * 64)
    with open(os.path.join(poisoned_local_root, "audit.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps(poisoned_record) + "\n")
    local_follow = dict(local_bad, seq=1, nonce="f" * 32, code="WD-LOCAL-FOLLOW",
                        prev=poisoned_record["hash"])
    local_follow["hash"] = fw._audit_hash(local_follow)
    try:
        poisoned_local.append_chained("audit.jsonl", 1, json.dumps(local_follow))
        check("A10 Local 壞歷史後 direct append 被拒", False, "沒有拋例外")
    except IOError:
        check("A10 Local 壞歷史後 direct append 被拒", True)
    check("A10b Local 壞歷史後不新增紀錄",
          len(poisoned_local.read_lines("audit.jsonl")) == 1)
    shutil.rmtree(poisoned_local_root, ignore_errors=True)

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

    # A valid-looking current record must still use the exact v4 schema and
    # canonical JSON object shape.  These are direct primitive tests: callers
    # may not manufacture a permanent locked slot by bypassing api.audit().
    direct_template = {
        "chain_v": fw.CHAIN_V, "nonce": "c" * 32, "seq": 0,
        "ts": "2026-09-03T00:00:00Z", "actor": "direct:test", "role": "admin",
        "org": "default", "action": "record_preview", "code": "WD-DIRECT",
        "result": "ok", "prev": "GENESIS",
    }
    direct_template["hash"] = fw._audit_hash(direct_template)
    malformed_current = []
    bad_hash = dict(direct_template, hash="0" * 64)
    malformed_current.append(json.dumps(bad_hash))
    bad_version = dict(direct_template, chain_v=4.0)
    bad_version["hash"] = fw._audit_hash(bad_version)
    malformed_current.append(json.dumps(bad_version))
    downgraded_version = dict(direct_template, chain_v=3)
    downgraded_version["hash"] = fw._audit_hash(downgraded_version)
    malformed_current.append(json.dumps(downgraded_version))
    bad_nonce = dict(direct_template, nonce="not-a-nonce")
    bad_nonce["hash"] = fw._audit_hash(bad_nonce)
    malformed_current.append(json.dumps(bad_nonce))
    missing_actor = dict(direct_template)
    missing_actor.pop("actor")
    missing_actor["hash"] = fw._audit_hash(missing_actor)
    malformed_current.append(json.dumps(missing_actor))
    empty_action = dict(direct_template, action="")
    empty_action["hash"] = fw._audit_hash(empty_action)
    malformed_current.append(json.dumps(empty_action))
    bad_role_type = dict(direct_template, role=7)
    bad_role_type["hash"] = fw._audit_hash(bad_role_type)
    malformed_current.append(json.dumps(bad_role_type))
    extra_field = dict(direct_template, operator_note="not part of v4")
    extra_field["hash"] = fw._audit_hash(extra_field)
    malformed_current.append(json.dumps(extra_field))
    # Python's encoder deliberately emits NaN unless allow_nan=False.  The
    # strict audit parser must reject it before an immutable slot is created.
    malformed_current.append(json.dumps(dict(direct_template, result=float("nan"))))
    duplicate_code = json.dumps(direct_template)[:-1] + ',"code":"WD-DUP"}'
    for bad_line in malformed_current + [duplicate_code]:
        try:
            g.append_chained("audit.jsonl", 0, bad_line)
            check("B0c 非正典 v4 slot 在寫入前被拒", False, bad_line)
        except ValueError:
            check("B0c 非正典 v4 slot 在寫入前被拒", True)
    check("B0d 非正典 direct writes 後桶仍為空", not fake.objects, sorted(fake.objects))
    too_large = dict(direct_template, seq=10 ** 20)
    too_large["hash"] = fw._audit_hash(too_large)
    try:
        g.append_chained("audit.jsonl", 10 ** 20, json.dumps(too_large))
        check("B0e 21 位 seq 在寫入前被拒", False, "沒有拋例外")
    except ValueError:
        check("B0e 21 位 seq 在寫入前被拒", True)
    check("B0f 非法 seq 被拒後桶仍為空", not fake.objects, sorted(fake.objects))

    # B1 首筆:空前綴 → seq 0、prev GENESIS,物件名為 20 位零填充
    r0 = fw.audit("default:admin", "record_preview", "WD-G0", "ok", "admin", "default")
    names = sorted(fake.objects)
    check("B1 首筆落在 flywheel/audit.jsonl/00000000000000000000.jsonl",
          names == ["flywheel/audit.jsonl/00000000000000000000.jsonl"], names)
    check("B1b seq=0, prev=GENESIS", r0["seq"] == 0 and r0["prev"] == "GENESIS")
    check("B1c 以 if_generation_match=0 建立",
          any(c == ("upload", names[0], 0) for c in fake.calls))

    # B2 第二筆:prev 必須等於首筆的 hash。冷啟動驗完整前綴一次，之後
    # 每次只做一份 manifest list 並下載尚未驗過的 suffix。
    fake.calls.clear()
    r1 = fw.audit("default:admin", "record_preview", "WD-G1", "ok", "admin", "default")
    downloads_before_upload = [c for c in fake.calls if c[0] == "download"
                               and fake.calls.index(c) < next(i for i, c2 in enumerate(fake.calls) if c2[0] == "upload")]
    lists_before_upload = [c for c in fake.calls if c[0] == "list"
                          and fake.calls.index(c) < next(i for i, c2 in enumerate(fake.calls) if c2[0] == "upload")]
    check("B2 prev 鏈到首筆 hash", r1["prev"] == r0["hash"] and r1["seq"] == 1)
    check("B2b 單次 admission：只 list 一次、只下載新增 suffix 一筆",
          len(downloads_before_upload) == 1 and len(lists_before_upload) == 1,
          {"downloads": len(downloads_before_upload), "lists": len(lists_before_upload)})

    # B2c TOCTOU 的外部防線：GCS 稽核在 retention 尚未 lock 時一律拒寫。
    # 沒有 WORM，任何應用程式層的「驗完再 create」都不能原子綁住舊 slot。
    unlocked = make_gcs_store(_FakeGcs(PreconditionFailed))
    unlocked._audit_bucket._properties = {"retentionPolicy": {
        "retentionPeriod": "220903200", "isLocked": False}}
    st.reset_store(unlocked)
    try:
        fw.audit("default:admin", "record_preview", "WD-UNLOCKED", "ok", "admin", "default")
        check("B2c 未鎖 GCS epoch → AuditChainCorrupt", False, "沒有拋例外")
    except fw.AuditChainCorrupt:
        check("B2c 未鎖 GCS epoch → AuditChainCorrupt", True)
    check("B2d 未鎖 epoch 不得建立 slot", not unlocked._client.objects,
          unlocked._client.objects)
    # GCS store primitives must enforce the same boundary.  Otherwise an
    # internal caller could skip api_flywheel.audit() and poison a clean epoch
    # with a legacy append or a direct chained write.
    try:
        unlocked.append_line("audit.jsonl", "{\"bypass\":true}")
        check("B2d2 GCS raw append_line(audit) 被拒", False, "沒有拋例外")
    except PermissionError:
        check("B2d2 GCS raw append_line(audit) 被拒", True)
    try:
        unlocked.append_chained("audit.jsonl", 0, json.dumps(r0))
        check("B2d3 未鎖 GCS direct append_chained 被拒", False, "沒有拋例外")
    except PermissionError:
        check("B2d3 未鎖 GCS direct append_chained 被拒", True)
    check("B2d4 所有 direct bypass 後仍不得建立 slot", not unlocked._client.objects,
          unlocked._client.objects)

    # B2e 正式執行路徑沒有煙霧例外。測試工具必須在自己的短命行程內
    # monkeypatch 閘門；任何部署環境變數都不得讓未鎖桶取得寫入權。
    smoke = make_gcs_store(_FakeGcs(PreconditionFailed))
    smoke._audit_bucket_name = "synthetic-smoke"
    smoke._audit_bucket._properties = {"retentionPolicy": {}}
    previous_smoke = os.environ.get("WOUNDAI_AUDIT_SMOKE")
    os.environ["WOUNDAI_AUDIT_SMOKE"] = "1"
    st.reset_store(smoke)
    try:
        fw.audit("smoke:tester", "smoke_test", "SMOKE-GATE", "ok", "engineer", "smoke")
        check("B2e 環境變數不得繞過未鎖 epoch", False, "沒有拋例外")
    except fw.AuditChainCorrupt:
        check("B2e 環境變數不得繞過未鎖 epoch", True)
    finally:
        if previous_smoke is None:
            os.environ.pop("WOUNDAI_AUDIT_SMOKE", None)
        else:
            os.environ["WOUNDAI_AUDIT_SMOKE"] = previous_smoke
    check("B2e2 環境變數繞過後仍不得建立 slot", not smoke._client.objects,
          smoke._client.objects)

    # B2f GCS audit() 必須使用 snapshot API，而不是驗完後再 chain_tail。
    snapshot_only = make_gcs_store(_FakeGcs(PreconditionFailed))
    snapshot_only.chain_tail = lambda key: (_ for _ in ()).throw(
        AssertionError("audit() must not read a second tail snapshot"))
    st.reset_store(snapshot_only)
    try:
        snapshot_rec = fw.audit("default:admin", "record_preview", "WD-SNAPSHOT", "ok", "admin", "default")
        check("B2f GCS audit 使用單一 snapshot，不讀第二個 tail", snapshot_rec["seq"] == 0)
    except Exception as exc:
        check("B2f GCS audit 使用單一 snapshot，不讀第二個 tail", False, repr(exc))

    # B2g 網路成本守門：冷啟動全驗一次；暖 cache 只下載每輪新增的
    # suffix，再加本輪建立後的 exact-byte readback。不得退回 N×每筆的內容 GET。
    perf = _FakeGcs(PreconditionFailed)
    previous = "GENESIS"
    for seq in range(100):
        seeded = {
            "chain_v": fw.CHAIN_V, "nonce": ("%032x" % (seq + 1))[-32:], "seq": seq,
            "ts": "2026-09-03T00:00:00Z", "actor": "seed:test", "role": "test",
            "org": "test", "action": "seed", "code": "WD-SEED-%d" % seq,
            "result": "ok", "prev": previous,
        }
        seeded["hash"] = fw._audit_hash(seeded)
        name = "flywheel/audit.jsonl/%020d.jsonl" % seq
        perf.put_object(name, (json.dumps(seeded) + "\n").encode())
        previous = seeded["hash"]
    perf_store = make_gcs_store(perf)
    st.reset_store(perf_store)
    perf.calls.clear()
    fw.audit("perf:cold", "record_preview", "WD-PERF-COLD", "ok", "test", "perf")
    first_upload = next(i for i, call in enumerate(perf.calls) if call[0] == "upload")
    check("B2g 冷啟動 100 slots：1 list + 100 舊內容 downloads",
          sum(c[0] == "list" for c in perf.calls[:first_upload]) == 1
          and sum(c[0] == "download" for c in perf.calls[:first_upload]) == 100,
          perf.calls[:3] + perf.calls[-3:])
    perf.calls.clear()
    for i in range(10):
        fw.audit("perf:warm", "record_preview", "WD-PERF-%d" % i, "ok", "test", "perf")
    check("B2h 暖 cache 連寫 10 筆：10 lists、20 downloads（suffix+readback）",
          sum(c[0] == "list" for c in perf.calls) == 10
          and sum(c[0] == "download" for c in perf.calls) == 20,
          {"lists": sum(c[0] == "list" for c in perf.calls),
           "downloads": sum(c[0] == "download" for c in perf.calls)})

    # Bring the primary cache through its own last write, then let another
    # process/store append three slots.  The primary must verify exactly that
    # three-slot suffix, not redownload the original hundred.
    perf_store._verified_audit_prefix("audit.jsonl")
    other_store = make_gcs_store(perf)
    st.reset_store(other_store)
    for i in range(3):
        fw.audit("perf:other", "record_preview", "WD-OTHER-%d" % i, "ok", "test", "perf")
    st.reset_store(perf_store)
    perf.calls.clear()
    fw.audit("perf:primary", "record_preview", "WD-PRIMARY", "ok", "test", "perf")
    primary_upload = next(i for i, call in enumerate(perf.calls) if call[0] == "upload")
    check("B2i 另一 instance 新增 3 筆：原 instance 只下載 3-slot suffix",
          sum(c[0] == "download" for c in perf.calls[:primary_upload]) == 3,
          [c for c in perf.calls[:primary_upload] if c[0] == "download"])

    st.reset_store(g)

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
                fake._write_locked(intruder_name, (json.dumps(intruder) + "\n").encode())
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
    # google-cloud-storage 可能在最終 response、內部 retry exhaustion，或
    # requests transport 層丟錯；每一種都必須用 exact-byte readback 仲裁。
    lost_original_upload = _FakeBlob.upload_from_string
    loss_factories = [
        ("503", lambda: ServiceUnavailable("synthetic response lost after commit")),
        ("502", lambda: BadGateway("synthetic response lost after commit")),
        ("504", lambda: GatewayTimeout("synthetic response lost after commit")),
        ("retry-exhausted", lambda: RetryError(
            "synthetic retry exhausted", cause=ServiceUnavailable("lost"))),
        ("requests-connection", lambda: RequestsConnectionError("lost")),
        ("requests-timeout", lambda: RequestsTimeout("lost")),
    ]
    for loss_name, make_error in loss_factories:
        response_lost = _FakeGcs(PreconditionFailed)
        st.reset_store(make_gcs_store(response_lost))
        lost_state = {"raised": False}

        def accepted_then_response_lost(self, data, content_type=None,
                                        if_generation_match=None, _make=make_error):
            result = lost_original_upload(self, data, content_type, if_generation_match)
            if not lost_state["raised"]:
                lost_state["raised"] = True
                raise _make()
            return result

        _FakeBlob.upload_from_string = accepted_then_response_lost
        try:
            lost_rec = fw.audit("default:admin", "record_preview",
                                "WD-LOST-" + loss_name, "ok", "admin", "default")
        finally:
            _FakeBlob.upload_from_string = original_upload
        check("B4c %s 已接受但回應遺失 → exact-byte 成功" % loss_name,
              lost_rec["seq"] == 0)
        check("B4d %s 回應遺失後不新增重複 slot" % loss_name,
              len(response_lost.objects) == 1, sorted(response_lost.objects))

    # If the transport failed before a durable create, readback 404 is not
    # success and no new event may be invented on a later slot.
    no_commit = _FakeGcs(PreconditionFailed)
    st.reset_store(make_gcs_store(no_commit))

    def lost_before_commit(self, data, content_type=None, if_generation_match=None):
        raise GatewayTimeout("synthetic failure before create")

    _FakeBlob.upload_from_string = lost_before_commit
    try:
        try:
            fw.audit("default:admin", "record_preview", "WD-NO-COMMIT",
                     "ok", "admin", "default")
            check("B4e 回應遺失且物件不存在 → fail-closed", False, "沒有拋例外")
        except fw.AuditChainCorrupt:
            check("B4e 回應遺失且物件不存在 → fail-closed", True)
    finally:
        _FakeBlob.upload_from_string = original_upload
    check("B4f 未 durable 的傳輸失敗不留下 slot", not no_commit.objects,
          no_commit.objects)

    # B5 位元組不同但自雜湊仍正確的衝突 → ChainConflict(不是靜默覆蓋)。
    # Mutating only the JSON text would now correctly fail the stricter v4
    # contract before GCS collision, so re-hash it to exercise the collision path.
    evil = json.loads(line)
    evil["code"] = "WD-EVIL"
    evil["hash"] = fw._audit_hash(evil)
    try:
        g.append_chained("audit.jsonl", 0, json.dumps(evil))
        check("B5 位元組不同 → ChainConflict", False, "沒有拋例外")
    except st.ChainConflict:
        check("B5 位元組不同 → ChainConflict", True)
    check("B5b slot 0 內容未被改動", fake.objects[names[0]] == before[names[0]])

    # B5c Direct store callers cannot poison an empty locked epoch with a
    # self-hash-valid record that merely points at the wrong predecessor.
    wrong_prev = _FakeGcs(PreconditionFailed)
    wrong_prev_store = make_gcs_store(wrong_prev)
    direct_bad = dict(r0, prev="a" * 64, nonce="d" * 32, code="WD-DIRECT-BAD")
    direct_bad["hash"] = fw._audit_hash(direct_bad)
    try:
        wrong_prev_store.append_chained("audit.jsonl", 0, json.dumps(direct_bad))
        check("B5c direct wrong-prev 自驗 record 被拒", False, "沒有拋例外")
    except st.ChainConflict:
        check("B5c direct wrong-prev 自驗 record 被拒", True)
    check("B5d direct wrong-prev 被拒後 epoch 仍為空", not wrong_prev.objects,
          wrong_prev.objects)

    # The same predecessor rule applies beyond GENESIS: a direct caller cannot
    # attach a fresh slot to an older, otherwise valid record in an existing
    # chain.
    tail_bad = dict(r0, seq=4, nonce="e" * 32, code="WD-DIRECT-OLD-PREV",
                    prev=r0["hash"])
    tail_bad["hash"] = fw._audit_hash(tail_bad)
    before_tail_bad = dict(fake.objects)
    try:
        g.append_chained("audit.jsonl", 4, json.dumps(tail_bad))
        check("B5e nonempty direct wrong-prev 自驗 record 被拒", False, "沒有拋例外")
    except st.ChainConflict:
        check("B5e nonempty direct wrong-prev 自驗 record 被拒", True)
    check("B5f nonempty direct wrong-prev 不新增 slot", fake.objects == before_tail_bad)

    # B6 舊命名守門:前綴裡出現時間戳命名的物件 → admission 拒寫、不留下新物件
    legacy = _FakeGcs(PreconditionFailed)
    legacy.put_object("flywheel/audit.jsonl/01785812442826839294_e9234ffe.jsonl",
                      b'{"seq":0}\n')
    st.reset_store(make_gcs_store(legacy))
    try:
        fw.audit("default:admin", "record_preview", "WD-L", "ok", "admin", "default")
        check("B6 舊命名前綴 → AuditChainCorrupt 拒寫", False, "沒有拋例外")
    except fw.AuditChainCorrupt:
        check("B6 舊命名前綴 → AuditChainCorrupt 拒寫", True)
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
                contended._write_locked(self.name, (json.dumps(intruder) + "\n").encode())
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
    poisoned.put_object("flywheel/audit.jsonl/00000000000000000000.jsonl",
                        (json.dumps(poisoned_rec) + "\n").encode())
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
    broken_link.put_object("flywheel/audit.jsonl/00000000000000000000.jsonl",
                           (json.dumps(good0) + "\n").encode())
    broken_link.put_object("flywheel/audit.jsonl/00000000000000000001.jsonl",
                           (json.dumps(wrong1) + "\n").encode())
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
    middle_tamper.put_object("flywheel/audit.jsonl/00000000000000000000.jsonl",
                             (json.dumps(r0) + "\n").encode())
    middle_tamper.put_object("flywheel/audit.jsonl/00000000000000000001.jsonl",
                             (json.dumps(tampered1) + "\n").encode())
    middle_tamper.put_object("flywheel/audit.jsonl/00000000000000000002.jsonl",
                             (json.dumps(valid2) + "\n").encode())
    st.reset_store(make_gcs_store(middle_tamper))
    try:
        fw.audit("default:admin", "record_preview", "WD-MIDDLE", "ok", "admin", "default")
        check("B7e4 中段 hash mismatch → AuditChainCorrupt", False, "沒有拋例外")
    except fw.AuditChainCorrupt:
        check("B7e4 中段 hash mismatch → AuditChainCorrupt", True)
    check("B7e5 中段竄改後沒有建立新 slot", len(middle_tamper.objects) == 3)

    # B7e6 已驗 prefix 的 generation/name manifest 之後若改變，不得把它
    # 當一般 cache miss 全量重驗後繼續；locked epoch 出現這種變化本身就是事件。
    cached_tamper = _FakeGcs(PreconditionFailed)
    cached_tamper.put_object("flywheel/audit.jsonl/00000000000000000000.jsonl",
                             (json.dumps(r0) + "\n").encode())
    cached_tamper.put_object("flywheel/audit.jsonl/00000000000000000001.jsonl",
                             (json.dumps(r1) + "\n").encode())
    cached_tamper.put_object("flywheel/audit.jsonl/00000000000000000002.jsonl",
                             (json.dumps(valid2) + "\n").encode())
    cached_store = make_gcs_store(cached_tamper)
    cached_store._verified_audit_prefix("audit.jsonl")
    cached_tamper.put_object("flywheel/audit.jsonl/00000000000000000001.jsonl",
                             (json.dumps(tampered1) + "\n").encode())
    st.reset_store(cached_store)
    try:
        fw.audit("default:admin", "record_preview", "WD-CACHED-TAMPER", "ok", "admin", "default")
        check("B7e6 已驗 prefix generation 改變 → fail-closed", False, "沒有拋例外")
    except fw.AuditChainCorrupt:
        check("B7e6 已驗 prefix generation 改變 → fail-closed", True)
    check("B7e7 快取竄改後沒有建立新 slot", len(cached_tamper.objects) == 3)
    check("B7e7b generation 變更後舊 proof 已被驅逐",
          not cached_store._audit_prefix_cache,
          cached_store._audit_prefix_cache)

    # A GCS generation changes even when replacement bytes happen to be
    # identical.  Content-derived fake generations cannot exercise this case.
    same_bytes = _FakeGcs(PreconditionFailed)
    same_name = "flywheel/audit.jsonl/00000000000000000000.jsonl"
    same_payload = (json.dumps(r0) + "\n").encode()
    same_bytes.put_object(same_name, same_payload)
    same_store = make_gcs_store(same_bytes)
    same_store._verified_audit_prefix("audit.jsonl")
    generation_before = same_bytes.generations[same_name]
    same_bytes.put_object(same_name, same_payload)
    try:
        same_store._verified_audit_prefix("audit.jsonl")
        check("B7e7c 相同 bytes 但 generation 改變 → fail-closed", False, "沒有拋例外")
    except IOError:
        check("B7e7c 相同 bytes 但 generation 改變 → fail-closed", True)
    check("B7e7d 相同 bytes replacement 真的換 generation 且清 cache",
          same_bytes.generations[same_name] > generation_before
          and not same_store._audit_prefix_cache,
          same_bytes.generations[same_name])

    poison = _FakeGcs(PreconditionFailed)
    poison_store = make_gcs_store(poison)
    st.reset_store(poison_store)
    fw.audit("default:admin", "record_preview", "WD-POISON-BASE", "ok", "admin", "default")
    poison.put_object("flywheel/audit.jsonl/!poison", b"synthetic")
    before_poison = dict(poison.objects)
    try:
        fw.audit("default:admin", "record_preview", "WD-POISON-NAME", "ok", "admin", "default")
        check("B7e8 cache 後新增非正典名稱 → AuditChainCorrupt", False, "沒有拋例外")
    except fw.AuditChainCorrupt:
        check("B7e8 cache 後新增非正典名稱 → AuditChainCorrupt", True)
    check("B7e9 非正典名稱後沒有 upload", poison.objects == before_poison)
    check("B7e10 list/name 驗證失敗也會驅逐舊 proof",
          not poison_store._audit_prefix_cache,
          poison_store._audit_prefix_cache)

    # B7f Cloud Run 正常並行：3 個獨立 instance/store × concurrency 4。
    # 同一 instance 由 RLock 排隊；三個 instance 的第一次 manifest list 強制
    # 同時看見空桶，之後靠 create-if-absent + suffix revalidation 收斂。
    twelve = _FakeGcs(PreconditionFailed)
    twelve_stores = [make_gcs_store(twelve) for _ in range(3)]
    first_lists = threading.Barrier(3)
    for instance_store in twelve_stores:
        original_list = instance_store._list_audit_slots_for_admission
        first = {"done": False}

        def synchronized_first_list(key, _original=original_list, _first=first):
            value = _original(key)
            if not _first["done"]:
                _first["done"] = True
                first_lists.wait(timeout=5)
            return value

        instance_store._list_audit_slots_for_admission = synchronized_first_list

    original_fw_store = fw._store
    selected_store = threading.local()
    fw._store = lambda: selected_store.value
    twelve_errors = []

    def twelve_writer(i):
        try:
            selected_store.value = twelve_stores[i // 4]
            fw.audit("default:w%d" % i, "record_preview", "WD-12-%d" % i,
                     "ok", "admin", "default")
        except Exception as exc:
            twelve_errors.append(repr(exc))

    twelve_threads = [threading.Thread(target=twelve_writer, args=(i,)) for i in range(12)]
    [t.start() for t in twelve_threads]
    [t.join() for t in twelve_threads]
    fw._store = original_fw_store
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
    broken.put_object("flywheel/audit.jsonl/00000000000000000005.jsonl",
                      b'{"seq":9,"hash":"h"}\n')
    try:
        make_gcs_store(broken).chain_tail("audit.jsonl")
        check("B8 名稱 seq≠內容 seq → IOError", False, "沒有拋例外")
    except IOError:
        check("B8 名稱 seq≠內容 seq → IOError", True)

    # The standalone verifier must retain GCS slot boundaries.  A single
    # immutable object containing two otherwise valid JSON records cannot be
    # reinterpreted as a valid two-record chain by generic read_lines().
    multi_line_slot = _FakeGcs(PreconditionFailed)
    multi_line_slot.put_object(
        "flywheel/audit.jsonl/00000000000000000000.jsonl",
        (json.dumps(r0) + "\n" + json.dumps(r1) + "\n").encode())
    st.reset_store(make_gcs_store(multi_line_slot))
    try:
        fw.verify_audit_chain()
        check("B8b verifier 拒絕一 slot 多筆 JSON", False, "沒有拋例外")
    except IOError:
        check("B8b verifier 拒絕一 slot 多筆 JSON", True)

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
