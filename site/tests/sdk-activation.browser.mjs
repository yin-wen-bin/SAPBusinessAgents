// Offline: immutable build files and synthetic API only, no server or SDK.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('C:/Users/wenbi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const dist=path.resolve(process.env.SAPBA_BROWSER_DIST),out=path.resolve(process.env.SAPBA_BROWSER_REPORT);
await mkdir(out,{recursive:true});
const origin='http://127.0.0.1:4339',browser=await chromium.launch({headless:true,channel:'msedge'}),results=[];
try {
 for(const provider of ['codex','workbuddy'])for(const locale of ['zh','en'])for(const width of [1440,690,550,390])for(const lost of [false,true]) {
  const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage();const errors=[],denied=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',async dialog=>{assert.match(dialog.message(),/0\.162\.0/);assert.match(dialog.message(),/0\.163\.0/);await dialog.accept();});
  await page.addInitScript(({lost,provider})=>{
   const auth={status:'authenticated',authenticated:true,identity:'fixture',revision:9,authentication_context_status:'current',method:'browser',source:'official_cli',capabilities:{check:true},blockers:[]};
   const sdk={provider_id:provider,sdk_id:provider==='codex'?'codex-python-sdk':'codebuddy-agent-sdk',name:{zh:provider,en:provider},provider_implemented:true,platform_supported:true,
    enabled:true,installed:true,current_version:'0.162.0',cli_version:'0.162.0',latest_version:'0.163.0',can_update:true,
    update_enabled:true,update_supported:true,version_check_supported:true,update_available:true,authentication:auth,authenticated:true,
    service_site:'china',service_site_revision:1,service_site_digest:'fixture-site',service_site_switch_allowed:false,
    default_model_id:'fixture-model',status_refresh:{status:'ready',stale:false,running:false},blockers:[]};
   try{Object.assign(sdk,JSON.parse(sessionStorage.getItem('sdk')||'{}'));}catch{}
   window.fixture={posts:JSON.parse(sessionStorage.getItem('posts')||'[]'),activeReads:0,offline:false,stage(phase,status='running'){
    sdk.update_status={...sdk.update_status,phase,status,updated_at:new Date().toISOString(),error:undefined};
    if(phase==='awaiting_review'){sdk.update_status.outcome='candidate_prepared';sdk.update_status.error={code:'sdk_target_evidence_required',phase};}
    if(phase==='activated'){sdk.current_version='0.163.0';sdk.cli_version='0.163.0';sdk.update_available=false;sdk.update_status.outcome='activated';sdk.update_status.cleanup_complete=true;sdk.update_status.observed_versions={sdk:'0.163.0'};}
    sessionStorage.setItem('sdk',JSON.stringify(sdk));
   }};
   window.fetch=async(url,options={})=>{
    const u=new URL(url,location.href),method=options.method||'GET';let data;
    if(method!=='GET') {
     if(u.pathname!==`/api/system/sdks/${sdk.sdk_id}/update`||options.headers?.['X-SAPBA-Action']!=='sdk-update')throw Error('Unexpected mutation');
     if(u.searchParams.get('expectedTargetVersion')!=='0.163.0'||!u.searchParams.get('requestId'))throw Error('Missing frozen request');
     window.fixture.posts.push(u.pathname+u.search);sessionStorage.setItem('posts',JSON.stringify(window.fixture.posts));
     sdk.update_status={operation_id:'update_'+'a'.repeat(32),request_id:u.searchParams.get('requestId'),status:'running',phase:'environment_creation',target_version:'0.163.0',before_version:'0.162.0',started_at:new Date().toISOString(),updated_at:new Date().toISOString()};
     sessionStorage.setItem('sdk',JSON.stringify(sdk));
     if(lost)throw Error('Synthetic response loss');
     data={operation_id:sdk.update_status.operation_id,item:{...sdk,maintenance_kind:'online_upgrade',activated:false},restart_required:false};
    } else if(u.pathname.includes('/sdk-runtimes?') || u.pathname==='/api/system/sdk-runtimes') {
      if(window.fixture.offline)throw Error('Synthetic API restart');
      if(sdk.update_status?.outcome==='activated' && ++window.fixture.activeReads===2)throw Error('Synthetic display refresh failure');
      data={items:[sdk],default_provider_id:provider};
    } else if(u.pathname.endsWith('/models'))data={items:[],catalog_status:'ready',model_catalog_complete:true};
    else if(u.pathname.endsWith('/authentication'))data={authentication:auth};
    else if(u.pathname.endsWith('/free-query-history'))data={retention_days:30};
    else data={items:[sdk],default_provider_id:'codex'};
    return {ok:true,status:method==='POST'?202:200,json:async()=>data};
   };
  },{lost,provider});
  await context.route('**/*',async route=>{
   const u=new URL(route.request().url());if(u.origin!==origin){denied.push(u.origin);return route.abort();}
   const file=path.resolve(dist,u.pathname.replace(/^\//,'')+(u.pathname.endsWith('/')?'index.html':''));
   if(!file.startsWith(dist+path.sep))return route.abort();
   try{return route.fulfill({body:await readFile(file),contentType:file.endsWith('.html')?'text/html':file.endsWith('.css')?'text/css':'text/javascript'});}
   catch{return route.fulfill({status:404,body:''});}
  });
  await page.goto(`${origin}/${locale}/settings/?section=runtime&provider=${provider}`);
  const card=page.locator(`[data-provider-panel=${provider}]`),maintenance=card.locator('.settings-maintenance');await card.waitFor();
  const open=async()=>{if(!await maintenance.evaluate(n=>n.open))await maintenance.locator(':scope > summary').click();};await open();
  const section=card.locator('.settings-maintenance-versions'),button=section.locator('.sdk-update-button:not(.sdk-recovery-button)');
  const readCurrent=async()=>{const tab=page.locator(`[data-runtime-tab="${provider}"]`);if(await tab.isVisible())await tab.click();else await page.locator('[data-runtime-picker]').selectOption(provider);};
  assert.equal(await page.evaluate(()=>window.fixture.posts.length),0);await button.click();
  await page.waitForFunction(()=>window.fixture.posts.length===1);
  const modal=page.locator('.sdk-update-progress-dialog');await modal.waitFor({state:'visible'});
  await button.dispatchEvent('click');assert.equal(await page.evaluate(()=>window.fixture.posts.length),1);
  await modal.locator('footer button').first().click();await modal.waitFor({state:'hidden'});
  await page.reload();await card.waitFor();await open();
  assert.equal(await section.locator('.sdk-version-block strong').first().innerText(),'0.162.0');
  if(lost){
   await page.evaluate(()=>{window.fixture.offline=true;});
   await section.locator('.sdk-update-feedback-entry').click();
   await page.waitForFunction(text=>document.querySelector('.sdk-update-progress-state')?.textContent.includes(text),locale==='zh'?'重新连接':'Reconnecting');
   await modal.locator('footer button').first().click();
   await page.evaluate(()=>{window.fixture.offline=false;});
  }
  for(const [phase,status,zh,en] of [
   ['waiting_maintenance','running','等待任务','Waiting for tasks'],
   ['awaiting_review','needs_review','尚未生效','not active'],
   ['switching','running','切换','Activating'],
   ['cleanup','cleanup_pending','清理','Cleaning'],
   ['activated','completed','已生效','is active']]) {
   await page.evaluate(({phase,status})=>{window.fixture.stage(phase,status);},{phase,status});
   if(status==='completed')await section.locator('.sdk-update-feedback-entry').click();else await readCurrent();
   await page.waitForFunction(text=>document.querySelector('.settings-maintenance-versions')?.textContent.includes(text),locale==='zh'?zh:en);
   if(status!=='completed')assert.equal(await section.locator('.sdk-version-block strong').first().innerText(),'0.162.0');
   if(phase==='awaiting_review')await page.screenshot({path:path.join(out,`${provider}-${locale}-${width}-${lost?'lost':'accepted'}.png`),fullPage:true});
  }
  assert.equal(await section.locator('.sdk-version-block strong').first().innerText(),'0.163.0');
  await modal.waitFor({state:'visible'});
  await page.waitForFunction(text=>document.querySelector('.sdk-update-progress-state')?.textContent.includes(text),locale==='zh'?'已生效':'is active');
  await page.waitForFunction(text=>document.querySelector('.sdk-update-progress-dialog')?.textContent.includes(text),locale==='zh'?'页面信息读取失败':'page information');
  await modal.locator('footer button').nth(1).click();
  await page.screenshot({path:path.join(out,`${provider}-${locale}-${width}-${lost?'lost':'accepted'}-dialog.png`),fullPage:true});
  assert.equal(await page.evaluate(()=>window.fixture.posts.length),1);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,`${provider}/${locale}/${width}/${lost}: horizontal overflow`);
  assert.deepEqual(errors,[]);assert.deepEqual(denied,[]);
  results.push({provider,locale,width,lost,passed:true,real_installs:0,real_API:0});await context.close();
 }
 await writeFile(path.join(out,'report.json'),JSON.stringify({passed:true,results},null,2));console.log(JSON.stringify({passed:true,cases:results.length}));
}finally{await browser.close();}
