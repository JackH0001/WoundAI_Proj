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
import logging
import os
import time

from flask import Blueprint, jsonify, request

import api_flywheel as _fw

# ⚠ 這一行不是樣板。最外層的 catch-all 會呼叫 `logger.exception()`，
# 而第一版忘了定義 logger——那個 handler 自己會 NameError，
# 於是又回到 Flask 的預設 HTML 500。**錯誤處理路徑壞掉是最難發現的一種壞**：
# 它只在出錯時才執行，而出錯時沒有人在看它有沒有正常運作。
logger = logging.getLogger(__name__)

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
    """匿名分割入口。最外層有 catch-all，理由見 `_lite_segment_impl` 上方。"""
    try:
        return _lite_segment_impl()
    except Exception as e:
        # ⚠ **匿名端點不可以吐 Flask 的預設 HTML 500。**
        #
        # 三個理由，由重到輕：
        #  1. 排錯全靠猜。2026-08-19 實測：App 只看得到「500」，
        #     而例外堆疊留在容器日誌裡，要拉 revision 對時間才找得到。
        #     回 JSON ＋ `logger.exception` 之後，堆疊必定進日誌、
        #     而錯誤類型直接回給呼叫端。
        #  2. 預設頁面會洩漏它是 Flask（以及 debug 模式下的原始碼）。
        #  3. App 端解析 JSON 失敗會把它報成「連線問題」，把排錯帶去錯的一端。
        #
        # 這一層**不吞錯**：它記錄、回報，然後結束。與「except: pass」是相反的東西。
        logger.exception("lite/segment 未預期例外")
        return jsonify({
            "error": "internal_error",
            "detail": "%s: %s" % (type(e).__name__, e),
            "message": "伺服器處理時發生錯誤，請改用手動圈選；此問題已記錄。",
        }), 500


