// Offline production-page replay. All fetches and assets are intercepted.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('C:/Users/wenbi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const dist=path.resolve(process.env.SAPBA_BROWSER_DIST), out=path.resolve(process.env.SAPBA_BROWSER_REPORT);
const origin='http://127.0.0.1:4339', results=[];
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'msedge'});
try {
  for(const provider of ['codex','workbuddy']) for(const locale of ['zh','en']) for(const width of [1440,642,390]) {
    const context=await browser.newContext({viewport:{width,height:900}}), page=await context.newPage();
    const errors=[],escaped=[];
    page.on('pageerror', error=>errors.push(error.message));
    await page.clock.install({time:new Date('2026-10-30T03:51:21Z')});
    await page.addInitScript(({provider})=>{
      const done=new URL(location.href).searchParams.get('stage')==='done';
      const run={run_id:'run_clock_offline',mode:'free_query',status:done?'inconclusive':'waiting_input',
        query:'Synthetic offline budget check',input:{},plan:null,result:null,
        runtime:{provider_id:provider,model:'offline-model'},cancel_requested:false,
        created_at:'2026-10-30T03:50:33Z',started_at:'2026-10-30T03:50:33Z',
        error:done?null:{code:'clarification_required',message:'Company code?',detail:{input_request_id:'input_offline'}},
        progress:{phase:'preparing',state:done?'inconclusive':'waiting_input',elapsed_seconds:done?61:48,
          hard_limit_seconds:1800,updated_at:'2026-10-30T03:51:21Z',next_deadline_at:null}};
      window.synthetic={writes:0,reads:0};
      window.fetch=async(url,options={})=>{
        if(options.method && options.method!=='GET') {window.synthetic.writes++;throw Error('No writes allowed in clock replay');}
        window.synthetic.reads++;
        const pathname=new URL(url,location.href).pathname;
        return {ok:true,status:200,json:async()=>structuredClone(pathname==='/api/runs/'+run.run_id?run:pathname.includes('integration-actions')?[]:{items:[]})};
      };
      window.EventSource=class {static CLOSED=2;readyState=2;addEventListener(){}close(){}};
    },{provider});
    await context.route('**/*',async route=>{
      const u=new URL(route.request().url());
      if(u.origin!==origin || route.request().method()!=='GET') {escaped.push(u.pathname);return route.abort();}
      const file=path.resolve(dist,u.pathname.replace(/^\//,'')+(u.pathname.endsWith('/')?'index.html':''));
      if(!file.startsWith(dist+path.sep)) return route.abort();
      try {return route.fulfill({body:await readFile(file),contentType:file.endsWith('.html')?'text/html':file.endsWith('.css')?'text/css':file.endsWith('.woff2')?'font/woff2':'text/javascript'});}
      catch {return route.abort();}
    });
    await page.goto(origin+'/'+locale+'/run/?run=run_clock_offline');
    const message=page.locator('[data-progress-message]');
    await message.filter({hasText:locale==='zh'?'不计执行预算':'does not consume'}).waitFor();
    const before=await message.innerText();
    assert.match(before,/48/);
    await page.clock.fastForward(2083000);
    assert.equal(await message.innerText(),before);
    await page.reload();
    await message.filter({hasText:locale==='zh'?'不计执行预算':'does not consume'}).waitFor();
    assert.equal(await message.innerText(),before);
    assert.equal(await page.evaluate(()=>window.synthetic.writes),0);
    await page.goto(origin+'/'+locale+'/run/?run=run_clock_offline&stage=done');
    await message.filter({hasText:'61'}).waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
    assert.deepEqual(errors,[]);assert.deepEqual(escaped,[]);
    assert.equal(await page.evaluate(()=>window.synthetic.writes),0);
    if(locale==='zh' && width!==1440) await page.screenshot({path:path.join(out,provider+'-'+width+'.png'),fullPage:true});
    results.push({provider,locale,width,passed:true,real_requests:0,model_calls:0,SAP_calls:0});
    await context.close();
  }
  await writeFile(path.join(out,'report.json'),JSON.stringify({passed:true,results},null,2));
  console.log(JSON.stringify({passed:true,cases:results.length,real_requests:0}));
} finally {await browser.close();}
