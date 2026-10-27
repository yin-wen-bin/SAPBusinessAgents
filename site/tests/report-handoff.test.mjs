import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
const source=await readFile(new URL('../src/components/RunConsole.astro',import.meta.url),'utf8');
const script=source.slice(source.indexOf('  const executionInterruption ='),source.indexOf('  const progressIndex ='));
for(const locale of ['zh','en'])for(const provider of ['codex','workbuddy'])for(const cleanup of [true,false]) {
 test(`${locale} ${provider}: terminal fault and cleanup are distinct from business gaps (${cleanup})`,()=>{
  const context={locale};runInNewContext(script+';globalThis.view=executionInterruption;',context);
  const run={mode:'free_query',status:'inconclusive',runtime:{provider_id:provider},result:{
   completeness:{source_complete:true,business_complete:false,missing_evidence:['runtime_structured_output_missing']},
   harness:{diagnostics:{terminal_handoff_failure:{code:'runtime_structured_output_missing',cleanup_complete:cleanup,
    candidate_report_refs:['private-ref'],message:'private upstream text'}}}}};
  const view=context.view(run);
  assert.equal(view.tone,'error');assert.match(view.headline,locale==='zh'?/交接失败/:/handoff failed/i);
  assert.match(view.next_actions[0],locale==='zh'?(cleanup?/清理已确认/:/清理尚未确认/):(cleanup?/is confirmed/:/not confirmed/));
  assert.doesNotMatch(JSON.stringify(view),/private|business result is empty/);
  delete run.result.harness.diagnostics.terminal_handoff_failure;
  assert.equal(context.view(run),null,'normal evidence insufficiency is not an execution failure');
 });
}
