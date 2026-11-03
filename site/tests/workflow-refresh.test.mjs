import assert from 'node:assert/strict';
import test from 'node:test';
import {createWorkflowRefresh} from '../src/lib/workflowRefresh.ts';
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('a validation event burst has one active read and one trailing latest read',async()=>{
  const errors=[],calls=[];let release;
  const queue=createWorkflowRefresh(error=>errors.push(error));
  const first=queue.request('draft',async()=>{calls.push('first');await new Promise(resolve=>release=resolve);});
  await tick();
  for(let i=0;i<100;i++)queue.request('draft',async()=>{calls.push(i);});
  assert.deepEqual(calls,['first']);
  release();await first;
  assert.deepEqual(calls,['first',99]);assert.deepEqual(errors,[]);
});

test('switching draft aborts stale reads before they can overwrite the new draft',async()=>{
  const writes=[],errors=[];let release,oldSignal;
  const queue=createWorkflowRefresh(error=>errors.push(error));
  const first=queue.request('old',async signal=>{oldSignal=signal;await new Promise(resolve=>release=resolve);if(!signal.aborted)writes.push('old');});
  await tick();queue.request('new',async()=>{writes.push('new');});
  assert.equal(oldSignal.aborted,true);release();await first;
  assert.deepEqual(writes,['new']);assert.deepEqual(errors,[]);
});

test('unmount cancels pending work; a restored owner can start a fresh read',async()=>{
  const writes=[];let release;
  const queue=createWorkflowRefresh(error=>{throw error;});
  const first=queue.request('draft',async signal=>{await new Promise(resolve=>release=resolve);if(!signal.aborted)writes.push('late');});
  await tick();queue.request('draft',async()=>{writes.push('discarded');});queue.cancel();
  release();await first;assert.deepEqual(writes,[]);
  await queue.request('draft',async()=>{writes.push('fresh');});assert.deepEqual(writes,['fresh']);
});

test('an actual read error is reported and does not lose the latest event',async()=>{
  const errors=[],calls=[];let release;
  const queue=createWorkflowRefresh(error=>errors.push(error.message));
  const first=queue.request('draft',async()=>{await new Promise(resolve=>release=resolve);throw new Error('read failed');});
  await tick();queue.request('draft',async()=>{calls.push('latest');});release();await first;
  assert.deepEqual(errors,['read failed']);assert.deepEqual(calls,['latest']);
});

test('assistant history replay releases the active network read on publication unmount',async()=>{
  const errors=[],writes=[];let readsStarted=0,aborted=0;
  const queue=createWorkflowRefresh(error=>errors.push(error.message));
  const networkRead=signal=>new Promise((resolve,reject)=>{
    readsStarted++;
    signal.addEventListener('abort',()=>{aborted++;reject(new DOMException('Aborted','AbortError'));},{once:true});
  });
  const active=queue.request('assistant',networkRead);
  await tick();
  for(let index=0;index<100;index++)queue.request('assistant',async()=>{writes.push('stale snapshot');});
  queue.cancel();await active;
  assert.equal(readsStarted,1);assert.equal(aborted,1);
  assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);
});
