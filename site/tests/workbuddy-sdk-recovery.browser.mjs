// Recovery settings UI only. All SDK, authentication and model APIs are synthetic.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('C:/Users/wenbi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.dirname(new URL(import.meta.url).pathname.replace(/^\/(\w:)/,'$1'));
const dist=process.env.SAPBA_BROWSER_DIST||path.resolve(root,'../dist');
const out=process.env.SAPBA_BROWSER_REPORT||path.resolve(root,'../../.local-data/browser-workbuddy-recovery');await mkdir(out,{recursive:true});
const live=process.env.SAPBA_BROWSER_LIVE_SITE==='1',origin=live?'http://127.0.0.1:4321':'http://127.0.0.1:4339';
const browser=await chromium.launch({headless:true,channel:'msedge'}),results=[];
try {
 for(const locale of ['zh','en'])for(const width of [1440,390])for(const scenario of (process.env.SAPBA_RECOVERY_SCENARIOS?.split(',') || ['success','lost','blocked','needs_review','followup'])) {
  const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage();const errors=[],denied=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',async dialog=>{
   assert.match(dialog.message(),/0\.3\.247/);assert.match(dialog.message(),/0\.3\.291/);assert.doesNotMatch(dialog.message(),/0\.3\.999/);
   assert.match(dialog.message(),locale==='zh'?/不新增模型调用/:/No additional model calls/);await dialog.accept();
  });
  await page.addInitScript(({scenario})=>{
   const parent='update_'+'a'.repeat(32),auth={status:'authenticated',authenticated:true,identity:'de***',revision:9,checked_at:'2026-10-27T00:00:00Z',
    authentication_context_status:'current',method:'browser',source:'official_cli',capabilities:{check:true},blockers:[]};
   const sdk={provider_id:'workbuddy',sdk_id:'codebuddy-agent-sdk',name:{zh:'WorkBuddy',en:'WorkBuddy'},provider_implemented:true,
    platform_supported:true,enabled:true,installed:true,current_version:'0.3.247',cli_version:'2.141.0',latest_version:'0.3.999',
    can_update:true,update_enabled:true,update_supported:true,version_check_supported:true,update_available:true,
    authenticated:true,authentication:auth,default_model_id:'hy4-preview-f',runtime_configuration_revision:27,environment_digest:'original',
    service_site:'china',service_site_revision:1,service_site_digest:'site',service_site_switch_allowed:false,
    status_refresh:{status:'ready',stale:false,running:false},blockers:[],update_status:{operation_id:parent,status:'failed',phase:'evidence_publication',
    target_version:'0.3.291',before_version:'0.3.247',started_at:'2026-10-27T08:16:37.344Z',updated_at:'2026-10-27T08:19:01.928Z',
    error:{code:'workbuddy_job_index_recovery_required',phase:'evidence_publication'},recovery:{available:scenario!=='blocked',
     parent_operation_id:parent,target_version:'0.3.291',...(scenario==='blocked'?{blocked_reason:'sdk_update_binding_changed'}:{})}}};
   if(scenario==='followup')sdk.update_status={...sdk.update_status,operation_id:'recovery_'+'c'.repeat(32),parent_operation_id:parent,status:'rolled_back',phase:'rolled_back',cleanup_complete:true,recovery:{...sdk.update_status.recovery,retry_of:'recovery_'+'c'.repeat(32)}};
   try{Object.assign(sdk,JSON.parse(sessionStorage.getItem('fixture-sdk')||'{}'));}catch{}
   window.fixture={posts:JSON.parse(sessionStorage.getItem('fixture-posts')||'[]'),stage(phase,status='running'){
    sdk.update_status={...sdk.update_status,phase,status,updated_at:new Date().toISOString(),error:undefined};
    if(status==='completed'){sdk.current_version='0.3.291';sdk.cli_version='2.200.0';sdk.environment_digest='candidate';sdk.update_available=false;sdk.update_status.outcome='activated';}
    if(status==='needs_review')sdk.update_status.error={code:'sdk_update_outcome_unconfirmed',phase};
    sessionStorage.setItem('fixture-sdk',JSON.stringify(sdk));
   }};
   window.fetch=async(url,options={})=>{
    const u=new URL(url,location.href),method=options.method||'GET';let data;
    if(method!=='GET'){
     window.fixture.posts.push(u.pathname+u.search);sessionStorage.setItem('fixture-posts',JSON.stringify(window.fixture.posts));
     if(u.pathname===`/api/system/sdks/codebuddy-agent-sdk/update-operations/${parent}/recover`){
      if(options.headers?.['X-SAPBA-Action']!=='sdk-update')throw Error('Missing confirmation');
      if(u.searchParams.get('expectedTargetVersion')!=='0.3.291')throw Error('Original target changed');
      if(u.searchParams.get('retryOf')!==(scenario==='followup'?'recovery_'+'c'.repeat(32):null))throw Error('Unexpected successor recovery');
      sdk.update_status={operation_id:'recovery_'+'b'.repeat(32),parent_operation_id:parent,status:'running',phase:'recovery_verification',
       target_version:'0.3.291',before_version:'0.3.247',started_at:new Date().toISOString(),updated_at:new Date().toISOString(),
       recovery:{available:false,parent_operation_id:parent,target_version:'0.3.291'}};
      sessionStorage.setItem('fixture-sdk',JSON.stringify(sdk));
      if(scenario==='lost')throw Error('Synthetic response loss');
      data={operation_id:sdk.update_status.operation_id,item:{...sdk,maintenance_kind:'online_recovery'},restart_required:false};
     }else throw Error('Unexpected mutation: '+u.pathname);
    }else if(u.pathname.endsWith('/models'))data={items:[],catalog_status:'ready',model_catalog_complete:true};
    else if(u.pathname.endsWith('/authentication'))data={authentication:auth};
    else if(u.pathname.endsWith('/free-query-history'))data={retention_days:30};
    else data={items:[sdk],default_provider_id:'workbuddy'};
    sessionStorage.setItem('fixture-sdk',JSON.stringify(sdk));return {ok:true,status:method==='POST'?202:200,json:async()=>data};
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
  const card=page.locator('[data-provider-panel=workbuddy]');await card.waitFor();const maintenance=card.locator('.settings-maintenance');
  const open=async()=>{if(!await maintenance.evaluate(n=>n.open))await maintenance.locator(':scope > summary').click();};await open();
  const section=card.locator('.settings-maintenance-versions'),recover=section.getByRole('button',{name:locale==='zh'?'继续完成更新':'Continue update',exact:true});
  assert.equal(await page.evaluate(()=>window.fixture.posts.length),0);
  if(scenario==='blocked') {
   assert.equal(await recover.isVisible(),false);assert.match(await section.innerText(),/sdk_update_binding_changed/);
   await page.reload();await card.waitFor();await open();assert.equal(await page.evaluate(()=>window.fixture.posts.length),0);
  } else {
   assert.equal(await recover.isEnabled(),true);await recover.click();
   await page.waitForFunction(()=>window.fixture.posts.length===1);
   await page.waitForFunction(()=>document.querySelector('.settings-maintenance-versions')?.textContent.includes(document.documentElement.lang.startsWith('zh')?'正在核对恢复依据':'Verifying recovery evidence'));
   assert.equal(await section.locator('.sdk-version-block strong').first().innerText(),'0.3.247');
   // Synthetic duplicate DOM event must not dispatch while the operation is active.
   await section.locator('.sdk-recovery-button').dispatchEvent('click');assert.equal(await page.evaluate(()=>window.fixture.posts.length),1);
   await page.reload();await card.waitFor();await open();assert.equal(await page.evaluate(()=>window.fixture.posts.length),1);
   for(const phase of ['target_audit','evidence_publication','environment_commit','account_publication']) {
    await page.evaluate(phase=>{window.fixture.stage(phase);document.dispatchEvent(new Event('visibilitychange'));},phase);
    const label={target_audit:['正在审核目标环境','Auditing target environment'],evidence_publication:['正在交接验证证据','Publishing verification evidence'],environment_commit:['正在提交环境切换','Committing environment switch'],account_publication:['正在发布账号状态','Publishing account state']}[phase][locale==='zh'?0:1];
    await page.waitForFunction(label=>document.querySelector('.settings-maintenance-versions')?.textContent.includes(label),label);
   }
   await page.evaluate(scenario=>{window.fixture.stage(scenario==='needs_review'?'reconciliation':'completed',scenario==='needs_review'?'needs_review':'completed');document.dispatchEvent(new Event('visibilitychange'));},scenario);
   if(scenario==='needs_review') {
    await page.waitForFunction(()=>document.querySelector('.settings-maintenance-versions')?.textContent.includes('sdk_update_outcome_unconfirmed'));
    await section.locator('.settings-maintenance-error > summary').click();assert.match(await section.innerText(),/reconciliation/);
    assert.match(await section.innerText(),locale==='zh'?/维护保护仍保留/:/Maintenance protection remains active/);
    assert.equal(await section.getByRole('button',{name:locale==='zh'?'更新 SDK':'Update SDK',exact:true}).isDisabled(),true);
    await page.reload();await card.waitFor();await open();assert.equal(await page.evaluate(()=>window.fixture.posts.length),1);
   } else await page.waitForFunction(()=>document.querySelector('.sdk-version-block strong')?.textContent==='0.3.291');
   assert.equal(await page.evaluate(()=>window.fixture.posts.length),1);
  }
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);assert.deepEqual(errors,[]);assert.deepEqual(denied,[]);
  await page.screenshot({path:path.join(out,`${locale}-${width}-${scenario}.png`),fullPage:true});
  results.push({locale,width,scenario,passed:true,served_frontend:live,real_installations:0,model_calls:0,SAP_calls:0});await context.close();
 }
 await writeFile(path.join(out,'report.json'),JSON.stringify({passed:true,results},null,2));console.log(JSON.stringify({passed:true,cases:results.length}));
}finally{await browser.close();}