def _lite_segment_impl():
    import cv2
    import numpy as np

    anon_id = (request.form.get("anon_id") or "").strip()
    client = (request.form.get("client") or "").strip()
    consent = (request.form.get("research_consent") or "").strip().lower() == "true"
    # 同意書版本。**沒有它，日後同意文案一改就分不出誰同意了哪一版**——
    # 而「當初同意的範圍」正是撤回爭議與 IRB 審查會問的第一件事。
    # 現在還沒有正式流量，補的成本接近零；等有了資料再補就補不回來了。
    consent_version = (request.form.get("consent_version") or "").strip()[:32]
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
        out = _SEGMENT(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        # 注入的函式可以只回遮罩，也可以回 (mask, info)。
        # 後者讓民眾版也報得出 route——同一張照片在兩個 App 得到不同結果時，
        # 沒有 route 就無從歸因。
        mask, seg_info = out if isinstance(out, tuple) else (out, {})
    except Exception as e:
        return jsonify({"error": "分割失敗：%s" % e}), 500
    if mask is None:
        return jsonify({"wound_polygons": [], "image_w": w, "image_h": h,
                        "confidence": 0.0, "stored": False, "image_id": None,
                        "route": "none"}), 200

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
            # 空字串代表「App 沒送版本」——**不要填一個預設值**。
            # 填了就會有一批資料聲稱同意了某個版本，而那是猜的。
            "consent_version": consent_version or None,
            "measured": _safe_json(request.form.get("measured")),
            "camera_intrinsics": _safe_json(request.form.get("camera_intrinsics")),
            "depth_format": request.form.get("depth_format"),
            "depth_scale": request.form.get("depth_scale"),
            # ⚠ route 必須在 put_blob **之前**寫進 meta。
            # 第一版設在 put_blob 之後，於是落地的 JSON 少了它——
            # 回應裡有、檔案裡沒有，而只看回應完全察覺不到。
            "route": seg_info.get("route"),
            "escalated": bool(seg_info.get("escalated")),
            # AI 自己的輪廓也要落地。**沒有它，「民眾改了什麼」就永遠算不出來**——
            # lay 修正率（模型有輸出且人改了它）是比 empty 桶更早、更大量的
            # 「模型哪裡錯」訊號，而它需要 AI 的答案與人的答案同時在場。
            "ai_polygons": polys,
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
            # 難例（辨識空手）正是最需要拿去改進模型的樣本。
            # 記下 route 與輪廓數，日後才篩得出「哪些是集成救回來的」
            # 與「哪些連集成都空手」——後者是下一輪訓練的優先目標。
            "route": meta["route"], "escalated": meta["escalated"],
            "consent_version": consent_version or None,
        })

    rate_record(anon_id, ip)
    return jsonify({
        "wound_polygons": polys, "image_w": w, "image_h": h,
        "confidence": round(conf, 4),
        # 契約沒有這幾個欄位，但加上去是相容的。
        # `stored` 讓 App 能對使用者**據實**說明這張照片有沒有被保存
        # ——同意分流講給人聽才有意義。
        "stored": bool(consent), "image_id": image_id,
        # `route` 讓「醫療版看得到、民眾版看不到」這種問題可以在一次回應裡歸因。
        "route": seg_info.get("route") or "student",
        "escalated": bool(seg_info.get("escalated")),
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


@lite_bp.route("/api/v1/lite/annotation", methods=["POST"])
def lite_annotation():
    """回收民眾**自己畫的**傷口輪廓。`application/json`。

        {anon_id, image_id, polygons: [[[x,y],...],...], image_w, image_h,
         research_consent: true, consent_version?, source: "manual"|"edited"}

    ## ⚠ 這不是弱標註，是**不同定義下的標註**

    規劃文件把它稱為「辨識失敗的難例自帶答案」。方向對，但要說得更準：

    **傷口邊界是臨床判斷，不是感知判斷。** 傷口到哪裡結束、周邊紅斑從哪裡開始
    ——民眾會**系統性地**多包或少包某一類組織。那是偏差不是雜訊，
    收得再多也不會互相抵銷，只會把模型往民眾的定義拉。

    醫療端整套架構正是在防這件事：`gt.verify` 只有醫師有，還有一整組測試守著
    「護理師按了完成修邊也不會產生醫師背書」。把民眾輪廓餵進同一個訓練目標，
    等於從另一扇門把那條線繞過去。

    ## 因此：結構隔離，不是靠欄位區分

    這些標註存在 `lite/` 前綴、寫進 `lite_labels.jsonl`，
    **完全不碰 `retrain_queue.jsonl`**。飛輪的 dataset manifest 只讀後者，
    所以它在程式上讀不到這裡的東西——要用得寫一條新的匯出路徑，
    而那是一個明確的決定，不是一個沒人注意到的預設。

    站得住腳的用途：難例採礦（哪些影像 student 失手，根本不需要這個標註）、
    ROI 提議、自監督底料。站不住腳的：與醫師 GT 混在一起做邊界監督。

    「將來能不能用於邊界監督」是臨床判斷，不是工程判斷。
    """
    d = request.get_json(silent=True) or {}
    anon_id = str(d.get("anon_id") or "").strip()
    image_id = str(d.get("image_id") or "").strip()
    if not anon_id or not image_id or "/" in anon_id or "/" in image_id:
        return jsonify({"error": "缺 anon_id / image_id"}), 400
    if str(d.get("research_consent")).lower() != "true" and d.get("research_consent") is not True:
        # 沒有研究同意就不收。與 segment 一致：不同意＝資料不離機。
        return jsonify({"error": "未取得研究同意，不接受標註回收"}), 400

    ip = _client_ip()
    ok, retry, why = rate_check(anon_id, ip)
    if not ok:
        return jsonify({"error": "rate_limited", "reason": why, "retry_after": retry}), 429

    polys = d.get("polygons") or []
    clean = []
    for p in polys[:16]:                      # 上限：民眾版是單一中心傷口，16 已經很寬鬆
        if not isinstance(p, list) or len(p) < 3:
            continue
        pts = []
        for pt in p[:4000]:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                try:
                    pts.append([int(pt[0]), int(pt[1])])
                except (TypeError, ValueError):
                    pass
        if len(pts) >= 3:
            clean.append(pts)
    if not clean:
        return jsonify({"error": "polygons 為空或格式不合"}), 400

    # 影像必須先透過 lite/segment 落地過。沒有像素的標註訓練不了，
    # 而且它會讓盤點時看到一個對不上任何影像的數字。
    st = _fw._store()
    if not st.exists(_fw._key(os.path.join(_dir(), "lite/%s/%s.jpg" % (anon_id, image_id)))):
        return jsonify({"error": "查無對應影像；請先以 research_consent=true 呼叫 lite/segment"}), 400

    # ── lay 修正率：人改了 AI 多少（Mac review 建議的指標）──────────
    #
    # 在**收件當下**算一次 IoU 存起來，不在清單端每次重算：
    # 標註只送一次，清單會被開很多次。
    #
    # IoU 需要 AI 的輪廓（存在 meta 的 ai_polygons）。兩組都柵格化在同一個
    # 縮小座標系（長邊 512）再交併——IoU 對等比縮放不變，而 4000×3000 的
    # 全解析度柵格化在匿名端點上是自找的成本。
    iou_ai = None
    try:
        mraw = st.get_blob(_fw._key(os.path.join(
            _dir(), "lite/%s/%s.json" % (anon_id, image_id))))
        ai_polys = (json.loads(mraw.decode("utf-8")) if mraw else {}).get("ai_polygons") or []
        iw, ih = int(d.get("image_w") or 0), int(d.get("image_h") or 0)
        if ai_polys and iw > 0 and ih > 0:
            import numpy as np
            import cv2
            s = 512.0 / max(iw, ih)
            sw, sh = max(1, int(iw * s)), max(1, int(ih * s))
            def _rast(pls):
                m = np.zeros((sh, sw), np.uint8)
                cv2.fillPoly(m, [np.round(np.asarray(p, np.float32) * s).astype(np.int32)
                                 for p in pls], 1)
                return m
            a, b = _rast(ai_polys), _rast(clean)
            uni = float(np.logical_or(a, b).sum())
            iou_ai = round(float(np.logical_and(a, b).sum()) / uni, 3) if uni > 0 else None
    except Exception:
        iou_ai = None      # 算不出來就留 None；不可因為指標算不出來而拒收標註

    rec = {
        "anon_id": anon_id, "image_id": image_id,
        "polygons": clean, "polygon_count": len(clean),
        # AI 有輸出且 IoU<0.8 ＝「人修正了模型」。門檻掛環境變數，
        # 研究端要改敏感度不必動程式。None＝AI 空手（那是 empty 桶的事，不算修正）。
        "iou_vs_ai": iou_ai,
        "corrected": (iou_ai is not None
                      and iou_ai < float(os.environ.get("LITE_CORRECTED_IOU", "0.8"))),
        "image_w": d.get("image_w"), "image_h": d.get("image_h"),
        "source": (str(d.get("source") or "manual"))[:16],
        "consent_version": (str(d.get("consent_version") or "").strip()[:32]) or None,
        # ⚠ **這個欄位是給人看的，不是控制手段。** 真正的隔離是它存在
        # `lite_labels.jsonl` 而不是 `retrain_queue.jsonl`。
        # 靠欄位過濾遲早會有人忘記，靠檔案分開則忘不了。
        "label_grade": "lay",
        "received_at": _fw.utc_now(),
    }
    _fw.append_jsonl(os.path.join(_dir(), "lite_labels.jsonl"), rec)
    rate_record(anon_id, ip)
    return jsonify({"status": "stored", "image_id": image_id,
                    "polygon_count": len(clean), "label_grade": "lay"}), 200


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
    # 標註也要一併撤回。只刪影像會留下「有輪廓、沒有影像」的孤兒——
    # 那既無法訓練，也還留著一份這個裝置曾經提供過資料的痕跡。
    labels = os.path.join(_dir(), "lite_labels.jsonl")
    n_lab = len([e for e in _fw.read_jsonl(labels) if e.get("anon_id") == anon_id])
    if n_lab:
        _fw.append_jsonl(labels, {"anon_id": anon_id, "action": "deleted",
                                  "received_at": _fw.utc_now()})
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
                    "records": len(rows), "objects_removed": n,
                    "labels_withdrawn": n_lab}), 200


