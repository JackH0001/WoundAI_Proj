# -*- coding: utf-8 -*-
"""稽核鏈序列化的**真實 GCS** 煙霧測試。

假件能證明邏輯,證明不了 google-cloud-storage 在真桶上對 `if_generation_match=0` 的行為
(412 → PreconditionFailed)。這支腳本對一個**拋棄式**桶做最小驗證。

⚠ 它會寫入物件。請指向專供煙霧測試的桶(無保留政策、事後可刪),
   **不要**指向即將鎖定的正式稽核桶——寫進去的測試紀錄會跟著凍 7 年。

    $env:WOUNDAI_STORE="gcs"
    $env:WOUNDAI_GCS_BUCKET="<主桶>"                 # 只用來建構 GcsStore,不寫入
    $env:WOUNDAI_AUDIT_BUCKET="<拋棄式煙霧桶>"       # 實際寫入的地方
    $env:WOUNDAI_AUDIT_SMOKE="1"                    # 明確承認這是可刪的測試桶
    python engineering/phase2/smoke_audit_chain_gcs.py

退出碼 0＝全部通過;1＝有項目失敗;2＝環境不對(拒跑)。
"""
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))
sys.path.insert(0, BACKEND)

FAILED = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


def positive_int_env(name, default, maximum):
    """Read a bounded positive test-size override without silently coercing it."""
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("%s must be an integer, got %r" % (name, raw)) from exc
    if not 1 <= value <= maximum:
        raise ValueError("%s must be in 1..%d, got %d" % (name, maximum, value))
    return value


def nonnegative_seconds_env(name):
    """Return 0 for no timing gate; reject malformed acceptance limits."""
    raw = os.environ.get(name, "0")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("%s must be seconds, got %r" % (name, raw)) from exc
    if value < 0:
        raise ValueError("%s must not be negative" % name)
    return value


