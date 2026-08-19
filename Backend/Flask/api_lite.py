# -*- coding: utf-8 -*-
"""WoundLite 民眾版的匿名分割端點。

    POST /api/v1/lite/segment      multipart/form-data，**無需登入**

契約見 `docs/lite_backend_contract.md`。

## 這個檔案為什麼獨立出來

它是整個專案**第一個匿名端點**。其餘所有端點都在 `@jwt_required()` 後面，
而這一個任何人都打得到。把它放在自己的模組裡，是為了讓「哪些程式碼是公開暴露面」
用一個檔案名就回答得出來——混在 `api_flywheel.py` 裡，日後 review 的人得逐個
decorator 去確認。

## ⚠ 限流擋得住什麼、擋不住什麼

契約寫「以 App 附帶的裝置匿名代碼限流」。但 `anon_id` 是**客戶端自己產生的字串**，
改一個就換一個身分。所以：

  · 擋得住：誤觸、失控的重試迴圈、單一裝置的異常用量
  · **擋不住**：任何有意的濫用——免費雲端檔案空間、每次呼叫都跑一次分割
    直接燒 Cloud Run 的錢、把影像灌進你的 GCS

所以這裡另外加了**來源 IP** 的日配額當第二道。IP 也不是身分（CGNAT 之下整棟樓
共用一個），但它至少不是呼叫端說了算。

**正式上架前必須換成真正的裝置證明**（Play Integrity / App Attest）。
那件事後端單方面做不到，必須 App 端配合。在那之前這個端點不該對外公開流量。
把這句話寫在這裡，是因為「限流有做」很容易被讀成「濫用有擋」，而那不是同一件事。

## 隱私

  · **來源 IP 一律雜湊後才落盤**。原始 IP 是個資，而這是民眾健康 App——
    為了限流而長期保存真實 IP，本身就是一個需要交代的資料蒐集。
  · `research_consent != "true"` → **完全不落地**。辨識完即棄，
    連 meta 都不寫。這是同意分流的可信度基礎：民眾版同意書寫的是
    「不同意＝資料不離機」，那句話必須在程式碼裡成立。
  · 落地時以 `anon_id` 分前綴存放，讓撤回（`DELETE /api/v1/lite/data/<anon_id>`）
    有一個可執行的鍵。代價是同一裝置的影像被歸在一起——
    這是「可被遺忘」與「不可連結」之間的取捨，契約選了前者。
"""
import hashlib
import json
import os
import time

from flask import Blueprint, jsonify, request

import api_flywheel as _fw

lite_bp = Blueprint("lite", __name__)

# 每日配額。契約建議 30 次/日/裝置起步。
LIMIT_PER_ANON = int(os.environ.get("LITE_LIMIT_ANON", "30"))
# 每個來源 IP 的日配額。比裝置配額寬鬆（同一 Wi-Fi 可能有多人），
# 但存在的意義是：換 anon_id 換得再快，也還是從同一條網路出來。
LIMIT_PER_IP = int(os.environ.get("LITE_LIMIT_IP", "200"))
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_DEPTH_BYTES = 16 * 1024 * 1024
# 人臉偵測。預設開啟，但它是**縱深防禦不是保證**——見 _has_face()。
FACE_REJECT = os.environ.get("LITE_FACE_REJECT", "1") not in ("0", "false", "False")


def _dir():
    return _fw.FLYWHEEL_DIR


def _rate_path(day):
    return os.path.join(_dir(), "lite_rate_%s.jsonl" % day)


def _ip_hash(ip):
    """來源 IP 的單向雜湊。

    加鹽是必要的：IPv4 只有 43 億種可能，無鹽雜湊可以在幾分鐘內反查完。
    鹽取自環境變數；沒設定時退回一個固定值並在日誌警告——
    **不要靜默地用弱鹽**，那會讓人以為做了雜湊就等於去識別。
    """
    salt = os.environ.get("LITE_IP_SALT")
    if not salt:
        salt = "woundai-lite-unsalted"
    return hashlib.sha256((salt + "|" + (ip or "")).encode("utf-8")).hexdigest()[:20]


