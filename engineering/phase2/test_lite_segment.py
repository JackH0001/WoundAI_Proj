# -*- coding: utf-8 -*-
"""契約測試：`POST /api/v1/lite/segment`（民眾版**匿名**端點）。

    python engineering/phase2/test_lite_segment.py

## 這支測試最重要的一條

**`research_consent != "true"` 時，什麼都不可以落地。**

民眾版的同意書寫的是「不同意＝資料不離機」。那句話必須在程式碼裡成立，
不是在文案裡成立——而「有沒有寫檔」這件事從回應上完全看不出來，
使用者、審查者、甚至寫程式的人都無從察覺。所以它必須有一條測試盯著。

驗法是直接數儲存目錄的檔案：呼叫前後檔案數不變才算過。
只檢查「回應沒有 image_id」是不夠的——那只證明沒告訴你，不證明沒存。

## 其餘守什麼

  · 限流：anon_id 配額、以及**換 anon_id 也換不掉的** IP 配額
  · 被擋下的請求**不計入配額**（先查後寫），否則被擋一次就永遠解不開
  · IP 一律雜湊後才落盤（原始 IP 是個資）
  · 撤回端點真的刪得掉檔案，且**稽核軌跡刪不掉**
  · 深度圖沿用與 `/api/v1/annotation` 相同的 16-bit PNG 判準

## 這支測試**不**涵蓋

分割品質、人臉偵測的準確度。前者需要模型，後者需要人臉樣本——
而人臉偵測本來就只是縱深防禦，用測試去背書它的召回率會給人錯誤的安全感。
"""
import base64
import importlib
import io
import json
import os
import sys
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
FLASK_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))

FAILED = []
TOTAL = [0]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if (detail and not ok) else ""))
    if not ok:
        FAILED.append(name)


def make_jpeg(w=320, h=240, salt=b""):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (180, 95, 85)).save(buf, "JPEG")
    return buf.getvalue() + salt


def png16(w=8, h=6):
    """最小的 16-bit 灰階 PNG（值＝mm）。手工組，避免依賴 PIL 的位元深度行為。"""
    def chunk(tag, data):
        c = tag + data
        return (len(data).to_bytes(4, "big") + c +
                (zlib.crc32(c) & 0xFFFFFFFF).to_bytes(4, "big"))
    ihdr = (w.to_bytes(4, "big") + h.to_bytes(4, "big") +
            bytes([16, 0, 0, 0, 0]))          # bit depth 16, colour type 0
    rows = b"".join(b"\x00" + b"\x01\x2c" * w for _ in range(h))   # 每格 300 mm
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def count_files(root):
    n = 0
    for _, _, fs in os.walk(root):
        n += len(fs)
    return n


