# -*- coding: utf-8 -*-
"""契約測試：組織分割 GT 的收集與匯出（P0）。

鎖住的是一條**看不出來就會毀掉整個訓練集**的界線：

  **未經醫師修正的遮罩不得進入訓練集。**

  醫師若沒動組織筆刷，遮罩就是 `TissueSeg` 色彩啟發式的原樣輸出。拿它當 GT 訓練
  ＝用模型自己的輸出訓練自己：驗證 Dice 會很漂亮，因為模型只是在學會複製它已經
  會做的事，而臨床表現不會有任何改善。而且**從資料本身完全看不出來**——
  遮罩、影像、溯源欄位全都長得像正常樣本。

  唯一的分辨依據是 `tissue_edited`，所以它必須：
    (a) 由 App 誠實回報（依 tissue 與 auto 的差異計算，不是一個布林開關）
    (b) 匯出時預設排除，且被排除的筆數要出現在回應裡

其次鎖住：遮罩落盤失敗不可拖垮整筆標註、品質門檻可調且缺指標不擋、
撤回與誤送排除的樣本不得出現在資料集裡。

    python engineering/phase2/test_tissue_dataset.py
"""
import base64
import hashlib
import importlib
import json
import os
import shutil
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
FLASK_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))
FAILED = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


def make_png(w=8, h=8, val=1):
    """最小合法 PNG（8-bit RGBA），值放在 R 通道——與 TissueMaskCodec 的編碼一致。"""
    raw = b"".join(b"\x00" + bytes([val, 0, 0, 255] * w) for _ in range(h))
    def chunk(tag, data):
        c = tag + data
        return len(data).to_bytes(4, "big") + c + zlib.crc32(c).to_bytes(4, "big")
    ihdr = w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes([8, 6, 0, 0, 0])
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_jpeg():
    try:
        from PIL import Image
        import io
        b = io.BytesIO(); Image.new("RGB", (60, 40), (170, 90, 80)).save(b, "JPEG")
        return b.getvalue()
    except Exception:
        return b"\xff\xd8\xff\xe0" + b"woundai" * 32 + b"\xff\xd9"


