// Isolated component/browser test: no platform backend, SDK, model or SAP.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {writeFile,mkdir} from 'node:fs/promises';
import path from 'node:path';
import {build} from 'esbuild';
const require=createRequire(import.meta.url),{chromium}=require(process.env.SAPBA_PLAYWRIGHT_MODULE||'playwright');
const out=path.resolve(process.env.SAPBA_ACCEPTANCE_SCREENSHOT_DIR || '../.local-data/startup-version-update-20261022');await mkdir(out,{recursive:true});
const entry=`import React,{useState} from 'react';import{createRoot}from'react-dom/client';
import{AgentAcceptanceSetup}from'./src/components/AgentAcceptance';import './src/styles/global.css';import './src/styles/agent-draft.css';
function App(){const[cases,setCases]=useState([{uiKey:'stable-1',caseId:'case-1',source:'current',input:{company:'1710',supplier:'17300001',date:'2022-12-31'},sensitiveInputs:{}}]);
const schema={type:'object',required:['company','supplier','date'],properties:{company:{type:'string',title:{zh:'公司代码',en:'Company code'}},supplier:{type:'string',title:{zh:'供应商',en:'Supplier'}},date:{type:'string',format:'date',title:{zh:'过账截止日',en:'Posting cutoff'}}}};
return <main className='agent-management agent-draft-workspace'><AgentAcceptanceSetup open locale={new URLSearchParams(location.search).get('lang')||'zh'} mode='three_stage' runtime={{provider_id:'workbuddy',model:'glm-5.3-flash-with-a-long-verified-model-identity'}} schema={schema} cases={cases} currentInput={{}} currentSecrets={{}} canUseSample={false} onCases={setCases} onFindSample={()=>{throw Error('No SAP permitted')}} onClose={()=>{}} onStart={()=>{window.submissions=(window.submissions||0)+1}}/></main>}createRoot(document.getElementById('root')).render(<App/>);`;
const built=await build({stdin:{contents:entry,resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,outdir:'virtual-build',jsx:'automatic',loader:{'.woff2':'dataurl','.woff':'dataurl'},logLevel:'silent'});
const js=built.outputFiles.find(f=>f.path.endsWith('.js')).contents,css=built.outputFiles.find(f=>f.path.endsWith('.css')).contents;
const server=createServer((req,res)=>{res.setHeader('Content-Type',req.url.startsWith('/app.js')?'text/javascript':req.url.startsWith('/app.css')?'text/css':'text/html');res.end(req.url.startsWith('/app.js')?js:req.url.startsWith('/app.css')?css:'<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"><div id="root"></div><script type="module" src="/app.js"></script>')});
await new Promise(r=>server.listen(0,'127.0.0.1',r));const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined});const results=[];
try{for(const lang of ['zh','en'])for(const width of [1440,642,550,390]){const page=await browser.newPage({viewport:{width,height:900}});await page.goto(`http://127.0.0.1:${server.address().port}/?lang=${lang}`);await page.locator('dialog[open]').waitFor();
assert.equal(await page.locator('.acceptance-case-group[open]').count(),1);assert.equal(await page.locator('.acceptance-summary-grid select').count(),0);
const metrics=await page.locator('.acceptance-summary-grid').evaluate(el=>[...el.children].map(c=>Math.round(c.getBoundingClientRect().top)));if(width>=642)assert.equal(new Set(metrics).size,1);
assert(await page.locator('dialog').evaluate(el=>el.scrollWidth<=el.clientWidth+1));
await page.screenshot({path:path.join(out,`acceptance-${lang}-${width}.png`)});
const input=page.locator('.acceptance-case-meta input').first();const before=await page.locator('.runtime-field-grid input').first().getAttribute('id');await input.fill('renamed-case');assert.equal(await input.inputValue(),'renamed-case');assert.equal(await page.locator('.runtime-field-grid input').first().getAttribute('id'),before);
for(let n=1;n<5;n++)await page.getByRole('button',{name:lang==='zh'?'增加验收案例':'Add acceptance case',exact:true}).click();assert.equal(await page.locator('.acceptance-case-group').count(),5);assert.equal(await page.locator('.acceptance-case-group[open]').count(),1);
const ids=await page.locator('.runtime-field-grid input').evaluateAll(items=>items.map(i=>i.id));assert.equal(new Set(ids).size,ids.length);
await page.locator('.acceptance-case-group summary').first().click();await page.screenshot({path:path.join(out,`acceptance-${lang}-${width}-5cases.png`)});assert.equal(await page.evaluate(()=>window.submissions||0),0);results.push({lang,width,caseCount:5,noExecution:true});await page.close();}
await writeFile(path.join(out,'browser.json'),JSON.stringify(results,null,2));console.log(JSON.stringify({passed:results.length,modelCalls:0,sapCalls:0}));}finally{await browser.close();await new Promise(r=>server.close(r));}
