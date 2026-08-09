# -*- coding: utf-8 -*-
"""契約測試：`POST /api/v1/depth`（WoundAI3D 的原始深度補傳）。

    python engineering/phase2/test_depth_endpoint.py

## 這支測試在防什麼

深度圖**只存在於拍攝當下，事後補不回來**。所以一份收下來卻不能用的深度檔，
損失不是「一個壞檔案」，而是「那一次拍攝永遠沒有 3D 資料」——而且要到半年後
有人真的要建模時才會發現。

原始 float32 **沒有任何自我描述**。16-bit PNG 至少有 IHDR，位元深度寫在檔頭裡；
f32 沒有。以下四種壞資料在位元組層面看起來完全一樣，都只是一串 bytes：

  · 傳到一半斷線的（截斷）
  · 未寫入的緩衝區（全零）
  · 大端序寫出來的
  · 單位是毫米而不是公尺的

它們不會在上傳時出錯，會在建模時才現形。所以守門要在**收下之前**做完，
而且退件理由要講得夠具體，讓人知道該改什麼——退件會被看見並重傳，
收下不能用的檔案則會安靜地變成假庫存。

## 側檔 join

佇列（`retrain_queue.jsonl`）是唯讀累加的稽核物件。iOS 的深度是 sidecar：
標註先送、有網路才補傳，那時標註早就落盤了，改不得。所以補傳寫進
`depth_index.jsonl`，由清單端 join。

**沒有這個 join 的話**：補傳成功的樣本在主控台上會永遠顯示 `lidar_local`
（＝拍了沒傳），而檔案其實躺在 `depth_maps/`。盤點資料集時會少算。
"""
import hashlib
import importlib
import io
import json
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FLASK_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))

FAILED = []
TOTAL = [0]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if detail else ""))
    if not ok:
        FAILED.append(name)


W, H = 16, 12
K = {"fx": 500.0, "fy": 500.0, "cx": 8.0, "cy": 6.0, "ref_width": 16, "ref_height": 12}


def depth_bytes(value=0.30, n=W * H, little=True):
    return struct.pack(("<" if little else ">") + "%df" % n, *([value] * n))


def meta(**over):
    m = {"width": W, "height": H, "format": "f32_le_meters", "camera_intrinsics": dict(K)}
    m.update(over)
    return m


def make_jpeg(salt=b""):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (170, 90, 80)).save(buf, "JPEG")
    return buf.getvalue() + salt


