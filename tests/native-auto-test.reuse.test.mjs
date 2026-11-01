import test from 'node:test';
import assert from 'node:assert/strict';
import {QueryWriteGuard} from '../src/sap_business_agents_test_agent/assets/query-guard.mjs';

test('existing platform only accepts a single owned free-query submission',()=>{
  const guard=new QueryWriteGuard();
  assert.equal(guard.allow('POST','/api/runs',{mode:'agent',query:'x'}),false);
  assert.equal(guard.allow('POST','/api/runs',{mode:'free_query',query:'x',agent_id:'foreign'}),false);
  assert.equal(guard.allow('POST','/api/runs',{mode:'free_query',query:'x'}),true);
  assert.equal(guard.allow('POST','/api/runs',{mode:'free_query',query:'x'}),false);
});
test('foreign runs and sessions cannot be cancelled or continued',()=>{
  const guard=new QueryWriteGuard();
  guard.allow('POST','/api/runs',{mode:'free_query',query:'x'});
  guard.observe('/api/runs',{run_id:'owned',session_id:'session'});
  assert.equal(guard.allow('POST','/api/free-query-sessions',{sourceRunId:'owned'}),true);
  guard.observe('/api/free-query-sessions',{session_id:'new-session'});
  assert.equal(guard.allow('POST','/api/free-query-sessions/new-session/feedback'),true);
  assert.equal(guard.allow('POST','/api/runs/owned/cancel'),true);
  assert.equal(guard.allow('POST','/api/runs/foreign/cancel'),false);
  assert.equal(guard.allow('POST','/api/free-query-sessions/session/feedback'),true);
  assert.equal(guard.allow('POST','/api/free-query-sessions/foreign/feedback'),false);
  guard.observe('/api/free-query-sessions/foreign',{run_id:'foreign'});
  assert.equal(guard.allow('POST','/api/runs/foreign/cancel'),false);
  guard.observe('/api/free-query-sessions/session',{current_run_id:'round2'});
  assert.equal(guard.allow('POST','/api/runs/round2/cancel'),true);
});
test('query access never authorizes lifecycle, accounts or external mutations',()=>{
  const guard=new QueryWriteGuard();guard.sessions.add('owned');
  for(const path of ['/api/free-query-sessions/owned/agent-draft','/api/free-query-sessions/owned/accept',
      '/api/agent-drafts/x/publish','/api/system/authentication/codex/login','/api/integration-actions/x/execute'])
    assert.equal(guard.allow('POST',path),false,path);
  assert.equal(guard.allow('DELETE','/api/runs/owned'),false);
});
