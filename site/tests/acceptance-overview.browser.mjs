// Offline UI contract: mocked callbacks only; no SDK, platform API or SAP.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {build} from 'esbuild';
const require=createRequire(import.meta.url),{chromium}=require(process.env.SAPBA_PLAYWRIGHT_MODULE||'playwright');
const out=path.resolve(process.env.SAPBA_ACCEPTANCE_SCREENSHOT_DIR||'../.local-data/acceptance-overview-20261022');
await mkdir(out,{recursive:true});
const entry=`import React,{useState} from 'react';import{createRoot}from'react-dom/client';
import Overview from './src/components/AcceptanceOverview';import './src/styles/global.css';import './src/styles/agent-draft.css';
const p=new URLSearchParams(location.search),locale=p.get('lang')||'zh',kind=p.get('state')||'preparing';
window.calls={setup:0,result:0,analysis:0,refresh:0};
function App(){const[tick,setTick]=useState(0),[gate,setGate]=useState(true);window.fixtureRefresh=()=>setTick(x=>x+1);window.fixtureGate=value=>setGate(value);
const campaign=kind==='none'?null:{campaign_id:'mock-only',revision:5,status:kind==='running'?'running':kind==='pass'?'passed':kind==='cancelled'?'cancelled':'interrupted',acceptance_mode:'three_stage',
runtime:{provider_id:p.get('provider')||'workbuddy',model:'glm-5.3-flash'+(kind==='long'?'-a-very-long-frozen-model-identity-that-must-wrap-without-overflow':'')},
report:{verdict:kind==='pass'?'PASS':'NOT_TESTED',failure_stage:kind==='running'?null:'preparing',error:kind==='pass'||kind==='running'?null:{code:'agent_acceptance_dependency_unavailable'},diagnostics:{cleanup_complete:kind!=='cleanup'}},cases:[{case_id:'case-1',status:kind==='pass'?'passed':'interrupted'}]};
return <main className='agent-management agent-draft-workspace'><div className='draft-wizard-main'><section className='agent-panel acceptance-overview-panel' tabIndex={-1}>
<Overview locale={locale} currentRevision={5} acceptance={{verdict:kind==='pass'||kind==='priorpass'?'PASS':'NOT_TESTED'}} campaign={campaign}
readiness={{status:kind==='blocked'?'trial_required':'ready'}} effectiveTrial={{verdict:kind==='inconclusive'?'INCONCLUSIVE':'PASS'}} staticLabel={locale==='zh'?'自动检查通过':'Checks passed'} staticPassed staticKnown
canPublish={kind==='pass'} publicationKnown={kind!=='unknown'} blockers={[]} canStart={gate&&kind!=='blocked'} campaignActive={kind==='running'} busy={false} startDisabledReason={locale==='zh'?'请完成当前修订的试运行。':'Complete a trial for this revision.'}
onSetup={()=>window.calls.setup++} onResult={()=>window.calls.result++} onAnalyze={()=>window.calls.analysis++} onRefresh={()=>{window.calls.refresh++;setTick(x=>x+1)}}
diagnostics={<ul><li data-draft-ref='/errors/0' data-draft-annotation-kind='acceptance_issue' data-draft-source-id='mock-only' data-draft-source-digest='frozen' data-draft-revision='5'>Diagnostic reference preserved</li></ul>}/>
</section></div><footer className='draft-wizard-footer'><button className='agent-secondary-action'>{locale==='zh'?'上一步':'Previous'}</button><span>5 / 6 · {locale==='zh'?'正式验收':'Acceptance'}</span><button>{locale==='zh'?'继续':'Continue'}</button></footer><output hidden>{tick}</output></main>}
createRoot(document.getElementById('root')).render(<App/>);`;
const built=await build({stdin:{contents:entry,resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,outdir:'virtual-build',jsx:'automatic',loader:{'.woff2':'dataurl','.woff':'dataurl'},logLevel:'silent'});
const js=built.outputFiles.find(f=>f.path.endsWith('.js')).contents,css=built.outputFiles.find(f=>f.path.endsWith('.css')).contents;
let writes=0;
const server=createServer((req,res)=>{if(req.method!=='GET'){writes++;res.writeHead(405);res.end();return;}res.setHeader('Content-Type',req.url.startsWith('/app.js')?'text/javascript':req.url.startsWith('/app.css')?'text/css':'text/html');res.end(req.url.startsWith('/app.js')?js:req.url.startsWith('/app.css')?css:'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"><div id="root"></div><script type="module" src="/app.js"></script>')});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const url='http://127.0.0.1:'+server.address().port;
if(process.argv.includes('--serve')){console.log(JSON.stringify({url,mockOnly:true}));await new Promise(()=>{});}
const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined}),results=[];
try{
for(const locale of ['zh','en'])for(const width of [1440,690,642,550,390]){
 const page=await browser.newPage({viewport:{width,height:width===642?668:844}}),errors=[],external=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',route=>{const u=route.request().url();if(!u.startsWith(url)&&!u.startsWith('data:')){external.push(u);return route.abort();}return route.continue();});
 await page.goto(url+'/?lang='+locale);await page.locator('.acceptance-overview').waitFor();
 assert.equal(await page.locator('.acceptance-overview-details[open]').count(),0);
 const tops=await page.locator('.acceptance-overview-states > div').evaluateAll(els=>els.map(e=>e.getBoundingClientRect().top));
 if(width===642)assert.equal(new Set(tops).size,1);if(width===390)assert(tops[2]>tops[0]);
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 assert(await page.locator('.acceptance-overview-primary').evaluate(el=>el.getBoundingClientRect().bottom<innerHeight-60),'primary action visible');
 await page.screenshot({path:path.join(out,'overview-'+locale+'-'+width+'.png'),fullPage:true});
 const history=page.locator('.acceptance-overview-details').first();await history.locator('summary').click();
 await page.evaluate(()=>window.fixtureRefresh());assert.equal(await history.getAttribute('open'),'');
 await page.getByRole('button',{name:locale==='zh'?'刷新验证状态':'Refresh validation status'}).click();assert.equal(await history.getAttribute('open'),'');
 await page.locator('.acceptance-overview-primary').click();assert.equal(await page.evaluate(()=>window.calls.setup),1);
 await page.evaluate(()=>window.fixtureGate(false));await page.waitForFunction(()=>document.querySelector('.acceptance-overview-primary').disabled);assert(await page.locator('.acceptance-overview-primary').isDisabled());
 await page.locator('.acceptance-overview-details').nth(1).locator('summary').focus();await page.keyboard.press('Enter');
 assert.equal(await page.locator('[data-draft-ref="/errors/0"]').getAttribute('data-draft-source-digest'),'frozen');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);results.push({locale,width,passed:true});await page.close();
}
for(const state of ['none','running','blocked','cleanup','pass','priorpass','inconclusive','unknown','cancelled','long'])for(const provider of ['codex','workbuddy']){
 const page=await browser.newPage({viewport:{width:390,height:844}});await page.goto(url+'/?state='+state+'&provider='+provider);await page.locator('.acceptance-overview').waitFor();
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
 if(state==='running'){await page.locator('.acceptance-overview-primary').click();assert.equal(await page.evaluate(()=>window.calls.setup),0);assert.equal(await page.evaluate(()=>window.calls.result),1);}
 if(state==='blocked'||state==='cleanup')assert(await page.locator('.acceptance-overview-primary').isDisabled());
 if(state==='inconclusive')assert.match(await page.locator('.acceptance-overview-states').innerText(),/证据不足/);
 if(state==='pass')assert.match(await page.locator('.acceptance-overview-gate').innerText(),/发布仍需单独确认/);
 if(state==='priorpass')assert.match(await page.locator('.acceptance-overview-gate').innerText(),/其他发布条件尚未满足/);
 if(state==='unknown')assert.match(await page.locator('.acceptance-overview-gate').innerText(),/发布状态待核对/);
 if(state==='cancelled')assert.doesNotMatch(await page.locator('.acceptance-overview-attempt h3').innerText(),/准备失败/);
 assert.equal(await page.evaluate(()=>window.calls.analysis),0);results.push({state,provider,passed:true});await page.close();
}
assert.equal(writes,0);await writeFile(path.join(out,'overview-browser.json'),JSON.stringify(results,null,2));console.log(JSON.stringify({passed:results.length,writeRequests:writes,modelCalls:0,sapCalls:0}));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
