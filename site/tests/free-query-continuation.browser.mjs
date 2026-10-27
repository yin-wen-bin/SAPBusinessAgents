// Production HTML, synthetic API only. No live SDK, SAP, or formal writes.
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
 for(const locale of ['zh','en'])for(const width of [1440,642,550,390]) {
  const context=await browser.newContext({viewport:{width,height:800}}),page=await context.newPage();
  const errors=[],escaped=[];page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(({locale})=>{
   const presentation={title:{zh:'合成分析结果',en:'Synthetic analysis result'},blocks:[{type:'notice',text:{zh:'仅使用合成证据',en:'Synthetic evidence only'},evidence_refs:[]}]};
   const runtime={provider_id:'workbuddy',model:'synthetic-model',version:'synthetic'};
   const run={run_id:'synthetic-6',mode:'free_query',status:'inconclusive',query:'Synthetic question',runtime,input:{},
    started_at:new Date(Date.now()-396000).toISOString(),completed_at:new Date().toISOString(),
    progress:{phase:'preparing_result',state:'inconclusive',event_sequence:12},
    execution_facts:{evidence_reused:true,status:'inconclusive',cancel_requested:false,legacy_recovery_context:true,historical_cancelled_iteration:2},
    progress_display:{run_id:'synthetic-6',evidence_reused:true,phase:'preparing_result',steps:[{state:'completed'},{state:'completed'},{state:'skipped'},{state:'completed'},{state:'completed'}]},
    result:{plan:{kind:'free_query_evidence_reinterpretation'},presentation,summary:{zh:'合成分析',en:'Synthetic analysis'},evidence:[],tool_calls:[],
     completeness:{source_complete:false,business_complete:false,missing_evidence:['synthetic_gap']}},
    free_query_session:{session_id:'synthetic-session',current_iteration:6,iteration:6}};
   const session={session_id:'synthetic-session',status:'reviewing',current_iteration:6,original_query:'Synthetic question',runtime,
    iterations:Array.from({length:6},(_,i)=>({iteration:i+1,run_id:`synthetic-${i+1}`,status:i===1?'cancelled':i>1&&i<5?'failed':'inconclusive',execution_action:i===0?'initial':'reinterpret',result_digest:i===5?'valid':null,feedback:'Synthetic feedback',decision:{},change_summary:{}}))};
   const state=window.synthetic={run,session,writes:[],streams:[],lost:false,unknown:false,
    receipt:{feedback_request_id:'original-receipt',status:'queued',phase:'received'}};
   const reply=data=>({ok:true,status:200,json:async()=>JSON.parse(JSON.stringify(data))});
   window.fetch=async(url,options={})=>{
    if(options.method==='POST') {
     state.writes.push({url,body:JSON.parse(options.body || '{}')});
     state.receipt={feedback_request_id:'original-receipt',status:'queued',phase:'received'};
     session.active_feedback_request=state.receipt;
     if(state.lost)throw new Error('synthetic response loss');
     return reply(state.receipt);
    }
    if(url.includes('/feedback-requests/'))return state.unknown?{ok:false,status:404}:reply(state.receipt);
    if(url.includes('integration-actions'))return reply([]);
    if(url.includes('/free-query-sessions/'))return reply(session);
    return reply(run);
   };
   window.EventSource=class {
    static CLOSED=2;readyState=1;listeners={};
    constructor(url){this.url=url;state.streams.push(this);}
    addEventListener(name,fn){this.listeners[name]=fn;}
    close(){this.readyState=2;}
    emit(name,data={}){this.listeners[name]?.({data:JSON.stringify({run_id:run.run_id,sequence:20,data})});}
   };
  },{locale});
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin!=='http://127.0.0.1:4339' || request.method()!=='GET'){escaped.push(request.url());return route.abort();}
   const relative=url.pathname.replace(/^\/(?:SAPBusinessAgents\/)?/,'');
   const target=path.resolve(dist,relative.endsWith('/')?relative+'index.html':relative);
   if(!target.startsWith(dist+path.sep))return route.abort();
   try {const body=await readFile(target);const ext=path.extname(target);
    return route.fulfill({body,contentType:({'.html':'text/html','.css':'text/css','.js':'text/javascript','.mjs':'text/javascript','.woff2':'font/woff2','.svg':'image/svg+xml'})[ext]||'application/octet-stream'});
   }catch{return route.abort();}
  });
  await page.goto(`http://127.0.0.1:4339/${locale}/run/?session=synthetic-session&run=synthetic-6`);
  const open=page.locator('[data-session-continue]'),dialog=page.locator('[data-session-dialog]');
  await open.waitFor();
  assert.equal(await dialog.isVisible(),false);
  assert.equal(await page.locator('[data-session-history]').getAttribute('open'),null);
  assert.match(await page.locator('[data-session-history-title]').textContent(),/6/);
  assert.match(await page.locator('[data-execution-facts]').textContent(),locale==='zh'?/第2轮历史/:/historical round 2/);
  await page.locator('[data-session-history-title]').click();
  await page.locator('[data-session-iterations] details summary').nth(0).click();
  assert.equal(await page.locator('[data-session-iterations] details').nth(5).getAttribute('open'),null);
  await open.click();
  const input=dialog.locator('textarea');await input.fill('Keep this unsent draft');
  await page.keyboard.press('Escape');
  assert.equal(await dialog.isVisible(),false);
  assert.equal(await input.inputValue(),'Keep this unsent draft');
  assert.equal(await page.evaluate(()=>synthetic.writes.length),0);
  await open.click();
  assert.equal(await dialog.locator('[data-session-feedback-question]').isVisible(),false);
  await page.evaluate(()=>synthetic.streams.find(x=>x.url.includes('/runs/')).emit('progress_changed'));
  assert.equal(await input.inputValue(),'Keep this unsent draft');
  assert.notEqual(await page.locator('[data-session-iterations] details').nth(0).getAttribute('open'),null);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  assert.equal(await dialog.evaluate(el=>el.scrollWidth<=el.clientWidth+1),true);
  await page.screenshot({path:path.join(out,`${locale}-${width}-dialog.png`)});
  await input.fill('Rerun the original question');
  if(width===390)await page.evaluate(()=>{synthetic.lost=true;});
  if(width===550)await page.evaluate(()=>{synthetic.lost=true;synthetic.unknown=true;});
  await dialog.locator('button[type=submit]').click();
  await page.waitForFunction(()=>synthetic.writes.length===1);
  await page.waitForFunction(()=>!document.querySelector('[data-session-feedback]').dataset.submitting);
  const body=await page.evaluate(()=>synthetic.writes[0].body);
  assert.equal(body.continuationMode,'review');assert.ok(body.requestId);
  assert.equal(await page.evaluate(()=>synthetic.writes.length),1);
  if(width===550) {
   await page.reload();
   await open.waitFor();await open.click();
   assert.equal(await input.inputValue(),'Rerun the original question');
   assert.equal(await dialog.locator('button[type=submit]').isEnabled(),false);
   await dialog.locator('[data-recovery-check]').click();
   await page.waitForFunction(()=>!sessionStorage.getItem('sapba-feedback-pending:synthetic-session'));
   assert.equal(await page.evaluate(()=>synthetic.writes.length),0,'reload and recovery must not POST again');
  }
  assert.deepEqual(escaped,[]);assert.deepEqual(errors,[]);
  results.push({locale,width,passed:true,synthetic_submissions:1,external_requests:0});
  await context.close();
 }
 await writeFile(path.join(out,'report.json'),JSON.stringify({passed:true,results,real_model_calls:0,SAP_calls:0},null,2));
 console.log(JSON.stringify({passed:results.length,external_requests:0}));
} finally {await browser.close();}
