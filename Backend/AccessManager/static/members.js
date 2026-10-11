"use strict";
const el = id => document.getElementById(id);
let snapshot = null, busy = false, pending = null, pendingPath = "/api/entry", accountAction = "create";
let sessionState=null;
const messages = {
  management_not_configured: "管理服務尚未接線，不能執行權限變更。",
  administrator_verification_failed: "管理者驗證失敗，請重新完成 Google 與 WoundAI 管理者登入。",
  administrator_verification_required: "請先完成 Google 與 WoundAI 管理者登入。",
  administrator_access_denied: "目前帳號沒有機構成員管理權限。",
  stale_policy_refresh_required: "名單已由其他管理者更新。請重新整理並再次確認，尚未送出本次變更。",
  csrf_rejected: "登入工作階段已變更，請重新整理後再操作。",
  change_unconfirmed: "結果尚未確認；可能已寫入，請先核對門禁與稽核，勿直接重送。",
  member_state_unavailable: "目前無法讀取完整名單，請稍後重新整理。",
  protected_recovery_administrator: "此帳號是保留的救援管理入口，不能在此撤除。",
  change_not_started: "未完成變更前置檢查，請檢查管理服務及稽核狀態。",
  account_management_not_configured: "帳號管理尚未接線，請使用既有主控台。",
  stale_account_refresh_required: "帳號名單已變更，請重新整理並再次確認。",
  account_existence_changed: "帳號存在狀態已改變，請重新整理。",
  account_state_unavailable: "無法讀取完整帳號狀態，尚未開始本次變更。",
  account_version_unavailable: "後端尚未提供帳號版本，請先更新後端再使用同頁帳號管理。",
  invalid_account_fields: "帳號欄位不正確，請檢查代號、角色與名稱。",
  account_change_unconfirmed: "帳號變更結果尚未確認，可能已保存；請先刷新核對，勿直接重送。若新帳號已存在但未取得密碼，請由既有主控台重設。"
};
function status(text, kind="info") { el("status").textContent=text; el("status").dataset.kind=kind; }
function clearAccessView(){
  snapshot=null;pending=null;hidePassword();
  el("entries").replaceChildren();el("accounts").replaceChildren();
  el("account-form").hidden=true;el("app-console").hidden=true;
  el("actor").textContent="管理者狀態未確認";
  if(el("confirm").open)el("confirm").close();
}
function lock(value) {
  busy=value;
  document.querySelectorAll("button").forEach(b=>{b.disabled=value||b.dataset.protected==="true"||(!snapshot&&(b.dataset.entry==="true"||b.id==="apply"));});
  el("email").disabled=value;
  el("grant").disabled=value||!snapshot;
  el("new-account").disabled=value||!snapshot?.account_management_enabled;
  document.querySelectorAll("[data-account]").forEach(b=>{b.disabled=value||!snapshot?.account_management_enabled||b.dataset.protected==="true";});
  el("review-account").disabled=value||!snapshot?.account_management_enabled;
  ["account-name","account-role","account-disabled"].forEach(id=>{el(id).disabled=value;});
  el("account-user").disabled=value||accountAction==="update";
  el("login-submit").disabled=value||!sessionState?.google_verified;
  el("logout").disabled=value||!sessionState?.app_authenticated;
}
async function api(path, body) {
  const options={credentials:"same-origin",cache:"no-store",headers:{}};
  if(body){options.method="POST";options.headers={"Content-Type":"application/json","X-MMH-CSRF":snapshot.csrf};options.body=JSON.stringify(body);}
  const response=await fetch(path,options);
  const data=await response.json();
  if(response.status===401){clearAccessView();sessionState=null;el("app-status").textContent="WoundAI：登入驗證已失效，請重新整理後登入";el("logout").hidden=true;el("login-form").hidden=false;}
  if(!response.ok){const error=new Error(messages[data.error]||"操作失敗，請核對服務狀態。");error.data=data;throw error;}
  return data;
}
function td(row,text){const cell=document.createElement("td");cell.textContent=text;row.append(cell);return cell;}
function render(data){
  el("demo").hidden=!data.demonstration;
  el("actor").textContent=`管理者：${data.actor.google_email} ／ ${data.actor.app_identity}`;
  el("entries").replaceChildren();
  data.entries.forEach(item=>{
    const row=document.createElement("tr");
    td(row,item.email);td(row,item.direct?"已設定直接授權":"無直接授權");
    td(row,item.protected?"救援管理入口":item.conditional?"另有條件式授權，需另核對":"有效存取仍需驗證");
    const button=document.createElement("button");button.type="button";button.textContent=item.direct?"撤除直接授權":"開通直接授權";
    button.dataset.entry="true";button.dataset.protected=String(item.protected&&item.direct);button.disabled=button.dataset.protected==="true";
    button.addEventListener("click",()=>confirmChange(item.email,item.direct?"revoke":"grant"));
    td(row,"").append(button);el("entries").append(row);
  });
  if(!data.entries.length){const row=document.createElement("tr");td(row,"目前沒有具名使用者的直接入口設定。").colSpan=4;el("entries").append(row);}
  el("accounts").replaceChildren();
  data.accounts.forEach(account=>{const row=document.createElement("tr");td(row,account.identity);td(row,account.role_zh||account.role);td(row,account.display_name||"—");td(row,account.disabled?"已停用":"啟用中");
    const button=document.createElement("button");button.type="button";button.textContent="角色／啟用狀態";button.dataset.account="true";
    button.dataset.protected=String(account.identity===data.actor.app_identity||account.identity==="mmhps20261007:admin");
    button.addEventListener("click",()=>editAccount(account));td(row,"").append(button);el("accounts").append(row);});
  if(!data.accounts.length){const row=document.createElement("tr");td(row,"目前沒有 WoundAI 操作帳號。").colSpan=5;el("accounts").append(row);}
  // Never use a server-supplied arbitrary URL as a credential-bearing destination.
  el("app-console").href="https://candidate---woundai-backend-mmhps20261007-z4kgfkob4a-de.a.run.app/console#users";
  el("app-console").hidden=data.demonstration;
}
async function refresh(){
  if(busy)return;hidePassword();el("account-form").hidden=true;lock(true);snapshot=null;status("正在讀取門禁與帳號狀態…");
  try{const data=await api("/api/members");render(data);snapshot=data;status("已讀取直接授權與 WoundAI 帳號；兩者未自動配對。有效入口仍需實際驗證。");}
  catch(error){el("entries").replaceChildren();el("accounts").replaceChildren();el("actor").textContent="管理者狀態未確認";el("app-console").hidden=true;status(error.message,"error");}
  finally{lock(false);}
}
function confirmChange(email,action){
  if(busy||!snapshot)return;
  pending={email,action,policy_snapshot:snapshot.policy_snapshot};
  pendingPath="/api/entry";
  el("change-scope").textContent="僅修改 MMH 直接入口授權。WoundAI 角色與 OAuth 測試名單不會同步變更。";
  el("change-description").textContent=`${action==="grant"?"開通":"撤除"} ${email} 的直接入口授權？`;
  el("confirm").showModal();
}
el("grant-form").addEventListener("submit",event=>{event.preventDefault();confirmChange(el("email").value.trim(),"grant");});
el("refresh").addEventListener("click",checkSession);
function hidePassword(){el("new-password").textContent="";el("account-secret").hidden=true;}
function editAccount(account=null){
  if(busy||!snapshot?.account_management_enabled)return;
  hidePassword();accountAction=account?"update":"create";
  el("account-heading").textContent=account?"變更帳號角色／狀態":"新增 WoundAI 帳號";
  el("account-user").value=account?.user||"";el("account-name").value=account?.display_name||"";
  el("account-role").value=account?.role||"physician";el("account-disabled").checked=account?.disabled||false;
  el("account-form").hidden=false;lock(false);el("account-form").scrollIntoView({block:"nearest"});
}
el("new-account").addEventListener("click",()=>editAccount());
el("hide-password").addEventListener("click",hidePassword);
el("close-account").addEventListener("click",()=>{el("account-form").hidden=true;});
el("account-form").addEventListener("submit",event=>{
  event.preventDefault();if(busy||!snapshot?.account_management_enabled)return;
  pending={action:accountAction,user:el("account-user").value,role:el("account-role").value,display_name:el("account-name").value,disabled:el("account-disabled").checked,account_snapshot:snapshot.account_snapshot};
  pendingPath="/api/accounts";
  el("change-description").textContent=`${accountAction==="create"?"新增":"更新"} ${pending.user}；角色：${el("account-role").selectedOptions[0].textContent}；${pending.disabled?"停用":"啟用"}。`;
  el("change-scope").textContent="僅修改 WoundAI 操作帳號，Google 門禁與 OAuth 名單不會同步變更。管理者角色可管理其他帳號，請確認授權對象。";
  el("confirm").showModal();
});
el("confirm").addEventListener("cancel",event=>{if(busy)event.preventDefault();});
el("apply").addEventListener("click",async()=>{
  if(busy||!pending||!snapshot)return;
  const change=pending,path=pendingPath;pending=null;hidePassword();lock(true);status("正在保存並讀回設定，請勿重複操作…");
  try{
    const result=await api(path,change);
    if(path==="/api/accounts"){
      if(result.status!=="account_readback_verified"||typeof result.changed!=="boolean"||result.identity!==`mmhps20261007:${change.user}`||(change.action==="create"&&typeof result.generated_password!=="string"))throw new Error(messages.account_change_unconfirmed);
      el("confirm").close();el("account-form").hidden=true;el("accounts").replaceChildren();snapshot=null;
      if(result.generated_password){el("new-password").textContent=result.generated_password;el("account-secret").hidden=false;}
      status(`${result.changed?"帳號已保存並讀回確認":"帳號已符合要求，未重複寫入"}。Google 門禁未變更。${result.generated_password?"請先保存新密碼，再重新整理。":"請重新整理名單。"}`,"success");
      return;
    }
    if(result.status!=="direct_policy_verified"||typeof result.changed!=="boolean"||!result.operation_id)throw new Error(messages.change_unconfirmed);
    el("confirm").close();snapshot=null;
    status(`${result.changed?"直接授權已更新並讀回確認":"直接授權已符合要求，未重複寫入"}。WoundAI 帳號未變更；實際入口及既有工作階段仍需驗證。請重新整理名單。`,"success");
  }catch(error){el("confirm").close();snapshot=null;status(error.message+(error.data?.operation_id?` 操作編號：${error.data.operation_id}`:""),"error");}
  finally{lock(false);}
});
async function checkSession(){
  if(busy)return;
  clearAccessView();sessionState=null;
  el("google-status").textContent="正在確認 Google 門禁…";
  el("app-status").textContent="WoundAI：正在確認登入狀態…";
  el("logout").hidden=true;el("login-form").hidden=false;
  lock(true);status("正在確認登入與管理權限…");
  try{
    const response=await fetch("/api/session",{credentials:"same-origin",cache:"no-store"});
    // Local fixture and older unconfigured factory do not mount session routes.
    if(response.status===404){el("login-panel").hidden=true;lock(false);return refresh();}
    el("login-panel").hidden=false;
    if(!response.ok)throw new Error("Google 門禁尚未驗證或此帳號未獲管理授權，請核對登入與授權。");
    const state=await response.json();
    if(state.google_verified!==true||typeof state.google_email!=="string"||!state.google_email||
       typeof state.app_authenticated!=="boolean"||typeof state.session_csrf!=="string"||!state.session_csrf||
       (state.app_authenticated&&(typeof state.app_identity!=="string"||!state.app_identity.startsWith("mmhps20261007:"))))
      throw new Error("登入狀態回覆不完整，請重新整理核對；尚未開放管理操作。");
    sessionState=state;
    el("google-status").textContent=`Google 門禁：已驗證（${sessionState.google_email}）`;
    el("app-status").textContent=sessionState.app_authenticated?`WoundAI：已登入（${sessionState.app_identity}）`:"WoundAI：尚未登入或工作階段已到期";
    el("logout").hidden=!sessionState.app_authenticated;el("login-form").hidden=sessionState.app_authenticated;
    if(sessionState.app_authenticated){lock(false);return refresh();}
    el("entries").replaceChildren();el("accounts").replaceChildren();el("actor").textContent="請完成 WoundAI 管理者登入";
    status("Google 門禁已通過，請登入 WoundAI 管理帳號。");
  }catch(error){sessionState=null;el("google-status").textContent="Google 門禁：尚未確認";el("app-status").textContent="WoundAI：尚未確認";el("logout").hidden=true;el("entries").replaceChildren();el("accounts").replaceChildren();status(error.message,"error");}
  finally{lock(false);}
}
el("login-form").addEventListener("submit",async event=>{
  event.preventDefault();if(busy||!sessionState?.google_verified)return;
  lock(true);status("正在登入並確認管理權限，請稍候…");
  let success=false;
  try{
    const response=await fetch("/api/session/login",{method:"POST",credentials:"same-origin",cache:"no-store",
      headers:{"Content-Type":"application/json","X-MMH-Session-CSRF":sessionState.session_csrf},
      body:JSON.stringify({username:el("login-user").value.trim(),password:el("login-password").value})});
    const result=await response.json();
    if(!response.ok||result.status!=="authenticated")throw new Error("登入未完成，請核對 MMH 管理帳密與管理者權限；連續失敗請稍候再試。");
    success=true;
  }catch(error){status(error.message,"error");}
  finally{el("login-password").value="";lock(false);}
  if(success)await checkSession();
});
el("logout").addEventListener("click",async()=>{
  if(busy||!sessionState)return;lock(true);hidePassword();
  try{
    const response=await fetch("/api/session/logout",{method:"POST",credentials:"same-origin",cache:"no-store",
      headers:{"Content-Type":"application/json","X-MMH-Session-CSRF":sessionState.session_csrf},body:"{}"});
    const result=await response.json();
    if(!response.ok||result.status!=="signed_out")throw new Error("登出尚未確認，請重新整理核對。");
    snapshot=null;sessionState=null;el("entries").replaceChildren();el("accounts").replaceChildren();el("account-form").hidden=true;
    lock(false);await checkSession();
  }catch(error){status(error.message,"error");}finally{lock(false);}
});
checkSession();
