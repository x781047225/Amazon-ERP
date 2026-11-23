<script setup lang="ts">
import {ref,computed,onMounted} from 'vue'
import {ElMessage} from 'element-plus'
import {api} from './session'
import {actualCpc} from './adsSearchPricing'
import {submitSearchKeywords} from './adsSearchJobs'
import AdsProductImage from './AdsProductImage.vue'
const props=defineProps<{rows:any[],profiles:any[],mode:'keyword'|'negative'}>()
const emit=defineEmits(['close','done'])
const pid=ref(props.rows[0]?.profile_id||''),cid=ref(''),gid=ref(''),options=ref<any>({campaigns:[],groups:[]}),context=ref<any>({}),matches=ref(['EXACT']),negativeMatch=ref('NEGATIVE_EXACT')
const entries=ref<any[]>([]),selected=ref<any[]>([]),busy=ref(false),reading=ref(false),suggesting=ref(false),error=ref(''),warning=ref(''),stage=ref('form'),batches=ref<any[]>([])
const basis=ref('default'),custom=ref<number>(.25),adjust=ref(0),direction=ref(1)
const names:Record<string,string>={EXACT:'精准匹配',PHRASE:'词组匹配',BROAD:'广泛匹配',NEGATIVE_EXACT:'否定精准',NEGATIVE_PHRASE:'否定词组',ASIN:'否定商品'}
const states:Record<string,string>={ready:'待提交',exists:'已存在，跳过',sending:'提交中',succeeded:'成功',failed:'失败',unknown:'结果待核对'}
const campaigns=computed(()=>options.value.campaigns.filter((c:any)=>c.targeting==='MANUAL'))
const groups=computed(()=>options.value.groups.filter((g:any)=>g.campaign_id===cid.value))
const currency=computed(()=>props.profiles.find(p=>p.id===pid.value)?.currency||'')
const norm=(v:string)=>v.trim().replace(/\s+/g,' ').toLowerCase()
const isAsin=(v:string)=>/^[A-Z0-9]{10}$/i.test(v)
const key=(v:any)=>norm(v.text)+'|'+v.match
const canPreview=computed(()=>!reading.value&&!busy.value&&(props.mode==='negative'||(gid.value&&matches.value.length&&entries.value.some(e=>!e.exists))))
let request=0,suggestionRequest=0
function rebuild(){
 const old=new Map(entries.value.map(e=>[key(e),e]));const terms=[...new Map(props.rows.map(r=>[norm(r.name),r.name.trim().replace(/\s+/g,' ')])).values()];const existing=new Set((context.value.existing||[]).map((e:any)=>key(e)))
 entries.value=terms.flatMap(text=>matches.value.map(match=>{const prior=old.get(key({text,match}));return {text,match,bid:prior?.bid??context.value.default_bid,suggestion:prior?.suggestion,cpc:actualCpc(props.rows.filter(r=>norm(r.name)===norm(text)),currency.value,props.profiles),exists:existing.has(key({text,match}))}}));selected.value=[]
}
async function loadOptions(){const r=++request;suggestionRequest++;suggesting.value=false;reading.value=true;error.value='';gid.value='';cid.value='';context.value={};entries.value=[]
 try{const data=await api('/advertising/create/options?'+new URLSearchParams({profile_id:pid.value}));if(r!==request)return;options.value=data
 const sources=props.rows.filter(v=>v.profile_id===pid.value);const same=sources.length===props.rows.length&&sources.every(v=>v.adgroup_id===sources[0]?.adgroup_id)
 if(same&&campaigns.value.some((c:any)=>c.id===sources[0].campaign_id)){cid.value=sources[0].campaign_id;gid.value=sources[0].adgroup_id}
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
async function suggestions(){if(!gid.value||!entries.value.length)return;const r=request,s=++suggestionRequest;suggesting.value=true;warning.value=''
 try{const data=await api('/advertising/search-actions/suggestions',{profile_id:pid.value,adgroup_id:gid.value,mode:'keyword',items:entries.value.map(e=>({text:e.text,match:e.match}))});if(r!==request||s!==suggestionRequest)return
 const suggestions=new Map(data.items.map((i:any)=>[key(i),i.suggestion]));for(const e of entries.value)e.suggestion=suggestions.get(key(e))
 }catch(e){if(r===request&&s===suggestionRequest)warning.value='建议竞价暂未取得，可使用广告组默认竞价或自定义竞价。'+(e as Error).message}finally{if(r===request&&s===suggestionRequest)suggesting.value=false}
}
function apply(){const targets=(selected.value.length?selected.value:entries.value).filter(e=>!e.exists);let missing=0
 for(const e of targets){const base=basis.value==='default'?context.value.default_bid:basis.value==='custom'?custom.value:basis.value==='cpc'?e.cpc:e.suggestion?.[basis.value];const value=Math.round(Number(base)*(1+direction.value*adjust.value/100)*100)/100;if(base==null||!Number.isFinite(value)||value<=0||value>10000){missing++;continue}e.bid=value}
 if(missing)ElMessage.warning(`${missing} 项没有可用竞价或调整后超出范围，已保留原值`)
}
function negativeBatches(){const grouped=new Map<string,any>();for(const r of props.rows){const k=r.profile_id+'|'+r.adgroup_id;if(!grouped.has(k))grouped.set(k,{profile_id:r.profile_id,adgroup_id:r.adgroup_id,mode:'negative',label:r.store+' / '+r.country+' · '+r.adgroup_name,items:[]});grouped.get(k).items.push({text:r.name,match:isAsin(r.name)?'ASIN':negativeMatch.value})}return [...grouped.values()]}
function submitKeywords(){
 if(!canPreview.value)return
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
async function preview(){busy.value=true;error.value='';batches.value=[]
 try{const plans=props.mode==='negative'?negativeBatches():[{profile_id:pid.value,adgroup_id:gid.value,mode:'keyword',label:props.profiles.find(p=>p.id===pid.value)?.account+' / '+props.profiles.find(p=>p.id===pid.value)?.country+' · '+groups.value.find((g:any)=>g.id===gid.value)?.name,items:entries.value.map(e=>({text:e.text,match:e.match,bid:e.bid}))}]
 if(props.mode==='keyword'&&plans[0].items.some((e:any)=>e.bid==null||!Number.isFinite(e.bid)||e.bid<=0))throw Error('请给每个关键词填写有效竞价')
 for(const plan of plans){const {label,...body}=plan;try{const result=await api('/advertising/search-actions/preview',body);batches.value.push({...result,label})}catch(e){batches.value.push({label,status:'failed',message:(e as Error).message,items:[]})}}
 stage.value='preview'
 }catch(e){error.value=(e as Error).message}finally{busy.value=false}
}
async function confirm(){busy.value=true;error.value='';stage.value='result'
 try{for(const batch of batches.value.filter(b=>b.id&&b.items.some((i:any)=>i.status==='ready'))){
 try{Object.assign(batch,await api('/advertising/search-actions/confirm',{id:batch.id}));while(batch.status==='sending'){await new Promise(resolve=>setTimeout(resolve,1500));Object.assign(batch,await api('/advertising/search-actions/status/'+batch.id))}}
 catch(e){batch.status='unknown';batch.message='请求结果待核对，请查看操作日志，勿重复提交。'+(e as Error).message}
 }}finally{busy.value=false;emit('done')}
}
onMounted(()=>{if(props.mode==='keyword')void loadOptions()})
</script>
<template>
<el-dialog :model-value="true" :title="mode==='keyword'?'添加关键词':'批量添加否定'" width="min(1200px,96vw)" top="4vh" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @close="emit('close')">
<el-alert v-if="error" :title="error" type="error" :closable="false"/>
<template v-if="stage==='form'">
<template v-if="mode==='keyword'">
<div class="destination"><el-select v-model="pid" aria-label="投放店铺站点" filterable :disabled="busy" @change="loadOptions"><el-option v-for="p in profiles" :key="p.id" :value="p.id" :label="p.account+' / '+p.country+' · '+p.currency"/></el-select><el-select v-model="cid" placeholder="选择手动广告活动" aria-label="目标广告活动" filterable :disabled="reading" @change="changeCampaign"><el-option v-for="c in campaigns" :key="c.id" :value="c.id" :label="c.name"/></el-select><el-select v-model="gid" placeholder="选择广告组" aria-label="目标广告组" filterable :disabled="reading||!cid" @change="loadContext"><el-option v-for="g in groups" :key="g.id" :value="g.id" :label="g.name"/></el-select></div>
<p class="muted">默认使用目标广告组当前默认竞价：{{context.default_bid??'未同步'}} {{currency}}。已存在的匹配类型不会重复添加。</p>
<el-checkbox-group :disabled="reading" v-model="matches" @change="changeMatches"><el-checkbox v-for="m in ['EXACT','PHRASE','BROAD']" :key="m" :value="m">{{names[m]}}</el-checkbox></el-checkbox-group>
<el-alert v-if="warning" :title="warning" type="warning" :closable="false"/>
<div class="pricing"><span>批量定价</span><el-select v-model="basis" aria-label="竞价依据"><el-option label="广告组默认竞价" value="default"/><el-option label="亚马逊建议价 · 低档" value="low"/><el-option label="亚马逊建议价 · 中档" value="mid"/><el-option label="亚马逊建议价 · 高档" value="high"/><el-option label="实际 CPC" value="cpc"/><el-option label="自定义竞价" value="custom"/></el-select><el-input-number v-if="basis==='custom'" v-model="custom" :min=".01" :max="10000" :precision="2" aria-label="自定义竞价"/><el-select v-model="direction" aria-label="调整方向"><el-option label="上调" :value="1"/><el-option label="下调" :value="-1"/></el-select><el-input-number v-model="adjust" :min="0" :max="direction===-1?99:1000" :precision="0" aria-label="调整百分比"/><span>%</span><el-button @click="apply" :disabled="reading||!gid">应用到{{selected.length?'所选':'全部'}}关键词</el-button><el-button link :loading="suggesting" @click="suggestions" :disabled="!gid">刷新建议价</el-button></div>
<p class="muted">实际 CPC 按列表所选时段的花费 ÷ 点击量计算；同词按点击量加权。无有效点击或币种不同不套用，保留原竞价。</p><el-table :data="entries" border max-height="420" v-loading="reading" @selection-change="(v:any[])=>selected=v"><el-table-column type="selection" width="45" :selectable="(r:any)=>!r.exists"/><el-table-column prop="text" label="关键词" min-width="180"/><el-table-column label="匹配类型" width="140"><template #default="{row}">{{names[row.match]}}<el-tag v-if="row.exists" type="info" size="small">已存在</el-tag></template></el-table-column><el-table-column :label="'实际 CPC（'+currency+'）'" width="165" align="center"><template #default="{row}"><el-button v-if="row.cpc!=null" size="small" :disabled="row.exists||Math.round(row.cpc*100)<1" :title="'所选日期范围内的花费 ÷ 点击量；点击采用该竞价'" @click="row.bid=Math.round(row.cpc*100)/100">{{row.cpc.toFixed(2)}}</el-button><span v-else class="muted" title="无有效点击数据或来源与目标币种不同">不可用</span></template></el-table-column><el-table-column label="亚马逊建议竞价（低 / 中 / 高）" min-width="320" align="center"><template #default="{row}"><div class="tiers"><el-button v-for="(label,tier) in {low:'低',mid:'中',high:'高'}" :key="tier" size="small" :disabled="row.exists||!row.suggestion?.[tier]" @click="row.bid=row.suggestion[tier]">{{label}} {{row.suggestion?.[tier]?.toFixed(2)??'—'}}</el-button></div></template></el-table-column><el-table-column :label="'竞价（'+currency+'）'" width="190" align="center"><template #default="{row}"><el-input-number v-model="row.bid" :min=".01" :max="10000" :precision="2" :step=".01" controls-position="right" :disabled="row.exists" :aria-label="row.text+' '+row.match+'竞价'"/></template></el-table-column></el-table>
</template>
<template v-else><p>已选 {{rows.length}} 条搜索词，按各自所属店铺和广告组添加否定；ASIN 自动识别为否定商品。</p><el-radio-group v-model="negativeMatch"><el-radio value="NEGATIVE_EXACT">否定精准</el-radio><el-radio value="NEGATIVE_PHRASE">否定词组</el-radio></el-radio-group><el-table :data="rows" border max-height="420"><el-table-column label="搜索词 / 商品" min-width="260"><template #default="{row}"><div class="negative-item"><AdsProductImage v-if="isAsin(row.name)" :profile-id="row.profile_id" :asin="row.name"/><span>{{row.name}}</span></div></template></el-table-column><el-table-column prop="store" label="店铺"/><el-table-column prop="country" label="站点"/><el-table-column prop="adgroup_name" label="广告组"/><el-table-column label="类型"><template #default="{row}">{{isAsin(row.name)?'否定商品':names[negativeMatch]}}</template></el-table-column></el-table></template>
</template>
<template v-else><div v-for="(batch,i) in batches" :key="i" class="batch"><b>{{batch.label}}</b><el-alert v-if="batch.message" :title="batch.message" type="warning" :closable="false"/><el-table :data="batch.items" border max-height="350"><el-table-column prop="text" label="关键词 / 商品" min-width="190"/><el-table-column label="匹配类型" width="120"><template #default="{row}">{{names[row.match]}}</template></el-table-column><el-table-column label="竞价" width="100" align="center"><template #default="{row}">{{row.payload.bid??'—'}}</template></el-table-column><el-table-column label="结果" min-width="180"><template #default="{row}">{{states[row.status]||row.status}}<div>{{row.message}}</div></template></el-table-column></el-table></div></template>
<template #footer><el-button :disabled="busy" @click="emit('close')">关闭</el-button><el-button v-if="stage==='preview'" :disabled="busy" @click="stage='form'">返回编辑</el-button><el-button v-if="stage==='form'" type="primary" :loading="busy" :disabled="!canPreview" @click="mode==='keyword'?submitKeywords():preview()">{{mode==='keyword'?'添加并提交':'预览添加'}}</el-button><el-button v-if="stage==='preview'" type="primary" :loading="busy" :disabled="!batches.some(b=>b.items.some((i:any)=>i.status==='ready'))" @click="confirm">确认提交亚马逊</el-button><span v-if="stage==='result'&&busy">正在提交，请勿重复操作…</span></template>
</el-dialog>
</template>
<style scoped>
.destination,.pricing,.tiers,.negative-item{display:flex;gap:10px;align-items:center}.destination{margin:8px 0 14px}.destination>.el-select{flex:1}.pricing{flex-wrap:wrap;margin:14px 0}.pricing>.el-select{width:190px}.pricing>.el-input-number{width:120px}.pricing>.el-select:nth-of-type(2){width:90px}.el-table .el-input-number{width:130px}.batch{margin:14px 0}.batch>b{display:block;margin-bottom:10px}.el-alert{margin:12px 0}
</style>
