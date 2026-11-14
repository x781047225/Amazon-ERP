<script setup lang="ts">
import {useTableWidths as useListTableWidths} from './tableWidths'
const listWidths=useListTableWidths('DataSyncPanelLists')

import {ref,onMounted,onUnmounted,watch} from 'vue'
import {api,session,can} from './session'
import {ElMessage} from 'element-plus'
const stores=ref<string[]>([]),store=ref(session.value?.current_store||''),rows=ref<any[]>([]),runs=ref<any[]>([]),error=ref(''),loading=ref(false),page=ref(1)
const days=[1,7,14,30,90,180,365],intervals=[1440,720,360,60,30,10,5]
const names:Record<string,string>={listing:'Listing',orders:'订单',inventory:'库存',shipments:'货件',finance:'利润结算',advertising:'广告活动',sta:'STA任务'}
const statuses:Record<string,string>={queued:'排队中',running:'同步中',waiting:'等待亚马逊报表',succeeded:'已完成',partial:'部分完成',failed:'失败'}
const intervalName=(n:number)=>n>=60?(n/60)+'小时':n+'分钟'
const time=(v:string)=>v?new Date(v).toLocaleString('zh-CN',{hour12:false}):'—'
const active=(r:any)=>r.active||['queued','running'].includes(r.latest?.status)
let timer:ReturnType<typeof setInterval>|undefined,seq=0,disposed=false
async function load(reset=false){
 if(!store.value)return
 const id=++seq,s=store.value
 if(reset)loading.value=true
 try{
  const [data,history]=await Promise.all([api('/settings/sync-data?store='+encodeURIComponent(s)),api('/settings/sync-data/runs?'+new URLSearchParams({store:s,page:String(page.value)}))])
  if(disposed||id!==seq)return
  rows.value=data.rows.map((r:any)=>{const old=rows.value.find(x=>x.kind===r.kind);return {...r,draft:!reset&&old?old.draft:{days:r.days,interval_minutes:r.interval_minutes,enabled:r.enabled},draftRevision:!reset&&old?old.draftRevision:r.revision,saving:old?.saving||false,starting:old?.starting||false}})
  runs.value=history;error.value=''
 }catch(e){if(!disposed&&id===seq)error.value=(e as Error).message}
 finally{if(id===seq)loading.value=false}
}
async function save(row:any){
 row.saving=true
 try{const result=await api(`/settings/sync-data/${encodeURIComponent(store.value)}/${row.kind}/schedule`,{...row.draft,revision:row.draftRevision});row.draftRevision=result.revision;ElMessage.success('同步设置已保存');await load()}
 catch(e){ElMessage.error((e as Error).message)}finally{row.saving=false;const current=rows.value.find(r=>r.kind===row.kind);if(current)current.saving=false}
}
async function initialize(row:any){
 row.starting=true
 try{await api(`/settings/sync-data/${encodeURIComponent(store.value)}/${row.kind}/initialize`,{days:row.draft.days});ElMessage.success('初始化已加入后台队列，关闭页面后继续执行');page.value=1;await load()}
 catch(e){ElMessage.error((e as Error).message)}finally{row.starting=false;const current=rows.value.find(r=>r.kind===row.kind);if(current)current.starting=false}
}
watch(store,()=>{seq++;rows.value=[];runs.value=[];page.value=1;load(true)})
onMounted(async()=>{try{stores.value=(await api('/options')).stores;if(!stores.value.includes(store.value))store.value=stores.value[0]||'';else await load(true);if(!disposed)timer=setInterval(()=>load(),5000)}catch(e){error.value=(e as Error).message}})
onUnmounted(()=>{disposed=true;seq++;if(timer)clearInterval(timer)})
</script>
<template>
<section class="sync-center">
 <div class="sync-heading"><div><h3>同步数据</h3><p>先在「店铺授权管理」配置授权，再选择店铺初始化。七类数据可以分别设置同步周期。</p></div><div class="sync-controls"><el-select v-model="store" aria-label="同步店铺" :disabled="rows.some(r=>r.saving||r.starting)"><el-option v-for="s in stores" :key="s" :value="s" :label="s"/></el-select><el-button @click="load(true)">刷新设置</el-button><TableReset table-key="DataSyncPanelLists/list-1"/></div></div>
 <el-alert v-if="error" :title="error" type="error" :closable="false"/>
 <p class="sync-note">初始化会补充或更新现有数据，不清空已有记录。定时任务由后台执行，服务器需保持运行；同一店铺同一类数据不会重复启动。修改设置后请点击该行「保存设置」。</p>
 <template v-if="true"><el-table data-width-key="DataSyncPanelLists/list-1" @header-dragend="(w:number,_:number,c:any)=>listWidths.resize('list-1',w,c)" @header-contextmenu="(c:any,e:MouseEvent)=>listWidths.resetColumn('list-1',c,e)" :data="rows" border v-loading="loading" row-key="kind" class="sync-settings">
  <el-table-column :column-key="'column-1'" :width="listWidths.width('list-1','column-1',undefined)" label="数据" min-width="260"><template #default="{row}"><b>{{row.label}}</b><p class="sync-note">{{row.note}}</p><span v-if="!row.ready" class="sync-error">{{row.message}}</span></template></el-table-column>
  <el-table-column :column-key="'column-2'" :width="listWidths.width('list-1','column-2',undefined)" label="初始化" min-width="235"><template #default="{row}"><div class="sync-stack"><el-tag v-if="['listing','inventory'].includes(row.kind)" type="info">当前全量</el-tag><el-select v-else v-model="row.draft.days" :aria-label="row.label+'初始化范围'" :disabled="!can('settings.sync-data','operate')"><el-option v-for="d in days" :key="d" :value="d" :label="'最近 '+d+' 天'"/></el-select><el-button v-if="can('settings.sync-data','operate')" type="primary" plain :disabled="!row.ready||active(row)" :loading="row.starting" @click="initialize(row)">{{active(row)?'同步进行中':row.latest?'重新初始化':'初始化'}}</el-button></div></template></el-table-column>
  <el-table-column :column-key="'column-3'" :width="listWidths.width('list-1','column-3',undefined)" label="定时同步" min-width="280"><template #default="{row}"><div class="sync-stack"><el-switch v-model="row.draft.enabled" :aria-label="row.label+'定时同步'" active-text="开启" inactive-text="关闭" :disabled="!can('settings.sync-data','operate')||!row.ready"/><el-select v-model="row.draft.interval_minutes" :aria-label="row.label+'同步周期'" :disabled="!can('settings.sync-data','operate')"><el-option v-for="n in intervals" :key="n" :value="n" :label="'每 '+intervalName(n)"/></el-select><el-button v-if="can('settings.sync-data','operate')" :loading="row.saving" @click="save(row)">保存设置</el-button></div><p class="sync-note">下次执行：{{row.enabled?time(row.next_run_at):'未开启'}}</p></template></el-table-column>
  <el-table-column :column-key="'column-4'" :width="listWidths.width('list-1','column-4',undefined)" label="最近任务" min-width="260"><template #default="{row}"><el-tag v-if="row.latest" :type="row.latest.status==='failed'?'danger':row.latest.status==='partial'?'warning':row.latest.status==='succeeded'?'success':'info'">{{statuses[row.latest.status]||row.latest.status}}</el-tag><span v-else>{{row.active?'已有任务进行中':'尚未初始化'}}</span><p class="sync-note">最近整次同步完成：{{time(row.last_success_at)}}</p><p v-if="row.latest?.total" class="sync-note">已完成分段 / 账户：{{row.latest.completed}} / {{row.latest.total}}<span v-if="active(row)">，整体仍在同步</span></p><template v-if="row.progress"><p class="sync-note">已入库订单：{{row.progress.stored_orders.toLocaleString()}} 笔</p><p v-if="row.progress.last_segment_at" class="sync-note">最近分段完成：{{time(row.progress.last_segment_at)}}</p><p v-for="s in row.progress.segments" :key="s.store" class="sync-note">正在处理：{{time(s.start_at)}} 至 {{time(s.end_at)}}；已读取 {{s.processed}} 笔</p></template><p v-if="row.error||row.latest?.error" class="sync-error">{{row.error||row.latest.error}}</p><p v-if="row.latest?.warning" class="sync-note">{{row.latest.warning}}</p></template></el-table-column>
 </el-table></template>
 <div class="sync-heading history-heading"><h3>任务记录</h3><span class="sync-note">每 5 秒更新状态</span><TableReset table-key="DataSyncPanelLists/list-2"/></div>
 <template v-if="true"><el-table data-width-key="DataSyncPanelLists/list-2" @header-dragend="(w:number,_:number,c:any)=>listWidths.resize('list-2',w,c)" @header-contextmenu="(c:any,e:MouseEvent)=>listWidths.resetColumn('list-2',c,e)" :data="runs" border row-key="id">
  <el-table-column :column-key="'column-1'" :width="listWidths.width('list-2','column-1',undefined)" type="expand"><template #default="{row}"><div class="sync-detail"><p>请求范围：{{time(row.start_at)}} 至 {{time(row.end_at)}}</p><p v-if="row.warning">{{row.warning}}</p><p v-if="row.error" class="sync-error">{{row.error}}</p><template v-if="true"><TableReset table-key="DataSyncPanelLists/list-3"/><el-table data-width-key="DataSyncPanelLists/list-3" @header-dragend="(w:number,_:number,c:any)=>listWidths.resize('list-3',w,c)" @header-contextmenu="(c:any,e:MouseEvent)=>listWidths.resetColumn('list-3',c,e)" :data="row.steps"><el-table-column :column-key="'column-1'" :width="listWidths.width('list-3','column-1',undefined)" label="分段 / 广告账户" min-width="300"><template #default="{row:step}">{{step.country?step.country+' · '+step.profile_id+' · ':''}}{{time(step.start)}} 至 {{time(step.end)}}</template></el-table-column><el-table-column :column-key="'column-2'" :width="listWidths.width('list-3','column-2','170')" label="状态" ><template #default="{row:step}">{{statuses[step.status]||'待执行'}}</template></el-table-column><el-table-column :column-key="'error'" :width="listWidths.width('list-3','error',undefined)" prop="error" label="说明" min-width="250"/></el-table></template></div></template></el-table-column>
  <el-table-column :column-key="'column-2'" :width="listWidths.width('list-2','column-2','120')" label="数据" ><template #default="{row}">{{names[row.kind]}}</template></el-table-column><el-table-column :column-key="'column-3'" :width="listWidths.width('list-2','column-3','130')" label="任务" ><template #default="{row}">{{row.mode==='initialize'?'初始化':'定时同步'}}</template></el-table-column><el-table-column :column-key="'column-4'" :width="listWidths.width('list-2','column-4','130')" label="状态" ><template #default="{row}">{{statuses[row.status]||row.status}}</template></el-table-column><el-table-column :column-key="'column-5'" :width="listWidths.width('list-2','column-5',undefined)" label="发起时间" min-width="175"><template #default="{row}">{{time(row.created_at)}}</template></el-table-column><el-table-column :column-key="'column-6'" :width="listWidths.width('list-2','column-6',undefined)" label="完成时间" min-width="175"><template #default="{row}">{{time(row.finished_at)}}</template></el-table-column><el-table-column :column-key="'actor'" :width="listWidths.width('list-2','actor','140')" prop="actor" label="发起人" /><el-table-column :column-key="'column-8'" :width="listWidths.width('list-2','column-8',undefined)" label="说明" min-width="260"><template #default="{row}">{{row.error||row.warning||'—'}}</template></el-table-column>
 </el-table></template><div class="sync-pages"><el-button :disabled="page===1" @click="page--;load()">上一页</el-button><span>第 {{page}} 页</span><el-button :disabled="runs.length<30" @click="page++;load()">下一页</el-button></div>
</section>
</template>
<style scoped>
.sync-center{padding-top:20px}.sync-heading{display:flex;justify-content:space-between;gap:20px;align-items:center}.sync-heading h3{margin:0}.sync-heading p,.sync-note{color:#7a8c84;font-size:13px;line-height:1.7}.sync-controls{display:flex;gap:12px}.sync-controls .el-select{width:200px}.sync-stack{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.sync-stack .el-select{width:140px}.sync-error{color:#d04e4e;font-size:13px;white-space:pre-wrap}.sync-detail{padding:12px 24px}.history-heading{margin:26px 0 16px}.sync-pages{display:flex;justify-content:flex-end;align-items:center;gap:15px;margin-top:16px}@media(max-width:800px){.sync-heading{align-items:flex-start;flex-direction:column}}
</style>
