import assert from 'node:assert/strict';
import test from 'node:test';
import {runtimeGroups} from '../src/lib/settingsNavigation.ts';
const item=(provider_id,fields={})=>({provider_id,provider_implemented:true,platform_supported:true,enabled:true,...fields});
test('SDK navigation classifies integration/platform, never enablement or model quality',()=>{
 const items=[item('a'),item('b',{enabled:false}),item('c',{provider_implemented:false}),item('d',{platform_supported:false})];
 assert.deepEqual(runtimeGroups(items,'a').map(e=>e.main),[true,true,false,false]);
 assert.deepEqual(items.map(e=>e.provider_id),['a','b','c','d']);
});
test('unknown projections retain prior classification and current default always remains discoverable',()=>{
 const prior=new Map(); runtimeGroups([item('a'),item('b',{provider_implemented:false})],null,prior);
 assert.deepEqual(runtimeGroups([{provider_id:'a'},{provider_id:'b'},{provider_id:'new'}],null,prior).map(e=>e.main),[true,false,true]);
 assert.equal(runtimeGroups([item('b',{platform_supported:false})],'b',prior)[0].main,true);
});
test('two, four and six supported SDKs preserve API order, including stopped SDKs',()=>{
 for(const count of [2,4,6]){
  const items=Array.from({length:count},(_,i)=>item('p'+i,{enabled:i%2===0}));
  assert.deepEqual(runtimeGroups(items,'p1').filter(e=>e.main).map(e=>e.item.provider_id),items.map(e=>e.provider_id));
 }
});