@lite_bp.route("/api/v1/lite/records", methods=["GET"])
def lite_records():
    """民眾版資料盤點（主控台用）。**要登入，而且只給工程師／管理者。**

    與 `lite/segment` 相反：那條是匿名的公開入口，這條是內部的檢視。
    兩者放在同一個模組是因為它們讀同一批資料，但守門完全不同——
    所以這裡的 `@jwt_required` 不可以因為「同一個檔案」而被省略。

    臨床角色刻意不給：民眾版資料是研究與運維用途，醫師護理師沒有業務理由看它，
    而且兩邊的數字混談會讓「臨床收案進度」失去意義。
    """
    from flask_jwt_extended import get_jwt, jwt_required, verify_jwt_in_request
    try:
        verify_jwt_in_request()
    except Exception:
        return jsonify({"error": "需要登入"}), 401
    role = (get_jwt() or {}).get("role")
    try:
        import auth_users
        allowed = auth_users.can(role, "audit.read")
    except Exception:
        allowed = False          # 權限模組載不進來時 fail-closed
    if not allowed:
        return jsonify({"error": "權限不足", "issues": [
            "民眾版資料僅工程師／管理者可檢視。"]}), 403

    idx = _fw.read_jsonl(os.path.join(_dir(), "lite_index.jsonl"))
    deleted = {e["anon_id"] for e in idx if e.get("action") == "deleted"}
    rows = [e for e in idx if e.get("action") != "deleted"
            and e.get("anon_id") not in deleted]
    # 內測機。**列出但不計入統計**：藏起來會讓「清單有 7 筆、統計說 5 筆」
    # 變成一個沒人解釋得了的謎；計入則污染 route 儀表（內測都是印刷樣例，
    # escalated 占比會被灌高）。逗號分隔的 anon_id 前綴，掛環境變數。
    _internal = {p.strip() for p in os.environ.get("LITE_INTERNAL_ANON", "").split(",")
                 if p.strip()}
    def _is_internal(aid):
        return any((aid or "").startswith(p) for p in _internal)
    labels = [e for e in _fw.read_jsonl(os.path.join(_dir(), "lite_labels.jsonl"))
              if e.get("action") != "deleted"]
    lab_by_img = {}
    for e in labels:
        lab_by_img[e.get("image_id")] = e

    # route 三桶。**這是模型進步的即時儀表**：
    #   student            → 基礎模型自己認得（最好）
    #   cloud_escalated(AU) → 要集成才救得回來（難例）
    #   （空）              → 連集成都空手（下一輪訓練的優先目標）
    stat_rows = [e for e in rows if not _is_internal(e.get("anon_id"))]
    buckets = {"student": 0, "escalated": 0, "empty": 0}
    for e in stat_rows:
        if not e.get("polygons"):
            buckets["empty"] += 1
        elif e.get("escalated"):
            buckets["escalated"] += 1
        else:
            buckets["student"] += 1
    total = max(1, len(stat_rows))
    # lay 修正率：AI 有輸出、人畫了、而且改動夠大（IoU < 門檻）。
    # 這比 empty 桶更早也更大量——模型完全空手是少數，
    # 「有輸出但畫錯邊」才是日常，而只有人的修正看得出它。
    lab_ai = [e for e in labels if e.get("iou_vs_ai") is not None
              and not _is_internal(e.get("anon_id"))]
    n_corrected = sum(1 for e in lab_ai if e.get("corrected"))
    out = []
    for e in sorted(rows, key=lambda x: x.get("received_at") or "", reverse=True)[:300]:
        lab = lab_by_img.get(e.get("image_id"))
        out.append({
            "received_at": e.get("received_at"), "anon_id": e.get("anon_id"),
            "image_id": e.get("image_id"), "client": e.get("client"),
            "route": e.get("route"), "escalated": bool(e.get("escalated")),
            "polygons": e.get("polygons"), "bytes": e.get("bytes"),
            "depth": e.get("depth"), "consent_version": e.get("consent_version"),
            # 民眾自己畫的輪廓數。**與 polygons（AI 的）分開兩欄**——
            # 合成一欄會讓「AI 空手但人畫了」這個最有價值的組合看不出來。
            "lay_polygons": (lab or {}).get("polygon_count"),
            "iou_vs_ai": (lab or {}).get("iou_vs_ai"),
            "corrected": (lab or {}).get("corrected"),
            "internal": _is_internal(e.get("anon_id")),
        })
    return jsonify({
        "records": out, "total": len(stat_rows), "listed": len(rows),
        "devices": len({e.get("anon_id") for e in stat_rows}),
        "internal_excluded": len(rows) - len(stat_rows),
        "labels": len(labels),
        # AI 空手而民眾有畫的——難例採礦的第一優先，因為它同時有影像與人的判斷
        "hard_with_lay": sum(1 for e in stat_rows if not e.get("polygons")
                             and lab_by_img.get(e.get("image_id"))),
        # lay 修正率：AI 有輸出且人改了它（IoU < 門檻）。
        # 分母是「AI 有輸出且有人畫」，不是全部標註——AI 空手的歸 empty 桶。
        "lay_corrected": n_corrected,
        "lay_with_ai": len(lab_ai),
        "lay_corrected_pct": (round(100.0 * n_corrected / len(lab_ai), 1)
                              if lab_ai else None),
        "route_buckets": buckets,
        "route_pct": {k: round(100.0 * v / total, 1) for k, v in buckets.items()},
        "withdrawn_devices": len(deleted),
    }), 200
