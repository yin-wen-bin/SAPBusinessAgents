// Built page + intercepted API. No software source, installer, SDK or SAP.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)(process.env.SAPBA_PLAYWRIGHT_MODULE||'playwright');
const dist=path.resolve(process.env.SAPBA_BROWSER_DIST),out=process.env.SAPBA_BROWSER_REPORT;
await mkdir(out,{recursive:true});const browser=await chromium.launch({headless:true,channel:'msedge'}),records=[];
try{
 for(const locale of ['zh','en'])for(const width of [1440,690,550,390])for(const lost of [false,true]){
  const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',dialog=>dialog.accept());
  await page.addInitScript(({lost})=>{
   window.fixture={posts:[],lost};
   const auth={status:'authenticated',authenticated:true,identity:'demo***',revision:9,checked_at:'2026-10-25',
    authentication_context_status:'current',method:'browser',source:'official_cli',capabilities:{check:true},blockers:[]};
   const sdk={provider_id:'workbuddy',sdk_id:'codebuddy-agent-sdk',name:{zh:'WorkBuddy',en:'WorkBuddy'},provider_implemented:true,
    platform_supported:true,enabled:true,installed:true,current_version:'1.0.0',cli_version:'2.0.0',latest_version:null,
    can_update:true,update_enabled:true,update_preparation_supported:true,update_activation_automatic:false,update_available:false,
    authenticated:true,authentication:auth,default_model_id:'glm-5.3',runtime_configuration_revision:1,environment_digest:'original-environment',
    service_site:'china',service_site_revision:1,service_site_digest:'site',service_site_switch_allowed:false,
    status_refresh:{status:'ready',stale:false,running:false},blockers:[]};
   window.fixture.controlledSwitch=()=>{sdk.environment_digest='candidate';sdk.current_version='1.1.0';sdk.cli_version='2.1.0';};
   window.fetch=async(url,options={})=>{
    const u=new URL(url,location.href),method=options.method||'GET';let data;
    if(method!=='GET'){
     window.fixture.posts.push(u.pathname+u.search);
     if(u.pathname.endsWith('/check')&&u.search==='?scope=versions'){
      sdk.latest_version='1.1.0';sdk.update_available=true;sdk.version_check={status:'checked',checked_at:'2026-10-25T01:00:00Z'};data={item:{...sdk}};
     }else if(u.pathname.endsWith('/codebuddy-agent-sdk/update')){
      sdk.update_status={operation_id:'fixture-update',status:'needs_review',outcome:'candidate_prepared',phase:'version_confirmation',
       target_version:'1.1.0',started_at:new Date().toISOString(),updated_at:new Date().toISOString(),candidate_environment_digest:'candidate',error:{code:'workbuddy_update_evidence_required'}};
      if(window.fixture.lost)throw Error('synthetic response lost');
      data={item:{...sdk,maintenance_kind:'upgrade_candidate',activated:false},restart_required:false};
     }else throw Error('Unexpected mutation');
    }else if(u.pathname.endsWith('/models'))data={items:[],catalog_status:'ready',model_catalog_complete:true};
    else if(u.pathname.endsWith('/authentication'))data={authentication:auth};
    else if(u.pathname.endsWith('/free-query-history'))data={retention_days:30};
    else data={items:[sdk],default_provider_id:'workbuddy'};
    return {ok:true,json:async()=>data};
   };
  },{lost});
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());if(u.origin!=='http://127.0.0.1:4339')return route.abort();
   const clean=u.pathname.replace(/^\/SAPBusinessAgents\//,'/'),f=path.resolve(dist,(clean.endsWith('/')?clean+'index.html':clean).replace(/^\//,''));
   if(!f.startsWith(dist+path.sep))return route.abort();
   try{return route.fulfill({body:await readFile(f),contentType:f.endsWith('.html')?'text/html':f.endsWith('.css')?'text/css':'text/javascript'});}catch{return route.abort();}
  });
  await page.goto(`http://127.0.0.1:4339/${locale}/settings/`);
  const card=page.locator('[data-provider-panel="workbuddy"]');
  await card.locator('.settings-maintenance > summary').click();
  const versions=card.locator('.settings-maintenance-versions');
  assert.equal(await page.evaluate(()=>window.fixture.posts.length),0);
  await versions.getByRole('button',{name:locale==='zh'?'检查可用版本':'Check available versions',exact:true}).click();
  const prepare=versions.getByRole('button',{name:locale==='zh'?'准备SDK升级':'Prepare SDK upgrade',exact:true});
  await prepare.click();
  await versions.getByText(locale==='zh'?'候选环境已准备，尚未切换；需审核目标接入认证、账号及模型兼容证据。':'Candidate prepared, not activated. Target integration, account and model evidence must be reviewed.',{exact:true}).first().waitFor();
  assert.equal(await prepare.isDisabled(),true);
  assert.equal(await page.evaluate(()=>window.fixture.posts.length),2);
  assert.equal(await page.evaluate(()=>window.fixture.posts.some(p=>/authentication|models/.test(p))),false);
  assert.match(await versions.innerText(),/1\.0\.0/);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
  await page.screenshot({path:path.join(out,`${locale}-${width}-${lost?'lost':'success'}.png`),fullPage:true});
  await page.evaluate(()=>window.fixture.controlledSwitch());
  await page.getByRole('button',{name:locale==='zh'?'重新读取状态':'Read status again',exact:true}).first().click();
  await page.waitForFunction(()=>document.querySelector('.settings-maintenance-versions .sdk-version-block strong')?.textContent==='1.1.0');
  assert.equal(await page.evaluate(()=>window.fixture.posts.length),2);
  records.push({locale,width,lost,candidate_only:true,posts:2,no_auth_model_SAP:true});await context.close();
 }
 await writeFile(path.join(out,'report.json'),JSON.stringify(records,null,2));console.log(JSON.stringify({passed:records.length,real_install_calls:0,model_SAP_calls:0}));
}finally{await browser.close();}
