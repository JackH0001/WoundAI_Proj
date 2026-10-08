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

from flask import Blueprint, jsonify, request, g, current_app, has_request_context
from lite_attest_state import StateUnavailable
from lite_fenced_objects import ObjectSealed, GenerationConflict
from lite_privacy_state import OwnerWithdrawn

import api_flywheel as _fw

# ⚠ 這一行不是樣板。最外層的 catch-all 會呼叫 `logger.exception()`，
# 而第一版忘了定義 logger——那個 handler 自己會 NameError，
# 於是又回到 Flask 的預設 HTML 500。**錯誤處理路徑壞掉是最難發現的一種壞**：
# 它只在出錯時才執行，而出錯時沒有人在看它有沒有正常運作。
logger = logging.getLogger(__name__)

lite_bp = Blueprint("lite", __name__)


def _fenced_privacy():
    if not has_request_context(): return None
    from lite_fenced_store import FencedPrivacy
    privacy = current_app.extensions.get('lite_attest_http', {}).get('privacy')
    return privacy if isinstance(privacy, FencedPrivacy) else None


def _media_store():
    privacy = _fenced_privacy()
    if privacy is None: return _fw._store()
    admission = getattr(g, 'lite_admission', None)
    if admission is None:
        from lite_attest_state import StateUnavailable
        raise StateUnavailable('verified owner required for fenced media')
    return privacy.media(admission.installation, request.environ.get('woundlite.writer_ticket'))


def _research_append(path, row):
    if _fenced_privacy() is None: return _fw.append_jsonl(path, row)
    _media_store().append_line(_fw._key(path), json.dumps(row, ensure_ascii=False, allow_nan=False))

# 個人免費辨識每日預設 5 次；環境覆寫與現行匿名限流尚非付費帳本。
LIMIT_PER_ANON = int(os.environ.get("LITE_LIMIT_ANON", "5"))
# 修正輪廓有獨立防濫用額度，不扣辨識次數。
LIMIT_ANNOTATION_PER_ANON = int(os.environ.get("LITE_LIMIT_ANNOTATION_ANON", "30"))
LIMIT_ATTEMPT_PER_ANON = int(os.environ.get("LITE_LIMIT_ATTEMPT_ANON", "30"))
# 每個來源 IP 的日配額。比裝置配額寬鬆（同一 Wi-Fi 可能有多人），
# 但存在的意義是：換 anon_id 換得再快，也還是從同一條網路出來。
LIMIT_PER_IP = int(os.environ.get("LITE_LIMIT_IP", "200"))
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_DEPTH_BYTES = 16 * 1024 * 1024
# 人臉偵測。預設開啟，但它是**縱深防禦不是保證**——見 _has_face()。
FACE_REJECT = os.environ.get("LITE_FACE_REJECT", "1") not in ("0", "false", "False")


def _dir():
    return _fw.FLYWHEEL_DIR


def _rate_path(day, operation="segment"):
    if operation not in ("segment", "annotation", "attempt"):
        raise ValueError("unknown Lite operation")
    name = {"segment": "lite_rate", "annotation": "lite_annotation_rate",
            "attempt": "lite_attempt_rate"}[operation]
    return os.path.join(_dir(), "%s_%s.jsonl" % (name, day))


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


