// Mock-only failure UI. No platform API, real Runtime, model or SAP.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {build} from 'esbuild';
const require=createRequire(import.meta.url),{chromium}=require(process.env.SAPBA_PLAYWRIGHT_MODULE||'playwright');
const out=path.resolve(process.env.SAPBA_ACCEPTANCE_SCREENSHOT_DIR||'../.local-data/frozen-campaign-startup-20261022');await mkdir(out,{recursive:true});
const entry=`import React from 'react';import{createRoot}from'react-dom/client';
import{AgentAcceptanceProgress}from'./src/components/AgentAcceptance';import './src/styles/global.css';import './src/styles/agent-draft.css';
const params=new URLSearchParams(location.search),locale=params.get('lang')||'zh';
const campaign={campaign_id:'isolated',status:'interrupted',phase:'completed',acceptance_mode:'three_stage',
 report:{verdict:'NOT_TESTED',failure_category:'environment',failure_stage:'preparing',error:{code:params.get('code')},diagnostics:{cleanup_complete:true,dependency_module:'scripts.run_three_stage_acceptance'}},
 cases:[{case_id:'case-1',status:'interrupted',phase:'completed',baseline_run_id:null,free_query_run_id:null,fixed_agent_run_id:null,result:{verdict:'NOT_TESTED'}}]};
const noDispatch=()=>{throw Error('No execution permitted')};
createRoot(document.getElementById('root')).render(<AgentAcceptanceProgress open locale={locale} campaign={campaign} apiBase='' draftId='fixture' onClose={()=>{}} onCancel={noDispatch} onAdjust={()=>{}} onRetry={()=>{}}/>);`;
const built=await build({stdin:{contents:entry,resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,outdir:'virtual-build',jsx:'automatic',loader:{'.woff2':'dataurl','.woff':'dataurl'},logLevel:'silent'});
const js=built.outputFiles.find(file=>file.path.endsWith('.js')).contents,css=built.outputFiles.find(file=>file.path.endsWith('.css')).contents;
let writes=0;
const server=createServer((req,res)=>{if(req.method!=='GET'){writes++;res.writeHead(405);res.end();return;}res.setHeader('Content-Type',req.url.startsWith('/app.js')?'text/javascript':req.url.startsWith('/app.css')?'text/css':'text/html');res.end(req.url.startsWith('/app.js')?js:req.url.startsWith('/app.css')?css:'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"><div id="root"></div><script type="module" src="/app.js"></script>')});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined}),results=[];
try{
for(const lang of ['zh','en'])for(const width of [1440,690,390])for(const code of ['agent_acceptance_internal_error','agent_acceptance_dependency_unavailable']){
 const page=await browser.newPage({viewport:{width,height:900}}),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(`http://127.0.0.1:${server.address().port}/?lang=${lang}&code=${code}`);await page.locator('dialog[open]').waitFor();
 assert.match(await page.locator('[role=status]').first().innerText(),lang==='zh'?/验收准备失败，尚未开始SAP取证/:/Acceptance preparation failed; SAP evidence collection has not started/);
 assert.deepEqual(await page.locator('.acceptance-stage-grid dd').allTextContents(),Array(4).fill(lang==='zh'?'未执行':'Not run'));
 await page.locator('.acceptance-diagnostics summary').click();assert.match(await page.locator('.acceptance-diagnostics').innerText(),/scripts.run_three_stage_acceptance/);
 assert(await page.locator('dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));assert.deepEqual(errors,[]);
 await page.screenshot({path:path.join(out,`preparation-${lang}-${width}-${code.endsWith('unavailable')?'new':'legacy'}.png`)});
 results.push({lang,width,code,noDispatch:true});await page.close();
}
assert.equal(writes,0);await writeFile(path.join(out,'preparation-browser.json'),JSON.stringify(results,null,2));console.log(JSON.stringify({passed:results.length,writeRequests:writes,modelCalls:0,sapCalls:0}));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
