// Offline host-bridge and deterministic browser-executor integration tests.
import assert from 'node:assert/strict';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const repository = path.resolve(fileURLToPath(new URL('..',import.meta.url)));
const output = path.join(repository,'.local-data/auto-test/browser-verification');
await mkdir(output,{recursive:true});
const {chromium} = createRequire(import.meta.url)(process.env.SAPBA_PLAYWRIGHT_MODULE || 'playwright');
const template = await readFile(path.join(repository,'src/sap_business_agents_test_agent/assets/card.html'),'utf8');
const features = [
  ['free-query','自由查询',true],['workflows','工作流',true],['agent-management','Agent 管理',true],
  ['fixed-agents','固定业务 Agent',false],['role-matching','岗位匹配',false],['plugins','插件与连接',false],
  ['settings','系统配置',false],['history','历史与证据',false]
].map(([id,title,value])=>({id,title,default:value,operations:['query']}));
features[0].operations.push('feedback');
const make = payload => template.replaceAll('__ROOT_ID__','native-test-card').replace('__PAYLOAD__',JSON.stringify(payload));
const browser = await chromium.launch({channel:process.env.SAPBA_BROWSER_CHANNEL || 'msedge',headless:true});
const errors = [];
try {
  const context = await browser.newContext();
  for(const width of [390,736]) {
    const page = await context.newPage(); page.on('pageerror',error=>errors.push(error.message));
    await page.setViewportSize({width,height:900});
    await page.addInitScript(()=>{});
    await page.evaluate(()=>{
      window.received=[];window.saved=[];
      window.openai={sendFollowUpMessage:async value=>window.received.push(value),
        setWidgetState:async value=>window.saved.push(value)};
    });
    await page.setContent('<style>body{font-family:system-ui;margin:8px}.viz-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}.viz-row{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}.form-check{display:flex;gap:8px}textarea{width:95%}button{padding:8px}</style>'+make({stage:'select',inventory_digest:'fresh',features,repository}));
    assert.equal(await page.locator('input[data-feature-check]').count(),8);
    assert.equal(await page.locator('input[data-feature-check]:checked').count(),3);
    assert.equal(await page.evaluate(()=>window.received.length),0);
    await page.getByLabel('固定业务 Agent',{exact:true}).check();
    assert.equal(await page.evaluate(()=>window.saved.at(-1).modelContent.selected.length),4);
    assert.equal(await page.evaluate(()=>window.received.length),0);
    await page.locator('[data-choices] > div').first().locator('summary').click();
    await page.getByLabel('feedback',{exact:true}).uncheck();
    await page.getByRole('button',{name:'生成测试脚本',exact:true}).click();
    const message = await page.evaluate(()=>window.received[0].prompt);
    assert.match(message,/\$sapba-auto-test prepare/);
    assert.equal(JSON.parse(message.split('\n')[1]).selected.length,4);
    assert.equal(JSON.parse(message.split('\n')[1]).selected.includes('free-query/query'),true);
    await page.getByRole('button',{name:'生成测试脚本',exact:true}).dispatchEvent('click');
    assert.equal(await page.evaluate(()=>window.received.length),1);
    assert.equal(await page.getByRole('button',{name:'生成测试脚本'}).isDisabled(),true);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.screenshot({path:path.join(output,`selection-${width}.png`),fullPage:true});
    await page.close();
  }
  const page = await context.newPage(); page.on('pageerror',error=>errors.push(error.message));
  await page.setContent(make({stage:'select',inventory_digest:'fresh',features,repository}));
  await page.getByRole('button',{name:'生成测试脚本'}).click();
  assert.equal(await page.locator('textarea').isVisible(),true);
  assert.match(await page.locator('textarea').inputValue(),/prepare/);
  await page.evaluate(()=>{window.openai={sendFollowUpMessage:async value=>{window.sent=value}}});
  await page.setContent(make({stage:'start',plan_id:'p',plan_digest:'d',request_id:'start-intent',cases:[{scenario:'主路径',mode:'simulated'}],repository}));
  await page.getByRole('button',{name:'开始测试'}).click();
  assert.equal(JSON.parse((await page.evaluate(()=>window.sent.prompt)).split('\n')[1]).plan_digest,'d');
  assert.equal(JSON.parse((await page.evaluate(()=>window.sent.prompt)).split('\n')[1]).request_id,'start-intent');
  await page.close(); await context.close();
} finally {await browser.close();}
assert.deepEqual(errors,[]);

let realWrites=0;
const server=createServer((request,response)=>{
  if(request.method!=='GET')realWrites++;
  if(request.url==='/api/result') {
    response.setHeader('content-type','application/json');
    response.end(JSON.stringify({rows:[{id:'0001'}],complete:true}));return;
  }
  response.setHeader('content-type','text/html; charset=utf-8');
  response.end('<label>业务问题<input aria-label="业务问题"></label><button onclick="fetch(\'/api/runs\',{method:\'POST\'}).then(r=>r.json()).then(()=>document.getElementById(\'status\').textContent=\'查询完成\')">查询</button><p id="status">等待</p>');
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const origin=`http://127.0.0.1:${server.address().port}`;
try {
  const actions=[{type:'navigate',path:'/zh/ask/'},{type:'fill',label:'业务问题',value:'查询采购订单'},
    {type:'click',role:'button',name:'查询'},{type:'wait_for',selector:'#status',state:'visible'},
    {type:'expect_text',selector:'#status',text:'查询完成'},
    {type:'capture_json',path:'/api/result',rows_pointer:'/rows',complete_pointer:'/complete',scope:{company:'1710'}},
    {type:'screenshot'}];
  for(const [name,fixtures,expected] of [
    ['simulated',[{path:'/api/runs',method:'POST',body:{run_id:'fixture'}}],'passed'],
    ['denied',[],'failed']
  ]) {
    const destination=path.join(output,name);await mkdir(destination,{recursive:true});
    const config={case:{id:name,mode:'simulated',actions,fixtures,action_timeout_ms:1000},target:{site_url:origin,api_url:origin},output:destination};
    const input=path.join(destination,'input.json');await writeFile(input,JSON.stringify(config));
    const entry=path.join(destination,'execute.mjs');
    const executor=new URL('../src/sap_business_agents_test_agent/assets/browser.mjs',import.meta.url).href;
    await writeFile(entry,`import {run} from ${JSON.stringify(executor)};await run(process.argv[2],${JSON.stringify(name)});`);
    // Async process keeps the HTTP fixture responsive while the browser runs.
    const {spawn}=await import('node:child_process');
    const process=spawn(globalThis.process.execPath,[entry,input],{stdio:'pipe',env:globalThis.process.env});
    let log='';process.stderr.on('data',data=>{log+=data});
    await new Promise((resolve,reject)=>{process.on('exit',resolve);process.on('error',reject)});
    const result=JSON.parse(await readFile(path.join(destination,'result.json'),'utf8'));
    assert.equal(result.status,expected,log);
    if(name==='simulated')assert.equal(JSON.parse(await readFile(path.join(destination,'observation.json'),'utf8')).rows[0].id,'0001');
  }
  assert.equal(realWrites,0);
} finally {await new Promise(resolve=>server.close(resolve));}
console.log('PASS: inline selection/start/fallback, duplicate-submit fence, narrow layout, browser capture, simulated external-write fence');