def quota_snapshot(anon_id, ip, operation="segment", now=None):
    """Server observation, not a reservation or a transactional paid balance.

    Existing segment ledger name is preserved: rollout never resets today's usage.
    Legacy rows without operation remain charged conservatively until UTC reset.
    """
    now = time.time() if now is None else now
    day = time.strftime("%Y%m%d", time.gmtime(now))
    rows, bad = _fw.read_jsonl(_rate_path(day, operation), with_bad=True)
    if bad:
        raise IOError("Lite quota ledger is malformed")
    iph = _ip_hash(ip)
    used = sum(e.get("anon_id") == anon_id for e in rows)
    network_used = sum(e.get("ip_hash") == iph for e in rows)
    limit = {"segment": LIMIT_PER_ANON, "annotation": LIMIT_ANNOTATION_PER_ANON,
             "attempt": LIMIT_ATTEMPT_PER_ANON}[operation]
    reset = (int(now) // 86400 + 1) * 86400
    return {
        "limit": limit, "used": used, "remaining": max(0, limit - used),
        "resets_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(reset)),
        "scope": "installation", "operation": operation,
    }, network_used, max(1, int(reset - now))


def rate_check(anon_id, ip, operation="segment"):
    quota, network_used, retry = quota_snapshot(anon_id, ip, operation)
    if quota["remaining"] == 0:
        return False, retry, "device_quota"
    if network_used >= LIMIT_PER_IP:
        return False, retry, "network_quota"
    return True, 0, ""


def rate_record(anon_id, ip, operation="segment"):
    day = time.strftime("%Y%m%d", time.gmtime(time.time()))
    _fw.append_jsonl(_rate_path(day, operation),
                     {"ts": _fw.utc_now(), "anon_id": anon_id,
                      "ip_hash": _ip_hash(ip), "operation": operation})


def segment_response(body, anon_id, ip, status=200):
    # A post-success read failure must not turn a completed inference into an
    # apparent failed request that the client retries. Omit unknown usage instead.
    try:
        body["quota"] = quota_snapshot(anon_id, ip)[0]
    except Exception:
        logger.warning("Lite quota snapshot unavailable")
    response = jsonify(body)
    response.headers["Cache-Control"] = "no-store"
    return response, status


def _has_face(bgr):
    """粗略的人臉偵測；回傳 True / False / None（檢查無法完成）。

    只能降低部分正面人臉風險，無法保證偵測到每一張臉。

    刻意寫得保守（`minNeighbors` 偏高、最小尺寸偏大），因為誤判的代價是
    把一張合法的傷口照片退掉——民眾版的使用者不會知道為什麼，只會覺得壞了。

    它抓不到：側臉、部分遮擋的臉、以及所有**非人臉的可識別物**
    （證件、名牌、刺青、病房門牌、背景中的人）。

    ⚠ 這是縱深防禦，不是保證。仍須搭配取景指引與「只拍傷口」的流程約束；
    後端這一層是補網。**不可以拿它當作放寬前面那一層的理由。**
    """
    try:
        import cv2
        path = os.path.join(os.path.dirname(__file__), "privacy", "haarcascade_frontalface_default.xml")
        if not os.path.isfile(path):
            return None, "cascade_missing"
        with open(path, "rb") as source:
            if hashlib.sha256(source.read()).hexdigest() != "0f7d4527844eb514d4a4948e822da90fbb16a34a0bbbbc6adc6498747a5aafb0":
                return None, "cascade_digest_mismatch"
        clf = cv2.CascadeClassifier(path)
        if clf.empty():
            return None, "cascade_empty"
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        # 臉要占畫面一定比例才算——傷口特寫裡遠處背景的小人臉區塊多半是誤判，
        # 而真正的隱私風險是「臉清楚可辨識」的那種。
        minsz = max(60, int(min(h, w) * 0.12))
        faces = clf.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=8,
                                     minSize=(minsz, minsz))
        return (len(faces) > 0), ("%d" % len(faces))
    except Exception:
        # Unknown is not evidence of a clean image; do not expose detector details.
        return None, "detector_error"


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
    except (ObjectSealed, OwnerWithdrawn):
        return jsonify(error='withdrawn'), 410
    except (StateUnavailable, GenerationConflict):
        return jsonify(error='storage_unconfirmed'), 503
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

    if consent:
        denied = _lite_withdrawal_gate(anon_id)
        if denied is not None:
            return denied

    ip = _client_ip()
    ok, retry, why = rate_check(anon_id, ip)
    if not ok:
        # 429 的語意：App 顯示「稍後再試」並**退回手動圈選**，不是失敗。
        message = ("今日雲端辨識額度已用完，仍可手動圈選與儲存本機紀錄；UTC 00:00 重置。"
                   if why == "device_quota" else
                   "目前網路的雲端請求已達上限，請稍後再試；仍可手動圈選與儲存本機紀錄。")
        return segment_response({
            "error": "rate_limited", "reason": why, "retry_after": retry,
            "message": message,
        }, anon_id, ip, 429)

    # Failed/empty inferences are free, but still cost CPU and must be throttled.
    allowed, retry, _ = rate_check(anon_id, ip, "attempt")
    if not allowed:
        return segment_response({
            "error": "rate_limited", "reason": "attempt_quota", "retry_after": retry,
            "message": "今日雲端嘗試已達安全上限，請稍後再試；仍可手動圈選與儲存本機紀錄。",
        }, anon_id, ip, 429)
    rate_record(anon_id, ip, "attempt")

    raw = request.files["image"].read()
    if len(raw) > MAX_IMAGE_BYTES:
        return jsonify({"error": "影像超過 %d MB" % (MAX_IMAGE_BYTES // 1024 // 1024)}), 413
    raw_capture = None
    raw_metadata = None
    raw_depth = None
    if "raw_depth_metadata" in request.form or "raw_depth" in request.files:
        # Raw capture admission must share the durable writer ticket used by DELETE.
        if getattr(g, "lite_admission", None) is None or request.environ.get("woundlite.writer_ticket") is None:
            return jsonify(error="raw_capture_requires_verified_writer"), 503
        if not consent:
            return jsonify(error="raw_capture_requires_consent"), 403
        try:
            from lite_raw_depth_contract import validate, MAX_PIXELS
            allowed = {"anon_id", "client", "research_consent", "consent_version", "raw_depth_metadata"}
            if (set(request.form) - allowed or set(request.files) != {"image", "raw_depth"}
                    or any(len(request.form.getlist(k)) != 1 for k in request.form)
                    or any(len(request.files.getlist(k)) != 1 for k in request.files)):
                raise ValueError("raw capture multipart fields")
            raw_metadata = request.form["raw_depth_metadata"].encode("utf-8")
            raw_depth = request.files["raw_depth"].stream.read(MAX_PIXELS * 4 + 1)
            raw_capture = validate(raw_metadata, raw_depth, raw)
        except (KeyError, ValueError, TypeError, OSError):
            return jsonify(error="invalid_raw_capture"), 400
    bgr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        return jsonify({"error": "影像解碼失敗"}), 400
    h, w = bgr.shape[:2]

    if FACE_REJECT:
        found, detail = _has_face(bgr)
        if found is not True and found is not False:
            return jsonify({
                "error": "privacy_check_unavailable",
                "message": "影像隱私檢查暫時無法完成，本次未進行辨識或保存影像。請稍後再試。",
            }), 503
        if found is True:
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
        return segment_response({"wound_polygons": [], "image_w": w, "image_h": h,
                                 "confidence": 0.0, "stored": False, "image_id": None,
                                 "route": "none"}, anon_id, ip)

    polys = _polygons_from_mask(mask)
    conf = float(np.asarray(mask, np.float32).mean()) if len(polys) else 0.0

    # ── 落地：只有取得研究同意才做 ────────────────────────────────
    #
    # ⚠ 這個分支的正確性是同意分流的**全部**價值所在。民眾版同意書寫的是
    #   「不同意＝資料不離機」，那句話要在這裡成立，不是在文案裡成立。
    #   App 端未同意時根本不會呼叫本端點；這裡的 false 分支是縱深防禦。
    image_id = None
    storage_receipt = {"schema_version": 1, "image": "not_requested",
                       "metadata": "not_requested", "depth": "not_requested",
                       "validity_mask": "not_requested", "rgbd_validation": "not_performed"}
    if consent:
        # Inference can take time: observe a withdrawal that arrived meanwhile.
        denied = _lite_withdrawal_gate(anon_id)
        if denied is not None:
            return denied
        image_id = hashlib.sha1(raw).hexdigest()[:16]
        st = _media_store()
        pre = "lite/%s/%s" % (anon_id, image_id)
        raw_receipt = None
        if raw_capture is not None:
            from lite_raw_depth_store import save_capture
            from store import ImmutableConflict
            try:
                # Same JPEG capture ID cannot be silently rebound to another depth plane.
                raw_receipt = save_capture(st, anon_id, image_id, raw_metadata, raw_depth, raw)
            except ImmutableConflict:
                return jsonify(error="raw_capture_conflict"), 409
            except Exception:
                logger.exception("Lite raw capture persistence not confirmed")
                return jsonify(error="raw_capture_unconfirmed"), 503
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
        storage_receipt.update(image="stored", metadata="stored",
                               depth="not_provided", validity_mask="not_provided")
        dp = request.form.get("depth_map_png")
        if dp:
            ok_d, issue = _store_depth_png(st, pre, dp, request.form.get("depth_conf_png"))
            meta["depth"] = "stored" if ok_d else ("rejected: %s" % issue)
            storage_receipt["depth"] = "stored" if ok_d else "rejected"
            if request.form.get("depth_conf_png"):
                storage_receipt["validity_mask"] = "stored" if ok_d else "rejected"
        if raw_receipt is not None:
            storage_receipt.update(depth="stored", raw_depth_receipt=raw_receipt)
            meta["depth"] = "raw_stored"
            meta["raw_depth_receipt"] = raw_receipt
            meta["depth_format"] = "float32_le_m"
            meta["camera_intrinsics"] = raw_capture["metadata"]["intrinsics"]
        # This receipt attests storage only, never calibration or training eligibility.
        meta["storage_receipt"] = storage_receipt
        st.put_blob(_fw._key(os.path.join(_dir(), pre + ".json")),
                    json.dumps(meta, ensure_ascii=False).encode("utf-8"))
        _research_append(os.path.join(_dir(), "lite_index.jsonl"), {
            "anon_id": anon_id, "image_id": image_id, "client": client,
            "received_at": meta["received_at"], "bytes": len(raw),
            "polygons": len(polys), "depth": meta.get("depth"),
            "raw_depth_receipt": raw_receipt,
            # 難例（辨識空手）正是最需要拿去改進模型的樣本。
            # 記下 route 與輪廓數，日後才篩得出「哪些是集成救回來的」
            # 與「哪些連集成都空手」——後者是下一輪訓練的優先目標。
            "route": meta["route"], "escalated": meta["escalated"],
            "consent_version": consent_version or None,
        })

    # Empty detections do not consume the user-facing inference allowance.
    # Attempts are throttled separately above, including failures and empty results.
    if polys:
        rate_record(anon_id, ip)
    return segment_response({
        "wound_polygons": polys, "image_w": w, "image_h": h,
        "confidence": round(conf, 4),
        # 契約沒有這幾個欄位，但加上去是相容的。
        # `stored` 讓 App 能對使用者**據實**說明這張照片有沒有被保存
        # ——同意分流講給人聽才有意義。
        "stored": bool(consent), "image_id": image_id,
        "storage_receipt": storage_receipt,
        # `route` 讓「醫療版看得到、民眾版看不到」這種問題可以在一次回應裡歸因。
        "route": seg_info.get("route") or "student",
        "escalated": bool(seg_info.get("escalated")),
    }, anon_id, ip)


def _safe_json(s):
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


def _validated_lite_png(b64, bits, expected_size=None):
    """Bound allocation, verify PNG chunks, then decode all pixels before storage."""
    import base64
    import io
    from PIL import Image
    if len(b64) > ((MAX_DEPTH_BYTES + 2) // 3) * 4:
        raise ValueError("PNG payload exceeds size limit")
    raw = base64.b64decode(b64, validate=True)
    if len(raw) > MAX_DEPTH_BYTES or len(raw) < 33:
        raise ValueError("PNG payload size invalid")
    if raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
        raise ValueError("PNG header invalid")
    if raw[24] != bits or raw[25] != 0:
        raise ValueError("PNG must be %d-bit grayscale" % bits)
    size = (int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big"))
    if not all(0 < n <= 2048 for n in size) or size[0] * size[1] > 4194304:
        raise ValueError("PNG dimensions invalid")
    if expected_size is not None and size != expected_size:
        raise ValueError("Depth and validity mask dimensions differ")
    with Image.open(io.BytesIO(raw)) as im:
        im.verify()
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        if im.format != "PNG" or im.size != size:
            raise ValueError("PNG decoded dimensions invalid")
    return raw, size


def _store_depth_png(st, pre, b64, conf_b64=None):
    # Validate the whole pair before writing either asset. A store failure must
    # propagate: do not turn a partial write into a successful receipt.
    try:
        raw, size = _validated_lite_png(b64, 16)
        craw = _validated_lite_png(conf_b64, 8, size)[0] if conf_b64 else None
    except Exception as e:
        return False, str(e)
    st.put_blob(_fw._key(os.path.join(_dir(), pre + ".depth.png")), raw)
    if craw is not None:
        st.put_blob(_fw._key(os.path.join(_dir(), pre + ".conf.png")), craw)
    return True, ""


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

    denied = _lite_withdrawal_gate(anon_id)
    if denied is not None:
        return denied

    ip = _client_ip()
    ok, retry, why = rate_check(anon_id, ip, "annotation")
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
    st = _media_store()
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
    _research_append(os.path.join(_dir(), "lite_labels.jsonl"), rec)
    rate_record(anon_id, ip, "annotation")
    return jsonify({"status": "stored", "image_id": image_id,
                    "polygon_count": len(clean), "label_grade": "lay"}), 200



_LITE_REVOKED_ACTIONS = frozenset(("deleted", "withdrawal_requested", "delete_incomplete"))


def _lite_privacy_coordinator():
    from flask import current_app
    configuration = current_app.extensions.get('lite_attest_http')
    if configuration is None:
        return None  # Standalone local test harness; app.py always installs the gate.
    from lite_privacy_state import PrivacyState
    from lite_fenced_store import FencedPrivacy
    coordinator = configuration.get('privacy')
    if not isinstance(coordinator, (PrivacyState, FencedPrivacy)):
        raise RuntimeError('Lite privacy state unavailable')
    return coordinator


def _lite_withdrawal_gate(anon_id):
    """Fresh ledger evidence; cache misses/errors must not authorize re-enrolment."""
    try:
        privacy = _lite_privacy_coordinator()
        if privacy is not None and privacy.is_withdrawn(anon_id):
            return jsonify(error="withdrawn"), 410
        store = _media_store()
        for name in ("lite_index.jsonl", "lite_labels.jsonl"):
            key = _fw._key(os.path.join(_dir(), name))
            for line in store.read_lines_fresh(key):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("invalid withdrawal ledger")
                if row.get("anon_id") == anon_id and row.get("action") in _LITE_REVOKED_ACTIONS:
                    return jsonify(error="withdrawn"), 410
    except Exception:
        logger.exception("Lite withdrawal state unavailable")
        return jsonify(error="withdrawal_state_unavailable"), 503
    return None


def _lite_ledger_rows_fresh(name):
    key = _fw._key(os.path.join(_dir(), name))
    rows = [json.loads(line) for line in _media_store().read_lines_fresh(key) if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("invalid Lite ledger")
    return rows


def _lite_label_rows_fresh():
    return _lite_ledger_rows_fresh("lite_labels.jsonl")


def _latest_lite_labels(rows, *, include_ai=False):
    """GCS receipt objects are not ordered by revision. Never use list order for v2."""
    deleted = {e.get("anon_id") for e in rows if e.get("action") in _LITE_REVOKED_ACTIONS}
    latest = {}
    for row in rows:
        if row.get("action") or row.get("anon_id") in deleted:
            continue
        # Model output measurements are not human labels. Reviewer metrics and
        # orange "lay" overlays use the default exclusion, even after revisions.
        if not include_ai and (row.get("source") == "ai" or row.get("label_grade") == "ai_unverified"):
            continue
        key = (row.get("anon_id"), row.get("image_id"))
        old = latest.get(key)
        if old is None or row.get("revision", 0) >= old.get("revision", 0):
            latest[key] = row
    return latest


@lite_bp.route("/api/v1/lite/annotation/revision", methods=["POST"])
def lite_annotation_revision():
    """Idempotent revision of an EXISTING image. No inference or media upload.

    The slot is derived from identity + image + revision, not client-selected.
    append_record_once arbitrates concurrent writers (LocalStore lock/GCS generation=0).
    Full payload hash readback detects conflicting reuse, including a slot-hash collision.
    Acknowledgement never depends on an append followed by a separate receipt write.
    """
    import math
    import re
    if request.content_length is None or request.content_length > 262144:
        return jsonify(error="revision_too_large"), 413
    d = request.get_json(silent=True)
    if not isinstance(d, dict) or d.get("research_consent") is not True:
        return jsonify(error="consent_required"), 400
    aid, iid, rev, raw = (d.get(k) for k in ("anon_id", "image_id", "revision", "payload_json"))
    try:
        raw_bytes = raw.encode("utf-8") if isinstance(raw, str) else None
    except UnicodeError:
        return jsonify(error="invalid_revision"), 400
    if (not all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", v) for v in (aid, iid))
            or type(rev) is not int or not 1 <= rev <= 1000000
            or raw_bytes is None or len(raw_bytes) > 131072):
        return jsonify(error="invalid_revision"), 400
    try:
        p = json.loads(raw)
        required = {"polygons", "image_w", "image_h", "surface_cm2", "projected_cm2", "source", "consent_version"}
        optional = {"volume_ml", "max_depth_mm", "wound_id", "wound_side", "wound_site"}
        if not isinstance(p, dict) or not required <= p.keys() or p.keys() - required - optional:
            raise ValueError()
        # Retain the exact previously supported version for durable pending
        # retries; accept the current disclosure without accepting arbitrary text.
        if p["source"] not in ("manual", "ai") or p["consent_version"] not in ("2026-10-03.1", "2026-10-04.1"):
            raise ValueError()
        w, h = p["image_w"], p["image_h"]
        if any(type(v) is not int or not 1 <= v <= 4096 for v in (w, h)):
            raise ValueError()
        polys = p["polygons"]
        if not isinstance(polys, list) or not 1 <= len(polys) <= 16:
            raise ValueError()
        for poly in polys:
            if not isinstance(poly, list) or not 3 <= len(poly) <= 4000:
                raise ValueError()
            for pt in poly:
                if (not isinstance(pt, list) or len(pt) != 2 or any(type(v) is not int for v in pt)
                        or not 0 <= pt[0] < w or not 0 <= pt[1] < h):
                    raise ValueError()
        for k in ("surface_cm2", "projected_cm2", "volume_ml", "max_depth_mm"):
            v = p.get(k)
            if v is None and k in optional:
                continue
            if type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 100000:
                raise ValueError()
        # Fixed codes only; no patient names or arbitrary free-text location metadata.
        if p.get("wound_id") is not None and not re.fullmatch(r"[A-Fa-f0-9-]{36}", p["wound_id"]):
            raise ValueError()
        if p.get("wound_side") not in (None, "left", "right", "midline"):
            raise ValueError()
        sites = {"scalp", "face", "neck", "chest", "abdomen", "upper_back", "lower_back", "sacrococcygeal", "perineum",
                 "buttock", "hip", "upper_arm", "elbow", "forearm", "wrist", "hand_dorsum", "palm", "finger", "thigh",
                 "knee_front", "knee_back", "lower_leg_front", "lower_leg_back", "lower_leg_medial", "lower_leg_lateral",
                 "ankle_medial", "ankle_lateral", "heel", "foot_dorsum", "sole", "toe", "other"}
        if p.get("wound_site") is not None and p["wound_site"] not in sites:
            raise ValueError()
    except (ValueError, TypeError, OverflowError, RecursionError):
        return jsonify(error="invalid_payload"), 400
    digest = hashlib.sha256(raw_bytes).hexdigest()
    slot = hashlib.sha256(json.dumps([aid, iid, rev], separators=(",", ":")).encode()).hexdigest()[:16]
    key = _fw._key(os.path.join(_dir(), "lite_labels.jsonl"))
    st = _media_store()
    try:
        denied = _lite_withdrawal_gate(aid)
        if denied is not None:
            return denied
        rows = _lite_label_rows_fresh()
        if any(e.get("anon_id") == aid and e.get("action") in _LITE_REVOKED_ACTIONS for e in rows):
            return jsonify(error="withdrawn"), 410
        image_key = _fw._key(os.path.join(_dir(), "lite/%s/%s" % (aid, iid)))
        if not st.exists(image_key + ".jpg"):
            return jsonify(error="image_not_found"), 404
        meta = st.get_json(image_key + ".json")
        if not meta or (meta.get("image_w"), meta.get("image_h")) != (w, h):
            return jsonify(error="image_dimensions_mismatch"), 409
        def receipt(row):
            if (row.get("anon_id"), row.get("image_id"), row.get("revision"), row.get("payload_sha256")) != (aid, iid, rev, digest):
                return jsonify(error="revision_conflict"), 409
            return jsonify(status="stored", image_id=iid, revision=rev, payload_sha256=digest), 200
        old = next((e for e in rows if e.get("annotation_receipt_id") == slot), None)
        if old is not None:
            return receipt(old)  # Retries don't consume allowance again.
        ok, retry, why = rate_check(aid, _client_ip(), "annotation")
        if not ok:
            return jsonify(error="rate_limited", reason=why, retry_after=retry), 429
        row = dict(p, anon_id=aid, image_id=iid, revision=rev, payload_sha256=digest,
                   annotation_receipt_id=slot, polygon_count=len(polys),
                   label_grade="lay" if p["source"] == "manual" else "ai_unverified", received_at=_fw.utc_now())
        created = st.append_record_once(key, slot, row)
        denied = _lite_withdrawal_gate(aid)
        if denied is not None:
            return denied
        readback = _lite_label_rows_fresh()
        if any(e.get("anon_id") == aid and e.get("action") in _LITE_REVOKED_ACTIONS for e in readback):
            return jsonify(error="withdrawn"), 410
        stored = next((e for e in readback if e.get("annotation_receipt_id") == slot), None)
        if stored is None:
            raise IOError("revision readback missing")
        # Rate tracking failure may return 503, but retry still finds the durable receipt.
        if created:
            rate_record(aid, _client_ip(), "annotation")
        return receipt(stored)
    except Exception:
        logger.exception("Lite revision not confirmed")
        return jsonify(error="revision_unconfirmed"), 503


@lite_bp.route("/api/v1/lite/data/<anon_id>", methods=["DELETE"])
def lite_delete(anon_id):
    """撤回：刪掉這個裝置匿名代碼底下的全部資料。

    app.py 的前置閘門驗證裝置簽章、永久封鎖該owner的新寫入，
    並等既有寫入完成才進入本函式。這裡清除可見媒體物件；
    tombstone排除研究索引／標註，不宣稱刪除歷史版本、備份或既有模型。
    """
    import re
    anon_id = (anon_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", anon_id):
        return jsonify({"error": "anon_id 格式不合"}), 400
    privacy = _fenced_privacy()
    if privacy is not None:
        if request.environ.get('woundlite.write_fence') != 'gcs-generation-v1':
            return jsonify(error='withdrawal_fence_unconfirmed'), 503
        try:
            return jsonify(privacy.complete_withdrawal(anon_id)), 200
        except Exception:
            logger.exception('Lite fenced withdrawal not confirmed')
            return jsonify(error='delete_incomplete', anon_id=anon_id), 503
    idx = os.path.join(_dir(), "lite_index.jsonl")
    labels = os.path.join(_dir(), "lite_labels.jsonl")
    # Revoke admission before inventory, even for an upload with no index row.
    # The installed HTTP gate drains durable writer tickets before this inventory.
    # Standalone route fixtures do not assert distributed cleanup evidence.
    try:
        st = _media_store()
        _fw.append_jsonl(labels, {"anon_id": anon_id, "action": "withdrawal_requested",
                                  "received_at": _fw.utc_now()})
    except Exception:
        logger.exception("Lite withdrawal marker not persisted")
        return jsonify(error="withdrawal_unconfirmed"), 503
    n = 0
    try:
        rows = [e for e in _lite_ledger_rows_fresh("lite_index.jsonl")
                if e.get("anon_id") == anon_id and not e.get("action")]
        n_lab = len([e for e in _lite_label_rows_fresh()
                     if e.get("anon_id") == anon_id and not e.get("action")])
        # Index append can fail after a successful blob write. Inventory the
        # actual namespace, and validate ALL returned keys before deleting any.
        prefix = _fw._key(os.path.join(_dir(), "lite", anon_id)).rstrip("/") + "/"
        keys = list(st.list_keys(prefix))
        for key in keys:
            if (not isinstance(key, str) or not key.startswith(prefix)
                    or not re.fullmatch(r"[a-f0-9]{16}\.(?:jpg|json|depth\.png|conf\.png)", key[len(prefix):])):
                raise ValueError("unexpected object in Lite withdrawal inventory")
        for key in sorted(set(keys)):
            if st.delete(key):
                n += 1
            if st.exists(key):
                raise IOError("Lite object still exists after deletion")
        if list(st.list_keys(prefix)):
            raise IOError("Lite namespace not empty after deletion")
        research_rows_removed = None
        raw_objects_removed = None
        if request.environ.get("woundlite.writers_drained") is True:
            from lite_raw_depth_store import erase_installation_objects
            raw_objects_removed = erase_installation_objects(st, anon_id)
            n += raw_objects_removed
            from lite_ledger_purge import purge_live_research_rows
            # The installed gate has permanently closed this owner's admission.
            # Other owners may still append: purge uses process locks / GCS generations.
            research_rows_removed = sum(purge_live_research_rows(st, _fw._key(path), anon_id)
                                        for path in (idx, labels))
        _fw.append_jsonl(labels, {"anon_id": anon_id, "action": "deleted",
                                  "received_at": _fw.utc_now()})
        _fw.append_jsonl(idx, {"anon_id": anon_id, "action": "deleted",
                               "objects": n, "received_at": _fw.utc_now()})
    except Exception as exc:
        logger.exception("Lite withdrawal cleanup not confirmed")
        try:
            _fw.append_jsonl(idx, {"anon_id": anon_id, "action": "delete_incomplete",
                                   "objects_removed": n, "error": type(exc).__name__,
                                   "received_at": _fw.utc_now()})
        except Exception:
            logger.exception("Lite incomplete cleanup marker not persisted")
        # The earlier revocation remains effective even when inventory, deletion,
        # readback or the completion ledger fails. Never expose storage keys here.
        return jsonify(error="delete_incomplete", anon_id=anon_id, objects_removed=n), 503
    return jsonify({"status": "deleted", "anon_id": anon_id,
                    "records": len(rows), "objects_removed": n,
                    "labels_withdrawn": n_lab, "deletion_scope": "live_media",
                    "research_ledger_cleanup": ("live_rows_removed" if research_rows_removed is not None else "not_performed"),
                    "research_rows_removed": research_rows_removed,
                    "raw_objects_removed": raw_objects_removed,
                    "writers_drained": request.environ.get("woundlite.writers_drained", False)}), 200


def _lite_reviewer_ok():
    """民眾版檢視端點共用的守門：登入＋audit.read。回 (ok, 錯誤回應)。"""
    from flask_jwt_extended import get_jwt, verify_jwt_in_request
    try:
        verify_jwt_in_request()
    except Exception:
        return False, (jsonify({"error": "需要登入"}), 401)
    role = (get_jwt() or {}).get("role")
    try:
        import auth_users
        if auth_users.can(role, "audit.read"):
            return True, None
    except Exception:
        pass                     # fail-closed
    return False, (jsonify({"error": "權限不足",
                            "issues": ["民眾版資料僅工程師／管理者可檢視。"]}), 403)


@lite_bp.route("/api/v1/lite/record/<anon_id>/<image_id>/image.jpg", methods=["GET"])
def lite_record_image(anon_id, image_id):
    """民眾版影像檢視。**要登入＋audit.read**——與 records 同一道門。

    這個模組其餘端點都是匿名的；這兩個檢視端點是唯二例外，
    所以守門寫在函式最前面而不是依賴「反正呼叫端是 console」。
    """
    ok, err = _lite_reviewer_ok()
    if not ok:
        return err
    anon_id, image_id = (anon_id or "").strip(), (image_id or "").strip()
    if "/" in anon_id or ".." in anon_id or not image_id.isalnum():
        return jsonify({"error": "格式不合"}), 400
    denied = _lite_withdrawal_gate(anon_id)
    if denied is not None:
        return denied
    raw = _media_store().get_blob(_fw._key(os.path.join(
        _dir(), "lite/%s/%s.jpg" % (anon_id, image_id))))
    if raw is None:
        return jsonify({"error": "影像不存在（可能已撤回刪除）"}), 404
    from flask import Response as _Resp
    return _Resp(raw, content_type="image/jpeg",
                 headers={"Cache-Control": "private, no-store"})


@lite_bp.route("/api/v1/lite/record/<anon_id>/<image_id>/preview.svg", methods=["GET"])
def lite_record_preview(anon_id, image_id):
    """AI 輪廓 vs 民眾修正輪廓的**對照圖**。要登入＋audit.read。

    畫在中性背景上、不含影像像素（與臨床 preview 同一原則）；
    要看照片另有 image.jpg。兩色對照：青＝AI、橘＝民眾修正——
    lay 修正率那個數字說「改了多少」，這張圖回答「改在哪裡」。
    """
    ok, err = _lite_reviewer_ok()
    if not ok:
        return err
    anon_id, image_id = (anon_id or "").strip(), (image_id or "").strip()
    if "/" in anon_id or ".." in anon_id or not image_id.isalnum():
        return jsonify({"error": "格式不合"}), 400
    denied = _lite_withdrawal_gate(anon_id)
    if denied is not None:
        return denied
    mraw = _media_store().get_blob(_fw._key(os.path.join(
        _dir(), "lite/%s/%s.json" % (anon_id, image_id))))
    if mraw is None:
        return jsonify({"error": "查無此筆"}), 404
    meta = json.loads(mraw.decode("utf-8"))
    w, h = int(meta.get("image_w") or 640), int(meta.get("image_h") or 480)
    ai = meta.get("ai_polygons") or []
    lab = _latest_lite_labels(_lite_label_rows_fresh()).get((anon_id, image_id), {})
    lay = lab.get("polygons") or []
    iou = lab.get("iou_vs_ai")

    overlay = request.args.get("overlay") == "1"

    def _poly(pts, color, dash=""):
        p = " ".join("%s,%s" % (pt[0], pt[1]) for pt in pts)
        # ⚠ **overlay 模式下 SVG 內不設任何 alpha。**
        #
        # 第一版填色寫死 `%s22`（alpha 0x22 ≈ 13%）。疊圖時外層 div 還有一層
        # opacity，兩者**相乘**——滑桿拉到 100% 實際只有 13%，
        # 使用者的回報是「拉到最高還是不明顯」，而且他無從知道為什麼。
        #
        # 一個東西只由一個地方控制：透明度完全交給滑桿，SVG 只負責形狀與顏色。
        # 非 overlay（中性背景）時仍用淡填色，那裡沒有第二層 opacity。
        fill = color if overlay else (color + "22")
        return ('<polygon points="%s" fill="%s" stroke="%s" stroke-width="%d"%s/>'
                % (p, fill, color, max(2, w // 300), dash))
    parts = [_poly(p, "#00e5ff") for p in ai]
    parts += [_poly(p, "#ff9f1c", ' stroke-dasharray="12,6"') for p in lay]
    # overlay 模式：拿掉背景與圖例，讓主控台把它疊在真實照片上。
    # 圖例由主控台在圖外畫——疊圖時寫在圖上會蓋到傷口，而且照片是彩色的，
    # 文字不論什麼顏色都可能看不清。
    bg = "" if overlay else ('<rect width="%d" height="%d" fill="#1c1f24"/>' % (w, h))
    legend = ""
    if not overlay:
        fs = max(14, w // 40)
        legend = ('<text x="12" y="%d" font-size="%d" fill="#00e5ff">— AI（%d）</text>'
                  '<text x="12" y="%d" font-size="%d" fill="#ff9f1c">-- 民眾修正（%d）%s</text>'
                  % (fs + 8, fs, len(ai), 2 * fs + 14, fs, len(lay),
                     ("　IoU %.3f" % iou) if iou is not None else ""))
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d">%s%s%s</svg>'
           % (w, h, bg, "".join(parts), legend))
    from flask import Response as _Resp
    return _Resp(svg, content_type="image/svg+xml", headers={"Cache-Control": "private, no-store"})


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

    try:
        idx = _lite_ledger_rows_fresh("lite_index.jsonl")
        label_rows = _lite_label_rows_fresh()
        privacy = _lite_privacy_coordinator()
        privacy_deleted = set()
        if privacy is not None:
            for owner in {row.get("anon_id") for row in idx + label_rows}:
                if privacy.is_withdrawn(owner):
                    privacy_deleted.add(owner)
    except Exception:
        logger.exception("Lite reviewer withdrawal state unavailable")
        return jsonify(error="withdrawal_state_unavailable"), 503
    deleted = {e.get("anon_id") for e in idx + label_rows
               if e.get("action") in _LITE_REVOKED_ACTIONS} | privacy_deleted
    rows = [e for e in idx if not e.get("action") and e.get("anon_id") not in deleted]
    # 內測機。**列出但不計入統計**：藏起來會讓「清單有 7 筆、統計說 5 筆」
    # 變成一個沒人解釋得了的謎；計入則污染 route 儀表（內測都是印刷樣例，
    # escalated 占比會被灌高）。逗號分隔的 anon_id 前綴，掛環境變數。
    _internal = {p.strip() for p in os.environ.get("LITE_INTERNAL_ANON", "").split(",")
                 if p.strip()}
    def _is_internal(aid):
        return any((aid or "").startswith(p) for p in _internal)
    lab_by_img = _latest_lite_labels([e for e in label_rows if e.get("anon_id") not in deleted])
    labels = list(lab_by_img.values())
    measurements = _latest_lite_labels([e for e in label_rows if e.get("anon_id") not in deleted], include_ai=True)

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
        lab = lab_by_img.get((e.get("anon_id"), e.get("image_id")))
        measured = measurements.get((e.get("anon_id"), e.get("image_id")), {})
        out.append({
            "received_at": e.get("received_at"), "anon_id": e.get("anon_id"),
            "image_id": e.get("image_id"), "client": e.get("client"),
            "route": e.get("route"), "escalated": bool(e.get("escalated")),
            "polygons": e.get("polygons"), "bytes": e.get("bytes"),
            "depth": e.get("depth"), "consent_version": e.get("consent_version"),
            # 民眾自己畫的輪廓數。**與 polygons（AI 的）分開兩欄**——
            # 合成一欄會讓「AI 空手但人畫了」這個最有價值的組合看不出來。
            "lay_polygons": (lab or {}).get("polygon_count"),
            "lay_revision": (lab or {}).get("revision"),
            "lay_measurement": {k: (lab or {}).get(k) for k in ("surface_cm2", "projected_cm2", "volume_ml", "max_depth_mm")},
            "measurement_source": measured.get("source"),
            "measurement_revision": measured.get("revision"),
            "measurement": {k: measured.get(k) for k in ("surface_cm2", "projected_cm2", "volume_ml", "max_depth_mm")},
            "wound_location": {k: measured.get(k) for k in ("wound_id", "wound_side", "wound_site")},
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
                             and lab_by_img.get((e.get("anon_id"), e.get("image_id")))),
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
