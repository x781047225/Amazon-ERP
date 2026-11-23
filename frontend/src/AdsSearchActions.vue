<script setup lang="ts">
import {ref,computed,onMounted} from 'vue'
import {ElMessage} from 'element-plus'
import {api} from './session'
import {actualCpc} from './adsSearchPricing'
import {keywordError} from './adsKeywordValidation'
import {submitSearchKeywords} from './adsSearchJobs'
import AdsProductImage from './AdsProductImage.vue'
const props=defineProps<{rows:any[],profiles:any[],mode:'keyword'|'negative',manualDestination?:{profileId:string,campaignId?:string,adgroupId?:string}}>()
const emit=defineEmits(['close','done'])
const pid=ref(props.manualDestination?.profileId||props.rows[0]?.profile_id||''),cid=ref(''),gid=ref(''),options=ref<any>({campaigns:[],groups:[]}),context=ref<any>({}),matches=ref(['EXACT']),negativeMatches=ref<string[]>(['NEGATIVE_EXACT'])
const entries=ref<any[]>([]),selected=ref<any[]>([]),busy=ref(false),reading=ref(false),suggesting=ref(false),error=ref(''),warning=ref('')
const basis=ref('default'),custom=ref<number>(.25),adjust=ref(0),direction=ref(1)
const names:Record<string,string>={EXACT:'精准匹配',PHRASE:'词组匹配',BROAD:'广泛匹配',NEGATIVE_EXACT:'否定精准',NEGATIVE_PHRASE:'否定词组',ASIN:'否定商品'}
const campaigns=computed(()=>options.value.campaigns.filter((c:any)=>c.targeting==='MANUAL'))
const groups=computed(()=>options.value.groups.filter((g:any)=>g.campaign_id===cid.value))
const currency=computed(()=>props.profiles.find(p=>p.id===pid.value)?.currency||'')
const norm=(v:string)=>v.trim().replace(/\s+/g,' ').toLowerCase()
const isAsin=(v:string)=>/^[A-Z0-9]{10}$/i.test(v)
const key=(v:any)=>norm(v.text)+'|'+v.match
const manualText=ref(''),manualTerms=ref<string[]>([])
const excludedTerms=ref<string[]>([])
function addManualKeywords(){
 error.value='';const terms=[...new Map(manualText.value.split(/\r?\n/).map(s=>s.trim().replace(/\s+/g,' ')).filter(Boolean).map(s=>[norm(s),s])).values()]
 if(!terms.length){error.value='请先输入关键词，每行一个';return}
 const invalid=terms.find(t=>keywordError(t));if(invalid){error.value=invalid+'：'+keywordError(invalid);return}
 const combined=[...new Map([...manualTerms.value,...terms].map(t=>[norm(t),t])).values()]
 if(combined.length*Math.max(matches.value.length,1)>300){error.value='单次最多添加300项关键词与匹配类型组合';return}
 manualTerms.value=combined;excludedTerms.value=excludedTerms.value.filter(t=>!terms.some(s=>norm(s)===t));manualText.value='';rebuild();void suggestions()
}
function removeManualKeyword(text:string){manualTerms.value=manualTerms.value.filter(t=>norm(t)!==norm(text));rebuild()}
const invalidKeywords=computed(()=>[...new Map(entries.value.filter(e=>!e.exists&&keywordError(e.text)).map(e=>[norm(e.text),{text:e.text,error:keywordError(e.text)}])).values()])
function removeInvalidKeywords(){excludedTerms.value.push(...invalidKeywords.value.map(e=>norm(e.text)));rebuild();error.value=''}
const hasNegativeKeywords=computed(()=>props.rows.some(r=>!isAsin(r.name)))
const canSubmit=computed(()=>!reading.value&&!busy.value&&(props.mode==='negative'?(props.rows.length>0&&(!hasNegativeKeywords.value||negativeMatches.value.length>0)):(gid.value&&matches.value.length&&!invalidKeywords.value.length&&(!props.manualDestination||!manualText.value.trim())&&entries.value.some(e=>!e.exists))))
let request=0,suggestionRequest=0
function rebuild(){
 const old=new Map(entries.value.map(e=>[key(e),e]));const terms=props.manualDestination?manualTerms.value:[...new Map(props.rows.map(r=>[norm(r.name),r.name.trim().replace(/\s+/g,' ')])).values()];const existing=new Set((context.value.existing||[]).map((e:any)=>key(e)))
 entries.value=terms.filter(text=>!excludedTerms.value.includes(norm(text))).flatMap(text=>matches.value.map(match=>{const prior=old.get(key({text,match}));return {text,match,bid:prior?.bid??context.value.default_bid,suggestion:prior?.suggestion,cpc:actualCpc(props.rows.filter(r=>norm(r.name)===norm(text)),currency.value,props.profiles),exists:existing.has(key({text,match}))}}));selected.value=[]
}
async function loadOptions(){const r=++request;suggestionRequest++;suggesting.value=false;reading.value=true;error.value='';gid.value='';cid.value='';context.value={};entries.value=[]
 try{const data=await api('/advertising/create/options?'+new URLSearchParams({profile_id:pid.value}));if(r!==request)return;options.value=data
 const sources=props.rows.filter(v=>v.profile_id===pid.value);const same=sources.length===props.rows.length&&sources.every(v=>v.adgroup_id===sources[0]?.adgroup_id)
 if(props.manualDestination){
  const destination=props.manualDestination;if(campaigns.value.some((c:any)=>c.id===destination.campaignId)){cid.value=destination.campaignId!;if(groups.value.some((g:any)=>g.id===destination.adgroupId))gid.value=destination.adgroupId!}
 }else if(same&&campaigns.value.some((c:any)=>c.id===sources[0].campaign_id)){cid.value=sources[0].campaign_id;gid.value=sources[0].adgroup_id}
 }catch(e){if(r===request)error.value=(e as Error).message}finally{if(r===request)reading.value=false}
 if(gid.value)await loadContext()
}
async function loadContext(){const r=++request;suggestionRequest++;suggesting.value=false;reading.value=true;error.value='';warning.value='';context.value={};entries.value=[]
 try{const data=await api('/advertising/search-actions/context?'+new URLSearchParams({profile_id:pid.value,adgroup_id:gid.value}));if(r!==request)return;context.value=data;rebuild()}
 catch(e){if(r===request)error.value=(e as Error).message}finally{if(r===request)reading.value=false}
 if(r===request&&entries.value.length)void suggestions()
}
function changeCampaign(){request++;gid.value='';context.value={};entries.value=[];reading.value=false;suggesting.value=false}
async function changeMatches(){rebuild();if(gid.value&&entries.value.length)await suggestions()}
async function suggestions(){const valid=entries.value.filter(e=>!keywordError(e.text));if(!gid.value||!valid.length)return;const r=request,s=++suggestionRequest;suggesting.value=true;warning.value=''
 try{const data=await api('/advertising/search-actions/suggestions',{profile_id:pid.value,adgroup_id:gid.value,mode:'keyword',items:valid.map(e=>({text:e.text,match:e.match}))});if(r!==request||s!==suggestionRequest)return
 const suggestions=new Map(data.items.map((i:any)=>[key(i),i.suggestion]));for(const e of entries.value)e.suggestion=suggestions.get(key(e))
 }catch(e){if(r===request&&s===suggestionRequest)warning.value='建议竞价暂未取得，可使用广告组默认竞价或自定义竞价。'+(e as Error).message}finally{if(r===request&&s===suggestionRequest)suggesting.value=false}
}
function apply(){const targets=(selected.value.length?selected.value:entries.value).filter(e=>!e.exists);let missing=0
 for(const e of targets){const base=basis.value==='default'?context.value.default_bid:basis.value==='custom'?custom.value:basis.value==='cpc'?e.cpc:e.suggestion?.[basis.value];const value=Math.round(Number(base)*(1+direction.value*adjust.value/100)*100)/100;if(base==null||!Number.isFinite(value)||value<=0||value>10000){missing++;continue}e.bid=value}
 if(missing)ElMessage.warning(`${missing} 项没有可用竞价或调整后超出范围，已保留原值`)
}
function negativeBatches(){const grouped=new Map<string,any>();for(const r of props.rows){const k=r.profile_id+'|'+r.adgroup_id;if(!grouped.has(k))grouped.set(k,{profile_id:r.profile_id,adgroup_id:r.adgroup_id,mode:'negative',label:r.store+' / '+r.country+' · '+r.adgroup_name,items:[]});for(const match of isAsin(r.name)?['ASIN']:negativeMatches.value){const item={text:r.name,match};if(!grouped.get(k).items.some((i:any)=>key(i)===key(item)))grouped.get(k).items.push(item)}}return [...grouped.values()]}
function submitKeywords(){
 if(invalidKeywords.value.length){error.value='请先移除或修改无效关键词';return}
 if(!canSubmit.value)return
 error.value=''
 const items=entries.value.filter(e=>!e.exists).map(e=>({text:e.text,match:e.match,bid:e.bid}))
 if(!items.length){error.value='没有需要新增的关键词';return}
 if(items.length>300){error.value='单次最多添加300项关键词与匹配类型组合，请减少选择';return}
 if(items.some(e=>e.bid==null||!Number.isFinite(e.bid)||e.bid<.01||e.bid>10000)){error.value='请给每个关键词填写0.01至10000之间的竞价';return}
 busy.value=true
 const profile=props.profiles.find(p=>p.id===pid.value)
 submitSearchKeywords({profile_id:pid.value,adgroup_id:gid.value,mode:'keyword',items},`${profile?.account} / ${profile?.country} · ${groups.value.find((g:any)=>g.id===gid.value)?.name}`)
 emit('close')
}
function submitNegatives(){
 if(!canSubmit.value)return
 error.value=''
 const plans=negativeBatches()
 for(const plan of plans){
  if(!plan.profile_id||!plan.adgroup_id){error.value='搜索词缺少所属广告组，请刷新后重试';return}
  if(plan.items.length>300){error.value='每个广告组单次最多添加300项否定词与匹配类型组合';return}
  const invalid=plan.items.find((i:any)=>i.match!=='ASIN'&&keywordError(i.text))
  if(invalid){error.value=invalid.text+'：'+keywordError(invalid.text);return}
 }
 busy.value=true
 for(const {label,...body} of plans)submitSearchKeywords(body,label)
 emit('close')
}
onMounted(()=>{if(props.mode==='keyword')void loadOptions()})
</script>
<template>
<el-dialog :model-value="true" :title="manualDestination?'新增投放':mode==='keyword'?'添加关键词':'批量添加否定'" width="min(1200px,96vw)" top="4vh" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @close="emit('close')">
<el-alert v-if="error" :title="error" type="error" :closable="false"/>
<template v-if="mode==='keyword'">
<el-alert v-if="invalidKeywords.length" type="error" :closable="false" class="keyword-validation" title="有无效关键词，请先处理后再提交"><div v-for="item in invalidKeywords" :key="item.text">{{item.text}}：{{item.error}}</div><el-button type="danger" plain size="small" @click="removeInvalidKeywords">移除无效关键词</el-button></el-alert>
<div class="destination"><el-select v-model="pid" aria-label="投放店铺站点" filterable :disabled="busy||!!manualDestination?.campaignId" @change="loadOptions"><el-option v-for="p in profiles" :key="p.id" :value="p.id" :label="p.account+' / '+p.country+' · '+p.currency"/></el-select><el-select v-model="cid" placeholder="选择手动广告活动" aria-label="目标广告活动" filterable :disabled="reading||!!manualDestination?.campaignId" @change="changeCampaign"><el-option v-for="c in campaigns" :key="c.id" :value="c.id" :label="c.name"/></el-select><el-select v-model="gid" placeholder="选择广告组" aria-label="目标广告组" filterable :disabled="reading||!cid||!!manualDestination?.adgroupId" @change="loadContext"><el-option v-for="g in groups" :key="g.id" :value="g.id" :label="g.name"/></el-select></div>
<p class="muted">默认使用目标广告组当前默认竞价：{{context.default_bid??'未同步'}} {{currency}}。已存在的匹配类型不会重复添加。</p>
<el-checkbox-group :disabled="reading" v-model="matches" @change="changeMatches"><el-checkbox v-for="m in ['EXACT','PHRASE','BROAD']" :key="m" :value="m">{{names[m]}}</el-checkbox></el-checkbox-group>
<div v-if="manualDestination" class="manual-keyword-entry"><el-input v-model="manualText" type="textarea" :rows="4" placeholder="每行一个关键词，可粘贴多行" aria-label="新增投放关键词"/><div><el-button :disabled="reading||!gid||!matches.length" @click="addManualKeywords">添加到列表</el-button><span class="muted">已添加 {{manualTerms.length}} 个词；输入内容需先添加到列表再提交。</span></div></div>
<el-alert v-if="warning" :title="warning" type="warning" :closable="false"/>
<div class="pricing"><span>批量定价</span><el-select v-model="basis" aria-label="竞价依据"><el-option label="广告组默认竞价" value="default"/><el-option label="亚马逊建议价 · 低档" value="low"/><el-option label="亚马逊建议价 · 中档" value="mid"/><el-option label="亚马逊建议价 · 高档" value="high"/><el-option v-if="!manualDestination" label="实际 CPC" value="cpc"/><el-option label="自定义竞价" value="custom"/></el-select><el-input-number v-if="basis==='custom'" v-model="custom" :min=".01" :max="10000" :precision="2" aria-label="自定义竞价"/><el-select v-model="direction" aria-label="调整方向"><el-option label="上调" :value="1"/><el-option label="下调" :value="-1"/></el-select><el-input-number v-model="adjust" :min="0" :max="direction===-1?99:1000" :precision="0" aria-label="调整百分比"/><span>%</span><el-button @click="apply" :disabled="reading||!gid">应用到{{selected.length?'所选':'全部'}}关键词</el-button><el-button link :loading="suggesting" @click="suggestions" :disabled="!gid">刷新建议价</el-button></div>
<p v-if="!manualDestination" class="muted">实际 CPC 按列表所选时段的花费 ÷ 点击量计算；同词按点击量加权。无有效点击或币种不同不套用，保留原竞价。</p><el-table :data="entries" border max-height="420" v-loading="reading" @selection-change="(v:any[])=>selected=v"><el-table-column type="selection" width="45" :selectable="(r:any)=>!r.exists"/><el-table-column label="关键词" min-width="180"><template #default="{row}"><span>{{row.text}}</span><div v-if="!row.exists&&keywordError(row.text)" class="keyword-error">{{keywordError(row.text)}}</div></template></el-table-column><el-table-column label="匹配类型" width="140"><template #default="{row}">{{names[row.match]}}<el-tag v-if="row.exists" type="info" size="small">已存在</el-tag></template></el-table-column><el-table-column v-if="!manualDestination" :label="'实际 CPC（'+currency+'）'" width="165" align="center"><template #default="{row}"><el-button v-if="row.cpc!=null" size="small" :disabled="row.exists||Math.round(row.cpc*100)<1" :title="'所选日期范围内的花费 ÷ 点击量；点击采用该竞价'" @click="row.bid=Math.round(row.cpc*100)/100">{{row.cpc.toFixed(2)}}</el-button><span v-else class="muted" title="无有效点击数据或来源与目标币种不同">不可用</span></template></el-table-column><el-table-column label="亚马逊建议竞价（低 / 中 / 高）" min-width="320" align="center"><template #default="{row}"><div class="tiers"><el-button v-for="(label,tier) in {low:'低',mid:'中',high:'高'}" :key="tier" size="small" :disabled="row.exists||!row.suggestion?.[tier]" @click="row.bid=row.suggestion[tier]">{{label}} {{row.suggestion?.[tier]?.toFixed(2)??'—'}}</el-button></div></template></el-table-column><el-table-column :label="'竞价（'+currency+'）'" width="190" align="center"><template #default="{row}"><el-input-number v-model="row.bid" :min=".01" :max="10000" :precision="2" :step=".01" controls-position="right" :disabled="row.exists" :aria-label="row.text+' '+row.match+'竞价'"/></template></el-table-column><el-table-column v-if="manualDestination" label="操作" width="100"><template #default="{row}"><el-button link type="danger" @click="removeManualKeyword(row.text)">移除此词</el-button></template></el-table-column></el-table>
</template>
<template v-else><p>已选 {{rows.length}} 条搜索词，按各自所属店铺和广告组添加否定；ASIN 自动识别为否定商品。</p><el-checkbox-group v-model="negativeMatches" :disabled="!hasNegativeKeywords" aria-label="否定匹配方式"><el-checkbox value="NEGATIVE_EXACT">否定精准</el-checkbox><el-checkbox value="NEGATIVE_PHRASE">否定词组</el-checkbox></el-checkbox-group><p v-if="hasNegativeKeywords" class="muted">可同时勾选两种方式，每个关键词分别添加；至少选择一种。</p><el-table :data="rows" border max-height="420"><el-table-column label="搜索词 / 商品" min-width="260"><template #default="{row}"><div class="negative-item"><AdsProductImage v-if="isAsin(row.name)" :profile-id="row.profile_id" :asin="row.name"/><span>{{row.name}}</span></div></template></el-table-column><el-table-column prop="store" label="店铺"/><el-table-column prop="country" label="站点"/><el-table-column prop="adgroup_name" label="广告组"/><el-table-column label="类型"><template #default="{row}">{{isAsin(row.name)?'否定商品':negativeMatches.map(m=>names[m]).join('、')||'请选择匹配方式'}}</template></el-table-column></el-table></template>
<template #footer><el-button :disabled="busy" @click="emit('close')">关闭</el-button><el-button type="primary" :loading="busy" :disabled="!canSubmit" @click="mode==='keyword'?submitKeywords():submitNegatives()">添加并提交</el-button></template>
</el-dialog>
</template>
<style scoped>.manual-keyword-entry{margin:12px 0}.manual-keyword-entry>div:last-child{display:flex;gap:12px;align-items:center;margin-top:8px}.keyword-validation{margin-bottom:12px}.keyword-validation .el-button{margin-top:8px}.keyword-error{color:#c45656;font-size:12px;line-height:1.5;margin-top:4px}
.destination,.pricing,.tiers,.negative-item{display:flex;gap:10px;align-items:center}.destination{margin:8px 0 14px}.destination>.el-select{flex:1}.pricing{flex-wrap:wrap;margin:14px 0}.pricing>.el-select{width:190px}.pricing>.el-input-number{width:120px}.pricing>.el-select:nth-of-type(2){width:90px}.el-table .el-input-number{width:130px}.batch{margin:14px 0}.batch>b{display:block;margin-bottom:10px}.el-alert{margin:12px 0}
</style>
