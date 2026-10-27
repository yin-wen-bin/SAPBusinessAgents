// Built front end + synthetic API only. Abort every real network/write request.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)(process.env.SAPBA_PLAYWRIGHT_MODULE || 'playwright');
const dist=path.resolve(process.env.SAPBA_BROWSER_DIST),out=path.resolve(process.env.SAPBA_BROWSER_REPORT);
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined});
const results=[];
try {
 for(const locale of ['zh','en'])for(const provider of ['workbuddy','codex'])for(const width of [1440,642,550,390]) {
  const context=await browser.newContext({viewport:{width,height:800}}),page=await context.newPage();
  const errors=[],escaped=[];page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(({provider})=>{
   const runtime={provider_id:provider,model:'synthetic-model',version:'synthetic'};
   const run={run_id:'synthetic-handoff',mode:'free_query',status:'inconclusive',query:'Synthetic question',runtime,input:{},
    started_at:'2026-10-27T00:41:00Z',completed_at:'2026-10-27T01:02:41Z',
    progress:{phase:'preparing_result',state:'inconclusive',event_sequence:12,hard_limit_seconds:1800,elapsed_seconds:1278},
    progress_display:{run_id:'synthetic-handoff',phase:'preparing_result',steps:Array.from({length:5},()=>({state:'completed'}))},
    result:{summary:{zh:'合成报告候选不是终态',en:'A synthetic report candidate is not a terminal'},evidence:[],tool_calls:[],
     completeness:{source_complete:true,business_complete:false,missing_evidence:['runtime_structured_output_missing']},
     errors:[{code:'runtime_structured_output_missing',category:'runtime_terminal_handoff',phase:'final_handoff'}],
     harness:{runtime:provider==='workbuddy'?'workbuddy_worker':'codex_app_server',stop_reason:'terminal_handoff_failed',
      diagnostics:{terminal_handoff_failure:{code:'runtime_structured_output_missing',cleanup_complete:true,
       candidate_report_refs:['synthetic-only'],message:'DO_NOT_RENDER_RAW_DIAGNOSTIC'}}}}};
   const state=window.synthetic={run,writes:[]};
   window.fetch=async(url,options={})=>{
    if(options.method && options.method!=='GET') {state.writes.push({url,method:options.method});throw Error('No writes in this scenario');}
    return {ok:true,status:200,json:async()=>JSON.parse(JSON.stringify(url.includes('integration-actions')?[]:run))};
   };
   window.EventSource=class {static CLOSED=2;readyState=1;addEventListener(){}close(){this.readyState=2;}};
  },{provider});
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin!=='http://127.0.0.1:4339' || request.method()!=='GET'){escaped.push(request.url());return route.abort();}
   const relative=url.pathname.replace(/^\/(?:SAPBusinessAgents\/)?/,''),target=path.resolve(dist,relative.endsWith('/')?relative+'index.html':relative);
   if(!target.startsWith(dist+path.sep))return route.abort();
   try {return route.fulfill({body:await readFile(target),contentType:({'.html':'text/html','.css':'text/css','.js':'text/javascript','.mjs':'text/javascript','.woff2':'font/woff2','.svg':'image/svg+xml'})[path.extname(target)]||'application/octet-stream'});}
   catch {return route.abort();}
  });
  await page.goto(`http://127.0.0.1:4339/${locale}/run/?run=synthetic-handoff`);
  const headline=page.locator('[data-run-status]');
  await page.waitForFunction(()=>/交接失败|handoff failed/.test(document.querySelector('[data-run-status]')?.textContent||''));
  assert.match(await headline.textContent(),locale==='zh'?/交接失败/:/handoff failed/);
  assert.match(await page.locator('[data-progress-indicator]').getAttribute('class'),/is-stopped/);
  assert.doesNotMatch(await page.locator('[data-progress-step]').last().getAttribute('class'),/is-complete/);
  assert.match(await page.locator('[data-progress-step]').last().getAttribute('class'),/is-stopped/);
  const body=await page.locator('body').innerText();
  assert.match(body,locale==='zh'?/清理已确认/:/cleanup is confirmed/);
  assert.doesNotMatch(body,/DO_NOT_RENDER_RAW_DIAGNOSTIC/);
  await page.screenshot({path:path.join(out,`${locale}-${provider}-${width}.png`),fullPage:true});
  // Reload preserves the failure classification, never resubmits or promotes a candidate.
  await page.reload();
  await page.waitForFunction(()=>/交接失败|handoff failed/.test(document.querySelector('[data-run-status]')?.textContent||''));
  assert.equal(await page.evaluate(()=>synthetic.writes.length),0);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  assert.deepEqual(errors,[]);assert.deepEqual(escaped,[]);
  results.push({locale,provider,width,passed:true,real_requests:0});await context.close();
 }
 await writeFile(path.join(out,'report.json'),JSON.stringify({passed:true,results,model_calls:0,SAP_calls:0},null,2));
 console.log(JSON.stringify({passed:results.length,model_calls:0,SAP_calls:0}));
} finally {await browser.close();}
