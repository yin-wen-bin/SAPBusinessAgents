import assert from 'node:assert/strict';
import test from 'node:test';
import { CatalogResource, catalogPollingDelay } from '../src/lib/agentCatalog.ts';
const response = (id=1, values={}) => ({items:[{id:'agent'}],snapshot:{id,instance:'api-one',available:true,
  checked_at:new Date(100000).toISOString(),stale:false,refreshing:false,error_code:null,...values}});

test('fresh catalog survives navigation and does not refetch on focus',async()=>{
  let calls=0;const fetcher=async()=>{calls++;return response();};
  const first=new CatalogResource('fresh-case',fetcher,()=>{},()=>100001);
  await first.refresh();await first.refresh();
  const next=new CatalogResource('fresh-case',fetcher,()=>{},()=>100002);
  await next.refresh();assert.equal(calls,1);assert.equal(next.state.loaded,true);
});
test('cold pending is not an empty loaded directory; stale data survives errors',async()=>{
  let value=response(0,{available:false,refreshing:true,checked_at:null});
  const resource=new CatalogResource('pending-case',async()=>value,()=>{},()=>100001);
  await resource.refresh();assert.equal(resource.state.loaded,false);
  value=response();await resource.refresh();assert.equal(resource.state.loaded,true);
  value=response(1,{stale:true,error_code:'agent_catalog_refresh_failed'});
  await resource.refresh(true);assert.equal(resource.state.data.length,1);assert.ok(resource.state.error);
});
test('explicit refresh coalesces; disposal rejects late results',async()=>{
  const pending=[];const modes=[];
  const resource=new CatalogResource('concurrency-case',(_s,force)=>{modes.push(force);return new Promise(r=>pending.push(r));},()=>{},()=>100001);
  const first=resource.refresh(true);assert.equal(resource.refresh(true),first);await Promise.resolve();
  resource.dispose();const next=resource.refresh();await Promise.resolve();
  pending[1](response(2));await next;pending[0](response(1));await first;
  assert.equal(resource.snapshot.id,2);assert.deepEqual(modes,[true,false]);
});
test('older snapshot rejected but a restarted API has its own sequence',async()=>{
  let value=response(5);const r=new CatalogResource('order-case',async()=>value,()=>{},()=>100001);
  await r.refresh();value=response(4);await r.refresh(true);assert.equal(r.snapshot.id,5);
  value=response(1,{instance:'api-two'});await r.refresh(true);assert.equal(r.snapshot.id,1);
});
test('expiry and scoped invalidation trigger only a display GET',async()=>{
  let now=100001,calls=0;const r=new CatalogResource('ttl-case',async()=>{calls++;return response();},()=>{},()=>now);
  await r.refresh();now+=60000;await r.refresh();assert.equal(calls,2);
  r.invalidate();await r.refresh();assert.equal(calls,3);
  assert.deepEqual([0,1,2,3,10].map(catalogPollingDelay),[1000,2000,5000,5000,5000]);
});
