"""Shared Lite raster helpers, independent of Flask, storage and research APIs.

Contour ordering/thresholds and the existing conservative face check are kept
unchanged. Face detection is a partial privacy check, not deidentification.
"""
import os
import hashlib


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
