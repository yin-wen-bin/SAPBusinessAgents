// Offline browser regression: all network requests are intercepted; no live API.
import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import ts from "typescript";
import { build } from "vite";
const { chromium } = createRequire(import.meta.url)(process.env.SAPBA_PLAYWRIGHT_MODULE || "playwright");
const destination = process.env.SAPBA_BROWSER_REPORT;
assert.ok(destination); await mkdir(destination, { recursive: false });
const component = name => path.resolve("src/components", name + ".tsx").replaceAll("\\", "/");
const entry = `import React from 'react'; import {createRoot} from 'react-dom/client';
import Draft from '${component("AgentDraftWorkspace")}'; import Workflow from '${component("WorkflowAssistant")}';
import '${path.resolve("src/styles/global.css").replaceAll("\\", "/")}';
import '${path.resolve("src/styles/assistant-compact.css").replaceAll("\\", "/")}';
const root=createRoot(document.getElementById('root')); let kind='draft',locale='zh';
const turns=Array.from({length:15},(_,i)=>({turn:i+1,kind:'feedback',status:'completed',base_revision:i+1,result_revision:i+2,user_message:'调整名称 '+(i+1),diff:[{path:'/manifest/title/zh',before:'原名称',after:'新名称'}],decision:{action:'revise_agent',presentation_version:i===14?'business-summary/1':undefined,summary:{zh:i===14?'已调整业务名称，请核对修改。':'LEGACY_PRIVATE_TECHNICAL',en:i===14?'Business name adjusted. Review the changes.':'LEGACY_PRIVATE_TECHNICAL'}}}));
window.draft={draft_id:'synthetic',agent_id:'example',revision:16,status:'draft',target_version:'0.1.0',package:{manifest:{slug:'example',module:'Common',title:{zh:'示例',en:'Example'},summary:{zh:'离线夹具',en:'Offline fixture'},version:'0.1.0',execution:{inputSchema:{type:'object',properties:{}}}},readme:'',rules:'',files:{}},conversation:turns,technical_identity:{kind:'new_agent',confirmed:true,locked:false,can_rename:true},publishability:{can_publish:false,blockers:[]}};
window.rounds=turns.map((t,i)=>({request_id:String(i),intent:'revise',message:t.user_message,status:t.status,base_revision:t.base_revision,result_revision:t.result_revision,diff:t.diff,presentation_version:t.decision.presentation_version,answer:t.decision.summary.zh}));
const noop=()=>{};
window.renderHost=(k=kind,l=locale)=>{kind=k;locale=l; root.render(kind==='draft'?React.createElement(Draft,{initialDraft:window.draft,locale,apiBase:'',runPath:'/run',onBack:noop,onPublished:noop}):React.createElement('div',{className:'workflow-builder-shell'},React.createElement('main',null,'Workflow canvas'),React.createElement(Workflow,{locale,apiBase:'',draftId:'synthetic',revision:16,step:'review',published:false,dirty:false,busy:false,prepare:async()=>({draft_id:'synthetic',revision:16}),refresh:noop})));}; window.renderHost();`;
const output = await build({ configFile: false, root: process.cwd(), logLevel: "error",
  plugins: [{ name: "synthetic-compact", resolveId: id => id === "virtual:compact" ? "\0compact" : null,
    load: id => id === "\0compact" ? entry : null,
    transform(code, id) { if (id.endsWith(".tsx")) return ts.transpileModule(code, { compilerOptions: {
      jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022,
    } }).outputText; } }],
  build: { write: false, minify: false, rollupOptions: { input: "virtual:compact" } } });
