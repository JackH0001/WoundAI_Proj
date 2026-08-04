# -*- coding: utf-8 -*-
"""C0 唯讀主控台：用瀏覽器看收案進度與佇列健康度，不必開 App、不必看 jsonl。

## 為什麼要有

飛輪的狀態目前只有兩個看得到的地方：手機設定頁，和直接讀 `retrain_queue.jsonl`。
但真正需要盯著收案進度的是**不拿手機的人**——臨床資料負責人、法遵窗口。
後端上了 Cloud Run 之後有固定網址，這一頁就是他們唯一需要的入口。

## 分區依角色展開

登入後依 JWT 角色決定看得到什麼（`perms` 由後端在登入回應中給出）：

| 區塊 | 需要權限 | 誰 |
|---|---|---|
| 飛輪佇列健康度 | `flywheel.stats` | 醫師／護理師／工程師／管理者 |
| 系統狀態（降級偵測、儲存後端） | `audit.read` | 工程師／管理者 |
| 稽核軌跡與鏈完整性 | `audit.read` | 工程師／管理者 |
| 帳號管理 | `user.manage` | **僅管理者** |

⚠ **前端隱藏不是存取控制。** 依 `perms` 隱藏區塊是為了讓人看得懂自己能做什麼；
真正的拒絕在每個端點的伺服器端檢查（`docs/rbac_design.md` §5）。
把 JS 改掉照樣按得下去，但後端會回 403 且**留下稽核紀錄**。

## 刻意的限制

- **不提供刪除帳號，只能停用。** 稽核軌跡引用了那些識別碼，刪掉會讓歷史紀錄
  指向一個不存在的人——那正是稽核最不能發生的事。
- **不在此做標註審核與模型晉升**（見 `docs/cloud_console_ui_spec.md` C1 之後），
  那需要影像檢視，會把這一頁從「無 PII」變成「有影像」，是完全不同的資安等級。
- **密碼只顯示一次。** 後端只存 PBKDF2 雜湊，重設後取不回——刻意如此。
- **不顯示任何 PII。** 雲端本來就沒有——這句寫在這裡是為了防止日後有人「為了方便對照」
  而把姓名同步上來。那會一次拆掉整個架構最重要的合規槓桿。
- **需要登入。** 沿用後端既有的 JWT。頁面本身是靜態的（不含任何資料），
  資料一律靠瀏覽器帶 token 去打 API——不必為主控台再開一套權限模型，
  也少一個可以被繞過的入口。
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
.hide{display:none}
h2{font-size:17px;font-weight:500;margin:26px 0 8px}
.pw{font-family:ui-monospace,monospace;background:rgba(255,200,0,.18);padding:2px 6px;border-radius:4px}
.off{opacity:.5}
code{font-family:ui-monospace,monospace;font-size:13px}
</style></head><body>
<h1>WoundAI 主控台</h1>
<p class="sub">不顯示任何個資——雲端本來就只有 WD- 去識別代碼。
登入後依你的角色展開可用區塊。</p>

<div class="row">
  <input id="u" placeholder="帳號" autocomplete="username" style="width:130px">
  <input id="p" type="password" placeholder="密碼" autocomplete="current-password" style="width:150px">
  <button onclick="login()">登入</button>
  <button onclick="load()">重新整理</button>
  <span id="msg" style="font-size:13px"></span>
</div>

<div class="cards" id="cards"></div>
<div id="detail"></div>

<div id="sysbox" class="hide">
  <h2>系統狀態</h2>
  <div id="sys"></div>
</div>

<div id="auditbox" class="hide">
  <h2>稽核軌跡</h2>
  <div class="row">
    <select id="afilter"><option value="">全部動作</option></select>
    <button onclick="loadAudit()">查詢</button>
    <span id="chain" style="font-size:13px"></span>
  </div>
  <div id="audit"></div>
</div>

<div id="userbox" class="hide">
  <h2>帳號管理</h2>
  <div class="row">
    <input id="nu" placeholder="編號（如 ns05）" style="width:150px">
    <select id="nr">
      <option value="physician">醫師</option>
      <option value="nurse">護理師</option>
      <option value="assistant">助理</option>
      <option value="engineer">工程師</option>
      <option value="admin">管理者</option>
    </select>
    <input id="nn" placeholder="顯示名稱（勿填真實姓名）" style="width:190px">
    <button onclick="createUser()">新增（自動產生密碼）</button>
  </div>
  <p class="note" style="margin-top:0">
    ⚠ 顯示名稱請用「護理師 05」這類編號，<b>不要填真實姓名</b>——
    識別碼會永久留在稽核軌跡（append-only，日後進 WORM 桶就再也拿不掉）。
    帳號↔真人的對照表請留在院方保管。
  </p>
  <div id="users"></div>
</div>

<p class="note">
「臨床」那一欄才是 n=20 收案進度的分母。範例與模擬圖走同一條管線收進來，
但不計入臨床樣本數——訓練時也要排除（範例是難例升級路由的驗收基準，拿去訓練等於考卷當講義）。
</p>

<script>
let tok = "", perms = [], meRole = "";
const $ = id => document.getElementById(id);
// 凡是要放進 innerHTML 的外部字串一律經過 esc()。
// 這裡的「外部」包含後端錯誤訊息與管理者自己填的顯示名稱——
// 後者是自我 XSS，危害有限，但這是醫療系統，一行的成本不值得為它做例外判斷。
const esc = t => String(t==null?"":t).replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
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
        `<span style="color:#8a8a8a">後端回應：${esc(detail)||"(無內容)"}　|　` +
        `你送出的密碼長度 ${pass.length} 字元</span>`;
      return;
    }
    const j = await r.json();
    tok = j.access_token || "";
    // perms 由後端算好回傳(伺服器端的權限矩陣是唯一真相)，前端不自己推導角色能做什麼——
    // 兩邊各算一次遲早會不一致，而不一致的那一刻通常是前端顯示得比後端寬鬆。
    perms = j.perms || []; meRole = j.role || "";
    $("msg").textContent = tok
      ? `已登入：${esc(j.display_name||j.user||"")}（${esc(j.role_zh||meRole)}）` : "⚠ 回應沒有 token";
    if(tok){ applyPerms(); load(); }
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

/* ───────── 以下為依角色展開的管理區 ─────────
   ⚠ show()/hide() 只是可讀性設計，不是存取控制。
   每個 API 端點都在伺服器端獨立檢查權限（fail-closed）；
   改前端 JS 打得出請求，但會拿到 403 並留下稽核紀錄。            */
const show = (id,on) => $(id).classList.toggle("hide", !on);

async function api(path, opt){
  const o = Object.assign({headers:{}}, opt||{});
  o.headers = Object.assign({Authorization:"Bearer "+tok}, o.headers||{});
  if(o.body){ o.headers["Content-Type"]="application/json"; o.method = o.method||"POST"; }
  const r = await fetch(path, o);
  let j = null; try{ j = await r.json(); }catch(_){}
  if(!r.ok) throw new Error((j && (j.error||j.msg)) || ("HTTP "+r.status));
  return j;
}

function applyPerms(){
  show("sysbox",   perms.includes("audit.read"));
  show("auditbox", perms.includes("audit.read"));
  show("userbox",  perms.includes("user.manage"));
  if(perms.includes("audit.read")){ loadSys(); loadAudit(); }
  if(perms.includes("user.manage")) loadUsers();
}

/* ── 系統狀態 ── */
async function loadSys(){
  // /api/health 不需 token（Cloud Run 探針要用），但這裡仍只在有 audit.read 時顯示——
  // 降級原因會透露內部模組名稱，沒必要讓臨床角色看到。
  const r = await fetch("/api/health"); const h = await r.json().catch(()=>({}));
  const sv = h.services || {};
  const ok = v => v ? "<span class='ok'>✓</span>" : "<span class='bad'>✗</span>";
  // ⚠ 正常值是 "healthy"（不是 "ok"）。寫錯會讓正常服務永遠顯示紅字——
  // 狼來了幾次之後，真的降級時就沒人會看了。
  const deg = (h.status||"") !== "healthy";
  $("sys").innerHTML =
    `<table>
      <tr><th>項目</th><th>狀態</th><th>影響</th></tr>
      <tr><td>整體</td><td class="${deg?'bad':'ok'}">${esc(h.status||"?")}</td>
          <td style="color:#8a8a8a">${esc(h.degraded_reason||"正常")}</td></tr>
      <tr><td>ONNX Runtime</td><td>${ok(sv.onnxruntime)}</td>
          <td style="color:#8a8a8a">缺了會退回 HSV 色彩分割，面積會錯但仍回 200——最危險的失敗模式</td></tr>
      <tr><td>分割模型</td><td>${ok(sv.segmentation_model)}</td>
          <td style="color:#8a8a8a">缺了無法產生遮罩</td></tr>
      <tr><td>classify 模組</td><td>${ok(sv.classify_modules)}</td>
          <td style="color:#8a8a8a">缺了 /api/v1/classify 直接 503</td></tr>
      <tr><td>儲存後端</td><td colspan="2" class="${/^gcs:/.test(h.store||"")?"":"bad"}">
          <code>${esc(h.store||"—")}</code>
          ${/^gcs:/.test(h.store||"") ? "" :
            "<br><span style='font-size:12px'>不是 GCS：Cloud Run 的容器檔案系統是暫時的，"+
            "佇列與影像會在實例回收時**無聲消失**</span>"}</td></tr>
      <tr><td>稽核 WORM 桶</td>
          <td>${/WORM/.test(h.store||"") ? "<span class='ok'>✓</span>" : "<span class='bad'>✗</span>"}</td>
          <td style="color:#8a8a8a">未接上時稽核紀錄寫在主桶，刪得掉——雜湊鏈擋不住整份重寫</td></tr>
    </table>
    <p class="note">⚠ 若「整體」是 degraded 仍能量測，但<b>面積數值不可作臨床用途</b>。
    這正是把降級狀態拉到檯面上的理由——先前它只寫在日誌裡，前端照樣顯示一個看起來正常的數字。</p>`;
}

/* ── 稽核軌跡 ── */
async function loadAudit(){
  let j; try{ j = await api("/api/v1/audit?limit=100" +
      ($("afilter").value ? "&action="+encodeURIComponent($("afilter").value) : "")); }
  catch(e){ $("audit").innerHTML = "<p class='bad'>讀取失敗："+esc(e.message)+"</p>"; return; }

  $("chain").innerHTML = j.chain_ok
    ? `<span class="ok">✓ 雜湊鏈完整</span>（${j.total} 筆，head <code>${esc((j.chain_head||"").slice(0,12))}…</code>）`
    : `<span class="bad">✗ 雜湊鏈異常 ${j.chain_issues.length} 處</span>——` +
      esc(j.chain_issues.slice(0,3).map(i=>i.kind+"@"+i.seq).join("、"));

  const f = $("afilter");
  if(f.options.length <= 1)
    (j.actions||[]).forEach(a => f.add(new Option(a, a)));

  $("audit").innerHTML = "<table><tr><th>時間</th><th>操作者</th><th>角色</th>" +
    "<th>動作</th><th>對象</th><th>結果</th></tr>" +
    (j.entries||[]).map(e => `<tr>
       <td style="white-space:nowrap">${esc((e.ts||"").replace("T"," ").slice(0,19))}</td>
       <td>${esc(e.actor)}</td><td>${esc(e.role||"—")}</td>
       <td>${esc(e.action)}</td><td><code>${esc(e.code||"—")}</code></td>
       <td style="color:#8a8a8a">${esc(e.result||"")}</td></tr>`).join("") + "</table>" +
    `<p class="note">雜湊鏈能偵測「竄改／刪除／重排」，但<b>擋不住整份重寫</b>——
     所以稽核另寫一份到 WORM 桶（保留 7 年、無法覆寫）。兩者缺一不可。</p>`;
}

/* ── 帳號管理 ── */
let users = [];
async function loadUsers(){
  let j; try{ j = await api("/api/v1/users"); }
  catch(e){ $("users").innerHTML = "<p class='bad'>讀取失敗："+esc(e.message)+"</p>"; return; }
  users = j.users || [];
  const n = users.filter(u=>!u.disabled).length;
  $("users").innerHTML = "<table><tr><th>登入帳號</th><th>角色</th><th>顯示名稱</th>" +
    "<th>建立</th><th>狀態</th><th></th></tr>" +
    users.map(u => `<tr class="${u.disabled?'off':''}">
      <td><code>${esc(u.identity||(u.org+":"+u.user))}</code></td>
      <td>${esc(u.role_zh||u.role)}</td><td>${esc(u.display_name||"")}</td>
      <td style="color:#8a8a8a">${esc((u.created_at||"").slice(0,10))}</td>
      <td class="${u.disabled?'bad':'ok'}">${u.disabled?"已停用":"啟用中"}</td>
      <td style="white-space:nowrap">
        <button onclick="renameUser('${esc(u.user)}')">改名</button>
        <button onclick="toggleUser('${esc(u.user)}')">${u.disabled?"啟用":"停用"}</button>
        <button onclick="resetPw('${esc(u.user)}')">重設密碼</button>
      </td></tr>`).join("") +
    `</table><p class="note">啟用中 ${n} / 共 ${users.length}。
     <b>刪除刻意不提供</b>——稽核軌跡引用這些識別碼，刪掉會讓歷史紀錄指向不存在的人。
     人員離職請停用，識別碼不再配發給別人。</p>`;
}

function banner(html){
  const d = document.createElement("div");
  d.style.cssText = "background:rgba(255,200,0,.15);border-radius:8px;padding:12px;margin:10px 0";
  d.innerHTML = html; $("userbox").insertBefore(d, $("users"));
}

async function createUser(){
  const user = $("nu").value.trim(), role = $("nr").value, name = $("nn").value.trim();
  if(!user){ alert("請填編號"); return; }
  try{
    const j = await api("/api/v1/users",
      {body: JSON.stringify({user, role, display_name:name, generate_password:true})});
    banner(`✓ 已建立 <code>${esc(j.user.identity)}</code>（${esc(j.user.role_zh||role)}）<br>` +
           `密碼：<span class="pw">${esc(j.generated_password)}</span><br>` +
           `<span style="font-size:12px">${esc(j.note||"")}　` +
           `請個別傳給本人，不要用同一封訊息發給所有人——一次外洩就是全部。</span>`);
    $("nu").value = ""; $("nn").value = "";
    loadUsers();
  }catch(e){ alert("建立失敗：" + e.message); }
}

// 改名走瀏覽器是有理由的：瀏覽器一律以 UTF-8 送出請求主體，
// 而 PowerShell 5.1 的 Invoke-RestMethod 不帶 charset 時會用 ISO-8859-1，
// 把中文名整批變成「?」且**不報任何錯**（2026-08-04 十組帳號就是這樣壞的）。
// 這一頁因此是修顯示名稱最不會出事的地方。
async function renameUser(u){
  const rec = users.find(x => x.user === u);
  if(!rec) return;
  const name = prompt("新的顯示名稱（請用編號式，例如「護理師 05」，不要填真實姓名）：",
                      rec.display_name || "");
  if(name === null) return;
  try{
    // 不傳 password → 沿用既有雜湊；不傳 disabled → 沿用目前狀態。
    await api("/api/v1/users", {body: JSON.stringify({user:u, role:rec.role, display_name:name})});
    loadUsers(); if(perms.includes("audit.read")) loadAudit();
  }catch(e){ alert("失敗：" + e.message); }
}

async function toggleUser(u){
  const rec = users.find(x => x.user === u);
  if(!rec) return;
  const to = !rec.disabled;
  if(!confirm((to?"停用":"啟用") + " " + u + "？")) return;
  try{
    // upsert 是整筆覆寫，role 少傳會被後端拒絕；密碼不傳＝不變更。
    await api("/api/v1/users", {body: JSON.stringify({user:u, role:rec.role, disabled:to,
                                                      display_name:rec.display_name||""})});
    loadUsers(); if(perms.includes("audit.read")) loadAudit();
  }catch(e){ alert("失敗：" + e.message); }
}

async function resetPw(u){
  const rec = users.find(x => x.user === u);
  if(!rec) return;
  if(!confirm("重設 " + u + " 的密碼？舊密碼立即失效，新密碼只顯示一次。")) return;
  try{
    const j = await api("/api/v1/users", {body: JSON.stringify({user:u, role:rec.role,
      display_name:rec.display_name||"", disabled:!!rec.disabled, generate_password:true})});
    banner(`✓ <code>${esc(u)}</code> 新密碼：<span class="pw">${esc(j.generated_password)}</span><br>` +
           `<span style="font-size:12px">舊密碼已失效。${esc(j.note||"")}</span>`);
    if(perms.includes("audit.read")) loadAudit();
  }catch(e){ alert("失敗：" + e.message); }
}
</script></body></html>"""


@console_bp.route("/console", methods=["GET"])
def console_page():
    # 頁面本身不含資料，所以不必驗證；資料一律要帶 JWT 才拿得到。
    # 這樣就不用為主控台再造一套 session/cookie，也少一個可以被繞過的入口。
    # ⚠ 用 content_type 而非 mimetype。Flask 會**再補一次** charset 到 mimetype 上，
    # 於是標頭變成 `text/html; charset=utf-8; charset=utf-8`（實測如此）。
    # 瀏覽器容忍，但重複參數在嚴格的解析器（代理、掃描器）上是未定義行為。
    return Response(_PAGE, content_type="text/html; charset=utf-8")
