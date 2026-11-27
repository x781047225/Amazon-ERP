const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {parse,compileScript}=require('@vue/compiler-sfc');
const ts=require('typescript');
const path=require('node:path');
const src=path.resolve(__dirname,'../src');
const transpile=s=>ts.transpileModule(s,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText;
const helper={exports:{}};vm.runInNewContext(transpile(fs.readFileSync(path.join(src,'adsDestinationOptions.ts'),'utf8')),{module:helper,exports:helper.exports});
const profiles=[{id:'A',account:'YESAH',country:'FR',currency:'EUR'},{id:'B',account:'YESAH',country:'FR',currency:'EUR'},{id:'C',account:'OTHER',country:'FR',currency:'EUR'},{id:'D',account:'YESAH',country:'UK',currency:'GBP'}];
assert.deepEqual(Array.from(helper.exports.destinationProfiles(profiles,'A'),p=>p.id),['A','B']);
const data=(name,date)=>({campaigns:[{id:'same',name,targeting:'MANUAL',created_at:date}],groups:[{id:'group',name:'组',campaign_id:'same',default_bid:.2}]});
const merged=helper.exports.mergeDestinationOptions(new Map([['A',data('旧','2026-10-01')],['B',data('新','2026-11-27')]]));
assert.deepEqual(Array.from(merged.campaigns,c=>c.id),['B:same','A:same']);
assert.equal(merged.groups[1].profile_id,'B');assert.equal(merged.groups[1].campaign_id,'B:same');
const compiled=transpile(compileScript(parse(fs.readFileSync(path.join(src,'AdsSearchActions.vue'),'utf8')).descriptor,{id:'destination-test'}).content);
function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}}
async function run(){
 const slow=deferred(),calls=[],writes=[],hooks=[];let stage=0;
 const vue={defineComponent:o=>o,ref:v=>({value:v}),computed:f=>({get value(){return f()}}),onMounted:f=>hooks.push(f),onBeforeUnmount:()=>{}};
 const api=async(url,body)=>{calls.push({url,body});
  if(url.includes('/create/options')){const id=new URLSearchParams(url.split('?')[1]).get('profile_id');assert.ok(['A','B'].includes(id));assert.ok(!url.includes('refresh=true'));if(id==='B'&&stage===0)return slow.promise;return data('旧','2026-10-01')}
  if(url.includes('/context')){assert.ok(url.includes('profile_id=B'));assert.ok(url.includes('adgroup_id=group'));return {default_bid:.2,existing:[]}}
  if(url.includes('/suggestions')){assert.equal(body.profile_id,'B');assert.equal(body.adgroup_id,'group');return {items:[]}}
  throw Error(url)
 };
 const module={exports:{}};vm.runInNewContext(compiled,{module,exports:module.exports,URLSearchParams,AbortController,setTimeout,clearTimeout,require:n=>n==='vue'?vue:n==='./session'?{api}:n==='./adsDestinationOptions'?helper.exports:n==='./adsSearchPricing'?{actualCpc:()=>.1}:n==='./adsKeywordValidation'?{keywordError:()=>''}:n==='./adsSearchJobs'?{submitSearchKeywords:b=>writes.push(b)}:n==='element-plus'?{ElMessage:{warning:()=>{}}}:{}});
 const ctx=module.exports.default.setup({profiles,rows:[{profile_id:'A',name:'test keyword',campaign_id:'auto',adgroup_id:'auto-group'}],mode:'keyword'},{expose:()=>{},emit:()=>{}});
 const loading=ctx.loadOptions();await new Promise(r=>setImmediate(r));
 assert.equal(ctx.reading.value,false);assert.equal(ctx.campaigns.value.length,1);assert.equal(ctx.entries.value.length,1);assert.equal(ctx.optionsLoading.value,true);
 slow.resolve(data('新','2026-11-27'));await loading;
 assert.equal(ctx.campaigns.value.length,2);assert.equal(ctx.campaigns.value[0].id,'B:same');assert.equal(ctx.siteProfiles.value.length,3);
 ctx.cid.value='B:same';ctx.changeCampaign();ctx.gid.value='B:group';await ctx.loadContext();
 assert.equal(ctx.entries.value[0].bid,.2);assert.ok(ctx.canSubmit.value);ctx.submitKeywords();assert.equal(writes[0].profile_id,'B');assert.equal(writes[0].adgroup_id,'group');assert.equal(writes[0].items[0].text,'test keyword');
 // A result from an old site selection cannot restore its options.
 stage=1;ctx.pid.value='A';const old=ctx.loadOptions();ctx.pid.value='D';await old;assert.equal(ctx.options.value.campaigns.length,0);
 console.log('PASS: same-site accounts, newest-first, scoped IDs, nonblocking slow account, correct context/suggestion/submission destination, stale response guard; all APIs mocked.');
}
run().catch(e=>{console.error(e);process.exitCode=1});
