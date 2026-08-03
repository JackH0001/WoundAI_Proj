# -*- coding: utf-8 -*-
"""C0 唯讀主控台：用瀏覽器看收案進度與佇列健康度，不必開 App、不必看 jsonl。

## 為什麼要有

飛輪的狀態目前只有兩個看得到的地方：手機設定頁，和直接讀 `retrain_queue.jsonl`。
但真正需要盯著收案進度的是**不拿手機的人**——臨床資料負責人、法遵窗口。
後端上了 Cloud Run 之後有固定網址，這一頁就是他們唯一需要的入口。

## 刻意的限制

- **唯讀。** 這一版不提供任何會改變資料的操作。審核、撤回、晉升模型都需要更完整的
  身分與稽核設計（見 `docs/cloud_console_ui_spec.md` 的 C1 之後），現在做只會做出
  一個「按了不知道誰按的」的按鈕。
- **不顯示任何 PII。** 雲端本來就沒有——這句寫在這裡是為了防止日後有人「為了方便對照」
  而把姓名同步上來。那會一次拆掉整個架構最重要的合規槓桿。
- **需要登入。** 沿用後端既有的 JWT。頁面本身是靜態的，資料靠瀏覽器帶 token 去打
  `/api/v1/flywheel/stats`——這樣就不必為主控台再開一套權限模型。
"""
from flask import Blueprint, Response

console_bp = Blueprint("console", __name__)

_PAGE = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WoundAI 主控台</title>
<style>
:root{color-scheme:light dark}
body{font-family:system-ui,"Noto Sans TC",sans-serif;margin:0;padding:24px;
     line-height:1.7;max-width:860px;margin-inline:auto}
