<script setup lang="ts">
import {asyncPage} from './asyncPage'
import {useTableWidths} from './tableWidths'
const tableWidths=useTableWidths('Workspace')

import {checkPriceJobs} from './priceJobs'
import {displayCurrency} from './currency'
const LogisticsPanel=asyncPage(()=>import('./LogisticsPanel.vue'))
const SettingsPanel=asyncPage(()=>import('./SettingsPanel.vue'))
import DashboardPanel from './DashboardPanel.vue'
import AdsSearchTaskStatus from './AdsSearchTaskStatus.vue'
import TodoReminder from './TodoReminder.vue'
const TodoPanel=asyncPage(()=>import('./TodoPanel.vue'))
async function openTodos(){await navigate('todos')}
import OrderRows from './OrderRows.vue'
import OrderIncomeDialog from './OrderIncomeDialog.vue'
const calculatorOrder=ref<any>(null)
function newAdsTabKey(prefix:string){return prefix+Array.from(crypto.getRandomValues(new Uint32Array(4)),n=>n.toString(16).padStart(8,'0')).join('')}
function openListingAds(row:any){if(!canAccessAds.value)return;const key=newAdsTabKey('ads-listing-');insertTab({key,title:'广告 · '+row.sku,adsFocus:{store:row.store,marketplace:row.marketplace,asin:row.asin,request:Date.now()}});tab.value=key}
const UserPanel=asyncPage(()=>import('./UserPanel.vue'))
const FinancePanel=asyncPage(()=>import('./FinancePanel.vue'))
const ProductPanel=asyncPage(()=>import('./ProductPanel.vue'))
const ProductEditor=asyncPage(()=>import('./ProductEditor.vue'))
const InvoiceEditor=asyncPage(()=>import('./InvoiceEditor.vue'))
const CustomsProfileEditor=asyncPage(()=>import('./CustomsProfileEditor.vue'))
const WarehousePanel=asyncPage(()=>import('./WarehousePanel.vue'))
const ProcurementPanel=asyncPage(()=>import('./ProcurementPanel.vue'))
const ReplenishmentPanel=asyncPage(()=>import('./ReplenishmentPanel.vue'))
const ShippingPanel=asyncPage(()=>import('./ShippingPanel.vue'))
const StaPanel=asyncPage(()=>import('./StaPanel.vue'))
const AdvertisingPanel=asyncPage(()=>import('./AdvertisingPanel.vue'))
const AdsCreatePage=asyncPage(()=>import('./AdsCreatePage.vue'))
import {session,can,api as authApi} from './session'
const FbaPanel=asyncPage(()=>import('./FbaPanel.vue'))
const ListingPanel=asyncPage(()=>import('./ListingPanel.vue'))
import {ref,reactive,onMounted,onUnmounted,computed,nextTick,watch} from 'vue'
import {ElMessage,ElMessageBox} from 'element-plus'
import {QuestionFilled,Box,Search,Refresh,Document,ArrowRight,Clock,ShoppingCart,OfficeBuilding,Setting,Download,Check,Warning} from '@element-plus/icons-vue'
type Item={item_id:string,sku:string,asin:string,title:string,quantity:number,amount:string|null,currency:string|null}
type Order={estimate:any,id:number,store:string,order_id:string,marketplace:string,purchased_at:string,updated_at:string,status:string,fulfilled_by:string,amount_source:string,amount:string|null,currency:string|null,quantity:number,synced_at:string,items:Item[]}
type Job={id:string,store:string,markets:string[],mode:string,status:string,start_at:string,end_at:string,pages:number,processed:number,error:string|null,created_at:string,finished_at:string|null}
let priceTimer:ReturnType<typeof setInterval>
onMounted(()=>{if(can('listing')){checkPriceJobs();priceTimer=setInterval(checkPriceJobs,3000)}})
onUnmounted(()=>clearInterval(priceTimer))
const settlementTarget=ref<any>(null),fbaTarget=ref<any>(null)
async function openSettlement(row:any){settlementTarget.value={...row,nonce:Date.now()};await navigate('logistics')}
async function openShipment(row:any){fbaTarget.value={...row,nonce:Date.now()};await navigate('fba')}
const currentStore=ref(session.value!.current_store)
const canAccessAds=computed(()=>can('advertising'))
const mobileMenuOpen=ref(false)
const tab=ref('home'),loading=ref(false),syncing=ref(false),error=ref(''),jobsError=ref(''),drawer=ref(false),syncDialog=ref(false)
const productPanel=ref<{canLeave:()=>Promise<boolean>,refresh:()=>void}|null>(null)
const listingPanel=ref<{canLeave:()=>Promise<boolean>}|null>(null)
type WorkTab={key:string,procurementCreate?:any,procurementGenerate?:any,adsContext?:any,adsFocus?:any,adsCreate?:any,title?:string,shippingId?:number,taskId?:string,productId?:number,customsId?:number,invoiceId?:number,invoiceStore?:string,invoiceShipment?:string}
const ProcurementCreate=asyncPage(()=>import('./ProcurementCreate.vue'))
function openProcurementCreate(context:any){const key='purchase-create-'+Date.now()+'-'+Math.random().toString(36).slice(2,7);insertTab({key,title:context.kind==='plans'?'新增采购计划':'新增采购单',procurementCreate:context});tab.value=key}
const ProcurementGenerate=asyncPage(()=>import('./ProcurementGenerate.vue'))
const procurementPanel=ref<any>(null),procurementGenerators=new Map<string,any>()
function openProcurementGenerate(context:any){const key='purchase-generate-'+Date.now()+'-'+Math.random().toString(36).slice(2,7);insertTab({key,title:context.mode==='purchase'?'生成采购单':'生成发货计划',procurementGenerate:context});tab.value=key}
function procurementGenerated(key:string){removeTab(key);navigate('procurement');nextTick(()=>procurementPanel.value?.refresh())}
const tabStorageKey='erp:workspace-tabs:'+session.value!.username
const opened=ref<WorkTab[]>([{key:'home'}])
const tabStrip=ref<HTMLElement|null>(null)
function revealActiveTab(){const strip=tabStrip.value,active=strip?.querySelector<HTMLElement>('.active');if(!strip||!active)return;const box=strip.getBoundingClientRect(),item=active.getBoundingClientRect();if(item.left<box.left)strip.scrollLeft-=box.left-item.left;else if(item.right>box.right)strip.scrollLeft+=item.right-box.right}
watch(tab,()=>nextTick(revealActiveTab),{flush:'post'})
onMounted(()=>nextTick(revealActiveTab))
try{const saved=JSON.parse(sessionStorage.getItem(tabStorageKey)||'null');const modules=['home','todos','logistics','settings','orders','fba','listing','finance','products','warehouse','procurement','shipping','replenishment',...(can('users')?['users']:[]),...(canAccessAds.value?['advertising']:[])];if(Array.isArray(saved?.tabs)){const seen=new Set<string>();const restored=saved.tabs.filter((t:any)=>t&&typeof t.key==='string'&&!seen.has(t.key)&&((modules.includes(t.key)&&can(t.key))||(t.key.startsWith('purchase-create-')&&['plans','orders'].includes(t.procurementCreate?.kind)&&can('procurement.'+t.procurementCreate.kind,'operate'))||(t.key.startsWith('purchase-generate-')&&can('procurement.'+t.procurementGenerate?.kind,'operate')&&Array.isArray(t.procurementGenerate?.items))||(t.key.startsWith('ads-')&&canAccessAds.value&&typeof t.adsContext?.profileId==='string'&&typeof t.adsContext?.store==='string'&&t.adsContext?.campaign?.id)||(t.key.startsWith('ads-create-')&&can('advertising','operate')&&['campaign','adgroup'].includes(t.adsCreate?.mode)&&typeof t.adsCreate?.profileId==='string')||(t.key.startsWith('ads-listing-')&&canAccessAds.value&&typeof t.adsFocus?.store==='string'&&typeof t.adsFocus?.marketplace==='string'&&typeof t.adsFocus?.asin==='string')||(t.key.startsWith('invoice-')&&can('fba.invoice')&&typeof t.invoiceStore==='string'&&typeof t.invoiceShipment==='string')||(t.key.startsWith('customs-')&&(can('products.declaration')||can('products.clearance'))&&(t.customsId===undefined||Number.isInteger(t.customsId)))||(t.key.startsWith('product-')&&can('products.products','operate')&&(t.productId===undefined||Number.isInteger(t.productId)))||(t.key.startsWith('sta-')&&(can('shipping')||can('fba.sta'))&&(typeof t.taskId==='string'||Number.isInteger(t.shippingId))))&&seen.add(t.key));if(restored.length){opened.value=restored;tab.value=restored.some((t:WorkTab)=>t.key===saved.active)?saved.active:restored[0].key}}}catch{}
if(!can(tab.value)&&!(tab.value.startsWith('ads-')&&canAccessAds.value)&&!tab.value.startsWith('sta-')&&!tab.value.startsWith('product-')&&!tab.value.startsWith('customs-')&&!tab.value.startsWith('invoice-')&&!tab.value.startsWith('purchase-generate-')){const key=['home','todos','products','listing','orders','fba','procurement','shipping','warehouse','finance','users','logistics','settings'].find(k=>can(k));opened.value=key?[{key}]:[];tab.value=key||'no-access'}
watch([opened,tab],()=>{try{sessionStorage.setItem(tabStorageKey,JSON.stringify({tabs:opened.value,active:tab.value}))}catch{}},{deep:true})
// Capture before v-show hides the form and the document height changes.
const tabScrollPositions=new Map<string,{left:number,top:number}>()
watch(tab,(_next,previous)=>{
 if(opened.value.some(t=>t.key===previous))tabScrollPositions.set(previous,{left:window.scrollX,top:window.scrollY})
},{flush:'sync'})
watch(tab,async next=>{
 const position=tabScrollPositions.get(next)||{left:0,top:0}
 await nextTick()
 await new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve())))
 if(tab.value!==next)return
 window.scrollTo({...position,behavior:'instant'})
 if(next==='orders'){void loadOrders(orders.value.length>0);void fetchJobs()}
},{flush:'post'})
const compactPage=computed(()=>tab.value.startsWith('purchase-create-')||tab.value.startsWith('purchase-generate-')||tab.value==='advertising'||tab.value.startsWith('ads-')||tab.value.startsWith('invoice-')||tab.value.startsWith('product-')||tab.value.startsWith('customs-')||['todos','orders','logistics','settings','fba','shipping','listing','finance','users','products','warehouse','procurement'].includes(tab.value))
const hasTab=(key:string)=>opened.value.some(x=>x.key===key)
function sameAdsCampaign(a:any,b:any){return !!a?.campaign?.id&&!!b?.campaign?.id&&String(a.profileId)===String(b.profileId)&&String(a.campaign.id)===String(b.campaign.id)}
function updateAdsDates(context:any,range:any){
 for(const entry of opened.value)if(sameAdsCampaign(entry.adsContext,context))entry.adsContext={...entry.adsContext,dateRange:{preset:range.preset,dates:[...range.dates]}}
}
function openAdsEntity(context:any){if(!canAccessAds.value)return;const saved=opened.value.find(t=>sameAdsCampaign(t.adsContext,context)&&t.adsContext.dateRange)?.adsContext.dateRange;context={...context,dateRange:saved||context.dateRange};const key='ads-'+context.profileId+'-'+(context.group?'group-'+context.group.id:'campaign-'+context.campaign.id);if(!hasTab(key))insertTab({key,title:(context.group?'广告组 · ':'广告活动 · ')+(context.group?.name||context.campaign.name),adsContext:context});tab.value=key;nextTick(()=>{if(context.group)adsPanels.get(key)?.showAds()})}
const adsPanels=new Map<string,any>(),adsCreators=new Map<string,any>()
function openAdsCreate(context:any){if(!can('advertising','operate'))return;const key=newAdsTabKey('ads-create-');insertTab({key,title:context.mode==='campaign'?'创建广告活动':'创建广告组',adsCreate:context});tab.value=key}
function adsCreated(){for(const panel of adsPanels.values())panel?.refresh()}
function insertTab(entry:WorkTab){const i=opened.value.findIndex(t=>t.key===tab.value);opened.value.splice(i<0?opened.value.length:i+1,0,entry)}
async function navigate(next:string){if(!can(next))return;mobileMenuOpen.value=false;if(!hasTab(next))insertTab({key:next});tab.value=next;}
const shippingTarget=ref<{id:number,request:number}>()
function openShipping(id:number){if(!can('shipping'))return;shippingTarget.value={id,request:Date.now()};void navigate('shipping')}
function openSta(data:{shippingId?:number,taskId?:string}){let found=opened.value.find(x=>(data.taskId&&x.taskId===data.taskId)||(data.shippingId&&x.shippingId===data.shippingId));if(!found){found={key:'sta-'+(data.taskId||data.shippingId),title:'STA 入库计划',...data};insertTab(found)}tab.value=found.key}
function staCreated(entry:WorkTab,data:{id:string,name:string}){entry.taskId=data.id;entry.title='STA · '+data.name}
const invoiceEditors=new Map<string,any>(),fbaPanel=ref<any>()
function openInvoice(row:any){if(!can('fba.invoice'))return;const key='invoice-'+row.store+'-'+row.shipment_id;if(!hasTab(key))insertTab({key,title:'清关发票 · '+row.shipment_id,invoiceId:row.invoiceId,invoiceStore:row.store,invoiceShipment:row.shipment_id});tab.value=key}
function invoiceSaved(entry:WorkTab){removeTab(entry.key);if(!hasTab('fba'))insertTab({key:'fba'});tab.value='fba';nextTick(()=>fbaPanel.value?.refreshInvoices())}
const productEditors=new Map<string,any>()
const customsEditors=new Map<string,any>()
function openCustoms(id?:number){if(!can('products.declaration')&&!can('products.clearance'))return;const key=id?'customs-'+id:'customs-new';if(!hasTab(key))insertTab({key,customsId:id,title:id?'清关&报关资料':'新增清关&报关资料'});tab.value=key}
function customsSaved(entry:WorkTab,_data:{id:number,name:string}){removeTab(entry.key);productPanel.value?.refresh()}
function openProduct(id?:number){const key=id?'product-'+id:'product-new';let entry=opened.value.find(t=>t.key===key);if(!entry){entry={key,productId:id,title:id?'编辑产品':'新增产品'};insertTab(entry)}tab.value=key}
function productSaved(entry:WorkTab,_data:{id:number,name:string}){removeTab(entry.key);productPanel.value?.refresh()}
async function closeTab(key:string){if(procurementGenerators.has(key)&&!await procurementGenerators.get(key).canLeave())return;if(key.startsWith('ads-create-')&&adsCreators.get(key)&&!(await adsCreators.get(key).canLeave()))return;if(key.startsWith('invoice-')&&invoiceEditors.get(key)&&!(await invoiceEditors.get(key).canLeave()))return;if(key.startsWith('customs-')&&customsEditors.get(key)&&!(await customsEditors.get(key).canLeave()))return;if(key.startsWith('product-')&&productEditors.get(key)&&!(await productEditors.get(key).canLeave()))return;if(key==='products'&&productPanel.value&&!(await productPanel.value.canLeave()))return;if(key==='listing'&&listingPanel.value&&!(await listingPanel.value.canLeave()))return;removeTab(key)}
function removeTab(key:string){const i=opened.value.findIndex(x=>x.key===key);if(i<0)return;tabScrollPositions.delete(key);opened.value.splice(i,1);if(!opened.value.length){const fallback=['home','todos','products','listing','orders','fba','procurement','shipping','warehouse','finance','users','logistics','settings','advertising','replenishment'].find(k=>can(k));if(fallback)opened.value.push({key:fallback})};if(tab.value===key)tab.value=opened.value[Math.max(0,i-1)]?.key||'no-access'}
const dragging=ref(''),dragOver=ref('')
function startTabDrag(event:DragEvent,key:string){dragging.value=key;tabMenu.value=null;if(event.dataTransfer){event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',key)}}
function dropTab(target:string){const from=opened.value.findIndex(t=>t.key===dragging.value),to=opened.value.findIndex(t=>t.key===target);if(from>=0&&to>=0&&from!==to){const [item]=opened.value.splice(from,1);opened.value.splice(to,0,item)}dragging.value='';dragOver.value=''}
const passwordOpen=ref(false),passwordSaving=ref(false),passwordError=ref('')
const passwordForm=reactive({current_password:'',new_password:'',confirm:''})
function accountAction(command:string){if(command==='logout'){logout();return}Object.assign(passwordForm,{current_password:'',new_password:'',confirm:''});passwordError.value='';passwordOpen.value=true}
async function changePassword(){
 passwordError.value=''
 if(passwordForm.new_password.length<12){passwordError.value='新密码至少 12 位';return}
 if(passwordForm.new_password!==passwordForm.confirm){passwordError.value='两次输入的新密码不一致';return}
 passwordSaving.value=true
 try{await authApi('/auth/password',{current_password:passwordForm.current_password,new_password:passwordForm.new_password});passwordOpen.value=false;ElMessage.success('密码已修改，其他设备需要重新登录')}
 catch(e){passwordError.value=(e as Error).message}finally{passwordSaving.value=false}
}
watch(passwordOpen,open=>{if(!open)Object.assign(passwordForm,{current_password:'',new_password:'',confirm:''})})
const tabMenu=ref<{key:string,x:number,y:number}|null>(null)
async function closeTabs(mode:string){const m=tabMenu.value;if(!m)return;const i=opened.value.findIndex(x=>x.key===m.key);const keys=opened.value.filter((x,n)=>mode==='others'?x.key!==m.key:mode==='left'?n<i:n>i).map(x=>x.key);tabMenu.value=null;for(const key of keys)await closeTab(key)}
const tabTitle=(key:string)=>opened.value.find(x=>x.key===key)?.title||titles[key]||'STA 入库计划'

const options=reactive({stores:[] as string[],markets:[] as string[],auto_sync:false})
const filters=reactive({store:currentStore.value,marketplace:'',status:'',fulfilled_by:'',q:''})
const orderDays=ref(1),orderSort=ref('date'),page=ref(1),pageSize=ref(25),total=ref(0),orders=ref<Order[]>([]),jobs=ref<Job[]>([]),selected=ref<Order|null>(null)
const todayOverview=ref<any>(null),orderSync=ref<any>(null)
const summary=ref({statuses:{} as Record<string,number>,amounts:[] as {currency:string,amount:string}[],units:0,last_synced:null as string|null})
const form=reactive({stores:[] as string[],markets:['DE','UK'],days:7,mode:'incremental'})
const names:Record<string,string>={DE:'德国',UK:'英国',FR:'法国',IT:'意大利',ES:'西班牙',NL:'荷兰',BE:'比利时',PL:'波兰',SE:'瑞典',IE:'爱尔兰',AE:'阿联酋',SA:'沙特'}
const statuses:Record<string,string>={PENDING_AVAILABILITY:'预售待发',PENDING:'待付款',UNSHIPPED:'待发货',PARTIALLY_SHIPPED:'部分发货',SHIPPED:'已发货',CANCELLED:'已取消',UNFULFILLABLE:'无法配送',UNKNOWN:'待更新'}
const jobNames:Record<string,string>={held:'等待分段执行',queued:'排队中',running:'同步中',succeeded:'已完成',failed:'失败'}
const activeJobs=computed(()=>jobs.value.filter(j=>['queued','running'].includes(j.status)))
const latestSuccess=computed(()=>jobs.value.find(j=>j.status==='succeeded'))
const money=(amount:string|null,currency:string|null)=>amount==null?'—':new Intl.NumberFormat('zh-CN',{style:'currency',currency:currency||'EUR'}).format(Number(amount))
const time=(value:string|null)=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚未同步'
const dateOnly=(value:string)=>new Date(value).toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai'})
async function api(path:string,body?:unknown){
 try{return await authApi(path,body)}catch(e){
  if(body!==undefined||!(e as any).transient)throw e
  await new Promise(resolve=>setTimeout(resolve,700))
  return authApi(path)
 }
}
let requestId=0,orderRequests=0
async function fetchOrders(){await loadOrders()}
async function loadOrders(background=false){
 if(!can('orders'))return
 if(background&&orderRequests>0)return
 const id=++requestId;orderRequests++
 if(!background){loading.value=true;error.value=''}
 const normalizedFilters=Object.fromEntries(Object.entries(filters).map(([key,value])=>[key,value??'']))
 const params=new URLSearchParams({...normalizedFilters,page:String(page.value),page_size:String(pageSize.value)})
 params.set('days',String(orderDays.value));params.set('sort',orderSort.value)
 try{
  const [data,today]=await Promise.all([api('/orders?'+params),api('/order-overview?'+new URLSearchParams({store:filters.store??'',marketplace:filters.marketplace??'',currency:displayCurrency.value}))])
  if(id!==requestId)return
  if(JSON.stringify(todayOverview.value)!==JSON.stringify(today))todayOverview.value=today
  if(JSON.stringify(orders.value)!==JSON.stringify(data.orders))orders.value=data.orders
  error.value='';total.value=data.total;orderSync.value=data.sync_progress
  if(JSON.stringify(summary.value)!==JSON.stringify(data.summary))summary.value=data.summary
 }catch(e){if(id===requestId)error.value='订单数据更新失败：'+(e as Error).message}
 finally{orderRequests--;if(id===requestId&&!background)loading.value=false}
}
let jobsLoading=false
async function fetchJobs(){if(!can('orders')||jobsLoading)return;jobsLoading=true;try{jobs.value=await api('/sync/jobs');jobsError.value=''}catch(e){jobsError.value='同步状态暂未更新：'+(e as Error).message}finally{jobsLoading=false}}
async function refresh(){await Promise.all([fetchOrders(),fetchJobs()])}
function search(){page.value=1;fetchOrders()}
function reset(){Object.assign(filters,{store:currentStore.value,marketplace:'',status:'',fulfilled_by:'',q:''});orderDays.value=1;orderSort.value='date';search()}
async function detail(order:Order){try{selected.value=await api('/orders/'+order.id);drawer.value=true}catch(e){ElMessage.error((e as Error).message)}}
async function submitSync(){
 if(!form.stores.length||!form.markets.length){ElMessage.warning('请选择店铺和站点');return}
 syncing.value=true
 try{const result=await api('/sync',form);syncDialog.value=false;ElMessage.success(result.jobs.some((j:{created:boolean})=>j.created)?'同步任务已加入队列':'所选店铺已有同步任务');await fetchJobs()}catch(e){ElMessage.error((e as Error).message)}finally{syncing.value=false}
}
async function retry(job:Job){try{await api('/sync/jobs/'+job.id+'/retry',{});ElMessage.success('已安排继续同步');fetchJobs()}catch(e){ElMessage.error((e as Error).message)}}
async function toggleAuto(value:string|number|boolean){try{await api('/sync/automatic',{enabled:!!value});options.auto_sync=!!value;ElMessage.success(value?'已开启每 5 分钟增量同步（全部店铺、已配置订单站点）':'已关闭自动同步；当前任务仍继续')}catch(e){options.auto_sync=!value;ElMessage.error((e as Error).message)}}
function openSync(){form.stores=filters.store?[filters.store]:[...options.stores];form.markets=filters.marketplace?[filters.marketplace]:['DE','UK'];syncDialog.value=true}
let timer:ReturnType<typeof setInterval>,polling=false
onMounted(async()=>{try{const o=await api('/options');Object.assign(options,o,{markets:o.order_markets||o.markets});form.stores=[...options.stores]}catch(e){error.value=(e as Error).message}if(tab.value==='orders')await refresh();timer=setInterval(async()=>{if(polling)return;polling=true;try{const wasActive=activeJobs.value.length>0;await fetchJobs();if(activeJobs.value.length||wasActive||(tab.value==='orders'&&(orderSync.value?.active||orders.value.some(o=>o.estimate?.pending))))await loadOrders(true)}finally{polling=false}},8000)})
onUnmounted(()=>clearInterval(timer))

const titles:Record<string,string>={home:'首页',todos:'待办事项',orders:'订单中心',listing:'Listing',fba:'FBA 货件',finance:'利润结算',users:'用户管理',products:'产品管理',warehouse:'仓库',logistics:'物流中心',settings:'设置',procurement:'采购管理',shipping:'发货计划',replenishment:'补货建议',advertising:'广告活动'}
async function switchStore(value:string){
 if(listingPanel.value&&!(await listingPanel.value.canLeave()))return
 try{session.value=await authApi('/auth/store',{store:value});currentStore.value=value;filters.store=value;page.value=1;drawer.value=false;syncDialog.value=false;await fetchOrders()}catch(e){ElMessage.error((e as Error).message)}
}
async function logout(){try{await ElMessageBox.confirm('退出会清除本次会话中尚未提交的编辑，确定退出？','退出登录',{type:'warning'});await authApi('/auth/logout',{});session.value=null}catch{}}
</script>

<template>
<OrderIncomeDialog :order="calculatorOrder" @close="calculatorOrder=null" @calculated="loadOrders(true)"/><div class="shell">
 <button v-if="mobileMenuOpen" class="sidebar-backdrop" aria-label="关闭导航菜单" @click="mobileMenuOpen=false"/>
 <aside id="workspace-navigation" class="sidebar" :class="{'mobile-open':mobileMenuOpen}" aria-label="业务导航" @keydown.esc="mobileMenuOpen=false">
  <button class="mobile-nav-close" aria-label="收起导航菜单" @click="mobileMenuOpen=false">关闭菜单 ×</button>
  <div class="brand"><div class="brand-icon"><el-icon><Box/></el-icon></div><div>Amazon ERP<small>你的跨境业务工作台</small></div></div>
  <div class="workspace workspace-switch"><div class="workspace-caption"><div class="avatar">{{session?.username.slice(0,1).toUpperCase()}}</div><div>我的工作空间<small>{{session?.username}} · 主营 {{session?.default_store}}</small></div></div><el-select :model-value="currentStore" aria-label="当前店铺" @change="switchStore"><el-option v-for="s in session?.stores" :key="s" :value="s" :label="s"/></el-select></div>
  <div class="nav-label">业务管理</div>
  <button v-if="can('home')" class="nav-item" :class="{active:tab==='home'}" @click="navigate('home')"><el-icon><OfficeBuilding/></el-icon><span>首页</span></button>
  <button v-if="can('todos')" class="nav-item" :class="{active:tab==='todos'}" @click="navigate('todos')"><el-icon><Check/></el-icon><span>待办事项</span><TodoReminder/></button>
  <button v-if="can('products')" class="nav-item" :class="{active:tab==='products'}" @click="navigate('products')"><el-icon><Box/></el-icon><span>产品管理</span></button>
  <button v-if="can('listing')" class="nav-item" :class="{active:tab==='listing'}" @click="navigate('listing')"><el-icon><Document/></el-icon><span>Listing</span></button>
  <button v-if="can('orders')" class="nav-item" :class="{active:tab==='orders'}" @click="navigate('orders')"><el-icon><Document/></el-icon><span>订单中心</span></button>
  <button v-if="can('fba')" class="nav-item" :class="{active:tab==='fba'}" @click="navigate('fba')"><el-icon><Box/></el-icon><span>FBA货件</span></button>
  <button v-if="can('logistics')" class="nav-item" :class="{active:tab==='logistics'}" @click="navigate('logistics')"><el-icon><Box/></el-icon><span>物流中心</span></button>
  <button v-if="can('procurement')" class="nav-item" :class="{active:tab==='procurement'}" @click="navigate('procurement')"><el-icon><ShoppingCart/></el-icon><span>采购管理</span></button>
  <button v-if="can('shipping')" class="nav-item" :class="{active:tab==='shipping'}" @click="navigate('shipping')"><el-icon><ShoppingCart/></el-icon><span>发货计划</span></button>
  <button v-if="can('replenishment')" class="nav-item" :class="{active:tab==='replenishment'}" @click="navigate('replenishment')"><el-icon><ShoppingCart/></el-icon><span>补货建议</span></button>
  <button v-if="can('warehouse')" class="nav-item" :class="{active:tab==='warehouse'}" @click="navigate('warehouse')"><el-icon><OfficeBuilding/></el-icon><span>仓库</span></button>
  <button v-if="can('finance')" class="nav-item" :class="{active:tab==='finance'}" @click="navigate('finance')"><el-icon><ShoppingCart/></el-icon><span>利润结算</span></button>
  <button v-if="canAccessAds" class="nav-item" :class="{active:tab==='advertising'||tab.startsWith('ads-')}" @click="navigate('advertising')"><el-icon><Document/></el-icon><span>广告活动</span></button>
  <button v-if="can('users')" class="nav-item" :class="{active:tab==='users'}" @click="navigate('users')"><el-icon><Setting/></el-icon><span>用户管理</span></button>
  <button v-if="can('settings')" class="nav-item" :class="{active:tab==='settings'}" @click="navigate('settings')"><el-icon><Setting/></el-icon><span>设置</span></button>
  <div class="sidebar-bottom"><div class="secure-dot"></div><span>数据存储在本机<small>Amazon-ERP / data</small></span><span class="version">v0.5</span></div>
 </aside>
 <main class="main">

  <div class="workspace-bar"><button class="mobile-nav-toggle" aria-label="展开导航菜单" aria-controls="workspace-navigation" :aria-expanded="mobileMenuOpen" @click="mobileMenuOpen=!mobileMenuOpen">☰ 菜单</button><nav ref="tabStrip" class="work-tabs" aria-label="已打开页面"><div v-for="t in opened" :key="t.key" :data-tab-key="t.key" draggable="true" @dragstart="startTabDrag($event,t.key)" @dragover.prevent="dragOver=t.key" @drop.prevent="dropTab(t.key)" @dragend="dragging='';dragOver=''" :class="{active:tab===t.key,dragging:dragging===t.key,'drag-over':dragOver===t.key&&dragging!==t.key}" @contextmenu.prevent="tabMenu={key:t.key,x:$event.clientX,y:$event.clientY}"><button class="tab-title" @click="tab=t.key" :title="tabTitle(t.key)">{{tabTitle(t.key)}}</button><button class="tab-close" :aria-label="'关闭'+tabTitle(t.key)" @click.stop="closeTab(t.key)">×</button></div></nav><div class="workspace-account"><AdsSearchTaskStatus/><el-tooltip content="帮助文档"><a class="help-button" href="/help" target="_blank" rel="noopener noreferrer" aria-label="帮助文档"><el-icon><QuestionFilled/></el-icon></a></el-tooltip><el-dropdown trigger="click" @command="accountAction"><button class="account-avatar" :aria-label="session?.username+' 账户菜单'" :title="session?.username">{{session?.username.slice(0,1).toUpperCase()}}</button><template #dropdown><el-dropdown-menu><el-dropdown-item disabled>{{session?.username}}</el-dropdown-item><el-dropdown-item command="password" divided>修改密码</el-dropdown-item><el-dropdown-item command="logout">退出登录</el-dropdown-item></el-dropdown-menu></template></el-dropdown></div></div>
  <div v-if="tabMenu" class="tab-menu-mask" @click="tabMenu=null"><div class="tab-menu" :style="{left:tabMenu.x+'px',top:tabMenu.y+'px'}" @click.stop><button @click="closeTabs('others')">关闭其他</button><button @click="closeTabs('left')">关闭左边</button><button @click="closeTabs('right')">关闭右边</button></div></div>
  <div class="content" :class="{'shipping-content':tab==='shipping','compact-content':compactPage,'campaign-workspace-content':tab==='advertising'||tab.startsWith('ads-')}">
   <div v-if="!compactPage" class="page-heading"><div><div class="eyebrow">{{tab==='home'?'STORE OVERVIEW':tab==='finance'?'PAYMENTS & PROFIT':tab==='users'?'PEOPLE & ACCESS':tab.toUpperCase()}}</div><h1>{{tabTitle(tab)}}</h1><p>{{tab==='home'?'掌握当前店铺的销售、库存与经营进展。':tab==='orders'?'汇集店铺订单，掌握销售与履约进度。':tab==='listing'?'查看亚马逊商品信息，预览修改后确认提交。':tab==='finance'?'查询订单回款，逐步建立完整利润口径。':tab==='users'?'管理账号、主营店铺和访问权限。':tab==='advertising'?'查看广告活动、广告组及广告表现。':tab==='shipping'?'按采购到货安排发货，维护装箱与标签。':tab==='replenishment'?'结合销量、本地库存及在途供应，安排普通商品补货。':tab==='procurement'?'汇总采购需求，跟踪采购与到货入库。':tab==='warehouse'?'管理本地仓库、库存及出入库记录。':tab==='products'?'维护本地标品，统一管理跨店铺 SKU 对应关系。':'追踪货件状态与商品接收进度。'}}</p></div></div>

   <el-empty v-if="tab==='no-access'" description="尚未分配页面权限，请联系管理员"/><el-alert v-if="error && tab==='orders'" :title="error" type="error" show-icon :closable="false" class="error-alert"/><el-alert v-if="jobsError && tab==='orders'" :title="jobsError" type="warning" show-icon :closable="false"/>
   <div v-if="activeJobs.length && tab==='orders'" class="sync-banner"><el-icon class="spin"><Refresh/></el-icon><span><b>{{activeJobs.map(j=>j.store).join('、')}}</b> 正在同步订单 · 已处理 {{activeJobs.reduce((n,j)=>n+j.processed,0)}} 条</span></div>
   <div v-if="hasTab('orders')" v-show="tab==='orders'" class="orders-page">
    <div class="stats-grid order-today-stats compact-order-today"><div class="stat-card"><div class="stat-top">今日订单量</div><div class="stat-value">{{todayOverview?.orders?.toLocaleString()??'—'}}<small>笔</small></div><div class="stat-foot">按所选店铺、站点当地日期 · 不含取消订单</div></div><div class="stat-card"><div class="stat-top">今日订单金额 · {{displayCurrency}}</div><div class="stat-value">{{money(todayOverview?.converted?.amount??null,displayCurrency)}}</div><div class="stat-foot">按首页所选币种换算<span v-if="todayOverview?.unknown_amount_orders"> · {{todayOverview.unknown_amount_orders}} 笔金额待返回</span></div></div></div>
    <section class="panel">
     <el-alert v-if="orderSync?.active" type="info" :closable="false" show-icon style="margin-bottom:12px">
      <template #title>订单同步仍在进行，已入库 {{orderSync.stored_orders.toLocaleString()}} 笔；当前筛选最近 {{orderDays}} 天。</template>
      <div v-for="r in orderSync.runs" :key="r.store">{{r.store}} · {{r.mode==='initialize'?'初始化':'定时同步'}}（{{dateOnly(r.start_at)}} 至 {{dateOnly(r.end_at)}}）：已完成 {{r.completed}} / {{r.total}} 段，全部分段完成后更新整次同步时间。</div>
      <div v-for="r in orderSync.segments" :key="r.store">正在处理 {{dateOnly(r.start_at)}} 至 {{dateOnly(r.end_at)}}，已读取 {{r.processed}} 笔。</div>
      <div v-if="!total&&orderSync.stored_orders">当前日期范围暂无订单。已入库日期：{{dateOnly(orderSync.oldest_order_at)}} 至 {{dateOnly(orderSync.newest_order_at)}}，可调整日期范围查看。</div>
     </el-alert>
     <div class="panel-head"><div><h2>全部订单 <span class="count-badge">{{total.toLocaleString()}}</span></h2><span class="muted">{{summary.units.toLocaleString()}} 件商品</span></div><span class="last-sync">最近整次同步完成：{{time(orderSync?.last_success_at||null)}}<br v-if="orderSync?.last_segment_at"/><span v-if="orderSync?.last_segment_at">最近分段完成：{{time(orderSync.last_segment_at)}}</span></span></div>
     <form class="filters order-filters" @submit.prevent="search">
      <el-input v-model="filters.q" placeholder="搜索订单号 / SKU / ASIN / 商品名" :prefix-icon="Search" clearable class="keyword" @clear="search"/>
      <el-select v-model="filters.store" :value-on-clear="''" placeholder="全部店铺" clearable><el-option v-for="s in options.stores" :key="s" :label="s" :value="s"/></el-select>
      <el-select v-model="filters.marketplace" :value-on-clear="''" placeholder="全部站点" clearable><el-option v-for="m in options.markets" :key="m" :label="(names[m]||m)+' · '+m" :value="m"/></el-select>
      <el-select v-model="filters.status" :value-on-clear="''" placeholder="全部状态" clearable><el-option v-for="(v,k) in statuses" :key="k" :label="v" :value="k"/></el-select>

      <el-select v-model="orderDays" aria-label="订单日期范围" @change="search"><el-option v-for="d in [1,3,7,14,30,90,180,365]" :key="d" :value="d" :label="`最近${d}天`"/></el-select><el-select v-model="orderSort" aria-label="订单排序" style="flex-basis:180px;width:180px" @change="search"><el-option value="date" label="按订单日期降序"/><el-option value="quantity" label="按销量降序"/></el-select>
      <el-button type="primary" native-type="submit" :icon="Search">查询</el-button><el-button @click="reset">重置</el-button><el-button :icon="Refresh" @click="refresh">刷新页面</el-button>
     <TableReset table-key="OrderRows/orders"/></form>
     <OrderRows :orders="orders" :loading="loading" :statuses="statuses" :names="names" @detail="detail" @calculate="calculatorOrder=$event" @sync="openSync"/>
     <div class="table-footer"><span>下单时间按北京时间显示</span><AppPagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" @change="fetchOrders"/></div>
    </section>
   </div>
   <div v-if="hasTab('home')" v-show="tab==='home'"><DashboardPanel :key="currentStore" :store="currentStore" :visible="tab==='home'" @navigate="navigate"/></div><div v-if="hasTab('todos') && can('todos')" v-show="tab==='todos'"><TodoPanel :active="tab==='todos'"/></div><div v-if="hasTab('users') && can('users')" v-show="tab==='users'"><UserPanel/></div><div v-if="hasTab('finance')" v-show="tab==='finance'"><FinancePanel :key="currentStore" :store="currentStore"/></div><div v-if="hasTab('fba')" v-show="tab==='fba'"><FbaPanel ref="fbaPanel" @open-shipping="openShipping" @open-invoice="openInvoice" :active="tab==='fba'" :target="fbaTarget" @settlement="openSettlement" @open-sta="openSta" :key="currentStore" :initial-store="currentStore"/></div>
   <div v-for="entry in opened.filter(t=>t.adsContext||t.adsFocus)" :key="entry.key" v-show="tab===entry.key"><AdvertisingPanel :active="tab===entry.key" :context="entry.adsContext" :focus="entry.adsFocus" @date-range="range=>updateAdsDates(entry.adsContext,range)" :ref="el=>el?adsPanels.set(entry.key,el):adsPanels.delete(entry.key)" @open-create="openAdsCreate" @open-entity="openAdsEntity" @home="navigate('advertising')"/></div>
   <div v-for="entry in opened.filter(t=>t.procurementCreate)" :key="entry.key" v-show="tab===entry.key"><ProcurementCreate :context="entry.procurementCreate" :ref="el=>el?procurementGenerators.set(entry.key,el):procurementGenerators.delete(entry.key)" @created="procurementGenerated(entry.key)" @close="closeTab(entry.key)"/></div>
   <div v-for="entry in opened.filter(t=>t.procurementGenerate)" :key="entry.key" v-show="tab===entry.key"><ProcurementGenerate :context="entry.procurementGenerate" :ref="el=>el?procurementGenerators.set(entry.key,el):procurementGenerators.delete(entry.key)" @completed="procurementGenerated(entry.key)" @close="closeTab(entry.key)"/></div>
   <div v-for="entry in opened.filter(t=>t.adsCreate)" :key="entry.key" v-show="tab===entry.key"><AdsCreatePage :mode="entry.adsCreate.mode" :profile-id="entry.adsCreate.profileId" :campaign="entry.adsCreate.campaign" :draft-key="entry.key" :ref="el=>el?adsCreators.set(entry.key,el):adsCreators.delete(entry.key)" @created="adsCreated" @close="removeTab(entry.key)"/></div>
   <div v-for="entry in opened.filter(t=>t.key.startsWith('invoice-'))" :key="entry.key" v-show="tab===entry.key"><InvoiceEditor :store="entry.invoiceStore!" :shipment-id="entry.invoiceShipment!" :invoice-id="entry.invoiceId" :ref="el=>el?invoiceEditors.set(entry.key,el):invoiceEditors.delete(entry.key)" @saved="invoiceSaved(entry)" @close="closeTab(entry.key)"/></div>
   <div v-for="entry in opened.filter(t=>t.key.startsWith('customs-'))" :key="entry.key" v-show="tab===entry.key"><CustomsProfileEditor :profile-id="entry.customsId" :ref="el=>el?customsEditors.set(entry.key,el):customsEditors.delete(entry.key)" @saved="data=>customsSaved(entry,data)" @close="closeTab(entry.key)"/></div>
   <div v-for="entry in opened.filter(t=>t.key.startsWith('product-'))" :key="entry.key" v-show="tab===entry.key"><ProductEditor :product-id="entry.productId" :ref="el=>el?productEditors.set(entry.key,el):productEditors.delete(entry.key)" @saved="data=>productSaved(entry,data)" @close="closeTab(entry.key)"/></div>
   <div v-if="hasTab('shipping')" v-show="tab==='shipping'"><ShippingPanel :target="shippingTarget" @open-sta="openSta" :initial-store="currentStore" :active="tab==='shipping'"/></div><div v-if="hasTab('replenishment')" v-show="tab==='replenishment'"><ReplenishmentPanel :initial-store="currentStore" @navigate="navigate"/></div><div v-if="hasTab('procurement')" v-show="tab==='procurement'"><ProcurementPanel ref="procurementPanel" :initial-store="currentStore" @open-generate="openProcurementGenerate" @open-create="openProcurementCreate"/></div><div v-if="hasTab('warehouse')" v-show="tab==='warehouse'"><WarehousePanel/></div><div v-if="hasTab('advertising') && canAccessAds" v-show="tab==='advertising'"><AdvertisingPanel :active="tab==='advertising'" :ref="el=>el?adsPanels.set('advertising',el):adsPanels.delete('advertising')" @open-create="openAdsCreate" @open-entity="openAdsEntity" @home="navigate('advertising')"/></div><div v-if="hasTab('products')" v-show="tab==='products'"><ProductPanel :initial-store="currentStore" ref="productPanel" @open-product="openProduct" @open-customs="openCustoms"/></div><div v-if="hasTab('listing')" v-show="tab==='listing'"><ListingPanel :key="currentStore" :initial-store="currentStore" ref="listingPanel" @open-ads="openListingAds"/></div>
   <div v-if="hasTab('logistics')" v-show="tab==='logistics'"><LogisticsPanel :shipment="settlementTarget" @open-shipment="openShipment"/></div><div v-if="hasTab('settings')" v-show="tab==='settings'"><SettingsPanel/></div>
   <StaPanel v-for="t in opened.filter(x=>x.key.startsWith('sta-'))" :key="t.key" v-show="tab===t.key" :shipping-id="t.shippingId" :task-id="t.taskId" :active="tab===t.key" @created="staCreated(t,$event)" @completed="closeTab(t.key)"/>
   <footer v-if="tab!=='advertising'&&!tab.startsWith('ads-')" class="page-footer"><span>Amazon ERP · 本地业务工作台</span><span>本地存储，按需同步</span></footer>
  </div>
 </main>
 
 <el-dialog v-model="passwordOpen" title="修改密码" width="440px" :close-on-click-modal="false" :close-on-press-escape="!passwordSaving" :show-close="!passwordSaving"><el-alert v-if="passwordError" :title="passwordError" type="error" :closable="false"/><el-form label-position="top" @submit.prevent="changePassword"><el-form-item label="当前密码"><el-input v-model="passwordForm.current_password" aria-label="当前密码" type="password" show-password autocomplete="current-password" :disabled="passwordSaving"/></el-form-item><el-form-item label="新密码（至少 12 位）"><el-input v-model="passwordForm.new_password" aria-label="新密码" type="password" show-password autocomplete="new-password" maxlength="128" :disabled="passwordSaving"/></el-form-item><el-form-item label="确认新密码"><el-input v-model="passwordForm.confirm" aria-label="确认新密码" type="password" show-password autocomplete="new-password" :disabled="passwordSaving"/></el-form-item><p class="muted">修改后其他设备需要重新登录，当前会话保留。</p><el-button type="primary" native-type="submit" :loading="passwordSaving">保存密码</el-button></el-form></el-dialog>
 <el-drawer v-model="drawer" title="订单详情" size="650px">
  <template v-if="selected"><div class="detail-hero"><div><span class="eyebrow">AMAZON ORDER</span><h2>{{selected.order_id}}</h2></div><span class="status-tag" :class="selected.status"><i></i>{{statuses[selected.status]||selected.status}}</span></div><el-descriptions :column="2" border class="detail-info"><el-descriptions-item label="店铺">{{selected.store}}</el-descriptions-item><el-descriptions-item label="站点">{{names[selected.marketplace]||selected.marketplace}}</el-descriptions-item><el-descriptions-item label="下单时间" :span="2">{{time(selected.purchased_at)}}</el-descriptions-item><el-descriptions-item label="配送方式">{{selected.fulfilled_by==='AMAZON'?'FBA':selected.fulfilled_by==='MERCHANT'?'FBM':'—'}}</el-descriptions-item><el-descriptions-item label="订单金额">{{money(selected.estimate?.display_amount??selected.amount,selected.estimate?.display_currency??selected.currency)}}<small v-if="selected.estimate?.amount_estimated"> · 售价预估</small><small v-if="selected.amount_source==='order_report'"> · 历史报表汇总，非回款金额</small></el-descriptions-item><el-descriptions-item label="亚马逊更新时间" :span="2">{{time(selected.updated_at)}}</el-descriptions-item><el-descriptions-item label="本地同步时间" :span="2">{{time(selected.synced_at)}}</el-descriptions-item></el-descriptions><div class="order-estimate-detail"><h3>预估毛利 {{money(selected.estimate?.gross_profit??null,selected.estimate?.display_currency??selected.currency)}}</h3><p v-if="selected.estimate?.gross_profit!=null">亚马逊费用 {{selected.estimate.amazon_fees}} · 增值税预估 {{selected.estimate.vat}} · 采购成本 {{selected.estimate.purchase_cost}} · 头程运费 {{selected.estimate.freight_cost}}（{{selected.estimate.display_currency}}）</p><p class="muted">{{selected.estimate?.method}} {{selected.estimate?.notes?.join('；')}}</p></div><h3 class="detail-title">商品明细 <span>{{selected.items.length}} 款 · {{selected.quantity}} 件</span></h3><div v-for="item in selected.items" :key="item.item_id" class="detail-product"><div class="detail-product-name">{{item.title||'商品信息待更新'}}</div><div class="detail-sku">SKU：{{item.sku||'—'}}<br>ASIN：{{item.asin||'—'}}</div><div class="detail-product-bottom"><span>数量 × {{item.quantity}}</span><b>{{money(item.amount,item.currency)}}</b></div></div></template>
 </el-drawer>
</div>
</template>

<style scoped>
.help-button{display:grid;place-items:center;background:transparent;color:#d8e6e1;border:0;border-radius:50%;width:34px;height:34px;margin-right:8px;font-size:21px;cursor:pointer}.help-button:hover{background:#ffffff18;color:white}.help-button:focus-visible{outline:2px solid #6ee7ca}

.order-filters{flex-wrap:nowrap!important;align-items:center;overflow-x:auto}.order-filters>.keyword{flex:0 0 245px;width:245px;min-width:245px}.order-filters>.el-select{flex:0 0 115px;width:115px}.order-filters>.el-button{flex-shrink:0}.compact-order-today{display:flex;gap:28px;margin:0;background:white;padding:12px}.compact-order-today>.stat-card{border:0;border-radius:0;min-height:0;padding:0;min-width:230px}.compact-order-today .stat-value{font-size:24px;margin:5px 0}.compact-order-today .stat-foot{margin-top:2px}
.workspace-bar{position:sticky;top:0;z-index:1000;display:flex;align-items:stretch;background:#263548;min-width:0;height:46px}.workspace-account{display:flex;align-items:center;padding:4px 16px 4px 10px;flex-shrink:0}.workspace-bar .work-tabs{flex:1;min-width:0}.workspace-bar>.mobile-nav-toggle{align-self:center;margin-left:12px}@media(max-width:760px){.workspace-account{padding:4px 8px}.workspace-bar .work-tabs{padding-left:8px}}.work-tabs{display:flex;align-items:flex-start;gap:4px;padding:4px 16px 0;overflow-x:auto;overflow-y:hidden;background:#263548;border-bottom:0;scrollbar-width:none;scrollbar-color:transparent transparent}.work-tabs:hover,.work-tabs:focus-within{scrollbar-color:#72809466 transparent}.work-tabs::-webkit-scrollbar{display:none;height:0}.work-tabs::-webkit-scrollbar-track{background:transparent}.work-tabs::-webkit-scrollbar-thumb{background:transparent;border-radius:8px}.work-tabs:hover::-webkit-scrollbar-thumb,.work-tabs:focus-within::-webkit-scrollbar-thumb{background:#72809466}.work-tabs>div{display:flex;flex:0 0 140px;width:140px;height:42px;box-sizing:border-box;align-items:center;border:1px solid transparent;border-radius:7px 7px 0 0}.work-tabs>div.active{background:white;border-color:#dce5df;border-bottom-color:white}.work-tabs button{border:0;background:transparent;color:#cbd7e5;padding:8px 10px;font-size:13px;line-height:18px;cursor:pointer;white-space:nowrap}.work-tabs .tab-title{flex:1 1 auto;min-width:0;text-overflow:ellipsis;overflow:hidden}.work-tabs .active button{color:#21745f;font-weight:600}.work-tabs .tab-close{flex:0 0 30px;width:30px;padding:7px 4px;font-size:18px;text-overflow:clip;overflow:visible}.tab-menu-mask{position:fixed;inset:0;z-index:3000}.tab-menu{position:absolute;background:white;box-shadow:0 4px 24px #0002;padding:6px;border-radius:6px;display:grid}.tab-menu button{border:0;background:white;text-align:left;padding:10px 25px;cursor:pointer}.tab-menu button:hover{background:#edf5f1}.work-tabs>div{cursor:grab}.work-tabs>div.dragging{opacity:.45}.work-tabs>div.drag-over{box-shadow:inset 3px 0 #6ee7ca}.work-tabs>div:not(.active):hover{background:#33465e}.work-tabs button:focus-visible{outline:2px solid #6ee7ca;outline-offset:-3px}.account-avatar{height:32px;width:32px;border-radius:50%;border:2px solid #b3d7cd;background:#247b69;color:#fff;font-size:16px;font-weight:700;cursor:pointer}.account-avatar:focus-visible{outline:3px solid #68b4a3;outline-offset:3px}.content.campaign-workspace-content{padding:0}
</style>
