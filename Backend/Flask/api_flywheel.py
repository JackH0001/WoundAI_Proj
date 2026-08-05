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
# 誤送排除。**與 withdrawn.jsonl 分開存，這不是潔癖。**
#
# 「病患撤回訓練同意」與「操作者送錯了」在 IRB 眼中是完全不同的兩件事：
# 前者是受試者行使權利，必須出現在同意管理的統計裡；後者是一次操作失誤。
# 用 withdraw 去標記一個誤送，IRB 報告上就會出現一筆**根本沒發生過的撤回**——
# 而那份報告的可信度正是整個飛輪存在的理由。
#
# 這與先前「拒絕理由要是『已撤回同意』而不是『查無影像』」是同一條原則：
# 稽核紀錄的價值在於它說的是真正發生的事。
RETRACTED = os.path.join(FLYWHEEL_DIR, "retracted.jsonl")
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


def _store():
    """目前的儲存實作(本機檔案或雲端物件儲存)。見 store.py。"""
    import store as _st
    return _st.get_store(FLYWHEEL_DIR)


def _key(path: str) -> str:
    """把呼叫端傳的路徑換算成儲存層的鍵。

    這樣抽象化就不必改動任何呼叫端——它們照樣傳 QUEUE / WITHDRAWN / AUDIT 這些路徑常數，
    只是底下換成了可插拔的實作。

    ⚠ **飛輪目錄以外的絕對路徑原樣保留**，不可退化成檔名。
    測試與匯出腳本會傳入暫存目錄的絕對路徑（`effective_queue(queue_path=...)` 等），
    換算成相對鍵之後會被解析到 `FLYWHEEL_DIR` 底下——讀到的是產線資料而不是測試資料，
    測試會以「找不到剛寫入的紀錄」這種難以歸因的方式失敗（實際發生過 13 條）。
    `LocalStore` 收到絕對路徑就直接用；雲端模式在產線只會拿到相對鍵，不受影響。
    """
    p = os.path.abspath(path)
    root = os.path.abspath(FLYWHEEL_DIR)
    if p == root or p.startswith(root + os.sep):
        return os.path.relpath(p, root).replace(os.sep, "/")
    return p


def append_jsonl(path: str, rec: dict):
    _store().append_line(_key(path), json.dumps(rec, ensure_ascii=False))


def read_jsonl(path: str, with_bad: bool = False):
    """回紀錄清單(只收 dict)。壞行不靜默消失:with_bad=True 時一併回 bad 計數,
    讓統計把它算進 malformed(否則 append-only 稽核軌跡的破損無從察覺,
    且一行是合法 JSON 但非物件時 effective_queue 會 AttributeError)。"""
    out, bad = [], 0
    for line in _store().read_lines(_key(path)):
        line = line.strip()
        if not line: continue
        try:
            rec = json.loads(line)
        except Exception:
            bad += 1; continue
        if isinstance(rec, dict): out.append(rec)
        else: bad += 1
    return (out, bad) if with_bad else out


# ── 稽核軌跡的雜湊鏈 ──────────────────────────────────────────────────
#
# 「append-only」原本只是一個**約定**：大家說好不刪不改。約定擋不住手滑，也無法向
# IRB 稽核員證明「這份紀錄從沒被動過」——他只能選擇相信你。
#
# 雜湊鏈把它變成可驗證的事實：每一筆都帶著前一筆的雜湊，改動任何一筆、刪掉任何一筆、
# 或調換順序，後面每一筆的鏈結都會對不上，而且**指得出第一個斷點在哪**。
#
# ⚠ 誠實邊界:雜湊鏈能**偵測**竄改,不能**阻止**。有寫入權限的人仍可整條重算。
# 要做到不可竄改需要物件儲存的保留政策(WORM),見 harden_bucket.ps1——
# 那是另一個層次的控制,兩者互補而非替代。
# role/org 也進雜湊鏈:稽核要回答的是「**誰、以什麼身分**做了什麼」。
# 角色只查當下的帳號設定是不夠的——使用者的角色日後可能變更,
# 而那不該讓歷史紀錄的意義跟著改變。
AUDIT_CHAIN_FIELDS = ("seq", "ts", "actor", "role", "org", "action", "code", "result", "prev")