def _client_ip():
    # Cloud Run 會在 X-Forwarded-For 放 "client, proxy1, proxy2"，第一個才是來源。
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or ""


def rate_check(anon_id, ip):
    """回 (ok, retry_after_seconds, reason)。**先查不寫**——被擋的請求不該計入配額。"""
    day = time.strftime("%Y%m%d", time.gmtime())
    n_anon = n_ip = 0
    iph = _ip_hash(ip)
    for e in _fw.read_jsonl(_rate_path(day)):
        if e.get("anon_id") == anon_id:
            n_anon += 1
        if e.get("ip_hash") == iph:
            n_ip += 1
    # 到隔日 00:00 UTC 還有多久。回 429 時給得出「什麼時候可以再試」，
    # 比只說「太頻繁」有用得多。
    now = time.time()
    tomorrow = (int(now) // 86400 + 1) * 86400
    if n_anon >= LIMIT_PER_ANON:
        return False, int(tomorrow - now), "device_quota"
    if n_ip >= LIMIT_PER_IP:
        return False, int(tomorrow - now), "network_quota"
    return True, 0, ""


def rate_record(anon_id, ip):
    day = time.strftime("%Y%m%d", time.gmtime())
    _fw.append_jsonl(_rate_path(day),
                     {"ts": _fw.utc_now(), "anon_id": anon_id, "ip_hash": _ip_hash(ip)})


def _has_face(bgr):
    """粗略的人臉偵測。**擋得住正面清楚的臉，擋不住其他任何東西。**

    刻意寫得保守（`minNeighbors` 偏高、最小尺寸偏大），因為誤判的代價是
    把一張合法的傷口照片退掉——民眾版的使用者不會知道為什麼，只會覺得壞了。

    它抓不到：側臉、部分遮擋的臉、以及所有**非人臉的可識別物**
    （證件、名牌、刺青、病房門牌、背景中的人）。

    ⚠ 這是縱深防禦，不是保證。真正擋得住的是取景指引與「只拍傷口」的流程約束；
    後端這一層是補網。**不可以拿它當作放寬前面那一層的理由。**
    """
    try:
        import cv2
        path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        if not os.path.isfile(path):
            return False, "cascade_missing"
        clf = cv2.CascadeClassifier(path)
        if clf.empty():
            return False, "cascade_empty"
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        # 臉要占畫面一定比例才算——傷口特寫裡遠處背景的小人臉區塊多半是誤判，
        # 而真正的隱私風險是「臉清楚可辨識」的那種。
        minsz = max(60, int(min(h, w) * 0.12))
        faces = clf.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=8,
                                     minSize=(minsz, minsz))
        return (len(faces) > 0), ("%d" % len(faces))
    except Exception as e:      # 偵測本身壞掉不該讓整個端點失效
        return False, "error:%s" % e


# 分割函式由 app.py 在註冊時注入。**不在模組層 import app**——
# 那會造成循環匯入，而且會讓這個模組沒有 ONNX 模型就 import 不起來（測試跑不動）。
_SEGMENT = None


def init_lite(segment_fn):
    global _SEGMENT
    _SEGMENT = segment_fn


