import assert from 'node:assert/strict';
import test from 'node:test';
import { ListResource } from '../src/lib/agentManagement.ts';

test('manual refresh starts a new read and a late old reply cannot hide the new draft', async () => {
  const calls = [];
  const resource = new ListResource(signal => new Promise(resolve => calls.push({ signal, resolve })), () => {});
  try {
    const old = resource.refresh();
    await Promise.resolve();
    const manual = resource.refresh(true);
    await Promise.resolve();
    assert.equal(calls.length, 2, 'Manual refresh must not coalesce with an older request');
    assert.equal(calls[0].signal.aborted, true);
    calls[1].resolve([{ draft_id: 'new-draft' }]);
    await manual;
    calls[0].resolve([]);
    await old;
    assert.equal(resource.state.data[0].draft_id, 'new-draft');
  } finally {
    resource.dispose();
    for (const call of calls) call.resolve([]);
  }
});

test('a stalled read times out visibly and permits the next read', async () => {
  const calls = [];
  const resource = new ListResource(signal => new Promise(resolve => calls.push({ signal, resolve })),
    () => {}, { timeoutMs: 25 });
  try {
    const stalled = resource.refresh();
    await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(resource.state.loading, false, 'An unresponsive read must release loading');
    assert.ok(resource.state.error, 'Timeout must be visible rather than successful empty data');
    assert.equal(calls[0].signal.aborted, true);
    const retry = resource.refresh(true);
    await Promise.resolve();
    assert.equal(calls.length, 2);
    calls[1].resolve([{ draft_id: 'recovered' }]);
    await retry;
    assert.equal(resource.state.error, '');
    assert.equal(resource.state.data[0].draft_id, 'recovered');
    calls[0].resolve([]);
    await stalled;
  } finally {
    resource.dispose();
    for (const call of calls) call.resolve([]);
  }
});
