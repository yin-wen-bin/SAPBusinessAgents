// Full ask -> submit once -> run result. Synthetic driver fixtures only.
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
const {chromium}=createRequire(import.meta.url)('C:/Users/wenbi/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const dist=path.resolve(process.env.SAPBA_BROWSER_DIST),out=path.resolve(process.env.SAPBA_BROWSER_REPORT),fixtures=path.resolve(process.env.SAPBA_STRICT_FIXTURES);
const query='查询供应商17300003截止2022/12/31的未清发票校验',origin='http://127.0.0.1:4339';
await mkdir(out,{recursive:true});const browser=await chromium.launch({headless:true,channel:'msedge'}),results=[];
try {
 for(const provider of ['codex','workbuddy'])for(const locale of ['zh','en'])for(const width of [1440,642,390])for(const kind of ['valid_native','preflight_rejected','historical_schema_rejection']){
  const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),errors=[],escaped=[];
  page.on('pageerror',error=>errors.push(error.message));
  const run=JSON.parse(await readFile(path.join(fixtures,provider+'.json'),'utf8'));
  if(kind==='preflight_rejected'){run.status='failed';run.result=null;run.error={code:'runtime_output_contract_invalid',message:'runtime_output_contract_invalid',detail:{phase:'output_contract'}};}
  if(kind==='historical_schema_rejection'){run.status='failed';run.result=null;run.error={code:'run_failed',message:"invalid_json_schema:Invalid schema for response_format 'codex_output_schema': properties is required for object schemas."};}
  await page.addInitScript(({run,query})=>{
   window.synthetic={writes:JSON.parse(sessionStorage.getItem('strict-fixture-writes')||'[]'),reads:[]};
   window.fetch=async(url,options={})=>{
    const u=new URL(url,location.href),method=options.method||'GET';let value;
    if(method!=='GET'){
     if(method!=='POST'||u.pathname!=='/api/runs')throw Error('Unexpected mock mutation '+u.pathname);
     const body=JSON.parse(options.body);if(body.query!==query)throw Error('Query changed');
     window.synthetic.writes.push({path:u.pathname,body});sessionStorage.setItem('strict-fixture-writes',JSON.stringify(window.synthetic.writes));value={run_id:run.run_id};
    }else{
     window.synthetic.reads.push(u.pathname);
     value=u.pathname==='/api/runs/'+run.run_id?run:u.pathname.includes('integration-actions')?[]:{items:[],actions:[]};
    }
    return {ok:true,status:200,json:async()=>structuredClone(value)};
   };
   window.EventSource=class {static CLOSED=2;readyState=2;addEventListener(){}close(){}};
  },{run,query});
  await context.route('**/*',async route=>{
   const u=new URL(route.request().url());
   if(u.origin!==origin||route.request().method()!=='GET'){escaped.push(u.pathname);return route.abort();}
   const file=path.resolve(dist,u.pathname.replace(/^\//,'')+(u.pathname.endsWith('/')?'index.html':''));
   if(!file.startsWith(dist+path.sep))return route.abort();
   try{return route.fulfill({body:await readFile(file),contentType:file.endsWith('.html')?'text/html':file.endsWith('.css')?'text/css':file.endsWith('.woff2')?'font/woff2':'text/javascript'});}
   catch{return route.abort();}
  });
  await page.goto(origin+'/'+locale+'/ask/');
  await page.locator('[data-free-query] textarea').fill(query);
  await page.locator('[data-free-query] button[type=submit]').click();
  await page.waitForURL('**/'+locale+'/run/?run='+run.run_id);
  await page.locator('[data-run-identity]').filter({hasText:run.run_id}).waitFor();
  const body=await page.locator('body').innerText();
  if(kind==='valid_native'){
   await page.locator('[data-report-verification]').waitFor({state:'attached'});
   assert.equal(await page.locator('[data-report-verification] li').count(),4);
   assert.ok(await page.locator('.presentation-table').count());
   assert.doesNotMatch(body,/invalid_json_schema|\[object Object\]/);
  }else{
   assert.match(body,locale==='zh'?/平台输出契约不兼容，查询未完成/:/Platform output contract is incompatible/);
   assert.match(body,locale==='zh'?/请勿重复提交/:/Do not resubmit/);
  }
  await page.waitForTimeout(300);
  const requests=await page.evaluate(()=>window.synthetic.writes);
  assert.equal(requests.length,1);assert.equal(requests[0].body.query,query);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  assert.deepEqual(errors,[]);assert.deepEqual(escaped,[]);
  if(locale==='zh'&&width!==1440)await page.screenshot({path:path.join(out,provider+'-'+width+'-'+kind+'.png'),fullPage:true});
  results.push({provider,locale,width,kind,passed:true,synthetic_submissions:1,real_requests:0,model_calls:0,SAP_calls:0});
  await context.close();
 }
 await writeFile(path.join(out,'report.json'),JSON.stringify({passed:true,results},null,2));
 console.log(JSON.stringify({passed:true,cases:results.length,real_requests:0}));
}finally{await browser.close();}