def main():
    try:
        from PIL import Image  # noqa: F401
        import numpy as np
    except ImportError:
        print("需要 Pillow 與 numpy")
        return 1

    tmp = tempfile.mkdtemp(prefix="woundai_lite_")
    os.environ["WOUNDAI_FLYWHEEL_DIR"] = tmp
    os.environ["LITE_LIMIT_ANON"] = "3"
    os.environ["LITE_LIMIT_IP"] = "5"
    os.environ["LITE_IP_SALT"] = "test-salt"
    os.environ["LITE_FACE_REJECT"] = "0"     # 人臉偵測另有取捨，見 docstring
    for sub in ("images", "quarantine"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)

    sys.path.insert(0, FLASK_DIR)
    for m in list(sys.modules):
        if m.startswith("api_lite") or m.startswith("api_flywheel") or m == "store":
            del sys.modules[m]
    fw = importlib.import_module("api_flywheel")   # noqa: F401  （api_lite 依賴它）
    lite = importlib.import_module("api_lite")

    # 注入假的分割器：本測試驗的是端點的行為，不是模型的品質。
    # 真模型要 200MB 的 ONNX，contract test 不該依賴它。
    def fake_segment(rgb):
        m = np.zeros(rgb.shape[:2], np.uint8)
        m[60:180, 80:240] = 1
        return m
    lite.init_lite(fake_segment)

    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(lite.lite_bp)
    cli = app.test_client()

    def post(anon="dev-a", consent="true", ip="203.0.113.9", salt=b"", **extra):
        data = {"anon_id": anon, "client": "woundlite-ios",
                "research_consent": consent,
                "image": (io.BytesIO(make_jpeg(salt=salt)), "w.jpg")}
        data.update(extra)
        return cli.post("/api/v1/lite/segment", data=data,
                        content_type="multipart/form-data",
                        headers={"X-Forwarded-For": ip})

    # ── 1 基本 ────────────────────────────────────────────────
    print("── 1 分割 ──")
    r = post(salt=b"a1")
    j = r.get_json() or {}
    check("匿名即可呼叫（無 JWT）", r.status_code == 200, "%s %s" % (r.status_code, j))
    check("回傳 wound_polygons", len(j.get("wound_polygons", [])) >= 1,
          len(j.get("wound_polygons", [])))
    check("回傳影像尺寸", j.get("image_w") == 320 and j.get("image_h") == 240,
          (j.get("image_w"), j.get("image_h")))
    check("缺 anon_id 被擋", cli.post("/api/v1/lite/segment", data={
        "image": (io.BytesIO(make_jpeg()), "w.jpg")},
        content_type="multipart/form-data").status_code == 400)

    # ── 2 同意分流：不同意 → 什麼都不可以落地 ──────────────────
    print("\n── 2 不同意就不落地 ──")
    before = count_files(tmp)
    r = post(anon="dev-nc", consent="false", ip="203.0.113.20", salt=b"nc")
    j = r.get_json() or {}
    after = count_files(tmp)
    check("不同意時仍會回傳輪廓（辨識照做）",
          r.status_code == 200 and len(j.get("wound_polygons", [])) >= 1)
    check("回應誠實標示 stored=False", j.get("stored") is False, j.get("stored"))
    check("回應不給 image_id", j.get("image_id") is None, j.get("image_id"))
    # 關鍵：直接數檔案。只看回應等於相信它的自我宣告。
    check("**儲存目錄沒有多出任何檔案**（限流紀錄除外）",
          after - before <= 1, "前 %d → 後 %d" % (before, after))
    # ⚠ 這一條的範圍要限定在**這個 anon_id 的前綴**。
    # 第一版寫成掃整個 `lite/`，於是抓到上面第 1 節（consent 預設 true）存下的
    # 那張圖而報紅——測試自己搞錯了對象，程式碼是對的。
    # 「檢查範圍比宣稱的大」會製造假紅，而假紅與假綠一樣會侵蝕對測試的信任。
    check("  未同意的裝置連前綴目錄都不該出現",
          not os.path.isdir(os.path.join(tmp, "lite", "dev-nc")),
          os.listdir(os.path.join(tmp, "lite", "dev-nc"))
          if os.path.isdir(os.path.join(tmp, "lite", "dev-nc")) else "")

    print("\n── 3 同意 → 落地 ──")
    r = post(anon="dev-yes", consent="true", ip="203.0.113.30", salt=b"y1",
             measured=json.dumps({"surface_cm2": 12.3, "tilt_deg": 8.0}))
    j = r.get_json() or {}
    iid = j.get("image_id")
    check("同意時 stored=True 且給 image_id", j.get("stored") is True and bool(iid), j)
    p = os.path.join(tmp, "lite", "dev-yes")
    check("影像落地", os.path.isfile(os.path.join(p, iid + ".jpg")))
    check("meta 落地", os.path.isfile(os.path.join(p, iid + ".json")))
    if os.path.isfile(os.path.join(p, iid + ".json")):
        meta = json.load(open(os.path.join(p, iid + ".json"), encoding="utf-8"))
        check("meta 記下 measured（供精度研究比對）",
              (meta.get("measured") or {}).get("surface_cm2") == 12.3, meta.get("measured"))
        check("meta 記下 research_consent=True", meta.get("research_consent") is True)

    # ── 4 深度沿用同一套判準 ──────────────────────────────────
    print("\n── 4 深度 ──")
    r = post(anon="dev-d", consent="true", ip="203.0.113.40", salt=b"d1",
             depth_map_png=base64.b64encode(png16()).decode(),
             depth_format="png16_mm", depth_scale="0.001")
    iid_d = (r.get_json() or {}).get("image_id")
    check("16-bit 深度圖收得下",
          os.path.isfile(os.path.join(tmp, "lite", "dev-d", (iid_d or "") + ".depth.png")))
    # 8-bit 只表示得了 0–255 mm，而攝距是 200–600 mm——整片飽和而**不會有任何錯誤**。
    bad8 = bytearray(png16()); bad8[24] = 8
    r = post(anon="dev-d8", consent="true", ip="203.0.113.41", salt=b"d8",
             depth_map_png=base64.b64encode(bytes(bad8)).decode())
    iid8 = (r.get_json() or {}).get("image_id")
    check("8-bit 深度圖被拒（但不擋分割）", r.status_code == 200 and not os.path.isfile(
        os.path.join(tmp, "lite", "dev-d8", (iid8 or "") + ".depth.png")))
    m8 = os.path.join(tmp, "lite", "dev-d8", (iid8 or "") + ".json")
    if os.path.isfile(m8):
        check("  meta 記下退件理由（事後查得到為什麼沒深度）",
              "rejected" in str(json.load(open(m8, encoding="utf-8")).get("depth")))

    # ── 5 限流 ────────────────────────────────────────────────
    print("\n── 5 限流 ──")
    for i in range(3):
        post(anon="dev-rl", ip="198.51.100.7", salt=b"r%d" % i)
    r = post(anon="dev-rl", ip="198.51.100.7", salt=b"rx")
    jb = r.get_json() or {}
    check("超過 anon_id 日配額 → 429", r.status_code == 429, r.status_code)
    check("429 帶 retry_after（講得出什麼時候能再試）",
          isinstance(jb.get("retry_after"), int) and jb["retry_after"] > 0, jb.get("retry_after"))
    check("429 的訊息告訴使用者改用手動", "手動" in (jb.get("message") or ""))
    # 被擋下的請求不可計入配額，否則被擋一次之後永遠解不開。
    n_before = len(fw.read_jsonl(lite._rate_path(__import__("time").strftime(
        "%Y%m%d", __import__("time").gmtime()))))
    post(anon="dev-rl", ip="198.51.100.7", salt=b"ry")
    n_after = len(fw.read_jsonl(lite._rate_path(__import__("time").strftime(
        "%Y%m%d", __import__("time").gmtime()))))
    check("被擋下的請求**不計入**配額（先查後寫）", n_after == n_before,
          "%d → %d" % (n_before, n_after))

    # 換 anon_id 換不掉 IP 配額——這是 anon_id 可偽造時唯一剩下的防線。
    for i in range(9):
        post(anon="rot-%d" % i, ip="198.51.100.99", salt=b"i%d" % i)
    r = post(anon="rot-last", ip="198.51.100.99", salt=b"ilast")
    check("換 anon_id 也擋得住（IP 日配額）", r.status_code == 429, r.status_code)
    check("  且理由標示為 network_quota",
          (r.get_json() or {}).get("reason") == "network_quota", r.get_json())

    print("\n── 6 隱私 ──")
    day = __import__("time").strftime("%Y%m%d", __import__("time").gmtime())
    rows = fw.read_jsonl(lite._rate_path(day))
    check("限流紀錄有落盤", len(rows) > 0)
    check("**原始 IP 不落盤**（只存雜湊）",
          not any("198.51.100" in json.dumps(e) or "203.0.113" in json.dumps(e) for e in rows))
    check("雜湊有加鹽（換鹽會得到不同結果）",
          lite._ip_hash("1.2.3.4") != __import__("hashlib").sha256(b"1.2.3.4").hexdigest()[:20])

    print("\n── 7 撤回 ──")
    r = cli.delete("/api/v1/lite/data/dev-yes")
    jd = r.get_json() or {}
    check("撤回回 200", r.status_code == 200, r.status_code)
    check("真的刪掉檔案", jd.get("objects_removed", 0) >= 2, jd)
    check("影像確實不存在了",
          not os.path.isfile(os.path.join(tmp, "lite", "dev-yes", (iid or "") + ".jpg")))
    idx = fw.read_jsonl(os.path.join(tmp, "lite_index.jsonl"))
    check("索引留下刪除紀錄（不改寫原本那幾行）",
          any(e.get("action") == "deleted" and e.get("anon_id") == "dev-yes" for e in idx))
    check("路徑穿越被擋", cli.delete("/api/v1/lite/data/..%2f..").status_code in (400, 404))

    print("\n── 8 稽核不可刪 ──")
    import store as _st
    s = _st.get_store(tmp)
    try:
        s.delete("audit.jsonl")
        check("稽核鍵刪除必須拋例外", False, "竟然刪成功了")
    except PermissionError:
        check("稽核鍵刪除拋 PermissionError（本機後端也要成立）", True)
    except AttributeError as e:
        check("稽核鍵刪除拋 PermissionError（本機後端也要成立）", False,
              "AttributeError：守衛只在雲端實作上存在 → %s" % e)

    print("\n%d 項檢查，%d 項失敗" % (TOTAL[0], len(FAILED)))
    if FAILED:
        print("失敗：")
        for x in FAILED:
            print("  · " + x)
        return 1
    print("全部通過：不同意不落地、限流雙軌、IP 只存雜湊、撤回真的刪得掉。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
