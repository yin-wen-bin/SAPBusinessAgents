import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ts from 'typescript';
import * as helpers from '../src/lib/agentDraft.ts';
const {feedbackFailureText, feedbackValidationIssues, canRetryFeedback}=helpers;

test('both locales expose real inherited-blocker causes rather than authentication or generic retry',()=>{
  for(const locale of ['zh','en']) {
    const message=feedbackFailureText('agent_business_output_contract_missing',locale);
    assert.match(message,locale==='zh'?/未保存/:/No revision was saved/);
    assert.doesNotMatch(message,/系统配置后重试|system settings before retrying/);
    const issues=feedbackValidationIssues({decision:{error_code:'agent_business_output_contract_missing',
      validation_issues:[{code:'agent_business_output_contract_missing',path:'/manifest/execution/outputSchema',
        message:'PRIVATE raw SDK output'}]}},locale);
    assert.equal(issues[0].path,'/manifest/execution/outputSchema');
    assert.match(issues[0].text,locale==='zh'?/确定性业务规则/:/deterministic business rule/);
    assert.doesNotMatch(JSON.stringify(issues),/PRIVATE/);
    assert.doesNotMatch(feedbackFailureText('agent_harness_no_progress',locale),/本轮未完成|This turn did not complete/);
  }
});

test('historical failures without structural evidence do not acquire invented causes',()=>{
  assert.deepEqual(feedbackValidationIssues({decision:{error_code:'agent_harness_no_progress'}},'zh'),[]);
  assert.doesNotMatch(feedbackFailureText('PRIVATE traceback','en'),/PRIVATE|traceback/);
});

test('an unresolved inherited blocker does not offer an identical generic retry',()=>{
  const old={kind:'feedback',status:'failed',turn:8,user_message:'Change the inputs',decision:{}};
  assert.equal(canRetryFeedback(old),true);
  assert.equal(canRetryFeedback({...old,decision:{authoring_convergence:{stop_reason:'preexisting_blocker_unchanged'}}}),false);
});

test('actual progress dialog renders provider-neutral causes and a return-to-feedback action',async()=>{
  const source=await readFile(new URL('../src/components/AgentFeedbackProgress.tsx',import.meta.url),'utf8');
  const output=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS}});
  const module={exports:{}},require=createRequire(import.meta.url);
  new Function('require','module','exports',output.outputText)(name=>name==='../lib/agentDraft'?helpers:require(name),module,module.exports);
  for(const provider of ['codex','workbuddy']) for(const locale of ['zh','en']) {
    const turn={kind:'feedback',status:'failed',turn:8,user_message:'Change the inputs',decision:{
      error_code:'agent_business_output_contract_missing',runtime_snapshot:{provider_id:provider,model:'offline-model'},
      authoring_convergence:{stop_reason:'preexisting_blocker_unchanged',attempts:1},
      validation_issues:[{code:'agent_business_output_contract_missing',path:'/manifest/execution/outputSchema',message:'PRIVATE raw'}]}};
    const html=renderToStaticMarkup(createElement(module.exports.default,{open:true,turn,locale,
      onClose(){},onCancel(){},onRetry(){throw Error('Rendering cannot retry');},onReview(){}}));
    assert.match(html,locale==='zh'?/本轮修改未保存/:/No revision was saved/);
    assert.match(html,/\/manifest\/execution\/outputSchema/);
    assert.match(html,locale==='zh'?/返回修改意见/:/Return to feedback/);
    assert.doesNotMatch(html,/PRIVATE|返回并重试|Return and retry/);
  }
});
