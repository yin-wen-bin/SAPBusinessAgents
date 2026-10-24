// Real rendered page, all API responses synthetic; every non-static request is blocked.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)(process.env.SAPBA_PLAYWRIGHT_MODULE || 'playwright');
const dist=path.resolve(process.env.SAPBA_BROWSER_DIST || 'dist');
const out=process.env.SAPBA_BROWSER_REPORT; assert.ok(out); await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined});
const results=[];
try {
 for(const locale of ['zh','en']) for(const width of [1440,1024,690,550,390]) {
  const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{
   const auth={status:'authenticated',authenticated:true,identity:'demo***',revision:1,method:'browser',source:'official_cli',checked_at:'2026-10-20T00:00:00Z',capabilities:{check:true,login:true,logout:true,switch_account:true},blockers:[],operation:null};
   window.fixture={phase:'pending',reads:[],writes:[],unknown:false};
   window.fetch=async(url,options={})=>{
    const f=window.fixture,u=new URL(url,location.href),method=options.method||'GET';
    if(method!=='GET'){f.writes.push({url,method});throw new Error('Unexpected write');}
    f.reads.push(u.pathname+u.search);let data;
    const display={snapshot_id:f.phase==='ready'?2:1,instance_id:'synthetic-boot',binding:'same-config',stale:false,running:false};
    const refresh={status:f.phase==='ready'?'ready':'unknown',stale:f.phase!=='ready',running:false,checked_at:'2026-10-20T01:00:00Z'};
    const model=(id,selected)=>({model_id:id,display_name:id,selected,actual_model:id,check_status:'compatible',last_confirmed:f.phase!=='ready',selectable:f.phase==='ready',display_pending:f.phase!=='ready',check:{compatible:true,check_digest:'proof'}});
    if(u.pathname.endsWith('/free-query-history'))data={retention_days:30};
    else if(u.pathname.endsWith('/authentication'))data={authentication:auth};
    else if(u.pathname.endsWith('/models'))data={provider_id:'workbuddy',items:[model('sdk-a',true),model('sdk-b',false)],display_status:display,status_refresh:refresh,catalog_digest:'catalog',environment_digest:'env',service_site_digest:'site'};
    else if(u.pathname.endsWith('/sdk-runtimes')) {
     const base={provider_implemented:true,platform_supported:true,enabled:true,installed:true,current_version:'synthetic',authenticated:true,authentication:auth,can_enable:true,selectable:true,model_discovery_supported:true,model_check_supported:true,status_refresh:{status:'ready',stale:false,running:false,checked_at:'2026-10-20T00:00:00Z'},blockers:[],capabilities:[]};
     const wb={...base,provider_id:'workbuddy',sdk_id:'codebuddy-agent-sdk',name:{zh:'WorkBuddy',en:'WorkBuddy'},default_model_id:'sdk-a',environment_digest:'env',runtime_configuration_revision:1,service_site:'china',service_site_revision:1,service_site_digest:'site',service_site_switch_allowed:false,display_status:display,status_refresh:refresh,control_status:{status:'ready',revision:5},authentication:{...auth,display_only:f.phase!=='ready',capabilities:f.phase==='ready'?auth.capabilities:{}},authenticated:f.phase==='ready'?true:null,selectable:f.phase==='ready',can_enable:f.phase==='ready'};
     data={items:[{...base,provider_id:'codex',sdk_id:'codex',name:{zh:'Codex',en:'Codex'},default_model_id:'codex-model'},wb],default_provider_id:'workbuddy'};
    } else throw new Error('Unexpected API read: '+u.pathname);
    return {ok:true,json:async()=>data};
   };
  });
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());if(u.origin!=='http://127.0.0.1:4339')return route.abort();
   const clean=u.pathname.replace(/^\/SAPBusinessAgents\//,'/');
   const file=path.resolve(dist,(clean.endsWith('/')?clean+'index.html':clean).replace(/^\//,''));
   if(!file.startsWith(dist+path.sep))return route.abort();
   try{return route.fulfill({body:await readFile(file),contentType:file.endsWith('.html')?'text/html':file.endsWith('.css')?'text/css':'text/javascript'});}catch{return route.abort();}
  });
  const start=Date.now();await page.goto(`http://127.0.0.1:4339/${locale}/settings/`);
  const card=page.locator('[data-provider-panel="workbuddy"]'),picker=card.locator('.sdk-model-section .sdk-model-picker select');
  await picker.waitFor();const firstContentMs=Date.now()-start;
  assert.deepEqual(await picker.locator('option').evaluateAll(options=>options.map(o=>o.value)),['sdk-a','sdk-b']);
  assert.match(await card.innerText(),locale==='zh'?/完整状态核对中/:/full status/i);
  assert.equal(await card.getByRole('button',{name:locale==='zh'?'检查兼容性':'Check compatibility',exact:true}).isDisabled(),true);
  assert.equal(await card.locator('[data-auth-action="check"]').isDisabled(),true);
  assert.match(await card.innerText(),locale==='zh'?/上次兼容通过/:/Last confirmed compatible/);
  await picker.selectOption('sdk-b');await picker.focus();
  await page.evaluate(()=>{window.keptPicker=document.querySelector('[data-provider-panel="workbuddy"] .sdk-model-section .sdk-model-picker select');window.fixture.phase='ready';});
  await page.waitForFunction(()=>document.querySelector('[data-provider-panel="workbuddy"]')?.innerText.includes('显示已保存目录') || document.querySelector('[data-provider-panel="workbuddy"]')?.innerText.includes('Showing the saved catalog'));
  assert.equal(await picker.inputValue(),'sdk-b');
  assert.equal(await page.evaluate(()=>window.keptPicker===document.querySelector('[data-provider-panel="workbuddy"] .sdk-model-section .sdk-model-picker select')),true);
  assert.equal(await page.evaluate(()=>document.activeElement===window.keptPicker),true);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.equal(await page.evaluate(()=>window.fixture.writes.length),0);
  assert.equal(await page.evaluate(()=>window.fixture.reads.some(url=>url.endsWith('/authentication'))),false);
  assert.equal(await card.locator('[data-auth-action="check"]').isDisabled(),false);
  assert.equal(await page.evaluate(()=>window.fixture.reads.some(url=>url==='/api/system/sdk-runtimes/workbuddy/models')),false);
  assert.ok(await page.evaluate(()=>window.fixture.reads.includes('/api/system/sdk-runtimes/workbuddy/models?view=summary')));
  await page.screenshot({path:path.join(out,`${locale}-${width}.png`),fullPage:true});
  assert.deepEqual(errors,[]);
  results.push({locale,width,firstContentMs,pickerPreserved:true,writes:0,fullModelsGet:0});await context.close();
 }
 await writeFile(path.join(out,'report.json'),JSON.stringify({cases:results,model_calls:0,SAP_calls:0},null,2));
 console.log(JSON.stringify({passed:results.length,model_calls:0,SAP_calls:0}));
} finally {await browser.close();}
