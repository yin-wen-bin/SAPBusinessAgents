// Offline replay of built pages. No real API, SDK, model or SAP is reachable.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('C:/Users/wenbi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const dist=path.resolve(process.env.SAPBA_BROWSER_DIST),out=path.resolve(process.env.SAPBA_BROWSER_REPORT);
const origin='http://127.0.0.1:4343', results=[];
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,channel:'msedge'});
try {
  for(const provider of ['codex','workbuddy']) for(const locale of ['zh','en']) for(const width of [1440,642,550,390]) {
    const context=await browser.newContext({viewport:{width,height:850}}),page=await context.newPage();
    const errors=[],escaped=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(({provider})=>{
      const scenario=new URL(location.href).searchParams.get('scenario') || 'slow';
      const run={run_id:'run_initial_offline',mode:'free_query',status:'running',query:'Synthetic offline query',
        runtime:{provider_id:provider,version:'offline',model:'offline-model'},input:{},plan:null,result:null,
        progress:{phase:'preparing',state:'active',elapsed_seconds:3,hard_limit_seconds:1800},cancel_requested:false};
      window.synthetic={reads:0,writes:0,created:0,fail:false};
      let accept,reject;
      const pending=new Promise((resolve,fail)=>{accept=resolve;reject=fail;});
      window.releaseRun=()=>accept({ok:true,status:200,json:async()=>structuredClone(run)});
      window.rejectRun=()=>reject(new Error('offline read failure'));
      window.recoverRun=()=>{window.synthetic.fail=false;window.synthetic.recovered=true;};
      window.fetch=async(url,options={})=>{
        const pathname=new URL(url,location.href).pathname;
        if(options.method && options.method!=='GET') {
          window.synthetic.writes++;
          if(pathname==='/api/runs') window.synthetic.created++;
          throw Error('No write is allowed in initial-view replay');
        }
        if(pathname==='/api/runs/'+run.run_id) {
          window.synthetic.reads++;
          if(window.synthetic.fail) throw Error('offline connection failure');
          if(window.synthetic.recovered) return {ok:true,status:200,json:async()=>structuredClone(run)};
          if(scenario==='missing') return {ok:false,status:404,text:async()=>'Not found'};
          return pending;
        }
        return {ok:true,status:200,json:async()=>pathname.includes('integration-actions')?[]:{items:[]}};
      };
      navigator.sendBeacon=()=>true;
      window.EventSource=class {
        static CLOSED=2;readyState=1;
        constructor(){window.synthetic.stream=this;}
        addEventListener(){}close(){this.readyState=2;}
      };
    },{provider});
    await context.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(url.origin!==origin || route.request().method()!=='GET') {escaped.push(url.pathname);return route.abort();}
      const file=path.resolve(dist,url.pathname.replace(/^\//,'')+(url.pathname.endsWith('/')?'index.html':''));
      if(!file.startsWith(dist+path.sep)) return route.abort();
      try{return route.fulfill({body:await readFile(file),contentType:file.endsWith('.html')?'text/html':file.endsWith('.css')?'text/css':file.endsWith('.woff2')?'font/woff2':'text/javascript'});}
      catch{return route.abort();}
    });
    const root=page.locator('[data-run-console]');
    const ready=()=>page.waitForFunction(()=>document.querySelector('[data-run-console]').dataset.runInitialState==='ready');
    await page.goto(`${origin}/${locale}/run/?run=run_initial_offline`);
    await page.waitForFunction(()=>window.synthetic.reads===1);
    assert.equal(await page.locator('[data-run-loading]').count(),0);
    assert.equal(await page.locator('.run-console-header').isVisible(),true);
    assert.match(await page.locator('[data-run-identity]').innerText(),/run_initial_offline/);
    for(const selector of ['[data-progress-disclosure]','.run-evidence-details','.run-technical-details'])
      for(const element of await page.locator(selector).all()) assert.equal(await element.isVisible(),false,selector);
    assert.equal(await root.getAttribute('aria-busy'),'true');
    if(provider==='codex' && locale==='zh' && [642,390].includes(width))
      await page.screenshot({path:path.join(out,`loading-${width}.png`),fullPage:true});
    await page.evaluate(()=>window.releaseRun());await ready();
    assert.equal(await page.locator('[data-run-loading]').count(),0);
    assert.equal(await page.locator('[data-progress-disclosure]').isVisible(),true);
    assert.equal(await page.locator('[data-cancel]').isVisible(),false);
    assert.match(await page.locator('[data-run-identity]').innerText(),new RegExp(provider));
    assert.equal(await page.locator('[data-run-alert]').isVisible(),false);
    // A later failed GET must not replace confirmed content with the placeholder.
    await page.evaluate(()=>{window.synthetic.fail=true;window.synthetic.stream.onerror();});
    await page.locator('[data-run-alert]').waitFor();
    assert.equal(await root.getAttribute('data-run-initial-state'),'ready');
    assert.equal(await page.locator('[data-progress-disclosure]').isVisible(),true);
    await page.evaluate(()=>window.recoverRun());
    await page.locator('[data-reconnect]').click();
    await page.waitForFunction(()=>document.querySelector('[data-run-alert]').hidden);
    // A first-read failure stays compact, and reconnect recovers the same run.
    await page.goto(`${origin}/${locale}/run/?run=run_initial_offline&scenario=error`);
    await page.waitForFunction(()=>window.synthetic.reads===1);
    await page.evaluate(()=>window.rejectRun());
    await page.waitForFunction(()=>document.querySelector('[data-run-console]').dataset.runInitialState==='error');
    assert.equal(await page.locator('[data-run-alert]').isVisible(),true);
    assert.equal(await page.locator('.run-console-header').isVisible(),true);
    assert.equal(await page.locator('[data-progress-disclosure]').isVisible(),false);
    assert.equal(await page.locator('[data-reconnect]').isVisible(),true);
    await page.evaluate(()=>window.recoverRun());await page.locator('[data-reconnect]').click();await ready();
    assert.equal(await page.locator('[data-run-alert]').isVisible(),false);
    if(provider==='codex' && locale==='zh' && [642,390].includes(width))
      await page.screenshot({path:path.join(out,`ready-${width}.png`),fullPage:true});
    await page.goto(`${origin}/${locale}/run/?run=run_initial_offline&scenario=missing`);
    await page.locator('[data-run-alert]').waitFor();
    assert.match(await page.locator('[data-run-alert]').innerText(),locale==='zh'?/过期或删除/:/expired or was deleted/);
    assert.equal(await page.locator('[data-progress-disclosure]').isVisible(),false);
    assert.equal(await page.evaluate(()=>window.synthetic.writes),0);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
    assert.deepEqual(errors,[]);assert.deepEqual(escaped,[]);
    results.push({provider,locale,width,passed:true,scenarios:['slow','ready','later_failure','reconnect','initial_failure','missing'],writes:0,real_requests:0});
    await context.close();
  }
  await writeFile(path.join(out,'report.json'),JSON.stringify({passed:true,results},null,2));
  console.log(JSON.stringify({passed:true,layouts:results.length,scenarios:results.length*6,model_calls:0,SAP_calls:0}));
} finally {await browser.close();}
