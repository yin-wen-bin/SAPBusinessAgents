import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const path = new URL('../src/components/AgentAcceptance.tsx', import.meta.url);
const source = fs.readFileSync(path, 'utf8');
const ast = ts.createSourceFile('AgentAcceptance.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const statement = ast.statements.find(node => ts.isVariableStatement(node) && node.declarationList.declarations.some(d => d.name.getText(ast) === 'stageState'));
assert.ok(statement, 'Load the actual stage display function');
const compiled = ts.transpileModule(statement.getText(ast), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const stageState = vm.runInNewContext(compiled + '\nstageState');

test('a completed business comparison is verified even when the outer run is inconclusive', () => {
  for (const verdict of ['MATCH', 'MISMATCH']) {
    const item = { free_query_run_id: 'owned-run', result: { free_query: { status: 'inconclusive', comparison: { verdict } } } };
    assert.equal(stageState(item, 'free_query', true, 'zh'), '已完成');
    assert.equal(stageState(item, 'free_query', true, 'en'), 'Completed');
  }
});

test('an unverified or inconclusive comparison never claims completion', () => {
  for (const comparison of [undefined, {}, { verdict: 'INCONCLUSIVE' }]) {
    const item = { free_query_run_id: 'owned-run', result: { free_query: { status: 'inconclusive', comparison } } };
    assert.equal(stageState(item, 'free_query', true, 'zh'), '已启动，结果未核验');
  }
  assert.equal(stageState({ result: {} }, 'free_query', true, 'zh'), '未执行');
  assert.equal(stageState({ phase: 'free_query', result: {} }, 'free_query', false, 'zh'), '执行中');
});