def _audit_hash(rec: dict) -> str:
    """對紀錄的正規化形式取雜湊。欄位順序固定,不含 hash 自身。"""
    payload = json.dumps({k: rec.get(k) for k in AUDIT_CHAIN_FIELDS},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def audit(actor: str, action: str, code: str, result: str,
          role: str = None, org: str = None):
    prev_recs = read_jsonl(AUDIT)
    last = prev_recs[-1] if prev_recs else None
    # actor 是 `<org>:<user>`；org 沒另外傳就從中拆出來，讓舊呼叫端不必全部改。
    if org is None and isinstance(actor, str) and ":" in actor:
        org = actor.split(":", 1)[0]
    rec = {
        "seq": (last.get("seq", len(prev_recs) - 1) + 1) if last else 0,
        "ts": utc_now(), "actor": actor, "role": role, "org": org,
        "action": action, "code": code, "result": result,
        # 鏈首用固定字串,讓「第一筆」與「前面被刪光了」可以區分開來
        "prev": (last or {}).get("hash") or "GENESIS",
    }
    rec["hash"] = _audit_hash(rec)
    append_jsonl(AUDIT, rec)


def verify_audit_chain(audit_path=None, recs=None):
    """驗證稽核軌跡的完整性。回 (ok, 問題清單, 統計)。

    `recs` 可傳入已讀好的紀錄以避免重複讀取——GCS 後端下每次 read 都要列舉
    整個前綴的物件，主控台同時要「列表」與「驗鏈」時讀兩遍會明顯變慢。

    分開回報三種異常,因為處置完全不同:
      - `hash_mismatch`  紀錄內容被改過
      - `broken_link`    前一筆被刪除或順序被調換
      - `fork`           兩筆指向同一個前驅(多實例並行寫入,或有人補塞紀錄)
    """
    recs = read_jsonl(audit_path or AUDIT) if recs is None else recs
    issues, seen_prev = [], {}
    prev_hash = "GENESIS"
    for i, r in enumerate(recs):
        # 舊格式(雜湊鏈導入前)的紀錄沒有 hash 欄位,跳過但要計數——
        # 靜默忽略會讓「整條鏈其實沒在驗」看起來像通過
        if "hash" not in r:
            issues.append({"index": i, "kind": "legacy_no_hash",
                           "detail": "雜湊鏈導入前的舊紀錄,無法驗證", "ts": r.get("ts")})
            prev_hash = "GENESIS"   # 舊紀錄之後重新起鏈
            continue
        if _audit_hash(r) != r.get("hash"):
            issues.append({"index": i, "kind": "hash_mismatch",
                           "detail": "紀錄內容與其雜湊不符(內容被改過)", "ts": r.get("ts")})
        if r.get("prev") != prev_hash:
            issues.append({"index": i, "kind": "broken_link",
                           "detail": "prev 指向 %s,但前一筆的 hash 是 %s(前一筆被刪或順序被調換)"
                                     % (str(r.get("prev"))[:12], str(prev_hash)[:12]),
                           "ts": r.get("ts")})
        p = r.get("prev")
        if p in seen_prev and p != "GENESIS":
            issues.append({"index": i, "kind": "fork",
                           "detail": "與第 %d 筆指向同一個前驅(並行寫入或被補塞)" % seen_prev[p],
                           "ts": r.get("ts")})
        seen_prev[p] = i
        prev_hash = r.get("hash")
    stats = {"total": len(recs), "issues": len(issues),
             "head": prev_hash if recs else "GENESIS",
             "kinds": {k: sum(1 for x in issues if x["kind"] == k)
                       for k in {x["kind"] for x in issues}}}
    return (len(issues) == 0, issues, stats)


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
    return _store().exists(_key(os.path.join(quarantine_dir or QUARANTINE_DIR, f"{image_id}.jpg")))


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
    tagged = classify_queue(recs, images_dir, wd_codes, wd_imgs, keep=keep)

    stats = {"total": len(recs) + bad, "malformed": bad}
    for k in RECORD_STATUS:
        stats.setdefault(k, 0)
    for _, st in tagged:
        stats[st] = stats.get(st, 0) + 1
    out = [r for r, st in tagged if st == "trainable"]
    stats["trainable"] = len(out)
    # 分項:臨床樣本數不可被範例/模擬影像灌水(送件數字誠實與否的關鍵)
    stats["by_source"] = {s: sum(1 for r in out if rec_source(r) == s) for s in SOURCES}
    return out, stats


# 誤送排除的理由分類。**刻意不含「撤回同意」**——那要走 /api/v1/consent/withdraw。
RETRACT_REASONS = {
    "mis_submitted": "誤送（不該送出這一筆）",
    "wrong_source":  "來源標錯（範例／模擬圖被當成臨床）",
    "poor_quality":  "影像不可用（模糊、對焦錯、標記不完整）",
    "duplicate":     "重複送出",
    "other":         "其他（請填 note）",
}


def retracted_images(retracted_path=None):
    """目前處於「已排除」狀態的 image_id 集合。

    重播整份 jsonl：`retract` 加入、`unretract` 移除。與 withdrawn 一樣**不刪除歷史紀錄**——
    「排除又還原」本身就是稽核要看的東西，改寫檔案會讓那段過程消失。
    """
    out = set()
    for r in read_jsonl(retracted_path or RETRACTED):
        iid = r.get("image_id")
        if not iid:
            continue
        if r.get("action") == "unretract":
            out.discard(iid)
        else:
            out.add(iid)
    return out


# 逐筆狀態。**effective_queue 與 /flywheel/records 共用這一份判斷**——
# 兩邊各寫一次的話，「統計說可訓練、清單說被排除」這種矛盾遲早出現，
# 而那會讓人不知道該相信哪一個數字。
def classify_queue(recs, images_dir=None, wd_codes=None, wd_imgs=None,
                   rt_imgs=None, keep=None):
    """回 [(rec, status)]。status 見 RECORD_STATUS。"""
    images_dir = images_dir or IMAGES_DIR
    if wd_codes is None or wd_imgs is None:
        wd_codes, wd_imgs = withdrawn_keys()
    if rt_imgs is None:
        rt_imgs = retracted_images()

    prelim, best = [], {}
    for i, r in enumerate(recs):
        iid = r.get("image_id")
        if not iid:
            st = "orphan_no_image"
        elif not ID_RE.match(str(iid)) or not r.get("image_w") or not r.get("image_h") \
                or len(r.get("gt_polygon") or []) < 3 or not CODE_RE.match(str(r.get("code") or "")):
            st = "malformed"
        # 撤回同意排在最前：它是受試者的權利，任何其他理由都不該蓋過它的可見性。
        elif r.get("code") in wd_codes or iid in wd_imgs:
            st = "withdrawn"
        elif iid in rt_imgs:
            st = "retracted"
        elif not (r.get("doctor_verified") and r.get("deidentified") and r.get("consent_train")):
            st = "consent_invalid"
        elif not _store().exists(_key(os.path.join(images_dir, str(iid) + ".jpg"))):
            st = "image_file_missing"
        elif keep is not None and rec_source(r) not in keep:
            st = "other_source"
        else:
            st = "candidate"
        prelim.append(st)
        if st == "candidate":
            # received_at 遞增(UTC) → 後到者為醫師最新修訂。
            # 用 `or ""` 而非 get 預設值:顯式 null 會變字串 "None",而 "None" > "2026-..."
            #（字典序）會讓沒有時間戳的那筆永遠勝出。
            ra = r.get("received_at") or ""
            cur = best.get(iid)
            if cur is None or ra >= cur[0]:
                best[iid] = (ra, i)
    winners = {i for _, i in best.values()}
    return [(r, ("trainable" if i in winners else "superseded") if st == "candidate" else st)
            for i, (r, st) in enumerate(zip(recs, prelim))]


RECORD_STATUS = {
    "trainable":       "可訓練",
    "superseded":      "被較新的修訂版取代",
    "withdrawn":       "已撤回訓練同意",
    "retracted":       "已標記排除（誤送等）",
    "consent_invalid": "三同意未齊",
    "image_file_missing": "後端查無影像",
    "malformed":       "欄位或格式不合",
    "orphan_no_image": "孤兒 GT（無 image_id）",
    "other_source":    "來源不在篩選範圍",
}


def poly_area_cm2(poly, mm_per_px):
    """多邊形面積（cm²）。算不出來回 None——**不要回 0**：
    0 看起來像「量到了但很小」，None 才看得出是「沒有這個數字」。"""
    try:
        pts = [(float(x), float(y)) for x, y in (poly or [])]
        if len(pts) < 3 or not mm_per_px:
            return None
        a = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            a += x1 * y2 - x2 * y1
        px2 = abs(a) / 2.0
        return round(px2 * (float(mm_per_px) ** 2) / 100.0, 2)
    except Exception:
        return None


def quarantine_image(image_id, images_dir=None, quarantine_dir=None):
    """撤回同意 → 影像移出 images/,不再進任何資料集。保留於 quarantine/ 供稽核,
    實體銷毀另走資料刪除程序(需留紀錄)。回 True 表示有搬動。"""
    if not image_id or not ID_RE.match(str(image_id)): return False
    src = os.path.join(images_dir or IMAGES_DIR, f"{image_id}.jpg")
    dst = os.path.join(quarantine_dir or QUARANTINE_DIR, f"{image_id}.jpg")
    return _store().move(_key(src), _key(dst))


# ---- Flask Blueprint(import flask 失敗時不影響純函式測試) ----
try:
    from flask import Blueprint, request, jsonify
    from flask_jwt_extended import jwt_required, get_jwt_identity, get_jwt
    flywheel_bp = Blueprint("flywheel", __name__)

    def _who():
        """(identity, role, org)。JWT 裡沒有 role 的舊 token 一律視為無權限。"""
        c = get_jwt() or {}
        return (get_jwt_identity() or "unknown", c.get("role"), c.get("org"))

    def _can(role, perm):
        try:
            import auth_users
            return auth_users.can(role, perm)
        except Exception:
            # 權限模組載不進來時 **fail-closed**。放行才是危險的那一邊——
            # 一個載不到權限表的服務不該假設「大家都可以」。
            return False

    @flywheel_bp.route("/api/v1/annotation", methods=["POST"])
    @jwt_required()
    def post_annotation():
        d = request.get_json(silent=True) or {}
        actor, role, org = _who()

        # ⚠ **doctor_verified 由伺服器依角色決定,不採信客戶端送來的值。**
        #
        #   effective = client_says AND (role 具 gt.verify 權限)
        #
        # 客戶端說 false 時我們相信它(那是保守方向);說 true 時必須由角色背書。
        # 只有 physician 具 gt.verify——護理師、助理、工程師按下「完成修邊」時
        # 紀錄照樣更新面積與輪廓,只是不會產生「醫師已驗證」的背書。
        #
        # 這一段刻意放在 validate_annotation **之前**:讓驗證看到的是修正後的值,
        # 否則非醫師送 true 會通過驗證、之後才被降級,錯誤訊息會變得莫名其妙。
        if d.get("doctor_verified") and not _can(role, "gt.verify"):
            audit(actor, "annotation_rejected", d.get("code", "?"),
                  f"角色 {role} 不具醫師確認權限,doctor_verified 被伺服器否決", role, org)
            return jsonify({"error": "權限不足",
                            "issues": ["只有醫師（physician）可以送出經確認的訓練標註。"
                                       f"目前登入角色為 {role}。"
                                       "護理師/助理仍可量測與存入病歷,但 GT 的背書須由醫師完成。"]}), 403
        if not _can(role, "annotation.submit"):
            audit(actor, "annotation_rejected", d.get("code", "?"),
                  f"角色 {role} 不具送標註權限", role, org)
            return jsonify({"error": "權限不足",
                            "issues": [f"角色 {role} 不得送出訓練標註（僅醫師可）。"]}), 403

        ok, issues = validate_annotation(d)
        if not ok:
            audit(actor, "annotation_rejected", d.get("code", "?"), ";".join(issues), role, org)
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
            audit(actor, "annotation_rejected", d.get("code", "?"), msg, role, org)
            return jsonify({"error": "標註不符上傳規範", "issues": [msg]}), 400

        # 影像必須真的在後端(classify 時已存);沒有像素的 GT 不可訓練 → 直接擋
        if not _store().exists(_key(os.path.join(IMAGES_DIR, image_id + ".jpg"))):
            msg = (f"後端查無影像 {image_id}。可能是:(a) 未先呼叫 /api/v1/classify、"
                   f"(b) 後端曾清空 flywheel/images、(c) 這是舊版 App 產生的紀錄。"
                   f"請重新以後端模式量測一次再送出")
            audit(actor, "annotation_rejected", d.get("code", "?"), msg, role, org)
            return jsonify({"error": "標註不符上傳規範", "issues": [msg]}), 400

        exact, same_img = find_duplicate(QUEUE, image_id, d.get("gt_polygon"))
        # 已撤回的舊紀錄不算重複(否則重新取得同意後永遠補不回來)
        if exact is not None and exact.get("code") not in wd_codes:
            audit(actor, "annotation_duplicate", d.get("code", "?"),
                  f"同影像同遮罩已在佇列({exact.get('code')})", role, org)
            return jsonify({"status": "duplicate_skipped", "code": d.get("code"),
                            "note": "相同影像的相同遮罩已在再訓練佇列,已自動略過(避免重複樣本)"}), 200

        rec = {k: d.get(k) for k in REQUIRED}
        for k in PROVENANCE:
            if d.get(k) is not None: rec[k] = d.get(k)
        rec["source"] = str(d.get("source") or DEFAULT_SOURCE)   # 顯式落盤,免得日後靠預設值猜
        rec["poly_sig"] = poly_sig(d.get("gt_polygon"))
        rec["supersedes"] = [r.get("code") for r in same_img] or None
        rec["actor"] = actor
        rec["role"] = role
        rec["org"] = org        # 現在只有一個值也要寫:事後補欄位=舊紀錄全是 null
        rec["received_at"] = utc_now()
        append_jsonl(QUEUE, rec)
        note = None
        if same_img:
            note = f"同影像已有 {len(same_img)} 筆舊標註,本筆視為醫師修訂版,匯出時只取最新"
            audit(actor, "annotation_revised", rec["code"], note, role, org)
        audit(actor, "annotation_enqueued", rec["code"], "ok", role, org)
        return jsonify({"status": "enqueued", "code": rec["code"], "queue": "retrain",
                        "image_id": image_id, "note": note}), 200


    @flywheel_bp.route("/api/v1/retract", methods=["POST"])
    @jwt_required()
    def post_retract():
        """誤送排除。**與撤回同意是兩件事，端點也分開。**

        撤回同意是受試者行使權利；誤送是操作者的失誤。用 withdraw 去標記一次失誤，
        IRB 報告上就會出現一筆根本沒發生過的撤回——而那份報告的可信度，
        正是整個飛輪存在的理由。

        誰可以排除：
          - 醫師：**只能排除自己送的**（`rec["actor"] == 你`）
          - 管理者：任何一筆

        佇列是 append-only，所以這裡不刪任何東西：寫一筆 `retract` 紀錄，
        影像移進 quarantine/（不再進任何資料集），原本的佇列紀錄原封不動留著。
        「誰在什麼時候以什麼理由排除了哪一筆」本身就是稽核要看的內容。
        """
        d = request.get_json(silent=True) or {}
        actor, role, org = _who()
        is_admin = _can(role, "user.manage")
        if not (is_admin or _can(role, "annotation.submit")):
            return jsonify({"error": "權限不足",
                            "issues": [f"角色 {role} 不得標記排除（僅醫師排除自己的、管理者排除任何一筆）。"]}), 403

        image_id = str(d.get("image_id") or "")
        reason = str(d.get("reason") or "")
        note = (d.get("note") or "").strip()
        issues = []
        if not ID_RE.match(image_id):
            issues.append("image_id 格式不合（應為 16 位小寫十六進位）")
        if reason not in RETRACT_REASONS:
            issues.append("reason 須為 %s 之一" % "／".join(RETRACT_REASONS))
        if reason == "other" and not note:
            issues.append("reason=other 時必須填 note——「其他」而沒有說明，"
                          "等於這筆排除在稽核上無法解釋")
        if issues:
            return jsonify({"error": "參數不合", "issues": issues}), 400

        mine = [r for r in read_jsonl(QUEUE) if r.get("image_id") == image_id]
        if not mine:
            return jsonify({"error": "查無此紀錄", "issues": [f"佇列中沒有 image_id {image_id}"]}), 404
        owners = {r.get("actor") for r in mine}
        if not is_admin and owners != {actor}:
            audit(actor, "retract_denied", mine[0].get("code", "?"),
                  f"嘗試排除他人送出的紀錄（送出者 {sorted(owners)}）", role, org)
            return jsonify({"error": "權限不足",
                            "issues": ["只能排除自己送出的紀錄。這一筆是其他人送的，"
                                       "請聯絡管理者。"]}), 403

        code = mine[-1].get("code")
        append_jsonl(RETRACTED, {
            "image_id": image_id, "code": code, "action": "retract",
            "reason": reason, "reason_zh": RETRACT_REASONS[reason], "note": note,
            "actor": actor, "role": role, "org": org, "ts": utc_now(),
        })
        moved = quarantine_image(image_id)
        audit(actor, "record_retract", code or "?",
              "理由=%s%s；影像隔離=%s；影響 %d 筆"
              % (reason, ("（%s）" % note if note else ""), moved, len(mine)), role, org)
        return jsonify({
            "status": "retracted", "image_id": image_id, "code": code,
            "reason": reason, "reason_zh": RETRACT_REASONS[reason],
            "affected": len(mine), "quarantined": moved,
            "effect": "該影像的所有標註一律排除於訓練集與統計，影像移入 quarantine/。"
                      "佇列為 append-only 稽核軌跡故原紀錄保留（不竄改）。"
                      "**這不是撤回同意**——同意狀態未變更。",
        }), 200

    @flywheel_bp.route("/api/v1/unretract", methods=["POST"])
    @jwt_required()
    def post_unretract():
        """還原誤送排除。**僅管理者。**

        「排除」本身也可能按錯，沒有還原路徑的話那就是死局。
        但不開放給醫師自己還原：能自行排除又自行還原，等於這個標記可以被反覆翻面，
        稽核上就看不出最終狀態是誰決定的。
        """
        d = request.get_json(silent=True) or {}
        actor, role, org = _who()
        if not _can(role, "user.manage"):
            return jsonify({"error": "權限不足", "issues": ["僅管理者可還原排除。"]}), 403
        image_id = str(d.get("image_id") or "")
        if not ID_RE.match(image_id):
            return jsonify({"error": "image_id 格式不合"}), 400
        if image_id not in retracted_images():
            return jsonify({"error": "這一筆並未被排除", "image_id": image_id}), 400
        note = (d.get("note") or "").strip()
        append_jsonl(RETRACTED, {"image_id": image_id, "action": "unretract",
                                 "note": note, "actor": actor, "role": role,
                                 "org": org, "ts": utc_now()})
        back = _store().move(_key(os.path.join(QUARANTINE_DIR, image_id + ".jpg")),
                             _key(os.path.join(IMAGES_DIR, image_id + ".jpg")))
        audit(actor, "record_unretract", image_id, f"還原排除；影像復原={back}；{note}", role, org)
        return jsonify({"status": "unretracted", "image_id": image_id, "restored": back}), 200

    @flywheel_bp.route("/api/v1/flywheel/records", methods=["GET"])
    @jwt_required()
    def get_records():
        """我的送件清單。

        ⚠ **範圍限制在伺服器端，不看客戶端參數。**
        非管理者／工程師一律只看得到自己 `actor` 的紀錄，就算送 `scope=all` 也一樣——
        「前端只顯示自己的」不是隔離，那只是沒有畫出來而已。

        回傳仍是**去識別**的：`WD-` 代碼、遮罩、面積、來源、路由。沒有姓名、沒有影像。
        """
        actor, role, org = _who()
        if not _can(role, "flywheel.stats"):
            return jsonify({"error": "權限不足"}), 403
        may_see_all = _can(role, "audit.read")          # 工程師／管理者
        scope = request.args.get("scope", "mine")
        all_scope = may_see_all and scope == "all"

        recs = read_jsonl(QUEUE)
        tagged = classify_queue(recs)
        rt = retracted_images()
        rt_meta = {}
        for r in read_jsonl(RETRACTED):
            if r.get("action") == "unretract":
                rt_meta.pop(r.get("image_id"), None)
            else:
                rt_meta[r.get("image_id")] = r

        f_status = request.args.get("status")
        out = []
        for r, st in tagged:
            if not all_scope and r.get("actor") != actor:
                continue
            if f_status and st != f_status:
                continue
            meta = rt_meta.get(r.get("image_id")) or {}
            out.append({
                "code": r.get("code"), "image_id": r.get("image_id"),
                "received_at": r.get("received_at"), "source": rec_source(r),
                "route": r.get("route"), "status": st,
                "status_zh": RECORD_STATUS.get(st, st),
                "doctor_verified": bool(r.get("doctor_verified")),
                "area_cm2": poly_area_cm2(r.get("gt_polygon"), r.get("mm_per_px")),
                "actor": r.get("actor"), "role": r.get("role"),
                # 只有自己送的（或管理者）才需要知道能不能排除；直接算好，
                # 免得前端自己推導一套規則然後跟後端對不上。
                "can_retract": (st != "retracted") and
                               (_can(role, "user.manage") or r.get("actor") == actor),
                "retract_reason": meta.get("reason_zh"), "retract_note": meta.get("note"),
                "retracted_by": meta.get("actor"),
            })
        out.reverse()       # 新到舊
        return jsonify({
            "records": out, "scope": "all" if all_scope else "mine",
            "may_see_all": may_see_all, "actor": actor,
            "reasons": RETRACT_REASONS, "statuses": RECORD_STATUS,
        }), 200

    @flywheel_bp.route("/api/v1/consent/withdraw", methods=["POST"])
    @jwt_required()
    def post_withdraw():
        d = request.get_json(silent=True) or {}
        code = d.get("code")
        actor, role, org = _who()
        if not _can(role, "patient.manage"):
            return jsonify({"error": "權限不足",
                            "issues": [f"角色 {role} 不得撤回同意（僅醫師/護理師）。"]}), 403
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
            if _store().move(_key(os.path.join(QUARANTINE_DIR, f"{i}.jpg")),
                             _key(os.path.join(IMAGES_DIR, f"{i}.jpg"))):
                restored.append(i)
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
        _a, _r, _o = _who()
        if not _can(_r, "flywheel.stats"):
            return jsonify({"error": "權限不足",
                            "issues": [f"角色 {_r} 不得查看佇列健康度。"]}), 403
        src = request.args.get("source")
        _, stats = effective_queue(source=(src or None))
        st = _store()
        stats["images_on_disk"] = len([k for k in st.list_keys("images") if k.endswith(".jpg")])
        stats["quarantined"] = len([k for k in st.list_keys("quarantine") if k.endswith(".jpg")])
        stats["store"] = st.describe()
        return jsonify(stats), 200
except ImportError:
    flywheel_bp = None  # 無 flask 環境(僅跑純函式測試)
