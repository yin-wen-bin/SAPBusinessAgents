// Built/current settings UI. All business, registry and maintenance APIs are synthetic.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('C:/Users/wenbi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.dirname(new URL(import.meta.url).pathname.replace(/^\/(\w:)/,'$1'));
const dist=process.env.SAPBA_BROWSER_DIST||path.resolve(root,'../dist');
const out=process.env.SAPBA_BROWSER_REPORT||path.resolve(root,'../../.local-data/browser-workbuddy-online-update');await mkdir(out,{recursive:true});
const live=process.env.SAPBA_BROWSER_LIVE_SITE==='1',origin=live?'http://127.0.0.1:4321':'http://127.0.0.1:4339';
const browser=await chromium.launch({headless:true,channel:'msedge'}),results=[];
try {
 for(const locale of ['zh','en'])for(const width of [1440,390])for(const scenario of ['success','lost','model_failure','unsupported']) {
  const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage();const errors=[],denied=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',async dialog=>{assert.match(dialog.message(),/1\.0\.0/);assert.match(dialog.message(),/1\.1\.0/);assert.match(dialog.message(),/glm-5\.3/);await dialog.accept();});
  await page.addInitScript(({scenario})=>{
   const auth={status:'authenticated',authenticated:true,identity:'de***',revision:9,checked_at:'2026-10-27T00:00:00Z',
    authentication_context_status:'current',method:'browser',source:'official_cli',capabilities:{check:true},blockers:[]};
   const sdk={provider_id:'workbuddy',sdk_id:'codebuddy-agent-sdk',name:{zh:'WorkBuddy',en:'WorkBuddy'},provider_implemented:true,
    platform_supported:true,enabled:true,installed:true,current_version:'1.0.0',cli_version:'2.0.0',latest_version:null,
    can_update:true,update_enabled:scenario!=='unsupported',update_supported:scenario!=='unsupported',version_check_supported:true,
    update_preparation_supported:scenario!=='unsupported',update_activation_automatic:true,update_available:false,
    authenticated:true,authentication:auth,default_model_id:'glm-5.3',runtime_configuration_revision:1,environment_digest:'original',
    service_site:'china',service_site_revision:1,service_site_digest:'site',service_site_switch_allowed:false,
    status_refresh:{status:'ready',stale:false,running:false},blockers:[]};
   try{Object.assign(sdk,JSON.parse(sessionStorage.getItem('fixture-sdk')||'{}'));}catch{}
   window.fixture={posts:JSON.parse(sessionStorage.getItem('fixture-posts')||'[]'),stage(phase,status='running'){
    sdk.update_status={...sdk.update_status,phase,status,updated_at:new Date().toISOString()};
    if(status==='completed'){sdk.current_version='1.1.0';sdk.cli_version='2.1.0';sdk.environment_digest='candidate';sdk.update_available=false;sdk.update_status.outcome='activated';}
    if(status==='failed')sdk.update_status.error={code:'workbuddy_update_model_failed',phase};
    sessionStorage.setItem('fixture-sdk',JSON.stringify(sdk));
   }};
   window.fetch=async(url,options={})=>{
    const u=new URL(url,location.href),method=options.method||'GET';let data;
    if(method!=='GET'){
     window.fixture.posts.push(u.pathname+u.search);sessionStorage.setItem('fixture-posts',JSON.stringify(window.fixture.posts));
     if(u.pathname.endsWith('/check')&&u.search==='?scope=versions'){
      sdk.latest_version='1.1.0';sdk.update_available=true;sdk.version_check={status:'checked',checked_at:new Date().toISOString()};data={item:{...sdk}};
     }else if(u.pathname.endsWith('/codebuddy-agent-sdk/update')){
      if(options.headers?.['X-SAPBA-Action']!=='sdk-update')throw Error('Missing confirmation');
      if(u.searchParams.get('expectedTargetVersion')!=='1.1.0')throw Error('Missing frozen target');
      sdk.update_status={operation_id:'update_'+'a'.repeat(32),status:'running',phase:'downloading',
       target_version:'1.1.0',before_version:'1.0.0',started_at:new Date().toISOString(),updated_at:new Date().toISOString()};
      sessionStorage.setItem('fixture-sdk',JSON.stringify(sdk));
      if(scenario==='lost')throw Error('Synthetic response loss');
      data={operation_id:sdk.update_status.operation_id,item:{...sdk,maintenance_kind:'online_upgrade'},restart_required:false};
     }else throw Error('Unexpected mutation: '+u.pathname);
    }else if(u.pathname.endsWith('/models'))data={items:[],catalog_status:'ready',model_catalog_complete:true};
    else if(u.pathname.endsWith('/authentication'))data={authentication:auth};
    else if(u.pathname.endsWith('/free-query-history'))data={retention_days:30};
    else data={items:[sdk],default_provider_id:'workbuddy'};
    sessionStorage.setItem('fixture-sdk',JSON.stringify(sdk));
    return {ok:true,status:method==='POST'&&u.pathname.endsWith('/update')?202:200,json:async()=>data};
   };
  },{scenario});
  await context.route('**/*',async route=>{
   const u=new URL(route.request().url());if(u.origin!==origin){denied.push(u.origin);return route.abort();}
   if(live)return route.continue();
   const relative=u.pathname.replace(/^\/(?:SAPBusinessAgents\/)?/,'');const file=path.resolve(dist,relative.endsWith('/')?relative+'index.html':relative);
   if(!file.startsWith(path.resolve(dist)+path.sep))return route.abort();
   try{return route.fulfill({body:await readFile(file),contentType:file.endsWith('.html')?'text/html':file.endsWith('.css')?'text/css':'text/javascript'});}
   catch{return route.fulfill({status:404,body:''});}
  });
  await page.goto(`${origin}/${locale}/settings/?section=runtime&provider=workbuddy`);
  const card=page.locator('[data-provider-panel=workbuddy]');await card.waitFor();
  const maintenance=card.locator('.settings-maintenance');if(!await maintenance.evaluate(n=>n.open))await maintenance.locator(':scope > summary').click();
  const section=card.locator('.settings-maintenance-versions');
  const versions=section.getByRole('button',{name:locale==='zh'?'检查可用版本':'Check available versions',exact:true});
  assert.equal(await versions.isEnabled(),true);assert.equal(await page.evaluate(()=>window.fixture.posts.length),0);
  await versions.click();await section.locator('.sdk-version-block strong').filter({hasText:'1.1.0'}).waitFor();
  if(scenario!=='unsupported') {
   const update=section.getByRole('button',{name:locale==='zh'?'更新 SDK':'Update SDK',exact:true});
   await update.click();
   await page.waitForFunction(()=>window.fixture.posts.some(p=>p.includes('/update?')));
   if(scenario!=='lost')await page.waitForFunction(()=>document.querySelector('.settings-maintenance-versions')?.textContent.includes(document.documentElement.lang.startsWith('zh')?'正在下载':'Downloading'));
   await page.reload();await card.waitFor();if(!await maintenance.evaluate(n=>n.open))await maintenance.locator(':scope > summary').click();
   assert.equal(await page.evaluate(()=>window.fixture.posts.length),2,'Reload must not replay a POST');
   // Init scripts create a fresh server fixture on navigation; rehydrate the owned server job without a POST.
   await page.evaluate(()=>{window.fixture.stage('model_verification');document.dispatchEvent(new Event('visibilitychange'));});
   await page.waitForFunction(()=>document.querySelector('.settings-maintenance-versions')?.textContent.includes(document.documentElement.lang.startsWith('zh')?'仅一次':'once'));
   await page.evaluate(scenario=>{window.fixture.stage(scenario==='model_failure'?'model_verification':'completed',scenario==='model_failure'?'failed':'completed');document.dispatchEvent(new Event('visibilitychange'));},scenario);
   if(scenario==='model_failure') {
    await page.waitForFunction(()=>document.querySelector('.settings-maintenance-versions')?.textContent.includes('workbuddy_update_model_failed'));
    await section.locator('.settings-maintenance-error > summary').click();
    assert.equal(await section.locator('.sdk-version-block strong').first().innerText(),'1.0.0');
   }else await page.waitForFunction(()=>document.querySelector('.sdk-version-block strong')?.textContent==='1.1.0');
  } else assert.equal(await section.getByRole('button',{name:locale==='zh'?'更新 SDK':'Update SDK',exact:true}).isVisible(),false);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  assert.deepEqual(errors,[]);assert.deepEqual(denied,[]);
  await page.screenshot({path:path.join(out,`${locale}-${width}-${scenario}.png`),fullPage:true});
  results.push({locale,width,scenario,passed:true,served_frontend:live,real_installations:0,model_calls:0,SAP_calls:0});
  await context.close();
 }
 await writeFile(path.join(out,'report.json'),JSON.stringify({passed:true,results},null,2));console.log(JSON.stringify({passed:true,cases:results.length}));
}finally{await browser.close();}
