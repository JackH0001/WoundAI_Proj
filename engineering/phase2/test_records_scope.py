# -*- coding: utf-8 -*-
"""契約測試：**「我的送件」對送不出件的角色不可以是一片空白**。

    python engineering/phase2/test_records_scope.py

## 這個 bug 的形狀

`/api/v1/flywheel/records` 的頁籤開放給 `flywheel.stats`＝
{physician, nurse, engineer, admin}，但**只有醫師送得出訓練標註**
（`annotation.submit` = {physician}）。

於是護理師／工程師／管理者的「我的送件」**結構上永遠是空的**，
而畫面顯示的是「沒有符合條件的送件」——那句話讓人以為是資料或篩選壞了。
2026-08-19 管理者實際回報「送件無法呈現」，去找了一個不存在的故障。

護理師更糟：她沒有 `audit.read`，勾不了「看全部人的」，
所以那一頁對她是**永久空白且無路可走**。

## 這裡守的兩件事

1. 送不出件的人在沒指定 scope 時，若有稽核權限就直接看到全部
   （不要讓人先撞一次空畫面才學會勾那個框）
2. 回應帶 `can_submit` / `role`，讓前端**不必自己從角色推導**——
   前端推導遲早會與伺服器的權限矩陣分岔，而分岔方向通常是前端比較寬鬆

⚠ 這一支不驗前端文案（那在 `api_console.py` 的 JS 裡）。
但後端若不給 `can_submit`，前端就只能猜，所以這裡是那段文案的地基。
"""
import hashlib
import importlib
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FLASK_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "Backend", "Flask"))

FAILED = []
TOTAL = [0]


