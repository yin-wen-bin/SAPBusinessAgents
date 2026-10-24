// Synthetic-only browser regression. Grouping/paging is GET-only. The page's
// existing last-step preference PUT is mocked separately; all execution writes fail.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url),{chromium}=require(process.env.SAPBA_PLAYWRIGHT_MODULE||'playwright');
const root=fileURLToPath(new URL('../dist/',import.meta.url));
const server=createServer(async(req,res)=>{
 let relative=decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/SAPBusinessAgents\/?/,'').replace(/^\//,'');
 if(!relative||relative.endsWith('/'))relative+='index.html';
 const file=path.resolve(root,relative);if(!file.startsWith(root)){res.writeHead(403).end();return;}
 try{res.writeHead(200,{'Content-Type':{'.js':'text/javascript','.css':'text/css','.html':'text/html'}[path.extname(file)]||'application/octet-stream'});res.end(await readFile(file));}
 catch{res.writeHead(404).end();}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const origin=`http://127.0.0.1:${server.address().port}`,browser=await chromium.launch({headless:true,channel:process.platform==='win32'?'msedge':undefined}),checks=[];
const pair=(zh,en=zh)=>({zh,en});
try{
for(const locale of ['zh','en'])for(const width of [1440,690,390]){
 const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage(),zh=locale==='zh';
 const all=[...Array.from({length:25},(_,i)=>['OPEN-'+i,'Open']),...Array.from({length:15},(_,i)=>['CLEARED-'+i,'Cleared']),['UNKNOWN','Unknown']];
 const rows=items=>items.map(r=>({values:r.map(s=>pair(s))}));
 const groups={field:'state',label:pair('截止日状态','Status at cutoff'),total_rows:41,source_complete:true,choices:[
  {value:'"open"',label:pair('未清','Open'),count:25},{value:'"closed"',label:pair('已清','Cleared'),count:15},{value:'"unknown"',label:pair('未知','Unknown'),count:1}]};
 const trial={run_id:'saved-trial',revision:5,status:'completed',verdict:'INCONCLUSIVE',business_output_available:true};
 const run={run_id:'saved-trial',mode:'agent',status:'completed',result:{rule_results:[{source_complete:true,evidence_complete:false,business_report:{}}],
  steps:[{step_id:'read',executor:'sap_read',status:'completed',execution:{record_count:41}},
  {step_id:'check',executor:'rule',status:'completed',execution:{summary_kind:'rule_check',record_count:null}},
  {step_id:'compute',executor:'rule',status:'completed',execution:{summary_kind:'business_records',record_count:41}}],
  business_display:{business_status_label:pair('证据待核对','Evidence needs review'),groups:{'0':groups}},
  presentation:{title:pair('已保存的业务结果','Saved business result'),blocks:[{type:'table',title:pair('业务记录','Business records'),columns:[{key:'document',label:pair('凭证','Document')},{key:'state',label:pair('状态','State')}],rows:rows(all),total_rows:41},
  {type:'key_value',title:pair('业务结论','Business conclusion'),entries:[{label:pair('结论','Conclusion'),value:pair('证据待核对','Evidence needs review')}]},
  {type:'table',title:pair('分币种金额','Amounts by currency'),columns:[{key:'currency',label:pair('币种','Currency')},{key:'amount',label:pair('带符号金额','Signed amount')}],rows:rows([['USD','-1.2300'],['EUR','9.00']]),total_rows:2}]}}};
 const draft={draft_id:'generic-view',agent_id:'unrelated-domain-agent',revision:5,status:'draft',target_version:'0.1.0',technical_identity:{confirmed:true},
  conversation:[],revisions:[],trial,static_checks:{errors:[],checks:[],presentation_contract:{status:'ready'}},publishability:{can_publish:false},package:{readme:'',manifest:{slug:'unrelated-domain-agent',module:'Common',version:'0.1.0',title:pair('合成草稿','Synthetic draft'),execution:{inputSchema:{type:'object',properties:{}}}}}};
 const writes=[];const pages=[];let preferenceWrites=0;
 await page.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url());
  if(url.pathname.startsWith('/api/')){
   if(req.method()==='OPTIONS'){await route.fulfill({status:204,headers:{'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'*','Access-Control-Allow-Methods':'*'}});return;}
   if(req.method()==='PUT'&&url.pathname==='/api/authoring/agents/generic-view/ui-state'){
    assert.deepEqual(req.postDataJSON(),{lastStep:'trial'});preferenceWrites++;
    await route.fulfill({json:{},headers:{'Access-Control-Allow-Origin':'*'}});return;
   }
   if(req.method()!=='GET'){writes.push({method:req.method(),path:url.pathname});await route.abort();return;}
   let body={};
   if(url.pathname.endsWith('/presentation/blocks/0/rows')){
    const group=url.searchParams.get('group'),offset=Number(url.searchParams.get('offset')||0),limit=Number(url.searchParams.get('limit')||20);
    const filtered=group==='"open"'?all.filter(r=>r[1]==='Open'):group==='"closed"'?all.filter(r=>r[1]==='Cleared'):group==='"unknown"'?all.filter(r=>r[1]==='Unknown'):all;
    pages.push({group,offset});body={rows:rows(filtered.slice(offset,offset+limit)),total_rows:filtered.length,group};
   }else if(url.pathname.endsWith('/api/runs/saved-trial'))body=run;
   else if(url.pathname.endsWith('/validation-report'))body={trial};
   else if(url.pathname.endsWith('/generic-view'))body=draft;
   else if(url.pathname.endsWith('/agents')||url.pathname.endsWith('/catalog'))body=[];
   await route.fulfill({json:body,headers:{'Access-Control-Allow-Origin':'*'}});
  }else if(url.origin===origin)await route.continue();else await route.abort();
 });
 await page.goto(`${origin}/SAPBusinessAgents/${locale}/agent-management/?draft=generic-view&step=trial`);
 const result=page.locator('.draft-trial-result');await result.waitFor();
 await result.getByRole('combobox',{name:zh?'业务记录分组':'Business record group'}).waitFor();
 assert.match(await result.innerText(),/-1.2300/);assert.match(await result.innerText(),/EUR/);assert.match(await result.innerText(),/9.00/);
 const select=result.getByRole('combobox',{name:zh?'业务记录分组':'Business record group'});
 await select.selectOption('"open"');await result.getByRole('cell',{name:'OPEN-0',exact:true}).waitFor();
 const recordTable=select.locator('xpath=ancestor::section[1]');
 assert.doesNotMatch(await recordTable.innerText(),/CLEARED-0/);
 await recordTable.getByRole('button',{name:zh?'下一页':'Next',exact:true}).click();await recordTable.getByRole('cell',{name:'OPEN-24',exact:true}).waitFor();
 await select.selectOption('"closed"');await recordTable.getByRole('cell',{name:'CLEARED-0',exact:true}).waitFor();
 assert.doesNotMatch(await recordTable.innerText(),/OPEN-24/);
 await select.selectOption('"unknown"');await recordTable.getByRole('cell',{name:'UNKNOWN',exact:true}).waitFor();
 assert.deepEqual(writes,[]);assert.ok(pages.some(p=>p.group==='"open"'&&p.offset===20));
 assert.ok(await result.getByRole('heading',{name:zh?'实际处理步骤':'Executed steps'}).isVisible());
 if(process.env.SAPBA_BROWSER_OUTPUT)await page.screenshot({path:path.join(process.env.SAPBA_BROWSER_OUTPUT,`${locale}-${width}.png`),fullPage:true});
 const overflow=await page.evaluate(()=>({viewport:innerWidth,scroll:document.documentElement.scrollWidth,nodes:[...document.querySelectorAll('body *')].filter(n=>n.getBoundingClientRect().right>innerWidth+2&&getComputedStyle(n).position!=='fixed').slice(0,8).map(n=>({tag:n.tagName,css:n.className,width:n.getBoundingClientRect().width,right:n.getBoundingClientRect().right}))}));
 assert.ok(overflow.scroll<=width+2,JSON.stringify({locale,width,overflow}));
 checks.push({locale,width,passed:true,execution_writes:writes.length,mocked_existing_preference_writes:preferenceWrites,group_pages:pages.length});await context.close();
}
console.log(JSON.stringify({synthetic_browser_checks:checks,model_calls:0,SAP_calls:0}));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