def main():
    if os.environ.get("WOUNDAI_STORE") != "gcs":
        print("拒跑:WOUNDAI_STORE 必須是 gcs(這支只對真 GCS 有意義)")
        return 2
    audit_bucket = os.environ.get("WOUNDAI_AUDIT_BUCKET", "")
    main_bucket = os.environ.get("WOUNDAI_GCS_BUCKET", "")
    if os.environ.get("WOUNDAI_AUDIT_SMOKE") != "1":
        print("拒跑:必須明確設定 WOUNDAI_AUDIT_SMOKE=1（此工具會寫入可刪測試桶）")
        return 2
    if not audit_bucket or "smoke" not in audit_bucket:
        # 硬性要求桶名含 smoke:防止手滑對正式稽核桶寫測試紀錄
        print("拒跑:WOUNDAI_AUDIT_BUCKET 必須是名稱含 'smoke' 的拋棄式桶(目前=%r)" % audit_bucket)
        return 2
    if not main_bucket or audit_bucket == main_bucket:
        print("拒跑:WOUNDAI_AUDIT_BUCKET 必須與 WOUNDAI_GCS_BUCKET 不同")
        return 2

    import store as st
    import api_flywheel as fw
    g = fw._store()
    check("0  後端是 GcsStore", isinstance(g, st.GcsStore), type(g).__name__)
    print("   稽核桶:gs://%s" % audit_bucket)
    if FAILED:
        return 1

    info = g.retention_info()
    if not info.get("verified") or info.get("locked") or int(info.get("retention_seconds") or 0) != 0:
        print("拒跑:煙霧桶必須可讀、未鎖且無 retention policy（目前=%s）" % info)
        return 2
    existing_objects = [blob.name for blob in g._client.list_blobs(audit_bucket)]
    if existing_objects:
        print("拒跑:煙霧桶整桶必須為空（前 5 筆=%s）" % existing_objects[:5])
        return 2

    # The production admission gate has no environment-variable bypass.  This
    # short-lived test process replaces the method only on the exact GcsStore
    # instance whose disposable, empty, unlocked bucket was verified above.
    g.require_locked_audit_epoch = lambda: None

    key = "audit.jsonl"
    tail_seq, _ = g.chain_tail(key)
    if tail_seq != -1:
        print("拒跑:煙霧桶必須為空，避免測試混入既有稽核資料（tail=%d）" % tail_seq)
        return 2
    check("1  起始尾端為空(seq=-1)", True)

    # 2. 連寫三筆,鏈必須乾淨
    recs = [fw.audit("smoke:tester", "smoke_test", "SMOKE-%d" % i, "ok", "engineer", "smoke")
            for i in range(3)]
    check("2  三筆 seq 連續", [r["seq"] for r in recs] == [tail_seq + 1, tail_seq + 2, tail_seq + 3],
          [r["seq"] for r in recs])
    check("2b prev 正確鏈接", recs[1]["prev"] == recs[0]["hash"] and recs[2]["prev"] == recs[1]["hash"])
    ok, issues, stats = fw.verify_audit_chain()
    check("2c verify_audit_chain real_issues=0", stats["real_issues"] == 0, stats["kinds"])

    # 3. 真 412:直接以 GCS create-if-absent 對已存在 slot 條件建立，證明雲端
    # 語意本身。這個 transport-level probe 只存在於 disposable smoke 工具，
    # 不會替 production admission 增加可設定的 bypass。
    taken = recs[0]["seq"]
    line = json.dumps(recs[0], ensure_ascii=False)
    evil = dict(recs[0], code="SMOKE-EVIL")
    evil["hash"] = fw._audit_hash(evil)
    try:
        from google.api_core.exceptions import PreconditionFailed
        slot_name = g._k(key) + "/%020d.jsonl" % taken
        g._audit_bucket.blob(slot_name).upload_from_string(
            json.dumps(evil, ensure_ascii=False), content_type="application/json",
            if_generation_match=0)
        check("3  已存在格 + create-if-absent → PreconditionFailed(真 412)", False, "沒有拋例外")
    except PreconditionFailed:
        check("3  已存在格 + create-if-absent → PreconditionFailed(真 412)", True)
    except Exception as e:
        check("3  已存在格 + create-if-absent → PreconditionFailed(真 412)", False, repr(e))

    try:
        g.append_chained(key, taken, json.dumps(evil, ensure_ascii=False))
        check("3b public direct 不同位元組 → ChainConflict", False, "沒有拋例外")
    except st.ChainConflict:
        check("3b public direct 不同位元組 → ChainConflict", True)
    except Exception as e:
        check("3b public direct 不同位元組 → ChainConflict", False, repr(e))

    # 4. Public exact-byte replay → False(自己的重試)
    try:
        r = g.append_chained(key, taken, line)
        check("4  已存在格 + 相同位元組 → False", r is False, r)
    except Exception as e:
        check("4  已存在格 + 相同位元組 → False", False, repr(e))
    _, tail_after = g.chain_tail(key)
    check("4b 3/4 兩步都沒有改動鏈(尾端仍是第三筆)", tail_after["hash"] == recs[2]["hash"])

    # A direct store caller may not supply a self-hash-valid record with an old
    # predecessor.  The public primitive must refresh and reject it before any
    # new immutable slot reaches the smoke bucket.
    wrong_prev = dict(recs[0], seq=3, nonce="f" * 32,
                      code="SMOKE-DIRECT-WRONG-PREV", prev=recs[0]["hash"])
    wrong_prev["hash"] = fw._audit_hash(wrong_prev)
    try:
        g.append_chained(key, 3, json.dumps(wrong_prev, ensure_ascii=False))
        check("4c direct wrong-prev 被拒且不寫入", False, "沒有拋例外")
    except st.ChainConflict:
        check("4c direct wrong-prev 被拒且不寫入", True)
    _, tail_after_direct = g.chain_tail(key)
    check("4d direct wrong-prev 後尾端不變", tail_after_direct["hash"] == recs[2]["hash"])

    # 5. 真並行。預設保留 8×10 壓力測試；部署前可設 12×1，對應
    # Cloud Run 的 max-instances=3 × concurrency=4。若設定 timing gate，
    # 它是整個同步 burst 的 wall-clock 上限，因此也是每一筆的上限。
    try:
        N_T = positive_int_env("WOUNDAI_AUDIT_SMOKE_THREADS", 8, 32)
        N_E = positive_int_env("WOUNDAI_AUDIT_SMOKE_EVENTS_PER_THREAD", 10, 100)
        max_seconds = nonnegative_seconds_env("WOUNDAI_AUDIT_SMOKE_MAX_SECONDS")
    except ValueError as exc:
        print("拒跑:無效的併發驗收參數: %s" % exc)
        return 2
    barrier = threading.Barrier(N_T)
    errors = []
    durations = []
    durations_lock = threading.Lock()

    def worker(t):
        try:
            barrier.wait()
            for i in range(N_E):
                started = time.monotonic()
                fw.audit("smoke:t%d" % t, "smoke_concurrent", "SMOKE-C%d-%d" % (t, i), "ok",
                         "engineer", "smoke")
                with durations_lock:
                    durations.append(time.monotonic() - started)
        except Exception as e:
            with durations_lock:
                errors.append(repr(e))

    ths = [threading.Thread(target=worker, args=(t,)) for t in range(N_T)]
    burst_started = time.monotonic()
    [x.start() for x in ths]
    [x.join() for x in ths]
    burst_seconds = time.monotonic() - burst_started
    check("5  並行寫入無例外", not errors, errors[:3])
    all_recs = fw.read_jsonl(fw.AUDIT)
    seqs = [r["seq"] for r in all_recs]
    check("5b seq 無重複", len(seqs) == len(set(seqs)), (len(seqs), len(set(seqs))))
    check("5c 筆數增加恰為 %d" % (N_T * N_E), len(all_recs) == tail_seq + 1 + 3 + N_T * N_E, len(all_recs))
    ok, issues, stats = fw.verify_audit_chain()
    check("5d 真 GCS 並行後 fork=0 broken_link=0",
          stats["kinds"].get("fork", 0) == 0 and stats["kinds"].get("broken_link", 0) == 0, stats["kinds"])
    check("5e 整條鏈 real_issues=0", stats["real_issues"] == 0)
    max_latency = max(durations) if durations else 0.0
    check("5f 每筆都有延遲樣本", len(durations) == N_T * N_E,
          {"expected": N_T * N_E, "observed": len(durations)})
    print("   timing: %d×%d, burst=%.3fs, max_request=%.3fs" %
          (N_T, N_E, burst_seconds, max_latency))
    if max_seconds:
        check("5g 同步 burst 在 %.3fs 驗收上限內" % max_seconds,
              burst_seconds <= max_seconds,
              {"burst_seconds": burst_seconds, "max_request_seconds": max_latency})

    print()
    if FAILED:
        print("FAILED %d 項:%s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("全部通過:真 GCS 上的條件建立、412 仲裁、並行序列化皆如設計。")
    print("(煙霧桶內的測試物件可整桶刪除;它沒有保留政策。)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