def check(name, ok, detail=""):
    TOTAL[0] += 1
    print(("PASS  " if ok else "FAIL  ") + name + (("   " + str(detail)) if (detail and not ok) else ""))
    if not ok:
        FAILED.append(name)


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

    tmp = tempfile.mkdtemp(prefix="woundai_scope_")
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

    def hdr(user, role):
        with app.app_context():
            return {"Authorization": "Bearer " + create_access_token(
                identity="default:" + user,
                additional_claims={"role": role, "org": "default", "user": user})}

    H = {r: hdr(u, r) for u, r in
         [("dr01", "physician"), ("ns01", "nurse"),
          ("eng01", "engineer"), ("admin", "admin")]}

    # 醫師送一筆，讓佇列裡有東西可看
    jpg = make_jpeg(b"s1")
    iid = hashlib.sha1(jpg).hexdigest()[:16]
    open(os.path.join(tmp, "images", iid + ".jpg"), "wb").write(jpg)
    r = cli.post("/api/v1/annotation", headers=H["physician"], json={
        "code": "WD-SCOPE1", "gt_polygon": [[10, 10], [200, 10], [200, 200], [10, 200]],
        "exudate": 1, "image_id": iid, "image_w": 640, "image_h": 480, "mm_per_px": 0.5,
        "doctor_verified": True, "deidentified": True, "consent_train": True,
        "route": "cloud", "source": "sample"})
    check("前置：醫師送出一筆標註", r.status_code == 200, r.status_code)

    def get(role, qs=""):
        return cli.get("/api/v1/flywheel/records" + qs, headers=H[role]).get_json() or {}

    print("\n── 1 醫師：預設看自己的，而且看得到 ──")
    j = get("physician")
    check("scope 預設 mine", j.get("scope") == "mine", j.get("scope"))
    check("can_submit = True", j.get("can_submit") is True)
    check("看得到自己那一筆", len(j.get("records", [])) == 1, len(j.get("records", [])))

    print("\n── 2 管理者／工程師：送不出件 → 預設就給全部 ──")
    for role in ("admin", "engineer"):
        j = get(role)
        check("%s can_submit = False" % role, j.get("can_submit") is False, j.get("can_submit"))
        check("%s 預設 scope = all（不必先撞一次空畫面）" % role,
              j.get("scope") == "all", j.get("scope"))
        check("%s 預設就看得到醫師那一筆" % role,
              len(j.get("records", [])) == 1, len(j.get("records", [])))
        check("%s 回應帶 role（前端不必自己推導）" % role, j.get("role") == role, j.get("role"))

    print("\n── 3 明確指定 scope=mine 時仍要尊重 ──")
    # 預設值是體貼，不是覆寫。使用者主動選了 mine 就該給 mine，
    # 否則勾選框會勾不掉——那比一開始就空白更難理解。
    j = get("admin", "?scope=mine")
    check("admin 指定 mine → 真的是 mine", j.get("scope") == "mine", j.get("scope"))
    check("admin 的 mine 是空的（他本來就送不出件）",
          len(j.get("records", [])) == 0, len(j.get("records", [])))

    print("\n── 4 護理師：送不出件，也沒有稽核權限 ──")
    j = get("nurse")
    check("nurse can_submit = False", j.get("can_submit") is False)
    check("nurse may_see_all = False（她不該看到別人的）", j.get("may_see_all") is False)
    check("nurse scope 維持 mine", j.get("scope") == "mine", j.get("scope"))
    check("nurse 清單是空的", len(j.get("records", [])) == 0)
    # 這是**設計如此**，不是 bug：護理師不該看到他人送件。
    # 但畫面必須說清楚「你的角色不會產生送件」，而不是「沒有符合條件」——
    # 那段文案靠 can_submit 驅動，所以這個欄位對她一定要在。
    check("nurse 仍拿得到 can_submit（空畫面的文案靠它）",
          "can_submit" in j)

    print("\n── 5 護理師不得越權看全部 ──")
    j = get("nurse", "?scope=all")
    check("nurse 送 scope=all 也只會拿到 mine", j.get("scope") == "mine", j.get("scope"))
    check("nurse 送 scope=all 仍看不到醫師的紀錄",
          len(j.get("records", [])) == 0, len(j.get("records", [])))

    print("\n── 5b 影像檢視：自己的可以看，他人的要稽核權限 ──")
    # preview.svg 不含像素（看標註形狀），image.jpg 是真照片（看標註對不對得上傷口）。
    # 審閱需要兩者對照；影像開放範圍照最小需要：本人＋audit.read。
    img_url = "/api/v1/flywheel/record/%s/image.jpg" % iid
    r = cli.get(img_url, headers=H["physician"])
    check("送件醫師看得到自己的影像", r.status_code == 200
          and r.content_type.startswith("image/jpeg"),
          "%s %s" % (r.status_code, r.content_type))
    check("回應帶 no-store（影像不可被中間層快取）",
          "no-store" in (r.headers.get("Cache-Control") or ""))
    check("admin 看得到（audit.read）",
          cli.get(img_url, headers=H["admin"]).status_code == 200)
    with app.app_context():
        H_DR2 = {"Authorization": "Bearer " + create_access_token(
            identity="default:dr02",
            additional_claims={"role": "physician", "org": "default", "user": "dr02"})}
    check("**另一位醫師**看不到（非本人送件且無稽核權限）",
          cli.get(img_url, headers=H_DR2).status_code == 403)
    check("nurse 看不到（她有 flywheel.stats 但不是送件人）",
          cli.get(img_url, headers=H["nurse"]).status_code == 403)
    check("查無此件回 404",
          cli.get("/api/v1/flywheel/record/%s/image.jpg" % ("0" * 16),
                  headers=H["admin"]).status_code == 404)
    acts = [a.get("action") for a in fw.read_jsonl(fw.AUDIT)]
    check("成功檢視有留 image_viewed（影像被誰看過是 IRB 會問的事）",
          "image_viewed" in acts)
    check("被拒也有留 image_view_denied", "image_view_denied" in acts)

    # 撤回後：理由必須是「撤回」，不是「查無影像」——稽核要記真正的原因
    rw = cli.post("/api/v1/consent/withdraw", headers=H["physician"],
                  json={"code": "WD-SCOPE1"})
    check("前置：撤回成功", rw.status_code == 200, rw.status_code)
    r = cli.get(img_url, headers=H["admin"])
    check("撤回後回 410 且理由明講撤回", r.status_code == 410
          and "撤回" in ((r.get_json() or {}).get("error") or ""),
          "%s %s" % (r.status_code, r.get_json()))

    print("\n── 5c 疊圖模式：viewBox 必須恰好等於影像 ──")
    # 疊圖時 SVG 與影像等比縮放後要逐點對齊。原本底部留 48px 給文字，
    # 那會讓每個 y 被壓縮 h/(h+48)——遮罩整體上移，圖越小偏得越明顯。
    # **對不準的疊圖比不疊更糟**：審閱者會以為模型畫歪了。
    import re as _re
    p_norm = cli.get("/api/v1/flywheel/record/%s/preview.svg" % iid,
                     headers=H["physician"]).data.decode()
    p_ov = cli.get("/api/v1/flywheel/record/%s/preview.svg?overlay=1" % iid,
                   headers=H["physician"]).data.decode()

    def _vb(s):
        m = _re.search(r'viewBox="0 0 (\d+) (\d+)"', s)
        return (int(m.group(1)), int(m.group(2))) if m else None

    check("一般模式 viewBox 比影像高（底部留文字）",
          _vb(p_norm) and _vb(p_norm)[1] > 480, _vb(p_norm))
    check("疊圖模式 viewBox **恰好** 640×480", _vb(p_ov) == (640, 480), _vb(p_ov))
    check("疊圖模式沒有背景矩形（否則蓋住照片）",
          'fill="#f5f5f5"' not in p_ov and 'fill="#f5f5f5"' in p_norm)
    check("疊圖模式沒有底部文字（會蓋到傷口）",
          "不含任何原始影像像素" not in p_ov and "不含任何原始影像像素" in p_norm)
    check("疊圖模式仍畫出輪廓", "<polygon" in p_ov)
    # ⚠ 疊圖時 SVG 內**不可以有任何 alpha**。外層 div 還有一層 opacity，
    # 兩者相乘——滑桿拉到 100% 實際只有 SVG 自己的 alpha，
    # 使用者的回報是「拉到最高還是不明顯」，而且無從知道為什麼。
    # 一個東西只由一個地方控制：透明度全交給滑桿。
    check("疊圖模式 SVG 內沒有 stroke-opacity（避免兩層 alpha 相乘）",
          "stroke-opacity" not in p_ov, p_ov.count("stroke-opacity"))
    check("一般模式仍保留襯線的 stroke-opacity", "stroke-opacity" in p_norm)
    _ov_w = _re.findall(r'stroke="#00e5ff" stroke-width="([\d.]+)"', p_ov)
    _nm_w = _re.findall(r'stroke="#00e5ff" stroke-width="([\d.]+)"', p_norm)
    # 照片是彩色高頻背景，1px 的青線在傷口紋理上看不見。
    check("疊圖的輪廓線比一般模式粗",
          _ov_w and _nm_w and float(_ov_w[0]) > float(_nm_w[0]) * 2,
          "疊圖 %s vs 一般 %s" % (_ov_w, _nm_w))

    print("\n── 6 前端必須明確送出 scope ──")
    # 後端把「沒有 scope 參數」解讀為「伺服器替你決定」。前端若把它當成 mine，
    # 取消勾選「看全部人的」→ 不送參數 → 後端回 all → 勾選框又被畫回勾起來，
    # **那個框就永遠取消不了**（2026-08-19 admin 實測踩到，是本輪自己引入的）。
    #
    # 這是純前端 JS，Python 測不到行為，只能守原始碼的形狀——
    # 但守得住的是「同一個參數缺席在兩端有兩種意思」這個共同形狀。
    js = open(os.path.join(FLASK_DIR, "api_console.py"), encoding="utf-8").read()
    check("取消勾選時會明確送 scope=mine（不是靠不送參數）",
          'q.set("scope", window._recScopeAll ? "all" : "mine")' in js,
          "找不到明確送出 scope 的程式碼")
    check("不再是「只有 true 才送 scope」的舊寫法",
          'if(window._recScopeAll) q.set("scope", "all");' not in js)
    check("勾選框的 checked 依伺服器回的 scope，而非本地變數",
          '${j.scope==="all"?"checked":""}' in js)

    print("\n── 7 檢視器的面板不可以用 || 挑 ──")
    # 兩個面板都存在於 DOM（只是所屬 section 沒有 .on），所以
    # `$("r_panel") || $("lite_panel")` 永遠選到前者——
    # 2026-08-20 實測：在民眾版按「看影像」，圖開到送件審閱頁籤去了。
    check("showOverlay 由呼叫端指定面板（不自己挑）",
          "async function showOverlay(box," in js)
    check("沒有殘留 `$(\"r_panel\") || $(\"lite_panel\")` 的挑法",
          '$("r_panel") || $("lite_panel")' not in js)
    check("民眾版走 lite_panel", 'showOverlay($("lite_panel")' in js)
    check("送件審閱走 r_panel", 'showOverlay($("r_panel")' in js)
    check("疊圖請求帶 overlay=1（否則 viewBox 對不準）",
          js.count("preview.svg?overlay=1") >= 2)
    check("影像與 SVG 分別請求，影像失敗時仍顯示標註",
          "allSettled" in js and "okImg" in js)

    print("\n── 8 疊圖檢視器不可用 inline 事件屬性 ──")
    # 這顆關閉鈕修了三次才抓到真因：
    #
    #     Uncaught TypeError: URL.revokeObjectURL is not a function
    #
    # inline 事件處理器的**作用域鏈裡有 document**，而 `document.URL` 是文件網址
    # **字串**——handler 裡的裸 `URL` 解析到那個字串而不是 `window.URL`。
    # 第一行就拋錯，後面的清空永遠跑不到；而事件處理器裡的例外不會傳回
    # `.click()` 的呼叫端，所以外面看到的是「按了沒反應、也沒有錯誤」。
    #
    # 「看標註」的關閉鈕沒事，只因為它剛好不碰 `URL`——那是運氣不是設計。
    ov = js[js.find("async function showOverlay"):]
    ov = ov[:ov.find("\nasync function showImage")] if "\nasync function showImage" in ov else ov[:4000]
    ov_code = _re.sub(r"//[^\n]*", "", ov)
    check("showOverlay 內沒有任何 inline on* 屬性",
          not _re.search(r'\son(click|input|change)=', ov_code),
          _re.findall(r'\son(?:click|input|change)="[^"]{0,40}', ov_code)[:3])
    check("關閉與滑桿改用 addEventListener（作用域是函式，不是 DOM 樹）",
          ov_code.count("addEventListener") >= 2, ov_code.count("addEventListener"))
    check("revokeObjectURL 明寫 window.（不依賴裸 URL 解析到什麼）",
          "window.URL.revokeObjectURL" in ov_code and
          not _re.search(r'[^.\w]URL\.revokeObjectURL', ov_code))

    print("\n── 8b 關閉鈕直接清面板 ──")
    # 註解裡會引用壞寫法當反例，所以剝完註解再數——
    # 「檢查器分不出程式碼與談論程式碼的文字」已經踩過四次了。
    js_code = _re.sub(r"//[^\n]*", "", _re.sub(r"/\*(?:.|\n)*?\*/", "", js))
    check("showOverlay 的關閉直接清面板（閉包持有 box，不必再查 DOM）",
          'box.innerHTML = ""' in ov_code)
    check("疊圖區塊沒有殘留 closest('.banner')（含失敗分支）",
          "closest('.banner').remove()" not in js_code,
          "還有 %d 處" % js_code.count("closest('.banner').remove()"))
    # 滑桿要看得到目前數值，否則「有沒有真的到 100」只能用猜的。
    check("滑桿旁顯示目前百分比", 'lbl.textContent = slider.value + "%"' in js)
    check("滑桿範圍 0–100", 'min="0" max="100"' in js)

    print("\n%d 項檢查，%d 項失敗" % (TOTAL[0], len(FAILED)))
    if FAILED:
        print("失敗：")
        for x in FAILED:
            print("  · " + x)
        return 1
    print("全部通過：送不出件的角色預設看得到東西，且範圍限制沒有被放寬。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
