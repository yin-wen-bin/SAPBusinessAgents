// Built UI, synthetic responses only. Never generates a real Agent or calls a Runtime/SAP.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)(process.env.SAPBA_PLAYWRIGHT_MODULE || 'playwright');
const dist=path.resolve(process.env.SAPBA_BROWSER_DIST),out=path.resolve(process.env.SAPBA_BROWSER_REPORT);
await mkdir(out,{recursive:true});
const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined}),results=[];
try {
 for(const provider of ['codex','workbuddy'])for(const locale of ['zh','en'])for(const width of [642,390])for(const scenario of ['create','existing','rejected','import_failed','blocked','accept_blocked','accept_ready','pending','accept_pending','pending_rejected']) {
  const context=await browser.newContext({viewport:{width,height:800}}),page=await context.newPage();
  const errors=[],escaped=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(({scenario,provider})=>{
   const session=JSON.parse(sessionStorage.getItem('synthetic-session')||'null') || {
    session_id:'synthetic-session',status:scenario==='existing'?'draft_created':scenario.startsWith('accept_')?'reviewing':'satisfied',current_iteration:1,
    original_query:'Synthetic question',can_create_draft:!['existing','blocked','accept_blocked'].includes(scenario),
    draft_blocker:['blocked','accept_blocked'].includes(scenario)?'query_not_successful':null,
    draft_creation_mode:scenario.includes('pending')?'needs_review':'standard',
    draft_warnings:scenario.includes('pending')?['source_semantics_unconfirmed','money_business_semantics_unproven']:[],
    accepted_iteration:1,accepted_result_digest:'synthetic-digest',
    draft_id:scenario==='existing'?'source-draft':null,managed_draft_id:scenario==='existing'?'agent_draft_synthetic':null,
    iterations:[{iteration:1,run_id:'synthetic-run',status:['blocked','accept_blocked'].includes(scenario)?'inconclusive':'completed',execution_action:'initial',result_digest:'synthetic-digest'}]};
   const runtime={provider_id:provider,model:'synthetic-model',version:'synthetic'};
   const run={run_id:'synthetic-run',mode:'free_query',status:'completed',query:'Synthetic question',runtime,input:{},
    progress:{phase:'preparing_result',state:'completed'},result:{summary:{zh:'合成结果',en:'Synthetic result'},
     completeness:{source_complete:true,business_complete:true,missing_evidence:[]},tool_calls:[],evidence:[]}};
   const state=window.synthetic={session,writes:[]};
   const reply=(data,status=200)=>({ok:status<400,status,json:async()=>JSON.parse(JSON.stringify(data)),text:async()=>JSON.stringify(data)});
   window.fetch=async(url,options={})=>{
    if(options.method==='POST') {
     state.writes.push({url,body:JSON.parse(options.body||'{}')});
     if(url.endsWith('/accept')) {
      session.status='satisfied';sessionStorage.setItem('synthetic-session',JSON.stringify(session));
      const {can_create_draft,draft_blocker,...ack}=session;
      return reply(ack); // Existing acceptance POST does not carry Factory eligibility.
     } else if(url.endsWith('/agent-draft')) {
      if(scenario==='rejected'||scenario==='pending_rejected')return reply({detail:{code:'source_ineligible',message:'Synthetic source is not eligible',blocker:'query_not_successful'}},409);
      session.status='draft_created';session.draft_id='source-draft';session.can_create_draft=false;
      session.managed_draft_id=scenario==='import_failed'?null:'agent_draft_synthetic';
     } else if(url.endsWith('/import-to-management'))session.managed_draft_id='agent_draft_synthetic';
     else throw Error('Unexpected mutation');
     sessionStorage.setItem('synthetic-session',JSON.stringify(session));
     return reply({draft_id:session.draft_id,managed_draft_id:session.managed_draft_id,
      management_import_status:session.managed_draft_id?'complete':'failed',status:'validated'},201);
    }
    if(url.includes('/free-query-sessions/'))return reply(session);
    if(url.includes('integration-actions'))return reply([]);
    return reply(run);
   };
   window.EventSource=class {static CLOSED=2;readyState=1;addEventListener(){}close(){this.readyState=2;}};
  },{scenario,provider});
  await page.route('**/*',async route=>{
   const req=route.request(),url=new URL(req.url());
   if(url.origin!=='http://127.0.0.1:4339' || req.method()!=='GET'){escaped.push(req.url());return route.abort();}
   if(url.pathname.endsWith('/agent-management/') && url.searchParams.has('draft'))return route.fulfill({contentType:'text/html',body:'<h1>Synthetic fixed Agent draft</h1>'});
   const relative=url.pathname.replace(/^\/(?:SAPBusinessAgents\/)?/,''),target=path.resolve(dist,relative.endsWith('/')?relative+'index.html':relative);
   if(!target.startsWith(dist+path.sep))return route.abort();
   try {return route.fulfill({body:await readFile(target),contentType:({'.html':'text/html','.css':'text/css','.js':'text/javascript','.mjs':'text/javascript','.svg':'image/svg+xml','.woff2':'font/woff2'})[path.extname(target)]||'application/octet-stream'});}
   catch {return route.abort();}
  });
  const url=`http://127.0.0.1:4339/${locale}/run/?session=synthetic-session&run=synthetic-run`;
  await page.goto(url);
  const create=page.locator('[data-session-draft]'),view=page.locator('[data-session-draft-view]');
  if(scenario.startsWith('accept_')) {
   await page.locator('[data-session-accept]').waitFor();await page.locator('[data-session-accept]').click();
   await create.waitFor();
   await page.waitForFunction(()=>synthetic.writes.filter(w=>w.url.endsWith('/accept')).length===1);
  }
  if(['blocked','accept_blocked'].includes(scenario)) {
   await page.locator('[data-free-query-session]').waitFor();
   assert.equal(await create.isVisible(),true);assert.equal(await create.isEnabled(),false);assert.equal(await view.isVisible(),false);
   await page.locator('[data-session-draft-blocker]').waitFor();
   assert.match(await page.locator('[data-session-draft-blocker]').textContent(),locale==='zh'?/业务证据仍不足/:/insufficient business evidence/);
   assert.equal(await page.evaluate(()=>synthetic.writes.filter(w=>w.url.endsWith('/agent-draft')).length),0);
   await page.locator('[data-free-query-session]').screenshot({path:path.join(out,`${provider}-${locale}-${width}-${scenario}-action.png`)});
  } else if(scenario!=='existing') {
   if(scenario==='accept_ready'||scenario==='accept_pending')await page.waitForFunction(()=>!document.querySelector('[data-session-draft]').disabled);
   await create.waitFor();const label=await create.textContent();
   if(scenario.includes('pending')) {
    assert.equal(label,locale==='zh'?'生成待完善草稿':'Generate draft for completion');
    assert.match(await page.locator('[data-session-draft-blocker]').innerText(),locale==='zh'?/2.*不授予PASS/:/2.*not PASS/);
   }
   await create.click();
   if(scenario.includes('pending'))await page.locator('[data-draft-module-warning]').waitFor();
   await page.locator('[data-draft-module-confirm]').click();
   await page.waitForFunction(()=>synthetic.writes.filter(w=>w.url.endsWith('/agent-draft')).length===1);
   const posted=await page.evaluate(()=>synthetic.writes.find(w=>w.url.endsWith('/agent-draft')).body);
   assert.equal(posted.expectedIteration,1);assert.equal(posted.expectedResultDigest,'synthetic-digest');
   if(scenario==='rejected'||scenario==='pending_rejected') {
    await page.waitForFunction(()=>!document.querySelector('[data-session-draft]').disabled);
    assert.equal(await create.textContent(),label);assert.equal(await view.isVisible(),false);
    assert.match(await page.locator('body').innerText(),locale==='zh'?/暂不能生成固定Agent草稿/:/cannot be generated yet/);
   } else if(scenario==='import_failed') {
    await page.locator('[data-import-draft]').waitFor();assert.equal(await view.isVisible(),false);
    await page.locator('[data-import-draft]').click();await view.waitFor();
    assert.equal(await page.evaluate(()=>synthetic.writes.filter(w=>w.url.endsWith('/agent-draft')).length),1);
   }
  }
  assert.doesNotMatch(await page.locator('body').innerText(),/\[object Object\]/);
  if(!['rejected','pending_rejected','blocked','accept_blocked'].includes(scenario)) {
   await view.waitFor();assert.equal(await view.textContent(),locale==='zh'?'查看固定Agent草稿':'View fixed Agent draft');
   assert.equal(await create.isVisible(),false);
   assert.equal(new URL(await view.getAttribute('href'),page.url()).href,`http://127.0.0.1:4339/${locale}/agent-management/?draft=agent_draft_synthetic`);
   await page.reload();await view.waitFor();
   assert.equal(await page.evaluate(()=>synthetic.writes.length),0,'reload must not regenerate or import');
   assert.equal(await view.evaluate(el=>getComputedStyle(el).textDecorationLine),'none');
   assert.ok(await view.evaluate(el=>el.getBoundingClientRect().height>=44));
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
   await page.screenshot({path:path.join(out,`${provider}-${locale}-${width}-${scenario}.png`),fullPage:true});
   await page.locator('[data-free-query-session]').screenshot({path:path.join(out,`${provider}-${locale}-${width}-${scenario}-action.png`)});
   await view.click();await page.waitForURL(`**/${locale}/agent-management/?draft=agent_draft_synthetic`);
   await page.getByRole('heading',{name:'Synthetic fixed Agent draft'}).waitFor();
  }
  assert.deepEqual(errors,[]);assert.deepEqual(escaped,[]);
  results.push({provider,locale,width,scenario,passed:true,real_model_calls:0,SAP_calls:0});await context.close();
 }
 await writeFile(path.join(out,'report.json'),JSON.stringify({passed:true,results},null,2));
 console.log(JSON.stringify({passed:results.length,real_model_calls:0,SAP_calls:0}));
} finally {await browser.close();}
