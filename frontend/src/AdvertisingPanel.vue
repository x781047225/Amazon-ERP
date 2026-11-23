<script setup lang="ts">
import {submitBulkEdits,pendingBulkItem} from './adsSearchJobs'
import {pendingAdEdits as pendingBids} from './adsPendingEdits'
import {adDisplayData,internalAdId} from './adDisplay'
import {adsPeriodDates as periodDates} from './adsDates'
import {useTableWidths} from './tableWidths'
const tableWidths=useTableWidths('AdvertisingPanel')

import {ref,computed,onMounted,onUnmounted,watch,h} from 'vue'
import {ElMessage,ElNotification,ElMessageBox} from 'element-plus'
import {Search,Refresh,ArrowRight,EditPen,Check} from '@element-plus/icons-vue'
import {api,session,can} from './session'
import AdsColumnHelp from './AdsColumnHelp.vue'
import {columnDescription} from './adsColumnDescriptions'
import CopyValue from './CopyValue.vue'
import AdsRecentChange from './AdsRecentChange.vue'
import AdsStrategyPanel from './AdsStrategyPanel.vue'
import AdsAddProducts from './AdsAddProducts.vue'
import AdsSearchActions from './AdsSearchActions.vue'
import AdsProductImage from './AdsProductImage.vue'
import AdsEntityLog from './AdsEntityLog.vue'
const logRow=ref<any>(null)
import {useAdsSticky} from './useAdsSticky'
const panelRoot=ref<HTMLElement|null>(null)
useAdsSticky(panelRoot)
const addAdsOpen=ref(false)
const manualDestination=ref<{profileId:string,campaignId?:string,adgroupId?:string}>()
function openManualTarget(){manualDestination.value={profileId:profile.value,campaignId:campaign.value?.id,adgroupId:group.value?.id};searchActionRows.value=[];searchAction.value='keyword'}
const searchAction=ref<'keyword'|'negative'|null>(null),searchActionRows=ref<any[]>([]),matchTypes=ref<string[]>([])
function openSearchAction(mode:'keyword'|'negative',row?:any){manualDestination.value=undefined;searchActionRows.value=(row?[row]:selectedCampaigns.value).map(r=>({...r}));if(searchActionRows.value.length)searchAction.value=mode}
const props=defineProps<{focus?:{store:string,marketplace:string,asin:string,request:number}|null,context?:any,active?:boolean}>()
const emit=defineEmits(['open-entity','open-create','home','date-range'])
const section=ref(props.context?.group?'ad':props.context?'adgroup':'campaign')
const levelFor=(key:string)=>['auto','keyword','product'].includes(key)?'target':['negative_keyword','negative_product'].includes(key)?'negative':['strategy','settings'].includes(key)?'campaign':key
const rootSections=[['campaign','广告活动'],['adgroup','广告组'],['ad','广告'],['auto','自动投放'],['keyword','关键词'],['product','商品投放'],['negative_keyword','否定词'],['negative_product','否定投放'],['search','用户搜索词'],['strategy','广告策略'],['placement','广告位']]
const entitySections=[['campaign','广告活动'],['adgroup','广告组'],['ad','广告'],['target','投放'],['negative','否定投放'],['search','用户搜索词'],['strategy','广告策略'],['placement','广告位'],['settings','广告活动设置']]
const sections=computed(()=>(props.context?entitySections:rootSections).filter(([key])=>['campaign','adgroup','placement'].includes(levelFor(key))?can('advertising'):can('advertising.'+levelFor(key))))
function selectSection(key:string){section.value=key;sort.value='';inlineValues.value={};search()}

