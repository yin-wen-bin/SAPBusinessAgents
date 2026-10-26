// Actual built UI; every API response is synthetic, all remote requests blocked.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)(process.env.SAPBA_PLAYWRIGHT_MODULE || 'playwright');
const dist=path.resolve(process.env.SAPBA_BROWSER_DIST),out=process.env.SAPBA_BROWSER_REPORT;
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'msedge'}),records=[];
try {
 for(const locale of ['zh','en'])for(const width of [1440,690,550,390]){
  const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{
   window.fixture={phase:'checking',reads:[],writes:[]};
   window.fetch=async(url,options={})=>{
    const f=window.fixture,u=new URL(url,location.href);
    if((options.method||'GET')!=='GET'){f.writes.push(url);throw Error('No writes permitted');}
    f.reads.push(u.pathname);const ready=f.phase==='confirmed';
    const auth={status:ready?'authenticated':'not_checked',authenticated:ready?true:null,identity:'test***',revision:9,service_site_digest:'site',last_successful_check:'2026-10-24T00:00:00Z',checked_at:'2026-10-24T00:00:00Z',authentication_context_status:ready?'current':'check_required',failure_code:ready?null:'runtime_authentication_check_required',authentication_sync:{status:f.phase},capabilities:{check:true,login:true,logout:true,switch_account:true},blockers:[],operation:null};
    const sdk={provider_id:'workbuddy',sdk_id:'workbuddy',name:{zh:'WorkBuddy',en:'WorkBuddy'},provider_implemented:true,platform_supported:true,enabled:true,installed:true,authenticated:auth.authenticated,authentication:auth,default_model_id:'glm-5.3',runtime_configuration_revision:1,environment_digest:'env',service_site:'china',service_site_digest:'site',service_site_revision:1,can_enable:false,selectable:false,model_discovery_supported:true,status_refresh:{status:'ready',stale:false,running:false},display_status:{snapshot_id:1,instance_id:'test',binding:'same',stale:false,running:false},control_status:{status:'ready',revision:1},blockers:[]};
    let data;if(u.pathname.endsWith('/authentication'))data={authentication:auth};
    else if(u.pathname.endsWith('/models'))data={items:[],catalog_status:'ready',model_catalog_complete:true};
    else if(u.pathname.endsWith('/free-query-history'))data={retention_days:30};
    else data={items:[sdk],default_provider_id:'workbuddy'};
    return {ok:true,json:async()=>data};
   };
  });
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());if(u.origin!=='http://127.0.0.1:4339')return route.abort();
   const clean=u.pathname.replace(/^\/SAPBusinessAgents\//,'/');
   const f=path.resolve(dist,(clean.endsWith('/')?clean+'index.html':clean).replace(/^\//,''));
   if(!f.startsWith(dist+path.sep))return route.abort();
   try{return route.fulfill({body:await readFile(f),contentType:f.endsWith('.html')?'text/html':f.endsWith('.css')?'text/css':'text/javascript'});}catch{return route.abort();}
  });
  await page.goto(`http://127.0.0.1:4339/${locale}/settings/`);
  const section=page.locator('.sdk-authentication-section');
  await section.getByText(locale==='zh'?'认证待核对（不是已退出）':'Verification pending (not signed out)',{exact:true}).waitFor();
  assert.equal(await section.locator('[data-auth-action="login"]').count(),0);
  await page.screenshot({path:path.join(out,`${locale}-${width}-pending.png`),fullPage:true});
  await page.evaluate(()=>window.fixture.phase='confirmed');
  await section.getByText(locale==='zh'?'已认证':'Authenticated',{exact:true}).waitFor();
  const reads=await page.evaluate(()=>window.fixture.reads.filter(x=>x.endsWith('/authentication')).length);
  await page.waitForTimeout(2400);
  assert.equal(await page.evaluate(()=>window.fixture.reads.filter(x=>x.endsWith('/authentication')).length),reads);
  assert.equal(await page.evaluate(()=>window.fixture.writes.length),0);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
  records.push({locale,width,pending_not_signed_out:true,automatic_settlement:true,polling_stopped:true,writes:0});
  await context.close();
 }
 await writeFile(path.join(out,'report.json'),JSON.stringify(records,null,2));console.log(JSON.stringify({passed:records.length,model_calls:0,SAP_calls:0}));
}finally{await browser.close();}