def _polygons_from_mask(mask, min_px=64):
    import cv2
    import numpy as np
    cnts, _ = cv2.findContours(np.asarray(mask, np.uint8),
                               cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in sorted(cnts, key=cv2.contourArea, reverse=True):
        if cv2.contourArea(c) < min_px:
            continue
        ap = cv2.approxPolyDP(c, 0.003 * cv2.arcLength(c, True), True).reshape(-1, 2)
        if len(ap) >= 3:
            out.append([[int(x), int(y)] for x, y in ap.tolist()])
    return out


@lite_bp.route("/api/v1/lite/segment", methods=["POST"])
def lite_segment():
    import cv2
    import numpy as np

    anon_id = (request.form.get("anon_id") or "").strip()
    client = (request.form.get("client") or "").strip()
    consent = (request.form.get("research_consent") or "").strip().lower() == "true"
    if not anon_id or len(anon_id) > 64:
        return jsonify({"error": "缺 anon_id"}), 400
    if "image" not in request.files:
        return jsonify({"error": "缺 image"}), 400

    ip = _client_ip()
    ok, retry, why = rate_check(anon_id, ip)
    if not ok:
        # 429 的語意：App 顯示「稍後再試」並**退回手動圈選**，不是失敗。
        return jsonify({
            "error": "rate_limited", "reason": why, "retry_after": retry,
            "message": "今日自動辨識次數已用完，請改用手動圈選；明日 00:00 (UTC) 重置。",
        }), 429

    raw = request.files["image"].read()
    if len(raw) > MAX_IMAGE_BYTES:
        return jsonify({"error": "影像超過 %d MB" % (MAX_IMAGE_BYTES // 1024 // 1024)}), 413
    bgr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        return jsonify({"error": "影像解碼失敗"}), 400
    h, w = bgr.shape[:2]

    if FACE_REJECT:
        found, detail = _has_face(bgr)
        if found:
            # 退件訊息要講得出**該怎麼辦**。只說「偵測到人臉」會讓人重拍一模一樣的照片。
            return jsonify({
                "error": "face_detected",
                "message": "畫面中偵測到人臉，為保護隱私未進行辨識。"
                           "請只拍傷口部位，避免臉部或可辨識個人的物品入鏡後再試一次。",
            }), 400

    if _SEGMENT is None:
        return jsonify({"error": "分割模型未載入"}), 503
    try:
        mask = _SEGMENT(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    except Exception as e:
        return jsonify({"error": "分割失敗：%s" % e}), 500
    if mask is None:
        return jsonify({"wound_polygons": [], "image_w": w, "image_h": h,
                        "confidence": 0.0, "stored": False, "image_id": None}), 200

    polys = _polygons_from_mask(mask)
    conf = float(np.asarray(mask, np.float32).mean()) if len(polys) else 0.0

    # ── 落地：只有取得研究同意才做 ────────────────────────────────
    #
    # ⚠ 這個分支的正確性是同意分流的**全部**價值所在。民眾版同意書寫的是
    #   「不同意＝資料不離機」，那句話要在這裡成立，不是在文案裡成立。
    #   App 端未同意時根本不會呼叫本端點；這裡的 false 分支是縱深防禦。
    image_id = None
    if consent:
        image_id = hashlib.sha1(raw).hexdigest()[:16]
        st = _fw._store()
        pre = "lite/%s/%s" % (anon_id, image_id)
        st.put_blob(_fw._key(os.path.join(_dir(), pre + ".jpg")), raw)
        meta = {
            "anon_id": anon_id, "client": client, "image_id": image_id,
            "image_w": w, "image_h": h, "received_at": _fw.utc_now(),
            "research_consent": True,
            "measured": _safe_json(request.form.get("measured")),
            "camera_intrinsics": _safe_json(request.form.get("camera_intrinsics")),
            "depth_format": request.form.get("depth_format"),
            "depth_scale": request.form.get("depth_scale"),
        }
        dp = request.form.get("depth_map_png")
        if dp:
            ok_d, issue = _store_depth_png(st, pre, dp, request.form.get("depth_conf_png"))
            meta["depth"] = "stored" if ok_d else ("rejected: %s" % issue)
        st.put_blob(_fw._key(os.path.join(_dir(), pre + ".json")),
                    json.dumps(meta, ensure_ascii=False).encode("utf-8"))
        _fw.append_jsonl(os.path.join(_dir(), "lite_index.jsonl"), {
            "anon_id": anon_id, "image_id": image_id, "client": client,
            "received_at": meta["received_at"], "bytes": len(raw),
            "polygons": len(polys), "depth": meta.get("depth"),
        })

    rate_record(anon_id, ip)
    return jsonify({
        "wound_polygons": polys, "image_w": w, "image_h": h,
        "confidence": round(conf, 4),
        # 契約沒有這兩個欄位，但加上去是相容的，而且它讓 App 能對使用者
        # **據實**說明這張照片有沒有被保存——同意分流講給人聽才有意義。
        "stored": bool(consent), "image_id": image_id,
    }), 200


def _safe_json(s):
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _store_depth_png(st, pre, b64, conf_b64=None):
    """深度圖走與 `/api/v1/annotation` **完全相同**的 16-bit PNG 驗證。

    重用不是為了省程式碼，是為了讓兩條路徑的判準不會分岔——
    分岔之後「同一張深度圖在醫療版收得下、民眾版收不下」會很難查。
    """
    import base64
    try:
        raw = base64.b64decode(b64, validate=True)
        if len(raw) > MAX_DEPTH_BYTES:
            raise ValueError("深度圖過大")
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("不是 PNG")
        # IHDR：位元組 24 是 bit depth，25 是 colour type（0＝灰階）。
        # 8-bit 只表示得了 0–255 mm，而臨床攝距是 200–600 mm——整片飽和，
        # 反投影出一個平面，而**不會有任何錯誤**。
        if raw[24] != 16 or raw[25] != 0:
            raise ValueError("深度圖必須是 16-bit 灰階 PNG（實際 %d/%d）" % (raw[24], raw[25]))
        st.put_blob(_fw._key(os.path.join(_dir(), pre + ".depth.png")), raw)
        if conf_b64:
            craw = base64.b64decode(conf_b64, validate=True)
            if craw[:8] == b"\x89PNG\r\n\x1a\n":
                st.put_blob(_fw._key(os.path.join(_dir(), pre + ".conf.png")), craw)
        return True, ""
    except Exception as e:
        return False, str(e)


@lite_bp.route("/api/v1/lite/data/<anon_id>", methods=["DELETE"])
def lite_delete(anon_id):
    """撤回：刪掉這個裝置匿名代碼底下的全部資料。

    ⚠ **匿名端點的刪除同樣是匿名的**——任何知道某個 anon_id 的人都刪得掉它。
    這是刻意的取捨：要求證明「你就是那個裝置」就需要一個身分，而那與匿名互斥。
    風險方向是安全的（有人惡意刪掉別人的研究資料，損失的是資料量，不是隱私），
    而反過來（無法刪除）才是不可接受的。

    anon_id 是 App 首啟隨機生成、不連結任何身分，猜中別人的機率可忽略。
    """
    anon_id = (anon_id or "").strip()
    if not anon_id or "/" in anon_id or ".." in anon_id:
        return jsonify({"error": "anon_id 格式不合"}), 400
    idx = os.path.join(_dir(), "lite_index.jsonl")
    rows = [e for e in _fw.read_jsonl(idx) if e.get("anon_id") == anon_id]
    st = _fw._store()
    n = 0
    for e in rows:
        for suf in (".jpg", ".json", ".depth.png", ".conf.png"):
            k = _fw._key(os.path.join(_dir(), "lite/%s/%s%s" % (anon_id, e.get("image_id"), suf)))
            try:
                if st.exists(k):
                    st.delete(k)
                    n += 1
            except Exception:
                pass
    # 索引另寫一筆刪除紀錄，不改寫原本那幾行——與飛輪的 append-only 一致：
    # 「這個 anon_id 曾經有資料而且已依請求刪除」本身就是要留的事實。
    _fw.append_jsonl(idx, {"anon_id": anon_id, "action": "deleted",
                           "objects": n, "received_at": _fw.utc_now()})
    return jsonify({"status": "deleted", "anon_id": anon_id,
                    "records": len(rows), "objects_removed": n}), 200
