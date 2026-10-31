// Synthetic UI data; all network requests intercepted, no API mutation.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.SAPBA_PLAYWRIGHT_MODULE || 'playwright');
const dist=path.resolve(process.env.SAPBA_BROWSER_DIST), destination=process.env.SAPBA_BROWSER_REPORT;
await mkdir(destination,{recursive:false});
const browser=await chromium.launch({headless:true,channel:'msedge'}),results=[];
try {
  for (const locale of ['zh','en']) for (const width of [1440,390]) for (const kind of ['mixed','partial','legacy']) {
    const context=await browser.newContext({viewport:{width,height:1100}}),page=await context.newPage(),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.addInitScript(({kind})=>{
      const proof={version:'sapba.report-verification/2',summary_amounts:{status:kind==='partial'?'not_requested':'verified'},
        detail_amounts:{status:'verified'},business_facts:{status:kind==='partial'?'verified':'needs_evidence',issues:kind==='partial'?[]:[{code:'business_claim_binding_required',path:'/blocks/2',evidence_ref:'ev_synthetic'}]},
        coverage:{status:kind==='partial'?'verified':'failed',issues:kind==='partial'?[]:[{code:'answer_scope_coverage_unproven',path:'/verification/coverage'}]}};
      window.syntheticWrites=[];
      const run={run_id:'synthetic',mode:'free_query',status:'inconclusive',query:'Synthetic UI',input:{},
        created_at:'2026-10-29T00:00:00Z',started_at:'2026-10-29T00:00:00Z',completed_at:'2026-10-29T00:01:00Z',
        progress:{state:'inconclusive'},result:{summary:{zh:'测试结果',en:'Synthetic result'},
          completeness:{source_complete:true,business_complete:false,reason:'Synthetic evidence'},
          evidence:[],tool_calls:[],artifacts:[],verification:kind==='legacy'?undefined:proof}};
      window.fetch=async(url,options={})=>{
        if(options.method && options.method!=='GET'){window.syntheticWrites.push(url);throw new Error('Unexpected mutation');}
        return {ok:true,json:async()=>String(url).endsWith('/api/runs/synthetic')?run:{items:[],actions:[]}};
      };
      window.EventSource=class {static CLOSED=2;readyState=2;addEventListener(){}close(){}};
    },{kind});
    await page.route('**/*',async route=>{
      const url=new URL(route.request().url());
      if(url.origin!=='http://127.0.0.1:4339')return route.abort();
      const file=url.pathname==='/'?path.join(dist,locale,'run/index.html'):path.resolve(dist,url.pathname.replace(/^\//,''));
      if(!file.startsWith(dist+path.sep))return route.abort();
      try{return route.fulfill({body:await readFile(file),contentType:file.endsWith('.html')?'text/html':file.endsWith('.css')?'text/css':file.endsWith('.js')||file.endsWith('.mjs')?'text/javascript':'application/octet-stream'});}catch{return route.abort();}
    });
    await page.goto('http://127.0.0.1:4339/?run=synthetic');
    await page.locator('[data-run-identity]').filter({hasText:'synthetic'}).waitFor();
    if(kind==='legacy')assert.equal(await page.locator('[data-report-verification]').count(),0);
    else {
      const list=page.locator('[data-report-verification]');await list.waitFor({state:'attached'});
      assert.equal(await list.locator('li').count(),4);
      assert.deepEqual(await list.locator('li').evaluateAll(items=>items.map(i=>i.dataset.status)),kind==='mixed'?['verified','verified','needs_evidence','failed']:['not_requested','verified','verified','verified']);
      if(kind==='mixed') {
        const details=list.locator('details').first();await details.locator('summary').click();
        assert.ok((await details.textContent()).includes('/blocks/2'));
        if(width===1440)await list.screenshot({path:path.join(destination,locale+'-verification.png')});
      }
    }
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.deepEqual(await page.evaluate(()=>window.syntheticWrites),[]);assert.deepEqual(errors,[]);
    results.push({locale,width,kind,passed:true,zero_writes:true});await context.close();
  }
  await writeFile(path.join(destination,'results.json'),JSON.stringify(results,null,2));
  console.log(JSON.stringify({passed:results.length,zero_API_writes:true}));
} finally {await browser.close();}