function openCreate(){emit('open-create',{mode:section.value,profileId:profile.value,campaign:campaign.value?{id:campaign.value.id,name:campaign.value.name}:null})}
defineExpose({refresh:requestBackgroundRefresh,showAds:()=>{if(section.value!=='ad'&&can('advertising.ad'))selectSection('ad')}})
const canAddManualTarget=computed(()=>currentLevel.value==='target'&&(props.context?data.value.campaign_targeting==='MANUAL'||campaign.value?.targeting_type==='MANUAL':section.value==='keyword'))
const canEditAds=computed(()=>can(['campaign','adgroup','placement'].includes(currentLevel.value)?'advertising':'advertising.'+currentLevel.value,'operate'))
const profiles=ref<any[]>([]),profile=ref(''),country=ref('UK'),store=ref(''),campaign=ref<any>(props.context?.campaign||null),group=ref<any>(props.context?.group||null)
const dates=ref<string[]>([]),preset=ref('7'),q=ref(''),searchType=ref('all'),state=ref('NOT_ARCHIVED'),page=ref(1),pageSize=ref(25),loading=ref(false),syncing=ref(false),error=ref(''),data=ref<any>({rows:[],total:0}),jobs=ref<any[]>([]),showJobs=ref(false),sort=ref(''),direction=ref('desc'),hover=ref<number|null>(null)
const editOpen=ref(false),editBusy=ref(false),editRow=ref<any>(null),isCreate=ref(false),editKind=ref(''),editForm=ref<any>({}),previewData=ref<any>(null),editError=ref('')
const suggestLoading=ref(false)
const labels:Record<string,string>={campaign:'广告活动',adgroup:'广告组',ad:'广告',target:'投放',negative:'否定投放',search:'用户搜索词',logs:'操作日志',placement:'广告位'}
const states:Record<string,string>={ENABLED:'已开启',PAUSED:'已暂停',ARCHIVED:'已归档',UNKNOWN:'未知'}
const statusNames:Record<string,string>={preview:'待确认',sending:'提交中／待核对',succeeded:'成功',failed:'失败',unknown:'结果待核对'}
const fieldNames:Record<string,string>={state:'状态',bid:'竞价',defaultBid:'默认竞价',name:'名称',dailyBudget:'每日预算',portfolioId:'广告组合',endDate:'结束日期',biddingStrategy:'竞价策略',placementTop:'首页首位加价',placementRest:'搜索其余位置加价',placementProduct:'商品页面加价',placementBusiness:'亚马逊企业购加价',audiences:'受众加价',offAmazonStrategy:'站外投放设置',note:'备注（仅本地）',keywordText:'否定关键词',matchType:'否定方式',asin:'否定商品ASIN'}
const matchNames:Record<string,string>={BROAD:'广泛匹配',PHRASE:'词组匹配',EXACT:'精准匹配',AUTO:'自动投放',MANUAL:'商品投放',NEGATIVE_EXACT:'否定精准',NEGATIVE_PHRASE:'否定词组',NEGATIVE_PRODUCT:'否定商品',CLOSE:'紧密匹配',LOOSE:'宽泛匹配',SUBSTITUTES:'同类商品',COMPLEMENTS:'关联商品',UNKNOWN:'未知类型'}
const periodOptions=[['today','今天'],['yesterday','昨天'],['3','最近3天'],['7','最近7天'],['14','最近14天'],['30','最近30天'],['m3','最近三个月'],['m6','最近半年'],['y1','最近一年'],['custom','自定义']]
const jobStates:Record<string,string>={queued:'排队中',running:'同步中',waiting:'等待亚马逊生成报表',succeeded:'已完成',failed:'部分或全部失败'}
const currentLevel=computed(()=>levelFor(section.value))
const entityFilter=computed(()=>['auto','keyword','product','negative_keyword','negative_product'].includes(section.value)?section.value:'')
const specialView=computed(()=>['settings'].includes(section.value))
const countryNames:Record<string,string>={FR:'法国',UK:'英国',DE:'德国',IT:'意大利',ES:'西班牙',NL:'荷兰',BE:'比利时',IE:'爱尔兰',SE:'瑞典',PL:'波兰',AE:'阿联酋',SA:'沙特'}
const amazonDomains:Record<string,string>={UK:'co.uk',GB:'co.uk',DE:'de',FR:'fr',IT:'it',ES:'es',NL:'nl',BE:'com.be',IE:'ie',SE:'se',PL:'pl',US:'com',CA:'ca',MX:'com.mx',BR:'com.br',JP:'co.jp',AU:'com.au',IN:'in',SG:'sg',AE:'ae',SA:'sa',TR:'com.tr',EG:'eg',ZA:'co.za'}
function searchTermUrl(row:any){
 const market=String(row.country||profiles.value.find(p=>String(p.id)===String(row.profile_id))?.country||country.value||'').toUpperCase()
 const domain=amazonDomains[market],term=String((row.kind==='negative_target'?row.asin:null)||row.name||'').trim()
 if(!domain||!term)return ''
 const asin=/^(?:B[0-9][A-Z0-9]{8}|[0-9]{9}[0-9X])$/i.test(term)
 return `https://www.amazon.${domain}/${asin?'dp/'+term.toUpperCase():'s?k='+encodeURIComponent(term)}`
}
const countries=computed(()=>[...new Set(profiles.value.filter(p=>p.account===store.value).map(p=>p.country==='GB'?'UK':p.country))])
const stores=computed(()=>[...new Set(profiles.value.map(p=>p.account))])
const active=computed(()=>jobs.value.some(j=>['queued','running','waiting'].includes(j.status)))
const syncNotice=ref(false)
watch(active,value=>{if(value)syncNotice.value=true})
const performanceCurrency=ref('')
const currency=computed(()=>data.value.currencies?.includes(performanceCurrency.value)?performanceCurrency.value:data.value.currency||data.value.currencies?.[0]||profiles.value.find(p=>p.id===profile.value)?.currency||'EUR')
const summary=computed(()=>data.value.summaries?.[currency.value]??data.value.summary)
const editCurrency=computed(()=>editRow.value?.currency||currency.value)
const money=(v:any,code=currency.value)=>v==null?'—':new Intl.NumberFormat('zh-CN',{style:'currency',currency:code}).format(Number(v))
const number=(v:any)=>v==null?'—':Number(v).toLocaleString('zh-CN',{maximumFractionDigits:0})
const percent=(v:any)=>v==null?'—':Number(v).toFixed(2)+'%'
const time=(v:any)=>v?new Date(v).toLocaleString('zh-CN',{hour12:false}):'未同步'
const fieldValue=(key:string,v:any)=>v==null?'未设置':internalAdId(key)?(campaignSettings.value?.portfolios?.find((p:any)=>String(p.portfolioId)===String(v))?.name||'已设置'):['bid','defaultBid','dailyBudget'].includes(key)?money(v,editCurrency.value):key==='state'?(states[v]||v):key==='matchType'?(matchNames[v]||v):typeof v==='object'?JSON.stringify(adDisplayData(v)):String(v)
const iso=(d:Date)=>d.toISOString().slice(0,10)
let datesReady=false,datesNeedReload=false
function dateRange(){return {preset:preset.value,dates:[...dates.value]}}
function shareDates(){if(props.context&&dates.value.length===2)emit('date-range',dateRange())}
function restoreDateRange(range:any){
 if(!range||!periodOptions.some(p=>p[0]===range.preset))return false
 const next=range.preset==='custom'?range.dates:periodDates(range.preset)
 if(!Array.isArray(next)||next.length!==2||!next.every(v=>typeof v==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(v))||next[0]>next[1])return false
 if(preset.value===range.preset&&next.every((v,i)=>v===dates.value[i]))return false
 preset.value=range.preset;dates.value=[...next];page.value=1;return true
}
watch(()=>props.context?.dateRange,range=>{
 if(!datesReady||!restoreDateRange(range))return
 if(props.active!==false)void load();else{datesNeedReload=true;request++}
},{deep:true})
function refreshRelativeDates(){
 if(preset.value==='custom'||dates.value?.length!==2)return false
 const next=periodDates(preset.value)
 if(next.every((v,i)=>v===dates.value[i]))return false
 dates.value=next;page.value=1;shareDates();return true
}
function resumeDates(){if(!document.hidden&&props.active!==false){if(refreshRelativeDates())void load();else if(backgroundDirty)requestBackgroundRefresh()}}
function choosePeriod(){if(preset.value!=='custom'){dates.value=periodDates(preset.value);shareDates();search()}}
function changeDates(value:string[]|null){preset.value='custom';dates.value=value||[];if(dates.value.length===2&&dates.value.every(Boolean)){shareDates();search()}}
const shortcuts=periodOptions.filter(p=>p[0]!=='custom').map(p=>({text:p[1],value:()=>periodDates(p[0]).map(v=>new Date(v+'T12:00:00'))}))
let disposed=false
let loadedLevel='',request=0,timer:ReturnType<typeof setInterval>|undefined
let backgroundDirty=false,backgroundRunning=false,backgroundTimer:ReturnType<typeof setTimeout>|undefined
function requestBackgroundRefresh(){
 if(disposed)return
 backgroundDirty=true;clearTimeout(backgroundTimer)
 if(props.active===false||document.hidden)return
 backgroundTimer=setTimeout(async()=>{
  if(disposed||props.active===false||document.hidden||backgroundRunning)return
  if(loading.value){requestBackgroundRefresh();return}
  backgroundDirty=false;backgroundRunning=true
  try{await load(true)}finally{backgroundRunning=false;if(backgroundDirty)requestBackgroundRefresh()}
 },150)
}
function acceptWorkspace(next:any,quiet:boolean){
 if(quiet){
  const previous=new Map((data.value.rows||[]).map((row:any)=>[row.key,row]))
  next.rows=(next.rows||[]).map((row:any)=>{
   const old:any=row.key&&previous.get(row.key);if(!old)return row
   for(const key of Object.keys(old))if(!(key in row))delete old[key]
   Object.assign(old,row);return old
  })
 }
 data.value=next
}
async function load(quiet=false){
 if(disposed)return
 quiet=quiet===true
 refreshRelativeDates()
 if(section.value==='strategy'||!store.value||dates.value?.length!==2)return
 const id=++request;if(!quiet){loading.value=true;selectedCampaigns.value=[];campaignTable.value?.clearSelection();hover.value=null}error.value=''
 if(loadedLevel!==currentLevel.value){data.value={rows:[],total:0};loadedLevel=currentLevel.value}
 try{const p=new URLSearchParams({profile_id:profile.value,level:currentLevel.value,campaign_id:campaign.value?.id||'',adgroup_id:currentLevel.value==='campaign'?'':group.value?.id||'',start:dates.value[0],end:dates.value[1],q:q.value,search_type:searchType.value,state:currentLevel.value==='logs'||currentLevel.value==='search'?'':(state.value||''),page:String(page.value),page_size:String(pageSize.value),sort:sort.value,direction:direction.value,entity_filter:entityFilter.value,match_types:currentLevel.value==='search'?matchTypes.value.join(','):''});if(!props.context){p.delete('profile_id');p.set('store',store.value);p.set('country',country.value)}const r=await api((props.context?'/advertising/workspace?':'/advertising/workspace-overview?')+p);if(id===request){acceptWorkspace(r,quiet);applyPendingBids();if(currentLevel.value==='target')void ensureSuggestions()}}catch(e){if(id===request)error.value=(e as Error).message}finally{if(id===request)loading.value=false}
}
const reportStatusText=computed(()=>!data.value.metrics_ready?'所选日期的日报尚未同步，—不代表0。自动任务会补充当天数据。':`日报更新时间：${time(data.value.report_oldest_at||data.value.report_at)}${data.value.coverage?.complete===false?'；所选日期尚有日报未同步':''}${data.value.report_provisional?'；近期数据仍在更新':''}`)
const budgetStatus=ref<Record<string,any>>({})
let budgetCheckAt=0,budgetBusy=false,budgetReload=false
const budgetProfiles=computed(()=>profiles.value.filter(p=>props.context?p.id===profile.value:p.account===store.value&&(!country.value||(p.country==='GB'?'UK':p.country)===country.value)).map(p=>p.id))
const budgetStates=computed(()=>budgetProfiles.value.map(pid=>budgetStatus.value[pid]).filter(Boolean))
const budgetPending=computed(()=>budgetStates.value.some(s=>['queued','running'].includes(s.status)))
const budgetStatusText=computed(()=>budgetPending.value?'预算及广告组状态更新中':budgetStates.value.some(s=>s.status==='failed')?'预算及广告组状态更新失败，保留旧值':budgetStates.value.length===budgetProfiles.value.length&&budgetStates.value.length&&budgetStates.value.every(s=>s.updated_at&&s.includes_adgroups)?'预算及广告组状态已更新':'预算及广告组状态待更新')
const budgetStatusDetail=computed(()=>budgetProfiles.value.map(pid=>{const p=profiles.value.find(p=>p.id===pid),s=budgetStatus.value[pid];return `${p?.country}：${s?.message||time(s?.updated_at)}`}).join('；')+'。页面停留期间每5分钟检查活动预算与广告组状态；花费按日报更新时间展示。')
async function checkBudgets(force=false){
 if(props.active===false||document.hidden||budgetBusy||!budgetProfiles.value.length)return
 budgetBusy=true
 try{
  const refresh=force||Date.now()-budgetCheckAt>=300000
  if(refresh)budgetCheckAt=Date.now()
  await Promise.all(budgetProfiles.value.map(async pid=>{
   try{const old=budgetStatus.value[pid],r=await api('/advertising/campaign-refresh'+(refresh?'':'?'+new URLSearchParams({profile_id:pid})),refresh?{profile_id:pid,force}:undefined);budgetStatus.value[pid]=r;if(r.updated_at&&old?.updated_at!==r.updated_at)budgetReload=true}
   catch(e){budgetStatus.value[pid]={...budgetStatus.value[pid],status:'failed',message:(e as Error).message}}
  }))
  if(budgetReload&&!loading.value&&!editOpen.value&&!bulkOpen.value&&!selectedCampaigns.value.length&&!Object.keys(inlineValues.value).length){budgetReload=false;requestBackgroundRefresh()}
 }finally{budgetBusy=false}
}
function refreshPage(){void load();void checkBudgets(true)}
watch(()=>props.active,v=>{if(v){if(datesNeedReload){datesNeedReload=false;void load()}else resumeDates();if(backgroundDirty)requestBackgroundRefresh();void checkBudgets()}})
async function loadJobs(){const pid=profile.value;if(pid){const result=await api('/advertising/jobs?'+new URLSearchParams({profile_id:pid}));if(profile.value===pid)jobs.value=result}}
function search(){page.value=1;load()}
function openEntity(kind:string,row:any,parent:any=null){
 const pid=row.profile_id||profile.value,p=profiles.value.find(p=>p.id===pid)
 const brief=(r:any)=>r?{id:r.id,name:r.name,state:r.state,default_bid:r.default_bid,targeting_type:r.targeting_type||campaign.value?.targeting_type,profile_id:pid,currency:r.currency||p?.currency}:null
 emit('open-entity',{profileId:pid,store:p?.account||store.value,country:p?.country||country.value,campaign:brief(kind==='campaign'?row:parent),group:kind==='adgroup'?brief(row):null,dateRange:dateRange()})
}
function drill(row:any){if(currentLevel.value==='campaign')openEntity('campaign',row);else if(currentLevel.value==='adgroup')openEntity('adgroup',row,campaign.value||{id:row.campaign_id,name:row.campaign_name})}
function openFocusedAsin(){
 if(props.context)return
 const f=props.focus;if(!f||!profiles.value.length)return
 store.value=f.store;country.value=f.marketplace==='GB'?'UK':f.marketplace;section.value='campaign';q.value=f.asin;searchType.value='asin';accountChange()
}
watch(()=>props.focus,openFocusedAsin)
async function accountChange(){
 budgetCheckAt=0;jobs.value=[];preferences.value={};widths.value={};profile.value=profiles.value.find(p=>p.account===store.value&&(!country.value||(p.country==='GB'?'UK':p.country)===country.value))?.id||''
 data.value={rows:[],total:0};inlineValues.value={};search();void checkBudgets();await Promise.all([loadJobs(),loadPreferences()])
}
async function sync(){if(dates.value?.length!==2)return;syncing.value=true;try{const r=await api('/advertising/sync',{profile_id:profile.value,start:dates.value[0],end:dates.value[1]});ElMessage.success(r.created?'同步已排队，按天保存表现数据':'已有同步任务，请等待完成');await loadJobs()}catch(e){ElMessage.error((e as Error).message)}finally{syncing.value=false}}
function startEdit(row:any){quickField.value='';campaignSettings.value=null;editRow.value=row;editKind.value=row.kind;isCreate.value=false;editForm.value={state:row.state,bid:row.bid,defaultBid:row.default_bid,name:row.name};previewData.value=null;editError.value='';editOpen.value=true}
function startNegative(row?:any){if(!row&&!group.value){ElMessage.warning('请先进入一个广告组，再新增否定投放');return}editRow.value=row||group.value;isCreate.value=true;editKind.value=/^[A-Z0-9]{10}$/i.test(row?.name||'')?'negative_target':'negative_keyword';editForm.value={keywordText:row?.name||'',matchType:'NEGATIVE_EXACT',asin:editKind.value==='negative_target'?row.name.toUpperCase():''};previewData.value=null;editError.value='';editOpen.value=true}
async function preview(){editBusy.value=true;editError.value='';try{let changes:any={};if(isCreate.value)changes=editKind.value==='negative_keyword'?{keywordText:editForm.value.keywordText,matchType:editForm.value.matchType}:{asin:editForm.value.asin?.trim().toUpperCase()};else{const old=editRow.value;const values:any=editKind.value==='campaign'?campaignBaseline.value:{state:old.state,bid:old.bid,defaultBid:old.default_bid,name:old.name};const keys=editKind.value==='campaign'?Object.keys(editForm.value):editKind.value==='adgroup'?['state','defaultBid','name']:['keyword','target'].includes(editKind.value)?['state','bid']:['state'];for(const key of keys){const val=editForm.value[key];const different=['bid','defaultBid','dailyBudget'].includes(key)?Math.round(Number(val)*100)!==Math.round(Number(values[key])*100):JSON.stringify(val)!==JSON.stringify(values[key]);if(different)changes[key]=val}if(!Object.keys(changes).length){ElMessage.info('没有修改内容');return}}previewData.value=await api('/advertising/edit/preview',{profile_id:editRow.value?.profile_id||profile.value,kind:editKind.value,entity_id:isCreate.value?'':editRow.value.id,adgroup_id:isCreate.value?(editRow.value.adgroup_id||editRow.value.id):'',changes,create:isCreate.value})}catch(e){editError.value=(e as Error).message}finally{editBusy.value=false}}
async function confirm(){if(!previewData.value)return;editBusy.value=true;editError.value='';try{const changed={...previewData.value.changes};const r=await api('/advertising/edit/confirm',{id:previewData.value.id});previewData.value=null;if(r.status==='succeeded'){if(editKind.value==='adgroup'&&group.value?.id===editRow.value.id)group.value={...group.value,...changed,default_bid:changed.defaultBid??group.value.default_bid};ElMessage.success(r.message);editOpen.value=false;delete inlineValues.value[editRow.value.key];await load()}else{editError.value=r.message;await load()}}catch(e){editError.value=(e as Error).message;previewData.value=null}finally{editBusy.value=false}}
async function refreshSuggestions(){
 const batches=new Map<string,Set<string>>();for(const r of data.value.rows){if(!r.adgroup_id)continue;const pid=r.profile_id||profile.value;if(!batches.has(pid))batches.set(pid,new Set());batches.get(pid)!.add(r.adgroup_id)}
 suggestLoading.value=true;try{const errors:string[]=[];for(const [pid,ids] of batches){const r=await api('/advertising/target-suggestions/refresh',{profile_id:pid,group_ids:[...ids]});errors.push(...r.errors.map((e:any)=>e.message))}await load();if(errors.length)ElMessage.warning(errors.join('；'));else ElMessage.success('建议竞价已更新')}catch(e){ElMessage.error((e as Error).message)}finally{suggestLoading.value=false}
}
let suggestionTimer:ReturnType<typeof setTimeout>|undefined
async function ensureSuggestions(){
 const batches=new Map<string,Set<string>>();for(const r of data.value.rows){if(!r.adgroup_id)continue;const pid=r.profile_id||profile.value;if(!batches.has(pid))batches.set(pid,new Set());batches.get(pid)!.add(r.adgroup_id)}
 try{const results=await Promise.all([...batches].map(([pid,ids])=>api('/advertising/target-suggestions/ensure',{profile_id:pid,group_ids:[...ids]})));if(results.some(r=>r.queued)){clearTimeout(suggestionTimer);suggestionTimer=setTimeout(()=>{if(props.active!==false&&!editOpen.value&&!Object.keys(inlineValues.value).length)void load(true)},15000)}}catch{}
}
function applySuggestion(row:any,key:string){const value=row.suggestion?.[key];if(value!=null){changeInline(row,'bid',value);ElMessage.success('已填入竞价框，点击保存即可提交')}}
function keywordJobFinished(event:Event){const detail=(event as CustomEvent).detail;for(const item of detail?.items||[])if(item.status==='succeeded'&&item.key&&item.field)clearInline(item,item.field);const pids=new Set([detail?.profile_id,...(detail?.items||[]).map((i:any)=>i.profile_id)]);if(budgetProfiles.value.some(pid=>pids.has(pid)))requestBackgroundRefresh()}
onMounted(async()=>{window.addEventListener('erp:keyword-job-finished',keywordJobFinished);document.addEventListener('visibilitychange',resumeDates);window.addEventListener('focus',resumeDates);try{const r=await api('/advertising/profiles');profiles.value=r.profiles;store.value=props.context?.store||props.focus?.store||session.value?.default_store||session.value?.current_store||r.profiles[0]?.account||'';country.value=props.context?.country||props.focus?.marketplace||'UK';if(country.value==='GB')country.value='UK';profile.value=props.context?.profileId||r.profiles.find((p:any)=>p.account===store.value&&(p.country==='GB'?'UK':p.country)===country.value)?.id||'';if(!sections.value.some(([k])=>k===section.value))section.value=sections.value[0]?.[0]||'campaign';if(props.focus&&!props.context){q.value=props.focus.asin;searchType.value='asin';section.value='campaign'}dates.value=periodDates('7');restoreDateRange(props.context?.dateRange);datesReady=true;shareDates();await Promise.all([load(),loadJobs(),loadPreferences()]);void checkBudgets();timer=setInterval(async()=>{try{if(document.hidden||props.active===false)return;resumeDates();if(budgetPending.value||budgetReload||Date.now()-budgetCheckAt>=300000)void checkBudgets();if(!active.value)return;await loadJobs();if(!active.value)requestBackgroundRefresh()}catch{}},15000)}catch(e){error.value=(e as Error).message}})
onUnmounted(()=>{disposed=true;window.removeEventListener('erp:keyword-job-finished',keywordJobFinished);document.removeEventListener('visibilitychange',resumeDates);window.removeEventListener('focus',resumeDates);clearTimeout(suggestionTimer);clearTimeout(backgroundTimer);if(timer)clearInterval(timer);request++})
const metricColumns=[{key:'impressions',label:'曝光',type:'number'},{key:'clicks',label:'点击量',type:'number'},{key:'ctr',label:'点击率',type:'percent'},{key:'cost',label:'总成本',type:'money'},{key:'cpc',label:'CPC',type:'money'},{key:'purchases',label:'购买量',type:'number'},{key:'sales',label:'销售额',type:'money'},{key:'acos',label:'ACOS',type:'percent'},{key:'roas',label:'ROAS',type:'ratio'}]
function plainHeader({column}:any){
 const keys:Record<string,string>={'广告活动':'campaign_name','国家':'country','广告位':'placement','竞价调整':'adjustment','状态':'state','竞价策略':'bidding_strategy','每日预算':'budget','开始日期':'start_date','结束日期':'end_date','操作':'actions','时间':'created_at','操作人':'actor','对象':'name','修改内容':'changes','结果':'status','说明':'error'}
 return h('span',[column.label,h(AdsColumnHelp,{label:column.label,description:columnDescription(keys[column.label]||column.property||column.label,currentLevel.value,column.label)})])
}
function columnHelp(col:any){
 let text=columnDescription(col.key,currentLevel.value,col.label)
 if(['money','number','percent','ratio'].includes(col.type)&&!['budget','bid','default_bid','available','top_adjustment'].includes(col.key)){
  text+='\n按广告账户当地日期统计，最近N天截至昨天，不包含今天；不同层级采用各自报表。'
  text+=data.value.metrics_ready?`\n已覆盖 ${data.value.coverage?.days??0} / ${data.value.coverage?.expected??0} 天；更新 ${time(data.value.report_at)}。`:'\n尚无所选日期的表现报表。'
  if(!data.value.coverage?.complete)text+='仅含已同步日期；缺失数据不计为零。'
 }
 return text
}
function format(v:any,t:string,code=currency.value){return v==null?'—':t==='money'?money(v,code):t==='percent'?percent(v):t==='ratio'?Number(v).toFixed(2):number(v)}

