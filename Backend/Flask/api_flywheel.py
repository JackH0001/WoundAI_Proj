# -*- coding: utf-8 -*-
"""飛輪 HTTP 端點(Blueprint):/api/v1/annotation(上傳醫師驗證標註→再訓練佇列)、
/api/v1/consent/withdraw(撤回同意→排除訓練+隔離影像)、/api/v1/flywheel/stats(佇列健康度)。
需 JWT、去識別、醫師驗證。輔助、非診斷。
app.py 註冊:from api_flywheel import flywheel_bp; app.register_blueprint(flywheel_bp)

【2026-07-28 資料鏈修正 P0】先前佇列只存 gt_polygon,**沒有影像也沒有影像尺寸**
→ 所有樣本都是「孤兒 GT」,無法柵格化成遮罩、無法訓練(稽核發現 8/8 筆不可用)。
現在強制綁定 classify 回傳的 image_id(內容 sha1,後端已存 flywheel/images/<id>.jpg)
與 image_w/image_h(polygon 座標空間)。去重鍵改 (image_id, poly_sig):
同影像微差描邊不再灌成兩筆互相矛盾的 GT。

【撤回語義】撤回的是「這張影像/這位受試者」的同意,不是「這一筆紀錄」——
故以 **code 與 image_id 雙鍵**排除:同影像的其他標註(含被取代的舊版)一併失效,
且影像檔移入 quarantine/ 不再進任何資料集(對齊 IRB 同意書「撤回即下架」承諾)。
佇列 jsonl 維持 append-only(稽核軌跡不竄改, IEC 62304),排除發生在消費端。

驗證邏輯抽出為純函式(validate_annotation/effective_queue/...)供契約與單元測試。"""
import os, json, re, time, hashlib, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
# 測試/驗收腳本可用 WOUNDAI_FLYWHEEL_DIR 指到暫存目錄,避免把測試樣本寫進產線佇列
# 與 append-only 的 audit.jsonl(洗不掉)。app.py 存影像時亦 import 本模組的 IMAGES_DIR。
FLYWHEEL_DIR = os.environ.get("WOUNDAI_FLYWHEEL_DIR") or os.path.join(HERE, "flywheel")
QUEUE = os.path.join(FLYWHEEL_DIR, "retrain_queue.jsonl")
WITHDRAWN = os.path.join(FLYWHEEL_DIR, "withdrawn.jsonl")
AUDIT = os.path.join(FLYWHEEL_DIR, "audit.jsonl")
IMAGES_DIR = os.path.join(FLYWHEEL_DIR, "images")
QUARANTINE_DIR = os.path.join(FLYWHEEL_DIR, "quarantine")

# 去識別 + 醫師驗證守門
REQUIRED_CORE = ["code", "gt_polygon", "exudate", "doctor_verified", "deidentified", "consent_train"]
# 影像綁定(無這三個欄位 = 孤兒 GT,不可訓練)
REQUIRED_IMAGE = ["image_id", "image_w", "image_h"]
REQUIRED = REQUIRED_CORE + REQUIRED_IMAGE
# 溯源欄位(非強制,但缺了無法做模型治理/回溯歸因)
PROVENANCE = ["mm_per_px", "route", "seg_model", "app_version", "correction_iou", "care_note", "source"]

# 樣本來源。範例/驗證影像可以走同一條管線收進來,但**不得混入臨床樣本數**:
#   clinical = 真實病人傷口(唯一可作臨床證據者)
#   sample   = 範例/示範用真實傷口照(如 test_wounds_aruco_v2 那 5 張)——注意它們是 escalate
#              路由的驗收基準,拿去訓練等於「考卷當講義」,匯出訓練集時請明確排除
#   phantom  = 印刷模擬傷口/幾何色塊(面積驗證用)——**不具傷口材質**,訓練它只會教模型
#              分割印刷紅方塊,對真實傷口無遷移價值
#   external = 外部公開資料集
SOURCES = ("clinical", "sample", "phantom", "external")
DEFAULT_SOURCE = "clinical"

