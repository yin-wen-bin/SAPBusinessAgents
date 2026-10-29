// Mount the real workspace with a DTO created by the isolated Python API test.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {build} from 'esbuild';
const require=createRequire(import.meta.url),{chromium}=require(process.env.SAPBA_PLAYWRIGHT_MODULE||'playwright');
const fixture=JSON.parse(await readFile(process.env.SAPBA_PENDING_FIXTURE,'utf8'));
assert.equal(fixture.validation.verdict,'NOT_TESTED');assert.equal(fixture.metadata.pending_review.mode,'needs_review');
const out=path.resolve(process.env.SAPBA_BROWSER_REPORT);await mkdir(out,{recursive:true});
const entry=`import React from 'react';import{createRoot}from'react-dom/client';
import Workspace from './src/components/AgentDraftWorkspace';import './src/styles/global.css';
const locale=new URLSearchParams(location.search).get('lang')||'zh';
window.writes=[];window.uiStateWrites=0;window.EventSource=class{addEventListener(){}close(){}};
window.fetch=async(url,options={})=>{if(options.method&&options.method!=='GET'){
if(String(url).endsWith('/ui-state')){window.uiStateWrites++;return {ok:true,json:async()=>({})};}
window.writes.push(url);throw Error('Mutation forbidden');}
return {ok:true,json:async()=>String(url).includes('/diff')?{changes:[]}:window.fixture};};
createRoot(document.getElementById('root')).render(<Workspace initialDraft={window.fixture} apiBase='/mock' locale={locale}
runPath={'/'+locale+'/run/'} initialStep='publish' onBack={()=>{}} onPublished={()=>{throw Error('Unexpected publication')}}/>);`;
const bundle=await build({stdin:{contents:entry,resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,
 outdir:'virtual-build',jsx:'automatic',loader:{'.woff2':'dataurl','.woff':'dataurl'},logLevel:'silent'});
const js=bundle.outputFiles.find(f=>f.path.endsWith('.js')).contents,css=bundle.outputFiles.find(f=>f.path.endsWith('.css')).contents;
const server=createServer((req,res)=>{assert.equal(req.method,'GET');res.setHeader('Content-Type',req.url.startsWith('/app.js')?'text/javascript':req.url.startsWith('/app.css')?'text/css':'text/html');
res.end(req.url.startsWith('/app.js')?js:req.url.startsWith('/app.css')?css:'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/app.css"><div id="root"></div><script>window.fixture='+JSON.stringify(fixture).replaceAll('<','\\u003c')+'</script><script type="module" src="/app.js"></script>');});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const origin='http://127.0.0.1:'+server.address().port;
const browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined}),results=[];
try{
 for(const locale of ['zh','en'])for(const width of [642,390]){
  const page=await browser.newPage({viewport:{width,height:844}}),errors=[],external=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',r=>{if(new URL(r.request().url()).origin!==origin){external.push(r.request().url());return r.abort();}return r.continue();});
  await page.goto(origin+'/?lang='+locale);const review=page.locator('[data-pending-draft-review]');await review.waitFor();
  assert.match(await review.innerText(),locale==='zh'?/不授予PASS、执行或发布资格/:/does not grant PASS/);
  await review.locator('summary').click();
  assert.match(await review.innerText(),/source_semantics_unconfirmed/);
  assert.match(await review.innerText(),locale==='zh'?/当前修订/:/current revision/);
  assert.equal(await page.getByRole('button',{name:locale==='zh'?'发布为未启用版本':'Publish inactive',exact:true}).isDisabled(),true);
  assert.equal(await page.getByRole('button',{name:locale==='zh'?'发布并启用':'Publish and activate',exact:true}).isDisabled(),true);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true);
  assert.deepEqual(await page.evaluate(()=>window.writes),[]);assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  await page.screenshot({path:path.join(out,`${locale}-${width}.png`),fullPage:true});
  results.push({locale,width,passed:true,real_model_calls:0,SAP_calls:0,real_drafts_created:0});await page.close();
 }
 await writeFile(path.join(out,'report.json'),JSON.stringify({passed:true,results},null,2));console.log(JSON.stringify({passed:results.length,writeRequests:0}));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