const preferences=ref<Record<string,string[]>>({}),columnsOpen=ref(false),columnDraft=ref<string[]>([]),columnSearch=ref(''),columnsBusy=ref(false)
const quickField=ref('')
const inlineValues=ref<Record<string,Record<string,number>>>({}),campaignSettings=ref<any>(null),campaignBaseline=ref<any>({})
const strategyNames:Record<string,string>={LEGACY_FOR_SALES:'动态竞价－只降低',AUTO_FOR_SALES:'动态竞价－提高和降低',MANUAL:'固定竞价',RULE_BASED:'基于规则的竞价'}
const placementFields=[['placementTop','搜索结果顶部（首页）'],['placementRest','搜索结果的其余位置'],['placementProduct','商品页面'],['placementBusiness','亚马逊企业购']]
const widths=ref<Record<string,Record<string,number>>>({}),widthVersion=ref(0)
const width=(key:string,fallback:number)=>widths.value[currentLevel.value]?.[key]||fallback
async function loadPreferences(){const pid=profile.value;if(pid){const params=new URLSearchParams({profile_id:pid});const [p,w]=await Promise.all([api('/advertising/column-preferences?'+params),api('/advertising/column-widths?'+params)]);if(pid===profile.value){preferences.value=p;widths.value=w}}}
let widthSave=Promise.resolve()
function saveWidths(){const body={profile_id:profile.value,level:currentLevel.value,widths:{...(widths.value[currentLevel.value]||{})}};widthSave=widthSave.then(()=>api('/advertising/column-widths',body)).catch((e)=>{ElMessage.error('列宽保存失败：'+(e as Error).message)})}
function resizeColumn(w:number,_old:number,column:{columnKey:string}){if(!column.columnKey)return;widths.value[currentLevel.value]={...(widths.value[currentLevel.value]||{}),[column.columnKey]:Math.max(60,Math.min(1600,Math.round(w)))};saveWidths()}
function resetWidths(){widths.value[currentLevel.value]={};widthVersion.value++;saveWidths()}

