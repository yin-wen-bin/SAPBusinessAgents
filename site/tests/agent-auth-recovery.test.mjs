import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { feedbackFailureText, feedbackValidationIssues, prepareFeedbackRequest } from '../src/lib/agentDraft.ts';

test('authentication failure explains the blocked stage in both languages', () => {
  for (const locale of ['zh','en']) {
    for (const code of ['runtime_authentication_check_required','runtime_authentication_binding_changed',
      'workbuddy_authentication_confirmation_required','workbuddy_authentication_required',
      'workbuddy_authentication_refresh_failed','runtime_authentication_operation_active']) {
      const text=feedbackFailureText(code,locale);
      assert.ok(text.length>25);
      assert.doesNotMatch(text,/本轮未完成。请核对|This turn did not complete/);
      assert.doesNotMatch(text,/token=|https?:/);
    }
  }
  assert.match(feedbackFailureText('runtime_authentication_check_required','zh'),/不能认定账号已退出/);
  assert.match(feedbackFailureText('runtime_authentication_binding_changed','en'),/no account or model is switched/);
});

test('recheck uses the existing site-bound authentication route, not draft submission',async()=>{
  const source=await readFile(new URL('../src/components/AgentDraftWorkspace.tsx',import.meta.url),'utf8');
  const start=source.indexOf('const recheckWorkbuddyAuthentication');
  const end=source.indexOf('const submitFeedback',start);
  const handler=source.slice(start,end);
  assert.match(handler,/\/api\/system\/sdk-runtimes\/workbuddy\/authentication\/check/);
  assert.match(handler,/X-SAPBA-Action/);assert.match(handler,/X-SAPBA-Service-Site-Digest/);
  assert.doesNotMatch(handler,/submitFeedback\(|\/feedback|\/login|\/operations|model-check/);
  assert.match(source,/Recheck WorkBuddy authentication/);
  assert.match(source,/disabled=\{busy \|\| feedbackTaskActive \|\| uncertainFeedback\}/);
});

test('authentication recheck cannot silently rewrite an uncertain edit request',()=>{
  const base={baseTurn:3,baseRevision:1,intent:'revise',feedback:'rename',locale:'zh',
    executionMode:'trusted_local',trustedLocalConfirmed:true};
  const old=prepareFeedbackRequest(null,base,()=> 'original-request');
  assert.equal(prepareFeedbackRequest(old,base,()=> 'must-not-create'),old);
  assert.equal(old.requestId,'original-request');
});

test('progress dialog renders safe validation reasons instead of an empty below hint',async()=>{
  const source=await readFile(new URL('../src/components/AgentFeedbackProgress.tsx',import.meta.url),'utf8');
  assert.match(source,/feedbackValidationIssues\(turn, locale\)/);
  assert.match(source,/validationIssues\.map/);
  assert.match(source,/Definition validation blockers/);
  for (const locale of ['zh','en']) {
    const issues=feedbackValidationIssues({decision:{validation_issues:[{code:'agent_business_output_contract_missing',path:'/manifest/execution/outputSchema'}]}},locale);
    assert.equal(issues[0].path,'/manifest/execution/outputSchema');
    assert.match(issues[0].text,locale==='zh'?/确定性业务规则/:/deterministic business rule/);
  }
});

test('account reference recovery does not claim current authentication and never retries from the dialog',async()=>{
  const source=await readFile(new URL('../src/components/AgentFeedbackProgress.tsx',import.meta.url),'utf8');
  assert.match(source,/current authentication is a separate check/);
  assert.match(source,/authenticationBlocked \? onClose : onRetry/);
  assert.match(source,/Return to check authentication/);
});
