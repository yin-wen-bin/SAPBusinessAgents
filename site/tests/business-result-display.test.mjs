import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
const require=createRequire(import.meta.url), ts=require('typescript'), React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
function load(file){
 const filename=path.resolve(file),mod={exports:{}};
 const compiled=ts.transpileModule(readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX,target:ts.ScriptTarget.ES2020,esModuleInterop:true}}).outputText;
 const local=name=>name.startsWith('.')?load(path.resolve(path.dirname(filename),name+'.ts')):require(name);
 vm.runInNewContext(`(function(require,module,exports){${compiled}\n})`,{console,URLSearchParams})(local,mod,mod.exports);return mod.exports;
}
const Result=load('src/components/AgentDraftResult.tsx').default;
for(const locale of ['zh','en'])test(`generic trial groups, conclusion, currencies and rule counts (${locale})`,()=>{
 const run={run_id:'generic',status:'completed',result:{rule_results:[{business_report:{},source_complete:true,evidence_complete:true}],steps:[
  {step_id:'check',executor:'rule',status:'completed',execution:{summary_kind:'rule_check',record_count:null}},
  {step_id:'calculate',executor:'rule',status:'completed',execution:{summary_kind:'business_records',record_count:3}}],
  business_display:{business_status_label:{zh:'存在未清项目',en:'Open items found'},groups:{'0':{label:{zh:'状态',en:'State'},total_rows:3,source_complete:false,choices:[{value:'"open"',label:{zh:'未清',en:'Open'},count:2},{value:'"closed"',label:{zh:'已清',en:'Cleared'},count:1}]}}},
  presentation:{title:{zh:'业务结果',en:'Business result'},blocks:[
   {type:'table',title:{zh:'业务记录',en:'Business records'},columns:[{key:'id',label:{zh:'凭证',en:'Document'}}],rows:[{values:[{zh:'A',en:'A'}]}],total_rows:3},
   {type:'table',title:{zh:'分币种金额',en:'Amounts by currency'},columns:[{key:'currency',label:{zh:'币种',en:'Currency'}},{key:'value',label:{zh:'金额',en:'Amount'}}],rows:[{values:[{zh:'USD',en:'USD'},{zh:'-1.2300',en:'-1.2300'}]},{values:[{zh:'EUR',en:'EUR'},{zh:'9.00',en:'9.00'}]}],total_rows:2}]}}};
 const html=renderToStaticMarkup(React.createElement(Result,{run,trial:{business_output_available:true},locale,apiBase:'',runPath:'/run'}));
 assert.match(html,/USD/);assert.match(html,/EUR/);assert.match(html,/-1.2300/);assert.match(html,/9.00/);
 assert.match(html,locale==='zh'?/存在未清项目/:/Open items found/);
 assert.match(html,locale==='zh'?/业务记录分组/:/Business record group/);
 assert.match(html,locale==='zh'?/本地检查/:/Local check/);
 assert.match(html,locale==='zh'?/来源尚不完整/:/The source is incomplete/);
 assert.doesNotMatch(html,/0条|Not found|未找到/);
});