function cycleSort(key:string){if(sort.value!==key){sort.value=key;direction.value='asc'}else if(direction.value==='asc')direction.value='desc';else{sort.value='';direction.value='desc'}search()}
function sortMark(key:string){return sort.value===key?(direction.value==='asc'?' ↑':' ↓'):' ↕'}
const fieldLabels:Record<string,string>={'extendedData.creationDateTime':'创建时间','extendedData.lastUpdateDateTime':'更新时间','extendedData.servingStatus':'投放状态','extendedData.servingStatusDetails':'投放状态详情',creationDate:'创建时间',lastUpdateDate:'更新时间',servingStatus:'投放状态',campaignId:'活动ID',adGroupId:'广告组ID',adId:'广告ID',keywordId:'关键词ID',targetId:'投放ID',portfolioId:'广告组合ID',startDate:'开始日期',endDate:'结束日期',targetingType:'投放类型',state:'设置状态',name:'名称',sku:'SKU',asin:'ASIN',expression:'投放表达式',expressionType:'表达式类型',resolvedExpression:'解析投放项',tags:'标签',globalCampaignId:'全局活动ID',marketplaceBudgetAllocation:'预算分配方式','budget.budget':'每日预算','budget.budgetType':'预算类型','dynamicBidding.strategy':'竞价策略','dynamicBidding.placementBidding':'展示位置加价','dynamicBidding.shopperCohortBidding':'受众加价','offAmazonSettings.offAmazonBudgetControlStrategy':'站外投放设置'}
function metricLabel(k:string){const m=k.match(/^(purchasesSameSku|purchases|unitsSoldClicks|unitsSoldSameSku|attributedSalesSameSku|sales|acosClicks|roasClicks)(\d+)d$/);if(m)return ({purchasesSameSku:'直接订单',purchases:'购买量',unitsSoldClicks:'销售件数',unitsSoldSameSku:'直接销售件数',attributedSalesSameSku:'直接销售额',sales:'销售额',acosClicks:'ACOS',roasClicks:'ROAS'} as any)[m[1]]+'（'+m[2]+'天归因）';return ({salesOtherSku7d:'其他商品销售额（7天）',unitsSoldOtherSku7d:'其他商品件数（7天）',addToList:'加入清单',qualifiedBorrows:'有效借阅',royaltyQualifiedBorrows:'版税有效借阅',kindleEditionNormalizedPagesRead14d:'Kindle阅读页数（14天）',kindleEditionNormalizedPagesRoyalties14d:'Kindle版税（14天）',cvr:'转化率'} as any)[k]||k}
const columnCatalog=computed(()=>{
 const l=currentLevel.value;const c:any[]=[];const add=(key:string,label:string,type='text',width=150,def=false)=>c.push({key,label,type,width,def})
 add('country','国家','text',100,!props.context&&!country.value);add('store','店铺')
 if(l!=='campaign'){add('campaign_name','广告活动','text',200,['ad','placement'].includes(l));add('adgroup_name','广告组','text',180)}
 if(['campaign','adgroup','ad'].includes(l))add('targeting_type','活动类型','type',130,true)
 if(['campaign','adgroup','ad','target','placement'].includes(l))add('servingStatus','投放状态','text',150,true)
 if(l==='campaign'){add('budget','每日预算','budget',180,true);add('top_share','首页首位展示份额','percent',170,true);add('top_adjustment','首页首位竞价调整','percent',170,true);add('start_date','开始日期');add('end_date','结束日期');add('bidding_strategy','竞价策略')}
 if(l==='adgroup'){add('default_bid','默认竞价','default_bid',180,true);add('counts','投放 / 商品','counts',120,true)}
 if(l==='ad'){add('identifiers','ASIN / SKU / 品名','identifiers',285,true);add('stock_total','库存','number',120,true);add('available','FBA可售','number',110);add('image','商品图片','image',90,true)}
 if(l==='placement'){add('adjustment','竞价调整','adjustment',160,true);add('bidding_strategy','竞价策略','text',180,true)}
 if(['target','negative','search'].includes(l))add('match_type','匹配类型','match',140,true)
 if(l==='target'){add('suggestion','建议竞价','suggestion',265,true);add('bid','竞价','bid',180,true);add('top_share','首页首位展示份额','percent',170)}
 if(l==='search')add('target','对应投放','text',180,true)
 {
  if(l==='negative')add('image','商品图片','negative_image',90,true)
  for(const m of metricColumns)add(m.key,m.label,m.type,m.type==='money'?125:110,true)
  add('cvr','转化率','percent',120,l==='negative')
  for(const k of data.value.report_columns||[])if(/^(purchases|sales|attributedSales|unitsSold|acosClicks|roasClicks|addToList|qualifiedBorrows|royaltyQualifiedBorrows|kindleEdition)/.test(k))add(k,metricLabel(k),k.startsWith('acos')?'percent':k.startsWith('roas')?'ratio':/sales|royalties/i.test(k)?'money':'number',175)
 }
 for(const k of data.value.raw_columns||[])if(!internalAdId(k)&&!['extendedData.servingStatus','servingStatus','_budget_status_pending'].includes(k)&&!['name','sku','asin','startDate','endDate','budget.budget','targetingType','dynamicBidding.strategy'].includes(k))add('raw:'+k,fieldLabels[k]||k,'raw',180)
 return c
})
const visibleColumns=computed(()=>{
 const keys=preferences.value[currentLevel.value]
 const normalize=(k:string)=>['raw:servingStatus','raw:extendedData.servingStatus'].includes(k)?'servingStatus':currentLevel.value==='ad'&&k==='product_name'?'identifiers':k
 const saved=keys&&currentLevel.value==='negative'&&!keys.some(k=>['clicks','cost','purchases','sales'].includes(k))?[...new Set([...keys,'image','clicks','cost','purchases','sales','cvr'])]:keys
 const columns=saved?[...new Set(saved.map(normalize))].map(k=>columnCatalog.value.find(c=>c.key===k)).filter(Boolean):columnCatalog.value.filter(c=>c.def)
 return currentLevel.value==='ad'?[...columns.filter(c=>c.key==='identifiers'),...columns.filter(c=>c.key!=='identifiers')]:columns
})
const filteredColumns=computed(()=>columnCatalog.value.filter(c=>(c.label+' '+c.key).toLowerCase().includes(columnSearch.value.toLowerCase())))
function openColumns(){columnDraft.value=visibleColumns.value.map(c=>c.key);columnSearch.value='';columnsOpen.value=true}
function moveColumn(i:number,step:number){const a=[...columnDraft.value];if(i+step<0||i+step>=a.length)return;[a[i],a[i+step]]=[a[i+step],a[i]];columnDraft.value=a}
async function saveColumns(){columnsBusy.value=true;try{await api('/advertising/column-preferences',{profile_id:profile.value,level:currentLevel.value,columns:columnDraft.value});preferences.value[currentLevel.value]=[...columnDraft.value];columnsOpen.value=false}catch(e){ElMessage.error((e as Error).message)}finally{columnsBusy.value=false}}
function inlineValue(row:any,key:string){return inlineValues.value[row.key]?.[key]??row[key]}
function changeInline(row:any,key:string,v:any){if(rowSaving(row))return;(inlineValues.value[row.key]??={})[key]=v}
function clearInline(row:any,key:string){const values=inlineValues.value[row.key];if(!values)return;delete values[key];if(!Object.keys(values).length)delete inlineValues.value[row.key]}
function dirtyInline(row:any,key:string){return inlineValues.value[row.key]?.[key]!=null&&Math.round(Number(inlineValues.value[row.key][key])*100)!==Math.round(Number(row[key])*100)}
function pendingRow(row:any){return [...pendingBids.values()].find(c=>c.profile===(row.profile_id||profile.value)&&c.kind===row.kind&&c.id===row.id)}
function bulkPending(row:any){return pendingBulkItem(row.profile_id||profile.value,row.kind||'campaign',row.id)}
function rowSaving(row:any){return !!pendingRow(row)||!!bulkPending(row)}
function pendingLabel(row:any){if(bulkPending(row))return '后台处理中';const stage=pendingRow(row)?.stage;return stage==='checking'?'后台核对中':stage==='sending'?'后台提交中':stage==='refreshing'?'正在更新结果':''}
function applyPendingBids(){for(const change of pendingBids.values())for(const row of data.value.rows||[])if((row.profile_id||profile.value)===change.profile&&row.kind===change.kind&&row.id===change.id&&(change.field!=='adjustment'||row.placement_field===change.key)){row[change.field]=change.value;if(change.pendingStatus)row.servingStatus='BUDGET_UPDATING'}}
async function submitInlineChange(row:any,key:string,value:any){
 if(!canEditAds.value||rowSaving(row)||loading.value||row.state==='ARCHIVED')return
 const isState=key==='state'
 const field=isState?'state':key==='dailyBudget'?'budget':key==='defaultBid'?'default_bid':key.startsWith('placement')?'adjustment':'bid',amount=isState?String(value):Number(value)
 const noun=isState?'状态':key==='dailyBudget'?'预算':'竞价'
 if(isState?!['ENABLED','PAUSED'].includes(String(value)):(value==null||!Number.isFinite(amount)||Number(amount)<(field==='adjustment'?0:0.01)||Number(amount)>(field==='adjustment'?900:field==='budget'?1000000000:10000))){ElNotification({title:noun+'未提交',message:'请输入有效'+noun+(field==='adjustment'?'百分比（0–900）':''),type:'error',position:'top-right'});clearInline(row,field);return}
 if(isState?amount===row.state:amount===Number(row[field]))return
 const oldStatus=row.servingStatus
 const pid=row.profile_id||profile.value,id=[pid,row.kind,row.id,key].join(':'),previous=row[field]
 let restore=previous,submitted=false
 pendingBids.set(id,{profile:pid,kind:row.kind,id:row.id,field,value:amount,key,pendingStatus:key==='dailyBudget'&&Number(amount)>Number(previous),stage:'checking'})
 row[field]=amount;if(key==='dailyBudget'&&Number(amount)>Number(previous))row.servingStatus='BUDGET_UPDATING';clearInline(row,field)
 try{
  const preview=await api('/advertising/edit/preview',{profile_id:pid,kind:row.kind,entity_id:row.id,changes:{[key]:amount}})
  restore=preview.before[key]??previous;submitted=true
  pendingBids.get(id)!.stage='sending'
  const result=await api('/advertising/edit/confirm',{id:preview.id})
  if(result.status!=='succeeded')throw Object.assign(new Error(result.message||'亚马逊未接受修改'),{uncertain:result.status==='unknown'||result.status==='sending'})
  pendingBids.get(id)!.stage='refreshing'
  if(key==='defaultBid'&&profile.value===pid&&group.value?.id===row.id)group.value={...group.value,default_bid:amount}
  await load(true)
  if(!disposed&&key==='dailyBudget')void checkBudgets(true)
 }catch(e){
  pendingBids.delete(id);clearInline(row,field);row[field]=restore;row.servingStatus=oldStatus
  for(const visible of data.value.rows||[])if((visible.profile_id||profile.value)===pid&&visible.kind===row.kind&&visible.id===row.id&&(field!=='adjustment'||visible.placement_field===key)){visible[field]=restore;visible.servingStatus=oldStatus}
  const uncertain=(e as any).uncertain??submitted
  const temporary=(e as any).transient===true
  const message=uncertain?'暂时无法确认亚马逊是否已接受修改，页面暂显示原值。请在操作日志或亚马逊后台核对后再修改，不要重复提交。':temporary?'核对最新设置时连接中断，本次修改尚未提交亚马逊，已恢复原值，请稍后再试。':(e as Error).message
  ElNotification({title:uncertain?noun+'提交结果待核对':temporary?noun+'暂未提交':noun+'提交失败，已回退',message:row.name+'：'+message,type:uncertain||temporary?'warning':'error',position:'top-right',duration:0})
 }finally{pendingBids.delete(id)}
}
async function quickEdit(row:any,key:string,value:any){if(['state','bid','defaultBid','dailyBudget'].includes(key)||key.startsWith('placement')){await submitInlineChange(row,key,value);return}if(rowSaving(row)||editBusy.value||loading.value)return;startEdit(row);quickField.value=key;if(row.kind==='campaign'){campaignBaseline.value={state:row.state,dailyBudget:row.budget,...(row.placement_field?{[row.placement_field]:row.adjustment}:{})};editForm.value={...campaignBaseline.value}}editForm.value[key]=value;await preview()}
async function startCampaign(row:any){startEdit(row);editBusy.value=true;try{const d=await api('/advertising/campaign-settings/'+encodeURIComponent(row.id)+'?'+new URLSearchParams({profile_id:row.profile_id||profile.value}));campaignSettings.value=d;campaignBaseline.value=structuredClone(d.values);editForm.value=structuredClone(d.values)}catch(e){editError.value=(e as Error).message}finally{editBusy.value=false}}
function audienceChange(id:string){editForm.value.audiences=id?[{shopperCohortType:'AUDIENCE_SEGMENT',percentage:0,audienceSegments:[{audienceId:id}]}]:[]}
type ServingTone='success'|'danger'|'warning'|'muted'
type ServingLabel={label:string,tone:ServingTone}
const servingNames:Record<string,[string,ServingTone]>={
 BUDGET_UPDATING:['预算更新中','warning'],AD_STATUS_LIVE:['投放中','success'],TARGETING_CLAUSE_STATUS_LIVE:['投放中','success'],AD_ELIGIBLE:['可投放','success'],ELIGIBLE:['可投放','success'],AD_INELIGIBLE:['不可投放','danger'],INELIGIBLE:['不可投放','danger'],OTHER:['其他状态','muted'],UNKNOWN:['未知状态','muted'],AD_CREATION_FAILED:['广告创建失败','danger'],AD_CREATION_OFFLINE_FAILED:['广告创建失败','danger'],AD_CREATION_OFFLINE_IN_PROGRESS:['广告创建中','warning'],AD_CREATION_OFFLINE_PENDING:['等待创建','warning'],PIR_RULE_EXCLUDED:['规则已排除','muted'],VARIATION_PARENT:['父体不可投放','danger'],
 OUT_OF_BUDGET:['预算不足','danger'],EXCEED_SPENDS_LIMIT:['超出花费限制','danger'],PAYMENT_FAILURE:['付款失败','danger'],PENDING_REVIEW:['待审核','warning'],PENDING_START_DATE:['未到开始日期','warning'],CREATIVE_REJECTED:['素材审核未通过','danger'],REJECTED:['审核未通过','danger'],POLICING_SUSPENDED:['投放受限','danger'],ARCHIVED:['已归档','muted'],PAUSED:['已暂停','muted'],STATUS_ENABLED:['已启用','success'],INCOMPLETE:['资料不完整','warning'],LOW_BID:['竞价过低','warning'],ENDED:['已结束','muted'],OUT_OF_STOCK:['缺货','danger'],NO_INVENTORY:['缺货','danger'],NOT_BUYABLE:['商品不可购买','danger'],NOT_IN_BUYBOX:['未获得购物车','warning'],NO_PURCHASABLE_OFFER:['没有可购买报价','danger'],MISSING_IMAGE:['缺少图片','warning'],MISSING_DECORATION:['素材不完整','warning'],LANDING_PAGE_NOT_AVAILABLE:['落地页不可用','danger'],ADS_NOT_DELIVERING:['广告未投放','warning'],BLOCKED:['投放被阻止','danger']
}
function servingLabels(value:any):ServingLabel[]{
 if(value==null||value==='')return []
 if(Array.isArray(value))return value.flatMap(servingLabels)
 if(typeof value==='object')return Object.values(value).flatMap(servingLabels)
 const code=String(value).replace(/_DETAIL$/,'')
 const key=servingNames[code]?code:Object.keys(servingNames).find(k=>code.endsWith('_'+k))
 const [label,tone]=key?servingNames[key]:['其他投放状态','muted'] as const
 return [{label,tone}]
}
function servingStatus(value:any){return servingLabels(value).map(s=>s.label).join('；')||'—'}
function statusItems(row:any,col:any){
 const pending=pendingRow(row);if(col.key==='servingStatus'&&pending?.field==='state')return [{label:pending.value==='ENABLED'?'开启提交中':'暂停提交中',tone:'warning'}]
 const value=col.key==='servingStatus'?(row.servingStatus??row.raw_fields?.['extendedData.servingStatus']??row.raw_fields?.servingStatus):(col.type==='raw'?row.raw_fields?.[col.key.slice(4)]:row[col.key])
 const labels=servingLabels(value)
 return labels.length?labels:[{label:'—',tone:'muted'}]
}
function cellText(row:any,col:any){let v=col.type==='raw'?row.raw_fields?.[col.key.slice(4)]:row.metrics&&col.key in row.metrics?row.metrics[col.key]:row[col.key];if(col.key==='product_name'&&!v)return '';if(v==null||v==='')return '—';if(col.key.toLowerCase().includes('servingstatus'))return servingStatus(v);if(col.key.endsWith('DateTime'))return time(v);if(col.type==='type')return v==='AUTO'?'自动投放':'手动投放';if(col.type==='match')return matchNames[String(v).toUpperCase()]||v;if(col.key==='bidding_strategy')return strategyNames[v]||v;if(['money','number','percent','ratio'].includes(col.type))return format(v,col.type,row.currency||currency.value);return typeof v==='object'?JSON.stringify(adDisplayData(v)):String(v)}
const reportedChartRows=computed(()=>data.value.trends?.[currency.value]||data.value.trend||[])
const chartRows=computed(()=>{
 const rows=reportedChartRows.value
 if(!rows.length||dates.value?.length!==2)return rows
 const byDate=new Map(rows.map((r:any)=>[r.date,r])),result:any[]=[]
 for(let d=new Date((data.value.start||dates.value[0])+'T12:00:00Z'),end=new Date((data.value.end||dates.value[1])+'T12:00:00Z');d<=end;d.setUTCDate(d.getUTCDate()+1)){
  const day=iso(d);result.push(byDate.get(day)||{date:day,clicks:null,cost:null,acos:null,missing:true})
 }
 return result
})
const chartCoverageText=computed(()=>{
 const n=reportedChartRows.value.length,total=chartRows.value.length
 return n&&n<total?`所选 ${total} 天中，已有 ${n} 天日报；缺失日期留空。`:''
})
const chartSeries=[{key:'clicks',label:'点击量',color:'#438df1'},{key:'cost',label:'花费',color:'#e89260'},{key:'acos',label:'ACOS',color:'#75bc5a'}]
const chartMax=computed(()=>Math.max(1,...chartRows.value.flatMap((r:any)=>[Number(r.clicks||0),Number(r.cost||0)])))
const acosMax=computed(()=>Math.max(1,...chartRows.value.map((r:any)=>Number(r.acos||0)))*1.1)
const seriesMax=(key:string)=>key==='acos'?acosMax.value:chartMax.value
function x(i:number){return 55+i*1090/Math.max(1,chartRows.value.length-1)}
function path(key:string,max:number){let result='',prev='';chartRows.value.forEach((r:any,i:number)=>{if(r[key]==null){prev='';return}const consecutive=prev&&new Date(r.date).getTime()-new Date(prev).getTime()===86400000;result+=(consecutive?' L':' M')+x(i)+','+(185-Number(r[key])/max*155);prev=r.date});return result}
function hoverChart(e:MouseEvent){const b=(e.currentTarget as SVGElement).getBoundingClientRect();hover.value=Math.max(0,Math.min(chartRows.value.length-1,Math.round(((e.clientX-b.left)/b.width*1200-55)/1090*Math.max(1,chartRows.value.length-1))))}
function inlineEnter(row:any,key:string,event:KeyboardEvent){if(!(event.target instanceof HTMLInputElement)||event.key!=='Enter')return;event.preventDefault();event.stopPropagation();const value=event.target.value.trim();changeInline(row,key,value===''?null:Number(value));if(key==='adjustment')void quickEdit(row,row.placement_field,inlineValue(row,key));else saveInline(row,key)}
function saveInline(row:any,key:string){if(!canEditAds.value||row.state==='ARCHIVED'||!dirtyInline(row,key)||editBusy.value||loading.value)return;quickEdit(row,key==='budget'?'dailyBudget':key==='default_bid'?'defaultBid':'bid',inlineValue(row,key))}
const campaignTable=ref<any>(null),selectedCampaigns=ref<any[]>([])
const bulkOpen=ref(false),bulkBusy=ref(false),bulkMode=ref('state'),bulkStage=ref('form'),bulkState=ref('PAUSED'),bulkError=ref('')
const bulkAdjustment=ref('fixed'),bulkPercent=ref<number|undefined>()
const bulkSuggestionDirection=ref('increase'),bulkSuggestionPercent=ref<number|undefined>(0)
const bulkSuggestionTier=computed(()=>bulkMode.value==='bid'&&['low','mid','high'].includes(bulkAdjustment.value))
const suggestionTierNames:Record<string,string>={low:'低档',mid:'中档',high:'高档'}
function bulkSuggestionValue(row:any,tier=bulkAdjustment.value){const raw=row.suggestion?.[tier];if(raw==null||raw==='')return null;const value=Math.round(Number(raw)*100)/100;return Number.isFinite(value)&&value>=.01&&value<=10000?value:null}
function bulkSuggestedBid(row:any){
 const base=bulkSuggestionValue(row),percent=bulkSuggestionPercent.value
 if(base==null||!Number.isFinite(percent))return null
 return Math.round(base*100*(1+(bulkSuggestionDirection.value==='decrease'?-1:1)*Number(percent)/100))/100
}
const bulkRows=ref<any[]>([]),bulkBudgets=ref<Record<string,number|undefined>>({})
const bulkCurrencies=computed(()=>[...new Set<string>(bulkRows.value.map(r=>r.currency))])
const bulkReady=computed(()=>bulkRows.value.filter(r=>r.status==='preview'&&r.previewId))
const bulkStatusNames:Record<string,string>={pending:'待预览',preview:'待确认',skipped:'无须修改',no_suggestion:'已跳过',preview_failed:'预览失败',sending:'提交中',succeeded:'成功',failed:'失败',unknown:'结果待核对'}
function openBulk(mode:string){
 if(!canEditAds.value||!selectedCampaigns.value.length||loading.value)return
 bulkRows.value=selectedCampaigns.value.filter(r=>!rowSaving(r)).map(r=>({...r,name:currentLevel.value==='ad'?(r.sku||r.asin||r.name):r.name,profile_id:r.profile_id||profile.value,currency:r.currency||currency.value,status:'pending',previewId:null,message:'',before:null,after:null}))
 bulkMode.value=mode;bulkStage.value='form';bulkState.value='PAUSED';bulkBudgets.value={};bulkAdjustment.value='fixed';bulkPercent.value=undefined;bulkSuggestionDirection.value='increase';bulkSuggestionPercent.value=0;bulkError.value='';bulkOpen.value=true
}
function validateBulk(){
 if(bulkBusy.value)return false
 if(bulkMode.value!=='state'&&bulkAdjustment.value==='fixed'&&bulkCurrencies.value.some(c=>!Number.isFinite(bulkBudgets.value[c])||Number(bulkBudgets.value[c])<=0||(bulkMode.value==='bid'&&Number(bulkBudgets.value[c])>10000))){bulkError.value='请为每种币种填写大于 0 的金额';return false}
 if(bulkMode.value!=='state'&&['increase','decrease'].includes(bulkAdjustment.value)&&(!Number.isFinite(bulkPercent.value)||Number(bulkPercent.value)<=0||Number(bulkPercent.value)>10000||(bulkAdjustment.value==='decrease'&&Number(bulkPercent.value)>=100))){bulkError.value='请填写有效百分比；降低幅度必须小于100%';return false}
 if(bulkSuggestionTier.value){
  const percent=bulkSuggestionPercent.value
  if(!Number.isFinite(percent)||Number(percent)<0||Number(percent)>10000||(bulkSuggestionDirection.value==='decrease'&&Number(percent)>=100)){bulkError.value='请填写有效百分比（可填 0 表示不调整）；降低幅度必须小于100%';return false}
  if(bulkRows.value.some(row=>{const value=bulkSuggestedBid(row);return value!=null&&(value<.01||value>10000)})){bulkError.value='调整后的竞价须在 0.01 至 10000 之间，请修改百分比';return false}
 }
 return true
}
async function previewBulk(){
 if(!validateBulk())return
 bulkBusy.value=true;bulkError.value='';bulkStage.value='preview'
 try{for(const row of bulkRows.value){
  row.status='pending';row.previewId=null;row.message='';row.before=null;row.after=null
  const key=bulkMode.value==='state'?'state':bulkMode.value==='bid'?'bid':'dailyBudget',value=bulkMode.value==='state'?bulkState.value:bulkSuggestionTier.value?bulkSuggestedBid(row):bulkBudgets.value[row.currency]
  if(bulkSuggestionTier.value&&value==null){row.status='no_suggestion';row.message='没有可用的'+suggestionTierNames[bulkAdjustment.value]+'建议竞价，保留原竞价';continue}
  const adjustment=bulkMode.value!=='state'&&['increase','decrease'].includes(bulkAdjustment.value)?{percentage:{field:key,direction:bulkAdjustment.value,percent:bulkPercent.value}}:{changes:{[key]:value}}
  try{const result=await api('/advertising/edit/preview',{profile_id:row.profile_id,kind:row.kind||'campaign',entity_id:row.id,...adjustment});row.previewId=result.id;row.before=result.calculated_from??result.before[key];row.after=result.changes[key];row.status='preview'}
  catch(e){row.message=(e as Error).message;row.status=row.message==='没有修改内容'?'skipped':'preview_failed'}
 }}finally{bulkBusy.value=false}
}
function submitBulkBids(){
 if(bulkMode.value!=='bid'||!validateBulk())return
 const rows=bulkRows.value.filter(row=>!bulkSuggestionTier.value||bulkSuggestedBid(row)!=null)
 if(!rows.length){bulkError.value='所选项目没有有效建议价，请选择其他档位或调整方式';return}
 const items=rows.map(row=>({profile_id:row.profile_id,kind:row.kind,entity_id:row.id,...(['increase','decrease'].includes(bulkAdjustment.value)?{percentage:{field:'bid',direction:bulkAdjustment.value,percent:bulkPercent.value}}:{changes:{bid:bulkSuggestionTier.value?bulkSuggestedBid(row):bulkBudgets.value[row.currency]}})}))
 const skipped=bulkRows.value.length-rows.length
 const label=`批量调整竞价（${rows.length} 项）`+(skipped?`，${skipped} 项缺少建议价已跳过`:'')
 if(submitBulkEdits(rows.map(row=>({...row,field:'bid',after:bulkSuggestionTier.value?bulkSuggestedBid(row):bulkAdjustment.value==='fixed'?bulkBudgets.value[row.currency]:undefined})),label,false,items)){bulkOpen.value=false;bulkStage.value='done'}
}
async function confirmBulk(){
 if(bulkBusy.value||bulkStage.value!=='preview'||!bulkReady.value.length)return
 const archiving=bulkMode.value==='state'&&bulkState.value==='ARCHIVED'
 if(archiving){try{await ElMessageBox.confirm(`确认归档这 ${bulkReady.value.length} 项？归档后无法撤回，也不能重新开启。`,'归档不可撤回',{type:'warning',confirmButtonText:'确认归档，无法撤回',cancelButtonText:'取消'})}catch{return}}
 const ready=[...bulkReady.value]
 if(submitBulkEdits(ready.map(r=>({...r,field:bulkMode.value})),'批量调整'+({state:'状态',bid:'竞价',budget:'预算'}[bulkMode.value])+`（${ready.length} 项）`,archiving)){
  bulkOpen.value=false;bulkStage.value='done'
  for(const row of ready){row.previewId=null;row.status='queued'}
 }
}
const bulkValue=(row:any,value:any)=>value==null?'—':bulkMode.value==='state'?(states[value]||value):money(value,row.currency)
const draggedColumn=ref(''),dragOverColumn=ref('')
function dragColumn(event:DragEvent,key:string){draggedColumn.value=key;if(event.dataTransfer){event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',key)}}
function dropColumn(event:DragEvent,key:string){const from=draggedColumn.value;if(!from||from===key)return;const rect=(event.currentTarget as HTMLElement).getBoundingClientRect();const after=event.clientY>rect.top+rect.height/2;const items=columnDraft.value.filter(k=>k!==from);items.splice(items.indexOf(key)+(after?1:0),0,from);columnDraft.value=items;draggedColumn.value='';dragOverColumn.value=''}
const placementRows=computed(()=>data.value.rows.flatMap((r:any)=>{const raw=r.raw_fields?.['dynamicBidding.placementBidding'];const values=Array.isArray(raw)?raw:[];return [['PLACEMENT_TOP','搜索结果顶部（首页）'],['PLACEMENT_REST_OF_SEARCH','搜索结果其余位置'],['PLACEMENT_PRODUCT_PAGE','商品页面'],['PLACEMENT_AMAZON_BUSINESS','亚马逊企业购']].map(([key,label])=>({...r,placement:label,adjustment:values.find((v:any)=>v.placement===key)?.percentage??null}))}))
</script>
<template>
<div class="advertising-panel" ref="panelRoot">
 <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
 <div v-if="campaign" class="ads-breadcrumb"><el-button link @click="emit('home')">{{store}}-{{country}}</el-button><el-icon><ArrowRight/></el-icon><el-button link class="breadcrumb-campaign" @click="openEntity('campaign',campaign)">广告活动：{{campaign.name}}</el-button><template v-if="group"><el-icon><ArrowRight/></el-icon><strong>广告组：{{group.name}}</strong></template></div>
 <nav class="ads-sections" aria-label="广告分类"><el-radio-group :model-value="section" @change="(value:string|number|boolean)=>selectSection(String(value))"><el-radio-button v-for="[key,label] in sections" :key="key" :value="key">{{label}}</el-radio-button></el-radio-group></nav>

 <div v-if="syncNotice" class="ads-progress" role="status">{{active?'正在同步资料与日报，完成后自动更新，关闭页面不影响后台同步。':jobs.some(j=>j.status==='failed')?'同步记录中有失败项目，可查看同步记录。':'同步已完成，数据已自动更新。'}}</div>
 <AdsStrategyPanel v-if="section==='strategy'" :profiles="profiles" :context="context" :store="store" :country="country"/>
 <AdsEntityLog v-if="logRow" :row="logRow" @close="logRow=null"/>
 <AdsSearchActions v-if="searchAction" :mode="searchAction" :rows="searchActionRows" :profiles="profiles" :manual-destination="manualDestination" @close="searchAction=null" @done="requestBackgroundRefresh"/>
 <AdsAddProducts v-if="addAdsOpen" :profile-id="profile" :campaign-id="campaign?.id" :group-id="group?.id" @close="addAdsOpen=false" @done="requestBackgroundRefresh"/>
 <section v-if="section!=='strategy'&&!['negative','logs'].includes(currentLevel)" class="panel performance">
  <div class="kpis"><div><span>总花费</span><b>{{money(summary?.cost)}}</b></div><div><span>ACOS</span><b>{{percent(summary?.acos)}}</b></div><div><span>销售额</span><b>{{money(summary?.sales)}}</b></div><div><span>广告订单</span><b>{{number(summary?.purchases)}}</b></div><div><span>曝光</span><b>{{number(summary?.impressions)}}</b></div><div><span>点击</span><b>{{number(summary?.clicks)}}</b></div></div>
  <p v-if="chartCoverageText" class="chart-coverage muted">{{chartCoverageText}}</p>
  <div class="chart" v-if="chartRows.length"><svg viewBox="0 0 1200 220" role="img" :aria-label="specialView?'活动整体每日表现走势':'广告每日表现走势'" @mousemove="hoverChart" @mouseleave="hover=null"><g v-for="i in 4" :key="i"><line x1="55" x2="1145" :y1="30+(i-1)*155/3" :y2="30+(i-1)*155/3" stroke="#e5eeea" stroke-dasharray="3 5"/><text x="0" :y="34+(i-1)*155/3">{{(chartMax*(4-i)/3).toFixed(1)}}</text><text x="1150" :y="34+(i-1)*155/3">{{(acosMax*(4-i)/3).toFixed(0)}}%</text></g><g v-for="series in chartSeries" :key="series.key" :data-series="series.key"><path :d="path(series.key,seriesMax(series.key))" :stroke="series.color" fill="none" stroke-width="2.5"/><template v-for="(r,i) in chartRows" :key="r.date"><circle v-if="r[series.key]!=null" :cx="x(Number(i))" :cy="185-Number(r[series.key])/seriesMax(series.key)*155" r="2" :fill="series.color"/></template></g><text x="55" y="212">{{chartRows[0].date}}</text><text x="1050" y="212">{{chartRows.at(-1).date}}</text><line v-if="hover!==null" :x1="x(hover)" :x2="x(hover)" y1="20" y2="190" stroke="#a5b8b1" stroke-dasharray="5"/></svg><div class="chart-tooltip" v-if="hover!==null&&chartRows[hover]"><b>{{chartRows[hover].date}}</b><span v-if="chartRows[hover].missing">该日尚未同步</span><span v-if="specialView">活动整体表现</span><span v-for="series in chartSeries" :key="series.key"><i :style="{background:series.color}"/>{{series.label}} {{series.key==='acos'?percent(chartRows[hover].acos):series.key==='cost'?money(chartRows[hover].cost):number(chartRows[hover].clicks)}}</span></div></div><div v-else class="chart-empty"></div>
 </section>
 <section v-if="section!=='strategy'" class="panel results">
  <form class="ads-filters" @submit.prevent="search">
   <el-select v-model="store" :disabled="!!context" aria-label="店铺" placeholder="店铺" @change="accountChange"><el-option v-for="v in stores" :key="v" :value="v" :label="v"/></el-select>
   <el-select v-model="country" :disabled="!!context" clearable aria-label="国家" placeholder="全部国家" @change="accountChange"><el-option label="全部国家" value=""/><el-option v-for="v in countries" :key="v" :value="v" :label="(countryNames[v]||v)+' · '+v"/></el-select>
   <el-select v-model="searchType" aria-label="搜索类型"><el-option label="综合搜索" value="all"/><el-option label="SKU" value="sku"/><el-option label="ASIN" value="asin"/><el-option label="FNSKU" value="fnsku"/><el-option label="品名" value="product_name"/><el-option label="活动 / 广告组名称" value="name"/></el-select>
   <el-input v-model="q" :prefix-icon="Search" clearable placeholder="SKU / ASIN / FNSKU / 品名 / 活动或组名称" aria-label="广告搜索"/>
   <el-select v-model="preset" aria-label="时间范围" @change="choosePeriod"><el-option v-for="p in periodOptions" :key="p[0]" :value="p[0]" :label="p[1]"/></el-select>
   <el-date-picker v-model="dates" type="daterange" value-format="YYYY-MM-DD" :shortcuts="shortcuts" start-placeholder="开始日期" end-placeholder="结束日期" range-separator="至" @change="changeDates"/>
   <el-select v-if="currentLevel==='search'" v-model="matchTypes" multiple collapse-tags collapse-tags-tooltip clearable placeholder="匹配类型（多选）" aria-label="匹配类型多选" style="width:210px"><el-option v-for="k in ['EXACT','PHRASE','BROAD','CLOSE','LOOSE','SUBSTITUTES','COMPLEMENTS','AUTO','MANUAL','UNKNOWN']" :key="k" :value="k" :label="matchNames[k]"/></el-select>
   <el-select v-if="!['logs','search'].includes(currentLevel)" v-model="state" clearable :value-on-clear="''" placeholder="全部状态" aria-label="状态"><el-option label="除归档外" value="NOT_ARCHIVED"/><el-option label="全部状态" value=""/><el-option v-for="k in ['ENABLED','PAUSED','ARCHIVED']" :key="k" :label="states[k]" :value="k"/></el-select><el-button native-type="submit" type="primary">查询</el-button>
  <el-select v-if="data.currencies?.length>1" v-model="performanceCurrency" aria-label="走势币种" :placeholder="currency"><el-option v-for="c in data.currencies" :key="c" :value="c" :label="c"/></el-select><el-button :disabled="!profile" @click="resetWidths">重置列宽</el-button><el-button :icon="Refresh" @click="refreshPage">刷新页面</el-button><span class="budget-status muted" :title="budgetStatusDetail">{{budgetStatusText}}</span><el-button v-if="canEditAds && ['campaign','adgroup'].includes(section)" type="primary" plain :disabled="!profile||(!props.context&&!country)" @click="openCreate">{{section==='campaign'?'创建广告活动':'创建广告组'}}</el-button><el-button v-if="canEditAds&&canAddManualTarget" type="primary" :disabled="!profile||loading||data.campaign_state==='ARCHIVED'||data.adgroup_state==='ARCHIVED'" @click="openManualTarget">新增投放</el-button><template v-if="['campaign','ad','target','negative'].includes(currentLevel)&&canEditAds"><span class="selection-count">已选 {{selectedCampaigns.length}} 项</span><el-button :disabled="!selectedCampaigns.length||loading" @click="openBulk('state')">批量调整状态</el-button><el-button v-if="['campaign','target'].includes(currentLevel)" :disabled="!selectedCampaigns.length||loading" @click="openBulk(currentLevel==='target'?'bid':'budget')">{{currentLevel==='target'?'批量调整竞价':'批量调整预算'}}</el-button></template><template v-if="currentLevel==='search'&&canEditAds"><span>已选 {{selectedCampaigns.length}} 项</span><el-button :disabled="!selectedCampaigns.length||loading" @click="openSearchAction('keyword')">批量添加关键词</el-button><el-button :disabled="!selectedCampaigns.length||loading" @click="openSearchAction('negative')">批量否定</el-button></template><el-button v-if="canEditAds && currentLevel==='negative'" type="primary" @click="startNegative()">新增否定投放</el-button><el-button v-if="section==='ad'&&canEditAds" :disabled="!profile||(!props.context&&!country)" @click="addAdsOpen=true">添加广告</el-button><el-button v-if="currentLevel!=='logs'&&!specialView" :disabled="!profile" @click="openColumns">自定义列</el-button></form>
  <p v-if="currentLevel==='negative'" class="report-freshness muted">点击、花费、订单、销售额和转化率来自所选日期内，同一广告组（活动级规则为同一活动）匹配到的搜索词；否定词组按完整词组关联。规则之间可能重叠，不代表否定规则自身产生业绩。{{reportStatusText}}</p>
  <p v-if="!['negative','logs'].includes(currentLevel)" class="report-freshness muted">{{reportStatusText}}<el-button v-if="canEditAds&&profile&&country" link :loading="syncing" :disabled="active" @click="sync">同步所选日期日报</el-button></p>
 <template v-if="specialView">
 <el-table v-if="section==='placement'" :data="placementRows" border v-loading="loading"><el-table-column :render-header="plainHeader" prop="name" label="广告活动" min-width="220"/><el-table-column :render-header="plainHeader" prop="country" label="国家" width="90"/><el-table-column :render-header="plainHeader" prop="placement" label="广告位" min-width="220"/><el-table-column :render-header="plainHeader" label="竞价调整" align="center"><template #default="{row}">{{percent(row.adjustment)}}</template></el-table-column><el-table-column :render-header="plainHeader" label="操作" width="120"><template #default="{row}"><el-button v-if="canEditAds" link @click="startCampaign(row)">编辑设置</el-button></template></el-table-column></el-table>
 <el-table v-else :data="data.rows" border v-loading="loading"><el-table-column :render-header="plainHeader" prop="name" label="广告活动" min-width="220"/><el-table-column :render-header="plainHeader" prop="country" label="国家" width="90"/><el-table-column :render-header="plainHeader" label="状态"><template #default="{row}">{{states[row.state]}}</template></el-table-column><el-table-column :render-header="plainHeader" label="竞价策略" min-width="200"><template #default="{row}">{{strategyNames[row.bidding_strategy]||row.bidding_strategy||'—'}}</template></el-table-column><el-table-column :render-header="plainHeader" label="每日预算" align="center"><template #default="{row}">{{money(row.budget,row.currency)}}</template></el-table-column><el-table-column :render-header="plainHeader" v-if="section==='settings'" prop="start_date" label="开始日期"/><el-table-column :render-header="plainHeader" v-if="section==='settings'" prop="end_date" label="结束日期"/><el-table-column :render-header="plainHeader" label="操作" width="120"><template #default="{row}"><el-button v-if="canEditAds" link @click="startCampaign(row)">编辑设置</el-button></template></el-table-column></el-table>
 </template>
 <el-table v-else-if="currentLevel==='logs'" border class="ads-table" @header-dragend="resizeColumn" :key="widthVersion" :data="data.rows" v-loading="loading"><el-table-column :render-header="plainHeader" label="时间" column-key="created_at" :width="width('created_at',180)"><template #default="{row}">{{time(row.created_at)}}</template></el-table-column><el-table-column :render-header="plainHeader" prop="actor" label="操作人" column-key="actor" :width="width('actor',100)"/><el-table-column :render-header="plainHeader" prop="name" label="对象" column-key="name" :width="width('name',220)"/><el-table-column :render-header="plainHeader" label="修改内容" column-key="changes" :width="width('changes',300)"><template #default="{row}"><div v-for="(v,k) in row.changes" :key="k">{{fieldNames[String(k)]||k}}：{{fieldValue(String(k),row.before[k])}} → {{fieldValue(String(k),v)}}</div></template></el-table-column><el-table-column :render-header="plainHeader" label="结果" column-key="status" :width="width('status',135)"><template #default="{row}">{{statusNames[row.status]||row.status}}</template></el-table-column><el-table-column :render-header="plainHeader" prop="error" label="说明" column-key="error" :width="width('error',220)"/></el-table>
 <el-table v-else ref="campaignTable" border @selection-change="(rows:any[])=>selectedCampaigns=rows" @header-dragend="resizeColumn" :data="data.rows" v-loading="loading" row-key="key" class="ads-table" :key="widthVersion+currentLevel+visibleColumns.map(c=>c.key).join(',')">
  <el-table-column v-if="['campaign','ad','target','negative','search'].includes(currentLevel)" type="selection" column-key="selection" :reserve-selection="true" :width="48" fixed :selectable="(row:any)=>canEditAds&&!loading&&!rowSaving(row)&&row.state!=='ARCHIVED'"/>
  <el-table-column v-if="!['search','placement'].includes(currentLevel)" column-key="state" label="开关" :width="width('state',100)" fixed><template #header><span>开关</span><AdsColumnHelp label="开关" :description="columnDescription('state')"/></template><template #default="{row}"><div class="state-control"><el-tooltip :content="states[row.state]||row.state"><el-switch :model-value="row.state==='ENABLED'" :disabled="!canEditAds||row.state==='ARCHIVED'||rowSaving(row)" :aria-label="'开启或暂停 '+row.name" @change="(v:any)=>quickEdit(row,'state',v?'ENABLED':'PAUSED')"/></el-tooltip><span v-if="pendingRow(row)?.field==='state'||bulkPending(row)?.field==='state'" class="inline-submit-status" role="status">{{pendingLabel(row)}}</span><AdsRecentChange :change="row.recent_changes?.state" field="state"/></div></template></el-table-column>
  <el-table-column v-if="currentLevel!=='ad'" column-key="name" :width="width('name',250)" fixed><template #header><button class="sort-head" @click="cycleSort('name')">{{currentLevel==='search'?'用户搜索词':currentLevel==='negative'?'否定关键词 / 商品':currentLevel==='target'?'投放项':currentLevel==='placement'?'广告位':labels[currentLevel]+'名称'}}{{sortMark('name')}}</button><AdsColumnHelp :label="labels[currentLevel]||'名称'" :description="columnDescription('name',currentLevel)"/></template><template #default="{row}"><div class="name-cell"><el-button v-if="['campaign','adgroup'].includes(currentLevel)" link type="primary" @click="drill(row)">{{row.name}}</el-button><a v-else-if="(['search','negative'].includes(currentLevel)||(currentLevel==='target'&&row.kind==='keyword'))&&searchTermUrl(row)" class="search-term-link" :href="searchTermUrl(row)" target="_blank" rel="noopener noreferrer" title="在亚马逊打开" @click.stop>{{row.name}}</a><strong v-else>{{row.name}}</strong><CopyValue v-if="['campaign','adgroup','search'].includes(currentLevel)||(currentLevel==='target'&&row.kind==='keyword')" :label="currentLevel==='campaign'?'广告活动名称':currentLevel==='adgroup'?'广告组名称':currentLevel==='search'?'用户搜索词':'关键词'" :value="row.name"/><el-button v-if="canEditAds && currentLevel==='campaign'" link :icon="EditPen" :aria-label="'编辑活动 '+row.name" :disabled="!canEditAds||row.state==='ARCHIVED'||rowSaving(row)" @click.stop="startCampaign(row)"/></div><div v-if="currentLevel==='search'&&row.existing_matches?.length" class="existing-matches"><el-tag v-for="m in row.existing_matches" :key="m" size="small">{{({EXACT:'已投精准',PHRASE:'已投词组',BROAD:'已投广泛'} as Record<string,string>)[m]}}</el-tag></div></template></el-table-column>
  <el-table-column v-for="col in visibleColumns" :key="col.key" :column-key="col.key" :width="width(col.key,col.width)" :align="['money','number','percent','ratio','budget','default_bid','bid','adjustment','counts','suggestion'].includes(col.type)?'center':'left'"><template #header><button v-if="!['image','suggestion','counts','identifiers'].includes(col.type)" class="sort-head" :aria-label="col.label+'排序'" @click="cycleSort(col.key)">{{col.label}}{{sortMark(col.key)}}</button><span v-else>{{col.label}}</span><AdsColumnHelp :label="col.label" :description="columnHelp(col)"/><div v-if="col.type==='suggestion'" class="suggest-header"><span>平日</span><el-button link :icon="Refresh" aria-label="刷新建议竞价" :loading="suggestLoading" @click.stop="refreshSuggestions"/></div></template><template #default="{row}">
   <div v-if="['budget','default_bid','bid'].includes(col.type)" class="inline-price inline-price--aligned" @keydown.capture="inlineEnter(row,col.key,$event)"><el-input-number :key="String(rowSaving(row))" :model-value="inlineValue(row,col.key)" @update:model-value="(v:any)=>changeInline(row,col.key,v)" :min="0.01" :precision="2" :step="0.01" controls-position="right" :disabled="!canEditAds||row.state==='ARCHIVED'||rowSaving(row)" :placeholder="col.type==='bid'&&row.bid==null?'未同步':undefined" :aria-label="col.label+' '+row.name"/><span v-if="rowSaving(row)" class="inline-submit-status" role="status">{{pendingLabel(row)}}</span><span class="inline-price-actions"><AdsRecentChange :change="row.recent_changes?.[col.key==='budget'?'dailyBudget':col.key==='default_bid'?'defaultBid':col.key]" :currency="row.currency"/><el-button v-if="canEditAds && dirtyInline(row,col.key)" link type="success" :icon="Check" :disabled="rowSaving(row)" :aria-label="'保存'+col.label+' '+row.name" title="保存（Enter）" @click="saveInline(row,col.key)"/></span></div>
   <div v-else-if="col.type==='suggestion'" class="bid-suggestion"><template v-if="row.suggestion"><el-tooltip :content="'更新于 '+time(row.suggestion.fetched_at)"><span>{{Number(row.suggestion.mid).toFixed(2)}} <span class="muted">({{Number(row.suggestion.low).toFixed(2)}}–{{Number(row.suggestion.high).toFixed(2)}})</span></span></el-tooltip><el-dropdown trigger="click" @command="(key:string)=>applySuggestion(row,key)" :disabled="!canEditAds||row.state==='ARCHIVED'||rowSaving(row)"><el-button size="small" :disabled="!canEditAds||row.state==='ARCHIVED'||rowSaving(row)">应用⌄</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item v-for="(label,key) in {mid:'建议',low:'最低',high:'最高'}" :key="key" :command="key">{{label}}：{{money(row.suggestion[key],row.currency)}}</el-dropdown-item></el-dropdown-menu></template></el-dropdown></template><span v-else class="muted">—</span></div>
   <div v-else-if="col.type==='adjustment'" class="inline-price" @keydown.capture="inlineEnter(row,'adjustment',$event)"><el-input-number :key="String(rowSaving(row))" :model-value="inlineValue(row,'adjustment')" @update:model-value="(v:any)=>changeInline(row,'adjustment',v)" :min="0" :max="900" :precision="0" controls-position="right" :disabled="!canEditAds||row.state==='ARCHIVED'||rowSaving(row)"/><span>%</span><span v-if="rowSaving(row)" class="inline-submit-status" role="status">{{pendingLabel(row)}}</span><el-button :disabled="rowSaving(row)" v-if="dirtyInline(row,'adjustment')" link :icon="Check" @click="quickEdit(row,row.placement_field,inlineValue(row,'adjustment'))"/></div>
   <div v-else-if="col.key==='campaign_name'&&row.campaign_id" class="copy-line"><el-button link type="primary" @click="openEntity('campaign',{id:row.campaign_id,name:row.campaign_name,profile_id:row.profile_id})">{{row.campaign_name}}</el-button><CopyValue label="广告活动名称" :value="row.campaign_name"/></div>
   <div v-else-if="col.key==='adgroup_name'&&row.adgroup_id" class="copy-line"><el-button link type="primary" @click="openEntity('adgroup',{id:row.adgroup_id,name:row.adgroup_name,profile_id:row.profile_id},{id:row.campaign_id,name:row.campaign_name})">{{row.adgroup_name}}</el-button><CopyValue label="广告组名称" :value="row.adgroup_name"/></div>
   <el-tooltip v-else-if="col.key==='stock_total'" :content="'FBA可售 '+number(row.available)+' + 接收中 '+number(row.receiving)+' + 入库中 '+number(row.inbound)+'；更新 '+time(row.inventory_synced_at)"><span>{{number(row.stock_total)}}</span></el-tooltip>
   <span v-else-if="col.type==='counts'">{{row.target_count}} / {{row.product_count}}</span>
   <div class="ad-identifiers" v-else-if="col.type==='identifiers'"><div class="copy-line"><strong>{{row.asin||'—'}}</strong><CopyValue label="ASIN" :value="row.asin"/></div><div class="cell-sub copy-line"><span>{{row.sku||'—'}}</span><CopyValue label="SKU" :value="row.sku"/></div><div class="product-name copy-line"><span>{{row.product_name||'未关联标品'}}</span><CopyValue label="品名" :value="row.product_name"/></div></div>
   <AdsProductImage v-else-if="col.type==='negative_image'&&row.asin" :profile-id="row.profile_id" :asin="row.asin"/>
   <el-image v-else-if="col.type==='image'&&row.image" :src="row.image" fit="contain" style="width:48px;height:48px"/>
   <span v-else-if="col.key.toLowerCase().includes('servingstatus')" :title="col.key==='servingStatus'&&row.serving_status_inherited?(row.serving_status_source==='campaign'?'受所属广告活动限制':'受所属广告组限制')+' · 状态更新时间：'+time(row.serving_status_at):undefined" class="serving-status"><template v-for="(status,i) in statusItems(row,col)" :key="i"><span v-if="i" class="status-separator">；</span><span :class="'serving-status--'+status.tone">{{status.label}}</span></template></span>
   <span v-else :title="cellText(row,col)">{{cellText(row,col)}}</span>
  </template></el-table-column>
  <el-table-column v-if="can('advertising.logs')&&currentLevel!=='search'" column-key="actions" label="操作" :width="width('actions',115)" fixed="right"><template #default="{row}"><el-button link type="primary" @click="logRow={...row,profile_id:row.profile_id||profile}">查看日志</el-button></template></el-table-column>
  <el-table-column v-if="canEditAds && currentLevel==='search'" column-key="actions" label="操作" :width="width('actions',170)" fixed="right"><template #header><span>操作</span><AdsColumnHelp label="操作" :description="columnDescription('actions')"/></template><template #default="{row}"><el-button link type="primary" @click="openSearchAction('keyword',row)">添加关键词</el-button><el-button link type="primary" @click="openSearchAction('negative',row)">添加否定</el-button></template></el-table-column>
  <template #empty><el-empty :description="currentLevel==='search'&&!data.metrics_ready?'尚未同步该时段的搜索词报表':'没有匹配的记录'"/></template>
 </el-table>
 <div class="ads-results-footer">
 <div class="table-footer"><AppPagination v-model:current-page="page" :total="data.total||0" @change="load" v-model:page-size="pageSize"/></div></div></section>
 <el-dialog v-model="bulkOpen" :title="bulkMode==='state'?'批量调整状态':bulkMode==='bid'?'批量调整竞价':'批量调整每日预算'" width="850px" :close-on-click-modal="false" :close-on-press-escape="!bulkBusy" :show-close="!bulkBusy">
 <p>已选择 {{bulkRows.length}} 个{{currentLevel==='negative'?'否定投放项':currentLevel==='target'?'投放项':currentLevel==='ad'?'广告':'广告活动'}}</p><el-alert v-if="bulkError" :title="bulkError" type="error" :closable="false"/>
 <el-form v-if="bulkStage==='form'" label-position="top"><el-form-item v-if="bulkMode==='state'" label="统一调整为"><el-radio-group v-model="bulkState"><el-radio value="ENABLED">开启</el-radio><el-radio value="PAUSED">暂停</el-radio><el-radio value="ARCHIVED">归档</el-radio></el-radio-group><p v-if="bulkState==='ARCHIVED'" class="serving-status--danger">归档后无法撤回，也不能重新开启。</p></el-form-item><template v-else><el-form-item label="调整方式"><el-radio-group v-model="bulkAdjustment"><el-radio value="fixed">固定金额</el-radio><el-radio value="increase">提高百分比</el-radio><el-radio value="decrease">降低百分比</el-radio><template v-if="bulkMode==='bid'"><el-radio v-for="(label,tier) in suggestionTierNames" :key="tier" :value="tier">建议价{{label}}</el-radio></template></el-radio-group></el-form-item><template v-if="bulkAdjustment==='fixed'"><el-form-item v-for="code in bulkCurrencies" :key="code" :label="(bulkMode==='bid'?'竞价':'每日预算')+'（'+code+'）'"><el-input-number v-model="bulkBudgets[code]" :min="0.01" :max="bulkMode==='bid'?10000:1000000000" :precision="2" :step="0.01" :aria-label="(bulkMode==='bid'?'批量竞价 ':'批量预算 ')+code"/></el-form-item></template><el-form-item v-else-if="!bulkSuggestionTier" label="调整幅度"><el-input-number v-model="bulkPercent" :min="0.01" :max="bulkAdjustment==='decrease'?99.99:10000" :precision="2" :step="1" aria-label="批量调整百分比"/><span> %</span></el-form-item><template v-if="bulkSuggestionTier"><el-form-item class="bulk-suggestion-adjustment" label="在所选建议价基础上调整"><el-radio-group v-model="bulkSuggestionDirection" aria-label="建议价调整方向"><el-radio value="increase">提高</el-radio><el-radio value="decrease">降低</el-radio></el-radio-group><el-input-number v-model="bulkSuggestionPercent" :min="0" :max="bulkSuggestionDirection==='decrease'?99.99:10000" :precision="2" :step="1" aria-label="建议价调整百分比"/><span> %</span></el-form-item><p class="muted">以每项各自的{{suggestionTierNames[bulkAdjustment]}}建议价为基准{{bulkSuggestionDirection==='decrease'?'降低':'提高'}} {{bulkSuggestionPercent??'—'}}%，四舍五入保留两位小数；0% 表示直接应用建议价。没有有效建议价的项目会跳过并保留原竞价。</p><el-table :data="bulkRows" border max-height="300"><el-table-column prop="name" label="投放项" min-width="200"/><el-table-column v-for="(label,tier) in suggestionTierNames" :key="tier" :label="'建议价'+label" width="140" align="center"><template #default="{row}">{{bulkSuggestionValue(row,tier)==null?'—':money(bulkSuggestionValue(row,tier),row.currency)}}</template></el-table-column><el-table-column label="调整后竞价" width="140" align="center"><template #default="{row}">{{bulkSuggestedBid(row)==null?'—':money(bulkSuggestedBid(row),row.currency)}}</template></el-table-column></el-table></template><p v-else class="muted">{{bulkAdjustment==='fixed'?'按各自币种统一设置金额。':'按每项从亚马逊读取的当前金额分别计算，四舍五入保留两位小数。'}}<template v-if="bulkMode==='bid'">未单独设置竞价的投放项按广告组默认竞价计算，提交后设为独立竞价。</template></p></template></el-form>
 <template v-else><p v-if="bulkStage==='preview'">{{bulkBusy?'正在读取最新设置…':`可提交 ${bulkReady.length} 项；无须修改、缺少建议价或预览失败的项目不会提交。`}}</p><el-table :data="bulkRows" border max-height="430"><el-table-column prop="name" :label="currentLevel==='negative'?'否定投放项':currentLevel==='target'?'投放项':currentLevel==='ad'?'广告':'广告活动'" min-width="190"/><el-table-column prop="country" label="国家" width="75"/><el-table-column label="修改前" width="110" :align="bulkMode==='state'?'left':'center'"><template #default="{row}">{{bulkValue(row,row.before)}}</template></el-table-column><el-table-column label="修改后" width="110" :align="bulkMode==='state'?'left':'center'"><template #default="{row}">{{bulkValue(row,row.after)}}</template></el-table-column><el-table-column label="处理结果" min-width="190"><template #default="{row}"><span :class="row.status==='succeeded'?'serving-status--success':['failed','unknown','preview_failed'].includes(row.status)?'serving-status--danger':''">{{bulkStatusNames[row.status]||row.status}}</span><div class="cell-sub">{{row.message}}</div></template></el-table-column></el-table></template>
 <template #footer><el-button :disabled="bulkBusy" @click="bulkOpen=false">{{bulkStage==='done'?'关闭':'取消'}}</el-button><el-button v-if="bulkStage==='preview'" :disabled="bulkBusy" @click="bulkStage='form'">返回编辑</el-button><el-button v-if="bulkStage==='form'" type="primary" :loading="bulkBusy" @click="bulkMode==='bid'?submitBulkBids():previewBulk()">{{bulkMode==='bid'?'提交后台修改':'预览修改'}}</el-button><el-button v-else-if="bulkStage==='preview'" type="primary" :loading="bulkBusy" :disabled="bulkBusy||!bulkReady.length" @click="confirmBulk">确认提交 {{bulkReady.length}} 项</el-button></template>
 </el-dialog>
 <el-dialog v-model="editOpen" :title="isCreate?'新增否定投放':'编辑广告设置'" :width="editKind==='campaign'?'760px':'620px'" top="6vh" :close-on-click-modal="false" :close-on-press-escape="!editBusy" :show-close="!editBusy"><p><b>{{editRow?.name}}</b> · {{editRow?.store||store}} / {{editRow?.country||country}}</p><el-alert v-if="editError" :title="editError" type="error" :closable="false" show-icon/>
 <template v-if="!previewData"><el-form label-position="top" class="edit-form"><template v-if="isCreate"><el-form-item label="否定类型"><el-radio-group v-model="editKind"><el-radio-button value="negative_keyword">否定关键词</el-radio-button><el-radio-button value="negative_target">否定商品</el-radio-button></el-radio-group></el-form-item><template v-if="editKind==='negative_keyword'"><el-form-item label="否定关键词"><el-input v-model="editForm.keywordText" maxlength="250"/></el-form-item><el-form-item label="否定方式"><el-select v-model="editForm.matchType"><el-option label="否定精准" value="NEGATIVE_EXACT"/><el-option label="否定词组" value="NEGATIVE_PHRASE"/></el-select></el-form-item></template><el-form-item v-else label="商品ASIN"><el-input v-model="editForm.asin" maxlength="10"/><AdsProductImage v-if="/^[A-Z0-9]{10}$/i.test(editForm.asin||'')" :profile-id="editRow?.profile_id||profile" :asin="editForm.asin"/></el-form-item></template><template v-else-if="editKind==='campaign'"><template v-if="campaignSettings">
 <el-alert v-for="w in campaignSettings.warnings" :key="w" :title="w" type="warning" :closable="false"/>
 <el-form-item label="活动名称"><el-input v-model="editForm.name" maxlength="250"/></el-form-item>
 <el-form-item label="状态"><el-radio-group v-model="editForm.state"><el-radio value="ENABLED">开启</el-radio><el-radio value="PAUSED">暂停</el-radio></el-radio-group></el-form-item>
 <el-form-item label="广告组合"><el-select v-model="editForm.portfolioId" filterable clearable @clear="editForm.portfolioId=null" placeholder="未设置广告组合"><el-option v-for="v in campaignSettings.portfolios" :key="v.portfolioId" :value="String(v.portfolioId)" :label="v.name"/></el-select></el-form-item>
 <el-form-item :label="'每日预算（'+editCurrency+'）'"><el-input-number v-model="editForm.dailyBudget" :min="0.01" :precision="2"/></el-form-item>
 <div class="campaign-dates"><el-form-item label="开始日期"><el-input :model-value="campaignSettings.startDate" disabled/></el-form-item><el-form-item label="结束日期"><el-date-picker v-model="editForm.endDate" type="date" value-format="YYYY-MM-DD" placeholder="无结束日期" clearable/></el-form-item></div>
 <el-form-item label="投放类型">{{campaignSettings.targetingType==='AUTO'?'自动投放':'手动投放'}}</el-form-item>
 <el-form-item label="广告活动竞价策略"><el-radio-group v-model="editForm.biddingStrategy" class="strategy-options"><el-radio v-for="(v,k) in strategyNames" :key="k" :value="k" :disabled="k==='RULE_BASED'&&campaignBaseline.biddingStrategy!=='RULE_BASED'">{{v}}</el-radio></el-radio-group><small class="muted">基于规则的竞价需先在亚马逊配置规则；已有设置会保留。</small></el-form-item>
 <el-form-item v-for="p in placementFields" :key="p[0]" :label="p[1]+'加价（%）'"><el-input-number v-model="editForm[p[0]]" :min="0" :max="900" :precision="0" :disabled="campaignSettings.restricted&&p[0]==='placementBusiness'"/></el-form-item>
 <el-form-item label="受众加价"><el-select :model-value="editForm.audiences?.[0]?.audienceSegments?.[0]?.audienceId||''" clearable filterable :disabled="campaignSettings.restricted" placeholder="不对受众提价" @change="audienceChange"><el-option v-for="a in campaignSettings.audiences" :key="a.audienceId" :value="a.audienceId" :label="a.audienceResolved||a.audienceId"/><el-option v-if="editForm.audiences?.[0]&&!campaignSettings.audiences.some((a:any)=>a.audienceId===editForm.audiences[0].audienceSegments[0].audienceId)" :value="editForm.audiences[0].audienceSegments[0].audienceId" label="当前受众（保留已有设置）"/></el-select><el-input-number v-if="editForm.audiences?.[0]" v-model="editForm.audiences[0].percentage" :min="0" :max="900" :precision="0" :disabled="campaignSettings.restricted"/><span v-if="editForm.audiences?.[0]">%</span></el-form-item>
 <el-form-item label="站外投放设置"><el-radio-group v-model="editForm.offAmazonStrategy" :disabled="campaignSettings.restricted"><el-radio value="MAXIMIZE_REACH">扩大受众触达</el-radio><el-radio value="MINIMIZE_SPEND">限制站外花费</el-radio></el-radio-group></el-form-item>
 <el-form-item label="备注（保存在本地）"><el-input v-model="editForm.note" type="textarea" :rows="3" maxlength="500" show-word-limit/></el-form-item>
 </template><template v-else-if="quickField"><el-form-item v-if="quickField==='dailyBudget'" :label="'每日预算（'+editCurrency+'）'"><el-input-number v-model="editForm.dailyBudget" :min="0.01" :precision="2"/></el-form-item><el-form-item v-else-if="quickField.startsWith('placement')" label="竞价调整（%）"><el-input-number v-model="editForm[quickField]" :min="0" :max="900" :precision="0"/></el-form-item><el-form-item v-else label="状态"><el-radio-group v-model="editForm.state"><el-radio value="ENABLED">开启</el-radio><el-radio value="PAUSED">暂停</el-radio></el-radio-group></el-form-item></template><p v-else>{{editBusy?'正在读取最新活动设置…':'可返回列表重新打开活动设置。'}}</p></template><template v-else><el-form-item v-if="editKind==='adgroup'" label="广告组名称"><el-input v-model="editForm.name" maxlength="250"/></el-form-item><el-form-item label="状态"><el-radio-group v-model="editForm.state"><el-radio value="ENABLED">开启</el-radio><el-radio value="PAUSED">暂停</el-radio></el-radio-group></el-form-item><el-form-item v-if="['keyword','target'].includes(editKind)" :label="'竞价（'+editCurrency+'）'"><el-input-number v-model="editForm.bid" :min="0.01" :max="10000" :precision="2" :step="0.01"/></el-form-item><el-form-item v-if="editKind==='adgroup'" :label="'默认竞价（'+editCurrency+'）'"><el-input-number v-model="editForm.defaultBid" :min="0.01" :max="10000" :precision="2" :step="0.01"/></el-form-item></template></el-form></template>
 <template v-else><p class="preview-notice">请确认以下修改（备注仅保存在本地，其余提交亚马逊）：</p><template v-if="true"><TableReset table-key="AdvertisingPanel/table-3"/><el-table border data-width-key="AdvertisingPanel/table-3" @header-dragend="(width: number, _old: number, column: any) => tableWidths.resize('table-3', width, column)" @header-contextmenu="(column: any, event: MouseEvent) => tableWidths.resetColumn('table-3', column, event)" :data="Object.entries(previewData.changes).map(([key,value])=>({key,before:previewData.before[key],after:value}))"><el-table-column :column-key="'column-1'" :width="tableWidths.width('table-3', 'column-1', undefined)" label="属性"><template #default="{row}">{{fieldNames[row.key]||row.key}}</template></el-table-column><el-table-column :column-key="'column-2'" :width="tableWidths.width('table-3', 'column-2', undefined)" label="修改前"><template #default="{row}">{{fieldValue(row.key,row.before)}}</template></el-table-column><el-table-column :column-key="'column-3'" :width="tableWidths.width('table-3', 'column-3', undefined)" label="修改后"><template #default="{row}">{{fieldValue(row.key,row.after)}}</template></el-table-column></el-table></template></template>
 <template #footer><el-button :disabled="editBusy" @click="previewData?previewData=null:editOpen=false">{{previewData?'返回编辑':'取消'}}</el-button><el-button type="primary" :loading="editBusy" @click="previewData?confirm():preview()">{{previewData?'确认并提交亚马逊':'提交预览'}}</el-button></template></el-dialog>
 <el-dialog v-model="columnsOpen" :title="labels[currentLevel]+' · 自定义列'" width="850px"><p class="muted">开关固定在最前面，广告页展示投放 ASIN 与 SKU。勾选显示列，右侧拖动排序或点击 × 移除；设置随账号保存。未获取的数据显示 —。</p><el-input v-model="columnSearch" placeholder="搜索列名称" clearable/><div class="column-picker"><el-checkbox-group v-model="columnDraft"><el-checkbox v-for="c in filteredColumns" :key="c.key" :value="c.key">{{c.label}}</el-checkbox></el-checkbox-group><div class="selected-columns"><b>已选 {{columnDraft.length}} 列</b><div v-for="(key,i) in columnDraft" :key="key" draggable="true" :data-column="key" :class="{'drag-over':dragOverColumn===key}" @dragstart="dragColumn($event,key)" @dragover.prevent="dragOverColumn=key" @drop.prevent="dropColumn($event,key)" @dragend="draggedColumn='';dragOverColumn=''"><span class="drag-handle" title="拖动调整顺序">⠿</span><span>{{columnCatalog.find(c=>c.key===key)?.label||key}}</span><el-button link :disabled="i===0" @click="moveColumn(i,-1)" :aria-label="'上移 '+key">↑</el-button><el-button link :disabled="i===columnDraft.length-1" @click="moveColumn(i,1)" :aria-label="'下移 '+key">↓</el-button><el-button link class="remove-column" :aria-label="'移除 '+(columnCatalog.find(c=>c.key===key)?.label||key)" @click="columnDraft=columnDraft.filter(k=>k!==key)">×</el-button></div></div></div><template #footer><el-button @click="columnDraft=columnCatalog.filter(c=>c.def).map(c=>c.key)">恢复默认列</el-button><el-button @click="columnsOpen=false">取消</el-button><el-button type="primary" :loading="columnsBusy" @click="saveColumns">保存列设置</el-button></template></el-dialog>
 <el-dialog v-model="showJobs" title="广告同步记录" width="920px"><template v-if="true"><TableReset table-key="AdvertisingPanel/table-4"/><el-table border data-width-key="AdvertisingPanel/table-4" @header-dragend="(width: number, _old: number, column: any) => tableWidths.resize('table-4', width, column)" @header-contextmenu="(column: any, event: MouseEvent) => tableWidths.resetColumn('table-4', column, event)" :data="jobs"><el-table-column :column-key="'column-1'" label="范围" :width="tableWidths.width('table-4', 'column-1', '220')"><template #default="{row}">{{row.start_date}} 至 {{row.end_date}}</template></el-table-column><el-table-column :column-key="'column-2'" label="状态" :width="tableWidths.width('table-4', 'column-2', '170')"><template #default="{row}">{{jobStates[row.status]||row.status}}</template></el-table-column><el-table-column :column-key="'column-3'" :width="tableWidths.width('table-4', 'column-3', undefined)" label="报表" min-width="230"><template #default="{row}">{{Object.entries(row.reports).map(([k,v]:any)=>(labels[k.split(':')[0]]||k)+': '+v.status).join('；')}}</template></el-table-column><el-table-column :column-key="'error'" :width="tableWidths.width('table-4', 'error', undefined)" prop="error" label="说明" min-width="250"/></el-table></template><template #footer><el-button @click="showJobs=false">关闭</el-button></template></el-dialog>

</div>
</template>
<style scoped>
.bulk-suggestion-adjustment :deep(.el-form-item__content){gap:12px}

.inline-submit-status{flex-basis:100%;font-size:12px;color:#598578;white-space:nowrap;text-align:center}.copy-line{display:flex;align-items:center;gap:3px;min-width:0}.copy-line>span,.copy-line>strong{min-width:0;overflow-wrap:anywhere}

.report-freshness{margin:0;padding:8px 16px;font-size:13px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.advertising-panel{display:flex;flex-direction:column;min-height:calc(100dvh - 46px)}
.advertising-panel>.results{overflow:visible;display:flex;flex-direction:column;flex:1;margin-bottom:0}
.advertising-panel>.results>.el-table{flex:0 0 auto}
.advertising-panel>.results>.ads-results-footer{margin-top:auto;flex-shrink:0;background:white}.ads-results-footer .table-footer{padding:12px;justify-content:flex-end}
.advertising-panel>.results>.ads-filters,.advertising-panel>.performance{flex-shrink:0}

.existing-matches{display:flex;gap:4px;flex-wrap:wrap;margin-top:6px}
.ads-table :deep(th .cell){border-right:2px solid #d5e1d9}.ads-table :deep(.el-table__cell){border-right-color:#edf2ef}.resize-hint{font-size:11px;align-self:center}.suggest-header{display:flex;gap:10px;align-items:center;font-size:12px}.bid-suggestion{display:flex;align-items:center;gap:8px;white-space:nowrap}.bid-suggestion :deep(.el-button){white-space:nowrap}.result-actions{flex-wrap:wrap}

.state-control{display:flex;align-items:center;gap:3px;flex-wrap:nowrap}.state-control :deep(.el-switch){flex-shrink:0}
.sort-head{border:0;background:none;color:inherit;font:inherit;cursor:pointer;text-align:inherit;padding:0;line-height:1.5}.sort-head:hover{color:#19765e}.name-cell{display:flex;gap:8px;align-items:center}.search-term-link{color:#19765e;font-weight:600;text-decoration:none;min-width:0;overflow-wrap:anywhere}.search-term-link:hover{text-decoration:underline}.search-term-link:focus-visible{outline:2px solid #19765e;outline-offset:2px}.inline-price{display:flex;flex-wrap:wrap;align-items:center;gap:5px}.inline-price .el-input-number{width:112px}.inline-price.inline-price--aligned{display:grid;grid-template-columns:minmax(0,1fr) 112px minmax(0,1fr);grid-template-rows:32px 18px;column-gap:5px;row-gap:5px}.inline-price--aligned>.el-input-number{grid-column:2;grid-row:1}.inline-price--aligned>.inline-price-actions{grid-column:3;grid-row:1;display:flex;flex-direction:column;align-items:center;gap:2px;justify-self:start;white-space:nowrap}.inline-price--aligned>.inline-submit-status{grid-column:1/-1;grid-row:2}.inline-price-actions :deep(.el-button){margin:0}.column-picker{display:grid;grid-template-columns:1.2fr 1fr;gap:25px;height:420px;margin-top:18px}.column-picker .el-checkbox-group{overflow:auto;display:flex;flex-direction:column}.column-picker :deep(.el-checkbox){flex-shrink:0;height:32px;margin-right:0}.selected-columns{overflow:auto;border-left:1px solid #e5ede8;padding-left:18px}.selected-columns>div{display:flex;align-items:center;gap:5px;padding:5px 0}.selected-columns span{flex:1}.campaign-dates{display:flex;gap:20px}.strategy-options{display:flex;flex-direction:column;align-items:flex-start}.edit-form small{display:block;width:100%}

.ads-toolbar{display:flex;justify-content:space-between;gap:16px;margin-bottom:18px;flex-wrap:wrap}.ads-filters{display:flex;gap:10px;padding:20px 22px;flex-wrap:wrap}.ads-filters>.el-select{width:145px}.ads-filters>.el-input{flex:1;min-width:300px}.ads-filters :deep(.el-date-editor){max-width:300px;min-width:250px}.ads-breadcrumb{display:flex;align-items:center;gap:12px;padding:18px 22px;flex-wrap:wrap}.ads-notes{padding:12px 22px;font-size:12px;line-height:1.8;color:#81948b;border-top:1px solid #eef2ef}.ads-notes b{color:#b57e35}.ads-table :deep(.el-button){white-space:normal;text-align:left;height:auto;line-height:1.5}.ads-table :deep(.el-table__cell){padding:12px 0}.ads-totals{display:flex;gap:22px;flex-wrap:wrap;padding:20px 24px;background:#f3f8f5;font-size:13px}.ads-footer{font-size:12px;margin-top:18px;line-height:1.8}.ads-progress{padding:16px;background:#eef5f9;color:#47718b;border-radius:8px;margin-bottom:16px}.context,.performance,.results{margin-top:18px;margin-bottom:18px}.group-summary{display:flex;gap:24px;align-items:center;padding:0 22px 18px;color:#73847b}.detail-tabs{padding:0 22px}.detail-tabs :deep(.el-tabs__header){margin-bottom:0}.kpis{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;padding:22px}.kpis>div{border:1px solid #e5eee9;border-radius:8px;padding:14px 18px;display:flex;flex-direction:column;gap:10px}.kpis>div:first-child{background:#eff5ff;border-left:3px solid #438df1}.kpis>div:nth-child(2){background:#f2f9ef;border-left:3px solid #75bc5a}.kpis span{color:#84948c;font-size:12px}.kpis b{font-size:23px}.chart{position:relative;padding:10px 22px}.chart svg{width:100%;display:block;max-height:260px}.chart text{font-size:10px;fill:#94a39c}.chart-tooltip{position:absolute;right:80px;top:25px;display:flex;flex-direction:column;gap:6px;background:#fff;border:1px solid #e6eee9;box-shadow:0 6px 24px #183d2919;padding:14px 20px;border-radius:8px;font-size:12px;pointer-events:none}.chart-empty{padding:35px;color:#98a69f;text-align:center;font-size:13px}.result-actions{display:flex;gap:10px}.result-actions>.el-select{width:115px}.edit-form{margin-top:20px;max-height:65vh;overflow:auto;padding-right:12px}.preview-notice{color:#277864;margin-top:24px}@media(max-width:1100px){.kpis{grid-template-columns:repeat(3,1fr)}.group-summary{flex-wrap:wrap}}

.ads-sections{padding:10px 22px;margin:0;max-width:100%;overflow-x:auto;background:#fff}.ads-sections :deep(.el-radio-group){flex-wrap:nowrap}.ads-sections :deep(.el-radio-button){flex-shrink:0}.ads-sections :deep(.el-radio-button__inner){padding:9px 18px;font-weight:600;border-color:var(--el-border-color);box-shadow:none}.ads-sections :deep(.el-radio-button.is-active .el-radio-button__inner){border-color:var(--el-color-primary)}.ads-toolbar{margin-bottom:8px;justify-content:flex-end}.ads-breadcrumb{padding:10px 6px 16px}.breadcrumb-campaign{font-weight:700;color:#243b33}.performance,.results{margin:0 0 8px;border-radius:0}.kpis{padding:12px;gap:10px}.ads-filters{padding:12px;gap:8px}.ads-filters>.el-input{min-width:220px}.ads-notes{padding:8px 12px}.ads-footer{margin:8px 12px}.chart-empty{height:80px;padding:0}.selected-columns>div{cursor:grab;border-radius:4px;padding:5px}.selected-columns>div.drag-over{background:#e6f3ed;outline:1px dashed #20765f}.selected-columns .drag-handle{flex:0;color:#9caea5;margin-right:4px}.remove-column{font-size:20px}.selected-columns>div:active{cursor:grabbing}

.ads-filters{flex-wrap:nowrap;overflow-x:auto;align-items:center}.ads-filters>.el-select{width:110px;min-width:110px;flex:0 0 110px}.ads-filters>.el-input{width:200px;min-width:160px;flex:0 1 200px}.ads-filters :deep(.el-date-editor){width:250px;min-width:250px;flex:0 0 250px}.ads-filters>.el-button{flex-shrink:0;margin-left:0}

.product-name{margin-top:5px;line-height:1.45}.ad-identifiers{display:flex;flex-direction:column;gap:4px;padding:2px 0}.ad-identifiers>.copy-line{margin:0;min-height:24px;line-height:24px}.serving-status{font-weight:500}.serving-status--success{color:#20745f}.serving-status--danger{color:#c03639}.serving-status--warning{color:#a86412}.serving-status--muted,.status-separator{color:#727a82}

.chart-tooltip i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px}.selection-count{font-size:12px;align-self:center;white-space:nowrap;flex-shrink:0;color:#72877c}.ads-breadcrumb{background:#fff;font-size:var(--el-font-size-base)}.ads-sections :deep(.el-radio-button:focus-visible){outline:2px solid var(--el-color-primary);outline-offset:2px}
@media(max-width:800px){.ads-sections{padding-left:18px;padding-right:18px}}
.results>.ads-table{overflow:visible}.results>.ads-table :deep(.el-table__inner-wrapper){overflow:visible}.results>.ads-table :deep(.el-table__header-wrapper){position:sticky;top:var(--ads-sticky-top,103px);z-index:901;background:#f7faf8;box-shadow:0 2px 4px #183d2910}
</style>
<style scoped>

.budget-status{white-space:nowrap;flex-shrink:0}.results>.ads-filters{position:sticky;top:46px;z-index:902;background:white;border-bottom:1px solid #e7eeea}
.results>.ads-table{overflow:visible}.results>.ads-table :deep(.el-table__inner-wrapper){overflow:visible}.results>.ads-table :deep(.el-table__header-wrapper){position:sticky;top:var(--ads-sticky-top,103px);z-index:901;background:#f7faf8;box-shadow:0 2px 4px #183d2910}
</style>
