// The rendered production page with synthetic APIs. No network reaches an SDK or SAP.
import assert from 'node:assert/strict';
import {readFile, mkdir, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)(process.env.SAPBA_PLAYWRIGHT_MODULE || 'playwright');
const dist=path.resolve(process.env.SAPBA_BROWSER_DIST || 'dist');
const out=process.env.SAPBA_BROWSER_REPORT; assert.ok(out); await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined});
const results=[];
try {
for (const locale of ['zh','en']) for (const width of [1440,1024,690,550,390]) {
 const context=await browser.newContext({viewport:{width,height:1024}}), page=await context.newPage();
 const errors=[]; page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{
  const auth={status:'authenticated',authenticated:true,identity:'demo***',method:'browser',source:'official_cli',revision:1,capabilities:{check:true,login:true,logout:true,switch_account:true},blockers:[]};
  const base={provider_implemented:true,platform_supported:true,installed:true,current_version:'synthetic',cli_version:'synthetic',enabled:true,selectable:true,can_enable:true,authenticated:true,authentication:auth,model_discovery_supported:true,model_check_supported:true,check_status:'checked',capabilities:[],blockers:[],update_enabled:false,status_refresh:{status:'ready',stale:false,checked_at:'2026-10-20T00:00:00Z'}};
  const model=(id,selected=true)=>({model_id:id,display_name:id,selected,selectable:selected,check_status:selected?'compatible':'not_checked',compatibility_applicability:{status:selected?'reusable':'recovery_pending'},supported_reasoning_efforts:['low','high'],saved_reasoning_effort:'high',default_reasoning_effort:'low',input_modalities:['text']});
  window.fixture={reads:[],writes:[],count:2,revision:1,default:'workbuddy',delayModels:false,pending:null,operation:null,failLists:false};
  window.fetch=async(url,options={})=>{
   const f=window.fixture, method=options.method||'GET';
   if (method!=='GET') { f.writes.push({url,method,body:options.body,headers:options.headers}); return {ok:false,status:409,json:async()=>({detail:{message:'Synthetic rejection'}})}; }
   f.reads.push(String(url)); let data;
   if (String(url).endsWith('/free-query-history')) data={retention_days:30};
   else if (String(url).endsWith('/models')) { data={catalog_status:'ready',model_catalog_complete:true,items:[model('deepseek-v4.1-flash'),model('another-model',false)]}; if (f.delayModels) await new Promise(resolve=>{f.pending=resolve;}); }
   else {
    if(f.failLists) throw new Error('Synthetic offline');
    const codex={...base,provider_id:'codex',sdk_id:'codex',name:{zh:'Codex',en:'Codex'},default_model_id:'gpt-6.1-sol',selected:f.default==='codex'};
    const wb={...base,provider_id:'workbuddy',sdk_id:'workbuddy',name:{zh:'WorkBuddy',en:'WorkBuddy'},default_model_id:'deepseek-v4.1-flash',selected:f.default==='workbuddy',runtime_configuration_revision:f.revision,authentication:{...auth,operation:f.operation},service_site:'china',service_site_revision:1,service_site_digest:'a'.repeat(64),service_site_switch_allowed:false};
    const items=[codex,wb];
    for(let n=2;n<f.count;n++)items.push({...base,provider_id:'provider-'+n,sdk_id:'provider-'+n,name:{zh:'Provider '+n,en:'Provider '+n},enabled:n!==2,default_model_id:'model-'+n,runtime_configuration_revision:f.revision});
    items.push({...base,provider_id:'reserved',sdk_id:'reserved',name:{zh:'预留 SDK',en:'Reserved SDK'},provider_implemented:false,enabled:false,can_enable:false,selectable:false,blockers:['provider_not_implemented']});
    if(f.unknown){delete items[1].provider_implemented;delete items[1].platform_supported;items[1].enabled=null;}
    data={items,default_provider_id:f.default};
   }
   return {ok:true,json:async()=>data};
  };
 });
 await page.route('**/*',async route=>{
   const url=new URL(route.request().url()); if(url.origin!=='http://127.0.0.1:4339')return route.abort();
   const clean=url.pathname.replace(/^\/SAPBusinessAgents\//,'/');
   const pathname=clean==='/'?`/${locale}/settings/index.html`:clean.endsWith('/')?clean+'index.html':clean;
   const file=path.resolve(dist,pathname.replace(/^\//,'')); if(!file.startsWith(dist+path.sep))return route.abort();
   try{return route.fulfill({body:await readFile(file),contentType:file.endsWith('.html')?'text/html':file.endsWith('.css')?'text/css':file.endsWith('.js')?'text/javascript':'application/octet-stream'});}catch{return route.abort();}
 });
 await page.goto(`http://127.0.0.1:4339/${locale}/settings/`);
 const card=page.locator('[data-provider-panel="workbuddy"]');
 await card.locator('.sdk-model-section .sdk-model-picker select').waitFor();
 await page.waitForFunction(()=>document.querySelector('[data-runtime-picker]')?.value==='workbuddy');
 assert.equal(await page.locator('[data-provider-panel]:visible').count(),1);
 assert.equal(await page.locator('.settings-navigation a').count(),2);
 assert.match(await page.locator('[data-default-summary]').innerText(),/WorkBuddy.*deepseek/);
 assert.equal(await page.evaluate(()=>window.fixture.reads.filter(url=>url.endsWith('/models')).length),1);
 await card.locator('.sdk-model-section .sdk-model-picker select').selectOption('another-model');
 assert.match(await card.locator('.sdk-model-row').innerText(),locale==='zh'?/历史结果待承接/:/Historical result awaits/);
 assert.equal(await card.getByRole('button',{name:locale==='zh'?'检查兼容性':'Check compatibility',exact:true}).isDisabled(),true);
 assert.equal(await page.evaluate(()=>window.fixture.writes.length),0);
 await page.screenshot({path:path.join(out,`${locale}-${width}-pending-review.png`),fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await card.locator('.sdk-model-section .sdk-model-picker select').selectOption('deepseek-v4.1-flash');
 await page.screenshot({path:path.join(out,`${locale}-${width}.png`),fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 const choose=async provider=> width<=820 ? page.locator('[data-runtime-picker]').selectOption(provider) : page.locator(`[data-runtime-tab="${provider}"]`).click();
 const input=card.locator('.sdk-model-section .sdk-model-picker select');
 await input.selectOption('another-model');
 assert.equal(await input.locator('option[value="__custom__"]').count(),0);
 assert.equal(await card.locator('.sdk-model-section input').count(),0);
 await choose('codex'); await page.locator('[data-provider-panel="codex"] .sdk-model-picker select').waitFor();
 await choose('workbuddy'); assert.equal(await input.inputValue(),'another-model');
 if(width<=820) await page.locator('[data-runtime-picker]').selectOption('reserved');
 else { await page.locator('.settings-other > summary').click(); await page.locator('[data-other-runtimes] button').click(); }
 const reserved=page.locator('[data-provider-panel="reserved"]');
 assert.equal(await reserved.locator('.settings-overview').isVisible(),false);
 assert.equal(await reserved.locator('.settings-unavailable').isVisible(),true);
 assert.equal(await page.evaluate(()=>window.fixture.reads.some(url=>url.endsWith('/reserved/models'))),false);
 await choose('workbuddy');
 await input.focus(); await page.locator('[data-reread]').click();
 assert.equal(await input.inputValue(),'another-model');
 await page.locator('[data-settings-section="history"]').click();
 await page.locator('[data-settings-pane="history"]').waitFor();
 const reads=await page.evaluate(()=>window.fixture.reads.filter(url=>url.endsWith('/models')).length);
 await page.locator('[data-settings-section="runtime"]').click(); assert.equal(await input.inputValue(),'another-model');
 assert.equal(await page.evaluate(()=>window.fixture.reads.filter(url=>url.endsWith('/models')).length),reads);
 await page.goBack(); await page.locator('[data-settings-pane="history"]').waitFor();
 await page.goForward(); await card.waitFor();
 assert.match(await page.locator('[data-language-link]').getAttribute('href'),/provider=workbuddy/);
 await page.evaluate(()=>{window.fixture.count=6;}); await page.locator('[data-reread]').click();
 await page.waitForFunction(()=>document.querySelectorAll('[data-runtime-tab]').length===3);
 if(width>820){ await page.locator('[data-runtime-more]').selectOption('provider-5'); assert.equal(await page.locator('[data-runtime-more]').inputValue(),'provider-5'); }
 else await page.locator('[data-runtime-picker]').selectOption('provider-5');
 await page.locator('[data-provider-panel="provider-5"]').waitFor();
 await choose('workbuddy');
 await page.evaluate(()=>{window.fixture.revision=2;}); await page.locator('[data-reread]').click();
 await card.locator('.settings-binding-warning').waitFor(); assert.equal(await input.inputValue(),'another-model'); assert.equal(await input.isDisabled(),true);
 await page.locator('[data-reread]').click(); assert.equal(await card.locator('.settings-binding-warning button').isEnabled(),true);
 if(locale==='zh' && width===1440){
  // Dirty-view recovery remains explicit, only GET after confirmation.
  page.once('dialog',dialog=>dialog.accept()); await card.locator('.settings-binding-warning button').click();
  await page.waitForFunction(()=>!document.querySelector('.settings-binding-warning'));
  await page.locator('[data-runtime-tab="codex"]').focus(); await page.keyboard.press('End');
  assert.equal(await page.locator('[data-runtime-tab="provider-2"]').getAttribute('aria-selected'),'true');
  await page.keyboard.press('Home'); assert.equal(await page.locator('[data-runtime-tab="codex"]').getAttribute('aria-selected'),'true');
  await page.evaluate(()=>{window.fixture.count=4;window.fixture.default=null;}); await page.locator('[data-reread]').click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-runtime-tab]').length===4);
  assert.match(await page.locator('[data-default-summary]').innerText(),/尚未设置/);
  await page.evaluate(()=>{window.fixture.unknown=true;}); await page.locator('[data-reread]').click();
  await page.waitForFunction(()=>document.querySelector('[data-runtime-tab="workbuddy"]')?.textContent.includes('待核对'));
  await choose('workbuddy'); await card.locator('button').filter({hasText:'启用 Runtime'}).waitFor();
  assert.equal(await card.getByRole('button',{name:'启用 Runtime',exact:true}).isDisabled(),true);
  // An authentication workflow can be followed from another SDK without cancellation.
  await page.evaluate(()=>{window.fixture.operation={operation_id:'synthetic_auth',status:'cleanup_pending'};});
  await page.locator('[data-reread]').click(); await choose('codex');
  await page.waitForFunction(()=>document.querySelector('[data-runtime-tab="workbuddy"]')?.textContent.includes('操作进行中'));
  await page.evaluate(()=>{window.fixture.failLists=true;}); await page.locator('[data-reread]').click();
  await page.waitForFunction(()=>document.querySelector('[data-sdk-alert]')?.dataset.tone==='error');
  assert.equal(await page.locator('[data-provider-panel="codex"]').isVisible(),true);
  await page.evaluate(()=>{window.fixture.failLists=false;window.fixture.operation=null;window.fixture.unknown=false;}); await page.locator('[data-reread]').click();
 }
 assert.deepEqual(await page.evaluate(()=>window.fixture.writes),[]);
 if(locale==='zh' && width===1440) {
  // Explicit user action against a synthetic rejecting API only. Navigation above is write-free.
  await page.reload();
  await page.locator('[data-runtime-tab="workbuddy"]').waitFor(); await choose('workbuddy');
  const check=card.locator('.sdk-model-section').getByRole('button',{name:'检查兼容性',exact:true});
  await check.waitFor(); await check.click();
  await page.waitForFunction(()=>document.querySelector('[data-runtime-tab="workbuddy"]')?.textContent.includes('需处理'));
  await choose('codex');
  assert.match(await page.locator('[data-runtime-tab="workbuddy"]').innerText(),/需处理/);
  const writes=await page.evaluate(()=>window.fixture.writes);
  assert.equal(writes.length,1); assert.equal(writes[0].method,'POST');
  assert.ok(writes[0].url.endsWith('/workbuddy/models/check'));
  const body=JSON.parse(writes[0].body); assert.equal(body.model_id,'deepseek-v4.1-flash'); assert.ok(body.requestId);
 }
 assert.deepEqual(errors,[]);
 results.push({locale,width,single_panel:true,lazy_models:true,input_preserved:true,history_and_language:true,six_providers:true,binding_change_guarded:true,no_writes:true,no_overflow:true});
 await context.close();
}
} finally {await browser.close();}
await writeFile(path.join(out,'report.json'),JSON.stringify(results,null,2)); console.log(JSON.stringify(results));