const script = output.output.filter(item => item.type === "chunk").map(item => item.code).join("\n");
const styles = output.output.filter(item => item.type === "asset" && item.fileName.endsWith(".css")).map(item => item.source).join("\n");
const browser = await chromium.launch({ headless: true, channel: process.platform === "win32" ? "msedge" : undefined });
const records = [];
try {
  for (const kind of ["draft", "workflow"]) for (const locale of ["zh", "en"]) for (const width of [1440, 690, 550, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 900 } });
    const page = await context.newPage(); const errors = []; page.on("pageerror", error => errors.push(error.message));
    await page.route("**/*", route => route.request().url() === "http://127.0.0.1:4339/"
      ? route.fulfill({ contentType: "text/html; charset=utf-8", body: '<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>'+styles+'</style></head><body><div id="root"></div></body></html>' }) : route.abort());
    await page.addInitScript(() => {
      window.posts=[]; window.streams=[];
      window.EventSource=class {constructor(){this.listeners={};window.streams.push(this);}addEventListener(k,f){this.listeners[k]=f;}close(){}};
      window.fetch=async(url,options)=>{if(options?.method && options.method!=='GET'){window.posts.push(url);if(!String(url).endsWith('/ui-state'))throw Error('No business mutation allowed');}
        return {ok:true,json:async()=>String(url).includes('/assistant')?{rounds:window.rounds,events:[]}:window.draft};};
      window.confirm=()=>false;
    });
    await page.goto("http://127.0.0.1:4339/"); await page.addScriptTag({ type: "module", content: script });
    await page.evaluate(([k,l])=>window.renderHost(k,l),[kind,locale]);
    if (kind === "draft") await page.locator(".draft-assistant-toggle").click();
    else if (width < 1200) await page.locator(".workflow-assistant-toggle").click();
    const aside=page.locator(kind==="draft"?".draft-assistant":".workflow-assistant-v2");
    await aside.waitFor({state:"visible"});
    const history=aside.locator(".assistant-history-toggle"); await history.waitFor();
    assert.equal(await history.getAttribute("aria-expanded"),"false");
    assert.equal(await aside.locator(".assistant-history-older:visible").count(),0);
    assert.equal(await aside.locator(".assistant-history-latest:visible").count(),1);
    assert.equal(await aside.getByText("技术详情",{exact:true}).count(),0);
    assert.equal(await aside.locator(".draft-turn-context").count(),0);
    assert.equal(await aside.getByText("LEGACY_PRIVATE_TECHNICAL",{exact:true}).count(),0);
    const annotation=aside.locator("header .assistant-annotation-trigger");
    assert.equal(await annotation.isVisible(),true);
    assert.match(await annotation.innerText(),locale==="zh"?/页面标注/:/Annotate page/);
    await history.click(); const old=aside.locator(".assistant-history-older").first();
    await old.locator("summary").first().click();
    await page.evaluate(()=>window.renderHost());
    await page.evaluate(k=>window.streams.forEach(s=>s.listeners[k==="draft"?"phase":"workflow_assistant"]?.(new Event("phase"))),kind);
    await page.waitForTimeout(180);
    assert.equal(await history.getAttribute("aria-expanded"),"true");
    assert.equal(await old.locator("details").first().getAttribute("open"),"");
    await history.click();
    const textarea=aside.locator("textarea").last(); await textarea.fill("unsent draft");
    const separator=aside.getByRole("separator");
    if(width>550 && await separator.isVisible()){
      if(kind==="workflow" && width<1200){
        await aside.locator("button:not(:disabled)").last().focus(); await page.keyboard.press("Tab");
        assert.equal(await separator.evaluate(el=>document.activeElement===el),true);
      }
      const before=await aside.boundingBox(); await separator.focus(); await page.keyboard.press("ArrowLeft");
      await page.waitForTimeout(80); const after=await aside.boundingBox();
      assert.ok(after.width>=before.width+15 && after.width<=640.5,JSON.stringify({kind,width,before,after}));
      const grip=await separator.boundingBox();
      await page.mouse.move(grip.x+4,grip.y+20); await page.mouse.down(); await page.mouse.move(grip.x-30,grip.y+20,{steps:5}); await page.mouse.up();
      assert.equal(await textarea.inputValue(),"unsent draft");
      const saved=await page.evaluate(k=>localStorage.getItem('assistant-width:'+k),kind); assert.ok(Number(saved)>=320);
      await page.setViewportSize({width:390,height:900}); await page.waitForTimeout(80);
      assert.equal(await separator.isVisible(),false);
      assert.equal(await page.evaluate(k=>localStorage.getItem('assistant-width:'+k),kind),saved);
      await page.setViewportSize({width,height:900}); await page.waitForTimeout(80);
      await separator.dblclick({position:{x:4,y:20}});
    } else if(width<=550) assert.equal(await separator.isVisible(),false);
    assert.equal(await textarea.inputValue(),"unsent draft");
    const box=await aside.boundingBox(); assert.ok(box.x>=-1 && box.x+box.width<=width+1);
    assert.deepEqual(await page.evaluate(()=>window.posts.filter(url=>!String(url).endsWith('/ui-state'))),[]);
    assert.deepEqual(errors,[]);
    await page.screenshot({path:path.join(destination,kind+"-"+locale+"-"+width+".png")});
    records.push({kind,locale,width,result:"PASS"}); await context.close();
  }
} finally { await browser.close(); await writeFile(path.join(destination,"report.json"),JSON.stringify(records,null,2)); }
console.log(JSON.stringify({passed:records.length}));