def main():
    tmp = tempfile.mkdtemp(prefix="woundai_tissue_")
    os.environ["WOUNDAI_FLYWHEEL_DIR"] = tmp
    for sub in ("images", "quarantine", "tissue_masks"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    for f in ("retrain_queue.jsonl", "withdrawn.jsonl", "audit.jsonl",
              "users.jsonl", "retracted.jsonl"):
        open(os.path.join(tmp, f), "w").close()

    sys.path.insert(0, FLASK_DIR)
    fw = importlib.import_module("api_flywheel")

    from flask import Flask
    from flask_jwt_extended import JWTManager, create_access_token
    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = "test-only"
    JWTManager(app)
    app.register_blueprint(fw.flywheel_bp)
    cli = app.test_client()

    def tok(user, role):
        with app.app_context():
            return create_access_token(identity="default:%s" % user,
                                       additional_claims={"role": role, "org": "default", "user": user})
    H = {"physician": {"Authorization": "Bearer " + tok("dr01", "physician")},
         "engineer": {"Authorization": "Bearer " + tok("eng01", "engineer")},
         "nurse": {"Authorization": "Bearer " + tok("ns01", "nurse")},
         "admin": {"Authorization": "Bearer " + tok("admin", "admin")}}

    def post(path, role, body):
        r = cli.post(path, json=body, headers=H[role]); return r.status_code, (r.get_json() or {})

    def get(path, role):
        r = cli.get(path, headers=H[role]); return r.status_code, (r.get_json() or {})

    ids = {}
    for tag in ("edited", "raw", "bad", "big", "poor", "gone"):
        jpg = make_jpeg() + tag.encode()
        iid = hashlib.sha1(jpg).hexdigest()[:16]
        open(os.path.join(tmp, "images", iid + ".jpg"), "wb").write(jpg)
        ids[tag] = iid

    GOOD_Q = {"focus_lapvar": 180.0, "clipped_frac": 0.01, "marker_skew": 0.05,
              "marker_frac": 0.09, "roi_short_px": 700}

    def anno(tag, code, mask=None, edited=True, quality=None):
        d = {"code": code, "gt_polygon": [[5, 5], [50, 5], [50, 35], [5, 35]], "exudate": 1,
             "doctor_verified": True, "deidentified": True, "consent_train": True,
             "image_id": ids[tag], "image_w": 60, "image_h": 40, "mm_per_px": 0.2,
             "source": "clinical", "quality": quality if quality is not None else dict(GOOD_Q),
             "tissue_frac": {"granulation": 0.6, "slough": 0.3, "necrosis": 0.0,
                             "epithelial": 0.0, "other": 0.1}}
        if mask is not None:
            d["tissue_mask_png"] = base64.b64encode(mask).decode()
            d["tissue_raster"] = {"rx0": 0, "ry0": 0, "mw": 8, "mh": 8, "m_scale": 0.2}
            d["tissue_edited"] = edited
            d["tissue_edit_px"] = 40 if edited else 0
            d["tissue_edit_ratio"] = 0.31 if edited else 0.0
        return d

    # ── 1 遮罩落盤 ────────────────────────────────────────────────
    s, r = post("/api/v1/annotation", "physician", anno("edited", "WD-EDIT", make_png()))
    check("1  帶遮罩的標註入列", s == 200 and r.get("status") == "enqueued", (s, r))
    check("1b 遮罩落盤為 PNG",
          os.path.exists(os.path.join(tmp, "tissue_masks", ids["edited"] + ".png")))
    aud = [json.loads(l) for l in open(os.path.join(tmp, "audit.jsonl"), encoding="utf-8")]
    check("1c 落盤留痕（含醫師修正量）",
          any(a["action"] == "tissue_mask_stored" and "醫師修正" in a["result"] for a in aud))

    # ── 2 遮罩失敗不可拖垮整筆標註 ────────────────────────────────
    #
    # 遮罩缺席只是少一個組織訓練樣本；讓整筆標註失敗會連傷口分割的 GT 一起丟掉。
    s, r = post("/api/v1/annotation", "physician", anno("bad", "WD-BAD", make_jpeg()))
    check("2  送 JPEG 當遮罩 → 標註仍成功", s == 200 and r.get("status") == "enqueued", (s, r))
    check("2b 但遮罩沒落盤",
          not os.path.exists(os.path.join(tmp, "tissue_masks", ids["bad"] + ".png")))
    aud = [json.loads(l) for l in open(os.path.join(tmp, "audit.jsonl"), encoding="utf-8")]
    check("2c 拒收理由留痕（JPEG 會糊掉類別邊界）",
          any(a["action"] == "tissue_mask_rejected" and "PNG" in a["result"] for a in aud))

    s, _ = post("/api/v1/annotation", "physician",
                anno("big", "WD-BIG", make_png() + b"\x00" * (5 * 1024 * 1024)))
    check("2d 超大遮罩被拒但標註仍成功", s == 200)
    aud = [json.loads(l) for l in open(os.path.join(tmp, "audit.jsonl"), encoding="utf-8")]
    check("2e 超大拒收留痕", any(a["action"] == "tissue_mask_rejected" and "4MB" in a["result"] for a in aud))

    # ── 3 ★核心★ 未經醫師修正的遮罩預設排除 ───────────────────────
    s, _ = post("/api/v1/annotation", "physician",
                anno("raw", "WD-RAW", make_png(val=3), edited=False))
    check("3  未修正的遮罩仍會落盤（要留著才能事後檢視與比對）",
          s == 200 and os.path.exists(os.path.join(tmp, "tissue_masks", ids["raw"] + ".png")))

    s, m = get("/api/v1/dataset/manifest?kind=tissue", "engineer")
    got = {i["code"] for i in m["items"]}
    check("3b 匯出清單**預設排除**未經醫師修正者", s == 200 and "WD-RAW" not in got, sorted(got))
    check("3c WD-EDIT 在清單裡", "WD-EDIT" in got, sorted(got))
    check("3d 排除原因與筆數要現形（不能靜默消失）",
          m["excluded"].get("tissue_not_edited") == 1, m["excluded"])
    check("3e 沒有遮罩的那筆歸在 no_tissue_mask", m["excluded"].get("no_tissue_mask", 0) >= 1, m["excluded"])
    check("3f require_edited 明確回報", m["require_edited"] is True)

    s, m2 = get("/api/v1/dataset/manifest?kind=tissue&require_edited=0", "engineer")
    check("3g require_edited=0 才看得到未修正者",
          "WD-RAW" in {i["code"] for i in m2["items"]} and m2["require_edited"] is False)
    check("3h 每一筆都標明是否經醫師修正（訓練端可再篩一次）",
          all("tissue_edited" in i for i in m2["items"]))

    # ── 4 品質門檻 ────────────────────────────────────────────────
    s, _ = post("/api/v1/annotation", "physician",
                anno("poor", "WD-POOR", make_png(val=2),
                     quality={"focus_lapvar": 12.0, "clipped_frac": 0.01,
                              "marker_skew": 0.05, "marker_frac": 0.09, "roi_short_px": 700}))
    _, m3 = get("/api/v1/dataset/manifest?kind=tissue", "engineer")
    check("4  模糊影像被門檻擋下", "WD-POOR" not in {i["code"] for i in m3["items"]}
          and m3["excluded"].get("blurry") == 1, m3["excluded"])
    _, m4 = get("/api/v1/dataset/manifest?kind=tissue&min_focus=1", "engineer")
    check("4b 門檻可調（不是寫死的）——臨床顯示與訓練集本來就該用不同嚴格度",
          "WD-POOR" in {i["code"] for i in m4["items"]})
    check("4c 回應帶出實際使用的門檻（否則兩次匯出的差異無從解釋）",
          m4["thresholds"]["min_focus"] == 1.0, m4["thresholds"])

    # 缺品質指標的舊紀錄**不擋**：那些影像本身沒問題，只是收集時還沒有這個欄位
    s, _ = post("/api/v1/annotation", "physician",
                anno("gone", "WD-OLD", make_png(val=4), quality={}))
    _, m5 = get("/api/v1/dataset/manifest?kind=tissue", "engineer")
    check("4d 缺品質指標的舊紀錄不被排除", "WD-OLD" in {i["code"] for i in m5["items"]},
          sorted(i["code"] for i in m5["items"]))

    # ── 5 同意與排除規則一路貫穿到資料集 ──────────────────────────
    s, _ = post("/api/v1/consent/withdraw", "physician",
                {"code": "WD-EDIT", "image_id": ids["edited"], "reason": "病患撤回"})
    _, m6 = get("/api/v1/dataset/manifest?kind=tissue", "engineer")
    check("5  撤回同意後不再出現在資料集裡（規則在控制面執行）",
          "WD-EDIT" not in {i["code"] for i in m6["items"]}, sorted(i["code"] for i in m6["items"]))
    s, _ = post("/api/v1/retract", "physician",
                {"image_id": ids["gone"], "reason": "wrong_source", "note": "範例圖"})
    _, m7 = get("/api/v1/dataset/manifest?kind=tissue", "engineer")
    check("5b 誤送排除後也不再出現", "WD-OLD" not in {i["code"] for i in m7["items"]})

    # ── 6 權限與稽核 ──────────────────────────────────────────────
    denied = {r: get("/api/v1/dataset/manifest", r)[0] for r in ("physician", "nurse")}
    check("6  臨床角色不得匯出資料集", set(denied.values()) == {403}, denied)
    check("6b 管理者可以", get("/api/v1/dataset/manifest", "admin")[0] == 200)
    aud = [json.loads(l) for l in open(os.path.join(tmp, "audit.jsonl"), encoding="utf-8")]
    check("6c 每次匯出都進稽核（誰在什麼時候帶走了哪些資料）",
          any(a["action"] == "dataset_manifest" for a in aud))
    check("6d 稽核記下合格筆數與排除原因",
          any(a["action"] == "dataset_manifest" and "排除" in a["result"] for a in aud))

    # ── 7 manifest 內容足以重建資料集 ─────────────────────────────
    _, mm = get("/api/v1/dataset/manifest?kind=tissue&require_edited=0&min_focus=1", "engineer")
    it = mm["items"][0]
    need = ("image_key", "tissue_mask_key", "tissue_raster", "image_w", "image_h",
            "mm_per_px", "actor", "quality")
    check("7  每筆帶齊重建所需欄位", all(k in it for k in need), sorted(it))
    check("7b 帶 actor（切分訓練/驗證時避免同一標註者橫跨兩邊，否則指標虛高）",
          it["actor"].startswith("default:"))
    check("7c 帶仿射參數（遮罩柵格 → 影像座標）",
          set(it["tissue_raster"] or {}) >= {"rx0", "ry0", "mw", "mh", "m_scale"}, it.get("tissue_raster"))
    check("7d 不含任何 PII", not any(k in json.dumps(mm, ensure_ascii=False)
                                    for k in ("name", "mrn", "birth")))

    # ── 8 ★平行標註★ 兩位醫師標同一張影像 ────────────────────────
    #
    # 舊規則只用 image_id 當「取最新」的鍵，於是第二位醫師的標註會把第一位的
    # 標成 superseded 而排除——但那兩筆不是修訂關係，是**兩個人的獨立判斷**，
    # 而那正是 inter-rater 一致性分析唯一的資料來源。
    H["dr2"] = {"Authorization": "Bearer " + tok("dr02", "physician")}
    jpg = make_jpeg() + b"pair"
    pid = hashlib.sha1(jpg).hexdigest()[:16]
    open(os.path.join(tmp, "images", pid + ".jpg"), "wb").write(jpg)
    ids["pair"] = pid

    s1, _ = post("/api/v1/annotation", "physician", anno("pair", "WD-PAIR", make_png(val=1)))
    d2 = anno("pair", "WD-PAIR", make_png(val=2))
    d2["gt_polygon"] = [[6, 6], [49, 6], [49, 34], [6, 34]]      # 略有不同的邊界
    r2 = cli.post("/api/v1/annotation", json=d2, headers=H["dr2"])
    j2 = r2.get_json() or {}
    check("8  第二位醫師標同一張影像可以送出", s1 == 200 and r2.status_code == 200, r2.status_code)
    check("8b 回應說的是「平行標註」而非「你的修訂版」（稽核不可記載沒發生的事）",
          "平行標註" in (j2.get("note") or ""), j2.get("note"))
    aud = [json.loads(l) for l in open(os.path.join(tmp, "audit.jsonl"), encoding="utf-8")]
    check("8c 稽核動作是 annotation_parallel，不是 annotation_revised",
          any(a["action"] == "annotation_parallel" for a in aud))

    tagged = fw.classify_queue(fw.read_jsonl(fw.QUEUE))
    pair = [(r.get("actor"), st) for r, st in tagged if r.get("image_id") == pid]
    check("8d 兩筆都保留下來（一筆 trainable、一筆 parallel_rater）",
          sorted(st for _, st in pair) == ["parallel_rater", "trainable"], pair)
    check("8e 主標註者是**先送的那位**——取最新的話每多一位醫師訓練集就換一批，前後無從比較",
          [a for a, st in pair if st == "trainable"] == ["default:dr01"], pair)

    _, mt = get("/api/v1/dataset/manifest?kind=tissue", "engineer")
    check("8f 訓練集每張圖只取一份（同一張圖出現兩份 GT 會佔兩倍權重）",
          sum(1 for i in mt["items"] if i["image_id"] == pid) == 1)

    _, ir = get("/api/v1/dataset/manifest?kind=interrater", "engineer")
    check("8g interrater 清單找得到這張", ir["count"] == 1 and ir["pairs"] == 1, (ir["count"], ir["pairs"]))
    raters = {r["actor"] for r in ir["items"][0]["raters"]}
    check("8h 兩位標註者都在，且各自帶自己的遮罩",
          raters == {"default:dr01", "default:dr02"}
          and all(r["tissue_mask_key"] for r in ir["items"][0]["raters"]), raters)
    check("8i 匯出留痕", any(a["action"] == "dataset_manifest" and "interrater" in a["result"] for a in
                            [json.loads(l) for l in open(os.path.join(tmp, "audit.jsonl"), encoding="utf-8")]))

    # 同一人再送一次 → 那才是修訂，應取代自己先前那筆
    d3 = anno("pair", "WD-PAIR", make_png(val=3))
    d3["gt_polygon"] = [[7, 7], [48, 7], [48, 33], [7, 33]]
    r3 = cli.post("/api/v1/annotation", json=d3, headers=H["dr2"])
    n3 = (r3.get_json() or {}).get("note") or ""
    # 這一筆**同時**是「相對 dr01 的平行標註」與「相對 dr02 自己的修訂版」。
    # 訊息只講其中一件，稽核就記載了不完整的事實。
    check("8j 同一人再送 → 訊息同時說出平行標註與取代自己先前那筆",
          "平行標註" in n3 and "取代你自己先前" in n3, n3)
    tagged = fw.classify_queue(fw.read_jsonl(fw.QUEUE))
    dr02 = [st for r, st in tagged if r.get("image_id") == pid and r.get("actor") == "default:dr02"]
    check("8k dr02 自己的舊那筆被 superseded，只留最新",
          sorted(dr02) == ["parallel_rater", "superseded"], dr02)

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILED:
        print("FAILED %d 項：%s" % (len(FAILED), "; ".join(FAILED)))
        return 1
    print("全部通過：未經醫師修正的遮罩預設不進訓練集且被排除的筆數會現形；"
          "遮罩失敗不拖垮標註；同意、撤回與品質門檻一路貫穿到資料集匯出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
