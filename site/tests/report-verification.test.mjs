import test from 'node:test';
import assert from 'node:assert/strict';
import {verificationRows} from '../src/lib/report-verification.mjs';

test('dimensions stay independent and expose only safe issue locations', () => {
  const proof = {version:'sapba.report-verification/2',summary_amounts:{status:'verified'},
    detail_amounts:{status:'verified'}, business_facts:{status:'needs_evidence',issues:[{
      code:'business_claim_binding_required',path:'/blocks/2',secret:'private'}]},coverage:{status:'failed'}};
  const rows = verificationRows(proof);
  assert.deepEqual(rows.map(r=>r.text), ['已验证','已验证','待补证','校验失败']);
  assert.equal(rows[2].issues[0].path,'/blocks/2');
  assert.ok(!JSON.stringify(rows).includes('private'));
  assert.equal(verificationRows(proof,'en')[0].label,'Summary amounts');
});
test('legacy reports are not upgraded and unknown statuses are not successful',()=>{
  assert.deepEqual(verificationRows(null),[]);
  assert.deepEqual(verificationRows({version:'legacy'}),[]);
  assert.ok(verificationRows({version:'sapba.report-verification/2'}).every(r=>r.status==='needs_evidence'));
});
