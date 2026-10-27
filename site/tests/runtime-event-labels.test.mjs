import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createContext,runInContext} from 'node:vm';

const source=await readFile(new URL('../src/components/RunConsole.astro',import.meta.url),'utf8');
const labels=source.slice(source.indexOf('const labels ='),source.indexOf('\n---',source.indexOf('const labels =')));
const renderer=source.slice(source.indexOf('  const eventDetail ='),source.indexOf('  const progressPhases ='));
class Node {
  children=[];textContent='';
  append(...items){this.children.push(...items);}
}
function harness(locale,provider='workbuddy') {
  const timeline=new Node();
  const context=createContext({locale,
    root:{querySelector:()=>timeline},document:{createElement:()=>new Node()},
    currentRun:{runtime:{provider_id:provider}},
    friendlyId:value=>String(value || ''),
    localized:value=>typeof value==='object' && value ? value[locale] || value.zh || value.en || '' : String(value || ''),
    titleCase:value=>value.split('_').map(word=>word[0].toUpperCase()+word.slice(1)).join(' '),
  });
  runInContext(`${labels}\nconst seenEvents=new Set();\n${renderer}\nglobalThis.render=appendEvent;`,context);
  return {timeline,render:context.render};
}

for(const locale of ['zh','en']) {
  for(const provider of ['codex','workbuddy'])test(`${locale}/${provider}: shared broker and Harness labels never imply Codex routing`,()=>{
    const ui=harness(locale,provider);
    for(const [index,name] of ['tool_requested','harness_completed','harness_interrupted'].entries()) {
      const payload={sequence:index+1,created_at:'2026-10-27T00:22:43Z',data:{provider_id:provider,content:'private SDK content'}};
      const original=JSON.stringify(payload);
      ui.render(name,payload);
      const heading=ui.timeline.children[index].children[1].children[0].textContent;
      assert.doesNotMatch(heading,/Codex|WorkBuddy/);
      assert.match(heading,index===0?/Agent Runtime/:/Harness/);
      assert.equal(JSON.stringify(payload),original,'rendering must not rewrite history');
      assert.doesNotMatch(JSON.stringify(ui.timeline),/private SDK content/);
    }
  });
  test(`${locale}: native connection and submission headings reuse localized platform labels without duplicate text`,()=>{
    const ui=harness(locale);
    for(const [index,name] of ['agent_runtime_connected','agent_runtime_query_submitted','agent_runtime_result_received','agent_runtime_sdk_closed'].entries()) {
      ui.render(name,{sequence:index+1,data:{content:'private SDK content'}});
      const content=ui.timeline.children[index].children[1];
      assert.equal(content.children.length,1,'do not repeat the same heading as detail');
      assert.doesNotMatch(content.children[0].textContent,/Agent Runtime Connected|Agent Runtime Query Submitted/);
      if(index===0)assert.match(content.children[0].textContent,locale==='zh'?/已连接 SDK/:/SDK connected/);
      if(index===1)assert.match(content.children[0].textContent,locale==='zh'?/问题已提交/:/Question submitted/);
    }
  });
  test(`${locale}: genuine Codex-specific events retain their provider name`,()=>{
    const ui=harness(locale,'codex');
    ui.render('codex_turn_started',{sequence:1,data:{}});
    assert.match(ui.timeline.children[0].children[1].children[0].textContent,/Codex/);
  });
  test(`${locale}: repeated sequence does not add another event and unknown names retain the existing fallback`,()=>{
    const ui=harness(locale);
    ui.render('tool_requested',{sequence:1,data:{}});
    ui.render('tool_requested',{sequence:1,data:{}});
    assert.equal(ui.timeline.children.length,1);
    ui.render('future_event',{sequence:2,data:{}});
    assert.equal(ui.timeline.children[1].children[1].children[0].textContent,'Future Event');
  });
}
