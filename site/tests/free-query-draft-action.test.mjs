import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
const source=await readFile(new URL('../src/components/RunConsole.astro',import.meta.url),'utf8');
const script=source.slice(source.indexOf('  let sourceDraftId ='),source.indexOf('  root?.querySelector("[data-import-draft]")'));
for(const locale of ['zh','en']) {
 test(`${locale}: successful generation opens the management draft, never its source ID`,()=>{
  const nodes=new Map();
  const root={dataset:{managementPath:`/${locale}/agent-management/`},querySelector(selector){
   if(!nodes.has(selector))nodes.set(selector,{hidden:true,textContent:'',removeAttribute(name){delete this[name];}});
   return nodes.get(selector);
  }};
  const context={root,locale,sessionId:'session',labels:{unavailable:'Unavailable',importFailed:'Import failed',sourceDraftReady:'Ready'}};
  runInNewContext(script+';globalThis.ui={show:showDraftManagement,error:draftFailureMessage};',context);
  context.ui.show({draft_id:'source-draft',managed_draft_id:'agent_draft_123'});
  const link=nodes.get('[data-session-draft-view]');
  assert.equal(link.hidden,false);assert.equal(link.href,`/${locale}/agent-management/?draft=agent_draft_123`);
  assert.equal(nodes.get('[data-session-draft]').hidden,true);
  assert.equal(nodes.get('[data-session-actions]').hidden,false);
  assert.equal(nodes.get('[data-import-draft]').hidden,true);
  context.ui.show({draft_id:'source-draft',managed_draft_id:null,management_import_status:'failed'});
  assert.equal(link.hidden,true);assert.equal(link.href,undefined);
  assert.equal(nodes.get('[data-import-draft]').hidden,false);
  context.ui.show({draft_id:'source-draft',managed_draft_id:{malformed:true}});
  assert.equal(link.hidden,true);assert.doesNotMatch(String(link.href),/object Object/);
 });
 test(`${locale}: structured errors never become button labels or object strings`,()=>{
  const context={locale,labels:{unavailable:'Unavailable'}};
  runInNewContext(script+';globalThis.message=draftFailureMessage;',context);
  assert.equal(context.message({detail:{code:'failure',message:'Readable failure'}}),'Readable failure');
  assert.equal(context.message({detail:{message:{nested:true}}}),'Unavailable');
  assert.equal(context.message({detail:'Plain failure'}),'Plain failure');
  assert.match(context.message({detail:{blocker:'query_not_successful'}}),locale==='zh'?/暂不能生成/:/cannot be generated/);
  assert.doesNotMatch(context.message({detail:{code:'failure'}}),/object Object/);
 });
}
test('generation failures leave action labels stable; draft-created sessions retain navigation',()=>{
 assert.doesNotMatch(source,/button\.textContent = response\.ok.*String\(payload\.detail/);
 assert.doesNotMatch(source,/actions\.hidden = session\.status==='draft_created'/);
 assert.match(source,/data-session-draft-view/);
 assert.match(source,/manageDraft: "查看固定Agent草稿"/);
});

for(const locale of ['zh','en'])test(`${locale}: user acceptance never grants draft eligibility and blockers remain readable`,()=>{
 const context={locale};
 const projection=source.slice(source.indexOf('  const draftConversionBlocker ='),source.indexOf('  const renderFreeQuerySession ='));
 runInNewContext(projection+';globalThis.message=draftConversionBlocker;',context);
 const session={status:'satisfied',can_create_draft:false,draft_blocker:'query_not_successful'};
 const text=context.message(session,{status:'inconclusive'});
 assert.match(text,locale==='zh'?/只记录用户确认，不能代替业务校验/:/confirmation, not business validation/);
 assert.ok(context.message({...session,draft_blocker:'incomplete_evidence'},{}));
 assert.ok(context.message({...session,draft_blocker:'invalid_execution_plan'},{}));
 assert.ok(context.message({status:'satisfied'},{}),'unknown eligibility must not grant conversion');
 assert.equal(context.message({...session,can_create_draft:true},{}),'');
 assert.match(source,/draftButton\.disabled = session\.can_create_draft !== true/);
});
