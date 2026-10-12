// Execute the shipped UI script against a small DOM/fetch fixture.
// No browser, credentials, cloud requests, or third-party npm dependencies.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'static/members.html'),'utf8');
const script=fs.readFileSync(path.join(__dirname,'static/members.js'),'utf8');
class Element {
  constructor(tag='div'){Object.assign(this,{tag,textContent:'',value:'',hidden:false,disabled:false,open:false,dataset:{},children:[],listeners:{},selectedOptions:[{textContent:'醫師'}]});}
  addEventListener(event,fn){this.listeners[event]=fn;}
  replaceChildren(...nodes){this.children=nodes;}
  append(node){this.children.push(node);}
  showModal(){this.open=true;}
  close(){this.open=false;}
  scrollIntoView(){}
}
const response=(status,data)=>({status,ok:status>=200&&status<300,json:async()=>data});
const state=(authenticated=true)=>({google_verified:true,google_email:'admin@example.test',app_authenticated:authenticated,
  app_identity:authenticated?'mmhps20261007:admin':null,session_csrf:'synthetic-csrf'});
const members={demonstration:true,actor:{google_email:'admin@example.test',app_identity:'mmhps20261007:admin'},
  entries:[],accounts:[],account_management_enabled:true,csrf:'synthetic-member-csrf',policy_snapshot:'p',account_snapshot:'a'};
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(first=state()){
  const elements={};
  for(const match of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g)){
    const node=elements[match[2]]=new Element(match[1]);node.id=match[2];node.hidden=/\shidden[\s>]/.test(match[0]);
  }
  const calls=[];
  const routes={handler:async(url)=>response(200,url==='/api/session'?first:members)};
  const context=vm.createContext({document:{getElementById:id=>elements[id],createElement:tag=>new Element(tag),
    querySelectorAll:selector=>Object.values(elements).filter(e=>selector==='button'?e.tag==='button':selector==='[data-account]'&&e.dataset.account)},
    fetch:async(url,options)=>{calls.push({url,method:options.method||'GET'});return routes.handler(url,options);}});
  vm.runInContext(script,context);
  return {elements,calls,routes,async click(id){await elements[id].listeners.click();await flush();},
    async submit(id){await elements[id].listeners.submit({preventDefault(){}});await flush();}};
}
function noAccess(f){
  const e=f.elements;
  assert.equal(e.logout.hidden,true);assert.equal(e['account-form'].hidden,true);
  assert.equal(e['app-console'].hidden,true);assert.equal(e['account-secret'].hidden,true);
  assert.equal(e['new-password'].textContent,'');assert.equal(e.entries.children.length,0);
  assert.equal(e.accounts.children.length,0);assert.equal(e['new-account'].disabled,true);
  assert.equal(e.grant.disabled,true);assert.equal(e.confirm.open,false);
}
test('authenticated refresh restores status and member controls',async()=>{
  const f=fixture();await flush();await f.click('refresh');
  assert.match(f.elements['app-status'].textContent,/已登入/);
  assert.equal(f.elements.logout.hidden,false);assert.equal(f.elements['login-form'].hidden,true);
  assert.equal(f.elements['new-account'].disabled,false);
});
test('pending revalidation immediately removes old actor, editor, modal and secret',async()=>{
  const f=fixture();await flush();await f.click('new-account');
  f.elements['new-password'].textContent='synthetic-secret';f.elements['account-secret'].hidden=false;f.elements.confirm.open=true;
  let resolve;f.routes.handler=()=>new Promise(r=>{resolve=r;});
  const checking=f.click('refresh');await flush();noAccess(f);
  assert.equal(f.elements.actor.textContent,'管理者狀態未確認');assert.equal(f.elements['login-submit'].disabled,true);
  resolve(response(401,{}));await checking;noAccess(f);assert.match(f.elements['google-status'].textContent,/尚未確認/);
});
test('expired App session keeps Google status but shows login only',async()=>{
  const f=fixture();await flush();await f.click('new-account');
  f.routes.handler=async()=>response(200,state(false));await f.click('refresh');noAccess(f);
  assert.equal(f.elements['login-form'].hidden,false);assert.equal(f.elements['login-submit'].disabled,false);
  assert.match(f.elements['app-status'].textContent,/尚未登入/);
});
test('network failure cannot retain a previous authenticated display',async()=>{
  const f=fixture();await flush();f.routes.handler=async()=>{throw new Error('synthetic network failure');};
  await f.click('refresh');noAccess(f);assert.equal(f.elements['login-submit'].disabled,true);
  assert.equal(f.elements.actor.textContent,'管理者狀態未確認');assert.match(f.elements['app-status'].textContent,/尚未確認/);
});
test('malformed successful session response does not enable login or management',async()=>{
  for(const invalid of [{...state(),google_verified:false},{...state(),session_csrf:''},{...state(),app_identity:'foreign:admin'},{...state(),app_authenticated:'true'}]){
    const f=fixture(invalid);await flush();noAccess(f);assert.equal(f.elements['login-submit'].disabled,true);
    assert.equal(f.calls.some(c=>c.url==='/api/members'),false);
  }
});
test('member 401 after valid session clears identity and disables stale login CSRF',async()=>{
  const f=fixture();await flush();f.routes.handler=async url=>response(url==='/api/session'?200:401,url==='/api/session'?state():{error:'administrator_verification_required'});
  await f.click('refresh');noAccess(f);assert.equal(f.elements['login-submit'].disabled,true);
  assert.match(f.elements['app-status'].textContent,/失效/);
});
test('login uses one request, clears password, then reads verified status',async()=>{
  const f=fixture(state(false));await flush();f.elements['login-user'].value='admin';f.elements['login-password'].value='synthetic-test';
  f.routes.handler=async url=>response(200,url==='/api/session/login'?{status:'authenticated'}:url==='/api/session'?state():members);
  await f.submit('login-form');assert.equal(f.elements['login-password'].value,'');
  assert.equal(f.elements.logout.hidden,false);assert.equal(f.calls.filter(c=>c.url==='/api/session/login').length,1);
});
test('confirmed logout hides management and restores unauthenticated state',async()=>{
  const f=fixture();await flush();f.routes.handler=async url=>response(200,url==='/api/session/logout'?{status:'signed_out'}:state(false));
  await f.click('logout');noAccess(f);assert.equal(f.elements['login-form'].hidden,false);
  assert.equal(f.calls.filter(c=>c.url==='/api/session/logout').length,1);
});