h1{font-size:22px;font-weight:500;margin:0 0 4px}
.sub{color:#6b6b6b;font-size:13px;margin:0 0 20px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}
.card{background:rgba(128,128,128,.08);border-radius:8px;padding:14px}
.card .k{font-size:13px;color:#6b6b6b;margin:0 0 4px}
.card .v{font-size:24px;font-weight:500;margin:0}
.card .n{font-size:12px;color:#8a8a8a;margin:4px 0 0}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:7px 6px;border-bottom:.5px solid rgba(128,128,128,.3)}
th{font-weight:500;color:#6b6b6b;font-size:13px}
.bad{color:#c0392b}.ok{color:#1d9e75}
input,button{font:inherit;padding:7px 10px;border-radius:6px;
  border:.5px solid rgba(128,128,128,.5);background:transparent;color:inherit}
button{cursor:pointer}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:16px}
.note{font-size:12px;color:#8a8a8a;margin-top:24px}
code{font-family:ui-monospace,monospace;font-size:13px}
</style></head><body>
<h1>WoundAI 飛輪主控台</h1>
<p class="sub">唯讀。不顯示任何個資——雲端本來就只有 WD- 去識別代碼。</p>

<div class="row">
  <input id="u" placeholder="帳號" autocomplete="username" style="width:130px">
  <input id="p" type="password" placeholder="密碼" autocomplete="current-password" style="width:150px">
  <button onclick="login()">登入</button>
  <button onclick="load()">重新整理</button>
  <span id="msg" style="font-size:13px"></span>
</div>

<div class="cards" id="cards"></div>
<div id="detail"></div>

<p class="note">
「臨床」那一欄才是 n=20 收案進度的分母。範例與模擬圖走同一條管線收進來，
但不計入臨床樣本數——訓練時也要排除（範例是難例升級路由的驗收基準，拿去訓練等於考卷當講義）。
</p>

<script>
let tok = "";
const $ = id => document.getElementById(id);
async function login(){
  $("msg").textContent = "登入中…";
  // 密碼從剪貼簿貼進來時很容易帶到尾端空白或換行(從終端機複製尤其常見)。
  // 後端已經會 strip，這裡也先去掉，順便讓下面顯示的長度是使用者真正送出的長度。
  const user = $("u").value.trim(), pass = $("p").value.trim();
  try{
    const r = await fetch("/api/auth/login", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({username:user, password:pass})});
    if(!r.ok){
      // ⚠ 不可把所有失敗都說成「帳密不正確」。
      // 400(格式)、401(帳密)、500(伺服器例外)、503(冷啟動逾時)的處置完全不同，
      // 混成一句會讓人一直重打密碼，而真正的問題在別的地方。
      let detail = "";
      try{ const j = await r.json(); detail = j.error || j.msg || JSON.stringify(j); }
      catch(_){ detail = await r.text().catch(()=>""); }
      const hint = r.status===401 ? "帳號或密碼不符"
                 : r.status===400 ? "送出的格式有誤"
                 : r.status>=500  ? "後端發生例外，請看 Cloud Run 日誌"
                 : "";
      $("msg").innerHTML = `⚠ HTTP ${r.status} ${hint}<br>` +
        `<span style="color:#8a8a8a">後端回應：${detail||"(無內容)"}　|　` +
        `你送出的密碼長度 ${pass.length} 字元</span>`;
      return;
    }
    tok = (await r.json()).access_token || "";
    $("msg").textContent = tok ? "已登入" : "⚠ 回應沒有 token";
    if(tok) load();
  }catch(e){ $("msg").textContent = "⚠ 連不到後端：" + e.message; }
}
function card(k,v,n,cls){
  return `<div class="card"><p class="k">${k}</p>
    <p class="v ${cls||''}">${v}</p>${n?`<p class="n">${n}</p>`:""}</div>`;
}
async function load(){
  if(!tok){ $("msg").textContent = "請先登入"; return; }
  const r = await fetch("/api/v1/flywheel/stats", {headers:{Authorization:"Bearer "+tok}});
  if(!r.ok){ $("msg").textContent = "讀取失敗 " + r.status; return; }
  const s = await r.json();
  const by = s.by_source || {};
  const clinical = by.clinical || 0;
  const broken = (s.orphan_no_image||0) + (s.image_file_missing||0) + (s.malformed||0);
  $("cards").innerHTML =
    card("臨床收案進度", clinical + " <span style='font-size:14px;color:#8a8a8a'>/ 20</span>",
         Math.round(clinical/20*100) + "%") +
    card("佇列可訓練", s.trainable ?? "—", "共 " + (s.total ?? "—") + " 筆") +
    card("資料鏈異常", broken, "孤兒 " + (s.orphan_no_image||0) +
         "・影像遺失 " + (s.image_file_missing||0) + "・格式錯 " + (s.malformed||0),
         broken ? "bad" : "ok") +
    card("影像", s.images_on_disk ?? "—", "隔離 " + (s.quarantined||0));
  const rows = [
    ["臨床 clinical", by.clinical||0, "唯一可作臨床證據者"],
    ["範例 sample", by.sample||0, "驗收基準，訓練時排除"],
    ["模擬圖 phantom", by.phantom||0, "印刷色塊，無傷口材質"],
    ["外部 external", by.external||0, "公開資料集"],
    ["已撤回", s.withdrawn||0, "同意撤回即排除"],
    ["被取代", s.superseded||0, "同影像取最新的醫師修訂版"],
    ["同意失效", s.consent_invalid||0, "三同意任一為否"],
  ];
  $("detail").innerHTML = "<table><tr><th>項目</th><th>筆數</th><th>說明</th></tr>" +
    rows.map(r=>`<tr><td>${r[0]}</td><td>${r[1]}</td><td style="color:#8a8a8a">${r[2]}</td></tr>`).join("") +
    "</table><p class='note'>儲存後端：<code>" + (s.store||"—") + "</code></p>";
  $("msg").textContent = "更新於 " + new Date().toLocaleTimeString();
}
</script></body></html>"""


@console_bp.route("/console", methods=["GET"])
def console_page():
    # 頁面本身不含資料，所以不必驗證；資料一律要帶 JWT 才拿得到。
    # 這樣就不用為主控台再造一套 session/cookie，也少一個可以被繞過的入口。
    return Response(_PAGE, mimetype="text/html; charset=utf-8")
