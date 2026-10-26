import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import {createContext,runInContext} from 'node:vm';
const source=await readFile(new URL('../src/components/RunConsole.astro',import.meta.url),'utf8');
const script=source.slice(source.indexOf('  const recoveryMessage ='),source.indexOf('  root?.querySelector("[data-feedback-cancel]")'));
function harness({locale='zh',lost=false,found=true,rejected=false}={}) {
  const nodes=new Map(),calls=[],connected=[];
  const node=()=>({hidden:true,dataset:{},value:'',handlers:{},textContent:'',disabled:false,
    addEventListener(name,fn){this.handlers[name]=fn;},focus(){},reset(){this.value='';},querySelector(selector){return root.querySelector('form '+selector);}});
  const root={querySelector(selector){if(!nodes.has(selector))nodes.set(selector,node());return nodes.get(selector);},querySelectorAll(){return [];}};
  const form=root.querySelector('[data-session-feedback]');form.value='Explain the confirmed evidence';
  root.querySelector('[data-session-recovery-source]').value=JSON.stringify({iteration:1,kind:'saved_result',context_digest:'sha256:'+'a'.repeat(64)});
  const feedback={feedback_request_id:'original-request',status:'iteration_created',phase:'completed'};
  let canFind=found;
  const context=createContext({root,locale,crypto:webcrypto,TextEncoder,Uint8Array,
    currentSession:{current_iteration:2},sessionId:'session-1',apiBase:'http://test',selectedFeedbackType:'',
    FormData:class {constructor(form){this.form=form;}get(){return this.form.value;}},
    queryLifecycle:{claim(){}},labels:{freeQuerySession:{feedbackSteps:['received'],feedbackPrompt:'feedback'}},
    renderFeedbackProgress(){},connectFeedback(id){connected.push(id);},loadSession:async()=>{},openFeedbackIteration:async()=>false,
    fetch:async(url,options={})=>{
      calls.push({url,method:options.method||'GET',body:options.body&&JSON.parse(options.body)});
      if(options.method==='POST'){
        if(rejected)return {ok:false,status:409,json:async()=>({detail:{code:'free_query_recovery_cleanup_pending'}})};
        if(lost)throw new Error('network result unknown');
      }
      return {ok:options.method==='POST'||canFind,status:canFind?200:404,json:async()=>feedback};
    }});
  runInContext(script,context);
  return {nodes,calls,connected,form,setFound(value){canFind=value;},
    open(){return root.querySelector('[data-session-resume]').handlers.click();},
    submit(){return form.handlers.submit({preventDefault(){},currentTarget:form});},
    check(){return root.querySelector('[data-recovery-check]').handlers.click();}};
}
for(const locale of ['zh','en'])test(`${locale}: source selection never submits; explicit submit carries frozen context`,async()=>{
  const h=harness({locale});h.open();assert.equal(h.calls.length,0);assert.equal(h.form.dataset.mode,'recovery');
  await h.submit();assert.equal(h.calls.filter(x=>x.method==='POST').length,1);
  const body=h.calls[0].body;assert.equal(body.baseIteration,2);assert.equal(body.recoveryIteration,1);
  assert.equal(body.recoveryKind,'saved_result');assert.match(body.requestId,/^[\w-]{16,128}$/);
  assert.equal(h.form.hidden,true);
});
test('lost response is recovered by GET of the original request, without another POST',async()=>{
  const h=harness({lost:true});h.open();await h.submit();
  assert.deepEqual(h.calls.map(x=>x.method),['POST','GET']);
  assert.match(h.calls[1].url,/\/feedback-requests\/fqfeedback_[0-9a-f]{20}$/);
  assert.equal(h.form.hidden,true);assert.deepEqual(h.connected,['original-request']);
});
test('unknown result retains original input and blocks new dispatches',async()=>{
  const h=harness({lost:true,found:false});h.open();await h.submit();await h.submit();
  assert.equal(h.calls.filter(x=>x.method==='POST').length,1);assert.equal(h.form.value,'Explain the confirmed evidence');
  assert.equal(h.nodes.get('[data-recovery-check]').hidden,false);
  h.form.value='changed question';await h.submit();assert.equal(h.calls.filter(x=>x.method==='POST').length,1);
  h.setFound(true);await h.check();assert.equal(h.form.hidden,true);
});
test('cleanup blocker remains clear and input is retained',async()=>{
  const h=harness({rejected:true});h.open();await h.submit();
  assert.equal(h.calls.length,1);assert.equal(h.form.value,'Explain the confirmed evidence');
  assert.match(h.nodes.get('[data-session-feedback-status]').textContent,/清理状态/);
});
