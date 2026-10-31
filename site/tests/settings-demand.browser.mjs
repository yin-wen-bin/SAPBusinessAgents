// Offline fixture browser: all fetches and navigation are intercepted.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('C:/Users/wenbi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const dist=path.resolve(process.env.SAPBA_BROWSER_DIST),out=path.resolve(process.env.SAPBA_BROWSER_REPORT);
await mkdir(out,{recursive:true});
const origin='http://127.0.0.1:4339',browser=await chromium.launch({headless:true,channel:'msedge'}),results=[];
try{
 for(const locale of ['zh','en'])for(const width of [1440,690,642,550,390]){
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage(),errors=[],denied=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.clock.install({time:new Date('2026-10-29T00:00:00Z')});
  await page.addInitScript(()=>{
   const auth={status:'authenticated',authenticated:true,identity:'fixture',revision:1,capabilities:{check:true},blockers:[]};
   const sdks=['codex','workbuddy'].map(provider=>({provider_id:provider,sdk_id:provider,name:{zh:provider,en:provider},
    provider_implemented:true,platform_supported:true,enabled:true,installed:true,authenticated:true,authentication:auth,
    current_version:'1.0.0',default_model_id:'model-a',model_discovery_supported:true,model_check_supported:true,
    status_refresh:{status:'ready',running:false,stale:false},display_status:provider==='workbuddy'?{instance_id:'fixture',snapshot_id:1,stale:false}:undefined,
    environment_digest:'original-environment',service_site_digest:'china',blockers:[]}));
   const model=id=>({model_id:id,display_name:id,selected:id==='model-a',check_status:'compatible',selectable:true,
    actual_model:id,supported_reasoning_efforts:['low','high'],reasoning_effort:'high'});
   window.fixture={gets:[],writes:[],pending:false};
   window.fetch=async(url,options={})=>{
    const u=new URL(url,location.href),method=options.method||'GET';
    if(method!=='GET'){window.fixture.writes.push(u.pathname);throw Error('Unexpected mutation');}
    let data;
    if(u.pathname==='/api/system/sdk-runtimes'){
     const provider=u.searchParams.get('provider_id');window.fixture.gets.push(provider);
     data={default_provider_id:'codex',items:sdks.filter(item=>!provider||item.provider_id===provider).map(item=>({...item,
      status_refresh:item.provider_id==='workbuddy'&&window.fixture.pending?{status:'refreshing',running:true,stale:true}:item.status_refresh}))};
    }else if(u.pathname.endsWith('/models'))data={items:[model('model-a'),model('model-b')],catalog_status:'ready',model_catalog_complete:true,
      display_status:u.pathname.includes('workbuddy')?{instance_id:'fixture',snapshot_id:1,stale:false}:undefined};
    else if(u.pathname.endsWith('/authentication'))data={authentication:auth};
    else if(u.pathname.endsWith('/free-query-history'))data={retention_days:30};
    else throw Error('Unexpected API '+u.pathname);
    return {ok:true,status:200,json:async()=>structuredClone(data)};
   };
  });
  await context.route('**/*',async route=>{
   const u=new URL(route.request().url());if(u.origin!==origin){denied.push(u.origin);return route.abort();}
   const file=path.resolve(dist,u.pathname.replace(/^\//,'')+(u.pathname.endsWith('/')?'index.html':''));
   if(!file.startsWith(dist+path.sep))return route.abort();
   try{return route.fulfill({body:await readFile(file),contentType:file.endsWith('.html')?'text/html':file.endsWith('.css')?'text/css':'text/javascript'});}
   catch{return route.fulfill({status:404,body:''});}
  });
  await page.goto(`${origin}/${locale}/settings/?section=runtime&provider=workbuddy`);
  const card=page.locator('[data-provider-panel=workbuddy]');await card.waitFor();
  await page.locator('[data-runtime-picker] option').first().waitFor({state:'attached'});
  const choose=async provider=>{const tab=page.locator(`[data-runtime-tab="${provider}"]`);if(await tab.isVisible())await tab.click();else await page.locator('[data-runtime-picker]').selectOption(provider);};
  assert.equal(await page.locator('.settings-default-strip').count(),0);
  assert.equal(await page.locator('[data-default-summary]').count(),0);
  const picker=card.locator('.sdk-model-section .sdk-model-picker select');await picker.selectOption('model-b');
  await page.evaluate(()=>{window.savedInput=document.querySelector('[data-provider-panel=workbuddy] .sdk-model-section .sdk-model-picker select');});
  const before=await page.evaluate(()=>window.fixture.gets.length);
  await page.clock.runFor(600000);
  await page.evaluate(()=>{for(let i=0;i<8;i++)document.dispatchEvent(new Event('sapba:settings-activity'));document.dispatchEvent(new Event('visibilitychange'));});
  assert.equal(await page.evaluate(()=>window.fixture.gets.length),before);
  await choose('codex');await page.waitForFunction(()=>window.fixture.gets.at(-1)==='codex');
  await choose('workbuddy');await page.waitForFunction(()=>window.fixture.gets.at(-1)==='workbuddy');
  assert.equal(await picker.inputValue(),'model-b');
  assert.equal(await page.evaluate(()=>window.savedInput===document.querySelector('[data-provider-panel=workbuddy] .sdk-model-section .sdk-model-picker select')),true);
  assert.equal(await page.locator('[data-runtime-picker] option').count(),2);
  await page.evaluate(()=>{window.fixture.pending=true;});
  const pendingStart=await page.evaluate(()=>window.fixture.gets.length);await choose('workbuddy');
  await page.clock.runFor(10000);
  assert.equal(await page.evaluate(()=>window.fixture.gets.length),pendingStart+4);
  const stopped=await page.evaluate(()=>window.fixture.gets.length);await page.clock.runFor(600000);
  assert.equal(await page.evaluate(()=>window.fixture.gets.length),stopped);
  await page.evaluate(()=>{window.fixture.pending=false;});
  await card.getByRole('button',{name:locale==='zh'?'重试读取':'Retry reading',exact:true}).click();
  assert.equal(await page.evaluate(()=>window.fixture.gets.length),stopped+1);
  const history=page.locator('[data-settings-section=history]');await history.click();
  await page.clock.runFor(600000);assert.equal(await page.evaluate(()=>window.fixture.gets.length),stopped+1);
  await page.locator('[data-settings-section=runtime]').click();
  assert.equal(await page.evaluate(()=>window.fixture.gets.length),stopped+2);
  assert.equal(await page.evaluate(()=>window.fixture.writes.length),0);
  assert.equal(await page.evaluate(()=>window.fixture.gets.filter(value=>value===null).length),1);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  assert.deepEqual(errors,[]);assert.deepEqual(denied,[]);
  await page.screenshot({path:path.join(out,`${locale}-${width}.png`),fullPage:true});
  results.push({locale,width,idle_10min_extra_reads:0,pending_reads:4,unfiltered_navigation_reads:1,real_API:0,writes:0});
  await context.close();
 }
 await writeFile(path.join(out,'report.json'),JSON.stringify({passed:true,results},null,2));console.log(JSON.stringify({passed:true,cases:results.length}));
}finally{await browser.close();}