def main():
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("需要 Pillow：pip install pillow")
        return 1

    tmp = tempfile.mkdtemp(prefix="woundai_depth_")
    os.environ["WOUNDAI_FLYWHEEL_DIR"] = tmp
    os.environ["JWT_SECRET_KEY"] = "test-secret-key-please-ignore-0000"
    for sub in ("images", "quarantine", "tissue_masks", "depth_maps"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    for f in ("retrain_queue.jsonl", "withdrawn.jsonl", "audit.jsonl",
              "users.jsonl", "retracted.jsonl", "depth_index.jsonl"):
        open(os.path.join(tmp, f), "w").close()

    sys.path.insert(0, FLASK_DIR)
    for m in list(sys.modules):
        if m.startswith("api_flywheel"):
            del sys.modules[m]
    fw = importlib.import_module("api_flywheel")
    from flask import Flask
    from flask_jwt_extended import JWTManager, create_access_token
    app = Flask(__name__)
    app.config["JWT_SECRET_KEY"] = "test-only-please-ignore"
    JWTManager(app)
    app.register_blueprint(fw.flywheel_bp)
    cli = app.test_client()
    with app.app_context():
        H_DR = {"Authorization": "Bearer " + create_access_token(
            identity="default:dr01",
            additional_claims={"role": "physician", "org": "default", "user": "dr01"})}
        H_NURSE = {"Authorization": "Bearer " + create_access_token(
            identity="default:ns01",
            additional_claims={"role": "nurse", "org": "default", "user": "ns01"})}

    POLY = [[100, 100], [300, 100], [300, 400], [100, 400]]

    def submit(code, salt):
        jpg = make_jpeg(salt)
        iid = hashlib.sha1(jpg).hexdigest()[:16]
        open(os.path.join(tmp, "images", iid + ".jpg"), "wb").write(jpg)
        r = cli.post("/api/v1/annotation", headers=H_DR, json={
            "code": code, "gt_polygon": POLY, "exudate": 1, "image_id": iid,
            "image_w": 640, "image_h": 480, "mm_per_px": 0.5,
            "doctor_verified": True, "deidentified": True, "consent_train": True,
            "route": "cloud", "source": "sample", "depth_source": "lidar_local",
        })
        return r, iid

    def upload(iid, raw, m, headers=None):
        return cli.post("/api/v1/depth", headers=headers or H_DR,
                        data={"image_id": iid, "meta": json.dumps(m),
                              "depth_f32": (io.BytesIO(raw), "d.f32")},
                        content_type="multipart/form-data")

    # ── 1 純函式守門 ────────────────────────────────────────────
    print("── 1 守門（validate_depth_payload） ──")
    ok, iss = fw.validate_depth_payload(depth_bytes(), meta())
    check("正常的 f32 公尺深度圖通過", ok, iss)

    ok, iss = fw.validate_depth_payload(depth_bytes()[:-40], meta())
    check("截斷的資料被擋（長度 ≠ w×h×4）", not ok and any("截斷" in i for i in iss), iss)

    ok, iss = fw.validate_depth_payload(depth_bytes(), meta(width=None))
    check("缺 width/height 被擋（沒有尺寸就驗不了完整性）", not ok, iss)

    m = meta(); m["camera_intrinsics"].pop("fx")
    ok, iss = fw.validate_depth_payload(depth_bytes(), m)
    check("缺內參被擋（反投影不了＝存下來也用不了）",
          not ok and any("fx" in i for i in iss), iss)

    ok, iss = fw.validate_depth_payload(b"\x00" * (W * H * 4), meta())
    check("全零（未寫入的緩衝區）被擋", not ok, iss)
    check("  而且訊息點出「未寫入的緩衝區」", any("未寫入" in i for i in iss), iss)

    # 單位寫成毫米：值是 300 而不是 0.3。這是最容易犯、也最難事後發現的錯——
    # 反投影出來的點雲會放大 1000 倍，而形狀完全正常。
    ok, iss = fw.validate_depth_payload(depth_bytes(300.0), meta())
    check("單位是毫米（值 ~300）被擋", not ok, iss)
    check("  而且訊息點出可能是毫米", any("毫米" in i for i in iss), iss)

    # 大端序：0.3 的 BE 位元組讀成 LE 是 ~4.6e-40，落在有效範圍外。
    ok, iss = fw.validate_depth_payload(depth_bytes(0.30, little=False), meta())
    check("大端序寫出來的被擋（LE 讀出來全是次正規數）", not ok, iss)

    ok, iss = fw.validate_depth_payload(depth_bytes(), meta(format="u16_mm"))
    check("宣告了不支援的 format 被擋（本端點不做單位轉換）", not ok, iss)

    m = meta()
    fw.validate_depth_payload(depth_bytes(0.30), m)
    check("覆蓋率與最小/最大距離自動寫回 meta",
          m.get("coverage") == 1.0 and abs(m.get("min_m", 0) - 0.30) < 1e-3,
          "coverage=%s min=%s max=%s" % (m.get("coverage"), m.get("min_m"), m.get("max_m")))

    # ── 2 端點 ────────────────────────────────────────────────
    print("\n── 2 端點 ──")
    r, iid = submit("WD-DEPTH1", b"d1")
    check("前置：標註送出成功", r.status_code == 200, r.status_code)

    r = upload(iid, depth_bytes(), meta())
    j = r.get_json() or {}
    check("深度補傳成功", r.status_code == 200, "%s %s" % (r.status_code, j.get("issues") or ""))
    check("回傳 depth_id", bool(j.get("depth_id")), j.get("depth_id"))
    check("depth_source 升級為 lidar", j.get("depth_source") == "lidar", j.get("depth_source"))
    check("首次上傳不算覆蓋", j.get("replaced_previous") is False, j.get("replaced_previous"))
    check("深度檔真的落盤", os.path.isfile(os.path.join(tmp, "depth_maps", iid + ".f32")))
    check("meta 也落盤（內參事後查不到，必須跟著存）",
          os.path.isfile(os.path.join(tmp, "depth_maps", iid + ".meta.json")))

    r2 = upload(iid, depth_bytes(), meta())
    check("同一份重傳：不算覆蓋（斷網重試是正常的）",
          (r2.get_json() or {}).get("replaced_previous") is False)
    r3 = upload(iid, depth_bytes(0.35), meta())
    check("換一份重傳：標記為覆蓋（稽核上是不同的事件）",
          (r3.get_json() or {}).get("replaced_previous") is True)

    r = upload("0123456789abcdef", depth_bytes(), meta())
    check("孤兒 image_id 被擋（沒有 GT 可配對）", r.status_code == 400, r.status_code)

    r = upload(iid, depth_bytes()[:-4], meta())
    check("截斷的資料經端點也被擋", r.status_code == 400, r.status_code)

    r = upload(iid, depth_bytes(), meta(), headers=H_NURSE)
    check("護理師角色不得上傳深度（與送標註同一道權限）", r.status_code == 403, r.status_code)

    # ── 3 撤回同意 ────────────────────────────────────────────
    print("\n── 3 撤回同意 ──")
    r, iid2 = submit("WD-DEPTH2", b"d2")
    check("前置：第二筆標註送出成功", r.status_code == 200, r.status_code)
    rw = cli.post("/api/v1/consent/withdraw", headers=H_DR, json={"code": "WD-DEPTH2"})
    check("前置：撤回成功", rw.status_code == 200, rw.status_code)
    r = upload(iid2, depth_bytes(), meta())
    body = r.get_json() or {}
    check("撤回後不接受深度補傳", r.status_code == 400, r.status_code)
    check("而且理由講的是**撤回同意**，不是「查無標註」",
          any("撤回" in str(i) for i in (body.get("issues") or [])), body.get("issues"))

    # ── 4 清單 join ──────────────────────────────────────────
    print("\n── 4 清單 join ──")
    j = cli.get("/api/v1/flywheel/records", headers=H_DR).get_json() or {}
    row = next((x for x in j.get("records", []) if x["image_id"] == iid), None)
    check("清單找得到第一筆", row is not None)
    if row:
        # 佇列裡那一筆寫的是 lidar_local（唯讀累加，補傳不會改它）。
        # 清單若不 join 側檔，就會一直說「拍了沒傳」。
        check("清單顯示 lidar（側檔優先於唯讀佇列裡的 lidar_local）",
              row.get("depth_source") == "lidar", row.get("depth_source"))
        check("清單帶出深度位元組數（不解檔就能盤點）",
              row.get("depth_bytes") == W * H * 4, row.get("depth_bytes"))

    # ── 5 稽核 ────────────────────────────────────────────────
    print("\n── 5 稽核 ──")
    acts = [a.get("action") for a in fw.read_jsonl(fw.AUDIT)]
    check("成功有留 depth_stored", "depth_stored" in acts)
    check("失敗有留 depth_rejected（事後查『為什麼這批沒有深度』的唯一線索）",
          "depth_rejected" in acts)

    print("\n%d 項檢查，%d 項失敗" % (TOTAL[0], len(FAILED)))
    if FAILED:
        print("失敗：")
        for x in FAILED:
            print("  · " + x)
        return 1
    print("全部通過：截斷／全零／毫米／大端序都擋得住，補傳有 join 進清單。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
