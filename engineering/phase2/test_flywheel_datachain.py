# -*- coding: utf-8 -*-
"""飛輪訓練集資料鏈單元測試(純函式,免啟動 Flask)。

背景:2026-07-28 稽核發現佇列 8/8 筆是「孤兒 GT」——只有 gt_polygon,沒有影像、沒有影像尺寸,
無法柵格化成遮罩,一筆都不可訓練。本測試把「資料鏈完整性」釘成回歸,避免再次悄悄退化。

跑法:pytest engineering/phase2/test_flywheel_datachain.py -q
"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))
sys.path.insert(0, BACKEND)
import api_flywheel as fw  # noqa: E402

POLY = [[10, 10], [200, 10], [200, 200], [10, 200]]
IID = "aaaabbbbccccdddd"
BASE = {"code": "WD-UT0001", "gt_polygon": POLY, "exudate": 2,
        "doctor_verified": True, "deidentified": True, "consent_train": True,
        "image_id": IID, "image_w": 640, "image_h": 480}


# ---------- 守門 ----------
def test_合格標註通過():
    ok, iss = fw.validate_annotation(BASE)
    assert ok, iss


def test_孤兒GT被擋下():
    """核心回歸:沒有 image_id/尺寸 = 不可訓練樣本,必須拒收。"""
    for miss in ("image_id", "image_w", "image_h"):
        d = {k: v for k, v in BASE.items() if k != miss}
        ok, iss = fw.validate_annotation(d)
        assert not ok and any(miss in i for i in iss), (miss, iss)


def test_三同意與去識別碼守門():
    for flag in ("doctor_verified", "deidentified", "consent_train"):
        assert not fw.validate_annotation({**BASE, flag: False})[0]
    assert not fw.validate_annotation({**BASE, "code": "張三-001"})[0]


def test_滲液值域與多邊形合法性():
    assert not fw.validate_annotation({**BASE, "exudate": 9})[0]
    assert not fw.validate_annotation({**BASE, "exudate": -1})[0]
    assert not fw.validate_annotation({**BASE, "gt_polygon": [[1, 1], [2, 2]]})[0]      # <3 點
    assert not fw.validate_annotation({**BASE, "gt_polygon": [[0, 0], [9999, 0], [0, 9]]})[0]  # 超界


def test_路徑穿越被擋下():
    """image_id/code 會被當檔名用 → 未做白名單就是任意檔案讀取漏洞。"""
    for bad in ("../../secret/private", "..%2F..%2Fx", "AAAABBBBCCCCDDDD", "abc"):
        assert not fw.validate_annotation({**BASE, "image_id": bad})[0], bad
    for bad in ("WD-../x", "WD-a b", "WD-" + "x" * 40):
        assert not fw.validate_annotation({**BASE, "code": bad})[0], bad


# ---------- 去重 / 修訂 ----------
def _write(tmp, name, recs):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        for r in recs: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def test_同影像同遮罩算重複_不同遮罩算修訂():
    with tempfile.TemporaryDirectory() as tmp:
        q = _write(tmp, "q.jsonl", [{**BASE, "received_at": "2026-07-28T10:00:00Z"}])
        exact, same = fw.find_duplicate(q, IID, POLY)
        assert exact is not None, "同影像同遮罩應判為重複"
        moved = [[x + 1, y] for x, y in POLY]     # 醫師微調描邊
        exact2, same2 = fw.find_duplicate(q, IID, moved)
        assert exact2 is None and len(same2) == 1, "同影像不同遮罩應判為修訂(非重複)"
        # 不同影像的相同座標不該互相碰撞
        assert fw.find_duplicate(q, "ffffffffffffffff", POLY)[0] is None


# ---------- 有效佇列 ----------
def _imgs(tmp, ids):
    d = os.path.join(tmp, "images"); os.makedirs(d, exist_ok=True)
    for i in ids: open(os.path.join(d, i + ".jpg"), "wb").write(b"x")
    return d


def test_有效佇列排除孤兒_格式錯_同意失效_並只取最新修訂():
    with tempfile.TemporaryDirectory() as tmp:
        IID2 = "1111222233334444"
        imgs = _imgs(tmp, [IID, IID2])
        q = _write(tmp, "q.jsonl", [
            {"code": "WD-OLD", "gt_polygon": POLY, "received_at": "2026-07-01T00:00:00Z"},   # 孤兒
            {**BASE, "code": "WD-BAD", "image_id": IID, "image_w": None,
             "received_at": "2026-07-28T09:00:00Z"},                                          # 格式錯
            {**BASE, "code": "WD-NC", "image_id": IID2, "consent_train": False,
             "received_at": "2026-07-28T09:30:00Z"},                                          # 同意失效
            {**BASE, "code": "WD-A1", "received_at": "2026-07-28T10:00:00Z"},
            {**BASE, "code": "WD-A2", "gt_polygon": [[5, 5], [300, 5], [300, 300]],
             "received_at": "2026-07-28T11:00:00Z"},                                          # 同影像修訂
            {**BASE, "code": "WD-B1", "image_id": IID2, "received_at": "2026-07-28T12:00:00Z"},
            {**BASE, "code": "WD-C1", "image_id": "999999999999beef",
             "received_at": "2026-07-28T13:00:00Z"},                                          # 影像遺失
        ])
        w = _write(tmp, "w.jsonl", [])
        recs, st = fw.effective_queue(q, imgs, w)
        assert st["orphan_no_image"] == 1, st
        assert st["malformed"] == 1, st
        assert st["consent_invalid"] == 1, st
        assert st["image_file_missing"] == 1, st
        assert sorted(r["code"] for r in recs) == ["WD-A2", "WD-B1"]   # A 取最新修訂
        assert st["trainable"] == 2
        assert st["total"] == sum(st[k] for k in ("orphan_no_image", "malformed", "consent_invalid",
                                                  "image_file_missing", "withdrawn", "superseded",
                                                  "other_source", "trainable")), "統計未守恆"


def test_撤回同意涵蓋整張影像的所有標註():
    """P0 回歸:撤回若只比對 code,同影像的兄弟紀錄(含被取代的舊 GT)會遞補進訓練集,
    等於病人撤回了但影像照樣被用。必須以 code + image_id 雙鍵排除。"""
    with tempfile.TemporaryDirectory() as tmp:
        imgs = _imgs(tmp, [IID])
        q = _write(tmp, "q.jsonl", [
            {**BASE, "code": "WD-A1", "received_at": "2026-07-28T10:00:00Z"},
            {**BASE, "code": "WD-A2", "gt_polygon": [[5, 5], [300, 5], [300, 300]],
             "received_at": "2026-07-28T11:00:00Z"},
        ])
        w = _write(tmp, "w.jsonl", [{"code": "WD-A2", "image_id": IID, "image_ids": [IID]}])
        recs, st = fw.effective_queue(q, imgs, w)
        assert recs == [] and st["withdrawn"] == 2 and st["trainable"] == 0, (st, recs)


def test_撤回涵蓋一個code對到的多張影像():
    """P0 回歸:受試者多次回診 → 一個 code 對到多張影像。
    withdrawn 紀錄若只讀單數 image_id,第二張以後不會被擋,兄弟標註照樣進訓練集。"""
    with tempfile.TemporaryDirectory() as tmp:
        X, Y = "aaaabbbbccccdddd", "1111222233334444"
        imgs = _imgs(tmp, [X, Y])
        q = _write(tmp, "q.jsonl", [
            {**BASE, "code": "WD-SUBJ", "image_id": X, "received_at": "2026-07-28T10:00:00Z"},
            {**BASE, "code": "WD-SUBJ", "image_id": Y, "received_at": "2026-07-28T11:00:00Z"},
            {**BASE, "code": "WD-22220000", "image_id": Y, "received_at": "2026-07-28T12:00:00Z"},
        ])
        w = _write(tmp, "w.jsonl", [{"code": "WD-SUBJ", "action": "withdraw",
                                     "image_id": X, "image_ids": [X, Y]}])
        recs, st = fw.effective_queue(q, imgs, w)
        assert recs == [] and st["trainable"] == 0, (st, [r["code"] for r in recs])


def test_重新取得同意可回復():
    with tempfile.TemporaryDirectory() as tmp:
        imgs = _imgs(tmp, [IID])
        q = _write(tmp, "q.jsonl", [{**BASE, "received_at": "2026-07-28T10:00:00Z"}])
        w = _write(tmp, "w.jsonl", [
            {"code": BASE["code"], "action": "withdraw", "image_ids": [IID]},
            {"code": BASE["code"], "action": "restore", "image_ids": [IID]},
        ])
        recs, st = fw.effective_queue(q, imgs, w)
        assert st["trainable"] == 1 and recs[0]["code"] == BASE["code"], st


def test_壞行不靜默消失_且統計守恆():
    with tempfile.TemporaryDirectory() as tmp:
        imgs = _imgs(tmp, [IID])
        p = os.path.join(tmp, "q.jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({**BASE, "received_at": "2026-07-28T10:00:00Z"}) + "\n")
            f.write('{"code": "WD-BROKEN", trunc\n')      # 壞 JSON
            f.write('"我是字串不是物件"\n')                  # 合法 JSON 但非 dict
        _, st = fw.effective_queue(p, imgs, _write(tmp, "w.jsonl", []))
        assert st["malformed"] == 2 and st["trainable"] == 1, st
        assert st["total"] == sum(v for k, v in st.items()
                                  if k not in ("total", "by_source")), st


def test_無時間戳不會壓過有時間戳的修訂():
    with tempfile.TemporaryDirectory() as tmp:
        imgs = _imgs(tmp, [IID])
        q = _write(tmp, "q.jsonl", [
            {**BASE, "code": "WD-NULLTS", "received_at": None},
            {**BASE, "code": "WD-NEW", "gt_polygon": [[5, 5], [300, 5], [300, 300]],
             "received_at": "2026-07-28T11:00:00Z"},
        ])
        recs, _ = fw.effective_queue(q, imgs, _write(tmp, "w.jsonl", []))
        assert recs[0]["code"] == "WD-NEW", recs[0]["code"]


def test_撤回影像會被隔離():
    with tempfile.TemporaryDirectory() as tmp:
        imgs = _imgs(tmp, [IID]); qz = os.path.join(tmp, "quarantine")
        assert fw.quarantine_image(IID, imgs, qz) is True
        assert not os.path.exists(os.path.join(imgs, IID + ".jpg"))
        assert os.path.exists(os.path.join(qz, IID + ".jpg"))
        assert fw.quarantine_image("../../etc/passwd", imgs, qz) is False   # 穿越防護


# ---------- 匯出端到端 ----------
def _synth_image(path, w=640, h=480):
    import numpy as np, cv2
    cv2.imwrite(path, np.full((h, w, 3), 128, np.uint8))


def _run_export(tmp, q, imgs, w, out, extra=()):
    return subprocess.run([sys.executable, os.path.join(HERE, "export_flywheel_dataset.py"),
                           "--queue", q, "--images", imgs, "--withdrawn", w, "--out", out, *extra],
                          capture_output=True, text=True)


def test_匯出產出可訓練的image_mask對():
    try:
        import cv2  # noqa: F401
    except ImportError:
        import pytest; pytest.skip("無 cv2 環境")
    with tempfile.TemporaryDirectory() as tmp:
        imgs = os.path.join(tmp, "images"); os.makedirs(imgs)
        _synth_image(os.path.join(imgs, IID + ".jpg"))
        q = _write(tmp, "q.jsonl", [{**BASE, "mm_per_px": 0.2, "route": "student",
                                     "received_at": "2026-07-28T10:00:00Z"},
                                    {"code": "WD-ORPHAN", "gt_polygon": POLY,
                                     "received_at": "2026-07-01T00:00:00Z"}])
        wd = _write(tmp, "w.jsonl", [])
        out = os.path.join(tmp, "ds")
        r = _run_export(tmp, q, imgs, wd, out)
        assert r.returncode == 0, r.stdout + r.stderr

        import cv2
        stem = f"{BASE['code']}__{IID}"
        assert os.path.exists(os.path.join(out, "images", stem + ".jpg"))
        m = cv2.imread(os.path.join(out, "masks", stem + ".png"), cv2.IMREAD_GRAYSCALE)
        assert m is not None and m.shape == (480, 640)
        px = int((m > 0).sum())
        # fillPoly 對 (10,10)-(200,200) 的矩形含兩端 → 191x191(非 190x190),此為 cv2 既定行為
        assert px == 191 * 191, f"遮罩面積應為 36481,實得 {px}"
        man = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
        assert man["exported"] == 1 and man["queue_stats"]["orphan_no_image"] == 1
        s = man["samples"][0]
        # 面積換算鏈:px × (mm_per_px/10)² = cm²(與 App 修邊面積同一公式)
        assert abs(s["area_cm2"] - px * (0.2 / 10) ** 2) < 1e-3, s["area_cm2"]
        assert os.path.exists(os.path.join(out, "DATASET_CARD.md"))


def test_匯出_檔名碰撞不會靜默覆寫():
    """App 的 code 是毫秒尾 8 碼(~27.8h 循環),跨日必碰撞。
    單用 code 當檔名會讓後者覆蓋前者,manifest 說 2 筆、磁碟只有 1 對。"""
    try:
        import cv2  # noqa: F401
    except ImportError:
        import pytest; pytest.skip("無 cv2 環境")
    with tempfile.TemporaryDirectory() as tmp:
        IID2 = "1111222233334444"
        imgs = os.path.join(tmp, "images"); os.makedirs(imgs)
        _synth_image(os.path.join(imgs, IID + ".jpg"))
        _synth_image(os.path.join(imgs, IID2 + ".jpg"))
        q = _write(tmp, "q.jsonl", [
            {**BASE, "code": "WD-12345678", "image_id": IID, "received_at": "2026-07-28T10:00:00Z"},
            {**BASE, "code": "WD-12345678", "image_id": IID2, "received_at": "2026-07-28T11:00:00Z"},
        ])
        out = os.path.join(tmp, "ds")
        r = _run_export(tmp, q, imgs, _write(tmp, "w.jsonl", []), out)
        assert r.returncode == 0, r.stdout + r.stderr
        files = sorted(os.listdir(os.path.join(out, "images")))
        man = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
        assert len(files) == man["exported"] == 2, (files, man["exported"])
        assert len(set(files)) == 2


def test_匯出_輸出目錄非空需要force_避免留下過期GT():
    """同一天重跑 dataset_<日期> 必撞。若不擋,舊遮罩留在磁碟、manifest 還是上一輪的,
    看起來完全正常但內容過期——比直接報錯危險得多。"""
    try:
        import cv2  # noqa: F401
    except ImportError:
        import pytest; pytest.skip("無 cv2 環境")
    with tempfile.TemporaryDirectory() as tmp:
        imgs = os.path.join(tmp, "images"); os.makedirs(imgs)
        _synth_image(os.path.join(imgs, IID + ".jpg"))
        wd = _write(tmp, "w.jsonl", []); out = os.path.join(tmp, "ds")
        q1 = _write(tmp, "q1.jsonl", [{**BASE, "received_at": "2026-07-28T10:00:00Z"}])
        assert _run_export(tmp, q1, imgs, wd, out).returncode == 0
        # 醫師送了修訂 GT(面積大得多),同目錄重跑
        q2 = _write(tmp, "q2.jsonl", [{**BASE, "gt_polygon": [[5, 5], [500, 5], [500, 400], [5, 400]],
                                       "received_at": "2026-07-28T12:00:00Z"}])
        r = _run_export(tmp, q2, imgs, wd, out)
        assert r.returncode == 1 and "--force" in r.stdout, r.stdout
        r = _run_export(tmp, q2, imgs, wd, out, extra=("--force",))
        assert r.returncode == 0, r.stdout + r.stderr
        import cv2
        m = cv2.imread(os.path.join(out, "masks", f"{BASE['code']}__{IID}.png"), cv2.IMREAD_GRAYSCALE)
        assert int((m > 0).sum()) == 496 * 396, "應為修訂後的遮罩,不是上一輪的舊值"


def test_匯出_全部略過時回非零離開碼():
    with tempfile.TemporaryDirectory() as tmp:
        q = _write(tmp, "q.jsonl", [{"code": "WD-ORPHAN", "gt_polygon": POLY}])
        r = _run_export(tmp, q, os.path.join(tmp, "images"), _write(tmp, "w.jsonl", []),
                        os.path.join(tmp, "ds"))
        assert r.returncode == 1, r.stdout


def test_匯出_min_samples_誠實中止():
    try:
        import cv2  # noqa: F401
    except ImportError:
        import pytest; pytest.skip("無 cv2 環境")
    with tempfile.TemporaryDirectory() as tmp:
        imgs = os.path.join(tmp, "images"); os.makedirs(imgs)
        _synth_image(os.path.join(imgs, IID + ".jpg"))
        q = _write(tmp, "q.jsonl", [{**BASE, "received_at": "2026-07-28T10:00:00Z"}])
        r = _run_export(tmp, q, imgs, _write(tmp, "w.jsonl", []), os.path.join(tmp, "ds"),
                        extra=("--min-samples", "20"))
        assert r.returncode == 1 and "誠實中止" in r.stdout


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))


# ---------- source 標籤 ----------
def test_source_白名單與分項統計():
    """範例/模擬影像可以走同一條管線,但不得混入臨床樣本數(送件數字誠實與否的關鍵)。"""
    assert not fw.validate_annotation({**BASE, "source": "亂填"})[0]
    for s in fw.SOURCES:
        assert fw.validate_annotation({**BASE, "source": s})[0], s
    with tempfile.TemporaryDirectory() as tmp:
        A, B, C = IID, "1111222233334444", "2222333344445555"
        imgs = _imgs(tmp, [A, B, C])
        q = _write(tmp, "q.jsonl", [
            {**BASE, "code": "WD-CLIN", "image_id": A, "source": "clinical",
             "received_at": "2026-07-28T10:00:00Z"},
            {**BASE, "code": "WD-SAMP", "image_id": B, "source": "sample",
             "received_at": "2026-07-28T11:00:00Z"},
            {**BASE, "code": "WD-PHAN", "image_id": C, "source": "phantom",
             "received_at": "2026-07-28T12:00:00Z"},
        ])
        w = _write(tmp, "w.jsonl", [])
        _, st = fw.effective_queue(q, imgs, w)
        assert st["trainable"] == 3 and st["by_source"] == {
            "clinical": 1, "sample": 1, "phantom": 1, "external": 0}, st
        recs, st2 = fw.effective_queue(q, imgs, w, source="clinical")
        assert st2["trainable"] == 1 and st2["other_source"] == 2, st2
        assert recs[0]["code"] == "WD-CLIN"
        # 無 source 欄位的舊紀錄預設為 clinical(不會憑空變成範例)
        q2 = _write(tmp, "q2.jsonl", [{**BASE, "received_at": "2026-07-28T10:00:00Z"}])
        assert fw.effective_queue(q2, imgs, w, source="clinical")[1]["trainable"] == 1


def test_匯出依source篩選():
    try:
        import cv2  # noqa: F401
    except ImportError:
        import pytest; pytest.skip("無 cv2 環境")
    with tempfile.TemporaryDirectory() as tmp:
        A, B = IID, "1111222233334444"
        imgs = os.path.join(tmp, "images"); os.makedirs(imgs)
        _synth_image(os.path.join(imgs, A + ".jpg")); _synth_image(os.path.join(imgs, B + ".jpg"))
        q = _write(tmp, "q.jsonl", [
            {**BASE, "code": "WD-CLIN", "image_id": A, "source": "clinical",
             "received_at": "2026-07-28T10:00:00Z"},
            {**BASE, "code": "WD-PHAN", "image_id": B, "source": "phantom",
             "received_at": "2026-07-28T11:00:00Z"},
        ])
        out = os.path.join(tmp, "ds")
        r = _run_export(tmp, q, imgs, _write(tmp, "w.jsonl", []), out, extra=("--source", "clinical"))
        assert r.returncode == 0, r.stdout + r.stderr
        man = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
        assert man["exported"] == 1 and man["samples"][0]["source"] == "clinical", man["samples"]
        card = open(os.path.join(out, "DATASET_CARD.md"), encoding="utf-8").read()
        assert "clinical 1" in card, card[:400]
