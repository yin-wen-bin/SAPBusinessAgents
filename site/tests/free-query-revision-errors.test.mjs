import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
const source=await readFile(new URL('../src/components/RunConsole.astro',import.meta.url),'utf8');
const script=source.slice(source.indexOf('  const failureGuidance ='),source.indexOf('  const fallbackBusinessReport ='));
for(const locale of ['zh','en'])for(const phase of ['runtime_binding','runtime_invocation']){
  test(`${locale}: revision failure keeps phase and never echoes untrusted SDK text (${phase})`,()=>{
    const context={locale};
    runInNewContext(script+';globalThis.guidance=failureGuidance;',context);
    const result=context.guidance({error:{code:'free_query_presentation_revision_failed',
      message:'private SDK text',detail:{phase,cause_code:'unknown',message:'secret'}}});
    assert.ok(result.headline && result.overview && result.action);
    assert.doesNotMatch(JSON.stringify(result),/private|secret|automatic retry will/i);
    if(phase==='runtime_binding')assert.match(result.headline,locale==='zh'?/绑定/:/binding/i);
  });
}
test('auth admission failure is not presented as confirmed sign-out',()=>{
  const context={locale:'zh'};
  runInNewContext(script+';globalThis.guidance=failureGuidance;',context);
  const result=context.guidance({error:{code:'free_query_presentation_revision_failed',
    detail:{phase:'runtime_binding',cause_code:'runtime_authentication_binding_changed'}}});
  assert.match(result.overview,/不能仅凭此提示认定账号已退出/);
});
