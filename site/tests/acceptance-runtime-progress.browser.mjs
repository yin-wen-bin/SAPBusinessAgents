// Real components + mocked GET replies only; never connect to the platform API.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {build} from 'esbuild';
const require=createRequire(import.meta.url),{chromium}=require(process.env.SAPBA_PLAYWRIGHT_MODULE||'playwright');
const out=path.resolve(process.env.SAPBA_ACCEPTANCE_SCREENSHOT_DIR||'../.local-data/acceptance-start-progress-20261022');await mkdir(out,{recursive:true});
const entry=`import React,{useEffect,useState}from'react';import{createRoot}from'react-dom/client';
import{AgentAcceptanceSetup,AgentAcceptanceProgress}from'./src/components/AgentAcceptance';
import{useAcceptanceRuntimePreview}from'./src/components/useAcceptanceRuntimePreview';
import './src/styles/global.css';import './src/styles/agent-draft.css';
const params=new URLSearchParams(location.search),locale=params.get('lang')||'zh';
function App(){const[open,setOpen]=useState(false),[revision,setRevision]=useState(5),[unknown,setUnknown]=useState(false);
const[cases,setCases]=useState([{uiKey:'stable',caseId:'one',source:'current',input:{company:'1710'},sensitiveInputs:{}}]);
const[closes,setCloses]=useState(0),[stops,setStops]=useState(0);
const[campaign,setCampaign]=useState({campaign_id:'fixture',acceptance_mode:'three_stage',runtime:{provider_id:'workbuddy',model:'frozen-model-with-a-long-concrete-identity'},status:'running',phase:'free_query',current_case_id:'case-2',current_case_index:2,created_at:new Date(Date.now()-65000).toISOString(),started_at:new Date(Date.now()-60000).toISOString(),estimated_max_seconds:15000,cases:Array.from({length:5},(_,i)=>({case_id:'case-'+(i+1),status:i===1?'running':i===0?'pass':'queued',phase:i===1?'free_query':i===0?'completed':'preparing',result:i===0?{verdict:'PASS',baseline:{result_hash:'synthetic'},free_query:{status:'completed'},fixed_agent:{status:'completed',comparison:{verdict:'MATCH'}}}:{}}))});
const preview=useAcceptanceRuntimePreview('/api/authoring/agents/fixture','fixture',revision,true);
useEffect(()=>{if(preview.ready&&window.readBegan)window.renderElapsed=performance.now()-window.readBegan},[preview.runtime,preview.ready]);
window.fixture={read:()=>preview.read(),readPair:()=>Promise.all([preview.read(),preview.read()]),revision:setRevision,unknown:setUnknown,update:setCampaign};
if(params.get('screen')==='progress')return <><output id='closes'>{closes}</output><output id='stops'>{stops}</output><AgentAcceptanceProgress open locale={locale} campaign={campaign} apiBase='' draftId='fixture' onClose={()=>setCloses(n=>n+1)} onCancel={()=>setStops(n=>n+1)} onAdjust={()=>{}} onRetry={()=>{window.retries=(window.retries||0)+1}}/></>;
return <><button id='open' onClick={()=>{setOpen(true);void preview.read(false)}}>Open</button><AgentAcceptanceSetup open={open} locale={locale} schema={{type:'object',properties:{company:{type:'string',title:{zh:'公司代码',en:'Company code'}}},required:['company']}} mode='three_stage' runtime={preview.runtime} runtimeChecking={preview.checking} runtimeStartedAt={preview.startedAt} runtimeError={preview.error} onReadRuntime={()=>preview.read()} disabled={!unknown&&!preview.ready} submissionUnknown={unknown} cases={cases} currentInput={{}} currentSecrets={{}} canUseSample={false} onCases={setCases} onFindSample={()=>{throw Error('SAP forbidden')}} onClose={()=>setOpen(false)} onStart={()=>{window.starts=(window.starts||0)+1}}/></>}
createRoot(document.getElementById('root')).render(<main className='agent-management agent-draft-workspace'><App/></main>);`;
const built=await build({stdin:{contents:entry,resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,outdir:'virtual',jsx:'automatic',loader:{'.woff2':'dataurl','.woff':'dataurl'},logLevel:'silent'});
const js=built.outputFiles.find(f=>f.path.endsWith('.js')).contents,css=built.outputFiles.find(f=>f.path.endsWith('.css')).contents;
let writes=0;
const server=createServer((req,res)=>{if(req.method!=='GET'){writes++;res.writeHead(405);res.end();return;}res.setHeader('Content-Type',req.url.startsWith('/app.js')?'text/javascript':req.url.startsWith('/app.css')?'text/css':'text/html');res.end(req.url.startsWith('/app.js')?js:req.url.startsWith('/app.css')?css:'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"><div id="root"></div><script type="module" src="/app.js"></script>')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined}),results=[];
const reply=(model='saved-model',revision=5)=>({draft_id:'fixture',revision,formal_acceptance_runtime:{provider_id:'workbuddy',model},formal_acceptance_runtime_digest:'a'.repeat(64)});
try{
for(const lang of ['zh','en'])for(const width of [1440,642,550,390]){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[],requests=[],waiting=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/authoring/**',async route=>{requests.push(route.request().method());waiting.push(route)});
 await page.goto('http://127.0.0.1:'+server.address().port+'/?lang='+lang);await page.waitForFunction(()=>window.fixture);await page.waitForTimeout(80);
 await page.locator('#open').click();await page.locator('dialog[open]').waitFor();assert.equal(requests.length,1,'prefetch and open share request');
 const start=page.locator('.acceptance-setup-footer .agent-actions button').last();assert(await start.isDisabled());
 const field=page.locator('.runtime-field-grid input').first();await field.fill('1720');await field.focus();await page.waitForTimeout(1100);
 assert.match(await page.locator('.acceptance-runtime-wait').innerText(),lang==='zh'?/[1-9]秒/:/[1-9]s/);
 await waiting.shift().fulfill({json:reply()});await page.waitForFunction(()=>!document.querySelector('.acceptance-runtime-wait'));
 assert(!(await start.isDisabled()));assert.equal(await field.inputValue(),'1720');assert(await field.evaluate(el=>document.activeElement===el));
 await page.evaluate(()=>{void window.fixture.readPair()});await page.waitForTimeout(100);assert.equal(requests.length,2);assert.match(await page.locator('.acceptance-summary-grid').innerText(),/saved-model/);assert(await start.isDisabled());
 await waiting.shift().fulfill({status:503,json:{detail:{code:'runtime_authentication_binding_changed'}}});await page.locator('.acceptance-runtime-error').waitFor();assert(await start.isDisabled());assert.equal(await field.inputValue(),'1720');
 await page.getByRole('button',{name:lang==='zh'?'重新读取':'Read again',exact:true}).click();await page.waitForTimeout(50);await waiting.shift().fulfill({json:reply()});await page.waitForFunction(()=>!document.querySelector('.acceptance-runtime-error'));
 await page.evaluate(()=>{void window.fixture.read()});await page.waitForTimeout(50);const late=waiting.shift();await page.evaluate(()=>window.fixture.revision(6));await page.waitForTimeout(80);await waiting.shift().fulfill({json:reply('new-model',6)});await page.waitForFunction(()=>document.querySelector('.acceptance-summary-grid').textContent.includes('new-model'));
 await late.fulfill({json:reply('late-old-model',5)});await page.waitForTimeout(60);assert.doesNotMatch(await page.locator('.acceptance-summary-grid').innerText(),/late-old-model/);
 if(lang==='zh'&&width===642){const timings=[];for(let i=0;i<20;i++){await page.evaluate(()=>{window.renderElapsed=null;window.readBegan=performance.now();void window.fixture.read()});for(let tries=0;!waiting.length&&tries<100;tries++)await page.waitForTimeout(5);assert(waiting.length);await waiting.shift().fulfill({json:reply('hot-'+i,6)});await page.waitForFunction(()=>window.renderElapsed!==null);timings.push(await page.evaluate(()=>window.renderElapsed));}await writeFile(path.join(out,'render-performance.json'),JSON.stringify({samples:20,requestToRenderMs:timings,scope:'Mock GET responses, includes browser request/reconciliation; not production SDK latency.'},null,2));}
 await page.screenshot({path:path.join(out,'runtime-setup-'+lang+'-'+width+'.png')});
 await page.keyboard.press('Escape');await page.locator('#open').click();assert.equal(await field.inputValue(),'1720');
 assert(requests.every(x=>x==='GET'));assert.equal(await page.evaluate(()=>window.starts||0),0);assert.deepEqual(errors,[]);results.push({screen:'setup',lang,width,getRequests:requests.length,singleFlight:true,inputsAndFocusRetained:true,lateRejected:true});await page.close();
}
for(const lang of ['zh','en'])for(const width of [1440,642,550,390]){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.route('**/api/authoring/**',route=>route.fulfill({json:reply()}));
 await page.goto('http://127.0.0.1:'+server.address().port+'/?screen=progress&lang='+lang);await page.locator('dialog[open]').waitFor();
 assert.equal(await page.locator('.acceptance-progress-cases details[open]').count(),1);assert.match(await page.locator('.acceptance-progress-cases details[open] summary').innerText(),/case-2/);
 await page.locator('.acceptance-progress-cases summary').first().click();await page.evaluate(()=>window.fixture.update(old=>({...old,updated_at:new Date().toISOString()})));assert.equal(await page.locator('.acceptance-progress-cases details[open]').count(),2);
 const top=await page.locator('.acceptance-progress-cases details[open]').first().locator('.acceptance-stage-grid > div').evaluateAll(nodes=>nodes.map(n=>Math.round(n.getBoundingClientRect().top)));
 assert.equal(new Set(top).size,width>=642?1:2,`stage layout ${lang} ${width}`);
 const body=page.locator('.acceptance-progress-dialog .acceptance-dialog-body');await body.evaluate(el=>el.scrollTop=el.scrollHeight);
 const footer=await page.locator('.acceptance-progress-footer').boundingBox();assert(footer.y+footer.height<=900);assert(await page.locator('dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
 await body.evaluate(el=>el.scrollTop=0);await page.screenshot({path:path.join(out,'progress-running-'+lang+'-'+width+'.png')});
 await page.keyboard.press('Escape');assert.equal(await page.locator('#closes').innerText(),'1');assert.equal(await page.locator('#stops').innerText(),'0');
 await page.getByRole('button',{name:lang==='zh'?'停止验收':'Stop acceptance',exact:true}).click();assert.equal(await page.locator('#stops').innerText(),'1');
 await page.evaluate(()=>window.fixture.update(old=>({...old,status:'interrupted',phase:'cleanup',report:{verdict:'NOT_TESTED',error:{code:'runtime_authentication_binding_changed'},diagnostics:{cleanup_complete:false}},cases:old.cases.map(c=>({...c,status:'interrupted'}))})));
 await page.getByRole('alert').first().waitFor();
 assert(await page.getByRole('alert').count());assert.match(await page.locator('.acceptance-progress-header').innerText(),lang==='zh'?/中断/:/interrupted/);
 assert.equal(await page.evaluate(()=>window.retries||0),0);assert.deepEqual(errors,[]);await page.screenshot({path:path.join(out,'progress-cleanup-'+lang+'-'+width+'.png')});results.push({screen:'progress',lang,width,caseToggleRetained:true,closeDoesNotStop:true,cleanupVisible:true});await page.close();
}
for(const lang of ['zh','en'])for(const width of [642,390])for(const kind of ['date','mapping','metadata','question']){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.route('**/api/authoring/**',route=>route.fulfill({json:reply()}));
 await page.goto('http://127.0.0.1:'+server.address().port+'/?screen=progress&lang='+lang);await page.waitForFunction(()=>window.fixture);
 await page.evaluate(kind=>window.fixture.update(old=>({...old,status:'interrupted',phase:'completed',current_case_id:'case-1',current_case_index:1,estimated_max_seconds:3120,report:{verdict:'NOT_TESTED',failure_category:'environment',failure_stage:'baseline',error:{code:kind==='question'?'agent_acceptance_business_input_required':kind==='metadata'?'runtime_query_scope_rejected':'runtime_scope_clarification_required'},validation_issues:kind==='question'?[]:[{code:kind==='date'?'runtime_scope_date_invalid':kind==='mapping'?'runtime_scope_mapping_unsupported':'runtime_query_scope_rejected',input_field:'posting_date_to',path:'/input/posting_date_to',...(kind==='metadata'?{scope_issue:'authoritative_date_schema_required'}:{})}],diagnostics:{cleanup_complete:true,...(kind==='question'?{clarification_question:'请确认业务范围 / Confirm business scope'}:{})}},cases:[{case_id:'case-1',phase:'completed',status:'interrupted',baseline_run_id:'offline-baseline',result:{verdict:'NOT_TESTED',failure_stage:'baseline'}}]})),kind);
 const body=page.locator('.acceptance-dialog-body');await page.waitForTimeout(60);
 assert.match(await body.innerText(),['date','question'].includes(kind)?lang==='zh'?/业务输入需要核对/:/Business inputs need verification/:lang==='zh'?/平台查询范围契约受阻/:/Platform query-scope contract blocked/);
 if(kind==='question')assert.match(await page.getByRole('alert').first().innerText(),/Confirm business scope/);
 else assert.match(await body.innerText(),lang==='zh'?/过账截止日/:/Posting cutoff/);
 if(kind==='date')assert.match(await body.innerText(),/YYYY-MM-DD/);
 if(kind==='mapping')assert.match(await body.innerText(),lang==='zh'?/平台尚未支持/:/does not support/);
 if(kind==='metadata')assert.match(await body.innerText(),lang==='zh'?/权威元数据/:/authoritative metadata/);
 assert.doesNotMatch(await page.locator('.acceptance-progress-cases').innerText(),lang==='zh'?/验收通过|已完成/:/Passed|Completed/);
 assert(await page.locator('dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));assert.equal(await page.evaluate(()=>window.retries||0),0);assert.deepEqual(errors,[]);
 await page.screenshot({path:path.join(out,'scope-'+kind+'-'+lang+'-'+width+'.png')});results.push({screen:'scope',kind,lang,width,safeReasonVisible:true,noRetry:true});await page.close();
}
assert.equal(writes,0);await writeFile(path.join(out,'runtime-progress-browser.json'),JSON.stringify(results,null,2));console.log(JSON.stringify({passed:results.length,writeRequests:writes,modelCalls:0,sapCalls:0}));
}finally{await browser.close();await new Promise(r=>server.close(r));}
