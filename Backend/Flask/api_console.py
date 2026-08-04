# -*- coding: utf-8 -*-
"""主控台：用瀏覽器管理後端，不必開 App、不必看 jsonl、不必進 GCP Console。

## 為什麼要有

飛輪的狀態原本只有兩個看得到的地方：手機設定頁，和直接讀 `retrain_queue.jsonl`。
但真正需要盯著收案進度的是**不拿手機的人**——臨床資料負責人、法遵窗口、管理者。
後端上了 Cloud Run 之後有固定網址，這一頁就是他們唯一需要的入口。

## 為什麼拆成側欄頁籤

第一版把四塊內容直接疊在一頁上。收案第一天就看得出問題：稽核軌跡**只會單向變長**，
而它排在最下面，於是每次想看帳號都要先滑過幾百列稽核。更糟的是四塊資料在登入當下
全部一起載入，其中稽核那一支是最慢的——結果是「開個頁看收案數」也要等稽核讀完。

拆頁籤解掉的是這兩件事：**版面**（一次只看一件事）與**載入**（進到那一頁才去要資料）。
它解不掉的是後端的 O(n)，那部分在 `api_users.read_audit` 與 `store.GcsStore.read_lines`
各自處理（鏈驗證改成手動觸發、append-only 增量快取）。

## 分區依角色展開

登入後依 JWT 角色決定看得到哪些頁籤（`perms` 由後端在登入回應中給出）：

| 頁籤 | 需要權限 | 誰 |
|---|---|---|
| 飛輪 Dashboard | `flywheel.stats` | 醫師／護理師／工程師／管理者 |
| 系統狀態 | `audit.read` | 工程師／管理者 |
| 稽核軌跡 | `audit.read` | 工程師／管理者 |
| 帳號管理 | `user.manage` | **僅管理者** |

⚠ **前端隱藏不是存取控制。** 依 `perms` 隱藏頁籤是為了讓人看得懂自己能做什麼；
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
:root{color-scheme:light dark; --line:rgba(128,128,128,.28); --dim:#8a8a8a}
*{box-sizing:border-box}
body{font-family:system-ui,"Noto Sans TC",sans-serif;margin:0;line-height:1.65}
.shell{display:grid;grid-template-columns:196px 1fr;min-height:100vh}

nav{border-right:.5px solid var(--line);padding:18px 12px;position:sticky;top:0;
    height:100vh;overflow:auto}
nav .brand{font-size:15px;font-weight:600;padding:0 10px 14px;letter-spacing:.02em}
nav a{display:block;padding:8px 10px;border-radius:7px;cursor:pointer;
      font-size:14px;color:inherit;text-decoration:none;margin-bottom:2px}
nav a:hover{background:rgba(128,128,128,.12)}
nav a.on{background:rgba(128,128,128,.18);font-weight:500}
nav a.hide{display:none}
nav .who{margin-top:18px;padding:10px;border-top:.5px solid var(--line);
         font-size:12px;color:var(--dim);line-height:1.5}

main{padding:24px 28px;max-width:940px;min-width:0}
h1{font-size:20px;font-weight:500;margin:0 0 2px}
.sub{color:#6b6b6b;font-size:13px;margin:0 0 18px}
section{display:none}
section.on{display:block}

.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:12px;margin-bottom:18px}
.card{background:rgba(128,128,128,.08);border-radius:8px;padding:14px}
.card .k{font-size:13px;color:#6b6b6b;margin:0 0 4px}
.card .v{font-size:24px;font-weight:500;margin:0}
.card .n{font-size:12px;color:var(--dim);margin:4px 0 0}

table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:7px 6px;border-bottom:.5px solid var(--line);
      vertical-align:top}
th{font-weight:500;color:#6b6b6b;font-size:12.5px;white-space:nowrap}
.wrap{overflow-x:auto}
.bad{color:#c0392b}.ok{color:#1d9e75}
.off{opacity:.5}
.nowrap{white-space:nowrap}

input,button,select{font:inherit;font-size:13.5px;padding:6px 9px;border-radius:6px;
  border:.5px solid rgba(128,128,128,.5);background:transparent;color:inherit}
button{cursor:pointer}
button.primary{background:rgba(128,128,128,.16);font-weight:500}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px}
.note{font-size:12px;color:var(--dim);margin-top:18px}
.pw{font-family:ui-monospace,monospace;background:rgba(255,200,0,.18);padding:2px 6px;border-radius:4px}
code{font-family:ui-monospace,monospace;font-size:12.5px;word-break:break-all}
.banner{background:rgba(255,200,0,.15);border-radius:8px;padding:12px;margin:10px 0;font-size:13.5px}
.hide{display:none}

@media (max-width:760px){
  .shell{grid-template-columns:1fr}
  nav{position:static;height:auto;border-right:0;border-bottom:.5px solid var(--line);
      display:flex;gap:4px;overflow-x:auto;padding:10px}
  nav .brand,nav .who{display:none}
  nav a{white-space:nowrap;margin:0}
  main{padding:18px 14px}
}
</style></head><body>

<div class="shell">
<nav>
  <div class="brand">WoundAI 主控台</div>
  <a data-tab="dash" class="hide" onclick="go('dash')">飛輪 Dashboard</a>
  <a data-tab="sys"   class="hide" onclick="go('sys')">系統狀態</a>
  <a data-tab="audit" class="hide" onclick="go('audit')">稽核軌跡</a>
  <a data-tab="users" class="hide" onclick="go('users')">帳號管理</a>
  <div class="who" id="who">未登入</div>
</nav>

<main>

<section id="tab-login" class="on">
  <h1>登入</h1>
  <p class="sub">不顯示任何個資——雲端本來就只有 WD- 去識別代碼。登入後依你的角色展開可用頁籤。</p>
  <div class="row">
    <input id="u" placeholder="帳號" autocomplete="username" style="width:150px">
    <input id="p" type="password" placeholder="密碼" autocomplete="current-password" style="width:170px">
    <button class="primary" onclick="login()">登入</button>
  </div>
  <div id="msg" style="font-size:13px"></div>
</section>

<section id="tab-dash">
  <h1>飛輪 Dashboard</h1>
  <p class="sub">收案進度與佇列健康度。<button onclick="loadDash()">重新整理</button></p>
  <div class="cards" id="cards"></div>
  <div id="detail"></div>
  <p class="note">
  「臨床」那一欄才是 n=20 收案進度的分母。範例與模擬圖走同一條管線收進來，
  但不計入臨床樣本數——訓練時也要排除（範例是難例升級路由的驗收基準，拿去訓練等於考卷當講義）。
  </p>
</section>

<section id="tab-sys">
  <h1>系統狀態</h1>
  <p class="sub">降級偵測與儲存後端。<button onclick="loadSys()">重新整理</button></p>
  <div id="sys"></div>
</section>

<section id="tab-audit">
  <h1>稽核軌跡</h1>
  <p class="sub">誰、以什麼身分、對哪個 WD-代碼、做了什麼。不含任何病患姓名或影像。</p>
  <div class="row">
    <select id="f_action"><option value="">全部動作</option></select>
    <select id="f_actor"><option value="">全部操作者</option></select>
    <select id="f_role"><option value="">全部角色</option></select>
    <input id="f_since" type="date" title="起日">
    <input id="f_until" type="date" title="迄日（含當日）">
    <button class="primary" onclick="auditGo(0)">查詢</button>
    <button onclick="auditClear()">清除條件</button>
    <button onclick="auditCsv()">匯出 CSV</button>
  </div>
  <div class="row" style="margin-bottom:8px">
    <button onclick="auditPage(-1)">← 上一頁</button>
    <span id="a_range" style="font-size:13px;color:var(--dim)"></span>
    <button onclick="auditPage(1)">下一頁 →</button>
  </div>
  <div class="wrap"><div id="audit"></div></div>

  <h2 style="font-size:16px;font-weight:500;margin:26px 0 8px">雜湊鏈完整性</h2>
  <div class="row">
    <button onclick="auditVerify()">驗證完整鏈</button>
    <span id="a_chain" style="font-size:13px"></span>
  </div>
  <div id="a_anchor"></div>
  <p class="note">
  驗證要逐筆重算 SHA-256，是 <b>O(n)</b> 且沒有取巧空間（只驗最後一段等於沒驗），
  所以做成手動觸發——若跟著每次開頁跑，紀錄累積後這一頁會慢到沒人想開，
  而<b>不開的主控台等於沒有稽核</b>。<br>
  雜湊鏈能偵測「竄改／刪除／重排」，但<b>擋不住整份重寫</b>——
  所以稽核另寫一份到 WORM 桶（保留 7 年、無法覆寫）。兩者缺一不可。
  </p>
</section>

<section id="tab-users">
  <h1>帳號管理</h1>
  <p class="sub">開通、停用、改名、重設密碼。<b>不提供刪除</b>——稽核軌跡引用著那些識別碼。</p>
  <div class="row">
    <input id="nu" placeholder="編號（如 ns05）" style="width:150px">
    <select id="nr">
      <option value="physician">醫師</option>
      <option value="nurse">護理師</option>
      <option value="assistant">助理</option>
      <option value="engineer">工程師</option>
      <option value="admin">管理者</option>
    </select>
    <input id="nn" placeholder="顯示名稱（勿填真實姓名）" style="width:200px">
    <button class="primary" onclick="createUser()">新增（自動產生密碼）</button>
  </div>
  <p class="note" style="margin-top:0">
    ⚠ 顯示名稱請用「護理師 05」這類編號，<b>不要填真實姓名</b>——
    識別碼會永久留在稽核軌跡（append-only，日後進 WORM 桶就再也拿不掉）。
    帳號↔真人的對照表請留在院方保管。
  </p>
  <div id="ubanner"></div>
  <div class="wrap"><div id="users"></div></div>
</section>

</main>
</div>

<script>
let tok = "", perms = [], meRole = "";
const $ = id => document.getElementById(id);
// 凡是要放進 innerHTML 的外部字串一律經過 esc()。
// 這裡的「外部」包含後端錯誤訊息與管理者自己填的顯示名稱——
// 後者是自我 XSS，危害有限，但這是醫療系統，一行的成本不值得為它做例外判斷。
const esc = t => String(t==null?"":t).replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

async function api(path, opt){
  const o = Object.assign({headers:{}}, opt||{});
  o.headers = Object.assign({Authorization:"Bearer "+tok}, o.headers||{});
  if(o.body){ o.headers["Content-Type"]="application/json"; o.method = o.method||"POST"; }
  const r = await fetch(path, o);
  let j = null; try{ j = await r.json(); }catch(_){}
  if(!r.ok) throw new Error((j && (j.error||j.msg)) || ("HTTP "+r.status));
  return j;
}

/* ───────── 頁籤 ─────────
   ⚠ 顯示／隱藏只是可讀性設計，不是存取控制。
   每個 API 端點都在伺服器端獨立檢查權限（fail-closed）；
   改前端 JS 打得出請求，但會拿到 403 並留下稽核紀錄。            */
const TABS = {dash:"flywheel.stats", sys:"audit.read", audit:"audit.read", users:"user.manage"};
const loaded = {};
let cur = "login";

function go(name){
  if(!tok){ name = "login"; }
  else if(TABS[name] && !perms.includes(TABS[name])){
    // 沒權限的頁籤：退回第一個看得到的，而不是給一張空白頁讓人以為壞了。
    name = Object.keys(TABS).find(t => perms.includes(TABS[t])) || "login";
  }
  cur = name;
  document.querySelectorAll("section").forEach(s => s.classList.remove("on"));
  const sec = $("tab-"+name); if(sec) sec.classList.add("on");
  document.querySelectorAll("nav a").forEach(a =>
    a.classList.toggle("on", a.dataset.tab === name));
  if(location.hash.slice(1) !== name) history.replaceState(null,"","#"+name);
  // 延遲載入：進到那一頁才去要資料。第一版四支請求在登入當下全部發出，
  // 其中稽核最慢，於是「只想看收案數」也得等它讀完。
  if(!loaded[name]){ loaded[name] = true; ({dash:loadDash, sys:loadSys,
    audit:()=>auditGo(0), users:loadUsers}[name]||(()=>{}))(); }
}

function applyPerms(){
  document.querySelectorAll("nav a").forEach(a =>
    a.classList.toggle("hide", !perms.includes(TABS[a.dataset.tab])));
  const want = location.hash.slice(1);
  go(TABS[want] ? want : "dash");
}

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
        `<span style="color:var(--dim)">後端回應：${esc(detail)||"(無內容)"}　|　` +
        `你送出的密碼長度 ${pass.length} 字元</span>`;
      return;
    }
    const j = await r.json();
    tok = j.access_token || "";
    // perms 由後端算好回傳(伺服器端的權限矩陣是唯一真相)，前端不自己推導角色能做什麼——
    // 兩邊各算一次遲早會不一致，而不一致的那一刻通常是前端顯示得比後端寬鬆。
    perms = j.perms || []; meRole = j.role || "";
    if(!tok){ $("msg").textContent = "⚠ 回應沒有 token"; return; }
    $("msg").textContent = "";
    $("who").innerHTML = `${esc(j.display_name||j.user||"")}<br>` +
      `${esc(j.role_zh||meRole)}　<code>${esc(j.identity||"")}</code><br>` +
      `<a onclick="logout()" style="cursor:pointer;text-decoration:underline">登出</a>`;
    applyPerms();
  }catch(e){ $("msg").textContent = "⚠ 連不到後端：" + e.message; }
}
function logout(){
  tok=""; perms=[]; meRole="";
  Object.keys(loaded).forEach(k => delete loaded[k]);
  $("who").textContent = "未登入";
  document.querySelectorAll("nav a").forEach(a => a.classList.add("hide"));
  $("p").value = "";
  go("login");
}

/* ───────── 飛輪 Dashboard ───────── */
function card(k,v,n,cls){
  return `<div class="card"><p class="k">${k}</p>
    <p class="v ${cls||''}">${v}</p>${n?`<p class="n">${n}</p>`:""}</div>`;
}
async function loadDash(){
  let s; try{ s = await api("/api/v1/flywheel/stats"); }
  catch(e){ $("detail").innerHTML = "<p class='bad'>讀取失敗："+esc(e.message)+"</p>"; return; }
  const by = s.by_source || {};
  const clinical = by.clinical || 0;
  const broken = (s.orphan_no_image||0) + (s.image_file_missing||0) + (s.malformed||0);
  $("cards").innerHTML =
    card("臨床收案進度", clinical + " <span style='font-size:14px;color:var(--dim)'>/ 20</span>",
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
    rows.map(r=>`<tr><td>${r[0]}</td><td>${r[1]}</td><td style="color:var(--dim)">${r[2]}</td></tr>`).join("") +
    "</table><p class='note'>儲存後端：<code>" + esc(s.store||"—") + "</code></p>";
}

/* ───────── 系統狀態 ───────── */
async function loadSys(){
  // /api/health 不需 token（Cloud Run 探針要用），但這一頁只在有 audit.read 時開放——
  // 降級原因會透露內部模組名稱，沒必要讓臨床角色看到。
  const h = await fetch("/api/health").then(r=>r.json()).catch(()=>({}));
  const sv = h.services || {};
  const ok = v => v ? "<span class='ok'>✓</span>" : "<span class='bad'>✗</span>";
  // ⚠ 正常值是 "healthy"（不是 "ok"）。寫錯會讓正常服務永遠顯示紅字——
  // 狼來了幾次之後，真的降級時就沒人會看了。
  const deg = (h.status||"") !== "healthy";
  const isGcs = /^gcs:/.test(h.store||"");
  $("sys").innerHTML =
    `<table>
      <tr><th>項目</th><th>狀態</th><th>影響</th></tr>
      <tr><td>整體</td><td class="${deg?'bad':'ok'}">${esc(h.status||"?")}</td>
          <td style="color:var(--dim)">${esc(h.degraded_reason||"正常")}</td></tr>
      <tr><td>ONNX Runtime</td><td>${ok(sv.onnxruntime)}</td>
          <td style="color:var(--dim)">缺了會退回 HSV 色彩分割，面積會錯但仍回 200——最危險的失敗模式</td></tr>
      <tr><td>分割模型</td><td>${ok(sv.segmentation_model)}</td>
          <td style="color:var(--dim)">缺了無法產生遮罩</td></tr>
      <tr><td>classify 模組</td><td>${ok(sv.classify_modules)}</td>
          <td style="color:var(--dim)">缺了 /api/v1/classify 直接 503</td></tr>
      <tr><td>儲存後端</td><td colspan="2" class="${isGcs?'':'bad'}">
          <code>${esc(h.store||"—")}</code>
          ${isGcs ? "" : "<br><span style='font-size:12px'>不是 GCS：Cloud Run 的容器檔案系統是暫時的，"+
                         "佇列與影像會在實例回收時<b>無聲消失</b></span>"}</td></tr>
      <tr><td>稽核 WORM 桶</td><td>${ok(/WORM/.test(h.store||""))}</td>
          <td style="color:var(--dim)">未接上時稽核寫在主桶，刪得掉——雜湊鏈擋不住整份重寫</td></tr>
    </table>
    <p class="note">⚠ 若「整體」是 degraded 仍能量測，但<b>面積數值不可作臨床用途</b>。
    這正是把降級狀態拉到檯面上的理由——先前它只寫在日誌裡，前端照樣顯示一個看起來正常的數字。</p>`;
}

/* ───────── 稽核軌跡 ───────── */
const A = {limit:50, offset:0, matched:0};
function auditQuery(extra){
  const p = new URLSearchParams({limit:A.limit, offset:A.offset});
  [["action","f_action"],["actor","f_actor"],["role","f_role"],
   ["since","f_since"],["until","f_until"]].forEach(([k,id]) => {
    const v = ($(id).value||"").trim(); if(v) p.set(k, v);
  });
  Object.entries(extra||{}).forEach(([k,v]) => p.set(k,v));
  return p.toString();
}
function fillSelect(id, values, label){
  const el = $(id), keep = el.value;
  if(el.options.length > 1) return;            // 只填一次，避免每次查詢都重建而丟失選取
  values.forEach(v => el.add(new Option(v, v)));
  el.value = keep;
}
async function auditGo(offset){
  A.offset = Math.max(0, offset|0);
  let j; try{ j = await api("/api/v1/audit?" + auditQuery()); }
  catch(e){ $("audit").innerHTML = "<p class='bad'>讀取失敗："+esc(e.message)+"</p>"; return; }
  A.matched = j.matched;
  fillSelect("f_action", j.actions||[]);
  fillSelect("f_actor",  j.actors||[]);
  fillSelect("f_role",   j.roles||[]);
  const from = j.matched ? A.offset+1 : 0, to = Math.min(A.offset + j.limit, j.matched);
  $("a_range").textContent = `第 ${from}–${to} 筆（符合 ${j.matched}，全量 ${j.total}）`;
  $("audit").innerHTML = "<table><tr><th>#</th><th>時間 (UTC)</th><th>操作者</th><th>角色</th>" +
    "<th>動作</th><th>對象</th><th>結果</th></tr>" +
    (j.entries||[]).map(e => `<tr>
       <td style="color:var(--dim)">${esc(e.seq)}</td>
       <td class="nowrap">${esc((e.ts||"").replace("T"," ").replace("Z",""))}</td>
       <td class="nowrap">${esc(e.actor)}</td><td>${esc(e.role||"—")}</td>
       <td class="nowrap">${esc(e.action)}</td><td><code>${esc(e.code||"—")}</code></td>
       <td style="color:var(--dim)">${esc(e.result||"")}</td></tr>`).join("") + "</table>";
  if(!j.entries || !j.entries.length)
    $("audit").innerHTML += "<p class='note'>沒有符合條件的紀錄。</p>";
}
function auditPage(d){
  const next = A.offset + d*A.limit;
  if(next < 0 || next >= A.matched) return;
  auditGo(next);
}
function auditClear(){
  ["f_action","f_actor","f_role","f_since","f_until"].forEach(id => $(id).value = "");
  auditGo(0);
}
async function auditCsv(){
  // 不能用 <a href> 直接下載：那個請求不會帶 Authorization 標頭，會拿到 401。
  // 改成 fetch 取 blob 再觸發下載。
  try{
    const r = await fetch("/api/v1/audit?" + auditQuery({format:"csv"}),
                          {headers:{Authorization:"Bearer "+tok}});
    if(!r.ok) throw new Error("HTTP " + r.status);
    const blob = await r.blob(), u = URL.createObjectURL(blob), a = document.createElement("a");
    a.href = u; a.download = "woundai_audit.csv"; a.click();
    URL.revokeObjectURL(u);
  }catch(e){ alert("匯出失敗：" + e.message); }
}
async function auditVerify(){
  $("a_chain").textContent = "驗證中…（要逐筆重算雜湊，紀錄多時需要一點時間）";
  let j; try{ j = await api("/api/v1/audit?" + auditQuery({verify:1, limit:1})); }
  catch(e){ $("a_chain").innerHTML = "<span class='bad'>驗證失敗："+esc(e.message)+"</span>"; return; }
  const v = j.verified || {};
  $("a_chain").innerHTML = v.ok
    ? `<span class="ok">✓ 完整</span>（${v.total} 筆）`
    : `<span class="bad">✗ 異常 ${(v.issues||[]).length} 處</span>：` +
      esc((v.issues||[]).slice(0,3).map(i=>i.kind+"@#"+i.index).join("、"));
  // 錨定用：抄進會議紀錄後，日後可證明「此時間點之前未被竄改」。
  $("a_anchor").innerHTML =
    `<div class="banner"><b>錨定資訊</b>（可抄進會議紀錄／法遵文件）<br>
      head <code id="hh">${esc(v.head||"")}</code><br>
      驗證時間 ${esc(v.verified_at||"")}　驗證者 ${esc(v.verified_by||"")}
      筆數 ${esc(v.total)}<br>
      <button onclick="copyAnchor()" style="margin-top:6px">複製</button>
      <span style="font-size:12px;color:var(--dim)">
        ——之後任何一筆被改、被刪或被調換，重新驗證都會對不上這個 head。</span>
    </div>`;
  if(!v.ok) $("a_anchor").innerHTML +=
    "<p class='bad' style='font-size:13px'>⚠ 鏈異常時請立即通知工程師，並<b>停止寫入新紀錄</b>。</p>";
}
function copyAnchor(){
  const t = `WoundAI 稽核鏈錨定\\nhead: ${$("hh").textContent}\\n` +
            $("a_anchor").innerText.split("\\n").filter(l=>l.includes("驗證時間")).join("");
  navigator.clipboard.writeText(t).then(()=>alert("已複製"), ()=>alert("複製失敗，請手動選取"));
}

/* ───────── 帳號管理 ───────── */
let users = [];
async function loadUsers(){
  let j; try{ j = await api("/api/v1/users"); }
  catch(e){ $("users").innerHTML = "<p class='bad'>讀取失敗："+esc(e.message)+"</p>"; return; }
  users = j.users || [];
  const n = users.filter(u=>!u.disabled).length;
  $("users").innerHTML = "<table><tr><th>登入帳號</th><th>角色</th><th>顯示名稱</th>" +
    "<th>建立</th><th>狀態</th><th></th></tr>" +
    users.map(u => `<tr class="${u.disabled?'off':''}">
      <td class="nowrap"><code>${esc(u.identity||(u.org+":"+u.user))}</code></td>
      <td>${esc(u.role_zh||u.role)}</td><td>${esc(u.display_name||"")}</td>
      <td class="nowrap" style="color:var(--dim)">${esc((u.created_at||"").slice(0,10))}</td>
      <td class="${u.disabled?'bad':'ok'}">${u.disabled?"已停用":"啟用中"}</td>
      <td class="nowrap">
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
  d.className = "banner"; d.innerHTML = html;
  $("ubanner").appendChild(d);
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
  const rec = users.find(x => x.user === u); if(!rec) return;
  const name = prompt("新的顯示名稱（請用編號式，例如「護理師 05」，不要填真實姓名）：",
                      rec.display_name || "");
  if(name === null) return;
  try{
    // 不傳 password → 沿用既有雜湊；不傳 disabled → 沿用目前狀態。
    await api("/api/v1/users", {body: JSON.stringify({user:u, role:rec.role, display_name:name})});
    loadUsers(); loaded.audit = false;
  }catch(e){ alert("失敗：" + e.message); }
}
async function toggleUser(u){
  const rec = users.find(x => x.user === u); if(!rec) return;
  const to = !rec.disabled;
  if(!confirm((to?"停用":"啟用") + " " + u + "？")) return;
  try{
    // upsert 是整筆覆寫，role 少傳會被後端拒絕；密碼不傳＝不變更。
    await api("/api/v1/users", {body: JSON.stringify({user:u, role:rec.role, disabled:to,
                                                      display_name:rec.display_name||""})});
    loadUsers(); loaded.audit = false;
  }catch(e){ alert("失敗：" + e.message); }
}
async function resetPw(u){
  const rec = users.find(x => x.user === u); if(!rec) return;
  if(!confirm("重設 " + u + " 的密碼？舊密碼立即失效，新密碼只顯示一次。")) return;
  try{
    const j = await api("/api/v1/users", {body: JSON.stringify({user:u, role:rec.role,
      display_name:rec.display_name||"", disabled:!!rec.disabled, generate_password:true})});
    banner(`✓ <code>${esc(u)}</code> 新密碼：<span class="pw">${esc(j.generated_password)}</span><br>` +
           `<span style="font-size:12px">舊密碼已失效。${esc(j.note||"")}</span>`);
    loaded.audit = false;
  }catch(e){ alert("失敗：" + e.message); }
}

window.addEventListener("hashchange", () => { if(tok) go(location.hash.slice(1)); });
</script></body></html>"""


@console_bp.route("/console", methods=["GET"])
def console_page():
    # 頁面本身不含資料，所以不必驗證；資料一律要帶 JWT 才拿得到。
    # 這樣就不用為主控台再造一套 session/cookie，也少一個可以被繞過的入口。
    #
    # ⚠ 用 content_type 而非 mimetype。Flask 會**再補一次** charset 到 mimetype 上，
    # 於是標頭變成 `text/html; charset=utf-8; charset=utf-8`（實測如此）。
    # 瀏覽器容忍，但重複參數在嚴格的解析器（代理、掃描器）上是未定義行為。
    return Response(_PAGE, content_type="text/html; charset=utf-8")
