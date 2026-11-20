<script setup lang="ts">
import {useTableWidths as useListTableWidths} from './tableWidths'
const listWidths=useListTableWidths('DataSyncPanelLists')

import {ref,onMounted,onUnmounted,watch} from 'vue'
import {api,session,can} from './session'
import SyncIssue from './SyncIssue.vue'
import PayoutSchedule from './PayoutSchedule.vue'
import {ElMessage,ElMessageBox} from 'element-plus'
const stores=ref<string[]>([]),store=ref(session.value?.current_store||''),rows=ref<any[]>([]),runs=ref<any[]>([]),error=ref(''),historyError=ref(''),loading=ref(false),historyLoading=ref(false),page=ref(1),pageSize=ref(25),total=ref(0)
const days=[1,7,14,30,90,180,365],intervals=[1440,720,360,240,60,30,10,5,1]
const names:Record<string,string>={listing:'Listing',orders:'订单',inventory:'库存',shipments:'货件',finance:'利润结算',advertising:'广告活动',sta:'STA任务',mail:'绩效邮件',fees:'商品费用报告'}
const statuses:Record<string,string>={retry_pending:'等待继续',queued:'排队中',running:'同步中',waiting:'等待亚马逊报表',succeeded:'已完成',partial:'部分完成',failed:'失败'}
const runLabel=(r:any)=>r?.kind==='advertising'&&r.status==='partial'&&!r.error&&r.completed===r.total&&r.total?'已结束 · 历史范围受限':statuses[r?.status]||r?.status
const recommended:Record<string,number>={advertising:1440,fees:1440,orders:5,inventory:1,listing:60,finance:240,shipments:240,sta:240}
const intervalName=(n:number)=>n>=60?(n/60)+'小时':n+'分钟'
const time=(v:string)=>v?new Date(v).toLocaleString('zh-CN',{hour12:false}):'—'
const active=(r:any)=>r.active||['queued','running'].includes(r.latest?.status)
let timer:ReturnType<typeof setInterval>|undefined,seq=0,disposed=false,pending=false,controller:AbortController|undefined
async function load(reset=false,poll=false){
 if(!store.value||disposed||(poll&&pending))return
 controller?.abort()
 const request=new AbortController();controller=request;pending=true
 const id=++seq,s=store.value,current=()=>!disposed&&id===seq
 const timeout=setTimeout(()=>request.abort(),30000)
 if(reset||!rows.value.length)loading.value=true
 historyLoading.value=true
 const message=(e:unknown)=>request.signal.aborted?'加载超过 30 秒，请刷新重试。':(e as Error).message
 // Settings can render immediately even if the history request is slow or fails.
 const settings=api('/settings/sync-data?store='+encodeURIComponent(s),undefined,{signal:request.signal}).then(data=>{
  if(!current())return
  rows.value=data.rows.map((r:any)=>{const old=rows.value.find(x=>x.kind===r.kind);return {...r,draft:!reset&&old?old.draft:{days:r.days,interval_minutes:r.interval_minutes,enabled:r.enabled},draftRevision:!reset&&old?old.draftRevision:r.revision,saving:old?.saving||false,starting:old?.starting||false,resuming:old?.resuming||false}})
  error.value=''
 }).catch(e=>{if(current())error.value=message(e)}).finally(()=>{if(current())loading.value=false})
 const history=api('/settings/sync-data/runs?'+new URLSearchParams({store:s,page:String(page.value),page_size:String(pageSize.value),paginated:'true'}),undefined,{signal:request.signal}).then(data=>{
  if(!current())return
  runs.value=data.rows;total.value=data.total;historyError.value=''
 }).catch(e=>{if(current())historyError.value=message(e)}).finally(()=>{if(current())historyLoading.value=false})
 await Promise.allSettled([settings,history]);clearTimeout(timeout)
 if(current())pending=false
}
async function save(row:any){
 row.saving=true
 try{const result=await api(`/settings/sync-data/${encodeURIComponent(store.value)}/${row.kind}/schedule`,{...row.draft,revision:row.draftRevision});row.draftRevision=result.revision;ElMessage.success('同步设置已保存');await load()}
 catch(e){ElMessage.error((e as Error).message)}finally{row.saving=false;const current=rows.value.find(r=>r.kind===row.kind);if(current)current.saving=false}
}
async function initialize(row:any){
 if(row.starting||row.resuming)return
 if(row.latest?.status==='failed'){try{await ElMessageBox.confirm('重新初始化会按所选天数重新请求整个范围；如要接着原任务处理，请取消后选择「继续同步」。','重新初始化',{confirmButtonText:'重新初始化',cancelButtonText:'取消',type:'warning'})}catch{return}}
 row.starting=true
 try{await api(`/settings/sync-data/${encodeURIComponent(store.value)}/${row.kind}/initialize`,{days:row.draft.days});ElMessage.success('初始化已加入后台队列，关闭页面后继续执行');page.value=1;await load()}
 catch(e){ElMessage.error((e as Error).message)}finally{row.starting=false;const current=rows.value.find(r=>r.kind===row.kind);if(current)current.starting=false}
}
async function resume(row:any,run:any=row.latest){
 if(row.resuming||row.starting||active(row)||run?.status!=='failed')return
 row.resuming=true
 const selectedStore=store.value
 try{await api(`/settings/sync-data/${encodeURIComponent(selectedStore)}/${row.kind}/resume`,{run_id:run.id});ElMessage.success('已继续原任务，保留已完成进度');await load()}
 catch(e){ElMessage.error((e as Error).message)}finally{row.resuming=false;const current=rows.value.find(r=>r.kind===row.kind);if(current)current.resuming=false}
}
function historyRow(run:any){return rows.value.find(r=>r.kind===run.kind)}
watch(store,()=>{seq++;controller?.abort();rows.value=[];runs.value=[];total.value=0;error.value='';historyError.value='';page.value=1;load(true)})
onMounted(async()=>{try{stores.value=(await api('/options')).stores;if(!stores.value.includes(store.value))store.value=stores.value[0]||'';else await load(true);if(!disposed)timer=setInterval(()=>load(false,true),5000)}catch(e){error.value=(e as Error).message}})
onUnmounted(()=>{disposed=true;seq++;controller?.abort();if(timer)clearInterval(timer)})
</script>
<template>
<section class="sync-center">
 <div class="sync-heading"><div><h3>同步数据</h3><p>先在「店铺授权管理」配置授权，再选择店铺初始化。各类数据可以分别设置同步周期。</p></div><div class="sync-controls"><el-select v-model="store" aria-label="同步店铺" :disabled="rows.some(r=>r.saving||r.starting||r.resuming)"><el-option v-for="s in stores" :key="s" :value="s" :label="s"/></el-select><el-button :disabled="loading" @click="load(true)">刷新设置</el-button><TableReset table-key="DataSyncPanelLists/list-1"/></div></div>
 <el-alert v-if="error" :title="error" type="error" :closable="false"/>
 <p class="sync-note">初始化会补充或更新现有数据，不清空已有记录。定时任务由后台执行，服务器需保持运行；同一店铺同一类数据不会重复启动。初始化天数用于手动回溯；定时增量任务从成功位置续读，具体规则见各行。修改设置后请点击该行「保存设置」。</p>
 <template v-if="true"><el-table data-width-key="DataSyncPanelLists/list-1" @header-dragend="(w:number,_:number,c:any)=>listWidths.resize('list-1',w,c)" @header-contextmenu="(c:any,e:MouseEvent)=>listWidths.resetColumn('list-1',c,e)" :data="rows" border v-loading="loading" row-key="kind" class="sync-settings">
  <el-table-column :column-key="'column-1'" :width="listWidths.width('list-1','column-1',undefined)" label="数据" min-width="260"><template #default="{row}"><b>{{row.label}}</b><p class="sync-note">{{row.note}}</p><span v-if="!row.ready" class="sync-error">{{row.message}}</span></template></el-table-column>
  <el-table-column :column-key="'column-2'" :width="listWidths.width('list-1','column-2',undefined)" label="初始化 / 回溯范围" min-width="290"><template #default="{row}"><div class="sync-stack"><el-tag v-if="['listing','inventory'].includes(row.kind)" type="info">当前全量</el-tag><el-select v-else v-model="row.draft.days" :aria-label="row.label+'初始化范围'" :disabled="!can('settings.sync-data','operate')"><el-option v-for="d in days" :key="d" :value="d" :label="'最近 '+d+' 天'"/></el-select><div class="sync-init-actions"><el-button v-if="can('settings.sync-data','operate')" type="primary" plain :disabled="!row.ready||active(row)||row.resuming" :loading="row.starting" @click="initialize(row)">{{active(row)?'同步进行中':row.latest?'重新初始化':'初始化'}}</el-button><el-button v-if="row.latest?.status==='failed'&&can('settings.sync-data','operate')" type="primary" :disabled="!row.ready||active(row)||row.starting" :loading="row.resuming" @click="resume(row)">继续同步</el-button></div></div><p v-if="row.latest?.status==='failed'" class="sync-note">继续同步沿用原任务范围与进度，不使用上方新选天数。{{row.kind==='inventory'?'库存需重新读取当前完整快照。':row.kind==='listing'?'Listing 保留列表扫描检查点，模板和目录信息会重新核查。':''}}</p></template></el-table-column>
  <el-table-column :column-key="'column-3'" :width="listWidths.width('list-1','column-3',undefined)" label="定时同步" min-width="280"><template #default="{row}"><div class="sync-stack"><el-switch v-model="row.draft.enabled" :aria-label="row.label+'定时同步'" active-text="开启" inactive-text="关闭" :disabled="!can('settings.sync-data','operate')||!row.ready"/><el-select v-model="row.draft.interval_minutes" :aria-label="row.label+'同步周期'" :disabled="!can('settings.sync-data','operate')"><el-option v-for="n in intervals.filter(n=>n!==1||row.kind==='inventory')" :key="n" :value="n" :label="'每 '+intervalName(n)+(recommended[row.kind]===n?'（推荐）':'')"/></el-select><el-button v-if="can('settings.sync-data','operate')" :loading="row.saving" @click="save(row)">保存设置</el-button></div><p class="sync-note">{{row.schedule_note}}</p><p class="sync-note">已保存：{{row.enabled?'每 '+intervalName(row.interval_minutes):'未开启'}}<span v-if="row.draft.enabled!==row.enabled||row.draft.interval_minutes!==row.interval_minutes">（有未保存的修改）</span></p><p class="sync-note">计划触发：{{row.enabled?time(row.next_run_at):'未开启'}}</p><p v-if="row.enabled &amp;&amp; active(row)" class="sync-note">本轮结束前不会重复排队；若已到期，结束后接续一轮。</p></template></el-table-column>
  <el-table-column :column-key="'column-4'" :width="listWidths.width('list-1','column-4',undefined)" label="最近任务" min-width="260"><template #default="{row}"><el-tag v-if="row.latest" :type="row.latest.status==='failed'?'danger':row.latest.status==='partial'?'warning':row.latest.status==='succeeded'?'success':'info'">{{row.latest.status==='running' && row.latest.steps?.some((s:any)=>s.status==='queued')?'等待执行':runLabel({...row.latest,kind:row.kind})}}</el-tag><span v-else>{{row.active?'已有任务进行中':'尚未初始化'}}</span><p class="sync-note">{{row.kind==='advertising'?'最近同步结束':'最近整次同步完成'}}：{{time(row.kind==='advertising'?row.latest?.finished_at:row.last_success_at)}}</p><p v-if="row.latest?.total" class="sync-note">{{row.kind==='advertising'?'已处理广告账户':'已完成分段'}}：{{row.latest.completed}} / {{row.latest.total}}<span v-if="active(row)">，整体仍在同步</span></p><p v-if="row.kind==='advertising' &amp;&amp; row.latest?.status==='partial' &amp;&amp; row.latest.completed===row.latest.total" class="sync-note">账户处理已结束；不代表所选 {{row.latest.days}} 天历史数据全部齐全。</p><p v-if="row.kind==='mail' &amp;&amp; row.latest?.steps?.length" class="sync-note">本次已检查 {{row.latest.steps[0].read||0}} 封，新增归档 {{row.latest.steps[0].saved||0}} 封亚马逊邮件</p><template v-if="row.progress"><p class="sync-note">已入库订单：{{row.progress.stored_orders.toLocaleString()}} 笔</p><p v-if="row.progress.last_segment_at" class="sync-note">最近分段完成：{{time(row.progress.last_segment_at)}}</p><p v-for="s in row.progress.segments" :key="s.store" class="sync-note">正在处理：{{time(s.start_at)}} 至 {{time(s.end_at)}}；已读取 {{s.processed}} 笔</p></template><SyncIssue :message="active(row)?null:row.error||row.latest?.error"/><p v-if="row.latest?.warning" class="sync-note">{{row.latest.warning}}</p></template></el-table-column>
 </el-table></template>
 <PayoutSchedule :key="store" :store="store"/>
 <div class="sync-heading history-heading"><h3>任务记录</h3><span class="sync-note">每 5 秒更新状态</span><TableReset table-key="DataSyncPanelLists/list-2"/></div>
 <el-alert v-if="historyError" :title="'任务记录：'+historyError" type="error" :closable="false"/>
 <template v-if="true"><el-table v-loading="historyLoading && !runs.length" data-width-key="DataSyncPanelLists/list-2" @header-dragend="(w:number,_:number,c:any)=>listWidths.resize('list-2',w,c)" @header-contextmenu="(c:any,e:MouseEvent)=>listWidths.resetColumn('list-2',c,e)" :data="runs" border row-key="id">
  <el-table-column :column-key="'column-1'" :width="listWidths.width('list-2','column-1',undefined)" type="expand"><template #default="{row}"><div class="sync-detail"><p>请求范围：{{time(row.start_at)}} 至 {{time(row.end_at)}}</p><p v-if="row.warning">{{row.warning}}</p><SyncIssue :message="row.error"/><template v-if="true"><TableReset table-key="DataSyncPanelLists/list-3"/><el-table data-width-key="DataSyncPanelLists/list-3" @header-dragend="(w:number,_:number,c:any)=>listWidths.resize('list-3',w,c)" @header-contextmenu="(c:any,e:MouseEvent)=>listWidths.resetColumn('list-3',c,e)" :data="row.steps"><el-table-column :column-key="'column-1'" :width="listWidths.width('list-3','column-1',undefined)" label="分段 / 广告账户" min-width="300"><template #default="{row:step}">{{step.country?step.country+' · '+step.profile_id+' · ':''}}{{time(step.start)}} 至 {{time(step.end)}}</template></el-table-column><el-table-column :column-key="'column-2'" :width="listWidths.width('list-3','column-2','170')" label="状态" ><template #default="{row:step}">{{statuses[step.status]||'待执行'}}</template></el-table-column><el-table-column :column-key="'error'" :width="listWidths.width('list-3','error',undefined)" label="说明" min-width="250"><template #default="{row:step}"><SyncIssue :message="step.error"/></template></el-table-column></el-table></template></div></template></el-table-column>
  <el-table-column :column-key="'column-2'" :width="listWidths.width('list-2','column-2','120')" label="数据" ><template #default="{row}">{{names[row.kind]}}</template></el-table-column><el-table-column :column-key="'column-3'" :width="listWidths.width('list-2','column-3','130')" label="任务" ><template #default="{row}">{{row.mode==='initialize'?'初始化':'定时同步'}}</template></el-table-column><el-table-column :column-key="'column-4'" :width="listWidths.width('list-2','column-4','130')" label="状态" ><template #default="{row}">{{runLabel(row)}}</template></el-table-column><el-table-column :column-key="'column-5'" :width="listWidths.width('list-2','column-5',undefined)" label="发起时间" min-width="175"><template #default="{row}">{{time(row.created_at)}}</template></el-table-column><el-table-column :column-key="'column-6'" :width="listWidths.width('list-2','column-6',undefined)" label="完成时间" min-width="175"><template #default="{row}">{{time(row.finished_at)}}</template></el-table-column><el-table-column :column-key="'actor'" :width="listWidths.width('list-2','actor','140')" prop="actor" label="发起人" /><el-table-column :column-key="'column-8'" :width="listWidths.width('list-2','column-8',undefined)" label="说明" min-width="260"><template #default="{row}"><SyncIssue v-if="row.error" :message="row.error"/><span v-else>{{row.warning||'—'}}</span><el-button v-if="row.status==='failed'&&historyRow(row)&&can('settings.sync-data','operate')" link type="primary" :disabled="!historyRow(row).ready||active(historyRow(row))||historyRow(row).starting||historyRow(row).resuming" @click="resume(historyRow(row),row)">继续同步</el-button></template></el-table-column>
 </el-table></template><AppPagination v-model:current-page="page" v-model:page-size="pageSize" :total="total" @change="load()"/>
</section>
</template>
<style scoped>
.sync-center{padding-top:20px}.sync-heading{display:flex;justify-content:space-between;gap:20px;align-items:center}.sync-heading h3{margin:0}.sync-heading p,.sync-note{color:#7a8c84;font-size:13px;line-height:1.7}.sync-controls{display:flex;gap:12px}.sync-controls .el-select{width:200px}.sync-stack{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.sync-init-actions{display:flex;gap:10px;flex-wrap:nowrap}.sync-init-actions .el-button{margin-left:0}.sync-stack .el-select{width:140px}.sync-error{color:#d04e4e;font-size:13px;white-space:pre-wrap}.sync-detail{padding:12px 24px}.history-heading{margin:26px 0 16px}.sync-pages{display:flex;justify-content:flex-end;align-items:center;gap:15px;margin-top:16px}@media(max-width:800px){.sync-heading{align-items:flex-start;flex-direction:column}}
</style>
