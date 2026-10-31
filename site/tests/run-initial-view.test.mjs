import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createContext, runInContext} from 'node:vm';

const source = (await readFile(new URL('../src/components/RunConsole.astro', import.meta.url), 'utf8')).replace(/\r\n/g,'\n');
const alertSource = source.slice(source.indexOf('  const showAlert ='), source.indexOf('  const localized ='));
const readySource = source.slice(source.indexOf('    // Only reveal a real, fully rendered run;'), source.indexOf('\n  };\n\n  const readRun ='));

function view(state='loading') {
  const loading={hidden:false}, alert={hidden:true,textContent:'',dataset:{}};
  const root={dataset:{runInitialState:state},attributes:{'aria-busy':'true'},
    setAttribute(key,value){this.attributes[key]=value;},
    querySelector(selector){return selector==='[data-run-alert]'?alert:loading;}};
  const context=createContext({root,queryLifecycle:undefined,run:{run_id:'offline'},labels:{unavailable:'connection unknown'}});
  runInContext(`${alertSource}\nglobalThis.fail=showAlert; globalThis.ready=()=>{${readySource}};`, context);
  return {root,loading,alert,context};
}

test('initial reads keep the query header but no separate loading page or fabricated progress',()=>{
  assert.match(source,/<section class="run-console"[^>]*data-run-initial-state="loading"[^>]*aria-busy="true"/);
  assert.doesNotMatch(source,/data-run-loading|正在读取本次查询|Loading this query/);
  assert.match(source,/\.run-console:not\(\[data-run-initial-state="ready"\]\) > :not\(\.run-console-header\):not\(\[data-run-alert\]\):not\(\.run-actions\):not\(noscript\) \{ display: none; \}/);
  assert.match(source,/initialStatus: "查询状态待核对"/);
  assert.match(source,/initialStatus: "Query status awaiting confirmation"/);
});

test('initial read failure shows an error without exposing a fabricated progress page',()=>{
  const ui=view();ui.context.fail('Read outcome unknown; do not resubmit');
  assert.equal(ui.root.dataset.runInitialState,'error');
  assert.equal(ui.root.attributes['aria-busy'],'false');
  assert.equal(ui.alert.hidden,false);
});

test('a successful original-run read reveals the real page, including after reconnect',()=>{
  for(const state of ['loading','error']) {
    const ui=view(state);ui.context.ready();
    assert.equal(ui.root.dataset.runInitialState,'ready');
    assert.equal(ui.root.attributes['aria-busy'],'false');
  }
  const render=source.slice(source.indexOf('  const renderRun ='),source.indexOf('  const readRun ='));
  assert.ok(render.indexOf('root.dataset.runInitialState = "ready"')>render.indexOf('renderBusinessReport(run)'));
  assert.ok(render.indexOf('root.dataset.runInitialState = "ready"')>render.indexOf('stopProgressUpdates()'));
});

test('later connection failures keep the already-confirmed result visible',()=>{
  const ui=view();ui.context.ready();ui.context.fail('Reconnect to check the original task');
  assert.equal(ui.root.dataset.runInitialState,'ready');
  assert.equal(ui.alert.hidden,false);
});

test('the change adds no navigation, query dispatch, model call or cancellation',()=>{
  assert.doesNotMatch(alertSource+readySource,/fetch\(|location\.|\.cancel\(|EventSource|POST/);
  assert.match(source,/Promise\.all\(\[loadRun\(\), sessionId \? loadSession\(\)/);
  assert.match(source,/error.status === 404 \? labels.historyExpired : labels.unavailable/);
  assert.match(source,/root.querySelector\("\[data-reconnect\]"\).hidden = false/);
});

test('status read diagnostics distinguish transport, decoding and rendering without raw errors',async()=>{
  const readSource=source.slice(source.indexOf('  const readRun ='),source.indexOf('  const loadRun ='));
  for(const [failure,code] of [['network','network'],['decode','invalid_response'],['render','render_failed'],['http','http_503'],['timeout','timeout']]) {
    const root={dataset:{},querySelector:()=>null};
    const context=createContext({root,runId:'offline',sessionId:null,apiBase:'http://offline.invalid',AbortController,
      window:{setTimeout:()=>1,clearTimeout(){}},
      fetch:async()=>{
        if(failure==='network') throw Error('private transport detail');
        if(failure==='timeout') throw Object.assign(Error('private timeout detail'),{name:'AbortError'});
        return {ok:failure!=='http',status:503,json:async()=>{
          if(failure==='decode') throw Error('private response body');
          return {run_id:'offline'};
        }};
      },
      renderRun(){if(failure==='render') throw Error('private rendering detail');},
    });
    runInContext(`${readSource}\nglobalThis.read=readRun;`,context);
    await assert.rejects(context.read());
    assert.equal(root.dataset.statusReadError,code);
    assert.doesNotMatch(JSON.stringify(root.dataset),/private/);
  }
});