# 白名單:image_id/code 會被當成檔名使用 → 不做字元限制就是路徑穿越漏洞
ID_RE = re.compile(r"^[0-9a-f]{16}$")           # classify 產生的 sha1 前 16 碼
CODE_RE = re.compile(r"^WD-[A-Za-z0-9_-]{1,32}$")


def utc_now():
    """UTC ISO8601。用本地時間會讓 DST/搬機器時「取最新」排序反轉。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def validate_annotation(d: dict, require_image: bool = True):
    """回 (ok, 問題清單)。守門:必填齊、三同意皆 True、code 為合法 WD-*、
    image_id 為合法雜湊、polygon 至少 3 點且落在影像範圍內、exudate 0-3。
    require_image=False 僅供舊契約回歸測試,產線一律 True。"""
    issues = []
    need = REQUIRED if require_image else REQUIRED_CORE
    for k in need:
        if k not in d or d.get(k) is None:
            issues.append(f"缺 {k}")
    if not d.get("doctor_verified"): issues.append("未經醫師驗證(doctor_verified=false)")
    if not d.get("deidentified"): issues.append("未去識別化(deidentified=false)")
    if not d.get("consent_train"): issues.append("未取得訓練同意(consent_train=false)")
    if d.get("code") is not None and not CODE_RE.match(str(d.get("code"))):
        issues.append("code 格式不合(應為 WD- 加英數/底線/連字號,1-32 字)")
    if require_image and d.get("image_id") is not None and not ID_RE.match(str(d.get("image_id"))):
        issues.append("image_id 格式不合(應為 16 位小寫十六進位;防路徑穿越)")

    src = d.get("source")
    if src is not None and str(src) not in SOURCES:
        issues.append(f"source 須為 {'/'.join(SOURCES)}(預設 {DEFAULT_SOURCE})")

    ex = d.get("exudate")
    if ex is not None:
        try:
            if not (0 <= int(ex) <= 3): issues.append("exudate 應為 0-3(PUSH 滲液子分)")
        except (TypeError, ValueError):
            issues.append("exudate 非整數")

    poly = d.get("gt_polygon") or []
    if len(poly) < 3:
        issues.append("gt_polygon 少於 3 點(無法構成面)")
    elif require_image:
        try:
            w, h = int(d.get("image_w") or 0), int(d.get("image_h") or 0)
            if w <= 0 or h <= 0:
                issues.append("image_w/image_h 非正整數")
            else:
                oob = [p for p in poly if not (0 <= float(p[0]) <= w and 0 <= float(p[1]) <= h)]
                if oob:
                    issues.append(f"gt_polygon 有 {len(oob)} 點超出影像範圍 {w}x{h}(座標空間不符)")
        except (TypeError, ValueError, IndexError):
            issues.append("gt_polygon 或 image_w/h 格式錯誤")
    return (len(issues) == 0, issues)


def append_jsonl(path: str, rec: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read_jsonl(path: str, with_bad: bool = False):
    """回紀錄清單(只收 dict)。壞行不靜默消失:with_bad=True 時一併回 bad 計數,
    讓統計把它算進 malformed(否則 append-only 稽核軌跡的破損無從察覺,
    且一行是合法 JSON 但非物件時 effective_queue 會 AttributeError)。"""
    out, bad = [], 0
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try:
                    rec = json.loads(line)
                except Exception:
                    bad += 1; continue
                if isinstance(rec, dict): out.append(rec)
                else: bad += 1
    return (out, bad) if with_bad else out


def audit(actor: str, action: str, code: str, result: str):
    append_jsonl(AUDIT, {"ts": utc_now(), "actor": actor,
                         "action": action, "code": code, "result": result})


def poly_sig(poly):
    """遮罩輪廓內容雜湊(座標四捨五入後序列化)→ 供去重。同一傷口遮罩→同 sig。"""
    try:
        norm = json.dumps([[round(float(p[0])), round(float(p[1]))] for p in (poly or [])], sort_keys=True)
    except Exception:
        norm = json.dumps(poly or [])
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def sample_key(image_id, poly):
    """樣本身分 = (影像, 遮罩)。同影像不同描邊 → 不同 key(視為修訂,見 find_duplicate)。"""
    return f"{image_id or '-'}::{poly_sig(poly)}"


def find_duplicate(path, image_id, poly):
    """回 (完全重複 rec 或 None, 同影像既有 rec 清單)。
    - 完全重複(同影像同遮罩)→ 略過,避免灌爆同一樣本。
    - 同影像不同遮罩 → **不是重複,是修訂**:仍收下,但標 supersedes 讓匯出只取最新,
      避免同一張圖出現兩份互相矛盾的 GT(舊實作就是這樣混進 WD-14864776/WD-17879162)。"""
    key = sample_key(image_id, poly)
    exact, same_img = None, []
    for rec in read_jsonl(path):
        if rec.get("image_id") and image_id and rec.get("image_id") == image_id:
            same_img.append(rec)
            if sample_key(rec.get("image_id"), rec.get("gt_polygon")) == key:
                exact = rec
    return exact, same_img


def is_duplicate(path, poly, image_id=None):
    """相容舊呼叫端。image_id 給了就用 (image_id, poly) 當鍵,否則退回純 poly 比對。"""
    if image_id:
        return find_duplicate(path, image_id, poly)[0] is not None
    sig = poly_sig(poly)
    if not sig or sig == poly_sig([]): return False
    return any(poly_sig(r.get("gt_polygon")) == sig for r in read_jsonl(path))


def withdrawn_keys(withdrawn_path=None):
    """回 (撤回的 code 集合, 撤回的 image_id 集合)。

    - 撤回涵蓋**整張影像**:一個 code 可能對到多張影像(受試者多次回診),
      故 `image_ids`(複數)與 `image_id`(單數,相容欄位)都要吃。
      只讀單數會讓第二張以後的影像沒被擋住,兄弟標註照樣進訓練集。
    - 依檔案順序(＝時間順序)重播;`action:"restore"`(重新取得同意)會撤銷先前的排除。"""
    codes, imgs = set(), set()
    for r in read_jsonl(withdrawn_path or WITHDRAWN):
        ids = {i for i in (r.get("image_ids") or []) if i}
        if r.get("image_id"): ids.add(r["image_id"])
        if str(r.get("action", "withdraw")) == "restore":
            codes.discard(r.get("code")); imgs -= ids
        else:
            if r.get("code"): codes.add(r["code"])
            imgs |= ids
    return codes, imgs


def is_quarantined(image_id, quarantine_dir=None):
    """影像是否在隔離區。classify 重複上傳同一張已撤回的照片時,不可把它復活寫回 images/。"""
    if not image_id or not ID_RE.match(str(image_id)): return False
    return os.path.exists(os.path.join(quarantine_dir or QUARANTINE_DIR, f"{image_id}.jpg"))


def is_consent_blocked(image_id, withdrawn_path=None, quarantine_dir=None):
    """該影像是否已撤回訓練同意(withdrawn 名單或隔離區任一命中)。供 app.py classify 與端點共用。"""
    if not image_id: return False
    return str(image_id) in withdrawn_keys(withdrawn_path)[1] or is_quarantined(image_id, quarantine_dir)


def rec_source(rec):
    s = str(rec.get("source") or DEFAULT_SOURCE)
    return s if s in SOURCES else DEFAULT_SOURCE


def effective_queue(queue_path=None, images_dir=None, withdrawn_path=None, source=None):
    """可訓練樣本 = 欄位完整、有影像檔、三同意皆真、未撤回(code 或 image_id)、同影像取最新。
    回 (可用 rec 清單, 統計 dict)。匯出腳本與 /flywheel/stats 共用同一判準(單一真相)。

    @param source 只保留指定來源(str 或 iterable);None=全收。統計一律附 by_source 分項,
      讓「臨床樣本數」不會被範例/模擬影像灌水——這是送件數字誠實與否的關鍵。"""
    keep = None
    if source is not None:
        keep = {source} if isinstance(source, str) else set(source)
    queue_path = queue_path or QUEUE
    images_dir = images_dir or IMAGES_DIR
    wd_codes, wd_imgs = withdrawn_keys(withdrawn_path)
    recs, bad = read_jsonl(queue_path, with_bad=True)
    stats = {"total": len(recs) + bad, "orphan_no_image": 0, "malformed": bad, "image_file_missing": 0,
             "withdrawn": 0, "consent_invalid": 0, "superseded": 0, "other_source": 0, "trainable": 0}
    latest = {}
    for r in recs:
        iid = r.get("image_id")
        if not iid:
            stats["orphan_no_image"] += 1; continue
        # 匯出端不盲信端點:欄位/格式/同意狀態一律重驗(--queue 可指向任意檔)
        if not ID_RE.match(str(iid)) or not r.get("image_w") or not r.get("image_h") \
                or len(r.get("gt_polygon") or []) < 3 or not CODE_RE.match(str(r.get("code") or "")):
            stats["malformed"] += 1; continue
        if r.get("code") in wd_codes or iid in wd_imgs:
            stats["withdrawn"] += 1; continue
        if not (r.get("doctor_verified") and r.get("deidentified") and r.get("consent_train")):
            stats["consent_invalid"] += 1; continue
        if not os.path.exists(os.path.join(images_dir, str(iid) + ".jpg")):
            stats["image_file_missing"] += 1; continue
        if keep is not None and rec_source(r) not in keep:
            stats["other_source"] += 1; continue
        prev = latest.get(iid)
        if prev is not None: stats["superseded"] += 1
        # received_at 遞增(UTC) → 後到者為醫師最新修訂。
        # 用 `or ""` 而非 get 預設值:顯式 null 會變字串 "None",而 "None" > "2026-..."(字典序)
        # 會讓沒有時間戳的那筆永遠勝出。
        if prev is None or (r.get("received_at") or "") >= (prev.get("received_at") or ""):
            latest[iid] = r
    out = list(latest.values())
    stats["trainable"] = len(out)
    # 分項:臨床樣本數不可被範例/模擬影像灌水(送件數字誠實與否的關鍵)
    stats["by_source"] = {s: sum(1 for r in out if rec_source(r) == s) for s in SOURCES}
    return out, stats


def quarantine_image(image_id, images_dir=None, quarantine_dir=None):
    """撤回同意 → 影像移出 images/,不再進任何資料集。保留於 quarantine/ 供稽核,
    實體銷毀另走資料刪除程序(需留紀錄)。回 True 表示有搬動。"""
    if not image_id or not ID_RE.match(str(image_id)): return False
    src = os.path.join(images_dir or IMAGES_DIR, f"{image_id}.jpg")
    if not os.path.exists(src): return False
    dst_dir = quarantine_dir or QUARANTINE_DIR
    os.makedirs(dst_dir, exist_ok=True)
    shutil.move(src, os.path.join(dst_dir, f"{image_id}.jpg"))
    return True


# ---- Flask Blueprint(import flask 失敗時不影響純函式測試) ----
try:
    from flask import Blueprint, request, jsonify
    from flask_jwt_extended import jwt_required, get_jwt_identity
    flywheel_bp = Blueprint("flywheel", __name__)

    @flywheel_bp.route("/api/v1/annotation", methods=["POST"])
    @jwt_required()
    def post_annotation():
        d = request.get_json(silent=True) or {}
        ok, issues = validate_annotation(d)
        actor = get_jwt_identity() or "unknown"
        if not ok:
            audit(actor, "annotation_rejected", d.get("code", "?"), ";".join(issues))
            return jsonify({"error": "標註不符上傳規範", "issues": issues}), 400

        image_id = str(d.get("image_id"))

        # ⚠ 同意檢查必須排在「影像是否存在」**之前**。
        # 撤回同意會把影像移進 quarantine/,所以撤回後的補送必然先撞上 os.path.exists() 失敗,
        # 回給醫師與寫進 audit.jsonl 的理由就變成「後端查無影像」——技術上沒錯,但那是
        # **錯誤的拒絕理由**:稽核軌跡看不出這筆是因撤回同意而被擋(IRB 要看的正是這件事),
        # 醫師也會誤以為是上傳壞掉而反覆重送。先查同意,理由才會是真正的原因。
        wd_codes, wd_imgs = withdrawn_keys()
        # 端點與 effective_queue 的排除規則必須一致(code ∪ image_id),
        # 否則會出現「端點回 200 已入佇列、匯出時卻算 withdrawn 靜默丟掉」的假成功。
        blocked = []
        if image_id in wd_imgs or is_quarantined(image_id): blocked.append(f"影像 {image_id} 已撤回訓練同意")
        if d.get("code") in wd_codes: blocked.append(f"代碼 {d.get('code')} 已撤回訓練同意")
        if blocked:
            msg = ";".join(blocked) + "。重新取得同意請先呼叫 /api/v1/consent/restore"
            audit(actor, "annotation_rejected", d.get("code", "?"), msg)
            return jsonify({"error": "標註不符上傳規範", "issues": [msg]}), 400

        # 影像必須真的在後端(classify 時已存);沒有像素的 GT 不可訓練 → 直接擋
        if not os.path.exists(os.path.join(IMAGES_DIR, image_id + ".jpg")):
            msg = (f"後端查無影像 {image_id}。可能是:(a) 未先呼叫 /api/v1/classify、"
                   f"(b) 後端曾清空 flywheel/images、(c) 這是舊版 App 產生的紀錄。"
                   f"請重新以後端模式量測一次再送出")
            audit(actor, "annotation_rejected", d.get("code", "?"), msg)
            return jsonify({"error": "標註不符上傳規範", "issues": [msg]}), 400

        exact, same_img = find_duplicate(QUEUE, image_id, d.get("gt_polygon"))
        # 已撤回的舊紀錄不算重複(否則重新取得同意後永遠補不回來)
        if exact is not None and exact.get("code") not in wd_codes:
            audit(actor, "annotation_duplicate", d.get("code", "?"), f"同影像同遮罩已在佇列({exact.get('code')})")
            return jsonify({"status": "duplicate_skipped", "code": d.get("code"),
                            "note": "相同影像的相同遮罩已在再訓練佇列,已自動略過(避免重複樣本)"}), 200

        rec = {k: d.get(k) for k in REQUIRED}
        for k in PROVENANCE:
            if d.get(k) is not None: rec[k] = d.get(k)
        rec["source"] = str(d.get("source") or DEFAULT_SOURCE)   # 顯式落盤,免得日後靠預設值猜
        rec["poly_sig"] = poly_sig(d.get("gt_polygon"))
        rec["supersedes"] = [r.get("code") for r in same_img] or None
        rec["actor"] = actor
        rec["received_at"] = utc_now()
        append_jsonl(QUEUE, rec)
        note = None
        if same_img:
            note = f"同影像已有 {len(same_img)} 筆舊標註,本筆視為醫師修訂版,匯出時只取最新"
            audit(actor, "annotation_revised", rec["code"], note)
        audit(actor, "annotation_enqueued", rec["code"], "ok")
        return jsonify({"status": "enqueued", "code": rec["code"], "queue": "retrain",
                        "image_id": image_id, "note": note}), 200

    @flywheel_bp.route("/api/v1/consent/withdraw", methods=["POST"])
    @jwt_required()
    def post_withdraw():
        d = request.get_json(silent=True) or {}
        code = d.get("code")
        actor = get_jwt_identity() or "unknown"
        if not code:
            return jsonify({"error": "缺 code"}), 400
        # 撤回涵蓋整張影像:由 code 回查其 image_id(可能多張,受試者多次回診),連同呼叫端直接指定者
        imgs = {r["image_id"] for r in read_jsonl(QUEUE)
                if r.get("code") == code and r.get("image_id")}
        if d.get("image_id"):
            # 未驗證就收會讓呼叫端傳個 "000" 把整個影像層排除規則廢掉
            if not ID_RE.match(str(d["image_id"])):
                return jsonify({"error": "image_id 格式不合(應為 16 位小寫十六進位)"}), 400
            imgs.add(str(d["image_id"]))
        append_jsonl(WITHDRAWN, {"code": code, "action": "withdraw",
                                 "image_id": (sorted(imgs)[0] if imgs else None),
                                 "image_ids": sorted(imgs), "withdrawn_at": utc_now(), "actor": actor})
        moved = [i for i in sorted(imgs) if quarantine_image(i)]
        audit(actor, "consent_withdraw", code,
              f"排除訓練;影像隔離 {len(moved)}/{len(imgs)}")
        return jsonify({"status": "withdrawn", "code": code, "image_ids": sorted(imgs),
                        "quarantined": moved,
                        "effect": "已撤回:該影像的所有標註(含修訂版)一律排除於訓練集與統計,"
                                  "影像檔移入 quarantine/ 不再進任何資料集。"
                                  "佇列檔為 append-only 稽核軌跡故保留原紀錄(不竄改);"
                                  "實體銷毀請另走資料刪除程序並留紀錄。"}), 200

    @flywheel_bp.route("/api/v1/consent/restore", methods=["POST"])
    @jwt_required()
    def post_restore():
        """重新取得同意(re-consent)。沒有這條,撤回就是死局:影像被隔離、code 被封,
        受試者日後再次同意也補不回來,而端點只會回一句「已撤回」。
        寫入 action:"restore" 紀錄(不刪除原撤回紀錄,稽核軌跡保持完整),並把影像搬回 images/。"""
        d = request.get_json(silent=True) or {}
        code = d.get("code"); actor = get_jwt_identity() or "unknown"
        if not code:
            return jsonify({"error": "缺 code"}), 400
        _, wd_imgs = withdrawn_keys()
        imgs = {r["image_id"] for r in read_jsonl(QUEUE)
                if r.get("code") == code and r.get("image_id") and r["image_id"] in wd_imgs}
        if d.get("image_id"):
            if not ID_RE.match(str(d["image_id"])):
                return jsonify({"error": "image_id 格式不合(應為 16 位小寫十六進位)"}), 400
            imgs.add(str(d["image_id"]))
        restored = []
        for i in sorted(imgs):
            src = os.path.join(QUARANTINE_DIR, f"{i}.jpg")
            if os.path.exists(src):
                os.makedirs(IMAGES_DIR, exist_ok=True)
                shutil.move(src, os.path.join(IMAGES_DIR, f"{i}.jpg")); restored.append(i)
        append_jsonl(WITHDRAWN, {"code": code, "action": "restore", "image_ids": sorted(imgs),
                                 "restored_at": utc_now(), "actor": actor,
                                 "note": d.get("note")})
        audit(actor, "consent_restore", code, f"重新同意;影像回復 {len(restored)}/{len(imgs)}")
        return jsonify({"status": "restored", "code": code, "image_ids": sorted(imgs),
                        "restored": restored,
                        "effect": "已重新納入:該 code/影像不再被排除,可重新上傳標註。"
                                  "原撤回紀錄保留於 withdrawn.jsonl(稽核軌跡不刪改)。"}), 200

    @flywheel_bp.route("/api/v1/flywheel/stats", methods=["GET"])
    @jwt_required()
    def get_stats():
        """佇列健康度:總筆數 / 孤兒 / 格式錯 / 影像遺失 / 已撤回 / 同意失效 / 被取代 / 可訓練。
        `?source=clinical` 可只看臨床樣本(收案進度以此為準,不含範例/模擬影像)。"""
        src = request.args.get("source")
        _, stats = effective_queue(source=(src or None))
        stats["images_on_disk"] = len([f for f in os.listdir(IMAGES_DIR)
                                       if f.endswith(".jpg")]) if os.path.isdir(IMAGES_DIR) else 0
        stats["quarantined"] = len([f for f in os.listdir(QUARANTINE_DIR)
                                    if f.endswith(".jpg")]) if os.path.isdir(QUARANTINE_DIR) else 0
        return jsonify(stats), 200
except ImportError:
    flywheel_bp = None  # 無 flask 環境(僅跑純函式測試)
