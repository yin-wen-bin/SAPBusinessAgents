import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';
const source=await readFile(new URL('../src/components/SystemSettingsPanel.astro',import.meta.url),'utf8');
const label=source.match(/const authenticationDisplayState = ([\s\S]*?);/)[1];
const pending=source.match(/const authenticationSyncPending = ([\s\S]*?);/)[1];
const display=vm.runInNewContext(`(${label})`),poll=vm.runInNewContext(`(${pending})`);
test('pending is not sign-out, and Codex labels remain unchanged',()=>{
 const auth={status:'not_checked',last_successful_check:'saved',authentication_context_status:'check_required'};
 assert.equal(display('workbuddy',auth),'verification_pending');assert.equal(display('codex',auth),'not_checked');
 for(const status of ['signed_out','expired','check_failed','authenticated'])assert.equal(display('workbuddy',{...auth,status}),status);
 assert.equal(display('workbuddy',{...auth,last_successful_check:null}),'not_checked');
});
test('status polling finishes on confirmed/failed/blocked, never repeats a POST',()=>{
 for(const status of ['queued','checking','deferred'])assert.equal(poll({authentication_sync:{status}}),true);
 for(const status of ['confirmed','failed','blocked','cancelled','not_required'])assert.equal(poll({authentication_sync:{status}}),false);
 assert.match(source,/const result = await authRequest\(base\);/);
 assert.match(source,/clearTimeout\(authenticationSyncTimers.get\(provider\)\)/);
});
